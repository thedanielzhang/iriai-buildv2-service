from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.supervisor.digest_dedupe import (
    SUPPRESSION_COOLDOWN,
    SupervisorDigestDedupeStore,
    compute_dedupe_key,
)
from iriai_build_v2.supervisor.slack import (
    SupervisorSlackDigestDecisionStore,
    SupervisorRuntimeService,
    SupervisorSlackRoute,
    SupervisorSlackRouter,
    run_supervisor_slack_app,
)
from iriai_build_v2.supervisor.models import (
    ActionLevel,
    EvidencePacket,
    FailureClass,
    StaleCodexInvocation,
    SupervisorDigestKey,
    SupervisorMode,
)


class _FakeAdapter:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str | None]] = []
        self.updates: list[tuple[str, str, str]] = []
        self.block_posts: list[tuple[str, list, str, str | None]] = []
        self.block_updates: list[tuple[str, str, list, str]] = []

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
        if blocks is not None:
            self.block_updates.append((channel, ts, blocks, text or ""))

    async def post_blocks(
        self,
        channel: str,
        blocks: list[dict],
        text: str,
        *,
        thread_ts: str | None = None,
    ) -> str:
        self.block_posts.append((channel, blocks, text, thread_ts))
        return "2.345"


class _FakeService:
    def __init__(self) -> None:
        self.questions: list[SupervisorSlackRoute] = []
        self.actions: list[SupervisorSlackRoute] = []
        self.instructions: list[SupervisorSlackRoute] = []
        self.stale_actions: list[tuple[str, str]] = []

    async def answer_question(self, route: SupervisorSlackRoute) -> str:
        self.questions.append(route)
        return f"answer:{route.text}"

    async def evaluate_action_request(self, route: SupervisorSlackRoute) -> str:
        self.actions.append(route)
        return f"action:{route.text}"

    async def route_workflow_instruction(self, route: SupervisorSlackRoute) -> str:
        self.instructions.append(route)
        return f"instruction:{route.text}"

    async def handle_stale_codex_action(self, action_id: str, value: str) -> str:
        self.stale_actions.append((action_id, value))
        return f"stale-action:{action_id}:{value}"


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
        self.list_records_calls: list[dict[str, object]] = []
        self.list_summary_calls: list[dict[str, object]] = []

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
        self.list_records_calls.append({
            "feature_id": feature_id,
            "prefixes": prefixes,
            "after_id": after_id,
            "limit": limit,
            "order": order,
        })
        rows = [
            {"id": idx + 1, "key": key, "value": value}
            for idx, (key, value, _feature) in enumerate(self.writes)
            if idx + 1 > after_id and any(key.startswith(prefix) for prefix in prefixes)
        ]
        rows = sorted(rows, key=lambda row: row["id"], reverse=(order == "desc"))
        return rows[:limit]

    async def list_record_summaries(
        self,
        *,
        feature_id: str,
        prefixes,
        after_id: int,
        limit: int = 500,
        order: str = "asc",
    ):
        self.list_summary_calls.append({
            "feature_id": feature_id,
            "prefixes": prefixes,
            "after_id": after_id,
            "limit": limit,
            "order": order,
        })
        rows = [
            {
                "id": idx + 1,
                "key": key,
                "value": "",
                "value_preview": value,
                "summary_only": True,
            }
            for idx, (key, value, _feature) in enumerate(self.writes)
            if idx + 1 > after_id and any(key.startswith(prefix) for prefix in prefixes)
        ]
        rows = sorted(rows, key=lambda row: row["id"], reverse=(order == "desc"))
        return rows[:limit]


class _SupervisorDigestPool:
    def __init__(self) -> None:
        self.state: list[dict[str, object]] = []
        self.audit: list[dict[str, object]] = []

    @staticmethod
    def _semantic_dedupe_enabled(row: dict[str, object]) -> bool:
        payload = row.get("payload")
        if isinstance(payload, dict):
            return bool(payload.get("semantic_dedupe"))
        return False

    async def fetch(self, sql: str, *args):
        normalized = " ".join(sql.lower().split())
        if "from supervisor_slack_digest_state" not in normalized:
            return []
        if "status = 'pending'" in normalized or "status in ('pending', 'suppressed')" in normalized:
            feature_id, dedupe_key, signature_hash, semantic_dedupe, semantic_hash = args
            return [
                row
                for row in self.state
                if row["feature_id"] == feature_id
                and row["status"] in {"pending", "suppressed"}
                and not (row["status"] == "pending" and row.get("stale_pending"))
                and (
                    row["dedupe_key"] == dedupe_key
                    or row["signature_hash"] == signature_hash
                    or (
                        bool(semantic_dedupe)
                        and self._semantic_dedupe_enabled(row)
                        and row["semantic_signature_hash"] == semantic_hash
                    )
                )
            ][:1]
        feature_id, dedupe_key, signature_hash, semantic_dedupe, semantic_hash = args
        return [
            row
            for row in self.state
            if row["feature_id"] == feature_id
            and row["status"] == "delivered"
            and (
                row["dedupe_key"] == dedupe_key
                or row["signature_hash"] == signature_hash
                or (
                    bool(semantic_dedupe)
                    and self._semantic_dedupe_enabled(row)
                    and row["semantic_signature_hash"] == semantic_hash
                )
            )
        ][:1]

    async def fetchrow(self, sql: str, *args):
        normalized = " ".join(sql.lower().split())
        if "insert into supervisor_slack_digest_state" not in normalized:
            return None
        row = self._record_attempt_state(*args)
        return {"dedupe_key": row["dedupe_key"]} if row is not None else None

    async def execute(self, sql: str, *args):
        normalized = " ".join(sql.lower().split())
        if "insert into supervisor_slack_digest_state" in normalized:
            self._record_attempt_state(*args)
            return "INSERT 0 1"
        if "insert into supervisor_slack_digest_audit" in normalized:
            (
                feature_id,
                dedupe_key,
                snapshot_version,
                decision,
                reason,
                signature_hash,
                semantic_signature_hash,
                channel,
                thread_ts,
                message_ts,
                citations,
                payload,
            ) = args
            self.audit.append(
                {
                    "feature_id": feature_id,
                    "dedupe_key": dedupe_key,
                    "snapshot_version": snapshot_version,
                    "decision": decision,
                    "reason": reason,
                    "signature_hash": signature_hash,
                    "semantic_signature_hash": semantic_signature_hash,
                    "channel": channel,
                    "thread_ts": thread_ts,
                    "message_ts": message_ts,
                    "citations": json.loads(citations),
                    "payload": json.loads(payload),
                }
            )
            return "INSERT 0 1"
        if "update supervisor_slack_digest_state" in normalized and "delivered" in normalized:
            feature_id, dedupe_key, channel, thread_ts, message_ts = args
            for row in self.state:
                if row["feature_id"] == feature_id and row["dedupe_key"] == dedupe_key:
                    row.update(
                        {
                            "status": "delivered",
                            "channel": channel,
                            "thread_ts": thread_ts,
                            "message_ts": message_ts,
                        }
                    )
            return "UPDATE 1"
        if "update supervisor_slack_digest_state" in normalized and "suppressed" in normalized:
            feature_id, dedupe_key, reason = args
            for row in self.state:
                if (
                    row["feature_id"] == feature_id
                    and row["dedupe_key"] == dedupe_key
                    and row["status"] == "pending"
                ):
                    row.update({"status": "suppressed", "suppress_reason": reason})
            return "UPDATE 1"
        if "update supervisor_slack_digest_state" in normalized and "failed" in normalized:
            feature_id, dedupe_key, reason = args
            for row in self.state:
                if row["feature_id"] == feature_id and row["dedupe_key"] == dedupe_key:
                    row.update({"status": "failed", "suppress_reason": reason})
            return "UPDATE 1"
        raise AssertionError(sql)

    def _record_attempt_state(self, *args):
        (
            feature_id,
            dedupe_key,
            snapshot_version,
            signature_hash,
            semantic_signature_hash,
            classification,
            recommended_action,
            group_idx,
            retry,
            channel,
            thread_ts,
            reason,
            citations,
            payload,
            semantic_dedupe,
        ) = args
        payload_dict = json.loads(payload)
        active_duplicate = next(
            (
                row for row in self.state
                if row["feature_id"] == feature_id
                and row["status"] in {"pending", "delivered", "suppressed"}
                and not (row["status"] == "pending" and row.get("stale_pending"))
                and (
                    row["dedupe_key"] == dedupe_key
                    or row["signature_hash"] == signature_hash
                    or (
                        bool(semantic_dedupe)
                        and self._semantic_dedupe_enabled(row)
                        and row["semantic_signature_hash"] == semantic_signature_hash
                    )
                )
            ),
            None,
        )
        if active_duplicate is not None:
            return None
        existing = next(
            (
                row for row in self.state
                if row["feature_id"] == feature_id
                and row["dedupe_key"] == dedupe_key
            ),
            None,
        )
        existing_is_reclaimable_pending = (
            existing is not None
            and existing.get("status") == "pending"
            and bool(existing.get("stale_pending"))
        )
        if (
            existing is not None
            and existing.get("status") != "failed"
            and not existing_is_reclaimable_pending
        ):
            return None
        row = existing if existing is not None else {}
        row.update(
            {
                "feature_id": feature_id,
                "dedupe_key": dedupe_key,
                "snapshot_version": snapshot_version,
                "signature_hash": signature_hash,
                "semantic_signature_hash": semantic_signature_hash,
                "classification": classification,
                "recommended_action": recommended_action,
                "group_idx": group_idx,
                "retry": retry,
                "status": "pending",
                "channel": channel,
                "thread_ts": thread_ts,
                "message_ts": None,
                "send_reason": reason,
                "suppress_reason": "",
                "citations": json.loads(citations),
                "payload": payload_dict,
                "stale_pending": False,
            }
        )
        if existing is None:
            self.state.append(row)
        return row


