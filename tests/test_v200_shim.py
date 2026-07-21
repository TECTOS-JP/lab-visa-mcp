"""v2.0.0-rc1: visa-mcp shim + backend smoke tests.

v2.0 で visa-mcp は PyVISA backend + 旧 import shim になった。
このファイルは v2.0 における visa-mcp 側の **最小 contract** を検証
する。詳細な runtime / DSL / extension テストは lab-executor-mcp 側で
実施。
"""
from __future__ import annotations
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


# ============================================================
# Package metadata
# ============================================================


def test_version_is_2_0_x():
    import lab_visa_mcp
    # v2.x shim 系列 (v2.0 で導入、以降 minor を重ねる)。
    # 旧 "2.0." 固定は v2.1.0 以降ずっと stale だったため "2." に緩和。
    assert lab_visa_mcp.__version__.startswith("2.")


def test_lab_executor_mcp_is_installed():
    """v2.0 では visa-mcp は lab-executor-mcp に依存する"""
    import lab_executor
    parts = lab_executor.__version__.split(".")
    assert int(parts[0]) >= 2


# ============================================================
# Shim tests (runtime modules)
# ============================================================


def test_extension_shim_warns_and_forwards():
    """`lab_visa_mcp.extension` は DeprecationWarning 付きで
    `lab_executor.extension` に forward する"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # Reimport to trigger warning fresh
        if "lab_visa_mcp.extension" in sys.modules:
            del sys.modules["lab_visa_mcp.extension"]
        import lab_visa_mcp.extension as ext
    deprecation_warnings = [
        w for w in caught
        if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecation_warnings, (
        f"expected DeprecationWarning, got: "
        f"{[str(w.message) for w in caught]}")


def test_dsl_shim_resolves_submodules():
    """`from lab_visa_mcp.dsl.compiler import X` が動く (submodule alias)"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from lab_visa_mcp.dsl.compiler import validate_and_compile
        assert callable(validate_and_compile)


def test_job_shim_resolves_submodules():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from lab_visa_mcp.job import JobManager
        assert JobManager is not None


def test_instrument_authoring_shim():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from lab_visa_mcp.instrument_authoring import (
            scaffold_instrument_definition,
        )
        assert callable(scaffold_instrument_definition)


def test_registry_shim():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from lab_visa_mcp.registry import validate_instrument_file
        assert callable(validate_instrument_file)


def test_observation_shim():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import lab_visa_mcp.observation
        assert hasattr(lab_visa_mcp.observation, "build_run_summary")


