"""v2.1.2 integration: visa-mcp serve が登録する
`lab_visa_mcp.tools.export._extract_result_rows` も `raw_response` を読めること。

Codex 実機 E2E (v2.1.1) で発覚: lab-executor-mcp 側を v2.13.2 で
修正しても、visa-mcp の MCP server が登録するのは
`lab_visa_mcp.tools.export` の **独自コピー** であり、こちらは依然
旧名 `response_raw` しか読まなかったため rows=0 が再発した。

v2.3.1: fixtures (`job_store`, `seed_job`) に refactor。
"""
from __future__ import annotations
from unittest.mock import MagicMock

from lab_visa_mcp.tools.export import _extract_result_rows


def _mgr_with_store(store):
    mgr = MagicMock()
    mgr.store = store
    return mgr


def test_lab_visa_mcp_export_reads_raw_response(job_store, seed_job):
    job_id = "job_lab_visa_mcp_v2_1_2"
    seed_job(job_store, job_id)
    row_id = job_store.record_step_started(job_id, 0, "command")
    job_store.record_step_completed(
        row_id, status="ok",
        result={
            "command": "measure_voltage",
            "scpi_sent": "MEAS:VOLT?",
            "raw_response": "+1.234E+00",
            "success": True,
        },
    )
    rows = _extract_result_rows(_mgr_with_store(job_store), job_id)
    assert len(rows) == 1, (
        f"v2.1.2: visa-mcp 側 export shim も raw_response を読むべき "
        f"(rows={len(rows)})")
    assert rows[0]["measurement"] == "measure_voltage"
    assert rows[0]["value"] == "+1.234E+00"


def test_lab_visa_mcp_export_reads_parsed_alias(job_store, seed_job):
    job_id = "job_lab_visa_mcp_parsed"
    seed_job(job_store, job_id)
    row_id = job_store.record_step_started(job_id, 0, "command")
    job_store.record_step_completed(
        row_id, status="ok",
        result={
            "command": "measure_voltage",
            "parsed": {"value": 1.234, "unit": "V"},
            "raw_response": "+1.234E+00",
            "success": True,
        },
    )
    rows = _extract_result_rows(_mgr_with_store(job_store), job_id)
    # v2.14.1+ では metadata key (unit が str なので除外、value のみ)
    measurements = {r["measurement"] for r in rows}
    assert any("value" in m for m in measurements), measurements


def test_lab_visa_mcp_export_legacy_keys_still_work(job_store, seed_job):
    job_id = "job_lab_visa_mcp_legacy"
    seed_job(job_store, job_id)
    row_id = job_store.record_step_started(job_id, 0, "command")
    job_store.record_step_completed(
        row_id, status="ok",
        result={"command": "old_cmd", "response_raw": "L", "success": True},
    )
    rows = _extract_result_rows(_mgr_with_store(job_store), job_id)
    assert len(rows) == 1
    assert rows[0]["value"] == "L"


def test_v2_1_2_version():
    import lab_visa_mcp
    parts = lab_visa_mcp.__version__.split(".")
    assert tuple(int(p) for p in parts[:3]) >= (2, 1, 2)


def test_v2_1_3_version_sentinel_in_response():
    """v2.1.3: get_experiment_results response data に _meta.versions が
    入り、Codex 側が rows=0 のとき即座にバージョンを確認できる。"""
    from lab_visa_mcp.tools import export as _exp
    src = open(_exp.__file__, encoding="utf-8").read()
    assert "_meta" in src and "versions" in src
    assert "export_fix" in src
