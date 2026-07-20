from __future__ import annotations

from importlib import metadata
import sys
import warnings

import pytest

from lab_executor.backends import BackendRegistration

from visa_mcp.backends.pyvisa_backend import PyVisaBackend
from visa_mcp.discovery import VISA_RESOURCE_PREFIXES, make_backend


def test_factory_returns_default_pyvisa_registration():
    registration = make_backend()
    assert isinstance(registration, BackendRegistration)
    assert isinstance(registration.backend, PyVisaBackend)
    assert registration.prefixes == VISA_RESOURCE_PREFIXES
    assert "VISA::" not in registration.prefixes


def test_factory_rejects_unknown_or_malformed_config():
    with pytest.raises(ValueError, match="unknown"):
        make_backend({"visa_manager": object()})
    with pytest.raises(TypeError, match="mapping"):
        make_backend([])  # type: ignore[arg-type]


def test_deprecated_base_module_reexports_runtime_protocol():
    from lab_executor.backends.base import InstrumentBackend as RuntimeProtocol

    sys.modules.pop("visa_mcp.backends.base", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from visa_mcp.backends.base import InstrumentBackend as ShimProtocol

    assert ShimProtocol is RuntimeProtocol
    assert any(
        issubclass(item.category, DeprecationWarning)
        and "lab_executor.backends.base" in str(item.message)
        for item in caught
    )


def test_installed_entry_point_loads_factory():
    matches = [
        entry_point
        for entry_point in metadata.entry_points(group="lab_executor.backends")
        if entry_point.name == "visa"
    ]
    if not matches:
        pytest.skip("editable installation metadata is required")
    assert len(matches) == 1
    assert matches[0].load() is make_backend
