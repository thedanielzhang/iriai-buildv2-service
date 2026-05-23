import pytest

from iriai_build_v2.workflows.develop.execution.failure_router import (
    FailureObservation,
    FailureRouter,
)
from iriai_build_v2.workflows.develop.execution.repair import (
    RepairRequest,
    RetryRequest,
    RouteDecision,
    RouteExecutor,
    RouteExecutorError,
)


def _scope(**overrides):
    scope = {
        "feature_id": "feat-07",
        "dag_sha256": "dag-sha",
        "group_idx": 3,
        "task_id": "TASK-1",
        "failure_class": "product_defect",
        "failure_type": "semantic_verifier_rejected",
        "repo_ids": ["app"],
        "target_paths": ["src/app.py"],
        "contract_ids": [101],
        "gate_ids": ["gate:semantic"],
        "route_decision_evidence_ids": [9001],
        "non_goals": ["do not widen scope"],
    }
    scope.update(overrides)
    return scope


def _decision(
    action: str,
    *,
    route_decision_id: int | None = 77,
    required_evidence_ids: list[int] | None = None,
    **scope_overrides,
):
    return RouteDecision(
        failure_id=17,
        route_decision_id=route_decision_id,
        action=action,
        budget_remaining=1,
        reason=f"{action} selected",
        required_evidence_ids=[501, 502] if required_evidence_ids is None else required_evidence_ids,
        signature_hash="sig-hash",
        idempotency_key="route:idem",
        repair_scope=_scope(**scope_overrides),
    )


@pytest.mark.parametrize(
    ("action", "scope_overrides", "expected"),
    [
        (
            "run_product_repair",
            {},
            {
                "repair_kind": "product",
                "allowed_mutations": ["sandbox_product_patch"],
                "sandbox_mode": "repair",
                "enqueue_strategy": "merge_queue",
            },
        ),
        (
            "run_contract_repair",
            {
                "failure_class": "contract_compile",
                "failure_type": "contract_invalid_path",
                "target_paths": [],
            },
            {
                "repair_kind": "contract",
                "allowed_mutations": ["contract_recompile"],
                "sandbox_mode": "none",
                "enqueue_strategy": "metadata_only",
            },
        ),
        (
            "run_canonicalization_repair",
            {
                "failure_class": "worktree_alias",
                "failure_type": "alias_canonical_divergent",
                "canonicalization_mode": "product_content",
            },
            {
                "repair_kind": "canonicalization",
                "allowed_mutations": ["sandbox_product_patch"],
                "sandbox_mode": "canonicalization",
                "enqueue_strategy": "merge_queue",
            },
        ),
        (
            "run_workspace_repair",
            {
                "failure_class": "acl_workability",
                "failure_type": "unwritable_runtime_path",
                "target_paths": [],
                "workspace_repair_mode": "acl",
            },
            {
                "repair_kind": "workspace",
                "allowed_mutations": ["workspace_acl"],
                "sandbox_mode": "none",
                "enqueue_strategy": "metadata_only",
            },
        ),
        (
            "run_commit_hygiene_repair",
            {
                "failure_class": "commit_hygiene",
                "failure_type": "commit_hook_failed",
                "staged_paths": ["src/app.py"],
                "hook_evidence_ids": [601],
                "status_evidence_ids": [602],
                "no_dirty_proof_evidence_ids": [603],
            },
            {
                "repair_kind": "commit_hygiene",
                "allowed_mutations": ["commit_hygiene_patch"],
                "sandbox_mode": "repair",
                "enqueue_strategy": "merge_queue",
            },
        ),
    ],
)
def test_every_repairable_action_builds_deterministic_repair_request(
    action,
    scope_overrides,
    expected,
):
    executor = RouteExecutor()
    decision = _decision(action, **scope_overrides)

    first = executor.build_repair_request(decision)
    second = executor.build_route_request(decision)

    assert isinstance(first, RepairRequest)
    assert isinstance(second, RepairRequest)
    assert first == second
    assert first.action == action
    assert first.repair_kind == expected["repair_kind"]
    assert first.allowed_mutations == expected["allowed_mutations"]
    assert first.sandbox_mode == expected["sandbox_mode"]
    assert first.enqueue_strategy == expected["enqueue_strategy"]
    assert first.required_evidence_ids[:2] == [501, 502]
    assert 9001 in first.required_evidence_ids
    assert first.target_repo_ids == ["app"]
    assert first.target_contract_ids == [101]
    assert "gate:semantic" in first.required_gate_ids
    assert first.budget_key == "route-budget:feat-07:%s:%s:sig-hash" % (
        decision.repair_scope["failure_class"],
        decision.repair_scope["failure_type"],
    )
    assert first.idempotency_key.startswith("idem:repair-request:")
    assert len(first.input_digest) == 64


