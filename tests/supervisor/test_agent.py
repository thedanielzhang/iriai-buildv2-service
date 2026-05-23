from __future__ import annotations

import json

import pytest

from iriai_build_v2.supervisor.agent import SupervisorAgent
from iriai_build_v2.runtimes.codex import CodexAgentRuntime
from iriai_build_v2.supervisor.models import (
    ActionLevel,
    ArtifactRecord,
    EvidencePacket,
    FailureClass,
    SupervisorEvidenceBundle,
    SupervisorInvestigationRequest,
)


class _FakeRuntime:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.roles: list[object] = []
        self.kwargs: list[dict] = []

    async def invoke(self, role, prompt: str, **kwargs):
        self.roles.append(role)
        self.kwargs.append(kwargs)
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            return json.dumps(
                {
                    "type": "request_evidence",
                    "requests": [
                        {
                            "reason": "Need latest G38 retry row before answering.",
                            "artifact_keys": ["dag-verify:g38:retry-0"],
                            "event_after_id": 24000,
                        }
                    ],
                }
            )
        return json.dumps(
            {
                "type": "assessment",
                "assessment": {
                    "status": "healthy_progress",
                    "message": "Facts: latest G38 retry is approved. Inference: current run is healthy.",
                    "facts": ["latest G38 retry is approved"],
                    "inferences": ["current run is healthy"],
                    "citations": ["artifact:dag-verify:g38:retry-0 id=1360854"],
                    "confidence": 0.9,
                    "recommended_action": "digest",
                },
            }
        )


class _NonEnumActionRuntime:
    async def invoke(self, _role, _prompt: str, **_kwargs):
        return json.dumps(
            {
                "type": "assessment",
                "assessment": {
                    "status": "blocked_on_commit_hygiene",
                    "message": "Workflow is blocked by commit hygiene.",
                    "facts": ["Commit failure artifacts are present."],
                    "inferences": ["Focused commit hygiene is appropriate."],
                    "citations": ["artifact:dag-commit-failure:g38:retry-0 id=1353600"],
                    "confidence": 0.88,
                    "recommended_action": "repair_commit_hygiene",
                    "proposed_action": None,
                },
            }
        )


class _EscalatingDeterministicRuntime:
    async def invoke(self, _role, _prompt: str, **_kwargs):
        return json.dumps(
            {
                "type": "assessment",
                "assessment": {
                    "status": "workflow_blocked",
                    "message": "The deterministic workflow blocker needs the typed unblock path.",
                    "facts": ["Commit hygiene route is deterministic."],
                    "inferences": ["Escalate because it is blocked."],
                    "citations": ["artifact:dag-direct-repair-route:g38:retry-0 id=1"],
                    "confidence": 0.9,
                    "recommended_action": "stop/escalate",
                    "proposed_action": "supervisor_maintainer_dry_run",
                },
            }
        )


class _MalformedJsonRuntime:
    async def invoke(self, _role, _prompt: str, **_kwargs):
        return '{"type":"assessment","assessment":{"message":"This JSON should not be posted raw."'


class _OverflowRuntime:
    async def invoke(self, _role, _prompt: str, **_kwargs):
        raise RuntimeError("turn/start failed: Input exceeds the maximum length of 1048576 characters.")


class _PromptRecordingAssessmentRuntime:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def invoke(self, _role, prompt: str, **_kwargs):
        self.prompts.append(prompt)
        return json.dumps(
            {
                "type": "assessment",
                "assessment": {
                    "status": "current_failure_identified",
                    "message": "Latest verifier failure is product-level and cited by artifact id.",
                    "facts": ["artifact index retained the exact citation"],
                    "inferences": ["raw detail can be requested by chunk ref"],
                    "citations": ["artifact:dag-verify:g39:initial id=1420690"],
                    "confidence": 0.86,
                    "recommended_action": "observe",
                },
            }
        )


class _StaleCommitBlockerRuntime:
    async def invoke(self, _role, _prompt: str, **_kwargs):
        return json.dumps(
            {
                "type": "assessment",
                "assessment": {
                    "status": "blocked_on_commit_hygiene",
                    "message": "Workflow is currently blocked by commit hook failures.",
                    "facts": ["Commit failure artifacts are present."],
                    "inferences": ["Focused commit hygiene is appropriate."],
                    "citations": ["artifact:dag-commit-failure:g38:retry-0 id=1353600"],
                    "confidence": 0.88,
                    "recommended_action": "recommend",
                },
            }
        )


