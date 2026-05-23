from __future__ import annotations

import pytest

from iriai_build_v2.workflows.develop.execution import failure_router as router_module
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FailureObservation,
    FailureRouter,
    IdempotencyConflict,
    build_failure_signature,
    stable_signature_hash,
)


def _observation(**overrides: object) -> FailureObservation:
    data: dict[str, object] = {
        "feature_id": "feature-1",
        "dag_sha256": "dag-sha",
        "group_idx": 1,
        "task_id": "TASK-1",
        "attempt_id": 7,
        "source": "dispatcher",
        "failure_class": "product_defect",
        "failure_type": "semantic_verifier_rejected",
        "severity": "warning",
        "deterministic": False,
        "retryable": True,
        "operator_required": True,
        "evidence_ids": [3, 1, 3],
        "payload": {
            "repo_ids": ["repo"],
            "paths": ["src\\app.py"],
            "content_hash": "payload-sha",
        },
    }
    data.update(overrides)
    return FailureObservation.model_validate(data)


DOCUMENTED_ROUTE_ROWS = {
    ("product_defect", "semantic_verifier_rejected"),
    ("product_defect", "required_path_missing"),
    ("contract_compile", "contract_invalid_path"),
    ("contract_compile", "contract_scope_conflict"),
    ("contract_compile", "contract_missing_dependency"),
    ("contract_compile", "contract_same_wave_dependency"),
    ("contract_violation", "outside_allowed_paths"),
    ("contract_violation", "forbidden_path_touched"),
    ("contract_violation", "read_only_path_touched"),
    ("contract_violation", "contract_id_mismatch"),
    ("stale_projection", "verifier_context_stale"),
    ("stale_projection", "workspace_snapshot_stale"),
    ("worktree_alias", "alias_points_to_noncanonical_root"),
    ("worktree_alias", "alias_only_canonical_missing"),
    ("worktree_alias", "alias_canonical_divergent"),
    ("acl_workability", "unwritable_runtime_path"),
    ("sandbox_allocation", "sandbox_clone_failed"),
    ("sandbox_allocation", "sandbox_disk_quota"),
    ("sandbox_allocation", "sandbox_base_snapshot_unavailable"),
    ("sandbox_binding", "runtime_workspace_binding_failed"),
    ("sandbox_isolation", "canonical_path_exposed_to_writer"),
    ("sandbox_isolation", "path_escape_detected"),
    ("sandbox_capture", "patch_capture_failed"),
    ("sandbox_capture", "sandbox_index_corrupt"),
    ("sandbox_cleanup", "cleanup_failed"),
    ("commit_hygiene", "commit_hook_failed"),
    ("commit_hygiene", "dirty_after_commit"),
    ("merge_conflict", "stale_base_commit"),
    ("merge_conflict", "rebase_conflict"),
    ("merge_conflict", "patch_apply_conflict"),
    ("runtime_provider", "provider_internal_error"),
    ("runtime_provider", "provider_rate_limited"),
    ("runtime_provider", "provider_transport_error"),
    ("runtime_provider", "process_failed"),
    ("runtime_timeout", "watchdog_timeout"),
    ("runtime_cancelled", "runtime_cancelled"),
    ("runtime_context", "prompt_too_large"),
    ("runtime_context", "context_materialization_failed"),
    ("runtime_context", "context_permission_denied"),
    ("runtime_structured_output", "malformed_structured_output"),
    ("dispatcher_internal", "idempotency_conflict"),
    ("verifier_provider", "verifier_provider_timeout"),
    ("verifier_provider", "verifier_provider_crash"),
    ("verifier_provider", "verifier_parse_failed"),
    ("verifier_context", "context_materialization_failed"),
    ("verifier_context", "verifier_context_stale"),
    ("checkpoint_contradiction", "checkpoint_after_failed_gate"),
    ("regroup_invalid", "regroup_dependency_cycle"),
    ("regroup_invalid", "regroup_write_conflict"),
    ("evidence_corruption", "artifact_hash_mismatch"),
    ("evidence_corruption", "payload_digest_mismatch"),
    ("evidence_corruption", "projection_body_conflict"),
    ("resource_exhausted", "db_resource_exhausted"),
    ("resource_exhausted", "disk_resource_exhausted"),
    ("resource_exhausted", "process_resource_exhausted"),
    ("resource_exhausted", "provider_quota_exhausted"),
    ("resource_exhausted", "unclassified"),
    ("operator_required", "operator_clearance_required"),
    ("unknown", "unclassified"),
}


