from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from iriai_build_v2.supervisor.slack import (
    SupervisorRuntimeService,
    SupervisorSlackRoute,
    SupervisorSlackRouter,
    run_supervisor_slack_app,
)
from iriai_build_v2.supervisor.models import ActionLevel, EvidencePacket, FailureClass, SupervisorMode


class _FakeAdapter:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str | None]] = []
        self.updates: list[tuple[str, str, str]] = []

    async def post_message(
        self,
        channel: str,
        text: str,
        *,
        thread_ts: str | None = None,
    ) -> str:
        self.messages.append((channel, text, thread_ts))
        return "1.234"

    async def update_message(
        self,
        channel: str,
        ts: str,
        *,
        text: str | None = None,
        blocks=None,
    ) -> None:
        self.updates.append((channel, ts, text or ""))


class _FakeService:
    def __init__(self) -> None:
        self.questions: list[SupervisorSlackRoute] = []
        self.actions: list[SupervisorSlackRoute] = []
        self.instructions: list[SupervisorSlackRoute] = []

    async def answer_question(self, route: SupervisorSlackRoute) -> str:
        self.questions.append(route)
        return f"answer:{route.text}"

    async def evaluate_action_request(self, route: SupervisorSlackRoute) -> str:
        self.actions.append(route)
        return f"action:{route.text}"

    async def route_workflow_instruction(self, route: SupervisorSlackRoute) -> str:
        self.instructions.append(route)
        return f"instruction:{route.text}"


class _FakeToolbox:
    def __init__(self) -> None:
        self.requests = []

    async def gather_many(self, requests):
        self.requests.extend(requests)
        return []


class _FakeSupervisorApp:
    mode = SupervisorMode.READ_ONLY

    def __init__(self) -> None:
        self.calls: list[tuple[int, int | None, int | None, int]] = []

    async def run_once(
        self,
        *,
        feature_id: str,
        cursor: int = 0,
        event_cursor: int | None = None,
        artifact_cursor: int | None = None,
        bridge_log_cursor: int = 0,
    ):
        self.calls.append((cursor, event_cursor, artifact_cursor, bridge_log_cursor))
        return EvidencePacket(
            feature_id=feature_id,
            group_idx=38,
            retry=1,
            classification=FailureClass.DETERMINISTIC_UNBLOCK,
            confidence=0.88,
            facts={
                "cursor": cursor,
                "next_cursor": 42,
                "event_cursor": event_cursor or cursor,
                "next_event_cursor": 24,
                "artifact_cursor": artifact_cursor or cursor,
                "next_artifact_cursor": 42,
                "bridge_log_cursor": 9,
            },
            inference="Commit hook failed on a deterministic file hygiene rule.",
            recommended_action=ActionLevel.RECOMMEND,
            citations=["artifact:dag-commit-failure:g38:retry-0 id=1353600"],
        )

    def evidence_toolbox(self, feature_id: str):
        return _FakeToolbox()


class _RecordingFeatureStore:
    async def get_feature(self, feature_id: str):
        return SimpleNamespace(id=feature_id)


class _RecordingArtifactStore:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, object]] = []

    async def put(self, key: str, value: str, *, feature):
        self.writes.append((key, value, feature))

    async def list_records(
        self,
        *,
        feature_id: str,
        prefixes,
        after_id: int,
        limit: int = 500,
        order: str = "asc",
    ):
        rows = [
            {"id": idx + 1, "key": key, "value": value}
            for idx, (key, value, _feature) in enumerate(self.writes)
            if idx + 1 > after_id and any(key.startswith(prefix) for prefix in prefixes)
        ]
        rows = sorted(rows, key=lambda row: row["id"], reverse=(order == "desc"))
        return rows[:limit]


class _PersistingSupervisorApp(_FakeSupervisorApp):
    def __init__(self) -> None:
        super().__init__()
        self.feature_store = _RecordingFeatureStore()
        self.artifact_store = _RecordingArtifactStore()


class _DetailSupervisorApp(_FakeSupervisorApp):
    def __init__(self) -> None:
        super().__init__()
        self.toolbox = _FakeToolbox()

    def evidence_toolbox(self, feature_id: str):
        return self.toolbox


class ThinkingBlock:
    def __init__(self, thinking: str) -> None:
        self.thinking = thinking


class AssistantMessage:
    def __init__(self, content: list) -> None:
        self.content = content


class _FakeStreamingRuntime:
    def __init__(self) -> None:
        self.on_message = None

    async def invoke(self, *_args, **_kwargs):
        if self.on_message is not None:
            self.on_message(AssistantMessage([ThinkingBlock("checking latest artifacts")]))
        await asyncio.sleep(0.01)
        return "final supervisor answer"


class _CapturingRuntime:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def invoke(self, _role, prompt, **_kwargs):
        self.prompts.append(prompt)
        return json.dumps(
            {
                "type": "assessment",
                "assessment": {
                    "status": "ok",
                    "message": "thread-aware answer",
                    "facts": [],
                    "inferences": [],
                    "citations": [],
                    "confidence": 0.8,
                    "recommended_action": "observe",
                    "proposed_action": None,
                },
            }
        )


