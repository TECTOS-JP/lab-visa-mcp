"""
v0.8.0: DSL validator + compiler

ExperimentPlan を walk して:
- 15 項目を検証 (instrument / command / safety / verify / state_query 等)
- 既存 IR (CommandStep / WaitStep / ...) に compile
- sweep を展開、parallel を TargetExecution リストに変換
- safe_shutdown step は marker として残し、JobManager 側で処理

返り値は `CompiledPlan` データクラス。validation だけしたい場合は
`validate_only=True` で compile を省略可能 (ただし基本検証は同じ)。
"""
from __future__ import annotations
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from visa_mcp.dsl.schema import (
    ExperimentPlan, Step,
    DSLCommandStep, DSLQueryStep,
    DSLWaitStep, DSLWaitUntilStep,
    DSLWaitForConditionStep, DSLWaitForStableStep,
    DSLBarrierStep, DSLSafeShutdownStep,
    DSLSweepStep, DSLParallelStep,
    MAX_SWEEP_POINTS, MAX_PARALLEL_CONCURRENCY, MAX_PARALLEL_BRANCHES,
)
from visa_mcp.experiment_ir import (
    CommandStep, Plan, Step as IRStep, WaitStep,
    WaitUntilStep, WaitForConditionStep, WaitForStableStep,
    BarrierStep,
)
from visa_mcp.group.resolver import resolve_resource, ResolveError
from visa_mcp.session_manager import SessionManager
from visa_mcp.system_config import SystemConfig
from visa_mcp import safety as sf

logger = logging.getLogger(__name__)


# ============================================================
# Compile 結果
# ============================================================


@dataclass
class CompiledPlan:
    """validation + compile の結果"""
    valid: bool
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    # 展開後 step 数 / 推定 duration / required_resources / verify 使用有無 等
    summary: dict[str, Any] = field(default_factory=dict)
    # compile 結果: 「single thread plan」 + 「parallel branch plans」のリスト
    # v0.8.0.1: parallel は top-level 末尾 1 回のみ許可 (placement 制約強化)
    main_plan: Plan | None = None
    parallel_groups: list[dict] = field(default_factory=list)
    # safe_shutdown ステップが含まれていたか
    has_safe_shutdown: bool = False
    # v0.8.0.1: safe_shutdown.targets が明示指定された場合の対象 resource リスト
    # None なら "Plan で使用した全 resource" (= main_plan.required_resources)
    safe_shutdown_targets: list[str] | None = None
    # 解決済み bindings (alias → resource_name)
    resolved_instruments: dict[str, str] = field(default_factory=dict)
    # v0.8.0.1: dry_run 用 rendered SCPI / safety / verify 情報
    # 旧 v0.8.0 では _Context に蓄積されていたが、外部 (tools/dsl.py) から
    # private helper で再 compile していたため正式フィールド化
    rendered_steps: list[dict] = field(default_factory=list)


# ============================================================
# Validator + Compiler 本体
# ============================================================


