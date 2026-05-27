"""Slice 17 fourth sub-slice -- decision-record persistence + bounded
review projection.

Per ``docs/execution-control-plane/17-policy-recommendation-interface.md``
§ Refactoring Steps step 4 (line 172): *"Add decision records for
accept/reject/needs-more-evidence."*

Per doc-17:182-188 (Persistence And Artifact Compatibility): *"Store
recommendations as typed governance rows and project review artifacts
such as ``review:governance-recommendations:{corpus_id}``. Do not write
``dag-regroup-active:*``, route-budget state, supervisor actions, or
merge queue state from governance recommendation generation. If a
consumer later activates a policy, it writes its own activation
artifact and references the recommendation id."*

Per doc-17:99-105 the typed
:class:`~iriai_build_v2.execution_control.policy_recommendation.PolicyRecommendationDecision`
BaseModel shape:

* ``recommendation_id`` -- the typed reference back to the
  :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.recommendation_id`
  the decision applies to.
* ``decision`` -- the 3-value Literal ``"accept"`` / ``"reject"`` /
  ``"needs_more_evidence"`` per doc-17:101 verbatim.
* ``decided_by`` -- the typed decider identifier string.
* ``decided_at`` -- the typed decision timestamp (ISO-8601 projection
  under ``model_dump(mode='json')``).
* ``rationale`` -- the typed decision-rationale string.
* ``evidence_refs`` -- the list of Slice 13a shared
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  evidence references the decision cites.

Per doc-17:219 *"Consumers can ignore or reject recommendations with
durable rationale."* the decision-record writer is the typed audit
surface that persists the typed decision rows. Per doc-17:217
*"Recommendation generation has no direct mutation authority."* the
writer GRANTS NO CONSUMER-SIDE ACTIVATION AUTHORITY -- a persisted
``accept`` decision row does NOT activate the recommendation's proposed
policy artifact; activation remains consumer-owned per doc-17:159-163.

This module owns the decision-record-writer + bounded-review-projection
surface that consumes:

* The Slice 17 1st sub-slice typed
  :class:`~iriai_build_v2.execution_control.policy_recommendation.PolicyRecommendationDecision`
  BaseModel (the typed decision row).
* The Slice 17 1st sub-slice typed
  :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
  records (the typed recommendation surface the decisions reference).
* Optionally, the Slice 17 3rd sub-slice typed
  :class:`~iriai_build_v2.execution_control.policy_validation_interface.ValidationResult`
  records (as supporting evidence for the decision rationale; per
  doc-17:172 the decision record captures the reviewer's accept /
  reject / needs-more-evidence verdict; the validation result is the
  typed audit-trail evidence the reviewer cited).
* The Slice 13a shared
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  (cited by :attr:`PolicyRecommendationDecision.evidence_refs` +
  :attr:`DecisionWriterInputs.baseline_refs`).

The writer is a **post-checkpoint governance projection observer** that
emits BOTH the typed :class:`DecisionWriterResult` governance rows AND
the bounded review projection at
``review:governance-recommendations:{corpus_id}`` per doc-17:182-188.

**Persistence + review-projection surface.** The writer's public
method:

* :meth:`DecisionRecordWriter.write_decisions` -- composes a typed
  :class:`DecisionWriterInputs` bundle into a typed
  :class:`DecisionWriterResult` carrying the
  ``persisted_decision_ids`` (preserves the per-decision
  ``recommendation_id`` verbatim; the typed surface enforces all
  doc-17:99-105 invariants at construction; the writer does NOT mutate
  upstream decision rows) + the ``gap_records`` typed gap list (typed
  :class:`DecisionPersistenceGap` rows projected on persistence
  failure per the non-blocking observer contract).

The writer is also able to project the typed decision rows onto a
bounded review projection at the typed
:data:`REVIEW_PROJECTION_ID_PREFIX` key prefix per doc-17:182-188. The
projection is BOUNDED via the :data:`DEFAULT_REVIEW_PROJECTION_CAP`
LIMIT cap+1 truncation discipline (per
``IMPLEMENTATION_PROMPT_GOVERNANCE.md`` § "Bounded reads"); EMITS ONLY
REFS (NOT raw bodies) per doc-17:172 + doc-17:182-188; CARRIES the
decisions digest (per :func:`compute_decisions_digest`) for tamper
detection; CITES the per-decision recommendation_id (so subsequent
consumers can correlate the persisted decision rows with the
historical recommendations).

**Bounded-read discipline (IMPLEMENTATION_PROMPT_GOVERNANCE.md §
"Bounded reads" + doc-17:182-188 + doc-13a:18-23).** Per *"Reuse the
typed snapshot's ``LIMIT cap+1`` truncation discipline and the
supervisor's ``SET LOCAL statement_timeout`` pattern. No artifact-body
hydration on the governance read path."* the review projection's
decisions list + per-decision evidence_refs list + recommendations
list + validation_results list + baseline_refs list are all truncated
at ``cap+1`` (the projection emits up to ``cap+1`` items so the caller
can detect overflow). The default cap is
:data:`DEFAULT_REVIEW_PROJECTION_CAP` (200; mirrors the Slice 15 4th +
Slice 16 4th values verbatim).

**Refs-only discipline (doc-17:172 + doc-17:182-188).** The review
projection carries ONLY:

* The decision's typed control fields (``recommendation_id`` +
  ``decision`` + ``decided_by`` + ``decided_at`` ISO-string +
  ``rationale``).
* The decision's ``evidence_refs`` projected as
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  cited refs via ``model_dump(mode='json')`` (the typed ref shape
  carries authority + ref_id + digest + quality + completeness -- NOT
  raw bodies).
* The supporting recommendation's typed control fields projected via
  ``model_dump(mode='json')`` (typed-shape carries the consumer +
  status + source_finding_ids + source_metric_refs + the typed
  proposed_policy_artifact -- NOT raw bodies).
* The supporting validation result's typed control fields projected
  via ``model_dump(mode='json')`` (typed-shape carries is_valid +
  violations + consumer -- NOT raw bodies).
* The baseline_refs projected via ``model_dump(mode='json')``
  (typed-ref shape).

The projection NEVER carries raw artifact bodies, raw event payloads,
or any field that requires unbounded scanning. The Slice 13a typed
:class:`GovernanceEvidenceRef` surface itself enforces the
no-raw-body-hydration discipline at construction.

**Digest discipline (doc-17:182-188 + doc-13:201-204).** The review
projection carries the decisions digest
(:func:`compute_decisions_digest`) for tamper detection: two re-runs
against the same decision-set MUST produce byte-identical digests; a
digest change signals a re-projection (either new decisions or
modified decisions).

**Rollback discipline (doc-17:222-226).** Per *"Rollback disables
recommendation generation and consumer read APIs. Existing
recommendation artifacts remain historical audit records. Activated
policy rollback belongs to the owning consumer, not the governance
analyzer."* the writer's typed-shape outputs are immutable rows:
future re-projections emit NEW typed :class:`DecisionWriterInputs`
bundles (with NEW per-decision ``recommendation_id`` references)
rather than mutating historical decisions. The
:meth:`DecisionRecordWriter.write_decisions` surface is a pure
projection; the actual storage mechanism is the caller's
responsibility (e.g. a Postgres
``governance_recommendation_decision:*`` row insert + a separate
``review:governance-recommendations:{corpus_id}`` projection insert).

**Non-blocking failure routing discipline (doc-14:242-243 inherited
verbatim).** The writer mirrors the Slice 14 2nd sub-slice + Slice 15
2nd sub-slice + Slice 15 4th sub-slice + Slice 16 2nd + 3rd-A + 3rd-B
+ 4th sub-slice + Slice 17 2nd + 3rd sub-slice non-blocking observer
precedents: structural failures during persistence project onto the
typed :class:`DecisionPersistenceGap` shape with the typed failure id
:data:`DECISION_RECORD_PERSISTENCE_FAILURE_ID` registered under the
EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` with
the EXISTING NON-blocking :data:`RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action). The writer NEVER raises a failure to the
caller per the auto-memory ``feedback_no_silent_degradation`` rule
(every failure produces a typed
:class:`DecisionPersistenceGap` projection embedded in the typed
:class:`DecisionWriterResult` ``gap_records`` field; the caller
observes the gaps via :attr:`DecisionRecordWriter.gap_records`
property mirror).

**No consumer activation authority (doc-17:159-163 + doc-17:217 +
doc-17:222-226).** The decision-record writer is a **pure projection
observer**: a persisted ``"accept"`` decision row does NOT activate
the recommendation's proposed policy artifact; activation remains
consumer-owned per doc-17:159-163. The writer GRANTS NO CONSUMER-SIDE
ACTIVATION AUTHORITY -- it is structurally analogous to the Slice 17
3rd sub-slice
:class:`~iriai_build_v2.execution_control.policy_validation_interface.PolicyValidationInterface`
which validates but does NOT activate. Subsequent Slice 17 6th
sub-slice consumer read APIs separate accepted-but-not-activated
recommendations from consumer-owned activated policy artifacts per
doc-17:175-177.

**Implementation discipline.** Stdlib (``datetime`` + ``hashlib`` +
``json``) + Pydantic v2 + Slice 13a modules
(``..workflows.develop.governance.models``) + Slice 17 1st sub-slice
(``.policy_recommendation``) + Slice 17 3rd sub-slice
(``.policy_validation_interface`` for typed-input REUSE only) only.
NO imports from ``governance/`` outside ``governance.models``. NO
imports from ``workflows/develop/execution/phases/`` / ``supervisor``
/ ``dashboard``. NO imports from Slice 17 2nd sub-slice
(``.recommendation_builder``) -- the writer is a pure projection over
already-emitted decisions; the upstream recommendation builder is the
producer, not a writer dependency. NO mutation of any existing
``execution_control/`` module (per the implementer prompt
§ "Non-negotiables").

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.governance_scorecard_writer`
(Slice 15 4th sub-slice) +
:mod:`iriai_build_v2.execution_control.governance_finding_writer`
(Slice 16 4th sub-slice) +
:mod:`iriai_build_v2.execution_control.commit_provenance_writer`
(Slice 14 2nd sub-slice): ``BaseModel`` subclasses with
``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed as a typed
``ValidationError`` rather than being silently absorbed. Per the
auto-memory ``feedback_flat_structured_output`` rule the typed control
fields are flat primitives. Per the auto-memory
``feedback_no_silent_degradation`` rule every failure produces a typed
:class:`DecisionPersistenceGap`. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 15 4th + Slice 16 4th sub-slice precedent verbatim without
introducing new abstractions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from iriai_build_v2.execution_control.policy_recommendation import (
    GovernancePolicyRecommendation,
    PolicyRecommendationDecision,
)
from iriai_build_v2.execution_control.policy_validation_interface import (
    ValidationResult,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


__all__ = [
    # Typed inputs / outputs / gap (mirrors Slice 15 4th + Slice 16 4th
    # sub-slice precedent).
    "DecisionWriterInputs",
    "DecisionWriterResult",
    "DecisionPersistenceGap",
    # The NEW typed failure id under EXISTING evidence_corruption
    # failure_class (REUSES Slice 14 2nd sub-slice
    # retry_governance_projection NON-blocking RouteAction).
    "DECISION_RECORD_PERSISTENCE_FAILURE_ID",
    # Review projection id prefix (per doc-17:182-188 verbatim).
    "REVIEW_PROJECTION_ID_PREFIX",
    # Bounded review-projection cap (per IMPLEMENTATION_PROMPT_GOVERNANCE.md
    # § "Bounded reads"; mirrors Slice 15 4th + Slice 16 4th value verbatim).
    "DEFAULT_REVIEW_PROJECTION_CAP",
    # Pure helpers.
    "compute_decision_projection_id",
    "compute_decisions_digest",
    # The writer class.
    "DecisionRecordWriter",
]


# --- Typed failure id (doc-17:172 + doc-17:182-188 + doc-14:242-243
#     NON-BLOCKING) --------------------------------------------------------


DECISION_RECORD_PERSISTENCE_FAILURE_ID: Literal[
    "decision_record_persistence_failed"
] = "decision_record_persistence_failed"
"""Doc-17:172 + doc-17:182-188 + doc-14:242-243 -- the typed failure id
the decision-record writer projects onto when a persistence step fails
structurally.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd sub-slice precedent verbatim).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 17 4th pattern matches the Slice 14 + Slice 15 + Slice 16 +
Slice 17 2nd + 3rd sub-slice non-blocking governance projection
observer (the decision-record writer is also a post-checkpoint
governance projection observer).

