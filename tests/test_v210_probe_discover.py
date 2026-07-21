"""v2.1.0: probe_resource / discover_resources_safe tests.

`*IDN?` / `query` / `write` を絶対に呼ばないことを確認する safety
test を含む。
"""
from __future__ import annotations
from unittest.mock import MagicMock, patch

import pytest


def _make_mgr():
    """VisaManager を ResourceManager モック付きで構築"""
    with patch("lab_visa_mcp.visa_manager._PYVISA_AVAILABLE", True):
        from lab_visa_mcp.visa_manager import VisaManager
        mgr = VisaManager.__new__(VisaManager)
        mgr._rm = None
        mgr._locks = {}
        mgr._bus_manager = None
        return mgr


# ============================================================
# probe_resource
# ============================================================


@pytest.mark.asyncio
async def test_probe_resource_open_close_success():
    mgr = _make_mgr()
    fake_res = MagicMock()
    fake_res.interface_type = 7
    fake_res.resource_class = "INSTR"
    fake_rm = MagicMock()
    fake_rm.open_resource.return_value = fake_res
    mgr._rm = fake_rm

    res = await mgr.probe_resource("USB0::0x0B3E::INSTR",
                                     timeout_ms=3000)
    assert res["success"] is True
    d = res["data"]
    assert d["operation"] == "open_close_only"
    assert d["opened"] is True
    assert d["closed"] is True
    assert d["query_performed"] is False
    assert d["write_performed"] is False
    assert d["interface_type"] == 7
    assert d["resource_class"] == "INSTR"
    assert d["timeout_ms"] == 3000
    # close が呼ばれている
    fake_res.close.assert_called_once()


@pytest.mark.asyncio
async def test_probe_resource_open_failure_structured_error():
    mgr = _make_mgr()
    fake_rm = MagicMock()
    # open_resource が raise
    err = OSError("VI_ERROR_SYSTEM_ERROR (-1073807360)")
    err.error_code = -1073807360
    fake_rm.open_resource.side_effect = err
    mgr._rm = fake_rm

    res = await mgr.probe_resource("GPIB0::2::INSTR")
    assert res["success"] is False
    assert res["data"]["opened"] is False
    assert res["data"]["query_performed"] is False
    assert res["data"]["write_performed"] is False
    e = res["error"]
    assert e["error_class"] == "visa_open_resource_failed"
    assert "VI_ERROR_SYSTEM_ERROR" in e["message"]
    assert e["code"] == -1073807360


@pytest.mark.asyncio
async def test_probe_resource_does_not_query_or_write():
    """**probe は query / write / *IDN? を絶対に呼ばない** ことを
    モックの method 呼び出し回数で固定する"""
    mgr = _make_mgr()
    fake_res = MagicMock()
    fake_res.interface_type = 7
    fake_res.resource_class = "INSTR"
    fake_rm = MagicMock()
    fake_rm.open_resource.return_value = fake_res
    mgr._rm = fake_rm

    await mgr.probe_resource("USB0::INSTR")

    # 致命的: query / write / read のいずれも呼ばれない
    fake_res.query.assert_not_called()
    fake_res.write.assert_not_called()
    fake_res.read.assert_not_called()


@pytest.mark.asyncio
async def test_probe_resource_closes_resource_on_success():
    mgr = _make_mgr()
    fake_res = MagicMock()
    fake_rm = MagicMock()
    fake_rm.open_resource.return_value = fake_res
    mgr._rm = fake_rm

    await mgr.probe_resource("USB0::INSTR")
    fake_res.close.assert_called_once()


@pytest.mark.asyncio
async def test_probe_resource_closes_resource_on_attribute_failure():
    """属性読み取り中に例外 → finally で close される"""
    mgr = _make_mgr()
    fake_res = MagicMock()
    # interface_type 取得時に例外
    type(fake_res).interface_type = property(
        lambda s: (_ for _ in ()).throw(RuntimeError("attr")))
    fake_rm = MagicMock()
    fake_rm.open_resource.return_value = fake_res
    mgr._rm = fake_rm

    res = await mgr.probe_resource("USB0::INSTR")
    # finally で close
    fake_res.close.assert_called_once()
    # success かどうかは実装次第だが、close は必ず行われる


# ============================================================
# discover_resources_safe
# ============================================================


@pytest.mark.asyncio
async def test_discover_resources_safe_partial_success():
    """USB は成功、GPIB は失敗 → success=true / partial_success=true"""
    mgr = _make_mgr()
    fake_rm = MagicMock()

    def _list(query):
        if query.startswith("USB"):
            return ["USB0::0x0B3E::INSTR"]
        if query.startswith("GPIB"):
            err = OSError("VI_ERROR_SYSTEM_ERROR")
            err.error_code = -1073807360
            raise err
        return []
    fake_rm.list_resources.side_effect = _list
    mgr._rm = fake_rm

    res = await mgr.discover_resources_safe(
        queries=["USB?*", "GPIB?*"])
    assert res["success"] is True
    assert res["partial_success"] is True
    d = res["data"]
    assert len(d["resources"]) == 1
    assert d["resources"][0]["interface"] == "USB"
    assert "USB" in d["successful_interfaces"]
    assert "GPIB" in d["failed_interfaces"]
    # query 別 entry
    by_q = {q["query"]: q for q in d["queries"]}
    assert by_q["USB?*"]["success"] is True
    assert by_q["GPIB?*"]["success"] is False
    assert by_q["GPIB?*"]["error"]["error_class"] == \
        "visa_interface_discovery_failed"
    # recommended_next_actions が入る
    assert any("USB?*" in a
                for a in res["recommended_next_actions"])