def test_taxonomy_and_route_table_cover_documented_slice_producers() -> None:
    assert set(router_module.FAILURE_CLASSES) >= {failure_class for failure_class, _ in DOCUMENTED_ROUTE_ROWS}
    assert set(router_module.FAILURE_TYPES) >= {failure_type for _, failure_type in DOCUMENTED_ROUTE_ROWS}
    assert {
        "retry_dispatch",
        "run_product_repair",
        "run_contract_repair",
        "run_canonicalization_repair",
        "run_workspace_repair",
        "run_commit_hygiene_repair",
        "retry_verifier",
        "retry_merge",
        "retry_sandbox_capture",
        "run_sandbox_cleanup",
        "quiesce",
        "operator_required",
    } <= set(router_module.ROUTE_ACTIONS)
    assert DOCUMENTED_ROUTE_ROWS <= set(router_module.ROUTE_TABLE)
    assert DOCUMENTED_ROUTE_ROWS <= set(router_module.FAILURE_TYPE_POLICIES)


def test_signature_is_stable_and_omits_volatile_runtime_fields() -> None:
    left = _observation(
        evidence_ids=[9, 3, 9],
        payload={
            "paths": ["src\\b.py", "./src/a.py"],
            "contract_ids": [22, 11],
            "timestamp": "2026-05-21T12:00:00Z",
            "pid": 123,
            "retry_ordinal": 4,
            "stdout": "large changing body",
            "stderr": "another changing body",
            "provider_error_code": "provider_500",
        },
    )
    right = _observation(
        evidence_ids=[3, 9],
        payload={
            "paths": ["src/a.py", "src/b.py"],
            "contract_ids": [11, 22],
            "timestamp": "2026-05-21T12:01:00Z",
            "pid": 456,
            "retry_ordinal": 5,
            "stdout": "different body",
            "stderr": "different error body",
            "provider_error_code": "provider_500",
        },
    )

    assert stable_signature_hash(left) == stable_signature_hash(right)
    assert build_failure_signature(left)["payload"]["paths"] == ["src/a.py", "src/b.py"]
    assert "stdout" not in build_failure_signature(left)["payload"]

    changed_provider_code = right.model_copy(
        update={"payload": {**right.payload, "provider_error_code": "provider_429"}}
    )
    assert stable_signature_hash(left) != stable_signature_hash(changed_provider_code)


def test_signature_and_budget_are_stable_across_retry_attempts() -> None:
    router = FailureRouter()
    first_id = router.record(
        _observation(
            attempt_id=1,
            payload={"paths": ["src/app.py"], "provider_error_code": "provider_500"},
        )
    )
    second_id = router.record(
        _observation(
            attempt_id=2,
            payload={"paths": ["src/app.py"], "provider_error_code": "provider_500"},
        )
    )

    first_record = router.get_failure(first_id)
    second_record = router.get_failure(second_id)

    assert first_id != second_id
    assert first_record.signature_hash == second_record.signature_hash
    assert first_record.idempotency_key != second_record.idempotency_key

    first_decision = router.mark_route_started(router.decide(first_id))
    second_decision = router.decide(second_id)

    assert first_decision.budget_remaining == 1
    assert second_decision.budget_key == first_decision.budget_key
    assert second_decision.budget_remaining == 1
    assert second_decision.reservation_ordinal == 2


