"""
v0.8.0: Experiment DSL MCP ツール (4 個)

- validate_experiment_plan: 構文 + resource + safety + verify 検証
- dry_run_plan: 実機 I/O 無しの rendered + safety + verify summary
- start_experiment_job: validate → compile → persist → Job 実行
- save_experiment_template: 再利用可能テンプレート保存
"""
from __future__ import annotations
import logging
import uuid as _uuid

from fastmcp import FastMCP

from visa_mcp.dsl import CURRENT_DSL_VERSION
from visa_mcp.dsl.compiler import validate_and_compile
from visa_mcp.job import JobManager
from visa_mcp.job.state_machine import JobStatus
from visa_mcp.response_envelope import make_envelope, make_error
from visa_mcp.session_manager import SessionManager

logger = logging.getLogger(__name__)


def register_tools(
    mcp: FastMCP,
    session_mgr: SessionManager,
    job_mgr: JobManager,
) -> None:

    @mcp.tool()
    async def validate_experiment_plan(plan: dict) -> dict:
        """LLM が生成した DSL plan を検証する (v0.8.0)

        実機通信は行わず、以下を検証:
          - JSON schema (dsl_version, step type 等)
          - instrument / binding 解決
          - command 存在 + type + parameter 範囲
          - safety constraints (strict/advisory mode 連携)
          - verify 設定 (readback_command の存在 / query 型)
          - polling_safe ヒント
          - sweep 展開サイズ / parallel concurrency 上限

        返り値の data:
          - valid: bool
          - summary: { step_count_dsl, step_count_expanded, required_resources,
                       resolved_instruments, estimated_duration_s, uses_verify,
                       uses_polling, has_safe_shutdown, has_parallel }
        errors[]: validation 失敗時の構造化エラー (各 error に
                  recommended_next_actions を含む)
        warnings[]: 非致命的な注意事項 (例: polling_safe_false, raw_resource_used)
        """
        if not isinstance(plan, dict):
            return make_envelope(
                "error",
                errors=[make_error("validation",
                    "plan は JSON object である必要があります", recoverable=False)],
            )
        try:
            compiled = validate_and_compile(plan, session_mgr, job_mgr.system_config)
        except Exception as e:
            logger.exception("validate_experiment_plan 内部エラー")
            return make_envelope(
                "error",
                errors=[make_error("internal", str(e), recoverable=False)],
            )
        if compiled.valid:
            return make_envelope("ok", data={
                "valid": True,
                "summary": compiled.summary,
                "warnings": compiled.warnings,
            })
        else:
            # 構造化 errors を envelope errors にマップ
            envelope_errors = [
                make_error(
                    e.get("error_class", "validation"),
                    e.get("message", "?"),
                    recoverable=True,
                    recommended_next_actions=e.get("recommended_next_actions"),
                    details={k: v for k, v in e.items()
                             if k not in ("error_class", "message", "recommended_next_actions")},
                )
                for e in compiled.errors
            ]
            return make_envelope(
                "error",
                data={
                    "valid": False,
                    "summary": compiled.summary,
                    "warnings": compiled.warnings,
                },
                errors=envelope_errors,
            )

    @mcp.tool()
    async def dry_run_plan(plan: dict) -> dict:
        """DSL plan を実機 I/O 無しで「実行予定」として展開する (v0.8.0)

        validate_experiment_plan の結果に加え、各 command step の
        rendered SCPI / safety / verify 予定を返す。

        実機通信:
          - 一切行わない (*IDN? / state_query / verify readback も呼ばない)
          - YAML 定義 + parameter renderer + safety_validator のみ使用
        """
        if not isinstance(plan, dict):
            return make_envelope(
                "error",
                errors=[make_error("validation", "plan は JSON object", recoverable=False)],
            )
        try:
            compiled = validate_and_compile(plan, session_mgr, job_mgr.system_config)
        except Exception as e:
            logger.exception("dry_run_plan 内部エラー")
            return make_envelope(
                "error",
                errors=[make_error("internal", str(e), recoverable=False)],
            )
        # rendered_steps は compiler._Context に蓄積されているが、CompiledPlan には
        # 直接含めていない。compile を再実行して rendered を取得する代わりに、
        # ここでは compile_to_rendered() を追加する代わりに validate_and_compile に
        # rendered_steps を含めるよう拡張する設計が良い。
        # MVP では再 compile せず、compile 内部の summary + warnings を返す。
        rendered_steps = _extract_rendered_from_compile(plan, session_mgr, job_mgr)

        envelope_status = "ok" if compiled.valid else "error"
        envelope_errors = (
            [
                make_error(
                    e.get("error_class", "validation"),
                    e.get("message", "?"),
                    recoverable=True,
                    details={k: v for k, v in e.items()
                             if k not in ("error_class", "message")},
                )
                for e in compiled.errors
            ]
            if not compiled.valid else None
        )
        return make_envelope(
            envelope_status,
            data={
                "dry_run": True,
                "valid": compiled.valid,
                "summary": compiled.summary,
                "warnings": compiled.warnings,
                "rendered_steps": rendered_steps,
                "note": "実機通信は一切行っていません",
            },
            errors=envelope_errors,
        )

    @mcp.tool()
    async def start_experiment_job(
        plan: dict,
        owner: str = "",
        override_safety: bool = False,
        override_reason: str = "",
        job_timeout_s: float = 0.0,
        queue_policy: str = "queue",
    ) -> dict:
        """DSL plan を validate + compile + 実行する (v0.8.0)

        plan: ExperimentPlan に準拠した JSON
        owner / override_safety / override_reason: 既存 Job API と同様
        job_timeout_s: 0 で 24h default、それ以外は秒指定
        queue_policy: "queue" / "reject_if_busy"

        Job として登録された後、`get_job_status` / `get_job_result` /
        `cancel_job` で操作可能。original plan は experiment_plans テーブルに
        永続化される。
        """
        if queue_policy not in ("queue", "reject_if_busy"):
            return make_envelope(
                "error",
                errors=[make_error("validation",
                    f"queue_policy: {queue_policy}", recoverable=False)],
            )
        try:
            rec = await job_mgr.start_experiment_job(
                plan_dict=plan or {},
                owner=owner,
                override_safety=override_safety,
                override_reason=override_reason,
                job_timeout_s=(job_timeout_s if job_timeout_s > 0 else None),
                queue_policy=queue_policy,
            )
        except Exception as e:
            logger.exception("start_experiment_job 失敗")
            return make_envelope(
                "error",
                errors=[make_error("internal", str(e), recoverable=False)],
            )
        data = {
            "job_id": rec.job_id,
            "status": rec.status.value,
            "name": (plan or {}).get("name", ""),
            "dsl_version": (plan or {}).get("dsl_version", CURRENT_DSL_VERSION),
            "created_at": rec.created_at,
        }
        # plan_id を補完 (parameters_json に含まれている)
        if rec.parameters and rec.parameters.get("plan_id"):
            data["plan_id"] = rec.parameters["plan_id"]
        try:
            sch = await job_mgr.scheduler.get_scheduling_info(rec.job_id)
            sch["queue_policy"] = queue_policy
            data["scheduling"] = sch
        except Exception:
            pass

        # 失敗時は validation_errors を errors[] に
        envelope_errors = None
        if rec.status == JobStatus.FAILED and rec.result:
            verr = rec.result.get("validation_errors") or []
            if verr:
                envelope_errors = [
                    make_error(
                        e.get("error_class", "validation"),
                        e.get("message", "?"),
                        recoverable=True,
                        details={k: v for k, v in e.items()
                                 if k not in ("error_class", "message")},
                    ) for e in verr
                ]

        return make_envelope(
            "ok" if rec.status != JobStatus.FAILED else "error",
            data=data,
            errors=envelope_errors or (
                [make_error(
                    rec.error_class or "validation",
                    rec.last_step_summary or "failed",
                    recoverable=False,
                )] if rec.status == JobStatus.FAILED else None
            ),
            job_id=rec.job_id,
        )

    @mcp.tool()
    async def save_experiment_template(
        name: str,
        plan: dict,
        description: str = "",
    ) -> dict:
        """experiment_templates テーブルに再利用可能 template を保存 / 上書き (v0.8.0)

        保存前に validate_experiment_plan を実行 (実行はしない)。
        validation 失敗時は保存しない。

        name: テンプレート名 (主キー、重複は上書き)
        plan: ExperimentPlan JSON
        description: 説明 (任意)
        """
        if not name or not name.strip():
            return make_envelope(
                "error",
                errors=[make_error("validation", "name が必須", recoverable=False)],
            )
        if not isinstance(plan, dict):
            return make_envelope(
                "error",
                errors=[make_error("validation", "plan は JSON object", recoverable=False)],
            )
        try:
            compiled = validate_and_compile(plan, session_mgr, job_mgr.system_config)
        except Exception as e:
            return make_envelope(
                "error",
                errors=[make_error("internal", str(e), recoverable=False)],
            )
        if not compiled.valid:
            return make_envelope(
                "error",
                data={"valid": False, "warnings": compiled.warnings},
                errors=[
                    make_error(
                        e.get("error_class", "validation"),
                        e.get("message", "?"),
                        recoverable=True,
                        details={k: v for k, v in e.items()
                                 if k not in ("error_class", "message")},
                    ) for e in compiled.errors
                ],
            )
        try:
            job_mgr.store.save_experiment_template(
                name=name.strip(),
                dsl_version=plan.get("dsl_version", CURRENT_DSL_VERSION),
                plan=plan,
                description=description,
            )
        except Exception as e:
            return make_envelope(
                "error",
                errors=[make_error("internal", str(e), recoverable=False)],
            )
        return make_envelope("ok", data={
            "name": name.strip(),
            "dsl_version": plan.get("dsl_version", CURRENT_DSL_VERSION),
            "description": description,
            "summary": compiled.summary,
            "warnings": compiled.warnings,
        })


def _extract_rendered_from_compile(plan_dict, session_mgr, job_mgr):
    """dry_run 用に rendered_steps を取得する。

    現状の compiler は CompiledPlan に rendered_steps を含めていないため、
    内部 _Context を直接使う薄いヘルパ。本実装では再 compile して
    rendered_steps を抽出する。
    """
    from visa_mcp.dsl.compiler import _Context, _convert_step
    from visa_mcp.dsl.schema import ExperimentPlan

    try:
        plan = ExperimentPlan(**plan_dict)
    except Exception:
        return []
    ctx = _Context(plan, session_mgr, job_mgr.system_config)
    for i, s in enumerate(plan.steps):
        ctx.path = f"steps[{i}]"
        try:
            _convert_step(ctx, s, i, dict(plan.variables))
        except Exception:
            pass
        ctx.total_expanded_steps += 1
    return ctx.rendered_steps
