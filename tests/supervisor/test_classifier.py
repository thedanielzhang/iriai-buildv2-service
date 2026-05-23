from __future__ import annotations

from datetime import datetime, timezone

import pytest

from iriai_build_v2.supervisor.actions import ActionPolicy
from iriai_build_v2.supervisor.classifier import classify_observation
from iriai_build_v2.supervisor.models import (
    ActionLevel,
    ArtifactRecord,
    BridgeProbe,
    CurrentWorkflowSnapshot,
    EventRecord,
    FailureClass,
    GitPathFact,
    StaleCodexInvocation,
    SupervisorMode,
    SupervisorObservation,
    SupervisorActionStatus,
    WorktreeProbe,
)


def _observation(
    *,
    artifacts: list[ArtifactRecord] | None = None,
    events: list[EventRecord] | None = None,
    bridge: BridgeProbe | None = None,
    current: CurrentWorkflowSnapshot | None = None,
    control_plane_snapshot: dict | None = None,
    worktrees: list[WorktreeProbe] | None = None,
    stale_codex_invocations: list[StaleCodexInvocation] | None = None,
) -> SupervisorObservation:
    return SupervisorObservation(
        feature_id="8ac124d6",
        phase="implementation",
        cursor=100,
        next_cursor=200,
        artifacts=artifacts or [],
        events=events or [],
        bridge=bridge,
        current=current,
        control_plane_snapshot=control_plane_snapshot,
        worktrees=worktrees or [],
        stale_codex_invocations=stale_codex_invocations or [],
    )


def _artifact(key: str, value, artifact_id: int) -> ArtifactRecord:
    return ArtifactRecord(id=artifact_id, key=key, value=value)


def _event(
    event_type: str,
    event_id: int,
    content: str | None = None,
    *,
    metadata: dict | None = None,
    created_at: datetime | None = None,
):
    return EventRecord(
        id=event_id,
        event_type=event_type,
        source="runner",
        content=content,
        metadata=metadata or {},
        created_at=created_at,
    )


def test_g30_stale_derived_state_classifies_as_deterministic_unblock():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-repair-preflight:g30:retry-initial",
                    {
                        "status": "failed",
                        "path_problems": [
                            {
                                "path": "src/legacy/chat/ChatPane.tsx",
                                "reason": "retired path in generated snapshot",
                            }
                        ],
                    },
                    1044830,
                ),
                _artifact(
                    "dag-task-spec-reconcile:g30:retry-initial",
                    "task spec contains stale retired path src/legacy/chat/ChatPane.tsx",
                    1052604,
                ),
            ]
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND
    assert "artifact:dag-repair-preflight:g30:retry-initial id=1044830" in packet.citations
    assert any("Historical/advisory" in check for check in packet.false_positive_checks)
    assert "src/legacy/chat/ChatPane.tsx" in packet.facts["paths"]


def test_worktree_alias_preflight_classifies_as_deterministic_unblock_not_operator():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-worktree-alias-preflight:g48:initial-dispatch",
                    {
                        "approved": False,
                        "operator_required": False,
                        "recommended_action": (
                            "workflow deterministic alias reconcile / focused canonical repair"
                        ),
                        "blockers": [
                            {
                                "task_id": "TASK-7-1",
                                "reason": "worktree_alias_path",
                                "path": "iriai-studio-backend-wt/iriai_studio_backend/"
                                "workflow_worker/messaging/store.py",
                                "canonical_path": "iriai-studio-backend/iriai_studio_backend/"
                                "workflow_worker/messaging/store.py",
                            }
                        ],
                    },
                    3025600,
                )
            ]
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND
    assert "artifact:dag-worktree-alias-preflight:g48:initial-dispatch id=3025600" in packet.citations
    assert packet.classification is not FailureClass.OPERATOR_REQUIRED


def test_operator_required_workspace_control_plane_artifact_stops_escalates():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-workspace-acl-normalization:g48:repair-dispatch-retry-0",
                    {
                        "operator_required": True,
                        "deterministic": True,
                        "reason": "control-plane projection mismatch",
                        "recommended_action": "deterministic workflow unblock",
                    },
                    1678790,
                )
            ],
            worktrees=[
                WorktreeProbe(
                    root="/tmp/repo",
                    forbidden_paths=[
                        GitPathFact(path="legacy/chat/Old.tsx", reason="exists-on-disk")
                    ],
                )
            ],
        )
    )

    assert packet.classification == FailureClass.OPERATOR_REQUIRED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert "artifact:dag-workspace-acl-normalization:g48:repair-dispatch-retry-0 id=1678790" in packet.citations


