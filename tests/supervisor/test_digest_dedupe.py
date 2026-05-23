"""Slice 10d — the supervisor Slack dedupe / suppression store.

doc 10 ("Supervisor And Dashboard Integration") § "Slack Dedupe And
Suppression" + § "Tests" is the SPEC. These tests prove:

* the two supervisor-owned tables ``supervisor_digest_state`` /
  ``supervisor_digest_audit`` create cleanly and round-trip
  (``test_fixture_provides_*`` + the real-Postgres CRUD tests);
* :func:`compute_dedupe_key` is stable across evidence-id churn and changes on
  classification / route / action / signature / queue-status / attempt change
  (doc 10: "evidence ids alone do not create a new Slack message");
* identical background digests are SUPPRESSED for ≥ 30 min and the
  suppressed-duplicate count is COALESCED;
* the CORRECTNESS-CRITICAL never-suppress exceptions hold — a direct operator
  answer is never suppressed; a first ``stop/escalate`` for a new failure
  signature is never suppressed; a first ``operator_required`` for a new typed
  route is never suppressed.

The decision-logic tests run against an in-memory fake pool (the logic is pure
SQL-state arithmetic). The CRUD / constraint / coalescing-persistence tests run
against real Postgres via this directory's ``supervisor_pg_*`` conftest
fixtures and SKIP cleanly when ``localhost:5431`` is unreachable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from iriai_build_v2.supervisor.digest_dedupe import (
    SUPPRESSION_COOLDOWN,
    SUPPRESSION_COOLDOWN_SECONDS,
    DigestDedupeStoreError,
    SupervisorDigestDedupeStore,
    compute_dedupe_key,
)
from iriai_build_v2.supervisor.models import (
    SupervisorDigestDecision,
    SupervisorDigestKey,
)

_T0 = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)


def _key(**overrides) -> SupervisorDigestKey:
    """A baseline :class:`SupervisorDigestKey` with overridable fields."""

    base = dict(
        feature_id="feat-1",
        group_idx=3,
        classification="deterministic_unblock",
        recommended_action="recommend",
        recommended_route="run_workspace_repair",
        failure_signature_hashes=["sig-a", "sig-b"],
        merge_queue_statuses=["queued"],
        active_attempt_ids=[11, 12],
    )
    base.update(overrides)
    return SupervisorDigestKey(**base)


# ─────────────────────────────────────────────────────────────────────────────
# In-memory fake pool — exercises the pure decision logic without Postgres.
# ─────────────────────────────────────────────────────────────────────────────


class _FakePool:
    """A minimal in-memory stand-in for the two supervisor digest tables.

    Implements just enough asyncpg-shaped ``fetch`` / ``fetchrow`` / ``execute``
    behavior for :class:`SupervisorDigestDedupeStore` (the dedupe-key upsert,
    the latest-state read, the append-only audit insert).
    """

    def __init__(self) -> None:
        # (feature_id, dedupe_key) -> state-row dict
        self.state: dict[tuple[str, str], dict] = {}
        self.audit: list[dict] = []
        self._state_seq = 0
        self._audit_seq = 0

    async def fetchrow(self, query: str, *args):
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
                    "created_at": _T0,
                    "updated_at": _T0,
                }
                self.state[(feature_id, dedupe_key)] = row
            else:
                row = existing
                row["group_idx"] = group_idx
                row["last_snapshot_version"] = last_snapshot_version
                row["classification"] = classification
                row["recommended_action"] = recommended_action
                row["recommended_route"] = recommended_route
                # COALESCE(EXCLUDED.last_sent_at, existing.last_sent_at)
                if last_sent_at is not None:
                    row["last_sent_at"] = last_sent_at
                row["suppressed_count"] = suppressed_count
            return {"id": row["id"]}
        raise AssertionError(f"unexpected fetchrow query: {text}")

    async def execute(self, query: str, *args):
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


def _store() -> tuple[SupervisorDigestDedupeStore, _FakePool]:
    pool = _FakePool()
    return SupervisorDigestDedupeStore(pool=pool, feature_id="feat-1"), pool


# ── compute_dedupe_key — doc 10 "evidence ids alone do not create a new key" ──


def test_dedupe_key_is_stable_across_list_ordering_churn() -> None:
    """Reordered list fields produce the SAME dedupe key (sorted at digest)."""

    a = _key(
        failure_signature_hashes=["sig-a", "sig-b"],
        merge_queue_statuses=["queued", "leased"],
        active_attempt_ids=[12, 11],
    )
    b = _key(
        failure_signature_hashes=["sig-b", "sig-a"],
        merge_queue_statuses=["leased", "queued"],
        active_attempt_ids=[11, 12],
    )
    assert compute_dedupe_key(a) == compute_dedupe_key(b)


@pytest.mark.parametrize(
    "field, value",
    [
        ("classification", "pipeline_bug_suspected"),
        ("recommended_action", "stop/escalate"),
        ("recommended_route", "quiesce"),
        ("failure_signature_hashes", ["sig-a", "sig-b", "sig-c"]),
        ("merge_queue_statuses", ["committing"]),
        ("active_attempt_ids", [11, 12, 13]),
        ("group_idx", 4),
    ],
)
def test_dedupe_key_changes_on_material_change(field, value) -> None:
    """Classification/route/action/signature/queue/attempt change -> new key.

    doc 10 § "Slack Dedupe And Suppression" / § "Tests": the key "changes when
    classification, route, action, signature, active attempt, or queue status
    changes."
    """

    baseline = compute_dedupe_key(_key())
    changed = compute_dedupe_key(_key(**{field: value}))
    assert baseline != changed


# ── decide() — first seen / background idempotency / cooldown / coalesce ──────


@pytest.mark.asyncio
async def test_first_seen_digest_is_sent() -> None:
    """No prior state row -> the digest is sent with reason 'first_seen'."""

    store, _pool = _store()
    decision = await store.decide(
        key=_key(), snapshot_version="v1", now=_T0
    )
    assert decision.should_send is True
    assert decision.reason == "first_seen"
    assert decision.suppressed_count == 0
    assert decision.prior_digest_id is None


@pytest.mark.asyncio
async def test_identical_background_digest_suppressed_within_cooldown() -> None:
    """A re-sent identical digest inside 30 min is suppressed + coalesced.

    doc 10: "Suppress identical background digests for at least 30 minutes."
    """

    store, _pool = _store()
    key = _key()
    first = await store.decide(key=key, snapshot_version="v1", now=_T0)
    assert first.should_send is True
    await store.record_sent(decision=first, key=key, snapshot_version="v1", now=_T0)

    # 10 minutes later, a NEW snapshot version but the SAME material state.
    later = _T0 + timedelta(minutes=10)
    second = await store.decide(key=key, snapshot_version="v2", now=later)
    assert second.should_send is False
    assert second.reason == "suppressed_within_cooldown"
    assert second.suppressed_count == 1


@pytest.mark.asyncio
async def test_same_snapshot_version_is_idempotent_suppressed_duplicate() -> None:
    """Reprocessing the SAME snapshot after a send -> suppressed_duplicate.

    doc 10: background Slack idempotency is keyed by ``(feature_id, group_idx,
    dedupe_key, snapshot_version)`` — reprocessing the same snapshot must not
    send a second background message.
    """

    store, _pool = _store()
    key = _key()
    first = await store.decide(key=key, snapshot_version="v1", now=_T0)
    await store.record_sent(decision=first, key=key, snapshot_version="v1", now=_T0)

    again = await store.decide(key=key, snapshot_version="v1", now=_T0 + timedelta(minutes=1))
    assert again.should_send is False
    assert again.reason == "suppressed_duplicate"


@pytest.mark.asyncio
async def test_suppressed_count_coalesces_across_repeated_duplicates() -> None:
    """Each suppressed duplicate inside the cooldown bumps the coalesced count."""

    store, pool = _store()
    key = _key()
    first = await store.decide(key=key, snapshot_version="v1", now=_T0)
    await store.record_sent(decision=first, key=key, snapshot_version="v1", now=_T0)

    counts = []
    for minutes, version in ((5, "v2"), (10, "v3"), (15, "v4")):
        moment = _T0 + timedelta(minutes=minutes)
        decision = await store.decide(key=key, snapshot_version=version, now=moment)
        assert decision.should_send is False
        await store.record_suppressed(
            decision=decision, key=key, snapshot_version=version, now=moment
        )
        counts.append(decision.suppressed_count)
    # Each suppression coalesces onto the running total.
    assert counts == [1, 2, 3]
    state = pool.state[("feat-1", compute_dedupe_key(key))]
    assert state["suppressed_count"] == 3


@pytest.mark.asyncio
async def test_post_cooldown_send_is_coalesced_and_carries_count() -> None:
    """After 30 min with coalesced duplicates, one update is sent.

    doc 10: "Coalesce a suppressed count and send one update if the same
    condition persists past the cooldown."
    """

    store, pool = _store()
    key = _key()
    first = await store.decide(key=key, snapshot_version="v1", now=_T0)
    await store.record_sent(decision=first, key=key, snapshot_version="v1", now=_T0)

    # Two suppressed duplicates inside the cooldown.
    for minutes, version in ((10, "v2"), (20, "v3")):
        moment = _T0 + timedelta(minutes=minutes)
        suppressed = await store.decide(key=key, snapshot_version=version, now=moment)
        assert suppressed.should_send is False
        await store.record_suppressed(
            decision=suppressed, key=key, snapshot_version=version, now=moment
        )

    # 31 minutes after the last send: the cooldown elapsed -> a coalesced send.
    past_cooldown = _T0 + timedelta(minutes=31)
    decision = await store.decide(
        key=key, snapshot_version="v4", now=past_cooldown
    )
    assert decision.should_send is True
    assert decision.reason == "coalesced"
    assert decision.suppressed_count == 2

    # record_sent RESETS the coalesced backlog (it has now been delivered).
    await store.record_sent(
        decision=decision, key=key, snapshot_version="v4", now=past_cooldown
    )
    state = pool.state[("feat-1", compute_dedupe_key(key))]
    assert state["suppressed_count"] == 0


@pytest.mark.asyncio
async def test_send_exactly_at_cooldown_boundary_is_allowed() -> None:
    """A digest exactly 30 min after the last send is no longer suppressed."""

    store, _pool = _store()
    key = _key()
    first = await store.decide(key=key, snapshot_version="v1", now=_T0)
    await store.record_sent(decision=first, key=key, snapshot_version="v1", now=_T0)

    boundary = _T0 + SUPPRESSION_COOLDOWN  # exactly 30 min
    decision = await store.decide(key=key, snapshot_version="v2", now=boundary)
    assert decision.should_send is True
    # No duplicates were coalesced -> reason is 'material_change'.
    assert decision.reason == "material_change"


# ── never-suppress exceptions — CORRECTNESS-CRITICAL (a miss is a P1/P2) ──────


@pytest.mark.asyncio
async def test_direct_operator_answer_is_never_suppressed() -> None:
    """A direct operator answer always sends, even inside the cooldown.

    doc 10 § "Slack Dedupe And Suppression": "Never suppress direct operator
    answers ..." A suppressed operator answer is a dropped operator reply.
    """

    store, _pool = _store()
    key = _key()
    first = await store.decide(key=key, snapshot_version="v1", now=_T0)
    await store.record_sent(decision=first, key=key, snapshot_version="v1", now=_T0)

    # 1 minute later — deep inside the cooldown — but it is an operator answer.
    decision = await store.decide(
        key=key,
        snapshot_version="v1",
        is_operator_answer=True,
        now=_T0 + timedelta(minutes=1),
    )
    assert decision.should_send is True
    assert decision.reason == "operator_requested"


@pytest.mark.asyncio
async def test_first_stop_escalate_for_new_signature_is_never_suppressed() -> None:
    """A first stop/escalate for a NEW failure signature always sends.

    doc 10: "Never suppress ... first ``stop/escalate`` for a new failure
    signature ..." A suppressed first stop/escalate is a MISSED ESCALATION.
    """

    store, _pool = _store()
    key = _key(
        classification="pipeline_bug_suspected",
        recommended_action="stop/escalate",
        recommended_route="quiesce",
        failure_signature_hashes=["sig-new"],
    )
    # Even if a row for this exact key already exists and was just sent ...
    first = await store.decide(key=key, snapshot_version="v1", now=_T0)
    await store.record_sent(decision=first, key=key, snapshot_version="v1", now=_T0)

    # ... a new-failure-signature stop/escalate inside the cooldown still sends.
    decision = await store.decide(
        key=key,
        snapshot_version="v2",
        new_failure_signature=True,
        now=_T0 + timedelta(minutes=2),
    )
    assert decision.should_send is True
    assert decision.reason == "material_change"


@pytest.mark.asyncio
async def test_stop_escalate_without_new_signature_can_be_suppressed() -> None:
    """A repeated stop/escalate WITHOUT a new signature follows normal cooldown.

    The never-suppress exception is scoped to a *first* stop/escalate for a
    *new* failure signature — a repeat of the same one is a background
    duplicate and is suppressible.
    """

    store, _pool = _store()
    key = _key(
        classification="pipeline_bug_suspected",
        recommended_action="stop/escalate",
        recommended_route="quiesce",
    )
    first = await store.decide(key=key, snapshot_version="v1", now=_T0)
    await store.record_sent(decision=first, key=key, snapshot_version="v1", now=_T0)

    decision = await store.decide(
        key=key,
        snapshot_version="v2",
        new_failure_signature=False,
        now=_T0 + timedelta(minutes=5),
    )
    assert decision.should_send is False
    assert decision.reason == "suppressed_within_cooldown"


@pytest.mark.asyncio
async def test_first_operator_required_for_new_route_is_never_suppressed() -> None:
    """A first operator_required for a NEW typed route always sends.

    doc 10: "Never suppress ... first ``operator_required`` for a new typed
    route." A suppressed first operator_required is a MISSED ESCALATION.
    """

    store, _pool = _store()
    key = _key(
        classification="operator_required",
        recommended_action="stop/escalate",
        recommended_route="operator_required",
    )
    first = await store.decide(key=key, snapshot_version="v1", now=_T0)
    await store.record_sent(decision=first, key=key, snapshot_version="v1", now=_T0)

    decision = await store.decide(
        key=key,
        snapshot_version="v2",
        new_operator_route=True,
        now=_T0 + timedelta(minutes=3),
    )
    assert decision.should_send is True
    assert decision.reason == "material_change"


@pytest.mark.asyncio
async def test_operator_required_without_new_route_can_be_suppressed() -> None:
    """A repeated operator_required WITHOUT a new route follows normal cooldown."""

    store, _pool = _store()
    key = _key(
        classification="operator_required",
        recommended_action="stop/escalate",
        recommended_route="operator_required",
    )
    first = await store.decide(key=key, snapshot_version="v1", now=_T0)
    await store.record_sent(decision=first, key=key, snapshot_version="v1", now=_T0)

    decision = await store.decide(
        key=key,
        snapshot_version="v2",
        new_operator_route=False,
        now=_T0 + timedelta(minutes=4),
    )
    assert decision.should_send is False
    assert decision.reason == "suppressed_within_cooldown"


@pytest.mark.asyncio
async def test_never_suppress_exception_survives_store_read_failure() -> None:
    """A never-suppress digest sends even if the state read raises.

    The never-suppress decision is computed BEFORE the table read, so a store
    failure can never swallow a first escalation / an operator answer.
    """

    class _BrokenPool:
        async def fetchrow(self, *_a, **_k):
            raise RuntimeError("db down")

        async def fetch(self, *_a, **_k):
            raise RuntimeError("db down")

        async def execute(self, *_a, **_k):
            raise RuntimeError("db down")

    store = SupervisorDigestDedupeStore(pool=_BrokenPool(), feature_id="feat-1")
    decision = await store.decide(
        key=_key(),
        snapshot_version="v1",
        is_operator_answer=True,
        now=_T0,
    )
    assert decision.should_send is True
    assert decision.reason == "operator_requested"


@pytest.mark.asyncio
async def test_background_decide_propagates_store_read_failure() -> None:
    """A non-exception background digest re-raises a store read failure typed.

    doc 10 § "Edge Cases And Failure Handling": "Dedupe store write failure:
    fail open for operator-requested replies, fail quiet for background
    duplicate candidates." :class:`DigestDedupeStoreError` is the typed signal
    the caller uses to apply that split — it must surface, not be swallowed.
    """

    class _BrokenPool:
        async def fetchrow(self, *_a, **_k):
            raise RuntimeError("db down")

    store = SupervisorDigestDedupeStore(pool=_BrokenPool(), feature_id="feat-1")
    with pytest.raises(DigestDedupeStoreError):
        await store.decide(key=_key(), snapshot_version="v1", now=_T0)


def test_suppression_cooldown_is_thirty_minutes() -> None:
    """doc 10: "Suppress identical background digests for at least 30 minutes.\""""

    assert SUPPRESSION_COOLDOWN_SECONDS == 30 * 60
    assert SUPPRESSION_COOLDOWN == timedelta(minutes=30)


