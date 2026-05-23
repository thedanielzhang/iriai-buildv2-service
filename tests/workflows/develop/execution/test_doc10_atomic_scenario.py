"""Slice 10g-2 — the doc-10 atomic-feature end-to-end scenario.

doc 10 § "Tests" — Integration/regression: "Atomic feature test seeds typed
rows, legacy projections, dashboard request, MCP snapshot request, classifier
digest, and Slack dedupe in one scenario to prove all consumers use the same
snapshot version."

This module exercises the FULL Slice-10 surface against a real Postgres test
fixture (the ``tests/workflows/develop/execution/conftest.py``'s ``mq_conn`` —
the schema bundles supervisor + execution + public-outbox tables together).

Test plan (one comprehensive seeded scenario):

1. Seed typed rows across all EIGHT cursor tables that contribute to the typed
   snapshot version digest (doc 10 § "Proposed Interfaces/Types"):
   ``execution_attempts`` (physical: ``execution_journal_rows``),
   ``typed_failures`` + ``failure_route_budgets`` + ``evidence_nodes`` (physical:
   ``evidence_nodes``), ``merge_queue_items``, ``workspace_snapshots``,
   ``sandbox_leases``, ``runtime_workspace_bindings``.
2. Materialize a projection that ALSO advances the typed cursor by running
   ``project_control_plane_snapshot_if_changed`` (the same helper
   ``_complete_missing_projections`` invokes inside every projection
   transaction) and confirm exactly ONE
   ``control_plane.snapshot_changed`` row landed.
3. Drive the dashboard FastAPI route handlers directly
   (``dashboard.get_feature_control_plane`` + ``dashboard.get_feature``) and
   assert the typed shape, the ETag folding the typed snapshot version, and
   the bounded summary-only contract.
4. Drive the supervisor MCP path
   (``SupervisorEvidenceMcpService.get_current_snapshot``) and assert
   ``evidence_mode == "typed"`` AND that the Slice-10c-2 typed-PRIMARY
   classifier returns a verdict whose ``mapping_row`` proves it came from the
   typed mapping (not a legacy-artifact fallback path).
5. Drive ``SupervisorDigestDedupeStore.decide`` three times (a new key, a
   repeat of the same key + snapshot_version, a never-suppress escalation) and
   assert rows land in ``supervisor_digest_state`` + ``supervisor_digest_audit``
   with a complete audit history.
6. After every state transition that advances the snapshot version, verify
   exactly ONE ``control_plane.snapshot_changed`` row was emitted per unique
   version and the row's payload reflects the latest typed snapshot
   (``visibility="public"``, idempotency key folds the typed version).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

import dashboard
from iriai_build_v2.execution_control.store import ExecutionControlStore
from iriai_build_v2.public_dashboard import (
    PublicDashboardOutbox,
    control_plane_snapshot_event_id,
    project_control_plane_snapshot_if_changed,
)
from iriai_build_v2.supervisor.classifier import SupervisorClassifier
from iriai_build_v2.supervisor.digest_dedupe import (
    SupervisorDigestDedupeStore,
    compute_dedupe_key,
)
from iriai_build_v2.supervisor.evidence import (
    evidence_mode_for_snapshot,
    load_typed_control_plane_snapshot,
)
from iriai_build_v2.supervisor.mcp_server import SupervisorEvidenceMcpService
from iriai_build_v2.supervisor.models import (
    SupervisorDigestKey,
    SupervisorObservation,
)
from iriai_build_v2.workflows.develop.execution.snapshots import (
    ControlPlaneSnapshotQuery,
)


_FEATURE_ID = "feat-doc10-atomic"
_GROUP_IDX = 7
_DAG_SHA = "d" * 64


# ─────────────────────────────────────────────────────────────────────────────
# Seeding helpers — raw inserts against the typed tables.
#
# Each helper cites the ``schema.sql`` column set it writes; the column list
# matches schema.sql 1:1 so a reviewer can verify the contract. The public
# ``ExecutionControlStore`` API is too coarse for one-row-per-table seeding
# (e.g. it would require a successful end-to-end runtime invocation just to
# land one typed failure row). Raw inserts are the established pattern used by
# the sibling ``test_snapshots_store.py``.
# ─────────────────────────────────────────────────────────────────────────────


async def _insert_feature(conn: asyncpg.Connection, feature_id: str) -> None:
    """schema.sql ``features`` (lines 1-11)."""

    # ``workflow_name='bugfix-v2'`` selects the bugfix branch in
    # ``dashboard.get_feature``. The bugfix branch's bounded artifact-preview
    # SQL works against a single-connection asyncpg fixture (the
    # develop-branch SQL uses a ``CASE ... THEN $2 ELSE $3`` substring length
    # expression that asyncpg's prepared-statement parser cannot type-infer
    # against an unprepared connection — a pre-existing source quirk
    # documented in the test report). The embedded ``control_plane`` object
    # comes from the same ``_compact_typed_control_plane`` builder in both
    # branches (see ``dashboard.py:668-684``), so this still exercises the
    # doc-10 step-6 embedding contract.
    await conn.execute(
        "INSERT INTO features (id, name, slug, workflow_name, workspace_id) "
        "VALUES ($1, $2, $3, $4, $5)",
        feature_id,
        feature_id,
        feature_id,
        "bugfix-v2",
        "ws-1",
    )


async def _insert_attempt(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    group_idx: int | None = _GROUP_IDX,
    entry_type: str = "dispatch_attempt",
    status: str = "started",
    payload: dict[str, Any] | None = None,
) -> int:
    """schema.sql ``execution_journal_rows`` (lines 33-64).

    Backs the doc-10 logical ``execution_attempts`` cursor.
    """

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
        _DAG_SHA,
        "implementer",
        "claude",
        json.dumps(payload or {}),
    )


async def _insert_failure_evidence(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    attempt_id: int,
    failure_class: str = "worktree_alias",
    route: str = "run_canonicalization_repair",
    signature: str = "sig-1",
    budget_remaining: int = 3,
    max_attempts: int = 5,
    reservation_ordinal: int = 1,
    deterministic: bool = True,
    operator_required: bool = False,
    retryable: bool = True,
    status: str = "rejected",
    severity: str = "error",
) -> int:
    """schema.sql ``evidence_nodes`` (lines 457-503).

    Backs the doc-10 logical ``typed_failures`` + ``failure_route_budgets``
    cursors. The route-decision payload mirrors
    ``implementation.py:_route_decision_compat_payload`` so the Slice-10c-2
    ``_typed_retry_budgets`` derives a genuine budget (see
    ``execution_control/store.py:2158-2263``).
    """

    metadata = {
        "failure_class": failure_class,
        "failure_type": failure_class,
        "severity": severity,
        "operator_required": str(operator_required).lower(),
        "retryable": str(retryable).lower(),
        "route": route,
        "signature_hash": signature,
    }
    retry_budget = {
        "route": route,
        "budget_key": f"budget:{failure_class}:{signature}",
        "max_attempts": max_attempts,
        "max_retries": max_attempts,
        "remaining_attempts": budget_remaining,
        "reservation_ordinal": reservation_ordinal,
    }
    payload = {
        "route_decision": {
            "route": route,
            "action": route,
            "failure_class": failure_class,
            "budget_remaining": budget_remaining,
            "budget_exhausted": budget_remaining <= 0,
            "reservation_ordinal": reservation_ordinal,
            "signature_hash": signature,
            "operator_required": operator_required,
            "retryable": retryable,
            "deterministic": deterministic,
            "retry_budget": retry_budget,
        },
        "retry_budget": retry_budget,
        "route": route,
        "failure_class": failure_class,
        "signature_hash": signature,
    }
    return await conn.fetchval(
        "INSERT INTO evidence_nodes "
        "(feature_id, idempotency_key, attempt_id, kind, content_hash, "
        " group_idx, status, deterministic, summary, metadata, payload) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb) RETURNING id",
        feature_id,
        f"ev:{uuid.uuid4().hex}",
        attempt_id,
        "runtime_failure_context",
        f"hash:{uuid.uuid4().hex[:16]}",
        _GROUP_IDX,
        status,
        deterministic,
        f"typed-failure: {failure_class}/{route}",
        json.dumps(metadata),
        json.dumps(payload),
    )


async def _insert_gate_evidence(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    name: str = "merge-gate",
    status: str = "approved",
    kind: str = "deterministic_gate",
) -> int:
    """schema.sql ``evidence_nodes`` (lines 457-503) for a gate node."""

    return await conn.fetchval(
        "INSERT INTO evidence_nodes "
        "(feature_id, idempotency_key, kind, content_hash, group_idx, "
        " name, status, deterministic) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id",
        feature_id,
        f"ev:{uuid.uuid4().hex}",
        kind,
        f"hash:{uuid.uuid4().hex[:16]}",
        _GROUP_IDX,
        name,
        status,
        True,
    )


async def _insert_workspace_snapshot(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    attempt_id: int,
) -> int:
    """schema.sql ``workspace_snapshots`` (lines 247-274)."""

    payload = {
        "role": "primary",
        "workspace_relative_path": "repo",
        "head_sha": "head-abc",
        "index_digest": "idx-1",
        "worktree_status_digest": "wt-1",
        "no_dirty": True,
        "safety_status": "ok",
        "dirty_paths": [],
        "forbidden_paths": [],
        "registry_digest": "reg-1",
    }
    return await conn.fetchval(
        "INSERT INTO workspace_snapshots "
        "(feature_id, idempotency_key, execution_journal_row_id, group_idx, "
        " repo_id, canonical_path, stage, snapshot_digest, payload, "
        " dag_sha256) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10) RETURNING id",
        feature_id,
        f"ws:{uuid.uuid4().hex}",
        attempt_id,
        _GROUP_IDX,
        "repo-1",
        "/canonical/repo-1",
        "implement",
        f"snap-{uuid.uuid4().hex[:16]}",
        json.dumps(payload),
        _DAG_SHA,
    )


async def _insert_sandbox_lease(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    attempt_id: int,
    status: str = "running",
    attempt_no: int = 1,
) -> int:
    """schema.sql ``sandbox_leases`` (lines 276-328)."""

    leased_until = datetime.now(timezone.utc) + timedelta(hours=1)
    return await conn.fetchval(
        "INSERT INTO sandbox_leases "
        "(feature_id, idempotency_key, execution_journal_row_id, dag_sha256, "
        " group_idx, attempt_no, mode, status, lease_owner, leased_until, "
        " lease_digest) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING id",
        feature_id,
        f"sb:{uuid.uuid4().hex}",
        attempt_id,
        _DAG_SHA,
        _GROUP_IDX,
        attempt_no,
        "task",
        status,
        "implementer",
        leased_until,
        f"lease-{uuid.uuid4().hex[:16]}",
    )


async def _insert_runtime_binding(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    sandbox_lease_id: int,
    attempt_id: int,
    runtime_name: str = "claude",
    status: str = "bound",
) -> int:
    """schema.sql ``runtime_workspace_bindings`` (lines 366-403)."""

    return await conn.fetchval(
        "INSERT INTO runtime_workspace_bindings "
        "(feature_id, idempotency_key, sandbox_lease_id, attempt_id, "
        " runtime_name, status, role_metadata_digest, binding_digest) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id",
        feature_id,
        f"rwb:{uuid.uuid4().hex}",
        sandbox_lease_id,
        attempt_id,
        runtime_name,
        status,
        f"role-{uuid.uuid4().hex[:8]}",
        f"bind-{uuid.uuid4().hex[:16]}",
    )


async def _insert_merge_item(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    pre_gate_id: int,
    status: str = "queued",
) -> int:
    """schema.sql ``merge_queue_items`` (lines 621-711).

    A ``queued`` row needs ``pre_queue_gate_evidence_id`` NOT NULL (the
    ``merge_queue_items_pre_queue_gate_check`` constraint at lines 664-666).
    """

    return await conn.fetchval(
        "INSERT INTO merge_queue_items "
        "(feature_id, dag_sha256, group_idx, base_commit, status, "
        " request_digest, idempotency_key, repo_id, "
        " pre_queue_gate_evidence_id) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id",
        feature_id,
        _DAG_SHA,
        _GROUP_IDX,
        "base-commit",
        status,
        "rd",
        f"merge:{feature_id}:{uuid.uuid4().hex}",
        "repo-1",
        pre_gate_id,
    )


async def _count_snapshot_changed_outbox(
    conn: asyncpg.Connection, feature_id: str
) -> int:
    """Total ``control_plane.snapshot_changed`` rows for ``feature_id``."""

    return await conn.fetchval(
        "SELECT count(*) FROM public_dashboard_outbox "
        "WHERE feature_id = $1 AND event_type = 'control_plane.snapshot_changed'",
        feature_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test stores backed by a real asyncpg.Connection.
#
# `_PoolBackedArtifactStore` exposes the same ``_pool``/``list_record_summaries``
# surface ``SupervisorEvidenceMcpService.get_current_snapshot`` requires. Using
# the live `mq_conn` as the `_pool` lets the Slice-10e
# ``load_typed_control_plane_snapshot`` call ``ExecutionControlStore(conn)``
# end-to-end against the real database.
# ─────────────────────────────────────────────────────────────────────────────


class _PoolBackedFeatureStore:
    """Minimal FeatureStore-like backed by an asyncpg connection."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._pool = conn  # the typed-snapshot fallback looks here

    async def get_feature(self, feature_id: str):
        row = await self._pool.fetchrow(
            "SELECT id, name, slug, workflow_name, workspace_id, phase, metadata "
            "FROM features WHERE id = $1",
            feature_id,
        )
        if row is None:
            return None
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata or "{}")
        return SimpleNamespace(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            workflow_name=row["workflow_name"],
            workspace_id=row["workspace_id"],
            phase=row["phase"],
            metadata=metadata or {},
        )

    async def list_event_summaries(
        self,
        feature_id: str,
        *,
        after_id: int = 0,
        limit: int = 50,
        order: str = "asc",
        group_idx: int | None = None,
        preview_chars: int = 700,
    ):
        del group_idx, preview_chars  # unused in this minimal stub
        return []


