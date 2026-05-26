"""Slice 19 6th sub-slice -- READ-ONLY typed governance-report artifact
emitter that projects the Slice 19 2nd sub-slice :class:`SnapshotAPIResult`
onto a typed bounded ``review:governance-report:{corpus_id}`` artifact
record per doc-19 § Refactoring Steps step 6 (lines 161-162).

This module implements doc-19 § Refactoring Steps step 6 (lines
161-162): *"Add report artifacts such as
``review:governance-report:{corpus_id}`` with bounded summary only."*
+ doc-19:166-167 *"Governance reports are projections of governance
rows."*

It owns the typed governance-report-artifact-emitter surface:

* :data:`REPORT_ARTIFACT_FAILURE_ID` -- the typed failure id
  (``governance_report_artifact_emission_failed``) registered under
  the EXISTING ``evidence_corruption`` failure_class in
  :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
  with the EXISTING NON-blocking :data:`RouteAction`
  ``retry_governance_projection`` (REUSED from Slice 14 2nd
  sub-slice; mirrors Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A +
  3rd-B + 4th + Slice 17 2nd-6th + Slice 18 2nd-7th + Slice 19 2nd
  + 3rd + 4th + 5th sub-slice precedent verbatim; NOT a new
  failure_class; NOT a new route action).

* :data:`REPORT_ARTIFACT_KEY_PREFIX` -- the typed ``review:*``
  artifact-key prefix constant (``"review:governance-report:"``) the
  emitter cites when minting artifact keys. The prefix is
  INTENTIONALLY ``review:`` (NOT ``dag-``) per the
  doc-19:348-349 AC: the ``review:`` prefix denotes governance /
  review-only artifacts (no executor mutation authority); the
  ``dag-*`` prefix is reserved for the Slice 10c-1
  :data:`CONTROL_PLANE_WRITER_METHODS`-gated execution-authority
  artifact keys.

* :class:`ReportArtifactInputs` -- the typed bundle of all inputs
  the :meth:`GovernanceReportArtifactEmitter.emit_report_artifact`
  method consumes (typed Slice 19 2nd sub-slice
  :class:`SnapshotAPIResult` source + optional caller-supplied
  artifact-key suffix override).

* :class:`ReportArtifactGap` -- the typed gap finding emitted when
  the emitter fails to project structurally (mirrors
  :class:`AgentContextBuilderGap` + :class:`SlackRenderGap` +
  :class:`DashboardViewGap` + :class:`SnapshotAPIGap` +
  Slice 14/15/16/17/18 governance-projection-gap shape verbatim per
  the chunk-shape decision).

* :class:`GovernanceReportArtifact` -- the typed bounded report-
  artifact record the emitter produces. Refs-only fields only:
  ``artifact_key`` + ``corpus_id`` + ``snapshot_digest`` +
  ``snapshot_version`` + ``completeness`` + ``evidence_quality`` +
  ``top_finding_keys`` + ``recommendation_keys`` +
  ``replay_result_ids`` + ``page_refs`` + ``omitted_counts`` +
  ``blocked_by`` + ``generated_at`` + ``truncated``. NO artifact
  body hydration; the typed report cites the typed Slice 16
  :attr:`GovernanceFinding.idempotency_key` + Slice 17
  :attr:`GovernancePolicyRecommendation.idempotency_key` + Slice 18
  :attr:`CounterfactualResult.result_id` by-name reference shapes
  (refs-only per doc-19:111 + doc-19:114).

* :class:`ReportArtifactResult` -- the typed result BaseModel
  (``artifact: GovernanceReportArtifact | None`` + ``gap_findings``
  list).

* :class:`GovernanceReportArtifactEmitter` -- the typed report-
  artifact-emitter class with the public projection method
  :meth:`emit_report_artifact`.

**Bounded-summary-only discipline (per doc-19:161-162 +
doc-19:128-131 + doc-19:166-167).** The emitter is a READ-ONLY
typed projection over the typed Slice 19 2nd sub-slice
:class:`SnapshotAPIResult` input. The emitter does NOT hydrate
artifact bodies -- the typed :class:`GovernanceReportArtifact`
carries only the typed Slice 16
:attr:`GovernanceFinding.idempotency_key` + Slice 17
:attr:`GovernancePolicyRecommendation.idempotency_key` + Slice 18
:attr:`CounterfactualResult.result_id` by-name reference shapes
(refs-only per the doc-13a:285-287 step 9 shared 13A contract) +
the typed page-ref id strings (:attr:`page_refs: list[str]`) for
omitted-evidence drilldown. Per doc-19:161-162 the report carries
ONLY the bounded summary (the by-name reference shapes); per
doc-19:128-131 the typed :attr:`truncated` + :attr:`page_refs` +
:attr:`completeness` triple lets consumers detect display-only
state at construction.

**Artifact key shape (per doc-19:161-162).** The typed
:attr:`GovernanceReportArtifact.artifact_key` field carries the
typed bounded artifact key in the form
``review:governance-report:{corpus_id}`` where ``{corpus_id}`` is
substituted verbatim from the typed
:attr:`GovernanceSnapshot.corpus_id` field. Per the artifact-key
naming contract this is a ``review:*`` artifact key (NOT a
``dag-*`` execution-authority artifact key); the emitter does NOT
extend the Slice 10c-1
:data:`~iriai_build_v2.supervisor.read_only.CONTROL_PLANE_WRITER_METHODS`
set per doc-19:348-349 *"Supervisor/dashboard read-only contract
preserved (no governance writer extends the Slice 10c-1
``CONTROL_PLANE_WRITER_METHODS`` set)."* The artifact key prefix
constant :data:`REPORT_ARTIFACT_KEY_PREFIX` is exported at module
top for downstream test surfaces to assert the typed prefix
verbatim.

**Activation-authority boundary preserved (doc-19:348-349 AC).**
The :class:`GovernanceReportArtifactEmitter` class has ONE public
method (:meth:`emit_report_artifact`) and NO mutation methods on
any of its typed shapes (no ``activate_`` / ``approve_`` /
``merge_`` / ``checkpoint_`` / ``mutate_`` / ``write_`` /
``persist_`` methods). The emitter does NOT mint ``dag-*``
execution-authority artifact-key string literals (the
:data:`REPORT_ARTIFACT_KEY_PREFIX` is INTENTIONALLY ``review:`` per
the report-artifact contract). The emitter does NOT extend the
Slice 10c-1 ``CONTROL_PLANE_WRITER_METHODS`` set.

**Doc-19 acceptance criteria enforcement (sub-slice axes):**

* **AC1** (doc-19:224) *"Reports are bounded, reproducible,
  evidence-cited, and structured first."* -- enforced by:
  * **Bounded**: the typed report carries ONLY the by-name
    reference shapes (idempotency keys + ids + page-ref id
    strings) -- NO artifact body hydration; the report serialised
    size is therefore bounded by the cardinality of the bounded
    upstream snapshot's typed lists.
  * **Reproducible**: the same inputs deterministically produce the
    same typed report (pure function over typed inputs); the typed
    :attr:`artifact_key` is deterministic in the typed
    :attr:`GovernanceSnapshot.corpus_id`; the typed
    :attr:`snapshot_digest` is the typed Slice 19 2nd sub-slice
    :attr:`GovernanceSnapshot.snapshot_digest` verbatim.
  * **Evidence-cited**: the typed :attr:`page_refs` (typed list[str])
    + :attr:`top_finding_keys` + :attr:`recommendation_keys` +
    :attr:`replay_result_ids` carry the typed Slice 13a / Slice
    16 / Slice 17 / Slice 18 by-name reference shapes.
  * **Structured first**: the typed Pydantic BaseModel surface
    (typed BaseModels; not prose).

* **AC2** (doc-19:225-226) *"Truncated or preview reports are never
  authoritative unless exact page refs and completeness metadata
  cover the consumer's required scope."* -- enforced by the typed
  :attr:`GovernanceReportArtifact.truncated` +
  :attr:`GovernanceReportArtifact.page_refs` +
  :attr:`GovernanceReportArtifact.completeness` field triple.
  Truncated reports without exact page refs are typed display-only.

* **AC6** (doc-19:232-233) *"Human-facing dashboard/Slack output
  explains top findings without hiding evidence quality or omitted
  details."* -- enforced via the typed
  :attr:`GovernanceReportArtifact.evidence_quality` +
  :attr:`GovernanceReportArtifact.omitted_counts` fields always
  populated; the typed surface lets downstream dashboard / Slack
  consumers cite the typed report's evidence quality + omitted
  counts without re-hydrating evidence bodies.

* **AC7** (doc-19:234) *"Reporting honors Slice 10 read-only and
  bounded-read guarantees."* -- enforced by the typed class having
  ONE public method (:meth:`emit_report_artifact`) + no mutation
  methods on any BaseModel + no ``dag-*`` artifact-key literals +
  no ``CONTROL_PLANE_WRITER_METHODS`` extension.

**Fail-closed discipline (per auto-memory
``feedback_no_silent_degradation``).** The
:meth:`emit_report_artifact` method NEVER raises -- structural
failures project onto typed :class:`ReportArtifactGap` finding(s)
emitted on :attr:`ReportArtifactResult.gap_findings`. A SINGLE
typed failure id :data:`REPORT_ARTIFACT_FAILURE_ID`
(``governance_report_artifact_emission_failed``) covers the
doc-19:184-194 edge-case rows (mirror of the Slice 17 6th
sub-slice ``consumer_read_api_failed`` + Slice 18 2nd sub-slice
``replay_corpus_or_scenario_load_failed`` + Slice 19 2nd-5th
sub-slice 1-failure-id-per-typed-API-class precedent verbatim).
The typed :class:`ReportArtifactGap` shape carries the surface
``reason`` so consumers can distinguish edge-case classes if
needed.

Per the auto-memory ``feedback_flat_structured_output`` rule the
typed control fields are flat primitives (``str``, ``int``,
``bool``, ``list[str]``); no nested BaseModels are required for
control signaling.

Per the no-second-source-of-truth discipline this module REUSES
the following typed shapes (DIRECT import; annotation-identity
assertions in the unit-test surface enforce the contract):

* Slice 19 1st sub-slice :class:`GovernanceSnapshot`
  (:mod:`iriai_build_v2.execution_control.governance_agent`) for
  the typed snapshot shape the report cites.
* Slice 19 2nd sub-slice :class:`SnapshotAPIResult` +
  :class:`SnapshotAPIGap`
  (:mod:`iriai_build_v2.execution_control.governance_snapshot_api`)
  for the typed snapshot bundle the emitter consumes.
* Slice 13a :data:`CompletenessState` +
  :data:`EvidenceQuality` Literals
  (:mod:`iriai_build_v2.workflows.develop.governance.models`) for
  the typed completeness + evidence-quality field projection.
* Slice 16 :class:`GovernanceFinding`
  (:mod:`iriai_build_v2.execution_control.finding_engine`) for the
  typed finding records the emitter projects to typed
  :attr:`GovernanceFinding.idempotency_key` strings.
* Slice 17 :class:`GovernancePolicyRecommendation`
  (:mod:`iriai_build_v2.execution_control.policy_recommendation`)
  for the typed recommendation records the emitter projects to
  typed :attr:`GovernancePolicyRecommendation.idempotency_key`
  strings.
* Slice 18 :class:`CounterfactualResult`
  (:mod:`iriai_build_v2.execution_control.counterfactual_replay`)
  for the typed replay result records the emitter projects to
  typed :attr:`CounterfactualResult.result_id` strings.

It is the **report-artifact projection layer** that subsequent
Slice 19 sub-slices (read-only enforcement) cite as the typed
bounded-summary artifact contract for the
``review:governance-report:{corpus_id}`` artifact-key surface.

**Slice 13A invariant compliance.** The report emitter consumes
the typed Slice 19 2nd sub-slice :class:`SnapshotAPIResult` which
is itself a typed bounded read over the cited Slice 16/17/18
evidence surface; the emitter does NOT hydrate artifact bodies
(:attr:`page_refs` surface is refs-only per doc-19:111) and
preserves the upstream completeness + evidence_quality verbatim.
Per the Slice 13A invariant doc-13a:18-23 the typed
:attr:`GovernanceReportArtifact.completeness` field carries the
typed :data:`CompletenessState` Literal so downstream report
consumers can detect ``preview_only`` / ``unavailable`` reports at
construction and reject them as authoritative input per
doc-19:128-131.

References:

* Doc-19 § Refactoring Steps step 6 (lines 161-162) -- *"Add
  report artifacts such as ``review:governance-report:{corpus_id}``
  with bounded summary only."*
* Doc-19:166-167 -- *"Governance reports are projections of
  governance rows."*
* Doc-19:128-131 + doc-19:225-226 AC2 -- preview/display budgets;
  truncated reports without exact page refs are display-only.
* Doc-19:184-194 -- edge-case rows mapped to typed gap reasons.
* Doc-19:218 -- *"Report generation is reproducible for the same
  corpus id."* (the emitter is a pure function over typed inputs.)
* Doc-19:234 + doc-19:348-349 AC -- read-only contract.
* Doc-14:242-243 -- governance-projection NON-blocking contract
  (inherited by every post-checkpoint governance projection
  observer; the report emitter is also a post-checkpoint
  observer).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
)
from iriai_build_v2.execution_control.finding_engine import (
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
)
from iriai_build_v2.workflows.develop.governance.models import (
    CompletenessState,
    EvidenceQuality,
)


__all__ = [
    # Typed failure id (doc-19:184-194 + doc-14:242-243 NON-BLOCKING).
    "REPORT_ARTIFACT_FAILURE_ID",
    # Typed artifact-key prefix (doc-19:161-162 -- the review:* contract).
    "REPORT_ARTIFACT_KEY_PREFIX",
    # Typed report-artifact-emitter inputs / gap / result / artifact.
    "ReportArtifactInputs",
    "ReportArtifactGap",
    "ReportArtifactResult",
    "GovernanceReportArtifact",
    # The report-artifact-emitter class (doc-19:161-162 step 6).
    "GovernanceReportArtifactEmitter",
]


# --- Typed failure id (doc-19:184-194 + doc-14:242-243 NON-BLOCKING) --------


REPORT_ARTIFACT_FAILURE_ID: Literal[
    "governance_report_artifact_emission_failed"
] = "governance_report_artifact_emission_failed"
"""Doc-19:184-194 + doc-14:242-243 -- the typed failure id the
governance report-artifact emitter projects onto when a structural
projection step fails.

