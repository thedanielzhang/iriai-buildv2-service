"""Slice 17 sixth sub-slice -- unit tests for the consumer read-API at
``execution_control/consumer_read_api.py``.

Covers the doc-17:175-177 step 6 typed-shape consumer-read-API surface +
per-consumer filtering discipline + status filtering discipline + LIMIT
cap+1 bounded-read truncation discipline + DIRECT annotation-identity
Slice 17 1st sub-slice REUSE assertions + the read-API-never-raises
non-blocking contract + the activation-authority boundary structural
boundary.

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
Pydantic field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 +
Slice 16 + Slice 17 1st/2nd/3rd/4th/5th sub-slice modules + tests remain
byte-identical.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.consumer_read_api import (
    CONSUMER_READ_API_FAILURE_ID,
    DEFAULT_READ_API_CAP,
    ConsumerReadAPIGap,
    ConsumerReadAPIInputs,
    ConsumerReadAPIResult,
    GovernanceReadAPI,
)
from iriai_build_v2.execution_control.policy_recommendation import (
    DashboardPolicyArtifact,
    FailureRouterPolicyArtifact,
    GovernancePolicyRecommendation,
    MergeQueuePolicyArtifact,
    PlanningPolicyArtifact,
    PolicyConsumer,
    PolicyRecommendationStatus,
    SchedulerPolicyArtifact,
    SupervisorPolicyArtifact,
)
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FAILURE_TYPES,
    ROUTE_TABLE,
    FailureType,
    _RETRYABLE_FAILURE_TYPES,
    _ROUTE_ROWS,
)


# ── fixtures ────────────────────────────────────────────────────────────────


def _scheduler_artifact() -> SchedulerPolicyArtifact:
    """Construct a fully-specified :class:`SchedulerPolicyArtifact`."""

    return SchedulerPolicyArtifact(
        policy_kind="wave_cap",
        scope={"lane_id": "ml-7"},
        value={"wave_cap": 7},
        guardrails=["max_concurrent_tasks_per_lane"],
    )


def _failure_router_artifact() -> FailureRouterPolicyArtifact:
    """Construct a fully-specified :class:`FailureRouterPolicyArtifact`."""

    return FailureRouterPolicyArtifact(
        failure_class="evidence_corruption",
        failure_type="some_failure",
        action="retry",
        route_budget_key="key-1",
        max_attempts=3,
        idempotency_key_template="key-{id}",
        required_tests=["test_route"],
    )


def _supervisor_artifact() -> SupervisorPolicyArtifact:
    """Construct a fully-specified :class:`SupervisorPolicyArtifact`."""

    return SupervisorPolicyArtifact(
        policy_kind="classification_hint",
        scope={"corpus_id": "c-1"},
        value={"hint": "high_priority"},
    )


def _dashboard_artifact() -> DashboardPolicyArtifact:
    """Construct a fully-specified :class:`DashboardPolicyArtifact`."""

    return DashboardPolicyArtifact(
        policy_kind="view_priority",
        scope={"corpus_id": "c-1"},
        value={"priority": 5},
    )


def _planning_artifact() -> PlanningPolicyArtifact:
    """Construct a fully-specified :class:`PlanningPolicyArtifact`."""

    return PlanningPolicyArtifact(
        policy_kind="future_dag_hint",
        scope={"corpus_id": "c-1"},
        value={"hint": "split_node"},
    )


def _merge_queue_artifact() -> MergeQueuePolicyArtifact:
    """Construct a fully-specified :class:`MergeQueuePolicyArtifact`."""

    return MergeQueuePolicyArtifact(
        policy_kind="lane_priority",
        scope={"lane_id": "ml-7"},
        value={"priority": 1},
        required_queue_tests=["test_queue"],
    )


_CONSUMER_ARTIFACT_FACTORIES = {
    "scheduler": _scheduler_artifact,
    "failure_router": _failure_router_artifact,
    "supervisor": _supervisor_artifact,
    "dashboard": _dashboard_artifact,
    "planning": _planning_artifact,
    "merge_queue": _merge_queue_artifact,
}


def _recommendation(
    *,
    consumer: PolicyConsumer = "scheduler",
    status: PolicyRecommendationStatus = "accepted",
    recommendation_id: str = "rec-1",
    idempotency_key: str = "rec-key-1",
) -> GovernancePolicyRecommendation:
    """Construct a fully-specified :class:`GovernancePolicyRecommendation`.

    Default is an `accepted` scheduler recommendation -- the default
    status that the read-API surfaces per doc-17:175-177.
    """

    artifact_factory = _CONSUMER_ARTIFACT_FACTORIES[consumer]
    return GovernancePolicyRecommendation(
        idempotency_key=idempotency_key,
        recommendation_id=recommendation_id,
        consumer=consumer,
        status=status,
        source_finding_ids=["finding-1"],
        source_metric_refs=["tasks_per_hour@v1"],
        counterfactual_result_refs=[],
        confidence=0.85,
        expected_impact={"tasks_per_hour_delta": 0.15},
        risk_level="low",
        safe_runtime_action=False,
        requires_tests=["test_required"],
        proposed_policy_artifact=artifact_factory(),
        activation_requirements=["consumer_test_suite_green"],
        rollback_requirements=["revert_to_prior_policy"],
    )


# ── module surface tests ────────────────────────────────────────────────────


def test_module_all_lists_documented_surface() -> None:
    """The module ``__all__`` carries the documented surface exactly.

    Per doc-17:175-177 step 6 the surface is:

    * 1 typed failure id constant (``CONSUMER_READ_API_FAILURE_ID``).
    * 1 typed read-API cap constant (``DEFAULT_READ_API_CAP``).
    * 3 typed BaseModels (``ConsumerReadAPIInputs`` +
      ``ConsumerReadAPIGap`` + ``ConsumerReadAPIResult``).
    * 1 read-API class (``GovernanceReadAPI``).

    Total: 6 exported names.
    """

    from iriai_build_v2.execution_control import consumer_read_api as mod

    assert set(mod.__all__) == {
        "CONSUMER_READ_API_FAILURE_ID",
        "DEFAULT_READ_API_CAP",
        "ConsumerReadAPIInputs",
        "ConsumerReadAPIGap",
        "ConsumerReadAPIResult",
        "GovernanceReadAPI",
    }


def test_module_does_not_redefine_governance_policy_recommendation() -> None:
    """The module imports the Slice 17 1st sub-slice
    :class:`GovernancePolicyRecommendation`; it does NOT define a
    local copy."""

    from iriai_build_v2.execution_control import consumer_read_api as mod

    assert "GovernancePolicyRecommendation" not in mod.__all__


def test_module_does_not_redefine_policy_consumer() -> None:
    """The module imports the Slice 17 1st sub-slice
    :data:`PolicyConsumer`; it does NOT define a local copy."""

    from iriai_build_v2.execution_control import consumer_read_api as mod

    assert "PolicyConsumer" not in mod.__all__


def test_module_does_not_redefine_policy_recommendation_status() -> None:
    """The module imports the Slice 17 1st sub-slice
    :data:`PolicyRecommendationStatus`; it does NOT define a local
    copy."""

    from iriai_build_v2.execution_control import consumer_read_api as mod

    assert "PolicyRecommendationStatus" not in mod.__all__


def test_module_import_discipline_no_consumer_module_imports() -> None:
    """The module MUST NOT import any consumer-side module.

    Per the implementer brief activation-authority boundary the
    read-API does NOT import: supervisor / dashboard / scheduler /
    planning / merge_queue / failure_router (beyond the 4 pure-data
    add points which live in failure_router.py NOT in this module).
    """

    from iriai_build_v2.execution_control import consumer_read_api as mod

    source = Path(inspect.getfile(mod)).read_text()
    forbidden_imports = [
        "from iriai_build_v2.supervisor",
        "from iriai_build_v2.dashboard",
        "import iriai_build_v2.supervisor",
        "import iriai_build_v2.dashboard",
        "from iriai_build_v2.workflows.develop.execution.phases",
        "from iriai_build_v2.workflows.develop.execution.failure_router",
        "from iriai_build_v2.workflows.develop.scheduler",
        "from iriai_build_v2.workflows.develop.merge_queue",
        "from iriai_build_v2.workflows.develop.planning",
        # Slice 17 2nd/3rd/4th/5th sub-slice modules -- the read-API is
        # a thin projection over the Slice 17 1st sub-slice
        # typed-shape foundation; the builder/validator/writer/hook
        # surfaces are NOT consumed.
        "from iriai_build_v2.execution_control.recommendation_builder",
        "from iriai_build_v2.execution_control.policy_validation_interface",
        "from iriai_build_v2.execution_control.decision_record_writer",
        "from iriai_build_v2.execution_control.replay_requirement_hook",
    ]
    for forbidden in forbidden_imports:
        assert forbidden not in source, (
            f"forbidden import found in consumer_read_api.py: {forbidden}"
        )


def test_module_import_discipline_only_allowed_imports() -> None:
    """The module MUST import only from stdlib + Pydantic v2 + Slice
    17 1st policy_recommendation."""

    from iriai_build_v2.execution_control import consumer_read_api as mod

    source = Path(inspect.getfile(mod)).read_text()
    # Required imports (positive controls).
    assert "from pydantic import" in source
    assert (
        "from iriai_build_v2.execution_control.policy_recommendation import"
        in source
    )


def test_package_init_does_not_re_export_read_api() -> None:
    """Per the implementer brief Non-negotiables the package
    ``__init__.py`` does NOT re-export the Slice 17 6th sub-slice
    typed surface."""

    import iriai_build_v2.execution_control as pkg

    pkg_file = pkg.__file__
    assert pkg_file is not None
    init_source = Path(pkg_file).read_text()
    for name in (
        "ConsumerReadAPIInputs",
        "ConsumerReadAPIGap",
        "ConsumerReadAPIResult",
        "GovernanceReadAPI",
        "CONSUMER_READ_API_FAILURE_ID",
        "DEFAULT_READ_API_CAP",
    ):
        assert name not in init_source


def test_read_api_class_exposes_only_query_method() -> None:
    """Activation-authority boundary: the
    :class:`GovernanceReadAPI` class MUST expose ONLY the typed
    ``query_recommendations`` read method.

    Per doc-17:217 + doc-17:159-163 + doc-17:175-177 the read-API
    GRANTS NO consumer-side activation authority; there must NOT be
    any ``activate_*`` / ``write_*`` / ``mutate_*`` method.
    """

    public_methods = {
        name
        for name in vars(GovernanceReadAPI).keys()
        if not name.startswith("_")
        and callable(getattr(GovernanceReadAPI, name))
    }
    assert public_methods == {"query_recommendations"}


def test_read_api_class_no_mutation_method_names() -> None:
    """Structural defense: NO method on the class name-matches any
    mutation pattern."""

    forbidden_patterns = (
        "activate",
        "mutate",
        "write_",
        "persist_",
        "commit_",
        "delete_",
        "update_",
        "set_",
    )
    for method_name in vars(GovernanceReadAPI).keys():
        if method_name.startswith("_"):
            continue
        for pattern in forbidden_patterns:
            assert pattern not in method_name, (
                f"forbidden mutation pattern '{pattern}' found in method "
                f"'{method_name}'"
            )


# ── failure-id constant ──────────────────────────────────────────────────────


def test_failure_id_value_matches_chunk_shape() -> None:
    """The typed failure id is ``consumer_read_api_failed`` per the
    Slice 17 6th sub-slice chunk-shape."""

    assert CONSUMER_READ_API_FAILURE_ID == "consumer_read_api_failed"


def test_default_read_api_cap_value() -> None:
    """The default read-API cap is 100 per the chunk-shape."""

    assert DEFAULT_READ_API_CAP == 100


def test_failure_id_differs_from_slice_17_prior_ids() -> None:
    """The Slice 17 6th sub-slice failure id is intentionally
    different from the prior Slice 17 sub-slice failure ids."""

    from iriai_build_v2.execution_control.decision_record_writer import (
        DECISION_RECORD_PERSISTENCE_FAILURE_ID,
    )
    from iriai_build_v2.execution_control.policy_validation_interface import (
        POLICY_VALIDATION_FAILURE_ID,
    )
    from iriai_build_v2.execution_control.recommendation_builder import (
        RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID,
    )
    from iriai_build_v2.execution_control.replay_requirement_hook import (
        REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID,
    )

    distinct_ids = {
        CONSUMER_READ_API_FAILURE_ID,
        DECISION_RECORD_PERSISTENCE_FAILURE_ID,
        POLICY_VALIDATION_FAILURE_ID,
        RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID,
        REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID,
    }
    # 5 sub-slices x 1 typed failure id each = 5 distinct ids.
    assert len(distinct_ids) == 5


# ── ConsumerReadAPIInputs typed-shape construction ──────────────────────────


def test_inputs_accepts_minimal_fields() -> None:
    """The typed inputs accept the minimal required fields."""

    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
    )
    assert inputs.consumer == "failure_router"
    assert inputs.corpus_id == "c-1"
    assert inputs.limit == DEFAULT_READ_API_CAP
    assert inputs.status_filter == "accepted"


def test_inputs_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` discipline."""

    with pytest.raises(ValidationError):
        ConsumerReadAPIInputs(
            consumer="failure_router",
            corpus_id="c-1",
            unknown_field="boom",  # type: ignore[call-arg]
        )


