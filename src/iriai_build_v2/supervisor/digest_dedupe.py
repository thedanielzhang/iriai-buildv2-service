"""Slice 10d — the supervisor Slack dedupe / suppression store.

doc 10 ("Supervisor And Dashboard Integration") § "Slack Dedupe And
Suppression" is the SPEC for this module. It adds :class:`SupervisorDigest
DedupeStore`, backed by two supervisor-owned tables:

* ``supervisor_digest_state`` — the latest aggregate per dedupe key (one row
  per ``(feature_id, dedupe_key)``).
* ``supervisor_digest_audit`` — an append-only log of every send/suppress
  decision.

doc 10: "They are audit state, not execution authority. Do not use artifacts
for dedupe state; artifacts may be projected for operator review only after
the table write succeeds." Both tables carry ``supervisor-digest-state:`` /
``supervisor-digest-audit:`` key prefixes already registered in
``supervisor/read_only.py`` :data:`SUPERVISOR_OWNED_AUDIT_KEY_PREFIXES`, so a
supervisor-owned projection of this state passes the Slice-10c-1 read-only
contract. The store NEVER touches an :class:`ExecutionControlStore` writer — it
is a supervisor-owned audit/dedupe writer, on the allowed side of the read-only
enforcement (doc 10 § "Read-Only And Audit Exception Policy" "Allowed writes":
"Slack dedupe/suppression records").

This is ADDITIVE (Slice 10d): the legacy artifact-classifier-driven
``SupervisorSlackDigestDecisionStore`` (``supervisor/slack.py``) is left
byte-for-byte unchanged. This new store is the typed-control-plane dedupe
contract — its dedupe key is the doc-10 stable JSON digest over
:class:`~iriai_build_v2.supervisor.models.SupervisorDigestKey`.

doc 10 § "Slack Dedupe And Suppression" rules implemented by :meth:`Supervisor
DigestDedupeStore.decide`:

* The dedupe key is a stable JSON digest over ``SupervisorDigestKey`` — evidence
  ids alone do not create a new key.
* Background Slack idempotency is keyed by ``(feature_id, group_idx,
  dedupe_key, snapshot_version)``. Reprocessing the same snapshot appends a
  suppress/coalesce audit row but does not send a second background message.
* Suppress identical background digests for at least 30 minutes; coalesce a
  suppressed count; send one update after the cooldown if the condition
  persists.
* NEVER suppress: a direct operator answer, a first ``stop/escalate`` for a NEW
  failure signature, or a first ``operator_required`` for a NEW typed route.
  These never-suppress exceptions are CORRECTNESS-CRITICAL — a suppressed first
  ``stop/escalate``/``operator_required`` is a MISSED ESCALATION.
* Record every send/suppress decision with snapshot version, dedupe key,
  citations, Slack channel/thread, message timestamp if sent, and reason.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import SupervisorDigestDecision, SupervisorDigestKey

logger = logging.getLogger(__name__)

__all__ = [
    "SUPPRESSION_COOLDOWN",
    "SUPPRESSION_COOLDOWN_SECONDS",
    "DigestDedupeStoreError",
    "compute_dedupe_key",
    "SupervisorDigestDedupeStore",
]

# doc 10 § "Slack Dedupe And Suppression": "Suppress identical background
# digests for at least 30 minutes." The cooldown window is a doc-10 constant,
# NOT a caller preference.
SUPPRESSION_COOLDOWN_SECONDS: int = 30 * 60
SUPPRESSION_COOLDOWN: timedelta = timedelta(seconds=SUPPRESSION_COOLDOWN_SECONDS)

# Bound the cited-evidence list written into the audit row (doc 10 §
# "Bounded-Read Constraints" — supervisor audit payloads stay bounded; no
# artifact bodies).
_CITATION_LIMIT: int = 12
# A read-bound for the rare audit-history reads (doc 10 § "Bounded-Read
# Constraints": "All reads bounded").
_AUDIT_HISTORY_CAP: int = 50
# A defensive cap on the JSONB display payload chars (doc 10 §
# "Bounded-Read Constraints" — no artifact bodies in dedupe state).
_PAYLOAD_CHAR_CAP: int = 20_000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DigestDedupeStoreError(RuntimeError):
    """A :class:`SupervisorDigestDedupeStore` table operation failed.

    doc 10 § "Edge Cases And Failure Handling": "Dedupe store write failure:
    fail open for operator-requested replies, fail quiet for background
    duplicate candidates." This exception lets the caller distinguish a store
    failure from a genuine suppress decision and apply that fail-open /
    fail-quiet split itself.
    """


def _stable_json(value: Any) -> str:
    """Deterministic JSON — mirrors ``execution_control.models.stable_json``.

    Re-implemented locally so this module stays a supervisor leaf with no
    ``execution_control`` import edge (the same isolation rationale the
    Slice-10c-2 ``classifier_mapping`` / ``read_only`` modules use).
    """

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def compute_dedupe_key(key: SupervisorDigestKey) -> str:
    """Return the stable dedupe-key digest for a :class:`SupervisorDigestKey`.

    doc 10 § "Slack Dedupe And Suppression": "Key digest uses stable JSON over
    ``SupervisorDigestKey``; evidence ids alone do not create a new Slack
    message unless classification, route, action, active attempt, queue
    status, or failure signature changes."

    The three list fields are SORTED here so list-ordering churn (the typed
    snapshot may emit failures/attempts in a different order between polls)
    never invents a new key. ``group_idx`` is part of the digest, so a
    feature-level and a group-level digest with otherwise identical state are
    distinct keys (matching the doc-10 idempotency tuple ``(feature_id,
    group_idx, dedupe_key, snapshot_version)``).
    """

    normalized = {
        "feature_id": str(key.feature_id),
        "group_idx": key.group_idx,
        "classification": str(key.classification),
        "recommended_action": str(key.recommended_action),
        "recommended_route": str(key.recommended_route),
        "failure_signature_hashes": sorted(
            str(item) for item in key.failure_signature_hashes
        ),
        "merge_queue_statuses": sorted(
            str(item) for item in key.merge_queue_statuses
        ),
        "active_attempt_ids": sorted(int(item) for item in key.active_attempt_ids),
    }
    return hashlib.sha256(_stable_json(normalized).encode("utf-8")).hexdigest()


def _bounded_citations(citation_refs: list[Any] | None) -> list[str]:
    """Bound the cited-evidence list for an audit row (no artifact bodies)."""

    if not citation_refs:
        return []
    return [
        str(item)[:240]
        for item in list(citation_refs)[:_CITATION_LIMIT]
        if str(item).strip()
    ]


def _bounded_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Bound the display payload written to a state/audit row.

    doc 10 § "Slack Dedupe And Suppression": ``last_digest_payload`` / ``payload``
    are "Bounded display payload, no artifact bodies." This drops nothing
    structurally — it only caps the serialized size as defence in depth.
    """

    if not payload:
        return {}
    encoded = _stable_json(payload)
    if len(encoded) <= _PAYLOAD_CHAR_CAP:
        return dict(payload)
    return {
        "truncated": True,
        "truncated_payload_chars": len(encoded),
    }