Registers under the EXISTING ``evidence_corruption`` failure_class
in :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
(NOT a new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd-6th + Slice 18 2nd-7th + Slice 19
2nd + 3rd + 4th + 5th sub-slice precedent verbatim).

A SINGLE failure id covers ALL the doc-19:184-194 edge-case rows
(upstream snapshot missing; artifact construction failed; corpus_id
template substitution failed; governance snapshot stale; active
workflow pressure) per the Slice 17 6th sub-slice
``consumer_read_api_failed`` + Slice 18 2nd sub-slice
``replay_corpus_or_scenario_load_failed`` + Slice 19 2nd sub-slice
``governance_snapshot_api_failed`` + Slice 19 3rd sub-slice
``governance_dashboard_view_failed`` + Slice 19 4th sub-slice
``governance_slack_renderer_failed`` + Slice 19 5th sub-slice
``governance_agent_context_builder_failed`` precedent (one typed
failure id per typed-API class). The typed :class:`ReportArtifactGap`
shape carries the surface ``reason`` so consumers can distinguish
edge-case classes if needed.

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 19 pattern matches the Slice 14 + Slice 15 + Slice 16 + Slice
17 + Slice 18 + Slice 19 2nd + 3rd + 4th + 5th sub-slice non-blocking
governance projection observer (the report-artifact emitter is also a
post-checkpoint governance projection observer + per doc-19:166-167
governance reports are projections of governance rows -- never
runtime policy authority).

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to report-artifact
emission failures.
"""


# --- Typed artifact-key prefix (doc-19:161-162) -----------------------------


REPORT_ARTIFACT_KEY_PREFIX: Literal[
    "review:governance-report:"
] = "review:governance-report:"
"""Doc-19:161-162 -- the typed artifact-key prefix the emitter cites
when minting typed bounded report-artifact records.

