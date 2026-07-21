"""DEPRECATED shim package → `lab_executor.utils` (visa-mcp v2.0)

This package previously contained the visa-mcp v1.x
implementation. In v2.0 the experiment-execution runtime moved to
`lab-executor-mcp`. Importing `lab_visa_mcp.utils` now forwards to
`lab_executor.utils` with a DeprecationWarning.

Migration:
    # old
    from lab_visa_mcp.utils import X
    from lab_visa_mcp.utils.compiler import Y
    # new
    from lab_executor.utils import X
    from lab_executor.utils.compiler import Y

The shim itself will remain through the v2.x series but may be
removed in v3.0+. See `docs/v2_migration.md`.
"""
from __future__ import annotations
import sys as _sys
import warnings as _warnings

_warnings.warn(
    "lab_visa_mcp.utils is deprecated; use lab_executor.utils instead.",
    DeprecationWarning,
    stacklevel=2,
)

import lab_executor.utils as _le  # noqa: E402

# Re-export top-level attributes
from lab_executor.utils import *  # noqa: F401,F403,E402

# Submodule import + aliasing so
# `from lab_visa_mcp.utils.<sub> import X` resolves through
# `lab_executor.utils.<sub>`.
import lab_executor.utils.condition as _sub_condition  # noqa: E402
import lab_executor.utils.expression as _sub_expression  # noqa: E402
import lab_executor.utils.idn_matcher as _sub_idn_matcher  # noqa: E402
import lab_executor.utils.param_validator as _sub_param_validator  # noqa: E402
_submodules: dict[str, object] = {
    "lab_visa_mcp.utils.condition": _sub_condition,
    "lab_visa_mcp.utils.expression": _sub_expression,
    "lab_visa_mcp.utils.idn_matcher": _sub_idn_matcher,
    "lab_visa_mcp.utils.param_validator": _sub_param_validator,
}
for _name, _mod in _submodules.items():
    _sys.modules[_name] = _mod
del _name, _mod, _submodules, _le, _sys, _warnings