def test_active_snapshot_does_not_hide_worktree_alias_unblock():
    packet = classify_observation(
        _observation(
            current=CurrentWorkflowSnapshot(
                group_idx=48,
                retry=1,
                state="implementing",
                source="event",
                active_agents=["implementer-g48-fix-0"],
                latest_artifact_id=3025601,
                citations=["event:30256"],
            ),
            artifacts=[
                _artifact(
                    "dag-worktree-alias-preflight:g48:initial-dispatch",
                    {
                        "approved": False,
                        "operator_required": False,
                        "blockers": [
                            {
                                "reason": "worktree_alias_path",
                                "path": "iriai-studio-backend-wt/x.py",
                                "canonical_path": "iriai-studio-backend/x.py",
                            }
                        ],
                    },
                    3025600,
                )
            ],
            events=[
                _event(
                    "agent_invocation_start",
                    30256,
                    "implementer-g48-fix-0",
                    metadata={"group_idx": 48, "retry": 1},
                )
            ],
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND


def test_successful_alias_canonicalization_clears_alias_unblock():
    packet = classify_observation(
        _observation(
            current=CurrentWorkflowSnapshot(
                group_idx=48,
                retry=1,
                state="implementing",
                source="event",
                active_agents=["implementer-g48-t0-a0"],
                latest_artifact_id=3025602,
                citations=["event:30257"],
            ),
            artifacts=[
                _artifact(
                    "dag-worktree-alias-preflight:g48:initial-dispatch",
                    {
                        "approved": False,
                        "operator_required": False,
                        "blockers": [{"reason": "worktree_alias_path"}],
                    },
                    3025600,
                ),
                _artifact(
                    "dag-worktree-alias-canonicalization:g48:retry-1",
                    {
                        "approved": True,
                        "worktree_alias_rewritten_count": 3,
                        "summary": "worktree alias metadata canonicalized",
                    },
                    3025601,
                ),
            ],
            events=[
                _event(
                    "agent_invocation_start",
                    30257,
                    "implementer-g48-t0-a0",
                    metadata={"group_idx": 48, "retry": 1},
                )
            ],
        )
    )

    assert packet.classification == FailureClass.HEALTHY_PROGRESS


def test_later_group_checkpoint_does_not_clear_workflow_blocker():
    packet = classify_observation(
        _observation(
            current=CurrentWorkflowSnapshot(
                group_idx=45,
                retry=0,
                state="implementing",
                source="event",
                active_agents=["implementer-g45-t0-a0"],
                latest_artifact_id=1678801,
                citations=["event:30145"],
            ),
            artifacts=[
                _artifact(
                    "workflow-blocker:g45:retry-0",
                    {
                        "status": "failed",
                        "failure_class": "runtime_context",
                        "failure_type": "context_materialization_failed",
                    },
                    1678800,
                ),
                _artifact("dag-group:45", {"status": "checkpointed"}, 1678801),
            ],
            events=[
                _event(
                    "agent_invocation_start",
                    30145,
                    "implementer-g45-t0-a0",
                    metadata={"group_idx": 45, "retry": 0},
                )
            ],
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert "artifact:workflow-blocker:g45:retry-0 id=1678800" in packet.citations


def test_explicit_workflow_blocker_resolution_clears_unblock():
    packet = classify_observation(
        _observation(
            current=CurrentWorkflowSnapshot(
                group_idx=45,
                retry=0,
                state="implementing",
                source="event",
                active_agents=["implementer-g45-t0-a0"],
                latest_artifact_id=1678802,
                citations=["event:30146"],
            ),
            artifacts=[
                _artifact(
                    "workflow-blocker:g45:retry-0",
                    {
                        "status": "failed",
                        "failure_class": "runtime_context",
                        "failure_type": "context_materialization_failed",
                    },
                    1678800,
                ),
                _artifact(
                    "workflow-blocker:g45:retry-0",
                    {"resolved": True, "blocked": False, "resolution_status": "resolved"},
                    1678802,
                ),
            ],
            events=[
                _event(
                    "agent_invocation_start",
                    30146,
                    "implementer-g45-t0-a0",
                    metadata={"group_idx": 45, "retry": 0},
                )
            ],
        )
    )

    assert packet.classification == FailureClass.HEALTHY_PROGRESS


def test_later_group_checkpoint_does_not_clear_alias_unblock():
    packet = classify_observation(
        _observation(
            current=CurrentWorkflowSnapshot(
                group_idx=48,
                retry=0,
                state="implementing",
                source="event",
                active_agents=["implementer-g48-t0-a0"],
                latest_artifact_id=3025601,
                citations=["event:30257"],
            ),
            artifacts=[
                _artifact(
                    "dag-worktree-alias-preflight:g48:initial-dispatch",
                    {
                        "approved": False,
                        "operator_required": False,
                        "blockers": [{"reason": "worktree_alias_path"}],
                    },
                    3025600,
                ),
                _artifact("dag-group:48", {"status": "checkpointed"}, 3025601),
            ],
            events=[
                _event(
                    "agent_invocation_start",
                    30257,
                    "implementer-g48-t0-a0",
                    metadata={"group_idx": 48, "retry": 0},
                )
            ],
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert "artifact:dag-worktree-alias-preflight:g48:initial-dispatch id=3025600" in packet.citations


def test_later_group_checkpoint_does_not_clear_pending_merge_queue_blocker():
    packet = classify_observation(
        _observation(
            current=CurrentWorkflowSnapshot(
                group_idx=46,
                retry=0,
                state="implementing",
                source="event",
                active_agents=["implementer-g46-t0-a0"],
                latest_artifact_id=1678811,
                citations=["event:30147"],
            ),
            artifacts=[
                _artifact(
                    "dag-task-pending-merge:TASK-7",
                    {
                        "status": "blocked",
                        "notes": "canonical_mutation=pending_durable_merge_queue",
                        "summary": "Sandbox patch pending durable merge queue.",
                    },
                    1678810,
                ),
                _artifact("dag-group:46", {"status": "checkpointed"}, 1678811),
            ],
            events=[
                _event(
                    "agent_invocation_start",
                    30147,
                    "implementer-g46-t0-a0",
                    metadata={"group_idx": 46, "retry": 0},
                )
            ],
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert "artifact:dag-task-pending-merge:TASK-7 id=1678810" in packet.citations


def test_g37_raw_gate_checkpoint_contradiction_escalates():
    failed_at = datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc)
    checkpoint_at = datetime(2026, 5, 20, 15, 1, tzinfo=timezone.utc)

    packet = classify_observation(
        _observation(
            artifacts=[
                ArtifactRecord(
                    id=1273016,
                    key="dag-verify:g37:initial",
                    value={"status": "failed", "summary": "raw verifier/preflight failed"},
                    created_at=failed_at,
                ),
                _artifact("dag-group:37", {"status": "checkpointed"}, 1273018),
            ],
            events=[
                _event("dag_verify_finish", 23309, "failed"),
                _event(
                    "dag_checkpoint_written",
                    23310,
                    "checkpointed group 37",
                    created_at=checkpoint_at,
                ),
            ],
        )
    )

    assert packet.classification == FailureClass.PIPELINE_BUG_SUSPECTED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert packet.group_idx == 37
    assert "event:23310" in packet.citations


def test_stale_checkpoint_before_failed_gate_does_not_escalate_contradiction():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact("dag-group:37", {"status": "checkpointed"}, 1273015),
                _artifact(
                    "dag-verify:g37:initial",
                    {
                        "status": "failed",
                        "failure_class": "product_defect",
                        "summary": "later product verifier failure",
                    },
                    1273016,
                ),
            ],
        )
    )

    assert packet.classification == FailureClass.NORMAL_PRODUCT_REPAIR
    assert packet.classification != FailureClass.PIPELINE_BUG_SUSPECTED
    assert packet.facts["latest_failed_verify_artifacts"] == [
        "artifact:dag-verify:g37:initial id=1273016"
    ]


def test_checkpoint_event_after_failed_gate_escalates_contradiction():
    failed_at = datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc)
    checkpoint_at = datetime(2026, 5, 20, 15, 2, tzinfo=timezone.utc)

    packet = classify_observation(
        _observation(
            artifacts=[
                ArtifactRecord(
                    id=1273016,
                    key="dag-verify:g37:initial",
                    value={"status": "failed", "summary": "raw verifier failed"},
                    created_at=failed_at,
                ),
            ],
            events=[
                _event(
                    "dag_checkpoint_written",
                    23310,
                    "checkpointed group 37",
                    metadata={"group_idx": 37},
                    created_at=checkpoint_at,
                ),
            ],
        )
    )

    assert packet.classification == FailureClass.PIPELINE_BUG_SUSPECTED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert "event:23310" in packet.citations


def test_typed_checkpoint_contradiction_quiesce_escalates_without_artifacts():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "checkpoint attempted after failed gate",
                    metadata={
                        "evidence_node_id": 934,
                        "group_idx": 37,
                        "attempt_id": 12,
                        "failure_class": "checkpoint_contradiction",
                        "failure_type": "checkpoint_after_failed_gate",
                        "route": "quiesce",
                        "severity": "fatal",
                        "operator_required": False,
                        "retryable": False,
                        "deterministic": True,
                    },
                )
            ],
            bridge=BridgeProbe(ok=True, status={"state": "dead"}, errors=["Traceback"]),
        )
    )

    assert packet.classification == FailureClass.PIPELINE_BUG_SUSPECTED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert packet.classification != FailureClass.SAFE_RESTART_CANDIDATE
    assert packet.citations == ["event:control_plane_runtime_failure:evidence_node:934"]
    assert packet.facts["pipeline_runtime_failures"][0]["failure_class"] == (
        "checkpoint_contradiction"
    )


def test_dag_verify_graph_approval_clears_prior_failed_raw_gate():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-verify:g37:initial",
                    {"status": "failed", "summary": "raw verifier failed"},
                    100,
                ),
                _artifact(
                    "dag-verify-graph:g37:initial",
                    {
                        "aggregate": {"status": "approved"},
                        "approved": True,
                        "status": "approved",
                    },
                    101,
                ),
                _artifact("dag-group:37", {"status": "checkpointed"}, 102),
            ],
        )
    )

    assert packet.classification != FailureClass.PIPELINE_BUG_SUSPECTED


def test_rejected_dag_verify_graph_checkpoint_contradiction_escalates_without_legacy_projection():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-verify-graph:g37:initial",
                    {
                        "approved": False,
                        "aggregate": {
                            "approved": False,
                            "blocking_failure_class": "product_defect",
                        },
                        "aggregate_node": {"status": "rejected"},
                    },
                    1273016,
                ),
                _artifact("dag-group:37", {"status": "checkpointed"}, 1273018),
            ],
        )
    )

    assert packet.classification == FailureClass.PIPELINE_BUG_SUSPECTED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert "artifact:dag-verify-graph:g37:initial id=1273016" in packet.citations
    assert "artifact:dag-group:37 id=1273018" in packet.citations


def test_aggregate_conflict_dag_verify_graph_is_workflow_blocker_not_product_repair():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-verify-graph:g41:initial",
                    {
                        "approved": False,
                        "aggregate": {
                            "approved": False,
                            "blocking_failure_class": "aggregate.conflict",
                        },
                        "aggregate_node": {
                            "status": "rejected",
                            "metadata": {
                                "blocking_failure_class": "aggregate.conflict",
                            },
                        },
                        "nodes": [
                            {
                                "kind": "raw_verifier",
                                "status": "rejected",
                                "metadata": {
                                    "failure_class": "evidence_corruption",
                                    "failure_type": "projection_body_conflict",
                                    "blocking_failure_class": "aggregate.conflict",
                                },
                            }
                        ],
                    },
                    1274016,
                )
            ],
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND
    assert "artifact:dag-verify-graph:g41:initial id=1274016" in packet.citations
    assert "aggregate.conflict" in packet.facts["workflow_blocker_failure_classes"]
    assert packet.classification != FailureClass.NORMAL_PRODUCT_REPAIR


def test_historical_raw_failures_do_not_outrank_current_group_progress():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-verify:g0:initial",
                    {"status": "failed", "summary": "historical raw verifier failed"},
                    743238,
                ),
                _artifact("dag-group:0", {"status": "checkpointed"}, 743618),
                _artifact(
                    "dag-verify:g38:retry-0",
                    {"status": "passed", "summary": "current group reverify passed"},
                    1360400,
                ),
            ],
            events=[
                _event(
                    "dag_verify_start",
                    24438,
                    "g38:retry-0",
                    metadata={"group_idx": 38, "retry": 0},
                )
            ],
        )
    )

    assert packet.classification == FailureClass.HEALTHY_PROGRESS
    assert packet.group_idx == 38
    assert packet.group_idx == 38
    assert "artifact:dag-verify:g0:initial id=743238" not in packet.citations


def test_g38_commit_only_failure_routes_to_direct_unblock():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-commit-failure:g38:retry-0",
                    "WorkflowCommitError: husky failed in repos/app/src/ChatSidepaneShell.test.tsx:149",
                    1353600,
                )
            ],
            events=[_event("dag_commit_failed", 24286, "dag_commit_failed")],
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND
    assert packet.group_idx == 38
    assert packet.retry == 0
    assert "repos/app/src/ChatSidepaneShell.test.tsx" in packet.facts["commit_targets"]


def test_old_commit_failure_does_not_outrank_newer_group_verify_progress():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "dag_commit_failed",
                    24286,
                    "dag_commit_failed",
                    metadata={"group_idx": 38, "retry": 0},
                    created_at=datetime(2026, 5, 6, 16, 12, tzinfo=timezone.utc),
                ),
                _event(
                    "dag_verify_start",
                    24438,
                    "g38:retry-0",
                    metadata={"group_idx": 38, "retry": 0},
                    created_at=datetime(2026, 5, 6, 16, 31, tzinfo=timezone.utc),
                ),
            ]
        )
    )

    assert packet.classification == FailureClass.HEALTHY_PROGRESS
    assert packet.group_idx == 38