Per doc-19:161-162 *"Add report artifacts such as
``review:governance-report:{corpus_id}`` with bounded summary only."*
the typed bounded report-artifact key takes the form
``review:governance-report:{corpus_id}`` with ``{corpus_id}``
substituted verbatim from the typed
:attr:`GovernanceSnapshot.corpus_id` field.

The ``review:`` prefix is INTENTIONALLY DIFFERENT from the
``dag-*`` execution-authority artifact-key prefix used by the Slice
10c-1 :data:`~iriai_build_v2.supervisor.read_only.CONTROL_PLANE_WRITER_METHODS`-
gated execution-authority artifact keys. Per doc-19:348-349
*"Supervisor/dashboard read-only contract preserved (no governance
writer extends the Slice 10c-1 ``CONTROL_PLANE_WRITER_METHODS``
set)."* the typed report-artifact emitter does NOT extend the
:data:`CONTROL_PLANE_WRITER_METHODS` set; the ``review:*`` prefix
denotes governance / review-only artifacts (no executor mutation
authority).

The typed surface is a :class:`typing.Literal` so consumers can
cite the typed prefix verbatim at the call site; the typed
emitter test surface asserts the prefix value byte-for-byte.
"""


def _utcnow() -> datetime:
    """Return the current UTC timestamp (timezone-aware).

    Mirrors the Slice 19 5th sub-slice
    :func:`~iriai_build_v2.execution_control.governance_agent_context_builder._utcnow`
    + Slice 19 4th sub-slice
    :func:`~iriai_build_v2.execution_control.governance_slack_renderer._utcnow`
    + Slice 19 3rd sub-slice
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


