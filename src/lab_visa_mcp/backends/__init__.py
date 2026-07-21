"""v1.11: backend abstraction (実体化)

v1.1 (experimental spike) で導入した `InstrumentBackend` Protocol を、
v1.11 で adapter (`PyVisaBackend` / `MockBackend`) と共に実体化した。

- `base.InstrumentBackend`  : shared Protocol (両 repo が見る)
- `pyvisa_backend.PyVisaBackend` : visa-mcp 側 (PyVISA 透過)
- `mock_backend.MockBackend` : lab-executor-mcp 側 (PyVISA 不要)

v2.0 で実際のリポジトリ分離を行う際、lab-executor 側 runtime module
は `InstrumentBackend` Protocol のみに依存し、`VisaManager` を直接
import しない設計。

詳細: `docs/separation/notes.md` / `docs/backend_abstraction.md`
"""
from lab_visa_mcp.backends.base import InstrumentBackend
from lab_visa_mcp.backends.mock_backend import MockBackend
from lab_visa_mcp.backends.pyvisa_backend import PyVisaBackend

__all__ = ["InstrumentBackend", "PyVisaBackend", "MockBackend"]
