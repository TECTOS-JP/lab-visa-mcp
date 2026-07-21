"""v2.0.0-rc1 shim 化 script.

visa-mcp v1.11 で持っていた lab-executor owner runtime module を、
`lab_executor.*` への薄い shim に置き換える (DeprecationWarning 付き)。

実機 backend (`VisaManager` / `SessionManager` / `BusManager` /
`backends/pyvisa_backend.py` / `tools/discovery.py`) と raw VISA tools
は visa-mcp 側に残す。

Usage:
    python scripts/convert_to_shim.py
"""
from __future__ import annotations
import argparse
import shutil
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
REPO_ROOT = _THIS.parent.parent
SRC_ROOT = REPO_ROOT / "src" / "lab_visa_mcp"

# v2.0 shim 対象: 単一 file
SINGLE_FILE_SHIMS = [
    # (lab_visa_mcp path under src/lab_visa_mcp/, lab_executor module path)
    ("audit.py", "lab_executor.audit"),
    ("extension.py", "lab_executor.extension"),
    ("extension_authoring.py", "lab_executor.extension_authoring"),
    ("extension_catalog.py", "lab_executor.extension_catalog"),
    ("extension_install.py", "lab_executor.extension_install"),
    ("extension_integrity.py", "lab_executor.extension_integrity"),
    ("extension_packaging.py", "lab_executor.extension_packaging"),
    ("instrument_authoring.py", "lab_executor.instrument_authoring"),
    ("instrument_registry.py", "lab_executor.instrument_registry"),
    ("observation.py", "lab_executor.observation"),
    ("polling_executor.py", "lab_executor.polling_executor"),
    ("response_envelope.py", "lab_executor.response_envelope"),
    ("response_parser.py", "lab_executor.response_parser"),
    ("safety.py", "lab_executor.safety"),
    ("stability.py", "lab_executor.stability"),
    ("state_query.py", "lab_executor.state_query"),
    ("system_config.py", "lab_executor.system_config"),
    ("recipe_executor.py", "lab_executor.recipe_executor"),
    ("step_executor.py", "lab_executor.step_executor"),
    ("registry.py", "lab_executor.registry"),
]

# v2.0 shim 対象: package (submodule alias 含む)
PACKAGE_SHIMS = [
    # (lab_visa_mcp package, lab_executor package, submodules to alias)
    ("dsl", "lab_executor.dsl",
        ["compiler", "schema", "template"]),
    ("job", "lab_executor.job",
        ["manager", "store", "scheduler", "state_machine"]),
    ("group", "lab_executor.group",
        ["executor", "barrier", "target", "resolver"]),
    ("experiment_ir", "lab_executor.experiment_ir",
        ["plan", "step"]),
    ("models", "lab_executor.models",
        ["instrument_def"]),
    ("testing", "lab_executor.testing",
        ["mock_instruments", "benchmark_runner", "benchmark_task"]),
    ("utils", "lab_executor.utils",
        ["condition", "expression", "idn_matcher", "param_validator"]),
]


def make_single_file_shim(visa_name: str, le_module: str) -> str:
    return f'''"""DEPRECATED shim → `{le_module}` (visa-mcp v2.0)

This module previously contained the visa-mcp v1.x implementation.
In v2.0 the experiment-execution runtime moved to
`lab-executor-mcp`. Importing `lab_visa_mcp.{visa_name.replace(".py","")}`
now forwards to `{le_module}` with a DeprecationWarning.

Migration:
    # old
    from lab_visa_mcp.{visa_name.replace(".py","")} import X
    # new
    from {le_module} import X

The shim itself will remain through the v2.x series but may be
removed in v3.0+. See `docs/v2_migration.md`.
"""
from __future__ import annotations
import warnings as _warnings

_warnings.warn(
    "lab_visa_mcp.{visa_name.replace(".py","")} is deprecated; "
    "use {le_module} instead.",
    DeprecationWarning,
    stacklevel=2,
)

from {le_module} import *  # noqa: F401,F403
'''


