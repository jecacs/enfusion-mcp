from __future__ import annotations

import multiprocessing
import os
import sqlite3
import stat
import threading
import time
from datetime import UTC, datetime, timedelta
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from enfusion_mcp_rj.ledger import (
    DATABASE_FILENAME,
    InvalidOperationTransitionError,
    Ledger,
    OperationConflictError,
    OperationState,
    PlanExpiredError,
    PlanValidationError,
    SchemaVersionError,
    StateDirectoryError,
    TargetOperationConflictError,
    canonical_plan_json,
    plan_digest,
)

_KEY_ONE = "11111111-1111-4111-8111-111111111111"
_KEY_TWO = "22222222-2222-4222-8222-222222222222"
_CREATED = datetime(2020, 1, 1, tzinfo=UTC)
_EXPIRES = datetime(2100, 1, 1, tzinfo=UTC)


def _new_ledger(root: Path) -> Ledger:
    return Ledger(root / "state", allowed_root=root)


def _plan_payload(*, seed: int = 0) -> dict[str, object]:
    return {
        "algorithmVersion": "pcg32-v1",
        "count": 8,
        "seed": seed,
        "worldPath": "$thenewRJ:rj.ent",
    }


def _stored_plan(ledger: Ledger, *, seed: int = 0) -> str:
    return ledger.save_plan(
        _plan_payload(seed=seed), created_at=_CREATED, expires_at=_EXPIRES
    ).plan_id


def _leave_operation_sending(
    root_text: str,
    state_text: str,
    plan_id: str,
    idempotency_key: str,
) -> None:
    root = Path(root_text)
    ledger = Ledger(Path(state_text), allowed_root=root)
    operation = ledger.bind_operation(
        plan_id=plan_id,
        idempotency_key=idempotency_key,
        now=datetime(2025, 1, 1, tzinfo=UTC),
    )
    ledger.transition_operation(operation.operation_id, OperationState.SENDING)


def _race_operation_binding(
    root_text: str,
    state_text: str,
    plan_id: str,
    idempotency_key: str,
    connection: Connection,
) -> None:
    ledger = Ledger(Path(state_text), allowed_root=Path(root_text))
    connection.send("ready")
    connection.recv()
    try:
        operation = ledger.bind_operation(
            plan_id=plan_id,
            idempotency_key=idempotency_key,
            now=datetime(2025, 1, 1, tzinfo=UTC),
        )
    except OperationConflictError:
        connection.send(("conflict", idempotency_key))
    else:
        connection.send(("bound", operation.operation_id))
    finally:
        connection.close()


def test_canonical_plan_json_and_digest_are_stable_utf8() -> None:
    first = canonical_plan_json({"я": "куст 🌿", "a": [2, 1], "seed": 0})
    second = canonical_plan_json({"seed": 0, "a": [2, 1], "я": "куст 🌿"})

    assert first == second
    assert first == '{"a":[2,1],"seed":0,"я":"куст 🌿"}'
    assert plan_digest(first) == plan_digest(second)
    assert len(plan_digest(first)) == 64


def test_canonical_plan_json_rejects_lone_unicode_surrogate() -> None:
    with pytest.raises(PlanValidationError, match="not finite JSON"):
        canonical_plan_json({"name": "\ud800"})


@pytest.mark.parametrize(
    "invalid",
    [
        '{"b":1, "a":2}',
        '{"a":NaN}',
        '{"a":1,"a":2}',
        "[]",
    ],
)
def test_store_plan_rejects_noncanonical_or_nonfinite_json(tmp_path: Path, invalid: str) -> None:
    ledger = _new_ledger(tmp_path)

    with pytest.raises(PlanValidationError):
        ledger.store_plan(
            canonical_json=invalid,
            created_at=_CREATED,
            expires_at=_EXPIRES,
        )


def test_private_state_directory_database_and_schema_are_durable(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path)
    plan_id = _stored_plan(ledger)

    state_mode = stat.S_IMODE((tmp_path / "state").stat().st_mode)
    database_mode = stat.S_IMODE((tmp_path / "state" / DATABASE_FILENAME).stat().st_mode)
    assert state_mode & 0o077 == 0
    assert database_mode & 0o077 == 0
    assert ledger.quick_check()

    reopened = _new_ledger(tmp_path)
    assert reopened.get_plan(plan_id).canonical_json == canonical_plan_json(_plan_payload())
    assert reopened.quick_check()


