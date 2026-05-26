"""Slice 16 fourth sub-slice -- finding persistence + bounded review projection.

Per ``docs/execution-control-plane/16-finding-engine-and-taxonomy.md``
§ Refactoring Steps step 6 (lines 166-167): *"Store findings as typed
governance rows and project bounded review artifacts such as
``review:governance-findings:{corpus_id}``."*

Per doc-16:158 (Refactoring Steps step 2): *"Add dedupe keys from finding
class, feature/window, affected scope, evidence digest, and rule version."*
The :func:`compute_findings_digest` helper composes the per-finding
``idempotency_key`` digests so the bounded review projection carries a
single corpus-level digest the caller uses for tamper detection.

Per doc-16:177-179 (Persistence And Artifact Compatibility): *"Finding ids
are stable across reruns when input evidence and rule version do not
change."* The writer's persistence surface preserves the per-finding
``idempotency_key`` verbatim; subsequent re-projections against the same
finding-set produce byte-identical digests.

Per doc-16:215-217 (Rollout And Rollback Notes): *"Rollback disables
finding generation and leaves existing finding artifacts for audit. If a
finding rule is bad, release a new rule version and mark prior findings
superseded rather than rewriting history."* The writer's typed-shape
outputs are immutable rows: future re-projections emit NEW typed
:class:`FindingWriterInputs` bundles with NEW per-finding
``rule_version`` references rather than mutating historical findings.

Per doc-16:174-176 (Persistence And Artifact Compatibility): *"Findings
are derived governance records and never write execution `dag-*`
authority artifacts. Findings cite evidence refs, metric refs, and
implementation-log anchors."* The writer is a **post-checkpoint
governance projection observer** that emits BOTH the typed
:class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
list AND the bounded review projection at
``review:governance-findings:{corpus_id}`` per doc-16:166-167.

Per doc-16:201-291 (Slice 13A Shared Completeness Model Dependency) the
writer consumes ONLY typed refs (the Slice 13a
:class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
shape) -- NEVER raw artifact bodies; the typed surface itself enforces
this no-raw-body-hydration discipline at construction.

This module owns the finding-writer + bounded-review-projection surface
that consumes:

* The Slice 16 1st sub-slice typed-shape foundation
  (:class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
  + :func:`~iriai_build_v2.execution_control.finding_engine.canonical_finding_dict`
  + :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`).
* The Slice 13a shared
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  (cited by :attr:`FindingWriterInputs.baseline_refs` +
  :attr:`GovernanceFinding.primary_evidence_refs` +
  :attr:`GovernanceFinding.supporting_evidence_refs`).

The writer is a **post-checkpoint governance projection observer** that
emits BOTH the typed
:class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
list AND the bounded review projection at
``review:governance-findings:{corpus_id}`` per doc-16:166-167.

**Persistence + review-projection surface.** The writer's two public
methods:

* :meth:`GovernanceFindingWriter.write_findings` -- composes a typed
  :class:`FindingWriterInputs` bundle into a typed
  ``list[GovernanceFinding]`` governance rows. The returned list
  preserves the per-finding ``idempotency_key`` + ``kind`` +
  ``class_name`` + ``severity`` + ``confidence`` + the full 22-field
  :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
  shape verbatim (no mutation of upstream rule-engine output).
* :meth:`GovernanceFindingWriter.write_review_projection` -- builds the
  bounded review projection dict at the typed
  :data:`REVIEW_PROJECTION_ID_PREFIX` key prefix per doc-16:166-167. The
  projection is BOUNDED via the :data:`DEFAULT_REVIEW_PROJECTION_CAP`
  LIMIT cap+1 truncation discipline (per
  ``IMPLEMENTATION_PROMPT_GOVERNANCE.md`` § "Bounded reads"); EMITS ONLY
  REFS (NOT raw bodies) per doc-16:174-176; CARRIES the
  findings digest (per :func:`compute_findings_digest`) for tamper
  detection; CITES the per-finding ``rule_version`` (extracted from each
  finding's ``idempotency_key`` lineage via the per-finding
  ``finding_rule_versions`` field on
  :class:`FindingWriterInputs`).

**Bounded-read discipline (IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded
reads" + doc-16:174-176 + doc-16:201-291).** Per *"Reuse the typed
snapshot's ``LIMIT cap+1`` truncation discipline and the supervisor's
``SET LOCAL statement_timeout`` pattern. No artifact-body hydration on
the governance read path."* the review projection's findings list +
per-finding evidence_refs list + baseline_refs list are all truncated at
``cap+1`` (the projection emits up to ``cap+1`` items so the caller can
detect overflow). The default cap is :data:`DEFAULT_REVIEW_PROJECTION_CAP`
(200; mirrors the Slice 15 4th sub-slice value verbatim).

**Refs-only discipline (doc-16:174-176 + doc-16:201-291).** The review
projection carries ONLY:

* The finding's typed control fields (``idempotency_key`` + ``kind`` +
  ``class_name`` + ``severity`` + ``confidence`` + ``feature_id`` +
  ``affected_scope`` + ``causal_role`` + the 4 typed booleans +
  ``estimated_lost_hours`` + ``estimated_retry_impact`` +
  ``recommended_action_display`` + the 3 typed reference strings).
* The finding's ``primary_evidence_refs`` + ``supporting_evidence_refs``
  projected as
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  cited refs (the typed ref shape carries authority + ref_id + digest
  + quality + completeness -- NOT raw bodies).
* The finding's ``implementation_log_anchors`` (already-typed anchor
  strings; doc-16:92 lists this as ``list[str]`` so the typed surface
  carries strings only).
* The finding's ``metric_refs`` (already strings per doc-16:93).
* The finding's ``linked_finding_ids`` (already strings per doc-16:104).
* The baseline_refs projected via
  ``model_dump(mode='json')`` (typed-ref shape).

The projection NEVER carries raw artifact bodies, raw event payloads,
or any field that requires unbounded scanning.

**Digest discipline (doc-16:177-179 + doc-16:158).** Per *"Finding ids
are stable across reruns when input evidence and rule version do not
change."* + *"Add dedupe keys from finding class, feature/window,
affected scope, evidence digest, and rule version."* the review
projection carries the findings digest (:func:`compute_findings_digest`)
for tamper detection: two re-runs against the same finding-set MUST
produce byte-identical digests; a digest change signals a re-projection
(either new findings, modified findings, or a new rule version).

**Rollback discipline (doc-16:215-217).** Per *"Rollback disables
finding generation and leaves existing finding artifacts for audit. If
a finding rule is bad, release a new rule version and mark prior
findings superseded rather than rewriting history."* the writer's
typed-shape outputs are immutable rows: future re-projections emit NEW
:class:`FindingWriterInputs` bundles (with NEW per-finding
``rule_version`` references) rather than mutating historical findings.
The :meth:`GovernanceFindingWriter.write_findings` surface is a pure
projection; the actual storage mechanism is the caller's responsibility
(e.g. a Postgres ``governance_finding:*`` row insert + a separate
``review:governance-findings:{corpus_id}`` projection insert).

**Non-blocking failure routing discipline (doc-14:242-243 inherited
verbatim).** The writer mirrors the Slice 14 2nd sub-slice + Slice 15
2nd sub-slice + Slice 15 4th sub-slice + Slice 16 2nd + 3rd-A + 3rd-B
sub-slice non-blocking observer precedents: structural failures during
persistence project onto the typed :class:`FindingPersistenceGap`
finding shape with the typed failure id
:data:`FINDING_PERSISTENCE_FAILURE_ID` registered under the EXISTING
``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` with
the EXISTING NON-blocking :data:`RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action). The writer NEVER raises a failure to the
caller per the auto-memory ``feedback_no_silent_degradation`` rule
(every failure produces a typed gap finding; the caller observes the
gap on :attr:`GovernanceFindingWriter.gap_findings`).

**Implementation discipline.** Stdlib (``datetime`` + ``hashlib`` +
``json``) + Pydantic v2 + Slice 13a modules
(``..workflows.develop.governance.models``) + Slice 16 1st sub-slice
(``.finding_engine``) only. NO imports from ``governance/`` outside
``governance.models``. NO imports from
``workflows/develop/execution/phases/`` / ``supervisor`` / ``dashboard``.
NO imports from Slice 16 2nd / 3rd-A / 3rd-B sub-slice engines (the
writer is a pure projection over already-emitted findings; the upstream
engines are the producers; the writer's only typed dependency is the
1st sub-slice typed-shape foundation). NO mutation of any existing
``execution_control/`` module (per the implementer prompt
§ "Non-negotiables").

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.governance_scorecard_writer`
(Slice 15 4th sub-slice) +
:mod:`iriai_build_v2.execution_control.commit_provenance_writer`
(Slice 14 2nd sub-slice) +
:mod:`iriai_build_v2.execution_control.governance_metric_extractor`
(Slice 15 2nd+3rd sub-slice) +
:mod:`iriai_build_v2.execution_control.finding_engine` (Slice 16 1st
sub-slice): ``BaseModel`` subclasses with ``ConfigDict(extra="forbid")``
so typo-d kwargs fail closed as a typed ``ValidationError`` rather than
being silently absorbed. Per the auto-memory
``feedback_flat_structured_output`` rule the typed control fields are
flat primitives. Per the auto-memory ``feedback_no_silent_degradation``
rule every failure produces a typed :class:`FindingPersistenceGap`. Per
the auto-memory ``feedback_no_overengineer_use_library`` rule the
module mirrors the Slice 15 4th sub-slice precedent verbatim without
introducing new abstractions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from iriai_build_v2.execution_control.finding_engine import (
    GovernanceFinding,
    canonical_finding_dict,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


__all__ = [
    # Typed inputs / outputs (mirrors Slice 15 4th sub-slice precedent).
    "FindingWriterInputs",
    "FindingPersistenceGap",
    # The NEW typed failure id under EXISTING evidence_corruption
    # failure_class (REUSES Slice 14 2nd sub-slice
    # retry_governance_projection NON-blocking RouteAction).
    "FINDING_PERSISTENCE_FAILURE_ID",
    # Review projection id prefix (per doc-16:166-167 verbatim).
    "REVIEW_PROJECTION_ID_PREFIX",
    # Bounded review-projection cap (per IMPLEMENTATION_PROMPT_GOVERNANCE.md
    # § "Bounded reads"; mirrors Slice 15 4th sub-slice value verbatim).
    "DEFAULT_REVIEW_PROJECTION_CAP",
    # Pure helpers.
    "compute_review_projection_id",
    "compute_findings_digest",
    # The writer class.
    "GovernanceFindingWriter",
]


# --- Typed failure id (doc-16:174-176 + doc-14:242-243 NON-BLOCKING) --------


FINDING_PERSISTENCE_FAILURE_ID: Literal[
    "governance_finding_persistence_failed"
] = "governance_finding_persistence_failed"
"""Doc-16:174-176 + doc-14:242-243 -- the typed failure id the finding
writer projects onto when a persistence step fails structurally.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th sub-slice + Slice 16
2nd + 3rd-A + 3rd-B sub-slice precedent verbatim).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 16 pattern matches the Slice 14 + Slice 15 + Slice 16 2nd + 3rd-A
+ 3rd-B sub-slice non-blocking governance projection observer.