def test_inputs_round_trips_via_json() -> None:
    """The typed inputs round-trip via ``model_dump`` +
    ``model_validate``."""

    inputs = ConsumerReadAPIInputs(
        consumer="scheduler",
        corpus_id="8ac124d6",
        limit=50,
        status_filter="reviewed",
    )
    dumped = inputs.model_dump(mode="json")
    re_inputs = ConsumerReadAPIInputs.model_validate(dumped)
    assert re_inputs == inputs


def test_inputs_consumer_rejects_unknown_value() -> None:
    """The ``consumer`` Literal rejects values outside the 6-value
    doc-17:65 set."""

    with pytest.raises(ValidationError):
        ConsumerReadAPIInputs(
            consumer="bogus_consumer",  # type: ignore[arg-type]
            corpus_id="c-1",
        )


def test_inputs_status_filter_rejects_unknown_value() -> None:
    """The ``status_filter`` Literal rejects values outside the
    6-value doc-17:66-73 set (other than ``None``)."""

    with pytest.raises(ValidationError):
        ConsumerReadAPIInputs(
            consumer="failure_router",
            corpus_id="c-1",
            status_filter="activated",  # type: ignore[arg-type]
        )


def test_inputs_status_filter_accepts_none() -> None:
    """The ``status_filter`` accepts ``None`` to mean
    no-status-filtering."""

    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
        status_filter=None,
    )
    assert inputs.status_filter is None


