"""DEPRECATED shim package → `lab_executor.experiment_ir` (visa-mcp v2.0)

This package previously contained the visa-mcp v1.x
implementation. In v2.0 the experiment-execution runtime moved to
`lab-executor-mcp`. Importing `lab_visa_mcp.experiment_ir` now forwards to
`lab_executor.experiment_ir` with a DeprecationWarning.

Migration:
    # old
    from lab_visa_mcp.experiment_ir import X
    from lab_visa_mcp.experiment_ir.compiler import Y
    # new
    from lab_executor.experiment_ir import X
    from lab_executor.experiment_ir.compiler import Y

The shim itself will remain through the v2.x series but may be
removed in v3.0+. See `docs/v2_migration.md`.
"""
from __future__ import annotations
import sys as _sys
import warnings as _warnings

_warnings.warn(
    "lab_visa_mcp.experiment_ir is deprecated; use lab_executor.experiment_ir instead.",
    DeprecationWarning,
    stacklevel=2,
)

import lab_executor.experiment_ir as _le  # noqa: E402

# Re-export top-level attributes
from lab_executor.experiment_ir import *  # noqa: F401,F403,E402

# Submodule import + aliasing so
# `from lab_visa_mcp.experiment_ir.<sub> import X` resolves through
# `lab_executor.experiment_ir.<sub>`.
import lab_executor.experiment_ir.plan as _sub_plan  # noqa: E402
import lab_executor.experiment_ir.step as _sub_step  # noqa: E402
_submodules: dict[str, object] = {
    "lab_visa_mcp.experiment_ir.plan": _sub_plan,
    "lab_visa_mcp.experiment_ir.step": _sub_step,
}
for _name, _mod in _submodules.items():
    _sys.modules[_name] = _mod
del _name, _mod, _submodules, _le, _sys, _warnings