@pytest.mark.parametrize(
    ("action", "scope_overrides", "expected"),
    [
        (
            "retry_dispatch",
            {
                "failure_class": "sandbox_allocation",
                "failure_type": "sandbox_clone_failed",
                "contract_ids": [201],
                "gate_ids": ["gate:contract"],
                "sandbox_lease_id": 301,
            },
            {
                "retry_kind": "dispatch",
                "attempt_kind": "task",
                "allocate_new_sandbox": True,
                "preserve_sandbox_lease_id": 301,
                "preserve_merge_queue_item_id": None,
            },
        ),
        (
            "retry_verifier",
            {
                "failure_class": "verifier_context",
                "failure_type": "verifier_context_stale",
                "contract_ids": [202],
                "gate_ids": ["gate:verify"],
            },
            {
                "retry_kind": "verifier",
                "attempt_kind": "verify",
                "allocate_new_sandbox": False,
                "reset_context": True,
                "preserve_merge_queue_item_id": None,
            },
        ),
        (
            "retry_merge",
            {
                "failure_class": "merge_conflict",
                "failure_type": "rebase_conflict",
                "contract_ids": [203],
                "gate_ids": ["gate:merge"],
                "failed_merge_queue_item_id": 404,
                "failed_source_queue_item_evidence_id": 405,
                "source_queue_item_status": "failed",
            },
            {
                "retry_kind": "merge",
                "attempt_kind": "merge",
                "allocate_new_sandbox": False,
                "preserve_merge_queue_item_id": 404,
            },
        ),
        (
            "retry_sandbox_capture",
            {
                "failure_class": "sandbox_capture",
                "failure_type": "patch_capture_failed",
                "contract_ids": [204],
                "gate_ids": ["gate:capture"],
                "retained_sandbox_lease_id": 505,
            },
            {
                "retry_kind": "sandbox_capture",
                "attempt_kind": "repair",
                "allocate_new_sandbox": False,
                "preserve_sandbox_lease_id": 505,
                "preserve_merge_queue_item_id": None,
            },
        ),
        (
            "run_sandbox_cleanup",
            {
                "failure_class": "sandbox_cleanup",
                "failure_type": "cleanup_failed",
                "contract_ids": [205],
                "gate_ids": ["gate:cleanup"],
                "sandbox_lease_id": 606,
            },
            {
                "retry_kind": "sandbox_cleanup",
                "attempt_kind": "repair",
                "allocate_new_sandbox": False,
                "preserve_sandbox_lease_id": 606,
                "preserve_merge_queue_item_id": None,
            },
        ),
    ],
)
def test_every_retry_action_builds_retry_request_preserving_lineage(
    action,
    scope_overrides,
    expected,
):
    executor = RouteExecutor()
    decision = _decision(action, **scope_overrides)

    request = executor.build_route_request(decision)

    assert isinstance(request, RetryRequest)
    assert request.action == action
    assert request.retry_kind == expected["retry_kind"]
    assert request.attempt_kind == expected["attempt_kind"]
    assert request.preserve_contract_ids == scope_overrides["contract_ids"]
    assert request.preserve_gate_ids == scope_overrides["gate_ids"]
    assert request.preserve_merge_queue_item_id == expected["preserve_merge_queue_item_id"]
    if "preserve_sandbox_lease_id" in expected:
        assert request.preserve_sandbox_lease_id == expected["preserve_sandbox_lease_id"]
    assert request.allocate_new_sandbox is expected["allocate_new_sandbox"]
    if "reset_context" in expected:
        assert request.reset_context is expected["reset_context"]
    assert request.required_evidence_ids[:2] == [501, 502]
    assert 9001 in request.required_evidence_ids
    assert request.idempotency_key.startswith("idem:retry-request:")
    assert len(request.input_digest) == 64


