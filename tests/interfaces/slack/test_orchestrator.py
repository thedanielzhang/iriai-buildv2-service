from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.interfaces.slack.orchestrator import (
    SlackWorkflowOrchestrator,
    _SlackInvocationObserver,
)
from iriai_build_v2.interfaces.slack.parser import ParsedRequest
from iriai_build_v2.interfaces.slack.streamer import SlackStreamer


class _QueuedRuntime:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str]] = []

    def queue_user_note(self, feature_id: str, text: str) -> None:
        self.notes.append((feature_id, text))


class _RecoveringAdapter:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.updated_messages: list[tuple[str, str, str | None]] = []
        self.modes: list[tuple[str, str]] = []
        self.reactions: list[tuple[str, str, str]] = []
        self.created_channels: list[str] = []

    async def post_message(self, channel: str, text: str, **kwargs) -> str:
        self.messages.append((channel, text))
        return f"{len(self.messages):04d}.5678"

    async def update_message(self, channel: str, ts: str, *, text=None, blocks=None) -> None:
        self.updated_messages.append((channel, ts, text))

    def set_channel_mode(self, channel: str, mode: str) -> None:
        self.modes.append((channel, mode))

    async def add_reaction(self, channel: str, ts: str, reaction: str) -> None:
        self.reactions.append((channel, ts, reaction))

    async def create_channel(self, name: str) -> str:
        self.created_channels.append(name)
        return "CBUGFLOW"

    @property
    def planning_channel(self) -> str:
        return "CPLANNING"

    @property
    def web(self):
        return object()


class _RecoveringInteraction:
    def __init__(self) -> None:
        self.channels: list[tuple[str, str]] = []

    def register_channel(self, feature_id: str, channel: str) -> None:
        self.channels.append((feature_id, channel))

    def unregister_channel(self, feature_id: str) -> None:
        return None

    def has_pending(self, channel: str) -> bool:
        return False


@pytest.mark.asyncio
async def test_silent_invocation_observer_posts_heartbeat(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "iriai_build_v2.interfaces.slack.orchestrator._SILENT_INVOCATION_NOTICE_DELAY",
        0.01,
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces.slack.orchestrator._SILENT_INVOCATION_UPDATE_INTERVAL",
        0.01,
    )
    adapter = _RecoveringAdapter()
    streamer = SlackStreamer(adapter, "C123")
    observer = _SlackInvocationObserver(adapter, "C123", streamer)

    observer.on_invocation_start(
        "inv-1",
        actor_name="scoper",
        timeout_seconds=600,
    )
    await asyncio.sleep(0.03)

    assert adapter.messages
    assert "scoper" in adapter.messages[0][1]
    assert "hasn't produced Slack-visible progress yet" in adapter.messages[0][1]


@pytest.mark.asyncio
async def test_silent_invocation_observer_skips_when_streamer_is_visible(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "iriai_build_v2.interfaces.slack.orchestrator._SILENT_INVOCATION_NOTICE_DELAY",
        0.01,
    )
    adapter = _RecoveringAdapter()
    streamer = SlackStreamer(adapter, "C123")
    observer = _SlackInvocationObserver(adapter, "C123", streamer)

    observer.on_invocation_start(
        "inv-1",
        actor_name="scoper",
        timeout_seconds=600,
    )
    streamer._last_visible_update_at = time.monotonic()
    await asyncio.sleep(0.03)

    assert adapter.messages == []


def test_queue_user_note_forwards_to_active_runtime():
    runtime = _QueuedRuntime()
    orchestrator = SlackWorkflowOrchestrator.__new__(SlackWorkflowOrchestrator)
    orchestrator._user_notes = {}
    orchestrator._active_runtimes = {"feat-1": runtime}

    orchestrator._queue_user_note("feat-1", "Please include rollback notes.")

    assert orchestrator._user_notes == {"feat-1": ["Please include rollback notes."]}
    assert runtime.notes == [("feat-1", "Please include rollback notes.")]


