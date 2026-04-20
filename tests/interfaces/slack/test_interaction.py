"""Tests for SlackInteractionRuntime: card posting, action handling, modal resolution."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from iriai_compose.prompts import Select
from iriai_compose.storage import AgentSession
from iriai_compose.tasks import Ask

from iriai_build_v2.interfaces.slack.interaction import (
    SlackInteractionRuntime,
    _extract_question,
    _parse_modal_submission,
)
from iriai_build_v2.planning_signals import BACKGROUND_RESPONSE, GateRejection
from iriai_build_v2.roles import user


# ── Fixtures / Mocks ────────────────────────────────────────────────────────


@dataclass
class MockAdapter:
    """Minimal adapter mock for testing interaction runtime."""

    posted_blocks: list[tuple[str, list[dict], str]] = field(default_factory=list)
    posted_block_kwargs: list[dict[str, Any]] = field(default_factory=list)
    posted_decisions: list[dict] = field(default_factory=list)
    updated_messages: list[dict] = field(default_factory=list)
    opened_modals: list[dict] = field(default_factory=list)

    async def post_blocks(self, channel, blocks, text, **kwargs):
        self.posted_blocks.append((channel, blocks, text))
        self.posted_block_kwargs.append(kwargs)
        return "1234.5678"

    async def post_decision(self, channel, decision_id, title, context, options, **kwargs):
        self.posted_decisions.append(
            {"channel": channel, "id": decision_id, "title": title, "options": options}
        )
        return "1234.5678"

    async def update_message(self, channel, ts, *, text=None, blocks=None):
        self.updated_messages.append(
            {"channel": channel, "ts": ts, "text": text, "blocks": blocks}
        )

    async def resolve_decision(self, channel, ts, title, selected, user_id, feedback=""):
        self.updated_messages.append(
            {"channel": channel, "ts": ts, "selected": selected, "user_id": user_id}
        )

    async def open_modal(self, trigger_id, view):
        self.opened_modals.append({"trigger_id": trigger_id, "view": view})


@dataclass
class FakePending:
    """Minimal Pending-like object for testing."""

    id: str
    kind: str
    prompt: str
    feature_id: str = "feat-1"
    phase_name: str = "pm"
    options: list[str] | None = None


@dataclass
class MockSessionStore:
    sessions: dict[str, AgentSession] = field(default_factory=dict)

    async def load(self, session_key: str) -> AgentSession | None:
        return self.sessions.get(session_key)

    async def save(self, session: AgentSession) -> None:
        self.sessions[session.session_key] = session


@dataclass
class MockFeatureStore:
    events: list[dict[str, Any]] = field(default_factory=list)

    async def log_event(
        self,
        feature_id: str,
        event_type: str,
        source: str,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(
            {
                "feature_id": feature_id,
                "event_type": event_type,
                "source": source,
                "content": content,
                "metadata": metadata or {},
            }
        )


# ── Channel Registration ────────────────────────────────────────────────────


class TestChannelRegistration:
    def test_register_and_has_pending_false(self):
        runtime = SlackInteractionRuntime(MockAdapter())
        runtime.register_channel("feat-1", "C001")
        assert not runtime.has_pending("C001")

    def test_has_pending_tracks_feature_mapping_not_pending_id_shape(self):
        runtime = SlackInteractionRuntime(MockAdapter())
        runtime.register_channel("feat-1", "C001")
        runtime._pending_features["uuid-like-id"] = "feat-1"
        runtime._pending_futures["uuid-like-id"] = object()

        assert runtime.has_pending("C001")

    def test_unregister_clears_mapping(self):
        runtime = SlackInteractionRuntime(MockAdapter())
        runtime.register_channel("feat-1", "C001")
        runtime.unregister_channel("feat-1")
        assert not runtime.has_pending("C001")


class TestInstrumentation:
    @pytest.mark.asyncio
    async def test_gate_action_logs_missing_pending(self):
        runtime = SlackInteractionRuntime(MockAdapter())
        runtime.register_channel("feat-1", "C001")
        feature_store = MockFeatureStore()
        runtime._feature_store = feature_store

        await runtime.handle_action(
            {
                "channel": {"id": "C001"},
                "message": {"ts": "123.456"},
                "user": {"id": "U123"},
                "trigger_id": "trigger",
            },
            {"action_id": "gate_missing_reject"},
        )

        await asyncio.sleep(0)

        assert [event["event_type"] for event in feature_store.events] == [
            "slack_action_received",
            "slack_action_missing_pending",
        ]

    @pytest.mark.asyncio
    async def test_view_submission_logs_missing_pending(self):
        runtime = SlackInteractionRuntime(MockAdapter())
        feature_store = MockFeatureStore()
        runtime._feature_store = feature_store
        runtime._pending_features["modal-gate"] = "feat-1"

        await runtime.handle_view_submission(
            {
                "user": {"id": "U123"},
                "view": {
                    "private_metadata": json.dumps(
                        {"pending_id": "modal-gate", "kind": "gate_reject"}
                    ),
                    "state": {"values": {"reply_block": {"reply_input": {"value": "Needs revision"}}}},
                },
            }
        )

        await asyncio.sleep(0)

        assert [event["event_type"] for event in feature_store.events] == [
            "slack_view_submission_received",
            "slack_view_submission_missing_pending",
            "slack_pending_missing",
        ]


# ── Resolve: Respond Card ───────────────────────────────────────────────────


class TestResolveRespond:
    @pytest.mark.asyncio
    async def test_ask_posts_respond_blocks(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)
        runtime.register_channel("feat-1", "C001")

        task = Ask(actor=user, prompt="What is the goal?")

        async def resolve_later():
            await asyncio.sleep(0.01)
            pending_id = next(iter(runtime._pending_futures))
            runtime._resolve_pending(pending_id, "Build a dashboard")

        waiter = asyncio.create_task(resolve_later())
        result = await runtime.ask(task, feature_id="feat-1", phase_name="pm")
        await waiter

        assert result == "Build a dashboard"
        assert len(adapter.posted_blocks) == 1
        channel, _blocks, _text = adapter.posted_blocks[0]
        assert channel == "C001"

    @pytest.mark.asyncio
    async def test_posts_respond_blocks(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)
        runtime.register_channel("feat-1", "C001")

        pending = FakePending(id="p1", kind="respond", prompt="What is the goal?")

        async def resolve_later():
            await asyncio.sleep(0.01)
            runtime._resolve_pending("p1", "Build a dashboard")

        task = asyncio.create_task(resolve_later())
        result = await runtime.resolve(pending)
        await task

        assert result == "Build a dashboard"
        assert len(adapter.posted_blocks) == 1
        channel, blocks, _text = adapter.posted_blocks[0]
        assert channel == "C001"

    @pytest.mark.asyncio
    async def test_stores_pending_options(self):
        import json

        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)
        runtime.register_channel("feat-1", "C001")

        prompt = json.dumps({"question": "Pick?", "options": ["A", "B"]})
        pending = FakePending(id="p1", kind="respond", prompt=prompt)

        async def resolve_later():
            await asyncio.sleep(0.01)
            # Verify options are stored
            assert runtime._pending_options.get("p1") == ["A", "B"]
            runtime._resolve_pending("p1", "A")

        task = asyncio.create_task(resolve_later())
        await runtime.resolve(pending)
        await task

    @pytest.mark.asyncio
    async def test_raises_without_registered_channel(self):
        runtime = SlackInteractionRuntime(MockAdapter())
        pending = FakePending(id="p1", kind="respond", prompt="Q?", feature_id="unknown")

        with pytest.raises(RuntimeError, match="No Slack channel"):
            await runtime.resolve(pending)

    @pytest.mark.asyncio
    async def test_thread_runtime_posts_into_thread(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)
        runtime.register_channel("feat-1", "C001")
        thread_runtime = runtime.make_thread_runtime(
            feature_id="feat-1",
            channel="C001",
            thread_ts="1111.2222",
        )

        pending = FakePending(id="p-thread", kind="respond", prompt="What happened?")

        async def resolve_later():
            await asyncio.sleep(0.01)
            runtime._resolve_pending("p-thread", "It broke")

        task = asyncio.create_task(resolve_later())
        result = await thread_runtime.resolve(pending)
        await task

        assert result == "It broke"
        assert adapter.posted_block_kwargs[-1]["thread_ts"] == "1111.2222"

    @pytest.mark.asyncio
    async def test_thread_runtime_persists_user_turns_to_thread_runtime_session(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)
        runtime.register_channel("feat-1", "C001")
        runtime._session_store = MockSessionStore(
            {"thread-session": AgentSession(session_key="thread-session", metadata={"turns": []})}
        )
        runtime._agent_runtime = SimpleNamespace(get_active_session_key=lambda _feature_id: "wrong-session")
        thread_runtime = runtime.make_thread_runtime(
            feature_id="feat-1",
            channel="C001",
            thread_ts="1111.2222",
            persist_turns=True,
            agent_runtime=SimpleNamespace(get_active_session_key=lambda _feature_id: "thread-session"),
        )

        pending = FakePending(id="p-thread-persist", kind="respond", prompt="What happened?")

        async def resolve_later():
            await asyncio.sleep(0.01)
            runtime._resolve_pending("p-thread-persist", "It broke")

        task = asyncio.create_task(resolve_later())
        result = await thread_runtime.resolve(pending)
        await task
        await asyncio.sleep(0)

        assert result == "It broke"
        saved = await runtime._session_store.load("thread-session")
        assert saved is not None
        assert saved.metadata["turns"] == [{"role": "user", "text": "It broke", "turn": 1}]


# ── Resolve: Approve Card ──────────────────────────────────────────────────


class TestResolveApprove:
    @pytest.mark.asyncio
    async def test_posts_approve_blocks(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)
        runtime.register_channel("feat-1", "C001")

        pending = FakePending(id="p2", kind="approve", prompt="Approve the PRD?")

        async def approve_later():
            await asyncio.sleep(0.01)
            runtime._resolve_pending("p2", True)

        task = asyncio.create_task(approve_later())
        result = await runtime.resolve(pending)
        await task

        assert result is True
        assert len(adapter.posted_blocks) == 1

    @pytest.mark.asyncio
    async def test_preserves_gate_rejection_when_future_result_is_bool_false(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)
        runtime.register_channel("feat-1", "C001")
        runtime._feature_store = MockFeatureStore()

        pending = FakePending(id="p2", kind="approve", prompt="Approve the PRD?")

        async def reject_later():
            await asyncio.sleep(0.01)
            runtime._pending_values["p2"] = GateRejection("Use the reject feedback")
            runtime._pending_futures["p2"].set_result(False)

        task = asyncio.create_task(reject_later())
        result = await runtime.resolve(pending)
        await task
        await asyncio.sleep(0)

        assert result == GateRejection("Use the reject feedback")
        assert any(
            event["event_type"] == "slack_pending_result_mismatch"
            and event["metadata"]["future_type"] == "bool"
            and event["metadata"]["stored_type"] == "GateRejection"
            for event in runtime._feature_store.events
        )


# ── Resolve: Choose Card ───────────────────────────────────────────────────


class TestResolveChoose:
    @pytest.mark.asyncio
    async def test_ask_select_maps_to_choose(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)
        runtime.register_channel("feat-1", "C001")

        task = Ask(
            actor=user,
            prompt="Pick an option",
            input=Select(options=["Option A", "Option B"]),
            input_type=Select,
        )

        async def choose_later():
            await asyncio.sleep(0.01)
            pending_id = next(iter(runtime._pending_futures))
            runtime._resolve_pending(pending_id, "Option A")

        waiter = asyncio.create_task(choose_later())
        result = await runtime.ask(task, feature_id="feat-1", phase_name="pm")
        await waiter

        assert result == "Option A"
        assert len(adapter.posted_blocks) == 1

    @pytest.mark.asyncio
    async def test_posts_choose_card(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)
        runtime.register_channel("feat-1", "C001")

        pending = FakePending(
            id="p3", kind="choose", prompt="Pick an option",
            options=["Option A", "Option B"],
        )

        async def choose_later():
            await asyncio.sleep(0.01)
            runtime._resolve_pending("p3", "Option A")

        task = asyncio.create_task(choose_later())
        result = await runtime.resolve(pending)
        await task

        assert result == "Option A"
        assert len(adapter.posted_blocks) == 1


# ── Respond Action Handling ─────────────────────────────────────────────────


class TestRespondActions:
    @pytest.mark.asyncio
    async def test_option_button_resolves(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        runtime._pending_futures["abc"] = future
        runtime._pending_options["abc"] = ["CLI command", "Web page"]
        runtime._pending_messages["abc"] = ("C001", "123.456")

        body = {"trigger_id": "t1", "channel": {"id": "C001"}, "message": {"ts": "123.456"}, "user": {"id": "U001"}}
        action = {"action_id": "respond_abc_opt_0"}

        await runtime.handle_action(body, action)

        assert future.done()
        assert future.result() == "CLI command"

    @pytest.mark.asyncio
    async def test_reply_opens_modal(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        runtime._pending_futures["abc"] = loop.create_future()

        body = {"trigger_id": "t1", "channel": {"id": "C001"}, "message": {"ts": "123.456"}, "user": {"id": "U001"}}
        action = {"action_id": "respond_abc_reply"}

        await runtime.handle_action(body, action)

        assert len(adapter.opened_modals) == 1
        submission = _parse_modal_submission(
            adapter.opened_modals[0]["view"]["private_metadata"]
        )
        assert submission.pending_id == "abc"
        assert submission.kind == "reply"

    @pytest.mark.asyncio
    async def test_dropdown_select_resolves(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        runtime._pending_futures["abc"] = future
        runtime._pending_options["abc"] = ["A", "B", "C", "D", "E", "F"]
        runtime._pending_messages["abc"] = ("C001", "1")

        body = {"trigger_id": "t1", "channel": {"id": "C001"}, "message": {"ts": "1"}, "user": {"id": "U1"}}
        action = {"action_id": "respond_abc_select", "selected_option": {"value": "2"}}

        await runtime.handle_action(body, action)

        assert future.done()
        assert future.result() == "C"

    @pytest.mark.asyncio
    async def test_background_button_resolves_with_background_sentinel(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        runtime._pending_futures["abc"] = future
        runtime._pending_messages["abc"] = ("C001", "123.456")

        body = {"trigger_id": "t1", "channel": {"id": "C001"}, "message": {"ts": "123.456"}, "user": {"id": "U001"}}
        action = {"action_id": "respond_abc_background"}

        await runtime.handle_action(body, action)

        assert future.done()
        assert future.result() == BACKGROUND_RESPONSE


# ── Gate Action Handling ────────────────────────────────────────────────────


class TestGateActions:
    @pytest.mark.asyncio
    async def test_approve_resolves(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        runtime._pending_futures["abc"] = future
        runtime._pending_messages["abc"] = ("C001", "123.456")

        body = {"trigger_id": "t1", "channel": {"id": "C001"}, "message": {"ts": "123.456"}, "user": {"id": "U001"}}
        action = {"action_id": "gate_abc_approve"}

        await runtime.handle_action(body, action)

        assert future.done()
        assert future.result() is True

    @pytest.mark.asyncio
    async def test_reject_opens_modal(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        runtime._pending_futures["abc"] = loop.create_future()

        body = {"trigger_id": "t1", "channel": {"id": "C001"}, "message": {"ts": "123.456"}, "user": {"id": "U001"}}
        action = {"action_id": "gate_abc_reject"}

        await runtime.handle_action(body, action)

        assert len(adapter.opened_modals) == 1
        view = adapter.opened_modals[0]["view"]
        submission = _parse_modal_submission(view["private_metadata"])
        assert submission.pending_id == "abc"
        assert submission.kind == "gate_reject"
        assert view["blocks"][0].get("optional") is True


# ── Choose Action Handling ──────────────────────────────────────────────────


class TestChooseActions:
    @pytest.mark.asyncio
    async def test_option_resolves(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        runtime._pending_futures["sel"] = future
        runtime._pending_options["sel"] = ["Option A", "Option B"]
        runtime._pending_messages["sel"] = ("C001", "1")

        body = {"trigger_id": "t1", "channel": {"id": "C001"}, "message": {"ts": "1"}, "user": {"id": "U1"}}
        action = {"action_id": "choose_sel_opt_1"}

        await runtime.handle_action(body, action)

        assert future.done()
        assert future.result() == "Option B"


# ── Legacy Decision Actions ─────────────────────────────────────────────────


class TestLegacyDecision:
    @pytest.mark.asyncio
    async def test_legacy_approve_still_works(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        runtime._pending_futures["abc"] = future

        body = {"trigger_id": "t1", "channel": {"id": "C001"}, "message": {"ts": "1"}, "user": {"id": "U1"}}
        action = {"action_id": "decision_abc_approve"}

        await runtime.handle_action(body, action)

        assert future.done()
        assert future.result() is True

    @pytest.mark.asyncio
    async def test_legacy_option_still_works(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        runtime._pending_futures["abc"] = future

        body = {"trigger_id": "t1", "channel": {"id": "C001"}, "message": {"ts": "1"}, "user": {"id": "U1"}}
        action = {"action_id": "decision_abc_singleplayer"}

        await runtime.handle_action(body, action)

        assert future.done()
        assert future.result() == "singleplayer"


# ── View Submission ─────────────────────────────────────────────────────────


class TestHandleViewSubmission:
    @pytest.mark.asyncio
    async def test_resolves_pending_with_text(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        runtime._pending_futures["modal1"] = future

        payload = {
            "user": {"id": "U001"},
            "view": {
                "private_metadata": "modal1",
                "state": {
                    "values": {
                        "reply_block": {
                            "reply_input": {"value": "Here is my detailed reply"}
                        }
                    }
                },
            },
        }

        await runtime.handle_view_submission(payload)

        assert future.done()
        assert future.result() == "Here is my detailed reply"

    @pytest.mark.asyncio
    async def test_ignores_empty_text(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        runtime._pending_futures["modal2"] = future

        payload = {
            "user": {"id": "U001"},
            "view": {
                "private_metadata": "modal2",
                "state": {"values": {"reply_block": {"reply_input": {"value": ""}}}},
            },
        }

        await runtime.handle_view_submission(payload)

        assert future.done()
        assert future.result() == "Please revise."

    @pytest.mark.asyncio
    async def test_gate_reject_submission_preserves_feedback_and_updates_card(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        runtime._pending_futures["modal-gate"] = future
        runtime._pending_messages["modal-gate"] = ("C001", "msg.ts")
        runtime._pending_titles["modal-gate"] = "Approval Required"

        payload = {
            "user": {"id": "U001"},
            "view": {
                "private_metadata": json.dumps(
                    {"pending_id": "modal-gate", "kind": "gate_reject"}
                ),
                "state": {
                    "values": {
                        "reply_block": {
                            "reply_input": {"value": "Please tighten the rollout plan"}
                        }
                    }
                },
            },
        }

        await runtime.handle_view_submission(payload)
        await asyncio.sleep(0.02)

        assert future.done()
        assert future.result() == GateRejection("Please tighten the rollout plan")
        assert adapter.updated_messages
        blocks = adapter.updated_messages[-1]["blocks"]
        assert "Approval Required" in blocks[0]["text"]["text"]
        assert "Rejected" in blocks[0]["text"]["text"]
        assert "Please tighten the rollout plan" in blocks[0]["text"]["text"]

    @pytest.mark.asyncio
    async def test_legacy_reject_resolves_to_gate_rejection(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        runtime._pending_futures["abc"] = future

        body = {"trigger_id": "t1", "channel": {"id": "C001"}, "message": {"ts": "1"}, "user": {"id": "U1"}}
        action = {"action_id": "decision_abc_reject"}

        await runtime.handle_action(body, action)

        assert future.done()
        assert future.result() == GateRejection()


# ── Card Update on Resolution ───────────────────────────────────────────────


class TestCardUpdateOnResolve:
    @pytest.mark.asyncio
    async def test_card_updated_to_resolved(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        runtime._pending_futures["abc"] = future
        runtime._pending_messages["abc"] = ("C001", "msg.ts")

        runtime._resolve_pending("abc", True, label="Approved", user_id="U001")

        # Give the async update task a chance to run
        await asyncio.sleep(0.02)

        assert future.done()
        assert future.result() is True
        # Card should have been updated
        assert len(adapter.updated_messages) >= 1

    @pytest.mark.asyncio
    async def test_gate_card_uses_gate_title_when_approved(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        runtime._pending_futures["gate-approve"] = future
        runtime._pending_messages["gate-approve"] = ("C001", "msg.ts")
        runtime._pending_titles["gate-approve"] = "Approval Required"

        runtime._resolve_pending("gate-approve", True, label="Approved", user_id="U001")

        await asyncio.sleep(0.02)

        assert adapter.updated_messages
        blocks = adapter.updated_messages[-1]["blocks"]
        assert "Approval Required" in blocks[0]["text"]["text"]
        assert "Approved" in blocks[0]["text"]["text"]


# ── handle_message is no-op ─────────────────────────────────────────────────


class TestHandleMessage:
    @pytest.mark.asyncio
    async def test_handle_message_is_noop(self):
        adapter = MockAdapter()
        runtime = SlackInteractionRuntime(adapter)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        runtime._pending_futures["abc"] = future

        # Channel message should NOT resolve the pending
        await runtime.handle_message({"channel": "C001", "text": "some message"})

        assert not future.done()


# ── Prompt Parsing ──────────────────────────────────────────────────────────


class TestExtractQuestion:
    def test_plain_text(self):
        question, options = _extract_question("What do you think?")
        assert question == "What do you think?"
        assert options == []

    def test_json_with_question(self):
        import json

        prompt = json.dumps({"question": "Pick a color", "options": ["red", "blue"]})
        question, options = _extract_question(prompt)
        assert question == "Pick a color"
        assert options == ["red", "blue"]

    def test_json_without_question_key(self):
        import json

        prompt = json.dumps({"message": "hello"})
        question, options = _extract_question(prompt)
        # JSON objects without "question" key show fallback, not raw JSON
        assert "processing" in question.lower() or "feedback" in question.lower()
        assert options == []

    def test_non_dict_json(self):
        question, options = _extract_question("[1, 2, 3]")
        assert question == "[1, 2, 3]"
        assert options == []

    def test_invalid_json(self):
        question, options = _extract_question("not json {")
        assert question == "not json {"
        assert options == []