def test_policy_overrides_producer_booleans_on_record() -> None:
    router = FailureRouter()
    failure_id = router.record(
        _observation(
            deterministic=True,
            retryable=False,
            operator_required=True,
            severity="fatal",
        )
    )

    record = router.get_failure(failure_id)
    assert record.observation.deterministic is False
    assert record.observation.retryable is True
    assert record.observation.operator_required is False
    assert record.observation.severity == "error"


def test_product_repair_is_limited_to_product_defects_and_scoped_contract_violations() -> None:
    product_routes = [
        route
        for route in router_module.ROUTE_TABLE.values()
        if route.action == "run_product_repair"
    ]

    assert product_routes
    for route in product_routes:
        assert route.failure_class in {"product_defect", "contract_violation"}
        if route.failure_class == "contract_violation":
            assert route.failure_type in {
                "outside_allowed_paths",
                "forbidden_path_touched",
                "read_only_path_touched",
            }
            assert route.requires_scoped_product_repair is True

    workflow_classes = {
        "commit_hygiene",
        "worktree_alias",
        "acl_workability",
        "stale_projection",
        "sandbox_isolation",
        "sandbox_binding",
        "checkpoint_contradiction",
        "runtime_provider",
        "verifier_provider",
        "unknown",
    }
    assert all(
        route.action != "run_product_repair"
        for key, route in router_module.ROUTE_TABLE.items()
        if key[0] in workflow_classes
    )


@pytest.mark.parametrize(
    ("failure_class", "failure_type", "expected_action"),
    [
        ("commit_hygiene", "commit_hook_failed", "run_commit_hygiene_repair"),
        ("worktree_alias", "alias_canonical_divergent", "run_canonicalization_repair"),
        ("acl_workability", "unwritable_runtime_path", "run_workspace_repair"),
        ("stale_projection", "verifier_context_stale", "retry_verifier"),
        ("stale_projection", "workspace_snapshot_stale", "run_workspace_repair"),
        ("sandbox_isolation", "path_escape_detected", "quiesce"),
        ("checkpoint_contradiction", "checkpoint_after_failed_gate", "quiesce"),
        ("runtime_provider", "provider_transport_error", "retry_dispatch"),
        ("verifier_provider", "verifier_provider_timeout", "retry_verifier"),
        ("unknown", "unclassified", "quiesce"),
    ],
)
def test_workflow_and_provider_failures_do_not_route_to_product_repair(
    failure_class: str,
    failure_type: str,
    expected_action: str,
) -> None:
    router = FailureRouter()
    failure_id = router.record(
        _observation(
            failure_class=failure_class,
            failure_type=failure_type,
            payload={"paths": ["src/product.py"], "contract_ids": [11]},
        )
    )

    decision = router.decide(failure_id)
    assert decision.action == expected_action
    assert decision.action != "run_product_repair"


def test_contract_violation_product_repair_requires_path_and_contract_scope() -> None:
    router = FailureRouter()
    scoped_failure_id = router.record(
        _observation(
            failure_class="contract_violation",
            failure_type="outside_allowed_paths",
            source="contract",
            payload={"paths": ["src\\product.py"], "contract_ids": [11]},
        )
    )
    unscoped_failure_id = router.record(
        _observation(
            failure_class="contract_violation",
            failure_type="outside_allowed_paths",
            source="contract",
            attempt_id=8,
            payload={"paths": [], "contract_ids": [11]},
        )
    )

    scoped = router.decide(scoped_failure_id)
    unscoped = router.decide(unscoped_failure_id)

    assert scoped.action == "run_product_repair"
    assert scoped.repair_scope["target_paths"] == ["src/product.py"]
    assert scoped.repair_scope["target_contract_ids"] == [11]
    assert unscoped.action == "quiesce"
    assert unscoped.budget_remaining == 0
    assert scoped.repair_scope["dag_sha256"] == "dag-sha"
    assert scoped.repair_scope["group_idx"] == 1
    assert scoped.repair_scope["task_id"] == "TASK-1"