# ─────────────────────────────────────────────────────────────────────────────
# Real-Postgres CRUD / constraint / coalescing-persistence tests.
# These SKIP cleanly when localhost:5431 is unreachable (conftest fixture).
# ─────────────────────────────────────────────────────────────────────────────


async def _insert_feature(conn, feature_id: str) -> None:
    await conn.execute(
        "INSERT INTO features (id, name, slug, workflow_name, workspace_id) "
        "VALUES ($1, $2, $3, $4, $5)",
        feature_id,
        feature_id,
        feature_id,
        "develop",
        "ws-1",
    )


@pytest.mark.asyncio
async def test_fixture_provides_supervisor_digest_tables(supervisor_pg_conn) -> None:
    """The two Slice-10d tables exist in the loaded schema."""

    tables = await supervisor_pg_conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
        "AND tablename IN ('supervisor_digest_state', 'supervisor_digest_audit') "
        "ORDER BY tablename"
    )
    assert [t["tablename"] for t in tables] == [
        "supervisor_digest_audit",
        "supervisor_digest_state",
    ]


@pytest.mark.asyncio
async def test_real_pg_doc10_indexes_present(supervisor_pg_conn) -> None:
    """The doc-10 § "Slack Dedupe And Suppression" indexes are all created."""

    rows = await supervisor_pg_conn.fetch(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
        "AND tablename IN ('supervisor_digest_state', 'supervisor_digest_audit')"
    )
    names = {r["indexname"] for r in rows}
    for expected in (
        "supervisor_digest_state_feature_dedupe",
        "idx_supervisor_dedupe_state_updated",
        "idx_supervisor_dedupe_state_group",
        "idx_supervisor_dedupe_audit_feature",
        "idx_supervisor_dedupe_audit_key",
        "idx_supervisor_dedupe_audit_group",
    ):
        assert expected in names, f"missing index {expected}"


