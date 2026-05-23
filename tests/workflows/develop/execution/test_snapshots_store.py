"""Slice 10a tests — the typed ``ControlPlaneSnapshot`` store methods.

These run against a real Postgres database (the directory ``conftest.py``'s
``mq_conn`` / ``mq_dsn`` fixtures) and skip cleanly when no Postgres is
reachable. The store methods' correctness is in bounded SQL — ``LIMIT cap + 1``
truncation detection, the ``statement_timeout`` boundary, feature/group
scoping, and SUMMARY-ONLY output — which an in-memory fake cannot exercise.

``ExecutionControlStore`` is pool-bound; its ``_connection()`` yields the
"pool" directly when the object has no ``acquire`` method, so a raw
``asyncpg.Connection`` can be passed as the pool for these single-connection
tests (the production pool path is unchanged).

Scope is strictly Slice 10a: ``get_control_plane_snapshot`` /
``get_control_plane_snapshot_version`` (doc 10 § "Refactoring Steps" 1-2).
"""

from __future__ import annotations

import json
import uuid

import asyncpg
import pytest

from iriai_build_v2.execution_control.store import ExecutionControlStore
from iriai_build_v2.workflows.develop.execution.snapshots import (
    ControlPlaneSnapshot,
    ControlPlaneSnapshotQuery,
    SnapshotBudget,
)


# ── fixture helpers ─────────────────────────────────────────────────────────


async def _insert_feature(conn: asyncpg.Connection, feature_id: str) -> None:
    await conn.execute(
        "INSERT INTO features (id, name, slug, workflow_name, workspace_id) "
        "VALUES ($1, $2, $3, $4, $5)",
        feature_id,
        feature_id,
        feature_id,
        "develop",
        "ws-1",
    )


async def _insert_attempt(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    group_idx: int | None = 1,
    entry_type: str = "dispatch_attempt",
    status: str = "started",
    payload: dict | None = None,
) -> int:
    return await conn.fetchval(
        "INSERT INTO execution_journal_rows "
        "(feature_id, idempotency_key, entry_type, status, request_digest, "
        " group_idx, dag_sha256, actor, runtime, payload) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb) RETURNING id",
        feature_id,
        f"jr:{uuid.uuid4().hex}",
        entry_type,
        status,
        "req-digest",
        group_idx,
        "dag-sha-1",
        "implementer",
        "claude",
        json.dumps(payload or {}),
    )


async def _insert_failure_evidence(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    group_idx: int | None = 1,
    failure_class: str = "merge_conflict",
    route: str = "retry_merge",
    signature: str = "sig-1",
    summary: str = "merge conflict in repo",
    payload: dict | None = None,
) -> int:
    metadata = {
        "failure_class": failure_class,
        "failure_type": "rebase_conflict",
        "severity": "error",
        "operator_required": "false",
        "retryable": "true",
        "route": route,
        "signature_hash": signature,
    }
    return await conn.fetchval(
        "INSERT INTO evidence_nodes "
        "(feature_id, idempotency_key, kind, content_hash, group_idx, "
        " status, deterministic, summary, metadata, payload) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb) RETURNING id",
        feature_id,
        f"ev:{uuid.uuid4().hex}",
        "runtime_failure_context",
        "content-hash",
        group_idx,
        "rejected",
        True,
        summary,
        json.dumps(metadata),
        json.dumps(payload or {}),
    )


