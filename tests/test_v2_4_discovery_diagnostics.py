"""v2.4.0: discovery per-resource/per-interface diagnostic schema.

`discover_resources_safe` を拡張:
- per-query `status` enum ("ok"/"empty"/"timeout"/"error")
- per-query `elapsed_ms`
- timeout を error と区別 (`timed_out_interfaces`)
- top-level `backend` 情報
- `interface_status` 集計
すべて後方互換 (既存 key は不変)。
"""
from __future__ import annotations
import asyncio

import pytest

from lab_visa_mcp.visa_manager import (
    VisaManager, VisaError, VisaTimeoutError, _PYVISA_AVAILABLE,
)


class _FakeVisaManager(VisaManager):
    """list_resources を差し替えて discover_resources_safe を単体 test
    する。pyvisa 未導入環境でも動くよう __init__ を override。"""

    def __init__(self, behaviors: dict):
        # 親 __init__ (pyvisa 必須) を呼ばずに最小初期化
        self._rm = None
        self._locks = {}
        self._bus_manager = None
        self._behaviors = behaviors  # query -> callable/raise spec

    async def list_resources(self, query: str = "?*::INSTR"):
        spec = self._behaviors.get(query)
        if spec is None:
            return []
        if isinstance(spec, Exception):
            raise spec
        if callable(spec):
            return spec()
        return list(spec)

    def backend_info(self) -> dict:
        return {"available": True, "pyvisa_version": "x.y",
                "backend": "@ni"}


@pytest.mark.asyncio
async def test_per_query_status_ok_and_empty():
    mgr = _FakeVisaManager({
        "USB?*": ["USB0::0x1::INSTR"],   # ok
        "GPIB?*": [],                     # empty
    })
    res = await mgr.discover_resources_safe(["USB?*", "GPIB?*"])
    q = {e["query"]: e for e in res["data"]["queries"]}
    assert q["USB?*"]["status"] == "ok"
    assert q["GPIB?*"]["status"] == "empty"
    assert q["USB?*"]["elapsed_ms"] is not None
    assert res["data"]["interface_status"] == {"USB": "ok", "GPIB": "empty"}


@pytest.mark.asyncio
async def test_timeout_classified_separately():
    mgr = _FakeVisaManager({
        "USB?*": ["USB0::INSTR"],
        "GPIB?*": VisaTimeoutError("VI_ERROR_TMO: timeout expired"),
    })
    res = await mgr.discover_resources_safe(["USB?*", "GPIB?*"])
    q = {e["query"]: e for e in res["data"]["queries"]}
    assert q["GPIB?*"]["status"] == "timeout"
    assert "GPIB" in res["data"]["timed_out_interfaces"]
    assert "GPIB" not in res["data"]["failed_interfaces"]
    assert res["partial_success"] is True
    assert q["GPIB?*"]["error"]["error_class"] == (
        "visa_interface_discovery_timeout")


@pytest.mark.asyncio
async def test_generic_error_classified_as_error():
    mgr = _FakeVisaManager({
        "USB?*": ["USB0::INSTR"],
        "GPIB?*": VisaError("VI_ERROR_SYSTEM_ERROR (-1073807360)"),
    })
    res = await mgr.discover_resources_safe(["USB?*", "GPIB?*"])
    q = {e["query"]: e for e in res["data"]["queries"]}
    assert q["GPIB?*"]["status"] == "error"
    assert "GPIB" in res["data"]["failed_interfaces"]
    assert "GPIB" not in res["data"]["timed_out_interfaces"]
    assert res["partial_success"] is True


@pytest.mark.asyncio
async def test_all_success():
    mgr = _FakeVisaManager({
        "USB?*": ["USB0::INSTR"],
        "GPIB?*": ["GPIB0::2::INSTR"],
    })
    res = await mgr.discover_resources_safe(["USB?*", "GPIB?*"])
    assert res["success"] is True
    assert res["partial_success"] is False
    assert res["data"]["resource_count"] == 2
    assert res["data"]["timed_out_interfaces"] == []