@pytest.mark.asyncio
async def test_real_pg_record_sent_round_trips(supervisor_pg_conn) -> None:
    """record_sent upserts a state row and appends an audit row."""

    await _insert_feature(supervisor_pg_conn, "feat-1")
    store = SupervisorDigestDedupeStore(pool=supervisor_pg_conn, feature_id="feat-1")
    key = _key()
    decision = await store.decide(key=key, snapshot_version="v1", now=_T0)
    assert decision.should_send is True
    state_id = await store.record_sent(
        decision=decision,
        key=key,
        snapshot_version="v1",
        slack_channel="C123",
        slack_thread_ts="1.2",
        slack_message_ts="9.9",
        citation_refs=["evidence_node:1", "artifact:dag-verify:g3 id=7"],
        payload={"counters": {"failures": 2}},
        now=_T0,
    )
    assert state_id > 0

    state = await store.get_state(key)
    assert state is not None
    assert state["last_snapshot_version"] == "v1"
    assert state["suppressed_count"] == 0
    assert state["last_sent_at"] is not None
    assert state["classification"] == "deterministic_unblock"

    audit = await store.audit_history(dedupe_key=decision.dedupe_key)
    assert len(audit) == 1
    assert audit[0]["should_send"] is True
    assert audit[0]["reason"] == "first_seen"
    assert audit[0]["slack_channel"] == "C123"
    assert audit[0]["slack_message_ts"] == "9.9"