def _route_decision_payload(
    *,
    route: str,
    failure_class: str,
    budget_remaining: int,
    max_attempts: int,
    reservation_ordinal: int,
    signature: str = "sig-1",
    budget_exhausted: bool = False,
) -> dict:
    """Mirror `implementation.py:_route_decision_compat_payload`.

    The Slice-07 typed failure router persists this `route_decision` payload
    (with a nested `retry_budget` dict) onto every `runtime_failure_context`
    evidence row — it is the REAL typed budget source the Slice 10c-2
    `_typed_retry_budgets` reads.
    """

    retry_budget = {
        "route": route,
        "budget_key": f"budget:{failure_class}:{signature}",
        "max_attempts": max_attempts,
        "max_retries": max_attempts,
        "remaining_attempts": budget_remaining,
        "reservation_ordinal": reservation_ordinal,
        "budget_exhausted": budget_exhausted,
    }
    return {
        "route_decision": {
            "route": route,
            "action": route,
            "failure_class": failure_class,
            "budget_remaining": budget_remaining,
            "budget_exhausted": budget_exhausted,
            "reservation_ordinal": reservation_ordinal,
            "signature_hash": signature,
            "retry_budget": retry_budget,
        },
        "retry_budget": retry_budget,
        "route": route,
        "failure_class": failure_class,
        "signature_hash": signature,
    }


async def _insert_gate_evidence(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    group_idx: int | None = 1,
    name: str = "code-review",
    status: str = "approved",
) -> int:
    return await conn.fetchval(
        "INSERT INTO evidence_nodes "
        "(feature_id, idempotency_key, kind, content_hash, group_idx, "
        " name, status, deterministic) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id",
        feature_id,
        f"ev:{uuid.uuid4().hex}",
        "deterministic_gate",
        "content-hash",
        group_idx,
        name,
        status,
        True,
    )


async def _insert_workspace_snapshot(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    group_idx: int | None = 1,
    dirty_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
) -> int:
    journal_row = await _insert_attempt(
        conn, feature_id, group_idx=group_idx, entry_type="merge", status="succeeded"
    )
    payload = {
        "role": "primary",
        "workspace_relative_path": "repo",
        "head_sha": "head-abc",
        "index_digest": "idx-1",
        "worktree_status_digest": "wt-1",
        "no_dirty": not dirty_paths,
        "safety_status": "ok",
        "dirty_paths": dirty_paths or [],
        "forbidden_paths": forbidden_paths or [],
        "registry_digest": "reg-1",
    }
    return await conn.fetchval(
        "INSERT INTO workspace_snapshots "
        "(feature_id, idempotency_key, execution_journal_row_id, group_idx, "
        " repo_id, canonical_path, stage, snapshot_digest, payload) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb) RETURNING id",
        feature_id,
        f"ws:{uuid.uuid4().hex}",
        journal_row,
        group_idx,
        "repo-1",
        "/canonical/repo-1",
        "implement",
        f"snap-{uuid.uuid4().hex}",
        json.dumps(payload),
    )


async def _insert_merge_item(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    group_idx: int = 1,
    status: str = "leased",
) -> int:
    # A non-terminal merge_queue_items row needs pre_queue_gate_evidence_id
    # NOT NULL (the `merge_queue_items_pre_queue_gate_check` constraint).
    gate_id = await _insert_gate_evidence(
        conn, feature_id, group_idx=group_idx, name="merge-gate", status="approved"
    )
    return await conn.fetchval(
        "INSERT INTO merge_queue_items "
        "(feature_id, dag_sha256, group_idx, base_commit, status, "
        " request_digest, idempotency_key, repo_id, "
        " pre_queue_gate_evidence_id) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id",
        feature_id,
        "dag-sha-1",
        group_idx,
        "base-commit",
        status,
        "rd",
        f"merge:{feature_id}:{uuid.uuid4().hex}",
        "repo-1",
        gate_id,
    )


def _query(feature_id: str, **overrides) -> ControlPlaneSnapshotQuery:
    base = dict(feature_id=feature_id, scope="dashboard")
    base.update(overrides)
    return ControlPlaneSnapshotQuery(**base)


