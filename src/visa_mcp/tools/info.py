"""
機器情報・安全制約を LLM に提供するためのツール (v0.2.0)
"""
from __future__ import annotations
from fastmcp import FastMCP
from visa_mcp.session_manager import SessionManager
from visa_mcp.utils.param_validator import validate_and_build_scpi, ParameterValidationError
from visa_mcp import safety as sf


def register_tools(mcp: FastMCP, session_mgr: SessionManager) -> None:

    @mcp.tool()
    async def get_instrument_info(resource_name: str) -> dict:
        """
        識別済み機器の YAML 定義から、機器仕様・安全制約・推奨手順・応答フォーマットなど
        全情報を一括取得する。LLM が機器の能力と制約を把握するために使用する。

        resource_name: VISA リソース文字列
        """
        session = session_mgr.get_session(resource_name)
        if session is None or session.definition is None:
            return {
                "success": False,
                "error": "SessionNotFound",
                "message": f"{resource_name} は未識別、または YAML 定義がありません。",
            }

        d = session.definition
        return {
            "success": True,
            "data": {
                "metadata": d.metadata.model_dump(),
                "identification": d.identification.model_dump(),
                "connection": d.connection.model_dump(),
                "safety": d.safety.model_dump(),
                "specifications": d.specifications.model_dump(),
                "response_formats": {k: v.model_dump() for k, v in d.response_formats.items()},
                "command_count": len(d.commands),
                "command_names": list(d.commands.keys()),
                "safety_mode": sf.get_safety_mode(),
            },
        }

    @mcp.tool()
    async def list_safety_constraints(resource_name: str) -> dict:
        """
        指定機器の安全制約のみを抽出して返す。
        値制約 (ratings)・前提条件 (preconditions)・禁止行為 (cautions)・
        ハードウェア保護 (hardware_protections) を含む。

        resource_name: VISA リソース文字列
        """
        session = session_mgr.get_session(resource_name)
        if session is None or session.definition is None:
            return {
                "success": False,
                "error": "SessionNotFound",
                "message": f"{resource_name} は未識別、または YAML 定義がありません。",
            }
        d = session.definition
        return {
            "success": True,
            "data": {
                "instrument": d.display_name,
                "safety_mode": sf.get_safety_mode(),
                "safety": d.safety.model_dump(),
            },
        }

    @mcp.tool()
    async def validate_operation(
        resource_name: str,
        command_name: str,
        parameters: dict = {},
    ) -> dict:
        """
        実行せずに、コマンドが安全制約に違反するかを事前確認する (dry-run)。
        LLM が「これを送って大丈夫か？」を判断するために使う。

        resource_name: VISA リソース文字列
        command_name: YAML 定義のコマンドキー
        parameters: コマンドパラメータ辞書
        """
        session = session_mgr.get_session(resource_name)
        if session is None or session.definition is None:
            return {
                "success": False,
                "error": "SessionNotFound",
                "message": f"{resource_name} は未識別、または YAML 定義がありません。",
            }

        d = session.definition
        cmd_def = d.commands.get(command_name)
        if cmd_def is None:
            return {
                "success": False,
                "error": "CommandNotFound",
                "message": f"コマンド '{command_name}' は定義されていません。",
                "available_commands": list(d.commands.keys()),
            }

        # パラメータ検証
        param_errors: list[str] = []
        try:
            scpi = validate_and_build_scpi(cmd_def, parameters)
        except ParameterValidationError as e:
            param_errors.append(str(e))
            scpi = None

        # 安全制約検証
        violations = sf.validate(d, command_name, parameters, session_history=session.command_history)
        mode = sf.get_safety_mode()

        valid = not param_errors and not violations
        return {
            "success": True,
            "data": {
                "valid": valid,
                "scpi_to_send": scpi,
                "parameter_errors": param_errors,
                "safety_violations": list(violations),
                "safety_mode": mode,
                "would_block": bool(violations) and mode != "permissive",
                "can_override": bool(violations) and mode == "advisory",
            },
        }