class _Context:
    """compile 中の状態を持つ context"""
    def __init__(
        self,
        plan: ExperimentPlan,
        session_mgr: SessionManager,
        system_config: SystemConfig,
        path: str = "",
    ) -> None:
        self.plan = plan
        self.session_mgr = session_mgr
        self.system_config = system_config
        self.path = path  # 例: "steps[3].body[0]"
        self.errors: list[dict] = []
        self.warnings: list[dict] = []
        # 使用された resource_name の収集 (canonical sorted で出す)
        self.required_resources: set[str] = set()
        # 解決済み instrument_ref → resource_name
        self.resolved: dict[str, str] = {}
        # 推定 duration (s)
        self.estimated_duration_s: float = 0.0
        # verify 使用 step 数 / polling 使用有無
        self.uses_verify_count: int = 0
        self.uses_polling: bool = False
        # safe_shutdown 含む
        self.has_safe_shutdown: bool = False
        # v0.8.0.1: safe_shutdown.targets が明示指定された場合の解決済み resource list。
        # None ならコンパイル後の required_resources を使う。
        self.safe_shutdown_targets: list[str] | None = None
        # parallel が含まれていたか
        self.parallel_groups: list[dict] = []
        # validation-only mode?
        self.dry: bool = False
        # rendered SCPI (dry_run 用)
        self.rendered_steps: list[dict] = []
        # 上限カウント
        self.total_expanded_steps: int = 0

    def add_error(
        self, error_class: str, message: str,
        step_index: int | None = None,
        recommended_next_actions: list[dict] | None = None,
        **extra,
    ) -> None:
        e: dict[str, Any] = {
            "error_class": error_class,
            "message": message,
            "path": self.path,
        }
        if step_index is not None:
            e["step_index"] = step_index
        if recommended_next_actions:
            e["recommended_next_actions"] = recommended_next_actions
        e.update(extra)
        self.errors.append(e)

    def add_warning(self, warning_class: str, message: str, **extra) -> None:
        w: dict[str, Any] = {
            "warning_class": warning_class,
            "message": message,
            "path": self.path,
        }
        w.update(extra)
        self.warnings.append(w)


def _resolve_instrument(
    ctx: _Context, ref: str, step_index: int,
) -> tuple[str | None, "InstrumentSession | None"]:
    """DSL の instrument ref を (resource_name, session) に解決。

    解決順序 (実装方針 #13):
      1. plan.bindings (e.g. "$psu" → "psu001")
      2. system_config の experiment_units (省略、bindings に集約)
      3. _system.yaml instruments alias
      4. raw VISA resource (許可、ただし alias 推奨を warning)
    """
    if not ref:
        ctx.add_error(
            "validation",
            "instrument が空です", step_index=step_index,
        )
        return None, None

    target = ref
    if ref.startswith("$"):
        role = ref[1:]
        if role not in ctx.plan.bindings:
            ctx.add_error(
                "unknown_binding",
                f"plan.bindings に '{role}' がありません (ref={ref!r})",
                step_index=step_index,
                recommended_next_actions=[
                    {"action": "fix_bindings",
                     "reason": f"plan.bindings に '{role}' を追加してください"},
                ],
            )
            return None, None
        target = ctx.plan.bindings[role]

    # alias / resource_name 解決
    try:
        resource = resolve_resource(target, {}, ctx.system_config)
    except ResolveError as e:
        ctx.add_error(
            "unknown_instrument",
            f"instrument '{target}' を resource として解決できません: {e}",
            step_index=step_index,
            recommended_next_actions=[
                {"action": "list_resources", "tool": "list_resources"},
                {"action": "check_bindings",
                 "reason": "plan.bindings / _system.yaml.instruments を確認"},
            ],
        )
        return None, None

    # raw resource 指定 (alias を介さず) → warning
    if target == resource and "::" in target:
        ctx.add_warning(
            "raw_resource_used",
            f"raw VISA resource '{target}' が直接指定されています。"
            f"alias / bindings の使用を推奨します",
            instrument=target,
        )

    ctx.resolved[ref] = resource
    ctx.required_resources.add(resource)

    session = ctx.session_mgr.get_session(resource)
    if session is None or session.definition is None:
        ctx.add_error(
            "not_identified",
            f"instrument '{ref}' (→ {resource}) は未識別です",
            step_index=step_index,
            recommended_next_actions=[
                {"action": "identify_instrument",
                 "tool": "identify_instrument",
                 "args": {"resource_name": resource}},
            ],
        )
        return resource, None
    return resource, session


