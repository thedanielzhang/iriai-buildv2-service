"""Slice 19 fourth sub-slice -- READ-ONLY typed Slack rendering surface
that projects governance snapshots onto a bounded Block Kit payload.

This module implements doc-19 § Refactoring Steps step 4 (line 155):
*"Add Slack rendering with dedupe and rate limiting inherited from
Slice 10 patterns."* + doc-19:140-142 *"Slack digest with dedupe key
from ``snapshot_digest``, not only corpus id or top ids, so material
changes in evidence quality, replay confidence, omitted detail counts,
or implementation-deviation summaries are not suppressed."* +
doc-19:122-123 *"Slack digest: 40 KB serialized Block Kit payload and
5 top findings."*

It owns the typed Slack-rendering projection surface:

* :data:`SLACK_RENDERER_FAILURE_ID` -- the typed failure id
  (``governance_slack_renderer_failed``) registered under the EXISTING
  ``evidence_corruption`` failure_class in
  :mod:`iriai_build_v2.workflows.develop.execution.failure_router` with
  the EXISTING NON-blocking :data:`RouteAction`
  ``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
  mirrors Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B + 4th +
  Slice 17 2nd-6th + Slice 18 2nd-7th + Slice 19 2nd + 3rd sub-slice
  precedent verbatim; NOT a new failure_class; NOT a new route action).

* :data:`DedupeDecision` -- the typed Literal for the dedupe-suppression
  decision: ``emitted`` / ``suppressed_dedupe`` / ``budget_exceeded`` /
  ``upstream_missing``. The typed surface lets consumers detect
  dedupe + budget state at construction.

* :class:`SlackFindingSummary` -- the bounded display-only finding
  summary BaseModel (refs-only projection of the Slice 16
  :class:`GovernanceFinding` identity surface; no body hydration).

* :class:`SlackBlockKitBlock` -- the typed Block Kit block shape
  (per Slack Block Kit API ``type`` + ``text`` + ``fields`` fields).

* :class:`SlackBlockKitPayload` -- the typed bounded Block Kit payload
  (typed ``blocks: list[SlackBlockKitBlock]`` + typed dedupe_key +
  typed corpus_id; serialised to JSON by the rate-limiter caller).

* :class:`SlackRenderInputs` -- the typed bundle of inputs the
  :meth:`GovernanceSlackRenderer.render` method consumes (the caller-
  provided Slice 19 2nd sub-slice :class:`SnapshotAPIResult` source +
  optional caller-side display budget overrides + optional dedupe-
  cache of already-emitted dedupe keys).

* :class:`SlackRenderPayload` -- the typed bounded Slack payload per
  doc-19:140-142 (``dedupe_key`` = ``snapshot_digest`` verbatim;
  ``corpus_id``; ``generated_at``; truncation/completeness/quality
  bounded fields; bounded Block Kit payload; ``omitted_counts``;
  ``serialized_bytes`` count; ``display_only`` AC2 flag).

* :class:`SlackRenderGap` -- the typed gap finding emitted when the
  Slack rendering fails structurally (mirrors :class:`DashboardViewGap`
  + :class:`SnapshotAPIGap` verbatim per the chunk-shape decision).

* :class:`SlackRenderResult` -- the typed result BaseModel (``payload
  | None`` + ``decision: DedupeDecision`` + ``gap_findings`` list).

* :class:`GovernanceSlackRenderer` -- the typed Slack renderer class
  with the public projection method :meth:`render` + the public typed
  helper :meth:`compute_dedupe_key` (returns the typed
  ``snapshot.snapshot_digest`` verbatim per doc-19:140-142 -- the
  dedupe_key IS the snapshot_digest; no second source of truth).

**Bounded-reads + refs-only discipline (per governance prompt §
"Non-Negotiables").** The Slack renderer is a READ-ONLY typed
projection over the typed Slice 19 2nd sub-slice :class:`SnapshotAPIResult`
+ the typed Slice 19 1st sub-slice :class:`GovernanceSnapshot` it
contains. The renderer does NOT hydrate artifact bodies -- the typed
summaries carry only the typed identity-surface fields per the
Slice 16 contract (idempotency_key + class_name + severity +
confidence + estimated_lost_hours for findings). The typed page_refs
surface is referenced only via per-list counts (not full content).

**40 KB Block Kit budget enforcement (per doc-19:122-123).** The
typed :attr:`SlackRenderInputs.max_payload_bytes` defaults to ``40 960``
bytes (40 KB) per doc-19:122-123. The :meth:`render` method:

1. Constructs the typed Block Kit payload with the typed bounded
   summaries.
2. Serialises the typed payload to JSON to measure the byte count.
3. If the serialised byte count exceeds the budget, the renderer
   progressively truncates the bounded summary lists (findings first,
   then recommendations, then replay_results) until the serialised
   payload fits within budget OR all summaries are dropped.
4. If even the empty payload exceeds budget (structurally impossible
   under typical Block Kit headers), the renderer returns
   ``decision="budget_exceeded"`` + a typed gap finding.

The typed :attr:`SlackRenderPayload.serialized_bytes` exposes the
final serialised byte count so the rate-limiter caller can verify the
budget enforcement.

**5-top-findings cap (per doc-19:122-123).** The typed
:attr:`SlackRenderInputs.max_top_findings` defaults to ``5`` per
doc-19:122-123 *"and 5 top findings."*. The renderer caps the typed
findings list at this value BEFORE attempting the 40 KB budget
truncation; if the resulting payload still exceeds 40 KB, the
findings list is further truncated by the budget enforcement step.

**Slice 10 dedupe + rate-limiting pattern INHERITED structurally.**
Per doc-19:155 the dedupe + rate-limiting pattern is INHERITED from
Slice 10 -- NOT direct calls into Slice 10 writers (READ-ONLY only).
The renderer surfaces:

* Typed :attr:`SlackRenderPayload.dedupe_key` = the typed
  :attr:`GovernanceSnapshot.snapshot_digest` verbatim per
  doc-19:140-142.
* Typed :attr:`SlackRenderInputs.recently_emitted_dedupe_keys` cache
  override (the typed ``set[str]`` of recently-emitted dedupe keys
  the rate-limiter caller MAY pass in to suppress dedupe-hit
  rendering at the source).
* Typed :attr:`SlackRenderResult.decision` Literal that reports
  whether the payload was ``emitted`` (fresh), ``suppressed_dedupe``
  (cache hit), ``budget_exceeded`` (40 KB cap), or
  ``upstream_missing`` (upstream snapshot is None).

The rate-limiter (the caller -- typically the Slice 19 6th sub-slice
report artifact writer or a dedicated Slack outbox) owns the typed
rate-limit policy + the actual cache persistence; the renderer is a
pure typed projection that consumes the cache snapshot and reports
the typed dedupe decision.

**Activation-authority boundary preserved (doc-19:348-349 AC).** The
:class:`GovernanceSlackRenderer` class has TWO public methods
(:meth:`render` + :meth:`compute_dedupe_key`) and NO mutation methods
on any of its typed shapes (no ``activate_`` / ``approve_`` /
``merge_`` / ``checkpoint_`` / ``mutate_`` / ``write_`` / ``persist_``
methods). The renderer does NOT extend the Slice 10c-1
``CONTROL_PLANE_WRITER_METHODS`` set per doc-19:348-349 *"Supervisor/
dashboard read-only contract preserved (no governance writer extends
the Slice 10c-1 ``CONTROL_PLANE_WRITER_METHODS`` set)."* The renderer
does NOT mint ``dag-*`` execution-authority artifact-key string
literals.

**Doc-19 acceptance criteria enforcement (sub-slice axes):**

* **AC1** (doc-19:224) *"Reports are bounded, reproducible,
  evidence-cited, and structured first."* -- enforced by:
  * **Bounded**: the typed :attr:`SlackRenderPayload.serialized_bytes`
    + :attr:`SlackRenderPayload.truncated` + the typed
    :attr:`SlackRenderPayload.omitted_counts` + the 40 KB budget
    enforcement step.
  * **Reproducible**: the typed :attr:`SlackRenderPayload.dedupe_key`
    is the typed :attr:`GovernanceSnapshot.snapshot_digest` verbatim
    per doc-19:140-142; same snapshot -> same dedupe_key (->
    suppressed by the rate-limiter cache).
  * **Evidence-cited**: the typed
    :attr:`SlackRenderPayload.payload.blocks` carries the typed
    finding/recommendation/replay identity surface (idempotency_key
    + recommendation_id + result_id) in the per-block text fields.
  * **Structured first**: the typed Pydantic BaseModel surface
    (typed Block Kit blocks -- not prose).

* **AC2** (doc-19:225-226) *"Truncated or preview reports are never
  authoritative unless exact page refs and completeness metadata
  cover the consumer's required scope."* -- enforced by the typed
  :attr:`SlackRenderPayload.display_only` bool flag: ``True`` iff
  ``(truncated=True AND no page_refs)`` OR ``(completeness in
  {"preview_only", "unavailable"})``. The typed surface lets
  consumers detect display-only state at construction; the Block
  Kit payload also includes a typed "Display-only" text notice block
  when the flag is True.

* **AC6** (doc-19:232-233) *"Human-facing dashboard/Slack output
  explains top findings without hiding evidence quality or omitted
  details."* -- enforced by the typed
  :attr:`SlackRenderPayload.evidence_quality` (Slice 13a REUSE) +
  :attr:`SlackRenderPayload.omitted_counts` + the typed Block Kit
  payload always containing an evidence-quality text block + an
  omitted-counts text block (the renderer cannot omit them).

* **AC7** (doc-19:234) *"Reporting honors Slice 10 read-only and
  bounded-read guarantees."* -- enforced by the typed class having
  TWO public methods (:meth:`render` + :meth:`compute_dedupe_key`) +
  no mutation methods on any BaseModel + no ``dag-*`` artifact-key
  literals + no ``CONTROL_PLANE_WRITER_METHODS`` extension.

**Fail-closed discipline (per auto-memory
``feedback_no_silent_degradation``).** The :meth:`render` method
NEVER raises -- structural failures project onto typed
:class:`SlackRenderGap` finding(s) emitted on
:attr:`SlackRenderResult.gap_findings`. A SINGLE typed failure id
:data:`SLACK_RENDERER_FAILURE_ID` (``governance_slack_renderer_failed``)
covers the doc-19:184-194 edge-case rows + the doc-19:191-192 Slack
delivery failure row (mirror of the Slice 17 6th sub-slice
``consumer_read_api_failed`` + Slice 18 2nd sub-slice
``replay_corpus_or_scenario_load_failed`` + Slice 19 2nd sub-slice
``governance_snapshot_api_failed`` + Slice 19 3rd sub-slice
``governance_dashboard_view_failed`` 1-failure-id-per-typed-API-class
precedent verbatim). The typed :class:`SlackRenderGap` shape carries
the surface ``reason`` so consumers can distinguish edge-case classes
if needed.

Per the auto-memory ``feedback_flat_structured_output`` rule the
typed control fields are flat primitives (``str``, ``int``,
``bool``, ``list[str]``); no nested BaseModels are required for
control signaling.

Per the no-second-source-of-truth discipline this module REUSES the
following typed shapes (DIRECT import; annotation-identity assertions
in the unit-test surface enforce the contract):

* Slice 19 1st sub-slice :class:`GovernanceSnapshot`
  (:mod:`iriai_build_v2.execution_control.governance_agent`) for the
  snapshot input surface.
* Slice 19 2nd sub-slice :class:`SnapshotAPIResult` +
  :class:`SnapshotAPIGap`
  (:mod:`iriai_build_v2.execution_control.governance_snapshot_api`)
  for the typed source bundle the renderer consumes.
* Slice 13a :data:`CompletenessState` + :data:`EvidenceQuality`
  Literals (:mod:`iriai_build_v2.workflows.develop.governance.models`)
  for the typed completeness + evidence-quality fields.
* Slice 16 :class:`GovernanceFinding` + :data:`FindingSeverity` +
  :data:`FindingKind` Literals
  (:mod:`iriai_build_v2.execution_control.finding_engine`) for the
  typed display-only finding summary surface.
* Slice 17 :class:`GovernancePolicyRecommendation` +
  :data:`PolicyConsumer` + :data:`PolicyRecommendationStatus` Literals
  (:mod:`iriai_build_v2.execution_control.policy_recommendation`) for
  the typed display-only recommendation summary surface.
* Slice 18 :class:`CounterfactualResult` + :data:`RiskChange` Literal
  (:mod:`iriai_build_v2.execution_control.counterfactual_replay`) for
  the typed display-only replay-result summary surface.

It is the **Slack-projection layer** that subsequent Slice 19
sub-slices (agent-context builder / report artifacts) cite as the
typed bounded-display contract for the Slack surface.

**Slice 13A invariant compliance.** The Slack renderer consumes the
typed Slice 19 2nd sub-slice :class:`SnapshotAPIResult` which is itself
a typed bounded read over the cited Slice 16/17/18 evidence surface;
the Slack renderer does NOT hydrate artifact bodies (page_refs surface
is referenced only by count) and does NOT consume preview-only
evidence as execution authority (it is a READ-ONLY display
projection).

References:

* Doc-19 § Refactoring Steps step 4 (line 155) -- *"Add Slack
  rendering with dedupe and rate limiting inherited from Slice 10
  patterns."*
* Doc-19:140-142 -- *"Slack digest with dedupe key from
  ``snapshot_digest``..."*
* Doc-19:122-123 -- *"Slack digest: 40 KB serialized Block Kit
  payload and 5 top findings."*
* Doc-19:128-131 + doc-19:225-226 AC2 -- preview/display budgets;
  truncated snapshots without exact page refs are display-only.
* Doc-19:166-167 -- *"Governance reports are projections of governance
  rows."*
* Doc-19:184-194 -- edge-case rows mapped to typed gap reasons.
* Doc-19:191-192 -- *"Slack delivery failure: keep report artifact
  and retry via existing outbox policy if configured."*
* Doc-19:218 -- *"Report generation is reproducible for the same
  corpus id."* (dedupe_key = snapshot_digest = reproducible per
  doc-19:140-142.)
* Doc-19:234 + doc-19:348-349 AC -- read-only contract.
* Doc-14:242-243 -- governance-projection NON-blocking contract
  (inherited by every post-checkpoint governance projection
  observer; the Slack renderer is also a post-checkpoint observer).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
    RiskChange,
)
from iriai_build_v2.execution_control.finding_engine import (
    FindingKind,
    FindingSeverity,
    GovernanceFinding,
)
from iriai_build_v2.execution_control.governance_agent import (
    GovernanceSnapshot,
)
from iriai_build_v2.execution_control.governance_snapshot_api import (
    SnapshotAPIGap,
    SnapshotAPIResult,
)
from iriai_build_v2.execution_control.policy_recommendation import (
    GovernancePolicyRecommendation,
    PolicyConsumer,
    PolicyRecommendationStatus,
)
from iriai_build_v2.workflows.develop.governance.models import (
    CompletenessState,
    EvidenceQuality,
)


__all__ = [
    # Typed failure id (doc-19:184-194 + doc-14:242-243 NON-BLOCKING).
    "SLACK_RENDERER_FAILURE_ID",
    # Typed dedupe decision (doc-19:140-142 + doc-19:155 Slice 10 pattern).
    "DedupeDecision",
    # Bounded display-only summary BaseModels (refs-only).
    "SlackFindingSummary",
    # Typed Block Kit shapes (per Slack Block Kit API).
    "SlackBlockKitBlock",
    "SlackBlockKitPayload",
    # Typed Slack renderer inputs / payload / gap / result.
    "SlackRenderInputs",
    "SlackRenderPayload",
    "SlackRenderGap",
    "SlackRenderResult",
    # The Slack renderer class (doc-19:155 step 4).
    "GovernanceSlackRenderer",
]


# --- Typed failure id (doc-19:184-194 + doc-14:242-243 NON-BLOCKING) --------


SLACK_RENDERER_FAILURE_ID: Literal[
    "governance_slack_renderer_failed"
] = "governance_slack_renderer_failed"
"""Doc-19:184-194 + doc-19:191-192 + doc-14:242-243 -- the typed
failure id the governance Slack renderer projects onto when a
structural projection step fails or the 40 KB Block Kit budget is
exceeded.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT
a new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd-6th + Slice 18 2nd-7th + Slice 19
2nd + 3rd sub-slice precedent verbatim).