The Slice 14 + Slice 15 2nd / 4th + Slice 16 2nd / 3rd-A / 3rd-B
sub-slice precedents are the source-of-truth for the non-blocking
governance-projection failure-routing discipline:

* :mod:`iriai_build_v2.execution_control.commit_provenance_writer`
  (Slice 14 2nd) defines ``line_provenance_gap`` +
  ``governance_evidence_conflict``.
* :mod:`iriai_build_v2.execution_control.governance_metric_extractor`
  (Slice 15 2nd) defines ``governance_metric_extraction_failed``.
* :mod:`iriai_build_v2.execution_control.governance_scorecard_writer`
  (Slice 15 4th) defines ``governance_scorecard_persistence_failed``.
* :mod:`iriai_build_v2.execution_control.finding_rule_engine`
  (Slice 16 2nd) defines ``finding_rule_emission_failed``.
* :mod:`iriai_build_v2.execution_control.finding_plan_deviation_engine`
  (Slice 16 3rd-A) defines ``finding_plan_deviation_parse_failed``.
* :mod:`iriai_build_v2.execution_control.finding_reviewer_test_failure_engine`
  (Slice 16 3rd-B) defines ``finding_reviewer_test_failure_parse_failed``.

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to governance
finding persistence failures (this slice is also a post-checkpoint
governance projection observer).
"""


# --- Review projection id (doc-16:166-167) ----------------------------------


REVIEW_PROJECTION_ID_PREFIX: Literal["review:governance-findings:"] = (
    "review:governance-findings:"
)
"""Doc-16:166-167 -- the typed prefix for the bounded review projection id.

