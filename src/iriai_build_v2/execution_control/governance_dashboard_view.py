"""Slice 19 third sub-slice -- READ-ONLY typed dashboard view that
projects governance snapshots onto a bounded display-only payload.

This module implements doc-19 § Refactoring Steps step 3 (line 152):
*"Add dashboard view that consumes governance snapshots only."* +
doc-19:170-171: *"Dashboard reads governance snapshots with bounded
fields and ETags; it does not resolve full evidence bodies by default.
The ETag seed is ``snapshot_digest``."*

It owns the typed dashboard-view projection surface:

* :data:`DASHBOARD_VIEW_FAILURE_ID` -- the typed failure id
  (``governance_dashboard_view_failed``) registered under the EXISTING
  ``evidence_corruption`` failure_class in
  :mod:`iriai_build_v2.workflows.develop.execution.failure_router` with
  the EXISTING NON-blocking :data:`RouteAction`
  ``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
  mirrors Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B + 4th +
  Slice 17 2nd-6th + Slice 18 2nd-7th + Slice 19 2nd sub-slice
  precedent verbatim; NOT a new failure_class; NOT a new route action).

* :class:`DashboardFindingSummary` -- the bounded display-only
  finding summary BaseModel (refs-only projection of the Slice 16
  :class:`GovernanceFinding` identity surface; no body hydration).

* :class:`DashboardRecommendationSummary` -- the bounded display-only
  recommendation summary BaseModel (refs-only projection of the
  Slice 17 :class:`GovernancePolicyRecommendation` identity surface).

* :class:`DashboardReplayResultSummary` -- the bounded display-only
  replay-result summary BaseModel (refs-only projection of the
  Slice 18 :class:`CounterfactualResult` identity surface).

* :class:`DashboardViewInputs` -- the typed bundle of inputs the
  :meth:`GovernanceDashboardView.render` method consumes (the
  caller-provided Slice 19 2nd sub-slice :class:`SnapshotAPIResult`
  source + optional caller-side display budget overrides).

* :class:`DashboardViewPayload` -- the typed bounded dashboard payload
  per doc-19:170-171 (``etag`` = ``snapshot_digest`` verbatim;
  ``corpus_id``; ``generated_at``; truncation/completeness/quality
  bounded fields; bounded display-only summaries; ``omitted_counts``;
  ``page_refs``; ``blocked_by``; ``display_only`` AC2 flag).

* :class:`DashboardViewGap` -- the typed gap finding emitted when the
  dashboard view fails to render structurally (mirrors
  :class:`SnapshotAPIGap` + Slice 14/15/16/17/18 governance-projection-
  gap shape verbatim per the chunk-shape decision).

* :class:`DashboardViewResult` -- the typed result BaseModel
  (``payload | None`` + ``gap_findings`` list).

* :class:`GovernanceDashboardView` -- the typed dashboard view class
  with the public projection method :meth:`render` + the public typed
  helper :meth:`compute_etag` (returns the typed snapshot_digest
  verbatim per doc-19:170-171 -- the ETag IS the snapshot_digest;
  no second source of truth).

**Bounded-reads + refs-only discipline (per governance prompt §
"Non-Negotiables").** The dashboard view is a READ-ONLY typed
projection over the typed Slice 19 2nd sub-slice :class:`SnapshotAPIResult`
+ the typed Slice 19 1st sub-slice :class:`GovernanceSnapshot` it
contains. The view does NOT hydrate artifact bodies -- the typed
summaries carry only the typed identity-surface fields per the Slice
16/17/18 contract (idempotency_key + class_name + severity +
confidence + estimated_lost_hours for findings; idempotency_key +
recommendation_id + consumer + status + confidence for recommendations;
result_id + result_version + scenario_id + confidence +
estimated_delta_hours for replay results). The typed page_refs
surface is the typed ``list[str]`` from
:attr:`GovernanceSnapshot.page_refs` verbatim per doc-19:81.

**Activation-authority boundary preserved (doc-19:348-349 AC).** The
:class:`GovernanceDashboardView` class has TWO public methods
(:meth:`render` + :meth:`compute_etag`) and NO mutation methods on any
of its typed shapes (no ``activate_`` / ``approve_`` / ``merge_`` /
``checkpoint_`` / ``mutate_`` / ``write_`` / ``persist_`` methods).
The view does NOT extend the Slice 10c-1
``CONTROL_PLANE_WRITER_METHODS`` set per doc-19:348-349
*"Supervisor/dashboard read-only contract preserved (no governance
writer extends the Slice 10c-1 ``CONTROL_PLANE_WRITER_METHODS``
set)."* The view does NOT mint ``dag-*`` execution-authority
artifact-key string literals.

**Doc-19 acceptance criteria enforcement (sub-slice axes):**

* **AC1** (doc-19:224) *"Reports are bounded, reproducible,
  evidence-cited, and structured first."* -- enforced by:
  * **Bounded**: the typed :attr:`DashboardViewPayload.omitted_counts`
    + :attr:`DashboardViewPayload.truncated` + the per-summary
    bounded display-only projection (no body hydration).
  * **Reproducible**: the typed :attr:`DashboardViewPayload.etag` is
    the typed :attr:`GovernanceSnapshot.snapshot_digest` verbatim per
    doc-19:170-171; same snapshot -> same etag.
  * **Evidence-cited**: the typed
    :attr:`DashboardViewPayload.page_refs` (typed list[str] mirroring
    :attr:`GovernanceSnapshot.page_refs`) + the typed summary
    idempotency-key surface.
  * **Structured first**: the typed Pydantic BaseModel surface
    (typed display-only summaries -- not prose).

* **AC2** (doc-19:225-226) *"Truncated or preview reports are never
  authoritative unless exact page refs and completeness metadata cover
  the consumer's required scope."* -- enforced by the typed
  :attr:`DashboardViewPayload.display_only` bool flag: ``True`` iff
  ``(truncated=True AND no page_refs)`` OR ``(completeness in
  {"preview_only", "unavailable"})``. The typed surface lets
  consumers detect display-only state at construction.

* **AC6** (doc-19:232-233) *"Human-facing dashboard/Slack output
  explains top findings without hiding evidence quality or omitted
  details."* -- enforced by the typed
  :attr:`DashboardViewPayload.evidence_quality` (Slice 13a REUSE) +
  :attr:`DashboardViewPayload.omitted_counts` + the typed summaries
  always being present in the typed payload (the dashboard rendering
  surface cannot omit them).

* **AC7** (doc-19:234) *"Reporting honors Slice 10 read-only and
  bounded-read guarantees."* -- enforced by the typed class having
  TWO public methods (:meth:`render` + :meth:`compute_etag`) + no
  mutation methods on any BaseModel + no ``dag-*`` artifact-key
  literals + no ``CONTROL_PLANE_WRITER_METHODS`` extension.

**Fail-closed discipline (per auto-memory
``feedback_no_silent_degradation``).** The :meth:`render` method
NEVER raises -- structural failures project onto typed
:class:`DashboardViewGap` finding(s) emitted on
:attr:`DashboardViewResult.gap_findings`. A SINGLE typed failure id
:data:`DASHBOARD_VIEW_FAILURE_ID` (``governance_dashboard_view_failed``)
covers the doc-19:184-194 edge-case rows (mirror of the Slice 17 6th
sub-slice ``consumer_read_api_failed`` + Slice 18 2nd sub-slice
``replay_corpus_or_scenario_load_failed`` + Slice 19 2nd sub-slice
``governance_snapshot_api_failed`` 1-failure-id-per-typed-API-class
precedent verbatim). The typed :class:`DashboardViewGap` shape carries
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
  for the typed source bundle the view consumes.
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

It is the **dashboard-projection layer** that subsequent Slice 19
sub-slices (Slack rendering / agent-context builder / report
artifacts) cite as the typed bounded-display contract for the
dashboard surface.

**Slice 13A invariant compliance.** The dashboard view consumes the
typed Slice 19 2nd sub-slice :class:`SnapshotAPIResult` which is itself
a typed bounded read over the cited Slice 16/17/18 evidence surface;
the dashboard view does NOT hydrate artifact bodies (page_refs surface
is refs-only per doc-19:81) and does NOT consume preview-only evidence
as execution authority (it is a READ-ONLY display projection).

References:

* Doc-19 § Refactoring Steps step 3 (line 152) -- *"Add dashboard view
  that consumes governance snapshots only."*
* Doc-19:170-171 -- *"Dashboard reads governance snapshots with
  bounded fields and ETags; it does not resolve full evidence bodies
  by default. The ETag seed is ``snapshot_digest``."*
* Doc-19:128-131 + doc-19:225-226 AC2 -- preview/display budgets;
  truncated snapshots without exact page refs are display-only.
* Doc-19:166-167 -- *"Governance reports are projections of governance
  rows."*
* Doc-19:184-194 -- edge-case rows mapped to typed gap reasons.
* Doc-19:218 -- *"Report generation is reproducible for the same
  corpus id."* (ETag = snapshot_digest = reproducible per
  doc-19:170-171.)
* Doc-19:234 + doc-19:348-349 AC -- read-only contract.
* Doc-14:242-243 -- governance-projection NON-blocking contract
  (inherited by every post-checkpoint governance projection
  observer; the dashboard view is also a post-checkpoint observer).
"""