@pytest.mark.asyncio
async def test_recovery_preserves_saved_runtime_without_explicit_override():
    async def _list_active():
        return [
            SimpleNamespace(
                id="feat-1",
                name="Feature One",
                metadata={
                    "channel_id": "C123",
                    "workspace_path": "/tmp/workspace",
                    "mode": "singleplayer",
                    "agent_runtime": "claude",
                    "_db_phase": "pm",
                },
            )
        ]

    adapter = _RecoveringAdapter()
    interaction = _RecoveringInteraction()
    orchestrator = SlackWorkflowOrchestrator(
        adapter=adapter,
        interaction_runtime=interaction,
        agent_runtime_name="codex",
        agent_runtime_override=False,
    )
    orchestrator._env = SimpleNamespace(
        feature_store=SimpleNamespace(list_active=_list_active)
    )

    await orchestrator._recover_active_features()

    assert orchestrator._recoverable_features["feat-1"]["agent_runtime"] == "claude"
    assert adapter.messages == [
        ("C123", "Bridge restarted. Feature is in phase `pm`. Runtime: `claude`. Send any message to resume.")
    ]


@pytest.mark.asyncio
async def test_recovery_uses_bridge_runtime_when_explicitly_overridden():
    async def _list_active():
        return [
            SimpleNamespace(
                id="feat-1",
                name="Feature One",
                metadata={
                    "channel_id": "C123",
                    "workspace_path": "/tmp/workspace",
                    "mode": "singleplayer",
                    "agent_runtime": "claude",
                    "_db_phase": "pm",
                },
            )
        ]

    adapter = _RecoveringAdapter()
    interaction = _RecoveringInteraction()
    orchestrator = SlackWorkflowOrchestrator(
        adapter=adapter,
        interaction_runtime=interaction,
        agent_runtime_name="codex",
        agent_runtime_override=True,
    )
    orchestrator._env = SimpleNamespace(
        feature_store=SimpleNamespace(list_active=_list_active)
    )

    await orchestrator._recover_active_features()

    assert orchestrator._recoverable_features["feat-1"]["agent_runtime"] == "codex"
    assert adapter.messages == [
        ("C123", "Bridge restarted. Feature is in phase `pm`. Runtime: `codex`. Send any message to resume.")
    ]


class _FakeArtifacts:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    async def put(self, key: str, value: str, *, feature) -> None:
        self.values[(feature.id, key)] = value


class _FakeFeatureStore:
    def __init__(self, features: dict[str, SimpleNamespace]) -> None:
        self.features = features
        self.logged_events: list[tuple[str, str, str, str | None, dict | None]] = []
        self.transitions: list[tuple[str, str]] = []

    async def get_feature(self, feature_id: str):
        return self.features.get(feature_id)

    async def update_metadata(self, feature_id: str, patch: dict) -> None:
        feature = self.features[feature_id]
        current = dict(getattr(feature, "metadata", {}) or {})
        current.update(patch)
        feature.metadata = current

    async def transition_phase(self, feature_id: str, new_phase: str) -> None:
        self.transitions.append((feature_id, new_phase))
        feature = self.features[feature_id]
        current = dict(getattr(feature, "metadata", {}) or {})
        current["_db_phase"] = new_phase
        feature.metadata = current

    async def log_event(self, feature_id: str, event_type: str, source: str, content=None, metadata=None) -> None:
        self.logged_events.append((feature_id, event_type, source, content, metadata))

    async def list_active(self):
        return list(self.features.values())


