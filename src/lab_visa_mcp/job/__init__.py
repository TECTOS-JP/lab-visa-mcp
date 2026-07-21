"""DEPRECATED shim package → `lab_executor.job` (visa-mcp v2.0)

This package previously contained the visa-mcp v1.x
implementation. In v2.0 the experiment-execution runtime moved to
`lab-executor-mcp`. Importing `lab_visa_mcp.job` now forwards to
`lab_executor.job` with a DeprecationWarning.

Migration:
    # old
    from lab_visa_mcp.job import X
    from lab_visa_mcp.job.compiler import Y
    # new
    from lab_executor.job import X
    from lab_executor.job.compiler import Y

The shim itself will remain through the v2.x series but may be
removed in v3.0+. See `docs/v2_migration.md`.
"""
from __future__ import annotations
import sys as _sys
import warnings as _warnings

_warnings.warn(
    "lab_visa_mcp.job is deprecated; use lab_executor.job instead.",
    DeprecationWarning,
    stacklevel=2,
)

import lab_executor.job as _le  # noqa: E402

# Re-export top-level attributes
from lab_executor.job import *  # noqa: F401,F403,E402

# Submodule import + aliasing so
# `from lab_visa_mcp.job.<sub> import X` resolves through
# `lab_executor.job.<sub>`.
import lab_executor.job.manager as _sub_manager  # noqa: E402
import lab_executor.job.store as _sub_store  # noqa: E402
import lab_executor.job.scheduler as _sub_scheduler  # noqa: E402
import lab_executor.job.state_machine as _sub_state_machine  # noqa: E402
_submodules: dict[str, object] = {
    "lab_visa_mcp.job.manager": _sub_manager,
    "lab_visa_mcp.job.store": _sub_store,
    "lab_visa_mcp.job.scheduler": _sub_scheduler,
    "lab_visa_mcp.job.state_machine": _sub_state_machine,
}
for _name, _mod in _submodules.items():
    _sys.modules[_name] = _mod
del _name, _mod, _submodules, _le, _sys, _warnings
