from __future__ import annotations

import json

import pytest

from iriai_build_v2.supervisor.agent import SupervisorAgent
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


class _MalformedJsonRuntime:
    async def invoke(self, _role, _prompt: str, **_kwargs):
        return '{"type":"assessment","assessment":{"message":"This JSON should not be posted raw."'


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

    async def invoke(self, _role, prompt: str, **_kwargs):
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
        facts={"next_cursor": 1360857},
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
    assert "Evidence Bundles JSON" in runtime.prompts[1]
    assert runtime.kwargs[0]["session_key"] == "workflow-supervisor:8ac124d6"
    assert runtime.roles[0].metadata["max_session_chars"] > 0
    assert runtime.roles[0].metadata["keep_recent_messages"] == 12


@pytest.mark.asyncio
async def test_supervisor_agent_fallback_is_marked_when_runtime_absent():
    assessment, bundles, fallback = await SupervisorAgent().assess(
        _packet(),
        question="status?",
        runtime=None,
    )

    assert fallback is True
    assert bundles == []
    assert assessment.status == "normal_product_repair"
    assert assessment.message.startswith("Fallback answer")


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
async def test_supervisor_agent_does_not_post_unparseable_json_as_plain_text():
    assessment, bundles, fallback = await SupervisorAgent().assess(
        _packet(),
        question="status?",
        runtime=_MalformedJsonRuntime(),
    )

    assert fallback is True
    assert bundles == []
    assert assessment.message.startswith("Fallback answer")
    assert "This JSON should not be posted raw" not in assessment.message


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