class _DigestDedupePool:
    """In-memory stand-in for the two Slice-10d ``supervisor_digest_*`` tables.

    Mirrors ``tests/supervisor/test_digest_dedupe.py``'s ``_FakePool`` — just
    enough asyncpg-shaped ``fetch`` / ``fetchrow`` / ``execute`` behavior for
    :class:`SupervisorDigestDedupeStore` so the Slice-10d-2 ``watch_and_digest``
    routing can be exercised end-to-end without Postgres. Distinct from
    ``_SupervisorDigestPool`` (the LEGACY ``supervisor_slack_digest_*`` tables).
    """

    def __init__(self) -> None:
        # (feature_id, dedupe_key) -> state-row dict
        self.state: dict[tuple[str, str], dict] = {}
        self.audit: list[dict] = []
        self._state_seq = 0
        self._audit_seq = 0
        self.fail = False  # flip to simulate a store outage (DigestDedupeStoreError)

    async def fetchrow(self, query: str, *args):
        if self.fail:
            raise RuntimeError("simulated dedupe store outage")
        text = " ".join(query.split())
        if text.startswith("SELECT id, feature_id, group_idx, dedupe_key,"):
            return self.state.get((args[0], args[1]))
        if text.startswith("INSERT INTO supervisor_digest_state"):
            feature_id, group_idx, dedupe_key = args[0], args[1], args[2]
            (
                last_snapshot_version,
                classification,
                recommended_action,
                recommended_route,
                last_sent_at,
                suppressed_count,
                _payload,
            ) = args[3:10]
            existing = self.state.get((feature_id, dedupe_key))
            if existing is None:
                self._state_seq += 1
                row = {
                    "id": self._state_seq,
                    "feature_id": feature_id,
                    "group_idx": group_idx,
                    "dedupe_key": dedupe_key,
                    "last_snapshot_version": last_snapshot_version,
                    "classification": classification,
                    "recommended_action": recommended_action,
                    "recommended_route": recommended_route,
                    "last_sent_at": last_sent_at,
                    "suppressed_count": suppressed_count,
                    "last_digest_payload": {},
                    "created_at": None,
                    "updated_at": None,
                }
                self.state[(feature_id, dedupe_key)] = row
            else:
                row = existing
                row["group_idx"] = group_idx
                row["last_snapshot_version"] = last_snapshot_version
                row["classification"] = classification
                row["recommended_action"] = recommended_action
                row["recommended_route"] = recommended_route
                if last_sent_at is not None:  # COALESCE(EXCLUDED, existing)
                    row["last_sent_at"] = last_sent_at
                row["suppressed_count"] = suppressed_count
            return {"id": row["id"]}
        raise AssertionError(f"unexpected fetchrow query: {text}")

    async def execute(self, query: str, *args):
        if self.fail:
            raise RuntimeError("simulated dedupe store outage")
        text = " ".join(query.split())
        if text.startswith("INSERT INTO supervisor_digest_audit"):
            self._audit_seq += 1
            self.audit.append(
                {
                    "id": self._audit_seq,
                    "state_id": args[0],
                    "feature_id": args[1],
                    "group_idx": args[2],
                    "dedupe_key": args[3],
                    "snapshot_version": args[4],
                    "should_send": args[5],
                    "reason": args[6],
                }
            )
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute query: {text}")

    async def fetch(self, query: str, *args):
        if self.fail:
            raise RuntimeError("simulated dedupe store outage")
        text = " ".join(query.split())
        if "FROM supervisor_digest_audit" in text:
            rows = [
                r
                for r in self.audit
                if r["feature_id"] == args[0] and r["dedupe_key"] == args[1]
            ]
            rows.sort(key=lambda r: r["id"], reverse=True)
            return rows[: args[2]]
        raise AssertionError(f"unexpected fetch query: {text}")


class _PersistingSupervisorApp(_FakeSupervisorApp):
    def __init__(self) -> None:
        super().__init__()
        self.feature_store = _RecordingFeatureStore()
        self.artifact_store = _RecordingArtifactStore()


class _StaleCodexSupervisorApp(_PersistingSupervisorApp):
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
            trace_path=(
                "/tmp/20260510T191357.256022Z-implementer-g43-t19-a0-3737e65c.jsonl"
            ),
            output_path="/tmp/output.txt",
            elapsed_seconds=16_441,
            idle_seconds=16_441,
            liveness_timeout_seconds=600,
            threshold_seconds=1800,
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
            feature_id=feature_id,
            group_idx=43,
            retry=0,
            classification=FailureClass.STALE_CODEX_INVOCATION,
            confidence=0.93,
            facts={
                "cursor": cursor,
                "next_cursor": 42,
                "event_cursor": event_cursor or cursor,
                "next_event_cursor": 24,
                "artifact_cursor": artifact_cursor or cursor,
                "next_artifact_cursor": 42,
                "bridge_log_cursor": 9,
                "stale_codex_invocation": stale.model_dump(mode="json"),
            },
            inference="A Codex invocation is alive but heartbeat-only stale.",
            recommended_action=ActionLevel.RECOMMEND,
            citations=["dashboard:/api/bridge/logs"],
        )


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
        self.kwargs: list[dict] = []

    async def invoke(self, *_args, **_kwargs):
        self.kwargs.append(_kwargs)
        if self.on_message is not None:
            self.on_message(AssistantMessage([ThinkingBlock("checking latest artifacts")]))
        await asyncio.sleep(0.01)
        return "final supervisor answer"


class _CapturingRuntime:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.kwargs: list[dict] = []

    async def invoke(self, _role, prompt, **_kwargs):
        self.prompts.append(prompt)
        self.kwargs.append(_kwargs)
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


def test_supervisor_router_accepts_mentioned_messages_from_other_channels():
    router = SupervisorSlackRouter(
        adapter=_FakeAdapter(),
        channel="CSUP",
        service=_FakeService(),
    )

    route = router.classify(
        {
            "channel": "COTHER",
            "user": "U1",
            "text": "what is the failure?",
            "mentioned_bot": True,
            "ts": "1",
        }
    )

    assert route.kind == "supervisor_question"
    assert route.channel == "COTHER"


def test_supervisor_router_ignores_unmentioned_messages_from_other_channels():
    router = SupervisorSlackRouter(
        adapter=_FakeAdapter(),
        channel="CSUP",
        service=_FakeService(),
    )

    route = router.classify(
        {
            "channel": "COTHER",
            "user": "U1",
            "text": "what is the failure?",
            "mentioned_bot": False,
            "ts": "1",
        }
    )

    assert route.kind == "ignore"


def test_supervisor_router_accepts_direct_messages():
    router = SupervisorSlackRouter(
        adapter=_FakeAdapter(),
        channel="CSUP",
        service=_FakeService(),
    )

    route = router.classify(
        {
            "channel": "D123",
            "user": "U1",
            "text": "what is the failure?",
            "ts": "1",
        }
    )

    assert route.kind == "supervisor_question"


def test_supervisor_router_keeps_cross_channel_mutations_question_only():
    router = SupervisorSlackRouter(
        adapter=_FakeAdapter(),
        channel="CSUP",
        service=_FakeService(),
    )

    mentioned = router.classify(
        {
            "channel": "COTHER",
            "user": "U1",
            "text": "restart the supervisor",
            "mentioned_bot": True,
            "ts": "1",
        }
    )
    dm = router.classify(
        {"channel": "D123", "user": "U1", "text": "Tell the implementer to ship it", "ts": "2"}
    )

    assert mentioned.kind == "supervisor_question"
    assert dm.kind == "supervisor_question"


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


def test_supervisor_router_keeps_status_question_in_supervisor_boundary():
    router = SupervisorSlackRouter(
        adapter=_FakeAdapter(),
        channel="CSUP",
        service=_FakeService(),
    )

    route = router.classify(
        {
            "channel": "CSUP",
            "user": "U1",
            "text": "Tell me what the implementer is doing right now.",
            "ts": "1",
        }
    )

    assert route.kind == "supervisor_question"


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
    assert "Supervisor degraded while answering: what changed?" in reply2


@pytest.mark.asyncio
async def test_read_only_supervisor_runtime_does_not_forward_workflow_instruction_sink():
    app = _PersistingSupervisorApp()
    forwarded: list[SupervisorSlackRoute] = []

    async def sink(route: SupervisorSlackRoute):
        forwarded.append(route)
        return {"ok": True}

    service = SupervisorRuntimeService(
        app=app,
        feature_id="feat-1",
        agent_runtime=None,
        workflow_instruction_sink=sink,
    )

    reply = await service.route_workflow_instruction(
        SupervisorSlackRoute(
            kind="workflow_instruction",
            text="tell the implementer to patch the verifier",
            channel="CSUP",
            user="U1",
        )
    )

    assert forwarded == []
    assert "read-only mode" in reply
    key, value, _feature = app.artifact_store.writes[0]
    assert key.startswith("supervisor-action:feat-1:")
    payload = json.loads(value)
    assert payload["status"] == "blocked"
    assert payload["mode"] == "read_only"
    assert "read-only supervisor mode forbids forwarding" in payload["reason"]


