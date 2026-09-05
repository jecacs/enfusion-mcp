"""Durable, process-safe plan and mutation-operation ledger.

The ledger is deliberately independent from MCP sessions and agent identities.  A
plan and its idempotency binding therefore mean the same thing to every STDIO
server process using the same state directory.

The caller must provide an explicit trusted ``allowed_root``.  The state
directory is created as a private (0700) directory below that root, and the
SQLite database is a regular, non-symlink file with no group/other permissions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final
from uuid import RFC_4122, UUID

SCHEMA_VERSION: Final = 1
DATABASE_FILENAME: Final = "operations.sqlite3"
MAX_PLAN_JSON_BYTES: Final = 2 * 1024 * 1024
MAX_DETAIL_LENGTH: Final = 4096
_PLAN_ID_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_PROC_START_TICKS_INDEX: Final = 19
_UNIX_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


class LedgerError(RuntimeError):
    """Base class for durable-ledger failures."""


class StateDirectoryError(LedgerError):
    """The configured state location is not safe to use."""


class SchemaVersionError(LedgerError):
    """The on-disk ledger schema is unsupported or inconsistent."""


class PlanValidationError(LedgerError, ValueError):
    """A plan is malformed, non-canonical, or has the wrong digest."""


class PlanNotFoundError(LedgerError, LookupError):
    """No plan exists for the requested plan id."""


class PlanExpiredError(LedgerError):
    """A plan cannot be bound because its immutable expiry has passed."""


class PlanConflictError(LedgerError):
    """A plan id is already associated with different immutable content."""


class OperationNotFoundError(LedgerError, LookupError):
    """No operation exists for the requested operation id."""


class OperationConflictError(LedgerError):
    """An idempotency key or plan already has an incompatible binding."""


class InvalidOperationTransitionError(LedgerError):
    """The requested operation-state transition is not permitted."""


class OperationState(StrEnum):
    """Conservative lifecycle shared by plans and their apply operations."""

    PLANNED = "PLANNED"
    SENDING = "SENDING"
    UNKNOWN = "UNKNOWN"
    APPLIED = "APPLIED"
    PARTIAL = "PARTIAL"
    ENTITY_CONFLICT = "ENTITY_CONFLICT"
    ROLLBACK_VERIFIED = "ROLLBACK_VERIFIED"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    UNDONE = "UNDONE"
    PRE_SEND_FAILED = "PRE_SEND_FAILED"


_ALLOWED_TRANSITIONS: Final[dict[OperationState, frozenset[OperationState]]] = {
    OperationState.PLANNED: frozenset({OperationState.SENDING, OperationState.PRE_SEND_FAILED}),
    OperationState.PRE_SEND_FAILED: frozenset({OperationState.SENDING}),
    OperationState.SENDING: frozenset(
        {
            OperationState.UNKNOWN,
            OperationState.APPLIED,
            OperationState.PARTIAL,
            OperationState.ENTITY_CONFLICT,
            OperationState.ROLLBACK_VERIFIED,
            OperationState.ROLLBACK_FAILED,
        }
    ),
    OperationState.UNKNOWN: frozenset(
        {
            OperationState.APPLIED,
            OperationState.PARTIAL,
            OperationState.ENTITY_CONFLICT,
            OperationState.ROLLBACK_FAILED,
            OperationState.UNDONE,
        }
    ),
    OperationState.APPLIED: frozenset({OperationState.UNDONE, OperationState.ENTITY_CONFLICT}),
    OperationState.PARTIAL: frozenset(
        {
            OperationState.ROLLBACK_VERIFIED,
            OperationState.ROLLBACK_FAILED,
            OperationState.ENTITY_CONFLICT,
        }
    ),
    OperationState.ENTITY_CONFLICT: frozenset(),
    OperationState.ROLLBACK_VERIFIED: frozenset(),
    OperationState.ROLLBACK_FAILED: frozenset(
        {OperationState.ROLLBACK_VERIFIED, OperationState.UNKNOWN}
    ),
    OperationState.UNDONE: frozenset(),
}


@dataclass(frozen=True, slots=True)
class PlanRecord:
    """One immutable canonical plan plus its durable lifecycle state."""

    plan_id: str
    canonical_json: str
    created_at: datetime
    expires_at: datetime
    state: OperationState

    def is_expired(self, *, at: datetime | None = None) -> bool:
        """Return whether the immutable expiry is at or before ``at``."""

        instant = at if at is not None else datetime.now(tz=UTC)
        return self.expires_at <= _require_aware_utc(instant, field="at")


@dataclass(frozen=True, slots=True)
class OperationRecord:
    """Immutable apply binding and its mutable, audited state."""

    operation_id: str
    idempotency_key: str
    plan_id: str
    state: OperationState
    created_at: datetime
    updated_at: datetime
    detail: str | None


@dataclass(frozen=True, slots=True)
class _ProcessIdentity:
    pid: int
    boot_id: str | None
    start_ticks: str | None


def canonical_plan_json(plan: object) -> str:
    """Serialize a JSON-object plan with the project's canonical JSON rules.

    This layer fixes UTF-8 JSON spelling, key order, and separators.  Numeric
    quantization belongs to the deterministic planner and must happen before
    calling this function.
    """

    if not isinstance(plan, dict):
        raise PlanValidationError("a canonical plan must be a JSON object")
    try:
        encoded = json.dumps(
            plan,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise PlanValidationError(f"plan is not finite JSON: {exc}") from exc
    if len(encoded.encode("utf-8")) > MAX_PLAN_JSON_BYTES:
        raise PlanValidationError("canonical plan exceeds the ledger size limit")
    return encoded


def plan_digest(canonical_json: str) -> str:
    """Return the lowercase SHA-256 id for canonical immutable plan JSON."""

    _validate_canonical_json(canonical_json)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def validate_idempotency_key(value: str) -> str:
    """Validate and return a canonical lowercase RFC-4122 UUID string."""

    if not isinstance(value, str):
        raise OperationConflictError("idempotency_key must be a UUID string")
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as exc:
        raise OperationConflictError("idempotency_key must be a canonical UUID") from exc
    if str(parsed) != value or parsed.variant != RFC_4122 or parsed.int == 0:
        raise OperationConflictError(
            "idempotency_key must be a non-nil, canonical lowercase RFC-4122 UUID"
        )
    return value


def prepare_state_directory(  # noqa: PLR0912 - security checks stay explicit and ordered
    state_dir: Path, *, allowed_root: Path
) -> Path:
    """Create or validate a private state directory below a trusted root.

    Existing overly-permissive locations are rejected rather than silently
    chmod'ed, because they may already have exposed ledger contents.
    """

    raw_root = Path(allowed_root)
    raw_state = Path(state_dir)
    if not raw_root.is_absolute() or not raw_state.is_absolute():
        raise StateDirectoryError("state_dir and allowed_root must be absolute paths")
    try:
        root = raw_root.resolve(strict=True)
    except OSError as exc:
        raise StateDirectoryError(f"allowed_root cannot be resolved: {exc}") from exc
    if not root.is_dir():
        raise StateDirectoryError("allowed_root is not a directory")

    try:
        parent = raw_state.parent.resolve(strict=True)
    except OSError as exc:
        raise StateDirectoryError("the state directory parent must already exist") from exc
    if not parent.is_relative_to(root):
        raise StateDirectoryError("state directory escapes allowed_root")

    try:
        info = raw_state.lstat()
    except FileNotFoundError:
        try:
            os.mkdir(raw_state, mode=0o700)
        except FileExistsError:
            # A concurrent process created it; validate that object below.
            pass
        except OSError as exc:
            raise StateDirectoryError(f"cannot create state directory: {exc}") from exc
        try:
            info = raw_state.lstat()
        except OSError as exc:
            raise StateDirectoryError("state directory vanished during creation") from exc
    except OSError as exc:
        raise StateDirectoryError(f"cannot inspect state directory: {exc}") from exc

    if stat.S_ISLNK(info.st_mode):
        raise StateDirectoryError("state directory must not be a symlink")
    if not stat.S_ISDIR(info.st_mode):
        raise StateDirectoryError("state path is not a directory")
    if info.st_uid != os.geteuid():
        raise StateDirectoryError("state directory must be owned by the current user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise StateDirectoryError("state directory must not grant group/other permissions")

    resolved = raw_state.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise StateDirectoryError("resolved state directory escapes allowed_root")
    return resolved


class Ledger:
    """SQLite-backed immutable-plan and idempotent-operation repository."""

    def __init__(
        self,
        state_dir: Path,
        *,
        allowed_root: Path,
        busy_timeout_seconds: float = 5.0,
    ) -> None:
        if busy_timeout_seconds <= 0:
            raise ValueError("busy_timeout_seconds must be positive")
        self.state_dir = prepare_state_directory(state_dir, allowed_root=allowed_root)
        self.database_path = self.state_dir / DATABASE_FILENAME
        self._busy_timeout_ms = round(busy_timeout_seconds * 1000)
        _prepare_database_file(self.database_path)
        self._initialize_schema()
        # Only rows whose recorded Linux process identity is demonstrably gone
        # are recovered.  A second live MCP process must never turn another
        # process's in-flight send into UNKNOWN merely by opening the ledger.
        self.recovered_operations = self.recover_orphaned_sending()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *_exc: object) -> None:
        # Connections are intentionally method-scoped, so there is no durable
        # handle to close.  Keeping this protocol makes ownership explicit.
        return None

    def store_plan(
        self,
        *,
        canonical_json: str,
        created_at: datetime,
        expires_at: datetime,
        plan_id: str | None = None,
    ) -> PlanRecord:
        """Insert a plan once, preserving prior state on identical repeats."""

        _validate_canonical_json(canonical_json)
        computed_id = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        if plan_id is not None and plan_id != computed_id:
            raise PlanValidationError("plan_id does not match canonical plan SHA-256")
        effective_id = computed_id if plan_id is None else plan_id
        _validate_plan_id(effective_id)
        created_us = _datetime_to_microseconds(created_at, field="created_at")
        expires_us = _datetime_to_microseconds(expires_at, field="expires_at")
        if expires_us <= created_us:
            raise PlanValidationError("expires_at must be later than created_at")

        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM plans WHERE plan_id = ?", (effective_id,)
            ).fetchone()
            if existing is not None:
                record = _plan_from_row(existing)
                if record.canonical_json != canonical_json:
                    raise PlanConflictError("plan_id is already bound to different JSON")
                # created/expires metadata deliberately do not participate in
                # the digest.  An identical repeat returns the original row and
                # cannot reset APPLIED/UNKNOWN/UNDONE state or extend its TTL.
                return record

            connection.execute(
                """
                INSERT INTO plans(
                    plan_id, canonical_json, created_at_us, expires_at_us, state
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    effective_id,
                    canonical_json,
                    created_us,
                    expires_us,
                    OperationState.PLANNED.value,
                ),
            )
            row = connection.execute(
                "SELECT * FROM plans WHERE plan_id = ?", (effective_id,)
            ).fetchone()
            if row is None:  # pragma: no cover - SQLite contract guard
                raise LedgerError("inserted plan could not be read back")
            return _plan_from_row(row)

    def save_plan(
        self,
        plan: object,
        *,
        created_at: datetime,
        expires_at: datetime,
    ) -> PlanRecord:
        """Canonicalize and store a JSON-object plan."""

        return self.store_plan(
            canonical_json=canonical_plan_json(plan),
            created_at=created_at,
            expires_at=expires_at,
        )

    def get_plan(self, plan_id: str) -> PlanRecord:
        """Read a plan without changing its state or TTL."""

        _validate_plan_id(plan_id)
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if row is None:
            raise PlanNotFoundError(f"unknown plan_id: {plan_id}")
        return _plan_from_row(row)

    def bind_operation(
        self,
        *,
        plan_id: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> OperationRecord:
        """Atomically bind one plan to one canonical UUID operation.

        ``operation_id`` is exactly ``idempotency_key``.  There is no second
        random identifier whose binding could diverge across MCP processes.
        """

        _validate_plan_id(plan_id)
        canonical_key = validate_idempotency_key(idempotency_key)
        instant = datetime.now(tz=UTC) if now is None else now
        now_us = _datetime_to_microseconds(instant, field="now")

        with self._transaction() as connection:
            plan_row = connection.execute(
                "SELECT * FROM plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if plan_row is None:
                raise PlanNotFoundError(f"unknown plan_id: {plan_id}")
            plan = _plan_from_row(plan_row)

            by_key = connection.execute(
                "SELECT * FROM operations WHERE idempotency_key = ?", (canonical_key,)
            ).fetchone()
            if by_key is not None:
                existing = _operation_from_row(by_key)
                if existing.plan_id != plan_id:
                    raise OperationConflictError("idempotency key is already bound to another plan")
                return existing

            by_plan = connection.execute(
                "SELECT * FROM operations WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if by_plan is not None:
                existing = _operation_from_row(by_plan)
                raise OperationConflictError(
                    f"plan is already bound to operation {existing.operation_id}"
                )

            if plan.expires_at <= _microseconds_to_datetime(now_us):
                raise PlanExpiredError(f"plan {plan_id} has expired")
            if plan.state is not OperationState.PLANNED:
                raise OperationConflictError(
                    f"plan in state {plan.state.value} cannot start a new operation"
                )

            try:
                connection.execute(
                    """
                    INSERT INTO operations(
                        operation_id, idempotency_key, plan_id, state,
                        created_at_us, updated_at_us, detail,
                        owner_pid, owner_boot_id, owner_start_ticks
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)
                    """,
                    (
                        canonical_key,
                        canonical_key,
                        plan_id,
                        OperationState.PLANNED.value,
                        now_us,
                        now_us,
                    ),
                )
            except sqlite3.IntegrityError as exc:  # pragma: no cover - race guard
                raise OperationConflictError(
                    "operation binding raced with another process"
                ) from exc
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (canonical_key,)
            ).fetchone()
            if row is None:  # pragma: no cover - SQLite contract guard
                raise LedgerError("inserted operation could not be read back")
            return _operation_from_row(row)

    def get_operation(self, operation_id: str) -> OperationRecord:
        """Read an operation by its canonical UUID."""

        canonical_id = validate_idempotency_key(operation_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (canonical_id,)
            ).fetchone()
        if row is None:
            raise OperationNotFoundError(f"unknown operation_id: {canonical_id}")
        return _operation_from_row(row)

    def get_operation_for_plan(self, plan_id: str) -> OperationRecord | None:
        """Return a plan's unique operation binding, if one exists."""

        _validate_plan_id(plan_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        return None if row is None else _operation_from_row(row)

    def transition_operation(
        self,
        operation_id: str,
        to_state: OperationState,
        *,
        expected_state: OperationState | None = None,
        detail: str | None = None,
        now: datetime | None = None,
    ) -> OperationRecord:
        """Apply a validated state transition and mirror it onto the plan."""

        canonical_id = validate_idempotency_key(operation_id)
        if detail is not None and len(detail) > MAX_DETAIL_LENGTH:
            raise ValueError("operation detail is too long")
        instant = datetime.now(tz=UTC) if now is None else now
        now_us = _datetime_to_microseconds(instant, field="now")

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (canonical_id,)
            ).fetchone()
            if row is None:
                raise OperationNotFoundError(f"unknown operation_id: {canonical_id}")
            current = _operation_from_row(row)
            if expected_state is not None and current.state is not expected_state:
                raise InvalidOperationTransitionError(
                    f"expected {expected_state.value}, found {current.state.value}"
                )
            if current.state is to_state:
                return current
            if to_state not in _ALLOWED_TRANSITIONS[current.state]:
                raise InvalidOperationTransitionError(
                    f"transition {current.state.value} -> {to_state.value} is not allowed"
                )

            # Preserve SQLite's monotonic audit-time invariant if the wall
            # clock is adjusted backwards between state transitions.
            effective_now_us = max(now_us, int(row["updated_at_us"]))
            owner = _current_process_identity() if to_state is OperationState.SENDING else None
            connection.execute(
                """
                UPDATE operations
                SET state = ?, updated_at_us = ?, detail = ?,
                    owner_pid = ?, owner_boot_id = ?, owner_start_ticks = ?
                WHERE operation_id = ?
                """,
                (
                    to_state.value,
                    effective_now_us,
                    detail,
                    None if owner is None else owner.pid,
                    None if owner is None else owner.boot_id,
                    None if owner is None else owner.start_ticks,
                    canonical_id,
                ),
            )
            connection.execute(
                "UPDATE plans SET state = ? WHERE plan_id = ?",
                (to_state.value, current.plan_id),
            )
            connection.execute(
                """
                INSERT INTO operation_events(
                    operation_id, from_state, to_state, occurred_at_us, detail
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    canonical_id,
                    current.state.value,
                    to_state.value,
                    effective_now_us,
                    detail,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (canonical_id,)
            ).fetchone()
            if updated is None:  # pragma: no cover - SQLite contract guard
                raise LedgerError("updated operation could not be read back")
            return _operation_from_row(updated)

    def recover_orphaned_sending(self) -> int:
        """Conservatively turn dead-process ``SENDING`` rows into ``UNKNOWN``.

        A live sender is identified by Linux boot id, PID and process start
        ticks, avoiding both cross-process false recovery and ordinary PID reuse.
        If liveness cannot be disproved, the row remains ``SENDING`` (fail
        closed).  Kernel-released target locks make this safe to call at every
        process startup.
        """

        recovered = 0
        now_us = time.time_ns() // 1000
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT operation_id, plan_id, updated_at_us,
                       owner_pid, owner_boot_id, owner_start_ticks
                FROM operations WHERE state = ?
                """,
                (OperationState.SENDING.value,),
            ).fetchall()
            for row in rows:
                owner_pid_raw = row["owner_pid"]
                owner = _ProcessIdentity(
                    pid=-1 if owner_pid_raw is None else int(owner_pid_raw),
                    boot_id=(None if row["owner_boot_id"] is None else str(row["owner_boot_id"])),
                    start_ticks=(
                        None if row["owner_start_ticks"] is None else str(row["owner_start_ticks"])
                    ),
                )
                if _process_identity_is_alive(owner):
                    continue
                operation_id = str(row["operation_id"])
                plan_id = str(row["plan_id"])
                effective_now_us = max(now_us, int(row["updated_at_us"]))
                detail = "sender process exited while mutation outcome was unresolved"
                cursor = connection.execute(
                    """
                    UPDATE operations
                    SET state = ?, updated_at_us = ?, detail = ?,
                        owner_pid = NULL, owner_boot_id = NULL, owner_start_ticks = NULL
                    WHERE operation_id = ? AND state = ?
                    """,
                    (
                        OperationState.UNKNOWN.value,
                        effective_now_us,
                        detail,
                        operation_id,
                        OperationState.SENDING.value,
                    ),
                )
                if cursor.rowcount == 0:  # pragma: no cover - transaction serialized
                    continue
                connection.execute(
                    "UPDATE plans SET state = ? WHERE plan_id = ?",
                    (OperationState.UNKNOWN.value, plan_id),
                )
                connection.execute(
                    """
                    INSERT INTO operation_events(
                        operation_id, from_state, to_state, occurred_at_us, detail
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        operation_id,
                        OperationState.SENDING.value,
                        OperationState.UNKNOWN.value,
                        effective_now_us,
                        detail,
                    ),
                )
                recovered += 1
        return recovered

    def operation_events(self, operation_id: str) -> tuple[tuple[str, str, str | None], ...]:
        """Return ordered state transitions for diagnostics and tests."""

        canonical_id = validate_idempotency_key(operation_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT from_state, to_state, detail FROM operation_events
                WHERE operation_id = ? ORDER BY event_id
                """,
                (canonical_id,),
            ).fetchall()
        return tuple(
            (
                str(row["from_state"]),
                str(row["to_state"]),
                None if row["detail"] is None else str(row["detail"]),
            )
            for row in rows
        )

    def quick_check(self) -> bool:
        """Run SQLite's bounded integrity check."""

        with self._connection() as connection:
            row = connection.execute("PRAGMA quick_check(1)").fetchone()
        return row is not None and str(row[0]) == "ok"

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path,
            isolation_level=None,
            timeout=self._busy_timeout_ms / 1000,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def _initialize_schema(self) -> None:
        states = ", ".join(f"'{state.value}'" for state in OperationState)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN EXCLUSIVE")
            try:
                version_row = connection.execute("PRAGMA user_version").fetchone()
                version = 0 if version_row is None else int(version_row[0])
                if version == 0:
                    objects = connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE name NOT LIKE 'sqlite_%' AND type IN ('table', 'trigger')
                        """
                    ).fetchall()
                    if objects:
                        raise SchemaVersionError(
                            "unversioned non-empty ledger database is not safe to migrate"
                        )
                    self._create_schema(connection, states=states)
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                elif version != SCHEMA_VERSION:
                    raise SchemaVersionError(
                        f"ledger schema {version} is unsupported; expected {SCHEMA_VERSION}"
                    )

                metadata = connection.execute(
                    "SELECT version FROM schema_metadata WHERE singleton = 1"
                ).fetchone()
                if metadata is None or int(metadata[0]) != SCHEMA_VERSION:
                    raise SchemaVersionError("ledger schema metadata is inconsistent")
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    @staticmethod
    def _create_schema(connection: sqlite3.Connection, *, states: str) -> None:
        connection.execute(
            """
            CREATE TABLE schema_metadata (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                version INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO schema_metadata(singleton, version) VALUES (1, ?)",
            (SCHEMA_VERSION,),
        )
        connection.execute(
            f"""
            CREATE TABLE plans (
                plan_id TEXT PRIMARY KEY
                    CHECK(length(plan_id) = 64)
                    CHECK(lower(plan_id) = plan_id)
                    CHECK(plan_id NOT GLOB '*[^0-9a-f]*'),
                canonical_json TEXT NOT NULL,
                created_at_us INTEGER NOT NULL,
                expires_at_us INTEGER NOT NULL CHECK(expires_at_us > created_at_us),
                state TEXT NOT NULL CHECK(state IN ({states}))
            )
            """
        )
        connection.execute(
            f"""
            CREATE TABLE operations (
                operation_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                plan_id TEXT NOT NULL UNIQUE REFERENCES plans(plan_id) ON DELETE RESTRICT,
                state TEXT NOT NULL CHECK(state IN ({states})),
                created_at_us INTEGER NOT NULL,
                updated_at_us INTEGER NOT NULL,
                detail TEXT,
                owner_pid INTEGER,
                owner_boot_id TEXT,
                owner_start_ticks TEXT,
                CHECK(operation_id = idempotency_key),
                CHECK(length(operation_id) = 36),
                CHECK(lower(operation_id) = operation_id),
                CHECK(updated_at_us >= created_at_us)
            )
            """
        )
        connection.execute(
            f"""
            CREATE TABLE operation_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL REFERENCES operations(operation_id)
                    ON DELETE RESTRICT,
                from_state TEXT NOT NULL CHECK(from_state IN ({states})),
                to_state TEXT NOT NULL CHECK(to_state IN ({states})),
                occurred_at_us INTEGER NOT NULL,
                detail TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX operation_events_operation_idx "
            "ON operation_events(operation_id, event_id)"
        )
        connection.execute(
            """
            CREATE TRIGGER plans_immutable_fields
            BEFORE UPDATE OF plan_id, canonical_json, created_at_us, expires_at_us ON plans
            BEGIN
                SELECT RAISE(ABORT, 'immutable plan fields cannot be updated');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER operation_binding_immutable
            BEFORE UPDATE OF operation_id, idempotency_key, plan_id, created_at_us ON operations
            BEGIN
                SELECT RAISE(ABORT, 'operation binding cannot be updated');
            END
            """
        )


def _validate_plan_id(plan_id: str) -> None:
    if not isinstance(plan_id, str) or _PLAN_ID_RE.fullmatch(plan_id) is None:
        raise PlanValidationError("plan_id must be exactly 64 lowercase hexadecimal characters")


def _reject_json_constant(value: str) -> object:
    raise PlanValidationError(f"non-finite JSON constant is forbidden: {value}")


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PlanValidationError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _validate_canonical_json(value: str) -> None:
    if not isinstance(value, str):
        raise PlanValidationError("canonical_json must be a string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PlanValidationError("canonical plan is not valid UTF-8 text") from exc
    if len(encoded) > MAX_PLAN_JSON_BYTES:
        raise PlanValidationError("canonical plan exceeds the ledger size limit")
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except PlanValidationError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise PlanValidationError(f"invalid plan JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise PlanValidationError("a canonical plan must be a JSON object")
    if canonical_plan_json(parsed) != value:
        raise PlanValidationError("plan JSON is not in canonical form")


def _require_aware_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PlanValidationError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _datetime_to_microseconds(value: datetime, *, field: str) -> int:
    utc_value = _require_aware_utc(value, field=field)
    delta = utc_value - _UNIX_EPOCH
    return ((delta.days * 86_400) + delta.seconds) * 1_000_000 + delta.microseconds


def _microseconds_to_datetime(value: int) -> datetime:
    return _UNIX_EPOCH + timedelta(microseconds=value)


def _plan_from_row(row: sqlite3.Row) -> PlanRecord:
    return PlanRecord(
        plan_id=str(row["plan_id"]),
        canonical_json=str(row["canonical_json"]),
        created_at=_microseconds_to_datetime(int(row["created_at_us"])),
        expires_at=_microseconds_to_datetime(int(row["expires_at_us"])),
        state=OperationState(str(row["state"])),
    )


def _operation_from_row(row: sqlite3.Row) -> OperationRecord:
    detail_raw = row["detail"]
    return OperationRecord(
        operation_id=str(row["operation_id"]),
        idempotency_key=str(row["idempotency_key"]),
        plan_id=str(row["plan_id"]),
        state=OperationState(str(row["state"])),
        created_at=_microseconds_to_datetime(int(row["created_at_us"])),
        updated_at=_microseconds_to_datetime(int(row["updated_at_us"])),
        detail=None if detail_raw is None else str(detail_raw),
    )


def _prepare_database_file(path: Path) -> None:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        try:
            descriptor = os.open(
                path,
                os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as exc:
            raise StateDirectoryError(f"cannot safely open ledger database: {exc}") from exc
    except OSError as exc:
        raise StateDirectoryError(f"cannot create ledger database: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise StateDirectoryError("ledger database must be a regular file")
        if info.st_uid != os.geteuid():
            raise StateDirectoryError("ledger database must be owned by the current user")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise StateDirectoryError("ledger database must not grant group/other permissions")
    finally:
        os.close(descriptor)


def _current_process_identity() -> _ProcessIdentity:
    pid = os.getpid()
    return _ProcessIdentity(
        pid=pid,
        boot_id=_read_boot_id(),
        start_ticks=_read_process_start_ticks(pid),
    )


def _process_identity_is_alive(identity: _ProcessIdentity) -> bool:
    if identity.pid <= 0:
        return False
    try:
        os.kill(identity.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    current_boot = _read_boot_id()
    if (
        identity.boot_id is not None
        and current_boot is not None
        and identity.boot_id != current_boot
    ):
        return False
    current_start = _read_process_start_ticks(identity.pid)
    # Failure to prove death must not let a second live server process alter an
    # in-flight operation owned by the first one.
    return not (
        identity.start_ticks is not None
        and current_start is not None
        and identity.start_ticks != current_start
    )


def _read_boot_id() -> str | None:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        return None


def _read_process_start_ticks(pid: int) -> str | None:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return None
    closing_parenthesis = stat_text.rfind(")")
    if closing_parenthesis < 0:
        return None
    remaining_fields = stat_text[closing_parenthesis + 2 :].split()
    # The tail starts at procfs field 3; starttime is field 22.
    if len(remaining_fields) <= _PROC_START_TICKS_INDEX:
        return None
    return remaining_fields[_PROC_START_TICKS_INDEX]