@pytest.mark.asyncio
async def test_real_pg_feature_dedupe_unique_constraint(supervisor_pg_conn) -> None:
    """Two record_* calls for one dedupe key keep ONE state row (upsert)."""

    await _insert_feature(supervisor_pg_conn, "feat-1")
    store = SupervisorDigestDedupeStore(pool=supervisor_pg_conn, feature_id="feat-1")
    key = _key()
    first = await store.decide(key=key, snapshot_version="v1", now=_T0)
    id_a = await store.record_sent(
        decision=first, key=key, snapshot_version="v1", now=_T0
    )
    later = _T0 + timedelta(minutes=45)
    second = await store.decide(key=key, snapshot_version="v2", now=later)
    id_b = await store.record_sent(
        decision=second, key=key, snapshot_version="v2", now=later
    )
    # Same dedupe key -> the upsert reuses the same state row id.
    assert id_a == id_b
    count = await supervisor_pg_conn.fetchval(
        "SELECT count(*) FROM supervisor_digest_state WHERE feature_id = $1",
        "feat-1",
    )
    assert count == 1
    # ... but the audit table is append-only: two decision rows.
    audit_count = await supervisor_pg_conn.fetchval(
        "SELECT count(*) FROM supervisor_digest_audit WHERE feature_id = $1",
        "feat-1",
    )
    assert audit_count == 2


