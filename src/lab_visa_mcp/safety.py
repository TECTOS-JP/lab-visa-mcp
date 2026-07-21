"""DEPRECATED shim → `lab_executor.safety` (visa-mcp v2.0)

This module previously contained the visa-mcp v1.x implementation.
In v2.0 the experiment-execution runtime moved to
`lab-executor-mcp`. Importing `lab_visa_mcp.safety`
now forwards to `lab_executor.safety` with a DeprecationWarning.

Migration:
    # old
    from lab_visa_mcp.safety import X
    # new
    from lab_executor.safety import X

The shim itself will remain through the v2.x series but may be
removed in v3.0+. See `docs/v2_migration.md`.
"""
from __future__ import annotations
import warnings as _warnings

_warnings.warn(
    "lab_visa_mcp.safety is deprecated; "
    "use lab_executor.safety instead.",
    DeprecationWarning,
    stacklevel=2,
)

from lab_executor.safety import *  # noqa: F401,F403
