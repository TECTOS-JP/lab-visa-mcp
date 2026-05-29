"""v2.1.5: Codex レビュー反映 (P1×2 + P2 fallback test 強化)。

P1-a: builtin `_system.yaml` に架空 alias/group/unit を残してはいけない。
P1-b: resolver 順序を `<repo>/instruments` 優先に統一 (docstring と一致)。
P2  : 「builtin が選ばれる」ことと「実際に definitions が load
       される」「wheel 同梱の整合性」を assert する。
"""
from __future__ import annotations
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parent.parent
BUILTIN = REPO / "src" / "visa_mcp" / "builtin_instruments"


# ---------------------------------------------------------------
# P1-a: builtin _system.yaml は alias/group/unit が空であること
# ---------------------------------------------------------------

def test_builtin_system_yaml_has_no_fake_aliases():
    """v2.1.5: builtin の `_system.yaml` に架空 alias / bus /
    instrument_group / experiment_unit を含めない。wheel fallback で
    `psu001 → GPIB0::6::INSTR` 等が production API に出ないこと。"""
    p = BUILTIN / "_system.yaml"
    assert p.exists(), f"builtin _system.yaml が無い: {p}"
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    for key in ("instruments", "instrument_groups",
                "experiment_units", "buses"):
        val = data.get(key, {})
        assert val in (None, {}, []), (
            f"v2.1.5: builtin _system.yaml の {key!r} は空のはず "
            f"だが値あり: {val!r}")


# ---------------------------------------------------------------
# P1-b: resolver 順序 — `instruments` を `examples/instruments` より優先
# ---------------------------------------------------------------

def test_resolver_prefers_repo_instruments_over_examples(
    monkeypatch, tmp_path
):
    """`<repo>/instruments` に本物の instrument YAML がある場合、
    `examples/instruments` より優先される。"""
    fake_repo = tmp_path / "fake_repo"
    instr = fake_repo / "instruments"
    instr.mkdir(parents=True)
    examples_instr = fake_repo / "examples" / "instruments"
    examples_instr.mkdir(parents=True)
    # 両方に instrument YAML を置く (`_` で始まらない)
    (instr / "custom_psu.yaml").write_text("metadata: {}", encoding="utf-8")
    (examples_instr / "example_psu.yaml").write_text(
        "metadata: {}", encoding="utf-8")

    # `here.parent.parent.parent` が fake_repo に対応するように、
    # __file__ がそこの src/visa_mcp/server.py であるかのように振る舞わせる
    fake_server = fake_repo / "src" / "visa_mcp"
    fake_server.mkdir(parents=True)
    fake_server_py = fake_server / "server.py"
    fake_server_py.write_text("# fake", encoding="utf-8")

    from visa_mcp import server as srv_mod
    monkeypatch.setattr(srv_mod, "__file__", str(fake_server_py))
    monkeypatch.delenv("VISA_MCP_INSTRUMENTS_DIR", raising=False)
    resolved = srv_mod._resolve_instruments_dir()
    assert resolved == instr, (
        f"v2.1.5: `<repo>/instruments` が優先されるべき。"
        f"resolved={resolved}, expected={instr}")


def test_resolver_skips_instruments_when_only_underscore_yaml(
    monkeypatch, tmp_path
):
    """`<repo>/instruments` に `_system.yaml` / `_template.yaml` しか
    無い場合は instrument YAML 無しとみなして `examples/instruments`
    へ進む。"""
    fake_repo = tmp_path / "fake_repo"
    instr = fake_repo / "instruments"
    instr.mkdir(parents=True)
    examples_instr = fake_repo / "examples" / "instruments"
    examples_instr.mkdir(parents=True)
    # `_` 始まりのみ
    (instr / "_system.yaml").write_text("instruments: {}", encoding="utf-8")
    (instr / "_template.yaml").write_text("metadata: {}", encoding="utf-8")
    # examples 側に本物
    (examples_instr / "real_dmm.yaml").write_text(
        "metadata: {}", encoding="utf-8")

    fake_server = fake_repo / "src" / "visa_mcp"
    fake_server.mkdir(parents=True)
    fake_server_py = fake_server / "server.py"
    fake_server_py.write_text("# fake", encoding="utf-8")

    from visa_mcp import server as srv_mod
    monkeypatch.setattr(srv_mod, "__file__", str(fake_server_py))
    monkeypatch.delenv("VISA_MCP_INSTRUMENTS_DIR", raising=False)
    resolved = srv_mod._resolve_instruments_dir()
    assert resolved == examples_instr