@pytest.mark.asyncio
async def test_supervisor_runtime_service_answers_stale_heartbeat_as_wedge():
    app = _StaleCodexSupervisorApp()
    runtime = _CapturingRuntime()
    service = SupervisorRuntimeService(
        app=app,
        feature_id="8ac124d6",
        agent_runtime=runtime,
        session_epoch="proc-1",
    )

    reply = await service.answer_question(
        SupervisorSlackRoute(
            kind="supervisor_question",
            text="is the heartbeat still alive?",
            channel="CSUP",
            user="U1",
            thread_ts="10.123",
        )
    )

    assert "heartbeat is still alive, but" in reply
    assert "heartbeat-only liveness" in reply
    assert "stale_codex_invocation" in reply
    assert "reset the stale Codex invocation" in reply
    assert "implementer-g43-t19-a0" in reply
    assert runtime.prompts == []
    assert service._stale_codex_packets["tok123"].classification == (
        FailureClass.STALE_CODEX_INVOCATION
    )
    key, value, _feature = app.artifact_store.writes[0]
    assert key.startswith("supervisor-agent-assessment:8ac124d6:e24:a42:b9:")
    payload = json.loads(value)
    assert payload["fallback"] is False
    assert payload["assessment"]["status"] == "stale_codex_invocation"
    assert payload["assessment"]["evidence_mode"] == "deterministic_current_state"
    assert payload["assessment"]["proposed_action"] == "operator_reset_stale_codex"
    assert payload["assessment"]["session_scope"] == "question-thread-10.123"


@pytest.mark.asyncio
async def test_supervisor_runtime_service_does_not_preload_failure_detail_evidence():
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

    assert app.toolbox.requests == []


@pytest.mark.asyncio
async def test_supervisor_runtime_service_does_not_preload_current_status_detail_evidence():
    app = _DetailSupervisorApp()
    service = SupervisorRuntimeService(app=app, feature_id="feat-1", agent_runtime=None)

    await service.answer_question(
        SupervisorSlackRoute(
            kind="supervisor_question",
            text="What is the current status?",
            channel="CSUP",
            user="U1",
        )
    )

    assert app.toolbox.requests == []


@pytest.mark.asyncio
async def test_supervisor_runtime_service_persists_agent_assessment():
    app = _PersistingSupervisorApp()
    service = SupervisorRuntimeService(
        app=app,
        feature_id="feat-1",
        agent_runtime=_FakeStreamingRuntime(),
        session_epoch="proc-1",
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
    assert payload["fallback_reason"] is None
    assert payload["prompt_chars"] > 0
    assert payload["assessment"]["message"] == "final supervisor answer"
    assert payload["evidence_mode"] == "mcp"
    assert payload["tool_names_used"] == ["supervisor-evidence"]
    assert payload["session_epoch"] == "proc-1"
    assert payload["session_scope"] == "question-thread-10.123"
    assert payload["assessment"]["evidence_mode"] == "mcp"
    assert payload["assessment"]["session_epoch"] == "proc-1"
    assert payload["assessment"]["session_scope"] == "question-thread-10.123"
    assert service._agent_runtime.kwargs[0]["session_key"] == (
        "workflow-supervisor:proc-1:question-thread-10.123:feat-1"
    )
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
async def test_supervisor_digest_suppresses_repeated_seed_fallback_safe_restart():
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
    )
    first = EvidencePacket(
        feature_id="feat-1",
        group_idx=44,
        retry=0,
        phase="implementation",
        classification=FailureClass.SAFE_RESTART_CANDIDATE,
        confidence=0.86,
        facts={
            "next_cursor": 100,
            "current_workflow": {
                "phase": "implementation",
                "state": "implementation",
                "latest_event_id": 100,
                "latest_artifact_id": 200,
            },
            "bridge_state": "running",
            "active_agent_event_count": 0,
            "done_agent_event_count": 0,
        },
        inference="Bridge log evidence indicates a running but wedged bridge.",
        recommended_action=ActionLevel.ACT_GUARDED,
        citations=["dashboard:/api/bridge/status", "dashboard:/api/bridge/logs"],
    )
    same_state_new_cursors = first.model_copy(
        update={
            "facts": {
                "next_cursor": 101,
                "current_workflow": {
                    "phase": "implementation",
                    "state": "implementation",
                    "latest_event_id": 101,
                    "latest_artifact_id": 201,
                },
                "bridge_state": "running",
                "active_agent_event_count": 8,
                "done_agent_event_count": 3,
            }
        }
    )

    assert service._digest_packet_to_send(first) is first
    assert service._digest_packet_to_send(same_state_new_cursors) is None


@pytest.mark.asyncio
async def test_supervisor_digest_reposts_seed_fallback_when_actionable_state_changes():
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
    )
    running = EvidencePacket(
        feature_id="feat-1",
        group_idx=44,
        retry=1,
        phase="implementation",
        classification=FailureClass.SAFE_RESTART_CANDIDATE,
        confidence=0.86,
        facts={
            "current_workflow": {
                "phase": "implementation",
                "state": "implementation",
                "latest_event_id": 100,
                "latest_artifact_id": 200,
            },
            "bridge_state": "running",
        },
        inference="Bridge status/log evidence indicates a dead or wedged bridge.",
        recommended_action=ActionLevel.ACT_GUARDED,
        citations=["dashboard:/api/bridge/status", "dashboard:/api/bridge/logs"],
    )
    unreachable = running.model_copy(
        update={
            "facts": {
                "current_workflow": {
                    "phase": "implementation",
                    "state": "implementation",
                    "latest_event_id": 101,
                    "latest_artifact_id": 201,
                },
                "bridge_state": "unreachable",
            }
        }
    )

    assert service._digest_packet_to_send(running) is running
    assert service._digest_packet_to_send(unreachable) is unreachable


@pytest.mark.asyncio
async def test_supervisor_digest_suppresses_repeated_seed_fallback_healthy_progress():
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
    )
    first = EvidencePacket(
        feature_id="feat-1",
        group_idx=45,
        retry=0,
        phase="implementation",
        classification=FailureClass.HEALTHY_PROGRESS,
        confidence=0.74,
        facts={
            "current_workflow": {
                "phase": "implementation",
                "state": "implementation",
                "latest_event_id": 29027,
                "latest_artifact_id": 1632598,
            }
        },
        inference=(
            "Current workflow snapshot shows active G45 work, with no "
            "deterministic blocker for that selected group."
        ),
        recommended_action=ActionLevel.DIGEST,
        citations=["event:29027"],
    )
    same_progress_new_event = first.model_copy(
        update={
            "facts": {
                "current_workflow": {
                    "phase": "implementation",
                    "state": "implementation",
                    "latest_event_id": 29042,
                    "latest_artifact_id": 1632598,
                }
            },
            "citations": ["event:29042"],
        }
    )

    assert service._digest_packet_to_send(first) is first
    assert service._digest_packet_to_send(same_progress_new_event) is None


@pytest.mark.asyncio
async def test_supervisor_digest_suppresses_operator_required_evidence_id_churn():
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=object(),
        min_digest_interval_seconds=0.0,
    )
    first = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.OPERATOR_REQUIRED,
        confidence=0.92,
        facts={
            "operator_required_runtime_failures": [
                {
                    "evidence_node_id": 915,
                    "citation": "event:control_plane_runtime_failure:evidence_node:915",
                }
            ]
        },
        inference="Workspace permission failure requires operator action.",
        recommended_action=ActionLevel.STOP_ESCALATE,
        citations=["event:control_plane_runtime_failure:evidence_node:915"],
    )
    second = first.model_copy(
        update={
            "facts": {
                "operator_required_runtime_failures": [
                    {
                        "evidence_node_id": 916,
                        "citation": "event:control_plane_runtime_failure:evidence_node:916",
                    }
                ]
            },
            "citations": ["event:control_plane_runtime_failure:evidence_node:916"],
        }
    )

    assert service._digest_packet_to_send(first) is first
    assert service._digest_packet_to_send(second) is None


@pytest.mark.asyncio
async def test_supervisor_digest_reposts_when_failure_signature_changes():
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=object(),
        min_digest_interval_seconds=0.0,
    )
    first = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.OPERATOR_REQUIRED,
        confidence=0.92,
        facts={
            "operator_required_runtime_failures": [
                {
                    "evidence_node_id": 915,
                    "citation": "event:control_plane_runtime_failure:evidence_node:915",
                    "failure_class": "workspace_permission",
                    "failure_type": "writeability_denied",
                    "route": "operator_required",
                }
            ]
        },
        inference="Workspace permission failure requires operator action.",
        recommended_action=ActionLevel.STOP_ESCALATE,
        citations=["event:control_plane_runtime_failure:evidence_node:915"],
    )
    changed_failure = first.model_copy(
        update={
            "facts": {
                "operator_required_runtime_failures": [
                    {
                        "evidence_node_id": 916,
                        "citation": "event:control_plane_runtime_failure:evidence_node:916",
                        "failure_class": "workspace_permission",
                        "failure_type": "writeability_directory_denied",
                        "route": "operator_required",
                    }
                ]
            },
            "citations": ["event:control_plane_runtime_failure:evidence_node:916"],
        }
    )

    assert service._digest_packet_to_send(first) is first
    assert service._digest_packet_to_send(changed_failure) is changed_failure


