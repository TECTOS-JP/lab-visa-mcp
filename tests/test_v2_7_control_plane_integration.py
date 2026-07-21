"""v2.7.0: visa-mcp serve のコントロールプレーン統合の契約テスト。

lab-executor v2.24.0 の公開 runner (`run_mcp_with_control`) へ委譲する
経路を検証する。実機や実際の MCP transport は起動せず、境界を monkeypatch
して契約 (どの関数へ何が渡るか) だけを確認する。

- serve parser が --control-port を受け付けること。
- server.main(None) が従来経路 (mcp.run) を通ること。
- server.main(port) が run_mcp_with_control へ委譲すること。
"""
from __future__ import annotations

import pytest


# ============================================================
# serve parser: --control-port
# ============================================================


def test_serve_parser_accepts_control_port():
    from lab_visa_mcp.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["serve", "--control-port", "8300"])
    assert args.command == "serve"
    assert args.control_port == 8300


def test_serve_parser_control_port_defaults_none():
    from lab_visa_mcp.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["serve"])
    assert args.control_port is None


# ============================================================
# main(None): 従来経路 (mcp.run) — 挙動不変
# ============================================================


def test_main_none_uses_legacy_stdio(monkeypatch):
    from lab_visa_mcp import server

    # env も無ければ resolve は None を返す → 従来経路。
    monkeypatch.delenv("LAB_EXECUTOR_CONTROL_PORT", raising=False)

    calls = {"run": [], "control": []}
    monkeypatch.setattr(
        server.mcp, "run",
        lambda transport="stdio": calls["run"].append(transport),
    )

    def _boom(*a, **k):  # 呼ばれてはいけない
        calls["control"].append((a, k))

    # 委譲経路が呼ばれないことを担保するため control_plane を差し替え。
    import lab_executor.control_plane as cp
    monkeypatch.setattr(cp, "run_mcp_with_control", _boom)

    server.main(None)

    assert calls["run"] == ["stdio"]
    assert calls["control"] == []


def test_main_none_with_env_delegates(monkeypatch):
    """env LAB_EXECUTOR_CONTROL_PORT が設定されていれば control_port=None
    でも委譲経路 (asyncio.run(run_mcp_with_control(...))) に入る。"""
    from lab_visa_mcp import server

    monkeypatch.setenv("LAB_EXECUTOR_CONTROL_PORT", "8300")

    captured = {}

    async def _fake_runner(mcp, job_mgr, port, *, backend_id):
        captured["port"] = port
        captured["backend_id"] = backend_id
        captured["mcp_is_module_mcp"] = mcp is server.mcp
        captured["job_mgr_is_module"] = job_mgr is server.job_mgr

    monkeypatch.setattr(
        server.mcp, "run",
        lambda transport="stdio": captured.setdefault("legacy", True),
    )
    import lab_executor.control_plane as cp
    monkeypatch.setattr(cp, "run_mcp_with_control", _fake_runner)

    server.main(None)

    assert captured.get("legacy") is None  # 従来経路には入らない
    assert captured["port"] == 8300
    assert captured["backend_id"] == "pyvisa"
    assert captured["mcp_is_module_mcp"] is True
    assert captured["job_mgr_is_module"] is True


# ============================================================
# main(port): run_mcp_with_control へ委譲
# ============================================================


def test_main_port_delegates_to_runner(monkeypatch):
    from lab_visa_mcp import server

    monkeypatch.delenv("LAB_EXECUTOR_CONTROL_PORT", raising=False)

    captured = {}

    async def _fake_runner(mcp, job_mgr, port, *, backend_id):
        captured["port"] = port
        captured["backend_id"] = backend_id
        captured["mcp"] = mcp
        captured["job_mgr"] = job_mgr

    monkeypatch.setattr(
        server.mcp, "run",
        lambda transport="stdio": captured.setdefault("legacy", True),
    )
    import lab_executor.control_plane as cp
    monkeypatch.setattr(cp, "run_mcp_with_control", _fake_runner)

    server.main(9100)

    assert captured.get("legacy") is None
    assert captured["port"] == 9100
    assert captured["backend_id"] == "pyvisa"
    assert captured["mcp"] is server.mcp
    assert captured["job_mgr"] is server.job_mgr