from __future__ import annotations

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
    "DASHBOARD_VIEW_FAILURE_ID",
    # Bounded display-only summary BaseModels (refs-only).
    "DashboardFindingSummary",
    "DashboardRecommendationSummary",
    "DashboardReplayResultSummary",
    # Typed dashboard inputs / payload / gap / result.
    "DashboardViewInputs",
    "DashboardViewPayload",
    "DashboardViewGap",
    "DashboardViewResult",
    # The dashboard view class (doc-19:152 step 3).
    "GovernanceDashboardView",
]


# --- Typed failure id (doc-19:184-194 + doc-14:242-243 NON-BLOCKING) --------


DASHBOARD_VIEW_FAILURE_ID: Literal[
    "governance_dashboard_view_failed"
] = "governance_dashboard_view_failed"
"""Doc-19:184-194 + doc-14:242-243 -- the typed failure id the
governance dashboard view projects onto when a structural projection
step fails.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT
a new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd-6th + Slice 18 2nd-7th + Slice 19
2nd sub-slice precedent verbatim).

A SINGLE failure id covers ALL the doc-19:184-194 edge-case rows
(governance snapshot stale; missing line provenance; too many
findings; Slack delivery failure; active workflow pressure) per the
Slice 17 6th sub-slice ``consumer_read_api_failed`` + Slice 18 2nd
sub-slice ``replay_corpus_or_scenario_load_failed`` + Slice 19 2nd
sub-slice ``governance_snapshot_api_failed`` precedent (one typed
failure id per typed-API class). The typed :class:`DashboardViewGap`
shape carries the surface ``reason`` so consumers can distinguish
edge-case classes if needed.

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 19 pattern matches the Slice 14 + Slice 15 + Slice 16 + Slice
17 + Slice 18 + Slice 19 2nd sub-slice non-blocking governance
projection observer (the dashboard view is also a post-checkpoint
governance projection observer + per doc-19:170-171 dashboards read
snapshots with bounded fields + per doc-19:166-167 reports are
projections of governance rows -- never runtime policy authority).

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to dashboard view
failures.
"""


def _utcnow() -> datetime:
    """Return the current UTC timestamp (timezone-aware).

    Mirrors the Slice 19 2nd sub-slice
    :func:`~iriai_build_v2.execution_control.governance_snapshot_api._utcnow`
    + Slice 18 2nd sub-slice
    :func:`~iriai_build_v2.execution_control.counterfactual_replay_loader._utcnow`
    + Slice 17 4th sub-slice
    :func:`~iriai_build_v2.execution_control.decision_record_writer._utcnow`
    verbatim. Stdlib-only.
    """

    return datetime.now(timezone.utc)


# --- Bounded display-only summary BaseModels (refs-only) -------------------


