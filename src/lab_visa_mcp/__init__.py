"""visa-mcp v2.0: PyVISA backend + compatibility shim for lab-executor-mcp.

v2.0 で実験実行 runtime / DSL / extension ecosystem は
`lab-executor-mcp` (新 repo) に分離した。`visa-mcp` 側に残るのは:

- PyVISA backend (`VisaManager` / `SessionManager` / `BusManager`)
- `PyVisaBackend` adapter (`InstrumentBackend` 実装)
- raw VISA tools (env-gated)
- PyVISA resource discovery (`tools/discovery.py`)
- `visa-mcp serve` 互換 entry point
- 旧 import path の **shim** (`lab_visa_mcp.extension` 等は
  `lab_executor.extension` に DeprecationWarning 付きで forward)

詳細: `docs/v2_migration.md` / `docs/raw_visa.md`
"""

__version__ = "2.8.1"
