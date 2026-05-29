"""v2.2.1: Codex v2.2.0 レビュー対応テスト.

P1-a: resolver が wheel 環境で <venv>\\Lib\\instruments を builtin より
      優先するバグの修正 (pyproject.toml の有無で dev repo 判定)。
P1-b: 7563 YAML loose pattern が JPPC corruption を受け入れる。
P2  : visa-mcp 側 export shim も parsed metadata を rows 化しない。
"""
from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from lab_executor.job.store import JobStore
from visa_mcp.tools.export import _extract_result_rows


REPO = Path(__file__).resolve().parent.parent
BUILTIN = REPO / "src" / "visa_mcp" / "builtin_instruments"


# ==============================================================
# P1-a: pyproject 無いとき dev path を見ない
# ==============================================================


def test_resolver_ignores_venv_instruments_without_pyproject(
    monkeypatch, tmp_path
):
    """wheel install 環境を模擬: <venv>/Lib に instruments/ がある
    が pyproject.toml が無い場合、resolver は dev path を無視して
    builtin に落ちる。

    v2.3.1: resolver は `visa_mcp.instruments_dir` の純粋関数に切り
    出されたので、server module 全体を import せずに直接呼べる
    (Codex v2.2.1 レビュー対応 #2)。
    """
    fake_venv_lib = tmp_path / "Lib"
    sp = fake_venv_lib / "site-packages" / "visa_mcp"
    sp.mkdir(parents=True)
    fake_server = sp / "server.py"
    fake_server.write_text("# fake", encoding="utf-8")
    stale_instr = fake_venv_lib / "instruments"
    stale_instr.mkdir(parents=True)
    (stale_instr / "stale_dmm.yaml").write_text(
        "metadata: {}", encoding="utf-8")
    assert not (fake_venv_lib / "pyproject.toml").exists()

    from visa_mcp.instruments_dir import resolve_instruments_dir
    monkeypatch.delenv("VISA_MCP_INSTRUMENTS_DIR", raising=False)
    resolved = resolve_instruments_dir(str(fake_server))
    assert resolved != stale_instr, (
        f"v2.3.1: wheel install で <venv>/Lib/instruments を拾ってる: "
        f"{resolved}")


def test_resolver_uses_repo_instruments_when_pyproject_present(
    monkeypatch, tmp_path
):
    """dev リポジトリ (pyproject.toml がある) では従来通り
    <repo>/instruments を優先する。"""
    fake_repo = tmp_path / "fake_repo"
    fake_repo.mkdir()
    (fake_repo / "pyproject.toml").write_text("[project]", encoding="utf-8")
    instr = fake_repo / "instruments"
    instr.mkdir()
    (instr / "custom.yaml").write_text("metadata: {}", encoding="utf-8")
    sp = fake_repo / "src" / "visa_mcp"
    sp.mkdir(parents=True)
    fake_server = sp / "server.py"
    fake_server.write_text("# fake", encoding="utf-8")

    from visa_mcp.instruments_dir import resolve_instruments_dir
    monkeypatch.delenv("VISA_MCP_INSTRUMENTS_DIR", raising=False)
    resolved = resolve_instruments_dir(str(fake_server))
    assert resolved == instr


# ==============================================================
# P1-b: 7563 YAML loose pattern が JPPC corruption を受け入れる
# ==============================================================


def test_builtin_7563_yaml_loose_pattern_accepts_jppc():
    """builtin の 7563 YAML loose pattern が `JPPC+0029*0A+0` の
    ような `*` / `A` corruption を受け入れる正規表現に
    更新されていること。"""
    import re
    p = BUILTIN / "yokogawa_7563.yaml"
    text = p.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    rf = data["response_formats"]["measurement_data"]
    patterns = rf.get("patterns") or [rf.get("pattern")]
    # 少なくとも 1 つの pattern が JPPC を許容することを確認
    sample = "JPPC+0029*0A+0"
    accepted = False
    for pat in patterns:
        if pat and re.match(pat, sample):
            accepted = True
            break
    assert accepted, (
        f"v2.2.1: builtin 7563 YAML のどの pattern も "
        f"{sample!r} を受け付けない: {patterns}")


# ==============================================================
# P2: visa-mcp 側 export shim も metadata 除外
# ==============================================================


def test_visa_mcp_export_skips_parsed_metadata(job_store, seed_job):
    """v2.3.1: fixture を使い teardown で close される。"""
    job_id = "job_v2_2_1_metadata"
    seed_job(job_store, job_id)
    row_id = job_store.record_step_started(job_id, 0, "command")
    job_store.record_step_completed(
        row_id, status="ok",
        result={
            "command": "read_measurement",
            "raw_response": "JPPC+0029*0A+0",
            "parsed": {
                "matched": False,
                "fields": {},
                "raw": "JPPC+0029*0A+0",
                "value_numeric": 29.0,
                "fallback_used": "numeric_extract",
            },
            "success": True,
        },
    )
    mgr = MagicMock(); mgr.store = job_store
    rows = _extract_result_rows(mgr, job_id)
    measurements = {r["measurement"] for r in rows}
    assert "matched" not in measurements
    assert "fields" not in measurements
    assert "raw" not in measurements
    assert "fallback_used" not in measurements
    assert any("value_numeric" in m for m in measurements), measurements


def test_v2_2_1_version():
    import visa_mcp
    parts = visa_mcp.__version__.split(".")
    assert tuple(int(p) for p in parts[:3]) >= (2, 2, 1)