A SINGLE failure id covers ALL the doc-19:184-194 edge-case rows + the
doc-19:191-192 Slack delivery failure row (governance snapshot stale;
missing line provenance; too many findings; Slack delivery failure;
active workflow pressure) per the Slice 17 6th sub-slice
``consumer_read_api_failed`` + Slice 18 2nd sub-slice
``replay_corpus_or_scenario_load_failed`` + Slice 19 2nd sub-slice
``governance_snapshot_api_failed`` + Slice 19 3rd sub-slice
``governance_dashboard_view_failed`` precedent (one typed failure id
per typed-API class). The typed :class:`SlackRenderGap` shape carries
the surface ``reason`` so consumers can distinguish edge-case classes
if needed.

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 19 pattern matches the Slice 14 + Slice 15 + Slice 16 + Slice
17 + Slice 18 + Slice 19 2nd + 3rd sub-slice non-blocking governance
projection observer (the Slack renderer is also a post-checkpoint
governance projection observer + per doc-19:166-167 reports are
projections of governance rows -- never runtime policy authority).

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to Slack renderer
failures.

Per doc-19:191-192 *"Slack delivery failure: keep report artifact and
retry via existing outbox policy if configured."* -- the typed failure
id under EXISTING ``evidence_corruption`` class routes to the EXISTING
``retry_governance_projection`` non-blocking action so the outbox
retry semantics fall through cleanly.
"""


# --- Typed dedupe decision (doc-19:140-142 + doc-19:155 Slice 10 pattern) ---


DedupeDecision: Any = Literal[
    "emitted",
    "suppressed_dedupe",
    "budget_exceeded",
    "upstream_missing",
]
"""Doc-19:140-142 + doc-19:155 -- the typed Literal for the Slack
render dedupe-suppression decision.

Values:

* ``emitted`` -- the typed payload was constructed + the dedupe key was
  NOT a cache hit; the rate-limiter caller MAY deliver the payload.
* ``suppressed_dedupe`` -- the typed payload was constructed but the
  dedupe key WAS in the recently-emitted cache (per the typed
  :attr:`SlackRenderInputs.recently_emitted_dedupe_keys` set); the
  rate-limiter caller MUST suppress delivery to avoid duplicate
  notifications. Per doc-19:140-142 the dedupe key is derived from the
  typed :attr:`GovernanceSnapshot.snapshot_digest` verbatim so material
  changes in evidence quality, replay confidence, omitted detail
  counts, or implementation-deviation summaries are NOT suppressed
  (the digest changes when the underlying inputs change).