class SupervisorDigestDedupeStore:
    """Postgres-backed Slack digest send/suppress state + append-only audit.

    Backs the two supervisor-owned tables ``supervisor_digest_state`` and
    ``supervisor_digest_audit`` (doc 10 § "Slack Dedupe And Suppression"). A
    single instance is feature-scoped: every query is filtered by
    ``feature_id``.

    The store is a SUPERVISOR-OWNED audit/dedupe writer — it never reaches an
    ``ExecutionControlStore`` writer, so it is on the allowed side of the
    Slice-10c-1 read-only contract (doc 10 § "Read-Only And Audit Exception
    Policy" "Allowed writes": "Slack dedupe/suppression records").

    ``pool`` is anything exposing asyncpg-style ``fetch`` / ``fetchrow`` /
    ``execute`` coroutines (a real pool, a single connection, or an in-memory
    fake). All reads are bounded; all writes are feature-scoped.
    """

    def __init__(self, *, pool: Any, feature_id: str) -> None:
        self._pool = pool
        self._feature_id = str(feature_id)

    @property
    def feature_id(self) -> str:
        return self._feature_id

    # ── decision API ────────────────────────────────────────────────────────

    async def decide(
        self,
        *,
        key: SupervisorDigestKey,
        snapshot_version: str,
        is_operator_answer: bool = False,
        new_failure_signature: bool = False,
        new_operator_route: bool = False,
        now: datetime | None = None,
    ) -> SupervisorDigestDecision:
        """Decide whether a background Slack digest should be sent or suppressed.

        This is the doc-10 § "Slack Dedupe And Suppression" decision procedure.
        It reads the latest ``supervisor_digest_state`` row for the dedupe key
        and applies, in order:

        1. **Never-suppress exceptions** (CORRECTNESS-CRITICAL). If any of
           ``is_operator_answer`` / (``classification`` is a first-seen
           ``stop/escalate`` for a ``new_failure_signature``) / (a first-seen
           ``operator_required`` for a ``new_operator_route``) holds, the
           decision ALWAYS sends. doc 10: "Never suppress direct operator
           answers, first ``stop/escalate`` for a new failure signature, or
           first ``operator_required`` for a new typed route." A suppressed
           first ``stop/escalate``/``operator_required`` is a MISSED
           ESCALATION.
        2. **First seen** — no prior state row for the dedupe key -> send
           (``reason="first_seen"``).
        3. **Background idempotency** — the prior row already recorded a SENT
           digest at this exact ``snapshot_version`` -> suppress
           (``reason="suppressed_duplicate"``). doc 10: "Reprocessing the same
           snapshot ... must not send a second background Slack message for the
           same material state."
        4. **Cooldown** — the prior row was last sent < 30 min ago -> suppress
           and coalesce (``reason="suppressed_within_cooldown"``; the running
           ``suppressed_count`` is incremented).
        5. **Post-cooldown coalesced send** — the prior row was last sent ≥ 30
           min ago and the condition persists -> send one coalesced update
           (``reason="coalesced"``; the decision carries the coalesced
           ``suppressed_count``).

        This method is PURE w.r.t. the tables: it only READS state. The caller
        applies the decision via :meth:`record_sent` / :meth:`record_suppressed`
        AFTER the Slack send is attempted, so the audit row records the real
        outcome. ``now`` is injectable for deterministic tests.

        Raises :class:`DigestDedupeStoreError` on a store read failure so the
        caller can apply the doc-10 fail-open (operator) / fail-quiet
        (background) split.
        """

        moment = now or _utc_now()
        dedupe_key = compute_dedupe_key(key)

        # ── (1) never-suppress exceptions — evaluated BEFORE any table read so
        #        a store failure can never swallow a first escalation ────────
        never_suppress_reason = self._never_suppress_reason(
            key=key,
            is_operator_answer=is_operator_answer,
            new_failure_signature=new_failure_signature,
            new_operator_route=new_operator_route,
        )

        if never_suppress_reason is not None:
            # The state read here is purely INFORMATIONAL — it only fills the
            # decision's `suppressed_count` / `prior_digest_id` audit context
            # and never changes `should_send` (already True). doc 10 § "Edge
            # Cases And Failure Handling" requires an operator answer / a first
            # escalation to "fail open" — so a store read failure must NOT
            # block this send. We attempt the read for the audit context but
            # degrade to (0, None) if it fails.
            try:
                prior = await self._load_state(dedupe_key)
            except DigestDedupeStoreError:
                logger.debug(
                    "supervisor digest dedupe state read failed on a "
                    "never-suppress (%s) decision; sending fail-open",
                    never_suppress_reason,
                    exc_info=True,
                )
                prior = None
            return SupervisorDigestDecision(
                dedupe_key=dedupe_key,
                should_send=True,
                reason=never_suppress_reason,
                suppressed_count=int(prior["suppressed_count"]) if prior else 0,
                prior_digest_id=int(prior["id"]) if prior else None,
            )

        # A NON-exception background digest must surface a store read failure
        # as a typed error so the caller can apply the doc-10 fail-quiet split
        # (the caller suppresses a background duplicate candidate on a store
        # failure rather than risk a duplicate send).
        prior = await self._load_state(dedupe_key)

        # ── (2) first seen — no prior state row ─────────────────────────────
        if prior is None:
            return SupervisorDigestDecision(
                dedupe_key=dedupe_key,
                should_send=True,
                reason="first_seen",
                suppressed_count=0,
                prior_digest_id=None,
            )

        prior_id = int(prior["id"])
        prior_suppressed = int(prior["suppressed_count"])
        last_sent_at = _as_utc(prior["last_sent_at"])
        last_version = str(prior["last_snapshot_version"] or "")

        # ── (3) background idempotency — same snapshot already SENT ─────────
        # doc 10: background Slack idempotency is keyed by (feature_id,
        # group_idx, dedupe_key, snapshot_version). If the prior row was last
        # SENT (last_sent_at set) at this exact snapshot_version, reprocessing
        # the same snapshot must not send a second message.
        if (
            last_sent_at is not None
            and last_version != ""
            and last_version == str(snapshot_version)
        ):
            return SupervisorDigestDecision(
                dedupe_key=dedupe_key,
                should_send=False,
                reason="suppressed_duplicate",
                suppressed_count=prior_suppressed,
                prior_digest_id=prior_id,
            )

        # ── (4) cooldown — last send < 30 min ago -> suppress + coalesce ────
        if (
            last_sent_at is not None
            and moment - last_sent_at < SUPPRESSION_COOLDOWN
        ):
            return SupervisorDigestDecision(
                dedupe_key=dedupe_key,
                should_send=False,
                reason="suppressed_within_cooldown",
                # The coalesced count INCLUDES this suppressed duplicate.
                suppressed_count=prior_suppressed + 1,
                prior_digest_id=prior_id,
            )

        # ── (5) post-cooldown coalesced send (or a never-sent prior row) ────
        # Either the cooldown elapsed and the condition persists (doc 10:
        # "send one update if the same condition persists past the cooldown"),
        # or the prior row exists but was never sent (only suppressed) — both
        # resolve to a send. When duplicates were coalesced the decision is
        # `coalesced` and carries the count; otherwise it is a plain
        # `material_change` (the dedupe key row exists but nothing was
        # coalesced — e.g. a prior suppressed-only row at count 0).
        reason = "coalesced" if prior_suppressed > 0 else "material_change"
        return SupervisorDigestDecision(
            dedupe_key=dedupe_key,
            should_send=True,
            reason=reason,
            suppressed_count=prior_suppressed,
            prior_digest_id=prior_id,
        )

    def _never_suppress_reason(
        self,
        *,
        key: SupervisorDigestKey,
        is_operator_answer: bool,
        new_failure_signature: bool,
        new_operator_route: bool,
    ) -> str | None:
        """Return the decision reason iff this digest must NEVER be suppressed.

        doc 10 § "Slack Dedupe And Suppression": "Never suppress direct
        operator answers, first ``stop/escalate`` for a new failure signature,
        or first ``operator_required`` for a new typed route."

        The three exception arms map to the three :class:`SupervisorDigest
        Decision` reasons:

        * a direct operator answer -> ``operator_requested``;
        * a first ``stop/escalate`` for a NEW failure signature OR a first
          ``operator_required`` for a NEW typed route -> ``material_change``
          (a genuine new material state that must reach the operator).

        ``None`` means no exception applies and normal dedupe/cooldown logic
        runs.
        """

        # Arm A — a direct operator answer is NEVER a background digest and is
        # never suppressed (doc 10). The caller passes is_operator_answer=True
        # for an operator-initiated reply.
        if is_operator_answer:
            return "operator_requested"

        action = str(key.recommended_action or "").strip().lower()
        classification = str(key.classification or "").strip().lower()

        # Arm B — a first `stop/escalate` for a NEW failure signature. A
        # `stop/escalate` digest carries a new failure signature when the typed
        # snapshot's failure signature was not seen before; suppressing the
        # FIRST such digest is a missed P1/P2 escalation.
        is_stop_escalate = action in ("stop/escalate", "stop_escalate")
        if is_stop_escalate and new_failure_signature:
            return "material_change"

        # Arm C — a first `operator_required` for a NEW typed route. The
        # classification (doc 10 SupervisorDigest.classification) is
        # `operator_required`; `new_operator_route` is set when the typed
        # route behind it is new.
        if classification == "operator_required" and new_operator_route:
            return "material_change"

        return None

    # ── audit / state writers ───────────────────────────────────────────────

    async def record_sent(
        self,
        *,
        decision: SupervisorDigestDecision,
        key: SupervisorDigestKey,
        snapshot_version: str,
        slack_channel: str = "",
        slack_thread_ts: str = "",
        slack_message_ts: str = "",
        citation_refs: list[Any] | None = None,
        payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> int:
        """Persist a SENT decision: upsert latest state + append an audit row.

        Called by the caller AFTER a Slack digest send succeeds. Updates the
        ``supervisor_digest_state`` row for the dedupe key — stamps
        ``last_sent_at`` / ``last_snapshot_version`` and RESETS
        ``suppressed_count`` to 0 (the coalesced backlog has now been delivered)
        — and appends a ``should_send=true`` ``supervisor_digest_audit`` row.

        Returns the ``supervisor_digest_state`` row id. Raises
        :class:`DigestDedupeStoreError` on a store failure.
        """

        moment = now or _utc_now()
        state_id = await self._upsert_state(
            key=key,
            dedupe_key=decision.dedupe_key,
            snapshot_version=snapshot_version,
            last_sent_at=moment,
            suppressed_count=0,
            payload=payload,
        )
        await self._append_audit(
            state_id=state_id,
            key=key,
            decision=decision,
            snapshot_version=snapshot_version,
            should_send=True,
            slack_channel=slack_channel,
            slack_thread_ts=slack_thread_ts,
            slack_message_ts=slack_message_ts,
            citation_refs=citation_refs,
            payload=payload,
        )
        return state_id

    async def record_suppressed(
        self,
        *,
        decision: SupervisorDigestDecision,
        key: SupervisorDigestKey,
        snapshot_version: str,
        slack_channel: str = "",
        slack_thread_ts: str = "",
        citation_refs: list[Any] | None = None,
        payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> int:
        """Persist a SUPPRESSED decision: coalesce the count + append an audit row.

        Called by the caller when :meth:`decide` returned ``should_send=False``.
        Updates the ``supervisor_digest_state`` row — sets ``suppressed_count``
        to ``decision.suppressed_count`` (the coalesced running total) and
        LEAVES ``last_sent_at`` untouched (a suppression is not a send) — and
        appends a ``should_send=false`` ``supervisor_digest_audit`` row.

        Returns the ``supervisor_digest_state`` row id. Raises
        :class:`DigestDedupeStoreError` on a store failure.
        """

        state_id = await self._upsert_state(
            key=key,
            dedupe_key=decision.dedupe_key,
            snapshot_version=snapshot_version,
            last_sent_at=None,
            suppressed_count=max(0, int(decision.suppressed_count)),
            payload=payload,
        )
        await self._append_audit(
            state_id=state_id,
            key=key,
            decision=decision,
            snapshot_version=snapshot_version,
            should_send=False,
            slack_channel=slack_channel,
            slack_thread_ts=slack_thread_ts,
            slack_message_ts="",
            citation_refs=citation_refs,
            payload=payload,
        )
        return state_id

    # ── bounded reads ───────────────────────────────────────────────────────

    async def get_state(self, key: SupervisorDigestKey) -> dict[str, Any] | None:
        """Return the latest ``supervisor_digest_state`` row for ``key`` or None."""

        return await self._load_state(compute_dedupe_key(key))

    async def audit_history(
        self,
        *,
        dedupe_key: str,
        limit: int = _AUDIT_HISTORY_CAP,
    ) -> list[dict[str, Any]]:
        """Return recent ``supervisor_digest_audit`` rows for a dedupe key.

        Bounded (doc 10 § "Bounded-Read Constraints": "All reads bounded") —
        ``limit`` is clamped to :data:`_AUDIT_HISTORY_CAP` and the read uses the
        ``idx_supervisor_dedupe_audit_key`` index, newest-first.
        """

        capped = max(1, min(int(limit), _AUDIT_HISTORY_CAP))
        rows = await self._fetch(
            """
            SELECT id, state_id, feature_id, group_idx, dedupe_key,
                   snapshot_version, should_send, reason, citation_refs,
                   slack_channel, slack_thread_ts, slack_message_ts, payload,
                   created_at
            FROM supervisor_digest_audit
            WHERE feature_id = $1 AND dedupe_key = $2
            ORDER BY id DESC
            LIMIT $3
            """,
            self._feature_id,
            dedupe_key,
            capped,
        )
        return [dict(row) for row in rows]

    # ── internals ───────────────────────────────────────────────────────────

    async def _load_state(self, dedupe_key: str) -> dict[str, Any] | None:
        """Read the single latest-state row for a dedupe key (feature-scoped)."""

        row = await self._fetchrow(
            """
            SELECT id, feature_id, group_idx, dedupe_key,
                   last_snapshot_version, classification, recommended_action,
                   recommended_route, last_sent_at, suppressed_count,
                   last_digest_payload, created_at, updated_at
            FROM supervisor_digest_state
            WHERE feature_id = $1 AND dedupe_key = $2
            """,
            self._feature_id,
            dedupe_key,
        )
        return dict(row) if row is not None else None

    async def _upsert_state(
        self,
        *,
        key: SupervisorDigestKey,
        dedupe_key: str,
        snapshot_version: str,
        last_sent_at: datetime | None,
        suppressed_count: int,
        payload: dict[str, Any] | None,
    ) -> int:
        """Insert-or-update the latest ``supervisor_digest_state`` row.

        Keyed by the ``supervisor_digest_state_feature_dedupe`` unique
        constraint ``(feature_id, dedupe_key)``. On a SENT decision
        ``last_sent_at`` is supplied and ``last_sent_at`` is advanced; on a
        SUPPRESSED decision ``last_sent_at`` is ``None`` and the existing
        ``last_sent_at`` is PRESERVED (``COALESCE(EXCLUDED.last_sent_at,
        supervisor_digest_state.last_sent_at)``) so a suppression never
        masquerades as a send.
        """

        bounded_payload = _bounded_payload(payload)
        row = await self._fetchrow(
            """
            INSERT INTO supervisor_digest_state (
                feature_id, group_idx, dedupe_key, last_snapshot_version,
                classification, recommended_action, recommended_route,
                last_sent_at, suppressed_count, last_digest_payload, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, NOW())
            ON CONFLICT (feature_id, dedupe_key) DO UPDATE SET
                group_idx = EXCLUDED.group_idx,
                last_snapshot_version = EXCLUDED.last_snapshot_version,
                classification = EXCLUDED.classification,
                recommended_action = EXCLUDED.recommended_action,
                recommended_route = EXCLUDED.recommended_route,
                last_sent_at = COALESCE(
                    EXCLUDED.last_sent_at, supervisor_digest_state.last_sent_at
                ),
                suppressed_count = EXCLUDED.suppressed_count,
                last_digest_payload = EXCLUDED.last_digest_payload,
                updated_at = NOW()
            RETURNING id
            """,
            self._feature_id,
            key.group_idx,
            dedupe_key,
            str(snapshot_version),
            str(key.classification),
            str(key.recommended_action),
            str(key.recommended_route),
            last_sent_at,
            max(0, int(suppressed_count)),
            json.dumps(bounded_payload, sort_keys=True, default=str),
        )
        if row is None:
            raise DigestDedupeStoreError(
                "supervisor_digest_state upsert returned no row id"
            )
        return int(row["id"])

    async def _append_audit(
        self,
        *,
        state_id: int,
        key: SupervisorDigestKey,
        decision: SupervisorDigestDecision,
        snapshot_version: str,
        should_send: bool,
        slack_channel: str,
        slack_thread_ts: str,
        slack_message_ts: str,
        citation_refs: list[Any] | None,
        payload: dict[str, Any] | None,
    ) -> None:
        """Append one append-only ``supervisor_digest_audit`` decision row."""

        audit_payload = _bounded_payload(payload)
        await self._execute(
            """
            INSERT INTO supervisor_digest_audit (
                state_id, feature_id, group_idx, dedupe_key, snapshot_version,
                should_send, reason, citation_refs, slack_channel,
                slack_thread_ts, slack_message_ts, payload
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12::jsonb)
            """,
            int(state_id),
            self._feature_id,
            key.group_idx,
            decision.dedupe_key,
            str(snapshot_version),
            bool(should_send),
            str(decision.reason),
            json.dumps(_bounded_citations(citation_refs), default=str),
            str(slack_channel or ""),
            str(slack_thread_ts or ""),
            str(slack_message_ts or ""),
            json.dumps(audit_payload, sort_keys=True, default=str),
        )

    # ── pool adapters ───────────────────────────────────────────────────────
    #
    # Mirrors the duck-typed adapter pattern of `SupervisorSlackDigestDecision
    # Store` (`supervisor/slack.py`): a real asyncpg pool, a single connection,
    # or an in-memory fake all satisfy the same `fetch` / `fetchrow` /
    # `execute` surface. A store-layer failure is wrapped as
    # `DigestDedupeStoreError` so the caller can apply the doc-10 fail-open /
    # fail-quiet split.

    async def _fetch(self, query: str, *args: Any) -> list[Any]:
        fetch = getattr(self._pool, "fetch", None)
        if not callable(fetch):
            return []
        try:
            return list(await fetch(query, *args))
        except DigestDedupeStoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised as a typed error
            raise DigestDedupeStoreError(
                f"supervisor digest dedupe fetch failed: {exc}"
            ) from exc

    async def _fetchrow(self, query: str, *args: Any) -> Any:
        fetchrow = getattr(self._pool, "fetchrow", None)
        if not callable(fetchrow):
            raise DigestDedupeStoreError(
                "supervisor digest dedupe pool exposes no 'fetchrow' coroutine"
            )
        try:
            return await fetchrow(query, *args)
        except DigestDedupeStoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised as a typed error
            raise DigestDedupeStoreError(
                f"supervisor digest dedupe fetchrow failed: {exc}"
            ) from exc

    async def _execute(self, query: str, *args: Any) -> Any:
        execute = getattr(self._pool, "execute", None)
        if not callable(execute):
            raise DigestDedupeStoreError(
                "supervisor digest dedupe pool exposes no 'execute' coroutine"
            )
        try:
            return await execute(query, *args)
        except DigestDedupeStoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised as a typed error
            raise DigestDedupeStoreError(
                f"supervisor digest dedupe execute failed: {exc}"
            ) from exc


def _as_utc(value: Any) -> datetime | None:
    """Coerce a stored timestamp to a tz-aware UTC ``datetime`` (or None).

    A naive ``datetime`` is treated as UTC; a non-datetime returns ``None``
    (so a malformed ``last_sent_at`` degrades to "never sent" rather than
    raising — the conservative direction is to allow the send).
    """

    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