def test_state_location_must_be_absolute_contained_private_and_not_symlink(
    tmp_path: Path,
) -> None:
    with pytest.raises(StateDirectoryError, match="absolute"):
        Ledger(Path(".state"), allowed_root=tmp_path)

    outside = tmp_path.parent / f"{tmp_path.name}-outside-state"
    with pytest.raises(StateDirectoryError, match="escapes"):
        Ledger(outside, allowed_root=tmp_path)

    insecure = tmp_path / "insecure"
    insecure.mkdir(mode=0o755)
    insecure.chmod(0o755)
    with pytest.raises(StateDirectoryError, match="group/other"):
        Ledger(insecure, allowed_root=tmp_path)

    real_state = tmp_path / "real-state"
    real_state.mkdir(mode=0o700)
    linked_state = tmp_path / "linked-state"
    linked_state.symlink_to(real_state, target_is_directory=True)
    with pytest.raises(StateDirectoryError, match="symlink"):
        Ledger(linked_state, allowed_root=tmp_path)


def test_database_symlink_is_rejected(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path)
    database = ledger.database_path
    database.unlink()
    decoy = tmp_path / "decoy.sqlite3"
    decoy.touch(mode=0o600)
    database.symlink_to(decoy)

    with pytest.raises(StateDirectoryError, match="database"):
        _new_ledger(tmp_path)


def test_identical_plan_repeat_preserves_original_ttl_and_applied_state(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path)
    original = ledger.save_plan(_plan_payload(), created_at=_CREATED, expires_at=_EXPIRES)
    operation = ledger.bind_operation(
        plan_id=original.plan_id,
        idempotency_key=_KEY_ONE,
        now=datetime(2025, 1, 1, tzinfo=UTC),
    )
    ledger.transition_operation(operation.operation_id, OperationState.SENDING)
    ledger.transition_operation(operation.operation_id, OperationState.APPLIED)

    repeated = ledger.save_plan(
        _plan_payload(),
        created_at=_CREATED + timedelta(days=1),
        expires_at=_EXPIRES + timedelta(days=1),
    )

    assert repeated.plan_id == original.plan_id
    assert repeated.created_at == original.created_at
    assert repeated.expires_at == original.expires_at
    assert repeated.state is OperationState.APPLIED


def test_plan_hash_and_timestamp_validation_happen_before_insert(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path)
    canonical = canonical_plan_json(_plan_payload())

    with pytest.raises(PlanValidationError, match="does not match"):
        ledger.store_plan(
            canonical_json=canonical,
            plan_id="0" * 64,
            created_at=_CREATED,
            expires_at=_EXPIRES,
        )
    with pytest.raises(PlanValidationError, match="later"):
        ledger.store_plan(
            canonical_json=canonical,
            created_at=_CREATED,
            expires_at=_CREATED,
        )
    with pytest.raises(PlanValidationError, match="timezone-aware"):
        ledger.store_plan(
            canonical_json=canonical,
            created_at=datetime(2020, 1, 1),
            expires_at=_EXPIRES,
        )