def test_supervisor_router_classifies_natural_questions():
    router = SupervisorSlackRouter(
        adapter=_FakeAdapter(),
        channel="CSUP",
        service=_FakeService(),
        feature_id="feat-1",
        dashboard_url="https://dash.example/feature/feat-1",
    )

    route = router.classify(
        {"channel": "CSUP", "user": "U1", "text": "How's it looking?", "ts": "1"}
    )

    assert route.kind == "supervisor_question"
    assert route.feature_id == "feat-1"
    assert route.dashboard_url == "https://dash.example/feature/feat-1"


def test_supervisor_router_classifies_imperative_investigation_requests():
    router = SupervisorSlackRouter(
        adapter=_FakeAdapter(),
        channel="CSUP",
        service=_FakeService(),
    )

    route = router.classify(
        {
            "channel": "CSUP",
            "user": "U1",
            "text": "Give me all the revision cycles for group 38",
            "ts": "1",
        }
    )

    assert route.kind == "supervisor_question"


def test_supervisor_router_classifies_artifact_keyword_requests():
    router = SupervisorSlackRouter(
        adapter=_FakeAdapter(),
        channel="CSUP",
        service=_FakeService(),
    )

    route = router.classify(
        {
            "channel": "CSUP",
            "user": "U1",
            "text": "group 38 retry artifacts",
            "ts": "1",
        }
    )

    assert route.kind == "supervisor_question"


def test_supervisor_router_routes_any_channel_text_to_agent():
    router = SupervisorSlackRouter(
        adapter=_FakeAdapter(),
        channel="CSUP",
        service=_FakeService(),
    )

    route = router.classify(
        {
            "channel": "CSUP",
            "user": "U1",
            "text": "I sent this naturally and expect the supervisor to handle it",
            "ts": "1",
        }
    )

    assert route.kind == "supervisor_question"


def test_supervisor_router_classifies_action_requests_before_questions():
    router = SupervisorSlackRouter(
        adapter=_FakeAdapter(),
        channel="CSUP",
        service=_FakeService(),
    )

    route = router.classify(
        {"channel": "CSUP", "user": "U1", "text": "Should we restart?", "ts": "1"}
    )

    assert route.kind == "supervisor_action_request"


def test_supervisor_router_classifies_workflow_instructions():
    router = SupervisorSlackRouter(
        adapter=_FakeAdapter(),
        channel="CSUP",
        service=_FakeService(),
    )

    route = router.classify(
        {
            "channel": "CSUP",
            "user": "U1",
            "text": "Tell the implementer to focus on the hook failure.",
            "ts": "1",
        }
    )

    assert route.kind == "workflow_instruction"


@pytest.mark.asyncio
async def test_supervisor_router_dispatches_to_injected_service():
    adapter = _FakeAdapter()
    service = _FakeService()
    router = SupervisorSlackRouter(adapter=adapter, channel="CSUP", service=service)

    await router.handle_message(
        {
            "channel": "CSUP",
            "user": "U1",
            "text": "what changed?",
            "ts": "1",
        }
    )

    assert [route.text for route in service.questions] == ["what changed?"]
    assert adapter.messages == [("CSUP", "\U0001f4ad _Checking workflow evidence..._", "1")]
    assert adapter.updates == [("CSUP", "1.234", "answer:what changed?")]


@pytest.mark.asyncio
async def test_supervisor_router_streams_runtime_thinking_then_replaces_with_final(monkeypatch):
    monkeypatch.setattr(
        "iriai_build_v2.supervisor.slack._PROGRESS_MIN_UPDATE_INTERVAL",
        0.0,
    )
    adapter = _FakeAdapter()
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=_FakeStreamingRuntime(),
    )
    router = SupervisorSlackRouter(adapter=adapter, channel="CSUP", service=service)

    await router.handle_message(
        {
            "channel": "CSUP",
            "user": "U1",
            "text": "how is it looking?",
            "ts": "1",
        }
    )

    assert adapter.messages == [("CSUP", "\U0001f4ad _Checking workflow evidence..._", "1")]
    assert any("checking latest artifacts" in update[2] for update in adapter.updates)
    assert adapter.updates[-1] == ("CSUP", "1.234", "final supervisor answer")


@pytest.mark.asyncio
async def test_supervisor_runtime_service_answers_from_evidence_and_advances_cursors():
    app = _FakeSupervisorApp()
    service = SupervisorRuntimeService(app=app, feature_id="feat-1", agent_runtime=None)

    reply = await service.answer_question(
        SupervisorSlackRoute(
            kind="supervisor_question",
            text="how is it looking?",
            channel="CSUP",
            user="U1",
            thread_ts="10.123",
        )
    )
    reply2 = await service.answer_question(
        SupervisorSlackRoute(
            kind="supervisor_question",
            text="what changed?",
            channel="CSUP",
            user="U1",
        )
    )

    assert "deterministic_unblock" in reply
    assert "artifact:dag-commit-failure:g38:retry-0 id=1353600" in reply
    assert app.calls == [(0, 0, 0, 0), (42, 24, 42, 9)]
    assert "Fallback answer for question: what changed?" in reply2