def test_contract_violation_source_verdict_requires_authorized_direct_route() -> None:
    router = FailureRouter()
    missing_legacy_id = router.record(
        _observation(
            failure_class="contract_violation",
            failure_type="forbidden_path_touched",
            source="contract",
            payload={
                "paths": ["src/product.py"],
                "source_verdict_key": "dag-verify:g1:retry-0",
            },
        )
    )
    wrong_group_id = router.record(
        _observation(
            attempt_id=8,
            failure_class="contract_violation",
            failure_type="forbidden_path_touched",
            source="contract",
            payload={
                "paths": ["src/product.py"],
                "legacy_route": "manifest_forbidden_product_cleanup",
                "source_verdict_key": "dag-verify:g2:retry-0",
            },
        )
    )
    missing_group_id = router.record(
        _observation(
            attempt_id=10,
            group_idx=None,
            failure_class="contract_violation",
            failure_type="forbidden_path_touched",
            source="contract",
            payload={
                "paths": ["src/product.py"],
                "legacy_route": "manifest_forbidden_product_cleanup",
                "source_verdict_key": "dag-verify:g1:retry-0",
            },
        )
    )
    authorized_id = router.record(
        _observation(
            attempt_id=9,
            failure_class="contract_violation",
            failure_type="forbidden_path_touched",
            source="contract",
            payload={
                "paths": ["src/product.py"],
                "legacy_route": "manifest_forbidden_product_cleanup",
                "source_verdict_key": "dag-verify:g1:retry-0",
            },
        )
    )

    missing_legacy = router.decide(missing_legacy_id)
    wrong_group = router.decide(wrong_group_id)
    missing_group = router.decide(missing_group_id)
    authorized = router.decide(authorized_id)

    assert missing_legacy.action == "quiesce"
    assert wrong_group.action == "quiesce"
    assert missing_group.action == "quiesce"
    assert authorized.action == "run_product_repair"
    assert authorized.repair_scope["source"] == "contract"
    assert authorized.repair_scope["source_verdict_key"] == "dag-verify:g1:retry-0"
    assert authorized.repair_scope["legacy_route"] == "manifest_forbidden_product_cleanup"


def test_product_defect_product_repair_requires_typed_evidence_and_target_paths() -> None:
    router = FailureRouter()
    no_evidence_id = router.record(
        _observation(
            evidence_ids=[],
            payload={"paths": ["src/product.py"]},
        )
    )
    no_target_id = router.record(
        _observation(
            attempt_id=8,
            evidence_ids=[44],
            payload={"paths": []},
        )
    )

    no_evidence = router.decide(no_evidence_id)
    no_target = router.decide(no_target_id)

    assert no_evidence.action == "quiesce"
    assert no_evidence.budget_remaining == 0
    assert no_target.action == "quiesce"
    assert no_target.budget_remaining == 0


def test_decide_is_side_effect_free_and_route_start_reserves_once() -> None:
    router = FailureRouter()
    failure_id = router.record(_observation())

    first = router.decide(failure_id)
    second = router.decide(failure_id)

    assert first == second
    assert first.route_decision_id is None
    assert first.budget_remaining == 2
    assert router.port.get_budget(first.budget_key) is None

    started = router.mark_route_started(first)
    replayed = router.mark_route_started(first)
    budget = router.port.get_budget(first.budget_key)

    assert started.route_decision_id == replayed.route_decision_id
    assert started.started is True
    assert started.budget_remaining == 1
    assert budget is not None
    assert budget.reserved_attempts == 1

    after_reserve = router.decide(failure_id)
    assert after_reserve.budget_remaining == 1
    assert after_reserve.reservation_ordinal == 2

    second_failure_id = router.record(_observation(attempt_id=8))
    second_started = router.mark_route_started(router.decide(second_failure_id))
    budget = router.port.get_budget(first.budget_key)

    assert second_started.action == "run_product_repair"
    assert second_started.budget_remaining == 0
    assert budget is not None
    assert budget.reserved_attempts == 2

    exhausted_failure_id = router.record(_observation(attempt_id=9))
    exhausted = router.decide(exhausted_failure_id)
    exhausted_started = router.mark_route_started(exhausted)
    exhausted_replayed = router.mark_route_started(exhausted)

    assert exhausted.action == "quiesce"
    assert exhausted.budget_exhausted is True
    assert exhausted.budget_remaining == 0
    assert exhausted_started.action == "quiesce"
    assert exhausted_started.budget_exhausted is True
    assert exhausted_replayed.route_decision_id == exhausted_started.route_decision_id