class DashboardFindingSummary(BaseModel):
    """Bounded display-only projection of a Slice 16
    :class:`GovernanceFinding` for the dashboard rendering surface.

    Per doc-19:170-171 dashboards read snapshots with bounded fields
    and do NOT resolve full evidence bodies. This typed summary carries
    only the typed Slice 16 identity-surface fields (no body
    hydration); the typed surface enforces refs-only at construction.

    Per doc-19:232-233 AC6 *"Human-facing dashboard/Slack output
    explains top findings without hiding evidence quality or omitted
    details."* the typed summary preserves the typed
    :attr:`severity` + :attr:`confidence` + :attr:`estimated_lost_hours`
    fields so the dashboard can rank findings without losing the
    audit-trail surface.

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
    only identity surface for the finding row; the dashboard renders
    by id; consumers drill-through via the page_refs surface)."""

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
    doc-19:190-191 the dashboard ranks findings by severity +
    confidence + lost-hours estimate + recency; the typed surface
    preserves the lost-hours estimate so the dashboard ranking is
    reproducible."""


class DashboardRecommendationSummary(BaseModel):
    """Bounded display-only projection of a Slice 17
    :class:`GovernancePolicyRecommendation` for the dashboard
    rendering surface.

    Per doc-19:170-171 + doc-19:174-176 *"Agent ``policy_guidance``
    is prompt context only. It cannot override task contracts, gate
    requirements, failure-router policy, merge-queue policy, or any
    activated consumer policy artifact from Slice 17."* the typed
    summary is display-only: the dashboard renders the recommendation
    by id + consumer + status + confidence; the typed surface does
    NOT carry the policy artifact body (consumers drill-through via
    the page_refs surface).

    Per doc-19:232-233 AC6 the typed summary preserves the typed
    :attr:`confidence` + :attr:`status` + :attr:`consumer` fields so
    the dashboard can group recommendations without losing the
    audit-trail surface.
    """

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str
    """The Slice 17 :attr:`GovernancePolicyRecommendation.idempotency_key`
    (refs-only identity surface)."""

    recommendation_id: str
    """The Slice 17 :attr:`GovernancePolicyRecommendation.recommendation_id`
    (the human-facing id the dashboard renders)."""

    consumer: PolicyConsumer
    """The Slice 17 :data:`PolicyConsumer` Literal (6 values per
    doc-17:65). Typed REUSE -- NOT redefined."""

    status: PolicyRecommendationStatus
    """The Slice 17 :data:`PolicyRecommendationStatus` Literal (6
    values per doc-17:66-73). Typed REUSE -- NOT redefined."""

    confidence: float
    """The Slice 17 :attr:`GovernancePolicyRecommendation.confidence`
    (per doc-17 confidence score in 0.0-1.0 range; the typed Slice 17
    field validator enforces the range upstream)."""


class DashboardReplayResultSummary(BaseModel):
    """Bounded display-only projection of a Slice 18
    :class:`CounterfactualResult` for the dashboard rendering surface.

    Per doc-19:170-171 the typed summary is display-only: the
    dashboard renders the replay result by id + scenario + delta-hours
    + risk-change + confidence; the typed surface does NOT carry the
    full simulation trace (consumers drill-through via the page_refs
    surface).

    Per doc-19:232-233 AC6 the typed summary preserves the typed
    :attr:`confidence` + :attr:`estimated_risk_change` +
    :attr:`estimated_delta_hours` fields so the dashboard can rank
    replay results without losing the audit-trail surface.
    """

    model_config = ConfigDict(extra="forbid")

    result_id: str
    """The Slice 18 :attr:`CounterfactualResult.result_id` (refs-only
    identity surface)."""

    result_version: str
    """The Slice 18 :attr:`CounterfactualResult.result_version` (the
    typed version stamp; per doc-19:152-153 the snapshot digest is
    computed from recommendation + replay versions)."""

    scenario_id: str
    """The Slice 18 :attr:`CounterfactualResult.scenario_id` (the
    typed scenario the replay ran against)."""

    estimated_delta_hours: float | None
    """The Slice 18 :attr:`CounterfactualResult.estimated_delta_hours`
    (``None`` if the replay does not estimate delta-hours). Per
    doc-19:188-189 the dashboard ranks replay results by confidence +
    risk-change + recency + delta-hours; the typed surface preserves
    the delta-hours estimate so the dashboard ranking is reproducible."""

    estimated_risk_change: RiskChange
    """The Slice 18 :data:`RiskChange` Literal (per doc-18 risk-change
    classification). Typed REUSE -- NOT redefined."""

    confidence: float
    """The Slice 18 :attr:`CounterfactualResult.confidence` (per
    doc-18 confidence score in 0.0-1.0 range; the typed Slice 18 field
    validator enforces the range upstream)."""


# --- DashboardViewInputs (typed inputs; doc-19:152 step 3) -----------------


class DashboardViewInputs(BaseModel):
    """Typed bundle of all inputs the
    :meth:`GovernanceDashboardView.render` method consumes.

    Per doc-19:152 step 3 + doc-19:170-171 the inputs carry the typed
    Slice 19 2nd sub-slice :class:`SnapshotAPIResult` source (the
    typed bounded read the dashboard view projects) + the optional
    caller-side display budget overrides (independent from the typed
    snapshot API budgets; the typed surface lets callers cap the
    display surface separately from the snapshot surface, e.g. show
    fewer findings on a mobile dashboard than on the snapshot API
    response).

    The dashboard view does NOT itself fetch from the database -- it
    is a pure typed projection over the typed inputs (the caller owns
    the bounded-read transaction via the Slice 19 2nd sub-slice
    :class:`GovernanceSnapshotAPI` upstream; the dashboard view owns
    the typed display projection + display-only flag derivation).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`pydantic.ValidationError` rather than being silently
    absorbed.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives (``int`` /
    ``SnapshotAPIResult``).
    """

    model_config = ConfigDict(extra="forbid")

    source: SnapshotAPIResult
    """The typed Slice 19 2nd sub-slice :class:`SnapshotAPIResult` the
    dashboard view projects. Per the no-second-source-of-truth
    discipline the typed source is the Slice 19 2nd sub-slice typed
    result verbatim; the dashboard view does NOT redefine the typed
    snapshot or gap shape.

    If :attr:`SnapshotAPIResult.snapshot` is ``None`` (structural
    snapshot-API failure) the dashboard view emits a typed
    :class:`DashboardViewGap` with ``reason="upstream_snapshot_missing"``
    + the upstream gap findings are PROPAGATED verbatim to the
    :attr:`DashboardViewResult.gap_findings` list (the dashboard does
    not swallow upstream errors)."""

    max_display_findings: int = 10
    """The maximum number of typed :class:`DashboardFindingSummary`
    rows the view includes in
    :attr:`DashboardViewPayload.findings` before display truncation.

    Defaults to ``10`` (intentionally tighter than the Slice 19 2nd
    sub-slice 20-finding snapshot cap; dashboards typically show
    fewer findings than the snapshot surface). The view ADDS any
    additional display-truncation count to the existing
    :attr:`GovernanceSnapshot.omitted_counts["findings"]` so multi-
    level truncation is preserved per the doc-19:128-131 binding."""

    max_display_recommendations: int = 5
    """The maximum number of typed :class:`DashboardRecommendationSummary`
    rows the view includes in
    :attr:`DashboardViewPayload.recommendations` before display
    truncation.

    Defaults to ``5`` (intentionally tighter than the Slice 19 2nd
    sub-slice 10-recommendation snapshot cap)."""

    max_display_replay_results: int = 5
    """The maximum number of typed :class:`DashboardReplayResultSummary`
    rows the view includes in
    :attr:`DashboardViewPayload.replay_results` before display
    truncation.

    Defaults to ``5`` (intentionally tighter than the Slice 19 2nd
    sub-slice 10-replay-result snapshot cap)."""

    max_display_page_refs: int = 5
    """The maximum number of page-ref strings the view includes in
    :attr:`DashboardViewPayload.page_refs` before display truncation.

    Defaults to ``5`` (intentionally tighter than the Slice 19 2nd
    sub-slice 10-page-refs snapshot cap)."""

    max_display_blocked_by: int = 10
    """The maximum number of blocker-id strings the view includes in
    :attr:`DashboardViewPayload.blocked_by` before display truncation.

    Defaults to ``10``. Blocker-id strings are typically a small
    surface (per the doc-19:186-194 edge-case row count)."""


# --- DashboardViewPayload (typed bounded display payload; doc-19:170-171) --


class DashboardViewPayload(BaseModel):
    """Typed bounded dashboard payload per doc-19:170-171.

    Per doc-19:170-171 *"Dashboard reads governance snapshots with
    bounded fields and ETags; it does not resolve full evidence bodies
    by default. The ETag seed is ``snapshot_digest``."* the typed
    payload carries:

    * The typed :attr:`etag` = the typed
      :attr:`GovernanceSnapshot.snapshot_digest` verbatim per
      doc-19:170-171 (the ETag IS the snapshot_digest; no second
      source of truth).
    * The typed :attr:`corpus_id` + :attr:`generated_at` (identity
      surface).
    * The typed :attr:`completeness` + :attr:`evidence_quality` +
      :attr:`truncated` + :attr:`omitted_counts` + :attr:`page_refs`
      (the AC2 + AC6 enforcement triple).
    * The typed bounded display-only summaries
      (:attr:`findings` + :attr:`recommendations` +
      :attr:`replay_results`).
    * The typed :attr:`blocked_by` list.
    * The typed :attr:`display_only` AC2 flag (``True`` iff the
      payload is display-only per doc-19:225-226 + doc-19:128-131).

    Per doc-19:224 AC1 the typed surface is bounded (per-summary
    truncation; ``omitted_counts``), reproducible (``etag`` =
    ``snapshot_digest``), evidence-cited (typed summaries carry
    identity-surface refs), and structured first (typed Pydantic
    BaseModel; not prose).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`pydantic.ValidationError` rather than being silently
    absorbed.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives; the typed summary lists
    carry typed display-only BaseModels (NOT the full Slice 16/17/18
    body BaseModels) per the refs-only contract.
    """

    model_config = ConfigDict(extra="forbid")

    etag: str
    """The typed ETag per doc-19:170-171. Set to the typed
    :attr:`GovernanceSnapshot.snapshot_digest` verbatim; same snapshot
    -> same etag. The dashboard view enforces ``etag == snapshot_digest``
    structurally via :meth:`GovernanceDashboardView.compute_etag`.

    Per doc-19:218 *"Report generation is reproducible for the same
    corpus id."* the typed ETag is the reproducibility axis: same
    corpus id + same inputs -> same snapshot_digest -> same etag."""

    corpus_id: str
    """The typed corpus identifier the dashboard payload grounds on
    (mirrors the typed :attr:`GovernanceSnapshot.corpus_id` verbatim)."""

    snapshot_version: str
    """The typed snapshot version string the dashboard payload grounds
    on (mirrors the typed :attr:`GovernanceSnapshot.snapshot_version`
    verbatim)."""

    generated_at: datetime
    """The typed UTC timestamp the dashboard view generated the
    payload at (DIFFERENT from the typed
    :attr:`GovernanceSnapshot.generated_at` -- the snapshot was
    generated by the Slice 19 2nd sub-slice typed API; the dashboard
    payload is generated by THIS Slice 19 3rd sub-slice view; the
    typed surface preserves both timestamps so consumers can detect
    stale dashboard renders against fresh snapshots)."""

    snapshot_generated_at: datetime
    """The typed UTC timestamp the upstream Slice 19 2nd sub-slice
    typed API generated the snapshot at (= the typed
    :attr:`GovernanceSnapshot.generated_at` verbatim). The typed
    surface preserves the snapshot generation timestamp so consumers
    can detect stale snapshots vs fresh dashboard renders."""

    scorecard_id: str | None = None
    """The typed optional Slice 15 governance scorecard id (mirrors
    the typed :attr:`GovernanceSnapshot.scorecard_id` verbatim;
    ``None`` if the snapshot is not grounded on a specific
    scorecard)."""

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
    every dashboard payload so the dashboard rendering surface cannot
    omit it; the typed surface enforces the presence requirement at
    construction (the field has no default)."""

    max_response_bytes: int
    """The typed snapshot's effective max-response-bytes cap (mirrors
    the typed :attr:`GovernanceSnapshot.max_response_bytes` verbatim).
    The dashboard view does NOT enforce a separate byte budget -- the
    upstream snapshot API already enforced the byte budget; the
    dashboard view enforces its own per-list display truncation
    instead."""

    truncated: bool
    """``True`` if the typed payload's display lists have been
    truncated at ANY level (upstream snapshot OR dashboard view) per
    the doc-19:128-131 binding; the typed
    :attr:`omitted_counts` carries the per-list truncation counts."""

    omitted_counts: dict[str, int]
    """The typed dict of omitted-row counts by typed list name. The
    dashboard view ADDS its own per-list display truncation count to
    the upstream :attr:`GovernanceSnapshot.omitted_counts` so multi-
    level truncation is preserved per the doc-19:128-131 binding.

    Per doc-19:232-233 AC6 *"Human-facing dashboard/Slack output
    explains top findings without hiding evidence quality or omitted
    details."* the typed omitted-counts is REQUIRED on every dashboard
    payload so the dashboard rendering surface cannot omit it."""

    page_refs: list[str]
    """The typed list of page-ref string identifiers (mirrors the
    typed :attr:`GovernanceSnapshot.page_refs` verbatim, truncated to
    the typed :attr:`DashboardViewInputs.max_display_page_refs` cap
    if the snapshot's page_refs list exceeds the display cap).

    Per doc-19:81 the by-name reference shape mirrors the Slice 17
    1st sub-slice
    :attr:`GovernancePolicyRecommendation.source_finding_ids:
    list[str]` pattern. Per doc-19:128-131 truncated dashboard
    payloads MUST carry these refs OR the payload is display-only."""

    next_cursor: str | None = None
    """The typed optional pagination cursor for the next page of typed
    rows (mirrors the typed :attr:`GovernanceSnapshot.next_cursor`
    verbatim)."""

    findings: list[DashboardFindingSummary] = Field(default_factory=list)
    """The typed bounded display-only finding summaries (truncated to
    the typed :attr:`DashboardViewInputs.max_display_findings` cap).

    Per doc-19:170-171 + doc-19:232-233 AC6 the typed surface is
    display-only (no body hydration); per doc-19:190-191 the typed
    summaries are ranked by severity + confidence + lost-hours
    estimate + recency upstream by the Slice 19 2nd sub-slice
    snapshot API."""

    recommendations: list[DashboardRecommendationSummary] = Field(
        default_factory=list
    )
    """The typed bounded display-only recommendation summaries
    (truncated to the typed
    :attr:`DashboardViewInputs.max_display_recommendations` cap).

    Per doc-19:170-171 + doc-19:174-176 the typed summaries are
    display-only / advisory-only; the typed surface does NOT carry
    the policy artifact body."""

    replay_results: list[DashboardReplayResultSummary] = Field(
        default_factory=list
    )
    """The typed bounded display-only replay-result summaries
    (truncated to the typed
    :attr:`DashboardViewInputs.max_display_replay_results` cap).

    Per doc-19:170-171 the typed summaries are display-only; the
    typed surface does NOT carry the full simulation trace."""

    blocked_by: list[str] = Field(default_factory=list)
    """The typed list of blocker-id strings (mirrors the typed
    :attr:`GovernanceSnapshot.blocked_by` verbatim, truncated to the
    typed :attr:`DashboardViewInputs.max_display_blocked_by` cap if
    the snapshot's blocked_by list exceeds the display cap).

    Per doc-19:87 + doc-19:186-194 the various edge-case rows map onto
    blocker-id strings in this list; the typed surface accepts the
    empty list at construction (the typical case for a valid
    snapshot)."""

    display_only: bool
    """The typed AC2 enforcer flag per doc-19:225-226 + doc-19:128-131.

    ``True`` iff the payload is display-only (per doc-19:128-131
    *"...without those refs the response is display-only and cannot
    feed acceptance, recommendations, policy guidance, or task-execute
    context."*); ``False`` iff the payload is authoritative.

    Per the typed contract derived from doc-19:128-131 +
    doc-19:225-226 AC2 + doc-19:80 the flag is ``True`` iff:

    * ``(truncated=True AND no page_refs)`` -- truncated payload
      without exact page refs is display-only per doc-19:128-131.
    * ``(completeness in {"preview_only", "unavailable"})`` --
      preview/unavailable evidence per doc-13a CompletenessState
      Literal is display-only per doc-19:225-226 AC2.

    The flag is ``False`` iff ``completeness in {"complete", "paged"}``
    AND ``(not truncated OR page_refs exist)``."""