@pytest.mark.asyncio
async def test_supervisor_runtime_service_preloads_failure_detail_evidence():
    app = _DetailSupervisorApp()
    service = SupervisorRuntimeService(app=app, feature_id="feat-1", agent_runtime=None)

    await service.answer_question(
        SupervisorSlackRoute(
            kind="supervisor_question",
            text="What is the root cause of the failure?",
            channel="CSUP",
            user="U1",
        )
    )

    assert app.toolbox.requests
    request = app.toolbox.requests[0]
    assert "dag-verify:g38:" in request.artifact_prefixes
    assert "dag-commit-failure:g38:" in request.artifact_prefixes
    assert "dag-verify-rca:g38:" in request.artifact_prefixes
    assert "dag-fix:g38:" in request.artifact_prefixes
    assert request.include_bridge is True
    assert request.include_worktrees is True


@pytest.mark.asyncio
async def test_supervisor_runtime_service_persists_agent_assessment():
    app = _PersistingSupervisorApp()
    service = SupervisorRuntimeService(
        app=app,
        feature_id="feat-1",
        agent_runtime=_FakeStreamingRuntime(),
    )

    reply = await service.answer_question(
        SupervisorSlackRoute(
            kind="supervisor_question",
            text="how is it looking?",
            channel="CSUP",
            user="U1",
            thread_ts="10.123",
        )
    )

    assert reply == "final supervisor answer"
    key, value, feature = app.artifact_store.writes[0]
    assert key.startswith("supervisor-agent-assessment:feat-1:e24:a42:b9:")
    payload = json.loads(value)
    assert payload["question"] == "how is it looking?"
    assert payload["slack_channel"] == "CSUP"
    assert payload["slack_thread_ts"] == "10.123"
    assert payload["slack_user"] == "U1"
    assert payload["fallback"] is False
    assert payload["assessment"]["message"] == "final supervisor answer"
    assert feature.id == "feat-1"


@pytest.mark.asyncio
async def test_supervisor_digest_coalesces_bursty_material_changes():
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        min_digest_interval_seconds=60.0,
    )
    first = EvidencePacket(
        feature_id="feat-1",
        group_idx=39,
        retry=0,
        classification=FailureClass.HEALTHY_PROGRESS,
        confidence=0.7,
        facts={"next_cursor": 100},
        inference="G39 is implementing.",
        recommended_action=ActionLevel.DIGEST,
    )
    second = EvidencePacket(
        feature_id="feat-1",
        group_idx=39,
        retry=0,
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.9,
        facts={"next_cursor": 101},
        inference="Commit hook failed.",
        recommended_action=ActionLevel.RECOMMEND,
    )
    observe = EvidencePacket(
        feature_id="feat-1",
        group_idx=39,
        retry=0,
        classification=FailureClass.WATCH_ONLY,
        confidence=0.5,
        facts={"next_cursor": 102},
        inference="No new material evidence.",
        recommended_action=ActionLevel.OBSERVE,
    )

    assert service._digest_packet_to_send(first) is first
    assert service._digest_packet_to_send(second) is None
    assert service._pending_digest_packet is second

    service._last_digest_at -= 61.0

    assert service._digest_packet_to_send(observe) is second
    assert service._pending_digest_packet is None

    assert service._digest_packet_to_send(second) is None


@pytest.mark.asyncio
async def test_supervisor_runtime_service_includes_thread_context_on_followup():
    app = _PersistingSupervisorApp()
    runtime = _CapturingRuntime()
    service = SupervisorRuntimeService(
        app=app,
        feature_id="feat-1",
        agent_runtime=runtime,
    )

    route = SupervisorSlackRoute(
        kind="supervisor_question",
        text="Give me group 38 revision cycles",
        channel="CSUP",
        user="U1",
        thread_ts="10.123",
    )
    await service.answer_question(route)
    await service.answer_question(
        SupervisorSlackRoute(
            kind="supervisor_question",
            text="so the group is healthy?",
            channel="CSUP",
            user="U1",
            thread_ts="10.123",
        )
    )

    assert "## Slack Thread Context" in runtime.prompts[-1]
    assert "Give me group 38 revision cycles" in runtime.prompts[-1]


@pytest.mark.asyncio
async def test_supervisor_app_requires_separate_token_env_names(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_SLACK_APP_TOKEN", raising=False)
    monkeypatch.delenv("SUPERVISOR_SLACK_BOT_TOKEN", raising=False)

    try:
        await run_supervisor_slack_app(channel="CSUP")
    except RuntimeError as exc:
        assert "SUPERVISOR_SLACK_APP_TOKEN" in str(exc)
    else:
        raise AssertionError("expected missing supervisor Slack token to fail fast")