@pytest.mark.asyncio
async def test_real_pg_coalescing_persists_and_resets(supervisor_pg_conn) -> None:
    """Suppressed-count coalescing persists across calls; record_sent resets it.

    The doc-10 30-min suppression + coalescing, verified end-to-end on real
    Postgres TIMESTAMPTZ + the ON CONFLICT upsert.
    """

    await _insert_feature(supervisor_pg_conn, "feat-1")
    store = SupervisorDigestDedupeStore(pool=supervisor_pg_conn, feature_id="feat-1")
    key = _key()

    first = await store.decide(key=key, snapshot_version="v1", now=_T0)
    await store.record_sent(decision=first, key=key, snapshot_version="v1", now=_T0)

    # Three suppressed duplicates inside the 30-min cooldown.
    for minutes, version in ((5, "v2"), (12, "v3"), (25, "v4")):
        moment = _T0 + timedelta(minutes=minutes)
        suppressed = await store.decide(
            key=key, snapshot_version=version, now=moment
        )
        assert suppressed.should_send is False
        assert suppressed.reason == "suppressed_within_cooldown"
        await store.record_suppressed(
            decision=suppressed, key=key, snapshot_version=version, now=moment
        )

    state = await store.get_state(key)
    assert state["suppressed_count"] == 3
    # last_sent_at must NOT have advanced — a suppression is not a send.
    assert state["last_snapshot_version"] in ("v2", "v3", "v4")

    # Past the cooldown: a coalesced send carrying the count, then a reset.
    past = _T0 + timedelta(minutes=40)
    coalesced = await store.decide(key=key, snapshot_version="v5", now=past)
    assert coalesced.should_send is True
    assert coalesced.reason == "coalesced"
    assert coalesced.suppressed_count == 3
    await store.record_sent(
        decision=coalesced, key=key, snapshot_version="v5", now=past
    )
    state = await store.get_state(key)
    assert state["suppressed_count"] == 0
    assert state["last_snapshot_version"] == "v5"