def test_operation_binding_is_unique_and_operation_id_is_the_uuid(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path)
    first_plan = _stored_plan(ledger)
    second_plan = _stored_plan(ledger, seed=1)

    first = ledger.bind_operation(
        plan_id=first_plan,
        idempotency_key=_KEY_ONE,
        now=datetime(2025, 1, 1, tzinfo=UTC),
    )
    repeat = ledger.bind_operation(
        plan_id=first_plan,
        idempotency_key=_KEY_ONE,
        now=datetime(2025, 1, 2, tzinfo=UTC),
    )

    assert first == repeat
    assert first.operation_id == _KEY_ONE
    assert first.idempotency_key == _KEY_ONE
    with pytest.raises(OperationConflictError, match="another plan"):
        ledger.bind_operation(
            plan_id=second_plan,
            idempotency_key=_KEY_ONE,
            now=datetime(2025, 1, 1, tzinfo=UTC),
        )
    with pytest.raises(OperationConflictError, match="already bound"):
        ledger.bind_operation(
            plan_id=first_plan,
            idempotency_key=_KEY_TWO,
            now=datetime(2025, 1, 1, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "invalid_key",
    [
        "11111111-1111-4111-8111-11111111111Z",
        "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
        "{11111111-1111-4111-8111-111111111111}",
        "00000000-0000-0000-0000-000000000000",
    ],
)
def test_idempotency_key_is_a_strict_canonical_uuid(tmp_path: Path, invalid_key: str) -> None:
    ledger = _new_ledger(tmp_path)
    plan_id = _stored_plan(ledger)

    with pytest.raises(OperationConflictError, match="UUID"):
        ledger.bind_operation(
            plan_id=plan_id,
            idempotency_key=invalid_key,
            now=datetime(2025, 1, 1, tzinfo=UTC),
        )


def test_expired_plan_cannot_be_bound(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path)
    plan = ledger.save_plan(
        _plan_payload(),
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        expires_at=datetime(2025, 1, 2, tzinfo=UTC),
    )

    with pytest.raises(PlanExpiredError):
        ledger.bind_operation(
            plan_id=plan.plan_id,
            idempotency_key=_KEY_ONE,
            now=datetime(2025, 1, 2, tzinfo=UTC),
        )


def test_state_machine_mirrors_plan_state_and_audits_transitions(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path)
    plan_id = _stored_plan(ledger)
    operation = ledger.bind_operation(
        plan_id=plan_id,
        idempotency_key=_KEY_ONE,
        now=datetime(2025, 1, 1, tzinfo=UTC),
    )

    sending = ledger.transition_operation(
        operation.operation_id,
        OperationState.SENDING,
        expected_state=OperationState.PLANNED,
    )
    unknown = ledger.transition_operation(
        operation.operation_id,
        OperationState.UNKNOWN,
        detail="response lost after send",
    )
    applied = ledger.transition_operation(operation.operation_id, OperationState.APPLIED)

    assert sending.state is OperationState.SENDING
    assert unknown.state is OperationState.UNKNOWN
    assert applied.state is OperationState.APPLIED
    assert ledger.get_plan(plan_id).state is OperationState.APPLIED
    assert ledger.operation_events(operation.operation_id) == (
        ("PLANNED", "SENDING", None),
        ("SENDING", "UNKNOWN", "response lost after send"),
        ("UNKNOWN", "APPLIED", None),
    )
    with pytest.raises(InvalidOperationTransitionError):
        ledger.transition_operation(operation.operation_id, OperationState.SENDING)


def test_operation_state_enum_contains_the_complete_fail_closed_vocabulary() -> None:
    assert {state.value for state in OperationState} == {
        "PLANNED",
        "SENDING",
        "UNKNOWN",
        "APPLIED",
        "PARTIAL",
        "ENTITY_CONFLICT",
        "ROLLBACK_VERIFIED",
        "ROLLBACK_FAILED",
        "UNDONE",
        "PRE_SEND_FAILED",
    }


def test_second_live_ledger_does_not_recover_another_live_sender(tmp_path: Path) -> None:
    first_ledger = _new_ledger(tmp_path)
    plan_id = _stored_plan(first_ledger)
    operation = first_ledger.bind_operation(
        plan_id=plan_id,
        idempotency_key=_KEY_ONE,
        now=datetime(2025, 1, 1, tzinfo=UTC),
    )
    first_ledger.transition_operation(operation.operation_id, OperationState.SENDING)

    second_ledger = _new_ledger(tmp_path)

    assert second_ledger.recovered_operations == 0
    assert second_ledger.get_operation(operation.operation_id).state is OperationState.SENDING


def test_process_restart_recovers_orphaned_sending_as_unknown(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path)
    plan_id = _stored_plan(ledger)
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_leave_operation_sending,
        args=(str(tmp_path), str(ledger.state_dir), plan_id, _KEY_ONE),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 0

    restarted = _new_ledger(tmp_path)

    assert restarted.recovered_operations == 1
    recovered = restarted.get_operation(_KEY_ONE)
    assert recovered.state is OperationState.UNKNOWN
    assert restarted.get_plan(plan_id).state is OperationState.UNKNOWN
    assert "sender process exited" in (recovered.detail or "")
    assert restarted.operation_events(_KEY_ONE)[-1][:2] == ("SENDING", "UNKNOWN")


def test_two_processes_cannot_bind_different_keys_to_one_plan(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path)
    plan_id = _stored_plan(ledger)
    context = multiprocessing.get_context("spawn")
    parent_one, child_one = context.Pipe()
    parent_two, child_two = context.Pipe()
    first = context.Process(
        target=_race_operation_binding,
        args=(str(tmp_path), str(ledger.state_dir), plan_id, _KEY_ONE, child_one),
    )
    second = context.Process(
        target=_race_operation_binding,
        args=(str(tmp_path), str(ledger.state_dir), plan_id, _KEY_TWO, child_two),
    )
    first.start()
    second.start()
    child_one.close()
    child_two.close()
    assert parent_one.recv() == "ready"
    assert parent_two.recv() == "ready"
    parent_one.send("go")
    parent_two.send("go")
    results = {parent_one.recv(), parent_two.recv()}
    first.join(timeout=10)
    second.join(timeout=10)
    parent_one.close()
    parent_two.close()

    assert first.exitcode == 0
    assert second.exitcode == 0
    assert {result[0] for result in results} == {"bound", "conflict"}
    stored = ledger.get_operation_for_plan(plan_id)
    assert stored is not None
    assert stored.operation_id in {_KEY_ONE, _KEY_TWO}


def test_sqlite_constraints_make_plan_payload_and_operation_binding_immutable(
    tmp_path: Path,
) -> None:
    ledger = _new_ledger(tmp_path)
    plan_id = _stored_plan(ledger)
    ledger.bind_operation(
        plan_id=plan_id,
        idempotency_key=_KEY_ONE,
        now=datetime(2025, 1, 1, tzinfo=UTC),
    )

    connection = sqlite3.connect(ledger.database_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable plan"):
            connection.execute(
                "UPDATE plans SET canonical_json = ? WHERE plan_id = ?", ("{}", plan_id)
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="operation binding"):
            connection.execute(
                "UPDATE operations SET idempotency_key = ? WHERE operation_id = ?",
                (_KEY_TWO, _KEY_ONE),
            )
    finally:
        connection.close()


def test_unknown_schema_version_fails_closed(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path)
    connection = sqlite3.connect(ledger.database_path)
    try:
        connection.execute("PRAGMA user_version = 99")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(SchemaVersionError, match="unsupported"):
        _new_ledger(tmp_path)


def test_database_owner_matches_current_user(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path)

    assert ledger.database_path.stat().st_uid == os.geteuid()


@pytest.mark.parametrize(
    "state",
    [
        OperationState.SENDING,
        OperationState.UNKNOWN,
        OperationState.PARTIAL,
        OperationState.ROLLBACK_FAILED,
        OperationState.ENTITY_CONFLICT,
    ],
)
def test_send_claim_atomically_blocks_unresolved_same_endpoint(
    tmp_path: Path, state: OperationState
) -> None:
    ledger = _new_ledger(tmp_path)
    first = _stored_plan(ledger, seed=1)
    second = _stored_plan(ledger, seed=2)
    ledger.bind_operation(plan_id=first, idempotency_key=_KEY_ONE)
    ledger.bind_operation(plan_id=second, idempotency_key=_KEY_TWO)
    ledger.transition_operation(_KEY_ONE, OperationState.SENDING, target_endpoint="endpoint-a")
    if state is not OperationState.SENDING:
        ledger.transition_operation(_KEY_ONE, state)
    with pytest.raises(TargetOperationConflictError) as captured:
        ledger.transition_operation(_KEY_TWO, OperationState.SENDING, target_endpoint="endpoint-a")
    assert captured.value.operation.operation_id == _KEY_ONE
    assert ledger.get_operation(_KEY_TWO).state is OperationState.PLANNED
    # A different real endpoint has an independent mutation history.
    ledger.transition_operation(_KEY_TWO, OperationState.SENDING, target_endpoint="endpoint-b")


def test_endpoint_binding_survives_restart_and_cannot_be_reassigned(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path)
    plan_id = _stored_plan(ledger)
    ledger.bind_operation(plan_id=plan_id, idempotency_key=_KEY_ONE)
    ledger.transition_operation(_KEY_ONE, OperationState.SENDING, target_endpoint="endpoint-a")
    ledger.transition_operation(_KEY_ONE, OperationState.PRE_SEND_FAILED)
    restarted = _new_ledger(tmp_path)
    with pytest.raises(OperationConflictError, match="different Workbench endpoint"):
        restarted.transition_operation(
            _KEY_ONE, OperationState.SENDING, target_endpoint="endpoint-b"
        )
    with pytest.raises(OperationConflictError, match="different Workbench endpoint"):
        restarted.assert_target_binding(_KEY_ONE, "endpoint-b")
    with sqlite3.connect(ledger.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="cannot be updated"):
            connection.execute("UPDATE operation_targets SET endpoint = 'endpoint-b'")
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            connection.execute("DELETE FROM operation_targets")


def test_v1_migration_preserves_history_and_blocks_legacy_unresolved_globally(
    tmp_path: Path,
) -> None:
    ledger = _new_ledger(tmp_path)
    first = _stored_plan(ledger, seed=1)
    second = _stored_plan(ledger, seed=2)
    ledger.bind_operation(plan_id=first, idempotency_key=_KEY_ONE)
    ledger.transition_operation(_KEY_ONE, OperationState.SENDING)
    ledger.transition_operation(_KEY_ONE, OperationState.UNKNOWN, detail="legacy lost response")
    original_plan = ledger.get_plan(first)
    original_events = ledger.operation_events(_KEY_ONE)
    # Reconstitute the actual V1 layout by removing only the V2 addition.
    with sqlite3.connect(ledger.database_path) as connection:
        connection.execute("DROP TABLE operation_targets")
        connection.execute("PRAGMA user_version = 1")
        connection.execute("UPDATE schema_metadata SET version = 1")

    migrated = _new_ledger(tmp_path)
    assert migrated.get_plan(first) == original_plan
    assert migrated.operation_events(_KEY_ONE) == original_events
    assert migrated.quick_check()
    migrated.bind_operation(plan_id=second, idempotency_key=_KEY_TWO)
    for endpoint in ("endpoint-a", "endpoint-b"):
        with pytest.raises(TargetOperationConflictError):
            migrated.transition_operation(
                _KEY_TWO, OperationState.SENDING, target_endpoint=endpoint
            )
    with sqlite3.connect(ledger.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM operation_targets").fetchone()[0] == 0
    # A proved complete reconciliation can resolve a legacy UNKNOWN safely.
    migrated.transition_operation(_KEY_ONE, OperationState.APPLIED)
    migrated.transition_operation(_KEY_TWO, OperationState.SENDING, target_endpoint="endpoint-a")


@pytest.mark.parametrize("state", [OperationState.PLANNED, OperationState.PRE_SEND_FAILED])
def test_final_send_claim_checks_expiry_without_advancing_state(
    tmp_path: Path, state: OperationState
) -> None:
    ledger = _new_ledger(tmp_path)
    plan_id = _stored_plan(ledger)
    ledger.bind_operation(plan_id=plan_id, idempotency_key=_KEY_ONE)
    if state is OperationState.PRE_SEND_FAILED:
        ledger.transition_operation(_KEY_ONE, state)
    events = ledger.operation_events(_KEY_ONE)
    with pytest.raises(PlanExpiredError):
        ledger.transition_operation(
            _KEY_ONE, OperationState.SENDING, now=_EXPIRES, target_endpoint="endpoint-a"
        )
    assert ledger.get_operation(_KEY_ONE).state is state
    assert ledger.operation_events(_KEY_ONE) == events


def test_expiry_is_sampled_after_waiting_for_sqlite_write_lock(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path)
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=0.25)
    plan = ledger.save_plan(_plan_payload(), created_at=now, expires_at=expires_at)
    ledger.bind_operation(plan_id=plan.plan_id, idempotency_key=_KEY_ONE)
    started = threading.Event()
    outcome: list[str] = []

    def claim() -> None:
        started.set()
        try:
            ledger.transition_operation(
                _KEY_ONE, OperationState.SENDING, target_endpoint="endpoint-a"
            )
        except PlanExpiredError:
            outcome.append("expired")
        else:
            outcome.append("sent")

    with sqlite3.connect(ledger.database_path) as blocking_connection:
        blocking_connection.execute("BEGIN IMMEDIATE")
        worker = threading.Thread(target=claim)
        worker.start()
        assert started.wait(timeout=2)
        time.sleep(max(0.0, (expires_at - datetime.now(UTC)).total_seconds()) + 0.05)
        blocking_connection.commit()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert outcome == ["expired"]
    assert ledger.get_operation(_KEY_ONE).state is OperationState.PLANNED