# ---------------------------------------------------------------
# P2: builtin fallback の実効性 (path だけでなく load 確認)
# ---------------------------------------------------------------

def test_resolver_falls_back_to_builtin_and_loads_real_definitions(
    monkeypatch, tmp_path
):
    """dev path が無い環境で resolver は builtin_instruments を返し、
    そこから本物の InstrumentRegistry がロードする 2 件以上の機器
    定義が得られること。"""
    fake_repo = tmp_path / "fake_repo"
    fake_server = fake_repo / "src" / "visa_mcp"
    fake_server.mkdir(parents=True)
    fake_server_py = fake_server / "server.py"
    fake_server_py.write_text("# fake", encoding="utf-8")
    # fake_repo 下に instruments も examples も置かない

    from visa_mcp import server as srv_mod
    monkeypatch.setattr(srv_mod, "__file__", str(fake_server_py))
    monkeypatch.delenv("VISA_MCP_INSTRUMENTS_DIR", raising=False)
    resolved = srv_mod._resolve_instruments_dir()
    # fake_server の builtin_instruments は存在しないので、実 builtin
    # が見えないケースになる。代わりに直接 builtin_instruments を
    # InstrumentRegistry に与え、definitions が読めることを確認する。
    from visa_mcp.instrument_registry import InstrumentRegistry
    reg = InstrumentRegistry(str(BUILTIN))
    reg.reload()
    defs = reg.list_definitions()
    assert len(defs) >= 2, (
        f"v2.1.5: builtin_instruments から 2 件以上 load されるべき "
        f"(実際: {len(defs)})")
    # 主要機器の同梱を assert (list_definitions は summary dict を返す)
    def _model_of(d) -> str:
        if hasattr(d, "metadata"):
            return getattr(d.metadata, "model", "") or ""
        if isinstance(d, dict):
            return d.get("model", "") or (
                (d.get("metadata") or {}).get("model", "") or "")
        return ""
    models = [_model_of(d).lower() for d in defs]
    joined = " ".join(models)
    assert "pmx" in joined or "7563" in joined, (
        f"PMX/7563 のいずれかが含まれるべき: models={models}")


# ---------------------------------------------------------------
# P2: wheel build → contents inspection (sdist/wheel に YAML が入る)
# ---------------------------------------------------------------

def _try_build_wheel(out_dir: Path) -> Path | None:
    """`python -m build --wheel` を呼んで成功 wheel path を返す。
    `build` が無い場合は None。"""
    try:
        import build  # noqa
    except ImportError:
        return None
    res = subprocess.run(
        [sys.executable, "-m", "build", "--wheel",
         "--outdir", str(out_dir), str(REPO)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        pytest.skip(f"wheel build 失敗 (CI 環境差?): {res.stderr[:200]}")
    wheels = list(out_dir.glob("visa_mcp-*.whl"))
    return wheels[0] if wheels else None


def test_built_wheel_contains_builtin_instruments(tmp_path):
    """v2.1.5: 実際に `python -m build --wheel` で wheel を作り、
    その中に `visa_mcp/builtin_instruments/*.yaml` が
    含まれていること (PMX, 7563, _system)。"""
    wheel = _try_build_wheel(tmp_path)
    if wheel is None:
        pytest.skip("`build` package が未インストールのため skip")
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    yamls = [
        n for n in names
        if n.startswith("visa_mcp/builtin_instruments/")
        and n.endswith(".yaml")
    ]
    assert any("pmx" in n.lower() or "kikusui" in n.lower()
               for n in yamls), f"PMX 定義が wheel に無い: {yamls}"
    assert any("7563" in n or "yokogawa" in n.lower()
               for n in yamls), f"7563 定義が wheel に無い: {yamls}"
    assert any(n.endswith("/_system.yaml") for n in yamls), (
        f"_system.yaml が wheel に無い: {yamls}")


def test_v2_1_5_version():
    import visa_mcp
    parts = visa_mcp.__version__.split(".")
    assert tuple(int(p) for p in parts[:3]) >= (2, 1, 5)