def test_g39_historical_commit_failures_do_not_outrank_live_verify_lenses():
    packet = classify_observation(
        _observation(
            current=CurrentWorkflowSnapshot(
                group_idx=39,
                retry=0,
                state="verifying",
                source="event",
                active_agents=[
                    "security-auditor-dag-lens-g39-r0-security-boundary",
                    "verifier-dag-lens-g39-r0-contract-protocol",
                ],
                latest_event_id=25488,
                latest_artifact_id=1421016,
                citations=["event:25488"],
            ),
            artifacts=[
                _artifact(
                    "dag-commit-failure:g39:retry-0",
                    "WorkflowCommitError: old husky failure in retired chat subtree",
                    1398746,
                ),
                _artifact(
                    "dag-authority-gate:g39:retry-0",
                    {
                        "route": "semantic_verify_needed",
                        "status": "no_action",
                        "reason": "no_deterministic_artifact_only_problem",
                    },
                    1420693,
                ),
                _artifact(
                    "dag-verify:g39:initial",
                    {
                        "approved": False,
                        "summary": "Per-group verification failed on product-level concerns.",
                    },
                    1420690,
                ),
                _artifact(
                    "dag-repair-lens:g39:regression-downstream:retry-0",
                    {"completed": False, "summary": "product regression concerns remain"},
                    1421016,
                ),
            ],
            events=[
                _event(
                    "agent_invocation_start",
                    25488,
                    "security-auditor-dag-lens-g39-r0-security-boundary",
                    metadata={"group_idx": 39, "retry": 0},
                )
            ],
        )
    )

    assert packet.classification == FailureClass.NORMAL_PRODUCT_REPAIR
    assert packet.group_idx == 39
    assert packet.retry == 0
    assert "artifact:dag-commit-failure:g39:retry-0 id=1398746" not in packet.citations
    assert "artifact:dag-verify:g39:initial id=1420690" in packet.citations


def test_current_snapshot_prevents_stale_commit_blocker_from_older_group():
    packet = classify_observation(
        _observation(
            current=CurrentWorkflowSnapshot(
                group_idx=39,
                state="implementing",
                source="event",
                active_agents=["implementer-g39-t10-a0"],
                citations=["event:24517"],
            ),
            artifacts=[
                _artifact(
                    "dag-commit-failure:g38:retry-0",
                    "WorkflowCommitError: husky failed in old group",
                    1353600,
                ),
            ],
            events=[
                _event(
                    "agent_invocation_start",
                    24517,
                    "implementer-g39-t10-a0",
                    metadata={"group_idx": 39},
                )
            ],
        )
    )

    assert packet.classification == FailureClass.HEALTHY_PROGRESS
    assert packet.group_idx == 39
    assert packet.facts["active_agents"] == ["implementer-g39-t10-a0"]
    assert "artifact:dag-commit-failure:g38:retry-0 id=1353600" not in packet.citations


def test_bridge_only_current_snapshot_reports_active_implementation():
    packet = classify_observation(
        _observation(
            current=CurrentWorkflowSnapshot(
                group_idx=39,
                state="implementing",
                source="bridge",
                active_agents=["implementer-g39-t10-a0"],
                queued_agents=["implementer-g39-t11-a0"],
                citations=["dashboard:/api/bridge/logs"],
            ),
            bridge=BridgeProbe(
                ok=True,
                status={"state": "running"},
                log_lines=[
                    "Agent concurrency acquired actor=implementer-g39-t10-a0 active=1 queued=1 max=2"
                ],
            ),
        )
    )

    assert packet.classification == FailureClass.HEALTHY_PROGRESS
    assert packet.group_idx == 39
    assert packet.facts["queued_agents"] == ["implementer-g39-t11-a0"]
    assert "dashboard:/api/bridge/logs" in packet.citations


def test_product_verifier_failure_stays_normal_repair():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-verify:g38:retry-1",
                    {
                        "status": "failed",
                        "failure_class": "product_defect",
                        "issues": [
                            "pytest failure in current product file",
                            "backend compile regression",
                        ],
                    },
                    1326086,
                )
            ]
        )
    )

    assert packet.classification == FailureClass.NORMAL_PRODUCT_REPAIR
    assert packet.recommended_action == ActionLevel.OBSERVE
    assert any("No checkpoint contradiction" in check for check in packet.false_positive_checks)


def test_product_defect_dag_verify_graph_failure_stays_normal_repair():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-verify-graph:g38:retry-1",
                    {
                        "approved": False,
                        "summary": "pytest failure in current product file",
                        "aggregate": {
                            "approved": False,
                            "blocking_failure_class": "product_defect",
                        },
                        "aggregate_node": {"status": "rejected"},
                        "nodes": [
                            {
                                "kind": "raw_verifier",
                                "status": "failed",
                                "metadata": {
                                    "approved": True,
                                    "failure_class": "product_defect",
                                },
                            }
                        ],
                    },
                    1326087,
                )
            ]
        )
    )

    assert packet.classification == FailureClass.NORMAL_PRODUCT_REPAIR
    assert packet.recommended_action == ActionLevel.OBSERVE
    assert packet.facts["latest_failed_verify_artifacts"] == [
        "artifact:dag-verify-graph:g38:retry-1 id=1326087"
    ]


@pytest.mark.parametrize(
    "failure_class",
    ["verifier_provider", "runtime_context", "stale_context"],
)
def test_workflow_class_dag_verify_graph_failure_does_not_route_product_repair(
    failure_class: str,
):
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-verify-graph:g38:retry-1",
                    {
                        "approved": False,
                        "summary": f"{failure_class} blocked verification graph",
                        "aggregate": {
                            "approved": False,
                            "blocking_failure_class": failure_class,
                        },
                        "aggregate_node": {"status": "rejected"},
                        "nodes": [
                            {
                                "kind": "raw_verifier",
                                "status": "failed",
                                "metadata": {"failure_class": failure_class},
                            }
                        ],
                    },
                    1326088,
                )
            ]
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.classification != FailureClass.NORMAL_PRODUCT_REPAIR
    assert failure_class in packet.facts["workflow_blocker_failure_classes"]


def test_current_bound_product_verifier_outranks_older_deterministic_blocker():
    packet = classify_observation(
        _observation(
            current=CurrentWorkflowSnapshot(
                group_idx=57,
                retry=0,
                state="verifying",
                source="event",
                active_agents=["verifier-g57-t0-a0"],
                latest_artifact_id=1678821,
                citations=["event:30157"],
            ),
            artifacts=[
                _artifact(
                    "workflow-blocker:g56:retry-0",
                    {
                        "status": "failed",
                        "failure_class": "runtime_context",
                        "failure_type": "context_materialization_failed",
                    },
                    1678818,
                ),
                _artifact("dag-group:56", {"status": "checkpointed"}, 1678819),
                _artifact(
                    "dag-verify:g57:initial",
                    {
                        "status": "failed",
                        "failure_class": "product_defect",
                        "summary": "pytest failure in current product file",
                    },
                    1678821,
                ),
            ],
            events=[
                _event(
                    "agent_invocation_start",
                    30157,
                    "verifier-g57-t0-a0",
                    metadata={"group_idx": 57, "retry": 0},
                )
            ],
        )
    )

    assert packet.classification == FailureClass.NORMAL_PRODUCT_REPAIR
    assert packet.group_idx == 57
    assert "artifact:dag-verify:g57:initial id=1678821" in packet.citations
    assert "artifact:workflow-blocker:g56:retry-0 id=1678818" not in packet.citations


def test_sandbox_workflow_blocker_verify_does_not_classify_product_repair():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-verify:g41:initial",
                    {
                        "approved": False,
                        "summary": (
                            "SANDBOX_WORKFLOW_BLOCKER: verifier sandbox binding is terminal"
                        ),
                        "metadata": {
                            "failure_class": "runtime_context",
                            "failure_type": "verifier_context_stale",
                        },
                    },
                    1462001,
                )
            ]
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND
    assert "artifact:dag-verify:g41:initial id=1462001" in packet.citations
    assert packet.facts["workflow_blocker_failure_classes"] == ["runtime_context"]


def test_verifier_provider_failure_does_not_classify_product_repair():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-verify:g42:initial",
                    {
                        "approved": False,
                        "summary": (
                            "Verifier provider/runtime failed before product repair dispatch."
                        ),
                        "aggregate": {"blocking_failure_class": "verifier_provider"},
                        "nodes": [
                            {
                                "kind": "raw_verifier",
                                "status": "failed",
                                "metadata": {
                                    "failure_class": "verifier_provider",
                                    "failure_type": "verifier_provider_crash",
                                },
                            }
                        ],
                    },
                    1462002,
                )
            ]
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.classification != FailureClass.NORMAL_PRODUCT_REPAIR
    assert packet.facts["workflow_blocker_failure_classes"] == ["verifier_provider"]


