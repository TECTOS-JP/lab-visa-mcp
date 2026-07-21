"""lab-executor backend entry-point factory."""

from __future__ import annotations

from typing import Any

from lab_executor.backends import BackendRegistration

from lab_visa_mcp.backends.pyvisa_backend import PyVisaBackend


VISA_RESOURCE_PREFIXES = (
    "GPIB",
    "VXI",
    "ASRL",
    "PXI",
    "TCPIP",
    "USB",
    "VICP",
    "PRLGX-ASRL",
    "PRLGX-TCPIP",
)


def make_backend(config: dict[str, Any] | None = None) -> BackendRegistration:
    """Construct the PyVISA backend from strict configuration."""
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise TypeError("visa backend config must be a mapping")
    unknown = set(config)
    if unknown:
        raise ValueError(f"unknown visa backend config keys: {sorted(unknown)!r}")
    return BackendRegistration(
        backend=PyVisaBackend(),
        prefixes=VISA_RESOURCE_PREFIXES,
    )


__all__ = ["VISA_RESOURCE_PREFIXES", "make_backend"]
