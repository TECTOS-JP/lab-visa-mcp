"""DEPRECATED shim → `lab_executor.backends.base` (visa-mcp v2.0)

This module previously contained the visa-mcp v1.x implementation.
In v2.0 the experiment-execution runtime moved to
`lab-executor-mcp`. Importing `visa_mcp.backends.base`
now forwards to `lab_executor.backends.base` with a DeprecationWarning.

Migration:
    # old
    from visa_mcp.backends.base import InstrumentBackend
    # new
    from lab_executor.backends.base import InstrumentBackend

The shim itself will remain through the v2.x series but may be
removed in v3.0+. See `docs/v2_migration.md`.
"""
from __future__ import annotations
import warnings as _warnings

_warnings.warn(
    "visa_mcp.backends.base is deprecated; "
    "use lab_executor.backends.base instead.",
    DeprecationWarning,
    stacklevel=2,
)

from lab_executor.backends.base import *  # noqa: F401,F403