def test_unstarted_decision_cannot_build_request():
    with pytest.raises(RouteExecutorError, match="started/reserved"):
        RouteExecutor().build_route_request(
            _decision("run_product_repair", route_decision_id=None)
        )


def test_product_repair_rejects_workflow_failure_classes():
    # A routed workflow (class, type) whose route-table action is not
    # run_product_repair is rejected by the route-table consistency guard.
    with pytest.raises(RouteExecutorError, match="contradicts route table"):
        RouteExecutor().build_repair_request(
            _decision(
                "run_product_repair",
                failure_class="commit_hygiene",
                failure_type="dirty_after_commit",
            )
        )
    # A workflow class with an unrouted type passes the route-table guard but
    # is still rejected by the _product_repair failure-class allowlist.
    with pytest.raises(RouteExecutorError, match="product repair is only allowed"):
        RouteExecutor().build_repair_request(
            _decision(
                "run_product_repair",
                failure_class="commit_hygiene",
                failure_type="unrouted_workflow_type",
            )
        )


def test_scoped_contract_violation_may_build_product_repair():
    request = RouteExecutor().build_repair_request(
        _decision(
            "run_product_repair",
            failure_class="contract_violation",
            failure_type="outside_allowed_paths",
            contract_ids=[77],
            target_paths=["src/scoped.py"],
            offending_paths=["src/scoped.py"],
        )
    )

    assert request.repair_kind == "product"
    assert request.target_contract_ids == [77]
    assert request.target_paths == ["src/scoped.py"]
    assert "do not broaden contracts" in request.prompt_constraints


def test_direct_route_source_verdict_scopes_contract_product_repair_on_resume():
    request = RouteExecutor().build_repair_request(
        _decision(
            "run_product_repair",
            required_evidence_ids=[],
            failure_class="contract_violation",
            failure_type="forbidden_path_touched",
            contract_ids=[],
            target_contract_ids=[],
            target_paths=["src/generated/forbidden.ts"],
            offending_paths=["src/generated/forbidden.ts"],
            route_decision_evidence_ids=[],
            source_verdict_key="dag-verify:g31:retry-0",
            legacy_route="manifest_forbidden_product_cleanup",
            group_idx=31,
            source="contract",
        )
    )

    assert request.repair_kind == "product"
    assert request.target_paths == ["src/generated/forbidden.ts"]
    assert request.target_contract_ids == []
    assert request.required_evidence_ids == []
    assert request.source_verdict_key == "dag-verify:g31:retry-0"


def test_direct_route_source_verdict_requires_legacy_route_authority():
    with pytest.raises(RouteExecutorError, match="source verdict key|fixed contracts"):
        RouteExecutor().build_repair_request(
            _decision(
                "run_product_repair",
                required_evidence_ids=[],
                failure_class="contract_violation",
                failure_type="forbidden_path_touched",
                contract_ids=[],
                target_contract_ids=[],
                target_paths=["src/generated/forbidden.ts"],
                route_decision_evidence_ids=[],
                source_verdict_key="dag-verify:g31:retry-0",
                legacy_route="commit_hygiene_focused",
                group_idx=31,
            )
        )
    with pytest.raises(RouteExecutorError, match="source verdict key|fixed contracts"):
        RouteExecutor().build_repair_request(
            _decision(
                "run_product_repair",
                required_evidence_ids=[],
                failure_class="contract_violation",
                failure_type="forbidden_path_touched",
                contract_ids=[],
                target_contract_ids=[],
                target_paths=["src/generated/forbidden.ts"],
                route_decision_evidence_ids=[],
                source_verdict_key="dag-verify:g31:retry-0",
                legacy_route="manifest_forbidden_product_cleanup",
                group_idx=None,
                source="contract",
            )
        )
    with pytest.raises(RouteExecutorError, match="source verdict key|fixed contracts"):
        RouteExecutor().build_repair_request(
            _decision(
                "run_product_repair",
                required_evidence_ids=[],
                failure_class="contract_violation",
                failure_type="forbidden_path_touched",
                contract_ids=[],
                target_contract_ids=[],
                target_paths=["src/generated/forbidden.ts"],
                route_decision_evidence_ids=[],
                source_verdict_key="dag-verify:g31:retry-0",
                legacy_route="manifest_forbidden_product_cleanup",
                group_idx=31,
            )
        )
    with pytest.raises(RouteExecutorError, match="source verdict key"):
        RouteExecutor().build_repair_request(
            _decision(
                "run_product_repair",
                required_evidence_ids=[],
                failure_class="product_defect",
                failure_type="semantic_verifier_rejected",
                target_paths=["src/generated/forbidden.ts"],
                route_decision_evidence_ids=[],
                source_verdict_key="dag-verify:g31:retry-0",
                legacy_route="manifest_forbidden_product_cleanup",
                group_idx=31,
                source="contract",
            )
        )
    with pytest.raises(
        RouteExecutorError,
        match="scoped contract violation type|source verdict key|contradicts route table",
    ):
        RouteExecutor().build_repair_request(
            _decision(
                "run_product_repair",
                required_evidence_ids=[],
                failure_class="contract_violation",
                failure_type="contract_id_mismatch",
                contract_ids=[],
                target_contract_ids=[],
                target_paths=["src/generated/forbidden.ts"],
                route_decision_evidence_ids=[],
                source_verdict_key="dag-verify:g31:retry-0",
                legacy_route="manifest_forbidden_product_cleanup",
                group_idx=31,
                source="contract",
            )
        )