@pytest.mark.parametrize(
    ("failure_class", "failure_type"),
    [
        ("verifier_context", "context_materialization_failed"),
        ("stale_projection", "stale_projection_snapshot"),
        ("checkpoint_contradiction", "checkpoint_after_failed_gate"),
        ("commit_hygiene", "commit_hook_failed"),
    ],
)
def test_artifact_only_failed_dag_verify_workflow_classes_unblock(
    failure_class: str,
    failure_type: str,
):
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-verify:g43:initial",
                    {
                        "approved": False,
                        "status": "failed",
                        "summary": (
                            "Compatibility verifier artifact failed before product repair."
                        ),
                        "metadata": {
                            "failure_class": failure_class,
                            "failure_type": failure_type,
                        },
                    },
                    1462003,
                )
            ]
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND
    assert packet.classification != FailureClass.NORMAL_PRODUCT_REPAIR
    assert "artifact:dag-verify:g43:initial id=1462003" in packet.citations
    assert failure_class in packet.facts["workflow_blocker_failure_classes"]


def test_artifact_only_failed_dag_verify_textual_workflow_blocker_unblocks():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-verify:g44:initial",
                    (
                        "failed dag-verify compatibility artifact: stale projection "
                        "snapshot and verifier context materialization failed"
                    ),
                    1462004,
                )
            ]
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND
    assert packet.classification != FailureClass.NORMAL_PRODUCT_REPAIR
    assert "artifact:dag-verify:g44:initial id=1462004" in packet.citations


def test_normal_repair_seed_cites_latest_material_artifacts_not_old_verify_tail():
    packet = classify_observation(
        _observation(
            current=CurrentWorkflowSnapshot(
                group_idx=40,
                retry=0,
                state="implementing",
                source="event",
                active_agents=["implementer-g40-fix-0"],
                latest_artifact_id=1460467,
                citations=["event:26046"],
            ),
            artifacts=[
                _artifact(
                    "dag-verify:g40:retry-1",
                    {"approved": False, "summary": "old failed verifier issue"},
                    1458533,
                ),
                _artifact(
                    "dag-verify:g40:initial",
                    {
                        "approved": False,
                        "summary": "latest failed verifier issue",
                    },
                    1459599,
                ),
                _artifact(
                    "dag-repair-lens:g40:runtime-composition:retry-0",
                    {"status": "completed", "summary": "runtime lens done"},
                    1459762,
                ),
                _artifact(
                    "dag-verify-rca:g40:retry-0",
                    {"summary": "latest RCA names current root cause"},
                    1460467,
                ),
            ],
        )
    )

    assert packet.classification == FailureClass.NORMAL_PRODUCT_REPAIR
    assert "artifact:dag-verify:g40:initial id=1459599" in packet.citations
    assert "artifact:dag-verify-rca:g40:retry-0 id=1460467" in packet.citations
    assert "artifact:dag-verify:g40:retry-1 id=1458533" not in packet.citations
    assert packet.facts["latest_material_artifacts"][-1] == (
        "artifact:dag-verify-rca:g40:retry-0 id=1460467"
    )


def test_approved_verdict_with_historical_failure_prose_is_successful():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-verify:g38:retry-0",
                    {
                        "approved": True,
                        "summary": "PASS after prior verification failed on retry 0.",
                        "concerns": [],
                    },
                    1360854,
                ),
                _artifact("dag-group:38", {"status": "checkpointed"}, 1360857),
            ]
        )
    )

    assert packet.classification == FailureClass.HEALTHY_PROGRESS


def test_summary_only_failed_verify_preview_classifies_product_repair():
    packet = classify_observation(
        _observation(
            artifacts=[
                ArtifactRecord(
                    id=1459599,
                    key="dag-verify:g40:initial",
                    value="",
                    value_preview='{"status":"failed","summary":"component assertion failed"}',
                    summary_only=True,
                )
            ]
        )
    )

    assert packet.classification == FailureClass.NORMAL_PRODUCT_REPAIR
    assert packet.recommended_action == ActionLevel.OBSERVE
    assert "artifact:dag-verify:g40:initial id=1459599" in packet.citations
    assert packet.group_idx == 40


def test_failed_initial_verify_does_not_remain_current_after_approved_retry():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-verify:g38:initial",
                    {"approved": False, "summary": "initial verification failed"},
                    1358950,
                ),
                _artifact(
                    "dag-verify:g38:retry-0",
                    {"approved": True, "summary": "PASS", "concerns": []},
                    1360854,
                ),
                _artifact("dag-group:38", {"status": "checkpointed"}, 1360857),
            ]
        )
    )

    assert packet.classification == FailureClass.HEALTHY_PROGRESS
    assert packet.citations == ["artifact:dag-verify:g38:retry-0 id=1360854"]


def test_worktree_hygiene_forces_operator_required():
    packet = classify_observation(
        _observation(
            worktrees=[
                WorktreeProbe(
                    root="/tmp/repo",
                    embedded_git_paths=["packages/embedded/.git"],
                    gitlinks=["vendor/toolkit"],
                    forbidden_paths=[
                        GitPathFact(path="legacy/chat/Old.tsx", reason="exists-on-disk")
                    ],
                )
            ]
        )
    )

    assert packet.classification == FailureClass.OPERATOR_REQUIRED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert packet.facts["embedded_git_paths"] == ["packages/embedded/.git"]
    assert packet.facts["gitlinks"] == ["vendor/toolkit"]


def test_acl_artifact_forces_operator_required_over_bridge_noise():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-workspace-acl-normalization:g48:repair-dispatch-retry-0",
                    {
                        "operator_required": True,
                        "operator_reasons": [
                            "parent directory is not writable by repair agent"
                        ],
                        "target_files": [
                            "iriai-studio/src/workflow-tab/impl/bridge/catalogBindings.ts"
                        ],
                    },
                    1678790,
                )
            ],
            bridge=BridgeProbe(
                dashboard_url="http://127.0.0.1:51234",
                ok=True,
                status={"state": "running", "running": True},
                errors=[
                    "resumed workflow failed",
                    "Claude availability probe failed api_error_status:403",
                ],
            ),
        )
    )

    assert packet.classification == FailureClass.OPERATOR_REQUIRED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert "artifact:dag-workspace-acl-normalization:g48:repair-dispatch-retry-0 id=1678790" in packet.citations


def test_workspace_authority_operator_required_artifact_escalates():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "workspace-authority-routes:g48:initial-dispatch",
                    {
                        "approved": False,
                        "operator_required": True,
                        "routes": [
                            {
                                "failure_class": "operator_required",
                                "failure_type": "operator_clearance_required",
                                "route": "operator_required",
                            }
                        ],
                    },
                    1678792,
                )
            ]
        )
    )

    assert packet.classification == FailureClass.OPERATOR_REQUIRED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert packet.citations == [
        "artifact:workspace-authority-routes:g48:initial-dispatch id=1678792"
    ]


def test_operator_required_artifact_outranks_pipeline_runtime_failure():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "workspace-authority-routes:g48:initial-dispatch",
                    {
                        "approved": False,
                        "operator_required": True,
                        "routes": [
                            {
                                "failure_class": "operator_required",
                                "failure_type": "writeability_denied",
                                "route": "operator_required",
                            }
                        ],
                    },
                    1678793,
                )
            ],
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "checkpoint contradiction after failed raw gate",
                    metadata={
                        "evidence_node_id": 943,
                        "group_idx": 48,
                        "attempt_id": 88,
                        "failure_class": "checkpoint_contradiction",
                        "failure_type": "checkpoint_after_failed_gate",
                        "route": "quiesce",
                        "severity": "fatal",
                        "operator_required": False,
                        "retryable": False,
                        "deterministic": True,
                    },
                )
            ],
        )
    )

    assert packet.classification == FailureClass.OPERATOR_REQUIRED
    assert packet.citations == [
        "artifact:workspace-authority-routes:g48:initial-dispatch id=1678793"
    ]


def test_acl_artifact_not_suppressed_by_later_failed_verify_or_active_snapshot():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-writeability-preflight:g48:retry-0",
                    {
                        "operator_required": True,
                        "problems": [{"reason": "writeability_denied"}],
                    },
                    1678790,
                ),
                _artifact(
                    "dag-verify:g48:retry-0",
                    {"approved": False, "summary": "still failed"},
                    1678791,
                ),
            ],
            current=CurrentWorkflowSnapshot(
                group_idx=48,
                retry=0,
                phase="implementation",
                state="implementing",
                active_agents=["implementer-g48"],
                latest_artifact_id=1678791,
                latest_event_id=30169,
            ),
        )
    )

    assert packet.classification == FailureClass.OPERATOR_REQUIRED


def test_workspace_dirty_authority_route_classifies_deterministic_unblock():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "workspace-authority-routes:g48:initial-dispatch",
                    {
                        "approved": False,
                        "status": "blocked",
                        "routes": [
                            {
                                "failure_class": "workspace_dirty",
                                "failure_type": "dirty_snapshot_before_dispatch",
                                "status": "blocked",
                                "deterministic_workflow_blocker": True,
                                "route": "quiesce",
                            }
                        ],
                    },
                    1678794,
                )
            ]
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND


