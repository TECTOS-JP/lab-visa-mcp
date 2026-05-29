"""Project-level conftest: ensure local `src/` on sys.path so tests
run without `pip install -e .` (useful in network-restricted envs).
Also adds sibling `lab-executor-mcp` src if present (worktree layout).

v2.3.1: shared fixtures for JobStore tests that need close() teardown
(Windows SQLite WAL file lock 解放)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# lab-executor sibling (development monorepo layout)
LAB_SRC = ROOT.parent / "lab-executor-mcp" / "src"
if LAB_SRC.exists() and str(LAB_SRC) not in sys.path:
    sys.path.insert(0, str(LAB_SRC))


@pytest.fixture
def job_store(tmp_path):
    """JobStore fixture with explicit close on teardown."""
    from lab_executor.job.store import JobStore
    store = JobStore(str(tmp_path / "jobs.db"))
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def seed_job():
    """Seed a completed job row into a JobStore."""
    def _seed(store, job_id: str) -> None:
        store._connect().execute(
            "INSERT INTO jobs (job_id, owner, resource_name, status, "
            "current_step_index, created_at, updated_at) "
            "VALUES (?, '', '', 'completed', 0, "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (job_id,),
        )
    return _seed