The Slice 14 + Slice 15 2nd / 4th + Slice 16 2nd / 3rd-A / 3rd-B / 4th
+ Slice 17 2nd / 3rd sub-slice precedents are the source-of-truth for
the non-blocking governance-projection failure-routing discipline:

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
* :mod:`iriai_build_v2.execution_control.governance_finding_writer`
  (Slice 16 4th) defines ``governance_finding_persistence_failed``.
* :mod:`iriai_build_v2.execution_control.recommendation_builder`
  (Slice 17 2nd) defines ``recommendation_builder_emission_failed``.
* :mod:`iriai_build_v2.execution_control.policy_validation_interface`
  (Slice 17 3rd) defines ``policy_validation_failed``.

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to governance
decision-record persistence failures (this slice is also a
post-checkpoint governance projection observer).
"""


# --- Review projection id prefix (doc-17:182-188 verbatim) ------------------


REVIEW_PROJECTION_ID_PREFIX: Literal["review:governance-recommendations:"] = (
    "review:governance-recommendations:"
)
"""Doc-17:182-188 -- the typed prefix for the bounded review projection
id for governance recommendation decision records.

Per doc-17:182-188 *"Store recommendations as typed governance rows
and project review artifacts such as
``review:governance-recommendations:{corpus_id}``."* the projection id
is constructed by concatenating this prefix with the
:attr:`DecisionWriterInputs.corpus_id` field. The
:func:`compute_decision_projection_id` helper performs the
concatenation.

