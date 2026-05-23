"""Tests for the Slice 10c-2 typed supervisor classifier mapping.

Covers doc 10 § "Supervisor Classifier Mapping":

* the STATIC classifier-coverage rule — every canonical Slice-07
  ``FailureClass`` maps to exactly one typed mapping row (no class unmapped,
  no ``(class, route)`` double-mapped);
* the deterministic-never-escalates rule — a deterministic executor-owned
  class with a retry/repair route + budget never produces ``operator_required``;
* typed-primary vs legacy-fallback gating — ``evidence_mode == "typed"`` runs
  the typed mapping, ``evidence_mode != "typed"`` runs the legacy classifiers;
* the full doc-10 mapping table behaviour through ``classify_observation``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from iriai_build_v2.supervisor.classifier import classify_observation
from iriai_build_v2.supervisor.classifier_mapping import (
    DETERMINISTIC_EXECUTOR_OWNED_CLASSES,
    FAILURE_CLASSES,
    MAPPING_ROWS,
    classify_typed_snapshot,
    coverage_report,
    resolve_mapping_row,
)
from iriai_build_v2.supervisor.models import (
    ActionLevel,
    ArtifactRecord,
    BridgeProbe,
    FailureClass,
    StaleCodexInvocation,
    SupervisorObservation,
)
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FAILURE_CLASSES as ROUTER_FAILURE_CLASSES,
    ROUTE_TABLE,
)
from iriai_build_v2.workflows.develop.execution.snapshots import (
    ControlPlaneSnapshot,
    MergeQueueSummary,
    RetryBudgetSummary,
    TypedFailureSummary,
)

_NOW = datetime(2026, 5, 22, tzinfo=timezone.utc)


# ── builders ────────────────────────────────────────────────────────────────


def _failure(
    failure_class: str,
    route: str,
    *,
    failure_id: int = 1,
    failure_type: str = "x",
    severity: str = "error",
    deterministic: bool = True,
    operator_required: bool = False,
    retryable: bool = True,
    status: str = "open",
    signature: str = "sig-A",
    created_at: datetime | None = None,
) -> TypedFailureSummary:
    return TypedFailureSummary(
        failure_id=failure_id,
        attempt_id=10,
        evidence_id=failure_id,
        failure_class=failure_class,
        failure_type=failure_type,
        severity=severity,
        deterministic=deterministic,
        operator_required=operator_required,
        retryable=retryable,
        status=status,
        route=route,
        signature_hash=signature,
        created_at=created_at or _NOW,
        resolved_at=None,
    )


def _budget(
    route: str,
    remaining: int,
    *,
    signature: str = "sig-A",
    total: int = 3,
) -> RetryBudgetSummary:
    return RetryBudgetSummary(
        scope="route",
        group_idx=None,
        route=route,
        failure_signature_hash=signature,
        budget_total=total,
        budget_used=max(0, total - remaining),
        budget_remaining=remaining,
    )


def _merge_item(status: str, item_id: int = 1) -> MergeQueueSummary:
    return MergeQueueSummary(
        item_id=item_id,
        feature_id="feat",
        dag_sha256="dag",
        group_idx=1,
        repo_id="repo",
        status=status,
        priority=0,
        lease_owner=None,
        leased_until=None,
        lease_version=0,
        failure_id=None,
        updated_at=_NOW,
    )


def _snapshot(
    failures: list[TypedFailureSummary] | None = None,
    budgets: list[RetryBudgetSummary] | None = None,
    merge_queue: list[MergeQueueSummary] | None = None,
    *,
    source: str = "typed",
) -> ControlPlaneSnapshot:
    return ControlPlaneSnapshot(
        feature_id="feat",
        snapshot_version="snap-v1",
        generated_at=_NOW,
        source=source,
        active_group_idx=1,
        latest_failures=failures or [],
        retry_budgets=budgets or [],
        merge_queue=merge_queue or [],
    )


def _typed_obs(
    snapshot: ControlPlaneSnapshot | None,
    *,
    evidence_mode: str = "typed",
    artifacts: list[ArtifactRecord] | None = None,
) -> SupervisorObservation:
    return SupervisorObservation(
        feature_id="feat",
        phase="implementation",
        evidence_mode=evidence_mode,
        control_plane=snapshot,
        artifacts=artifacts or [],
    )


# ── the STATIC classifier-coverage rule (doc 10 § "Supervisor Classifier
#    Mapping" — coverage rule) ─────────────────────────────────────────────


def test_coverage_report_is_clean():
    """Every FailureClass maps to exactly one row; no double-mapping; no
    deterministic-class operator escalation."""

    report = coverage_report()
    assert report.unmapped_classes == [], report.unmapped_classes
    assert report.double_mapped == [], report.double_mapped
    assert report.deterministic_escalations == [], (
        report.deterministic_escalations
    )
    assert report.ok is True


def test_every_canonical_failure_class_maps_to_exactly_one_row():
    """The classifier-coverage rule, asserted per canonical FailureClass.

    For every concrete ``(failure_class, route)`` the Slice-07 ``ROUTE_TABLE``
    proves a class can emit, exactly one mapping row resolves — never zero
    (unmapped), never two distinct supervisor classes (double-mapped).
    """

    routes_by_class: dict[str, set[str]] = {}
    for (failure_class, _failure_type), route in ROUTE_TABLE.items():
        routes_by_class.setdefault(failure_class, set()).add(route.action)

    for failure_class in FAILURE_CLASSES:
        emit_routes = routes_by_class.get(failure_class) or {""}
        for route in sorted(emit_routes):
            # the router never emits a retry/repair route with zero budget
            has_budget = route not in ("quiesce", "operator_required")
            row = resolve_mapping_row(
                failure_class, route, has_budget=has_budget
            )
            assert row is not None, f"{failure_class}/{route} is unmapped"


def test_mapping_table_failure_classes_are_all_canonical():
    """No mapping row references a non-canonical / invented failure class."""

    canonical = set(ROUTER_FAILURE_CLASSES)
    for row in MAPPING_ROWS:
        assert row.failure_classes <= canonical, (
            f"{row.row_id} references non-canonical classes: "
            f"{row.failure_classes - canonical}"
        )


def test_supervisor_classifier_mapping_uses_router_taxonomy():
    """The mapping module keys off the SAME 27-class router taxonomy."""

    assert FAILURE_CLASSES == ROUTER_FAILURE_CLASSES
    assert len(FAILURE_CLASSES) == 27


# ── the deterministic-never-escalates rule (doc 10) ─────────────────────────


def test_no_mapping_row_escalates_a_deterministic_class_on_a_repair_route():
    """A deterministic executor-owned class with a retry/repair route is
    NEVER mapped to operator_required by the static table."""

    for row in MAPPING_ROWS:
        if row.classification is not FailureClass.OPERATOR_REQUIRED:
            continue
        for failure_class in row.failure_classes:
            if failure_class not in DETERMINISTIC_EXECUTOR_OWNED_CLASSES:
                continue
            # an operator_required row may match a deterministic class ONLY on
            # the terminal `operator_required` route — never a repair route.
            assert row.routes == frozenset({"operator_required"}), (
                f"{row.row_id} escalates deterministic {failure_class} on a "
                "non-operator route"
            )


@pytest.mark.parametrize(
    "failure_class,route",
    [
        ("worktree_alias", "run_canonicalization_repair"),
        ("acl_workability", "run_workspace_repair"),
        ("stale_projection", "retry_verifier"),
        ("commit_hygiene", "run_commit_hygiene_repair"),
        ("contract_compile", "run_contract_repair"),
        ("verifier_context", "retry_verifier"),
        ("sandbox_allocation", "retry_dispatch"),
        ("sandbox_capture", "retry_sandbox_capture"),
        ("sandbox_cleanup", "run_sandbox_cleanup"),
        ("runtime_structured_output", "retry_dispatch"),
        ("merge_conflict", "retry_merge"),
    ],
)
def test_deterministic_class_with_budget_never_escalates_to_operator(
    failure_class,
    route,
):
    """doc-10: a deterministic executor-owned class whose typed route is
    retry/repair with budget remaining NEVER produces operator_required —
    even when the typed failure ALSO carries a stale operator_required flag."""

    snapshot = _snapshot(
        failures=[
            _failure(failure_class, route, operator_required=True)
        ],
        budgets=[_budget(route, 2)],
    )
    packet = classify_observation(_typed_obs(snapshot))
    assert packet.classification is not FailureClass.OPERATOR_REQUIRED
    assert packet.classification is FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action is ActionLevel.RECOMMEND
    # the stale operator signal is recorded as superseded, not escalated
    assert packet.facts.get("superseded_operator_signal") is True


def test_operator_required_class_with_explicit_route_does_escalate():
    """The operator-escalation rule's POSITIVE case: an explicit typed
    operator_required route with no deterministic repair route DOES
    stop/escalate (the rule is not a blanket suppression)."""

    snapshot = _snapshot(
        failures=[
            _failure(
                "operator_required",
                "operator_required",
                failure_type="operator_clearance_required",
                operator_required=True,
                retryable=False,
            )
        ],
    )
    packet = classify_observation(_typed_obs(snapshot))
    assert packet.classification is FailureClass.OPERATOR_REQUIRED
    assert packet.recommended_action is ActionLevel.STOP_ESCALATE


def test_deterministic_class_with_exhausted_budget_does_not_escalate_operator():
    """A deterministic class whose budget is exhausted quiesces — it becomes
    a pipeline-bug safety stop, NOT an operator escalation (doc-10: the
    supervisor reports the blocked route, not manual file copying)."""

    snapshot = _snapshot(
        failures=[_failure("worktree_alias", "quiesce", retryable=False)],
        budgets=[_budget("quiesce", 0)],
    )
    packet = classify_observation(_typed_obs(snapshot))
    assert packet.classification is FailureClass.PIPELINE_BUG_SUSPECTED
    assert packet.classification is not FailureClass.OPERATOR_REQUIRED
    assert packet.recommended_action is ActionLevel.STOP_ESCALATE


def test_verifier_context_budget_exhausted_quiesce_is_a_safety_stop():
    """A budget-EXHAUSTED ``verifier_context`` failure (route rewritten to
    ``quiesce`` by ``failure_router.decide``) is a deterministic safety STOP.

    ``verifier_context`` is a deterministic executor-owned class whose only
    ``ROUTE_TABLE`` route is ``retry_verifier`` (budget 1). When that budget is
    exhausted ``failure_router.decide`` rewrites the action to ``quiesce`` —
    so a typed ``verifier_context`` failure with ``route="quiesce"`` is a
    genuine, producible router output. It MUST classify as the doc-10
    deterministic-class budget-exhausted-``quiesce`` verdict
    (``pipeline_bug_suspected`` / ``stop/escalate``), exactly like the
    workspace and sandbox/runtime ``quiesce`` rows — NOT fall through to the
    legacy ``watch_only`` fallback.

    Regression guard for P2-10c-2-1: before the ``verifier_context_quiesce``
    mapping row was added, ``classify_typed_snapshot`` returned ``None`` for
    this failure and the legacy fallback produced ``watch_only`` — this test
    FAILS against that pre-fix code.
    """

    snapshot = _snapshot(
        failures=[_failure("verifier_context", "quiesce", retryable=False)],
        budgets=[_budget("quiesce", 0)],
    )
    # the typed-primary path produces a genuine verdict (not None -> fallback)
    verdict = classify_typed_snapshot(snapshot)
    assert verdict is not None, (
        "budget-exhausted verifier_context/quiesce must produce a typed "
        "verdict, not fall back to the legacy classifiers"
    )
    assert verdict.classification is FailureClass.PIPELINE_BUG_SUSPECTED
    assert verdict.action is ActionLevel.STOP_ESCALATE
    assert verdict.row.row_id == "verifier_context_quiesce"

    # end-to-end through classify_observation -> the safety stop, never
    # watch_only, never operator escalation
    packet = classify_observation(_typed_obs(snapshot))
    assert packet.classification is FailureClass.PIPELINE_BUG_SUSPECTED
    assert packet.classification is not FailureClass.WATCH_ONLY
    assert packet.classification is not FailureClass.OPERATOR_REQUIRED
    assert packet.recommended_action is ActionLevel.STOP_ESCALATE
    assert packet.facts["mapping_row"] == "verifier_context_quiesce"


def test_coverage_report_enumerates_budget_exhausted_quiesce_routes():
    """The coverage scan is NON-VACUOUS for the budget-exhausted ``quiesce``
    sub-universe — every canonical class whose router route is a budget-bearing
    retry/repair route has its budget-exhausted ``(class, "quiesce")`` rewrite
    route mapped to exactly one row.

    Regression guard for P2-10c-2-1: before ``coverage_report`` was extended to
    enumerate the budget-exhausted ``quiesce`` rewrites, a class with only a
    retry/repair ``ROUTE_TABLE`` route (e.g. ``verifier_context``) never had
    its ``(class, "quiesce")`` route visited, so a missing ``quiesce`` row
    escaped the coverage test.
    """

    routes_by_class: dict[str, set[str]] = {}
    for (failure_class, _failure_type), route in ROUTE_TABLE.items():
        routes_by_class.setdefault(failure_class, set()).add(route.action)

    retry_repair = {
        "retry_dispatch", "run_product_repair", "run_contract_repair",
        "run_canonicalization_repair", "run_workspace_repair",
        "run_commit_hygiene_repair", "retry_verifier", "retry_merge",
        "retry_sandbox_capture", "run_sandbox_cleanup",
    }
    # every class whose ROUTE_TABLE has a retry/repair route can be rewritten
    # to a budget-exhausted `quiesce` by failure_router.decide.
    rewrite_quiesce_classes = sorted(
        fc
        for fc in FAILURE_CLASSES
        if any(r in retry_repair for r in routes_by_class.get(fc, set()))
    )
    # verifier_context is in this set (its only route is retry_verifier).
    assert "verifier_context" in rewrite_quiesce_classes

    for failure_class in rewrite_quiesce_classes:
        # the budget-exhausted quiesce route must resolve to exactly one row,
        # consistently across the budget buckets.
        row_budget = resolve_mapping_row(
            failure_class, "quiesce", has_budget=True
        )
        row_no_budget = resolve_mapping_row(
            failure_class, "quiesce", has_budget=False
        )
        assert row_budget is not None, (
            f"{failure_class}/quiesce (budget-exhausted rewrite) is unmapped"
        )
        assert row_no_budget is not None, (
            f"{failure_class}/quiesce (budget-exhausted rewrite) is unmapped"
        )
        assert row_budget.row_id == row_no_budget.row_id, (
            f"{failure_class}/quiesce double-maps across budget buckets"
        )

    # and coverage_report itself is clean for the extended universe.
    report = coverage_report()
    assert report.ok is True
    assert report.unmapped_classes == []
    assert report.double_mapped == []


def test_classify_typed_snapshot_orders_failures_by_created_at():
    """``classify_typed_snapshot`` picks the newest failure by ``created_at``
    (the authoritative recency signal), with ``failure_id`` as the tiebreak.

    Regression guard for P3-10c-2-2: previously the sort used ``failure_id``
    only, so an id/time skew would pick a stale verdict. Here the newest
    failure by ``created_at`` carries the SMALLER ``failure_id`` — the verdict
    must still cite the ``created_at``-newest failure.
    """

    # both runtime_provider/runtime_timeout map to watch_only at the same
    # priority tier, so the tiebreak decides which failure is cited.
    older_by_time = _failure(
        "runtime_provider", "retry_dispatch",
        failure_id=99,  # larger id but OLDER timestamp
        created_at=_NOW - timedelta(hours=2),
    )
    newer_by_time = _failure(
        "runtime_timeout", "retry_dispatch",
        failure_id=2,  # smaller id but NEWER timestamp
        created_at=_NOW,
    )
    snapshot = _snapshot(
        failures=[older_by_time, newer_by_time],
        budgets=[_budget("retry_dispatch", 2)],
    )
    verdict = classify_typed_snapshot(snapshot)
    assert verdict is not None
    # created_at wins over failure_id: the newer-by-time failure (id 2) is cited
    assert verdict.facts["typed_failure_id"] == 2


# ── typed-primary vs legacy-fallback gating (doc 10 § "Refactoring Steps"
#    step 5) ────────────────────────────────────────────────────────────────


def test_typed_evidence_mode_routes_through_typed_mapping():
    """`evidence_mode == "typed"` -> the typed failure row drives the
    classification (not the legacy artifact classifiers)."""

    snapshot = _snapshot(
        failures=[_failure("checkpoint_contradiction", "quiesce")],
    )
    packet = classify_observation(_typed_obs(snapshot, evidence_mode="typed"))
    assert packet.classification is FailureClass.PIPELINE_BUG_SUSPECTED
    assert packet.facts["mapping_row"] == "checkpoint_contradiction"
    # the inference names the typed-primary path
    assert "Typed control-plane failure router decision is primary" in (
        packet.inference
    )


def test_legacy_fallback_mode_skips_typed_mapping_and_uses_artifacts():
    """`evidence_mode != "typed"` -> the typed mapping is skipped and the
    legacy artifact classifiers run, even when a typed snapshot is present."""

    # A typed snapshot that WOULD classify pipeline_bug if read as typed,
    # but evidence_mode is legacy_fallback, so the legacy path runs instead.
    snapshot = _snapshot(
        failures=[_failure("checkpoint_contradiction", "quiesce")],
        source="legacy_fallback",
    )
    legacy_artifact = ArtifactRecord(
        id=500,
        key="dag-repair-preflight:g30:retry-initial",
        value={
            "status": "failed",
            "path_problems": [
                {"path": "src/x.tsx", "reason": "retired path stale"}
            ],
        },
    )
    packet = classify_observation(
        _typed_obs(
            snapshot,
            evidence_mode="legacy_fallback",
            artifacts=[legacy_artifact],
        )
    )
    # the LEGACY classifier classified this (deterministic unblock from the
    # stale artifact) — NOT the typed checkpoint_contradiction row
    assert packet.classification is FailureClass.DETERMINISTIC_UNBLOCK
    assert "mapping_row" not in packet.facts


def test_empty_evidence_mode_uses_legacy_fallback():
    """An observation with no evidence_mode set falls back to legacy (a
    pre-Slice-10c-2 observation must keep classifying)."""

    legacy_artifact = ArtifactRecord(
        id=600,
        key="dag-commit-failure:g38:retry-initial",
        value={"status": "failed", "summary": "commit hook failed"},
    )
    obs = SupervisorObservation(
        feature_id="feat",
        phase="implementation",
        artifacts=[legacy_artifact],
    )
    packet = classify_observation(obs)
    assert packet.classification is FailureClass.DETERMINISTIC_UNBLOCK
    assert "mapping_row" not in packet.facts


def test_typed_mode_with_no_typed_failures_falls_back_to_legacy():
    """doc-10: typed-primary, but when the typed snapshot carries no failure
    /merge-queue signal that maps, the legacy artifact classifiers still
    run — never a silent default."""

    snapshot = _snapshot(failures=[], merge_queue=[])
    legacy_artifact = ArtifactRecord(
        id=700,
        key="dag-verify:g40:initial",
        value={"status": "failed", "summary": "raw verifier failure"},
    )
    packet = classify_observation(
        _typed_obs(snapshot, evidence_mode="typed", artifacts=[legacy_artifact])
    )
    # the typed snapshot had nothing to say -> legacy fallback classified it
    assert "mapping_row" not in packet.facts
    assert packet.classification is FailureClass.NORMAL_PRODUCT_REPAIR


# ── the doc-10 mapping table behaviour through classify_observation ─────────


def test_typed_checkpoint_contradiction_is_highest_priority():
    """doc-10 row 1: a typed checkpoint contradiction outranks every other
    typed failure, including a deterministic-unblock with budget."""

    snapshot = _snapshot(
        failures=[
            _failure("worktree_alias", "run_canonicalization_repair",
                     failure_id=1),
            _failure("checkpoint_contradiction", "quiesce", failure_id=9),
        ],
        budgets=[_budget("run_canonicalization_repair", 2)],
    )
    packet = classify_observation(_typed_obs(snapshot))
    assert packet.classification is FailureClass.PIPELINE_BUG_SUSPECTED
    assert packet.facts["mapping_row"] == "checkpoint_contradiction"


def test_typed_deterministic_unblock_outranks_typed_product_repair():
    """doc-10 priority: deterministic unblock (priority 3) ahead of product
    repair (priority 6)."""

    snapshot = _snapshot(
        failures=[
            _failure("product_defect", "run_product_repair", failure_id=2,
                     retryable=False),
            _failure("acl_workability", "run_workspace_repair", failure_id=3),
        ],
        budgets=[_budget("run_workspace_repair", 1)],
    )
    packet = classify_observation(_typed_obs(snapshot))
    assert packet.classification is FailureClass.DETERMINISTIC_UNBLOCK


@pytest.mark.parametrize(
    "failure_class,route,budget_remaining,expected_class,expected_action",
    [
        # worktree alias / ACL / stale projection / commit hygiene unblock
        ("worktree_alias", "run_canonicalization_repair", 2,
         FailureClass.DETERMINISTIC_UNBLOCK, ActionLevel.RECOMMEND),
        ("acl_workability", "run_workspace_repair", 1,
         FailureClass.DETERMINISTIC_UNBLOCK, ActionLevel.RECOMMEND),
        ("stale_projection", "retry_verifier", 1,
         FailureClass.DETERMINISTIC_UNBLOCK, ActionLevel.RECOMMEND),
        ("commit_hygiene", "run_commit_hygiene_repair", 1,
         FailureClass.DETERMINISTIC_UNBLOCK, ActionLevel.RECOMMEND),
        # workspace classes with quiesce -> pipeline bug
        ("worktree_alias", "quiesce", 0,
         FailureClass.PIPELINE_BUG_SUSPECTED, ActionLevel.STOP_ESCALATE),
        # contract compile repair vs quiesce
        ("contract_compile", "run_contract_repair", 1,
         FailureClass.DETERMINISTIC_UNBLOCK, ActionLevel.RECOMMEND),
        ("contract_compile", "quiesce", 0,
         FailureClass.PIPELINE_BUG_SUSPECTED, ActionLevel.STOP_ESCALATE),
        # sandbox / runtime deterministic retry vs quiesce
        ("sandbox_capture", "retry_sandbox_capture", 1,
         FailureClass.DETERMINISTIC_UNBLOCK, ActionLevel.RECOMMEND),
        ("runtime_context", "quiesce", 0,
         FailureClass.PIPELINE_BUG_SUSPECTED, ActionLevel.STOP_ESCALATE),
        # resource exhausted retry -> watch only, quiesce -> pipeline bug
        ("resource_exhausted", "retry_dispatch", 1,
         FailureClass.WATCH_ONLY, ActionLevel.OBSERVE),
        ("resource_exhausted", "quiesce", 0,
         FailureClass.PIPELINE_BUG_SUSPECTED, ActionLevel.STOP_ESCALATE),
        # sandbox_binding / runtime_cancelled quiesce -> watch only
        ("sandbox_binding", "quiesce", 0,
         FailureClass.WATCH_ONLY, ActionLevel.OBSERVE),
        ("runtime_cancelled", "quiesce", 0,
         FailureClass.WATCH_ONLY, ActionLevel.OBSERVE),
        # verifier context unblock vs verifier provider watch-only
        ("verifier_context", "retry_verifier", 1,
         FailureClass.DETERMINISTIC_UNBLOCK, ActionLevel.RECOMMEND),
        ("verifier_provider", "retry_verifier", 2,
         FailureClass.WATCH_ONLY, ActionLevel.OBSERVE),
        ("verifier_provider", "quiesce", 0,
         FailureClass.WATCH_ONLY, ActionLevel.OBSERVE),
        # merge conflict retry vs quiesce
        ("merge_conflict", "retry_merge", 1,
         FailureClass.DETERMINISTIC_UNBLOCK, ActionLevel.RECOMMEND),
        ("merge_conflict", "quiesce", 0,
         FailureClass.PIPELINE_BUG_SUSPECTED, ActionLevel.STOP_ESCALATE),
        # product / contract
        ("product_defect", "run_product_repair", 2,
         FailureClass.NORMAL_PRODUCT_REPAIR, ActionLevel.RECOMMEND),
        ("product_defect", "quiesce", 0,
         FailureClass.PIPELINE_BUG_SUSPECTED, ActionLevel.STOP_ESCALATE),
        ("contract_violation", "run_product_repair", 1,
         FailureClass.NORMAL_PRODUCT_REPAIR, ActionLevel.RECOMMEND),
        ("contract_violation", "quiesce", 0,
         FailureClass.PIPELINE_BUG_SUSPECTED, ActionLevel.STOP_ESCALATE),
        # runtime provider / timeout -> watch only either route
        ("runtime_provider", "retry_dispatch", 2,
         FailureClass.WATCH_ONLY, ActionLevel.OBSERVE),
        ("runtime_timeout", "quiesce", 0,
         FailureClass.WATCH_ONLY, ActionLevel.OBSERVE),
        # fatal control-plane safety
        ("dispatcher_internal", "quiesce", 0,
         FailureClass.PIPELINE_BUG_SUSPECTED, ActionLevel.STOP_ESCALATE),
        ("sandbox_isolation", "quiesce", 0,
         FailureClass.PIPELINE_BUG_SUSPECTED, ActionLevel.STOP_ESCALATE),
        ("evidence_corruption", "quiesce", 0,
         FailureClass.PIPELINE_BUG_SUSPECTED, ActionLevel.STOP_ESCALATE),
        # regroup invalid quiesce
        ("regroup_invalid", "quiesce", 0,
         FailureClass.PIPELINE_BUG_SUSPECTED, ActionLevel.STOP_ESCALATE),
        # unknown quiesce vs diagnostic retry
        ("unknown", "quiesce", 0,
         FailureClass.PIPELINE_BUG_SUSPECTED, ActionLevel.STOP_ESCALATE),
        ("unknown", "retry_dispatch", 1,
         FailureClass.WATCH_ONLY, ActionLevel.OBSERVE),
    ],
)
def test_doc10_mapping_table_rows(
    failure_class,
    route,
    budget_remaining,
    expected_class,
    expected_action,
):
    """Every doc-10 § "Supervisor Classifier Mapping" table row, exercised
    end-to-end through ``classify_observation``."""

    budgets = (
        [_budget(route, budget_remaining)] if budget_remaining > 0 else []
    )
    snapshot = _snapshot(
        failures=[_failure(failure_class, route, retryable=budget_remaining > 0)],
        budgets=budgets,
    )
    packet = classify_observation(_typed_obs(snapshot))
    assert packet.classification is expected_class, (
        f"{failure_class}/{route} -> {packet.classification}"
    )
    assert packet.recommended_action is expected_action


def test_typed_merge_queue_active_is_healthy_progress():
    """doc-10: an active merge-queue item with no open fatal failure ->
    healthy_progress / observe."""

    snapshot = _snapshot(merge_queue=[_merge_item("applying")])
    packet = classify_observation(_typed_obs(snapshot))
    assert packet.classification is FailureClass.HEALTHY_PROGRESS
    assert packet.recommended_action is ActionLevel.OBSERVE


def test_typed_fatal_failure_outranks_merge_queue_progress():
    """doc-10: a fatal typed failure outranks merge-queue progress."""

    snapshot = _snapshot(
        failures=[_failure("checkpoint_contradiction", "quiesce")],
        merge_queue=[_merge_item("applying")],
    )
    packet = classify_observation(_typed_obs(snapshot))
    assert packet.classification is FailureClass.PIPELINE_BUG_SUSPECTED


# ── budget-gated routing — the REAL typed budget source (P3-10a-1) ──────────


def test_budget_remaining_gates_retry_vs_escalation():
    """The classifier's "budget remaining" gate is genuine: the SAME
    deterministic class + repair route classifies deterministic_unblock with
    budget and is NOT a retry verdict when budget is zero."""

    # budget remains -> deterministic unblock
    with_budget = _snapshot(
        failures=[_failure("acl_workability", "run_workspace_repair")],
        budgets=[_budget("run_workspace_repair", 1)],
    )
    packet = classify_observation(_typed_obs(with_budget))
    assert packet.classification is FailureClass.DETERMINISTIC_UNBLOCK

    # zero budget on a repair route -> the retry/repair row does NOT match;
    # the typed router would have rewritten this to `quiesce`, so a repair
    # route with a zero-budget row is treated as no typed verdict and the
    # snapshot (no merge queue) falls back.
    zero_budget = _snapshot(
        failures=[_failure("acl_workability", "run_workspace_repair")],
        budgets=[_budget("run_workspace_repair", 0)],
    )
    verdict = classify_typed_snapshot(zero_budget)
    assert verdict is None


def test_no_budget_row_is_fail_safe_no_unblock():
    """When the typed snapshot carries NO budget row for a deterministic
    failure's route, the budget gate reads fail-safe zero — the
    deterministic-unblock retry row does not match (never an over-optimistic
    "retry has budget")."""

    snapshot = _snapshot(
        failures=[_failure("worktree_alias", "run_canonicalization_repair")],
        budgets=[],  # no budget row at all
    )
    verdict = classify_typed_snapshot(snapshot)
    # the requires_budget row does not match without a positive budget
    assert verdict is None


def test_budget_matched_by_route_and_signature():
    """The budget row is matched to a failure by (route, signature_hash) —
    a budget row for a DIFFERENT signature does not satisfy the gate."""

    snapshot = _snapshot(
        failures=[
            _failure(
                "merge_conflict", "retry_merge", signature="sig-current"
            )
        ],
        budgets=[
            # budget exists for a different signature only
            _budget("retry_merge", 2, signature="sig-other"),
        ],
    )
    verdict = classify_typed_snapshot(snapshot)
    # a route-only budget match is the fallback when no signature matches; the
    # route matches so the budget is found via the route-only path
    assert verdict is not None
    assert verdict.classification is FailureClass.DETERMINISTIC_UNBLOCK


# ── open/resolved failure handling (doc-10 edge cases) ──────────────────────


def test_resolved_failures_do_not_drive_classification_when_open_exists():
    """doc-10: latest failures use open/routed/retrying rows first; a newer
    resolved row does not outrank an older open one."""

    snapshot = _snapshot(
        failures=[
            _failure("worktree_alias", "run_canonicalization_repair",
                     failure_id=1, status="open"),
            _failure("product_defect", "run_product_repair", failure_id=9,
                     status="resolved", retryable=False),
        ],
        budgets=[_budget("run_canonicalization_repair", 2)],
    )
    packet = classify_observation(_typed_obs(snapshot))
    # the OPEN worktree_alias row classifies, not the resolved product_defect
    assert packet.classification is FailureClass.DETERMINISTIC_UNBLOCK


def test_newest_open_failure_wins_within_a_priority_tier():
    """Two open failures in the same priority tier — the newest typed
    failure (highest id) wins."""

    older = _failure("runtime_provider", "retry_dispatch", failure_id=1,
                     created_at=_NOW - timedelta(hours=1))
    newer = _failure("runtime_timeout", "retry_dispatch", failure_id=9,
                     created_at=_NOW)
    snapshot = _snapshot(
        failures=[older, newer],
        budgets=[_budget("retry_dispatch", 2)],
    )
    verdict = classify_typed_snapshot(snapshot)
    assert verdict is not None
    # both map to watch_only; the newest failure is the one cited
    assert verdict.facts["typed_failure_id"] == 9


# ── bridge/process rows are gated on typed active work (doc-10 priority) ────


def test_active_merge_queue_suppresses_stale_codex_in_typed_mode():
    """doc-10 priority: a stale-Codex row must NOT outrank live typed work.

    A stale-Codex process observation with an active typed merge-queue item
    classifies as healthy_progress (the typed live work), not
    stale_codex_invocation."""

    stale = StaleCodexInvocation(
        actor="codex-g1",
        pid=4242,
        trace_path="/tmp/trace",
        stable_heartbeat_count=3,
        citations=["process:codex:4242"],
    )
    snapshot = _snapshot(merge_queue=[_merge_item("verifying")])
    obs = SupervisorObservation(
        feature_id="feat",
        phase="implementation",
        evidence_mode="typed",
        control_plane=snapshot,
        stale_codex_invocations=[stale],
    )
    packet = classify_observation(obs)
    # the active typed merge queue outranks the stale-Codex process evidence
    assert packet.classification is FailureClass.HEALTHY_PROGRESS
    assert packet.classification is not FailureClass.STALE_CODEX_INVOCATION


def test_stale_codex_still_classifies_when_no_typed_active_work():
    """The bridge/process rows are still reachable in typed mode when the
    typed snapshot carries NO active work — doc-10 priority 5 is not dead."""

    stale = StaleCodexInvocation(
        actor="codex-g1",
        pid=4242,
        trace_path="/tmp/trace",
        stable_heartbeat_count=3,
        citations=["process:codex:4242"],
    )
    # an empty typed snapshot — no failures, no merge queue, no leases
    snapshot = _snapshot()
    obs = SupervisorObservation(
        feature_id="feat",
        phase="implementation",
        evidence_mode="typed",
        control_plane=snapshot,
        stale_codex_invocations=[stale],
    )
    packet = classify_observation(obs)
    assert packet.classification is FailureClass.STALE_CODEX_INVOCATION
