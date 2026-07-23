"""Cancelling a VISA operation must not let a second one reach the instrument.

VISA I/O runs on a worker thread, and a thread cannot be interrupted. If the
awaiting coroutine is cancelled and the resource lock unwinds with it, the
lock is free while the real transfer is still in flight, so the next command --
possibly a safe-shutdown -- opens a second concurrent session to the same
device. These tests drive that race directly, without hardware.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from lab_visa_mcp.visa_manager import VisaManager


def _manager() -> VisaManager:
    manager = VisaManager.__new__(VisaManager)  # no pyvisa / ResourceManager
    manager._rm = None
    manager._locks = {}
    manager._bus_manager = None
    return manager


class _Instrument:
    """Records overlapping access the way a real instrument would suffer it."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls = 0
        self._guard = threading.Lock()

    def slow_io(self) -> str:
        with self._guard:
            self.calls += 1
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            self.release.wait(timeout=5)
            return "ok"
        finally:
            with self._guard:
                self.in_flight -= 1


@pytest.mark.asyncio
async def test_cancelled_operation_keeps_the_lock_until_the_thread_finishes():
    manager = _manager()
    instrument = _Instrument()
    lock = manager._get_lock("GPIB0::1::INSTR")

    async def operation():
        async with lock:
            return await manager._run(instrument.slow_io)

    task = asyncio.create_task(operation())
    await asyncio.sleep(0.1)  # let the worker thread enter slow_io
    assert instrument.in_flight == 1

    task.cancel()
    await asyncio.sleep(0.1)

    # The transfer is still running, so the lock must still be held.
    assert lock.locked(), "lock released while the instrument was still busy"

    instrument.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lock.locked()


@pytest.mark.asyncio
async def test_a_second_command_cannot_overlap_a_cancelled_one():
    """The scenario that matters: a safe-shutdown following a cancel."""
    manager = _manager()
    instrument = _Instrument()
    lock = manager._get_lock("GPIB0::1::INSTR")

    async def operation():
        async with lock:
            return await manager._run(instrument.slow_io)

    first = asyncio.create_task(operation())
    await asyncio.sleep(0.1)
    first.cancel()
    await asyncio.sleep(0.05)

    second = asyncio.create_task(operation())  # e.g. the shutdown command
    await asyncio.sleep(0.1)

    # The second command must still be queued behind the in-flight transfer.
    assert instrument.calls == 1, "a second transfer started during the first"

    instrument.release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second

    assert instrument.calls == 2
    assert instrument.max_in_flight == 1, "two transfers overlapped on one device"


@pytest.mark.asyncio
async def test_uncancelled_operations_are_unaffected():
    manager = _manager()
    instrument = _Instrument()
    instrument.release.set()  # return immediately

    async with manager._get_lock("GPIB0::1::INSTR"):
        assert await manager._run(instrument.slow_io) == "ok"
    assert instrument.calls == 1


@pytest.mark.asyncio
async def test_errors_still_propagate():
    manager = _manager()

    def explode():
        raise RuntimeError("instrument fault")

    with pytest.raises(RuntimeError, match="instrument fault"):
        await manager._run(explode)
