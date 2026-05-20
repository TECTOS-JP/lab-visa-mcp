from __future__ import annotations
import logging
import sys
from pathlib import Path

from fastmcp import FastMCP

from visa_mcp.instrument_registry import InstrumentRegistry
from visa_mcp.visa_manager import VisaManager
from visa_mcp.session_manager import SessionManager
from visa_mcp.tools import discovery, commands, pdf_extractor, info, recipes
from visa_mcp import safety as sf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)

INSTRUMENTS_DIR = Path(__file__).parent.parent.parent / "instruments"

_safety_mode = sf.get_safety_mode()

mcp = FastMCP(
    name="visa-instrument-controller",
    instructions=(
        "PyVISA 経由で GPIB / USB / シリアル計測器を制御する MCP サーバーです。\n"
        f"安全モード: {_safety_mode} (環境変数 VISA_MCP_SAFETY_MODE で変更可)\n"
        "使い方:\n"
        "1. list_resources で接続機器を列挙する\n"
        "2. identify_instrument または identify_all_instruments で機器を識別する\n"
        "   *IDN? 非対応の古い機器は bind_definition で手動バインドする\n"
        "3. get_instrument_info で機器仕様・安全制約を確認する (推奨)\n"
        "4. list_commands で利用可能なコマンドを確認する\n"
        "5. validate_operation で実行前に安全性を確認する (任意)\n"
        "6. execute_named_command で型安全にコマンドを実行する\n"
        "   任意 SCPI の直接送信は通常使用しない。\n"
        "   VISA_MCP_ENABLE_RAW_COMMANDS=1 かつ non-strict モード時のみ\n"
        "   unsafe_send_command / unsafe_query_instrument が登録される。\n"
        "   これらは YAML 安全制約を介さないため、原則として使用しない。\n"
        "安全制約違反時は警告レスポンスが返り、必要に応じて override_safety=True で\n"
        "明示的に override 可能 (advisory モードのみ、override_reason 必須)"
    ),
)

visa_mgr = VisaManager()
registry = InstrumentRegistry(INSTRUMENTS_DIR)
session_mgr = SessionManager(visa_mgr, registry)

discovery.register_tools(mcp, session_mgr)
commands.register_tools(mcp, session_mgr)
info.register_tools(mcp, session_mgr)
recipes.register_tools(mcp, session_mgr)
pdf_extractor.register_tools(mcp)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
