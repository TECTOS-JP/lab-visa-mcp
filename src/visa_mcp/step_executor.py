"""
個別 Step の実行ロジック (v0.5.0.1 で recipe_executor.py から切り出し)

各 Step 型を実機 / VISA に対して 1 回実行する関数群。
Recipe / Job / (将来の Group / DSL) executor から共通して使われる public API。

- execute_command_step: 機器の名前付きコマンドを 1 回実行 (安全制約 + パラメータ検証 + SCPI 送信)
- execute_wait_step: 単純秒待機 (cancel 非対応; Job で cancel ありの wait は job/manager.py 側に持つ)

v0.5.0.1 リファクタの動機:
recipe_executor.py 内に `_execute_*` の prefix で定義されていたが、
job/manager.py から外部 import されていたため、命名が誤解を招いていた。
public API として切り出し、import 経路を統一した。
"""
from __future__ import annotations
import asyncio
import logging

from . import safety as sf
from .experiment_ir import CommandStep, WaitStep
from .session_manager import InstrumentSession
from .utils.param_validator import validate_and_build_scpi, ParameterValidationError
from .visa_manager import VisaManager, VisaError

logger = logging.getLogger(__name__)


async def execute_wait_step(step: WaitStep) -> dict:
    """
    単純な秒待機ステップ実行。

    この関数は cancel/timeout 検出機能を持たない (素朴な asyncio.sleep)。
    Job 経由で cancel/timeout に応答する必要がある場合は
    visa_mcp.job.manager._JobRuntime と組み合わせた専用パスを使う。
    """
    await asyncio.sleep(step.seconds)
    return {
        "step_type": "wait",
        "seconds": step.seconds,
        "success": True,
    }


async def execute_command_step(
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
