"""v1.11: PyVisaBackend adapter

v2.0 で **visa-mcp 側 (PyVISA 依存)** に残る backend 実装。

`InstrumentBackend` Protocol を満たす薄い adapter で、既存
`VisaManager` を包む。lab-executor 側 runtime は `InstrumentBackend`
にのみ依存し、`VisaManager` を直接 import しない。

詳細: `docs/separation/notes.md` / `docs/backend_abstraction.md`
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from visa_mcp.visa_manager import VisaManager


class PyVisaBackend:
    """`InstrumentBackend` Protocol を満たす PyVISA adapter (v1.11)

    既存 `VisaManager` 実装をそのまま委譲する。v1.11 では新規 backend
    実体は作らず、runtime 側が `VisaManager` を直接触らないための
    境界としてのみ使う。
    """

    backend_id: str = "pyvisa"

    def __init__(self, visa_manager: "VisaManager | None" = None):
        if visa_manager is None:
            # 遅延 import (lab-executor 側 ownership_check 違反を起こさない)
            from visa_mcp.visa_manager import VisaManager as _VM
            visa_manager = _VM()
        self._visa: "VisaManager" = visa_manager

    async def list_resources(
        self, query: str = "?*::INSTR"
    ) -> list[str]:
        return await self._visa.list_resources(query)

    async def query(
        self,
        resource_name: str,
        command: str,
        timeout_ms: int = 5000,
        read_termination: str = "\n",
        write_termination: str = "\n",
    ) -> str:
        return await self._visa.query(
            resource_name, command,
            timeout_ms=timeout_ms,
            read_termination=read_termination,
            write_termination=write_termination,
        )

    async def write(
        self,
        resource_name: str,
        command: str,
        timeout_ms: int = 5000,
        read_termination: str = "\n",
        write_termination: str = "\n",
    ) -> None:
        await self._visa.write(
            resource_name, command,
            timeout_ms=timeout_ms,
            read_termination=read_termination,
            write_termination=write_termination,
        )

    def close(self) -> None:
        self._visa.close()

    @property
    def visa_manager(self) -> "VisaManager":
        """旧 API へのアクセス (v2.0 で削除候補、shim 用途のみ)"""
        return self._visa