def test_later_group_checkpoint_does_not_clear_operator_required_acl():
    packet = classify_observation(
        _observation(
            current=CurrentWorkflowSnapshot(
                group_idx=48,
                retry=0,
                phase="implementation",
                state="implementing",
                active_agents=["implementer-g48"],
                latest_artifact_id=1678791,
                latest_event_id=30170,
                citations=["event:30170"],
            ),
            artifacts=[
                _artifact(
                    "dag-writeability-preflight:g48:retry-0",
                    {
                        "operator_required": True,
                        "problems": [{"reason": "writeability_denied"}],
                    },
                    1678790,
                ),
                _artifact("dag-group:48", {"status": "checkpointed"}, 1678791),
            ],
            events=[
                _event(
                    "agent_invocation_start",
                    30170,
                    "implementer-g48",
                    metadata={"group_idx": 48, "retry": 0},
                )
            ],
        )
    )

    assert packet.classification == FailureClass.OPERATOR_REQUIRED
    assert "artifact:dag-writeability-preflight:g48:retry-0 id=1678790" in packet.citations


def test_direct_route_operator_required_classifies_operator_required():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-direct-repair-route:g48:retry-0",
                    {
                        "route": "manifest_forbidden_product_cleanup",
                        "operator_required": True,
                        "workspace_permission_repair": {
                            "operator_required": True,
                            "operator_reasons": ["chmod failed"],
                        },
                    },
                    1678792,
                )
            ]
        )
    )

    assert packet.classification == FailureClass.OPERATOR_REQUIRED
    assert "artifact:dag-direct-repair-route:g48:retry-0 id=1678792" in packet.citations


def test_typed_control_plane_operator_required_event_escalates_and_dedupes():
    event = _event(
        "control_plane_runtime_failure",
        0,
        "workspace_permission: operator required",
        metadata={
            "evidence_node_id": 915,
            "group_idx": 48,
            "attempt_id": 77,
            "failure_class": "workspace_permission",
            "failure_type": "writeability_denied",
            "operator_required": True,
            "retryable": False,
        },
    )
    packet = classify_observation(
        _observation(
            events=[
                event,
                event.model_copy(update={"content": "duplicate projected control-plane event"}),
            ],
        )
    )

    assert packet.classification == FailureClass.OPERATOR_REQUIRED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert packet.group_idx == 48
    assert packet.citations == ["event:control_plane_runtime_failure:evidence_node:915"]
    assert packet.facts["operator_required_runtime_failures"] == [{
        "citation": "event:control_plane_runtime_failure:evidence_node:915",
        "evidence_node_id": 915,
        "group_idx": 48,
        "attempt_id": 77,
        "failure_class": "workspace_permission",
        "failure_type": "writeability_denied",
        "route": None,
        "deterministic": None,
        "retryable": False,
        "content": "workspace_permission: operator required",
    }]


def test_operator_required_runtime_route_hint_escalates_without_boolean():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "workspace repair requires operator",
                    metadata={
                        "evidence_node_id": 944,
                        "group_idx": 48,
                        "attempt_id": 89,
                        "failure_class": "workspace_permission",
                        "failure_type": "writeability_denied",
                        "route": "operator_required",
                        "retryable": False,
                    },
                )
            ],
        )
    )

    assert packet.classification == FailureClass.OPERATOR_REQUIRED
    assert packet.citations == ["event:control_plane_runtime_failure:evidence_node:944"]


def test_deterministic_workspace_operator_required_event_still_escalates():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "workspace_permission: deterministic operator required",
                    metadata={
                        "evidence_node_id": 933,
                        "group_idx": 48,
                        "attempt_id": 78,
                        "failure_class": "workspace_permission",
                        "failure_type": "writeability_denied",
                        "route": "operator_required",
                        "operator_required": True,
                        "retryable": False,
                        "deterministic": True,
                    },
                )
            ],
        )
    )

    assert packet.classification == FailureClass.OPERATOR_REQUIRED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert packet.citations == ["event:control_plane_runtime_failure:evidence_node:933"]


def test_repairable_operator_required_runtime_event_routes_to_unblock():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "workspace_permission: deterministic repair requires operator",
                    metadata={
                        "evidence_node_id": 936,
                        "group_idx": 48,
                        "attempt_id": 80,
                        "failure_class": "workspace_permission",
                        "failure_type": "writeability_denied",
                        "route": "run_workspace_repair",
                        "operator_required": True,
                        "retryable": True,
                        "deterministic": True,
                    },
                )
            ],
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND
    assert packet.citations == ["event:control_plane_runtime_failure:evidence_node:936"]


def test_operator_required_runtime_event_with_retry_budget_routes_to_unblock():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "workspace_permission has deterministic repair budget",
                    metadata={
                        "evidence_node_id": 950,
                        "group_idx": 48,
                        "attempt_id": 80,
                        "failure_class": "workspace_permission",
                        "failure_type": "writeability_denied",
                        "route": "operator_required",
                        "retry_budget": {
                            "route": "run_workspace_repair",
                            "retry": 0,
                            "max_retries": 2,
                            "remaining_attempts": 1,
                        },
                        "operator_required": True,
                        "retryable": True,
                        "deterministic": True,
                    },
                )
            ],
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND
    assert packet.citations == ["event:control_plane_runtime_failure:evidence_node:950"]


def test_operator_required_runtime_event_outranks_pipeline_bug_route():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "checkpoint contradiction requires operator hold",
                    metadata={
                        "evidence_node_id": 937,
                        "group_idx": 48,
                        "attempt_id": 81,
                        "failure_class": "checkpoint_contradiction",
                        "failure_type": "checkpoint_contradiction",
                        "route": "quiesce",
                        "operator_required": True,
                        "retryable": False,
                        "deterministic": True,
                        "severity": "fatal",
                    },
                )
            ],
        )
    )

    assert packet.classification == FailureClass.OPERATOR_REQUIRED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert packet.citations == ["event:control_plane_runtime_failure:evidence_node:937"]


def test_deterministic_verifier_context_runtime_failure_routes_to_unblock():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "verifier_context/context_materialization_failed",
                    metadata={
                        "evidence_node_id": 918,
                        "group_idx": 49,
                        "attempt_id": 81,
                        "failure_class": "verifier_context",
                        "failure_type": "context_materialization_failed",
                        "route": "retry_verifier",
                        "operator_required": False,
                        "retryable": True,
                        "deterministic": True,
                    },
                )
            ]
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND
    assert packet.group_idx == 49
    assert packet.citations == ["event:control_plane_runtime_failure:evidence_node:918"]
    assert packet.classification not in {FailureClass.WATCH_ONLY, FailureClass.OPERATOR_REQUIRED}
    assert packet.facts["runtime_failure_events"][0]["route"] == "retry_verifier"


def test_acl_workability_workspace_repair_runtime_failure_routes_to_unblock():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "acl_workability/unwritable_runtime_path",
                    metadata={
                        "evidence_node_id": 935,
                        "group_idx": 49,
                        "attempt_id": 82,
                        "failure_class": "acl_workability",
                        "failure_type": "unwritable_runtime_path",
                        "route": "run_workspace_repair",
                        "operator_required": False,
                        "retryable": True,
                    },
                )
            ]
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND
    assert packet.citations == ["event:control_plane_runtime_failure:evidence_node:935"]
    assert packet.facts["runtime_failure_events"][0]["failure_class"] == "acl_workability"
    assert packet.facts["runtime_failure_events"][0]["route"] == "run_workspace_repair"


