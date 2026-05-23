from __future__ import annotations

from types import SimpleNamespace

import pytest

from iriai_build_v2.supervisor.actions import ActionPolicy
from iriai_build_v2.supervisor.classifier import classify_observation
from iriai_build_v2.supervisor.models import (
    ActionLevel,
    BridgeProbe,
    CurrentWorkflowSnapshot,
    EventRecord,
    EvidencePacket,
    FailureClass,
    StaleCodexInvocation,
    SupervisorMode,
    SupervisorObservation,
    SupervisorActionStatus,
)
from iriai_build_v2.supervisor.slack_blocks import SupervisorStaleInvocationCard, _validate_block_kit
from iriai_build_v2.supervisor.stale_codex import detect_stale_codex_invocations


def _run_result(stdout: str = "", returncode: int = 0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


def test_detector_fires_for_g43_style_stale_codex(monkeypatch: pytest.MonkeyPatch):
    actor = "implementer-g43-t19-a0"
    trace = (
        "/Users/danielzhang/src/iriai/.iriai/runtime/codex/traces/"
        "20260510T191357.256022Z-implementer-g43-t19-a0-3737e65c.jsonl"
    )

    def fake_run(cmd, **_kwargs):
        if cmd[0] == "ps":
            return _run_result(
                "51130 40159 0.0 0.1 node /Users/bin/codex exec "
                "-o /Users/danielzhang/src/iriai/.iriai/runtime/codex/tmp.txt "
                "-C /Users/danielzhang/src/iriai/.iriai/features/"
                "visual-studio-code-frontend-for-project-workflow-manager-8ac124d6/repos -"
            )
        if cmd[0] == "pgrep":
            return _run_result("51132\n")
        raise AssertionError(cmd)

    monkeypatch.setattr("iriai_build_v2.supervisor.stale_codex.subprocess.run", fake_run)

    bridge = BridgeProbe(
        ok=True,
        log_lines=[
            (
                "16:46:58 INFO iriai_build_v2.runtimes.codex: "
                f"Codex heartbeat pid=51130 elapsed=16381s trace={trace} "
                "stdout_events=5 stderr_lines=0 output_bytes=0 "
                "last_event=item.completed last_item=command_execution"
            ),
            (
                "16:47:58 INFO iriai_build_v2.runtimes.codex: "
                f"Codex heartbeat pid=51130 elapsed=16441s trace={trace} "
                "stdout_events=5 stderr_lines=0 output_bytes=0 "
                "last_event=item.completed last_item=command_execution"
            ),
        ],
    )
    events = [
        EventRecord(
            id=27784,
            event_type="agent_invocation_start",
            source=actor,
            metadata={
                "group_idx": 43,
                "retry": 0,
                "task_id": "T-SF6-S6-locks",
                "invocation_id": "4328208ee9fc43cc9895e34ec1aad7b4",
                "liveness_timeout_seconds": 600,
            },
        )
    ]

    stale = detect_stale_codex_invocations(
        feature_id="8ac124d6",
        bridge=bridge,
        events=events,
        current=CurrentWorkflowSnapshot(group_idx=43, active_agents=[actor]),
    )

    assert len(stale) == 1
    assert stale[0].actor == actor
    assert stale[0].pid == 51130
    assert stale[0].child_pids == [51132]
    assert stale[0].stable_heartbeat_count == 2
    assert stale[0].group_idx == 43
    assert stale[0].task_id == "T-SF6-S6-locks"


def test_detector_ignores_active_codex_with_changing_output(monkeypatch: pytest.MonkeyPatch):
    trace = "/tmp/20260510T191357.256022Z-implementer-g43-t19-a0-3737e65c.jsonl"

    def fake_run(cmd, **_kwargs):
        if cmd[0] == "ps":
            return _run_result(
                "51130 40159 0.0 0.1 codex exec -C "
                "/Users/danielzhang/src/iriai/.iriai/features/feature-8ac124d6/repos -"
            )
        if cmd[0] == "pgrep":
            return _run_result("")
        raise AssertionError(cmd)

    monkeypatch.setattr("iriai_build_v2.supervisor.stale_codex.subprocess.run", fake_run)
    bridge = BridgeProbe(
        ok=True,
        log_lines=[
            (
                f"Codex heartbeat pid=51130 elapsed=3600s trace={trace} "
                "stdout_events=5 stderr_lines=0 output_bytes=0 "
                "last_event=item.completed last_item=command_execution"
            ),
            (
                f"Codex heartbeat pid=51130 elapsed=3660s trace={trace} "
                "stdout_events=6 stderr_lines=0 output_bytes=12 "
                "last_event=item.completed last_item=command_execution"
            ),
        ],
    )

    assert (
        detect_stale_codex_invocations(
            feature_id="8ac124d6",
            bridge=bridge,
            events=[],
            current=CurrentWorkflowSnapshot(group_idx=43),
        )
        == []
    )


def test_detector_rejects_overlapping_feature_id_prefix(
    monkeypatch: pytest.MonkeyPatch,
):
    trace = "/tmp/20260510T191357.256022Z-implementer-g43-t19-a0-3737e65c.jsonl"

    def fake_run(cmd, **_kwargs):
        if cmd[0] == "ps":
            return _run_result(
                "51130 40159 0.0 0.1 codex exec -C "
                "/Users/danielzhang/src/iriai/.iriai/features/feature-8ac124d6abc/repos -"
            )
        if cmd[0] == "pgrep":
            return _run_result("")
        raise AssertionError(cmd)

    monkeypatch.setattr("iriai_build_v2.supervisor.stale_codex.subprocess.run", fake_run)
    bridge = BridgeProbe(
        ok=True,
        log_lines=[
            (
                f"Codex heartbeat pid=51130 elapsed=3600s trace={trace} "
                "stdout_events=5 stderr_lines=0 output_bytes=0 "
                "last_event=item.completed last_item=command_execution"
            ),
            (
                f"Codex heartbeat pid=51130 elapsed=3660s trace={trace} "
                "stdout_events=5 stderr_lines=0 output_bytes=0 "
                "last_event=item.completed last_item=command_execution"
            ),
        ],
    )

    assert (
        detect_stale_codex_invocations(
            feature_id="8ac124d6",
            bridge=bridge,
            events=[],
            current=CurrentWorkflowSnapshot(group_idx=43),
        )
        == []
    )


def test_classifier_routes_stale_codex_before_generic_progress():
    packet = classify_observation(
        SupervisorObservation(
            feature_id="8ac124d6",
            phase="implementation",
            current=CurrentWorkflowSnapshot(
                group_idx=43,
                state="implementing",
                active_agents=["implementer-g43-t19-a0"],
            ),
            stale_codex_invocations=[
                StaleCodexInvocation(
                    actor="implementer-g43-t19-a0",
                    group_idx=43,
                    pid=51130,
                    child_pids=[51132],
                    trace_path="/tmp/trace.jsonl",
                    command="codex exec -C /tmp/feature-8ac124d6/repos -",
                    elapsed_seconds=16_441,
                    stable_heartbeat_count=2,
                    evidence_token="tok123",
                    citations=["dashboard:/api/bridge/logs"],
                )
            ],
        )
    )

    assert packet.classification == FailureClass.STALE_CODEX_INVOCATION
    assert packet.recommended_action == ActionLevel.RECOMMEND
    assert packet.facts["stale_codex_invocation"]["pid"] == 51130


@pytest.mark.asyncio
async def test_action_policy_read_only_refuses_kill():
    packet = _stale_packet()
    record = await ActionPolicy(mode=SupervisorMode.READ_ONLY).maybe_kill_stale_codex(
        packet,
        evidence_token="tok123",
    )

    assert record.status == SupervisorActionStatus.BLOCKED
    assert "Read-only mode" in record.reason


@pytest.mark.asyncio
async def test_action_policy_guarded_kills_exact_codex_tree(monkeypatch: pytest.MonkeyPatch):
    packet = _stale_packet()
    packet.facts["stale_codex_invocation"]["descendant_pids"] = [51132, 51133]
    alive = {51130, 51132, 51133}
    killed: list[tuple[int, int]] = []

    def fake_run(cmd, **_kwargs):
        if cmd[0] == "ps":
            return _run_result(
                "51130 40159 0.0 0.1 codex exec -C "
                "/Users/danielzhang/src/iriai/.iriai/features/feature-8ac124d6/repos -"
            )
        if cmd[0] == "pgrep":
            if cmd[-1] == "51130":
                return _run_result("51132\n")
            if cmd[-1] == "51132":
                return _run_result("51133\n")
            return _run_result("")
        raise AssertionError(cmd)

    def fake_kill(pid, sig):
        if sig == 0:
            if pid not in alive:
                raise ProcessLookupError(pid)
            return
        killed.append((pid, sig))
        alive.discard(pid)

    monkeypatch.setattr("iriai_build_v2.supervisor.actions.subprocess.run", fake_run)
    monkeypatch.setattr("iriai_build_v2.supervisor.actions.os.kill", fake_kill)
    monkeypatch.setattr("iriai_build_v2.supervisor.actions.time.sleep", lambda _seconds: None)

    record = await ActionPolicy(mode=SupervisorMode.GUARDED).maybe_kill_stale_codex(
        packet,
        evidence_token="tok123",
    )

    assert record.status == SupervisorActionStatus.COMPLETED
    assert record.before["current_descendant_pids"] == [51132, 51133]
    assert record.after["terminated_pids"] == [51133, 51132, 51130]
    assert killed[:3] == [(51133, 15), (51132, 15), (51130, 15)]


@pytest.mark.asyncio
async def test_action_policy_guarded_refuses_new_descendant(
    monkeypatch: pytest.MonkeyPatch,
):
    packet = _stale_packet()
    packet.facts["stale_codex_invocation"]["descendant_pids"] = [51132]

    def fake_run(cmd, **_kwargs):
        if cmd[0] == "ps":
            return _run_result(
                "51130 40159 0.0 0.1 codex exec -C "
                "/Users/danielzhang/src/iriai/.iriai/features/feature-8ac124d6/repos -"
            )
        if cmd[0] == "pgrep":
            if cmd[-1] == "51130":
                return _run_result("51132\n")
            if cmd[-1] == "51132":
                return _run_result("51133\n")
            return _run_result("")
        raise AssertionError(cmd)

    monkeypatch.setattr("iriai_build_v2.supervisor.actions.subprocess.run", fake_run)

    record = await ActionPolicy(mode=SupervisorMode.GUARDED).maybe_kill_stale_codex(
        packet,
        evidence_token="tok123",
    )

    assert record.status == SupervisorActionStatus.BLOCKED
    assert "process tree" in record.reason
    assert record.before["current_descendant_pids"] == [51132, 51133]
    assert record.before["expected_descendant_pids"] == [51132]
    assert record.before["extra_descendant_pids"] == [51133]


@pytest.mark.asyncio
async def test_action_policy_refuses_stale_codex_tree_for_other_feature(
    monkeypatch: pytest.MonkeyPatch,
):
    packet = _stale_packet()
    packet.facts["stale_codex_invocation"]["command"] = (
        "codex exec -C /Users/danielzhang/src/iriai/.iriai/features/feature-other/repos -"
    )

    def fake_run(cmd, **_kwargs):
        if cmd[0] == "ps":
            return _run_result(
                "51130 40159 0.0 0.1 codex exec -C "
                "/Users/danielzhang/src/iriai/.iriai/features/feature-other/repos -"
            )
        if cmd[0] == "pgrep":
            return _run_result("")
        raise AssertionError(cmd)

    monkeypatch.setattr("iriai_build_v2.supervisor.actions.subprocess.run", fake_run)

    record = await ActionPolicy(mode=SupervisorMode.GUARDED).maybe_kill_stale_codex(
        packet,
        evidence_token="tok123",
    )

    assert record.status == SupervisorActionStatus.BLOCKED
    assert "exact feature" in record.reason


@pytest.mark.asyncio
async def test_action_policy_refuses_overlapping_feature_id_prefix(
    monkeypatch: pytest.MonkeyPatch,
):
    packet = _stale_packet()
    packet.facts["stale_codex_invocation"]["command"] = (
        "codex exec -C "
        "/Users/danielzhang/src/iriai/.iriai/features/feature-8ac124d6abc/repos -"
    )

    def fake_run(cmd, **_kwargs):
        if cmd[0] == "ps":
            return _run_result(
                "51130 40159 0.0 0.1 codex exec -C "
                "/Users/danielzhang/src/iriai/.iriai/features/feature-8ac124d6abc/repos -"
            )
        if cmd[0] == "pgrep":
            return _run_result("")
        raise AssertionError(cmd)

    monkeypatch.setattr("iriai_build_v2.supervisor.actions.subprocess.run", fake_run)

    record = await ActionPolicy(mode=SupervisorMode.GUARDED).maybe_kill_stale_codex(
        packet,
        evidence_token="tok123",
    )

    assert record.status == SupervisorActionStatus.BLOCKED
    assert "exact feature" in record.reason


@pytest.mark.asyncio
async def test_action_policy_refuses_live_command_without_exact_feature_workspace(
    monkeypatch: pytest.MonkeyPatch,
):
    packet = _stale_packet()
    packet.facts["stale_codex_invocation"]["trace_path"] = (
        "/Users/danielzhang/src/iriai/.iriai/features/feature-8ac124d6/"
        "runtime/codex/trace.jsonl"
    )

    def fake_run(cmd, **_kwargs):
        if cmd[0] == "ps":
            return _run_result(
                "51130 40159 0.0 0.1 codex exec "
                "-o /tmp/8ac124d6-output.jsonl "
                "-C /tmp/outside-workspace -"
            )
        if cmd[0] == "pgrep":
            return _run_result("")
        raise AssertionError(cmd)

    monkeypatch.setattr("iriai_build_v2.supervisor.actions.subprocess.run", fake_run)

    record = await ActionPolicy(mode=SupervisorMode.GUARDED).maybe_kill_stale_codex(
        packet,
        evidence_token="tok123",
    )

    assert record.status == SupervisorActionStatus.BLOCKED
    assert "exact feature" in record.reason


def test_stale_invocation_card_uses_new_block_kit_components():
    card = SupervisorStaleInvocationCard(_stale_packet(), mode=SupervisorMode.READ_ONLY)
    blocks = card.build_blocks()
    types = [block["type"] for block in blocks]

    assert set(types) <= {"header", "section", "context", "actions", "divider"}
    assert "header" in types
    assert "actions" in types
    actions = next(block for block in blocks if block["type"] == "actions")
    assert actions["elements"][0]["type"] == "button"
    assert actions["elements"][0]["text"]["text"] == "Needs guarded mode"


def test_slack_block_validator_rejects_unknown_block_types():
    with pytest.raises(ValueError, match="invalid Slack Block Kit block type"):
        _validate_block_kit([{"type": "alert", "text": "legacy"}])


def _stale_packet() -> EvidencePacket:
    stale = StaleCodexInvocation(
        actor="implementer-g43-t19-a0",
        invocation_id="4328208ee9fc43cc9895e34ec1aad7b4",
        group_idx=43,
        retry=0,
        task_id="T-SF6-S6-locks",
        pid=51130,
        parent_pid=40159,
        child_pids=[51132],
        cpu_percent=0.0,
        mem_percent=0.1,
        command=(
            "codex exec -C "
            "/Users/danielzhang/src/iriai/.iriai/features/feature-8ac124d6/repos -"
        ),
        trace_path="/tmp/20260510T191357.256022Z-implementer-g43-t19-a0-3737e65c.jsonl",
        output_path="/tmp/output.txt",
        elapsed_seconds=16_441,
        idle_seconds=16_441,
        stdout_events=5,
        stderr_lines=0,
        output_bytes=0,
        last_event="item.completed",
        last_item="command_execution",
        heartbeat_count=2,
        stable_heartbeat_count=2,
        evidence_token="tok123",
        citations=["dashboard:/api/bridge/logs"],
    )
    return EvidencePacket(
        feature_id="8ac124d6",
        group_idx=43,
        retry=0,
        phase="implementation",
        classification=FailureClass.STALE_CODEX_INVOCATION,
        confidence=0.93,
        facts={
            "next_cursor": 1,
            "stale_codex_invocation": stale.model_dump(mode="json"),
        },
        inference="A Codex invocation is alive but heartbeat-only stale.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["dashboard:/api/bridge/logs"],
    )
