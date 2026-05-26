"""Slice 17 first sub-slice -- unit tests for the foundational
``execution_control/policy_recommendation.py`` typed-shape module.

Covers the 2 doc-17:65-73 Literals + the 8 doc-17:75-145 typed
BaseModels + the canonical-JSON helper functions:

- :data:`PolicyConsumer` -- 6 values per doc-17:65.
- :data:`PolicyRecommendationStatus` -- 6 values per doc-17:66-73
  (deliberately EXCLUDES ``activated`` per doc-17:159-163).
- :class:`GovernancePolicyRecommendation` -- 14 fields per doc-17:75-97.
- :class:`PolicyRecommendationDecision` -- 5 fields per doc-17:99-105;
  Slice 13a shared ``GovernanceEvidenceRef`` consumption on
  ``evidence_refs`` (NOT redefined; DIRECT annotation-identity
  assertion via ``is``).
- :class:`FailureRouterPolicyArtifact` -- 7 fields per doc-17:107-114.
- :class:`SchedulerPolicyArtifact` -- 4 fields per doc-17:116-120.
- :class:`SupervisorPolicyArtifact` -- 4 fields per doc-17:122-126
  (with typed ``read_only: Literal[True] = True``).
- :class:`DashboardPolicyArtifact` -- 4 fields per doc-17:128-132
  (with typed ``read_only: Literal[True] = True``).
- :class:`PlanningPolicyArtifact` -- 4 fields per doc-17:134-138
  (with typed ``advisory_only: Literal[True] = True``).
- :class:`MergeQueuePolicyArtifact` -- 4 fields per doc-17:140-144.
- :func:`compute_policy_recommendation_idempotency_key` +
  :func:`canonical_policy_recommendation_dict` -- canonical-JSON +
  SHA-256 helpers mirroring Slice 13A ``compute_completeness_digest``
  + Slice 14 ``compute_payload_sha256`` + Slice 15
  ``compute_scorecard_digest`` + Slice 16 1st sub-slice
  ``compute_finding_idempotency_key`` + ``canonical_finding_dict``
  discipline.

Every model enforces ``extra="forbid"`` (typo-d kwargs ->
``ValidationError``). Every Literal range is enforced (per Pydantic
``Literal`` validator).

**Slice 14 P3-V3-2 addressed (DIRECT annotation-identity assertion).**
Per the implementer prompt § "Non-negotiables" the Slice 13a shared
model identity is enforced via a DIRECT
``get_args(get_origin(... .annotation))`` decomposition + ``is``
comparison rather than the indirect value-set + namespace assertions
used in Slice 14 1st sub-slice tests at
``tests/test_execution_control_commit_provenance.py:718``. This is the
stronger pattern V3 reviewer flagged in the Slice 14 close-out
(P3-V3-2 CARRY) + the pattern Slice 15 1st sub-slice + Slice 16 1st
sub-slice adopted.

**Slice 13A awareness asserted (doc-17:240-289).** No local
``CompletenessState`` / ``EvidenceCompleteness`` /
``AuthoritativePromptContextRouting`` /
``AuthoritativeGateCompanionRecord`` / ``AuthoritativeGateProofRow`` /
``AuthoritativeSnapshotListFieldCompleteness`` /
``AuthoritativeSnapshotClassifierRouting`` redefinition. The
policy-recommendation module exposes the typed surface that future
Slice 17 sub-slices wire to the Slice 13A typed shapes; this 1st
sub-slice enforces the no-redefinition discipline at the test-file
level.

**Doc-17:147-163 consumer-contract bindings awareness asserted.** The
typed surface deliberately EXCLUDES ``activated`` from
:data:`PolicyRecommendationStatus` (per doc-17:159-163). This 1st
sub-slice does NOT yet wire consumer activation rules; activation
belongs to a separate consumer-owned policy record per doc-17:147-163.

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 +
Slice 16 modules + tests remain byte-identical.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, get_args, get_origin

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.policy_recommendation import (
    DashboardPolicyArtifact,
    FailureRouterPolicyArtifact,
    GovernancePolicyRecommendation,
    MergeQueuePolicyArtifact,
    PlanningPolicyArtifact,
    PolicyConsumer,
    PolicyRecommendationDecision,
    PolicyRecommendationStatus,
    SchedulerPolicyArtifact,
    SupervisorPolicyArtifact,
    canonical_policy_recommendation_dict,
    compute_policy_recommendation_idempotency_key,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface_exactly() -> None:
    """The module ``__all__`` carries the 2 Literals + 8 typed
    BaseModels + 2 canonical-JSON helpers.

    Per doc-17:62-145 the surface is:
    * 2 Literals (``PolicyConsumer`` 6-value + ``PolicyRecommendationStatus``
      6-value).
    * 8 typed BaseModels (``GovernancePolicyRecommendation`` 14-field
      + ``PolicyRecommendationDecision`` 5-field + 6 consumer-specific
      policy artifact BaseModels per doc-17:107-145).
    * 2 canonical-JSON helpers (``compute_policy_recommendation_idempotency_key``
      + ``canonical_policy_recommendation_dict``).

    Total: 12 exported names.
    """

    from iriai_build_v2.execution_control import policy_recommendation as mod

    expected = {
        "PolicyConsumer",
        "PolicyRecommendationStatus",
        "GovernancePolicyRecommendation",
        "PolicyRecommendationDecision",
        "FailureRouterPolicyArtifact",
        "SchedulerPolicyArtifact",
        "SupervisorPolicyArtifact",
        "DashboardPolicyArtifact",
        "PlanningPolicyArtifact",
        "MergeQueuePolicyArtifact",
        "compute_policy_recommendation_idempotency_key",
        "canonical_policy_recommendation_dict",
    }
    assert set(mod.__all__) == expected
    assert len(expected) == 12
    for name in expected:
        assert hasattr(mod, name)


def test_module_does_not_redefine_governance_evidence_ref() -> None:
    """Per doc-13a:285-287 step 9 + doc-17:240-289 the Slice 17 module
    MUST NOT redefine :class:`GovernanceEvidenceRef` -- it consumes the
    Slice 13a shared model via direct import.
    """

    from iriai_build_v2.execution_control import policy_recommendation as mod

    # Either the module does NOT re-export GovernanceEvidenceRef OR (if
    # it does, e.g. via an internal import) the re-exported symbol IS
    # the Slice 13a shared class (identity).
    assert getattr(mod, "GovernanceEvidenceRef", None) is None or (
        mod.GovernanceEvidenceRef is GovernanceEvidenceRef  # type: ignore[attr-defined]
    )


def test_module_does_not_redefine_governance_finding() -> None:
    """Per doc-17:80 + doc-17:240-289 the Slice 17 module references
    Slice 16 :class:`GovernanceFinding` by-name via the
    ``source_finding_ids: list[str]`` field (NOT by re-defining the
    typed BaseModel locally; the by-name reference shape lives in
    doc-17:80 verbatim).
    """

    from iriai_build_v2.execution_control import policy_recommendation as mod

    # The Slice 17 module does NOT re-export or redefine the Slice 16
    # GovernanceFinding typed BaseModel (the by-name reference shape
    # is sufficient per doc-17:80).
    assert getattr(mod, "GovernanceFinding", None) is None


def test_module_does_not_redefine_governance_metric_value() -> None:
    """Per doc-17:81 + doc-17:240-289 the Slice 17 module references
    Slice 15 :class:`GovernanceMetricValue` by-name via the
    ``source_metric_refs: list[str]`` field (NOT by re-defining the
    typed BaseModel locally; the by-name reference shape lives in
    doc-17:81 verbatim).
    """

    from iriai_build_v2.execution_control import policy_recommendation as mod

    # The Slice 17 module does NOT re-export or redefine the Slice 15
    # GovernanceMetricValue typed BaseModel (the by-name reference shape
    # is sufficient per doc-17:81).
    assert getattr(mod, "GovernanceMetricValue", None) is None


def test_module_does_not_redefine_completeness_state() -> None:
    """Per doc-17:240-289 the Slice 17 module MUST NOT redefine
    :data:`CompletenessState` -- it consumes the Slice 13A shared
    Literal via direct import in subsequent sub-slices (not this 1st
    sub-slice which exposes only the typed-shape foundation).
    """

    from iriai_build_v2.execution_control import policy_recommendation as mod

    assert getattr(mod, "CompletenessState", None) is None


def test_module_does_not_redefine_evidence_completeness() -> None:
    """Per doc-17:240-289 the Slice 17 module MUST NOT redefine
    :class:`EvidenceCompleteness` -- it consumes the Slice 13A shared
    BaseModel via direct import in subsequent sub-slices.
    """

    from iriai_build_v2.execution_control import policy_recommendation as mod

    assert getattr(mod, "EvidenceCompleteness", None) is None


def test_module_does_not_redefine_authoritative_prompt_context_routing() -> None:
    """Per doc-17:240-289 the Slice 17 module MUST NOT redefine
    :class:`AuthoritativePromptContextRouting` -- it consumes the
    Slice 13A shared BaseModel via direct import in subsequent
    sub-slices.
    """

    from iriai_build_v2.execution_control import policy_recommendation as mod

    assert getattr(mod, "AuthoritativePromptContextRouting", None) is None


def test_module_does_not_redefine_authoritative_gate_proof_row() -> None:
    """Per doc-17:240-289 the Slice 17 module MUST NOT redefine
    :class:`AuthoritativeGateProofRow` -- it consumes the Slice 13A
    shared BaseModel via direct import in subsequent sub-slices.
    """

    from iriai_build_v2.execution_control import policy_recommendation as mod

    assert getattr(mod, "AuthoritativeGateProofRow", None) is None


def test_module_does_not_redefine_authoritative_snapshot_classifier_routing() -> None:
    """Per doc-17:240-289 the Slice 17 module MUST NOT redefine
    :class:`AuthoritativeSnapshotClassifierRouting` -- it consumes the
    Slice 13A shared BaseModel via direct import in subsequent
    sub-slices.
    """

    from iriai_build_v2.execution_control import policy_recommendation as mod

    assert getattr(mod, "AuthoritativeSnapshotClassifierRouting", None) is None


def test_module_import_discipline_no_implementation_py() -> None:
    """Per the implementer prompt § "Implementation discipline" the
    Slice 17 1st sub-slice module MUST NOT import from the legacy
    ``implementation.py`` Slice 00-12 monolith (this module is
    foundational; the Slice 00-12 monolith is the runtime authority
    layer and is downstream of Slice 17's typed-shape surface).
    """

    import iriai_build_v2.execution_control.policy_recommendation as mod

    src = open(mod.__file__).read()
    forbidden = (
        "from iriai_build_v2.workflows.develop.execution.implementation",
        "import iriai_build_v2.workflows.develop.execution.implementation",
    )
    for f in forbidden:
        assert f not in src, (
            f"policy_recommendation.py must not import from the Slice 00-12 "
            f"implementation.py monolith; found {f!r}"
        )


def test_module_import_discipline_no_failure_router() -> None:
    """Per the implementer prompt § "Implementation discipline" the
    Slice 17 1st sub-slice module MUST NOT import from
    ``workflows.develop.execution.failure_router`` (the typed failure
    router is the Slice 07 runtime authority; Slice 17 1st sub-slice
    is a pure typed-shape foundation that does not yet emit any failure
    ids).
    """

    import iriai_build_v2.execution_control.policy_recommendation as mod

    src = open(mod.__file__).read()
    forbidden = (
        "from iriai_build_v2.workflows.develop.execution.failure_router",
        "import iriai_build_v2.workflows.develop.execution.failure_router",
    )
    for f in forbidden:
        assert f not in src, (
            f"policy_recommendation.py must not import from "
            f"failure_router.py; found {f!r}"
        )


def test_module_import_discipline_no_other_execution_control_modules() -> None:
    """Per the implementer prompt § "Implementation discipline" the
    Slice 17 1st sub-slice module MUST NOT import from other parts of
    ``execution_control/`` (the existing Slice 00-16 ``execution_control``
    modules are READ-ONLY; the only allowed sibling import is via the
    Slice 13a shared model at
    ``workflows.develop.governance.models``).

    This sub-slice is the typed-shape foundation; subsequent Slice 17
    sub-slices wire it to Slice 13A / Slice 15 / Slice 16 modules.
    """

    import iriai_build_v2.execution_control.policy_recommendation as mod

    src = open(mod.__file__).read()
    forbidden = (
        "from iriai_build_v2.execution_control.commit_provenance",
        "from iriai_build_v2.execution_control.completeness",
        "from iriai_build_v2.execution_control.dispatcher_prompt_context",
        "from iriai_build_v2.execution_control.gate_companion",
        "from iriai_build_v2.execution_control.snapshot_companion",
        "from iriai_build_v2.execution_control.governance_metrics",
        "from iriai_build_v2.execution_control.governance_metric_extractor",
        "from iriai_build_v2.execution_control.governance_scorecard_writer",
        "from iriai_build_v2.execution_control.finding_engine",
        "from iriai_build_v2.execution_control.finding_rule_engine",
        "from iriai_build_v2.execution_control.finding_plan_deviation_engine",
        "from iriai_build_v2.execution_control.finding_reviewer_test_failure_engine",
        "from iriai_build_v2.execution_control.governance_finding_writer",
        "from iriai_build_v2.execution_control.store",
        "from iriai_build_v2.execution_control.atomic_landing",
        "from iriai_build_v2.execution_control.adoption",
        "from iriai_build_v2.execution_control.startup",
    )
    for f in forbidden:
        assert f not in src, (
            f"policy_recommendation.py 1st sub-slice must not import "
            f"from other execution_control modules; found {f!r}. "
            f"Subsequent Slice 17 sub-slices may add these imports per "
            f"doc-17:165-179."
        )


def test_module_import_discipline_no_supervisor_or_dashboard() -> None:
    """Per the governance prompt § "Non-Negotiables" + doc-17:155
    *"Supervisor and dashboard consume advisory summaries and must
    remain read-only."* the Slice 17 typed-shape module MUST NOT
    import from ``supervisor`` or ``dashboard`` (those are downstream
    consumers of the policy recommendation interface, not
    dependencies).
    """

    import iriai_build_v2.execution_control.policy_recommendation as mod

    src = open(mod.__file__).read()
    forbidden = (
        "from iriai_build_v2.supervisor",
        "import iriai_build_v2.supervisor",
        "from iriai_build_v2.dashboard",
        "import iriai_build_v2.dashboard",
        "from dashboard",
        "import dashboard",
    )
    for f in forbidden:
        assert f not in src, (
            f"policy_recommendation.py must not import from supervisor "
            f"or dashboard; found {f!r}"
        )


def test_package_init_does_not_re_export_policy_recommendation() -> None:
    """Per the Slice 13A + Slice 14 + Slice 15 + Slice 16 precedent
    (no-re-export discipline) the
    ``src/iriai_build_v2/execution_control/__init__.py`` MUST NOT
    re-export the Slice 17 1st sub-slice module's typed shapes.
    Consumers import directly from
    :mod:`iriai_build_v2.execution_control.policy_recommendation`.
    """

    from iriai_build_v2 import execution_control as pkg

    forbidden_re_exports = (
        "PolicyConsumer",
        "PolicyRecommendationStatus",
        "GovernancePolicyRecommendation",
        "PolicyRecommendationDecision",
        "FailureRouterPolicyArtifact",
        "SchedulerPolicyArtifact",
        "SupervisorPolicyArtifact",
        "DashboardPolicyArtifact",
        "PlanningPolicyArtifact",
        "MergeQueuePolicyArtifact",
        "compute_policy_recommendation_idempotency_key",
        "canonical_policy_recommendation_dict",
    )
    for name in forbidden_re_exports:
        assert name not in pkg.__all__, (
            f"execution_control/__init__.py must not re-export Slice "
            f"17 module symbol {name!r} (per Slice 13A/14/15/16 "
            f"no-re-export discipline)"
        )
        assert not hasattr(pkg, name), (
            f"execution_control/__init__.py must not re-export Slice "
            f"17 module symbol {name!r} (per Slice 13A/14/15/16 "
            f"no-re-export discipline)"
        )


# ── PolicyConsumer (doc-17:65) ─────────────────────────────────────────────


def test_policy_consumer_is_6_value_literal() -> None:
    """Per doc-17:65 :data:`PolicyConsumer` is exactly the 6-value
    Literal verbatim.
    """

    args = get_args(PolicyConsumer)
    assert len(args) == 6
    assert set(args) == {
        "scheduler",
        "failure_router",
        "supervisor",
        "dashboard",
        "planning",
        "merge_queue",
    }


@pytest.mark.parametrize(
    "consumer",
    [
        "scheduler",
        "failure_router",
        "supervisor",
        "dashboard",
        "planning",
        "merge_queue",
    ],
)
def test_policy_consumer_accepts_all_6_values(consumer: str) -> None:
    """Per doc-17:65 every one of the 6 Literal values is constructible
    on a :class:`GovernancePolicyRecommendation`.
    """

    r = _recommendation(consumer=consumer)
    assert r.consumer == consumer


def test_policy_consumer_rejects_unknown_value() -> None:
    """Per Pydantic Literal validation an unknown consumer value fails
    closed at construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _recommendation(consumer="random_made_up_consumer")


# ── PolicyRecommendationStatus (doc-17:66-73) ──────────────────────────────


def test_policy_recommendation_status_is_6_value_literal() -> None:
    """Per doc-17:66-73 :data:`PolicyRecommendationStatus` is exactly
    the 6-value Literal verbatim (NOT 7 -- ``activated`` is
    deliberately excluded per doc-17:159-163).
    """

    args = get_args(PolicyRecommendationStatus)
    assert len(args) == 6
    assert set(args) == {
        "draft",
        "reviewed",
        "accepted",
        "rejected",
        "needs_more_evidence",
        "superseded",
    }


def test_policy_recommendation_status_does_not_include_activated() -> None:
    """**Doc-17:159-163 binding statement.** Per doc-17:159-163
    *"`activated` is deliberately not a
    `GovernancePolicyRecommendation.status`. Activation belongs to a
    separate consumer-owned policy record with its own schema, tests,
    replay proof, rollback plan, and audit trail."* the typed Literal
    MUST NOT include ``activated`` as a value.

    This test enforces the typed-surface invariant at the Literal
    range; the runtime-policy-activation surface is consumer-owned
    (Slice 07 / Slice 09 / Slice 10 / Slice 12).
    """

    args = get_args(PolicyRecommendationStatus)
    assert "activated" not in args, (
        "PolicyRecommendationStatus must NOT include 'activated' per "
        "doc-17:159-163 (activation belongs to a separate "
        "consumer-owned policy record with its own schema, tests, "
        "replay proof, rollback plan, and audit trail)."
    )


@pytest.mark.parametrize(
    "status",
    [
        "draft",
        "reviewed",
        "accepted",
        "rejected",
        "needs_more_evidence",
        "superseded",
    ],
)
def test_policy_recommendation_status_accepts_all_6_values(status: str) -> None:
    """Per doc-17:66-73 every one of the 6 Literal values is
    constructible on a :class:`GovernancePolicyRecommendation`.
    """

    r = _recommendation(status=status)
    assert r.status == status


def test_policy_recommendation_status_rejects_unknown_value() -> None:
    """Per Pydantic Literal validation an unknown status value fails
    closed at construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _recommendation(status="random_made_up_status")


def test_policy_recommendation_status_rejects_activated_value() -> None:
    """**Doc-17:159-163 binding statement.** A typo-d / mistaken
    ``status="activated"`` fails closed at construction with a typed
    ``ValidationError`` (the typed Literal range is the enforcer; no
    governance-recommendation row can carry an activated status).
    """

    with pytest.raises(ValidationError):
        _recommendation(status="activated")


# ── Test fixtures / constructors ────────────────────────────────────────────


def _evidence_ref(**overrides: object) -> GovernanceEvidenceRef:
    """Construct a fully-specified Slice 13a :class:`GovernanceEvidenceRef`
    for tests.
    """

    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id="ref-17-1",
        digest="a" * 64,
        quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)


def _scheduler_artifact(**overrides: object) -> SchedulerPolicyArtifact:
    """Construct a fully-specified :class:`SchedulerPolicyArtifact` for
    tests.
    """

    base: dict[str, object] = dict(
        policy_kind="wave_cap",
        scope={"lane_id": "ml-7"},
        value={"wave_cap": 7},
        guardrails=["max_concurrent_tasks_per_lane"],
    )
    base.update(overrides)
    return SchedulerPolicyArtifact(**base)


def _failure_router_artifact(**overrides: object) -> FailureRouterPolicyArtifact:
    """Construct a fully-specified :class:`FailureRouterPolicyArtifact`
    for tests.
    """

    base: dict[str, object] = dict(
        failure_class="runtime_failure",
        failure_type="runtime_provider_outage",
        action="retry",
        route_budget_key="runtime_failure/runtime_provider_outage",
        max_attempts=3,
        idempotency_key_template="runtime_failure/{task_id}/{attempt_window}",
        required_tests=["test_failure_router_route_table"],
    )
    base.update(overrides)
    return FailureRouterPolicyArtifact(**base)


def _supervisor_artifact(**overrides: object) -> SupervisorPolicyArtifact:
    """Construct a fully-specified :class:`SupervisorPolicyArtifact` for
    tests.
    """

    base: dict[str, object] = dict(
        policy_kind="classification_hint",
        scope={"failure_class": "runtime_failure"},
        value={"hint": "elevate"},
    )
    base.update(overrides)
    return SupervisorPolicyArtifact(**base)


def _dashboard_artifact(**overrides: object) -> DashboardPolicyArtifact:
    """Construct a fully-specified :class:`DashboardPolicyArtifact` for
    tests.
    """

    base: dict[str, object] = dict(
        policy_kind="view_priority",
        scope={"panel": "merge_queue"},
        value={"priority": 100},
    )
    base.update(overrides)
    return DashboardPolicyArtifact(**base)


def _planning_artifact(**overrides: object) -> PlanningPolicyArtifact:
    """Construct a fully-specified :class:`PlanningPolicyArtifact` for
    tests.
    """

    base: dict[str, object] = dict(
        policy_kind="future_dag_hint",
        scope={"feature_id": "8ac124d6"},
        value={"hint": "split_into_smaller_tasks"},
    )
    base.update(overrides)
    return PlanningPolicyArtifact(**base)


def _merge_queue_artifact(**overrides: object) -> MergeQueuePolicyArtifact:
    """Construct a fully-specified :class:`MergeQueuePolicyArtifact` for
    tests.
    """

    base: dict[str, object] = dict(
        policy_kind="lane_priority",
        scope={"lane_id": "ml-7"},
        value={"priority": 100},
        required_queue_tests=["test_merge_queue_lane_priority"],
    )
    base.update(overrides)
    return MergeQueuePolicyArtifact(**base)


def _recommendation(**overrides: object) -> GovernancePolicyRecommendation:
    """Construct a fully-specified :class:`GovernancePolicyRecommendation`
    for tests.
    """

    base: dict[str, object] = dict(
        idempotency_key="recommendation-key-abc123",
        recommendation_id="rec-17-1",
        consumer="scheduler",
        status="draft",
        source_finding_ids=["finding-key-abc123", "finding-key-def456"],
        source_metric_refs=["tasks_per_hour@v1", "repair_cycles_per_task@v1"],
        counterfactual_result_refs=[],
        confidence=0.85,
        expected_impact={"tasks_per_hour_delta": 0.15},
        risk_level="low",
        safe_runtime_action=False,
        requires_tests=["test_scheduler_wave_cap"],
        proposed_policy_artifact=_scheduler_artifact(),
        activation_requirements=["replay_against_8ac124d6_passes"],
        rollback_requirements=["revert_to_prior_wave_cap"],
    )
    base.update(overrides)
    return GovernancePolicyRecommendation(**base)


def _decision(**overrides: object) -> PolicyRecommendationDecision:
    """Construct a fully-specified :class:`PolicyRecommendationDecision`
    for tests.
    """

    base: dict[str, object] = dict(
        recommendation_id="rec-17-1",
        decision="accept",
        decided_by="governance-reviewer-1",
        decided_at=datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc),
        rationale="Replay against 8ac124d6 passes; safe to accept for review.",
        evidence_refs=[_evidence_ref()],
    )
    base.update(overrides)
    return PolicyRecommendationDecision(**base)


# ── GovernancePolicyRecommendation (doc-17:75-97) ──────────────────────────


def test_recommendation_accepts_all_14_plus_fields() -> None:
    """The 14+ doc-17:75-97 fields all populate cleanly on a
    fully-specified :class:`GovernancePolicyRecommendation`.

    Per doc-17:75-97 the surface is:
    idempotency_key, recommendation_id, consumer, status,
    source_finding_ids, source_metric_refs, counterfactual_result_refs,
    confidence, expected_impact, risk_level, safe_runtime_action,
    requires_tests, proposed_policy_artifact, activation_requirements,
    rollback_requirements = 15 fields.
    """

    r = _recommendation()
    assert r.idempotency_key == "recommendation-key-abc123"
    assert r.recommendation_id == "rec-17-1"
    assert r.consumer == "scheduler"
    assert r.status == "draft"
    assert r.source_finding_ids == ["finding-key-abc123", "finding-key-def456"]
    assert r.source_metric_refs == [
        "tasks_per_hour@v1",
        "repair_cycles_per_task@v1",
    ]
    assert r.counterfactual_result_refs == []
    assert r.confidence == 0.85
    assert r.expected_impact == {"tasks_per_hour_delta": 0.15}
    assert r.risk_level == "low"
    assert r.safe_runtime_action is False
    assert r.requires_tests == ["test_scheduler_wave_cap"]
    assert isinstance(r.proposed_policy_artifact, SchedulerPolicyArtifact)
    assert r.activation_requirements == ["replay_against_8ac124d6_passes"]
    assert r.rollback_requirements == ["revert_to_prior_wave_cap"]


def test_recommendation_extra_forbid_rejects_unknown_field() -> None:
    """Per ``ConfigDict(extra="forbid")`` typo-d kwargs fail closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _recommendation(unknown_field="oops")  # type: ignore[arg-type]


def test_recommendation_round_trips_via_json() -> None:
    """Per the Pydantic v2 idiom a typed
    :class:`GovernancePolicyRecommendation` -> JSON ->
    :class:`GovernancePolicyRecommendation` round-trip is value-
    equivalent.
    """

    r = _recommendation()
    j = r.model_dump_json()
    r2 = GovernancePolicyRecommendation.model_validate_json(j)
    assert r == r2


def test_recommendation_risk_level_is_3_value_literal() -> None:
    """Per doc-17:85 :attr:`GovernancePolicyRecommendation.risk_level`
    is the 3-value Literal verbatim.
    """

    annotation = GovernancePolicyRecommendation.model_fields["risk_level"].annotation
    args = get_args(annotation)
    assert set(args) == {"low", "medium", "high"}


@pytest.mark.parametrize("risk_level", ["low", "medium", "high"])
def test_recommendation_risk_level_accepts_all_3_values(risk_level: str) -> None:
    """Per doc-17:85 each Literal value populates cleanly."""

    r = _recommendation(risk_level=risk_level)
    assert r.risk_level == risk_level


def test_recommendation_risk_level_rejects_unknown_value() -> None:
    """Per Pydantic Literal validation an unknown ``risk_level`` value
    fails closed at construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _recommendation(risk_level="extreme")


def test_recommendation_source_finding_ids_accepts_string_list() -> None:
    """Per doc-17:80 :attr:`GovernancePolicyRecommendation.source_finding_ids`
    is ``list[str]`` (NOT a list of the typed Slice 16
    :class:`GovernanceFinding` BaseModel; just the
    :attr:`GovernanceFinding.idempotency_key` STRINGS per the by-name
    reference contract).
    """

    annotation = GovernancePolicyRecommendation.model_fields[
        "source_finding_ids"
    ].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is str


def test_recommendation_source_finding_ids_accepts_empty_list() -> None:
    """Per doc-17:80 the typed surface accepts the empty list at
    construction (the at-least-one-source-finding invariant lives in
    the future Slice 17 2nd sub-slice recommendation builder per
    doc-17:204).
    """

    r = _recommendation(source_finding_ids=[])
    assert r.source_finding_ids == []


def test_recommendation_source_finding_ids_references_slice_16_idempotency_key_string_shape() -> None:
    """Per the implementer prompt § "Reuse contracts" the
    :attr:`source_finding_ids` field references Slice 16
    :attr:`GovernanceFinding.idempotency_key` STRINGS (per
    ``finding_engine.py:443`` the field is typed as ``str``); the
    typed surface enforces the by-name reference shape at construction.
    """

    from iriai_build_v2.execution_control.finding_engine import (
        GovernanceFinding,
    )

    # Slice 16 GovernanceFinding.idempotency_key field is typed as str.
    finding_id_annotation = GovernanceFinding.model_fields[
        "idempotency_key"
    ].annotation
    assert finding_id_annotation is str

    # Slice 17 source_finding_ids field is typed as list[str] (matching
    # the by-name reference shape).
    annotation = GovernancePolicyRecommendation.model_fields[
        "source_finding_ids"
    ].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert args[0] is str

    # A real Slice 16 finding idempotency_key string round-trips into
    # the Slice 17 source_finding_ids field.
    r = _recommendation(source_finding_ids=["finding-key-abc123"])
    assert r.source_finding_ids == ["finding-key-abc123"]


def test_recommendation_source_metric_refs_accepts_string_list() -> None:
    """Per doc-17:81 :attr:`GovernancePolicyRecommendation.source_metric_refs`
    is ``list[str]`` (NOT a list of the typed Slice 15
    :class:`GovernanceMetricValue` BaseModel; just the metric REF
    STRINGS per the by-name reference contract).
    """

    annotation = GovernancePolicyRecommendation.model_fields[
        "source_metric_refs"
    ].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is str


def test_recommendation_source_metric_refs_accepts_empty_list() -> None:
    """Per doc-17:81 the typed surface accepts the empty list at
    construction (some recommendations ground on findings + evidence
    only, with no direct metric reference).
    """

    r = _recommendation(source_metric_refs=[])
    assert r.source_metric_refs == []


def test_recommendation_source_metric_refs_references_slice_15_governance_metric_value_shape() -> None:
    """Per the implementer prompt § "Reuse contracts" the
    :attr:`source_metric_refs` field references Slice 15
    :class:`GovernanceMetricValue` ref STRINGS (per the Slice 15
    :attr:`GovernanceMetricValue.definition_name` +
    :attr:`GovernanceMetricValue.definition_version` shape at
    ``governance_metrics.py:316``); the typed surface enforces the
    by-name reference shape at construction.

    The ref-string projection (e.g. ``"tasks_per_hour@v1"``) is the
    documented doc-17:81 contract; the by-name reference shape mirrors
    the Slice 16 1st sub-slice ``metric_refs: list[str]`` pattern at
    ``finding_engine.py:574``.
    """

    from iriai_build_v2.execution_control.governance_metrics import (
        GovernanceMetricValue,
    )

    # Slice 15 GovernanceMetricValue.definition_name field is typed as
    # str (the name-portion of the ref).
    metric_name_annotation = GovernanceMetricValue.model_fields[
        "definition_name"
    ].annotation
    assert metric_name_annotation is str

    # Slice 17 source_metric_refs field is typed as list[str] (matching
    # the by-name reference shape).
    annotation = GovernancePolicyRecommendation.model_fields[
        "source_metric_refs"
    ].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert args[0] is str

    # A real Slice 15-style ref string round-trips into the Slice 17
    # source_metric_refs field.
    r = _recommendation(source_metric_refs=["tasks_per_hour@v1"])
    assert r.source_metric_refs == ["tasks_per_hour@v1"]


def test_recommendation_counterfactual_result_refs_accepts_empty_list() -> None:
    """Per doc-17:82 the typed surface accepts the empty list at
    construction (recommendations that propose no behavior changes can
    omit replay refs per doc-17:198-200 the
    safe_runtime_action=false case).
    """

    r = _recommendation(counterfactual_result_refs=[])
    assert r.counterfactual_result_refs == []


def test_recommendation_consumer_annotation_is_direct_policy_consumer_literal() -> None:
    """**Slice 17 typed-surface DIRECT annotation-identity assertion**.

    The :attr:`GovernancePolicyRecommendation.consumer` field MUST be
    typed against the DIRECT :data:`PolicyConsumer` Literal (NOT a
    re-aliased copy).
    """

    annotation = GovernancePolicyRecommendation.model_fields["consumer"].annotation
    assert annotation is PolicyConsumer
    assert set(get_args(annotation)) == {
        "scheduler",
        "failure_router",
        "supervisor",
        "dashboard",
        "planning",
        "merge_queue",
    }


def test_recommendation_status_annotation_is_direct_policy_recommendation_status_literal() -> None:
    """**Slice 17 typed-surface DIRECT annotation-identity assertion**.

    The :attr:`GovernancePolicyRecommendation.status` field MUST be
    typed against the DIRECT :data:`PolicyRecommendationStatus` Literal
    (NOT a re-aliased copy).
    """

    annotation = GovernancePolicyRecommendation.model_fields["status"].annotation
    assert annotation is PolicyRecommendationStatus
    # And the Literal does NOT include 'activated' (per doc-17:159-163).
    assert "activated" not in set(get_args(annotation))


def test_recommendation_proposed_policy_artifact_union_includes_all_6_consumer_shapes() -> None:
    """Per doc-17:88-95 :attr:`proposed_policy_artifact` is a typed
    union over the 6 consumer-specific policy artifact BaseModels.

    The typed surface MUST accept all 6 typed BaseModels in the union.
    """

    # Construct a recommendation per consumer artifact type; the typed
    # union accepts all 6 BaseModels.
    for artifact_ctor in (
        _scheduler_artifact,
        _failure_router_artifact,
        _supervisor_artifact,
        _dashboard_artifact,
        _planning_artifact,
        _merge_queue_artifact,
    ):
        artifact = artifact_ctor()
        r = _recommendation(proposed_policy_artifact=artifact)
        assert type(r.proposed_policy_artifact) is type(artifact)


# ── PolicyRecommendationDecision (doc-17:99-105) ───────────────────────────


def test_decision_accepts_all_5_fields() -> None:
    """The 5 doc-17:100-105 fields all populate cleanly on a
    fully-specified :class:`PolicyRecommendationDecision`.
    """

    d = _decision()
    assert d.recommendation_id == "rec-17-1"
    assert d.decision == "accept"
    assert d.decided_by == "governance-reviewer-1"
    assert d.decided_at == datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    assert d.rationale == (
        "Replay against 8ac124d6 passes; safe to accept for review."
    )
    assert len(d.evidence_refs) == 1
    assert isinstance(d.evidence_refs[0], GovernanceEvidenceRef)


def test_decision_extra_forbid_rejects_unknown_field() -> None:
    """Per ``ConfigDict(extra="forbid")`` typo-d kwargs fail closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _decision(unknown_field="oops")  # type: ignore[arg-type]


def test_decision_round_trips_via_json() -> None:
    """Per the Pydantic v2 idiom a typed
    :class:`PolicyRecommendationDecision` -> JSON ->
    :class:`PolicyRecommendationDecision` round-trip is value-
    equivalent (including the typed ``datetime`` field projecting to
    ISO-8601 string under ``mode='json'``).
    """

    d = _decision()
    j = d.model_dump_json()
    d2 = PolicyRecommendationDecision.model_validate_json(j)
    assert d == d2


def test_decision_decision_is_3_value_literal() -> None:
    """Per doc-17:101 :attr:`PolicyRecommendationDecision.decision` is
    the 3-value Literal verbatim.
    """

    annotation = PolicyRecommendationDecision.model_fields["decision"].annotation
    args = get_args(annotation)
    assert set(args) == {"accept", "reject", "needs_more_evidence"}


@pytest.mark.parametrize(
    "decision", ["accept", "reject", "needs_more_evidence"]
)
def test_decision_accepts_all_3_decision_values(decision: str) -> None:
    """Per doc-17:101 each Literal value populates cleanly."""

    d = _decision(decision=decision)
    assert d.decision == decision


def test_decision_rejects_unknown_decision_value() -> None:
    """Per Pydantic Literal validation an unknown ``decision`` value
    fails closed at construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _decision(decision="maybe")


def test_decision_evidence_refs_accepts_empty_list() -> None:
    """Per doc-17:105 the typed surface accepts the empty list at
    construction (the at-least-one-evidence-ref invariant lives in the
    future Slice 17 4th sub-slice decision-record writer).
    """

    d = _decision(evidence_refs=[])
    assert d.evidence_refs == []


# ── Slice 13a shared model identity (DIRECT annotation-identity) ───────────


def test_decision_evidence_refs_annotation_is_list_of_slice_13a_governance_evidence_ref() -> None:
    """**Slice 13a dependency reconciliation DIRECT annotation-identity
    assertion** (doc-13a:285-287 step 9 + doc-17:240-289).

    Per the implementer prompt § "Non-negotiables" this is the STRONGER
    pattern Slice 14 V3 reviewer flagged in P3-V3-2 CARRY + the pattern
    Slice 15 1st sub-slice + Slice 16 1st sub-slice adopted:
    the :attr:`PolicyRecommendationDecision.evidence_refs` field MUST
    be typed against ``list[GovernanceEvidenceRef]`` where
    :class:`GovernanceEvidenceRef` IS the Slice 13a shared model
    imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models` -- NOT
    redefined here. Assert the annotation resolves to
    ``list[GovernanceEvidenceRef]`` via DIRECT identity comparison on
    the list element type.
    """

    annotation = PolicyRecommendationDecision.model_fields["evidence_refs"].annotation
    # The annotation IS a parameterised list.
    assert get_origin(annotation) is list
    # The list element type IS the Slice 13a shared class via DIRECT
    # identity assertion (the stronger pattern).
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceEvidenceRef


def test_decision_evidence_refs_is_imported_from_slice_13a() -> None:
    """Per doc-13a:285-287 step 9 + doc-17:240-289 the
    :class:`GovernanceEvidenceRef` consumed by Slice 17 IS the Slice 13a
    shared class (identity-equal via the direct import) -- NOT a
    second copy.
    """

    # The Slice 13a import path.
    from iriai_build_v2.workflows.develop.governance.models import (
        GovernanceEvidenceRef as Slice13aGovernanceEvidenceRef,
    )

    # The Slice 17 binding (via the annotation on ``evidence_refs``).
    args = get_args(
        PolicyRecommendationDecision.model_fields["evidence_refs"].annotation
    )
    assert len(args) == 1
    # The list element type IS the Slice 13a class (identity).
    assert args[0] is Slice13aGovernanceEvidenceRef


# ── Slice 14 P3-V3-2 lineage (stronger pattern; meta-assertion) ────────────


def test_slice_14_p3_v3_2_stronger_pattern_evidence_ref_annotation_identity() -> None:
    """Meta-test re-pinning the stronger P3-V3-2 pattern.

    Per the Slice 14 close-out (P3-V3-2 CARRY) + the Slice 15 1st
    sub-slice + Slice 16 1st sub-slice (which both adopted the stronger
    pattern) the Slice 17 1st sub-slice continues the stronger pattern
    of asserting Slice 13a shared model identity via DIRECT
    ``get_args(... .annotation)[0] is GovernanceEvidenceRef``
    comparison rather than the indirect value-set + namespace
    assertions used in Slice 14 1st sub-slice tests at
    ``tests/test_execution_control_commit_provenance.py:718``.

    This meta-test is the cross-Slice-15 / Slice-16 / Slice-17 anchor
    that future Slice 17 sub-slices grow against; the pattern is now
    the canonical REUSE-assertion idiom across the governance phase.
    """

    annotation = PolicyRecommendationDecision.model_fields["evidence_refs"].annotation
    # The stronger pattern (DIRECT identity, NOT indirect value-set).
    assert get_args(annotation)[0] is GovernanceEvidenceRef


# ── FailureRouterPolicyArtifact (doc-17:107-114) ───────────────────────────


def test_failure_router_artifact_accepts_all_7_fields() -> None:
    """The 7 doc-17:108-114 fields all populate cleanly on a
    fully-specified :class:`FailureRouterPolicyArtifact`.
    """

    a = _failure_router_artifact()
    assert a.failure_class == "runtime_failure"
    assert a.failure_type == "runtime_provider_outage"
    assert a.action == "retry"
    assert a.route_budget_key == "runtime_failure/runtime_provider_outage"
    assert a.max_attempts == 3
    assert a.idempotency_key_template == (
        "runtime_failure/{task_id}/{attempt_window}"
    )
    assert a.required_tests == ["test_failure_router_route_table"]


def test_failure_router_artifact_action_is_5_value_literal() -> None:
    """Per doc-17:110 :attr:`FailureRouterPolicyArtifact.action` is the
    5-value Literal verbatim.
    """

    annotation = FailureRouterPolicyArtifact.model_fields["action"].annotation
    args = get_args(annotation)
    assert set(args) == {
        "retry",
        "repair",
        "queue_recovery",
        "quiesce",
        "operator_required",
    }


@pytest.mark.parametrize(
    "action",
    ["retry", "repair", "queue_recovery", "quiesce", "operator_required"],
)
def test_failure_router_artifact_accepts_all_5_actions(action: str) -> None:
    """Per doc-17:110 each Literal value populates cleanly."""

    a = _failure_router_artifact(action=action)
    assert a.action == action


def test_failure_router_artifact_rejects_unknown_action() -> None:
    """Per Pydantic Literal validation an unknown ``action`` value
    fails closed at construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _failure_router_artifact(action="random_action")


def test_failure_router_artifact_round_trips_via_json() -> None:
    """Per the Pydantic v2 idiom a typed
    :class:`FailureRouterPolicyArtifact` -> JSON ->
    :class:`FailureRouterPolicyArtifact` round-trip is value-
    equivalent.
    """

    a = _failure_router_artifact()
    j = a.model_dump_json()
    a2 = FailureRouterPolicyArtifact.model_validate_json(j)
    assert a == a2


def test_failure_router_artifact_extra_forbid_rejects_unknown_field() -> None:
    """Per ``ConfigDict(extra="forbid")`` typo-d kwargs fail closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _failure_router_artifact(unknown_field="oops")  # type: ignore[arg-type]


# ── SchedulerPolicyArtifact (doc-17:116-120) ───────────────────────────────


def test_scheduler_artifact_accepts_all_4_fields() -> None:
    """The 4 doc-17:117-120 fields all populate cleanly on a
    fully-specified :class:`SchedulerPolicyArtifact`.
    """

    a = _scheduler_artifact()
    assert a.policy_kind == "wave_cap"
    assert a.scope == {"lane_id": "ml-7"}
    assert a.value == {"wave_cap": 7}
    assert a.guardrails == ["max_concurrent_tasks_per_lane"]


def test_scheduler_artifact_policy_kind_is_3_value_literal() -> None:
    """Per doc-17:117 :attr:`SchedulerPolicyArtifact.policy_kind` is the
    3-value Literal verbatim.
    """

    annotation = SchedulerPolicyArtifact.model_fields["policy_kind"].annotation
    args = get_args(annotation)
    assert set(args) == {"wave_cap", "barrier", "lane_priority"}


def test_scheduler_artifact_round_trips_via_json() -> None:
    """Per the Pydantic v2 idiom round-trip is value-equivalent."""

    a = _scheduler_artifact()
    j = a.model_dump_json()
    a2 = SchedulerPolicyArtifact.model_validate_json(j)
    assert a == a2


def test_scheduler_artifact_extra_forbid_rejects_unknown_field() -> None:
    """Per ``ConfigDict(extra="forbid")`` typo-d kwargs fail closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _scheduler_artifact(unknown_field="oops")  # type: ignore[arg-type]


# ── SupervisorPolicyArtifact (doc-17:122-126) ──────────────────────────────


def test_supervisor_artifact_accepts_all_4_fields() -> None:
    """The 4 doc-17:123-126 fields all populate cleanly on a
    fully-specified :class:`SupervisorPolicyArtifact`.
    """

    a = _supervisor_artifact()
    assert a.policy_kind == "classification_hint"
    assert a.scope == {"failure_class": "runtime_failure"}
    assert a.value == {"hint": "elevate"}
    assert a.read_only is True


def test_supervisor_artifact_policy_kind_is_3_value_literal() -> None:
    """Per doc-17:123 :attr:`SupervisorPolicyArtifact.policy_kind` is
    the 3-value Literal verbatim.
    """

    annotation = SupervisorPolicyArtifact.model_fields["policy_kind"].annotation
    args = get_args(annotation)
    assert set(args) == {"classification_hint", "dedupe", "digest_priority"}


def test_supervisor_artifact_read_only_is_literal_true_default() -> None:
    """**Doc-17:155 binding statement enforcement.** Per doc-17:126 the
    :attr:`SupervisorPolicyArtifact.read_only` field is typed as
    ``Literal[True] = True`` -- the typed surface MUST enforce the
    read-only invariant at construction.

    Per doc-17:155 *"Supervisor and dashboard consume advisory
    summaries and must remain read-only."* the read-only flag CANNOT
    be set to False; the typed Literal range fails closed at
    construction.
    """

    # Default-construct without read_only -> the typed default is True.
    a = _supervisor_artifact()
    assert a.read_only is True

    # Explicit read_only=True still constructible.
    a = _supervisor_artifact(read_only=True)
    assert a.read_only is True

    # Typed Literal[True] rejects read_only=False at construction.
    with pytest.raises(ValidationError):
        _supervisor_artifact(read_only=False)


def test_supervisor_artifact_round_trips_via_json() -> None:
    """Per the Pydantic v2 idiom round-trip is value-equivalent."""

    a = _supervisor_artifact()
    j = a.model_dump_json()
    a2 = SupervisorPolicyArtifact.model_validate_json(j)
    assert a == a2


def test_supervisor_artifact_extra_forbid_rejects_unknown_field() -> None:
    """Per ``ConfigDict(extra="forbid")`` typo-d kwargs fail closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _supervisor_artifact(unknown_field="oops")  # type: ignore[arg-type]


# ── DashboardPolicyArtifact (doc-17:128-132) ───────────────────────────────


def test_dashboard_artifact_accepts_all_4_fields() -> None:
    """The 4 doc-17:129-132 fields all populate cleanly on a
    fully-specified :class:`DashboardPolicyArtifact`.
    """

    a = _dashboard_artifact()
    assert a.policy_kind == "view_priority"
    assert a.scope == {"panel": "merge_queue"}
    assert a.value == {"priority": 100}
    assert a.read_only is True


def test_dashboard_artifact_policy_kind_is_3_value_literal() -> None:
    """Per doc-17:129 :attr:`DashboardPolicyArtifact.policy_kind` is
    the 3-value Literal verbatim.
    """

    annotation = DashboardPolicyArtifact.model_fields["policy_kind"].annotation
    args = get_args(annotation)
    assert set(args) == {"view_priority", "alert_threshold", "panel_visibility"}


def test_dashboard_artifact_read_only_is_literal_true_default() -> None:
    """**Doc-17:155 binding statement enforcement.** Per doc-17:132 the
    :attr:`DashboardPolicyArtifact.read_only` field is typed as
    ``Literal[True] = True`` -- same pattern as
    :class:`SupervisorPolicyArtifact`.
    """

    # Default-construct without read_only -> the typed default is True.
    a = _dashboard_artifact()
    assert a.read_only is True

    # Typed Literal[True] rejects read_only=False at construction.
    with pytest.raises(ValidationError):
        _dashboard_artifact(read_only=False)


def test_dashboard_artifact_round_trips_via_json() -> None:
    """Per the Pydantic v2 idiom round-trip is value-equivalent."""

    a = _dashboard_artifact()
    j = a.model_dump_json()
    a2 = DashboardPolicyArtifact.model_validate_json(j)
    assert a == a2


def test_dashboard_artifact_extra_forbid_rejects_unknown_field() -> None:
    """Per ``ConfigDict(extra="forbid")`` typo-d kwargs fail closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _dashboard_artifact(unknown_field="oops")  # type: ignore[arg-type]


# ── PlanningPolicyArtifact (doc-17:134-138) ────────────────────────────────


def test_planning_artifact_accepts_all_4_fields() -> None:
    """The 4 doc-17:135-138 fields all populate cleanly on a
    fully-specified :class:`PlanningPolicyArtifact`.
    """

    a = _planning_artifact()
    assert a.policy_kind == "future_dag_hint"
    assert a.scope == {"feature_id": "8ac124d6"}
    assert a.value == {"hint": "split_into_smaller_tasks"}
    assert a.advisory_only is True


def test_planning_artifact_policy_kind_is_2_value_literal() -> None:
    """Per doc-17:135 :attr:`PlanningPolicyArtifact.policy_kind` is the
    2-value Literal verbatim.
    """

    annotation = PlanningPolicyArtifact.model_fields["policy_kind"].annotation
    args = get_args(annotation)
    assert set(args) == {"future_dag_hint", "contract_template_hint"}


def test_planning_artifact_advisory_only_is_literal_true_default() -> None:
    """**Doc-17:156 binding statement enforcement.** Per doc-17:138 the
    :attr:`PlanningPolicyArtifact.advisory_only` field is typed as
    ``Literal[True] = True`` -- the typed surface enforces the
    advisory-only invariant at construction.

    Per doc-17:156 *"Planning consumes historical recommendations as
    context for future DAG design."* the advisory-only flag CANNOT
    be set to False; the typed Literal range fails closed at
    construction.
    """

    # Default-construct without advisory_only -> the typed default is True.
    a = _planning_artifact()
    assert a.advisory_only is True

    # Typed Literal[True] rejects advisory_only=False at construction.
    with pytest.raises(ValidationError):
        _planning_artifact(advisory_only=False)


def test_planning_artifact_round_trips_via_json() -> None:
    """Per the Pydantic v2 idiom round-trip is value-equivalent."""

    a = _planning_artifact()
    j = a.model_dump_json()
    a2 = PlanningPolicyArtifact.model_validate_json(j)
    assert a == a2


def test_planning_artifact_extra_forbid_rejects_unknown_field() -> None:
    """Per ``ConfigDict(extra="forbid")`` typo-d kwargs fail closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _planning_artifact(unknown_field="oops")  # type: ignore[arg-type]


# ── MergeQueuePolicyArtifact (doc-17:140-144) ──────────────────────────────


def test_merge_queue_artifact_accepts_all_4_fields() -> None:
    """The 4 doc-17:141-144 fields all populate cleanly on a
    fully-specified :class:`MergeQueuePolicyArtifact`.
    """

    a = _merge_queue_artifact()
    assert a.policy_kind == "lane_priority"
    assert a.scope == {"lane_id": "ml-7"}
    assert a.value == {"priority": 100}
    assert a.required_queue_tests == ["test_merge_queue_lane_priority"]


def test_merge_queue_artifact_policy_kind_is_3_value_literal() -> None:
    """Per doc-17:141 :attr:`MergeQueuePolicyArtifact.policy_kind` is
    the 3-value Literal verbatim.
    """

    annotation = MergeQueuePolicyArtifact.model_fields["policy_kind"].annotation
    args = get_args(annotation)
    assert set(args) == {"lane_priority", "recovery_budget", "commit_gate_hint"}


def test_merge_queue_artifact_round_trips_via_json() -> None:
    """Per the Pydantic v2 idiom round-trip is value-equivalent."""

    a = _merge_queue_artifact()
    j = a.model_dump_json()
    a2 = MergeQueuePolicyArtifact.model_validate_json(j)
    assert a == a2


def test_merge_queue_artifact_extra_forbid_rejects_unknown_field() -> None:
    """Per ``ConfigDict(extra="forbid")`` typo-d kwargs fail closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _merge_queue_artifact(unknown_field="oops")  # type: ignore[arg-type]


# ── ConfigDict(extra="forbid") discipline (8 BaseModels) ───────────────────


@pytest.mark.parametrize(
    "model_cls",
    [
        GovernancePolicyRecommendation,
        PolicyRecommendationDecision,
        FailureRouterPolicyArtifact,
        SchedulerPolicyArtifact,
        SupervisorPolicyArtifact,
        DashboardPolicyArtifact,
        PlanningPolicyArtifact,
        MergeQueuePolicyArtifact,
    ],
)
def test_all_8_base_models_carry_extra_forbid(model_cls: type) -> None:
    """Per the Slice 13A + Slice 14 + Slice 15 + Slice 16 precedent all
    Slice 17 typed BaseModels carry ``model_config =
    ConfigDict(extra="forbid")`` so typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.
    """

    assert model_cls.model_config.get("extra") == "forbid"


# ── canonical_policy_recommendation_dict +
#    compute_policy_recommendation_idempotency_key ──────────────────────────


def test_canonical_policy_recommendation_dict_is_json_serialisable() -> None:
    """Per the Slice 13A + Slice 14 + Slice 15 + Slice 16 canonical-JSON
    discipline the canonical-dict projection of a
    :class:`GovernancePolicyRecommendation` is JSON-serialisable.
    """

    import json as _json

    r = _recommendation()
    d = canonical_policy_recommendation_dict(r)
    assert isinstance(d, dict)
    # The dict roundtrips through json.dumps (proves all values
    # serialise).
    _json.dumps(d, sort_keys=True)


def test_canonical_policy_recommendation_dict_is_deterministic() -> None:
    """Two calls with the same logical
    :class:`GovernancePolicyRecommendation` MUST produce equal
    canonical dicts (the cross-process determinism contract
    :func:`compute_policy_recommendation_idempotency_key` relies on).
    """

    r1 = _recommendation()
    r2 = _recommendation()
    assert canonical_policy_recommendation_dict(r1) == (
        canonical_policy_recommendation_dict(r2)
    )


def test_canonical_policy_recommendation_dict_round_trip_stable() -> None:
    """A :class:`GovernancePolicyRecommendation` -> canonical dict ->
    JSON -> dict round-trip preserves the canonical dict (no key
    reordering).
    """

    import json as _json

    r = _recommendation()
    d = canonical_policy_recommendation_dict(r)
    j = _json.dumps(d, sort_keys=True)
    d2 = _json.loads(j)
    assert d == d2


def test_compute_policy_recommendation_idempotency_key_is_deterministic() -> None:
    """The helper MUST produce identical keys for identical inputs
    (the cross-process freshness contract mirroring Slice 16 1st
    sub-slice :func:`compute_finding_idempotency_key`).
    """

    args: dict[str, object] = dict(
        consumer="scheduler",
        recommendation_id="rec-17-1",
        source_finding_ids=["finding-key-1", "finding-key-2"],
        source_metric_refs=["metric-1@v1"],
        counterfactual_result_refs=["replay-1"],
    )
    k1 = compute_policy_recommendation_idempotency_key(**args)  # type: ignore[arg-type]
    k2 = compute_policy_recommendation_idempotency_key(**args)  # type: ignore[arg-type]
    assert k1 == k2
    # And the key is a 64-char SHA-256 hex digest.
    assert len(k1) == 64
    assert all(c in "0123456789abcdef" for c in k1)


def test_compute_policy_recommendation_idempotency_key_differs_on_consumer_change() -> None:
    """A different :data:`PolicyConsumer` MUST produce a different key
    (the dedupe-key dimension).
    """

    base: dict[str, object] = dict(
        consumer="scheduler",
        recommendation_id="rec-17-1",
        source_finding_ids=["finding-key-1"],
        source_metric_refs=[],
        counterfactual_result_refs=[],
    )
    k1 = compute_policy_recommendation_idempotency_key(**base)  # type: ignore[arg-type]
    base["consumer"] = "failure_router"
    k2 = compute_policy_recommendation_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_policy_recommendation_idempotency_key_differs_on_recommendation_id_change() -> None:
    """A different :attr:`recommendation_id` MUST produce a different
    key.
    """

    base: dict[str, object] = dict(
        consumer="scheduler",
        recommendation_id="rec-17-1",
        source_finding_ids=["finding-key-1"],
        source_metric_refs=[],
        counterfactual_result_refs=[],
    )
    k1 = compute_policy_recommendation_idempotency_key(**base)  # type: ignore[arg-type]
    base["recommendation_id"] = "rec-17-2"
    k2 = compute_policy_recommendation_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_policy_recommendation_idempotency_key_differs_on_source_finding_ids_change() -> None:
    """A different :attr:`source_finding_ids` list MUST produce a
    different key (the dedupe-key dimension).
    """

    base: dict[str, object] = dict(
        consumer="scheduler",
        recommendation_id="rec-17-1",
        source_finding_ids=["finding-key-1"],
        source_metric_refs=[],
        counterfactual_result_refs=[],
    )
    k1 = compute_policy_recommendation_idempotency_key(**base)  # type: ignore[arg-type]
    base["source_finding_ids"] = ["finding-key-2"]
    k2 = compute_policy_recommendation_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_policy_recommendation_idempotency_key_differs_on_source_metric_refs_change() -> None:
    """A different :attr:`source_metric_refs` list MUST produce a
    different key.
    """

    base: dict[str, object] = dict(
        consumer="scheduler",
        recommendation_id="rec-17-1",
        source_finding_ids=["finding-key-1"],
        source_metric_refs=["metric-1@v1"],
        counterfactual_result_refs=[],
    )
    k1 = compute_policy_recommendation_idempotency_key(**base)  # type: ignore[arg-type]
    base["source_metric_refs"] = ["metric-2@v1"]
    k2 = compute_policy_recommendation_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_policy_recommendation_idempotency_key_differs_on_counterfactual_result_refs_change() -> None:
    """A different :attr:`counterfactual_result_refs` list MUST produce
    a different key.
    """

    base: dict[str, object] = dict(
        consumer="scheduler",
        recommendation_id="rec-17-1",
        source_finding_ids=["finding-key-1"],
        source_metric_refs=[],
        counterfactual_result_refs=["replay-1"],
    )
    k1 = compute_policy_recommendation_idempotency_key(**base)  # type: ignore[arg-type]
    base["counterfactual_result_refs"] = ["replay-2"]
    k2 = compute_policy_recommendation_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_policy_recommendation_idempotency_key_is_order_invariant_on_source_finding_ids() -> None:
    """Per the Slice 16 1st sub-slice
    :func:`compute_finding_idempotency_key` order-invariance pattern the
    key MUST be order-invariant w.r.t. source-finding-id ordering (the
    helper sorts the list before digesting).
    """

    base: dict[str, object] = dict(
        consumer="scheduler",
        recommendation_id="rec-17-1",
        source_finding_ids=["finding-key-1", "finding-key-2", "finding-key-3"],
        source_metric_refs=[],
        counterfactual_result_refs=[],
    )
    k1 = compute_policy_recommendation_idempotency_key(**base)  # type: ignore[arg-type]
    base["source_finding_ids"] = ["finding-key-3", "finding-key-2", "finding-key-1"]
    k2 = compute_policy_recommendation_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 == k2


def test_compute_policy_recommendation_idempotency_key_is_order_invariant_on_source_metric_refs() -> None:
    """The key MUST be order-invariant w.r.t. source-metric-ref ordering."""

    base: dict[str, object] = dict(
        consumer="scheduler",
        recommendation_id="rec-17-1",
        source_finding_ids=["finding-key-1"],
        source_metric_refs=["metric-a@v1", "metric-b@v1", "metric-c@v1"],
        counterfactual_result_refs=[],
    )
    k1 = compute_policy_recommendation_idempotency_key(**base)  # type: ignore[arg-type]
    base["source_metric_refs"] = ["metric-c@v1", "metric-b@v1", "metric-a@v1"]
    k2 = compute_policy_recommendation_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 == k2


def test_compute_policy_recommendation_idempotency_key_accepts_empty_lists() -> None:
    """The helper MUST accept all-empty source lists and produce a
    stable key (the typed empty-list case per doc-17:80-82).
    """

    args: dict[str, object] = dict(
        consumer="scheduler",
        recommendation_id="rec-17-1",
        source_finding_ids=[],
        source_metric_refs=[],
        counterfactual_result_refs=[],
    )
    k = compute_policy_recommendation_idempotency_key(**args)  # type: ignore[arg-type]
    assert isinstance(k, str)
    assert len(k) == 64


# ── doc-17:240-289 Slice 13A consumption awareness ─────────────────────────


def test_doc_17_240_289_no_local_completeness_state_redefinition() -> None:
    """Per doc-17:240-289 Slice 13A Shared Completeness Model Dependency:
    Slice 17 future sub-slices consume the shared
    :data:`CompletenessState` Literal + :class:`EvidenceCompleteness`
    BaseModel from :mod:`iriai_build_v2.execution_control.completeness`,
    NOT a local redefinition.

    This 1st sub-slice does not yet wire those typed shapes into the
    policy-recommendation surface (that lives in subsequent sub-slices);
    the discipline is asserted at the test-file level by checking the
    module's source for no local ``CompletenessState`` /
    ``Authoritative*`` class statements.
    """

    import iriai_build_v2.execution_control.policy_recommendation as mod

    src = open(mod.__file__).read()
    forbidden_redefinitions = (
        "class CompletenessState",
        "class EvidenceCompleteness",
        "class AuthoritativePromptContextRouting",
        "class AuthoritativeGateCompanionRecord",
        "class AuthoritativeGateProofRow",
        "class AuthoritativeSnapshotListFieldCompleteness",
        "class AuthoritativeSnapshotClassifierRouting",
        "class ExactEvidenceManifest",
        "class AuthoritativeContextRef",
        "class EvidencePageRef",
        # Slice 16 / Slice 15 typed BaseModel redefinitions also
        # forbidden (by-name reference shape is sufficient per
        # doc-17:80-81).
        "class GovernanceFinding",
        "class GovernanceMetricValue",
    )
    for forbidden in forbidden_redefinitions:
        assert forbidden not in src, (
            f"policy_recommendation.py must not redefine the Slice 13A "
            f"or Slice 15 or Slice 16 shared shape {forbidden!r}; "
            f"consume it via direct import per doc-13a:285-287 step 9 "
            f"+ doc-17:240-289 + doc-17:80-81 by-name reference "
            f"contract."
        )

    # Also forbid local Literal aliases of the Slice 13A shape names.
    forbidden_aliases = (
        "CompletenessState = Literal",
    )
    for forbidden in forbidden_aliases:
        assert forbidden not in src, (
            f"policy_recommendation.py must not locally re-alias the "
            f"Slice 13A shared shape {forbidden!r}."
        )


# ── doc-17:147-163 consumer-contract bindings (read-only awareness) ────────


def test_doc_17_147_163_no_consumer_activation_wiring_in_1st_sub_slice() -> None:
    """**Doc-17:147-163 consumer-contract bindings awareness assertion.**

    Per doc-17:159-163 *"`activated` is deliberately not a
    `GovernancePolicyRecommendation.status`. Activation belongs to a
    separate consumer-owned policy record with its own schema, tests,
    replay proof, rollback plan, and audit trail."* -- the Slice 17
    1st sub-slice MUST NOT wire any consumer activation rules; the
    typed-shape foundation exposes only the audit-trail surface for the
    recommendation lifecycle (draft / reviewed / accepted / rejected /
    needs_more_evidence / superseded; NO ``activated`` value).

    This test asserts the 1st sub-slice is pure typed-shape; the module
    source does NOT carry any consumer activation logic (no
    ``activate_*`` / ``apply_*`` / ``commit_*`` function definitions
    that would mutate a consumer surface).
    """

    import iriai_build_v2.execution_control.policy_recommendation as mod

    src = open(mod.__file__).read()
    # Forbidden top-level function names that would imply
    # consumer-activation wiring (the 1st sub-slice is pure typed-shape;
    # activation belongs to the consumer-owned modules per doc-17:147-163).
    forbidden_activation_function_defs = (
        "def activate_recommendation",
        "def activate_policy",
        "def apply_recommendation",
        "def apply_policy_artifact",
        "def commit_recommendation",
        "def commit_policy_artifact",
        "def write_route_table_update",
        "def write_scheduler_policy",
        "def write_merge_queue_policy",
    )
    for f in forbidden_activation_function_defs:
        assert f not in src, (
            f"policy_recommendation.py 1st sub-slice must not wire "
            f"consumer activation; found {f!r}. Per doc-17:159-163 "
            f"activation belongs to a separate consumer-owned policy "
            f"record."
        )


def test_doc_17_182_188_no_local_persistence_artifact_keys() -> None:
    """**Doc-17:182-188 persistence + artifact compatibility awareness.**

    Per doc-17:182-188 *"Store recommendations as typed governance rows
    and project review artifacts such as
    `review:governance-recommendations:{corpus_id}`. Do not write
    `dag-regroup-active:*`, route-budget state, supervisor actions, or
    merge queue state from governance recommendation generation."* --
    the Slice 17 1st sub-slice typed-shape module MUST NOT carry any
    consumer-owned artifact-key literals (those would imply the
    typed-shape module is writing consumer authority artifacts, which
    violates doc-17:182-188).

    The (future) Slice 17 4th sub-slice recommendation writer at
    ``review:governance-recommendations:{corpus_id}`` may carry the
    artifact key literal; this 1st sub-slice is pure typed-shape and
    MUST NOT.
    """

    import iriai_build_v2.execution_control.policy_recommendation as mod

    src = open(mod.__file__).read()
    forbidden_consumer_artifact_keys = (
        '"dag-regroup-active:',
        '"route-budget:',
        '"supervisor-action:',
        '"merge-queue:',
    )
    for f in forbidden_consumer_artifact_keys:
        assert f not in src, (
            f"policy_recommendation.py 1st sub-slice must not carry "
            f"consumer-owned artifact-key literal {f!r}; per "
            f"doc-17:182-188 the recommendation surface does NOT write "
            f"consumer authority artifacts."
        )
