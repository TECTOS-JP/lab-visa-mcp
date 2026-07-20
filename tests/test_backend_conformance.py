from __future__ import annotations

import pytest

from lab_executor.testing.backend_conformance import assert_backend_contract

from visa_mcp.backends.pyvisa_backend import PyVisaBackend


SAMPLE_RESOURCE = "GPIB0::1::INSTR"


class ConformanceVisaManager:
    """In-memory VisaManager substitute that can never access hardware."""

    def __init__(self) -> None:
        self.closed = False

    async def list_resources(self, query: str = "?*::INSTR") -> list[str]:
        return [SAMPLE_RESOURCE]

    async def query(
        self,
        resource_name: str,
        command: str,
        timeout_ms: int = 5000,
        read_termination: str = "\n",
        write_termination: str = "\n",
    ) -> str:
        return "visa-mcp conformance mock"

    async def write(
        self,
        resource_name: str,
        command: str,
        timeout_ms: int = 5000,
        read_termination: str = "\n",
        write_termination: str = "\n",
    ) -> None:
        return None

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_pyvisa_backend_passes_bef_conformance_without_hardware():
    backend = PyVisaBackend(visa_manager=ConformanceVisaManager())
    assert (
        await assert_backend_contract(backend, sample_resource=SAMPLE_RESOURCE)
        is backend
    )
