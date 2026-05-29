from __future__ import annotations
import logging
import os
import sys
from pathlib import Path

from fastmcp import FastMCP

from visa_mcp.instrument_registry import InstrumentRegistry
from visa_mcp.visa_manager import VisaManager
from visa_mcp.session_manager import SessionManager
from visa_mcp.session_store import SessionStore, default_session_store_path
from visa_mcp.tools import discovery, commands, pdf_extractor, info, recipes
from visa_mcp.tools import jobs as jobs_tools
from visa_mcp.tools import waits as waits_tools
from visa_mcp.tools import groups as groups_tools
from visa_mcp.tools import monitor as monitor_tools
from visa_mcp.tools import dsl as dsl_tools
from visa_mcp.tools import observation as observation_tools
from visa_mcp.tools import export as export_tools
from visa_mcp.tools import audit as audit_tools
from visa_mcp.job import JobManager
from visa_mcp.system_config import SystemConfig
from visa_mcp.bus_manager import BusManager
from visa_mcp import safety as sf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)

logger = logging.getLogger(__name__)

# v2.3.1: resolver を `visa_mcp.instruments_dir` に切り出した。
# Codex v2.2.1 レビュー指摘: `from visa_mcp import server` すると
# JobManager / JobStore まで初期化されるため、resolver の単体テストが
# 副作用を持っていた。新 module は import しても外部状態を変更しない。
# 後方互換のためここから `_resolve_instruments_dir()` を再エクスポート。
from visa_mcp.instruments_dir import (
    resolve_instruments_dir as _resolve_impl,
)


def _resolve_instruments_dir() -> Path:
    """Backward-compatible wrapper.
    v2.3.1+ は `visa_mcp.instruments_dir.resolve_instruments_dir` を
    直接 import するのが推奨。"""
    return _resolve_impl(__file__)


INSTRUMENTS_DIR = _resolve_instruments_dir()

_safety_mode = sf.get_safety_mode()

mcp = FastMCP(
    name="visa-instrument-controller",
    instructions=(
        "PyVISA 経由で GPIB / USB / シリアル計測器を制御する MCP サーバーです。\n"
        f"安全モード: {_safety_mode} (環境変数 VISA_MCP_SAFETY_MODE で変更可)\n"
        "\n"
        "## 基本フロー\n"
        "1. list_resources で接続機器を列挙する\n"
        "2. identify_instrument または identify_all_instruments で機器を識別する\n"
        "   *IDN? 非対応の古い機器は bind_definition で手動バインドする\n"
        "3. get_instrument_info で機器仕様・安全制約を確認する (推奨)\n"
        "4. list_commands で利用可能なコマンドを確認する\n"
        "5. validate_operation で実行前に安全性を確認する (任意)\n"
        "6. execute_named_command で型安全にコマンドを実行する\n"
        "\n"
        "## 長時間 / 複数 step / wait を含む recipe\n"
        "7. wait step を含む recipe や数十秒以上かかる recipe は execute_recipe ではなく\n"
        "   **start_recipe_job** を使うこと。LLM 側のツール呼び出しがブロックされない。\n"
        "   進捗は get_job_status、完了後の詳細は get_job_result で取得する。\n"
        "8. Job 終端が timeout / interrupted / failed / cancelled の場合は、\n"
        "   get_job_result の errors[].recommended_next_actions を参照する。\n"
        "9. 同一機器に対する Job は queued で順番待ちになる (queue_policy='queue' デフォルト)。\n"
        "   queue_policy='reject_if_busy' で busy 時に即 failed を返すことも可能。\n"
        "10. Job を停止したい場合は cancel_job(cancel_mode='safe_shutdown') を推奨\n"
        "    (機器の安全停止シーケンスを実行してから停止する)。\n"
        "\n"
        "## 実験計画 DSL (v0.8.0+)\n"
        "11. 複数機器・sweep・parallel を含む実験計画は、以下の順で実行する:\n"
        "    (a) validate_experiment_plan(plan) で構文・resource・safety を検証\n"
        "    (b) dry_run_plan(plan) で送信予定 SCPI と verify 予定を確認 (実機ノータッチ)\n"
        "    (c) start_experiment_job(plan) で Job として実行 (進捗は get_job_status)\n"
        "12. いきなり start_experiment_job を呼ばず、(a)(b) を通すことを強く推奨。\n"
        "    dry_run_plan は実機 I/O を一切発生させないため、安全に予行確認できる。\n"
        "13. 再利用したい計画は save_experiment_template(name, plan) で保存可能。\n"
        "\n"
        "## raw コマンド (危険、通常使用しない)\n"
        "任意 SCPI の直接送信は VISA_MCP_ENABLE_RAW_COMMANDS=1 かつ non-strict モード時のみ\n"
        "unsafe_send_command / unsafe_query_instrument が登録される。\n"
        "これらは YAML 安全制約を介さないため、原則として使用しない。\n"
        "\n"
        "## 安全制約と override\n"
        "安全制約違反時は警告レスポンスが返り、必要に応じて override_safety=True で\n"
        "明示的に override 可能 (advisory モードのみ、override_reason 必須)。\n"
        "**override は人間の事前承認がある場合のみ。LLM が単独で判断してはいけない**"
    ),
)

