"""Linux process/thread/async coordination for one Workbench target.

Lock filenames are SHA-256 digests of an unambiguous canonical target scope;
untrusted project/world strings never become path components.  ``flock`` locks
are owned by an open file description and are released by the kernel if a
process crashes.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import math
import os
import stat
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from enfusion_mcp_rj.ledger import prepare_state_directory

_DIGEST_LENGTH: Final = 64
_DEFAULT_POLL_INTERVAL: Final = 0.01
_MAX_TCP_PORT: Final = 65535


class LockError(RuntimeError):
    """Base class for target-lock failures."""


class LockSecurityError(LockError):
    """A lock directory or file failed ownership/type/mode checks."""


class LockTimeoutError(LockError, TimeoutError):
    """A target lock could not be acquired before its deadline."""


class LockMode(StrEnum):
    """Kernel lock mode used for a Workbench target."""

    SHARED = "shared"
    EXCLUSIVE = "exclusive"


@dataclass(frozen=True, slots=True)
class TargetScope:
    """Canonical target/project/world tuple and its filesystem-safe digest."""

    canonical: str
    digest: str

    def __post_init__(self) -> None:
        expected = hashlib.sha256(self.canonical.encode("utf-8")).hexdigest()
        if len(self.digest) != _DIGEST_LENGTH or self.digest != expected:
            raise ValueError("target scope digest does not match its canonical value")

    @classmethod
    def derive(
        cls,
        *,
        workbench_host: str,
        workbench_port: int,
        project_host_path: Path,
        project_engine_path: str,
        world: str,
    ) -> TargetScope:
        """Build a stable scope for a Workbench endpoint, project, and world."""

        host = _nonempty_text(workbench_host, field="workbench_host")
        if isinstance(workbench_port, bool) or not isinstance(workbench_port, int):
            raise ValueError("workbench_port must be an integer")
        if not 1 <= workbench_port <= _MAX_TCP_PORT:
            raise ValueError("workbench_port must be between 1 and 65535")
        raw_project_path = Path(project_host_path)
        if not raw_project_path.is_absolute():
            raise ValueError("project_host_path must be absolute")
        project_path = str(raw_project_path.resolve(strict=False))
        engine_path = _nonempty_text(project_engine_path, field="project_engine_path")
        world_name = _nonempty_text(world, field="world")

        canonical = json.dumps(
            {
                "projectEnginePath": engine_path,
                "projectHostPath": project_path,
                "workbenchHost": host,
                "workbenchPort": workbench_port,
                "world": world_name,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return cls(
            canonical=canonical,
            digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    @property
    def lock_filename(self) -> str:
        """Return a bounded filename containing no target-controlled text."""

        return f"target-{self.digest}.lock"


class InterProcessFileLock:
    """A secure synchronous Linux ``flock`` handle.

    Create a separate instance for each concurrent holder.  Shared holders may
    overlap; an exclusive holder excludes both shared and exclusive holders.
    """

    def __init__(
        self,
        path: Path,
        mode: LockMode,
        *,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ) -> None:
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("poll_interval must be finite and positive")
        self.path = Path(path)
        self.mode = mode
        self.poll_interval = poll_interval
        self._descriptor: int | None = None

    @property
    def acquired(self) -> bool:
        """Return whether this object currently owns a file descriptor lock."""

        return self._descriptor is not None

    def acquire(self, *, timeout: float | None = None) -> None:
        """Acquire the lock, optionally failing after ``timeout`` seconds."""

        if self._descriptor is not None:
            raise LockError("this lock object is already acquired")
        deadline = _deadline(timeout)
        descriptor = _open_secure_lock_file(self.path)
        operation = _flock_operation(self.mode) | fcntl.LOCK_NB
        try:
            while True:
                try:
                    fcntl.flock(descriptor, operation)
                except BlockingIOError:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise LockTimeoutError(
                            f"timed out acquiring {self.mode.value} lock"
                        ) from None
                    remaining = None if deadline is None else deadline - time.monotonic()
                    delay = (
                        self.poll_interval
                        if remaining is None
                        else min(self.poll_interval, max(0.0, remaining))
                    )
                    if delay == 0:
                        raise LockTimeoutError(
                            f"timed out acquiring {self.mode.value} lock"
                        ) from None
                    time.sleep(delay)
                else:
                    self._descriptor = descriptor
                    return
        except BaseException:
            os.close(descriptor)
            raise

    def release(self) -> None:
        """Release the lock and close its descriptor."""

        descriptor = self._descriptor
        if descriptor is None:
            raise LockError("this lock object is not acquired")
        self._descriptor = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> InterProcessFileLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class TargetLockManager:
    """Combine an async writer-preferring RW lock with Linux ``flock``."""

    def __init__(self, state_dir: Path, *, allowed_root: Path) -> None:
        self.state_dir = prepare_state_directory(state_dir, allowed_root=allowed_root)
        self._local_locks: dict[str, _AsyncReaderWriterLock] = {}

    def lock_path(self, scope: TargetScope) -> Path:
        """Return the fixed state-directory lock path for ``scope``."""

        # TargetScope verifies digest-to-canonical binding in __post_init__.
        return self.state_dir / scope.lock_filename

    def file_lock(self, scope: TargetScope, mode: LockMode) -> InterProcessFileLock:
        """Create a synchronous cross-process lock for workers/tests."""

        return InterProcessFileLock(self.lock_path(scope), mode)

    @contextmanager
    def sync_lock(
        self,
        scope: TargetScope,
        mode: LockMode,
        *,
        wait_limit: float | None = None,
    ) -> Iterator[None]:
        """Synchronously hold a target lock."""

        lock = self.file_lock(scope, mode)
        lock.acquire(timeout=wait_limit)
        try:
            yield
        finally:
            lock.release()

    @asynccontextmanager
    async def shared(
        self,
        scope: TargetScope,
        *,
        wait_limit: float | None = None,
    ) -> AsyncIterator[None]:
        """Allow concurrent read-only work while excluding apply/reconcile."""

        local_lock = self._local_lock(scope)
        await local_lock.acquire_shared()
        try:
            descriptor = await _acquire_file_lock_async(
                self.lock_path(scope), LockMode.SHARED, wait_limit=wait_limit
            )
            try:
                yield
            finally:
                _release_descriptor(descriptor)
        finally:
            await local_lock.release_shared()

    @asynccontextmanager
    async def exclusive(
        self,
        scope: TargetScope,
        *,
        wait_limit: float | None = None,
    ) -> AsyncIterator[None]:
        """Serialize mutation/reconciliation for exactly one target scope."""

        local_lock = self._local_lock(scope)
        await local_lock.acquire_exclusive()
        try:
            descriptor = await _acquire_file_lock_async(
                self.lock_path(scope), LockMode.EXCLUSIVE, wait_limit=wait_limit
            )
            try:
                yield
            finally:
                _release_descriptor(descriptor)
        finally:
            await local_lock.release_exclusive()

    def _local_lock(self, scope: TargetScope) -> _AsyncReaderWriterLock:
        lock = self._local_locks.get(scope.digest)
        if lock is None:
            lock = _AsyncReaderWriterLock()
            self._local_locks[scope.digest] = lock
        return lock


class _AsyncReaderWriterLock:
    """Small writer-preferring asyncio reader/writer lock."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    async def acquire_shared(self) -> None:
        async with self._condition:
            await self._condition.wait_for(lambda: not self._writer and self._waiting_writers == 0)
            self._readers += 1

    async def release_shared(self) -> None:
        async with self._condition:
            if self._readers <= 0:
                raise LockError("shared asyncio lock is not held")
            self._readers -= 1
            if self._readers == 0:
                self._condition.notify_all()

    async def acquire_exclusive(self) -> None:
        async with self._condition:
            self._waiting_writers += 1
            try:
                await self._condition.wait_for(lambda: not self._writer and self._readers == 0)
                self._writer = True
            finally:
                self._waiting_writers -= 1

    async def release_exclusive(self) -> None:
        async with self._condition:
            if not self._writer:
                raise LockError("exclusive asyncio lock is not held")
            self._writer = False
            self._condition.notify_all()