class _AssessmentRuntime:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.roles: list[object] = []

    async def invoke(self, role, prompt: str, **_kwargs):
        self.roles.append(role)
        self.prompts.append(prompt)
        return json.dumps(
            {
                "type": "assessment",
                "assessment": {
                    "status": "root_cause_identified",
                    "message": "Latest failure was a commit hook import-pattern error; retry fix removed it.",
                    "facts": ["commit hook import-pattern error"],
                    "inferences": ["focused commit hygiene was the right repair"],
                    "citations": [
                        "artifact:dag-commit-failure:g39:implementation id=1368712",
                        "artifact:dag-fix:g39:retry-0 id=1369000",
                    ],
                    "confidence": 0.91,
                    "recommended_action": "digest",
                },
            }
        )


class _FakeToolbox:
    def __init__(self) -> None:
        self.requests: list[SupervisorInvestigationRequest] = []

    async def gather(self, request):
        self.requests.append(request)
        return SupervisorEvidenceBundle(
            request=request,
            artifacts=[
                ArtifactRecord(
                    id=1360854,
                    key="dag-verify:g38:retry-0",
                    value={"approved": True},
                )
            ],
        )

    async def gather_many(self, requests):
        self.requests.extend(requests)
        return [
            SupervisorEvidenceBundle(
                request=requests[0],
                artifacts=[
                    ArtifactRecord(
                        id=1360854,
                        key="dag-verify:g38:retry-0",
                        value={"approved": True},
                    )
                ],
            )
        ]


def _packet() -> EvidencePacket:
    return EvidencePacket(
        feature_id="8ac124d6",
        group_idx=38,
        retry=0,
        classification=FailureClass.NORMAL_PRODUCT_REPAIR,
        confidence=0.78,
        facts={
            "next_cursor": 1360857,
            "current_workflow": {
                "group_idx": 38,
                "retry": 0,
                "latest_event_id": 24050,
                "latest_artifact_id": 1360857,
            },
        },
        inference="Classifier seed still sees prior verifier failure.",
        recommended_action=ActionLevel.OBSERVE,
        citations=["artifact:dag-verify:g38:initial id=1358950"],
    )


def _healthy_packet() -> EvidencePacket:
    return EvidencePacket(
        feature_id="8ac124d6",
        group_idx=38,
        retry=0,
        classification=FailureClass.HEALTHY_PROGRESS,
        confidence=0.7,
        facts={
            "next_cursor": 1360857,
            "successful_verify_artifacts": ["artifact:dag-verify:g38:retry-0 id=1360854"],
        },
        inference="Recent runner or verifier evidence shows progress and no deterministic blocker.",
        recommended_action=ActionLevel.DIGEST,
        citations=["artifact:dag-verify:g38:retry-0 id=1360854"],
    )


@pytest.mark.asyncio
async def test_supervisor_agent_requests_evidence_then_returns_assessment():
    runtime = _FakeRuntime()
    toolbox = _FakeToolbox()

    assessment, bundles, fallback = await SupervisorAgent().assess(
        _packet(),
        question="how is it looking?",
        runtime=runtime,
        feature_id="8ac124d6",
        toolbox=toolbox,
    )

    assert fallback is False
    assert assessment.status == "healthy_progress"
    assert assessment.recommended_action == ActionLevel.DIGEST
    assert assessment.citations == ["artifact:dag-verify:g38:retry-0 id=1360854"]
    assert toolbox.requests[0].artifact_keys == ["dag-verify:g38:retry-0"]
    assert bundles[0].artifacts[0].id == 1360854
    assert "Legacy Fallback Evidence JSON" in runtime.prompts[1]
    assert runtime.kwargs[0]["session_key"] == "workflow-supervisor:8ac124d6"
    assert runtime.roles[0].metadata["max_session_chars"] > 0
    assert runtime.roles[0].metadata["keep_recent_messages"] == 12
    assert runtime.roles[0].metadata["forbid_command_execution"] is True
    assert runtime.roles[0].metadata["auto_approve_mcp_tools"] is False
    assert runtime.roles[0].metadata["disable_shell_tools"] is True
    assert "supervisor-evidence" in runtime.roles[0].metadata["mcp_servers"]
    assert (
        runtime.roles[0]
        .metadata["mcp_servers"]["supervisor-evidence"]["env"]["IRIAI_SUPERVISOR_FEATURE_ID"]
        == "8ac124d6"
    )
    assert assessment.evidence_mode == "mcp+legacy_request_evidence"
    assert assessment.tool_names_used == ["supervisor-evidence"]


