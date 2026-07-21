"""DEPRECATED shim package → `lab_executor.group` (visa-mcp v2.0)

This package previously contained the visa-mcp v1.x
implementation. In v2.0 the experiment-execution runtime moved to
`lab-executor-mcp`. Importing `lab_visa_mcp.group` now forwards to
`lab_executor.group` with a DeprecationWarning.

Migration:
    # old
    from lab_visa_mcp.group import X
    from lab_visa_mcp.group.compiler import Y
    # new
    from lab_executor.group import X
    from lab_executor.group.compiler import Y

The shim itself will remain through the v2.x series but may be
removed in v3.0+. See `docs/v2_migration.md`.
"""
from __future__ import annotations
import sys as _sys
import warnings as _warnings

_warnings.warn(
    "lab_visa_mcp.group is deprecated; use lab_executor.group instead.",
    DeprecationWarning,
    stacklevel=2,
)

import lab_executor.group as _le  # noqa: E402

# Re-export top-level attributes
from lab_executor.group import *  # noqa: F401,F403,E402

# Submodule import + aliasing so
# `from lab_visa_mcp.group.<sub> import X` resolves through
# `lab_executor.group.<sub>`.
import lab_executor.group.executor as _sub_executor  # noqa: E402
import lab_executor.group.barrier as _sub_barrier  # noqa: E402
import lab_executor.group.target as _sub_target  # noqa: E402
import lab_executor.group.resolver as _sub_resolver  # noqa: E402
_submodules: dict[str, object] = {
    "lab_visa_mcp.group.executor": _sub_executor,
    "lab_visa_mcp.group.barrier": _sub_barrier,
    "lab_visa_mcp.group.target": _sub_target,
    "lab_visa_mcp.group.resolver": _sub_resolver,
}
for _name, _mod in _submodules.items():
    _sys.modules[_name] = _mod
del _name, _mod, _submodules, _le, _sys, _warnings
