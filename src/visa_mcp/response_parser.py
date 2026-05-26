"""DEPRECATED shim → `lab_executor.response_parser` (visa-mcp v2.0)

This module previously contained the visa-mcp v1.x implementation.
In v2.0 the experiment-execution runtime moved to
`lab-executor-mcp`. Importing `visa_mcp.response_parser`
now forwards to `lab_executor.response_parser` with a DeprecationWarning.

Migration:
    # old
    from visa_mcp.response_parser import X
    # new
    from lab_executor.response_parser import X

The shim itself will remain through the v2.x series but may be
removed in v3.0+. See `docs/v2_migration.md`.
"""
from __future__ import annotations
import warnings as _warnings

_warnings.warn(
    "visa_mcp.response_parser is deprecated; "
    "use lab_executor.response_parser instead.",
    DeprecationWarning,
    stacklevel=2,
)

from lab_executor.response_parser import *  # noqa: F401,F403