def _validate_command(
    ctx: _Context,
    session,
    command_name: str,
    args: dict,
    step_index: int,
    expect_type: str | None = None,
) -> None:
    """command の存在 + 型 + パラメータ + safety を検証"""
    from visa_mcp.utils.param_validator import validate_and_build_scpi, ParameterValidationError

    if session is None or session.definition is None:
        return

    cmd_def = session.definition.commands.get(command_name)
    if cmd_def is None:
        ctx.add_error(
            "unknown_command",
            f"command '{command_name}' が機器定義に存在しません",
            step_index=step_index,
            command=command_name,
            recommended_next_actions=[
                {"action": "list_commands", "tool": "list_commands",
                 "args": {"resource_name": session.resource_name}},
            ],
        )
        return

    if expect_type and cmd_def.type != expect_type:
        ctx.add_error(
            "wrong_command_type",
            f"command '{command_name}' の type は '{cmd_def.type}'、"
            f"'{expect_type}' が必要です",
            step_index=step_index,
            command=command_name,
        )
        return

    # parameter validation + SCPI rendering
    try:
        rendered = validate_and_build_scpi(cmd_def, args)
    except ParameterValidationError as e:
        ctx.add_error(
            "parameter_invalid",
            f"command '{command_name}' のパラメータ検証失敗: {e}",
            step_index=step_index,
            command=command_name,
        )
        return

    # safety validation
    violations = sf.validate(
        session.definition, command_name, args,
        session_history=session.command_history,
    )
    mode = sf.get_safety_mode()
    if violations:
        if mode in ("strict",):
            ctx.add_error(
                "safety_violation",
                f"command '{command_name}' は strict mode で safety に違反します",
                step_index=step_index,
                command=command_name,
                violations=list(violations),
                recommended_next_actions=[
                    {"action": "review_safety_constraints",
                     "tool": "list_safety_constraints",
                     "args": {"resource_name": session.resource_name}},
                ],
            )
        else:
            ctx.add_warning(
                "safety_violation",
                f"command '{command_name}' は safety violation を含みますが "
                f"{mode} mode のため警告のみ",
                command=command_name,
                violations=list(violations),
            )

    # polling_safe ヒント (query 用)
    if expect_type == "query" and cmd_def is not None and not cmd_def.polling_safe:
        ctx.add_warning(
            "polling_safe_false",
            f"command '{command_name}' は polling_safe=False。"
            f"polling/monitor で使うと副作用の可能性",
            command=command_name,
        )

    # verify 設定の確認
    if cmd_def.verify is not None:
        rb = session.definition.commands.get(cmd_def.verify.readback_command)
        if rb is None:
            ctx.add_warning(
                "verify_readback_missing",
                f"command '{command_name}' の verify.readback_command "
                f"'{cmd_def.verify.readback_command}' が定義されていません",
                command=command_name,
            )
        elif rb.type != "query":
            ctx.add_warning(
                "verify_readback_not_query",
                f"command '{command_name}' の verify.readback_command は "
                f"query 型である必要がありますが '{rb.type}' です",
                command=command_name,
            )
        else:
            ctx.uses_verify_count += 1

    # 推定 duration (1 命令 ~ 50ms と仮定、verify あれば +50ms)
    ctx.estimated_duration_s += 0.05 + (0.05 if cmd_def.verify is not None else 0.0)

    # rendered SCPI を dry_run 用に残す
    ctx.rendered_steps.append({
        "step_index": step_index,
        "path": ctx.path,
        "step_type": "command" if cmd_def.type == "write" else "query",
        "instrument": session.resource_name,
        "command": command_name,
        "args": dict(args),
        "rendered_scpi": rendered,
        "safety": {
            "status": "ok" if not violations else f"violated_{mode}",
            "violations": list(violations) if violations else [],
        },
        "verify": (
            {
                "enabled": True,
                "readback_command": cmd_def.verify.readback_command,
                "tolerance": cmd_def.verify.tolerance,
            }
            if cmd_def.verify is not None else {"enabled": False}
        ),
    })


# ============================================================
# walk step (DSL → IR conversion)
# ============================================================