@pytest.mark.asyncio
async def test_supervisor_digest_durable_dedupe_survives_restart():
    pool = _SupervisorDigestPool()
    store = SupervisorSlackDigestDecisionStore(pool=pool, feature_id="feat-1")
    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={
            "control_plane_snapshot_version": "cp-42",
            "current_workflow": {
                "phase": "implementation",
                "state": "blocked",
                "latest_event_id": 300,
                "latest_artifact_id": 400,
            },
        },
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:918"],
    )
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
    )

    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is packet
    assert pool.state[0]["status"] == "pending"
    assert [record["decision"] for record in pool.audit] == ["attempt"]

    await service._record_digest_delivered(packet, channel="CSUP", message_ts="2.345")
    assert pool.state[0]["status"] == "delivered"
    assert pool.state[0]["message_ts"] == "2.345"

    restarted = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
    )
    assert await restarted._digest_packet_to_send_durable(packet, channel="CSUP") is None

    assert [record["decision"] for record in pool.audit] == [
        "attempt",
        "delivered",
        "suppress",
    ]
    assert {record["snapshot_version"] for record in pool.audit} == {"cp-42"}
    assert pool.audit[0]["signature_hash"] == pool.audit[2]["signature_hash"]
    assert pool.audit[0]["citations"] == [
        "event:control_plane_runtime_failure:evidence_node:918"
    ]


@pytest.mark.asyncio
async def test_supervisor_digest_durable_dedupe_ignores_evidence_id_churn():
    pool = _SupervisorDigestPool()
    store = SupervisorSlackDigestDecisionStore(pool=pool, feature_id="feat-1")
    first = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={
            "control_plane_snapshot_version": "cp-semantic-runtime-a",
            "runtime_failure_events": [
                {
                    "evidence_node_id": 924,
                    "citation": "event:control_plane_runtime_failure:evidence_node:924",
                    "failure_class": "runtime_context",
                    "failure_type": "context_materialization_failed",
                    "route": "retry_context",
                    "deterministic": True,
                    "retryable": True,
                }
            ],
        },
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:924"],
    )
    same_failure_new_ids = first.model_copy(
        update={
            "facts": {
                "control_plane_snapshot_version": "cp-semantic-runtime-b",
                "runtime_failure_events": [
                    {
                        "evidence_node_id": 925,
                        "citation": "event:control_plane_runtime_failure:evidence_node:925",
                        "failure_class": "runtime_context",
                        "failure_type": "context_materialization_failed",
                        "route": "retry_context",
                        "deterministic": True,
                        "retryable": True,
                    }
                ],
            },
            "citations": ["event:control_plane_runtime_failure:evidence_node:925"],
        }
    )
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=object(),
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
    )

    assert await service._digest_packet_to_send_durable(first, channel="CSUP") is first
    await service._record_digest_delivered(first, channel="CSUP", message_ts="2.345")

    restarted = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=object(),
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
    )
    assert (
        await restarted._digest_packet_to_send_durable(
            same_failure_new_ids,
            channel="CSUP",
        )
        is None
    )

    assert [record["decision"] for record in pool.audit] == [
        "attempt",
        "delivered",
        "suppress",
    ]
    assert pool.audit[-1]["reason"] == "delivered_duplicate"
    assert pool.audit[0]["semantic_signature_hash"] == pool.audit[-1]["semantic_signature_hash"]


@pytest.mark.asyncio
async def test_supervisor_digest_pending_attempt_suppresses_after_restart():
    pool = _SupervisorDigestPool()
    store = SupervisorSlackDigestDecisionStore(pool=pool, feature_id="feat-1")
    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={"control_plane_snapshot_version": "cp-pending"},
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:920"],
    )
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
    )

    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is packet
    assert pool.state[0]["status"] == "pending"

    restarted = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
    )
    assert await restarted._digest_packet_to_send_durable(packet, channel="CSUP") is None

    assert pool.state[0]["status"] == "suppressed"
    assert pool.state[0]["suppress_reason"] == "pending_duplicate"
    assert [record["decision"] for record in pool.audit] == ["attempt", "suppress"]
    assert pool.audit[-1]["reason"] == "pending_duplicate"


@pytest.mark.asyncio
async def test_supervisor_digest_atomic_claim_loss_suppresses_send():
    class _RaceStore:
        def __init__(self) -> None:
            self.suppressed_reason = ""

        async def delivered_duplicate_exists(self, **kwargs):
            del kwargs
            return False

        async def pending_duplicate_exists(self, **kwargs):
            del kwargs
            return False

        async def record_attempt(self, **kwargs):
            del kwargs
            return False

        async def record_suppressed(self, **kwargs):
            self.suppressed_reason = kwargs["reason"]

    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={"control_plane_snapshot_version": "cp-race"},
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:922"],
    )
    store = _RaceStore()
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
    )

    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is None
    assert store.suppressed_reason == "pending_claim_lost"


@pytest.mark.asyncio
async def test_supervisor_digest_failed_delivery_does_not_poison_durable_dedupe():
    pool = _SupervisorDigestPool()
    store = SupervisorSlackDigestDecisionStore(pool=pool, feature_id="feat-1")
    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={"control_plane_snapshot_version": "cp-43"},
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:919"],
    )
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
    )

    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is packet
    await service._record_digest_failed(
        packet,
        channel="CSUP",
        error=RuntimeError("slack down"),
    )
    assert service._last_digest_signature is None
    assert service._last_digest_semantic_signature is None
    assert service._last_digest_at == 0.0

    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is packet
    await service._record_digest_failed(
        packet,
        channel="CSUP",
        error=RuntimeError("slack still down"),
    )

    restarted = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
    )
    assert await restarted._digest_packet_to_send_durable(packet, channel="CSUP") is packet

    assert [record["decision"] for record in pool.audit] == [
        "attempt",
        "failed",
        "attempt",
        "failed",
        "attempt",
    ]
    assert pool.state[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_supervisor_digest_failed_delivery_keeps_pending_claim_retryable_when_record_failed_fails():
    class _FlakyFailedStore:
        def __init__(self, delegate) -> None:
            self._delegate = delegate
            self.failures_remaining = 1

        async def delivered_duplicate_exists(self, **kwargs):
            return await self._delegate.delivered_duplicate_exists(**kwargs)

        async def pending_duplicate_exists(self, **kwargs):
            return await self._delegate.pending_duplicate_exists(**kwargs)

        async def record_attempt(self, **kwargs):
            return await self._delegate.record_attempt(**kwargs)

        async def record_failed(self, **kwargs):
            if self.failures_remaining:
                self.failures_remaining -= 1
                raise RuntimeError("database unavailable")
            return await self._delegate.record_failed(**kwargs)

    pool = _SupervisorDigestPool()
    durable_store = SupervisorSlackDigestDecisionStore(pool=pool, feature_id="feat-1")
    store = _FlakyFailedStore(durable_store)
    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={"control_plane_snapshot_version": "cp-44"},
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:920"],
    )
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
    )

    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is packet
    await service._record_digest_failed(
        packet,
        channel="CSUP",
        error=RuntimeError("slack down"),
    )
    assert service._pending_digest_delivery is not None
    assert pool.state[0]["status"] == "pending"
    assert service._last_digest_signature is None
    assert service._last_digest_semantic_signature is None

    next_packet = packet.model_copy(deep=True)
    assert await service._digest_packet_to_send_durable(next_packet, channel="CSUP") is next_packet
    assert service._pending_digest_delivery is not None
    assert pool.state[0]["status"] == "pending"

    await service._record_digest_failed(
        next_packet,
        channel="CSUP",
        error=RuntimeError("slack still down"),
    )
    assert service._pending_digest_delivery is None
    assert pool.state[0]["status"] == "failed"
    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is packet


@pytest.mark.asyncio
async def test_supervisor_digest_compose_failure_marks_pending_claim_failed():
    class _FailingDigestAgent:
        async def compose_message(self, *args, **kwargs):
            raise RuntimeError("compose failed")

    pool = _SupervisorDigestPool()
    store = SupervisorSlackDigestDecisionStore(pool=pool, feature_id="feat-1")
    # Slice 10d-2: production wires BOTH the legacy claim store and the new
    # typed dedupe store off the same pool. A first-seen digest decides
    # `should_send=True`, so the compose path is reached and its failure still
    # marks the legacy pending claim `failed` (the pre-10d-2 behavior).
    dedupe_pool = _DigestDedupePool()
    dedupe_store = SupervisorDigestDedupeStore(pool=dedupe_pool, feature_id="feat-1")
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent=_FailingDigestAgent(),  # type: ignore[arg-type]
        agent_runtime=None,
        poll_interval_seconds=60.0,
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
        digest_dedupe_store=dedupe_store,
    )
    adapter = _FakeAdapter()

    task = asyncio.create_task(service.watch_and_digest(adapter, "CSUP"))
    try:
        async def _failed_claim_recorded() -> None:
            for _ in range(100):
                if pool.state and pool.state[0]["status"] == "failed":
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("digest compose failure did not mark pending claim failed")

        await asyncio.wait_for(_failed_claim_recorded(), timeout=2.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert adapter.block_posts == []
    assert pool.state[0]["status"] == "failed"
    assert [record["decision"] for record in pool.audit] == ["attempt", "failed"]
    assert service._last_digest_signature is None
    assert service._pending_digest_delivery is None
    # The dedupe decision was `decide()`d (first-seen) but the send failed, so
    # NO `record_sent` audit row exists — `decide()` is pure and writes nothing.
    assert dedupe_pool.audit == []


@pytest.mark.asyncio
async def test_supervisor_digest_stale_pending_claim_is_reclaimed_after_restart():
    pool = _SupervisorDigestPool()
    store = SupervisorSlackDigestDecisionStore(pool=pool, feature_id="feat-1")
    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={"control_plane_snapshot_version": "cp-stale-pending"},
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:921"],
    )
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
    )

    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is packet
    assert pool.state[0]["status"] == "pending"
    pool.state[0]["stale_pending"] = True

    restarted = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
    )

    assert await restarted._digest_packet_to_send_durable(packet, channel="CSUP") is packet
    assert restarted._pending_digest_delivery is not None
    assert pool.state[0]["status"] == "pending"
    assert pool.state[0]["stale_pending"] is False
    assert [record["decision"] for record in pool.audit] == ["attempt", "attempt"]