The doc-17:48-54 compatible-deviations rule allows additional review
projection id schemes, but the v1 contract mandates this prefix +
``corpus_id`` suffix verbatim so the doc-17:182-188 backward-
compatibility contract holds (*"If a consumer later activates a
policy, it writes its own activation artifact and references the
recommendation id."*).

This is INTENTIONALLY DIFFERENT from:

* :data:`~iriai_build_v2.execution_control.governance_scorecard_writer.REVIEW_PROJECTION_ID_PREFIX`
  value ``review:governance-metrics:`` (Slice 15 4th sub-slice emits
  scorecard projections; metrics-keyed).
* :data:`~iriai_build_v2.execution_control.governance_finding_writer.REVIEW_PROJECTION_ID_PREFIX`
  value ``review:governance-findings:`` (Slice 16 4th sub-slice emits
  findings projections; findings-keyed).

Both share the ``review:`` artifact-key root + the bounded-read +
refs-only + non-blocking failure-routing discipline.

The Slice 17 4th sub-slice emits decision-record projections;
recommendation-decision-keyed (the typed projection ``decisions`` list
carries the per-recommendation typed decision rows).
"""


# --- Bounded review-projection cap (IMPLEMENTATION_PROMPT_GOVERNANCE.md §
#     "Bounded reads"; mirrors Slice 15 4th + Slice 16 4th value verbatim) ----


DEFAULT_REVIEW_PROJECTION_CAP: int = 200
"""IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" -- the default
LIMIT cap for the bounded review projection's decisions + recommendations
+ validation_results + per-decision evidence_refs + baseline_refs lists.

Per *"Reuse the typed snapshot's ``LIMIT cap+1`` truncation discipline
and the supervisor's ``SET LOCAL statement_timeout`` pattern. No
artifact-body hydration on the governance read path."* the projection
emits up to ``cap+1`` items so the caller can detect overflow (the
``+1`` is the sentinel that signals "the cap was reached + there may be
more"). The default cap is 200 (mirrors the Slice 15 4th sub-slice
:data:`~iriai_build_v2.execution_control.governance_scorecard_writer.DEFAULT_REVIEW_PROJECTION_CAP`
+ Slice 16 4th sub-slice
:data:`~iriai_build_v2.execution_control.governance_finding_writer.DEFAULT_REVIEW_PROJECTION_CAP`
values verbatim), comfortably above the v1 6-consumer + 3-decision-
literal contract; future per-corpus caps may tighten via the
:meth:`DecisionRecordWriter.write_review_projection` ``cap`` parameter.

The cap applies independently to:

* The decisions list (default: emit at most ``cap+1`` of
  :attr:`DecisionWriterInputs.decisions`).
* The recommendations list (default: emit at most ``cap+1`` of
  :attr:`DecisionWriterInputs.recommendations`).
* The validation_results list (default: emit at most ``cap+1`` of
  :attr:`DecisionWriterInputs.validation_results`).
* The baseline_refs list (default: emit at most ``cap+1`` of
  :attr:`DecisionWriterInputs.baseline_refs`).
* Each decision's evidence_refs list (default: emit at most ``cap+1``
  per :attr:`PolicyRecommendationDecision.evidence_refs`).
