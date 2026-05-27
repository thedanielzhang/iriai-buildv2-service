"""Slice 15 fourth sub-slice -- scorecard persistence + bounded review projection.

Per ``docs/execution-control-plane/15-governance-metrics-and-scoring.md``
§ Refactoring Steps step 6 (lines 133-134): *"Store metric scorecards as
typed governance rows and bounded review projections such as
``review:governance-metrics:{corpus_id}``."*

Per doc-15:144-145 (Persistence And Artifact Compatibility): *"Scorecards
must include metric definition versions so later changes do not silently
rewrite historical meaning."*

Per doc-15:186-188 (Rollout And Rollback Notes): *"This is an analytical
slice. Rollback disables metric generation and keeps historical
scorecards as review artifacts. It must not change scheduler caps,
failure routing, or workflow policy without Slice 17 policy artifacts."*

Per doc-15:141-142: *"Metrics cite evidence-set refs and
implementation-log anchors, not raw bodies."*

Per doc-15:182 (Acceptance Criteria AC5): *"No metric depends on
unbounded artifact/event body scans."*

This module owns the scorecard-writer + bounded-review-projection surface
that consumes:

* The Slice 15 1st sub-slice typed-shape foundation
  (:class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceScorecard`
  + :func:`~iriai_build_v2.execution_control.governance_metrics.compute_scorecard_digest`
  + :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`).
* The Slice 15 3rd sub-slice
  :meth:`~iriai_build_v2.execution_control.governance_metric_extractor.MetricExtractor.extract`
  output (a ``list[GovernanceMetricValue]``).
* The Slice 13a shared
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  (cited by :attr:`GovernanceScorecard.baseline_refs`).

The writer is a **post-checkpoint governance projection observer** that
emits BOTH the typed :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceScorecard`
governance row AND the bounded review projection at
``review:governance-metrics:{corpus_id}`` per doc-15:134.

**Persistence + review-projection surface.** The writer's two public
methods:

* :meth:`ScorecardWriter.write_scorecard` -- builds a typed
  :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceScorecard`
  from a :class:`ScorecardWriterInputs` bundle. The returned scorecard
  carries the typed ``metrics`` list + ``baseline_refs`` list +
  ``warnings`` + ``incomplete_scopes`` + ``generated_at`` timestamp +
  ``corpus_id`` (the 6 doc-15:90-97 fields).
* :meth:`ScorecardWriter.write_review_projection` -- builds the bounded
  review projection dict at the typed
  :data:`REVIEW_PROJECTION_ID_PREFIX` key prefix per doc-15:134. The
  projection is BOUNDED via the :data:`DEFAULT_REVIEW_PROJECTION_CAP`
  LIMIT cap+1 truncation discipline (per
  ``IMPLEMENTATION_PROMPT_GOVERNANCE.md`` § "Bounded reads"); EMITS ONLY
  REFS (NOT raw bodies) per doc-15:141-142 + AC5 doc-15:182; CARRIES the
  scorecard digest (per :func:`compute_scorecard_digest`) for tamper
  detection per doc-15:144-145; CITES the metric definition versions
  per :attr:`GovernanceMetricValue.definition_version`.

**Bounded-read discipline (IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded
reads" + doc-15:141-142 + AC5 doc-15:182).** Per *"Reuse the typed
snapshot's ``LIMIT cap+1`` truncation discipline and the supervisor's
``SET LOCAL statement_timeout`` pattern. No artifact-body hydration on
the governance read path."* the review projection's metric list + ref
list are truncated at ``cap+1`` (the projection emits up to
``cap+1`` items so the caller can detect overflow). The default cap is
:data:`DEFAULT_REVIEW_PROJECTION_CAP` (200; well above the v1 15-metric
contract).

**Refs-only discipline (doc-15:141-142 + AC5 doc-15:182).** The review
projection carries ONLY:

* The metric value's ``definition_name`` + ``definition_version`` +
  ``unit`` + ``confidence`` + ``data_quality`` + ``scope`` +
  ``exclusions`` (typed shape control fields).
* The scorecard's ``baseline_refs`` projected as
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  cited refs (the typed ref shape carries authority + ref_id + digest
  + quality + completeness — NOT raw bodies).
* The metric value's ``evidence_refs`` truncated at ``cap+1`` per the
  bounded-read discipline.

The projection NEVER carries raw artifact bodies, raw event payloads,
or any field that requires unbounded scanning.

**Digest discipline (doc-15:144-145).** Per *"Scorecards must include
metric definition versions so later changes do not silently rewrite
historical meaning."* the review projection carries the scorecard
digest (:func:`compute_scorecard_digest`) for tamper detection: two
re-runs against the same scorecard MUST produce byte-identical digests;
a digest change signals a re-projection.

**Rollback discipline (doc-15:186-188).** Per *"Rollback disables
metric generation and keeps historical scorecards as review artifacts."*
the writer's typed-shape outputs are immutable rows: future re-projections
emit NEW scorecards (with new ``generated_at`` timestamps + new
digests) rather than mutating historical scorecards. The
:meth:`ScorecardWriter.write_scorecard` surface is a pure projection;
the actual storage mechanism is the caller's responsibility (e.g. a
Postgres ``governance_scorecard:*`` row insert + a separate
``review:governance-metrics:{corpus_id}`` projection insert).

**Non-blocking failure routing discipline (doc-14:242-243 + doc-15
inherited).** The writer mirrors the Slice 14 2nd-sub-slice + Slice 15
2nd-sub-slice non-blocking observer precedents: structural failures
during persistence project onto the typed :class:`ScorecardPersistenceGap`
finding shape with the typed failure id
:data:`SCORECARD_PERSISTENCE_FAILURE_ID` registered under the EXISTING
``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` with
the EXISTING NON-blocking :data:`RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action). The writer NEVER raises a failure to the
caller per the auto-memory ``feedback_no_silent_degradation`` rule
(every failure produces a typed gap finding; the caller observes the
gap on :attr:`ScorecardWriter.gap_findings`).

**Implementation discipline.** Stdlib (``datetime``) + Pydantic v2 +
Slice 13a modules (``..workflows.develop.governance.models``) + Slice
15 1st sub-slice (``.governance_metrics``) + Slice 15 3rd sub-slice
(``.governance_metric_extractor`` for typed-input reuse only) only.
NO imports from ``governance/`` outside ``governance.models``. NO
imports from ``workflows/develop/execution/phases/`` / ``supervisor`` /
``dashboard``. NO mutation of any existing ``execution_control/``
module (per the implementer prompt § "Non-negotiables").

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.commit_provenance_writer`
(Slice 14 2nd sub-slice) + :mod:`iriai_build_v2.execution_control.governance_metric_extractor`
(Slice 15 2nd+3rd sub-slice) + :mod:`iriai_build_v2.execution_control.governance_metrics`
(Slice 15 1st sub-slice): ``BaseModel`` subclasses with
``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed as a typed
``ValidationError`` rather than being silently absorbed. Per the
auto-memory ``feedback_flat_structured_output`` rule the typed control
fields are flat primitives. Per the auto-memory
``feedback_no_silent_degradation`` rule every failure produces a typed
:class:`ScorecardPersistenceGap`. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 14 2nd sub-slice precedent verbatim without introducing new
abstractions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from iriai_build_v2.execution_control.governance_metrics import (
    GovernanceMetricValue,
    GovernanceScorecard,
    compute_scorecard_digest,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


__all__ = [
    # Typed inputs / outputs (mirrors Slice 14 2nd sub-slice precedent).
    "ScorecardWriterInputs",
    "ScorecardPersistenceGap",
    # The NEW typed failure id under EXISTING evidence_corruption
    # failure_class (per chunk-shape point 3; REUSES Slice 14 2nd sub-slice
    # retry_governance_projection NON-blocking RouteAction).
    "SCORECARD_PERSISTENCE_FAILURE_ID",
    # Review projection id prefix (per doc-15:134).
    "REVIEW_PROJECTION_ID_PREFIX",
    # Bounded review-projection cap (per IMPLEMENTATION_PROMPT_GOVERNANCE.md
    # § "Bounded reads").
    "DEFAULT_REVIEW_PROJECTION_CAP",
    # Pure helpers.
    "compute_review_projection_id",
    # The writer class.
    "ScorecardWriter",
]


# --- Typed failure id (doc-15:140-145 + doc-14:242-243 NON-BLOCKING) --------


SCORECARD_PERSISTENCE_FAILURE_ID: Literal[
    "governance_scorecard_persistence_failed"
] = "governance_scorecard_persistence_failed"
"""Doc-15:140-145 + doc-14:242-243 -- the typed failure id the scorecard
writer projects onto when a persistence step fails structurally.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 15 pattern matches the Slice 14 + Slice 15 2nd sub-slice non-blocking
governance projection observer.