def test_later_group_checkpoint_does_not_clear_typed_runtime_unblock():
    runtime_at = datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc)
    checkpoint_at = datetime(2026, 5, 20, 15, 5, tzinfo=timezone.utc)

    packet = classify_observation(
        _observation(
            current=CurrentWorkflowSnapshot(
                group_idx=49,
                retry=0,
                state="implementing",
                source="event",
                active_agents=["implementer-g49-t0-a0"],
                latest_artifact_id=1678811,
                citations=["event:30149"],
            ),
            events=[
                _event(
                    "control_plane_runtime_failure",
                    30148,
                    "verifier_context/context_materialization_failed",
                    metadata={
                        "evidence_node_id": 939,
                        "group_idx": 49,
                        "attempt_id": 83,
                        "failure_class": "verifier_context",
                        "failure_type": "context_materialization_failed",
                        "route": "retry_verifier",
                        "operator_required": False,
                        "retryable": True,
                        "deterministic": True,
                    },
                    created_at=runtime_at,
                ),
                _event(
                    "agent_invocation_start",
                    30149,
                    "implementer-g49-t0-a0",
                    metadata={"group_idx": 49, "retry": 0},
                    created_at=checkpoint_at,
                ),
            ],
            artifacts=[
                ArtifactRecord(
                    id=1678811,
                    key="dag-group:49",
                    value={"status": "checkpointed"},
                    created_at=checkpoint_at,
                )
            ],
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.citations == ["event:control_plane_runtime_failure:evidence_node:939"]


def test_deterministic_typed_unblock_outranks_stale_codex_invocation():
    stale = StaleCodexInvocation(
        actor="implementer-g49-t1-a0",
        group_idx=49,
        retry=0,
        task_id="TASK-1",
        pid=1234,
        trace_path="/tmp/trace.jsonl",
        elapsed_seconds=4000,
        idle_seconds=4000,
        evidence_token="tok",
    )

    packet = classify_observation(
        _observation(
            stale_codex_invocations=[stale],
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "stale projection requires host reconcile",
                    metadata={
                        "evidence_node_id": 920,
                        "group_idx": 49,
                        "attempt_id": 83,
                        "failure_class": "stale_projection",
                        "failure_type": "stale_projection_snapshot",
                        "route": "stale_projection",
                        "operator_required": False,
                        "retryable": True,
                        "deterministic": True,
                    },
                )
            ],
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND
    assert packet.citations == ["event:control_plane_runtime_failure:evidence_node:920"]


@pytest.mark.parametrize(
    "control_plane_snapshot",
    [
        {
            "snapshot_version": "cp-active-dispatch",
            "attempts": [
                {
                    "id": 77,
                    "entry_type": "dispatch_attempt",
                    "status": "started",
                    "dispatcher_state": "runtime_invoked",
                    "group_idx": 49,
                }
            ],
        },
        {
            "snapshot_version": "cp-active-sandbox",
            "sandbox_snapshots": [
                {"id": 12, "status": "running", "group_idx": 49, "attempt_no": 1}
            ],
        },
        {
            "snapshot_version": "cp-active-binding",
            "runtime_workspace_bindings": [
                {"id": 31, "status": "active", "group_idx": 49, "attempt_id": 77}
            ],
        },
        {
            "snapshot_version": "cp-active-merge",
            "merge_queue": {"pending_count": 1, "items": []},
        },
        {
            "snapshot_version": "cp-active-merge-item",
            "merge_queue": {
                "pending_count": 0,
                "items": [
                    {
                        "source": "execution_journal_rows",
                        "typed_row_id": 201,
                        "status": "checkpointing",
                        "group_idx": 49,
                    }
                ],
            },
        },
    ],
)
def test_active_typed_control_plane_work_suppresses_stale_codex(
    control_plane_snapshot: dict,
):
    stale = StaleCodexInvocation(
        actor="implementer-g49-t1-a0",
        group_idx=49,
        retry=0,
        task_id="TASK-1",
        pid=1234,
        trace_path="/tmp/trace.jsonl",
        elapsed_seconds=4000,
        idle_seconds=4000,
        evidence_token="tok",
    )

    packet = classify_observation(
        _observation(
            stale_codex_invocations=[stale],
            control_plane_snapshot=control_plane_snapshot,
        )
    )

    assert packet.classification == FailureClass.WATCH_ONLY
    assert packet.classification != FailureClass.STALE_CODEX_INVOCATION


def test_retained_sandbox_lease_does_not_suppress_stale_codex():
    stale = StaleCodexInvocation(
        actor="implementer-g49-t1-a0",
        group_idx=49,
        retry=0,
        task_id="TASK-1",
        pid=1234,
        trace_path="/tmp/trace.jsonl",
        elapsed_seconds=4000,
        idle_seconds=4000,
        evidence_token="tok",
    )

    packet = classify_observation(
        _observation(
            stale_codex_invocations=[stale],
            control_plane_snapshot={
                "snapshot_version": "cp-terminal-sandbox",
                "sandbox_snapshots": [
                    {"id": 12, "status": "retained", "group_idx": 49, "attempt_no": 1}
                ],
            },
        )
    )

    assert packet.classification == FailureClass.STALE_CODEX_INVOCATION


@pytest.mark.parametrize(
    ("route", "failure_class", "failure_type"),
    [
        ("worktree_alias", "worktree_alias", "worktree_alias_path"),
        ("commit_hygiene", "commit_hygiene", "commit_hygiene"),
        ("contract_compile", "contract_compile", "contract_compile_failed"),
        ("merge_conflict", "merge_conflict", "merge_conflict"),
        ("retry_dispatch", "runtime_context", "context_materialization_failed"),
        ("retry_sandbox_capture", "sandbox_capture", "sandbox_capture_failed"),
        ("run_sandbox_cleanup", "sandbox_cleanup", "sandbox_cleanup_failed"),
        ("retry_sandbox", "sandbox_allocation", "sandbox_allocation_failed"),
    ],
)
def test_broad_deterministic_runtime_routes_unblock(
    route: str,
    failure_class: str,
    failure_type: str,
):
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    f"{failure_class}: {failure_type}",
                    metadata={
                        "evidence_node_id": 930,
                        "group_idx": 51,
                        "attempt_id": 84,
                        "failure_class": failure_class,
                        "failure_type": failure_type,
                        "route": route,
                        "operator_required": False,
                        "retryable": True,
                        "deterministic": True,
                    },
                )
            ],
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.facts["runtime_failure_events"][0]["route"] == route


@pytest.mark.parametrize(
    ("route", "failure_class", "failure_type"),
    [
        ("retry_dispatch", "runtime_provider", "provider_rate_limited"),
        ("retry_runtime", "runtime_timeout", "runtime_timeout"),
        ("retry_verifier", "verifier_provider", "provider_crash"),
        ("resource_exhausted", "resource_exhausted", "resource_exhausted"),
        ("quiesce_workflow", "runtime_context", "post_test_notify_delivery_failed"),
    ],
)
def test_provider_quiesced_or_exhausted_runtime_failures_do_not_unblock(
    route: str,
    failure_class: str,
    failure_type: str,
):
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    f"{failure_class}: {failure_type}",
                    metadata={
                        "evidence_node_id": 931,
                        "group_idx": 51,
                        "attempt_id": 85,
                        "failure_class": failure_class,
                        "failure_type": failure_type,
                        "route": route,
                        "operator_required": False,
                        "retryable": True,
                        "deterministic": True,
                    },
                )
            ],
        )
    )

    assert packet.classification == FailureClass.WATCH_ONLY
    assert packet.recommended_action == ActionLevel.OBSERVE


@pytest.mark.parametrize(
    "metadata",
    [
        {
            "evidence_node_id": 945,
            "group_idx": 51,
            "attempt_id": 86,
            "failure_class": "checkpoint_contradiction",
            "failure_type": "checkpoint_after_failed_gate",
            "route": "quiesce",
            "severity": "fatal",
            "operator_required": False,
            "retryable": False,
            "deterministic": True,
            "status": "resolved",
        },
        {
            "evidence_node_id": 946,
            "group_idx": 51,
            "attempt_id": 87,
            "failure_class": "workspace_permission",
            "failure_type": "writeability_denied",
            "route": "operator_required",
            "operator_required": True,
            "retryable": False,
            "status": "cleared",
        },
    ],
)
def test_terminal_control_plane_runtime_failures_do_not_classify(metadata: dict):
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "terminal runtime failure row",
                    metadata=metadata,
                )
            ],
        )
    )

    assert packet.classification == FailureClass.WATCH_ONLY


@pytest.mark.parametrize(
    "payload",
    [
        {
            "failure_class": "runtime_provider",
            "failure_type": "provider_rate_limited",
            "route": "retry_dispatch",
            "deterministic": True,
            "blocked_before_product_repair": True,
        },
        {
            "failure_class": "runtime_context",
            "failure_type": "post_test_notify_delivery_failed",
            "route": "quiesce_workflow",
            "deterministic_workflow_blocker": True,
        },
        {
            "failure_type": "resource_exhausted",
            "deterministic": True,
            "typed_runtime_blocker": True,
        },
    ],
)
def test_dag_runtime_failure_artifacts_follow_non_unblock_guards(payload):
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact("dag-runtime-failure:g52:retry-0", payload, 1678800),
            ]
        )
    )

    assert packet.classification == FailureClass.WATCH_ONLY
    assert packet.recommended_action == ActionLevel.OBSERVE


@pytest.mark.parametrize(
    "failure_type",
    ["source_push_failed", "post_test_source_push_failed"],
)
def test_source_push_quiesce_runtime_failure_artifact_routes_to_unblock(failure_type: str):
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                        "dag-runtime-failure:source-push",
                        {
                            "failure_class": "runtime_context",
                            "failure_type": failure_type,
                        "route": "quiesce_workflow",
                        "deterministic": True,
                        "blocked_before_checkpoint": True,
                    },
                    1678801,
                ),
            ]
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND


def test_type_only_resource_exhausted_runtime_failure_does_not_unblock():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "resource exhausted",
                    metadata={
                        "evidence_node_id": 932,
                        "group_idx": 52,
                        "attempt_id": 86,
                        "failure_type": "resource_exhausted",
                        "operator_required": False,
                        "retryable": True,
                        "deterministic": True,
                    },
                )
            ],
        )
    )

    assert packet.classification == FailureClass.WATCH_ONLY
    assert packet.recommended_action == ActionLevel.OBSERVE


def test_quiesced_deterministic_repeat_runtime_failure_escalates_not_unblock():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "commit hygiene repair budget exhausted",
                    metadata={
                        "evidence_node_id": 936,
                        "group_idx": 53,
                        "attempt_id": 90,
                        "failure_class": "commit_hygiene",
                        "failure_type": "commit_hook_failed",
                        "route": "quiesce",
                        "operator_required": False,
                        "retryable": False,
                        "deterministic": True,
                    },
                )
            ],
        )
    )

    assert packet.classification == FailureClass.PIPELINE_BUG_SUSPECTED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert packet.classification != FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.facts["pipeline_runtime_failures"][0]["route"] == "quiesce"


