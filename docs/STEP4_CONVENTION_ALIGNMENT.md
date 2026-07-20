# Step 4: Backend convention alignment

## Changes

- Replaced the duplicate `InstrumentBackend` Protocol with a deprecated re-export
  shim to `lab_executor.backends.base`. The old import path remains valid and now
  resolves to the runtime's single frozen contract definition.
- Added the `visa_mcp.discovery:make_backend` factory and registered it under the
  `lab_executor.backends` entry-point group as `visa`.
- Made discovery configuration strict: only `None` or an empty mapping is valid.
  Unknown keys, including `visa_manager`, fail closed, so configuration cannot
  inject arbitrary objects.
- Added hardware-free BEF conformance and discovery tests.
- Expanded CI to run the non-hardware unit suite, a dedicated BEF check, released
  runtime integration, allowed-to-fail runtime-main compatibility, and the
  existing wheel build verification.
- Excluded `tests/_legacy_v1_archived` from default pytest collection. Its 70
  files are dead because they depend on removed private v1 functions; they could
  be removed in a separate maintainer decision.

## Prefix choice

The routing prefixes are the interface names used at the beginning of resource
strings accepted by PyVISA's resource-name parser: `GPIB`, `VXI`, `ASRL`, `PXI`,
`TCPIP`, `USB`, `VICP`, `PRLGX-ASRL`, and `PRLGX-TCPIP`. This includes PyVISA's
VICP and Prologix extensions as well as the standard VISA forms. Prefixes
intentionally omit a trailing `::` because VISA resource names
normally include a board number immediately after the interface name, such as
`GPIB0::1::INSTR`, `TCPIP0::host::INSTR`, or `USB0::...::INSTR`. No invented
`VISA::` namespace is used, so existing resource strings route unchanged.

## Uncertainties

None affecting the implementation. Vendor-defined VISA aliases can be arbitrary
strings and therefore cannot be routed reliably by a fixed prefix list; the
factory registers the resource forms supported by PyVISA's parser. PyVISA's
`InterfaceType` enum also contains interfaces such as RIO, FireWire, and RSNRP,
but its resource-name parser does not define corresponding grammars, so those
enum-only names are not advertised as routable prefixes.
