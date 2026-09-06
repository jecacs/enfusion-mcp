from __future__ import annotations

import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from enfusion_mcp_rj.locking import (
    InterProcessFileLock,
    LockError,
    LockMode,
    LockSecurityError,
    LockTimeoutError,
    TargetLockManager,
    TargetScope,
)


def _scope(root: Path, *, world: str = "$thenewRJ:rj.ent") -> TargetScope:
    return TargetScope.derive(
        workbench_host="127.0.0.1",
        workbench_port=5775,
        project_host_path=root / "project with spaces" / "карта",
        project_engine_path=r"C:\users\steamuser\Documents\My Games\new_rj",
        world=world,
    )


def _hold_lock_until_released(
    lock_path: str,
    mode_value: str,
    connection: Connection,
) -> None:
    lock = InterProcessFileLock(Path(lock_path), LockMode(mode_value))
    with lock:
        connection.send("acquired")
        connection.recv()
    connection.close()


def _acquire_then_crash(lock_path: str, connection: Connection) -> None:
    lock = InterProcessFileLock(Path(lock_path), LockMode.EXCLUSIVE)
    lock.acquire()
    connection.send("acquired")
    connection.close()
    os._exit(0)


def test_target_scope_is_stable_specific_and_never_leaks_input_into_filename(
    tmp_path: Path,
) -> None:
    first = _scope(tmp_path)
    second = _scope(tmp_path)
    other_world = _scope(tmp_path, world="$thenewRJ:other.ent")

    assert first == second
    assert first.digest != other_world.digest
    assert first.endpoint_digest == other_world.endpoint_digest
    assert len(first.digest) == 64
    assert first.lock_filename == f"target-{first.endpoint_digest}.lock"
    assert "карта" not in first.lock_filename
    assert "$thenewRJ" not in first.lock_filename
    assert "projectHostPath" in first.canonical
    assert "projectHostPath" not in first.endpoint_canonical


@pytest.mark.parametrize(
    ("host", "port", "engine_path", "world"),
    [
        ("", 5775, r"C:\project", "$id:world.ent"),
        ("127.0.0.1", 0, r"C:\project", "$id:world.ent"),
        ("127.0.0.1", 65536, r"C:\project", "$id:world.ent"),
        ("127.0.0.1", 5775, "", "$id:world.ent"),
        ("127.0.0.1", 5775, r"C:\project", "bad\x00world"),
    ],
)
def test_target_scope_rejects_ambiguous_fields(
    tmp_path: Path,
    host: str,
    port: int,
    engine_path: str,
    world: str,
) -> None:
    with pytest.raises(ValueError):
        TargetScope.derive(
            workbench_host=host,
            workbench_port=port,
            project_host_path=tmp_path / "project",
            project_engine_path=engine_path,
            world=world,
        )


def test_target_scope_requires_absolute_host_project_path() -> None:
    with pytest.raises(ValueError, match="absolute"):
        TargetScope.derive(
            workbench_host="127.0.0.1",
            workbench_port=5775,
            project_host_path=Path("relative/project"),
            project_engine_path=r"C:\project",
            world="$id:world.ent",
        )


def test_exclusive_file_lock_serializes_threads(tmp_path: Path) -> None:
    manager = TargetLockManager(tmp_path / "state", allowed_root=tmp_path)
    scope = _scope(tmp_path)
    guard = threading.Lock()
    active = 0
    maximum_active = 0
    visits: list[int] = []

    def worker(index: int) -> None:
        nonlocal active, maximum_active
        with manager.sync_lock(scope, LockMode.EXCLUSIVE):
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
                visits.append(index)
            time.sleep(0.025)
            with guard:
                active -= 1

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker, index) for index in range(4)]
        for future in futures:
            future.result(timeout=5)

    assert maximum_active == 1
    assert sorted(visits) == [0, 1, 2, 3]


def test_same_workbench_endpoint_serializes_different_project_scopes(tmp_path: Path) -> None:
    manager = TargetLockManager(tmp_path / "state", allowed_root=tmp_path)
    first = _scope(tmp_path)
    second = TargetScope.derive(
        workbench_host="127.0.0.1",
        workbench_port=5775,
        project_host_path=tmp_path / "other-project",
        project_engine_path=r"D:\other-project",
        world="$other:world.ent",
    )
    assert first.digest != second.digest
    assert manager.lock_path(first) == manager.lock_path(second)
    guard = threading.Lock()
    active = 0
    maximum_active = 0

    def worker(scope: TargetScope) -> None:
        nonlocal active, maximum_active
        with manager.sync_lock(scope, LockMode.EXCLUSIVE):
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.03)
            with guard:
                active -= 1

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(worker, first)
        second_future = executor.submit(worker, second)
        first_future.result(timeout=5)
        second_future.result(timeout=5)

    assert maximum_active == 1