def test_stale_route_start_refreshes_reservation_identity_after_budget_moves() -> None:
    router = FailureRouter()
    failure_id = router.record(_observation())
    stale_decision = router.decide(failure_id)
    record = router.get_failure(failure_id)
    route = router_module.ROUTE_TABLE[
        (record.observation.failure_class, record.observation.failure_type)
    ]

    router.port.reserve_budget(
        budget_key=stale_decision.budget_key,
        feature_id=record.observation.feature_id,
        failure_class=record.observation.failure_class,
        failure_type=record.observation.failure_type,
        signature_hash=record.signature_hash,
        max_attempts=route.budget,
        failure_id=failure_id,
    )

    started = router.mark_route_started(stale_decision)
    replayed = router.mark_route_started(stale_decision)

    assert started.started is True
    assert started.reservation_ordinal == 2
    assert started.idempotency_key.endswith(":n2")
    assert started.idempotency_key != stale_decision.idempotency_key
    assert started.budget_remaining == 0
    assert replayed.route_decision_id == started.route_decision_id
    assert router.port.get_route_by_key(started.idempotency_key) is not None


def test_same_failure_signature_returns_existing_row() -> None:
    router = FailureRouter()
    first_id = router.record(
        _observation(
            evidence_ids=[5, 2],
            payload={"paths": ["src\\a.py", "src/b.py"], "contract_ids": [22, 11]},
        )
    )
    second_id = router.record(
        _observation(
            evidence_ids=[2, 5],
            payload={"paths": ["src/b.py", "./src/a.py"], "contract_ids": [11, 22]},
        )
    )

    record = router.get_failure(first_id)
    assert second_id == first_id
    assert record.occurrence_count == 2


def test_same_explicit_idempotency_key_with_different_digest_conflicts() -> None:
    router = FailureRouter()
    payload = {
        "idempotency_key": "failure:manual-conflict",
        "paths": ["src/a.py"],
    }
    router.record(_observation(payload=payload))

    with pytest.raises(IdempotencyConflict) as exc:
        router.record(
            _observation(
                attempt_id=8,
                payload={
                    "idempotency_key": "failure:manual-conflict",
                    "paths": ["src/b.py"],
                },
            )
        )

    assert exc.value.idempotency_key == "failure:manual-conflict"


def test_every_failure_class_has_a_retry_budget() -> None:
    classes = {failure_class for failure_class, _ in router_module.ROUTE_TABLE}
    missing = classes - set(router_module.CLASS_RETRY_BUDGETS)
    assert not missing, f"failure classes missing a retry budget: {sorted(missing)}"


@pytest.mark.parametrize(
    ("failure_class", "failure_type", "budget"),
    [
        ("commit_hygiene", "commit_hook_failed", 1),
        ("worktree_alias", "alias_canonical_divergent", 1),
        ("sandbox_cleanup", "cleanup_failed", 3),
    ],
)
def test_budget_exhaustion_routes_to_quiesce_per_class(
    failure_class: str, failure_type: str, budget: int
) -> None:
    assert router_module.CLASS_RETRY_BUDGETS[failure_class] == budget
    router = FailureRouter()
    base_action = router_module.ROUTE_TABLE[(failure_class, failure_type)].action

    # Consume the full per-class budget with same-signature failures.
    for attempt in range(budget):
        failure_id = router.record(
            _observation(
                failure_class=failure_class,
                failure_type=failure_type,
                attempt_id=100 + attempt,
            )
        )
        started = router.mark_route_started(router.decide(failure_id))
        assert started.action == base_action
        assert started.budget_exhausted is False

    # The next same-signature failure has no budget left and quiesces.
    overflow_id = router.record(
        _observation(
            failure_class=failure_class,
            failure_type=failure_type,
            attempt_id=999,
        )
    )
    overflow = router.decide(overflow_id)
    assert overflow.action == "quiesce"
    assert overflow.budget_exhausted is True
    assert overflow.budget_remaining == 0