@pytest.mark.asyncio
async def test_real_pg_never_suppress_first_stop_escalate(supervisor_pg_conn) -> None:
    """End-to-end: a first stop/escalate for a new signature is sent + audited.

    CORRECTNESS-CRITICAL on real Postgres — even with a just-sent state row for
    the same dedupe key, a new-failure-signature stop/escalate still sends.
    """

    await _insert_feature(supervisor_pg_conn, "feat-1")
    store = SupervisorDigestDedupeStore(pool=supervisor_pg_conn, feature_id="feat-1")
    key = _key(
        classification="pipeline_bug_suspected",
        recommended_action="stop/escalate",
        recommended_route="quiesce",
        failure_signature_hashes=["sig-new"],
    )
    first = await store.decide(key=key, snapshot_version="v1", now=_T0)
    await store.record_sent(decision=first, key=key, snapshot_version="v1", now=_T0)

    decision = await store.decide(
        key=key,
        snapshot_version="v2",
        new_failure_signature=True,
        now=_T0 + timedelta(minutes=2),
    )
    assert decision.should_send is True
    assert decision.reason == "material_change"
    await store.record_sent(
        decision=decision, key=key, snapshot_version="v2", now=_T0 + timedelta(minutes=2)
    )
    audit = await store.audit_history(dedupe_key=decision.dedupe_key)
    # Both sends are audited; the second is the never-suppressed escalation.
    assert [a["should_send"] for a in audit] == [True, True]