@pytest.mark.asyncio
async def test_backend_info_in_response():
    mgr = _FakeVisaManager({"USB?*": ["USB0::INSTR"]})
    res = await mgr.discover_resources_safe(["USB?*"])
    backend = res["data"]["backend"]
    assert backend["available"] is True
    assert backend["backend"] == "@ni"
    # v2.4.1 で schema version は "2.4.1" に上がった
    assert res["data"]["diagnostic_schema_version"].startswith("2.4")


@pytest.mark.asyncio
async def test_backward_compatible_keys_present():
    """既存 key (success/partial_success/empty_with_success/
    resources/resource_count/queries/successful_interfaces/
    failed_interfaces) が引き続き存在すること。"""
    mgr = _FakeVisaManager({"USB?*": ["USB0::INSTR"]})
    res = await mgr.discover_resources_safe(["USB?*"])
    assert set(res) >= {
        "success", "partial_success", "empty_with_success",
        "data", "recommended_next_actions"}
    assert set(res["data"]) >= {
        "resources", "resource_count", "queries",
        "successful_interfaces", "failed_interfaces"}


@pytest.mark.asyncio
async def test_timeout_recommended_action():
    mgr = _FakeVisaManager({
        "GPIB?*": VisaTimeoutError("timeout"),
    })
    res = await mgr.discover_resources_safe(["GPIB?*"])
    joined = " ".join(res["recommended_next_actions"]).lower()
    assert "timed out" in joined or "timeout" in joined


@pytest.mark.asyncio
async def test_elapsed_ms_is_numeric():
    mgr = _FakeVisaManager({"USB?*": ["USB0::INSTR"]})
    res = await mgr.discover_resources_safe(["USB?*"])
    e = res["data"]["queries"][0]
    assert isinstance(e["elapsed_ms"], (int, float))
    assert e["elapsed_ms"] >= 0.0


def test_v2_4_0_version():
    import lab_visa_mcp
    parts = lab_visa_mcp.__version__.split(".")
    assert tuple(int(p) for p in parts[:3]) >= (2, 4, 0)


# ==============================================================
# v2.4.1: interface_status severity 優先集計 (Codex v2.4.0 P2)
# ==============================================================


@pytest.mark.asyncio
async def test_interface_status_severity_priority_error_over_ok():
    """同一 interface に error と ok の query があるとき、
    interface_status は error を優先 (ok で上書きされない)。"""
    mgr = _FakeVisaManager({
        "USB_FAIL?*": VisaError("VI_ERROR_SYSTEM_ERROR"),
        "USB?*": ["USB0::INSTR"],
    })
    # 両方 interface="USB" になるよう _interface_of は prefix 判定
    res = await mgr.discover_resources_safe(["USB_FAIL?*", "USB?*"])
    # USB_FAIL?* / USB?* どちらも interface=USB
    assert res["data"]["interface_status"]["USB"] == "error", (
        f"v2.4.1: error が ok で上書きされている: "
        f"{res['data']['interface_status']}")


@pytest.mark.asyncio
async def test_interface_status_detail_counts():
    """interface_status_detail に status 別カウントが入る。"""
    mgr = _FakeVisaManager({
        "USB_FAIL?*": VisaError("err"),
        "USB?*": ["USB0::INSTR"],
    })
    res = await mgr.discover_resources_safe(["USB_FAIL?*", "USB?*"])
    detail = res["data"]["interface_status_detail"]["USB"]
    assert detail.get("error") == 1
    assert detail.get("ok") == 1


@pytest.mark.asyncio
async def test_interface_status_severity_timeout_over_empty():
    mgr = _FakeVisaManager({
        "GPIB_A?*": VisaTimeoutError("timeout"),
        "GPIB?*": [],   # empty
    })
    res = await mgr.discover_resources_safe(["GPIB_A?*", "GPIB?*"])
    assert res["data"]["interface_status"]["GPIB"] == "timeout"


@pytest.mark.asyncio
async def test_diagnostic_schema_version_2_4_1():
    mgr = _FakeVisaManager({"USB?*": ["USB0::INSTR"]})
    res = await mgr.discover_resources_safe(["USB?*"])
    assert res["data"]["diagnostic_schema_version"] == "2.4.1"


def test_v2_4_1_version():
    import lab_visa_mcp
    parts = lab_visa_mcp.__version__.split(".")
    assert tuple(int(p) for p in parts[:3]) >= (2, 4, 1)
