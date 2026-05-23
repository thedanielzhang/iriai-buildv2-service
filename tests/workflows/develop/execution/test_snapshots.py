"""Slice 10a tests — the typed ``ControlPlaneSnapshot`` contract + models.

These are pure-Python model tests (no Postgres): budget caps / clamping,
validator behaviour, and the deterministic snapshot-version digest. The
real-Postgres bounded-read tests for the store methods live in
``test_snapshots_store.py``.

Scope is strictly Slice 10a: the typed contract in
``workflows/develop/execution/snapshots.py`` (doc 10 § "Proposed
Interfaces/Types"). The dashboard route / supervisor classifier / Slack dedupe
are later Slice 10 sub-slices.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from iriai_build_v2.workflows.develop.execution.snapshots import (
    ControlPlaneSnapshot,
    ControlPlaneSnapshotQuery,
    EvidenceRef,
    ExecutionAttemptSummary,
    RetryBudgetSummary,
    SnapshotBudget,
    SnapshotCursor,
    SupervisorDigest,
    WorkspaceSnapshotSummary,
    control_plane_snapshot_version,
)

_NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)


# ── SnapshotBudget ──────────────────────────────────────────────────────────


def test_snapshot_budget_defaults_match_doc_10() -> None:
    budget = SnapshotBudget()
    assert budget.max_attempts == 20
    assert budget.max_failures == 40
    assert budget.max_merge_items == 40
    assert budget.max_retry_budgets == 40
    assert budget.max_gate_results == 40
    assert budget.max_workspace_snapshots == 20
    assert budget.max_evidence_refs == 80
    assert budget.max_event_summaries == 100
    assert budget.max_artifact_summaries == 200
    assert budget.max_artifact_detail_chars == 20_000
    assert budget.max_path_samples_per_snapshot == 10
    assert budget.max_response_bytes == 250_000
    assert budget.query_timeout_ms == 1_500


@pytest.mark.parametrize(
    "field",
    [
        "max_attempts",
        "max_failures",
        "max_merge_items",
        "max_gate_results",
        "max_workspace_snapshots",
        "max_evidence_refs",
        "max_path_samples_per_snapshot",
        "query_timeout_ms",
    ],
)
def test_snapshot_budget_rejects_non_positive(field: str) -> None:
    # doc 10 § "Tests": "ControlPlaneSnapshotQuery enforces budget caps and
    # rejects negative limits." A zero or negative cap defeats `LIMIT cap + 1`.
    with pytest.raises(ValidationError):
        SnapshotBudget(**{field: 0})
    with pytest.raises(ValidationError):
        SnapshotBudget(**{field: -3})


# ── ControlPlaneSnapshotQuery — budget clamping ─────────────────────────────


def test_query_clamps_over_large_budget_down_to_ceiling() -> None:
    # doc 10: budgets are MAXIMUM caps, not caller preferences raised through
    # query params. An over-large caller budget is clamped DOWN per-field.
    query = ControlPlaneSnapshotQuery(
        feature_id="feat-1",
        scope="dashboard",
        budget=SnapshotBudget(
            max_attempts=9_999,
            max_failures=9_999,
            max_response_bytes=10_000_000,
            query_timeout_ms=600_000,
        ),
    )
    assert query.budget.max_attempts == 20
    assert query.budget.max_failures == 40
    assert query.budget.max_response_bytes == 250_000
    assert query.budget.query_timeout_ms == 1_500


def test_query_allows_caller_to_shrink_a_cap() -> None:
    # A caller may request a tighter read (smaller panel) — clamp never raises.
    query = ControlPlaneSnapshotQuery(
        feature_id="feat-1",
        scope="supervisor",
        budget=SnapshotBudget(max_attempts=5),
    )
    assert query.budget.max_attempts == 5
    # untouched fields keep the ceiling default
    assert query.budget.max_failures == 40


def test_query_rejects_empty_feature_id() -> None:
    with pytest.raises(ValidationError):
        ControlPlaneSnapshotQuery(feature_id="   ", scope="dashboard")
    with pytest.raises(ValidationError):
        ControlPlaneSnapshotQuery(feature_id="", scope="mcp")


def test_query_rejects_unknown_scope() -> None:
    with pytest.raises(ValidationError):
        ControlPlaneSnapshotQuery(feature_id="feat-1", scope="not-a-scope")


# ── deterministic snapshot version digest ───────────────────────────────────


def _cursor(table: str, max_id: int, when: datetime | None) -> SnapshotCursor:
    return SnapshotCursor(table=table, max_id=max_id, max_updated_at=when)


def test_snapshot_version_is_deterministic_and_order_independent() -> None:
    c1 = _cursor("execution_attempts", 12, _NOW)
    c2 = _cursor("merge_queue_items", 3, None)
    c3 = _cursor("evidence_nodes", 90, _NOW)
    version_a = control_plane_snapshot_version([c1, c2, c3])
    version_b = control_plane_snapshot_version([c3, c1, c2])
    assert version_a == version_b
    assert len(version_a) == 64  # sha256 hex


def test_snapshot_version_changes_when_a_max_id_advances() -> None:
    base = [_cursor("execution_attempts", 12, _NOW), _cursor("evidence_nodes", 5, _NOW)]
    bumped = [_cursor("execution_attempts", 13, _NOW), _cursor("evidence_nodes", 5, _NOW)]
    assert control_plane_snapshot_version(base) != control_plane_snapshot_version(bumped)


def test_snapshot_version_changes_when_max_updated_at_advances() -> None:
    base = [_cursor("execution_attempts", 12, _NOW)]
    later = [_cursor("execution_attempts", 12, _NOW + timedelta(seconds=1))]
    assert control_plane_snapshot_version(base) != control_plane_snapshot_version(later)


def test_snapshot_version_budget_only_update_advances_via_separate_cursor() -> None:
    # doc 10: a budget-only / sandbox-only update must advance the version even
    # when the failure row does not change. Each logical table has its OWN
    # cursor, so bumping (e.g.) sandbox_leases moves the digest.
    failures_unchanged = _cursor("typed_failures", 7, _NOW)
    base = [failures_unchanged, _cursor("sandbox_leases", 1, _NOW)]
    sandbox_bumped = [failures_unchanged, _cursor("sandbox_leases", 2, _NOW)]
    assert control_plane_snapshot_version(base) != control_plane_snapshot_version(
        sandbox_bumped
    )


def test_snapshot_version_stable_across_repeated_calls() -> None:
    cursors = [_cursor("execution_attempts", 4, _NOW), _cursor("merge_queue_items", 9, None)]
    assert control_plane_snapshot_version(cursors) == control_plane_snapshot_version(
        list(cursors)
    )


# ── summary-model validators ────────────────────────────────────────────────


def test_workspace_summary_rejects_negative_path_counts() -> None:
    kwargs = dict(
        snapshot_id=1,
        attempt_id=None,
        group_idx=2,
        repo_id="repo",
        role="primary",
        canonical_path="/repo",
        workspace_relative_path="repo",
        stage="implement",
        head_sha="abc",
        index_digest="idx",
        worktree_status_digest="wt",
        no_dirty=True,
        safety_status="ok",
        dirty_path_sample=[],
        forbidden_path_sample=[],
        captured_at=_NOW,
    )
    with pytest.raises(ValidationError):
        WorkspaceSnapshotSummary(dirty_path_count=-1, forbidden_path_count=0, **kwargs)
    with pytest.raises(ValidationError):
        WorkspaceSnapshotSummary(dirty_path_count=0, forbidden_path_count=-2, **kwargs)


def test_retry_budget_summary_rejects_negative_counters() -> None:
    with pytest.raises(ValidationError):
        RetryBudgetSummary(
            scope="route",
            group_idx=None,
            route="retry_merge",
            failure_signature_hash=None,
            budget_total=-1,
            budget_used=0,
            budget_remaining=0,
        )


def test_supervisor_digest_confidence_must_be_in_unit_range() -> None:
    kwargs = dict(
        feature_id="feat-1",
        group_idx=1,
        snapshot_version="v1",
        classification="healthy_progress",
        recommended_action="observe",
        slack_dedupe_key="dk-1",
    )
    SupervisorDigest(confidence=0.0, **kwargs)
    SupervisorDigest(confidence=1.0, **kwargs)
    with pytest.raises(ValidationError):
        SupervisorDigest(confidence=1.5, **kwargs)
    with pytest.raises(ValidationError):
        SupervisorDigest(confidence=-0.1, **kwargs)


def test_attempt_summary_rejects_unknown_kind_and_status() -> None:
    kwargs = dict(
        attempt_id=1,
        feature_id="feat-1",
        dag_sha256="dag",
        group_idx=1,
        task_id="T1",
        stage="attempt_started",
        retry=0,
        actor="implementer",
        runtime="claude",
        input_digest="d",
        workspace_snapshot_id=None,
        started_at=_NOW,
        finished_at=None,
        updated_at=_NOW,
    )
    with pytest.raises(ValidationError):
        ExecutionAttemptSummary(attempt_kind="not-a-kind", status="started", **kwargs)
    with pytest.raises(ValidationError):
        ExecutionAttemptSummary(attempt_kind="task", status="not-a-status", **kwargs)


# ── ControlPlaneSnapshot invariants ─────────────────────────────────────────


def _minimal_snapshot(**overrides: object) -> ControlPlaneSnapshot:
    base = dict(
        feature_id="feat-1",
        snapshot_version="v1",
        generated_at=_NOW,
        source="typed",
        active_group_idx=None,
    )
    base.update(overrides)
    return ControlPlaneSnapshot(**base)


def test_snapshot_degraded_requires_reasons() -> None:
    # Fail-fast / no silent degradation: a degraded snapshot must say WHY.
    with pytest.raises(ValidationError):
        _minimal_snapshot(degraded=True, degradation_reasons=[])


def test_snapshot_degradation_reasons_force_degraded_true() -> None:
    snap = _minimal_snapshot(degradation_reasons=["typed_failures:TimeoutError"])
    assert snap.degraded is True


def test_snapshot_truncated_requires_omitted_counts() -> None:
    with pytest.raises(ValidationError):
        _minimal_snapshot(truncated=True, omitted_counts={})
    ok = _minimal_snapshot(truncated=True, omitted_counts={"active_attempts": 1})
    assert ok.truncated is True


def test_snapshot_serializes_to_stable_json_without_body_fields() -> None:
    # doc 10 § "Tests": typed snapshot serialization contains no `value`,
    # `content`, raw prompt, stdout/stderr, or full dirty-path body fields.
    snap = _minimal_snapshot(
        workspace_snapshots=[
            WorkspaceSnapshotSummary(
                snapshot_id=1,
                attempt_id=2,
                group_idx=3,
                repo_id="repo",
                role="primary",
                canonical_path="/repo",
                workspace_relative_path="repo",
                stage="implement",
                head_sha="abc",
                index_digest="idx",
                worktree_status_digest="wt",
                no_dirty=False,
                safety_status="ok",
                dirty_path_count=999,
                dirty_path_sample=["a.py", "b.py"],
                forbidden_path_count=0,
                forbidden_path_sample=[],
                captured_at=_NOW,
            )
        ],
        evidence_refs=[
            EvidenceRef(table="artifacts", id=5, citation="artifact:k id=5"),
        ],
    )
    dumped = snap.model_dump(mode="json")
    text = str(dumped)
    for forbidden in ("value", "content", "stdout", "stderr", "raw_prompt"):
        assert f"'{forbidden}'" not in text
    # the dirty-path COUNT is present; the field is a count, not the full list
    ws = dumped["workspace_snapshots"][0]
    assert ws["dirty_path_count"] == 999
    assert ws["dirty_path_sample"] == ["a.py", "b.py"]
    assert "dirty_paths" not in ws  # the full list is never a snapshot field