def _convert_step(
    ctx: _Context,
    s: Step,
    step_index: int,
    variables: dict[str, Any] | None = None,
) -> list[IRStep]:
    """DSL step を IRStep のリストに変換 (sweep は複数 step に展開)。

    parallel は IR Plan には直接落とせないため、ctx.parallel_groups に蓄積。
    """
    variables = variables or {}

    # arg のテンプレート展開 (簡易: "{voltage}" → variables["voltage"])
    def _expand(value: Any) -> Any:
        if isinstance(value, str):
            # {var} 形式
            if value.startswith("{") and value.endswith("}"):
                name = value[1:-1]
                if name in variables:
                    return variables[name]
                # variables に無いがテンプレート → そのまま (validation error)
                ctx.add_error(
                    "unknown_variable",
                    f"テンプレート変数 '{name}' が未定義です",
                    step_index=step_index,
                )
                return value
        return value

    def _expand_args(args: dict) -> dict:
        return {k: _expand(v) for k, v in args.items()}

    if isinstance(s, (DSLCommandStep, DSLQueryStep)):
        is_query = isinstance(s, DSLQueryStep)
        resource, session = _resolve_instrument(ctx, s.instrument, step_index)
        args = _expand_args(s.args)
        _validate_command(
            ctx, session, s.command, args, step_index,
            expect_type="query" if is_query else None,
        )
        if resource is None:
            return []
        return [CommandStep(
            command=s.command,
            args=args,
            description=s.description,
            instrument=resource,
        )]

    if isinstance(s, DSLWaitStep):
        ctx.estimated_duration_s += float(s.seconds)
        return [WaitStep(seconds=float(s.seconds), description=s.description)]

    if isinstance(s, DSLWaitUntilStep):
        if s.seconds_from_now is not None:
            ctx.estimated_duration_s += float(s.seconds_from_now)
        # timestamp の場合は実行時計算 (推定不可)
        return [WaitUntilStep(
            timestamp=s.timestamp,
            seconds_from_now=s.seconds_from_now,
            description=s.description,
        )]

    if isinstance(s, DSLWaitForConditionStep):
        ctx.uses_polling = True
        resource, session = _resolve_instrument(ctx, s.instrument, step_index)
        _validate_command(
            ctx, session, s.command, _expand_args(s.args),
            step_index, expect_type="query",
        )
        ctx.estimated_duration_s += min(float(s.timeout_s), 30.0)  # 推定上限 30s
        if resource is None:
            return []
        return [WaitForConditionStep(
            instrument=resource,
            command=s.command,
            args=_expand_args(s.args),
            condition_expr=s.condition_expr,
            interval_s=s.interval_s,
            timeout_s=s.timeout_s,
            command_timeout_s=s.command_timeout_s,
            value_path=s.value_path,
            retry_on_error=s.retry_on_error,
            max_consecutive_errors=s.max_consecutive_errors,
            description=s.description,
        )]

    if isinstance(s, DSLWaitForStableStep):
        ctx.uses_polling = True
        resource, session = _resolve_instrument(ctx, s.instrument, step_index)
        _validate_command(
            ctx, session, s.command, _expand_args(s.args),
            step_index, expect_type="query",
        )
        ctx.estimated_duration_s += min(float(s.timeout_s), 30.0)
        if resource is None:
            return []
        return [WaitForStableStep(
            instrument=resource,
            command=s.command,
            args=_expand_args(s.args),
            tolerance=s.tolerance,
            window_s=s.window_s,
            interval_s=s.interval_s,
            timeout_s=s.timeout_s,
            command_timeout_s=s.command_timeout_s,
            value_path=s.value_path,
            min_samples=s.min_samples,
            retry_on_error=s.retry_on_error,
            max_consecutive_errors=s.max_consecutive_errors,
            description=s.description,
        )]

    if isinstance(s, DSLBarrierStep):
        return [BarrierStep(
            name=s.name, timeout_s=s.timeout_s,
            description=s.description,
        )]

    if isinstance(s, DSLSafeShutdownStep):
        ctx.has_safe_shutdown = True
        # v0.8.0.1: targets 指定があれば実 resource に解決して保持
        resolved_targets: list[str] | None = None
        if s.targets:
            resolved_targets = []
            for t in s.targets:
                r, _ = _resolve_instrument(ctx, t, step_index)
                if r is not None:
                    resolved_targets.append(r)
            # 既存解決と統合 (canonical sorted)
            resolved_targets = sorted(set(resolved_targets))
        if resolved_targets:
            # 複数 safe_shutdown step は union として扱う
            if ctx.safe_shutdown_targets is None:
                ctx.safe_shutdown_targets = resolved_targets
            else:
                ctx.safe_shutdown_targets = sorted(
                    set(ctx.safe_shutdown_targets) | set(resolved_targets)
                )
        # IR 上には残さない (Job 終端で JobManager が _best_effort_safe_shutdown を実行)
        # 但し dry_run の rendered 表示用に rendered_steps へ追加
        ctx.rendered_steps.append({
            "step_index": step_index,
            "path": ctx.path,
            "step_type": "safe_shutdown",
            "targets": (
                resolved_targets if resolved_targets is not None
                else "all_used_resources"
            ),
            "targets_raw": list(s.targets) if s.targets else None,
        })
        return []

    if isinstance(s, DSLSweepStep):
        # sweep を展開: 各 value で body を複製
        values = s.values.expand()
        if len(values) * max(1, len(s.body)) + ctx.total_expanded_steps > 10000:
            ctx.add_error(
                "expanded_too_large",
                f"sweep 展開後の総 step 数が大きすぎます "
                f"({len(values)} × {len(s.body)})",
                step_index=step_index,
            )
            return []
        expanded: list[IRStep] = []
        saved_path = ctx.path
        for i, v in enumerate(values):
            iter_vars = dict(variables)
            iter_vars[s.parameter] = v
            for j, body_step in enumerate(s.body):
                ctx.path = f"{saved_path}.sweep[{i}].body[{j}]"
                expanded.extend(_convert_step(ctx, body_step, step_index, iter_vars))
                ctx.total_expanded_steps += 1
        ctx.path = saved_path
        return expanded

    if isinstance(s, DSLParallelStep):
        # parallel: 各 branch を別 Plan として収集 → ctx.parallel_groups に
        # v0.8.0 MVP では「parallel は plan の終端で 1 度」想定。
        # branches を TargetExecution 相当へ落とす。
        saved_path = ctx.path
        branch_plans: list[Plan] = []
        for i, branch in enumerate(s.branches):
            ctx.path = f"{saved_path}.parallel.branches[{i}]"
            branch_ir: list[IRStep] = []
            for j, b_step in enumerate(branch):
                # branch 内で sub-context を共有 (resolved / required_resources は同一)
                old_path = ctx.path
                ctx.path = f"{ctx.path}[{j}]"
                branch_ir.extend(_convert_step(ctx, b_step, step_index, variables))
                ctx.path = old_path
            branch_plans.append(Plan(
                name=f"parallel_branch_{i}",
                steps=branch_ir,
                # required_resources は実行時 (compile 完了後) に集計
            ))
        ctx.path = saved_path
        ctx.parallel_groups.append({
            "step_index": step_index,
            "concurrency": s.concurrency,
            "branch_plans": branch_plans,
        })
        # IR Plan には parallel を直接落とさない (JobManager が special handle)
        return []

    # 未対応 step (理論上は到達不可、schema discriminator で reject される)
    ctx.add_error(
        "unsupported_step_type",
        f"未対応の step type: {getattr(s, 'type', '?')}",
        step_index=step_index,
    )
    return []