* ``budget_exceeded`` -- even the empty Block Kit payload exceeded
  the 40 KB byte budget (structurally impossible under typical Block
  Kit headers); the rate-limiter caller MUST NOT deliver the
  malformed payload.
* ``upstream_missing`` -- the upstream :class:`SnapshotAPIResult`
  carried no snapshot (None); the rate-limiter caller MUST suppress
  delivery; the upstream gap findings are propagated to
  :attr:`SlackRenderResult.gap_findings` verbatim.

Per the auto-memory ``feedback_flat_structured_output`` rule the typed
control field is a flat primitive Literal (``str``); no nested
BaseModel is required for the dedupe-decision signaling.
"""


def _utcnow() -> datetime:
    """Return the current UTC timestamp (timezone-aware).

    Mirrors the Slice 19 3rd sub-slice
    :func:`~iriai_build_v2.execution_control.governance_dashboard_view._utcnow`
    + Slice 19 2nd sub-slice
    :func:`~iriai_build_v2.execution_control.governance_snapshot_api._utcnow`
    + Slice 18 2nd sub-slice
    :func:`~iriai_build_v2.execution_control.counterfactual_replay_loader._utcnow`
    + Slice 17 4th sub-slice
    :func:`~iriai_build_v2.execution_control.decision_record_writer._utcnow`
    verbatim. Stdlib-only.
    """

    return datetime.now(timezone.utc)


# --- Bounded display-only summary BaseModels (refs-only) -------------------


class SlackFindingSummary(BaseModel):
    """Bounded display-only projection of a Slice 16
    :class:`GovernanceFinding` for the Slack rendering surface.

    Per doc-19:122-123 *"5 top findings"* the Slack digest carries up
    to 5 typed finding summaries (further truncated by the 40 KB byte
    budget if needed). The typed summary carries only the typed Slice
    16 identity-surface fields (no body hydration); the typed surface
    enforces refs-only at construction.

    Per doc-19:232-233 AC6 *"Human-facing dashboard/Slack output
    explains top findings without hiding evidence quality or omitted
    details."* the typed summary preserves the typed :attr:`severity`
    + :attr:`confidence` + :attr:`estimated_lost_hours` fields so the
    Slack rendering can rank findings without losing the audit-trail
    surface.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`pydantic.ValidationError` rather than being silently
    absorbed.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives (``str`` / ``float`` /
    ``Literal``).
    """

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str
    """The Slice 16 :attr:`GovernanceFinding.idempotency_key` (refs-
    only identity surface for the finding row; the Slack rendering
    surfaces the id; consumers drill-through via the dashboard which
    holds the page_refs surface)."""

    kind: FindingKind
    """The Slice 16 :data:`FindingKind` Literal (14 values per
    doc-16:63-78). Typed REUSE of the Slice 16 Literal -- NOT
    redefined."""

    class_name: str
    """The Slice 16 :attr:`GovernanceFinding.class_name` (the
    canonical fine-grained class name for the finding rule that
    fired)."""

    severity: FindingSeverity
    """The Slice 16 :data:`FindingSeverity` Literal (5 values per
    doc-16:62). Typed REUSE -- NOT redefined."""

    confidence: float
    """The Slice 16 :attr:`GovernanceFinding.confidence` (per doc-16
    confidence score in 0.0-1.0 range; the typed Slice 16 field
    validator enforces the range upstream)."""

    estimated_lost_hours: float | None
    """The Slice 16 :attr:`GovernanceFinding.estimated_lost_hours`
    (``None`` if the finding does not estimate lost hours). Per
    doc-19:190-191 the Slack digest ranks findings by severity +
    confidence + lost-hours estimate + recency; the typed surface
    preserves the lost-hours estimate so the ranking is
    reproducible."""


# --- Typed Block Kit shapes (per Slack Block Kit API) ----------------------


class SlackBlockKitBlock(BaseModel):
    """Typed Slack Block Kit block shape.

    Per the Slack Block Kit API (https://api.slack.com/block-kit) each
    block has a ``type`` field (e.g. ``header`` / ``section`` /
    ``context`` / ``divider``) + a typed ``text`` payload + optional
    ``fields`` list. The typed shape here is a minimal projection of
    the Slack Block Kit API sufficient for governance Slack digests
    (the renderer emits ``header`` + ``section`` + ``context`` +
    ``divider`` blocks; advanced blocks like ``actions`` / ``image``
    are out of scope for governance reporting).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`pydantic.ValidationError` rather than being silently
    absorbed.

    Per the Slack Block Kit API the typed surface uses snake_case
    field names (``block_type``); the JSON serialisation step renames
    ``block_type`` -> ``type`` for the actual Block Kit API payload
    (handled by :meth:`SlackBlockKitPayload.to_block_kit_json`).
    """

    model_config = ConfigDict(extra="forbid")

    block_type: Literal["header", "section", "context", "divider"]
    """The typed Slack Block Kit block type Literal. Per the
    governance digest design the renderer emits 4 block types
    (header for the title; section for findings/recommendations/
    replay; context for evidence quality + omitted counts; divider
    for visual separation)."""

    text: str | None = None
    """The typed text payload for the block. Per the Slack Block Kit
    API the text is plain text or markdown (``mrkdwn`` formatting);
    the typed surface carries the raw string + the
    :meth:`SlackBlockKitPayload.to_block_kit_json` step wraps it in
    the typed ``{"type": "mrkdwn", "text": ...}`` object the Slack
    API expects.

    ``None`` for blocks that don't carry text (e.g. ``divider``)."""

    fields: list[str] = Field(default_factory=list)
    """The typed list of field strings for the block (per the Slack
    Block Kit API ``section`` blocks may carry a typed ``fields`` list
    of up to 10 text fields, each rendered as a column).

    The typed surface accepts the empty list at construction (the
    typical case for ``header`` / ``divider`` blocks)."""


class SlackBlockKitPayload(BaseModel):
    """Typed bounded Slack Block Kit payload.

    Per doc-19:122-123 *"Slack digest: 40 KB serialized Block Kit
    payload and 5 top findings."* the typed payload carries the typed
    bounded blocks list + the typed dedupe_key + the typed corpus_id.

    Per doc-19:140-142 the typed :attr:`dedupe_key` is the typed
    :attr:`GovernanceSnapshot.snapshot_digest` verbatim; the rate-
    limiter caller uses the typed dedupe_key as the cache key.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`pydantic.ValidationError` rather than being silently
    absorbed.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives + a typed list of typed
    Block Kit block BaseModels.
    """

    model_config = ConfigDict(extra="forbid")

    blocks: list[SlackBlockKitBlock]
    """The typed list of typed Slack Block Kit blocks. Per the Slack
    Block Kit API the list may contain up to 50 blocks; the
    governance digest typically emits 10-20 blocks (header +
    metadata + per-finding + per-recommendation + per-replay-result
    + footer)."""

    dedupe_key: str
    """The typed dedupe key per doc-19:140-142. Set to the typed
    :attr:`GovernanceSnapshot.snapshot_digest` verbatim; same snapshot
    -> same dedupe_key. The Slack renderer enforces ``dedupe_key ==
    snapshot_digest`` structurally via
    :meth:`GovernanceSlackRenderer.compute_dedupe_key`.

    Per doc-19:218 *"Report generation is reproducible for the same
    corpus id."* the typed dedupe_key is the reproducibility axis:
    same corpus id + same inputs -> same snapshot_digest -> same
    dedupe_key. Per doc-19:201-202 *"Slack digest dedupes repeated
    identical governance snapshots by ``snapshot_digest`` and emits
    material updates when the digest changes."*"""

    corpus_id: str
    """The typed corpus identifier the Slack payload grounds on
    (mirrors the typed :attr:`GovernanceSnapshot.corpus_id`
    verbatim)."""

    def to_block_kit_json(self) -> str:
        """Serialise the typed payload to the Slack Block Kit JSON
        format the Slack API expects.

        Per the Slack Block Kit API the JSON payload is::

            {
              "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "..."}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "..."}},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": "..."}]},
                {"type": "divider"}
              ]
            }

        The typed surface preserves snake_case field names + nested
        Block Kit text objects; this helper renames + wraps the
        typed shape to match the Slack API format.

        Per doc-19:122-123 the serialised JSON is bounded by the 40
        KB byte budget; the byte count is measured via
        ``len(self.to_block_kit_json().encode("utf-8"))``.

        This is a READ-ONLY projection -- it does NOT mutate the
        typed payload; callers MAY invoke it repeatedly without
        side-effects.
        """

        block_kit_blocks: list[dict[str, Any]] = []
        for block in self.blocks:
            block_dict: dict[str, Any] = {"type": block.block_type}
            if block.block_type == "header" and block.text is not None:
                # Header blocks use plain_text per the Slack API.
                block_dict["text"] = {
                    "type": "plain_text",
                    "text": block.text,
                }
            elif block.block_type == "section" and block.text is not None:
                # Section blocks use mrkdwn per the Slack API.
                block_dict["text"] = {
                    "type": "mrkdwn",
                    "text": block.text,
                }
                if block.fields:
                    block_dict["fields"] = [
                        {"type": "mrkdwn", "text": field}
                        for field in block.fields
                    ]
            elif block.block_type == "context" and block.text is not None:
                # Context blocks use elements per the Slack API.
                block_dict["elements"] = [
                    {"type": "mrkdwn", "text": block.text}
                ]
            # divider blocks carry only the type field.
            block_kit_blocks.append(block_dict)
        return json.dumps(
            {"blocks": block_kit_blocks},
            sort_keys=True,
            separators=(",", ":"),
        )


# --- SlackRenderInputs (typed inputs; doc-19:155 step 4) -------------------


class SlackRenderInputs(BaseModel):
    """Typed bundle of all inputs the
    :meth:`GovernanceSlackRenderer.render` method consumes.

    Per doc-19:155 step 4 + doc-19:140-142 + doc-19:122-123 the inputs
    carry the typed Slice 19 2nd sub-slice :class:`SnapshotAPIResult`
    source (the typed bounded read the Slack renderer projects) + the
    optional caller-side display budget overrides (independent from
    the typed snapshot API budgets; the typed surface lets callers cap
    the Slack surface separately from the snapshot surface, e.g. the 5
    top findings cap per doc-19:122-123) + the optional dedupe-cache
    override (the typed ``set[str]`` of recently-emitted dedupe keys
    the rate-limiter caller MAY pass in to suppress dedupe-hit
    rendering at the source).

    The Slack renderer does NOT itself fetch from the database -- it
    is a pure typed projection over the typed inputs (the caller owns
    the bounded-read transaction via the Slice 19 2nd sub-slice
    :class:`GovernanceSnapshotAPI` upstream; the Slack renderer owns
    the typed Block Kit projection + 40 KB budget enforcement +
    dedupe-decision derivation).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`pydantic.ValidationError` rather than being silently
    absorbed.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives (``int`` / ``set[str]`` /
    ``SnapshotAPIResult``).
    """

    model_config = ConfigDict(extra="forbid")

    source: SnapshotAPIResult
    """The typed Slice 19 2nd sub-slice :class:`SnapshotAPIResult` the
    Slack renderer projects. Per the no-second-source-of-truth
    discipline the typed source is the Slice 19 2nd sub-slice typed
    result verbatim; the Slack renderer does NOT redefine the typed
    snapshot or gap shape.

    If :attr:`SnapshotAPIResult.snapshot` is ``None`` (structural
    snapshot-API failure) the Slack renderer returns
    :attr:`SlackRenderResult.decision` =``"upstream_missing"`` +
    emits a typed :class:`SlackRenderGap` with
    ``reason="upstream_snapshot_missing"`` + the upstream gap findings
    are PROPAGATED verbatim to the
    :attr:`SlackRenderResult.gap_findings` list (the renderer does
    not swallow upstream errors)."""

    max_top_findings: int = 5
    """Per doc-19:122-123 *"Slack digest: 40 KB serialized Block Kit
    payload and 5 top findings."* the typed default is ``5`` top
    findings. The renderer caps the typed findings list at this value
    BEFORE attempting the 40 KB budget truncation; if the resulting
    payload still exceeds 40 KB, the findings list is further
    truncated by the budget enforcement step.

    Intentionally tighter than the Slice 19 2nd sub-slice 20-finding
    snapshot cap (Slack is a notification surface; the dashboard is
    the deep-dive surface)."""

    max_payload_bytes: int = 40_960
    """Per doc-19:122-123 *"Slack digest: 40 KB serialized Block Kit
    payload..."* the typed default is ``40 960`` bytes (40 KB) for
    the serialised Block Kit JSON payload.

    The typed surface accepts any positive integer (the rate-limiter
    caller MAY override to a smaller value for testing or for a more
    aggressive truncation policy)."""

    recently_emitted_dedupe_keys: set[str] = Field(default_factory=set)
    """The typed set of dedupe keys recently emitted by the rate-
    limiter caller. Per doc-19:155 *"with dedupe and rate limiting
    inherited from Slice 10 patterns"* + doc-19:140-142 the dedupe
    key is the typed :attr:`GovernanceSnapshot.snapshot_digest`
    verbatim.

    If the typed
    :attr:`SnapshotAPIResult.snapshot.snapshot_digest` is in this set,
    the renderer returns
    :attr:`SlackRenderResult.decision` =``"suppressed_dedupe"`` +
    payload is STILL constructed (so the rate-limiter caller MAY
    inspect the payload for debugging / metrics) but the caller MUST
    NOT deliver the payload to Slack to avoid duplicate
    notifications.

    The renderer does NOT itself persist the cache -- the rate-
    limiter caller (typically the Slice 19 6th sub-slice report
    artifact writer or a dedicated Slack outbox) owns the typed
    cache + the rate-limit policy.

    Defaults to the empty set (no dedupe suppression; every render
    emits a fresh payload)."""


# --- SlackRenderPayload (typed bounded Slack payload; doc-19:140-142) ------


class SlackRenderPayload(BaseModel):
    """Typed bounded Slack render payload per doc-19:140-142 +
    doc-19:122-123.

    Per doc-19:140-142 *"Slack digest with dedupe key from
    ``snapshot_digest``..."* + doc-19:122-123 *"Slack digest: 40 KB
    serialized Block Kit payload and 5 top findings."* the typed
    payload carries:

    * The typed :attr:`payload` = the typed bounded
      :class:`SlackBlockKitPayload` (carries the typed Block Kit
      blocks + the typed dedupe_key + the typed corpus_id).
    * The typed :attr:`dedupe_key` (mirrors
      :attr:`SlackBlockKitPayload.dedupe_key` for callers that want
      to read the dedupe_key without descending into the typed Block
      Kit payload).
    * The typed :attr:`corpus_id` + :attr:`generated_at` (identity
      surface).
    * The typed :attr:`completeness` + :attr:`evidence_quality` +
      :attr:`truncated` + :attr:`omitted_counts` (the AC2 + AC6
      enforcement triple).
    * The typed :attr:`serialized_bytes` (the byte count of the
      typed Block Kit JSON payload; bounded by
      :attr:`SlackRenderInputs.max_payload_bytes`).
    * The typed :attr:`display_only` AC2 flag (``True`` iff the
      payload is display-only per doc-19:225-226 + doc-19:128-131).
    * The typed :attr:`top_findings` (the bounded typed finding
      summaries the renderer projected onto the Block Kit blocks;
      preserved for callers that want to inspect the typed surface
      without parsing the Block Kit JSON).

    Per doc-19:224 AC1 the typed surface is bounded (40 KB byte
    budget; per-list truncation), reproducible (``dedupe_key`` =
    ``snapshot_digest``), evidence-cited (typed summaries carry
    identity-surface refs), and structured first (typed Pydantic
    BaseModel; not prose).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`pydantic.ValidationError` rather than being silently
    absorbed.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives.
    """

    model_config = ConfigDict(extra="forbid")

    payload: SlackBlockKitPayload
    """The typed bounded :class:`SlackBlockKitPayload` (the actual
    Block Kit blocks + dedupe_key + corpus_id). Per doc-19:122-123
    the typed surface is bounded by the 40 KB byte budget; the
    typed :attr:`serialized_bytes` field carries the actual byte
    count."""

    dedupe_key: str
    """The typed dedupe key per doc-19:140-142 (mirrors
    :attr:`payload.dedupe_key`). Set to the typed
    :attr:`GovernanceSnapshot.snapshot_digest` verbatim."""

    corpus_id: str
    """The typed corpus identifier (mirrors :attr:`payload.corpus_id`)."""

    snapshot_version: str
    """The typed snapshot version string (mirrors the typed
    :attr:`GovernanceSnapshot.snapshot_version` verbatim)."""

    generated_at: datetime
    """The typed UTC timestamp the Slack renderer generated the
    payload at (DIFFERENT from the typed
    :attr:`GovernanceSnapshot.generated_at` -- the snapshot was
    generated by the Slice 19 2nd sub-slice typed API; the Slack
    payload is generated by THIS Slice 19 4th sub-slice renderer; the
    typed surface preserves both timestamps so consumers can detect
    stale Slack renders against fresh snapshots)."""

    snapshot_generated_at: datetime
    """The typed UTC timestamp the upstream Slice 19 2nd sub-slice
    typed API generated the snapshot at (= the typed
    :attr:`GovernanceSnapshot.generated_at` verbatim)."""

    completeness: CompletenessState
    """The typed Slice 13a :data:`CompletenessState` Literal (mirrors
    the typed :attr:`GovernanceSnapshot.completeness` verbatim). Typed
    REUSE of the Slice 13a shared Literal per doc-13a:285-287 step 9
    -- NOT redefined."""

    evidence_quality: EvidenceQuality
    """The typed Slice 13a :data:`EvidenceQuality` Literal (mirrors
    the typed :attr:`GovernanceSnapshot.evidence_quality` verbatim).
    Typed REUSE of the Slice 13a shared Literal per doc-13a:285-287
    step 9 -- NOT redefined.

    Per doc-19:232-233 AC6 the evidence-quality field is REQUIRED on
    every Slack payload so the rendering surface cannot omit it; the
    typed surface enforces the presence requirement at construction
    (the field has no default)."""

    truncated: bool
    """``True`` if the typed payload's display lists have been
    truncated at ANY level (upstream snapshot OR Slack renderer's 5-
    top-findings cap OR Slack renderer's 40 KB budget enforcement)
    per the doc-19:128-131 binding; the typed :attr:`omitted_counts`
    carries the per-list truncation counts."""

    omitted_counts: dict[str, int]
    """The typed dict of omitted-row counts by typed list name. The
    Slack renderer ADDS its own per-list display truncation count
    (from the 5-top-findings cap + 40 KB budget enforcement) to the
    upstream :attr:`GovernanceSnapshot.omitted_counts` so multi-level
    truncation is preserved per the doc-19:128-131 binding.

    Per doc-19:232-233 AC6 *"Human-facing dashboard/Slack output
    explains top findings without hiding evidence quality or omitted
    details."* the typed omitted-counts is REQUIRED on every Slack
    payload + the Block Kit payload carries an omitted-counts text
    block so the surface visibly explains the omissions."""

    serialized_bytes: int
    """The byte count of the serialised Block Kit JSON payload. Per
    doc-19:122-123 the typed surface MUST be bounded by the typed
    :attr:`SlackRenderInputs.max_payload_bytes` (default 40 960
    bytes); this field exposes the actual byte count so the rate-
    limiter caller can verify the budget enforcement."""

    display_only: bool
    """The typed AC2 enforcer flag per doc-19:225-226 + doc-19:128-131.

    ``True`` iff the payload is display-only (per doc-19:128-131
    *"...without those refs the response is display-only and cannot
    feed acceptance, recommendations, policy guidance, or task-execute
    context."*); ``False`` iff the payload is authoritative.

    Per the typed contract derived from doc-19:128-131 +
    doc-19:225-226 AC2 the flag is ``True`` iff:

    * ``(truncated=True AND no page_refs)`` -- truncated payload
      without exact page refs is display-only per doc-19:128-131.
      The Slack renderer DOES NOT carry the typed page_refs list
      (Slack is a notification surface; the dashboard is the deep-
      dive surface with page_refs); per the cross-surface
      consistency the Slack payload reports the typed display_only
      flag with the underlying snapshot's page_refs status.
    * ``(completeness in {"preview_only", "unavailable"})`` --
      preview/unavailable evidence per doc-13a CompletenessState
      Literal is display-only per doc-19:225-226 AC2.

    The flag is ``False`` iff ``completeness in {"complete", "paged"}``
    AND ``(not truncated OR snapshot.page_refs exist)``."""

    top_findings: list[SlackFindingSummary] = Field(default_factory=list)
    """The typed bounded display-only finding summaries (truncated to
    the typed :attr:`SlackRenderInputs.max_top_findings` cap and
    further truncated by the 40 KB byte budget enforcement).

    Per doc-19:122-123 the typed surface caps at 5 by default; per
    doc-19:140-141 the Slack rendering surface preserves the typed
    finding identity surface so consumers can verify the typed
    payload matches the dashboard payload (cross-surface
    consistency)."""


# --- SlackRenderGap (typed gap projection; doc-19:184-194 + doc-14:242-243) -


class SlackRenderGap(BaseModel):
    """Typed governance-gap finding produced when the Slack renderer
    fails to render a :class:`SlackRenderPayload` structurally.

    Mirrors the Slice 19 3rd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_dashboard_view.DashboardViewGap`
    + Slice 19 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_snapshot_api.SnapshotAPIGap`
    + Slice 18 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_replay_loader.ReplayCorpusLoaderGap`
    + Slice 17 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.recommendation_builder.RecommendationBuilderEmissionGap`
    + Slice 16 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_finding_writer.FindingPersistenceGap`
    + Slice 15 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardPersistenceGap`
    + Slice 14 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    verbatim per the chunk-shape decision.

    Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED here
    per the governance-projection discipline) the gap finding is
    NON-blocking: the caller MUST NOT propagate it to the executor /
    checkpoint / merge-queue / resume code paths. The corresponding
    typed failure id :data:`SLACK_RENDERER_FAILURE_ID`
    (``governance_slack_renderer_failed``) registers under the EXISTING
    ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).
    """

    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["governance_slack_renderer_failed"]
    """Doc-19:184-194 + doc-19:191-192 + doc-14:192-201 -- the typed
    failure id. Registers under the EXISTING ``evidence_corruption``
    failure_class with NON-blocking routing per doc-14:242-243 +
    doc-19:184-194 + doc-19:191-192."""

    corpus_id: str
    """The corpus scope of the failed projection (typically the
    :attr:`SnapshotAPIResult.snapshot.corpus_id` if the upstream
    snapshot is present, otherwise ``"<unknown>"``)."""

    reason: str
    """Free-form gap reason. Documented values:

    * ``upstream_snapshot_missing`` -- the upstream Slice 19 2nd
      sub-slice :attr:`SnapshotAPIResult.snapshot` is ``None``
      (structural snapshot-API failure); the upstream gap findings
      are PROPAGATED to the typed
      :attr:`SlackRenderResult.gap_findings` list verbatim so the
      Slack renderer does NOT swallow upstream errors.
    * ``payload_construction_failed`` -- Pydantic ValidationError on
      the typed :class:`SlackRenderPayload` /
      :class:`SlackBlockKitPayload` construction.
    * ``summary_projection_failed`` -- Pydantic ValidationError on a
      typed summary BaseModel construction (e.g.
      :class:`SlackFindingSummary`).
    * ``dedupe_key_computation_failed`` -- structural failure
      computing the typed :attr:`dedupe_key` from the typed snapshot.
    * ``budget_exceeded`` -- even the empty Block Kit payload
      exceeded the 40 KB byte budget (structurally impossible under
      typical Block Kit headers but reported for completeness).
    * ``governance_snapshot_stale`` -- doc-19:186-187 edge-case
      (propagated from the upstream typed
      :class:`SnapshotAPIGap.reason`).
    * ``slack_delivery_failure`` -- doc-19:191-192 edge-case
      (informational; reported by upstream delivery layer).
    * ``active_workflow_pressure`` -- doc-19:193-194 edge-case
      (cached Slack render requested).

    The caller distinguishes via this string. Per the auto-memory
    ``feedback_no_silent_degradation`` rule the typed surface is
    free-form so the renderer can emit new reason strings without a
    typed-shape breaking change."""

    observed_at: datetime
    """ISO-8601 timestamp the renderer observed the gap (UTC, timezone-
    aware). Mirrors the Slice 19 3rd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_dashboard_view.DashboardViewGap.observed_at`
    contract verbatim."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding
    (e.g. the Pydantic ValidationError detail; the serialized byte
    count when budget_exceeded; the propagated upstream gap reason).
    Free-form per the doc-14:192-201 + Slice 14/15/16/17/18/19
    governance-finding precedent."""


# --- SlackRenderResult (typed result; doc-19:155 step 4) -------------------


class SlackRenderResult(BaseModel):
    """Typed bundle of all outputs the
    :meth:`GovernanceSlackRenderer.render` method produces.

    The bundle composes:

    * ``payload`` -- the typed :class:`SlackRenderPayload` the
      renderer emitted, OR ``None`` if the projection failed
      structurally (in which case the gap is recorded in
      :attr:`gap_findings`).
    * ``decision`` -- the typed :data:`DedupeDecision` Literal
      reporting whether the payload was ``emitted`` (fresh),
      ``suppressed_dedupe`` (cache hit), ``budget_exceeded`` (40 KB
      cap), or ``upstream_missing`` (upstream snapshot is None).
    * ``gap_findings`` -- the list of typed :class:`SlackRenderGap`
      records emitted when a projection step fails structurally OR
      when an upstream gap is propagated.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.
    """

    model_config = ConfigDict(extra="forbid")

    payload: SlackRenderPayload | None = None
    """The typed :class:`SlackRenderPayload` the renderer emitted, OR
    ``None`` if the projection failed structurally.

    Per the doc-19:155 step 4 contract the renderer emits the typed
    payload when the upstream snapshot is present + the typed
    projection succeeds + the 40 KB budget is respected; on
    structural failure the payload is ``None`` + the gap finding is
    recorded in :attr:`gap_findings`. On upstream gap propagation
    the payload is ``None`` + the upstream gap findings are
    propagated to :attr:`gap_findings`.

    On dedupe-suppression (cache hit) the payload IS STILL emitted
    (so the rate-limiter caller MAY inspect the payload for
    debugging / metrics) but :attr:`decision` reports
    ``"suppressed_dedupe"`` so the caller knows NOT to deliver the
    payload to Slack to avoid duplicate notifications."""

    decision: DedupeDecision  # type: ignore[valid-type]
    """The typed :data:`DedupeDecision` Literal reporting the typed
    dedupe-suppression decision. See :data:`DedupeDecision` for the
    full Literal range + per-value semantics."""

    gap_findings: list[SlackRenderGap] = Field(default_factory=list)
    """The list of typed :class:`SlackRenderGap` records emitted
    when a projection step fails structurally OR when an upstream
    gap is propagated.

    The list is typically empty (no gaps fired for a healthy
    payload). On structural failure the list contains exactly ONE
    gap record + :attr:`payload` is ``None``. On upstream gap
    propagation the list contains the propagated upstream gap(s) +
    :attr:`payload` is ``None``."""


# --- GovernanceSlackRenderer (the Slack renderer class; doc-19:155 step 4) -


class GovernanceSlackRenderer:
    """The typed Slack renderer class (doc-19:155 step 4).

    Per *"Add Slack rendering with dedupe and rate limiting inherited
    from Slice 10 patterns."* + doc-19:140-142 *"Slack digest with
    dedupe key from ``snapshot_digest``..."* + doc-19:122-123 *"Slack
    digest: 40 KB serialized Block Kit payload and 5 top findings."*
    the renderer consumes the typed :class:`SlackRenderInputs` (which
    carries the typed Slice 19 2nd sub-slice
    :class:`SnapshotAPIResult`) and emits a typed
    :class:`SlackRenderPayload` record.

    **Read-only / display-only projection (per doc-19:155 + the
    governance prompt § "Non-Negotiables").** The renderer is a
    READ-ONLY typed projection over the typed Slice 19 2nd sub-slice
    :class:`SnapshotAPIResult`. The renderer does NOT hydrate artifact
    bodies -- the typed summaries carry only the typed identity-surface
    fields per the Slice 16 contract.

    **dedupe_key = snapshot_digest discipline (per doc-19:140-142).**
    The typed :attr:`SlackRenderPayload.dedupe_key` is the typed
    :attr:`GovernanceSnapshot.snapshot_digest` verbatim. The
    :meth:`compute_dedupe_key` helper returns the typed
    snapshot_digest directly; the typed surface enforces ``dedupe_key
    == snapshot_digest`` structurally (no second source of truth for
    the dedupe key).

    Per doc-19:140-142 *"...so material changes in evidence quality,
    replay confidence, omitted detail counts, or implementation-
    deviation summaries are not suppressed."* the dedupe key derives
    from the typed snapshot_digest (per the Slice 19 1st sub-slice
    :func:`compute_governance_snapshot_digest` canonical-JSON +
    SHA-256 discipline) which INCLUDES all the listed fields; material
    changes to any of those fields produce a different digest and
    thus a different dedupe key (cache miss; fresh notification).

    **40 KB budget enforcement (per doc-19:122-123).** The renderer:

    1. Caps the typed findings list at
       :attr:`SlackRenderInputs.max_top_findings` (default 5 per
       doc-19:122-123).
    2. Constructs the typed Block Kit payload with the typed bounded
       summaries.
    3. Serialises the typed payload to JSON to measure the byte
       count.
    4. If the serialised byte count exceeds the budget, the
       renderer progressively truncates the bounded summary lists
       (findings first, then recommendations text, then replay
       results text) until the serialised payload fits within
       budget OR all summaries are dropped.
    5. If even the empty payload exceeds budget (structurally
       impossible under typical Block Kit headers but reported for
       completeness), the renderer returns
       :attr:`SlackRenderResult.decision` =``"budget_exceeded"`` + a
       typed :class:`SlackRenderGap` finding.

    **Slice 10 dedupe + rate-limiting pattern INHERITED structurally.**
    Per doc-19:155 the dedupe + rate-limiting pattern is INHERITED
    from Slice 10 -- NOT direct calls into Slice 10 writers (READ-
    ONLY only). The typed
    :attr:`SlackRenderInputs.recently_emitted_dedupe_keys` cache
    override is the typed pass-in API the rate-limiter caller uses to
    suppress dedupe-hit rendering at the source; the renderer
    reports the typed :data:`DedupeDecision` Literal on the typed
    :attr:`SlackRenderResult.decision`.

    **Fail-closed discipline (per auto-memory
    ``feedback_no_silent_degradation``).** The :meth:`render` method
    NEVER raises a failure to the caller. Any structural failure
    projects onto a typed :class:`SlackRenderGap` finding emitted on
    the :attr:`SlackRenderResult.gap_findings` list. The
    corresponding typed failure id :data:`SLACK_RENDERER_FAILURE_ID`
    (``governance_slack_renderer_failed``) registers under the
    EXISTING ``evidence_corruption`` failure_class with the EXISTING
    NON-blocking RouteAction ``retry_governance_projection`` (REUSED
    from Slice 14 2nd sub-slice; NOT a new route action).

    **Activation-authority boundary (doc-19:348-349 AC).** The
    renderer has TWO public methods (:meth:`render` +
    :meth:`compute_dedupe_key`) and NO mutation methods on any of its
    typed shapes. The renderer does NOT extend the Slice 10c-1
    ``CONTROL_PLANE_WRITER_METHODS`` set.

    The renderer is stateless (no instance state beyond construction);
    callers MAY reuse a single instance across multiple snapshots.
    """

    def render(self, inputs: SlackRenderInputs) -> SlackRenderResult:
        """Render the typed :class:`SlackRenderPayload` from the
        typed inputs.

        Per doc-19:155 step 4 + doc-19:140-142 + doc-19:122-123 the
        method:

        1. Validates the upstream snapshot is present (the typed
           :attr:`SnapshotAPIResult.snapshot` is not ``None``); if
           absent, returns :attr:`decision` =``"upstream_missing"`` +
           emits a typed :class:`SlackRenderGap` + PROPAGATES the
           upstream gap findings verbatim.
        2. Computes the typed dedupe_key = typed
           :attr:`GovernanceSnapshot.snapshot_digest` verbatim via
           :meth:`compute_dedupe_key`.
        3. Caps the typed findings list at
           :attr:`SlackRenderInputs.max_top_findings` (default 5 per
           doc-19:122-123).
        4. Projects the typed Slice 16 :class:`GovernanceFinding`
           rows onto the typed display-only summary BaseModels
           (:class:`SlackFindingSummary`).
        5. Constructs the typed Block Kit payload with header +
           metadata blocks + per-finding blocks + per-recommendation
           text + per-replay-result text + omitted-counts block +
           display-only notice if AC2 flag is True.
        6. Enforces the 40 KB byte budget on the serialised JSON
           payload; progressively truncates if needed.
        7. Checks the typed
           :attr:`SlackRenderInputs.recently_emitted_dedupe_keys`
           cache; if the typed dedupe_key is a cache hit, returns
           :attr:`decision` =``"suppressed_dedupe"`` + STILL emits
           the typed payload (so the caller MAY inspect it).
        8. Sets the typed AC2 :attr:`display_only` flag from
           ``truncated`` + ``page_refs`` + ``completeness`` per
           doc-19:225-226 + doc-19:128-131.
        9. Constructs the typed :class:`SlackRenderPayload` record.
        10. Records projection failures in
            :attr:`SlackRenderResult.gap_findings` per
            ``feedback_no_silent_degradation``.

        Per the auto-memory ``feedback_no_silent_degradation`` rule
        the method NEVER raises on input -- structural failures
        produce typed gap findings.
        """

        gap_findings: list[SlackRenderGap] = []

        snapshot = inputs.source.snapshot
        if snapshot is None:
            # Upstream snapshot is missing (Slice 19 2nd sub-slice
            # snapshot API failed structurally). PROPAGATE the upstream
            # gap findings + emit our own gap finding so the rate-
            # limiter caller sees both layers of the failure. Return
            # decision=upstream_missing so the rate-limiter caller
            # knows NOT to deliver.
            corpus_id = self._corpus_id_from_upstream_gaps(
                inputs.source.gap_findings
            )
            gap_findings.append(
                SlackRenderGap(
                    failure_id=SLACK_RENDERER_FAILURE_ID,
                    corpus_id=corpus_id,
                    reason="upstream_snapshot_missing",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "upstream_gap_count": len(
                            inputs.source.gap_findings
                        ),
                    },
                )
            )
            # Propagate upstream gap reasons verbatim by mirroring each
            # upstream SnapshotAPIGap onto a typed SlackRenderGap
            # (refs-only; we copy the upstream reason + observed_at +
            # corpus_id verbatim so the rate-limiter caller sees the
            # same surface as the upstream caller).
            for upstream_gap in inputs.source.gap_findings:
                gap_findings.append(
                    SlackRenderGap(
                        failure_id=SLACK_RENDERER_FAILURE_ID,
                        corpus_id=upstream_gap.corpus_id,
                        reason=upstream_gap.reason,
                        observed_at=upstream_gap.observed_at,
                        evidence_payload={
                            "propagated_from": "snapshot_api_gap",
                            "upstream_failure_id": upstream_gap.failure_id,
                        },
                    )
                )
            return SlackRenderResult(
                payload=None,
                decision="upstream_missing",
                gap_findings=gap_findings,
            )

        # Compute the typed dedupe_key = snapshot_digest verbatim per
        # doc-19:140-142. The compute_dedupe_key helper is the single
        # source of truth for the dedupe-key derivation.
        try:
            dedupe_key = self.compute_dedupe_key(snapshot)
        except (ValueError, TypeError) as exc:
            gap_findings.append(
                SlackRenderGap(
                    failure_id=SLACK_RENDERER_FAILURE_ID,
                    corpus_id=snapshot.corpus_id,
                    reason="dedupe_key_computation_failed",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                    },
                )
            )
            return SlackRenderResult(
                payload=None,
                decision="budget_exceeded",
                gap_findings=gap_findings,
            )

        # Cap the typed findings list at the 5-top-findings cap per
        # doc-19:122-123. Project each row onto the typed display-only
        # SlackFindingSummary.
        try:
            findings_summaries, findings_omitted = (
                self._project_findings(
                    snapshot.top_findings,
                    inputs.max_top_findings,
                )
            )
        except (ValidationError, ValueError, TypeError) as exc:
            gap_findings.append(
                SlackRenderGap(
                    failure_id=SLACK_RENDERER_FAILURE_ID,
                    corpus_id=snapshot.corpus_id,
                    reason="summary_projection_failed",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                    },
                )
            )
            return SlackRenderResult(
                payload=None,
                decision="budget_exceeded",
                gap_findings=gap_findings,
            )

        # Compute multi-level omitted-counts (upstream snapshot counts
        # + this renderer's display truncation counts).
        upstream_findings = snapshot.omitted_counts.get("findings", 0)
        upstream_recommendations = snapshot.omitted_counts.get(
            "recommendations", 0
        )
        upstream_replay_results = snapshot.omitted_counts.get(
            "replay_results", 0
        )
        upstream_page_refs = snapshot.omitted_counts.get("page_refs", 0)
        omitted_counts: dict[str, int] = {
            "findings": upstream_findings + findings_omitted,
            "recommendations": upstream_recommendations,
            "replay_results": upstream_replay_results,
            "page_refs": upstream_page_refs,
        }

        # Truncated flag is True iff ANY dimension was truncated at
        # ANY level (this renderer, upstream snapshot, or upstream
        # bounded reader).
        truncated_pre_budget: bool = any(
            count > 0 for count in omitted_counts.values()
        ) or snapshot.truncated

        # AC2 display_only derivation per doc-19:225-226 +
        # doc-19:128-131:
        # - True iff (truncated AND no upstream page_refs) OR
        #   (completeness in {"preview_only", "unavailable"}).
        # - False iff (completeness in {"complete", "paged"}) AND
        #   (not truncated OR upstream page_refs exist).
        display_only: bool = self._derive_display_only(
            completeness=snapshot.completeness,
            truncated=truncated_pre_budget,
            page_refs=snapshot.page_refs,
        )

        # Build the typed Block Kit payload; iteratively truncate
        # findings to fit the 40 KB byte budget.
        try:
            (
                block_kit_payload,
                final_findings,
                budget_findings_dropped,
                serialized_bytes,
                budget_exceeded,
            ) = self._build_block_kit_with_budget(
                snapshot=snapshot,
                dedupe_key=dedupe_key,
                findings_summaries=findings_summaries,
                omitted_counts=omitted_counts,
                truncated_pre_budget=truncated_pre_budget,
                display_only=display_only,
                max_payload_bytes=inputs.max_payload_bytes,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            gap_findings.append(
                SlackRenderGap(
                    failure_id=SLACK_RENDERER_FAILURE_ID,
                    corpus_id=snapshot.corpus_id,
                    reason="payload_construction_failed",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                    },
                )
            )
            return SlackRenderResult(
                payload=None,
                decision="budget_exceeded",
                gap_findings=gap_findings,
            )

        if budget_exceeded:
            # Even the empty payload exceeded the byte budget;
            # structurally impossible under typical Block Kit headers
            # but reported for completeness.
            gap_findings.append(
                SlackRenderGap(
                    failure_id=SLACK_RENDERER_FAILURE_ID,
                    corpus_id=snapshot.corpus_id,
                    reason="budget_exceeded",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "serialized_bytes": serialized_bytes,
                        "max_payload_bytes": inputs.max_payload_bytes,
                    },
                )
            )
            return SlackRenderResult(
                payload=None,
                decision="budget_exceeded",
                gap_findings=gap_findings,
            )

        # Update truncated + omitted_counts if the budget enforcement
        # dropped additional findings.
        if budget_findings_dropped > 0:
            omitted_counts["findings"] = (
                omitted_counts["findings"] + budget_findings_dropped
            )
        truncated_final: bool = truncated_pre_budget or (
            budget_findings_dropped > 0
        )

        # Re-derive display_only with the final truncated flag.
        display_only_final: bool = self._derive_display_only(
            completeness=snapshot.completeness,
            truncated=truncated_final,
            page_refs=snapshot.page_refs,
        )

        # Determine the typed dedupe decision: if the dedupe_key is in
        # the recently-emitted cache, the caller MUST NOT deliver
        # (suppressed_dedupe); otherwise the payload may be delivered
        # (emitted).
        if dedupe_key in inputs.recently_emitted_dedupe_keys:
            decision: str = "suppressed_dedupe"
        else:
            decision = "emitted"

        # Construct the typed SlackRenderPayload record (per
        # doc-19:140-142 + doc-19:122-123 + the AC1/AC2/AC6/AC7
        # enforcement surface).
        try:
            payload = SlackRenderPayload(
                payload=block_kit_payload,
                dedupe_key=dedupe_key,
                corpus_id=snapshot.corpus_id,
                snapshot_version=snapshot.snapshot_version,
                generated_at=_utcnow(),
                snapshot_generated_at=snapshot.generated_at,
                completeness=snapshot.completeness,
                evidence_quality=snapshot.evidence_quality,
                truncated=truncated_final,
                omitted_counts=omitted_counts,
                serialized_bytes=serialized_bytes,
                display_only=display_only_final,
                top_findings=final_findings,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            gap_findings.append(
                SlackRenderGap(
                    failure_id=SLACK_RENDERER_FAILURE_ID,
                    corpus_id=snapshot.corpus_id,
                    reason="payload_construction_failed",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                    },
                )
            )
            return SlackRenderResult(
                payload=None,
                decision="budget_exceeded",
                gap_findings=gap_findings,
            )

        return SlackRenderResult(
            payload=payload,
            decision=decision,  # type: ignore[arg-type]
            gap_findings=gap_findings,
        )

    @staticmethod
    def compute_dedupe_key(snapshot: GovernanceSnapshot) -> str:
        """Compute the typed dedupe key for the typed snapshot per
        doc-19:140-142.

        Per *"Slack digest with dedupe key from ``snapshot_digest``,
        not only corpus id or top ids, so material changes in evidence
        quality, replay confidence, omitted detail counts, or
        implementation-deviation summaries are not suppressed."* the
        typed dedupe key IS the typed
        :attr:`GovernanceSnapshot.snapshot_digest` verbatim. The
        helper is the SINGLE source of truth for the typed dedupe-key
        derivation; the Slack renderer enforces ``dedupe_key ==
        snapshot_digest`` structurally by routing the dedupe key
        through this helper.

        Per doc-19:218 *"Report generation is reproducible for the
        same corpus id."* the typed dedupe key is reproducible because
        the typed snapshot_digest is reproducible (per the Slice 19
        1st sub-slice :func:`compute_governance_snapshot_digest`
        canonical-JSON + SHA-256 discipline).

        Per doc-19:201-202 *"Slack digest dedupes repeated identical
        governance snapshots by ``snapshot_digest`` and emits material
        updates when the digest changes."* the typed dedupe key
        derivation ensures material changes to any of the digest's
        logical inputs (corpus id + snapshot version + scorecard id +
        finding keys + recommendation keys + replay result ids/
        versions + omitted counts + evidence quality + completeness)
        produce a different dedupe key and thus a fresh notification.

        The helper is a static method (no instance state required);
        callers MAY invoke it directly on a typed
        :class:`GovernanceSnapshot` without constructing a renderer
        instance.
        """

        return snapshot.snapshot_digest

    # --- Bounded display-truncation + summary projection helpers ----

    @staticmethod
    def _project_findings(
        rows: list[GovernanceFinding], cap: int
    ) -> tuple[list[SlackFindingSummary], int]:
        """Truncate the typed findings list at the per-input cap +
        project each row onto the typed display-only summary.

        Per the LIMIT cap+1 discipline: if ``len(rows) > cap`` the
        method returns the first ``cap`` summaries + the truncated
        count ``len(rows) - cap``; otherwise returns the full list +
        ``0``.

        Per the refs-only contract the summary carries only the typed
        identity-surface fields (idempotency_key + kind + class_name +
        severity + confidence + estimated_lost_hours); the full
        Slice 16 :class:`GovernanceFinding` body is NOT carried in
        the Slack payload.
        """

        if cap < 0:
            # Defensive: a negative cap is treated as 0 (truncate
            # everything; informational rather than raising per
            # fail-closed discipline).
            return [], len(rows)
        if len(rows) > cap:
            truncated_rows = rows[:cap]
            omitted = len(rows) - cap
        else:
            truncated_rows = list(rows)
            omitted = 0
        summaries = [
            SlackFindingSummary(
                idempotency_key=row.idempotency_key,
                kind=row.kind,
                class_name=row.class_name,
                severity=row.severity,
                confidence=row.confidence,
                estimated_lost_hours=row.estimated_lost_hours,
            )
            for row in truncated_rows
        ]
        return summaries, omitted

    @staticmethod
    def _derive_display_only(
        *,
        completeness: CompletenessState,
        truncated: bool,
        page_refs: list[str],
    ) -> bool:
        """Derive the typed AC2 :attr:`SlackRenderPayload.display_only`
        flag from the typed completeness + truncated + page_refs.

        Per doc-19:225-226 AC2 + doc-19:128-131:

        * If ``completeness`` is ``"preview_only"`` OR
          ``"unavailable"`` the payload is display-only (preview
          evidence cannot be authoritative).
        * If ``truncated`` is ``True`` AND ``page_refs`` is empty
          the payload is display-only (truncated without exact refs
          is display-only per doc-19:128-131).
        * Otherwise the payload is NOT display-only (it is
          authoritative).

        The helper is the SINGLE source of truth for the AC2
        derivation; the Slack renderer enforces ``display_only ==
        _derive_display_only(...)`` structurally by routing the
        derivation through this helper.

        Note: the typed Slack payload itself does NOT carry the
        typed page_refs list (the dashboard surface owns the page-
        refs drilldown); the renderer derives display_only from the
        UNDERLYING SNAPSHOT's page_refs (per the cross-surface
        consistency contract).
        """

        if completeness in ("preview_only", "unavailable"):
            return True
        if truncated and not page_refs:
            return True
        return False

    @staticmethod
    def _corpus_id_from_upstream_gaps(
        upstream_gaps: list[SnapshotAPIGap],
    ) -> str:
        """Extract the corpus_id from the first upstream gap finding,
        or return ``"<unknown>"`` if no upstream gaps are present.

        Used when the upstream snapshot is missing + the Slack
        renderer needs a corpus_id for the typed
        :class:`SlackRenderGap` surface.
        """

        if upstream_gaps:
            return upstream_gaps[0].corpus_id
        return "<unknown>"

    @staticmethod
    def _format_finding_block(summary: SlackFindingSummary) -> str:
        """Format a typed :class:`SlackFindingSummary` as a Slack
        ``mrkdwn`` text string suitable for a Block Kit ``section``
        block.

        Per doc-19:232-233 AC6 + doc-19:190-191 the text surface
        includes the typed severity + confidence + lost-hours estimate
        + class name + idempotency key so the Slack rendering does
        not hide the audit-trail surface.
        """

        lost_hours = (
            f"{summary.estimated_lost_hours:.1f}h lost"
            if summary.estimated_lost_hours is not None
            else "(no lost-hours estimate)"
        )
        return (
            f"*[{summary.severity.upper()}]* `{summary.class_name}` "
            f"({summary.kind}) -- conf={summary.confidence:.2f}, "
            f"{lost_hours}\n_id_: `{summary.idempotency_key}`"
        )

    @staticmethod
    def _format_metadata_block(
        snapshot: GovernanceSnapshot,
    ) -> str:
        """Format the typed snapshot metadata as a Slack ``mrkdwn``
        text string for a Block Kit ``context`` block.

        Per doc-19:232-233 AC6 the metadata block ALWAYS includes the
        typed evidence_quality field so the Slack surface cannot omit
        it.
        """

        return (
            f"corpus=`{snapshot.corpus_id}` "
            f"version=`{snapshot.snapshot_version}` "
            f"quality=`{snapshot.evidence_quality}` "
            f"completeness=`{snapshot.completeness}`"
        )

    @staticmethod
    def _format_omitted_counts_block(
        omitted_counts: dict[str, int],
    ) -> str:
        """Format the typed omitted_counts dict as a Slack ``mrkdwn``
        text string for a Block Kit ``context`` block.

        Per doc-19:232-233 AC6 the omitted-counts block ALWAYS appears
        on the Slack payload so the surface visibly explains the
        omissions.
        """

        parts = [
            f"{key}: {value}"
            for key, value in sorted(omitted_counts.items())
        ]
        return "Omitted: " + ", ".join(parts) if parts else "Omitted: (none)"

    @staticmethod
    def _format_recommendations_block(
        recommendations: list[GovernancePolicyRecommendation],
    ) -> str:
        """Format the typed recommendations list as a Slack ``mrkdwn``
        text string for a Block Kit ``context`` block.

        Per doc-19:170-171 + doc-19:174-176 the surface is display-
        only / advisory-only; the renderer does NOT carry the policy
        artifact body, only the typed id + consumer + status.
        """

        if not recommendations:
            return "_Recommendations: none_"
        parts = [
            f"`{rec.recommendation_id}` -> "
            f"{rec.consumer}/{rec.status} (conf={rec.confidence:.2f})"
            for rec in recommendations
        ]
        return "*Recommendations:* " + " | ".join(parts)

    @staticmethod
    def _format_replay_results_block(
        results: list[CounterfactualResult],
    ) -> str:
        """Format the typed replay_results list as a Slack ``mrkdwn``
        text string for a Block Kit ``context`` block.

        Per doc-19:170-171 the surface is display-only; the renderer
        does NOT carry the full simulation trace.
        """

        if not results:
            return "_Replay results: none_"
        parts = [
            f"`{result.result_id}` -> "
            f"risk={result.estimated_risk_change} "
            f"(conf={result.confidence:.2f})"
            for result in results
        ]
        return "*Replay results:* " + " | ".join(parts)

    def _build_block_kit_payload(
        self,
        *,
        snapshot: GovernanceSnapshot,
        dedupe_key: str,
        findings_summaries: list[SlackFindingSummary],
        omitted_counts: dict[str, int],
        truncated: bool,
        display_only: bool,
    ) -> SlackBlockKitPayload:
        """Construct the typed :class:`SlackBlockKitPayload` from the
        typed snapshot + bounded summaries.

        Per doc-19:122-123 the typed payload is bounded by the 40 KB
        byte budget (enforced by
        :meth:`_build_block_kit_with_budget`); this helper only
        constructs the typed Block Kit blocks + delegates byte
        budget enforcement to the caller.
        """

        blocks: list[SlackBlockKitBlock] = []
        # Header: Governance Digest
        blocks.append(
            SlackBlockKitBlock(
                block_type="header",
                text=f"Governance Digest: {snapshot.corpus_id}",
            )
        )
        # Metadata context (corpus + version + evidence_quality +
        # completeness; AC6 required).
        blocks.append(
            SlackBlockKitBlock(
                block_type="context",
                text=self._format_metadata_block(snapshot),
            )
        )
        # Optional display-only notice if AC2 flag is True.
        if display_only:
            blocks.append(
                SlackBlockKitBlock(
                    block_type="context",
                    text=(
                        ":warning: *Display-only:* "
                        "preview/truncated; "
                        "see dashboard for exact refs."
                    ),
                )
            )
        # Divider before findings
        blocks.append(
            SlackBlockKitBlock(block_type="divider")
        )
        # Per-finding section blocks
        for summary in findings_summaries:
            blocks.append(
                SlackBlockKitBlock(
                    block_type="section",
                    text=self._format_finding_block(summary),
                )
            )
        # Divider before recommendations / replay summary
        blocks.append(
            SlackBlockKitBlock(block_type="divider")
        )
        # Recommendations context (compact list)
        blocks.append(
            SlackBlockKitBlock(
                block_type="context",
                text=self._format_recommendations_block(
                    snapshot.recommendations
                ),
            )
        )
        # Replay results context (compact list)
        blocks.append(
            SlackBlockKitBlock(
                block_type="context",
                text=self._format_replay_results_block(
                    snapshot.replay_results
                ),
            )
        )
        # Omitted counts context (AC6 required)
        blocks.append(
            SlackBlockKitBlock(
                block_type="context",
                text=self._format_omitted_counts_block(omitted_counts),
            )
        )
        return SlackBlockKitPayload(
            blocks=blocks,
            dedupe_key=dedupe_key,
            corpus_id=snapshot.corpus_id,
        )

    def _build_block_kit_with_budget(
        self,
        *,
        snapshot: GovernanceSnapshot,
        dedupe_key: str,
        findings_summaries: list[SlackFindingSummary],
        omitted_counts: dict[str, int],
        truncated_pre_budget: bool,
        display_only: bool,
        max_payload_bytes: int,
    ) -> tuple[
        SlackBlockKitPayload,
        list[SlackFindingSummary],
        int,
        int,
        bool,
    ]:
        """Construct the typed Block Kit payload + iteratively
        truncate findings to fit the 40 KB byte budget.

        Returns a 5-tuple:

        * ``payload`` -- the typed :class:`SlackBlockKitPayload`.
        * ``final_findings`` -- the typed bounded findings list the
          renderer actually included (after budget truncation).
        * ``budget_findings_dropped`` -- the count of findings the
          budget enforcement dropped (in addition to the pre-budget
          truncation).
        * ``serialized_bytes`` -- the actual byte count of the
          serialised payload.
        * ``budget_exceeded`` -- ``True`` iff even the empty payload
          exceeded the byte budget (structurally impossible under
          typical Block Kit headers).

        Per doc-19:122-123 the typed surface MUST be bounded by the
        typed :attr:`SlackRenderInputs.max_payload_bytes`; this helper
        is the structural enforcer of that contract.
        """

        # Try the full findings list first.
        current_findings = list(findings_summaries)
        budget_findings_dropped = 0

        while True:
            # Recompute omitted_counts to reflect the current
            # truncation level.
            current_omitted_counts = dict(omitted_counts)
            current_omitted_counts["findings"] = (
                omitted_counts["findings"] + budget_findings_dropped
            )
            current_truncated = (
                truncated_pre_budget or budget_findings_dropped > 0
            )
            current_display_only = self._derive_display_only(
                completeness=snapshot.completeness,
                truncated=current_truncated,
                page_refs=snapshot.page_refs,
            )
            payload = self._build_block_kit_payload(
                snapshot=snapshot,
                dedupe_key=dedupe_key,
                findings_summaries=current_findings,
                omitted_counts=current_omitted_counts,
                truncated=current_truncated,
                display_only=current_display_only,
            )
            serialized_bytes = len(
                payload.to_block_kit_json().encode("utf-8")
            )
            if serialized_bytes <= max_payload_bytes:
                # Fits within budget.
                return (
                    payload,
                    current_findings,
                    budget_findings_dropped,
                    serialized_bytes,
                    False,
                )
            # Over budget; try dropping one more finding.
            if not current_findings:
                # Even the empty payload exceeds budget; structurally
                # impossible under typical Block Kit headers but
                # reported for completeness.
                return (
                    payload,
                    current_findings,
                    budget_findings_dropped,
                    serialized_bytes,
                    True,
                )
            current_findings = current_findings[:-1]
            budget_findings_dropped += 1