# --- DashboardViewGap (typed gap projection; doc-19:184-194 + doc-14:242-243)


class DashboardViewGap(BaseModel):
    """Typed governance-gap finding produced when the dashboard view
    fails to render a :class:`DashboardViewPayload` structurally.

    Mirrors the Slice 19 2nd sub-slice
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
    typed failure id :data:`DASHBOARD_VIEW_FAILURE_ID`
    (``governance_dashboard_view_failed``) registers under the EXISTING
    ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).
    """

    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["governance_dashboard_view_failed"]
    """Doc-19:184-194 + doc-14:192-201 -- the typed failure id.
    Registers under the EXISTING ``evidence_corruption`` failure_class
    with NON-blocking routing per doc-14:242-243 + doc-19:184-194."""

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
      :attr:`DashboardViewResult.gap_findings` list verbatim so the
      dashboard view does NOT swallow upstream errors.
    * ``payload_construction_failed`` -- Pydantic ValidationError on
      the typed :class:`DashboardViewPayload` construction.
    * ``summary_projection_failed`` -- Pydantic ValidationError on a
      typed summary BaseModel construction (e.g.
      :class:`DashboardFindingSummary`); the typed surface enforces
      the Slice 16/17/18 identity-surface contract at construction.
    * ``etag_computation_failed`` -- structural failure computing the
      typed :attr:`etag` from the typed snapshot.
    * ``governance_snapshot_stale`` -- doc-19:186-187 edge-case
      (propagated from the upstream typed
      :class:`SnapshotAPIGap.reason`).
    * ``active_workflow_pressure`` -- doc-19:193-194 edge-case
      (cached dashboard render requested).

    The caller distinguishes via this string. Per the auto-memory
    ``feedback_no_silent_degradation`` rule the typed surface is
    free-form so the view can emit new reason strings without a typed-
    shape breaking change."""

    observed_at: datetime
    """ISO-8601 timestamp the view observed the gap (UTC, timezone-
    aware). Mirrors the Slice 19 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_snapshot_api.SnapshotAPIGap.observed_at`
    contract verbatim."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding
    (e.g. the Pydantic ValidationError detail; the propagated upstream
    gap reason). Free-form per the doc-14:192-201 + Slice
    14/15/16/17/18/19-2nd governance-finding precedent."""


# --- DashboardViewResult (typed result; doc-19:152 step 3) -----------------


class DashboardViewResult(BaseModel):
    """Typed bundle of all outputs the
    :meth:`GovernanceDashboardView.render` method produces.

    The bundle composes:

    * ``payload`` -- the typed :class:`DashboardViewPayload` the view
      emitted, OR ``None`` if the projection failed structurally (in
      which case the gap is recorded in :attr:`gap_findings`).
    * ``gap_findings`` -- the list of typed :class:`DashboardViewGap`
      records emitted when a projection step fails structurally OR
      when an upstream gap is propagated.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.
    """

    model_config = ConfigDict(extra="forbid")

    payload: DashboardViewPayload | None = None
    """The typed :class:`DashboardViewPayload` the view emitted, OR
    ``None`` if the projection failed structurally.

    Per the doc-19:152 step 3 contract the view emits the typed
    payload when the upstream snapshot is present + the typed
    projection succeeds; on structural failure the payload is
    ``None`` + the gap finding is recorded in :attr:`gap_findings`.
    On upstream gap propagation the payload is ``None`` + the
    upstream gap findings are propagated to :attr:`gap_findings`."""

    gap_findings: list[DashboardViewGap] = Field(default_factory=list)
    """The list of typed :class:`DashboardViewGap` records emitted
    when a projection step fails structurally OR when an upstream
    gap is propagated.

    The list is typically empty (no gaps fired for a healthy
    payload). On structural failure the list contains exactly ONE
    gap record + :attr:`payload` is ``None``. On upstream gap
    propagation the list contains the propagated upstream gap(s) +
    :attr:`payload` is ``None``."""


# --- GovernanceDashboardView (the dashboard view class; doc-19:152 step 3) --


class GovernanceDashboardView:
    """The typed dashboard view class (doc-19:152 step 3).

    Per *"Add dashboard view that consumes governance snapshots
    only."* + doc-19:170-171 *"Dashboard reads governance snapshots
    with bounded fields and ETags; it does not resolve full evidence
    bodies by default. The ETag seed is ``snapshot_digest``."* the
    view consumes the typed :class:`DashboardViewInputs` (which carries
    the typed Slice 19 2nd sub-slice :class:`SnapshotAPIResult`) and
    emits a typed :class:`DashboardViewPayload` record.

    **Read-only / display-only projection (per doc-19:170-171 + the
    governance prompt § "Non-Negotiables").** The view is a READ-ONLY
    typed projection over the typed Slice 19 2nd sub-slice
    :class:`SnapshotAPIResult`. The view does NOT hydrate artifact
    bodies -- the typed summaries carry only the typed identity-surface
    fields per the Slice 16/17/18 contract.

    **ETag = snapshot_digest discipline (per doc-19:170-171).** The
    typed :attr:`DashboardViewPayload.etag` is the typed
    :attr:`GovernanceSnapshot.snapshot_digest` verbatim. The
    :meth:`compute_etag` helper returns the typed snapshot_digest
    directly; the typed surface enforces ``etag == snapshot_digest``
    structurally (no second source of truth for the ETag).

    **Bounded-reads discipline (per governance prompt §
    "Non-Negotiables").** The view truncates each of the 4 typed list
    dimensions (findings + recommendations + replay_results +
    page_refs) at the per-input display budget cap from
    :class:`DashboardViewInputs`. The view ADDS its own display-
    truncation count to the upstream
    :attr:`GovernanceSnapshot.omitted_counts` so multi-level
    truncation is preserved.

    **Refs-only projection (per governance prompt §
    "Non-Negotiables" + Slice 13A invariant).** The view extracts only
    the typed identity-surface fields (idempotency_key + class_name +
    severity + confidence for findings; idempotency_key +
    recommendation_id + consumer + status + confidence for
    recommendations; result_id + result_version + scenario_id +
    confidence + estimated_delta_hours for replay results) -- the
    typed BaseModel bodies are NOT passed through to the typed
    :attr:`DashboardViewPayload` lists (only the typed summary
    BaseModels are).

    **Fail-closed discipline (per auto-memory
    ``feedback_no_silent_degradation``).** The :meth:`render` method
    NEVER raises a failure to the caller. Any structural failure
    projects onto a typed :class:`DashboardViewGap` finding emitted on
    the :attr:`DashboardViewResult.gap_findings` list. The
    corresponding typed failure id :data:`DASHBOARD_VIEW_FAILURE_ID`
    (``governance_dashboard_view_failed``) registers under the EXISTING
    ``evidence_corruption`` failure_class with the EXISTING NON-
    blocking RouteAction ``retry_governance_projection`` (REUSED
    from Slice 14 2nd sub-slice; NOT a new route action).

    **Activation-authority boundary (doc-19:348-349 AC).** The view
    has TWO public methods (:meth:`render` + :meth:`compute_etag`) and
    NO mutation methods on any of its typed shapes. The view does NOT
    extend the Slice 10c-1 ``CONTROL_PLANE_WRITER_METHODS`` set.

    The view is stateless (no instance state beyond construction);
    callers MAY reuse a single instance across multiple snapshots.
    """

    def render(self, inputs: DashboardViewInputs) -> DashboardViewResult:
        """Render the typed :class:`DashboardViewPayload` from the
        typed inputs.

        Per doc-19:152 step 3 + doc-19:170-171 the method:

        1. Validates the upstream snapshot is present (the typed
           :attr:`SnapshotAPIResult.snapshot` is not ``None``); if
           absent, emits a typed :class:`DashboardViewGap` with
           ``reason="upstream_snapshot_missing"`` + PROPAGATES the
           upstream gap findings verbatim.
        2. Applies the bounded display-truncation discipline to each
           of the 4 typed list dimensions; accumulates the truncated-
           row counts into :attr:`omitted_counts` ADDED to the
           upstream snapshot counts.
        3. Projects the typed Slice 16 :class:`GovernanceFinding` +
           Slice 17 :class:`GovernancePolicyRecommendation` + Slice 18
           :class:`CounterfactualResult` rows onto the typed display-
           only summary BaseModels.
        4. Computes the typed AC2 :attr:`display_only` flag from
           ``truncated`` + ``page_refs`` + ``completeness`` per
           doc-19:225-226 + doc-19:128-131.
        5. Sets the typed :attr:`etag` to the typed
           :attr:`GovernanceSnapshot.snapshot_digest` verbatim per
           doc-19:170-171.
        6. Constructs the typed :class:`DashboardViewPayload` record.
        7. Records projection failures in
           :attr:`DashboardViewResult.gap_findings` per
           ``feedback_no_silent_degradation``.

        Per the auto-memory ``feedback_no_silent_degradation`` rule
        the method NEVER raises on input -- structural failures
        produce typed gap findings.
        """

        gap_findings: list[DashboardViewGap] = []

        snapshot = inputs.source.snapshot
        if snapshot is None:
            # Upstream snapshot is missing (Slice 19 2nd sub-slice
            # snapshot API failed structurally). PROPAGATE the upstream
            # gap findings + emit our own gap finding so the dashboard
            # caller sees both layers of the failure.
            corpus_id = self._corpus_id_from_upstream_gaps(
                inputs.source.gap_findings
            )
            gap_findings.append(
                DashboardViewGap(
                    failure_id=DASHBOARD_VIEW_FAILURE_ID,
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
            # upstream SnapshotAPIGap onto a typed DashboardViewGap
            # (refs-only; we copy the upstream reason + observed_at +
            # corpus_id verbatim so the dashboard caller sees the same
            # surface as the upstream caller).
            for upstream_gap in inputs.source.gap_findings:
                gap_findings.append(
                    DashboardViewGap(
                        failure_id=DASHBOARD_VIEW_FAILURE_ID,
                        corpus_id=upstream_gap.corpus_id,
                        reason=upstream_gap.reason,
                        observed_at=upstream_gap.observed_at,
                        evidence_payload={
                            "propagated_from": "snapshot_api_gap",
                            "upstream_failure_id": upstream_gap.failure_id,
                        },
                    )
                )
            return DashboardViewResult(
                payload=None,
                gap_findings=gap_findings,
            )

        # Compute the typed ETag = snapshot_digest verbatim per
        # doc-19:170-171. The compute_etag helper is the single source
        # of truth for the ETag derivation.
        try:
            etag = self.compute_etag(snapshot)
        except (ValueError, TypeError) as exc:
            gap_findings.append(
                DashboardViewGap(
                    failure_id=DASHBOARD_VIEW_FAILURE_ID,
                    corpus_id=snapshot.corpus_id,
                    reason="etag_computation_failed",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                    },
                )
            )
            return DashboardViewResult(
                payload=None,
                gap_findings=gap_findings,
            )

        # Bounded display-truncation per the LIMIT cap+1 discipline.
        # For each of the 4 typed display dimensions: truncate at the
        # per-input display cap; accumulate the truncated count into
        # the per-dim omitted-counts (added to the upstream snapshot
        # omitted counts so multi-level truncation is preserved).
        try:
            findings_summaries, findings_omitted = (
                self._project_findings(
                    snapshot.top_findings, inputs.max_display_findings
                )
            )
            recommendation_summaries, recommendations_omitted = (
                self._project_recommendations(
                    snapshot.recommendations,
                    inputs.max_display_recommendations,
                )
            )
            replay_summaries, replay_results_omitted = (
                self._project_replay_results(
                    snapshot.replay_results,
                    inputs.max_display_replay_results,
                )
            )
        except (ValidationError, ValueError, TypeError) as exc:
            gap_findings.append(
                DashboardViewGap(
                    failure_id=DASHBOARD_VIEW_FAILURE_ID,
                    corpus_id=snapshot.corpus_id,
                    reason="summary_projection_failed",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                    },
                )
            )
            return DashboardViewResult(
                payload=None,
                gap_findings=gap_findings,
            )

        # Bounded page_refs + blocked_by display truncation. These are
        # already typed list[str] on GovernanceSnapshot; the dashboard
        # view just caps the length at the per-input display cap.
        page_refs, page_refs_omitted = self._truncate_string_list(
            snapshot.page_refs, inputs.max_display_page_refs
        )
        blocked_by, blocked_by_omitted = self._truncate_string_list(
            snapshot.blocked_by, inputs.max_display_blocked_by
        )

        # Multi-level omitted-counts accumulation (upstream snapshot
        # counts + this view's display truncation counts). Read the
        # upstream count safely (default to 0 if the upstream key is
        # missing) so we never silently drop a key from the typed
        # omitted-counts surface.
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
            "recommendations": (
                upstream_recommendations + recommendations_omitted
            ),
            "replay_results": (
                upstream_replay_results + replay_results_omitted
            ),
            "page_refs": upstream_page_refs + page_refs_omitted,
            "blocked_by": blocked_by_omitted,
        }
        # Truncated flag is True iff ANY dimension was truncated at
        # ANY level (this view, upstream snapshot, or upstream
        # bounded reader).
        truncated: bool = any(
            count > 0 for count in omitted_counts.values()
        ) or snapshot.truncated

        # AC2 display_only derivation per doc-19:225-226 +
        # doc-19:128-131 + doc-19:80:
        # - True iff (truncated AND no page_refs) OR (completeness in
        #   {"preview_only", "unavailable"}).
        # - False iff (completeness in {"complete", "paged"}) AND (not
        #   truncated OR page_refs exist).
        display_only: bool = self._derive_display_only(
            completeness=snapshot.completeness,
            truncated=truncated,
            page_refs=page_refs,
        )

        # Construct the typed DashboardViewPayload record (per
        # doc-19:170-171 + the AC1/AC2/AC6/AC7 enforcement surface).
        try:
            payload = DashboardViewPayload(
                etag=etag,
                corpus_id=snapshot.corpus_id,
                snapshot_version=snapshot.snapshot_version,
                generated_at=_utcnow(),
                snapshot_generated_at=snapshot.generated_at,
                scorecard_id=snapshot.scorecard_id,
                completeness=snapshot.completeness,
                evidence_quality=snapshot.evidence_quality,
                max_response_bytes=snapshot.max_response_bytes,
                truncated=truncated,
                omitted_counts=omitted_counts,
                page_refs=page_refs,
                next_cursor=snapshot.next_cursor,
                findings=findings_summaries,
                recommendations=recommendation_summaries,
                replay_results=replay_summaries,
                blocked_by=blocked_by,
                display_only=display_only,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            gap_findings.append(
                DashboardViewGap(
                    failure_id=DASHBOARD_VIEW_FAILURE_ID,
                    corpus_id=snapshot.corpus_id,
                    reason="payload_construction_failed",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                    },
                )
            )
            return DashboardViewResult(
                payload=None,
                gap_findings=gap_findings,
            )

        return DashboardViewResult(
            payload=payload,
            gap_findings=gap_findings,
        )

    @staticmethod
    def compute_etag(snapshot: GovernanceSnapshot) -> str:
        """Compute the typed ETag for the typed snapshot per
        doc-19:170-171.

        Per *"The ETag seed is ``snapshot_digest``."* the typed ETag
        IS the typed :attr:`GovernanceSnapshot.snapshot_digest`
        verbatim. The helper is the SINGLE source of truth for the
        typed ETag derivation; the dashboard view enforces ``etag ==
        snapshot_digest`` structurally by routing the ETag through
        this helper.

        Per doc-19:218 *"Report generation is reproducible for the
        same corpus id."* the typed ETag is reproducible because the
        typed snapshot_digest is reproducible (per the Slice 19 1st
        sub-slice
        :func:`compute_governance_snapshot_digest` canonical-JSON +
        SHA-256 discipline).

        The helper is a static method (no instance state required);
        callers MAY invoke it directly on a typed
        :class:`GovernanceSnapshot` without constructing a view
        instance.
        """

        return snapshot.snapshot_digest

    # --- Bounded display-truncation + summary projection helpers ----

    @staticmethod
    def _project_findings(
        rows: list[GovernanceFinding], cap: int
    ) -> tuple[list[DashboardFindingSummary], int]:
        """Truncate the typed findings list at the per-input cap +
        project each row onto the typed display-only summary.

        Per the LIMIT cap+1 discipline: if ``len(rows) > cap`` the
        method returns the first ``cap`` summaries + the truncated
        count ``len(rows) - cap``; otherwise returns the full list +
        ``0``.

        Per the refs-only contract the summary carries only the typed
        identity-surface fields (idempotency_key + kind + class_name +
        severity + confidence + estimated_lost_hours); the full
        Slice 16 :class:`GovernanceFinding` body is NOT carried in the
        dashboard payload.
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
            DashboardFindingSummary(
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
    def _project_recommendations(
        rows: list[GovernancePolicyRecommendation], cap: int
    ) -> tuple[list[DashboardRecommendationSummary], int]:
        """Truncate the typed recommendations list at the per-input
        cap + project each row onto the typed display-only summary.

        Mirrors :meth:`_project_findings` verbatim with the typed
        Slice 17 identity-surface fields (idempotency_key +
        recommendation_id + consumer + status + confidence).
        """

        if cap < 0:
            return [], len(rows)
        if len(rows) > cap:
            truncated_rows = rows[:cap]
            omitted = len(rows) - cap
        else:
            truncated_rows = list(rows)
            omitted = 0
        summaries = [
            DashboardRecommendationSummary(
                idempotency_key=row.idempotency_key,
                recommendation_id=row.recommendation_id,
                consumer=row.consumer,
                status=row.status,
                confidence=row.confidence,
            )
            for row in truncated_rows
        ]
        return summaries, omitted

    @staticmethod
    def _project_replay_results(
        rows: list[CounterfactualResult], cap: int
    ) -> tuple[list[DashboardReplayResultSummary], int]:
        """Truncate the typed replay_results list at the per-input
        cap + project each row onto the typed display-only summary.

        Mirrors :meth:`_project_findings` verbatim with the typed
        Slice 18 identity-surface fields (result_id + result_version +
        scenario_id + estimated_delta_hours + estimated_risk_change +
        confidence).
        """

        if cap < 0:
            return [], len(rows)
        if len(rows) > cap:
            truncated_rows = rows[:cap]
            omitted = len(rows) - cap
        else:
            truncated_rows = list(rows)
            omitted = 0
        summaries = [
            DashboardReplayResultSummary(
                result_id=row.result_id,
                result_version=row.result_version,
                scenario_id=row.scenario_id,
                estimated_delta_hours=row.estimated_delta_hours,
                estimated_risk_change=row.estimated_risk_change,
                confidence=row.confidence,
            )
            for row in truncated_rows
        ]
        return summaries, omitted

    @staticmethod
    def _truncate_string_list(
        rows: list[str], cap: int
    ) -> tuple[list[str], int]:
        """Truncate a typed list[str] at the per-input cap.

        Used for the typed :attr:`GovernanceSnapshot.page_refs` +
        :attr:`GovernanceSnapshot.blocked_by` display truncation (both
        are already typed list[str] on the snapshot surface; the
        dashboard view just caps the length).
        """

        if cap < 0:
            return [], len(rows)
        if len(rows) > cap:
            return list(rows[:cap]), len(rows) - cap
        return list(rows), 0

    @staticmethod
    def _derive_display_only(
        *,
        completeness: CompletenessState,
        truncated: bool,
        page_refs: list[str],
    ) -> bool:
        """Derive the typed AC2 :attr:`DashboardViewPayload.display_only`
        flag from the typed completeness + truncated + page_refs.

        Per doc-19:225-226 AC2 + doc-19:128-131 + doc-19:80:

        * If ``completeness`` is ``"preview_only"`` OR
          ``"unavailable"`` the payload is display-only (preview
          evidence cannot be authoritative).
        * If ``truncated`` is ``True`` AND ``page_refs`` is empty
          the payload is display-only (truncated without exact refs
          is display-only per doc-19:128-131).
        * Otherwise the payload is NOT display-only (it is
          authoritative).

        The helper is the SINGLE source of truth for the AC2
        derivation; the dashboard view enforces ``display_only ==
        _derive_display_only(...)`` structurally by routing the
        derivation through this helper.
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

        Used when the upstream snapshot is missing + the dashboard
        view needs a corpus_id for the typed
        :class:`DashboardViewGap` surface.
        """

        if upstream_gaps:
            return upstream_gaps[0].corpus_id
        return "<unknown>"
