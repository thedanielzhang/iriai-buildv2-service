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
    worktrees: list[WorktreeProbe] | None = None,
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
        worktrees=worktrees or [],
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


def test_g37_raw_gate_checkpoint_contradiction_escalates():
    packet = classify_observation(
        _observation(
            artifacts=[
                _artifact(
                    "dag-verify:g37:initial",
                    {"status": "failed", "summary": "raw verifier/preflight failed"},
                    1273016,
                ),
                _artifact("dag-group:37", {"status": "checkpointed"}, 1273018),
            ],
            events=[
                _event("dag_verify_finish", 23309, "failed"),
                _event("dag_checkpoint_written", 23310, "checkpointed group 37"),
            ],
        )
    )

    assert packet.classification == FailureClass.PIPELINE_BUG_SUSPECTED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert packet.group_idx == 37
    assert "event:23310" in packet.citations


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
    assert packet.group_idx == 38


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
    assert packet.recommended_action == ActionLevel.ACT_GUARDED


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
    assert "Read-only mode" in record.reason


@pytest.mark.asyncio
async def test_guarded_policy_runs_injected_restart_callable():
    packet = classify_observation(
        _observation(
            bridge=BridgeProbe(ok=True, status={"state": "dead"}, errors=["Traceback"])
        )
    )

    async def restart():
        return {"running": True, "state": "running"}

    record = await ActionPolicy(mode=SupervisorMode.GUARDED, restart=restart).maybe_restart(packet)

    assert record.status == SupervisorActionStatus.COMPLETED
    assert record.after == {"running": True, "state": "running"}


@pytest.mark.asyncio
async def test_guarded_policy_writes_before_and_after_action_artifacts():
    packet = classify_observation(
        _observation(
            bridge=BridgeProbe(ok=True, status={"state": "dead"}, errors=["Traceback"])
        )
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
    )

    async def restart():
        raise AssertionError("restart should not run")

    record = await ActionPolicy(mode=SupervisorMode.GUARDED, restart=restart).maybe_restart(
        packet,
        active_invocation=True,
    )

    assert record.status == SupervisorActionStatus.BLOCKED
    assert "Active invocation" in record.reason