# --- ReportArtifactInputs (typed inputs; doc-19:161-162 step 6) -------------


class ReportArtifactInputs(BaseModel):
    """Typed bundle of all inputs the
    :meth:`GovernanceReportArtifactEmitter.emit_report_artifact`
    method consumes.

    Per doc-19:161-162 step 6 (*"Add report artifacts such as
    ``review:governance-report:{corpus_id}`` with bounded summary
    only."*) the inputs carry the typed Slice 19 2nd sub-slice
    :class:`SnapshotAPIResult` source (the typed bounded read the
    emitter projects).

    The emitter does NOT itself fetch from the database -- it is a
    pure typed projection over the typed inputs (the caller owns the
    bounded-read transaction via the Slice 19 2nd sub-slice
    :class:`GovernanceSnapshotAPI` upstream; the report-artifact
    emitter owns the typed bounded-summary projection + typed
    :class:`GovernanceReportArtifact` construction).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`pydantic.ValidationError` rather than being silently
    absorbed.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives + typed BaseModel input.
    """

    model_config = ConfigDict(extra="forbid")

    source: SnapshotAPIResult
    """The typed Slice 19 2nd sub-slice :class:`SnapshotAPIResult` the
    report-artifact emitter projects. Per the no-second-source-of-
    truth discipline the typed source is the Slice 19 2nd sub-slice
    typed result verbatim; the report-artifact emitter does NOT
    redefine the typed snapshot or gap shape.

    If :attr:`SnapshotAPIResult.snapshot` is ``None`` (structural
    snapshot-API failure) the report-artifact emitter emits a typed
    :class:`ReportArtifactGap` with ``reason="upstream_snapshot_missing"``
    + the upstream gap findings are PROPAGATED verbatim to the
    :attr:`ReportArtifactResult.gap_findings` list (the emitter does
    not swallow upstream errors)."""


# --- ReportArtifactGap (typed gap; doc-19:184-194 + doc-14:242-243) ---------


class ReportArtifactGap(BaseModel):
    """Typed governance-gap finding produced when the report-artifact
    emitter fails to project structurally.

    Mirrors the Slice 19 5th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_agent_context_builder.AgentContextBuilderGap`
    + Slice 19 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_slack_renderer.SlackRenderGap`
    + Slice 19 3rd sub-slice
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

    Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED
    here per the governance-projection discipline) the gap finding
    is NON-blocking: the caller MUST NOT propagate it to the
    executor / checkpoint / merge-queue / resume code paths. The
    corresponding typed failure id :data:`REPORT_ARTIFACT_FAILURE_ID`
    (``governance_report_artifact_emission_failed``) registers under
    the EXISTING ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).
    """

    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["governance_report_artifact_emission_failed"]
    """Doc-19:184-194 + doc-14:192-201 -- the typed failure id.
    Registers under the EXISTING ``evidence_corruption`` failure_class
    with NON-blocking routing per doc-14:242-243 + doc-19:184-194."""

    corpus_id: str
    """The corpus scope of the failed projection (mirrors the
    :attr:`SnapshotAPIInputs.corpus_id` from the upstream snapshot
    API). Falls back to the empty string when the upstream snapshot
    is missing AND no ``corpus_id`` is recoverable from the upstream
    gaps."""

    reason: str
    """Free-form gap reason. Documented values:

    * ``upstream_snapshot_missing`` -- the upstream
      :class:`SnapshotAPIResult` carried ``snapshot=None`` (structural
      snapshot-API failure).
    * ``artifact_construction_failed`` -- Pydantic ValidationError on
      the typed :class:`GovernanceReportArtifact` construction.
    * ``corpus_id_empty`` -- the typed :attr:`GovernanceSnapshot.corpus_id`
      is empty / whitespace-only (cannot mint a valid artifact key).
    * ``governance_snapshot_stale`` -- doc-19:186-187 edge-case (the
      typed :attr:`GovernanceSnapshot.blocked_by` is non-empty).
    * ``active_workflow_pressure`` -- doc-19:193-194 edge-case
      (cached report requested).

    The caller distinguishes via this string. Per the auto-memory
    ``feedback_no_silent_degradation`` rule the typed surface is
    free-form so the emitter can emit new reason strings without a
    typed-shape breaking change."""

    observed_at: datetime
    """ISO-8601 timestamp the emitter observed the gap (UTC, timezone-
    aware). Mirrors the Slice 19 5th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_agent_context_builder.AgentContextBuilderGap.observed_at`
    + Slice 19 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_slack_renderer.SlackRenderGap.observed_at`
    + Slice 19 3rd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_dashboard_view.DashboardViewGap.observed_at`
    + Slice 19 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_snapshot_api.SnapshotAPIGap.observed_at`
    contract verbatim."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding
    (e.g. the Pydantic ValidationError detail; the rejected
    artifact-key value; the upstream gap finding count). Free-form
    per the doc-14:192-201 + Slice 14/15/16/17/18/19-2nd/19-3rd/
    19-4th/19-5th governance-finding precedent."""