visa_mgr = VisaManager()
registry = InstrumentRegistry(INSTRUMENTS_DIR)
# v2.3.0: bindings 永続化 (process 再起動耐性)。
# `$VISA_MCP_SESSION_STORE` で path 上書き可。
_session_store = SessionStore(default_session_store_path())
session_mgr = SessionManager(visa_mgr, registry, store=_session_store)
logger.info(
    "session store: path=%s, restored=%d",
    _session_store.path, len(session_mgr.list_sessions()),
)
# v2.1.4: 起動時に instrument YAML のロード状況を可視化
try:
    _defs = registry.list_definitions()
    logger.info(
        "instrument registry resolved: dir=%s, definitions=%d",
        INSTRUMENTS_DIR, len(_defs),
    )
    if not _defs:
        logger.warning(
            "instrument registry に YAML 定義がありません。"
            " VISA_MCP_INSTRUMENTS_DIR を設定するか、"
            " builtin_instruments を確認してください: %s", INSTRUMENTS_DIR,
        )
except Exception as _e:
    logger.warning("instrument registry list_definitions が失敗: %s", _e)
# v0.6.0: SystemConfig (instruments/_system.yaml) を読み込み
_system_config = SystemConfig.from_yaml(INSTRUMENTS_DIR / "_system.yaml")
_bus_mgr = BusManager(_system_config)
visa_mgr.set_bus_manager(_bus_mgr)
job_mgr = JobManager(visa_mgr, session_mgr, system_config=_system_config)

discovery.register_tools(mcp, session_mgr)
commands.register_tools(mcp, session_mgr)
# v0.7.0: info に visa / job_mgr を渡すと describe_instrument / get_state /
# get_last_measurement も登録される
info.register_tools(mcp, session_mgr, visa=visa_mgr, job_mgr=job_mgr)
recipes.register_tools(mcp, session_mgr)
jobs_tools.register_tools(mcp, job_mgr)
waits_tools.register_tools(mcp, job_mgr)
groups_tools.register_tools(mcp, job_mgr)
monitor_tools.register_tools(mcp, job_mgr)
# v0.8.0: Experiment DSL ツール
dsl_tools.register_tools(mcp, session_mgr, job_mgr)
# v0.8.2: Observation ツール
observation_tools.register_tools(mcp, job_mgr)
# v0.9.1: 測定結果 export ツール (experimental)
export_tools.register_tools(mcp, job_mgr)
# v0.9.3: audit / locks ツール (experimental)
audit_tools.register_tools(mcp, job_mgr)
pdf_extractor.register_tools(mcp)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