@pytest.mark.asyncio
async def test_start_bugflow_workflow_inherits_source_metadata(monkeypatch: pytest.MonkeyPatch):
    posted_threads: list[tuple[str, str, str, str]] = []

    async def _fake_post_to_thread(web, channel, thread_ts, text):
        posted_threads.append((str(web), channel, thread_ts, text))

    source_feature = SimpleNamespace(
        id="beced7b1",
        name="Complete checkout flow",
        slug="complete-checkout-flow-beced7b1",
        workflow_name="full-develop",
        metadata={"workspace_path": "/tmp/source-workspace", "channel_id": "CSOURCE"},
    )
    created_feature = SimpleNamespace(
        id="bf123456",
        name="Bugflow: Complete checkout flow",
        slug="bugflow-complete-checkout-flow-bf123456",
        workflow_name="bugfix-v2",
        metadata={},
    )
    feature_store = _FakeFeatureStore(
        {
            source_feature.id: source_feature,
            created_feature.id: created_feature,
        }
    )

    async def _fake_create_feature(store, name, workflow_name):
        assert store is feature_store
        assert name == "Bugflow: Complete checkout flow"
        assert workflow_name == "bugfix-v2"
        return created_feature

    async def _noop_run_workflow(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "iriai_build_v2.interfaces.slack.orchestrator.create_feature",
        _fake_create_feature,
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces.slack.helpers.post_to_thread",
        _fake_post_to_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces.slack.orchestrator.DASHBOARD_BASE_URL",
        "https://dash.example",
    )

    adapter = _RecoveringAdapter()
    interaction = _RecoveringInteraction()
    orchestrator = SlackWorkflowOrchestrator(adapter=adapter, interaction_runtime=interaction)
    orchestrator._env = SimpleNamespace(
        feature_store=feature_store,
        artifacts=_FakeArtifacts(),
        sessions=object(),
        context_provider=object(),
        feedback_service=object(),
        preview_service=object(),
        playwright_service=object(),
        artifact_mirror=object(),
    )
    orchestrator._create_runtime_and_runner = lambda **kwargs: (SimpleNamespace(), SimpleNamespace())  # type: ignore[method-assign]
    orchestrator._run_workflow = _noop_run_workflow  # type: ignore[method-assign]

    await orchestrator._start_bugflow_workflow(
        ParsedRequest("bugfix-v2", "beced7b1", "beced7b1"),
        {"ts": "999.111"},
    )

    assert adapter.created_channels == ["iriai-complete-checkout-flow-bugs-bf123456"]
    assert ("CBUGFLOW", "singleplayer") in adapter.modes
    assert posted_threads[0][1:] == ("CPLANNING", "999.111", "Bugflow started in <#CBUGFLOW>")
    assert adapter.messages[0] == ("CBUGFLOW", "Dashboard: https://dash.example/feature/bf123456")
    assert "Starting *bugfix-v2*" in adapter.messages[1][1]
    assert created_feature.metadata["source_feature_id"] == "beced7b1"
    assert created_feature.metadata["workspace_path"] == "/tmp/source-workspace"
    assert feature_store.transitions == [("bf123456", "bugflow-setup")]


@pytest.mark.asyncio
async def test_bugflow_dashboard_repost_prefers_live_base_url_over_saved_metadata(monkeypatch: pytest.MonkeyPatch):
    feature = SimpleNamespace(
        id="bf123456",
        name="Bugflow feature",
        slug="bugflow-feature-bf123456",
        workflow_name="bugfix-v2",
        metadata={"dashboard_url": "https://old.trycloudflare.com/feature/bf123456"},
    )
    feature_store = _FakeFeatureStore({feature.id: feature})

    adapter = _RecoveringAdapter()
    interaction = _RecoveringInteraction()
    orchestrator = SlackWorkflowOrchestrator(adapter=adapter, interaction_runtime=interaction)
    orchestrator._env = SimpleNamespace(feature_store=feature_store)
    monkeypatch.setattr(
        "iriai_build_v2.interfaces.slack.orchestrator.DASHBOARD_BASE_URL",
        "https://new.trycloudflare.com",
    )

    await orchestrator._maybe_post_dashboard_url(
        feature.id,
        "CBUGFLOW",
        workflow_name="bugfix-v2",
        recovery=True,
    )

    assert adapter.messages == [
        (
            "CBUGFLOW",
            "Dashboard: https://new.trycloudflare.com/feature/bf123456\nBridge restarted — reposting dashboard link.",
        )
    ]
    assert feature.metadata["dashboard_url"] == "https://new.trycloudflare.com/feature/bf123456"


@pytest.mark.asyncio
async def test_bugflow_root_bug_message_creates_report_artifact():
    feature = SimpleNamespace(
        id="bf123456",
        name="Bugflow feature",
        workflow_name="bugfix-v2",
        metadata={"channel_id": "CBUGFLOW"},
    )
    feature_store = _FakeFeatureStore({feature.id: feature})
    artifacts = _FakeArtifacts()

    adapter = _RecoveringAdapter()
    interaction = _RecoveringInteraction()
    orchestrator = SlackWorkflowOrchestrator(adapter=adapter, interaction_runtime=interaction)
    orchestrator._env = SimpleNamespace(feature_store=feature_store, artifacts=artifacts)
    orchestrator._feature_workflows = {feature.id: "bugfix-v2"}

    created = await orchestrator._maybe_capture_bugflow_report(
        feature.id,
        {
            "channel": "CBUGFLOW",
            "text": "[bug] Checkout button does nothing",
            "ts": "555.666",
        },
    )

    assert created is True
    artifact_keys = [key for (_feature_id, key) in artifacts.values.keys()]
    assert any(key.startswith("bugflow-report:BR-") for key in artifact_keys)
    assert feature_store.logged_events[0][1] == "bugflow_report_created"
    assert adapter.messages[0][0] == "CBUGFLOW"
    assert "Captured *BR-" in adapter.messages[0][1]