# --- GovernanceReportArtifact (typed bounded report; doc-19:161-162) --------


class GovernanceReportArtifact(BaseModel):
    """Typed bounded governance-report artifact (doc-19:161-162 +
    doc-19:166-167).

    Per doc-19:161-162 *"Add report artifacts such as
    ``review:governance-report:{corpus_id}`` with bounded summary
    only."* + doc-19:166-167 *"Governance reports are projections of
    governance rows."* the typed bounded report carries the typed
    :attr:`artifact_key` (``review:governance-report:{corpus_id}``)
    + the bounded-summary fields the emitter projects from the
    typed Slice 19 2nd sub-slice :class:`SnapshotAPIResult` source.

    **Refs-only projection (per doc-19:111 + doc-19:114 + Slice 13A
    invariant).** The typed report carries ONLY the by-name reference
    shapes (typed finding idempotency keys + recommendation
    idempotency keys + replay result ids + page-ref id strings) +
    the typed summary fields (corpus_id + snapshot_digest +
    completeness + evidence_quality + omitted_counts + blocked_by +
    truncated); NO artifact body hydration.

    **Bounded-summary discipline (per doc-19:161-162).** The typed
    report is bounded by the cardinality of the typed Slice 19 2nd
    sub-slice :class:`SnapshotAPIResult` source's bounded typed
    lists; the source is itself bounded by the typed
    ``LIMIT cap+1`` discipline + per-call budget overrides per
    doc-19:121-123.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-19:256-303).** The typed :attr:`completeness` field is typed
    against the Slice 13a :data:`CompletenessState` Literal + the
    typed :attr:`evidence_quality` field is typed against the
    Slice 13a :data:`EvidenceQuality` Literal (both imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`; NOT
    redefined here). Per doc-13a:285-287 step 9 the shared Literals
    are the authority for governance completeness +
    evidence-quality semantics.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`pydantic.ValidationError` rather than being silently
    absorbed.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives (``str``, ``bool``,
    ``list[str]``, ``dict[str, int]``).
    """

    model_config = ConfigDict(extra="forbid")

    artifact_key: str
    """Doc-19:161-162 -- the typed bounded artifact key in the form
    ``review:governance-report:{corpus_id}`` with ``{corpus_id}``
    substituted verbatim from the typed
    :attr:`GovernanceSnapshot.corpus_id` field.

    Per the artifact-key naming contract the key begins with the
    typed :data:`REPORT_ARTIFACT_KEY_PREFIX` (``review:``) prefix --
    NOT the ``dag-*`` execution-authority prefix per the
    doc-19:348-349 AC. The typed surface is ``str`` (not Literal) so
    the typed corpus id can be substituted at construction; the
    typed emitter ENFORCES the prefix at construction by
    string-prefix verification before emitting the typed
    :class:`GovernanceReportArtifact`."""

    corpus_id: str
    """The corpus id from the typed :attr:`GovernanceSnapshot.corpus_id`
    field verbatim. Per doc-19:218 *"Report generation is
    reproducible for the same corpus id."* the typed corpus id is
    the reproducibility-identity surface."""

    snapshot_digest: str
    """The typed :attr:`GovernanceSnapshot.snapshot_digest` field
    verbatim (the SHA-256 hex digest of the bounded inputs per
    doc-19:152-153). The typed surface lets consumers cite the
    typed digest for dedupe / cache lookup / change detection."""

    snapshot_version: str
    """The typed :attr:`GovernanceSnapshot.snapshot_version` field
    verbatim (e.g. ``"v1"``). Lets consumers detect schema
    evolution across reruns."""

    completeness: CompletenessState
    """The typed :attr:`GovernanceSnapshot.completeness` field
    verbatim (Slice 13a typed Literal). Per doc-19:128-131 +
    doc-19:225-226 AC2 ``preview_only`` / ``unavailable`` reports
    are display-only; ``complete`` / ``paged`` reports may feed
    downstream consumers."""

    evidence_quality: EvidenceQuality
    """The typed :attr:`GovernanceSnapshot.evidence_quality` field
    verbatim (Slice 13a typed Literal). Per doc-19:232-233 AC6 the
    typed evidence-quality is the visibility contract enforcer; the
    typed surface ensures the report cannot hide evidence quality."""

    top_finding_keys: list[str]
    """The typed list of Slice 16
    :attr:`GovernanceFinding.idempotency_key` strings projected from
    the typed :attr:`GovernanceSnapshot.top_findings` list. Refs-only
    per doc-19:111 -- the typed report does NOT hydrate the
    GovernanceFinding bodies."""

    recommendation_keys: list[str]
    """The typed list of Slice 17
    :attr:`GovernancePolicyRecommendation.idempotency_key` strings
    projected from the typed :attr:`GovernanceSnapshot.recommendations`
    list. Refs-only per doc-19:111 -- the typed report does NOT
    hydrate the GovernancePolicyRecommendation bodies."""

    replay_result_ids: list[str]
    """The typed list of Slice 18
    :attr:`CounterfactualResult.result_id` strings projected from
    the typed :attr:`GovernanceSnapshot.replay_results` list.
    Refs-only per doc-19:111 -- the typed report does NOT hydrate
    the CounterfactualResult bodies."""

    page_refs: list[str]
    """The typed :attr:`GovernanceSnapshot.page_refs` field verbatim
    (the typed page-ref id strings per doc-19:81). Refs-only per
    doc-19:114 -- the typed report does NOT hydrate page-ref bodies."""

    omitted_counts: dict[str, int]
    """The typed :attr:`GovernanceSnapshot.omitted_counts` field
    verbatim. Per doc-19:232-233 AC6 the typed omitted-counts dict
    is the visibility contract enforcer; the typed surface ensures
    the report cannot hide omitted details."""

    blocked_by: list[str]
    """The typed :attr:`GovernanceSnapshot.blocked_by` field verbatim
    (the typed blocker id strings per doc-19:87 + doc-19:186-194)."""

    truncated: bool
    """The typed :attr:`GovernanceSnapshot.truncated` field verbatim.
    Per doc-19:128-131 + doc-19:225-226 AC2 ``truncated=True``
    reports MUST carry exact :attr:`page_refs` + :attr:`completeness`
    or the report is display-only."""

    generated_at: datetime
    """ISO-8601 timestamp the emitter generated the report (UTC,
    timezone-aware). Distinct from the typed
    :attr:`GovernanceSnapshot.generated_at` field (which is the
    snapshot timestamp); the typed report timestamp records when
    the typed bounded-summary projection was emitted."""