class _PoolBackedArtifactStore:
    """Minimal ArtifactStore-like backed by an asyncpg connection."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._pool = conn

    async def list_records(self, *, feature_id, prefixes, after_id, limit=500, order="asc"):
        del feature_id, prefixes, after_id, limit, order
        return []

    async def list_record_summaries(
        self, *, feature_id, prefixes, after_id, limit=500, order="asc"
    ):
        del feature_id, prefixes, after_id, limit, order
        return []


# ─────────────────────────────────────────────────────────────────────────────
# The dashboard pool wrapper — yields a real `asyncpg.Connection` on `acquire`.
# Pattern mirrors `tests/test_dashboard_bugflow.py:_typed_cp_pool`.
# ─────────────────────────────────────────────────────────────────────────────


def _dashboard_pool(conn: asyncpg.Connection):
    class _Acquire:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    return SimpleNamespace(acquire=lambda: _Acquire())


# ─────────────────────────────────────────────────────────────────────────────
# The atomic-feature end-to-end scenario.
# ─────────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seeded_feature(mq_conn: asyncpg.Connection):
    """Seed the feature row + a baseline attempt and yield artifacts.

    Returns a record with the seeded `attempt_id`, the lease helper, and the
    raw asyncpg connection. The fixture is per-test (truncates between tests
    via the directory conftest's `mq_dsn`).
    """

    await _insert_feature(mq_conn, _FEATURE_ID)
    return SimpleNamespace(conn=mq_conn, feature_id=_FEATURE_ID)


@pytest.mark.asyncio
async def test_doc10_atomic_feature_scenario(
    seeded_feature, monkeypatch: pytest.MonkeyPatch
):
    """One comprehensive seeded scenario exercising the full Slice-10 surface.

    Asserts each consumer (dashboard route, MCP snapshot path, supervisor
    typed-PRIMARY classifier, Slack dedupe store, public outbox projection)
    uses the same typed snapshot version and that exactly ONE
    ``control_plane.snapshot_changed`` outbox row is emitted per unique
    snapshot version (the structural P2-10g-1 fix locked end-to-end).
    """

    conn = seeded_feature.conn
    feature_id = seeded_feature.feature_id

    # ── (1) seed across all 8 cursor tables ─────────────────────────────────
    #
    # doc-10 logical -> physical mapping (see
    # `execution_control/store.py:1291-1300`):
    #   execution_attempts        -> execution_journal_rows
    #   workspace_snapshots       -> workspace_snapshots
    #   typed_failures            -> evidence_nodes (kind=runtime_failure_context)
    #   failure_route_budgets     -> evidence_nodes (same row)
    #   merge_queue_items         -> merge_queue_items
    #   evidence_nodes            -> evidence_nodes (gates / checkpoints)
    #   sandbox_leases            -> sandbox_leases
    #   runtime_workspace_bindings-> runtime_workspace_bindings

    attempt_succeeded = await _insert_attempt(
        conn, feature_id, entry_type="dispatch_attempt", status="succeeded"
    )
    attempt_failed = await _insert_attempt(
        conn, feature_id, entry_type="dispatch_attempt", status="failed"
    )
    typed_failure_id = await _insert_failure_evidence(
        conn,
        feature_id,
        attempt_id=attempt_failed,
        failure_class="worktree_alias",
        route="run_canonicalization_repair",
        signature="sig-worktree-1",
        budget_remaining=3,
        max_attempts=5,
    )
    pre_gate_id = await _insert_gate_evidence(
        conn, feature_id, name="merge-gate", status="approved"
    )
    merge_queued = await _insert_merge_item(
        conn, feature_id, pre_gate_id=pre_gate_id, status="queued"
    )
    integrated_gate = await _insert_gate_evidence(
        conn, feature_id, name="integrated-gate", status="approved"
    )
    merge_integrated = await _insert_merge_item(
        conn, feature_id, pre_gate_id=integrated_gate, status="queued"
    )
    workspace_id = await _insert_workspace_snapshot(
        conn, feature_id, attempt_id=attempt_succeeded
    )
    sandbox_id = await _insert_sandbox_lease(
        conn, feature_id, attempt_id=attempt_succeeded, status="running"
    )
    binding_id = await _insert_runtime_binding(
        conn, feature_id, sandbox_lease_id=sandbox_id, attempt_id=attempt_succeeded
    )

    # Sanity: every typed insert is real (covers the eight-table seed).
    assert all(
        isinstance(row_id, int) and row_id > 0
        for row_id in (
            attempt_succeeded,
            attempt_failed,
            typed_failure_id,
            merge_queued,
            merge_integrated,
            workspace_id,
            sandbox_id,
            binding_id,
        )
    )

    # ── (2) configure the outbox + the store, then project ─────────────────
    #
    # The Slice-10g-1 wiring at `execution_control/store.py:4933-4943` calls
    # `project_control_plane_snapshot_if_changed` AT THE TAIL of every
    # `_complete_missing_projections` invocation when the outbox is configured.
    # Here we drive the helper directly (the same code path) to exercise the
    # projection end-to-end without orchestrating a runtime invocation.
    # `outbox_enabled=True` is the explicit opt-in (production defaults OFF
    # behind `IRIAI_PUBLIC_DASHBOARD_OUTBOX`; see
    # `public_dashboard.py:97-126`).
    outbox = PublicDashboardOutbox(
        pool=conn, outbox_enabled=True, display_jobs_enabled=False
    )
    store = ExecutionControlStore(conn, public_dashboard_outbox=outbox)

    # The typed snapshot version digest before the first projection — it
    # already advanced past the empty baseline because we seeded eight typed
    # rows above.
    version_after_seed = await store.get_control_plane_snapshot_version(feature_id)
    assert version_after_seed != ""
    # 64-hex sha256 digest (doc 10 § "Proposed Interfaces/Types": "stable
    # digest over typed table cursors"; see
    # `workflows/develop/execution/snapshots.py:552-595`).
    assert len(version_after_seed) == 64

    # Drive the projection — produces exactly ONE outbox row for this version.
    event_id_v1 = await project_control_plane_snapshot_if_changed(
        outbox,
        store,
        feature_id,
        previous_snapshot_version=None,
        scope="dashboard",
    )
    assert event_id_v1 is not None
    # The event_id is the doc-10 idempotency key (see
    # `public_dashboard.py:656-669`).
    assert event_id_v1 == control_plane_snapshot_event_id(
        feature_id, version_after_seed
    )

    # Idempotent on a repeat call at the same version: `ON CONFLICT (event_id)
    # DO NOTHING` (see `public_dashboard.py:243-308`).
    event_id_repeat = await project_control_plane_snapshot_if_changed(
        outbox,
        store,
        feature_id,
        previous_snapshot_version=version_after_seed,
        scope="dashboard",
    )
    assert event_id_repeat is None  # version unchanged -> no enqueue
    assert await _count_snapshot_changed_outbox(conn, feature_id) == 1

    # Verify the row is doc-10 compliant: visibility="public", payload is
    # summary-only (no artifact bodies); see
    # `public_dashboard.py:583-653`.
    row = await conn.fetchrow(
        "SELECT event_id, event_type, schema_version, visibility, payload "
        "FROM public_dashboard_outbox WHERE feature_id = $1 "
        "AND event_type = 'control_plane.snapshot_changed'",
        feature_id,
    )
    assert row is not None
    assert row["event_type"] == "control_plane.snapshot_changed"
    assert row["visibility"] == "public"
    assert row["event_id"] == event_id_v1
    payload = json.loads(row["payload"])
    # Bounded counters (the typed lists are reduced to len()).
    counters = payload["counters"]
    assert counters["active_attempts"] >= 1
    assert counters["latest_failures"] >= 1
    assert counters["merge_queue"] >= 1
    assert counters["workspace_snapshots"] >= 1
    assert counters["sandbox_leases"] >= 1
    assert counters["runtime_bindings"] >= 1
    # NO artifact body / unbounded list crossed the public boundary.
    assert "active_attempts_list" not in payload
    assert "workspace_snapshots_list" not in payload
    assert payload["snapshot_version"] == version_after_seed
    # The bounded summary-only contract: cited evidence refs are bounded.
    assert isinstance(payload["evidence_refs"], list)
    assert len(payload["evidence_refs"]) <= 12  # CONTROL_PLANE_EVENT_MAX_EVIDENCE_REFS
    # Size cap defence-in-depth (see `public_dashboard.py:40-43`).
    assert len(json.dumps(payload)) <= 250_000  # PUBLIC_DASHBOARD_DEFAULT_MAX_PAYLOAD_BYTES ceiling

    # ── (3) dashboard route handlers ───────────────────────────────────────
    #
    # Drive both the dedicated `/control-plane` route and the embedded
    # `control_plane` object in `/api/feature/{id}` — both must serve the
    # SAME typed snapshot version and the embedded shape must be the bounded
    # typed `compact_typed_control_plane` (doc 10 step 6).

    original_pool = dashboard.pool
    dashboard.pool = _dashboard_pool(conn)
    dashboard._response_cache.clear()
    try:
        cp_resp = await dashboard.get_feature_control_plane(
            feature_id, SimpleNamespace(headers={})
        )
        cp_etag = cp_resp.headers["etag"]
        cp_payload = json.loads(cp_resp.body.decode("utf-8"))

        feature_resp = await dashboard.get_feature(
            feature_id, SimpleNamespace(headers={}, base_url="http://test/")
        )
        feature_etag = feature_resp.headers["etag"]
        feature_payload = json.loads(feature_resp.body.decode("utf-8"))
    finally:
        dashboard.pool = original_pool
        dashboard._response_cache.clear()

    # `/control-plane` returns the typed `ControlPlaneSnapshot` shape (see
    # `dashboard.py:1145-1205`).
    assert cp_payload["feature_id"] == feature_id
    assert cp_payload["source"] == "typed"
    assert cp_payload["snapshot_version"] == version_after_seed
    # The ETag is the typed snapshot version digest (`dashboard.py:1198`).
    assert cp_etag == f'"control-plane:{version_after_seed}"'
    # Bounded summary-only contract: typed-failure summaries have NO body
    # field (no "value"/"content"); only ids + bounded preview text.
    for failure in cp_payload["latest_failures"]:
        assert "value" not in failure
        assert "content" not in failure

    # `/api/feature/{id}` embeds a single compact `control_plane` object (doc
    # 10 step 6); the ETag folds the typed snapshot version (the route's
    # `_compose_etag` at `dashboard.py:473-476`).
    embed = feature_payload["control_plane"]
    assert embed["schema"] == "typed"
    assert embed["source"] == "typed"
    assert embed["snapshot_version"] == version_after_seed
    # The ETag composition is
    # `"<updated_at>:<max_art>:<max_evt>:<last_activity>:<typed_version>"` —
    # the typed_version segment is the last component (see
    # `dashboard.py:473-476`).
    assert version_after_seed in feature_etag
    # Bounded summary-only contract on the embed: no field exceeds the doc-10
    # size cap (the entire embedded object stays small even with seeded rows).
    assert len(json.dumps(embed)) < 50_000

    # ── (4) supervisor MCP — `evidence_mode == "typed"` + typed classifier ─
    #
    # `SupervisorEvidenceMcpService.get_current_snapshot` reads the bounded
    # typed snapshot via `load_typed_control_plane_snapshot` (doc 10 step 4 —
    # see `supervisor/mcp_server.py:245-263`); the typed-PRIMARY classifier
    # is then live.

    mcp_artifact_store = _PoolBackedArtifactStore(conn)
    mcp_feature_store = _PoolBackedFeatureStore(conn)
    service = SupervisorEvidenceMcpService(
        feature_store=mcp_feature_store,
        artifact_store=mcp_artifact_store,
        allowed_feature_id=feature_id,
    )
    mcp_snapshot = await service.get_current_snapshot(
        feature_id=feature_id, include_bridge=False
    )
    # `evidence_mode == "typed"` (NOT `legacy_fallback` / `mixed`) — see
    # `supervisor/evidence.py:753-790`.
    assert mcp_snapshot["evidence_mode"] == "typed"
    typed_cp = mcp_snapshot["control_plane_typed"]
    assert typed_cp is not None
    assert typed_cp["source"] == "typed"
    assert typed_cp["snapshot_version"] == version_after_seed
    # The typed `ControlPlaneSnapshot` is summary-only — no artifact bodies.
    assert "value" not in json.dumps(typed_cp)

    # The Slice-10c-2 typed-PRIMARY classifier classifies from typed rows —
    # the verdict carries `mapping_row` (a typed-mapping-table row id from
    # `supervisor/classifier_mapping.py:741-770`); a legacy fallback path
    # would set NO `mapping_row` fact.
    typed_snapshot_obj = await load_typed_control_plane_snapshot(
        mcp_artifact_store, feature_id
    )
    assert typed_snapshot_obj is not None
    assert evidence_mode_for_snapshot(typed_snapshot_obj) == "typed"

    observation = SupervisorObservation(
        feature_id=feature_id,
        control_plane=typed_snapshot_obj,
        evidence_mode="typed",
    )
    classifier = SupervisorClassifier()
    packet = classifier.classify(observation)
    # The typed mapping row carries the verdict — proves we came from the
    # Slice-10c-2 typed-PRIMARY path (NOT the legacy artifact classifiers).
    assert "mapping_row" in packet.facts, packet.facts
    # The typed `worktree_alias / run_canonicalization_repair` failure with
    # budget remaining maps to `deterministic_unblock` -> recommend (doc 10
    # § "Supervisor Classifier Mapping", row 4 / row 5).
    assert packet.facts["failure_class"] == "worktree_alias"
    assert packet.facts["route"] == "run_canonicalization_repair"
    assert packet.facts["budget_remaining"] >= 1
    # The snapshot_version on the verdict matches the typed digest we just
    # computed — proves a single consistent typed read.
    assert packet.facts["snapshot_version"] == version_after_seed

    # ── (5) supervisor digest dedupe — three-call sequence ─────────────────
    #
    # doc 10 § "Slack Dedupe And Suppression" decision logic (see
    # `supervisor/digest_dedupe.py:202-360`).

    dedupe_store = SupervisorDigestDedupeStore(pool=conn, feature_id=feature_id)
    digest_key = SupervisorDigestKey(
        feature_id=feature_id,
        group_idx=_GROUP_IDX,
        classification="deterministic_unblock",
        recommended_action="recommend",
        recommended_route="run_canonicalization_repair",
        failure_signature_hashes=["sig-worktree-1"],
        merge_queue_statuses=["queued"],
        active_attempt_ids=[attempt_succeeded],
    )

    # ── (5a) NEW key → should_send=True, reason="first_seen" ───────────────
    decision1 = await dedupe_store.decide(
        key=digest_key, snapshot_version=version_after_seed
    )
    assert decision1.should_send is True
    assert decision1.reason == "first_seen"
    state_id_1 = await dedupe_store.record_sent(
        decision=decision1,
        key=digest_key,
        snapshot_version=version_after_seed,
        slack_channel="C-test",
        slack_message_ts="100.0",
        citation_refs=[f"evidence_node:{typed_failure_id}"],
        payload={"summary": "first send"},
    )
    assert state_id_1 > 0

    # ── (5b) SAME key + SAME version → should_send=False (suppressed_duplicate) ─
    decision2 = await dedupe_store.decide(
        key=digest_key, snapshot_version=version_after_seed
    )
    assert decision2.should_send is False
    assert decision2.reason == "suppressed_duplicate"
    await dedupe_store.record_suppressed(
        decision=decision2,
        key=digest_key,
        snapshot_version=version_after_seed,
        slack_channel="C-test",
        citation_refs=[f"evidence_node:{typed_failure_id}"],
    )

    # ── (5c) SAME signature but NEW escalation route → never-suppress arm ──
    #
    # doc 10 § "Slack Dedupe And Suppression": "Never suppress ... first
    # ``stop/escalate`` for a new failure signature, or first
    # ``operator_required`` for a new typed route." The dedupe key changes
    # when classification/action/route change, so we build a NEW key for a
    # stop/escalate escalation; the never-suppress arm fires via the
    # ``new_failure_signature`` flag (see
    # ``supervisor/digest_dedupe.py:362-412``).
    escalation_key = digest_key.model_copy(
        update={
            "classification": "pipeline_bug_suspected",
            "recommended_action": "stop/escalate",
            "recommended_route": "quiesce",
        }
    )
    decision3 = await dedupe_store.decide(
        key=escalation_key,
        snapshot_version=version_after_seed,
        new_failure_signature=True,
    )
    assert decision3.should_send is True
    assert decision3.reason == "material_change"  # the never-suppress arm
    await dedupe_store.record_sent(
        decision=decision3,
        key=escalation_key,
        snapshot_version=version_after_seed,
        slack_channel="C-test",
        slack_message_ts="200.0",
    )

    # Audit history: exactly THREE rows (one per `decide`/`record_*` pair).
    audit_count = await conn.fetchval(
        "SELECT count(*) FROM supervisor_digest_audit WHERE feature_id = $1",
        feature_id,
    )
    assert audit_count == 3
    # The first key's audit has 2 entries (sent + suppressed).
    history_first = await dedupe_store.audit_history(
        dedupe_key=compute_dedupe_key(digest_key)
    )
    assert len(history_first) == 2
    # Audit is newest-first; the latest entry is the suppression.
    assert history_first[0]["should_send"] is False
    assert history_first[1]["should_send"] is True
    # The escalation key's audit row was attributed to a distinct state row.
    history_escalation = await dedupe_store.audit_history(
        dedupe_key=compute_dedupe_key(escalation_key)
    )
    assert len(history_escalation) == 1
    assert history_escalation[0]["should_send"] is True

    # Two distinct state rows (one per dedupe_key); same feature.
    state_count = await conn.fetchval(
        "SELECT count(*) FROM supervisor_digest_state WHERE feature_id = $1",
        feature_id,
    )
    assert state_count == 2

    # ── (6) state transition advances the snapshot version + one new outbox row ─
    #
    # Advance the typed cursor by adding ONE more typed row (a new failure)
    # and re-running the projection. The Slice-10g-1 wiring guarantees
    # exactly one new `control_plane.snapshot_changed` row per unique
    # snapshot_version (see the proof test
    # `tests/test_execution_control_store.py:
    # test_project_control_plane_snapshot_changed_is_idempotent_on_repeat`).

    await _insert_failure_evidence(
        conn,
        feature_id,
        attempt_id=attempt_failed,
        failure_class="acl_workability",
        route="run_workspace_repair",
        signature="sig-acl-1",
        budget_remaining=2,
        max_attempts=3,
    )
    version_after_step = await store.get_control_plane_snapshot_version(feature_id)
    assert version_after_step != version_after_seed  # cursor advanced

    event_id_v2 = await project_control_plane_snapshot_if_changed(
        outbox,
        store,
        feature_id,
        previous_snapshot_version=version_after_seed,
        scope="dashboard",
    )
    assert event_id_v2 is not None
    assert event_id_v2 != event_id_v1  # NEW idempotency key for NEW version
    assert event_id_v2 == control_plane_snapshot_event_id(
        feature_id, version_after_step
    )

    # Exactly TWO outbox rows now — one per unique snapshot version. NO event
    # was silently dropped (the structural P2-10g-1 fix locked end-to-end).
    assert await _count_snapshot_changed_outbox(conn, feature_id) == 2

    # The most-recent event reflects the most-recent typed snapshot.
    latest_row = await conn.fetchrow(
        "SELECT event_id, payload FROM public_dashboard_outbox "
        "WHERE feature_id = $1 AND event_type = 'control_plane.snapshot_changed' "
        "ORDER BY id DESC LIMIT 1",
        feature_id,
    )
    assert latest_row["event_id"] == event_id_v2
    latest_payload = json.loads(latest_row["payload"])
    assert latest_payload["snapshot_version"] == version_after_step
    # The typed counter advanced — TWO typed failure rows now.
    assert latest_payload["counters"]["latest_failures"] >= 2

    # ── (7) cross-consumer agreement: ALL surfaces see the SAME version ───
    #
    # doc 10 § "Acceptance Criteria": "Snapshot APIs are typed, bounded,
    # versioned, and shared by dashboard, MCP, and Slack digest generation."
    # After the state transition the dashboard route, the MCP path, and a
    # fresh classifier read all agree on `version_after_step`.

    original_pool = dashboard.pool
    dashboard.pool = _dashboard_pool(conn)
    dashboard._response_cache.clear()
    try:
        cp_resp_v2 = await dashboard.get_feature_control_plane(
            feature_id, SimpleNamespace(headers={})
        )
    finally:
        dashboard.pool = original_pool
        dashboard._response_cache.clear()
    cp_payload_v2 = json.loads(cp_resp_v2.body.decode("utf-8"))
    assert cp_payload_v2["snapshot_version"] == version_after_step

    mcp_snapshot_v2 = await service.get_current_snapshot(
        feature_id=feature_id, include_bridge=False
    )
    assert mcp_snapshot_v2["control_plane_typed"]["snapshot_version"] == version_after_step
    assert mcp_snapshot_v2["evidence_mode"] == "typed"

    # The unique-snapshot-versions count equals the outbox event count.
    distinct_versions = {version_after_seed, version_after_step}
    assert len(distinct_versions) == await _count_snapshot_changed_outbox(
        conn, feature_id
    )
