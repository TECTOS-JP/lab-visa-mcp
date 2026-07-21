"""DEPRECATED shim package → `lab_executor.dsl` (visa-mcp v2.0)

This package previously contained the visa-mcp v1.x
implementation. In v2.0 the experiment-execution runtime moved to
`lab-executor-mcp`. Importing `lab_visa_mcp.dsl` now forwards to
`lab_executor.dsl` with a DeprecationWarning.

Migration:
    # old
    from lab_visa_mcp.dsl import X
    from lab_visa_mcp.dsl.compiler import Y
    # new
    from lab_executor.dsl import X
    from lab_executor.dsl.compiler import Y

The shim itself will remain through the v2.x series but may be
removed in v3.0+. See `docs/v2_migration.md`.
"""
from __future__ import annotations
import sys as _sys
import warnings as _warnings

_warnings.warn(
    "lab_visa_mcp.dsl is deprecated; use lab_executor.dsl instead.",
    DeprecationWarning,
    stacklevel=2,
)

import lab_executor.dsl as _le  # noqa: E402

# Re-export top-level attributes
from lab_executor.dsl import *  # noqa: F401,F403,E402

# Submodule import + aliasing so
# `from lab_visa_mcp.dsl.<sub> import X` resolves through
# `lab_executor.dsl.<sub>`.
import lab_executor.dsl.compiler as _sub_compiler  # noqa: E402
import lab_executor.dsl.schema as _sub_schema  # noqa: E402
import lab_executor.dsl.template as _sub_template  # noqa: E402
_submodules: dict[str, object] = {
    "lab_visa_mcp.dsl.compiler": _sub_compiler,
    "lab_visa_mcp.dsl.schema": _sub_schema,
    "lab_visa_mcp.dsl.template": _sub_template,
}
for _name, _mod in _submodules.items():
    _sys.modules[_name] = _mod
del _name, _mod, _submodules, _le, _sys, _warnings