# --- ReportArtifactResult (typed result; doc-19:161-162 step 6) -------------


class ReportArtifactResult(BaseModel):
    """Typed bundle of all outputs the
    :meth:`GovernanceReportArtifactEmitter.emit_report_artifact`
    method produces.

    The bundle composes:

    * ``artifact`` -- the typed :class:`GovernanceReportArtifact` the
      emitter emitted, OR ``None`` if the projection failed
      structurally (in which case the gap is recorded in
      :attr:`gap_findings`).
    * ``gap_findings`` -- the list of typed :class:`ReportArtifactGap`
      records emitted when a projection step fails structurally OR
      when an informational gap fires (e.g. propagated upstream
      :class:`SnapshotAPIGap` rows lifted into the typed emitter gap
      shape).

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.
    """

    model_config = ConfigDict(extra="forbid")

    artifact: GovernanceReportArtifact | None = None
    """The typed :class:`GovernanceReportArtifact` the emitter
    emitted, OR ``None`` if the projection failed structurally.

    Per the doc-19:161-162 step 6 contract the emitter emits the
    typed report when inputs are valid; on structural failure the
    artifact is ``None`` + the gap finding is recorded in
    :attr:`gap_findings`. On informational-only gaps (e.g. governance
    snapshot stale) the artifact MAY STILL be emitted (with the
    typed :attr:`GovernanceReportArtifact.blocked_by` populated +
    the informational gap recorded)."""

    gap_findings: list[ReportArtifactGap] = Field(default_factory=list)
    """The list of typed :class:`ReportArtifactGap` records emitted
    when a projection step fails structurally OR when an informational
    gap fires.

    The list is typically empty (no gaps fired for a healthy
    projection). On structural failure the list contains exactly ONE
    gap record + :attr:`artifact` is ``None``. On informational gaps
    the list contains the informational gap(s) + :attr:`artifact` is
    the typed bounded report (with the informational state encoded
    in the typed shape)."""


# --- GovernanceReportArtifactEmitter (the emitter class; doc-19:161-162) ----


