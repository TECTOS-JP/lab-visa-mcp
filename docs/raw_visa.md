# Raw VISA backend (visa-mcp draft, v2.0 以降の責務)

> v1.11 draft。v2.0 で `visa-mcp` リポジトリを **PyVISA backend に特化**
> させたあとの構成を明示するためのドキュメント。`lab-executor-mcp`
> (runtime / DSL / ecosystem) との関係をここに集約する。

## visa-mcp v2.0 以降の責務

v2.0 で大半の runtime / authoring / ecosystem コードは
`lab-executor-mcp` (新 repo) に移送される。`visa-mcp` 側に残るのは:

```
src/visa_mcp/visa_manager.py        ← PyVISA wrapper
src/visa_mcp/bus_manager.py         ← bus 単位 semaphore
src/visa_mcp/session_manager.py     ← session / definition lookup
src/visa_mcp/backends/base.py       ← InstrumentBackend Protocol
                                      (shared, 両 repo が見る)
src/visa_mcp/backends/pyvisa_backend.py
                                    ← Protocol を満たす adapter
src/visa_mcp/tools/discovery.py     ← list_resources
src/visa_mcp/tools/commands_raw_visa.py
                                    ← env-gated raw send_command /
                                      query_instrument
src/visa_mcp/__init__.py            ← shim (旧 import を warning 付き
                                      再 export)
src/visa_mcp/server.py              ← lab-executor runtime に
                                      PyVisaBackend を注入する composition
                                      root (互換 visa-mcp serve)
```

## PyVISA setup (visa-mcp v2.0+)

`visa-mcp` は **PyVISA を required dependency** として持つ。NI-VISA /
Keysight IO Libraries 等の OS-level backend は別途インストールが必要。

```bash
pip install visa-mcp      # PyVISA + visa-mcp 本体
# Windows: NI-VISA Runtime か Keysight IO Libraries を別途 install
# Linux:   pyvisa-py + USB / Serial driver (gpib_ctypes 等を任意)
```

`lab-executor-mcp` 単独 install (PyVISA 不要) は次が動く:

```bash
pip install lab-executor-mcp
# benchmark / validate / dry_run / extension lifecycle が PyVISA 無しで
# 動く (MockBackend 経由)
```

## list_resources

```python
from visa_mcp.backends import PyVisaBackend

backend = PyVisaBackend()
resources = await backend.list_resources()
# → ["GPIB0::5::INSTR", "USB0::0x1234::0x5678::SN001::INSTR", ...]
```

MCP tool 経由:

```
list_resources()  # Stable, v1.0 から不変
```

## Raw VISA tools (env-gated)

`visa-mcp` v2.0 では、raw write / query は **環境変数で明示的に許可**
された場合のみ expose される。

```bash
export VISA_MCP_ALLOW_RAW=1
visa-mcp serve  # raw tools が有効になる
```

該当 MCP tool (Experimental):

| Tool | 説明 | 危険度 |
|------|------|--------|
| `send_command(resource, command)`  | 任意 SCPI write | 高 |
| `query_instrument(resource, command)` | 任意 SCPI query | 中 |

これらは definition pack の `commands:` を経由しない **生 SCPI** 透過
なので、`safety.disallow_terms` / `parameter_validator` 等の保護が
**かからない**。production では使わないこと。

## lab-executor runtime との関係

```
┌─────────────────────────────────────────┐
│ lab-executor-mcp (PyPI: lab-executor-mcp) │
│  - DSL / Job / Observation / Benchmark    │
│  - Definition pack ecosystem              │
│  - MCP tool surface (Stable 43 + Exp 7)   │
│  - InstrumentBackend Protocol を要求      │
└──────────────┬──────────────────────────┘
               │ inject
               ▼
┌─────────────────────────────────────────┐
│ visa-mcp (PyPI: visa-mcp)                │
│  - PyVisaBackend(InstrumentBackend)      │
│  - tools.discovery / tools.commands_raw   │
│  - visa_manager / bus_manager /          │
│    session_manager                        │
│  - server.py: PyVisaBackend を注入して   │
│    lab-executor runtime を起動する shim   │
└─────────────────────────────────────────┘
```

`visa-mcp serve` は `lab-executor-mcp` を呼び出し、PyVisaBackend を
inject する **composition root** として動く。利用者の MCP tool 呼び出し
コードは v1.x と完全互換 (v1.0 で固定した Stable 43 + Experimental 7)。

## v1.x → v2.0 migration

詳細は `docs/v2_migration.md` (v2.0.0-rc1 で公開予定) を参照。要点:

```
# v1.x:
pip install visa-mcp
# v2.0:
pip install visa-mcp    # 自動的に lab-executor-mcp >= 2.0 も install

# 純粋実験 runtime のみ:
pip install lab-executor-mcp
```

import path の deprecation スケジュール:

```
v2.0: from visa_mcp.extension import ... が動く + DeprecationWarning
v2.0: from lab_executor.extension import ... を推奨
v2.2: 旧 import path 削除
```

## References

- `docs/separation/notes.md`
- `docs/separation/module_ownership.yaml`
- `docs/separation/split_manifest.yaml`
- `docs/backend_abstraction.md`
- `docs/naming_and_repository_strategy.md`