The Slice 14 + Slice 15 2nd sub-slice precedents are the source-of-truth
for the non-blocking governance-projection failure-routing discipline:

* :mod:`iriai_build_v2.execution_control.commit_provenance_writer`
  (Slice 14 2nd) defines ``line_provenance_gap`` +
  ``governance_evidence_conflict``.
* :mod:`iriai_build_v2.execution_control.governance_metric_extractor`
  (Slice 15 2nd) defines ``governance_metric_extraction_failed``.

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to governance
scorecard persistence failures (this slice is also a post-checkpoint
governance projection observer).
"""


# --- Review projection id (doc-15:134) --------------------------------------


REVIEW_PROJECTION_ID_PREFIX: Literal["review:governance-metrics:"] = (
    "review:governance-metrics:"
)
"""Doc-15:134 -- the typed prefix for the bounded review projection id.

Per *"Store metric scorecards as typed governance rows and bounded review
projections such as ``review:governance-metrics:{corpus_id}``."* the
projection id is constructed by concatenating this prefix with the
scorecard's ``corpus_id`` field. The :func:`compute_review_projection_id`
helper performs the concatenation.

The doc-15:50-54 compatible-deviations rule allows additional review
projection id schemes, but the v1 contract mandates this prefix +
``corpus_id`` suffix verbatim so the doc-15:142-143 backward-compatibility
contract holds (*"Existing ``review:dag-sizing:*`` artifacts remain
readable and may be imported as legacy metric evidence..."*).
"""


# --- Bounded review-projection cap (IMPLEMENTATION_PROMPT_GOVERNANCE.md §
#     "Bounded reads") ---------------------------------------------------------


DEFAULT_REVIEW_PROJECTION_CAP: int = 200
"""IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" -- the default
LIMIT cap for the bounded review projection's metric list + ref list.

Per *"Reuse the typed snapshot's ``LIMIT cap+1`` truncation discipline
and the supervisor's ``SET LOCAL statement_timeout`` pattern. No
artifact-body hydration on the governance read path."* the projection
emits up to ``cap+1`` items so the caller can detect overflow (the
``+1`` is the sentinel that signals "the cap was reached + there may be
more"). The default cap is 200, comfortably above the v1 doc-15:99-115
15-metric contract; future per-corpus caps may tighten via the
:meth:`ScorecardWriter.write_review_projection` ``cap`` parameter.

The cap applies independently to:

* The metric list (default: emit at most ``cap+1`` of
  :attr:`GovernanceScorecard.metrics`).
* The baseline_refs list (default: emit at most ``cap+1`` of
  :attr:`GovernanceScorecard.baseline_refs`).
* Each metric's evidence_refs list (default: emit at most ``cap+1``
  per :attr:`GovernanceMetricValue.evidence_refs`).
"""


def compute_review_projection_id(corpus_id: str) -> str:
    """Construct the typed review-projection id per doc-15:134.

    Returns ``review:governance-metrics:{corpus_id}`` -- the typed
    prefix concatenated with the scorecard's ``corpus_id`` field.

    Per doc-15:134 the review projection id is the typed key the
    governance review artifact store uses to read the bounded review
    projection; the caller is responsible for the actual insert
    operation (the writer is a pure projection per the doc-15:140
    "Governance metrics are derived rows. They do not change execution
    state." discipline).

    The construction is intentionally a pure-string concatenation
    (NOT a typed BaseModel) so the projection id is trivial to
    serialise + consume across process boundaries.
    """

    return f"{REVIEW_PROJECTION_ID_PREFIX}{corpus_id}"


# --- Typed inputs (chunk-shape point 2) -------------------------------------


class ScorecardWriterInputs(BaseModel):
    """Doc-15:133-134 step 6 + doc-15:90-97 -- typed bundle of all inputs
    the scorecard writer consumes.

    The bundle composes:

    * ``corpus_id`` -- the corpus identifier the scorecard groups against
      (per doc-15:91 same shape as
      :attr:`~iriai_build_v2.execution_control.governance_metrics.GovernanceScorecard.corpus_id`).
    * ``metrics`` -- the list of
      :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
      values the writer composes into the
      :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceScorecard`.
      This is the typed output of
      :meth:`~iriai_build_v2.execution_control.governance_metric_extractor.MetricExtractor.extract`
      (the Slice 15 3rd sub-slice's real-arithmetic projection).
    * ``baseline_refs`` -- the list of Slice 13a shared
      :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
      baseline references the scorecard grounds on (per doc-15:94).
    * ``warnings`` -- the list of warning-reason strings (per doc-15:96).
    * ``incomplete_scopes`` -- the list of incomplete-scope descriptors
      (per doc-15:95; free-form dicts so the writer surface can carry
      rich incomplete-scope shapes).

    Per doc-15:144-145 the bundle's ``metrics`` list MUST carry the
    metric definition versions (via
    :attr:`GovernanceMetricValue.definition_version`) so later changes
    do not silently rewrite historical meaning; the
    :meth:`ScorecardWriter.write_scorecard` projection passes the
    metric values through verbatim (the versions are preserved).

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.
    """

    # ``extra="forbid"`` aligns with the Slice 15 1st sub-slice precedent
    # at src/iriai_build_v2/execution_control/governance_metrics.py:245 +
    # the Slice 15 2nd sub-slice precedent at
    # src/iriai_build_v2/execution_control/governance_metric_extractor.py:399
    # + the Slice 14 2nd sub-slice precedent at
    # src/iriai_build_v2/execution_control/commit_provenance_writer.py:249
    # -- unknown fields fail closed as a typed ``ValidationError`` rather
    # than being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    corpus_id: str
    """Doc-15:91 -- the corpus identifier the scorecard groups against
    (e.g. ``"8ac124d6"`` for the calibration fixture per doc-15:135-136
    step 7; future feature ids for production scorecards)."""

    metrics: list[GovernanceMetricValue]
    """Doc-15:93 + chunk-shape point 2 -- the list of
    :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
    values produced by the Slice 15 3rd sub-slice
    :meth:`~iriai_build_v2.execution_control.governance_metric_extractor.MetricExtractor.extract`
    surface. The writer composes these into the typed
    :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceScorecard`
    verbatim (no mutation; preserves the metric definition versions per
    doc-15:144-145)."""

    baseline_refs: list[GovernanceEvidenceRef] = Field(default_factory=list)
    """Doc-15:94 -- the list of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    baseline references the scorecard grounds on.

    Per doc-15:141-142 + AC5 doc-15:182 the baseline refs cite typed
    evidence-set refs (NOT raw artifact bodies); the
    :class:`GovernanceEvidenceRef` typed surface enforces this
    no-raw-body-hydration discipline at construction."""

    warnings: list[str] = Field(default_factory=list)
    """Doc-15:96 -- the list of warning-reason strings. Free-form
    strings naming non-blocking issues (e.g. ``"legacy_heavy_corpus"`` /
    ``"stale_baseline"`` / ``"missing_typed_evidence_for_scope"``)."""

    incomplete_scopes: list[dict[str, Any]] = Field(default_factory=list)
    """Doc-15:95 -- the list of incomplete-scope descriptors. Per
    doc-15:148-150 + doc-15:163-164 the insufficient-implementation-journal
    case emits a scorecard with :attr:`incomplete_scopes` populated so
    consumers can see which scopes lacked sufficient evidence; future
    metric-extractor sub-slices may tighten to a typed shape once the
    extractor surface crystallises."""


# --- Typed gap finding (chunk-shape point 2; mirrors Slice 14) --------------


class ScorecardPersistenceGap(BaseModel):
    """Typed governance-gap finding produced when the scorecard writer
    fails to persist a scorecard or review projection structurally.

    Mirrors the Slice 14
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    + Slice 15 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_metric_extractor.GovernanceMetricExtractionGap`
    shapes verbatim per the chunk-shape point 2 in STATUS.md Next safe
    action.

    Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED here
    per doc-15:140-145 governance-projection discipline) the finding is
    NON-blocking: the caller MUST NOT propagate it to the executor /
    checkpoint / merge-queue / resume code paths. The corresponding
    typed failure id :data:`SCORECARD_PERSISTENCE_FAILURE_ID`
    (``governance_scorecard_persistence_failed``) registers under the
    EXISTING ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
    NOT a new route action).
    """

    # ``extra="forbid"`` aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["governance_scorecard_persistence_failed"]
    """Doc-15 + doc-14:192-201 -- the typed failure id. Registers under
    the EXISTING ``evidence_corruption`` failure_class with NON-blocking
    routing per doc-14:242-243 + doc-15:140-145."""

    corpus_id: str
    """The corpus scope of the failed persistence (same as the
    :attr:`ScorecardWriterInputs.corpus_id`)."""

    scorecard_digest: str | None
    """The scorecard digest (per :func:`compute_scorecard_digest`)
    computed at the time of failure, or ``None`` if the failure happened
    before the digest could be computed.

    The digest is the cross-process tamper-detection anchor per
    doc-15:144-145; preserving it in the gap finding lets downstream
    consumers correlate the gap with the historical scorecard whose
    persistence failed (per doc-15:186-188 rollback discipline:
    historical scorecards remain as review artifacts even when newer
    re-projections fail)."""

    review_projection_id: str | None
    """The bounded review projection id (per
    :func:`compute_review_projection_id`) the failure targeted, or
    ``None`` if the failure happened before the projection id could be
    computed."""

    reason: str
    """Free-form gap reason (e.g.
    ``scorecard_metric_count_exceeds_cap``,
    ``review_projection_digest_mismatch``)."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding (e.g.
    the metric definition names involved, the projection cap that was
    exceeded). Free-form per the doc-14:192-201 + doc-15 governance-finding
    contract."""


# --- The scorecard writer (chunk-shape point 2) -----------------------------


class ScorecardWriter:
    """Scorecard writer + bounded review projection emitter
    (doc-15:133-134 step 6).

    Per *"Store metric scorecards as typed governance rows and bounded
    review projections such as ``review:governance-metrics:{corpus_id}``."*
    the writer consumes the Slice 15 1st sub-slice typed-shape foundation
    (:class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceScorecard`)
    + the Slice 15 3rd sub-slice
    :meth:`~iriai_build_v2.execution_control.governance_metric_extractor.MetricExtractor.extract`
    output (a ``list[GovernanceMetricValue]``) and projects them onto:

    1. A typed :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceScorecard`
       governance row (the typed persistence shape).
    2. A bounded review projection dict (the typed review-artifact shape
       keyed by :func:`compute_review_projection_id`).

    **Bounded-read discipline (IMPLEMENTATION_PROMPT_GOVERNANCE.md §
    "Bounded reads" + doc-15:141-142 + AC5 doc-15:182).** The review
    projection's metric list + ref list use the ``LIMIT cap+1``
    truncation discipline: the writer emits up to ``cap+1`` items so the
    caller can detect overflow (per the typed snapshot pattern). The
    default cap is :data:`DEFAULT_REVIEW_PROJECTION_CAP`.

    **Refs-only discipline (doc-15:141-142 + AC5 doc-15:182).** The
    review projection carries ONLY typed control fields + cited refs
    (:class:`GovernanceEvidenceRef` typed shape) -- NEVER raw artifact
    bodies. The :class:`GovernanceEvidenceRef` typed surface itself
    enforces this no-raw-body discipline at construction.

    **Digest discipline (doc-15:144-145).** The review projection carries
    the scorecard digest (per :func:`compute_scorecard_digest`) for
    tamper detection: two re-runs against the same scorecard MUST
    produce byte-identical digests.

    **Rollback discipline (doc-15:186-188).** The writer's typed-shape
    outputs are immutable rows: future re-projections emit NEW
    scorecards (with new ``generated_at`` timestamps + new digests)
    rather than mutating historical scorecards. The
    :meth:`write_scorecard` surface is a pure projection; the actual
    storage mechanism (e.g. a Postgres ``governance_scorecard:*`` row
    insert) is the caller's responsibility.

    **Non-blocking discipline.** The writer NEVER raises a failure to
    the caller. Any structural failure projects onto a typed
    :class:`ScorecardPersistenceGap` finding emitted on the
    :attr:`ScorecardWriter.gap_findings` list (post-call).

    Per the auto-memory ``feedback_no_overengineer_use_library`` rule
    the writer mirrors the Slice 14 2nd sub-slice + Slice 15 2nd sub-slice
    precedents (single class with one or more public projection methods)
    without introducing new abstractions.

    Example usage::

        from iriai_build_v2.execution_control.governance_metric_extractor \
            import MetricExtractor, MetricExtractorInputs
        from iriai_build_v2.execution_control.governance_scorecard_writer \
            import ScorecardWriter, ScorecardWriterInputs

        extractor = MetricExtractor()
        metric_values = extractor.extract(extractor_inputs)

        writer = ScorecardWriter()
        scorecard = writer.write_scorecard(
            ScorecardWriterInputs(
                corpus_id="8ac124d6",
                metrics=metric_values,
                baseline_refs=[],
                warnings=[],
                incomplete_scopes=[],
            )
        )
        projection = writer.write_review_projection(scorecard)
        # caller persists scorecard + projection.
    """

    def __init__(self) -> None:
        """Construct a scorecard writer.

        The writer is stateless aside from the :attr:`gap_findings`
        accumulator the public :meth:`write_scorecard` +
        :meth:`write_review_projection` surfaces populate. Each call
        to a public method RESETS the accumulator (so per-call gap
        findings remain bounded; callers that need cross-call gap
        accumulation should snapshot the property after each call).
        """

        self._gap_findings: list[ScorecardPersistenceGap] = []

    @property
    def gap_findings(self) -> list[ScorecardPersistenceGap]:
        """The list of :class:`ScorecardPersistenceGap` findings the
        most-recent public-method call produced.

        Per the Slice 14
        :class:`~iriai_build_v2.execution_control.commit_provenance_writer.GitProvenanceWriter`
        + Slice 15 2nd sub-slice
        :class:`~iriai_build_v2.execution_control.governance_metric_extractor.MetricExtractor`
        precedents the writer NEVER raises a failure to the caller --
        every structural failure projects onto a typed gap finding.
        """

        return list(self._gap_findings)

    def write_scorecard(
        self,
        inputs: ScorecardWriterInputs,
    ) -> GovernanceScorecard:
        """Build a typed :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceScorecard`
        from a :class:`ScorecardWriterInputs` bundle (doc-15:133-134
        step 6 + doc-15:90-97).

        Returns the typed :class:`GovernanceScorecard` with the 6
        doc-15:90-97 fields populated:

        * ``corpus_id`` -- carried verbatim from
          :attr:`ScorecardWriterInputs.corpus_id`.
        * ``generated_at`` -- ``datetime.now(timezone.utc)`` (the
          cross-process freshness anchor per doc-15:131-132 step 5).
        * ``metrics`` -- carried verbatim from
          :attr:`ScorecardWriterInputs.metrics` (preserves the metric
          definition versions per doc-15:144-145).
        * ``baseline_refs`` -- carried verbatim from
          :attr:`ScorecardWriterInputs.baseline_refs`.
        * ``incomplete_scopes`` -- carried verbatim from
          :attr:`ScorecardWriterInputs.incomplete_scopes`.
        * ``warnings`` -- carried verbatim from
          :attr:`ScorecardWriterInputs.warnings`.

        Per doc-14:242-243 + doc-15:140-145 NEVER raises a failure to
        the caller. Any structural failure projects onto a typed
        :class:`ScorecardPersistenceGap` accumulated on
        :attr:`gap_findings`; the returned scorecard in that case
        still carries the typed shape (with the most-conservative
        projection) so the downstream Slice 17 policy layer can block
        recommendations on the corpus.

        The method RESETS the :attr:`gap_findings` accumulator at
        entry; per-call gap findings remain bounded.
        """

        # Reset per-call accumulator (mirrors Slice 15 2nd sub-slice
        # MetricExtractor.extract pattern).
        self._gap_findings = []

        # Build the typed scorecard. The Pydantic v2 model_validate path
        # at construction enforces all typed-shape invariants
        # (ConfigDict(extra="forbid") on every nested model + Literal
        # range enforcement) per the auto-memory
        # feedback_no_silent_degradation rule. Any ValidationError
        # raised at construction projects onto a typed gap finding
        # rather than being raised to the caller (non-blocking observer
        # contract per doc-14:242-243 + doc-15:140-145).
        try:
            scorecard = GovernanceScorecard(
                corpus_id=inputs.corpus_id,
                generated_at=datetime.now(timezone.utc),
                metrics=list(inputs.metrics),
                baseline_refs=list(inputs.baseline_refs),
                incomplete_scopes=list(inputs.incomplete_scopes),
                warnings=list(inputs.warnings),
            )
        except Exception as exc:
            # Defensive: projection construction should never fail given
            # the typed inputs above, but the writer is non-blocking per
            # the doc-14:242-243 contract. Project onto a typed gap
            # finding + return a conservative fallback scorecard so the
            # downstream Slice 17 policy layer can block recommendations.
            self._gap_findings.append(
                ScorecardPersistenceGap(
                    failure_id=SCORECARD_PERSISTENCE_FAILURE_ID,
                    corpus_id=inputs.corpus_id,
                    scorecard_digest=None,
                    review_projection_id=compute_review_projection_id(
                        inputs.corpus_id
                    ),
                    reason=f"scorecard_construction_failed: {type(exc).__name__}",
                    evidence_payload={"error_detail": str(exc)[:500]},
                )
            )
            # Conservative fallback: empty scorecard so downstream
            # consumers see "nothing was projected" rather than a
            # corrupted typed shape.
            scorecard = GovernanceScorecard(
                corpus_id=inputs.corpus_id,
                generated_at=datetime.now(timezone.utc),
                metrics=[],
                baseline_refs=[],
                incomplete_scopes=[
                    {
                        "scope": "scorecard_construction",
                        "reason": "construction_failed",
                    }
                ],
                warnings=["scorecard_construction_failed"],
            )

        return scorecard

    def write_review_projection(
        self,
        scorecard: GovernanceScorecard,
        *,
        cap: int | None = None,
    ) -> dict[str, Any]:
        """Build the bounded review projection dict for a
        :class:`GovernanceScorecard` at the typed
        :data:`REVIEW_PROJECTION_ID_PREFIX` key prefix per doc-15:134.

        The projection's structure:

        * ``review_projection_id`` -- the typed key per
          :func:`compute_review_projection_id`.
        * ``corpus_id`` -- carried from :attr:`GovernanceScorecard.corpus_id`.
        * ``generated_at`` -- ISO-8601 string projection of
          :attr:`GovernanceScorecard.generated_at`.
        * ``scorecard_digest`` -- the SHA-256 digest from
          :func:`~iriai_build_v2.execution_control.governance_metrics.compute_scorecard_digest`
          for tamper detection per doc-15:144-145.
        * ``metric_definition_versions`` -- the per-name version map
          extracted from
          :attr:`GovernanceMetricValue.definition_version` per
          doc-15:144-145.
        * ``metrics`` -- the metric values projected as compact dicts
          carrying ONLY typed control fields + cited refs (NEVER raw
          bodies), truncated at ``cap+1`` per the bounded-read
          discipline.
        * ``baseline_refs`` -- the baseline refs projected via
          ``model_dump`` (typed-ref shape; NEVER raw bodies), truncated
          at ``cap+1``.
        * ``incomplete_scopes`` -- carried verbatim from
          :attr:`GovernanceScorecard.incomplete_scopes`.
        * ``warnings`` -- carried verbatim from
          :attr:`GovernanceScorecard.warnings`.
        * ``metric_truncated`` / ``baseline_refs_truncated`` -- bool
          flags signalling whether the cap was reached (so the caller
          can detect overflow per the LIMIT cap+1 discipline).

        Per IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads"
        the projection EMITS at most ``cap+1`` items in each list (so
        the caller can detect overflow by checking
        ``len(projection["metrics"]) > cap``).

        Per doc-15:141-142 + AC5 doc-15:182 the projection EMITS ONLY
        REFS (NOT raw bodies). The
        :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
        typed surface itself enforces this no-raw-body discipline.

        Per doc-15:144-145 the projection CARRIES the scorecard digest
        (for tamper detection) + the metric definition versions (so
        later changes do not silently rewrite historical meaning).

        Per doc-14:242-243 + doc-15:140-145 NEVER raises a failure to
        the caller. Any structural failure projects onto a typed
        :class:`ScorecardPersistenceGap` accumulated on
        :attr:`gap_findings`; the returned projection in that case
        still carries the typed shape (with the most-conservative
        projection) so the downstream Slice 17 policy layer can block
        recommendations on the corpus.

        :param scorecard: the typed
            :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceScorecard`
            to project.
        :param cap: optional override for the LIMIT cap+1 truncation
            threshold; defaults to :data:`DEFAULT_REVIEW_PROJECTION_CAP`.
        """

        # Reset per-call accumulator (mirrors Slice 15 2nd sub-slice
        # MetricExtractor.extract pattern).
        self._gap_findings = []

        effective_cap = cap if cap is not None else DEFAULT_REVIEW_PROJECTION_CAP

        # Compute the digest defensively: if compute_scorecard_digest
        # fails (should never happen given the typed shape but the
        # writer is non-blocking) project onto a typed gap finding.
        try:
            digest = compute_scorecard_digest(scorecard)
        except Exception as exc:
            self._gap_findings.append(
                ScorecardPersistenceGap(
                    failure_id=SCORECARD_PERSISTENCE_FAILURE_ID,
                    corpus_id=scorecard.corpus_id,
                    scorecard_digest=None,
                    review_projection_id=compute_review_projection_id(
                        scorecard.corpus_id
                    ),
                    reason=f"scorecard_digest_failed: {type(exc).__name__}",
                    evidence_payload={"error_detail": str(exc)[:500]},
                )
            )
            digest = ""

        # Per-name version map per doc-15:144-145.
        # The dict preserves the LAST observed version when multiple
        # metric values share a definition_name (defence-in-depth; v1
        # scorecards have a one-to-one definition->value mapping).
        metric_definition_versions: dict[str, str] = {}
        for metric in scorecard.metrics:
            metric_definition_versions[metric.definition_name] = metric.definition_version

        # ── LIMIT cap+1 truncation of the metric list per
        #    IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" ───────────
        # Emit at most cap+1 items so the caller can detect overflow
        # (the +1 is the sentinel that signals "the cap was reached +
        # there may be more").
        metric_limit = effective_cap + 1
        truncated_metrics = scorecard.metrics[:metric_limit]
        metric_truncated = len(scorecard.metrics) > effective_cap

        # ── LIMIT cap+1 truncation of the baseline_refs list ────────────
        baseline_refs_limit = effective_cap + 1
        truncated_baseline_refs = scorecard.baseline_refs[:baseline_refs_limit]
        baseline_refs_truncated = len(scorecard.baseline_refs) > effective_cap

        # Project the metric values to bounded review-projection dicts.
        # The projection carries ONLY typed control fields + cited refs
        # (NEVER raw bodies). The evidence_refs list per metric is also
        # truncated at cap+1 per the bounded-read discipline.
        projected_metrics: list[dict[str, Any]] = []
        for metric in truncated_metrics:
            evidence_refs_limit = effective_cap + 1
            metric_evidence_refs = metric.evidence_refs[:evidence_refs_limit]
            metric_evidence_refs_truncated = (
                len(metric.evidence_refs) > effective_cap
            )
            projected_metrics.append(
                {
                    "definition_name": metric.definition_name,
                    "definition_version": metric.definition_version,
                    "scope": dict(metric.scope),
                    "value": metric.value,
                    "unit": metric.unit,
                    "confidence": metric.confidence,
                    "data_quality": metric.data_quality,
                    "source_mix": dict(metric.source_mix),
                    # evidence_refs use mode='json' to project to
                    # cross-process stable dict shapes per the Slice 13a
                    # GovernanceEvidenceRef typed contract; this preserves
                    # the typed ref shape (authority + ref_id + digest +
                    # quality + completeness) WITHOUT inserting any raw
                    # body field. The mode='json' projection lowers
                    # datetime to ISO-8601 string per the Pydantic v2
                    # canonical-projection contract.
                    "evidence_refs": [
                        ref.model_dump(mode="json") for ref in metric_evidence_refs
                    ],
                    "evidence_refs_truncated": metric_evidence_refs_truncated,
                    "exclusions": list(metric.exclusions),
                }
            )

        # Project the baseline_refs to cited refs per doc-15:141-142 +
        # AC5 doc-15:182 (NEVER raw bodies). Mode='json' projection per
        # the Slice 13a GovernanceEvidenceRef contract.
        projected_baseline_refs = [
            ref.model_dump(mode="json") for ref in truncated_baseline_refs
        ]

        # The full review projection dict.
        review_projection_id = compute_review_projection_id(scorecard.corpus_id)
        # generated_at projects to ISO-8601 string per the doc-13:201-204
        # canonical-JSON discipline.
        generated_at_iso = scorecard.generated_at.isoformat()
        projection: dict[str, Any] = {
            "review_projection_id": review_projection_id,
            "corpus_id": scorecard.corpus_id,
            "generated_at": generated_at_iso,
            "scorecard_digest": digest,
            "metric_definition_versions": metric_definition_versions,
            "metrics": projected_metrics,
            "metric_truncated": metric_truncated,
            "baseline_refs": projected_baseline_refs,
            "baseline_refs_truncated": baseline_refs_truncated,
            "incomplete_scopes": [dict(scope) for scope in scorecard.incomplete_scopes],
            "warnings": list(scorecard.warnings),
        }
        return projection