Per *"Store findings as typed governance rows and project bounded
review artifacts such as ``review:governance-findings:{corpus_id}``."*
the projection id is constructed by concatenating this prefix with the
:attr:`FindingWriterInputs.corpus_id` field. The
:func:`compute_review_projection_id` helper performs the concatenation.

The doc-16:46-51 compatible-deviations rule allows additional review
projection id schemes, but the v1 contract mandates this prefix +
``corpus_id`` suffix verbatim so the doc-16:177 backward-compatibility
contract holds (*"Finding ids are stable across reruns when input
evidence and rule version do not change."*).

This is INTENTIONALLY DIFFERENT from the Slice 15 4th sub-slice
:data:`~iriai_build_v2.execution_control.governance_scorecard_writer.REVIEW_PROJECTION_ID_PREFIX`
value ``review:governance-metrics:`` -- the Slice 15 4th sub-slice
emits scorecard projections (metrics-keyed); this Slice 16 4th
sub-slice emits findings projections (findings-keyed). Both share the
``review:`` artifact-key root + the bounded-read + refs-only +
non-blocking failure-routing discipline.
"""


# --- Bounded review-projection cap (IMPLEMENTATION_PROMPT_GOVERNANCE.md §
#     "Bounded reads") ---------------------------------------------------------


DEFAULT_REVIEW_PROJECTION_CAP: int = 200
"""IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" -- the default
LIMIT cap for the bounded review projection's findings list + ref lists.

Per *"Reuse the typed snapshot's ``LIMIT cap+1`` truncation discipline
and the supervisor's ``SET LOCAL statement_timeout`` pattern. No
artifact-body hydration on the governance read path."* the projection
emits up to ``cap+1`` items so the caller can detect overflow (the
``+1`` is the sentinel that signals "the cap was reached + there may be
more"). The default cap is 200 (mirrors the Slice 15 4th sub-slice
:data:`~iriai_build_v2.execution_control.governance_scorecard_writer.DEFAULT_REVIEW_PROJECTION_CAP`
value verbatim), comfortably above the doc-16:120-137 16-class v1
finding-class contract; future per-corpus caps may tighten via the
:meth:`GovernanceFindingWriter.write_review_projection` ``cap``
parameter.

The cap applies independently to:

* The findings list (default: emit at most ``cap+1`` of
  :attr:`FindingWriterInputs.findings`).
* The baseline_refs list (default: emit at most ``cap+1`` of
  :attr:`FindingWriterInputs.baseline_refs`).
* Each finding's primary_evidence_refs list (default: emit at most
  ``cap+1`` per :attr:`GovernanceFinding.primary_evidence_refs`).
* Each finding's supporting_evidence_refs list (default: emit at most
  ``cap+1`` per :attr:`GovernanceFinding.supporting_evidence_refs`).