@pytest.mark.asyncio
async def test_supervisor_digest_ignored_stale_codex_does_not_leave_pending_state():
    pool = _SupervisorDigestPool()
    store = SupervisorSlackDigestDecisionStore(pool=pool, feature_id="feat-1")
    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=43,
        retry=0,
        phase="implementation",
        classification=FailureClass.STALE_CODEX_INVOCATION,
        confidence=0.91,
        facts={
            "control_plane_snapshot_version": "cp-stale-ignored",
            "stale_codex_invocation": {"evidence_token": "stale-token-1"},
        },
        inference="A stale Codex invocation is no longer making progress.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["dashboard:/api/bridge/status"],
    )
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
    )

    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is packet
    assert pool.state[0]["status"] == "pending"

    await service._record_digest_suppressed(
        packet,
        channel="CSUP",
        reason="stale_codex_ignored",
    )

    assert service._pending_digest_delivery is None
    assert pool.state[0]["status"] == "suppressed"
    assert pool.state[0]["suppress_reason"] == "stale_codex_ignored"
    assert [record["decision"] for record in pool.audit] == ["attempt", "suppress"]
    assert not await store.record_attempt(
        dedupe_key="restart-different-key",
        snapshot_version="cp-stale-ignored-restart",
        signature_hash=str(pool.state[0]["signature_hash"]),
        semantic_signature_hash=str(pool.state[0]["semantic_signature_hash"]),
        reason="restart",
        packet=packet,
        channel="CSUP",
    )


@pytest.mark.asyncio
async def test_supervisor_digest_record_attempt_does_not_rearm_delivered_row():
    pool = _SupervisorDigestPool()
    store = SupervisorSlackDigestDecisionStore(pool=pool, feature_id="feat-1")
    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={"control_plane_snapshot_version": "cp-delivered"},
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:921"],
    )
    assert await store.record_attempt(
        dedupe_key="same-key",
        snapshot_version="cp-delivered",
        signature_hash="sig-1",
        semantic_signature_hash="sem-1",
        reason="first",
        packet=packet,
        channel="CSUP",
    )
    await store.record_delivered(
        dedupe_key="same-key",
        snapshot_version="cp-delivered",
        signature_hash="sig-1",
        semantic_signature_hash="sem-1",
        message_ts="2.345",
        packet=packet,
        channel="CSUP",
    )

    assert not await store.record_attempt(
        dedupe_key="same-key",
        snapshot_version="cp-new",
        signature_hash="sig-2",
        semantic_signature_hash="sem-2",
        reason="retry",
        packet=packet,
        channel="CSUP",
    )

    assert pool.state[0]["status"] == "delivered"
    assert pool.state[0]["snapshot_version"] == "cp-delivered"
    assert pool.state[0]["message_ts"] == "2.345"


@pytest.mark.asyncio
async def test_supervisor_digest_record_attempt_claims_pending_key_once():
    pool = _SupervisorDigestPool()
    store = SupervisorSlackDigestDecisionStore(pool=pool, feature_id="feat-1")
    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={"control_plane_snapshot_version": "cp-pending-key"},
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:923"],
    )

    assert await store.record_attempt(
        dedupe_key="pending-key",
        snapshot_version="cp-pending-key",
        signature_hash="sig-pending",
        semantic_signature_hash="sem-pending",
        reason="first",
        packet=packet,
        channel="CSUP",
    )
    assert not await store.record_attempt(
        dedupe_key="pending-key",
        snapshot_version="cp-pending-key-2",
        signature_hash="sig-pending-2",
        semantic_signature_hash="sem-pending-2",
        reason="second",
        packet=packet,
        channel="CSUP",
    )

    assert len(pool.state) == 1
    assert pool.state[0]["status"] == "pending"
    assert [record["decision"] for record in pool.audit] == ["attempt"]


def test_supervisor_digest_schema_uses_payload_jsonb_semantic_dedupe() -> None:
    schema = Path("schema.sql").read_text(encoding="utf-8")
    state_table = schema.split(
        "CREATE TABLE IF NOT EXISTS supervisor_slack_digest_state",
        1,
    )[1].split(");", 1)[0]

    assert "semantic_dedupe" not in state_table
    assert "supervisor_slack_digest_state_active_semantic" in schema
    assert "COALESCE((payload->>'semantic_dedupe')::boolean, FALSE)" in schema
    assert "status IN ('pending', 'delivered', 'suppressed')" in schema


@pytest.mark.asyncio
async def test_supervisor_digest_semantic_pending_claim_blocks_snapshot_variants():
    pool = _SupervisorDigestPool()
    store = SupervisorSlackDigestDecisionStore(pool=pool, feature_id="feat-1")
    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={"control_plane_snapshot_version": "cp-semantic-a"},
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:924"],
    )

    assert await store.record_attempt(
        dedupe_key="semantic-key-a",
        snapshot_version="cp-semantic-a",
        signature_hash="sig-semantic-a",
        semantic_signature_hash="sem-shared",
        semantic_dedupe=True,
        reason="first",
        packet=packet,
        channel="CSUP",
    )
    assert await store.pending_duplicate_exists(
        dedupe_key="semantic-key-b",
        signature_hash="sig-semantic-b",
        semantic_signature_hash="sem-shared",
        semantic_dedupe=True,
    )
    assert not await store.record_attempt(
        dedupe_key="semantic-key-b",
        snapshot_version="cp-semantic-b",
        signature_hash="sig-semantic-b",
        semantic_signature_hash="sem-shared",
        semantic_dedupe=True,
        reason="second",
        packet=packet,
        channel="CSUP",
    )

    assert len(pool.state) == 1
    assert pool.state[0]["dedupe_key"] == "semantic-key-a"
    assert [record["decision"] for record in pool.audit] == ["attempt"]


@pytest.mark.asyncio
async def test_supervisor_digest_delivered_semantic_match_requires_payload_opt_in():
    pool = _SupervisorDigestPool()
    store = SupervisorSlackDigestDecisionStore(pool=pool, feature_id="feat-1")
    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={"control_plane_snapshot_version": "cp-semantic-delivered"},
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:925"],
    )

    assert await store.record_attempt(
        dedupe_key="non-semantic-key",
        snapshot_version="cp-semantic-delivered",
        signature_hash="sig-non-semantic",
        semantic_signature_hash="sem-shared",
        semantic_dedupe=False,
        reason="first",
        packet=packet,
        channel="CSUP",
    )
    await store.record_delivered(
        dedupe_key="non-semantic-key",
        snapshot_version="cp-semantic-delivered",
        signature_hash="sig-non-semantic",
        semantic_signature_hash="sem-shared",
        message_ts="2.345",
        packet=packet,
        channel="CSUP",
    )

    assert not await store.delivered_duplicate_exists(
        dedupe_key="semantic-key-b",
        signature_hash="sig-semantic-b",
        semantic_signature_hash="sem-shared",
        semantic_dedupe=True,
    )

    assert await store.record_attempt(
        dedupe_key="semantic-key-b",
        snapshot_version="cp-semantic-delivered-b",
        signature_hash="sig-semantic-b",
        semantic_signature_hash="sem-shared",
        semantic_dedupe=True,
        reason="second",
        packet=packet,
        channel="CSUP",
    )
    await store.record_delivered(
        dedupe_key="semantic-key-b",
        snapshot_version="cp-semantic-delivered-b",
        signature_hash="sig-semantic-b",
        semantic_signature_hash="sem-shared",
        message_ts="2.346",
        packet=packet,
        channel="CSUP",
    )

    assert await store.delivered_duplicate_exists(
        dedupe_key="semantic-key-c",
        signature_hash="sig-semantic-c",
        semantic_signature_hash="sem-shared",
        semantic_dedupe=True,
    )


@pytest.mark.asyncio
async def test_supervisor_digest_record_attempt_unique_race_returns_claim_lost():
    class UniqueViolationError(Exception):
        sqlstate = "23505"

    class _UniqueRacePool:
        async def fetchrow(self, _sql: str, *_args):
            raise UniqueViolationError("duplicate key value violates unique constraint")

    store = SupervisorSlackDigestDecisionStore(pool=_UniqueRacePool(), feature_id="feat-1")
    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={"control_plane_snapshot_version": "cp-unique-race"},
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:926"],
    )

    assert not await store.record_attempt(
        dedupe_key="semantic-key-race",
        snapshot_version="cp-unique-race",
        signature_hash="sig-race",
        semantic_signature_hash="sem-race",
        semantic_dedupe=True,
        reason="race",
        packet=packet,
        channel="CSUP",
    )


