"""DEPRECATED shim package → `lab_executor.models` (visa-mcp v2.0)

This package previously contained the visa-mcp v1.x
implementation. In v2.0 the experiment-execution runtime moved to
`lab-executor-mcp`. Importing `visa_mcp.models` now forwards to
`lab_executor.models` with a DeprecationWarning.

Migration:
    # old
    from visa_mcp.models import X
    from visa_mcp.models.compiler import Y
    # new
    from lab_executor.models import X
    from lab_executor.models.compiler import Y

The shim itself will remain through the v2.x series but may be
removed in v3.0+. See `docs/v2_migration.md`.
"""
from __future__ import annotations
import sys as _sys
import warnings as _warnings

_warnings.warn(
    "visa_mcp.models is deprecated; use lab_executor.models instead.",
    DeprecationWarning,
    stacklevel=2,
)

import lab_executor.models as _le  # noqa: E402

# Re-export top-level attributes
from lab_executor.models import *  # noqa: F401,F403,E402

# Submodule import + aliasing so
# `from visa_mcp.models.<sub> import X` resolves through
# `lab_executor.models.<sub>`.
import lab_executor.models.instrument_def as _sub_instrument_def  # noqa: E402
_submodules: dict[str, object] = {
    "visa_mcp.models.instrument_def": _sub_instrument_def,
}
for _name, _mod in _submodules.items():
    _sys.modules[_name] = _mod
del _name, _mod, _submodules, _le, _sys, _warnings