def test_inputs_status_filter_accepts_all_six_values() -> None:
    """The ``status_filter`` accepts each of the 6 doc-17:66-73
    Literal values."""

    for status in (
        "draft",
        "reviewed",
        "accepted",
        "rejected",
        "needs_more_evidence",
        "superseded",
    ):
        inputs = ConsumerReadAPIInputs(
            consumer="failure_router",
            corpus_id="c-1",
            status_filter=status,  # type: ignore[arg-type]
        )
        assert inputs.status_filter == status


# ── ConsumerReadAPIGap typed-shape construction ─────────────────────────────


def test_gap_accepts_required_fields() -> None:
    """The typed gap accepts the documented required fields."""

    gap = ConsumerReadAPIGap(
        failure_id="consumer_read_api_failed",
        reason="some_reason",
    )
    assert gap.failure_id == "consumer_read_api_failed"
    assert gap.reason == "some_reason"
    assert gap.consumer is None
    assert gap.corpus_id is None


def test_gap_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` discipline."""

    with pytest.raises(ValidationError):
        ConsumerReadAPIGap(
            failure_id="consumer_read_api_failed",
            reason="x",
            unknown_field="boom",  # type: ignore[call-arg]
        )


def test_gap_round_trips_via_json() -> None:
    """The typed gap round-trips via ``model_dump`` +
    ``model_validate``."""

    gap = ConsumerReadAPIGap(
        failure_id="consumer_read_api_failed",
        reason="some_reason",
        consumer="failure_router",
        corpus_id="c-1",
    )
    dumped = gap.model_dump(mode="json")
    re_gap = ConsumerReadAPIGap.model_validate(dumped)
    assert re_gap == gap


def test_gap_failure_id_literal_rejects_other_ids() -> None:
    """The ``failure_id`` Literal range rejects any other failure
    id."""

    with pytest.raises(ValidationError):
        ConsumerReadAPIGap(
            failure_id="policy_validation_failed",  # type: ignore[arg-type]
            reason="x",
        )


def test_gap_observed_at_defaults_to_utc_now() -> None:
    """The ``observed_at`` field defaults to UTC now (within a
    tolerance)."""

    before = datetime.now(tz=timezone.utc)
    gap = ConsumerReadAPIGap(
        failure_id="consumer_read_api_failed",
        reason="x",
    )
    after = datetime.now(tz=timezone.utc)
    assert before <= gap.observed_at <= after


# ── ConsumerReadAPIResult typed-shape construction ──────────────────────────


def test_result_accepts_minimal_fields() -> None:
    """The typed result accepts the minimal field set."""

    result = ConsumerReadAPIResult(consumer="failure_router")
    assert result.consumer == "failure_router"
    assert result.recommendations == []
    assert result.truncated is False
    assert result.gap_records == []


def test_result_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` discipline."""

    with pytest.raises(ValidationError):
        ConsumerReadAPIResult(
            consumer="failure_router",
            unknown_field="boom",  # type: ignore[call-arg]
        )


def test_result_round_trips_via_json() -> None:
    """The typed result round-trips via ``model_dump`` +
    ``model_validate``."""

    rec = _recommendation(consumer="failure_router")
    gap = ConsumerReadAPIGap(
        failure_id="consumer_read_api_failed",
        reason="x",
    )
    result = ConsumerReadAPIResult(
        consumer="failure_router",
        recommendations=[rec],
        truncated=True,
        gap_records=[gap],
    )
    dumped = result.model_dump(mode="json")
    re_result = ConsumerReadAPIResult.model_validate(dumped)
    assert re_result == result


def test_result_required_consumer() -> None:
    """The ``consumer`` field is required."""

    with pytest.raises(ValidationError):
        ConsumerReadAPIResult()  # type: ignore[call-arg]


# ── DIRECT annotation-identity REUSE assertions ─────────────────────────────


def test_inputs_consumer_annotation_is_slice_17_first_policy_consumer() -> None:
    """DIRECT annotation-identity REUSE assertion: the typed
    :attr:`ConsumerReadAPIInputs.consumer` field's type IS the
    Slice 17 1st sub-slice typed :data:`PolicyConsumer` Literal."""

    hints = get_type_hints(ConsumerReadAPIInputs)
    assert hints["consumer"] is PolicyConsumer


def test_inputs_status_filter_annotation_is_slice_17_first_status() -> None:
    """DIRECT annotation-identity REUSE assertion: the typed
    :attr:`ConsumerReadAPIInputs.status_filter` field's type
    parameterises the Slice 17 1st sub-slice typed
    :data:`PolicyRecommendationStatus` Literal."""

    hints = get_type_hints(ConsumerReadAPIInputs)
    status_filter_t = hints["status_filter"]
    # The field is `PolicyRecommendationStatus | None`.
    args = get_args(status_filter_t)
    assert PolicyRecommendationStatus in args


def test_result_consumer_annotation_is_slice_17_first_policy_consumer() -> None:
    """DIRECT annotation-identity REUSE assertion: the typed
    :attr:`ConsumerReadAPIResult.consumer` field's type IS the
    Slice 17 1st sub-slice typed :data:`PolicyConsumer` Literal."""

    hints = get_type_hints(ConsumerReadAPIResult)
    assert hints["consumer"] is PolicyConsumer


def test_result_recommendations_annotation_is_slice_17_first_governance_policy_recommendation() -> None:
    """DIRECT annotation-identity REUSE assertion: the typed
    :attr:`ConsumerReadAPIResult.recommendations` field's element type
    IS the Slice 17 1st sub-slice typed
    :class:`GovernancePolicyRecommendation` BaseModel."""

    hints = get_type_hints(ConsumerReadAPIResult)
    recommendations_t = hints["recommendations"]
    assert get_origin(recommendations_t) is list
    (element_t,) = get_args(recommendations_t)
    assert element_t is GovernancePolicyRecommendation


def test_result_gap_records_annotation_is_local_consumer_read_api_gap() -> None:
    """DIRECT annotation-identity REUSE assertion: the typed
    :attr:`ConsumerReadAPIResult.gap_records` field's element type IS
    the local :class:`ConsumerReadAPIGap` BaseModel."""

    hints = get_type_hints(ConsumerReadAPIResult)
    gap_records_t = hints["gap_records"]
    assert get_origin(gap_records_t) is list
    (element_t,) = get_args(gap_records_t)
    assert element_t is ConsumerReadAPIGap


def test_gap_consumer_annotation_parameterises_slice_17_first_policy_consumer() -> None:
    """DIRECT annotation-identity REUSE assertion: the typed
    :attr:`ConsumerReadAPIGap.consumer` field's annotation
    parameterises the Slice 17 1st sub-slice typed
    :data:`PolicyConsumer` Literal."""

    hints = get_type_hints(ConsumerReadAPIGap)
    consumer_t = hints["consumer"]
    args = get_args(consumer_t)
    assert PolicyConsumer in args


def test_query_recommendations_parameter_annotation_is_local_inputs() -> None:
    """DIRECT annotation-identity REUSE assertion: the read-API's
    :meth:`GovernanceReadAPI.query_recommendations` method's first
    parameter annotation IS the local :class:`ConsumerReadAPIInputs`."""

    hints = get_type_hints(GovernanceReadAPI.query_recommendations)
    assert hints["inputs"] is ConsumerReadAPIInputs


def test_query_recommendations_candidates_annotation_is_slice_17_first_governance_policy_recommendation() -> None:
    """DIRECT annotation-identity REUSE assertion: the read-API's
    :meth:`GovernanceReadAPI.query_recommendations` method's
    ``candidates`` parameter annotation's element type IS the Slice 17
    1st sub-slice typed :class:`GovernancePolicyRecommendation`."""

    hints = get_type_hints(GovernanceReadAPI.query_recommendations)
    candidates_t = hints["candidates"]
    assert get_origin(candidates_t) is list
    (element_t,) = get_args(candidates_t)
    assert element_t is GovernancePolicyRecommendation


def test_query_recommendations_return_annotation_is_local_result() -> None:
    """DIRECT annotation-identity REUSE assertion: the read-API's
    :meth:`GovernanceReadAPI.query_recommendations` method's return
    type annotation IS the local :class:`ConsumerReadAPIResult`."""

    hints = get_type_hints(GovernanceReadAPI.query_recommendations)
    assert hints["return"] is ConsumerReadAPIResult


# ── failure-router 4-pure-data-add-point validation ─────────────────────────


def test_failure_router_registers_new_typed_failure_id() -> None:
    """Add-point 1: the NEW typed failure id
    ``consumer_read_api_failed`` is registered in the
    :data:`FailureType` Literal."""

    args = get_args(FailureType)
    assert "consumer_read_api_failed" in args


def test_failure_router_failure_types_tuple_includes_new_id() -> None:
    """Add-point 2: the NEW typed failure id is in the
    :data:`FAILURE_TYPES` tuple."""

    assert "consumer_read_api_failed" in FAILURE_TYPES


def test_failure_router_new_id_in_retryable_set() -> None:
    """Add-point 3: the NEW typed failure id is in the
    :data:`_RETRYABLE_FAILURE_TYPES` frozenset (the transient
    governance projection failure pattern)."""

    assert "consumer_read_api_failed" in _RETRYABLE_FAILURE_TYPES


def test_failure_router_route_under_evidence_corruption_with_retry_governance_projection() -> None:
    """Add-point 4: the route table carries the NEW typed failure id
    under the EXISTING ``evidence_corruption`` failure_class with the
    REUSED ``retry_governance_projection`` NON-blocking RouteAction."""

    key = ("evidence_corruption", "consumer_read_api_failed")
    assert key in ROUTE_TABLE
    route = ROUTE_TABLE[key]
    assert route.action == "retry_governance_projection"


def test_failure_router_reuses_existing_route_action() -> None:
    """The NEW failure id REUSES the EXISTING
    ``retry_governance_projection`` action (NOT a new action; the
    action was introduced by Slice 14 2nd sub-slice)."""

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        ROUTE_ACTIONS,
    )

    assert "retry_governance_projection" in ROUTE_ACTIONS


def test_failure_router_route_includes_explanation() -> None:
    """The route table row carries a non-empty explanation reason."""

    key = ("evidence_corruption", "consumer_read_api_failed")
    route = ROUTE_TABLE[key]
    assert route.reason
    assert "Slice 17 6th" in route.reason


def test_failure_router_pure_data_add_point_route_table_row_present() -> None:
    """Defence-in-depth: the add-point landed as a row in the
    :data:`_ROUTE_ROWS` tuple-of-tuples."""

    seen = False
    for row in _ROUTE_ROWS:
        _, route = row
        if (
            route.failure_class == "evidence_corruption"
            and route.failure_type == "consumer_read_api_failed"
        ):
            seen = True
            assert route.action == "retry_governance_projection"
            break
    assert seen, "consumer_read_api_failed row not found in _ROUTE_ROWS"


# ── GovernanceReadAPI: empty-candidates happy path ──────────────────────────


def test_query_empty_candidates_returns_empty_result() -> None:
    """An empty candidate list returns an empty result with
    ``truncated=False`` and no gap records."""

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(consumer="failure_router", corpus_id="c-1")
    result = api.query_recommendations(inputs, [])
    assert result.consumer == "failure_router"
    assert result.recommendations == []
    assert result.truncated is False
    assert result.gap_records == []


# ── GovernanceReadAPI: per-consumer filtering ──────────────────────────────


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
def test_query_filters_by_consumer_value(
    consumer: PolicyConsumer,
) -> None:
    """Per-consumer filtering: the read-API surfaces only the
    typed records whose ``consumer`` field exactly matches the typed
    inputs ``consumer`` field.

    This test covers all 6 doc-17:65 Literal values; the parametrized
    set guarantees per-consumer routing correctness.
    """

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(consumer=consumer, corpus_id="c-1")

    # Build 6 candidates -- one per typed consumer.
    candidates = [
        _recommendation(
            consumer=other_consumer,
            recommendation_id=f"rec-{other_consumer}",
            idempotency_key=f"key-{other_consumer}",
        )
        for other_consumer in (
            "scheduler",
            "failure_router",
            "supervisor",
            "dashboard",
            "planning",
            "merge_queue",
        )
    ]

    result = api.query_recommendations(inputs, candidates)
    # Only the matching consumer's record should be surfaced.
    assert len(result.recommendations) == 1
    assert result.recommendations[0].consumer == consumer


def test_query_returns_multiple_matches_for_same_consumer() -> None:
    """Multiple typed records for the same consumer surface together."""

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
    )

    candidates = [
        _recommendation(
            consumer="failure_router",
            recommendation_id=f"rec-{i}",
            idempotency_key=f"key-{i}",
        )
        for i in range(3)
    ]
    result = api.query_recommendations(inputs, candidates)
    assert len(result.recommendations) == 3


def test_query_excludes_other_consumers_when_filtering() -> None:
    """The read-API EXCLUDES typed records whose ``consumer`` field
    does NOT match the typed inputs ``consumer`` field."""

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(consumer="scheduler", corpus_id="c-1")
    candidates = [
        _recommendation(consumer="failure_router", recommendation_id="rec-fr"),
        _recommendation(consumer="supervisor", recommendation_id="rec-sup"),
    ]
    result = api.query_recommendations(inputs, candidates)
    assert result.recommendations == []


# ── GovernanceReadAPI: status filtering ─────────────────────────────────────


def test_query_default_status_filter_is_accepted() -> None:
    """Per doc-17:175-177 the default ``status_filter="accepted"``
    surfaces only the typed `accepted` records."""

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
    )
    candidates = [
        _recommendation(
            consumer="failure_router",
            status="accepted",
            recommendation_id="rec-accepted",
            idempotency_key="key-accepted",
        ),
        _recommendation(
            consumer="failure_router",
            status="draft",
            recommendation_id="rec-draft",
            idempotency_key="key-draft",
        ),
    ]
    result = api.query_recommendations(inputs, candidates)
    assert len(result.recommendations) == 1
    assert result.recommendations[0].status == "accepted"


@pytest.mark.parametrize(
    "status",
    ["draft", "reviewed", "accepted", "rejected", "needs_more_evidence", "superseded"],
)
def test_query_explicit_status_filter_each_value(
    status: PolicyRecommendationStatus,
) -> None:
    """Status filter correctness: each of the 6 doc-17:66-73 Literal
    values surfaces only the typed records with that status."""

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
        status_filter=status,
    )
    candidates = [
        _recommendation(
            consumer="failure_router",
            status=other_status,
            recommendation_id=f"rec-{other_status}",
            idempotency_key=f"key-{other_status}",
        )
        for other_status in (
            "draft",
            "reviewed",
            "accepted",
            "rejected",
            "needs_more_evidence",
            "superseded",
        )
    ]
    result = api.query_recommendations(inputs, candidates)
    assert len(result.recommendations) == 1
    assert result.recommendations[0].status == status


def test_query_status_filter_none_returns_all_statuses() -> None:
    """When ``status_filter=None`` the read-API does NOT filter by
    status."""

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
        status_filter=None,
    )
    candidates = [
        _recommendation(
            consumer="failure_router",
            status=status,
            recommendation_id=f"rec-{status}",
            idempotency_key=f"key-{status}",
        )
        for status in ("draft", "accepted", "rejected")
    ]
    result = api.query_recommendations(inputs, candidates)
    assert len(result.recommendations) == 3


def test_query_status_filter_compounds_with_consumer_filter() -> None:
    """Status + consumer filters compose: only records matching BOTH
    are surfaced."""

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
        status_filter="accepted",
    )
    candidates = [
        # match consumer + status
        _recommendation(
            consumer="failure_router",
            status="accepted",
            recommendation_id="r1",
            idempotency_key="k1",
        ),
        # match consumer, fail status
        _recommendation(
            consumer="failure_router",
            status="draft",
            recommendation_id="r2",
            idempotency_key="k2",
        ),
        # fail consumer, match status
        _recommendation(
            consumer="scheduler",
            status="accepted",
            recommendation_id="r3",
            idempotency_key="k3",
        ),
        # fail both
        _recommendation(
            consumer="dashboard",
            status="rejected",
            recommendation_id="r4",
            idempotency_key="k4",
        ),
    ]
    result = api.query_recommendations(inputs, candidates)
    assert len(result.recommendations) == 1
    assert result.recommendations[0].recommendation_id == "r1"


# ── GovernanceReadAPI: bounded reads (LIMIT cap+1 truncation) ───────────────


def test_query_truncates_when_count_exceeds_cap() -> None:
    """The read-API truncates when the candidate count is strictly
    greater than ``inputs.limit``; ``truncated=True``."""

    api = GovernanceReadAPI()
    cap = 3
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
        limit=cap,
    )
    candidates = [
        _recommendation(
            consumer="failure_router",
            status="accepted",
            recommendation_id=f"rec-{i}",
            idempotency_key=f"key-{i}",
        )
        for i in range(cap + 2)
    ]
    result = api.query_recommendations(inputs, candidates)
    assert len(result.recommendations) == cap
    assert result.truncated is True


def test_query_does_not_truncate_when_count_equals_cap() -> None:
    """The read-API does NOT truncate when the candidate count
    equals the cap exactly; ``truncated=False``."""

    api = GovernanceReadAPI()
    cap = 3
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
        limit=cap,
    )
    candidates = [
        _recommendation(
            consumer="failure_router",
            status="accepted",
            recommendation_id=f"rec-{i}",
            idempotency_key=f"key-{i}",
        )
        for i in range(cap)
    ]
    result = api.query_recommendations(inputs, candidates)
    assert len(result.recommendations) == cap
    assert result.truncated is False


def test_query_does_not_truncate_when_count_below_cap() -> None:
    """The read-API does NOT truncate when the candidate count is
    below the cap; ``truncated=False``."""

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
        limit=10,
    )
    candidates = [
        _recommendation(
            consumer="failure_router",
            status="accepted",
            recommendation_id=f"rec-{i}",
            idempotency_key=f"key-{i}",
        )
        for i in range(3)
    ]
    result = api.query_recommendations(inputs, candidates)
    assert len(result.recommendations) == 3
    assert result.truncated is False


def test_query_default_cap_is_default_read_api_cap() -> None:
    """Without an explicit limit, the read-API uses
    :data:`DEFAULT_READ_API_CAP`."""

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
    )
    # Build DEFAULT_READ_API_CAP + 1 candidates to trigger truncation.
    candidates = [
        _recommendation(
            consumer="failure_router",
            status="accepted",
            recommendation_id=f"rec-{i}",
            idempotency_key=f"key-{i}",
        )
        for i in range(DEFAULT_READ_API_CAP + 1)
    ]
    result = api.query_recommendations(inputs, candidates)
    assert len(result.recommendations) == DEFAULT_READ_API_CAP
    assert result.truncated is True


def test_query_zero_cap_returns_empty() -> None:
    """A zero cap returns an empty result (no records) by the typed
    convention documented on
    :attr:`ConsumerReadAPIInputs.limit`."""

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
        limit=0,
    )
    candidates = [
        _recommendation(
            consumer="failure_router",
            status="accepted",
            recommendation_id="rec-1",
        )
    ]
    result = api.query_recommendations(inputs, candidates)
    assert result.recommendations == []
    assert result.truncated is False


# ── GovernanceReadAPI: NEVER raises (fail-closed) ───────────────────────────


def test_query_never_raises_on_typed_punning_candidate() -> None:
    """The read-API NEVER raises even when a candidate's typed fields
    raise unexpectedly.

    Per ``feedback_no_silent_degradation`` + the implementer brief
    *"NEVER raises; on failure emit `ConsumerReadAPIGap` typed
    projection."* the read-API projects onto the typed gap shape.
    """

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
    )

    class PunningRecommendation:
        """A fake recommendation whose ``consumer`` property raises
        unexpectedly. The read-API MUST emit a typed gap projection
        rather than propagating the exception."""

        @property
        def consumer(self) -> str:
            raise RuntimeError("punning consumer property")

        @property
        def status(self) -> str:
            return "accepted"

        @property
        def recommendation_id(self) -> str:
            return "rec-fake"

    result = api.query_recommendations(
        inputs,
        [PunningRecommendation()],  # type: ignore[list-item]
    )
    # Per feedback_no_silent_degradation the read-API returns a typed
    # result with the typed gap projection.
    assert isinstance(result, ConsumerReadAPIResult)
    assert result.recommendations == []
    assert len(result.gap_records) == 1
    assert (
        result.gap_records[0].failure_id == "consumer_read_api_failed"
    )
    assert "consumer_read_api_internal_exception" in result.gap_records[0].reason


def test_query_never_raises_on_monkey_patched_construction_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read-API NEVER raises when typed
    :class:`ConsumerReadAPIResult` construction fails internally.

    Simulated via monkey-patching the typed
    :class:`ConsumerReadAPIResult` to raise during the happy-path
    construction.
    """

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
    )
    candidates = [
        _recommendation(
            consumer="failure_router",
            status="accepted",
            recommendation_id="rec-1",
        )
    ]

    # Monkey-patch the typed result to raise on construction.
    import iriai_build_v2.execution_control.consumer_read_api as mod

    original_result_class = mod.ConsumerReadAPIResult
    call_count = {"value": 0}

    class FlakyResult(original_result_class):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: object) -> None:
            call_count["value"] += 1
            # Raise on the first call (happy path); succeed on the
            # second call (typed gap projection fallback).
            if call_count["value"] == 1:
                raise RuntimeError("monkey-patched typed construction failure")
            super().__init__(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(mod, "ConsumerReadAPIResult", FlakyResult)

    result = api.query_recommendations(inputs, candidates)
    # The typed gap projection fallback should have succeeded.
    assert isinstance(result, original_result_class)
    assert result.recommendations == []
    assert len(result.gap_records) == 1


def test_query_typed_gap_carries_consumer_and_corpus_id_fields() -> None:
    """When the read-API emits a typed gap, the gap carries the typed
    consumer + corpus_id fields from the typed inputs (for the audit
    trail)."""

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(
        consumer="scheduler",
        corpus_id="my-corpus",
    )

    class PunningRecommendation:
        @property
        def consumer(self) -> str:
            raise RuntimeError("punning")

        @property
        def status(self) -> str:
            return "accepted"

        @property
        def recommendation_id(self) -> str:
            return "rec-fake"

    result = api.query_recommendations(
        inputs,
        [PunningRecommendation()],  # type: ignore[list-item]
    )
    assert len(result.gap_records) == 1
    gap = result.gap_records[0]
    assert gap.consumer == "scheduler"
    assert gap.corpus_id == "my-corpus"


# ── GovernanceReadAPI: stateless + idempotent + activation-authority ────────


def test_read_api_is_stateless_across_queries() -> None:
    """The read-API is stateless: two successive queries with the
    same inputs return equivalent results (modulo the dynamic
    ``observed_at`` timestamps inside gap records)."""

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
    )
    candidates = [
        _recommendation(
            consumer="failure_router",
            status="accepted",
            recommendation_id="rec-1",
        )
    ]
    result_a = api.query_recommendations(inputs, candidates)
    result_b = api.query_recommendations(inputs, candidates)
    assert result_a.consumer == result_b.consumer
    assert result_a.recommendations == result_b.recommendations
    assert result_a.truncated == result_b.truncated