@pytest.mark.asyncio
async def test_supervisor_digest_duplicate_stays_suppressed_when_audit_fails():
    class _DuplicateStore:
        async def delivered_duplicate_exists(self, **kwargs):
            del kwargs
            return True

        async def pending_duplicate_exists(self, **kwargs):
            raise AssertionError("pending check should not run after delivered duplicate")

        async def record_attempt(self, **kwargs):
            raise AssertionError("claim should not run after delivered duplicate")

        async def record_suppressed(self, **kwargs):
            del kwargs
            raise RuntimeError("audit unavailable")

    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={"control_plane_snapshot_version": "cp-audit-duplicate"},
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:927"],
    )
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_decision_store=_DuplicateStore(),
    )

    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is None


@pytest.mark.asyncio
async def test_supervisor_digest_claim_loss_stays_suppressed_when_audit_fails():
    class _ClaimLostStore:
        async def delivered_duplicate_exists(self, **kwargs):
            del kwargs
            return False

        async def pending_duplicate_exists(self, **kwargs):
            del kwargs
            return False

        async def record_attempt(self, **kwargs):
            del kwargs
            return False

        async def record_suppressed(self, **kwargs):
            del kwargs
            raise RuntimeError("audit unavailable")

    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={"control_plane_snapshot_version": "cp-audit-claim-lost"},
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:928"],
    )
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_decision_store=_ClaimLostStore(),
    )

    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is None


@pytest.mark.asyncio
async def test_supervisor_digest_suppresses_without_durable_decision_store():
    pool = _SupervisorDigestPool()
    store = SupervisorSlackDigestDecisionStore(pool=pool, feature_id="feat-1")
    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={"control_plane_snapshot_version": "cp-no-decision-store"},
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:931"],
    )
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
    )

    assert service._digest_decision_store is None
    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is None
    assert service._pending_digest_delivery is None
    assert service._last_digest_signature is None

    service._digest_decision_store = store
    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is packet
    assert pool.state[0]["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["duplicate_check", "claim"])
async def test_supervisor_digest_pre_claim_persistence_failure_suppresses_send(
    failure: str,
):
    failures = {failure}

    class _PreClaimFailingPool(_SupervisorDigestPool):
        async def fetch(self, sql: str, *args):
            if "duplicate_check" in failures:
                raise RuntimeError("duplicate check unavailable")
            return await super().fetch(sql, *args)

        async def fetchrow(self, sql: str, *args):
            if "claim" in failures:
                raise RuntimeError("claim unavailable")
            return await super().fetchrow(sql, *args)

    pool = _PreClaimFailingPool()
    store = SupervisorSlackDigestDecisionStore(pool=pool, feature_id="feat-1")
    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={"control_plane_snapshot_version": f"cp-pre-claim-{failure}"},
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:930"],
    )
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
    )

    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is None
    assert service._pending_digest_delivery is None
    assert service._last_digest_signature is None
    assert pool.state == []
    assert pool.audit == []

    failures.clear()
    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is packet
    assert service._pending_digest_delivery is not None
    assert pool.state[0]["status"] == "pending"
    assert [record["decision"] for record in pool.audit] == ["attempt"]


@pytest.mark.asyncio
async def test_supervisor_digest_attempt_audit_failure_keeps_delivery_tracked():
    class _AttemptAuditFailingPool(_SupervisorDigestPool):
        async def execute(self, sql: str, *args):
            normalized = " ".join(sql.lower().split())
            if "insert into supervisor_slack_digest_audit" in normalized:
                raise RuntimeError("audit unavailable")
            return await super().execute(sql, *args)

    pool = _AttemptAuditFailingPool()
    store = SupervisorSlackDigestDecisionStore(pool=pool, feature_id="feat-1")
    packet = EvidencePacket(
        feature_id="feat-1",
        group_idx=48,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.87,
        facts={"control_plane_snapshot_version": "cp-attempt-audit-fail"},
        inference="Verifier context materialization failed before product repair.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:929"],
    )
    service = SupervisorRuntimeService(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_decision_store=store,
    )

    assert await service._digest_packet_to_send_durable(packet, channel="CSUP") is packet
    delivery = service._pending_digest_delivery
    assert delivery is not None
    assert delivery["dedupe_key"] == pool.state[0]["dedupe_key"]
    assert pool.state[0]["status"] == "pending"

    await service._record_digest_failed(
        packet,
        channel="CSUP",
        error=RuntimeError("slack unavailable"),
    )

    assert service._pending_digest_delivery is None
    assert pool.state[0]["status"] == "failed"
    assert pool.state[0]["suppress_reason"].startswith("RuntimeError: slack unavailable")
    assert pool.audit == []


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
    assert app.artifact_store.list_summary_calls
    assert not app.artifact_store.list_records_calls
    assert any(
        key.startswith("supervisor-thread-context:feat-1:10.123:")
        for key, _value, _feature in app.artifact_store.writes
    )
    assert runtime.kwargs[0]["session_key"].split(":")[:-1] == runtime.kwargs[1][
        "session_key"
    ].split(":")[:-1]
    assert "question-thread-10.123" in runtime.kwargs[0]["session_key"]


@pytest.mark.asyncio
async def test_supervisor_router_resolves_stale_codex_card_actions():
    adapter = _FakeAdapter()
    router = SupervisorSlackRouter(
        adapter=adapter,
        channel="CSUP",
        service=_FakeService(),
        feature_id="feat-1",
    )

    await router.handle_action(
        {
            "channel": {"id": "CSUP"},
            "message": {"ts": "1.234"},
            "user": {"id": "U1"},
        },
        {"action_id": "stale_codex_ignore_tok123", "value": "tok123"},
    )

    assert adapter.updates[-1] == (
        "CSUP",
        "1.234",
        "stale-action:stale_codex_ignore_tok123:tok123",
    )
    assert adapter.block_updates[-1][2][0]["type"] == "header"


@pytest.mark.asyncio
async def test_supervisor_router_blocks_cross_channel_stale_codex_actions():
    adapter = _FakeAdapter()
    service = _FakeService()
    router = SupervisorSlackRouter(
        adapter=adapter,
        channel="CSUP",
        service=service,
        feature_id="feat-1",
    )

    await router.handle_action(
        {
            "channel": {"id": "COTHER"},
            "message": {"ts": "1.234"},
            "user": {"id": "U1"},
        },
        {"action_id": "stale_codex_kill_tok123", "value": "tok123"},
    )

    assert service.stale_actions == []
    assert adapter.updates[-1][0] == "COTHER"
    assert adapter.updates[-1][1] == "1.234"
    assert "configured supervisor channel" in adapter.updates[-1][2]


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


# ─────────────────────────────────────────────────────────────────────────────
# Slice 10d-2 — the SupervisorDigestDedupeStore routing through watch_and_digest.
#
# doc 10 ("Supervisor And Dashboard Integration") § "Slack Dedupe And
# Suppression" + § "Refactoring Steps" step 7 is the SPEC: EVERY background
# Slack digest is routed through `SupervisorDigestDedupeStore.decide()` before
# the Slack client is touched. These tests prove the routing, the send/record
# ordering, the never-suppress derivation, and the fail-open / fail-quiet split.
# ─────────────────────────────────────────────────────────────────────────────


class _OkDigestAgent:
    """A digest agent whose `compose_message` succeeds with a fixed message."""

    def __init__(self, message: str = "All good.") -> None:
        self._message = message
        self.compose_calls = 0

    async def compose_message(self, *args, **kwargs):
        self.compose_calls += 1
        sink = kwargs.get("assessment_sink")
        if sink is not None:
            with contextlib.suppress(Exception):
                await sink(SimpleNamespace(), [], False)
        return self._message


def _bg_packet(**overrides) -> EvidencePacket:
    """A baseline BACKGROUND digest packet (a deterministic-unblock recommend)."""

    base = dict(
        feature_id="feat-1",
        group_idx=12,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.88,
        facts={
            "control_plane_snapshot_version": "cp-1",
            "runtime_failure_events": [
                {
                    "failure_class": "stale_projection",
                    "failure_type": "verifier_context_stale",
                    "route": "retry_verifier",
                }
            ],
        },
        inference="Verifier context rebuild is executor-owned.",
        recommended_action=ActionLevel.RECOMMEND,
        citations=["event:control_plane_runtime_failure:evidence_node:940"],
    )
    base.update(overrides)
    return EvidencePacket(**base)


def _new_service(
    *,
    dedupe_pool: _DigestDedupePool | None = None,
    agent: object | None = None,
    legacy: bool = False,
) -> tuple[SupervisorRuntimeService, _DigestDedupePool]:
    """Build a `SupervisorRuntimeService` with a working in-memory dedupe store."""

    dedupe_pool = dedupe_pool if dedupe_pool is not None else _DigestDedupePool()
    dedupe_store = SupervisorDigestDedupeStore(pool=dedupe_pool, feature_id="feat-1")
    kwargs: dict[str, object] = dict(
        app=_FakeSupervisorApp(),
        feature_id="feat-1",
        agent_runtime=None,
        min_digest_interval_seconds=0.0,
        digest_dedupe_store=dedupe_store,
    )
    if agent is not None:
        kwargs["agent"] = agent
    if legacy:
        kwargs["digest_decision_store"] = SupervisorSlackDigestDecisionStore(
            pool=_SupervisorDigestPool(), feature_id="feat-1"
        )
    service = SupervisorRuntimeService(**kwargs)  # type: ignore[arg-type]
    return service, dedupe_pool