async def _acquire_file_lock_async(
    path: Path,
    mode: LockMode,
    *,
    wait_limit: float | None,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
) -> int:
    deadline = _deadline(wait_limit)
    descriptor = _open_secure_lock_file(path)
    operation = _flock_operation(mode) | fcntl.LOCK_NB
    try:
        while True:
            try:
                fcntl.flock(descriptor, operation)
            except BlockingIOError:
                if deadline is not None and time.monotonic() >= deadline:
                    raise LockTimeoutError(f"timed out acquiring {mode.value} lock") from None
                remaining = None if deadline is None else deadline - time.monotonic()
                delay = (
                    poll_interval if remaining is None else min(poll_interval, max(0.0, remaining))
                )
                if delay == 0:
                    raise LockTimeoutError(f"timed out acquiring {mode.value} lock") from None
                await asyncio.sleep(delay)
            else:
                return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _release_descriptor(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _open_secure_lock_file(path: Path) -> int:
    if not path.is_absolute():
        raise LockSecurityError("lock file path must be absolute")
    try:
        parent_info = path.parent.lstat()
    except OSError as exc:
        raise LockSecurityError(f"cannot inspect lock directory: {exc}") from exc
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise LockSecurityError("lock parent must be a real directory")
    if parent_info.st_uid != os.geteuid() or stat.S_IMODE(parent_info.st_mode) & 0o077:
        raise LockSecurityError("lock directory must be private and owned by the current user")

    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise LockSecurityError(f"cannot safely open lock file: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise LockSecurityError("lock path must be a regular file")
        if info.st_uid != os.geteuid():
            raise LockSecurityError("lock file must be owned by the current user")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise LockSecurityError("lock file must not grant group/other permissions")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _flock_operation(mode: LockMode) -> int:
    if mode is LockMode.SHARED:
        return fcntl.LOCK_SH
    if mode is LockMode.EXCLUSIVE:
        return fcntl.LOCK_EX
    raise ValueError(f"unsupported lock mode: {mode!r}")


def _deadline(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("timeout must be finite and non-negative")
    return time.monotonic() + timeout


def _nonempty_text(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or "\x00" in value:
        raise ValueError(f"{field} must be non-empty, trimmed text without NUL")
    return value