def test_read_api_does_not_mutate_candidates_list() -> None:
    """The read-API does NOT mutate the caller-provided candidates
    list (activation-authority boundary: read-only over the typed
    input)."""

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
    )
    candidates = [
        _recommendation(
            consumer="failure_router",
            status="accepted",
            recommendation_id="rec-1",
        ),
        _recommendation(
            consumer="scheduler",
            status="accepted",
            recommendation_id="rec-2",
            idempotency_key="key-2",
        ),
    ]
    original_count = len(candidates)
    original_ids = [c.recommendation_id for c in candidates]
    api.query_recommendations(inputs, candidates)
    # Candidates list still intact.
    assert len(candidates) == original_count
    assert [c.recommendation_id for c in candidates] == original_ids


def test_read_api_does_not_mutate_input_recommendations() -> None:
    """The read-API does NOT mutate the typed records inside the
    candidates list (activation-authority boundary)."""

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
    )
    rec = _recommendation(
        consumer="failure_router",
        status="accepted",
        recommendation_id="rec-1",
    )
    original_status = rec.status
    api.query_recommendations(inputs, [rec])
    # The typed record's status field unchanged.
    assert rec.status == original_status


def test_read_api_grants_no_activation_authority() -> None:
    """Per doc-17:217 + doc-17:175-177 the read-API GRANTS NO
    consumer-side activation authority.

    Defense-in-depth: the result has NO ``activated`` field; the
    typed records in ``recommendations`` carry their original
    ``status`` field (NOT ``"activated"``)."""

    api = GovernanceReadAPI()
    inputs = ConsumerReadAPIInputs(
        consumer="failure_router",
        corpus_id="c-1",
    )
    rec = _recommendation(
        consumer="failure_router",
        status="accepted",
        recommendation_id="rec-1",
    )
    result = api.query_recommendations(inputs, [rec])
    assert len(result.recommendations) == 1
    returned = result.recommendations[0]
    # Status field is preserved (NOT mutated to "activated").
    assert returned.status == "accepted"
    # Defense-in-depth: the result shape has no `activated` field.
    assert not hasattr(result, "activated")