def test_product_repair_route_hint_on_workflow_failure_routes_by_class():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "runtime context failed before product repair",
                    metadata={
                        "evidence_node_id": 947,
                        "group_idx": 54,
                        "attempt_id": 93,
                        "failure_class": "runtime_context",
                        "failure_type": "context_materialization_failed",
                        "route": "run_product_repair",
                        "operator_required": False,
                        "retryable": True,
                        "deterministic": True,
                    },
                )
            ]
        )
    )

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert packet.recommended_action == ActionLevel.RECOMMEND
    assert packet.facts["runtime_failure_events"][0]["failure_class"] == "runtime_context"
    assert packet.facts["runtime_failure_events"][0]["route"] == "run_product_repair"


def test_typed_route_decision_overrides_legacy_runtime_route_hint():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "runtime context failed before product repair",
                    metadata={
                        "evidence_node_id": 948,
                        "group_idx": 54,
                        "attempt_id": 94,
                        "failure_class": "runtime_context",
                        "failure_type": "context_materialization_failed",
                        "route": "run_product_repair",
                        "operator_required": False,
                        "retryable": True,
                        "deterministic": True,
                        "route_decision": {
                            "failure_class": "runtime_context",
                            "failure_type": "context_materialization_failed",
                            "route": "quiesce",
                        },
                    },
                )
            ]
        )
    )

    assert packet.classification == FailureClass.PIPELINE_BUG_SUSPECTED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert packet.facts["pipeline_runtime_failures"][0]["route"] == "quiesce"


def test_typed_route_decision_booleans_override_legacy_runtime_metadata():
    operator_packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "typed route decision requires operator",
                    metadata={
                        "evidence_node_id": 949,
                        "group_idx": 54,
                        "attempt_id": 95,
                        "failure_class": "runtime_provider",
                        "failure_type": "provider_transport_error",
                        "route": "retry_dispatch",
                        "operator_required": False,
                        "retryable": True,
                        "route_decision": {
                            "failure_class": "operator_required",
                            "failure_type": "operator_clearance_required",
                            "route": "operator_required",
                            "operator_required": True,
                            "retryable": False,
                        },
                    },
                )
            ]
        )
    )

    assert operator_packet.classification == FailureClass.OPERATOR_REQUIRED
    assert operator_packet.citations == ["event:control_plane_runtime_failure:evidence_node:949"]

    retry_packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "typed route decision retries verifier",
                    metadata={
                        "evidence_node_id": 951,
                        "group_idx": 55,
                        "attempt_id": 96,
                        "failure_class": "runtime_context",
                        "failure_type": "context_materialization_failed",
                        "route": "run_product_repair",
                        "operator_required": False,
                        "retryable": False,
                        "deterministic": True,
                        "route_decision": {
                            "failure_class": "verifier_context",
                            "failure_type": "context_materialization_failed",
                            "route": "retry_verifier",
                            "operator_required": False,
                            "retryable": True,
                        },
                    },
                )
            ]
        )
    )

    assert retry_packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    assert retry_packet.facts["runtime_failure_events"][0]["route"] == "retry_verifier"
    assert retry_packet.facts["runtime_failure_events"][0]["retryable"] is True

    nondeterministic_packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "typed route decision is not deterministic",
                    metadata={
                        "evidence_node_id": 952,
                        "group_idx": 56,
                        "attempt_id": 97,
                        "failure_class": "runtime_context",
                        "failure_type": "context_materialization_failed",
                        "route": "workflow_unblock",
                        "operator_required": False,
                        "retryable": True,
                        "deterministic": True,
                        "route_decision": {
                            "failure_class": "runtime_context",
                            "failure_type": "context_materialization_failed",
                            "route": "unknown",
                            "operator_required": False,
                            "retryable": True,
                            "deterministic": False,
                        },
                    },
                )
            ]
        )
    )

    assert nondeterministic_packet.classification != FailureClass.DETERMINISTIC_UNBLOCK


def test_contract_violation_runtime_failure_needs_product_evidence_for_product_repair():
    without_product_evidence = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "contract violation at runtime boundary",
                    metadata={
                        "evidence_node_id": 949,
                        "group_idx": 54,
                        "attempt_id": 95,
                        "failure_class": "contract_violation",
                        "failure_type": "semantic_contract_failed",
                        "route": "run_product_repair",
                        "operator_required": False,
                        "retryable": False,
                        "deterministic": False,
                    },
                )
            ]
        )
    )
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "contract violation in canonical product file",
                    metadata={
                        "evidence_node_id": 948,
                        "group_idx": 54,
                        "attempt_id": 94,
                        "failure_class": "contract_violation",
                        "failure_type": "semantic_contract_failed",
                        "route": "run_product_repair",
                        "operator_required": False,
                        "retryable": False,
                        "deterministic": False,
                        "canonical_product_files": ["src/product/Widget.tsx"],
                    },
                )
            ]
        )
    )

    assert without_product_evidence.classification == FailureClass.WATCH_ONLY
    assert without_product_evidence.classification != FailureClass.NORMAL_PRODUCT_REPAIR
    assert packet.classification == FailureClass.NORMAL_PRODUCT_REPAIR
    assert packet.recommended_action == ActionLevel.OBSERVE


def test_product_defect_runtime_failure_routes_to_product_repair():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "product defect still failing verifier",
                    metadata={
                        "evidence_node_id": 919,
                        "group_idx": 50,
                        "attempt_id": 82,
                        "failure_class": "product_defect",
                        "failure_type": "assertion_failed",
                        "route": "product_repair",
                        "operator_required": False,
                        "retryable": False,
                        "deterministic": True,
                    },
                )
            ]
        )
    )

    assert packet.classification == FailureClass.NORMAL_PRODUCT_REPAIR
    assert packet.recommended_action == ActionLevel.OBSERVE
    assert packet.citations == ["event:control_plane_runtime_failure:evidence_node:919"]


def test_product_defect_quiesce_runtime_failure_escalates_not_product_repair():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "product repair budget exhausted",
                    metadata={
                        "evidence_node_id": 937,
                        "group_idx": 50,
                        "attempt_id": 91,
                        "failure_class": "product_defect",
                        "failure_type": "semantic_verifier_rejected",
                        "route": "quiesce",
                        "operator_required": False,
                        "retryable": False,
                        "deterministic": False,
                    },
                )
            ]
        )
    )

    assert packet.classification == FailureClass.PIPELINE_BUG_SUSPECTED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert packet.classification != FailureClass.NORMAL_PRODUCT_REPAIR
    assert packet.citations == ["event:control_plane_runtime_failure:evidence_node:937"]


def test_product_defect_resource_exhausted_runtime_failure_does_not_product_repair():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "product repair resource exhausted",
                    metadata={
                        "evidence_node_id": 938,
                        "group_idx": 50,
                        "attempt_id": 92,
                        "failure_class": "product_defect",
                        "failure_type": "semantic_verifier_rejected",
                        "route": "resource_exhausted",
                        "operator_required": False,
                        "retryable": False,
                        "deterministic": False,
                    },
                )
            ]
        )
    )

    assert packet.classification == FailureClass.WATCH_ONLY
    assert packet.recommended_action == ActionLevel.OBSERVE
    assert packet.classification != FailureClass.NORMAL_PRODUCT_REPAIR


def test_product_defect_runtime_failure_clears_after_successful_verify():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    920,
                    "product defect failed before verifier success",
                    metadata={
                        "evidence_node_id": 920,
                        "group_idx": 50,
                        "attempt_id": 82,
                        "failure_class": "product_defect",
                        "failure_type": "assertion_failed",
                        "route": "product_repair",
                        "operator_required": False,
                        "retryable": False,
                        "deterministic": True,
                    },
                )
            ],
            artifacts=[
                _artifact(
                    "dag-verify:g50:retry-1",
                    {"approved": True, "summary": "Verifier approved after product repair."},
                    1678810,
                )
            ],
        )
    )

    assert packet.classification != FailureClass.NORMAL_PRODUCT_REPAIR
    assert packet.classification != FailureClass.DETERMINISTIC_UNBLOCK


def test_product_defect_runtime_failure_clears_after_successful_verify_graph():
    failed_at = datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc)
    approved_at = datetime(2026, 5, 20, 15, 5, tzinfo=timezone.utc)

    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    921,
                    "product defect failed before graph verifier success",
                    metadata={
                        "evidence_node_id": 921,
                        "group_idx": 50,
                        "attempt_id": 83,
                        "failure_class": "product_defect",
                        "failure_type": "assertion_failed",
                        "route": "product_repair",
                        "operator_required": False,
                        "retryable": False,
                        "deterministic": True,
                    },
                    created_at=failed_at,
                )
            ],
            artifacts=[
                ArtifactRecord(
                    id=1678811,
                    key="dag-verify-graph:g50:retry-1",
                    value={
                        "approved": True,
                        "aggregate": {"approved": True},
                        "aggregate_node": {"status": "approved"},
                    },
                    created_at=approved_at,
                )
            ],
        )
    )

    assert packet.classification == FailureClass.HEALTHY_PROGRESS
    assert packet.classification != FailureClass.NORMAL_PRODUCT_REPAIR
    assert packet.citations == ["artifact:dag-verify-graph:g50:retry-1 id=1678811"]


