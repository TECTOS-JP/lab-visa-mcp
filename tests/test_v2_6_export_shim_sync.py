"""v2.6 契約テスト: visa-mcp export shim を lab-executor v2.18/v2.19 に同期。

`docs/specs/v2.6_export_shim_sync.md` の契約を encode。
Codex はこれが全 PASS になるまで実装する。

同期対象 (現状 visa-mcp は pre-v2.18):
- _resolve_export_dir() (VISA_MCP_EXPORT_DIR)
- _safe_export_path の mkdir 失敗 → export_dir_not_writable
- RESULT_COLUMNS に sweep_index / sweep_value
- _extract_result_rows が sweep / instrument を row に載せる
- _filter_rows() + get/export ツールのフィルタ引数 + filters echo

教訓: 既存挙動 (フィルタ未指定・monkeypatch) の回帰ガードを置く。
"""
from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from visa_mcp.tools import export as exp


def _mgr_with_store(store):
    m = MagicMock()
    m.store = store
    return m


# ============== 課題 A: export dir env override ==============


def test_resolve_export_dir_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "myexports"
    monkeypatch.setenv("VISA_MCP_EXPORT_DIR", str(custom))
    assert exp._resolve_export_dir() == custom


def test_resolve_export_dir_default_constant(monkeypatch, tmp_path):
    """env 未指定なら DEFAULT_EXPORT_DIR 定数 (monkeypatch 互換)。"""
    monkeypatch.delenv("VISA_MCP_EXPORT_DIR", raising=False)
    monkeypatch.setattr(exp, "DEFAULT_EXPORT_DIR", tmp_path / "exports")
    assert exp._resolve_export_dir() == tmp_path / "exports"


def test_safe_export_path_mkdir_failure_returns_error(monkeypatch, tmp_path):
    monkeypatch.setenv("VISA_MCP_EXPORT_DIR", str(tmp_path / "x"))

    def _boom(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "mkdir", _boom)
    path, err = exp._safe_export_path(
        None, default_filename="out.csv", overwrite=True)
    assert path is None
    assert err is not None
    assert err["error_class"] == "export_dir_not_writable"


# ============== 課題 B: sweep 列 ==============


def test_result_columns_include_sweep():
    assert "sweep_index" in exp.RESULT_COLUMNS
    assert "sweep_value" in exp.RESULT_COLUMNS
    assert exp.RESULT_COLUMNS[:8] == (
        "timestamp", "target_id", "instrument", "measurement",
        "value", "unit", "step_index", "step_path")


def test_extract_result_rows_carries_sweep_and_instrument(job_store, seed_job):
    job_id = "job_v2_6_cols"
    seed_job(job_store, job_id)
    rid = job_store.record_step_started(job_id, 7, "command")
    job_store.record_step_completed(
        rid, status="ok",
        result={
            "command": "measure_voltage",
            "instrument": "GPIB0::1::INSTR",
            "raw_response": "+1.0E+0",
            "sweep_index": 2, "sweep_param": "v", "sweep_value": 3.0,
            "success": True,
        },
    )
    rows = exp._extract_result_rows(_mgr_with_store(job_store), job_id)
    assert rows
    r = rows[0]
    assert r["instrument"] == "GPIB0::1::INSTR"
    assert r["sweep_index"] == 2
    assert r["sweep_value"] == pytest.approx(3.0)


# ============== v2.19: フィルタ ==============


def _rows():
    return [
        {"instrument": "GPIB0::1::INSTR", "measurement": "measure_voltage",
         "sweep_index": 0, "sweep_value": 1.0, "value": "1.0"},
        {"instrument": "GPIB0::1::INSTR", "measurement": "measure_current",
         "sweep_index": 0, "sweep_value": 1.0, "value": "0.1"},
        {"instrument": "GPIB0::2::INSTR", "measurement": "measure_voltage",
         "sweep_index": 1, "sweep_value": 2.0, "value": "2.0"},
        {"instrument": "GPIB0::1::INSTR", "measurement": "measure_voltage",
         "sweep_index": None, "sweep_value": None, "value": "9.9"},
    ]


