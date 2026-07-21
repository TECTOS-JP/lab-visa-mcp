"""DEPRECATED shim package → `lab_executor.testing` (visa-mcp v2.0)

This package previously contained the visa-mcp v1.x
implementation. In v2.0 the experiment-execution runtime moved to
`lab-executor-mcp`. Importing `lab_visa_mcp.testing` now forwards to
`lab_executor.testing` with a DeprecationWarning.

Migration:
    # old
    from lab_visa_mcp.testing import X
    from lab_visa_mcp.testing.compiler import Y
    # new
    from lab_executor.testing import X
    from lab_executor.testing.compiler import Y

The shim itself will remain through the v2.x series but may be
removed in v3.0+. See `docs/v2_migration.md`.
"""
from __future__ import annotations
import sys as _sys
import warnings as _warnings

_warnings.warn(
    "lab_visa_mcp.testing is deprecated; use lab_executor.testing instead.",
    DeprecationWarning,
    stacklevel=2,
)

import lab_executor.testing as _le  # noqa: E402

# Re-export top-level attributes
from lab_executor.testing import *  # noqa: F401,F403,E402

# Submodule import + aliasing so
# `from lab_visa_mcp.testing.<sub> import X` resolves through
# `lab_executor.testing.<sub>`.
import lab_executor.testing.mock_instruments as _sub_mock_instruments  # noqa: E402
import lab_executor.testing.benchmark_runner as _sub_benchmark_runner  # noqa: E402
import lab_executor.testing.benchmark_task as _sub_benchmark_task  # noqa: E402
_submodules: dict[str, object] = {
    "lab_visa_mcp.testing.mock_instruments": _sub_mock_instruments,
    "lab_visa_mcp.testing.benchmark_runner": _sub_benchmark_runner,
    "lab_visa_mcp.testing.benchmark_task": _sub_benchmark_task,
}
for _name, _mod in _submodules.items():
    _sys.modules[_name] = _mod
del _name, _mod, _submodules, _le, _sys, _warnings