def test_contract_repair_rejects_widening_or_root_dag_edits():
    with pytest.raises(RouteExecutorError, match="contract repair cannot"):
        RouteExecutor().build_repair_request(
            _decision(
                "run_contract_repair",
                failure_class="contract_compile",
                failure_type="contract_invalid_path",
                widen_contracts=True,
            )
        )


def test_workspace_repair_rejects_product_file_mutation():
    with pytest.raises(RouteExecutorError, match="workspace repair cannot"):
        RouteExecutor().build_repair_request(
            _decision(
                "run_workspace_repair",
                failure_class="acl_workability",
                failure_type="unwritable_runtime_path",
                product_paths=["src/app.py"],
            )
        )


@pytest.mark.parametrize(
    "bad_path",
    ["/tmp/outside.py", "../outside.py", "C:/tmp/outside.py", ".", "./", "~/outside.py"],
)
def test_repair_request_rejects_unsafe_target_paths(bad_path):
    with pytest.raises(RouteExecutorError, match="unsafe repair target path"):
        RouteExecutor().build_repair_request(
            _decision(
                "run_product_repair",
                target_paths=[bad_path],
                route_decision_evidence_ids=[9001],
            )
        )


def test_metadata_only_canonicalization_uses_projection_refresh_without_sandbox():
    request = RouteExecutor().build_repair_request(
        _decision(
            "run_canonicalization_repair",
            failure_class="worktree_alias",
            failure_type="alias_points_to_noncanonical_root",
            target_paths=[],
            paths=[],
        )
    )

    assert request.repair_kind == "canonicalization"
    assert request.allowed_mutations == ["projection_refresh"]
    assert request.sandbox_mode == "none"
    assert request.enqueue_strategy == "metadata_only"
    assert "do not mutate product files" in request.prompt_constraints


def test_retry_actions_do_not_build_repair_requests():
    with pytest.raises(RouteExecutorError, match="RepairRequest"):
        RouteExecutor().build_repair_request(
            _decision("retry_dispatch", failure_class="runtime_provider")
        )


def test_retry_request_rejects_scope_drift_from_route_table():
    # A retry_dispatch decision whose scope claims a sandbox_isolation failure
    # (route table action: quiesce) was reconstructed inconsistently and must
    # not rerun dispatch for a path-escape failure.
    with pytest.raises(RouteExecutorError, match="contradicts route table"):
        RouteExecutor().build_retry_request(
            _decision(
                "retry_dispatch",
                failure_class="sandbox_isolation",
                failure_type="path_escape_detected",
            )
        )


def test_retry_merge_rejects_missing_failed_source_queue_item_evidence():
    with pytest.raises(RouteExecutorError, match="failed source queue item evidence"):
        RouteExecutor().build_retry_request(
            _decision(
                "retry_merge",
                failure_class="merge_conflict",
                failure_type="patch_apply_conflict",
                failed_merge_queue_item_id=808,
            )
        )