def test_zero_budget_class_cannot_start_a_repair() -> None:
    assert router_module.CLASS_RETRY_BUDGETS["sandbox_isolation"] == 0
    router = FailureRouter()
    failure_id = router.record(
        _observation(
            failure_class="sandbox_isolation",
            failure_type="path_escape_detected",
        )
    )

    decision = router.decide(failure_id)
    started = router.mark_route_started(decision)

    assert decision.action == "quiesce"
    assert decision.budget_remaining == 0
    assert started.action == "quiesce"


def test_route_reservation_is_idempotent_across_resume() -> None:
    # The port is the durable boundary; a fresh router simulates a restart.
    port = router_module.InMemoryFailureRouterPort()
    router = FailureRouter(port=port)
    failure_id = router.record(_observation())
    started = router.mark_route_started(router.decide(failure_id))
    reserved_after_start = port.get_budget(started.budget_key).reserved_attempts

    # Crash/restart: a new router over the same durable port replays the
    # persisted started decision. It must resume the same decision and must
    # not spend a second budget slot.
    resumed = FailureRouter(port=port)
    replayed = resumed.mark_route_started(started)

    assert replayed.route_decision_id == started.route_decision_id
    assert replayed.idempotency_key == started.idempotency_key
    assert port.get_budget(started.budget_key).reserved_attempts == reserved_after_start == 1

    # A fresh decide() on resume sees the durable reservation, not a reset.
    after_resume = resumed.decide(failure_id)
    assert after_resume.budget_remaining == 1
    assert after_resume.reservation_ordinal == 2


def test_mark_route_finished_records_outcome_and_is_idempotent() -> None:
    port = router_module.InMemoryFailureRouterPort()
    router = FailureRouter(port=port)
    failure_id = router.record(_observation())
    started = router.mark_route_started(router.decide(failure_id))

    router.mark_route_finished(started, succeeded=False, produced_failure_id=4242)

    record = port.routes[started.route_decision_id]
    assert record.status == "failed"
    assert record.produced_failure_id == 4242
    assert port.get_budget(started.budget_key).completed_attempts == 1

    # Idempotent replay: a second finish does not double-count completion.
    router.mark_route_finished(started, succeeded=False, produced_failure_id=4242)
    assert port.get_budget(started.budget_key).completed_attempts == 1
    assert port.routes[started.route_decision_id].status == "failed"

    # A decision that was never started is a safe no-op.
    unstarted = router.decide(router.record(_observation(attempt_id=21)))
    assert unstarted.route_decision_id is None
    router.mark_route_finished(unstarted, succeeded=True)


def test_repair_outcome_links_a_divergent_child_failure_to_the_route() -> None:
    port = router_module.InMemoryFailureRouterPort()
    router = FailureRouter(port=port)
    parent_id = router.record(_observation())
    started = router.mark_route_started(router.decide(parent_id))

    # A repair produces a new failure with a different signature.
    child_id = router.record(
        _observation(
            attempt_id=31,
            failure_type="required_path_missing",
            payload={"paths": ["src/other.py"], "content_hash": "child-sha"},
        )
    )
    assert child_id != parent_id

    router.mark_route_finished(started, succeeded=False, produced_failure_id=child_id)

    record = port.routes[started.route_decision_id]
    assert record.produced_failure_id == child_id
    child = router.get_failure(child_id)
    assert child.observation.failure_type == "required_path_missing"
    assert child.signature_hash != router.get_failure(parent_id).signature_hash