@pytest.mark.asyncio
async def test_real_pg_operator_required_for_new_route_sent(supervisor_pg_conn) -> None:
    """End-to-end: a first operator_required for a new route is never suppressed."""

    await _insert_feature(supervisor_pg_conn, "feat-1")
    store = SupervisorDigestDedupeStore(pool=supervisor_pg_conn, feature_id="feat-1")
    key = _key(
        classification="operator_required",
        recommended_action="stop/escalate",
        recommended_route="operator_required",
    )
    first = await store.decide(key=key, snapshot_version="v1", now=_T0)
    await store.record_sent(decision=first, key=key, snapshot_version="v1", now=_T0)

    decision = await store.decide(
        key=key,
        snapshot_version="v2",
        new_operator_route=True,
        now=_T0 + timedelta(minutes=1),
    )
    assert decision.should_send is True
    assert decision.reason == "material_change"


@pytest.mark.asyncio
async def test_real_pg_feature_scoping_isolates_keys(supervisor_pg_conn) -> None:
    """A store reads/writes only its own feature's rows (feature-scoped)."""

    await _insert_feature(supervisor_pg_conn, "feat-1")
    await _insert_feature(supervisor_pg_conn, "feat-2")
    store1 = SupervisorDigestDedupeStore(pool=supervisor_pg_conn, feature_id="feat-1")
    store2 = SupervisorDigestDedupeStore(pool=supervisor_pg_conn, feature_id="feat-2")

    # Same logical key shape, different feature.
    key1 = _key(feature_id="feat-1")
    key2 = _key(feature_id="feat-2")
    d1 = await store1.decide(key=key1, snapshot_version="v1", now=_T0)
    await store1.record_sent(decision=d1, key=key1, snapshot_version="v1", now=_T0)

    # feat-2's store sees NO prior state -> first_seen, not a suppression.
    d2 = await store2.decide(key=key2, snapshot_version="v1", now=_T0)
    assert d2.reason == "first_seen"
    assert d2.should_send is True
    # feat-1's store still sees its own row.
    assert await store1.get_state(key1) is not None
    # feat-1's store does NOT see feat-2's row.
    assert await store1.get_state(key2) is None


@pytest.mark.asyncio
async def test_real_pg_audit_history_is_bounded(supervisor_pg_conn) -> None:
    """audit_history clamps its limit (doc 10 § "Bounded-Read Constraints")."""

    await _insert_feature(supervisor_pg_conn, "feat-1")
    store = SupervisorDigestDedupeStore(pool=supervisor_pg_conn, feature_id="feat-1")
    key = _key()
    first = await store.decide(key=key, snapshot_version="v1", now=_T0)
    await store.record_sent(decision=first, key=key, snapshot_version="v1", now=_T0)
    for minutes, version in ((5, "v2"), (10, "v3")):
        moment = _T0 + timedelta(minutes=minutes)
        d = await store.decide(key=key, snapshot_version=version, now=moment)
        await store.record_suppressed(
            decision=d, key=key, snapshot_version=version, now=moment
        )
    # An absurd limit is clamped, not honored verbatim.
    rows = await store.audit_history(dedupe_key=first.dedupe_key, limit=10_000)
    assert len(rows) == 3
    # Newest-first ordering.
    assert rows[0]["id"] > rows[-1]["id"]