def test_retry_merge_preserves_failed_source_queue_item_id_when_evidence_present():
    request = RouteExecutor().build_retry_request(
        _decision(
            "retry_merge",
            failure_class="merge_conflict",
            failure_type="patch_apply_conflict",
            failed_merge_queue_item_id=808,
            failed_source_queue_item_evidence_id=809,
            contract_ids=[901],
            gate_ids=["gate:merge"],
        )
    )

    assert request.preserve_merge_queue_item_id == 808
    assert 809 in request.required_evidence_ids
    assert request.preserve_contract_ids == [901]
    assert request.preserve_gate_ids == ["gate:merge"]


def test_request_digest_is_stable_from_route_decision_and_repair_scope():
    executor = RouteExecutor()
    decision = _decision("run_product_repair")

    first = executor.build_route_request(decision)
    second = executor.build_route_request(decision)
    changed = executor.build_route_request(
        _decision("run_product_repair", target_paths=["src/other.py"])
    )

    assert first.idempotency_key == second.idempotency_key
    assert first.input_digest == second.input_digest
    assert first.input_digest != changed.input_digest


def test_started_router_decision_builds_route_request_with_lineage():
    router = FailureRouter()
    failure_id = router.record(
        FailureObservation(
            feature_id="feat-router-exec",
            dag_sha256="dag-router",
            group_idx=4,
            task_id="TASK-7",
            attempt_id=9,
            source="verification_graph",
            failure_class="product_defect",
            failure_type="semantic_verifier_rejected",
            deterministic=False,
            retryable=True,
            evidence_ids=[77, 76],
            payload={
                "repo_ids": ["app"],
                "paths": ["src\\bug.py"],
                "contract_ids": [909],
                "gate_ids": ["gate:raw"],
            },
        )
    )
    decision = router.mark_route_started(router.decide(failure_id))

    request = RouteExecutor().build_route_request(decision)

    assert isinstance(request, RepairRequest)
    assert request.feature_id == "feat-router-exec"
    assert request.dag_sha256 == "dag-router"
    assert request.group_idx == 4
    assert request.task_id == "TASK-7"
    assert request.route_decision_id == decision.route_decision_id
    assert request.failure_id == failure_id
    assert request.target_repo_ids == ["app"]
    assert request.target_paths == ["src/bug.py"]
    assert request.target_contract_ids == [909]
    assert request.required_gate_ids == ["gate:raw"]
    assert request.required_evidence_ids == [76, 77]


def test_router_commit_hygiene_decision_preserves_request_lineage():
    router = FailureRouter()
    failure_id = router.record(
        FailureObservation(
            feature_id="feat-commit-route",
            dag_sha256="dag-commit",
            group_idx=5,
            source="merge_queue",
            failure_class="commit_hygiene",
            failure_type="commit_hook_failed",
            deterministic=True,
            retryable=True,
            evidence_ids=[601, 602, 603],
            payload={
                "paths": ["src/app.py"],
                "hook_evidence_ids": [601],
                "status_evidence_ids": [602],
                "no_dirty_proof_evidence_ids": [603],
            },
        )
    )
    decision = router.mark_route_started(router.decide(failure_id))

    request = RouteExecutor().build_route_request(decision)

    assert isinstance(request, RepairRequest)
    assert request.repair_kind == "commit_hygiene"
    assert request.target_paths == ["src/app.py"]
    assert request.required_evidence_ids == [601, 602, 603]


def test_router_merge_retry_decision_preserves_failed_queue_lineage():
    router = FailureRouter()
    failure_id = router.record(
        FailureObservation(
            feature_id="feat-merge-route",
            dag_sha256="dag-merge",
            group_idx=6,
            source="merge_queue",
            failure_class="merge_conflict",
            failure_type="patch_apply_conflict",
            deterministic=False,
            retryable=True,
            evidence_ids=[809],
            payload={
                "queue_item_id": 808,
                "failed_source_queue_item_evidence_id": 809,
                "source_queue_item_status": "failed",
                "contract_ids": [901],
                "gate_ids": ["gate:merge"],
            },
        )
    )
    decision = router.mark_route_started(router.decide(failure_id))

    request = RouteExecutor().build_route_request(decision)

    assert isinstance(request, RetryRequest)
    assert request.retry_kind == "merge"
    assert request.preserve_merge_queue_item_id == 808
    assert request.required_evidence_ids == [809]
    assert request.preserve_contract_ids == [901]
    assert request.preserve_gate_ids == ["gate:merge"]
