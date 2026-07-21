"""v2.5.0: probe_all_safe — per-resource health check (100 台規模).

各 resource を probe_resource (open/close のみ) で個別診断し、
resource 単位の status ("ok"/"not_found"/"timeout"/"error") +
elapsed_ms を返す。1 台のエラーが他を捨てさせない (部分成功)。
"""
from __future__ import annotations
import asyncio

import pytest

from lab_visa_mcp.visa_manager import VisaManager


class _FakeProbeManager(VisaManager):
    """probe_resource を差し替えて probe_all_safe を単体 test する。"""

    def __init__(self, probe_results: dict):
        self._rm = None
        self._locks = {}
        self._bus_manager = None
        self._probe_results = probe_results  # resource -> dict | Exception
        self.probe_calls: list[str] = []

    async def probe_resource(self, resource_name: str,
                             timeout_ms: int = 3000) -> dict:
        self.probe_calls.append(resource_name)
        spec = self._probe_results.get(resource_name)
        if isinstance(spec, Exception):
            raise spec
        if spec is None:
            return {"success": True, "data": {
                "opened": True, "interface_type": "USB",
                "resource_class": "INSTR"}}
        return spec


def _ok(iface="USB"):
    return {"success": True, "data": {
        "opened": True, "interface_type": iface,
        "resource_class": "INSTR"}}


def _err(error_class="visa_open_resource_failed", message="boom", code=None):
    e = {"error_class": error_class, "type": "VisaIOError",
         "message": message}
    if code is not None:
        e["code"] = code
    return {"success": False, "error": e, "data": {}}


# ==============================================================
# 基本: all ok / 部分失敗
# ==============================================================


@pytest.mark.asyncio
async def test_all_ok():
    mgr = _FakeProbeManager({"R1": _ok(), "R2": _ok()})
    res = await mgr.probe_all_safe(["R1", "R2"])
    assert res["success"] is True
    assert res["partial_success"] is False
    assert res["data"]["all_ok"] is True
    assert res["data"]["status_counts"] == {"ok": 2}
    assert res["data"]["total"] == 2


@pytest.mark.asyncio
async def test_partial_failure():
    mgr = _FakeProbeManager({
        "R1": _ok(),
        "R2": _err(),
    })
    res = await mgr.probe_all_safe(["R1", "R2"])
    assert res["success"] is True
    assert res["partial_success"] is True
    assert res["data"]["all_ok"] is False
    assert res["data"]["status_counts"] == {"ok": 1, "error": 1}


# ==============================================================
# status 分類
# ==============================================================


@pytest.mark.asyncio
async def test_not_found_classified():
    mgr = _FakeProbeManager({
        "R1": _err(error_class="visa_resource_not_found",
                   message="VI_ERROR_RSRC_NFOUND", code=-1073807343),
    })
    res = await mgr.probe_all_safe(["R1"])
    r = res["data"]["results"][0]
    assert r["status"] == "not_found"


@pytest.mark.asyncio
async def test_timeout_classified():
    mgr = _FakeProbeManager({
        "R1": _err(message="VI_ERROR_TMO timeout", code=-1073807339),
    })
    res = await mgr.probe_all_safe(["R1"])
    assert res["data"]["results"][0]["status"] == "timeout"


@pytest.mark.asyncio
async def test_generic_error_classified():
    mgr = _FakeProbeManager({
        "R1": _err(message="VI_ERROR_SYSTEM_ERROR", code=-1073807360),
    })
    res = await mgr.probe_all_safe(["R1"])
    assert res["data"]["results"][0]["status"] == "error"


# ==============================================================
# per-resource fields
# ==============================================================


@pytest.mark.asyncio
async def test_per_resource_fields():
    mgr = _FakeProbeManager({"R1": _ok(iface="GPIB")})
    res = await mgr.probe_all_safe(["R1"])
    r = res["data"]["results"][0]
    assert r["resource_name"] == "R1"
    assert r["status"] == "ok"
    assert isinstance(r["elapsed_ms"], (int, float))
    assert r["interface_type"] == "GPIB"
    assert r["resource_class"] == "INSTR"


@pytest.mark.asyncio
async def test_probe_internal_exception_safe():
    """probe_resource が想定外に raise しても probe_all_safe は
    落ちず error として扱う。"""
    mgr = _FakeProbeManager({"R1": RuntimeError("unexpected")})
    res = await mgr.probe_all_safe(["R1"])
    r = res["data"]["results"][0]
    assert r["status"] == "error"
    assert r["error"]["error_class"] == "probe_internal_error"


# ==============================================================
# concurrency / empty
# ==============================================================


@pytest.mark.asyncio
async def test_empty_resource_list():
    mgr = _FakeProbeManager({})
    res = await mgr.probe_all_safe([])
    assert res["success"] is True
    assert res["data"]["total"] == 0
    assert res["data"]["all_ok"] is True


@pytest.mark.asyncio
async def test_concurrency_limit_respected():
    """concurrency=1 で全 resource が probe される。"""
    mgr = _FakeProbeManager({f"R{i}": _ok() for i in range(5)})
    res = await mgr.probe_all_safe(
        [f"R{i}" for i in range(5)], concurrency=1)
    assert res["data"]["total"] == 5
    assert len(mgr.probe_calls) == 5
    assert res["data"]["concurrency"] == 1


@pytest.mark.asyncio
async def test_all_results_present_with_mixed_status():
    mgr = _FakeProbeManager({
        "R1": _ok(),
        "R2": _err(error_class="visa_resource_not_found",
                   message="VI_ERROR_RSRC_NFOUND", code=-1073807343),
        "R3": _err(message="VI_ERROR_TMO", code=-1073807339),
        "R4": _err(message="VI_ERROR_SYSTEM_ERROR"),
    })
    res = await mgr.probe_all_safe(["R1", "R2", "R3", "R4"])
    assert res["data"]["total"] == 4
    counts = res["data"]["status_counts"]
    assert counts == {"ok": 1, "not_found": 1, "timeout": 1, "error": 1}
    # recommended actions に各カテゴリの案内が出る
    joined = " ".join(res["recommended_next_actions"]).lower()
    assert "not found" in joined
    assert "timed out" in joined or "timeout" in joined


@pytest.mark.asyncio
async def test_diagnostic_schema_version():
    mgr = _FakeProbeManager({"R1": _ok()})
    res = await mgr.probe_all_safe(["R1"])
    assert res["data"]["diagnostic_schema_version"] == "2.5"


def test_v2_5_0_version():
    import lab_visa_mcp
    parts = lab_visa_mcp.__version__.split(".")
    assert tuple(int(p) for p in parts[:3]) >= (2, 5, 0)