"""


def compute_decision_projection_id(corpus_id: str) -> str:
    """Construct the typed review-projection id per doc-17:182-188.

    Returns ``review:governance-recommendations:{corpus_id}`` -- the
    typed prefix concatenated with the
    :attr:`DecisionWriterInputs.corpus_id` field.

    Per doc-17:182-188 the review projection id is the typed key the
    governance review artifact store uses to read the bounded review
    projection; the caller is responsible for the actual insert
    operation (the writer is a pure projection per the doc-17:217
    "Recommendation generation has no direct mutation authority."
    discipline).

    The construction is intentionally a pure-string concatenation (NOT
    a typed BaseModel) so the projection id is trivial to serialise +
    consume across process boundaries.

    Mirrors :func:`~iriai_build_v2.execution_control.governance_scorecard_writer.compute_review_projection_id`
    (Slice 15 4th sub-slice) +
    :func:`~iriai_build_v2.execution_control.governance_finding_writer.compute_review_projection_id`
    (Slice 16 4th sub-slice) verbatim.
    """

    return f"{REVIEW_PROJECTION_ID_PREFIX}{corpus_id}"


# --- Canonical-JSON + SHA-256 digest helpers (mirrors Slice 15 1st sub-slice
#     compute_scorecard_digest + Slice 13A compute_completeness_digest +
#     Slice 14 compute_payload_sha256 + Slice 16 1st sub-slice
#     compute_finding_idempotency_key + Slice 16 4th sub-slice
#     compute_findings_digest canonical-JSON discipline) ----------------------


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Mirrors :func:`iriai_build_v2.execution_control.governance_finding_writer._canonical_json`
    + :func:`iriai_build_v2.execution_control.governance_scorecard_writer._canonical_json`
    + :func:`iriai_build_v2.execution_control.finding_engine._canonical_json`
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

    Mirrors :func:`iriai_build_v2.execution_control.governance_finding_writer._sha256_hex`
    + :func:`iriai_build_v2.execution_control.governance_scorecard_writer._sha256_hex`
    + :func:`iriai_build_v2.execution_control.finding_engine._sha256_hex`
    + :func:`iriai_build_v2.execution_control.governance_metrics._sha256_hex`
    + :func:`iriai_build_v2.execution_control.commit_provenance._sha256_hex`
    + :func:`iriai_build_v2.execution_control.completeness._sha256_hex`
    verbatim. Stdlib-only per the implementer prompt § "Non-negotiables"
    (``hashlib`` + ``json`` only).
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_decisions_digest(
    decisions: list[PolicyRecommendationDecision],
) -> str:
    """Compute the deterministic SHA-256-derived digest for a list of
    :class:`~iriai_build_v2.execution_control.policy_recommendation.PolicyRecommendationDecision`
    records.

    Per doc-17:182-188 *"Store recommendations as typed governance rows
    and project review artifacts such as
    ``review:governance-recommendations:{corpus_id}``."* the digest is
    the cross-process tamper-detection anchor subsequent re-projections
    rely on: two re-runs against the same decision-set MUST produce
    byte-identical digests; a digest change signals a re-projection
    (either new decisions or modified decisions).

    The digest is computed over the canonical-JSON projection of the
    decisions list; each decision's typed shape (via
    :meth:`BaseModel.model_dump` with ``mode='json'``) preserves all 6
    doc-17:99-105 fields including the per-decision ``recommendation_id``
    + ``decision`` Literal + ``decided_by`` + ``decided_at`` ISO-string
    + ``rationale`` + ``evidence_refs`` (typed-ref-shape only; NEVER
    raw bodies).

    The decisions list is NOT sorted before digesting -- the caller's
    upstream decision-emission flow is responsible for deterministic
    ordering (per the Slice 16 4th sub-slice
    :func:`~iriai_build_v2.execution_control.governance_finding_writer.compute_findings_digest`
    + Slice 15 1st sub-slice
    :func:`~iriai_build_v2.execution_control.governance_metrics.compute_scorecard_digest`
    precedent). Subsequent Slice 17 6th sub-slice consumer read APIs
    that require order-invariance MAY pre-sort the decisions list
    before passing to the writer.

    Mirrors the Slice 16 4th sub-slice
    :func:`~iriai_build_v2.execution_control.governance_finding_writer.compute_findings_digest`
    + Slice 15 1st sub-slice
    :func:`~iriai_build_v2.execution_control.governance_metrics.compute_scorecard_digest`
    + Slice 13A
    :func:`~iriai_build_v2.execution_control.completeness.compute_completeness_digest`
    + Slice 14
    :func:`~iriai_build_v2.execution_control.commit_provenance.compute_payload_sha256`
    canonical-JSON + SHA-256 discipline verbatim.

    :param decisions: the list of typed
        :class:`~iriai_build_v2.execution_control.policy_recommendation.PolicyRecommendationDecision`
        records to digest.
    :returns: the SHA-256 hex digest (64-char string) of the
        canonical-JSON projection of the decisions list.
    """

    canonical_list: list[dict[str, Any]] = [
        decision.model_dump(mode="json") for decision in decisions
    ]
    payload: dict[str, Any] = {"decisions": canonical_list}
    return _sha256_hex(_canonical_json(payload))


# --- Typed inputs (mirrors Slice 15 4th sub-slice ScorecardWriterInputs +
#     Slice 16 4th sub-slice FindingWriterInputs precedent) -------------------


class DecisionWriterInputs(BaseModel):
    """Doc-17:172 step 4 + doc-17:99-105 + doc-17:182-188 -- typed
    bundle of all inputs the decision-record writer consumes.

    The bundle composes:

    * ``corpus_id`` -- the corpus identifier the decision rows group
      against (e.g. ``"8ac124d6"`` for the calibration fixture per
      doc-17:236-237; future feature ids for production decisions).
    * ``decisions`` -- the list of
      :class:`~iriai_build_v2.execution_control.policy_recommendation.PolicyRecommendationDecision`
      records the writer composes into the typed persistence shape.
      This is the typed output of the upstream decision-emission flow
      (e.g. a governance reviewer producing accept / reject /
      needs_more_evidence decisions); the writer's caller is
      responsible for the upstream emission.
    * ``recommendations`` -- the list of
      :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
      records the decisions reference (per doc-17:172 the decision
      record captures the reviewer's verdict over a specific
      recommendation; the typed recommendation surface is the audit-
      trail evidence carried alongside). Optional; defaults to
      empty list when callers persist decisions WITHOUT the
      typed recommendation context (e.g. when the recommendations
      were already persisted in a prior projection).
    * ``validation_results`` -- the list of optional
      :class:`~iriai_build_v2.execution_control.policy_validation_interface.ValidationResult`
      records supporting the decision rationale (per doc-17:172 the
      reviewer's decision MAY cite the per-consumer validation
      verdict as supporting evidence; the typed validation surface is
      the audit-trail evidence carried alongside). Optional; defaults
      to empty list when callers persist decisions WITHOUT the typed
      validation context.
    * ``baseline_refs`` -- the list of Slice 13a shared
      :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
      baseline references the decision-set grounds on (per
      doc-17:182-188 + doc-13a:18-23). Optional; defaults to empty
      list.
    * ``warnings`` -- the list of warning-reason strings (mirrors the
      Slice 15 4th sub-slice
      :attr:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriterInputs.warnings`
      + Slice 16 4th sub-slice
      :attr:`~iriai_build_v2.execution_control.governance_finding_writer.FindingWriterInputs.warnings`
      shape).
    * ``incomplete_scopes`` -- the list of incomplete-scope descriptors
      (mirrors the Slice 15 4th + Slice 16 4th sub-slice
      ``incomplete_scopes`` shape; free-form dicts so the writer
      surface can carry rich incomplete-scope shapes).

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-17:240-289).** :attr:`baseline_refs` is a list of Slice 13a
    shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    (imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`; NOT
    redefined here). Per doc-13a:285-287 step 9 the shared model is
    the authority for governance evidence-ref semantics.

    Mirrors :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriterInputs`
    (Slice 15 4th sub-slice) +
    :class:`~iriai_build_v2.execution_control.governance_finding_writer.FindingWriterInputs`
    (Slice 16 4th sub-slice) verbatim per the chunk-shape decision.
    """

    # ``extra="forbid"`` aligns with the Slice 16 4th sub-slice precedent
    # at src/iriai_build_v2/execution_control/governance_finding_writer.py:531
    # + the Slice 15 4th sub-slice precedent at
    # src/iriai_build_v2/execution_control/governance_scorecard_writer.py:342
    # + the Slice 17 1st sub-slice precedent at
    # src/iriai_build_v2/execution_control/policy_recommendation.py:376
    # + the Slice 17 3rd sub-slice precedent at
    # src/iriai_build_v2/execution_control/policy_validation_interface.py:294
    # -- unknown fields fail closed as a typed ``ValidationError`` rather
    # than being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    corpus_id: str
    """The corpus identifier the decision rows group against (e.g.
    ``"8ac124d6"`` for the calibration fixture per doc-17:236-237;
    future feature ids for production decisions)."""

    decisions: list[PolicyRecommendationDecision]
    """Doc-17:172 step 4 + doc-17:99-105 -- the list of
    :class:`~iriai_build_v2.execution_control.policy_recommendation.PolicyRecommendationDecision`
    records the writer composes into the typed persistence shape.

    Per doc-17:101 each decision carries one of the 3 typed Literal
    values ``"accept"`` / ``"reject"`` / ``"needs_more_evidence"``;
    the typed shape enforces the Literal range at construction so
    typo-d decisions fail closed with a typed ``ValidationError`` per
    the auto-memory ``feedback_no_silent_degradation`` rule.

    The writer preserves all 6 doc-17:99-105 fields verbatim (no
    mutation; the typed shape itself enforces the invariants)."""

    recommendations: list[GovernancePolicyRecommendation] = Field(
        default_factory=list
    )
    """Doc-17:172 + doc-17:75-97 -- optional list of
    :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
    records the decisions reference (the typed recommendation surface
    is the audit-trail evidence carried alongside).

    Defaults to empty list -- callers MAY persist decisions WITHOUT
    the typed recommendation context (e.g. when the recommendations
    were already persisted in a prior projection). When non-empty, the
    writer projects the recommendations onto the review projection via
    ``model_dump(mode='json')`` (refs-only; NEVER raw bodies)."""

    validation_results: list[ValidationResult] = Field(default_factory=list)
    """Doc-17:172 + doc-17:170-171 -- optional list of
    :class:`~iriai_build_v2.execution_control.policy_validation_interface.ValidationResult`
    records supporting the decision rationale.

    Per doc-17:172 the reviewer's decision MAY cite the per-consumer
    validation verdict as supporting evidence; per doc-17:170-171
    *"Validation proves the artifact can be understood, not that it
    should be activated."* the validation result is read-only with
    respect to consumer state (a passing validation does NOT activate
    the recommendation).

    Defaults to empty list -- callers MAY persist decisions WITHOUT
    the typed validation context. When non-empty, the writer projects
    the validation results onto the review projection via
    ``model_dump(mode='json')`` (refs-only; NEVER raw bodies)."""

    baseline_refs: list[GovernanceEvidenceRef] = Field(default_factory=list)
    """Doc-17:182-188 + doc-13a:18-23 -- the list of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    baseline references the decision-set grounds on.

    Per doc-17:182-188 + doc-17:240-289 + doc-13a:285-287 step 9 the
    baseline refs cite typed evidence-set refs (NOT raw artifact
    bodies); the :class:`GovernanceEvidenceRef` typed surface enforces
    this no-raw-body-hydration discipline at construction."""

    warnings: list[str] = Field(default_factory=list)
    """Free-form warning-reason strings naming non-blocking issues
    encountered during decision-set persistence (e.g.
    ``"stale_recommendation_reference"`` /
    ``"missing_validation_for_decision"`` /
    ``"evidence_ref_count_exceeds_cap"``). Mirrors the Slice 15 4th
    + Slice 16 4th sub-slice
    :attr:`~iriai_build_v2.execution_control.governance_finding_writer.FindingWriterInputs.warnings`
    shape."""

    incomplete_scopes: list[dict[str, Any]] = Field(default_factory=list)
    """The list of incomplete-scope descriptors. Mirrors the Slice 15
    4th + Slice 16 4th sub-slice
    :attr:`~iriai_build_v2.execution_control.governance_finding_writer.FindingWriterInputs.incomplete_scopes`
    shape; free-form dicts so callers can carry rich incomplete-scope
    shapes."""


# --- Typed gap shape (mirrors Slice 15 4th sub-slice ScorecardPersistenceGap
#     + Slice 16 4th sub-slice FindingPersistenceGap precedent) --------------


class DecisionPersistenceGap(BaseModel):
    """Typed governance-gap shape produced when the decision-record
    writer fails to persist a decision-set or review projection
    structurally.

    Mirrors the Slice 16 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_finding_writer.FindingPersistenceGap`
    + Slice 15 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardPersistenceGap`
    + Slice 14
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    + Slice 15 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_metric_extractor.GovernanceMetricExtractionGap`
    + Slice 16 2nd / 3rd-A / 3rd-B + Slice 17 2nd / 3rd sub-slice gap
    shapes verbatim per the chunk-shape decision.

    Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED here
    per doc-17:172 + doc-17:182-188 governance-projection discipline)
    the gap is NON-blocking: the caller MUST NOT propagate it to the
    executor / checkpoint / merge-queue / resume code paths. The
    corresponding typed failure id
    :data:`DECISION_RECORD_PERSISTENCE_FAILURE_ID`
    (``decision_record_persistence_failed``) registers under the
    EXISTING ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-17:240-289).** :attr:`evidence_refs` is a list of Slice 13a
    shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    (imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`; NOT
    redefined here).
    """

    # ``extra="forbid"`` aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["decision_record_persistence_failed"]
    """Doc-17:172 + doc-17:182-188 + doc-14:192-201 -- the typed
    failure id. Registers under the EXISTING ``evidence_corruption``
    failure_class with NON-blocking routing per doc-14:242-243 +
    doc-17:172 + doc-17:182-188."""

    corpus_id: str
    """The corpus scope of the failed persistence (same as the
    :attr:`DecisionWriterInputs.corpus_id`)."""

    decisions_digest: str | None
    """The decisions digest (per :func:`compute_decisions_digest`)
    computed at the time of failure, or ``None`` if the failure happened
    before the digest could be computed.

    The digest is the cross-process tamper-detection anchor per
    doc-17:182-188; preserving it in the gap lets downstream consumers
    correlate the gap with the historical decision-set whose
    persistence failed (per doc-17:222-226 rollback discipline:
    historical decisions remain as review artifacts even when newer
    re-projections fail)."""

    review_projection_id: str | None
    """The bounded review projection id (per
    :func:`compute_decision_projection_id`) the failure targeted, or
    ``None`` if the failure happened before the projection id could be
    computed."""

    reason: str
    """Free-form gap reason (e.g.
    ``decisions_count_exceeds_cap``,
    ``review_projection_digest_mismatch``,
    ``decisions_construction_failed``)."""

    observed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    """Doc-17:172 + doc-13:201-204 -- the typed observation timestamp
    for the gap. Defaults to UTC now; projects to ISO-8601 string
    under :meth:`BaseModel.model_dump` with ``mode='json'``."""

    evidence_refs: list[GovernanceEvidenceRef] = Field(default_factory=list)
    """Optional list of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    evidence references the gap cites.

    Defaults to empty list -- per doc-17:172 the gap is advisory; it
    does NOT mandate evidence refs on every gap (the gap is itself
    derived from the typed
    :class:`DecisionWriterInputs` surface which carries its own
    typed evidence chain)."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap (e.g. the
    decision count involved, the projection cap that was exceeded, the
    error detail). Free-form per the doc-14:192-201 + doc-17:172
    governance-gap contract."""


# --- Typed writer result (mirrors Slice 17 3rd sub-slice ValidationResult
#     two-field result shape) -------------------------------------------------


class DecisionWriterResult(BaseModel):
    """Doc-17:172 step 4 + doc-17:182-188 -- typed per-call
    decision-record writer result.

    A :class:`DecisionWriterResult` is the typed audit record the
    :meth:`DecisionRecordWriter.write_decisions` method emits per
    call. The shape carries:

    * ``persisted_decision_ids`` -- the typed list of recommendation_id
      strings successfully persisted (preserves the per-decision
      ``recommendation_id`` verbatim; the typed surface enforces all
      doc-17:99-105 invariants at construction).
    * ``gap_records`` -- the typed list of
      :class:`DecisionPersistenceGap` records emitted when a
      structural failure occurs during persistence (NEVER raises per
      the non-blocking observer contract; mirrors the Slice 16 4th
      sub-slice :attr:`~iriai_build_v2.execution_control.governance_finding_writer.GovernanceFindingWriter.gap_findings`
      property pattern).
    * ``truncated`` -- typed boolean flag for whether the decisions
      list was truncated at the LIMIT cap+1 bounded-read discipline
      (per IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads").
      Defaults to ``False`` (no truncation).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    Per doc-17:172 *"Add decision records for accept/reject/needs-more-
    evidence."* the result's ``persisted_decision_ids`` field is the
    typed surface the (future) Slice 17 6th sub-slice consumer read
    APIs use to distinguish accepted-but-not-activated decisions from
    rejected decisions; per doc-17:217 *"Recommendation generation has
    no direct mutation authority."* the persisted ``"accept"``
    decision does NOT activate the recommendation -- activation
    remains consumer-owned per doc-17:159-163.
    """

    model_config = ConfigDict(extra="forbid")

    persisted_decision_ids: list[str] = Field(default_factory=list)
    """Doc-17:100 + doc-17:172 -- the list of recommendation_id strings
    successfully persisted. Each entry is the
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.PolicyRecommendationDecision.recommendation_id`
    of a persisted decision row; the typed list ordering preserves the
    input ordering of
    :attr:`DecisionWriterInputs.decisions`."""

    gap_records: list[DecisionPersistenceGap] = Field(default_factory=list)
    """Doc-17:172 + doc-14:242-243 -- the typed list of
    :class:`DecisionPersistenceGap` records emitted when a structural
    failure occurs during persistence.

    Per doc-14:242-243 the writer is non-blocking (NEVER raises a
    failure to the caller); every structural failure projects onto a
    typed gap record accumulated in this list. Empty when no failures
    occurred. Mirrors the Slice 16 4th sub-slice
    :attr:`~iriai_build_v2.execution_control.governance_finding_writer.GovernanceFindingWriter.gap_findings`
    + Slice 15 4th sub-slice
    :attr:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter.gap_findings`
    property pattern."""

    truncated: bool = False
    """IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" -- typed
    boolean flag for whether the decisions list was truncated at the
    LIMIT cap+1 bounded-read discipline.

    Defaults to ``False`` (no truncation). Set to ``True`` when the
    input decisions list exceeded the
    :data:`DEFAULT_REVIEW_PROJECTION_CAP` (or the per-call ``cap``
    override) -- the caller can detect overflow by checking this
    flag."""


# --- The decision-record writer (mirrors Slice 15 4th sub-slice
#     ScorecardWriter + Slice 16 4th sub-slice GovernanceFindingWriter
#     precedent) --------------------------------------------------------------


class DecisionRecordWriter:
    """Decision-record writer + bounded review projection emitter
    (doc-17:172 step 4 + doc-17:182-188).

    Per *"Add decision records for accept/reject/needs-more-
    evidence."* (doc-17:172) + *"Store recommendations as typed
    governance rows and project review artifacts such as
    ``review:governance-recommendations:{corpus_id}``."*
    (doc-17:182-188) the writer consumes the Slice 17 1st sub-slice
    typed-shape foundation
    (:class:`~iriai_build_v2.execution_control.policy_recommendation.PolicyRecommendationDecision`
    + :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`)
    + the optional Slice 17 3rd sub-slice typed
    :class:`~iriai_build_v2.execution_control.policy_validation_interface.ValidationResult`
    + the Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    and projects them onto:

    1. A typed :class:`DecisionWriterResult` carrying the
       ``persisted_decision_ids`` list + the ``gap_records`` list +
       the ``truncated`` flag (the typed persistence shape; mirrors
       the Slice 15 4th sub-slice + Slice 16 4th sub-slice
       persistence shapes).
    2. A bounded review projection dict (the typed review-artifact
       shape keyed by :func:`compute_decision_projection_id`).

    **Bounded-read discipline (IMPLEMENTATION_PROMPT_GOVERNANCE.md §
    "Bounded reads" + doc-17:182-188).** The review projection's
    decisions list + per-decision evidence_refs list + recommendations
    list + validation_results list + baseline_refs list all use the
    ``LIMIT cap+1`` truncation discipline: the writer emits up to
    ``cap+1`` items so the caller can detect overflow (per the typed
    snapshot pattern). The default cap is
    :data:`DEFAULT_REVIEW_PROJECTION_CAP`.

    **Refs-only discipline (doc-17:172 + doc-17:182-188).** The review
    projection carries ONLY typed control fields + cited refs
    (:class:`GovernanceEvidenceRef` typed shape; the
    :class:`GovernancePolicyRecommendation` typed shape; the
    :class:`ValidationResult` typed shape -- all via
    ``model_dump(mode='json')``) -- NEVER raw artifact bodies. The
    :class:`GovernanceEvidenceRef` typed surface itself enforces the
    no-raw-body discipline at construction.

    **Digest discipline (doc-17:182-188).** The review projection
    carries the decisions digest (per
    :func:`compute_decisions_digest`) for tamper detection: two
    re-runs against the same decision-set MUST produce byte-identical
    digests.

    **Rollback discipline (doc-17:222-226).** The writer's typed-shape
    outputs are immutable rows: future re-projections emit NEW
    :class:`DecisionWriterInputs` bundles (with NEW per-decision
    ``recommendation_id`` references) rather than mutating historical
    decisions. The :meth:`write_decisions` surface is a pure
    projection; the actual storage mechanism (e.g. a Postgres
    ``governance_recommendation_decision:*`` row insert) is the
    caller's responsibility.

    **Non-blocking discipline.** The writer NEVER raises a failure to
    the caller. Any structural failure projects onto a typed
    :class:`DecisionPersistenceGap` accumulated on the typed
    :attr:`DecisionWriterResult.gap_records` field (mirrored on the
    :attr:`DecisionRecordWriter.gap_records` property post-call).

    **No consumer activation authority (doc-17:159-163 + doc-17:217 +
    doc-17:222-226).** The writer is a **pure projection observer**:
    a persisted ``"accept"`` decision row does NOT activate the
    recommendation's proposed policy artifact; activation remains
    consumer-owned per doc-17:159-163. The writer GRANTS NO CONSUMER-
    SIDE ACTIVATION AUTHORITY.

    Per the auto-memory ``feedback_no_overengineer_use_library`` rule
    the writer mirrors the Slice 15 4th sub-slice + Slice 16 4th
    sub-slice precedents (single class with one or more public
    projection methods) without introducing new abstractions.

    Example usage::

        from iriai_build_v2.execution_control.decision_record_writer import (
            DecisionRecordWriter, DecisionWriterInputs,
        )
        from iriai_build_v2.execution_control.policy_recommendation import (
            PolicyRecommendationDecision,
        )

        writer = DecisionRecordWriter()
        result = writer.write_decisions(
            DecisionWriterInputs(
                corpus_id="8ac124d6",
                decisions=[
                    PolicyRecommendationDecision(
                        recommendation_id="rec-1",
                        decision="accept",
                        decided_by="reviewer-1",
                        decided_at=datetime.now(timezone.utc),
                        rationale="Validation passed; replay supports.",
                        evidence_refs=[],
                    )
                ],
                recommendations=[],
                validation_results=[],
                baseline_refs=[],
                warnings=[],
                incomplete_scopes=[],
            )
        )
        projection = writer.write_review_projection(
            decisions=[<...>], corpus_id="8ac124d6"
        )
        # caller persists result + projection.
    """

    def __init__(self) -> None:
        """Construct a decision-record writer.

        The writer is stateless aside from the :attr:`gap_records`
        accumulator the public :meth:`write_decisions` +
        :meth:`write_review_projection` surfaces populate. Each call
        to a public method RESETS the accumulator (so per-call gap
        records remain bounded; callers that need cross-call gap
        accumulation should snapshot the property after each call).

        Mirrors the Slice 15 4th sub-slice
        :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter`
        + Slice 16 4th sub-slice
        :class:`~iriai_build_v2.execution_control.governance_finding_writer.GovernanceFindingWriter`
        constructor verbatim.
        """

        self._gap_records: list[DecisionPersistenceGap] = []

    @property
    def gap_records(self) -> list[DecisionPersistenceGap]:
        """The list of :class:`DecisionPersistenceGap` records the
        most-recent public-method call produced.

        Per the Slice 14
        :class:`~iriai_build_v2.execution_control.commit_provenance_writer.GitProvenanceWriter`
        + Slice 15 4th sub-slice
        :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter`
        + Slice 16 4th sub-slice
        :class:`~iriai_build_v2.execution_control.governance_finding_writer.GovernanceFindingWriter`
        precedents the writer NEVER raises a failure to the caller --
        every structural failure projects onto a typed gap record.

        Returns a fresh snapshot list each call so caller mutations do
        not leak into the internal accumulator.
        """

        return list(self._gap_records)

    def write_decisions(
        self,
        inputs: DecisionWriterInputs,
        *,
        cap: int | None = None,
    ) -> DecisionWriterResult:
        """Project the typed :class:`DecisionWriterInputs` bundle into
        a typed :class:`DecisionWriterResult` (doc-17:172 step 4 +
        doc-17:182-188).

        Returns the typed :class:`DecisionWriterResult` with the
        ``persisted_decision_ids`` list (preserves the per-decision
        ``recommendation_id`` verbatim; the typed surface enforces
        all doc-17:99-105 invariants at construction; the writer does
        NOT mutate upstream decision rows) + the ``gap_records`` list
        (typed :class:`DecisionPersistenceGap` rows projected on
        persistence failure per the non-blocking observer contract) +
        the ``truncated`` flag (typed boolean signalling whether the
        decisions list was truncated at the LIMIT cap+1 bounded-read
        discipline).

        Per doc-14:242-243 + doc-17:172 + doc-17:182-188 NEVER raises
        a failure to the caller. Any structural failure projects onto
        a typed :class:`DecisionPersistenceGap` accumulated in the
        result's ``gap_records`` field; the persisted_decision_ids in
        that case is the most-conservative projection (empty list) so
        the downstream Slice 17 6th sub-slice consumer read APIs can
        block on the corpus.

        The method RESETS the :attr:`gap_records` accumulator at
        entry; per-call gap records remain bounded.

        Per IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" the
        decisions list is truncated at ``cap+1`` (the writer emits up
        to ``cap+1`` items so the caller can detect overflow). The
        default cap is :data:`DEFAULT_REVIEW_PROJECTION_CAP`.

        Per doc-17:159-163 + doc-17:217 + doc-17:222-226 the writer
        GRANTS NO CONSUMER-SIDE ACTIVATION AUTHORITY -- a persisted
        ``"accept"`` decision does NOT activate the recommendation's
        proposed policy artifact; activation remains consumer-owned
        per doc-17:159-163.

        Mirrors :meth:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter.write_scorecard`
        (Slice 15 4th sub-slice) +
        :meth:`~iriai_build_v2.execution_control.governance_finding_writer.GovernanceFindingWriter.write_findings`
        (Slice 16 4th sub-slice) verbatim.

        :param inputs: the typed :class:`DecisionWriterInputs` bundle.
        :param cap: optional override for the LIMIT cap+1 truncation
            threshold; defaults to
            :data:`DEFAULT_REVIEW_PROJECTION_CAP`.
        :returns: the typed :class:`DecisionWriterResult` (NEVER
            raises; failures project onto the result's ``gap_records``
            field).
        """

        # Reset per-call accumulator (mirrors Slice 15 4th + Slice 16 4th
        # sub-slice ScorecardWriter / GovernanceFindingWriter pattern).
        self._gap_records = []

        effective_cap = cap if cap is not None else DEFAULT_REVIEW_PROJECTION_CAP

        # ── LIMIT cap+1 truncation of the decisions list per
        #    IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" ────────────
        # Emit at most cap+1 items so the caller can detect overflow.
        decisions_limit = effective_cap + 1
        truncated_decisions = inputs.decisions[:decisions_limit]
        decisions_truncated = len(inputs.decisions) > effective_cap

        # The projection is a pure pass-through: each
        # PolicyRecommendationDecision's typed shape is already
        # validated at construction by the upstream emission flow.
        # Defensive try/except wraps the list construction so any
        # structural failure projects onto a typed gap rather than
        # being raised to the caller (non-blocking observer contract
        # per doc-14:242-243 + doc-17:172 + doc-17:182-188).
        try:
            persisted_ids: list[str] = [
                decision.recommendation_id for decision in truncated_decisions
            ]
        except Exception as exc:
            # Defensive: list comprehension should never fail given the
            # typed inputs above, but the writer is non-blocking per
            # the doc-14:242-243 contract. Project onto a typed gap +
            # return an empty result so the downstream Slice 17 6th
            # sub-slice consumer read APIs can block on the corpus.
            self._gap_records.append(
                DecisionPersistenceGap(
                    failure_id=DECISION_RECORD_PERSISTENCE_FAILURE_ID,
                    corpus_id=inputs.corpus_id,
                    decisions_digest=None,
                    review_projection_id=compute_decision_projection_id(
                        inputs.corpus_id
                    ),
                    reason=f"decisions_construction_failed: {type(exc).__name__}",
                    evidence_payload={"error_detail": str(exc)[:500]},
                )
            )
            persisted_ids = []

        return DecisionWriterResult(
            persisted_decision_ids=persisted_ids,
            gap_records=list(self._gap_records),
            truncated=decisions_truncated,
        )

    def write_review_projection(
        self,
        *,
        decisions: list[PolicyRecommendationDecision],
        corpus_id: str,
        recommendations: list[GovernancePolicyRecommendation] | None = None,
        validation_results: list[ValidationResult] | None = None,
        baseline_refs: list[GovernanceEvidenceRef] | None = None,
        warnings: list[str] | None = None,
        incomplete_scopes: list[dict[str, Any]] | None = None,
        cap: int | None = None,
    ) -> dict[str, Any]:
        """Build the bounded review projection dict for a list of
        :class:`PolicyRecommendationDecision` records at the typed
        :data:`REVIEW_PROJECTION_ID_PREFIX` key prefix per
        doc-17:182-188.

        The projection's structure:

        * ``review_projection_id`` -- the typed key per
          :func:`compute_decision_projection_id`.
        * ``corpus_id`` -- the corpus identifier.
        * ``generated_at`` -- ISO-8601 string projection of
          ``datetime.now(timezone.utc)`` (the cross-process freshness
          anchor per the Slice 15 4th + Slice 16 4th sub-slice
          precedent verbatim).
        * ``decisions_digest`` -- the SHA-256 digest from
          :func:`compute_decisions_digest` for tamper detection per
          doc-17:182-188.
        * ``decisions`` -- the decision values projected as compact
          dicts carrying ONLY typed control fields + cited refs
          (NEVER raw bodies), truncated at ``cap+1`` per the
          bounded-read discipline.
        * ``recommendations`` -- the recommendation values projected
          via ``model_dump(mode='json')`` (refs-only; NEVER raw
          bodies), truncated at ``cap+1``.
        * ``validation_results`` -- the validation results projected
          via ``model_dump(mode='json')`` (refs-only; NEVER raw
          bodies), truncated at ``cap+1``.
        * ``baseline_refs`` -- the baseline refs projected via
          ``model_dump(mode='json')`` (typed-ref shape; NEVER raw
          bodies), truncated at ``cap+1``.
        * ``incomplete_scopes`` -- carried verbatim from the
          ``incomplete_scopes`` parameter.
        * ``warnings`` -- carried verbatim from the ``warnings``
          parameter.
        * ``decisions_truncated`` / ``recommendations_truncated`` /
          ``validation_results_truncated`` /
          ``baseline_refs_truncated`` -- bool flags signalling
          whether the cap was reached (so the caller can detect
          overflow per the LIMIT cap+1 discipline).

        Per IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" the
        projection EMITS at most ``cap+1`` items in each list (so the
        caller can detect overflow by checking
        ``len(projection["decisions"]) > cap``).

        Per doc-17:172 + doc-17:182-188 the projection EMITS ONLY
        REFS (NOT raw bodies). The
        :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
        typed surface itself enforces the no-raw-body discipline.

        Per doc-17:182-188 the projection CARRIES the decisions digest
        (for tamper detection) + the per-decision recommendation_id
        (so subsequent consumers can correlate the persisted decision
        rows with the historical recommendations).

        Per doc-14:242-243 + doc-17:172 + doc-17:182-188 NEVER raises
        a failure to the caller. Any structural failure projects onto
        a typed :class:`DecisionPersistenceGap` accumulated on
        :attr:`gap_records`; the returned projection in that case
        still carries the typed shape (with the most-conservative
        projection) so the downstream Slice 17 6th sub-slice consumer
        read APIs can block on the corpus.

        Mirrors :meth:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter.write_review_projection`
        (Slice 15 4th sub-slice) +
        :meth:`~iriai_build_v2.execution_control.governance_finding_writer.GovernanceFindingWriter.write_review_projection`
        (Slice 16 4th sub-slice) verbatim per the chunk-shape
        decision.

        :param decisions: the typed
            ``list[PolicyRecommendationDecision]`` to project.
        :param corpus_id: the corpus identifier the projection groups
            against (keyword-only).
        :param recommendations: the typed
            ``list[GovernancePolicyRecommendation]`` the decisions
            reference (defaults to an empty list).
        :param validation_results: the typed
            ``list[ValidationResult]`` supporting the decision
            rationale (defaults to an empty list).
        :param baseline_refs: the typed baseline references (defaults
            to an empty list).
        :param warnings: free-form warning strings (defaults to an
            empty list).
        :param incomplete_scopes: free-form incomplete-scope dicts
            (defaults to an empty list).
        :param cap: optional override for the LIMIT cap+1 truncation
            threshold; defaults to
            :data:`DEFAULT_REVIEW_PROJECTION_CAP`.
        """

        # Reset per-call accumulator (mirrors Slice 15 4th + Slice 16 4th
        # sub-slice ScorecardWriter / GovernanceFindingWriter pattern).
        self._gap_records = []

        # Coerce optional parameter defaults (never mutates caller lists).
        effective_recommendations: list[GovernancePolicyRecommendation] = (
            list(recommendations) if recommendations is not None else []
        )
        effective_validation_results: list[ValidationResult] = (
            list(validation_results) if validation_results is not None else []
        )
        effective_baseline_refs: list[GovernanceEvidenceRef] = (
            list(baseline_refs) if baseline_refs is not None else []
        )
        effective_warnings: list[str] = list(warnings) if warnings is not None else []
        effective_incomplete_scopes: list[dict[str, Any]] = (
            [dict(scope) for scope in incomplete_scopes]
            if incomplete_scopes is not None
            else []
        )

        effective_cap = cap if cap is not None else DEFAULT_REVIEW_PROJECTION_CAP

        # Compute the digest defensively: if compute_decisions_digest
        # fails (should never happen given the typed shape but the
        # writer is non-blocking) project onto a typed gap record.
        try:
            digest = compute_decisions_digest(decisions)
        except Exception as exc:
            self._gap_records.append(
                DecisionPersistenceGap(
                    failure_id=DECISION_RECORD_PERSISTENCE_FAILURE_ID,
                    corpus_id=corpus_id,
                    decisions_digest=None,
                    review_projection_id=compute_decision_projection_id(corpus_id),
                    reason=f"decisions_digest_failed: {type(exc).__name__}",
                    evidence_payload={"error_detail": str(exc)[:500]},
                )
            )
            digest = ""

        # ── LIMIT cap+1 truncation of the decisions list per
        #    IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" ───────────
        # Emit at most cap+1 items so the caller can detect overflow.
        decisions_limit = effective_cap + 1
        truncated_decisions = decisions[:decisions_limit]
        decisions_truncated = len(decisions) > effective_cap

        # ── LIMIT cap+1 truncation of the recommendations list ──────────
        recommendations_limit = effective_cap + 1
        truncated_recommendations = effective_recommendations[
            :recommendations_limit
        ]
        recommendations_truncated = (
            len(effective_recommendations) > effective_cap
        )

        # ── LIMIT cap+1 truncation of the validation_results list ───────
        validation_results_limit = effective_cap + 1
        truncated_validation_results = effective_validation_results[
            :validation_results_limit
        ]
        validation_results_truncated = (
            len(effective_validation_results) > effective_cap
        )

        # ── LIMIT cap+1 truncation of the baseline_refs list ────────────
        baseline_refs_limit = effective_cap + 1
        truncated_baseline_refs = effective_baseline_refs[:baseline_refs_limit]
        baseline_refs_truncated = (
            len(effective_baseline_refs) > effective_cap
        )

        # Project the decision values to bounded review-projection
        # dicts. The projection carries ONLY typed control fields +
        # cited refs (NEVER raw bodies). The evidence_refs list per
        # decision is also truncated at cap+1 per the bounded-read
        # discipline.
        projected_decisions: list[dict[str, Any]] = []
        for decision in truncated_decisions:
            evidence_refs_limit = effective_cap + 1
            decision_evidence_refs = decision.evidence_refs[
                :evidence_refs_limit
            ]
            decision_evidence_refs_truncated = (
                len(decision.evidence_refs) > effective_cap
            )
            projected_decisions.append(
                {
                    "recommendation_id": decision.recommendation_id,
                    "decision": decision.decision,
                    "decided_by": decision.decided_by,
                    # decided_at projects to ISO-8601 string per
                    # the doc-13:201-204 canonical-JSON discipline +
                    # the Pydantic v2 mode='json' contract.
                    "decided_at": decision.decided_at.isoformat()
                    if isinstance(decision.decided_at, datetime)
                    else decision.decided_at,
                    "rationale": decision.rationale,
                    # evidence_refs use mode='json' to project to
                    # cross-process stable dict shapes per the Slice
                    # 13a GovernanceEvidenceRef typed contract; this
                    # preserves the typed ref shape (authority +
                    # ref_id + digest + quality + completeness)
                    # WITHOUT inserting any raw body field. The
                    # mode='json' projection lowers datetime to
                    # ISO-8601 string per the Pydantic v2
                    # canonical-projection contract.
                    "evidence_refs": [
                        ref.model_dump(mode="json")
                        for ref in decision_evidence_refs
                    ],
                    "evidence_refs_truncated": decision_evidence_refs_truncated,
                }
            )

        # Project the recommendations to typed-shape dicts per
        # doc-17:172 + doc-17:182-188 (NEVER raw bodies). Mode='json'
        # projection per the Slice 17 1st sub-slice
        # GovernancePolicyRecommendation contract.
        projected_recommendations = [
            rec.model_dump(mode="json") for rec in truncated_recommendations
        ]

        # Project the validation results to typed-shape dicts per
        # doc-17:172 + doc-17:182-188 (NEVER raw bodies). Mode='json'
        # projection per the Slice 17 3rd sub-slice ValidationResult
        # contract.
        projected_validation_results = [
            res.model_dump(mode="json")
            for res in truncated_validation_results
        ]

        # Project the baseline_refs to cited refs per doc-17:172 +
        # doc-17:182-188 + doc-13a:18-23 (NEVER raw bodies).
        # Mode='json' projection per the Slice 13a
        # GovernanceEvidenceRef contract.
        projected_baseline_refs = [
            ref.model_dump(mode="json") for ref in truncated_baseline_refs
        ]

        # The full review projection dict.
        review_projection_id = compute_decision_projection_id(corpus_id)
        # generated_at projects to ISO-8601 string per the
        # doc-13:201-204 canonical-JSON discipline (mirrors Slice 15
        # 4th + Slice 16 4th sub-slice generated_at projection
        # pattern verbatim).
        generated_at_iso = datetime.now(timezone.utc).isoformat()
        projection: dict[str, Any] = {
            "review_projection_id": review_projection_id,
            "corpus_id": corpus_id,
            "generated_at": generated_at_iso,
            "decisions_digest": digest,
            "decisions": projected_decisions,
            "decisions_truncated": decisions_truncated,
            "recommendations": projected_recommendations,
            "recommendations_truncated": recommendations_truncated,
            "validation_results": projected_validation_results,
            "validation_results_truncated": validation_results_truncated,
            "baseline_refs": projected_baseline_refs,
            "baseline_refs_truncated": baseline_refs_truncated,
            "incomplete_scopes": effective_incomplete_scopes,
            "warnings": effective_warnings,
        }
        return projection