def test_shared_file_locks_overlap_between_threads(tmp_path: Path) -> None:
    manager = TargetLockManager(tmp_path / "state", allowed_root=tmp_path)
    scope = _scope(tmp_path)
    barrier = threading.Barrier(2)

    def reader() -> None:
        with manager.sync_lock(scope, LockMode.SHARED):
            barrier.wait(timeout=2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(reader)
        second = executor.submit(reader)
        first.result(timeout=5)
        second.result(timeout=5)


def test_process_shared_lock_allows_shared_and_blocks_exclusive(tmp_path: Path) -> None:
    manager = TargetLockManager(tmp_path / "state", allowed_root=tmp_path)
    lock_path = manager.lock_path(_scope(tmp_path))
    context = get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(
        target=_hold_lock_until_released,
        args=(str(lock_path), LockMode.SHARED.value, child),
    )
    process.start()
    child.close()
    assert parent.poll(5)
    assert parent.recv() == "acquired"

    peer_reader = InterProcessFileLock(lock_path, LockMode.SHARED)
    peer_reader.acquire(timeout=0.5)
    peer_reader.release()
    blocked_writer = InterProcessFileLock(lock_path, LockMode.EXCLUSIVE)
    with pytest.raises(LockTimeoutError):
        blocked_writer.acquire(timeout=0.1)

    parent.send("release")
    process.join(timeout=5)
    parent.close()
    assert process.exitcode == 0
    blocked_writer.acquire(timeout=0.5)
    blocked_writer.release()


def test_process_exclusive_lock_serializes_another_process(tmp_path: Path) -> None:
    manager = TargetLockManager(tmp_path / "state", allowed_root=tmp_path)
    lock_path = manager.lock_path(_scope(tmp_path))
    context = get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(
        target=_hold_lock_until_released,
        args=(str(lock_path), LockMode.EXCLUSIVE.value, child),
    )
    process.start()
    child.close()
    assert parent.poll(5)
    assert parent.recv() == "acquired"

    contender = InterProcessFileLock(lock_path, LockMode.EXCLUSIVE)
    with pytest.raises(LockTimeoutError):
        contender.acquire(timeout=0.1)

    parent.send("release")
    process.join(timeout=5)
    parent.close()
    assert process.exitcode == 0
    contender.acquire(timeout=0.5)
    contender.release()


def test_kernel_releases_exclusive_lock_when_process_crashes(tmp_path: Path) -> None:
    manager = TargetLockManager(tmp_path / "state", allowed_root=tmp_path)
    lock_path = manager.lock_path(_scope(tmp_path))
    context = get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_acquire_then_crash, args=(str(lock_path), child))
    process.start()
    child.close()
    assert parent.poll(5)
    assert parent.recv() == "acquired"
    process.join(timeout=5)
    parent.close()
    assert process.exitcode == 0

    recovered = InterProcessFileLock(lock_path, LockMode.EXCLUSIVE)
    recovered.acquire(timeout=0.5)
    recovered.release()


async def test_async_shared_holders_overlap_and_exclusive_waits(tmp_path: Path) -> None:
    manager = TargetLockManager(tmp_path / "state", allowed_root=tmp_path)
    scope = _scope(tmp_path)
    both_readers_entered = asyncio.Event()
    release_readers = asyncio.Event()
    writer_entered = asyncio.Event()
    reader_count = 0

    async def reader() -> None:
        nonlocal reader_count
        async with manager.shared(scope):
            reader_count += 1
            if reader_count == 2:
                both_readers_entered.set()
            await release_readers.wait()

    async def writer() -> None:
        async with manager.exclusive(scope):
            writer_entered.set()

    readers = [asyncio.create_task(reader()) for _ in range(2)]
    await asyncio.wait_for(both_readers_entered.wait(), timeout=2)
    writer_task = asyncio.create_task(writer())
    await asyncio.sleep(0.05)
    assert not writer_entered.is_set()
    release_readers.set()
    await asyncio.gather(*readers)
    await asyncio.wait_for(writer_entered.wait(), timeout=2)
    await writer_task


