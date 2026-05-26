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
    import visa_mcp
    assert visa_mcp.__version__.startswith("2.0.")


def test_lab_executor_mcp_is_installed():
    """v2.0 では visa-mcp は lab-executor-mcp に依存する"""
    import lab_executor
    parts = lab_executor.__version__.split(".")
    assert int(parts[0]) >= 2


# ============================================================
# Shim tests (runtime modules)
# ============================================================


def test_extension_shim_warns_and_forwards():
    """`visa_mcp.extension` は DeprecationWarning 付きで
    `lab_executor.extension` に forward する"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # Reimport to trigger warning fresh
        if "visa_mcp.extension" in sys.modules:
            del sys.modules["visa_mcp.extension"]
        import visa_mcp.extension as ext
    deprecation_warnings = [
        w for w in caught
        if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecation_warnings, (
        f"expected DeprecationWarning, got: "
        f"{[str(w.message) for w in caught]}")


def test_dsl_shim_resolves_submodules():
    """`from visa_mcp.dsl.compiler import X` が動く (submodule alias)"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from visa_mcp.dsl.compiler import validate_and_compile
        assert callable(validate_and_compile)


def test_job_shim_resolves_submodules():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from visa_mcp.job import JobManager
        assert JobManager is not None


def test_instrument_authoring_shim():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from visa_mcp.instrument_authoring import (
            scaffold_instrument_definition,
        )
        assert callable(scaffold_instrument_definition)


def test_registry_shim():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from visa_mcp.registry import validate_instrument_file
        assert callable(validate_instrument_file)


def test_observation_shim():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import visa_mcp.observation
        assert hasattr(visa_mcp.observation, "build_run_summary")


def test_stability_unchanged_via_shim():
    """Stable 43 + Experimental 7 = 50 が shim 経由で確認できる"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from visa_mcp import stability
    flat = [t for ts in stability.STABLE_TOOLS.values() for t in ts]
    exp = [t for ts in stability.EXPERIMENTAL_TOOLS.values()
           for t in ts]
    assert len(flat) == 43
    assert len(exp) == 7


# ============================================================
# Backend layer (visa-mcp 側に残るもの)
# ============================================================


def test_visa_manager_class_available():
    from visa_mcp.visa_manager import VisaManager, VisaError
    assert VisaManager is not None
    assert issubclass(VisaError, Exception)


def test_session_manager_class_available():
    from visa_mcp.session_manager import SessionManager
    assert SessionManager is not None


def test_bus_manager_class_available():
    from visa_mcp.bus_manager import BusManager
    assert BusManager is not None


def test_pyvisa_backend_imports_without_opening_hardware():
    """PyVisaBackend module の import は実機 resource を開かない"""
    import visa_mcp.backends.pyvisa_backend as pb
    assert hasattr(pb, "PyVisaBackend")


def test_pyvisa_backend_satisfies_instrument_backend_protocol():
    """PyVisaBackend が lab_executor.backends.base.InstrumentBackend
    の structural shape を満たす"""
    from visa_mcp.backends.pyvisa_backend import PyVisaBackend
    from lab_executor.backends.base import InstrumentBackend  # noqa
    for name in ("backend_id", "list_resources", "query", "write",
                  "close"):
        assert hasattr(PyVisaBackend, name), (
            f"PyVisaBackend missing {name}")


def test_pyvisa_backend_constructor_lazy():
    """PyVisaBackend() instance 生成 (実 resource は開かない)"""
    from visa_mcp.backends.pyvisa_backend import PyVisaBackend
    b = PyVisaBackend()
    assert b.backend_id == "pyvisa"


# ============================================================
# Tools (visa-mcp 側に残るもの)
# ============================================================


def test_discovery_tool_importable():
    """tools/discovery.py (PyVISA resource 列挙) は visa-mcp 側に残る"""
    import visa_mcp.tools.discovery as disc
    assert hasattr(disc, "register_tools")


def test_commands_tool_importable():
    """tools/commands.py (raw VISA + named command) は visa-mcp 側"""
    import visa_mcp.tools.commands as cmd
    assert hasattr(cmd, "register_tools")


# ============================================================
# CLI smoke (visa-mcp serve / list-resources 互換)
# ============================================================


def test_visa_mcp_cli_version():
    result = subprocess.run(
        [sys.executable, "-m", "visa_mcp.cli", "--help"],
        text=True, capture_output=True, encoding="utf-8",
    )
    # --help は exit 0 で usage を返す
    assert result.returncode == 0, (
        f"stdout: {result.stdout[:200]}\nstderr: {result.stderr[:200]}")