def test_explicit_operator_required_runtime_provider_event_escalates():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "runtime_provider: provider crashed",
                    metadata={
                        "evidence_node_id": 917,
                        "group_idx": 48,
                        "failure_class": "runtime_provider",
                        "failure_type": "provider_crash",
                        "operator_required": True,
                        "deterministic": True,
                        "retryable": False,
                    },
                )
            ]
        )
    )

    assert packet.classification == FailureClass.OPERATOR_REQUIRED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert packet.citations == ["event:control_plane_runtime_failure:evidence_node:917"]


def test_typed_control_plane_retryable_failure_does_not_escalate_operator_required():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "runtime provider rate limited",
                    metadata={
                        "evidence_node_id": 916,
                        "group_idx": 48,
                        "failure_class": "runtime_provider",
                        "failure_type": "provider_rate_limited",
                        "operator_required": False,
                        "retryable": True,
                    },
                )
            ]
        )
    )

    assert packet.classification == FailureClass.WATCH_ONLY
    assert packet.recommended_action == ActionLevel.OBSERVE


def test_typed_operator_required_event_can_be_cleared_by_later_acl_success():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "control_plane_runtime_failure",
                    0,
                    "workspace_permission: operator required",
                    metadata={
                        "evidence_node_id": 915,
                        "group_idx": 48,
                        "failure_class": "workspace_permission",
                        "failure_type": "writeability_denied",
                        "operator_required": True,
                    },
                    created_at=datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc),
                )
            ],
            artifacts=[
                ArtifactRecord(
                    id=1678791,
                    key="dag-workspace-acl-normalization:g48:initial-dispatch",
                    value={"operator_required": False, "changed": [{"path": "impl"}]},
                    created_at=datetime(2026, 5, 20, 15, 5, tzinfo=timezone.utc),
                )
            ],
        )
    )

    assert packet.classification != FailureClass.OPERATOR_REQUIRED


def test_successful_stale_reconcile_artifact_clears_deterministic_blocker():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-repair-preflight:g30:retry-initial",
                    {
                        "status": "failed",
                        "path_problems": [
                            {
                                "path": "src/legacy/chat/ChatPane.tsx",
                                "reason": "retired path in generated snapshot",
                            }
                        ],
                    },
                    1044830,
                ),
                _artifact(
                    "dag-task-spec-reconcile:g30:retry-initial",
                    {
                        "applied": True,
                        "changed": [
                            {
                                "path": "src/legacy/chat/ChatPane.tsx",
                                "canonical_path": "src/chat/ChatPane.tsx",
                                "reason": "stale path reconciled",
                            }
                        ],
                    },
                    1052604,
                ),
            ]
        )
    )

    assert packet.classification != FailureClass.DETERMINISTIC_UNBLOCK


def test_operator_required_artifact_can_be_cleared_by_later_acl_success():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-writeability-preflight:g48:retry-0",
                    {"operator_required": True, "problems": [{"reason": "writeability_denied"}]},
                    1678790,
                ),
                _artifact(
                    "dag-workspace-acl-normalization:g48:initial-dispatch",
                    {"operator_required": False, "changed": [{"path": "impl"}]},
                    1678791,
                ),
            ]
        )
    )

    assert packet.classification != FailureClass.OPERATOR_REQUIRED


def test_dead_bridge_with_no_active_agent_is_safe_restart_candidate():
    packet = classify_observation(
        _observation(
            bridge=BridgeProbe(
                dashboard_url="http://127.0.0.1:8080",
                ok=True,
                status={"state": "dead", "running": False},
                errors=["Traceback: bridge crashed"],
            )
        )
    )

    assert packet.classification == FailureClass.SAFE_RESTART_CANDIDATE
    assert packet.recommended_action == ActionLevel.RECOMMEND


def test_slack_reconnect_noise_does_not_recommend_restart():
    packet = classify_observation(
        _observation(
            bridge=BridgeProbe(
                ok=True,
                status={"state": "running"},
                errors=[
                    "ERROR slack_sdk.socket_mode.aiohttp: Cannot write to closing transport",
                    "INFO slack_sdk.socket_mode.aiohttp: Reconnecting session",
                    "Traceback (most recent call last):",
                ],
            )
        )
    )

    assert packet.classification == FailureClass.WATCH_ONLY
    assert packet.recommended_action == ActionLevel.OBSERVE


def test_claude_readiness_probe_noise_does_not_recommend_restart():
    packet = classify_observation(
        _observation(
            bridge=BridgeProbe(
                ok=True,
                status={"state": "running"},
                errors=[
                    "INFO iriai_build_v2.runtimes.claude_pool: Claude pool profile "
                    "readiness probe failed: RuntimeError('Claude availability probe "
                    "failed with exit code 1: {\"api_error_status\":403}')"
                ],
            )
        )
    )

    assert packet.classification == FailureClass.WATCH_ONLY
    assert packet.recommended_action == ActionLevel.OBSERVE


@pytest.mark.asyncio
async def test_read_only_policy_blocks_restart_even_when_candidate():
    packet = classify_observation(
        _observation(
            bridge=BridgeProbe(ok=True, status={"state": "dead"}, errors=["Traceback"])
        )
    )
    called = False

    async def restart():
        nonlocal called
        called = True
        return {"running": True}

    record = await ActionPolicy(mode=SupervisorMode.READ_ONLY, restart=restart).maybe_restart(packet)

    assert record.status == SupervisorActionStatus.BLOCKED
    assert called is False
    assert "recommend-only" in record.reason


@pytest.mark.asyncio
async def test_guarded_policy_runs_injected_restart_callable():
    packet = classify_observation(
        _observation(
            bridge=BridgeProbe(ok=True, status={"state": "dead"}, errors=["Traceback"])
        )
    ).model_copy(
        update={"recommended_action": ActionLevel.ACT_GUARDED}
    )

    async def restart():
        return {"running": True, "state": "running"}

    record = await ActionPolicy(mode=SupervisorMode.GUARDED, restart=restart).maybe_restart(packet)

    assert record.status == SupervisorActionStatus.COMPLETED
    assert record.after == {"running": True, "state": "running"}


@pytest.mark.asyncio
async def test_guarded_policy_blocks_recommend_only_restart_without_active_flag():
    packet = classify_observation(
        _observation(
            events=[
                _event(
                    "agent_invocation_start",
                    29042,
                    "implementer-g48-t1-a0",
                    metadata={"group_idx": 48},
                )
            ],
            bridge=BridgeProbe(ok=True, status={"state": "dead"}, errors=["Traceback"]),
        )
    )
    called = False

    async def restart():
        nonlocal called
        called = True
        return {"running": True}

    record = await ActionPolicy(mode=SupervisorMode.GUARDED, restart=restart).maybe_restart(packet)

    assert packet.classification == FailureClass.SAFE_RESTART_CANDIDATE
    assert packet.recommended_action == ActionLevel.RECOMMEND
    assert record.status == SupervisorActionStatus.BLOCKED
    assert called is False
    assert "recommend-only" in record.reason


@pytest.mark.asyncio
async def test_guarded_policy_writes_before_and_after_action_artifacts():
    packet = classify_observation(
        _observation(
            bridge=BridgeProbe(ok=True, status={"state": "dead"}, errors=["Traceback"])
        )
    ).model_copy(
        update={"recommended_action": ActionLevel.ACT_GUARDED}
    )
    feature = object()

    class Sink:
        def __init__(self):
            self.records = []

        async def put(self, key, value, *, feature):
            self.records.append((key, value, feature))

    sink = Sink()

    record = await ActionPolicy(
        mode=SupervisorMode.GUARDED,
        restart=lambda: {"running": True},
        artifact_sink=sink,
        feature=feature,
    ).maybe_restart(packet)

    assert record.status == SupervisorActionStatus.COMPLETED
    assert [item[0] for item in sink.records] == [
        "supervisor-action:8ac124d6:200:restart_bridge:planned",
        "supervisor-action:8ac124d6:200:restart_bridge:completed",
    ]
    assert all(item[2] is feature for item in sink.records)


@pytest.mark.asyncio
async def test_guarded_restart_blocks_active_invocation_when_bridge_is_only_wedged():
    packet = classify_observation(
        _observation(
            bridge=BridgeProbe(
                ok=True,
                status={"state": "running"},
                errors=["Resumed workflow failed for 8ac124d6 RuntimeError: commit failed"],
            )
        )
    ).model_copy(
        update={"recommended_action": ActionLevel.ACT_GUARDED}
    )

    async def restart():
        raise AssertionError("restart should not run")

    record = await ActionPolicy(mode=SupervisorMode.GUARDED, restart=restart).maybe_restart(
        packet,
        active_invocation=True,
    )

    assert record.status == SupervisorActionStatus.BLOCKED
    assert "Active invocation" in record.reason