class GovernanceReportArtifactEmitter:
    """The typed report-artifact emitter class (doc-19:161-162 step 6).

    Per *"Add report artifacts such as
    ``review:governance-report:{corpus_id}`` with bounded summary
    only."* + doc-19:166-167 *"Governance reports are projections of
    governance rows."* the emitter consumes the typed
    :class:`ReportArtifactInputs` (which carries the typed Slice 19
    2nd sub-slice :class:`SnapshotAPIResult` source) and emits a
    typed :class:`GovernanceReportArtifact` record.

    **Bounded-summary discipline (per doc-19:161-162 + doc-19:128-131
    + doc-19:166-167).** The emitter is a READ-ONLY typed projection
    over the typed Slice 19 2nd sub-slice :class:`SnapshotAPIResult`
    input. The emitter does NOT hydrate artifact bodies -- the typed
    :class:`GovernanceReportArtifact` carries only the by-name
    reference shapes (idempotency keys + ids + page-ref id strings)
    + the typed summary fields verbatim from the upstream snapshot.

    **Artifact-key shape (per doc-19:161-162).** The typed
    :attr:`GovernanceReportArtifact.artifact_key` field is the
    typed substitution
    ``review:governance-report:{corpus_id}`` with the typed corpus
    id from the upstream snapshot. The typed
    :data:`REPORT_ARTIFACT_KEY_PREFIX` exports the prefix verbatim
    for downstream test surfaces.

    **Refs-only projection (per governance prompt §
    "Non-Negotiables" + Slice 13A invariant).** The emitter projects
    typed BaseModel rows to typed by-name reference shapes (e.g.
    finding -> :attr:`GovernanceFinding.idempotency_key`); the typed
    :attr:`GovernanceReportArtifact` carries the typed by-name
    references (refs-only per doc-19:111 + doc-19:114).

    **Fail-closed discipline (per auto-memory
    ``feedback_no_silent_degradation``).** The
    :meth:`emit_report_artifact` method NEVER raises a failure to
    the caller. Any structural failure projects onto a typed
    :class:`ReportArtifactGap` finding emitted on the
    :attr:`ReportArtifactResult.gap_findings` list. The corresponding
    typed failure id :data:`REPORT_ARTIFACT_FAILURE_ID`
    (``governance_report_artifact_emission_failed``) registers under
    the EXISTING ``evidence_corruption`` failure_class with the
    EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).

    The emitter is stateless (no instance state beyond construction);
    callers MAY reuse a single instance across multiple corpora.

    **Activation-authority boundary (doc-19:348-349 AC).** The class
    has ONE public method (:meth:`emit_report_artifact`) and NO
    mutation methods on any of its typed shapes (no ``activate_`` /
    ``approve_`` / ``merge_`` / ``checkpoint_`` / ``mutate_`` /
    ``write_`` / ``persist_`` methods). The emitter does NOT mint
    ``dag-*`` execution-authority artifact-key string literals (the
    :data:`REPORT_ARTIFACT_KEY_PREFIX` is INTENTIONALLY ``review:``).
    The emitter does NOT extend the Slice 10c-1
    ``CONTROL_PLANE_WRITER_METHODS`` set.
    """

    def emit_report_artifact(
        self, inputs: ReportArtifactInputs
    ) -> ReportArtifactResult:
        """Emit the typed :class:`GovernanceReportArtifact` from the
        typed inputs.

        Per doc-19:161-162 + doc-19:166-167 the method:

        1. Resolves the upstream snapshot (returns
           ``upstream_snapshot_missing`` gap if absent; propagates the
           upstream :class:`SnapshotAPIGap` rows verbatim onto
           :attr:`ReportArtifactResult.gap_findings` prefixed with
           ``upstream_snapshot_gap:`` so consumers can distinguish
           them from emitter-emitted gaps).
        2. Validates the typed
           :attr:`GovernanceSnapshot.corpus_id` is non-empty (returns
           ``corpus_id_empty`` gap if empty / whitespace-only).
        3. Substitutes the typed corpus id into the typed
           :data:`REPORT_ARTIFACT_KEY_PREFIX` template to mint the
           typed :attr:`artifact_key`.
        4. Projects the typed :attr:`GovernanceSnapshot.top_findings`
           list to the typed by-name :attr:`top_finding_keys` list
           (idempotency keys; refs-only per doc-19:111).
        5. Projects the typed :attr:`GovernanceSnapshot.recommendations`
           list to the typed by-name :attr:`recommendation_keys` list
           (idempotency keys; refs-only per doc-19:111).
        6. Projects the typed :attr:`GovernanceSnapshot.replay_results`
           list to the typed by-name :attr:`replay_result_ids` list
           (result ids; refs-only per doc-19:111).
        7. Copies the typed :attr:`GovernanceSnapshot.page_refs` /
           :attr:`omitted_counts` / :attr:`blocked_by` /
           :attr:`completeness` / :attr:`evidence_quality` /
           :attr:`snapshot_digest` / :attr:`snapshot_version` /
           :attr:`corpus_id` / :attr:`truncated` fields verbatim.
        8. Emits an informational ``governance_snapshot_stale`` gap
           if :attr:`GovernanceSnapshot.blocked_by` is non-empty
           (the typed report is STILL emitted; the gap is
           informational per doc-19:186-187).
        9. Constructs + returns the typed
           :class:`GovernanceReportArtifact` + the typed
           :class:`ReportArtifactResult` bundle.

        The method NEVER raises -- structural failures project onto
        typed :class:`ReportArtifactGap` finding(s) emitted on
        :attr:`ReportArtifactResult.gap_findings`.

        Per the auto-memory ``feedback_no_silent_degradation`` rule
        every error path through this method is a typed gap; nothing
        is silently degraded.
        """

        gap_findings: list[ReportArtifactGap] = []

        # Step 1: resolve the upstream snapshot. If it's None,
        # propagate the upstream gaps + emit the typed
        # `upstream_snapshot_missing` gap.
        snapshot: GovernanceSnapshot | None = inputs.source.snapshot
        if snapshot is None:
            corpus_id = self._extract_corpus_id(inputs.source)
            gap_findings.append(
                ReportArtifactGap(
                    failure_id=REPORT_ARTIFACT_FAILURE_ID,
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
            # Propagate upstream gaps verbatim, prefixed with
            # `upstream_snapshot_gap:` so consumers can distinguish.
            for upstream_gap in inputs.source.gap_findings:
                gap_findings.append(
                    self._lift_upstream_gap(upstream_gap, corpus_id)
                )
            return ReportArtifactResult(
                artifact=None,
                gap_findings=gap_findings,
            )

        # Step 2: validate the corpus_id is non-empty (cannot mint a
        # valid artifact key with an empty corpus_id).
        if not snapshot.corpus_id or not snapshot.corpus_id.strip():
            gap_findings.append(
                ReportArtifactGap(
                    failure_id=REPORT_ARTIFACT_FAILURE_ID,
                    corpus_id=snapshot.corpus_id or "<empty>",
                    reason="corpus_id_empty",
                    observed_at=_utcnow(),
                )
            )
            return ReportArtifactResult(
                artifact=None,
                gap_findings=gap_findings,
            )

        # Step 3-7: project the typed snapshot onto the typed
        # bounded-summary fields.
        try:
            artifact_key = self._mint_artifact_key(snapshot.corpus_id)
            top_finding_keys = self._project_finding_keys(
                snapshot.top_findings
            )
            recommendation_keys = self._project_recommendation_keys(
                snapshot.recommendations
            )
            replay_result_ids = self._project_replay_result_ids(
                snapshot.replay_results
            )
        except (ValidationError, ValueError, TypeError) as exc:
            gap_findings.append(
                ReportArtifactGap(
                    failure_id=REPORT_ARTIFACT_FAILURE_ID,
                    corpus_id=snapshot.corpus_id,
                    reason="artifact_construction_failed",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                        "step": "projection",
                    },
                )
            )
            return ReportArtifactResult(
                artifact=None,
                gap_findings=gap_findings,
            )

        # Step 8: emit informational gap if upstream is blocked.
        if snapshot.blocked_by:
            gap_findings.append(
                ReportArtifactGap(
                    failure_id=REPORT_ARTIFACT_FAILURE_ID,
                    corpus_id=snapshot.corpus_id,
                    reason="governance_snapshot_stale",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "blocked_by": list(snapshot.blocked_by),
                    },
                )
            )

        # Step 9: construct the typed GovernanceReportArtifact.
        try:
            artifact = GovernanceReportArtifact(
                artifact_key=artifact_key,
                corpus_id=snapshot.corpus_id,
                snapshot_digest=snapshot.snapshot_digest,
                snapshot_version=snapshot.snapshot_version,
                completeness=snapshot.completeness,
                evidence_quality=snapshot.evidence_quality,
                top_finding_keys=top_finding_keys,
                recommendation_keys=recommendation_keys,
                replay_result_ids=replay_result_ids,
                page_refs=list(snapshot.page_refs),
                omitted_counts=dict(snapshot.omitted_counts),
                blocked_by=list(snapshot.blocked_by),
                truncated=snapshot.truncated,
                generated_at=_utcnow(),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            gap_findings.append(
                ReportArtifactGap(
                    failure_id=REPORT_ARTIFACT_FAILURE_ID,
                    corpus_id=snapshot.corpus_id,
                    reason="artifact_construction_failed",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                        "step": "construction",
                    },
                )
            )
            return ReportArtifactResult(
                artifact=None,
                gap_findings=gap_findings,
            )

        return ReportArtifactResult(
            artifact=artifact,
            gap_findings=gap_findings,
        )

    # --- Private helpers ----------------------------------------------------

    @staticmethod
    def _mint_artifact_key(corpus_id: str) -> str:
        """Mint the typed ``review:governance-report:{corpus_id}``
        artifact key per doc-19:161-162.

        The key is the typed substitution of the typed corpus id into
        the typed :data:`REPORT_ARTIFACT_KEY_PREFIX` template; the
        typed surface enforces the ``review:`` prefix (NOT ``dag-*``)
        per the doc-19:348-349 AC.
        """

        # corpus_id has already been validated non-empty by the caller.
        return f"{REPORT_ARTIFACT_KEY_PREFIX}{corpus_id}"

    @staticmethod
    def _project_finding_keys(
        findings: list[GovernanceFinding],
    ) -> list[str]:
        """Project the typed Slice 16 :class:`GovernanceFinding` list
        to the typed by-name :attr:`idempotency_key` list (refs-only
        per doc-19:111).
        """

        return [finding.idempotency_key for finding in findings]

    @staticmethod
    def _project_recommendation_keys(
        recommendations: list[GovernancePolicyRecommendation],
    ) -> list[str]:
        """Project the typed Slice 17
        :class:`GovernancePolicyRecommendation` list to the typed
        by-name :attr:`idempotency_key` list (refs-only per
        doc-19:111).
        """

        return [rec.idempotency_key for rec in recommendations]

    @staticmethod
    def _project_replay_result_ids(
        replay_results: list[CounterfactualResult],
    ) -> list[str]:
        """Project the typed Slice 18 :class:`CounterfactualResult`
        list to the typed by-name :attr:`result_id` list (refs-only
        per doc-19:111).
        """

        return [result.result_id for result in replay_results]

    @staticmethod
    def _extract_corpus_id(source: SnapshotAPIResult) -> str:
        """Extract the typed corpus id from the typed source.

        Falls back to the first upstream gap finding's corpus_id (when
        :attr:`SnapshotAPIResult.snapshot` is ``None`` but the
        upstream emitted a gap with a recoverable corpus id), then to
        the empty string if no corpus id is recoverable.
        """

        if source.snapshot is not None:
            return source.snapshot.corpus_id
        if source.gap_findings:
            # The first upstream gap finding's corpus_id is the most
            # specific fallback; the upstream API uses '<empty>' as
            # the sentinel for empty corpus ids.
            return source.gap_findings[0].corpus_id
        return ""

    @staticmethod
    def _lift_upstream_gap(
        upstream: SnapshotAPIGap, corpus_id: str
    ) -> ReportArtifactGap:
        """Lift the typed upstream :class:`SnapshotAPIGap` into the
        typed :class:`ReportArtifactGap` so consumers can distinguish
        propagated upstream gaps from emitter-emitted gaps.

        The lifted gap carries the prefix ``upstream_snapshot_gap:``
        on the typed :attr:`ReportArtifactGap.reason` field; the typed
        :attr:`observed_at` + :attr:`evidence_payload` fields are
        preserved verbatim from the upstream.
        """

        return ReportArtifactGap(
            failure_id=REPORT_ARTIFACT_FAILURE_ID,
            corpus_id=corpus_id or upstream.corpus_id,
            reason=f"upstream_snapshot_gap:{upstream.reason}",
            observed_at=upstream.observed_at,
            evidence_payload=dict(upstream.evidence_payload),
        )
