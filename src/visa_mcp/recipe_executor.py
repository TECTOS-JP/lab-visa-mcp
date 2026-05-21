"""
Recipe 実行エンジン (v0.5.0-rc1 で IR ベースに refactor)

設計:
- YAML の `RecipeDefinition` を内部 IR (`experiment_ir.Plan`) に変換
- Plan を `execute_plan()` が walk して実行
- v0.5.0-rc1: CommandStep (従来の機器コマンド) + WaitStep (asyncio.sleep) のみ
- v0.5.1 以降で wait_for_* 系 step が追加されても execute_plan のディスパッチを増やすだけ

外部 API (`execute_recipe`) の戻り値形式は v0.3.0 までと互換性を維持:
- `{"success": bool, "recipe": str, "steps_executed": [...], "step_count": N}`
- 失敗時は `{"success": False, ..., "halted_at_step": idx}`

新しい標準 envelope (`response_envelope.make_envelope`) は v0.5.0+ で新規追加される
MCP ツール (Job 系等) で採用する。既存 `execute_recipe` ツールは後方互換のため従来形式。
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any

from .experiment_ir import CommandStep, Plan, Step, WaitStep
from .models.instrument_def import InstrumentDefinition, RecipeDefinition, RecipeStep
from .utils.expression import resolve_arg, ExpressionError
from .utils.param_validator import validate_and_build_scpi, ParameterValidationError
from .visa_manager import VisaManager, VisaError
from .session_manager import InstrumentSession
from . import safety as sf

logger = logging.getLogger(__name__)


# ============================================================
# Recipe → IR Plan 変換
# ============================================================

def recipe_to_plan(recipe: RecipeDefinition, variables: dict[str, Any]) -> Plan:
    """
    YAML の RecipeDefinition + 変数辞書 → IR Plan に変換する。
    args 内の `$var` / `$var * 1.1` 等の式は事前に評価して具体値にする。

    式評価エラーが起きると ExpressionError が propagate するので、呼び出し側で捕捉する。
    """
    plan_steps: list[Step] = []
    for rs in recipe.steps:
        if rs.step_type == "wait":
            seconds_raw = rs.wait["seconds"]
            seconds = float(resolve_arg(seconds_raw, variables))
            plan_steps.append(WaitStep(
                seconds=seconds,
                description=rs.description,
            ))
        else:  # command
            resolved_args = {k: resolve_arg(v, variables) for k, v in rs.args.items()}
            plan_steps.append(CommandStep(
                command=rs.command or "",
                args=resolved_args,
                result_as=rs.result_as,
                description=rs.description,
            ))

    return Plan(
        name=(recipe.description[:80] if recipe.description else "recipe"),
        parameters=dict(variables),
        steps=plan_steps,
    )


# ============================================================
# Plan executor (各 Step type を dispatch)
# ============================================================

async def execute_plan(
    visa: VisaManager,
    session: InstrumentSession,
    plan: Plan,
    recipe_name: str | None = None,
    override_safety: bool = False,
    override_reason: str = "",
) -> dict:
    """
    IR Plan を実行する。返り値の形式は execute_recipe と同じ (後方互換)。
    """
    if session.definition is None:
        return {
            "success": False,
            "recipe": recipe_name or plan.name,
            "error": "NoDefinitionFound",
            "message": "機器定義が読み込まれていません",
            "steps_executed": [],
        }

    step_results: list[dict] = []

    for idx, step in enumerate(plan.steps):
        if isinstance(step, WaitStep):
            result = await _execute_wait_step(step)
        elif isinstance(step, CommandStep):
            result = await _execute_command_step(
                visa, session, step,
                override_safety=override_safety,
                override_reason=override_reason,
            )
        else:
            # 将来 step type 追加時に備えた fallback
            result = {
                "success": False,
                "error": "UnsupportedStepType",
                "step_type": getattr(step, "type", "unknown"),
                "message": "未対応のステップ型です",
            }

        step_results.append({"step": idx, **result})

        if not result.get("success", False):
            return {
                "success": False,
                "recipe": recipe_name or plan.name,
                "steps_executed": step_results,
                "halted_at_step": idx,
            }

    return {
        "success": True,
        "recipe": recipe_name or plan.name,
        "steps_executed": step_results,
        "step_count": len(step_results),
    }


# ============================================================
# 個別 step 実行
# ============================================================

async def _execute_wait_step(step: WaitStep) -> dict:
    """単純な秒待機。Job 化されていない場合はこの間 await でブロックする (v0.5.0-rc1)。"""
    await asyncio.sleep(step.seconds)
    return {
        "step_type": "wait",
        "seconds": step.seconds,
        "success": True,
    }


async def _execute_command_step(
    visa: VisaManager,
    session: InstrumentSession,
    step: CommandStep,
    override_safety: bool,
    override_reason: str,
) -> dict:
    """機器コマンドを 1 回実行。安全制約 + パラメータ検証 + SCPI 送信。"""
    cmd_def = session.definition.commands.get(step.command)
    if cmd_def is None:
        return {
            "command": step.command,
            "success": False,
            "error": "CommandNotFound",
            "message": f"コマンド '{step.command}' が定義されていません",
        }

    resolved_args = step.args  # recipe_to_plan で解決済み
    mode = sf.get_safety_mode()

    # 安全制約検証
    violations = sf.validate(
        session.definition, step.command, resolved_args,
        session_history=session.command_history,
    )
    action, msg = sf.decide_action(violations, mode, override_safety, override_reason or None)

    if action in ("block_advisory", "block_strict"):
        sf.write_audit(
            session.resource_name, step.command, resolved_args, violations,
            action=action, mode=mode,
            override_safety=override_safety, override_reason=override_reason or None,
        )
        return {
            "command": step.command,
            "success": False,
            "blocked_by_safety": True,
            "violations": list(violations),
            "action": action,
            "message": msg,
        }

    if violations:
        sf.write_audit(
            session.resource_name, step.command, resolved_args, violations,
            action="proceed_with_override" if override_safety else "proceed_permissive",
            mode=mode,
            override_safety=override_safety, override_reason=override_reason or None,
        )

    # パラメータ検証 + SCPI 組み立て
    try:
        scpi = validate_and_build_scpi(cmd_def, resolved_args)
    except ParameterValidationError as e:
        return {
            "command": step.command,
            "success": False,
            "error": "ParameterValidationError",
            "message": str(e),
        }

    conn = session.definition.connection
    timeout_ms = cmd_def.timeout_ms or conn.default_timeout_ms

    try:
        if cmd_def.type == "query":
            raw = await visa.query(
                session.resource_name, scpi, timeout_ms=timeout_ms,
                read_termination=conn.read_termination,
                write_termination=conn.write_termination,
            )
            session.record_command(step.command)
            return {
                "command": step.command,
                "args": resolved_args,
                "scpi_sent": scpi,
                "raw_response": raw,
                "success": True,
            }
        else:
            await visa.write(
                session.resource_name, scpi, timeout_ms=timeout_ms,
                read_termination=conn.read_termination,
                write_termination=conn.write_termination,
            )
            session.record_command(step.command)
            return {
                "command": step.command,
                "args": resolved_args,
                "scpi_sent": scpi,
                "success": True,
            }
    except VisaError as e:
        return {
            "command": step.command,
            "success": False,
            "error": type(e).__name__,
            "message": str(e),
        }


# ============================================================
# 公開エントリポイント (既存 API、後方互換維持)
# ============================================================

async def execute_recipe(
    visa: VisaManager,
    session: InstrumentSession,
    recipe_name: str,
    parameters: dict[str, Any] | None,
    override_safety: bool = False,
    override_reason: str = "",
) -> dict:
    """
    指定の recipe を実行する。

    v0.5.0-rc1 で内部実装を IR Plan ベースに refactor したが、戻り値形式は v0.3.0/v0.4.x と同一。
    """
    parameters = parameters or {}

    if session.definition is None:
        return {
            "success": False,
            "error": "NoDefinitionFound",
            "message": "機器定義が読み込まれていません",
        }

    recipe: RecipeDefinition | None = session.definition.recipes.get(recipe_name)
    if recipe is None:
        return {
            "success": False,
            "error": "RecipeNotFound",
            "message": f"recipe '{recipe_name}' は定義されていません",
            "available_recipes": list(session.definition.recipes.keys()),
        }

    # パラメータ検証 (簡易: 必須チェックのみ)
    for p in recipe.parameters:
        if p.required and p.name not in parameters and p.default is None:
            return {
                "success": False,
                "error": "MissingParameter",
                "message": f"必須パラメータ '{p.name}' が指定されていません",
            }
    # default 適用
    variables = dict(parameters)
    for p in recipe.parameters:
        if p.name not in variables and p.default is not None:
            variables[p.name] = p.default

    # Recipe → IR Plan 変換
    try:
        plan = recipe_to_plan(recipe, variables)
    except ExpressionError as e:
        return {
            "success": False,
            "recipe": recipe_name,
            "error": "ExpressionError",
            "message": str(e),
            "steps_executed": [],
        }

    # Plan 実行
    return await execute_plan(
        visa, session, plan,
        recipe_name=recipe_name,
        override_safety=override_safety,
        override_reason=override_reason,
    )