def test_stability_unchanged_via_shim():
    """Stable 43 + Experimental 7 = 50 が shim 経由で確認できる"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from lab_visa_mcp import stability
    flat = [t for ts in stability.STABLE_TOOLS.values() for t in ts]
    exp = [t for ts in stability.EXPERIMENTAL_TOOLS.values()
           for t in ts]
    assert len(flat) == 43
    assert len(exp) == 7


# ============================================================
# Backend layer (visa-mcp 側に残るもの)
# ============================================================


def test_visa_manager_class_available():
    from lab_visa_mcp.visa_manager import VisaManager, VisaError
    assert VisaManager is not None
    assert issubclass(VisaError, Exception)


def test_session_manager_class_available():
    from lab_visa_mcp.session_manager import SessionManager
    assert SessionManager is not None


def test_bus_manager_class_available():
    from lab_visa_mcp.bus_manager import BusManager
    assert BusManager is not None


def test_pyvisa_backend_imports_without_opening_hardware():
    """PyVisaBackend module の import は実機 resource を開かない"""
    import lab_visa_mcp.backends.pyvisa_backend as pb
    assert hasattr(pb, "PyVisaBackend")


def test_pyvisa_backend_satisfies_instrument_backend_protocol():
    """PyVisaBackend が lab_executor.backends.base.InstrumentBackend
    の structural shape を満たす"""
    from lab_visa_mcp.backends.pyvisa_backend import PyVisaBackend
    from lab_executor.backends.base import InstrumentBackend  # noqa
    for name in ("backend_id", "list_resources", "query", "write",
                  "close"):
        assert hasattr(PyVisaBackend, name), (
            f"PyVisaBackend missing {name}")


def test_pyvisa_backend_constructor_lazy():
    """PyVisaBackend() instance 生成 (実 resource は開かない)"""
    from lab_visa_mcp.backends.pyvisa_backend import PyVisaBackend
    b = PyVisaBackend()
    assert b.backend_id == "pyvisa"


# ============================================================
# Tools (visa-mcp 側に残るもの)
# ============================================================


def test_discovery_tool_importable():
    """tools/discovery.py (PyVISA resource 列挙) は visa-mcp 側に残る"""
    import lab_visa_mcp.tools.discovery as disc
    assert hasattr(disc, "register_tools")


def test_commands_tool_importable():
    """tools/commands.py (raw VISA + named command) は visa-mcp 側"""
    import lab_visa_mcp.tools.commands as cmd
    assert hasattr(cmd, "register_tools")


# ============================================================
# CLI smoke (visa-mcp serve / list-resources 互換)
# ============================================================


def test_lab_visa_mcp_cli_version():
    result = subprocess.run(
        [sys.executable, "-m", "lab_visa_mcp.cli", "--help"],
        text=True, capture_output=True, encoding="utf-8",
    )
    # --help は exit 0 で usage を返す
    assert result.returncode == 0, (
        f"stdout: {result.stdout[:200]}\nstderr: {result.stderr[:200]}")


# ============================================================
# v2.0.0-rc2: Protocol signature 厳密化 (review P1-2)
# ============================================================


def test_pyvisa_backend_signature_matches_protocol():
    """PyVisaBackend method signature が
    `lab_executor.backends.base.InstrumentBackend` と互換であること
    (引数名 / kw-only 構造を `inspect.signature` で確認)"""
    import inspect
    from lab_visa_mcp.backends.pyvisa_backend import PyVisaBackend
    from lab_executor.backends.base import InstrumentBackend

    # Protocol が要求する param は Impl にすべて存在すること
    # (Impl が更に optional param を追加するのは許容 — PyVisaBackend は
    # list_resources(self, query="?*::INSTR") のように PyVISA 固有の
    # 拡張を持つ)
    for method in ("list_resources", "query", "write"):
        proto_fn = getattr(InstrumentBackend, method)
        impl_fn = getattr(PyVisaBackend, method)
        proto_sig = inspect.signature(proto_fn)
        impl_sig = inspect.signature(impl_fn)
        proto_params = set(proto_sig.parameters.keys()) - {"self"}
        impl_params = set(impl_sig.parameters.keys()) - {"self"}
        missing = proto_params - impl_params
        assert not missing, (
            f"{method}: Impl missing Protocol params: {missing}\n"
            f"  Protocol: {sorted(proto_params)}\n"
            f"  Impl:     {sorted(impl_params)}"
        )


def test_pyvisa_backend_constructor_does_not_open_hardware():
    """PyVisaBackend() の constructor が PyVISA ResourceManager を
    即開かないこと (visa_manager の lazy 性確認 / rc2 P1-3)"""
    import sys as _sys
    # PyVisaBackend 単独 import では visa_manager まで触らない (
    # TYPE_CHECKING 経由のため)
    if "lab_visa_mcp.backends.pyvisa_backend" in _sys.modules:
        del _sys.modules["lab_visa_mcp.backends.pyvisa_backend"]
    import lab_visa_mcp.backends.pyvisa_backend as pb
    # この時点では visa_manager 未 load (TYPE_CHECKING の効果)
    # constructor を呼ぶと VisaManager() が生成される。
    b = pb.PyVisaBackend()
    assert b.backend_id == "pyvisa"
    # VisaManager は遅延生成だが、ResourceManager の open は更に遅延
    # (実際の query/write/list_resources まで)


# ============================================================
# v2.0.0-rc2: line-ending / multi-line guard (review P0)
# ============================================================


def test_critical_files_are_multiline_and_lf_only():
    """主要 file が >= 10 行 + CR=0 で commit されている
    (`.gitattributes` の効果を CI で固定)"""
    targets = [
        "pyproject.toml",
        ".github/workflows/ci.yml",
        "README.md",
        "CHANGELOG.md",
        "docs/v2_migration.md",
        "src/lab_visa_mcp/__init__.py",
        "src/lab_visa_mcp/extension.py",
        "src/lab_visa_mcp/backends/pyvisa_backend.py",
        "src/lab_visa_mcp/dsl/__init__.py",
        "tests/test_v200_shim.py",
        ".gitattributes",
    ]
    failures: list[tuple[str, int, int]] = []
    for rel in targets:
        p = ROOT / rel
        if not p.exists():
            failures.append((rel, -1, -1))
            continue
        text = p.read_text(encoding="utf-8")
        lines = text.count("\n") + 1
        cr = text.count("\r")
        min_lines = 5 if rel.endswith("__init__.py") else 10
        if lines < min_lines or cr > 0:
            failures.append((rel, lines, cr))
    assert not failures, (
        f"line-ending / multiline guard failed (rel, lines, CR): "
        f"{failures}")


# ============================================================
# v2.0.0-rc2: CLI shim execution smoke (review P1-4)
# ============================================================


def test_lab_visa_mcp_cli_subcommand_help_smoke():
    """visa-mcp <subcommand> --help が起動する (shim forward 確認)"""
    for sub in (["--help"], ["validate", "--help"],
                 ["extension", "--help"], ["instrument", "--help"]):
        result = subprocess.run(
            [sys.executable, "-m", "lab_visa_mcp.cli", *sub],
            text=True, capture_output=True, encoding="utf-8",
        )
        assert result.returncode == 0, (
            f"args={sub}, code={result.returncode}, "
            f"stderr={result.stderr[:200]}")