async def test_async_exclusive_lock_is_cancel_safe_while_waiting_on_process(
    tmp_path: Path,
) -> None:
    manager = TargetLockManager(tmp_path / "state", allowed_root=tmp_path)
    scope = _scope(tmp_path)
    external = manager.file_lock(scope, LockMode.EXCLUSIVE)
    external.acquire()

    async def waiter() -> None:
        async with manager.exclusive(scope):
            pytest.fail("cancelled waiter must not enter")

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    external.release()

    async with manager.exclusive(scope, wait_limit=0.5):
        pass


def test_insecure_or_symlink_lock_file_is_rejected(tmp_path: Path) -> None:
    manager = TargetLockManager(tmp_path / "state", allowed_root=tmp_path)
    scope = _scope(tmp_path)
    lock_path = manager.lock_path(scope)
    lock_path.touch(mode=0o600)
    lock_path.chmod(0o644)

    with pytest.raises(LockSecurityError, match="group/other"):
        manager.file_lock(scope, LockMode.EXCLUSIVE).acquire(timeout=0)

    lock_path.unlink()
    decoy = tmp_path / "decoy.lock"
    decoy.touch(mode=0o600)
    lock_path.symlink_to(decoy)
    with pytest.raises(LockSecurityError, match="safely open"):
        manager.file_lock(scope, LockMode.EXCLUSIVE).acquire(timeout=0)


def test_lock_object_rejects_double_acquire_release_and_invalid_timeout(tmp_path: Path) -> None:
    manager = TargetLockManager(tmp_path / "state", allowed_root=tmp_path)
    lock = manager.file_lock(_scope(tmp_path), LockMode.EXCLUSIVE)

    with pytest.raises(ValueError, match="non-negative"):
        lock.acquire(timeout=-1)
    lock.acquire(timeout=0)
    assert lock.acquired
    with pytest.raises(LockError, match="already acquired"):
        lock.acquire()
    lock.release()
    with pytest.raises(LockError, match="not acquired"):
        lock.release()


@pytest.mark.parametrize("mode", [LockMode.SHARED, LockMode.EXCLUSIVE])
async def test_async_wait_limit_includes_local_contention(tmp_path: Path, mode: LockMode) -> None:
    manager = TargetLockManager(tmp_path / "state", allowed_root=tmp_path)
    scope = _scope(tmp_path)
    acquire = manager.shared if mode is LockMode.SHARED else manager.exclusive

    async with manager.exclusive(scope):
        # The outer timeout is a test watchdog, not the expected exception.
        async with asyncio.timeout(1):
            with pytest.raises(LockTimeoutError, match="local"):
                async with acquire(scope, wait_limit=0.03):
                    pytest.fail("contender entered an exclusively held lock")

    async with acquire(scope, wait_limit=0):
        pass


async def test_timed_out_writer_wakes_readers_while_existing_reader_remains(
    tmp_path: Path,
) -> None:
    manager = TargetLockManager(tmp_path / "state", allowed_root=tmp_path)
    scope = _scope(tmp_path)

    async def writer() -> None:
        with pytest.raises(LockTimeoutError):
            async with manager.exclusive(scope, wait_limit=0.08):
                pytest.fail("writer entered while a reader was active")

    reader_entered = asyncio.Event()

    async def reader() -> None:
        async with manager.shared(scope, wait_limit=0.5):
            reader_entered.set()

    async with manager.shared(scope):
        writer_task = asyncio.create_task(writer())
        # Let the writer join the condition queue before starting the reader.
        await asyncio.sleep(0)
        reader_task = asyncio.create_task(reader())
        await writer_task
        await asyncio.wait_for(reader_entered.wait(), timeout=0.5)
        await reader_task


async def test_invalid_async_wait_limit_rejected_before_local_wait(tmp_path: Path) -> None:
    manager = TargetLockManager(tmp_path / "state", allowed_root=tmp_path)
    scope = _scope(tmp_path)
    async with manager.exclusive(scope):
        with pytest.raises(ValueError, match="non-negative"):
            async with manager.shared(scope, wait_limit=-1):
                pytest.fail("invalid wait limit accepted")
