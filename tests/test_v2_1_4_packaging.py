"""v2.1.4: wheel に instrument YAML を同梱、resolver で見つかること。

Codex E2E (v2.1.3) で `pip install visa-mcp` 直後の
`visa-mcp serve` 起動で instrument 定義 0 件問題が発生した。
v2.1.4 でこれを下記により解消:
- `src/lab_visa_mcp/builtin_instruments/` 配下に YAML を同梱
- pyproject.toml の force-include に追加
- server.py 側 resolver が env / dev / wheel-default の順で探す
"""
from __future__ import annotations
import os
from pathlib import Path

import pytest


def test_builtin_instruments_dir_exists():
    """ソースツリーに `builtin_instruments` ディレクトリがあり
    YAML が配置されていること。"""
    import lab_visa_mcp
    builtin = Path(lab_visa_mcp.__file__).parent / "builtin_instruments"
    assert builtin.is_dir(), f"builtin_instruments が無い: {builtin}"
    yamls = list(builtin.glob("*.yaml"))
    assert yamls, f"builtin_instruments に YAML が無い: {builtin}"
    # 主要機器は最低限同梱
    names = {p.name for p in yamls}
    assert any("kikusui" in n.lower() or "pmx" in n.lower() for n in names), (
        f"PMX 定義が無い: {names}")
    assert any("yokogawa" in n.lower() or "7563" in n for n in names), (
        f"7563 定義が無い: {names}")


def test_resolve_instruments_dir_env_override(monkeypatch, tmp_path):
    """`VISA_MCP_INSTRUMENTS_DIR` を設定するとそちらが優先される。

    v2.3.6: server module を import せず純粋関数を直接呼ぶ
    (server import は JobManager/JobStore 初期化の副作用がある)。
    """
    yaml_dir = tmp_path / "custom"
    yaml_dir.mkdir()
    (yaml_dir / "dummy.yaml").write_text("hello", encoding="utf-8")
    monkeypatch.setenv("VISA_MCP_INSTRUMENTS_DIR", str(yaml_dir))
    from lab_visa_mcp.instruments_dir import resolve_instruments_dir
    # env override が効くので server_file は任意の path で良い
    resolved = resolve_instruments_dir(str(tmp_path / "fake_server.py"))
    assert resolved == yaml_dir


def test_resolve_instruments_dir_falls_back_to_builtin(monkeypatch):
    """env 未設定 / repo dev path 不在のとき同梱 builtin_instruments が
    fallback として返ること。"""
    monkeypatch.delenv("VISA_MCP_INSTRUMENTS_DIR", raising=False)
    import lab_visa_mcp
    from lab_visa_mcp.instruments_dir import resolve_instruments_dir
    # 実 server.py path を渡す (本物の dev/wheel layout で resolver を動かす)
    real_server = Path(lab_visa_mcp.__file__).parent / "server.py"
    resolved = resolve_instruments_dir(str(real_server))
    assert resolved is not None
    assert isinstance(resolved, Path)


def test_v2_1_4_version():
    import lab_visa_mcp
    parts = lab_visa_mcp.__version__.split(".")
    assert tuple(int(p) for p in parts[:3]) >= (2, 1, 4), (
        f"version {lab_visa_mcp.__version__} < 2.1.4")


def test_pyproject_includes_builtin_instruments():
    """pyproject.toml に force-include 設定があること。"""
    pyproject = (
        Path(__file__).resolve().parent.parent / "pyproject.toml")
    text = pyproject.read_text(encoding="utf-8")
    assert "builtin_instruments" in text, (
        "pyproject.toml に builtin_instruments の force-include "
        "設定が無い")