# ============================================================
# Public API
# ============================================================


def validate_and_compile(
    plan_dict: dict[str, Any],
    session_mgr: SessionManager,
    system_config: SystemConfig,
) -> CompiledPlan:
    """JSON dict (LLM 出力) を validate + compile。

    返り値の `CompiledPlan.valid` で成否を判定。
    `errors` が空でない場合は valid=False。warnings は補助情報。
    """
    # schema validation
    try:
        plan = ExperimentPlan(**plan_dict)
    except Exception as e:
        return CompiledPlan(
            valid=False,
            errors=[{
                "error_class": "schema_invalid",
                "message": str(e),
                "path": "$",
            }],
        )

    ctx = _Context(plan, session_mgr, system_config)

    # v0.8.0.1: parallel placement 制約 ── top-level steps の末尾 1 回のみ許可。
    # 中間に parallel を置くと前後の step との実行順序が曖昧になるため、
    # MVP では「最後の step に 1 つだけ」を validation で強制する。
    # body 内 (sweep の中など) の parallel も MVP では不可。
    parallel_indices = [
        i for i, s in enumerate(plan.steps) if isinstance(s, DSLParallelStep)
    ]
    if parallel_indices:
        last_idx = len(plan.steps) - 1
        if len(parallel_indices) > 1:
            ctx.add_error(
                "parallel_placement",
                f"parallel step は 1 plan に 1 つだけ許可されます "
                f"(検出: index {parallel_indices})",
                step_index=parallel_indices[1],
                recommended_next_actions=[
                    {"action": "split_plan",
                     "reason": "複数 parallel を使う場合は plan を分割してください"},
                ],
            )
        elif parallel_indices[0] != last_idx:
            ctx.add_error(
                "parallel_placement",
                f"parallel step は top-level steps の末尾 (index {last_idx}) に "
                f"配置してください (現在 index {parallel_indices[0]})。"
                f"末尾以降に他の step を置けません",
                step_index=parallel_indices[0],
                recommended_next_actions=[
                    {"action": "move_parallel_to_end",
                     "reason": "parallel の前段に置きたい step は parallel.branches の "
                               "各 branch 先頭に複製してください"},
                ],
            )

    ir_steps: list[IRStep] = []
    for i, s in enumerate(plan.steps):
        ctx.path = f"steps[{i}]"
        ir_steps.extend(_convert_step(ctx, s, i, dict(plan.variables)))
        ctx.total_expanded_steps += 1

    if ctx.errors:
        return CompiledPlan(
            valid=False,
            errors=ctx.errors,
            warnings=ctx.warnings,
            summary=_make_summary(ctx, plan),
            rendered_steps=list(ctx.rendered_steps),
            resolved_instruments=dict(ctx.resolved),
        )

    main_plan = Plan(
        name=plan.name or "experiment",
        steps=ir_steps,
        required_resources=sorted(ctx.required_resources),
        metadata={
            "dsl_version": plan.dsl_version,
            "rendered_step_count": len(ctx.rendered_steps),
        },
    )

    return CompiledPlan(
        valid=True,
        errors=[],
        warnings=ctx.warnings,
        summary=_make_summary(ctx, plan),
        main_plan=main_plan,
        parallel_groups=ctx.parallel_groups,
        has_safe_shutdown=ctx.has_safe_shutdown,
        safe_shutdown_targets=ctx.safe_shutdown_targets,
        resolved_instruments=dict(ctx.resolved),
        rendered_steps=list(ctx.rendered_steps),
    )


def _make_summary(ctx: _Context, plan: ExperimentPlan) -> dict[str, Any]:
    return {
        "dsl_version": plan.dsl_version,
        "name": plan.name,
        "step_count_dsl": len(plan.steps),
        "step_count_expanded": ctx.total_expanded_steps,
        "rendered_step_count": len(ctx.rendered_steps),
        "required_resources": sorted(ctx.required_resources),
        "resolved_instruments": dict(ctx.resolved),
        "estimated_duration_s": round(ctx.estimated_duration_s, 2),
        "uses_verify": ctx.uses_verify_count > 0,
        "uses_verify_count": ctx.uses_verify_count,
        "uses_polling": ctx.uses_polling,
        "has_safe_shutdown": ctx.has_safe_shutdown,
        "has_parallel": len(ctx.parallel_groups) > 0,
        "parallel_group_count": len(ctx.parallel_groups),
    }
