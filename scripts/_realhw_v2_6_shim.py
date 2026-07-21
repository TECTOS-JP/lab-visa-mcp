"""v2.6 実機 E2E: lab_visa_mcp.tools.export (shim copy) の sweep 列 + フィルタ。

visa-mcp serve が登録するのは lab_visa_mcp.tools.export の独自コピー。
これが lab-executor v2.18/v2.19 と同じ列・フィルタを返すか実機で確認する。

ユーザ承認済み (レジスタ加熱配線): PMX35-3A を 1->3V sweep。
OVP6/OCP1.5、各点 1.2s、末尾 OFF + safe_shutdown。
"""
from __future__ import annotations
import asyncio
import csv
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
LE_SRC = ROOT.parent / "lab-executor-mcp" / "src"
sys.path.insert(0, str(LE_SRC))

import lab_visa_mcp  # noqa
import lab_executor  # noqa
print(f"[versions] lab_visa_mcp={lab_visa_mcp.__version__}, "
      f"lab_executor={lab_executor.__version__}")

from lab_visa_mcp.visa_manager import VisaManager
from lab_visa_mcp.session_manager import SessionManager
from lab_visa_mcp.instrument_registry import InstrumentRegistry
from lab_visa_mcp.job import JobManager, JobStore  # = lab_executor.job (shim)
from lab_visa_mcp.tools import export as exp        # shim copy under test
from fastmcp import FastMCP


async def main() -> int:
    tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmpdb.close()
    export_dir = Path(tempfile.mkdtemp(prefix="v2_6_shim_"))
    os.environ["VISA_MCP_EXPORT_DIR"] = str(export_dir)
    print(f"[export module] {exp.__file__}")
    print(f"[export_dir] {export_dir}")
    assert "lab_visa_mcp" in exp.__file__, "shim copy を使うこと"

    examples = ROOT / "examples" / "instruments"
    registry = InstrumentRegistry(str(examples))
    registry.reload()
    visa = VisaManager()
    sessions = SessionManager(visa, registry)

    pmx = "USB0::0x0B3E::0x1029::ZM000463::INSTR"
    dmm = "GPIB0::2::INSTR"
    s_pmx = await sessions.identify(pmx)
    assert s_pmx.definition is not None
    print(f"[step 1] PMX: {s_pmx.definition.metadata.model}")
    s_dmm = sessions.bind_manually(dmm, "Yokogawa", "7563")
    assert s_dmm and s_dmm.definition is not None
    print(f"[step 2] DMM: {s_dmm.definition.metadata.model}")

    store = JobStore(tmpdb.name)
    mgr = JobManager(backend=visa, session_mgr=sessions, store=store)
    plan = {
        "dsl_version": "0.8", "name": "v2_6_shim_sweep",
        "bindings": {"psu": pmx, "dmm": dmm},
        "safe_shutdown": {"targets": [
            {"resource": "$psu", "commands": [
                {"command": "set_output", "args": {"state": "0"}}]}]},
        "steps": [
            {"type": "command", "instrument": "$psu",
             "command": "set_voltage_protection", "args": {"voltage": 6.0}},
            {"type": "command", "instrument": "$psu",
             "command": "set_current_protection", "args": {"current": 1.5}},
            {"type": "command", "instrument": "$psu",
             "command": "set_voltage", "args": {"voltage": 0.0}},
            {"type": "command", "instrument": "$psu",
             "command": "set_current", "args": {"current": 1.0}},
            {"type": "command", "instrument": "$psu",
             "command": "set_output", "args": {"state": "1"}},
            {"type": "sweep", "parameter": "v",
             "values": {"values": [1.0, 2.0, 3.0]},
             "body": [
                 {"type": "command", "instrument": "$psu",
                  "command": "set_voltage", "args": {"voltage": "{v}"}},
                 {"type": "wait", "seconds": 1.2},
                 {"type": "query", "instrument": "$psu",
                  "command": "measure_voltage"},
                 {"type": "query", "instrument": "$dmm",
                  "command": "read_measurement"},
             ]},
            {"type": "command", "instrument": "$psu",
             "command": "set_output", "args": {"state": "0"}},
        ],
    }

    print("\n[step 3] start_experiment_job (1->3V)")
    rec = await mgr.start_experiment_job(plan)
    job_id = rec.job_id
    for _ in range(400):
        await asyncio.sleep(0.2)
        cur = store.get(job_id)
        if cur and cur.status.value in (
                "completed", "failed", "cancelled", "timeout"):
            break
    cur = store.get(job_id)
    print(f"  -> {job_id} status={cur.status.value if cur else '?'}")
    if not cur or cur.status.value != "completed":
        print(json.dumps(cur.result, default=str)[:600] if cur else "")
        return 1

    mcp = FastMCP("e2e")
    exp.register_tools(mcp, mgr)
    gtool = await mcp.get_tool("get_experiment_results")
    etool = await mcp.get_tool("export_experiment_results")

    r = await gtool.fn(job_id=job_id, limit=10000)
    cols = r["data"]["columns"]
    ok_cols = "sweep_index" in cols and "sweep_value" in cols
    print(f"\n[check cols] {ok_cols} -> {cols}")
    versions = r["data"]["_meta"]["versions"]
    ok_sentinel = versions.get("export_fix") == "v2.6.0"
    print(f"[check sentinel] export_fix={versions.get('export_fix')} "
          f"ok={ok_sentinel} (full={versions})")

    r1 = await gtool.fn(job_id=job_id, instrument=pmx,
                        measurement="measure_voltage", limit=10000)
    rows1 = r1["data"]["rows"]
    ok1 = len(rows1) == 3 and all(
        r["instrument"] == pmx for r in rows1)
    print(f"[check filter] PMX measure_voltage rows={len(rows1)} ok={ok1}")
    for r_ in rows1:
        print(f"    sweep_index={r_['sweep_index']} "
              f"sweep_value={r_['sweep_value']} value={r_['value']}")

    r2 = await gtool.fn(job_id=job_id, sweep_index=2, limit=10000)
    meas2 = [x for x in r2["data"]["rows"]
             if x.get("measurement") == "measure_voltage"]
    ok2 = len(meas2) == 1 and meas2[0]["sweep_index"] == 2
    print(f"[check sweep_index=2] measure rows={len(meas2)} ok={ok2}")

    e = await etool.fn(job_id=job_id, format="csv", instrument=pmx,
                      measurement="measure_voltage")
    ed = e["data"]
    ok3 = ed.get("rows") == 3 and str(ed.get("path")).startswith(
        str(export_dir))
    if ed.get("path") and Path(ed["path"]).exists():
        with open(ed["path"], newline="", encoding="utf-8") as f:
            crows = list(csv.DictReader(f))
        ok3 = ok3 and len(crows) == 3 and "sweep_index" in (
            crows[0].keys() if crows else [])
    print(f"[check csv] rows={ed.get('rows')} under_dir+cols ok={ok3} "
          f"filters={ed.get('filters')}")

    success = all([ok_cols, ok_sentinel, ok1, ok2, ok3])
    print(f"\n[verdict] {'PASS' if success else 'FAIL'}")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