@pytest.mark.asyncio
async def test_watch_and_digest_routes_background_digest_through_decide(monkeypatch):
    """doc 10 step 7: a background digest ALWAYS goes through `decide()`.

    The dedupe store's `decide` is spied; the loop must call it before any
    Slack send, and the only `post_blocks` happens AFTER `decide()` said send.
    """

    agent = _OkDigestAgent()
    service, dedupe_pool = _new_service(agent=agent, legacy=True)

    decide_calls: list[dict] = []
    real_decide = service._digest_dedupe_store.decide

    async def _spy_decide(**kwargs):
        decide_calls.append(kwargs)
        return await real_decide(**kwargs)

    monkeypatch.setattr(service._digest_dedupe_store, "decide", _spy_decide)
    adapter = _FakeAdapter()

    task = asyncio.create_task(service.watch_and_digest(adapter, "CSUP"))
    try:
        async def _digest_sent() -> None:
            for _ in range(200):
                if adapter.block_posts:
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("background digest never sent")

        await asyncio.wait_for(_digest_sent(), timeout=2.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # `decide()` was called for the background digest BEFORE the Slack send.
    # (`_FakeSupervisorApp.run_once` emits no explicit snapshot version, so the
    # version is the cursor-derived fallback — the point is `decide()` ran.)
    assert decide_calls, "watch_and_digest never called decide()"
    assert decide_calls[0]["snapshot_version"], "decide() got an empty version"
    # The send happened (first-seen -> should_send=True) and recorded a SENT row.
    assert len(adapter.block_posts) == 1
    sent_rows = [r for r in dedupe_pool.audit if r["should_send"]]
    assert len(sent_rows) == 1
    assert sent_rows[0]["reason"] == "first_seen"
    # The dedupe state row was upserted with the real send.
    assert len(dedupe_pool.state) == 1


@pytest.mark.asyncio
async def test_route_background_digest_should_send_true_sends_then_records_sent():
    """should_send=True (first_seen) -> Slack send happens, THEN record_sent."""

    agent = _OkDigestAgent()
    service, dedupe_pool = _new_service(agent=agent, legacy=True)
    adapter = _FakeAdapter()
    packet = _bg_packet()

    await service._route_background_digest(packet, adapter=adapter, channel="CSUP")

    assert len(adapter.block_posts) == 1  # the digest was posted
    assert agent.compose_calls == 1
    # record_sent persisted exactly one should_send=true audit row + a state row.
    assert [r["should_send"] for r in dedupe_pool.audit] == [True]
    assert dedupe_pool.audit[0]["reason"] == "first_seen"
    state_row = next(iter(dedupe_pool.state.values()))
    assert state_row["last_sent_at"] is not None  # a real send stamped last_sent_at
    assert state_row["suppressed_count"] == 0


@pytest.mark.asyncio
async def test_route_background_digest_duplicate_within_cooldown_is_suppressed():
    """A 2nd identical background digest inside 30 min -> suppressed, no send.

    doc 10: "Suppress identical background digests for at least 30 minutes."
    This proves the end-to-end suppression: the 2nd call records a
    `record_suppressed` audit row and posts NOTHING to Slack.
    """

    agent = _OkDigestAgent()
    service, dedupe_pool = _new_service(agent=agent, legacy=True)
    adapter = _FakeAdapter()

    # First identical digest -> sent.
    await service._route_background_digest(_bg_packet(), adapter=adapter, channel="CSUP")
    assert len(adapter.block_posts) == 1

    # Second identical digest, SAME snapshot version -> background idempotency
    # suppression (same snapshot already SENT) -> no second Slack message.
    await service._route_background_digest(_bg_packet(), adapter=adapter, channel="CSUP")
    assert len(adapter.block_posts) == 1, "duplicate background digest was re-sent"

    reasons = [(r["should_send"], r["reason"]) for r in dedupe_pool.audit]
    assert reasons == [(True, "first_seen"), (False, "suppressed_duplicate")]


@pytest.mark.asyncio
async def test_route_background_digest_duplicate_new_snapshot_within_cooldown():
    """A duplicate dedupe key at a NEW snapshot version still suppresses < 30m.

    Same material state (same dedupe key), newer snapshot version -> not the
    background-idempotency arm, but the 30-min cooldown arm: suppressed and
    coalesced (doc 10 "Coalesce a suppressed count").
    """

    agent = _OkDigestAgent()
    service, dedupe_pool = _new_service(agent=agent, legacy=True)
    adapter = _FakeAdapter()

    await service._route_background_digest(
        _bg_packet(facts={"control_plane_snapshot_version": "cp-1",
                           "runtime_failure_events": [
                               {"failure_class": "stale_projection",
                                "failure_type": "verifier_context_stale",
                                "route": "retry_verifier"}]}),
        adapter=adapter, channel="CSUP",
    )
    assert len(adapter.block_posts) == 1

    # Same dedupe key (classification/action/route/signature unchanged), a
    # different snapshot version -> cooldown suppression.
    await service._route_background_digest(
        _bg_packet(facts={"control_plane_snapshot_version": "cp-2",
                          "runtime_failure_events": [
                              {"failure_class": "stale_projection",
                               "failure_type": "verifier_context_stale",
                               "route": "retry_verifier"}]}),
        adapter=adapter, channel="CSUP",
    )
    assert len(adapter.block_posts) == 1
    suppress_rows = [r for r in dedupe_pool.audit if not r["should_send"]]
    assert len(suppress_rows) == 1
    assert suppress_rows[0]["reason"] == "suppressed_within_cooldown"
    # The coalesced suppressed count was persisted on the state row.
    state_row = next(iter(dedupe_pool.state.values()))
    assert state_row["suppressed_count"] == 1


@pytest.mark.asyncio
async def test_route_background_digest_first_stop_escalate_new_signature_sends():
    """doc 10 never-suppress: a first `stop/escalate` for a NEW failure
    signature ALWAYS sends, even immediately after another digest.

    The escalation packet has a distinct failure signature -> a brand-new
    dedupe key with no prior state row -> `new_failure_signature=True` ->
    `decide()`'s never-suppress short-circuit fires.
    """

    agent = _OkDigestAgent()
    service, dedupe_pool = _new_service(agent=agent, legacy=True)
    adapter = _FakeAdapter()

    # A first, unrelated background digest is sent.
    await service._route_background_digest(_bg_packet(), adapter=adapter, channel="CSUP")
    assert len(adapter.block_posts) == 1

    # A DIFFERENT failure signature, a stop/escalate action -> never suppressed.
    escalate = _bg_packet(
        classification=FailureClass.PIPELINE_BUG_SUSPECTED,
        recommended_action=ActionLevel.STOP_ESCALATE,
        facts={
            "control_plane_snapshot_version": "cp-1",
            "runtime_failure_events": [
                {
                    "failure_class": "checkpoint_contradiction",
                    "failure_type": "interior_checkpoint",
                    "route": "quiesce",
                }
            ],
        },
        citations=["event:control_plane_runtime_failure:evidence_node:999"],
    )
    await service._route_background_digest(escalate, adapter=adapter, channel="CSUP")
    assert len(adapter.block_posts) == 2, "a first stop/escalate was suppressed"

    sent_rows = [r for r in dedupe_pool.audit if r["should_send"]]
    assert {r["reason"] for r in sent_rows} == {"first_seen", "material_change"}


@pytest.mark.asyncio
async def test_route_background_digest_first_operator_required_new_route_sends():
    """doc 10 never-suppress: a first `operator_required` for a NEW typed
    route ALWAYS sends."""

    agent = _OkDigestAgent()
    service, dedupe_pool = _new_service(agent=agent, legacy=True)
    adapter = _FakeAdapter()

    operator_required = _bg_packet(
        classification=FailureClass.OPERATOR_REQUIRED,
        recommended_action=ActionLevel.STOP_ESCALATE,
        facts={
            "control_plane_snapshot_version": "cp-op",
            "recommended_route": "operator_required",
            "runtime_failure_events": [
                {
                    "failure_class": "operator_required",
                    "failure_type": "writeability_denied",
                    "route": "operator_required",
                }
            ],
        },
    )
    decision = await service._route_background_digest(
        operator_required, adapter=adapter, channel="CSUP"
    )
    assert decision is None  # routing returns nothing; the assertion is the send
    assert len(adapter.block_posts) == 1
    sent_rows = [r for r in dedupe_pool.audit if r["should_send"]]
    assert len(sent_rows) == 1
    assert sent_rows[0]["reason"] == "material_change"


@pytest.mark.asyncio
async def test_repeated_stop_escalate_same_signature_is_suppressible():
    """A REPEAT `stop/escalate` for the SAME signature is NOT never-suppress.

    This is the negative arm — proving the never-suppress derivation does not
    over-fire and flood the operator: only the FIRST digest of a signature is
    forced; a repeat reuses the dedupe key, finds the prior row, and suppresses.
    """

    agent = _OkDigestAgent()
    service, dedupe_pool = _new_service(agent=agent, legacy=True)
    adapter = _FakeAdapter()

    escalate = _bg_packet(
        classification=FailureClass.PIPELINE_BUG_SUSPECTED,
        recommended_action=ActionLevel.STOP_ESCALATE,
        facts={
            "control_plane_snapshot_version": "cp-esc",
            "runtime_failure_events": [
                {
                    "failure_class": "merge_conflict",
                    "failure_type": "rebase_failed",
                    "route": "quiesce",
                }
            ],
        },
    )
    await service._route_background_digest(escalate, adapter=adapter, channel="CSUP")
    assert len(adapter.block_posts) == 1  # first one sent

    # The exact same escalation again -> same dedupe key, prior row exists ->
    # new_failure_signature is False -> normal suppression.
    await service._route_background_digest(escalate, adapter=adapter, channel="CSUP")
    assert len(adapter.block_posts) == 1, "a repeated same-signature escalation flooded"
    assert [r["should_send"] for r in dedupe_pool.audit] == [True, False]


@pytest.mark.asyncio
async def test_operator_answer_path_does_not_route_through_background_dedupe():
    """doc 10: "Direct operator Slack questions bypass suppression."

    `answer_question` (the direct operator-answer path) must NOT touch the
    background dedupe store at all — only `watch_and_digest` background digests
    route through `decide()`.
    """

    agent = _OkDigestAgent()
    service, dedupe_pool = _new_service(agent=agent, legacy=True)

    route = SupervisorSlackRoute(
        kind="supervisor_question",
        text="what is the current status?",
        channel="CSUP",
        user="U1",
    )
    answer = await service.answer_question(route)

    assert isinstance(answer, str) and answer
    # The background dedupe store was never written by the operator-answer path.
    assert dedupe_pool.audit == []
    assert dedupe_pool.state == {}


@pytest.mark.asyncio
async def test_route_background_digest_fail_quiet_on_store_outage():
    """doc 10 § "Edge Cases": a `decide()` store failure on a NON-escalation
    background digest fails QUIET — suppress the candidate, no Slack send."""

    agent = _OkDigestAgent()
    dedupe_pool = _DigestDedupePool()
    dedupe_pool.fail = True  # every dedupe table op now raises
    service, _ = _new_service(dedupe_pool=dedupe_pool, agent=agent, legacy=True)
    adapter = _FakeAdapter()

    # A plain deterministic-unblock recommend is NOT a never-suppress case.
    await service._route_background_digest(_bg_packet(), adapter=adapter, channel="CSUP")

    assert adapter.block_posts == [], "fail-quiet must not send on a store outage"
    assert agent.compose_calls == 0


@pytest.mark.asyncio
async def test_route_background_digest_fail_open_on_store_outage_for_escalation():
    """doc 10 § "Edge Cases": a `decide()` store failure on a first
    `stop/escalate` fails OPEN — the escalation is still sent."""

    agent = _OkDigestAgent()
    dedupe_pool = _DigestDedupePool()
    dedupe_pool.fail = True
    service, _ = _new_service(dedupe_pool=dedupe_pool, agent=agent, legacy=True)
    adapter = _FakeAdapter()

    escalate = _bg_packet(
        classification=FailureClass.PIPELINE_BUG_SUSPECTED,
        recommended_action=ActionLevel.STOP_ESCALATE,
        facts={
            "control_plane_snapshot_version": "cp-fatal",
            "runtime_failure_events": [
                {
                    "failure_class": "checkpoint_contradiction",
                    "failure_type": "interior_checkpoint",
                    "route": "quiesce",
                }
            ],
        },
    )
    await service._route_background_digest(escalate, adapter=adapter, channel="CSUP")

    assert len(adapter.block_posts) == 1, "fail-open must still send a first escalation"


@pytest.mark.asyncio
async def test_route_background_digest_stale_codex_ignored_token_suppressed():
    """An ignored stale-Codex card is suppressed before `decide()`/Slack.

    The stale-Codex ignore/missing-token gate runs first; an ignored card must
    not reach the dedupe `decide()` or the Slack client.
    """

    service, dedupe_pool = _new_service(legacy=True)
    adapter = _FakeAdapter()
    service._ignored_stale_codex_tokens.add("tok-ignored")
    stale = _bg_packet(
        classification=FailureClass.STALE_CODEX_INVOCATION,
        recommended_action=ActionLevel.RECOMMEND,
        facts={
            "control_plane_snapshot_version": "cp-stale",
            "stale_codex_invocation": {"evidence_token": "tok-ignored"},
        },
    )
    await service._route_background_digest(stale, adapter=adapter, channel="CSUP")

    assert adapter.block_posts == []
    # The ignored stale card never reached the dedupe decide()/record path.
    assert dedupe_pool.audit == []


@pytest.mark.asyncio
async def test_route_background_digest_stale_codex_card_sends_through_decide():
    """A live stale-Codex card IS a background digest -> routes through
    `decide()` and posts the card on `should_send=True`."""

    service, dedupe_pool = _new_service(legacy=True)
    adapter = _FakeAdapter()
    stale = _bg_packet(
        classification=FailureClass.STALE_CODEX_INVOCATION,
        recommended_action=ActionLevel.RECOMMEND,
        facts={
            "control_plane_snapshot_version": "cp-stale-live",
            "stale_codex_invocation": {
                "evidence_token": "tok-live",
                "actor": "codex-runner",
                "pid": 4242,
            },
        },
    )
    await service._route_background_digest(stale, adapter=adapter, channel="CSUP")

    assert len(adapter.block_posts) == 1  # the stale-Codex card was posted
    sent_rows = [r for r in dedupe_pool.audit if r["should_send"]]
    assert len(sent_rows) == 1 and sent_rows[0]["reason"] == "first_seen"


def test_build_digest_key_is_stable_across_evidence_id_churn():
    """`_build_digest_key` -> a dedupe key stable across evidence-id churn.

    doc 10: "evidence ids alone do not create a new Slack message." Two packets
    differing only in citation/evidence ids must yield the SAME dedupe key.
    """

    service, _ = _new_service()
    a = _bg_packet(citations=["event:x:evidence_node:1"])
    b = _bg_packet(citations=["event:x:evidence_node:99999"])
    key_a = service._build_digest_key(a)
    key_b = service._build_digest_key(b)
    assert compute_dedupe_key(key_a) == compute_dedupe_key(key_b)


def test_build_digest_key_changes_on_classification_change():
    """`_build_digest_key` -> a NEW dedupe key when the classification changes."""

    service, _ = _new_service()
    base = service._build_digest_key(_bg_packet())
    changed = service._build_digest_key(
        _bg_packet(classification=FailureClass.PIPELINE_BUG_SUSPECTED)
    )
    assert compute_dedupe_key(base) != compute_dedupe_key(changed)


def test_build_digest_key_folds_typed_route_and_merge_queue_and_attempts():
    """`_build_digest_key` folds the typed route / merge-queue / attempt ids.

    doc 10: a queue-status, route, or active-attempt change invents a new key.
    """

    service, _ = _new_service()
    typed_facts = {
        "control_plane_snapshot_version": "cp-typed",
        "recommended_route": "retry_verifier",
        "control_plane": {
            "merge_queue": {"items": [{"status": "leased"}]},
            "active_attempts": [{"attempt_id": 7}],
        },
    }
    base = service._build_digest_key(_bg_packet(facts=dict(typed_facts)))
    assert base.recommended_route == "retry_verifier"
    assert base.merge_queue_statuses == ["leased"]
    assert base.active_attempt_ids == [7]

    moved_queue = dict(typed_facts)
    moved_queue["control_plane"] = {
        "merge_queue": {"items": [{"status": "committing"}]},
        "active_attempts": [{"attempt_id": 7}],
    }
    changed = service._build_digest_key(_bg_packet(facts=moved_queue))
    assert compute_dedupe_key(base) != compute_dedupe_key(changed)


@pytest.mark.asyncio
async def test_derive_never_suppress_flags_first_seen_then_repeat():
    """`_derive_never_suppress_flags` -> first-seen True, then False on repeat.

    Proves the exact derivation: a new dedupe key (no prior state row) is
    `new_failure_signature` True; once a state row exists for that key the
    repeat derives False (so a repeat is suppressible).
    """

    agent = _OkDigestAgent()
    service, _ = _new_service(agent=agent, legacy=True)
    packet = _bg_packet()
    key = service._build_digest_key(packet)

    first = await service._derive_never_suppress_flags(packet, key=key)
    assert first["new_failure_signature"] is True
    assert first["new_operator_route"] is True
    assert first["is_operator_answer"] is False

    # Persist a SENT decision so a prior state row now exists for the key.
    adapter = _FakeAdapter()
    await service._route_background_digest(packet, adapter=adapter, channel="CSUP")

    repeat = await service._derive_never_suppress_flags(packet, key=key)
    assert repeat["new_failure_signature"] is False
    assert repeat["new_operator_route"] is False


@pytest.mark.asyncio
async def test_derive_never_suppress_flags_fail_open_on_store_outage():
    """A prior-state read failure -> flags degrade to first-seen (fail-open)."""

    dedupe_pool = _DigestDedupePool()
    dedupe_pool.fail = True
    service, _ = _new_service(dedupe_pool=dedupe_pool)
    packet = _bg_packet()
    flags = await service._derive_never_suppress_flags(
        packet, key=service._build_digest_key(packet)
    )
    assert flags["new_failure_signature"] is True
    assert flags["new_operator_route"] is True