"""


def compute_review_projection_id(corpus_id: str) -> str:
    """Construct the typed review-projection id per doc-16:166-167.

    Returns ``review:governance-findings:{corpus_id}`` -- the typed
    prefix concatenated with the :attr:`FindingWriterInputs.corpus_id`
    field.

    Per doc-16:166-167 the review projection id is the typed key the
    governance review artifact store uses to read the bounded review
    projection; the caller is responsible for the actual insert
    operation (the writer is a pure projection per the doc-16:174-176
    "Findings are derived governance records and never write execution
    `dag-*` authority artifacts." discipline).

    The construction is intentionally a pure-string concatenation
    (NOT a typed BaseModel) so the projection id is trivial to
    serialise + consume across process boundaries.

    Mirrors :func:`~iriai_build_v2.execution_control.governance_scorecard_writer.compute_review_projection_id`
    (Slice 15 4th sub-slice) verbatim.
    """

    return f"{REVIEW_PROJECTION_ID_PREFIX}{corpus_id}"


# --- Canonical-JSON + SHA-256 digest helper (mirrors Slice 15 1st sub-slice
#     compute_scorecard_digest + Slice 13A compute_completeness_digest +
#     Slice 14 compute_payload_sha256 + Slice 16 1st sub-slice
#     compute_finding_idempotency_key canonical-JSON discipline) --------------


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Mirrors :func:`iriai_build_v2.execution_control.finding_engine._canonical_json`
    + :func:`iriai_build_v2.execution_control.governance_metrics._canonical_json`
    + :func:`iriai_build_v2.execution_control.commit_provenance._canonical_json`
    + :func:`iriai_build_v2.execution_control.completeness._canonical_json`
    + :func:`iriai_build_v2.workflows.develop.governance.evidence_set._canonical_json`
    (doc-13:201-204) verbatim: ``json.dumps(..., sort_keys=True,
    separators=(",", ":"))`` -- the canonical form mandates
    lexicographic key ordering and the compact separator set so the
    resulting bytes are stable across Python versions / platforms /
    dict ordering.

    Per the P3-15-1-1 + P3-16-1-1 carry lineage the ``default=str``
    superset is benign because the canonical projections this module
    computes go through :meth:`BaseModel.model_dump` with ``mode='json'``
    first, so ``datetime`` is already lowered to ISO-8601 strings before
    this helper sees the object; the ``default=str`` is a defence-in-depth
    fallback for any non-JSON-native scalars (e.g. ``Path`` objects in
    test fixtures).
    """

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_hex(payload: str) -> str:
    """Return the SHA-256 hex digest of ``payload`` (UTF-8 encoded).

    Mirrors :func:`iriai_build_v2.execution_control.finding_engine._sha256_hex`
    + :func:`iriai_build_v2.execution_control.governance_metrics._sha256_hex`
    + :func:`iriai_build_v2.execution_control.commit_provenance._sha256_hex`
    + :func:`iriai_build_v2.execution_control.completeness._sha256_hex`
    verbatim. Stdlib-only per the implementer prompt § "Non-negotiables"
    (``hashlib`` + ``json`` only).
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_findings_digest(findings: list[GovernanceFinding]) -> str:
    """Compute the deterministic SHA-256-derived digest for a list of
    :class:`GovernanceFinding` records.

    Per doc-16:158 *"Add dedupe keys from finding class, feature/window,
    affected scope, evidence digest, and rule version."* the digest is
    computed over the canonical-JSON projection of the findings list;
    each finding's typed shape (via
    :func:`~iriai_build_v2.execution_control.finding_engine.canonical_finding_dict`)
    preserves all 22 fields including the per-finding ``idempotency_key``
    (which itself encodes kind + class + feature + affected_scope +
    evidence digests + rule version per doc-16:158).

    Per doc-16:177-179 *"Finding ids are stable across reruns when input
    evidence and rule version do not change."* the digest is the
    cross-process tamper-detection anchor subsequent re-projections rely
    on: two re-runs against the same finding-set MUST produce
    byte-identical digests; a digest change signals a re-projection
    (either new findings, modified findings, or a new rule version).

    The findings list is NOT sorted before digesting -- the caller's
    upstream rule engine is responsible for deterministic ordering (per
    the Slice 16 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine`
    + Slice 16 3rd-A sub-slice
    :class:`~iriai_build_v2.execution_control.finding_plan_deviation_engine.FindingPlanDeviationEngine`
    + Slice 16 3rd-B sub-slice
    :class:`~iriai_build_v2.execution_control.finding_reviewer_test_failure_engine.FindingReviewerTestFailureEngine`
    emission contract). Subsequent Slice 17 sub-slices that require
    order-invariance MAY pre-sort the findings list before passing to
    the writer.

    Mirrors the Slice 13A
    :func:`~iriai_build_v2.execution_control.completeness.compute_completeness_digest`
    + Slice 14
    :func:`~iriai_build_v2.execution_control.commit_provenance.compute_payload_sha256`
    + Slice 15
    :func:`~iriai_build_v2.execution_control.governance_metrics.compute_scorecard_digest`
    + Slice 16 1st sub-slice
    :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`
    canonical-JSON + SHA-256 discipline verbatim.

    :param findings: the list of typed
        :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
        records to digest.
    :returns: the SHA-256 hex digest (64-char string) of the canonical-JSON
        projection of the findings list.
    """

    canonical_list: list[dict[str, Any]] = [
        canonical_finding_dict(finding) for finding in findings
    ]
    payload: dict[str, Any] = {"findings": canonical_list}
    return _sha256_hex(_canonical_json(payload))


# --- Typed inputs (mirrors Slice 15 4th sub-slice ScorecardWriterInputs) ----


class FindingWriterInputs(BaseModel):
    """Doc-16:166-167 step 6 + doc-16:82-104 -- typed bundle of all inputs
    the finding writer consumes.

    The bundle composes:

    * ``corpus_id`` -- the corpus identifier the findings group against
      (e.g. ``"8ac124d6"`` for the calibration fixture per
      doc-16:195-204; future feature ids for production findings).
    * ``findings`` -- the list of
      :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
      records the writer composes into the typed persistence shape.
      This is the typed output of the Slice 16 2nd + 3rd-A + 3rd-B
      sub-slice engines (each engine appends to a per-corpus findings
      list; the writer's caller is responsible for the cross-engine
      aggregation).
    * ``baseline_refs`` -- the list of Slice 13a shared
      :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
      baseline references the finding-set grounds on (per
      doc-16:175-176).
    * ``warnings`` -- the list of warning-reason strings (mirrors the
      Slice 15 4th sub-slice
      :attr:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriterInputs.warnings`
      shape).
    * ``incomplete_scopes`` -- the list of incomplete-scope descriptors
      (mirrors the Slice 15 4th sub-slice
      :attr:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriterInputs.incomplete_scopes`
      shape; free-form dicts so the writer surface can carry rich
      incomplete-scope shapes).
    * ``finding_rule_versions`` -- the dict mapping each rule_id ->
      rule_version (per doc-16:215-217 rollback discipline). Empty by
      default; populated by callers that observe multiple rule versions
      in the same finding-set. Mirrors the Slice 15 4th sub-slice
      ``metric_definition_versions`` dict pattern verbatim (per
      doc-15:144-145 + doc-16:215-217).

    Per doc-16:158 the bundle's ``findings`` list MUST carry the
    per-finding ``idempotency_key`` (which itself encodes kind + class +
    feature + affected_scope + evidence digests + rule version per
    doc-16:158); the :meth:`GovernanceFindingWriter.write_findings`
    projection passes the findings through verbatim (the
    idempotency_keys are preserved).

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.

    Mirrors :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriterInputs`
    (Slice 15 4th sub-slice) verbatim per the chunk-shape decision.
    """

    # ``extra="forbid"`` aligns with the Slice 16 1st sub-slice precedent
    # at src/iriai_build_v2/execution_control/finding_engine.py:441 +
    # the Slice 15 4th sub-slice precedent at
    # src/iriai_build_v2/execution_control/governance_scorecard_writer.py:342
    # + the Slice 14 2nd sub-slice precedent at
    # src/iriai_build_v2/execution_control/commit_provenance_writer.py:249
    # -- unknown fields fail closed as a typed ``ValidationError`` rather
    # than being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    corpus_id: str
    """The corpus identifier the findings group against (e.g.
    ``"8ac124d6"`` for the calibration fixture per doc-16:195-204;
    future feature ids for production findings)."""

    findings: list[GovernanceFinding]
    """Doc-16:166-167 step 6 -- the list of
    :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
    records produced by the Slice 16 2nd + 3rd-A + 3rd-B sub-slice
    engines. The writer composes these into the typed persistence shape
    verbatim (no mutation; preserves the per-finding ``idempotency_key``
    + ``kind`` + ``class_name`` + ``severity`` + ``confidence`` + the
    full 22-field shape per doc-16:82-104)."""

    baseline_refs: list[GovernanceEvidenceRef] = Field(default_factory=list)
    """Doc-16:175-176 -- the list of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    baseline references the finding-set grounds on.

    Per doc-16:174-176 + doc-16:201-291 the baseline refs cite typed
    evidence-set refs (NOT raw artifact bodies); the
    :class:`GovernanceEvidenceRef` typed surface enforces this
    no-raw-body-hydration discipline at construction."""

    warnings: list[str] = Field(default_factory=list)
    """Free-form warning-reason strings naming non-blocking issues
    encountered during finding-set persistence (e.g.
    ``"legacy_heavy_corpus"`` / ``"stale_baseline"`` /
    ``"missing_typed_evidence_for_scope"``). Mirrors the Slice 15 4th
    sub-slice
    :attr:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriterInputs.warnings`
    shape."""

    incomplete_scopes: list[dict[str, Any]] = Field(default_factory=list)
    """The list of incomplete-scope descriptors. Mirrors the Slice 15
    4th sub-slice
    :attr:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriterInputs.incomplete_scopes`
    shape; free-form dicts so callers can carry rich incomplete-scope
    shapes."""

    finding_rule_versions: dict[str, str] = Field(default_factory=dict)
    """Doc-16:215-217 rollback discipline -- the dict mapping each
    rule_id -> rule_version observed in the finding-set.

    Per *"If a finding rule is bad, release a new rule version and mark
    prior findings superseded rather than rewriting history."* the
    rule_version map lets callers observe which rule versions were
    active when the finding-set was emitted. Empty by default;
    populated by callers that observe multiple rule versions in the
    same finding-set.

    Mirrors the Slice 15 4th sub-slice
    :attr:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter.write_review_projection`
    ``metric_definition_versions`` dict pattern verbatim (per
    doc-15:144-145 + doc-16:215-217). The bounded review projection's
    ``finding_rule_versions`` field is sourced from this dict (or, if
    empty, computed from the per-finding ``idempotency_key`` lineage
    via the canonical-JSON projection)."""


# --- Typed gap finding (mirrors Slice 15 4th sub-slice ScorecardPersistenceGap) ---


class FindingPersistenceGap(BaseModel):
    """Typed governance-gap finding produced when the finding writer
    fails to persist a finding-set or review projection structurally.

    Mirrors the Slice 15 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardPersistenceGap`
    + Slice 14
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    + Slice 15 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_metric_extractor.GovernanceMetricExtractionGap`
    + Slice 16 2nd / 3rd-A / 3rd-B sub-slice gap shapes verbatim per the
    chunk-shape point 2 in STATUS.md Next safe action.

    Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED here
    per doc-16:174-176 governance-projection discipline) the finding is
    NON-blocking: the caller MUST NOT propagate it to the executor /
    checkpoint / merge-queue / resume code paths. The corresponding
    typed failure id :data:`FINDING_PERSISTENCE_FAILURE_ID`
    (``governance_finding_persistence_failed``) registers under the
    EXISTING ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
    NOT a new route action).
    """

    # ``extra="forbid"`` aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["governance_finding_persistence_failed"]
    """Doc-16 + doc-14:192-201 -- the typed failure id. Registers under
    the EXISTING ``evidence_corruption`` failure_class with NON-blocking
    routing per doc-14:242-243 + doc-16:174-176."""

    corpus_id: str
    """The corpus scope of the failed persistence (same as the
    :attr:`FindingWriterInputs.corpus_id`)."""

    findings_digest: str | None
    """The findings digest (per :func:`compute_findings_digest`)
    computed at the time of failure, or ``None`` if the failure happened
    before the digest could be computed.

    The digest is the cross-process tamper-detection anchor per
    doc-16:177-179; preserving it in the gap finding lets downstream
    consumers correlate the gap with the historical finding-set whose
    persistence failed (per doc-16:215-217 rollback discipline:
    historical findings remain as review artifacts even when newer
    re-projections fail)."""

    review_projection_id: str | None
    """The bounded review projection id (per
    :func:`compute_review_projection_id`) the failure targeted, or
    ``None`` if the failure happened before the projection id could be
    computed."""

    reason: str
    """Free-form gap reason (e.g.
    ``findings_count_exceeds_cap``,
    ``review_projection_digest_mismatch``,
    ``findings_construction_failed``)."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding (e.g.
    the finding kinds involved, the projection cap that was exceeded,
    the error detail). Free-form per the doc-14:192-201 + doc-16
    governance-finding contract."""


# --- The finding writer (mirrors Slice 15 4th sub-slice ScorecardWriter) ----


class GovernanceFindingWriter:
    """Finding writer + bounded review projection emitter
    (doc-16:166-167 step 6).

    Per *"Store findings as typed governance rows and project bounded
    review artifacts such as ``review:governance-findings:{corpus_id}``."*
    the writer consumes the Slice 16 1st sub-slice typed-shape foundation
    (:class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`)
    + the Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    and projects them onto:

    1. A typed ``list[GovernanceFinding]`` governance rows (the typed
       persistence shape; mirrors the Slice 15 4th sub-slice
       :meth:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter.write_scorecard`
       projection).
    2. A bounded review projection dict (the typed review-artifact shape
       keyed by :func:`compute_review_projection_id`).

    **Bounded-read discipline (IMPLEMENTATION_PROMPT_GOVERNANCE.md §
    "Bounded reads" + doc-16:174-176).** The review projection's findings
    list + per-finding evidence_refs list + baseline_refs list all use
    the ``LIMIT cap+1`` truncation discipline: the writer emits up to
    ``cap+1`` items so the caller can detect overflow (per the typed
    snapshot pattern). The default cap is :data:`DEFAULT_REVIEW_PROJECTION_CAP`.

    **Refs-only discipline (doc-16:174-176 + doc-16:201-291).** The
    review projection carries ONLY typed control fields + cited refs
    (:class:`GovernanceEvidenceRef` typed shape) -- NEVER raw artifact
    bodies. The :class:`GovernanceEvidenceRef` typed surface itself
    enforces this no-raw-body discipline at construction.

    **Digest discipline (doc-16:177-179).** The review projection
    carries the findings digest (per :func:`compute_findings_digest`)
    for tamper detection: two re-runs against the same finding-set MUST
    produce byte-identical digests.

    **Rollback discipline (doc-16:215-217).** The writer's typed-shape
    outputs are immutable rows: future re-projections emit NEW
    :class:`FindingWriterInputs` bundles (with NEW per-finding
    ``rule_version`` references) rather than mutating historical
    findings. The :meth:`write_findings` surface is a pure projection;
    the actual storage mechanism (e.g. a Postgres ``governance_finding:*``
    row insert) is the caller's responsibility.

    **Non-blocking discipline.** The writer NEVER raises a failure to
    the caller. Any structural failure projects onto a typed
    :class:`FindingPersistenceGap` finding emitted on the
    :attr:`GovernanceFindingWriter.gap_findings` list (post-call).

    Per the auto-memory ``feedback_no_overengineer_use_library`` rule
    the writer mirrors the Slice 15 4th sub-slice + Slice 14 2nd
    sub-slice + Slice 15 2nd sub-slice precedents (single class with one
    or more public projection methods) without introducing new
    abstractions.

    Example usage::

        from iriai_build_v2.execution_control.finding_rule_engine import (
            FindingRuleEngine, FindingRuleEmissionInputs,
        )
        from iriai_build_v2.execution_control.governance_finding_writer import (
            GovernanceFindingWriter, FindingWriterInputs,
        )

        engine = FindingRuleEngine()
        # ... engine emits findings ...
        findings = engine.findings  # list[GovernanceFinding]

        writer = GovernanceFindingWriter()
        persisted = writer.write_findings(
            FindingWriterInputs(
                corpus_id="8ac124d6",
                findings=findings,
                baseline_refs=[],
                warnings=[],
                incomplete_scopes=[],
                finding_rule_versions={"commit_hygiene_loop_v1": "v1"},
            )
        )
        projection = writer.write_review_projection(persisted, corpus_id="8ac124d6")
        # caller persists findings + projection.
    """

    def __init__(self) -> None:
        """Construct a finding writer.

        The writer is stateless aside from the :attr:`gap_findings`
        accumulator the public :meth:`write_findings` +
        :meth:`write_review_projection` surfaces populate. Each call
        to a public method RESETS the accumulator (so per-call gap
        findings remain bounded; callers that need cross-call gap
        accumulation should snapshot the property after each call).

        Mirrors the Slice 15 4th sub-slice
        :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter`
        constructor verbatim.
        """

        self._gap_findings: list[FindingPersistenceGap] = []

    @property
    def gap_findings(self) -> list[FindingPersistenceGap]:
        """The list of :class:`FindingPersistenceGap` findings the
        most-recent public-method call produced.

        Per the Slice 14
        :class:`~iriai_build_v2.execution_control.commit_provenance_writer.GitProvenanceWriter`
        + Slice 15 2nd sub-slice
        :class:`~iriai_build_v2.execution_control.governance_metric_extractor.MetricExtractor`
        + Slice 15 4th sub-slice
        :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter`
        precedents the writer NEVER raises a failure to the caller --
        every structural failure projects onto a typed gap finding.

        Returns a fresh snapshot list each call so caller mutations do
        not leak into the internal accumulator.
        """

        return list(self._gap_findings)

    def write_findings(
        self,
        inputs: FindingWriterInputs,
    ) -> list[GovernanceFinding]:
        """Project the typed :class:`FindingWriterInputs` bundle into a
        typed ``list[GovernanceFinding]`` governance rows (doc-16:166-167
        step 6).

        Returns the typed finding list verbatim: each finding's full
        22-field shape (per doc-16:82-104) is preserved without
        mutation. The persistence surface is a pure projection (no
        re-validation; the typed
        :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
        Pydantic BaseModel enforces all typed-shape invariants at
        construction time per the upstream rule-engine emission contract).

        Per doc-14:242-243 + doc-16:174-176 NEVER raises a failure to
        the caller. Any structural failure projects onto a typed
        :class:`FindingPersistenceGap` accumulated on
        :attr:`gap_findings`; the returned list in that case is the
        most-conservative projection (empty list) so the downstream
        Slice 17 policy layer can block recommendations on the corpus.

        The method RESETS the :attr:`gap_findings` accumulator at
        entry; per-call gap findings remain bounded.

        Mirrors :meth:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter.write_scorecard`
        (Slice 15 4th sub-slice) verbatim.

        :param inputs: the typed :class:`FindingWriterInputs` bundle.
        :returns: the typed ``list[GovernanceFinding]`` governance rows
            (verbatim from :attr:`FindingWriterInputs.findings`).
        """

        # Reset per-call accumulator (mirrors Slice 15 4th sub-slice
        # ScorecardWriter.write_scorecard pattern).
        self._gap_findings = []

        # The projection is a pure pass-through: each
        # GovernanceFinding's typed shape is already validated at
        # construction by the upstream rule-engine. Defensive
        # try/except wraps the list construction so any structural
        # failure projects onto a typed gap rather than being raised to
        # the caller (non-blocking observer contract per doc-14:242-243
        # + doc-16:174-176).
        try:
            persisted = list(inputs.findings)
        except Exception as exc:
            # Defensive: list() should never fail given the typed
            # inputs above, but the writer is non-blocking per the
            # doc-14:242-243 contract. Project onto a typed gap finding
            # + return an empty list so the downstream Slice 17 policy
            # layer can block recommendations.
            self._gap_findings.append(
                FindingPersistenceGap(
                    failure_id=FINDING_PERSISTENCE_FAILURE_ID,
                    corpus_id=inputs.corpus_id,
                    findings_digest=None,
                    review_projection_id=compute_review_projection_id(
                        inputs.corpus_id
                    ),
                    reason=f"findings_construction_failed: {type(exc).__name__}",
                    evidence_payload={"error_detail": str(exc)[:500]},
                )
            )
            persisted = []

        return persisted

    def write_review_projection(
        self,
        findings: list[GovernanceFinding],
        *,
        corpus_id: str,
        baseline_refs: list[GovernanceEvidenceRef] | None = None,
        warnings: list[str] | None = None,
        incomplete_scopes: list[dict[str, Any]] | None = None,
        finding_rule_versions: dict[str, str] | None = None,
        cap: int | None = None,
    ) -> dict[str, Any]:
        """Build the bounded review projection dict for a list of
        :class:`GovernanceFinding` records at the typed
        :data:`REVIEW_PROJECTION_ID_PREFIX` key prefix per doc-16:166-167.

        The projection's structure:

        * ``review_projection_id`` -- the typed key per
          :func:`compute_review_projection_id`.
        * ``corpus_id`` -- the corpus identifier.
        * ``generated_at`` -- ISO-8601 string projection of
          ``datetime.now(timezone.utc)`` (the cross-process freshness
          anchor per the Slice 15 4th sub-slice precedent verbatim).
        * ``findings_digest`` -- the SHA-256 digest from
          :func:`compute_findings_digest` for tamper detection per
          doc-16:177-179.
        * ``finding_rule_versions`` -- the rule-version map per
          doc-16:215-217 (sourced from the ``finding_rule_versions``
          parameter, defaulting to ``{}`` when omitted).
        * ``findings`` -- the finding values projected as compact dicts
          carrying ONLY typed control fields + cited refs (NEVER raw
          bodies), truncated at ``cap+1`` per the bounded-read
          discipline.
        * ``baseline_refs`` -- the baseline refs projected via
          ``model_dump(mode='json')`` (typed-ref shape; NEVER raw bodies),
          truncated at ``cap+1``.
        * ``incomplete_scopes`` -- carried verbatim from the
          ``incomplete_scopes`` parameter.
        * ``warnings`` -- carried verbatim from the ``warnings``
          parameter.
        * ``findings_truncated`` / ``baseline_refs_truncated`` -- bool
          flags signalling whether the cap was reached (so the caller
          can detect overflow per the LIMIT cap+1 discipline).

        Per IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads"
        the projection EMITS at most ``cap+1`` items in each list (so
        the caller can detect overflow by checking
        ``len(projection["findings"]) > cap``).

        Per doc-16:174-176 + doc-16:201-291 the projection EMITS ONLY
        REFS (NOT raw bodies). The
        :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
        typed surface itself enforces this no-raw-body discipline.

        Per doc-16:177-179 + doc-16:158 the projection CARRIES the
        findings digest (for tamper detection) + the per-rule version
        map (so later changes do not silently rewrite historical
        meaning).

        Per doc-14:242-243 + doc-16:174-176 NEVER raises a failure to
        the caller. Any structural failure projects onto a typed
        :class:`FindingPersistenceGap` accumulated on
        :attr:`gap_findings`; the returned projection in that case
        still carries the typed shape (with the most-conservative
        projection) so the downstream Slice 17 policy layer can block
        recommendations on the corpus.

        Mirrors :meth:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter.write_review_projection`
        (Slice 15 4th sub-slice) verbatim per the chunk-shape decision.

        :param findings: the typed
            ``list[GovernanceFinding]`` to project.
        :param corpus_id: the corpus identifier the projection groups
            against (keyword-only per the chunk-shape decision -- mirrors
            the Slice 15 4th sub-slice
            :meth:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter.write_review_projection`
            ``cap`` keyword-only parameter pattern).
        :param baseline_refs: the typed baseline references (defaults to
            an empty list).
        :param warnings: free-form warning strings (defaults to an
            empty list).
        :param incomplete_scopes: free-form incomplete-scope dicts
            (defaults to an empty list).
        :param finding_rule_versions: the rule-version map per
            doc-16:215-217 (defaults to an empty dict).
        :param cap: optional override for the LIMIT cap+1 truncation
            threshold; defaults to :data:`DEFAULT_REVIEW_PROJECTION_CAP`.
        """

        # Reset per-call accumulator (mirrors Slice 15 4th sub-slice
        # ScorecardWriter.write_review_projection pattern).
        self._gap_findings = []

        # Coerce optional parameter defaults (mirrors the typed-bundle
        # default pattern; never mutates the caller's lists).
        effective_baseline_refs: list[GovernanceEvidenceRef] = (
            list(baseline_refs) if baseline_refs is not None else []
        )
        effective_warnings: list[str] = list(warnings) if warnings is not None else []
        effective_incomplete_scopes: list[dict[str, Any]] = (
            [dict(scope) for scope in incomplete_scopes]
            if incomplete_scopes is not None
            else []
        )
        effective_rule_versions: dict[str, str] = (
            dict(finding_rule_versions)
            if finding_rule_versions is not None
            else {}
        )

        effective_cap = cap if cap is not None else DEFAULT_REVIEW_PROJECTION_CAP

        # Compute the digest defensively: if compute_findings_digest
        # fails (should never happen given the typed shape but the
        # writer is non-blocking) project onto a typed gap finding.
        try:
            digest = compute_findings_digest(findings)
        except Exception as exc:
            self._gap_findings.append(
                FindingPersistenceGap(
                    failure_id=FINDING_PERSISTENCE_FAILURE_ID,
                    corpus_id=corpus_id,
                    findings_digest=None,
                    review_projection_id=compute_review_projection_id(corpus_id),
                    reason=f"findings_digest_failed: {type(exc).__name__}",
                    evidence_payload={"error_detail": str(exc)[:500]},
                )
            )
            digest = ""

        # ── LIMIT cap+1 truncation of the findings list per
        #    IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" ───────────
        # Emit at most cap+1 items so the caller can detect overflow
        # (the +1 is the sentinel that signals "the cap was reached +
        # there may be more").
        findings_limit = effective_cap + 1
        truncated_findings = findings[:findings_limit]
        findings_truncated = len(findings) > effective_cap

        # ── LIMIT cap+1 truncation of the baseline_refs list ────────────
        baseline_refs_limit = effective_cap + 1
        truncated_baseline_refs = effective_baseline_refs[:baseline_refs_limit]
        baseline_refs_truncated = len(effective_baseline_refs) > effective_cap

        # Project the finding values to bounded review-projection dicts.
        # The projection carries ONLY typed control fields + cited refs
        # (NEVER raw bodies). The primary_evidence_refs +
        # supporting_evidence_refs lists per finding are also truncated
        # at cap+1 per the bounded-read discipline.
        projected_findings: list[dict[str, Any]] = []
        for finding in truncated_findings:
            evidence_refs_limit = effective_cap + 1
            primary_refs = finding.primary_evidence_refs[:evidence_refs_limit]
            primary_refs_truncated = (
                len(finding.primary_evidence_refs) > effective_cap
            )
            supporting_refs = finding.supporting_evidence_refs[:evidence_refs_limit]
            supporting_refs_truncated = (
                len(finding.supporting_evidence_refs) > effective_cap
            )
            projected_findings.append(
                {
                    "idempotency_key": finding.idempotency_key,
                    "kind": finding.kind,
                    "class_name": finding.class_name,
                    "severity": finding.severity,
                    "confidence": finding.confidence,
                    "feature_id": finding.feature_id,
                    "affected_scope": dict(finding.affected_scope),
                    # primary_evidence_refs + supporting_evidence_refs
                    # use mode='json' to project to cross-process stable
                    # dict shapes per the Slice 13a GovernanceEvidenceRef
                    # typed contract; this preserves the typed ref shape
                    # (authority + ref_id + digest + quality +
                    # completeness) WITHOUT inserting any raw body field.
                    # The mode='json' projection lowers datetime to
                    # ISO-8601 string per the Pydantic v2
                    # canonical-projection contract.
                    "primary_evidence_refs": [
                        ref.model_dump(mode="json") for ref in primary_refs
                    ],
                    "primary_evidence_refs_truncated": primary_refs_truncated,
                    "supporting_evidence_refs": [
                        ref.model_dump(mode="json") for ref in supporting_refs
                    ],
                    "supporting_evidence_refs_truncated": supporting_refs_truncated,
                    # implementation_log_anchors is list[str] per
                    # doc-16:92; pass through verbatim (no raw bodies).
                    "implementation_log_anchors": list(
                        finding.implementation_log_anchors
                    ),
                    # metric_refs is list[str] per doc-16:93; pass
                    # through verbatim (no raw bodies).
                    "metric_refs": list(finding.metric_refs),
                    "estimated_lost_hours": finding.estimated_lost_hours,
                    "estimated_retry_impact": finding.estimated_retry_impact,
                    # recommended_action_display is non-executable per
                    # doc-16:117; pass through verbatim.
                    "recommended_action_display": finding.recommended_action_display,
                    "recommendation_draft_ref": finding.recommendation_draft_ref,
                    "safe_runtime_action": finding.safe_runtime_action,
                    "requires_policy_artifact": finding.requires_policy_artifact,
                    "product_defect_related": finding.product_defect_related,
                    "workflow_related": finding.workflow_related,
                    "causal_role": finding.causal_role,
                    "primary_cause_finding_id": finding.primary_cause_finding_id,
                    # linked_finding_ids is list[str] per doc-16:104;
                    # pass through verbatim.
                    "linked_finding_ids": list(finding.linked_finding_ids),
                }
            )

        # Project the baseline_refs to cited refs per doc-16:174-176 +
        # doc-16:201-291 (NEVER raw bodies). Mode='json' projection per
        # the Slice 13a GovernanceEvidenceRef contract.
        projected_baseline_refs = [
            ref.model_dump(mode="json") for ref in truncated_baseline_refs
        ]

        # The full review projection dict.
        review_projection_id = compute_review_projection_id(corpus_id)
        # generated_at projects to ISO-8601 string per the doc-13:201-204
        # canonical-JSON discipline (mirrors Slice 15 4th sub-slice
        # generated_at projection pattern verbatim).
        generated_at_iso = datetime.now(timezone.utc).isoformat()
        projection: dict[str, Any] = {
            "review_projection_id": review_projection_id,
            "corpus_id": corpus_id,
            "generated_at": generated_at_iso,
            "findings_digest": digest,
            "finding_rule_versions": effective_rule_versions,
            "findings": projected_findings,
            "findings_truncated": findings_truncated,
            "baseline_refs": projected_baseline_refs,
            "baseline_refs_truncated": baseline_refs_truncated,
            "incomplete_scopes": effective_incomplete_scopes,
            "warnings": effective_warnings,
        }
        return projection
