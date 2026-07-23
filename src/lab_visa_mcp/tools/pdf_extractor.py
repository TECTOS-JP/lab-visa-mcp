"""Re-export of ``lab_executor.tools.pdf_extractor`` (visa-mcp v2.8.2).

This module was a byte-for-byte copy of the runtime implementation. Keeping a
second copy meant every runtime fix had to be applied twice, and the ones that
were not have already diverged elsewhere in this package (see
``tests/test_tool_module_drift.py``). The implementation now lives solely in
``lab_executor``; this name is kept so existing imports keep working.

Import ``lab_executor.tools.pdf_extractor`` directly in new code.
"""

from __future__ import annotations

from lab_executor.tools.pdf_extractor import *  # noqa: F401,F403
from lab_executor.tools import pdf_extractor as _upstream

__all__ = getattr(_upstream, "__all__", None) or [
    _name for _name in dir(_upstream) if not _name.startswith("_")
]
