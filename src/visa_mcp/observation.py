"""DEPRECATED shim → `lab_executor.observation` (visa-mcp v2.0)

This module previously contained the visa-mcp v1.x implementation.
In v2.0 the experiment-execution runtime moved to
`lab-executor-mcp`. Importing `visa_mcp.observation`
now forwards to `lab_executor.observation` with a DeprecationWarning.

Migration:
    # old
    from visa_mcp.observation import X
    # new
    from lab_executor.observation import X

The shim itself will remain through the v2.x series but may be
removed in v3.0+. See `docs/v2_migration.md`.
"""
from __future__ import annotations
import warnings as _warnings

_warnings.warn(
    "visa_mcp.observation is deprecated; "
    "use lab_executor.observation instead.",
    DeprecationWarning,
    stacklevel=2,
)

from lab_executor.observation import *  # noqa: F401,F403