@pytest.mark.asyncio
async def test_resume_failure_keeps_bugflow_recoverable():
    feature = SimpleNamespace(
        id="bf123456",
        name="Bugflow feature",
        slug="bugflow-feature-bf123456",
        workflow_name="bugfix-v2",
        metadata={
            "channel_id": "CBUGFLOW",
            "workspace_path": "/tmp/workspace",
            "mode": "singleplayer",
            "agent_runtime": "claude",
            "_db_phase": "bugflow-queue",
        },
    )
    feature_store = _FakeFeatureStore({feature.id: feature})

    adapter = _RecoveringAdapter()
    interaction = _RecoveringInteraction()
    orchestrator = SlackWorkflowOrchestrator(adapter=adapter, interaction_runtime=interaction)
    orchestrator._env = SimpleNamespace(
        feature_store=feature_store,
        artifacts=object(),
    )
    orchestrator._recoverable_features = {
        feature.id: {
            "workspace_path": "/tmp/workspace",
            "mode": "singleplayer",
            "phase": "bugflow-queue",
            "agent_runtime": "claude",
        }
    }

    original_is_dir = Path.is_dir

    def _fake_is_dir(self):
        if str(self) == "/tmp/workspace":
            return True
        return original_is_dir(self)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "is_dir", _fake_is_dir)
        mp.setattr(
            "iriai_build_v2.interfaces.slack.orchestrator.rebuild_state",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("state rebuild exploded")),
        )
        await orchestrator._resume_workflow(feature.id, "CBUGFLOW")

    assert feature.id in orchestrator._recoverable_features
    assert adapter.messages[-1] == (
        "CBUGFLOW",
        "Resume failed for `bf123456`: state rebuild exploded\nSend any message to retry.",
    )


@pytest.mark.asyncio
async def test_start_bugflow_failure_marks_feature_failed(monkeypatch: pytest.MonkeyPatch):
    posted_threads: list[tuple[str, str, str, str]] = []

    async def _fake_post_to_thread(web, channel, thread_ts, text):
        posted_threads.append((str(web), channel, thread_ts, text))

    source_feature = SimpleNamespace(
        id="beced7b1",
        name="Complete checkout flow",
        slug="complete-checkout-flow-beced7b1",
        workflow_name="full-develop",
        metadata={"workspace_path": "/tmp/source-workspace", "channel_id": "CSOURCE"},
    )
    created_feature = SimpleNamespace(
        id="bf123456",
        name="Bugflow: Complete checkout flow",
        slug="bugflow-complete-checkout-flow-bf123456",
        workflow_name="bugfix-v2",
        metadata={},
    )
    feature_store = _FakeFeatureStore(
        {
            source_feature.id: source_feature,
            created_feature.id: created_feature,
        }
    )

    async def _fake_create_feature(store, name, workflow_name):
        assert store is feature_store
        assert workflow_name == "bugfix-v2"
        return created_feature

    async def _boom_create_channel(_name: str) -> str:
        raise RuntimeError("slack create_channel failed")

    monkeypatch.setattr(
        "iriai_build_v2.interfaces.slack.orchestrator.create_feature",
        _fake_create_feature,
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces.slack.helpers.post_to_thread",
        _fake_post_to_thread,
    )

    adapter = _RecoveringAdapter()
    adapter.create_channel = _boom_create_channel  # type: ignore[method-assign]
    interaction = _RecoveringInteraction()
    orchestrator = SlackWorkflowOrchestrator(adapter=adapter, interaction_runtime=interaction)
    orchestrator._env = SimpleNamespace(
        feature_store=feature_store,
        artifacts=_FakeArtifacts(),
        sessions=object(),
        context_provider=object(),
        feedback_service=object(),
        preview_service=object(),
        playwright_service=object(),
        artifact_mirror=object(),
    )

    await orchestrator._start_bugflow_workflow(
        ParsedRequest("bugfix-v2", "beced7b1", "beced7b1"),
        {"ts": "999.111"},
    )

    assert feature_store.transitions == [("bf123456", "failed")]
    assert created_feature.id not in orchestrator._feature_workflows
    assert posted_threads[-1][1:] == (
        "CPLANNING",
        "999.111",
        "Could not start bugflow for `beced7b1`: slack create_channel failed",
    )