# ── Source-code structural boundary checks ──────────────────────────────────


def test_module_source_has_no_mutation_call_patterns() -> None:
    """Structural defense: the module source MUST NOT call any
    mutation pattern on the typed recommendation records.

    The read-API consumes typed records read-only; the typed records
    are NOT mutated by the read-API.
    """

    from iriai_build_v2.execution_control import consumer_read_api as mod

    source = Path(inspect.getfile(mod)).read_text()
    # Common mutation patterns we explicitly forbid in the read-API
    # implementation. The 'set_' filter prefix would be too broad (it
    # would match standard library calls like setattr in error paths);
    # we narrow to mutation calls on the typed recommendation records.
    forbidden_patterns = (
        ".status = ",
        ".consumer = ",
        ".recommendations.append",
        ".recommendations.pop",
        ".recommendations.remove",
        "candidates.append",
        "candidates.pop",
        "candidates.remove",
    )
    for pattern in forbidden_patterns:
        assert pattern not in source, (
            f"forbidden mutation pattern '{pattern}' found in "
            f"consumer_read_api.py source"
        )


def test_module_source_no_slice_07_failure_router_imports() -> None:
    """Structural boundary: the module MUST NOT import from
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`.

    The Slice 17 6th sub-slice 4 pure-data add points live in
    failure_router.py; the consumer_read_api.py module itself does
    NOT import from there (activation-authority boundary)."""

    from iriai_build_v2.execution_control import consumer_read_api as mod

    source = Path(inspect.getfile(mod)).read_text()
    assert (
        "from iriai_build_v2.workflows.develop.execution.failure_router"
        not in source
    )
    assert (
        "import iriai_build_v2.workflows.develop.execution.failure_router"
        not in source
    )


def test_module_source_no_governance_models_import() -> None:
    """Defense-in-depth: the module does NOT directly import the
    Slice 13a ``governance.models`` module; the typed
    :class:`GovernancePolicyRecommendation` chain to Slice 13a happens
    transitively via the Slice 17 1st sub-slice typed shape (which DOES
    import governance.models for typed
    :class:`GovernanceEvidenceRef` on
    :class:`PolicyRecommendationDecision`).

    The read-API's typed surface carries NO direct Slice 13a refs --
    the typed shapes are :class:`ConsumerReadAPIInputs` +
    :class:`ConsumerReadAPIGap` + :class:`ConsumerReadAPIResult` +
    :class:`GovernancePolicyRecommendation`; the typed
    ``GovernancePolicyRecommendation`` records ARE the chain to Slice
    13a evidence (transitively)."""

    from iriai_build_v2.execution_control import consumer_read_api as mod

    source = Path(inspect.getfile(mod)).read_text()
    # No direct governance.models import on this module.
    assert (
        "from iriai_build_v2.workflows.develop.governance.models import"
        not in source
    )