@pytest.mark.asyncio
async def test_supervisor_agent_fallback_is_marked_when_runtime_absent():
    assessment, bundles, fallback = await SupervisorAgent().assess(
        _packet(),
        question="status?",
        runtime=None,
    )

    assert fallback is True
    assert bundles == []
    assert assessment.status == "supervisor_degraded"
    assert assessment.fallback_reason == "runtime_absent"
    assert assessment.message.startswith("Supervisor degraded")


@pytest.mark.asyncio
async def test_supervisor_agent_does_not_launch_shell_capable_codex_runtime(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "iriai_build_v2.runtimes.codex.shutil.which",
        lambda _command: "/usr/local/bin/codex",
    )
    runtime = CodexAgentRuntime()

    async def _invoke_should_not_run(*_args, **_kwargs):
        raise AssertionError("supervisor should not launch shell-capable Codex runtime")

    monkeypatch.setattr(runtime, "invoke", _invoke_should_not_run)

    assessment, bundles, fallback = await SupervisorAgent().assess(
        _packet(),
        question="status?",
        runtime=runtime,
    )

    assert fallback is True
    assert bundles == []
    assert assessment.status == "supervisor_degraded"
    assert assessment.fallback_reason == "evidence_only_no_shell_runtime"


@pytest.mark.asyncio
async def test_supervisor_agent_can_opt_into_read_only_codex_runtime(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("IRIAI_SUPERVISOR_CODEX_READ_ONLY", "1")
    monkeypatch.setattr(
        "iriai_build_v2.runtimes.codex.shutil.which",
        lambda _command: "/usr/local/bin/codex",
    )
    runtime = CodexAgentRuntime()
    captured: dict[str, object] = {}

    async def _invoke(_role, prompt, **kwargs):
        captured["role"] = _role
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return json.dumps(
            {
                "type": "assessment",
                "assessment": {
                    "status": "healthy_progress",
                    "message": "Agent-authored status from read-only Codex.",
                    "facts": ["snapshot says G45 is active"],
                    "inferences": ["no deterministic blocker"],
                    "citations": ["event:1"],
                    "confidence": 0.8,
                    "recommended_action": "digest",
                    "proposed_action": None,
                },
            }
        )

    monkeypatch.setattr(runtime, "invoke", _invoke)

    assessment, bundles, fallback = await SupervisorAgent().assess(
        _packet(),
        question="status?",
        runtime=runtime,
    )

    role = captured["role"]
    assert fallback is False
    assert bundles == []
    assert assessment.message == "Agent-authored status from read-only Codex."
    assert role.metadata["codex_read_only_shell"] is True
    assert role.metadata["forbid_command_execution"] is True
    assert "local read-only sandbox" in role.prompt
    assert "Do not run Bash" in role.prompt


@pytest.mark.asyncio
async def test_read_only_codex_supervisor_preloads_host_evidence(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("IRIAI_SUPERVISOR_CODEX_READ_ONLY", "1")
    monkeypatch.setattr(
        "iriai_build_v2.runtimes.codex.shutil.which",
        lambda _command: "/usr/local/bin/codex",
    )
    runtime = CodexAgentRuntime()
    toolbox = _FakeToolbox()
    captured: dict[str, object] = {}

    async def _invoke(_role, prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return json.dumps(
            {
                "type": "assessment",
                "assessment": {
                    "status": "root_cause_identified",
                    "message": "G38 is repairing the verifier failure from host evidence.",
                    "facts": ["host evidence included the retry verifier artifact"],
                    "inferences": ["MCP cancellation would not block this digest"],
                    "citations": ["artifact:dag-verify:g38:retry-0 id=1360854"],
                    "confidence": 0.82,
                    "recommended_action": "digest",
                },
            }
        )

    monkeypatch.setattr(runtime, "invoke", _invoke)

    assessment, bundles, fallback = await SupervisorAgent().assess(
        _packet(),
        question="status?",
        runtime=runtime,
        feature_id="8ac124d6",
        toolbox=toolbox,
    )

    prompt = str(captured["prompt"])
    assert fallback is False
    assert bundles[0].request.reason == "host-preloaded-current-supervisor-evidence"
    assert toolbox.requests[0].artifact_prefixes[:2] == [
        "dag-verify:g38:",
        "dag-fix:g38:",
    ]
    assert toolbox.requests[0].event_after_id == 23970
    assert "Host Deterministic Seed JSON" in prompt
    assert "Host Preloaded Evidence JSON" in prompt
    assert "MCP tool calls are unavailable or cancelled" in prompt
    assert assessment.evidence_mode == "mcp+host_preloaded"


@pytest.mark.asyncio
async def test_supervisor_agent_normalizes_non_enum_recommended_action():
    assessment, bundles, fallback = await SupervisorAgent().assess(
        _packet(),
        question="status?",
        runtime=_NonEnumActionRuntime(),
    )

    assert fallback is False
    assert bundles == []
    assert assessment.message == "Workflow is blocked by commit hygiene."
    assert assessment.recommended_action == ActionLevel.RECOMMEND
    assert any("repair_commit_hygiene" in item for item in assessment.inferences)


@pytest.mark.asyncio
async def test_supervisor_agent_caps_deterministic_seed_escalation():
    packet = EvidencePacket(
        feature_id="8ac124d6",
        group_idx=38,
        retry=0,
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.78,
        facts={"next_cursor": 1360857},
        inference="Commit hygiene has a deterministic typed route.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["artifact:dag-direct-repair-route:g38:retry-0 id=1"],
    )

    assessment, bundles, fallback = await SupervisorAgent().assess(
        packet,
        question="status?",
        runtime=_EscalatingDeterministicRuntime(),
    )

    assert fallback is False
    assert bundles == []
    assert assessment.recommended_action == ActionLevel.RECOMMEND
    assert assessment.proposed_action is None
    assert any("capped" in item for item in assessment.inferences)


@pytest.mark.asyncio
async def test_supervisor_agent_does_not_post_unparseable_json_as_plain_text():
    assessment, bundles, fallback = await SupervisorAgent().assess(
        _packet(),
        question="status?",
        runtime=_MalformedJsonRuntime(),
    )

    assert fallback is True
    assert bundles == []
    assert assessment.status == "supervisor_degraded"
    assert assessment.fallback_reason == "parse_error"
    assert assessment.message.startswith("Supervisor degraded")
    assert "This JSON should not be posted raw" not in assessment.message


@pytest.mark.asyncio
async def test_supervisor_agent_marks_input_overflow_as_degraded():
    assessment, bundles, fallback = await SupervisorAgent().assess(
        _packet(),
        question="status?",
        runtime=_OverflowRuntime(),
    )

    assert fallback is True
    assert bundles == []
    assert assessment.status == "supervisor_degraded"
    assert assessment.fallback_reason == "input_overflow"
    assert "Do not treat this as an agent-authored assessment" in assessment.message


@pytest.mark.asyncio
async def test_supervisor_agent_guard_treats_old_commit_failures_as_historical():
    assessment, bundles, fallback = await SupervisorAgent().assess(
        _healthy_packet(),
        question="status?",
        runtime=_StaleCommitBlockerRuntime(),
    )

    assert fallback is False
    assert bundles == []
    assert assessment.status == "healthy_progress"
    assert "superseded by newer same-group progress" in assessment.message
    assert assessment.recommended_action == ActionLevel.DIGEST
    assert assessment.citations == ["artifact:dag-verify:g38:retry-0 id=1360854"]


@pytest.mark.asyncio
async def test_supervisor_agent_detail_question_includes_initial_evidence_in_first_prompt():
    runtime = _AssessmentRuntime()
    bundle = SupervisorEvidenceBundle(
        request=SupervisorInvestigationRequest(
            reason="preload detail",
            artifact_prefixes=["dag-commit-failure:g39:", "dag-fix:g39:"],
        ),
        artifacts=[
            ArtifactRecord(
                id=1368712,
                key="dag-commit-failure:g39:implementation",
                value={
                    "stderr": (
                        "src/vs/workbench/contrib/studioWorkflow/browser/"
                        "workflowTab/chat/test/browser/cardVariantRegistry.test.ts:6:25 "
                        "import pattern violation"
                    )
                },
            ),
            ArtifactRecord(
                id=1369000,
                key="dag-fix:g39:retry-0",
                value={"summary": "removed retired chat import and fixed test hygiene"},
            ),
        ],
    )

    assessment, bundles, fallback = await SupervisorAgent().assess(
        _packet(),
        question="What is the root cause of the failure?",
        runtime=runtime,
        feature_id="8ac124d6",
        initial_bundles=[bundle],
    )

    assert fallback is False
    assert bundles == [bundle]
    assert assessment.status == "root_cause_identified"
    assert "Required Detail For This Question" in runtime.prompts[0]
    assert "cardVariantRegistry.test.ts" in runtime.prompts[0]
    assert "dag-fix:g39:retry-0" in runtime.prompts[0]
    assert "Legacy Fallback Evidence JSON" in runtime.prompts[0]


@pytest.mark.asyncio
async def test_supervisor_agent_status_question_requires_human_readable_failure_context():
    runtime = _AssessmentRuntime()
    bundle = SupervisorEvidenceBundle(
        request=SupervisorInvestigationRequest(
            reason="preload current status detail",
            artifact_prefixes=["dag-verify:g40:", "dag-fix:g40:"],
        ),
        artifacts=[
            ArtifactRecord(
                id=1459599,
                key="dag-verify:g40:initial",
                value={
                    "approved": False,
                    "summary": (
                        "Worker spawn failed because supervisor credentials sent "
                        "ISO expires_at while bootstrap expected integer seconds."
                    ),
                },
            ),
            ArtifactRecord(
                id=1460912,
                key="dag-fix:g40:retry-0",
                value={
                    "summary": (
                        "Coerced expires_at to integer seconds and hardened worker "
                        "credential parsing."
                    ),
                },
            ),
        ],
    )

    assessment, bundles, fallback = await SupervisorAgent().assess(
        _packet(),
        question="what is the current status?",
        runtime=runtime,
        feature_id="8ac124d6",
        initial_bundles=[bundle],
    )

    assert fallback is False
    assert bundles == [bundle]
    assert assessment.status == "root_cause_identified"
    assert "Required Detail For This Question" in runtime.prompts[0]
    assert "must be human-readable" in runtime.prompts[0]
    assert "Use artifact/event ids only as citations" in runtime.prompts[0]
    assert "ISO expires_at" in runtime.prompts[0]
    assert "Coerced expires_at" in runtime.prompts[0]


@pytest.mark.asyncio
async def test_supervisor_agent_compacts_large_evidence_below_runtime_limit():
    runtime = _PromptRecordingAssessmentRuntime()
    huge_text = "failure detail for src/app/BigVerifier.ts\n" + ("x" * 1_300_000)
    bundle = SupervisorEvidenceBundle(
        request=SupervisorInvestigationRequest(
            reason="load exact oversized verifier artifact",
            artifact_ids=[1420690],
        ),
        artifacts=[
            ArtifactRecord(
                id=1420690,
                key="dag-verify:g39:initial",
                value={"approved": False, "summary": huge_text},
            )
        ],
    )

    assessment, bundles, fallback = await SupervisorAgent().assess(
        _packet(),
        question="What failed?",
        runtime=runtime,
        feature_id="8ac124d6",
        initial_bundles=[bundle],
    )

    assert fallback is False
    assert bundles == [bundle]
    assert len(runtime.prompts[0]) < 1_048_576
    assert "artifact:dag-verify:g39:initial id=1420690" in runtime.prompts[0]
    assert "chunk_refs" in runtime.prompts[0]
    assert assessment.prompt_chars == len(runtime.prompts[0])
    assert assessment.omitted_detail_refs == ["artifact:dag-verify:g39:initial id=1420690"]


@pytest.mark.asyncio
async def test_supervisor_agent_prompt_uses_mcp_without_preloaded_evidence():
    runtime = _AssessmentRuntime()

    assessment, bundles, fallback = await SupervisorAgent().assess(
        _packet(),
        question="what is the current status?",
        runtime=runtime,
        feature_id="8ac124d6",
    )

    assert fallback is False
    assert bundles == []
    assert "supervisor-evidence" in runtime.prompts[0]
    assert "get_current_snapshot" in runtime.prompts[0]
    assert "MCP-only" in runtime.roles[0].prompt
    assert "do not run Bash" in runtime.roles[0].prompt
    assert "Do not call `readonly_sql`" in runtime.roles[0].prompt
    assert "Fetch at most three artifact details" in runtime.roles[0].prompt
    assert "Deterministic Seed Packet JSON" not in runtime.prompts[0]
    assert "Evidence Bundles JSON" not in runtime.prompts[0]
    assert len(runtime.prompts[0]) < 20_000
    assert assessment.evidence_mode == "mcp"


@pytest.mark.asyncio
async def test_supervisor_agent_namespaces_session_by_process_epoch():
    runtime = _FakeRuntime()

    await SupervisorAgent().assess(
        _packet(),
        question="how is it looking?",
        runtime=runtime,
        feature_id="8ac124d6",
        toolbox=_FakeToolbox(),
        session_epoch="proc-1",
    )

    assert runtime.kwargs[0]["session_key"] == "workflow-supervisor:proc-1:8ac124d6"


@pytest.mark.asyncio
async def test_supervisor_agent_namespaces_session_by_thread_scope():
    runtime = _FakeRuntime()

    await SupervisorAgent().assess(
        _packet(),
        question="how is it looking?",
        runtime=runtime,
        feature_id="8ac124d6",
        toolbox=_FakeToolbox(),
        session_epoch="proc-1",
        session_scope="question-thread-10.123",
    )

    assert (
        runtime.kwargs[0]["session_key"]
        == "workflow-supervisor:proc-1:question-thread-10.123:8ac124d6"
    )
