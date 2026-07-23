"""Guard against this package drifting away from the runtime it wraps.

``lab_visa_mcp.tools`` once held copies of the runtime's tool modules. Copies
diverge silently: fixes land in ``lab_executor`` and never reach the copy, so a
standalone VISA server quietly loses features the runtime already has. That
already happened -- artifact bundling and the monitor's safe-shutdown action
exist upstream but not in the copies still kept here.

Modules verified identical were replaced with re-exports. These tests keep that
true, and make the remaining divergence explicit instead of invisible.
"""

from __future__ import annotations

import hashlib
import importlib
import pathlib
import re

import pytest


TOOLS = pathlib.Path(__file__).parents[1] / "src" / "lab_visa_mcp" / "tools"

# Re-exports: the implementation lives in lab_executor. These must not grow a
# local implementation again.
REEXPORTED = ["audit", "dsl", "groups", "info", "jobs", "pdf_extractor", "waits"]

# Still local, and known to differ from upstream. Each entry is a debt: the
# standalone VISA server does not get the upstream behaviour listed here.
# Removing an entry requires routing that module to lab_executor.
KNOWN_DIVERGENT = {
    "commands": "upstream has additional command-execution handling",
    "export": "upstream embeds bulk-acquisition artifacts in bundles (P1)",
    "monitor": "upstream supports on_stop_condition safe shutdown (P2)",
    "observation": "upstream implementation is substantially larger",
    "recipes": "minor upstream difference",
}

# No runtime counterpart: VISA-specific by nature.
VISA_ONLY = ["discovery"]


def _normalised(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = text.replace("lab_visa_mcp", "lab_executor")
    return re.sub(r"[ \t]+\n", "\n", text)


def _upstream_path(name: str) -> pathlib.Path:
    module = importlib.import_module(f"lab_executor.tools.{name}")
    return pathlib.Path(module.__file__)


def _local_modules() -> set[str]:
    return {p.stem for p in TOOLS.glob("*.py") if p.stem != "__init__"}


def test_every_tool_module_is_classified():
    """A new module must be deliberately placed in one of the three groups."""
    classified = set(REEXPORTED) | set(KNOWN_DIVERGENT) | set(VISA_ONLY)
    assert _local_modules() == classified, (
        "an unclassified tool module appeared; decide whether it re-exports "
        "lab_executor, is knowingly divergent, or is VISA-specific"
    )


@pytest.mark.parametrize("name", REEXPORTED)
def test_reexported_modules_do_not_reimplement_upstream(name):
    """These must stay thin: a local implementation would drift again."""
    source = (TOOLS / f"{name}.py").read_text(encoding="utf-8")
    assert f"from lab_executor.tools.{name} import" in source
    assert "def register_tools" not in source, (
        f"tools/{name}.py defines register_tools locally again; it must "
        f"re-export lab_executor.tools.{name}"
    )
    # A re-export cannot be large.
    assert len(source.splitlines()) < 40


@pytest.mark.parametrize("name", REEXPORTED)
def test_reexported_modules_expose_the_upstream_entry_point(name):
    local = importlib.import_module(f"lab_visa_mcp.tools.{name}")
    upstream = importlib.import_module(f"lab_executor.tools.{name}")
    assert local.register_tools is upstream.register_tools


@pytest.mark.parametrize("name", sorted(KNOWN_DIVERGENT))
def test_known_divergence_is_still_real(name):
    """If a copy caught up with upstream, promote it to a re-export.

    This fails when someone silently syncs a file instead of routing it, which
    would leave the duplication in place for the next fix to miss.
    """
    local, upstream = TOOLS / f"{name}.py", _upstream_path(name)
    same = hashlib.sha256(_normalised(local).encode()).hexdigest() == hashlib.sha256(
        _normalised(upstream).encode()
    ).hexdigest()
    assert not same, (
        f"tools/{name}.py now matches lab_executor.tools.{name}: replace it "
        "with a re-export and drop it from KNOWN_DIVERGENT"
    )


@pytest.mark.parametrize("name", VISA_ONLY)
def test_visa_only_modules_have_no_upstream_counterpart(name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"lab_executor.tools.{name}")