@pytest.mark.asyncio
async def test_discover_resources_safe_all_success():
    mgr = _make_mgr()
    fake_rm = MagicMock()
    fake_rm.list_resources.return_value = ["USB0::INSTR"]
    mgr._rm = fake_rm
    res = await mgr.discover_resources_safe(queries=["USB?*"])
    assert res["success"] is True
    assert res["partial_success"] is False
    assert res["recommended_next_actions"] == []


@pytest.mark.asyncio
async def test_discover_resources_safe_all_failure():
    mgr = _make_mgr()
    fake_rm = MagicMock()
    fake_rm.list_resources.side_effect = OSError("denied")
    mgr._rm = fake_rm
    res = await mgr.discover_resources_safe(
        queries=["USB?*", "GPIB?*"])
    assert res["success"] is False
    assert res["partial_success"] is False
    assert res["data"]["successful_interfaces"] == []
    assert set(res["data"]["failed_interfaces"]) == {"USB", "GPIB"}


@pytest.mark.asyncio
async def test_discover_resources_safe_default_queries():
    """queries=None で default のサブ filter が試行される"""
    mgr = _make_mgr()
    called: list[str] = []
    fake_rm = MagicMock()

    def _list(q):
        called.append(q)
        return []
    fake_rm.list_resources.side_effect = _list
    mgr._rm = fake_rm
    await mgr.discover_resources_safe()
    # USB / GPIB / ASRL / TCPIP の 4 件
    assert "USB?*" in called
    assert "GPIB?*" in called
    assert "ASRL?*" in called
    assert "TCPIP?*" in called


# ============================================================
# Regression
# ============================================================


def test_lab_visa_mcp_v210_version():
    import lab_visa_mcp
    parts = lab_visa_mcp.__version__.split(".")
    assert int(parts[0]) >= 2 and int(parts[1]) >= 1


# ============================================================
# v2.1.1: empty-with-success + VI_ERROR_RSRC_NFOUND classification
# ============================================================


@pytest.mark.asyncio
async def test_discover_safe_empty_with_success_flag():
    """全 interface 成功 / 全 resource 0 → empty_with_success=True +
    専用 recommended_next_actions"""
    mgr = _make_mgr()
    fake_rm = MagicMock()
    fake_rm.list_resources.return_value = []
    mgr._rm = fake_rm
    res = await mgr.discover_resources_safe(
        queries=["USB?*", "GPIB?*"])
    assert res["success"] is True
    assert res["partial_success"] is False
    assert res["empty_with_success"] is True
    assert res["data"]["resource_count"] == 0
    actions = res["recommended_next_actions"]
    joined = " ".join(actions)
    # device 起因の next actions が含まれる
    assert any("0 resources" in a or "device power" in a.lower()
                or "NI MAX" in a for a in actions)


@pytest.mark.asyncio
async def test_discover_safe_not_empty_no_empty_flag():
    """resource が 1 件以上あれば empty_with_success=False"""
    mgr = _make_mgr()
    fake_rm = MagicMock()
    fake_rm.list_resources.return_value = ["USB0::INSTR"]
    mgr._rm = fake_rm
    res = await mgr.discover_resources_safe(queries=["USB?*"])
    assert res["empty_with_success"] is False
    assert res["data"]["resource_count"] == 1


@pytest.mark.asyncio
async def test_probe_resource_rsrc_nfound_classification():
    """VI_ERROR_RSRC_NFOUND (-1073807343) は error_class=
    visa_resource_not_found に分類し、専用 next actions を付ける"""
    mgr = _make_mgr()
    fake_rm = MagicMock()
    err = OSError(
        "VI_ERROR_RSRC_NFOUND (-1073807343): Insufficient location "
        "information or the requested device or resource is not "
        "present in the system.")
    err.error_code = -1073807343
    fake_rm.open_resource.side_effect = err
    mgr._rm = fake_rm
    res = await mgr.probe_resource("USB0::0x0B3E::INSTR")
    assert res["success"] is False
    assert res["error"]["error_class"] == "visa_resource_not_found"
    assert res["error"]["code"] == -1073807343
    actions = res["recommended_next_actions"]
    joined = " ".join(actions)
    assert "list_resources" in joined
    assert "NI MAX" in joined or "cable" in joined.lower()


@pytest.mark.asyncio
async def test_probe_resource_other_error_keeps_generic_class():
    """RSRC_NFOUND 以外は visa_open_resource_failed のまま"""
    mgr = _make_mgr()
    fake_rm = MagicMock()
    err = OSError("VI_ERROR_SYSTEM_ERROR (-1073807360)")
    err.error_code = -1073807360
    fake_rm.open_resource.side_effect = err
    mgr._rm = fake_rm
    res = await mgr.probe_resource("GPIB0::2::INSTR")
    assert res["error"]["error_class"] == "visa_open_resource_failed"
    # こちらは専用 recommended_next_actions は付かない
    assert "recommended_next_actions" not in res or \
        res.get("recommended_next_actions") is None