def test_filter_rows_by_instrument():
    out = exp._filter_rows(_rows(), instrument="GPIB0::1::INSTR")
    assert len(out) == 3


def test_filter_rows_by_sweep_index_zero_valid():
    out = exp._filter_rows(_rows(), sweep_index=0)
    assert len(out) == 2
    assert all(r["sweep_index"] == 0 for r in out)


def test_filter_rows_combined_and():
    out = exp._filter_rows(
        _rows(), instrument="GPIB0::1::INSTR",
        measurement="measure_voltage", sweep_index=0)
    assert len(out) == 1


def test_filter_rows_empty_noop():
    rows = _rows()
    assert exp._filter_rows(rows, instrument="", sweep_index=None,
                            measurement="") == rows


def _seed_two(job_store, seed_job, job_id):
    seed_job(job_store, job_id)
    for idx, (sidx, sval, mv) in enumerate(
            [(0, 1.0, "+1.0E+0"), (1, 2.0, "+2.0E+0")]):
        rid = job_store.record_step_started(job_id, idx, "command")
        job_store.record_step_completed(
            rid, status="ok",
            result={
                "command": "measure_voltage",
                "instrument": "GPIB0::1::INSTR", "raw_response": mv,
                "sweep_index": sidx, "sweep_param": "v", "sweep_value": sval,
                "success": True,
            },
        )


@pytest.mark.asyncio
async def test_get_experiment_results_filter(job_store, seed_job):
    from fastmcp import FastMCP
    job_id = "job_v2_6_get"
    _seed_two(job_store, seed_job, job_id)
    job_mgr = MagicMock()
    job_mgr.store = job_store
    job_mgr.get.return_value = MagicMock()

    mcp = FastMCP("t")
    exp.register_tools(mcp, job_mgr)
    tool = await mcp.get_tool("get_experiment_results")

    res = await tool.fn(job_id=job_id, sweep_index=1)
    rows = res["data"]["rows"]
    assert len(rows) == 1 and rows[0]["sweep_index"] == 1
    assert res["data"]["filters"]["sweep_index"] == 1

    res0 = await tool.fn(job_id=job_id)
    assert res0["data"]["filters"] == {
        "instrument": None, "sweep_index": None, "measurement": None}
    assert res0["data"]["pagination"]["total"] == 2


@pytest.mark.asyncio
async def test_export_experiment_results_filtered_csv(
    job_store, seed_job, tmp_path, monkeypatch
):
    import csv
    from fastmcp import FastMCP
    monkeypatch.setenv("VISA_MCP_EXPORT_DIR", str(tmp_path / "ex"))
    job_id = "job_v2_6_exp"
    _seed_two(job_store, seed_job, job_id)
    job_mgr = MagicMock()
    job_mgr.store = job_store
    job_mgr.get.return_value = MagicMock()

    mcp = FastMCP("t")
    exp.register_tools(mcp, job_mgr)
    tool = await mcp.get_tool("export_experiment_results")

    res = await tool.fn(job_id=job_id, format="csv", sweep_index=1)
    data = res["data"]
    assert data["rows"] == 1
    assert data["filters"]["sweep_index"] == 1
    with open(Path(data["path"]), newline="", encoding="utf-8") as f:
        csv_rows = list(csv.DictReader(f))
    assert len(csv_rows) == 1 and csv_rows[0]["sweep_index"] == "1"


# ============== version / sentinel ==============


def test_v2_6_version():
    import visa_mcp
    parts = visa_mcp.__version__.split(".")
    assert tuple(int(p) for p in parts[:3]) >= (2, 6, 0), visa_mcp.__version__


def test_export_fix_sentinel_bumped():
    src = open(exp.__file__, encoding="utf-8").read()
    assert '"v2.6.0"' in src or "'v2.6.0'" in src, (
        "export_fix sentinel を v2.6.0 に更新すること")