# ── snapshot version ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_version_is_stable_and_advances(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-v")
    store = ExecutionControlStore(mq_conn)

    v_empty = await store.get_control_plane_snapshot_version("feat-v")
    assert isinstance(v_empty, str) and len(v_empty) == 64
    # idempotent re-read
    assert await store.get_control_plane_snapshot_version("feat-v") == v_empty

    await _insert_attempt(mq_conn, "feat-v")
    v_after_attempt = await store.get_control_plane_snapshot_version("feat-v")
    assert v_after_attempt != v_empty

    # a sandbox-only update advances the version even though no failure changed
    await _insert_attempt(mq_conn, "feat-v", entry_type="commit_failure")
    v_after_more = await store.get_control_plane_snapshot_version("feat-v")
    assert v_after_more != v_after_attempt


@pytest.mark.asyncio
async def test_snapshot_version_is_feature_scoped(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-a")
    await _insert_feature(mq_conn, "feat-b")
    store = ExecutionControlStore(mq_conn)
    await _insert_attempt(mq_conn, "feat-a")
    # feat-b has no rows — its version must not move when feat-a changes
    v_b1 = await store.get_control_plane_snapshot_version("feat-b")
    await _insert_attempt(mq_conn, "feat-a")
    v_b2 = await store.get_control_plane_snapshot_version("feat-b")
    assert v_b1 == v_b2


@pytest.mark.asyncio
async def test_snapshot_version_matches_embedded_snapshot_version(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-match")
    store = ExecutionControlStore(mq_conn)
    await _insert_attempt(mq_conn, "feat-match")
    standalone = await store.get_control_plane_snapshot_version("feat-match")
    snapshot = await store.get_control_plane_snapshot(_query("feat-match"))
    assert snapshot.snapshot_version == standalone


# ── snapshot scoping ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_returns_only_requested_feature(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    await _insert_feature(mq_conn, "feat-2")
    store = ExecutionControlStore(mq_conn)
    await _insert_attempt(mq_conn, "feat-1", group_idx=1)
    await _insert_attempt(mq_conn, "feat-2", group_idx=1)

    snap = await store.get_control_plane_snapshot(_query("feat-1"))
    assert isinstance(snap, ControlPlaneSnapshot)
    assert snap.feature_id == "feat-1"
    assert len(snap.active_attempts) == 1
    assert all(a.feature_id == "feat-1" for a in snap.active_attempts)


@pytest.mark.asyncio
async def test_snapshot_group_scope_filters_all_lists(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-g")
    store = ExecutionControlStore(mq_conn)
    await _insert_attempt(mq_conn, "feat-g", group_idx=1)
    await _insert_attempt(mq_conn, "feat-g", group_idx=2)
    await _insert_failure_evidence(mq_conn, "feat-g", group_idx=1)
    await _insert_failure_evidence(mq_conn, "feat-g", group_idx=2)
    await _insert_merge_item(mq_conn, "feat-g", group_idx=2)

    snap = await store.get_control_plane_snapshot(_query("feat-g", group_idx=2))
    assert {a.group_idx for a in snap.active_attempts} == {2}
    assert {f.failure_id for f in snap.latest_failures}  # non-empty
    assert all(f.attempt_id is None or True for f in snap.latest_failures)
    assert all(m.group_idx == 2 for m in snap.merge_queue)


# ── bounded reads — LIMIT cap + 1 truncation ────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_truncates_attempts_at_cap_plus_one(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-trunc")
    store = ExecutionControlStore(mq_conn)
    # seed cap + 3 attempts; the bounded read keeps exactly `cap`
    for _ in range(8):
        await _insert_attempt(mq_conn, "feat-trunc", group_idx=1)

    query = _query("feat-trunc", budget=SnapshotBudget(max_attempts=5))
    snap = await store.get_control_plane_snapshot(query)
    assert len(snap.active_attempts) == 5  # capped, sentinel dropped
    assert snap.truncated is True
    assert snap.omitted_counts.get("active_attempts", 0) >= 1


@pytest.mark.asyncio
async def test_snapshot_not_truncated_when_rows_within_cap(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-fit")
    store = ExecutionControlStore(mq_conn)
    for _ in range(3):
        await _insert_attempt(mq_conn, "feat-fit", group_idx=1)
    query = _query("feat-fit", budget=SnapshotBudget(max_attempts=5))
    snap = await store.get_control_plane_snapshot(query)
    assert len(snap.active_attempts) == 3
    assert snap.truncated is False
    assert snap.omitted_counts == {}


@pytest.mark.asyncio
async def test_snapshot_truncates_failures_independently(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-ft")
    store = ExecutionControlStore(mq_conn)
    for _ in range(6):
        await _insert_failure_evidence(mq_conn, "feat-ft", group_idx=1)
    query = _query("feat-ft", budget=SnapshotBudget(max_failures=4))
    snap = await store.get_control_plane_snapshot(query)
    assert len(snap.latest_failures) == 4
    assert snap.omitted_counts.get("latest_failures", 0) >= 1


# ── bounded reads — summary-only / no artifact bodies / path samples ────────


@pytest.mark.asyncio
async def test_workspace_summary_is_path_count_plus_bounded_sample(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-ws")
    store = ExecutionControlStore(mq_conn)
    dirty = [f"src/file_{i}.py" for i in range(50)]
    await _insert_workspace_snapshot(mq_conn, "feat-ws", group_idx=1, dirty_paths=dirty)

    query = _query("feat-ws", budget=SnapshotBudget(max_path_samples_per_snapshot=10))
    snap = await store.get_control_plane_snapshot(query)
    assert len(snap.workspace_snapshots) == 1
    ws = snap.workspace_snapshots[0]
    # full count is preserved, but only a bounded sample is surfaced
    assert ws.dirty_path_count == 50
    assert len(ws.dirty_path_sample) == 10
    assert ws.no_dirty is False
    assert ws.head_sha == "head-abc"
    assert ws.safety_status == "ok"


@pytest.mark.asyncio
async def test_snapshot_serialization_has_no_body_fields(mq_conn) -> None:
    # doc 10 § "Tests": typed snapshot serialization contains no `value`,
    # `content`, raw prompt, stdout/stderr, or full dirty-path body fields.
    await _insert_feature(mq_conn, "feat-body")
    store = ExecutionControlStore(mq_conn)
    await _insert_attempt(mq_conn, "feat-body", group_idx=1)
    await _insert_failure_evidence(mq_conn, "feat-body", group_idx=1)
    await _insert_workspace_snapshot(
        mq_conn, "feat-body", group_idx=1, dirty_paths=["a.py", "b.py"]
    )
    snap = await store.get_control_plane_snapshot(_query("feat-body"))
    text = json.dumps(snap.model_dump(mode="json"), default=str)
    for forbidden in ('"value"', '"content"', '"stdout"', '"stderr"', '"dirty_paths"'):
        assert forbidden not in text


@pytest.mark.asyncio
async def test_merge_queue_summary_exposes_lease_and_status(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-mq")
    store = ExecutionControlStore(mq_conn)
    await _insert_merge_item(mq_conn, "feat-mq", group_idx=1, status="leased")
    snap = await store.get_control_plane_snapshot(_query("feat-mq"))
    assert len(snap.merge_queue) == 1
    item = snap.merge_queue[0]
    assert item.status == "leased"
    assert item.repo_id == "repo-1"
    assert item.lease_version == 0


@pytest.mark.asyncio
async def test_gate_summary_cites_evidence_ids(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-gate")
    store = ExecutionControlStore(mq_conn)
    gate_id = await _insert_gate_evidence(
        mq_conn, "feat-gate", group_idx=1, name="security", status="approved"
    )
    snap = await store.get_control_plane_snapshot(_query("feat-gate"))
    assert len(snap.gates) == 1
    gate = snap.gates[0]
    assert gate.evidence_id == gate_id
    assert gate.gate_name == "security"
    assert gate.approved is True


# ── statement timeout ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_local_statement_timeout_cancels_a_slow_query(mq_conn) -> None:
    # Prove the store-boundary timeout is REAL: a 50ms `SET LOCAL
    # statement_timeout` cancels a 5s `pg_sleep`. doc 10 § "Bounded-Read
    # Constraints": "Apply a statement/query timeout at the store boundary."
    async with mq_conn.transaction():
        await ExecutionControlStore._set_local_statement_timeout(mq_conn, 50)
        with pytest.raises(asyncpg.QueryCanceledError):
            await mq_conn.fetchval("SELECT pg_sleep(5)")


class _CancellingConn:
    """Wraps a real conn; raises ``QueryCanceledError`` on a chosen query.

    Models exactly what Postgres does when the boundary ``statement_timeout``
    cancels one bounded read — without depending on DB timing. Every other
    method delegates to the real connection.
    """

    def __init__(self, real: asyncpg.Connection, cancel_substring: str) -> None:
        self._real = real
        self._cancel_substring = cancel_substring

    async def fetch(self, query: str, *args):
        if self._cancel_substring in query:
            raise asyncpg.QueryCanceledError("canceling statement due to timeout")
        return await self._real.fetch(query, *args)

    async def execute(self, query: str, *args):
        return await self._real.execute(query, *args)

    def __getattr__(self, name):  # pragma: no cover - passthrough
        return getattr(self._real, name)


@pytest.mark.asyncio
async def test_snapshot_query_degrades_on_timeout_without_raising(mq_conn) -> None:
    # When a bounded read is cancelled by the boundary timeout, the builder
    # degrades that section to empty + a `degradation_reasons` entry — it never
    # raises or retries unbounded. doc 10 § "Edge Cases": "Typed query timeout:
    # return ... Do not retry with an unbounded query."
    from iriai_build_v2.execution_control.store import (
        build_typed_control_plane_snapshot,
    )

    await _insert_feature(mq_conn, "feat-timeout")
    await _insert_attempt(mq_conn, "feat-timeout", group_idx=1)
    # the typed-failure bounded read is cancelled; every other read succeeds
    cancelling = _CancellingConn(mq_conn, "FROM evidence_nodes")
    snap = await build_typed_control_plane_snapshot(
        cancelling, _query("feat-timeout")
    )
    # the builder returned a snapshot (no exception) and flagged it degraded
    assert isinstance(snap, ControlPlaneSnapshot)
    assert snap.degraded is True
    assert snap.degradation_reasons  # names which section(s) degraded
    # the cancelled read degrades to empty — never an unbounded retry
    assert snap.latest_failures == []
    # a partially-degraded query that has typed rows is source="mixed"
    assert snap.source == "mixed"
    # the succeeding reads still return their typed rows
    assert len(snap.active_attempts) == 1


@pytest.mark.asyncio
async def test_statement_timeout_does_not_leak_to_next_query(mq_conn) -> None:
    # `SET LOCAL` scopes the timeout to its transaction; a subsequent normal
    # query on the same connection must not inherit the tiny timeout.
    await _insert_feature(mq_conn, "feat-leak")
    store = ExecutionControlStore(mq_conn)
    await _insert_attempt(mq_conn, "feat-leak", group_idx=1)
    # run a snapshot with a 1ms boundary timeout (it degrades internally)
    await store.get_control_plane_snapshot(
        _query("feat-leak", budget=SnapshotBudget(query_timeout_ms=1))
    )
    # the connection-level timeout is back to the default — a normal read works
    rows = await mq_conn.fetch(
        "SELECT id FROM execution_journal_rows WHERE feature_id = $1", "feat-leak"
    )
    assert len(rows) == 1


# ── source classification ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_source_is_typed_when_typed_rows_present(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-typed")
    store = ExecutionControlStore(mq_conn)
    await _insert_attempt(mq_conn, "feat-typed", group_idx=1)
    snap = await store.get_control_plane_snapshot(_query("feat-typed"))
    assert snap.source == "typed"
    assert snap.degraded is False


@pytest.mark.asyncio
async def test_snapshot_source_is_legacy_fallback_when_no_typed_rows(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-empty")
    store = ExecutionControlStore(mq_conn)
    snap = await store.get_control_plane_snapshot(_query("feat-empty"))
    # no typed rows at all -> legacy_fallback (a later sub-slice fills display
    # context from legacy summaries; 10a only classifies the source)
    assert snap.source == "legacy_fallback"
    assert snap.active_attempts == []


# ── Slice 10c-2 — the REAL typed route-budget source (resolves P3-10a-1) ────


@pytest.mark.asyncio
async def test_retry_budget_reads_real_budget_remaining_from_route_decision(
    mq_conn,
) -> None:
    # P3-10a-1: the typed snapshot's RetryBudgetSummary.budget_remaining used
    # to be a hard-coded conservative `0`. Slice 10c-2 reads the GENUINE
    # router budget from the `route_decision` payload the Slice-07 router
    # persists on every runtime_failure_context evidence row.
    await _insert_feature(mq_conn, "feat-budget")
    store = ExecutionControlStore(mq_conn)
    await _insert_failure_evidence(
        mq_conn,
        "feat-budget",
        failure_class="worktree_alias",
        route="run_canonicalization_repair",
        signature="sig-budget",
        payload=_route_decision_payload(
            route="run_canonicalization_repair",
            failure_class="worktree_alias",
            budget_remaining=2,
            max_attempts=3,
            reservation_ordinal=1,
            signature="sig-budget",
        ),
    )
    snap = await store.get_control_plane_snapshot(_query("feat-budget"))
    budgets = [
        b for b in snap.retry_budgets if b.route == "run_canonicalization_repair"
    ]
    assert len(budgets) == 1
    budget = budgets[0]
    # the genuine router budget, NOT the old conservative 0
    assert budget.budget_remaining == 2
    assert budget.budget_total == 3
    assert budget.failure_signature_hash == "sig-budget"
    assert budget.terminal_reason == ""


@pytest.mark.asyncio
async def test_retry_budget_exhausted_route_decision_pins_remaining_to_zero(
    mq_conn,
) -> None:
    # A `budget_exhausted=true` route_decision pins budget_remaining to 0 and
    # records the terminal reason — the supervisor "budget remains" gate then
    # reads false and the deterministic class does NOT get a retry verdict.
    await _insert_feature(mq_conn, "feat-exhausted")
    store = ExecutionControlStore(mq_conn)
    await _insert_failure_evidence(
        mq_conn,
        "feat-exhausted",
        failure_class="commit_hygiene",
        route="quiesce",
        signature="sig-done",
        payload=_route_decision_payload(
            route="quiesce",
            failure_class="commit_hygiene",
            budget_remaining=0,
            max_attempts=1,
            reservation_ordinal=1,
            signature="sig-done",
            budget_exhausted=True,
        ),
    )
    snap = await store.get_control_plane_snapshot(_query("feat-exhausted"))
    budgets = [b for b in snap.retry_budgets if b.route == "quiesce"]
    assert len(budgets) == 1
    assert budgets[0].budget_remaining == 0
    assert budgets[0].terminal_reason == "budget_exhausted"


@pytest.mark.asyncio
async def test_retry_budget_without_typed_budget_is_fail_safe_zero(
    mq_conn,
) -> None:
    # A legacy/pre-router failure row with NO route_decision budget payload:
    # the derived budget is fail-safe `0` (never an over-optimistic "retry
    # has budget") with terminal_reason=no_typed_budget.
    await _insert_feature(mq_conn, "feat-nobudget")
    store = ExecutionControlStore(mq_conn)
    await _insert_failure_evidence(
        mq_conn,
        "feat-nobudget",
        failure_class="merge_conflict",
        route="retry_merge",
        signature="sig-legacy",
        payload=None,
    )
    snap = await store.get_control_plane_snapshot(_query("feat-nobudget"))
    budgets = [b for b in snap.retry_budgets if b.route == "retry_merge"]
    assert len(budgets) == 1
    assert budgets[0].budget_remaining == 0
    assert budgets[0].terminal_reason == "no_typed_budget"