def make_package_init_shim(visa_pkg: str, le_pkg: str,
                            submodules: list[str]) -> str:
    submod_imports = "\n".join(
        f"import {le_pkg}.{sm} as _sub_{sm}  # noqa: E402"
        for sm in submodules
    )
    submod_aliases = "\n".join(
        f'    "lab_visa_mcp.{visa_pkg}.{sm}": _sub_{sm},'
        for sm in submodules
    )
    return f'''"""DEPRECATED shim package → `{le_pkg}` (visa-mcp v2.0)

This package previously contained the visa-mcp v1.x
implementation. In v2.0 the experiment-execution runtime moved to
`lab-executor-mcp`. Importing `lab_visa_mcp.{visa_pkg}` now forwards to
`{le_pkg}` with a DeprecationWarning.

Migration:
    # old
    from lab_visa_mcp.{visa_pkg} import X
    from lab_visa_mcp.{visa_pkg}.compiler import Y
    # new
    from {le_pkg} import X
    from {le_pkg}.compiler import Y

The shim itself will remain through the v2.x series but may be
removed in v3.0+. See `docs/v2_migration.md`.
"""
from __future__ import annotations
import sys as _sys
import warnings as _warnings

_warnings.warn(
    "lab_visa_mcp.{visa_pkg} is deprecated; use {le_pkg} instead.",
    DeprecationWarning,
    stacklevel=2,
)

import {le_pkg} as _le  # noqa: E402

# Re-export top-level attributes
from {le_pkg} import *  # noqa: F401,F403,E402

# Submodule import + aliasing so
# `from lab_visa_mcp.{visa_pkg}.<sub> import X` resolves through
# `{le_pkg}.<sub>`.
{submod_imports}
_submodules: dict[str, object] = {{
{submod_aliases}
}}
for _name, _mod in _submodules.items():
    _sys.modules[_name] = _mod
del _name, _mod, _submodules, _le, _sys, _warnings
'''


def shim_single_file(rel: str, le_module: str, dry_run: bool = False):
    src = SRC_ROOT / rel
    if not src.exists():
        print(f"  skip (not found): {rel}")
        return
    content = make_single_file_shim(rel, le_module)
    if dry_run:
        print(f"  would shim: {rel} -> {le_module} "
              f"({len(content)} bytes)")
    else:
        src.write_text(content, encoding="utf-8")
        print(f"  shimmed: {rel} -> {le_module}")


def shim_package(visa_pkg: str, le_pkg: str,
                  submodules: list[str], dry_run: bool = False):
    pkg_dir = SRC_ROOT / visa_pkg
    if not pkg_dir.exists():
        print(f"  skip (not found): {visa_pkg}/")
        return
    # Rewrite __init__.py
    init_path = pkg_dir / "__init__.py"
    content = make_package_init_shim(
        visa_pkg, le_pkg, submodules,
    )
    if dry_run:
        print(f"  would shim package: {visa_pkg}/__init__.py -> {le_pkg}")
        return
    init_path.write_text(content, encoding="utf-8")
    # Remove other .py files (they are now aliased via sys.modules)
    # Keep only __init__.py - submodules become aliases
    removed: list[str] = []
    for sub_file in pkg_dir.rglob("*.py"):
        if sub_file.name == "__init__.py":
            continue
        try:
            sub_file.unlink()
            removed.append(sub_file.name)
        except Exception:
            pass
    # Remove empty subdirectories
    for sub_dir in sorted(
        [p for p in pkg_dir.rglob("*") if p.is_dir()],
        reverse=True,
    ):
        try:
            sub_dir.rmdir()
        except OSError:
            pass
    print(f"  shimmed package: {visa_pkg}/ "
          f"(removed {len(removed)} files)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="v2.0.0-rc1 shim 化",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    print("=== single-file shims ===")
    for rel, le_module in SINGLE_FILE_SHIMS:
        shim_single_file(rel, le_module, dry_run=args.dry_run)

    print("\n=== package shims ===")
    for visa_pkg, le_pkg, submodules in PACKAGE_SHIMS:
        shim_package(visa_pkg, le_pkg, submodules,
                      dry_run=args.dry_run)

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
