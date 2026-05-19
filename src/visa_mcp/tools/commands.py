from __future__ import annotations
from fastmcp import FastMCP
from visa_mcp.session_manager import SessionManager
from visa_mcp.visa_manager import VisaError
from visa_mcp.utils.param_validator import validate_and_build_scpi, ParameterValidationError
from visa_mcp import safety as sf


def register_tools(mcp: FastMCP, session_mgr: SessionManager) -> None:

    @mcp.tool()
    async def query_instrument(
        resource_name: str,
        command: str,
        timeout_ms: int = 5000,
    ) -> dict:
        """
        任意の SCPI クエリコマンドを送信し応答を返す（汎用パススルー）。
        resource_name: VISA リソース文字列（例: "GPIB0::1::INSTR"）
        command: SCPI コマンド文字列（例: "*IDN?", "MEASure:VOLTage?"）
        timeout_ms: タイムアウト（ミリ秒）
        """
        session = session_mgr.get_session(resource_name)
        conn = session.definition.connection if (session and session.definition) else None

        try:
            response = await session_mgr._visa.query(
                resource_name,
                command,
                timeout_ms=timeout_ms,
                read_termination=conn.read_termination if conn else "\n",
                write_termination=conn.write_termination if conn else "\n",
            )
            return {"success": True, "data": {"response": response, "command": command}}
        except VisaError as e:
            return {"success": False, "error": type(e).__name__, "message": str(e), "resource_name": resource_name}

    @mcp.tool()
    async def send_command(
        resource_name: str,
        command: str,
        timeout_ms: int = 5000,
    ) -> dict:
        """
        応答を必要としない SCPI コマンドを送信する（汎用パススルー）。
        resource_name: VISA リソース文字列
        command: SCPI コマンド文字列（例: "*RST", "AUTOSet EXECute"）
        timeout_ms: タイムアウト（ミリ秒）
        """
        session = session_mgr.get_session(resource_name)
        conn = session.definition.connection if (session and session.definition) else None

        try:
            await session_mgr._visa.write(
                resource_name,
                command,
                timeout_ms=timeout_ms,
                read_termination=conn.read_termination if conn else "\n",
                write_termination=conn.write_termination if conn else "\n",
            )
            return {"success": True, "data": {"command": command, "resource_name": resource_name}}
        except VisaError as e:
            return {"success": False, "error": type(e).__name__, "message": str(e), "resource_name": resource_name}

    @mcp.tool()
    async def execute_named_command(
        resource_name: str,
        command_name: str,
        parameters: dict = {},
        override_safety: bool = False,
        override_reason: str = "",
    ) -> dict:
        """
        YAML 定義に登録された名前付きコマンドを実行する。
        パラメータを型・範囲検証してから SCPI 文字列を組み立てて送信する。
        さらに YAML の safety セクションに基づく安全制約検証を行う。

        resource_name: VISA リソース文字列
        command_name: YAML 定義のコマンドキー（例: "measure_voltage"）
        parameters: コマンドパラメータ辞書（例: {"channel": 1}）
        override_safety: 安全警告を無視して実行する場合 True（advisory モード時のみ有効）
        override_reason: override する理由（override_safety=True の場合は必須）
        """
        session = session_mgr.get_session(resource_name)
        if session is None:
            return {
                "success": False,
                "error": "SessionNotFound",
                "message": f"{resource_name} はまだ識別されていません。identify_instrument を先に実行してください。",
            }
        if session.definition is None:
            return {
                "success": False,
                "error": "NoDefinitionFound",
                "message": f"{resource_name} の YAML 定義が見つかりません。query_instrument で直接コマンドを送信してください。",
            }

        cmd_def = session.definition.commands.get(command_name)
        if cmd_def is None:
            available = list(session.definition.commands.keys())
            return {
                "success": False,
                "error": "CommandNotFound",
                "message": f"コマンド '{command_name}' は定義されていません。利用可能: {available}",
            }

        # 安全制約チェック (v0.2.0)
        mode = sf.get_safety_mode()
        violations = sf.validate(
            session.definition,
            command_name,
            parameters,
            session_history=session.command_history,
        )
        action, msg = sf.decide_action(violations, mode, override_safety, override_reason or None)

        if action in ("block_advisory", "block_strict"):
            sf.write_audit(
                resource_name, command_name, parameters, violations,
                action=action, mode=mode,
                override_safety=override_safety, override_reason=override_reason or None,
            )
            resp = sf.make_warning_response(violations, action, mode)
            resp["message"] = msg
            return resp

        # advisory モードで override 経由 or permissive モードでの違反は監査ログに記録
        if violations:
            sf.write_audit(
                resource_name, command_name, parameters, violations,
                action="proceed_with_override" if override_safety else "proceed_permissive",
                mode=mode,
                override_safety=override_safety, override_reason=override_reason or None,
            )

        try:
            scpi = validate_and_build_scpi(cmd_def, parameters)
        except ParameterValidationError as e:
            return {"success": False, "error": "ParameterValidationError", "message": str(e)}

        conn = session.definition.connection
        timeout_ms = cmd_def.timeout_ms or conn.default_timeout_ms

        try:
            if cmd_def.type == "query":
                raw = await session_mgr._visa.query(
                    resource_name, scpi, timeout_ms=timeout_ms,
                    read_termination=conn.read_termination,
                    write_termination=conn.write_termination,
                )
                result = _cast_response(raw, cmd_def.returns.type)
                session.record_command(command_name)
                return {
                    "success": True,
                    "data": {
                        "command_name": command_name,
                        "scpi_sent": scpi,
                        "raw_response": raw,
                        "value": result,
                        "unit": cmd_def.returns.unit,
                    },
                    "safety_violations_overridden": list(violations) if violations else [],
                }
            else:
                await session_mgr._visa.write(
                    resource_name, scpi, timeout_ms=timeout_ms,
                    read_termination=conn.read_termination,
                    write_termination=conn.write_termination,
                )
                session.record_command(command_name)
                return {
                    "success": True,
                    "data": {"command_name": command_name, "scpi_sent": scpi},
                    "safety_violations_overridden": list(violations) if violations else [],
                }
        except VisaError as e:
            return {"success": False, "error": type(e).__name__, "message": str(e), "resource_name": resource_name}


def _cast_response(raw: str, return_type: str) -> object:
    raw = raw.strip()
    try:
        if return_type == "integer":
            return int(float(raw))
        elif return_type == "float":
            return float(raw)
        elif return_type == "boolean":
            return raw not in ("0", "OFF", "false", "FALSE")
    except (ValueError, TypeError):
        pass
    return raw
