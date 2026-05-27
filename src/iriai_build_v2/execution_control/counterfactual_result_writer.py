"""Slice 18 sixth sub-slice -- counterfactual-result persistence +
bounded review projection.

Per ``docs/execution-control-plane/18-counterfactual-replay-and-simulation.md``
§ Refactoring Steps step 6 (lines 116-117): *"Emit counterfactual
results as typed governance rows and review artifacts."*

Per doc-18:123-125 (Persistence And Artifact Compatibility): *"Replay
results are review/governance artifacts only. Replay must not write
``dag-*`` execution authority artifacts or active policy markers."*

Per doc-18:127-129 (Persistence And Artifact Compatibility): *"Historical
replay is immutable by corpus id and scenario id. New assumptions
require a new result version."*

Per doc-18:60-96 + doc-18:79-96 the typed Slice 18 1st sub-slice
:class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
BaseModel shape carries the 16 fields the result writer projects onto
typed governance rows + bounded review artifacts. The writer also
consumes the optional Slice 18 5th sub-slice typed
:class:`~iriai_build_v2.execution_control.counterfactual_metrics_comparator.MetricsComparatorResult`
records (the per-axis comparator output) as supporting evidence for
the review projection (per doc-18:115 + doc-18:88-92).

Per doc-18:123-125 *"Replay results are review/governance artifacts
only. Replay must not write `dag-*` execution authority artifacts or
active policy markers."* the result writer is the typed audit surface
that persists the typed counterfactual rows. Per doc-18:164 AC3
*"Replay cannot mutate live workflow state."* the writer GRANTS NO
CONSUMER-SIDE ACTIVATION AUTHORITY -- a persisted result row does NOT
activate the recommendation's proposed policy artifact; activation
remains consumer-owned per doc-17:159-163 (the Slice 18 7th sub-slice
recommendation citation hook enforces the cite-or-explicit-evidence-
needed contract structurally).

This module owns the counterfactual-result-writer +
bounded-review-projection surface that consumes:

* The Slice 18 1st sub-slice typed
  :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
  records (the typed result-row surface).
* Optionally, the Slice 18 5th sub-slice typed
  :class:`~iriai_build_v2.execution_control.counterfactual_metrics_comparator.MetricsComparatorResult`
  records (as supporting evidence for the per-result review projection;
  per doc-18:115 the comparator emits the per-axis confidence + delta
  records the writer carries alongside the typed result rows).
* The Slice 13a shared
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  (cited by :attr:`CounterfactualResult.policy_provenance_refs` +
  :attr:`CounterfactualResultWriterInputs.baseline_refs`).

The writer is a **post-checkpoint governance projection observer** that
emits BOTH the typed :class:`CounterfactualResultWriterResult`
governance rows AND the bounded review projection at
``review:governance-counterfactuals:{corpus_id}`` per doc-18:116-117.

**Persistence + review-projection surface.** The writer's public
methods:

* :meth:`CounterfactualResultWriter.write_results` -- composes a typed
  :class:`CounterfactualResultWriterInputs` bundle into a typed
  :class:`CounterfactualResultWriterResult` carrying the
  ``persisted_result_ids`` (preserves the per-result
  :attr:`CounterfactualResult.result_id` verbatim; the typed surface
  enforces all doc-18:79-96 invariants at construction; the writer does
  NOT mutate upstream result rows) + the ``gap_records`` typed gap list
  (typed :class:`CounterfactualResultPersistenceGap` rows projected on
  persistence failure per the non-blocking observer contract) + the
  ``truncated`` flag (typed boolean signalling whether the results
  list was truncated at the LIMIT cap+1 bounded-read discipline).
* :meth:`CounterfactualResultWriter.write_review_projection` -- builds
  the bounded review projection dict at the typed
  :data:`REVIEW_PROJECTION_ID_PREFIX` key prefix per doc-18:116-117.
  The projection is BOUNDED via the
  :data:`DEFAULT_REVIEW_PROJECTION_CAP` LIMIT cap+1 truncation
  discipline (per ``IMPLEMENTATION_PROMPT_GOVERNANCE.md`` § "Bounded
  reads"); EMITS ONLY REFS (NOT raw bodies) per doc-18:186-249; CARRIES
  the results digest (per :func:`compute_counterfactual_results_digest`)
  for tamper detection per doc-18:127-129; CITES the per-result
  ``result_id`` + ``result_version`` (so subsequent consumers can
  correlate the persisted result rows with the historical replay
  corpora).

**Bounded-read discipline (IMPLEMENTATION_PROMPT_GOVERNANCE.md §
"Bounded reads" + doc-18:186-249 + doc-13a:18-23).** Per *"Reuse the
typed snapshot's ``LIMIT cap+1`` truncation discipline and the
supervisor's ``SET LOCAL statement_timeout`` pattern. No artifact-body
hydration on the governance read path."* the review projection's
results list + per-result evidence_refs (``policy_provenance_refs``)
list + comparator_results list + baseline_refs list are all truncated
at ``cap+1`` (the projection emits up to ``cap+1`` items so the caller
can detect overflow). The default cap is
:data:`DEFAULT_REVIEW_PROJECTION_CAP` (200; mirrors the Slice 15 4th +
Slice 16 4th + Slice 17 4th values verbatim).

**Refs-only discipline (doc-18:186-249).** The review projection
carries ONLY:

* The result's typed control fields (``result_id`` +
  ``result_version`` + ``scenario_id`` + ``corpus_id`` +
  ``assumptions`` + ``validity_limits`` + ``safety_guard_class`` +
  ``estimated_delta_hours`` + ``estimated_delta_repair_cycles`` +
  ``estimated_delta_commit_failures`` + ``estimated_risk_change`` +
  ``confidence`` + ``invalidated_by`` + ``supporting_finding_ids`` +
  ``recommended_next_step``).
* The result's ``policy_provenance_refs`` projected as
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  cited refs via ``model_dump(mode='json')`` (the typed ref shape
  carries authority + ref_id + digest + quality + completeness -- NOT
  raw bodies).
* The supporting comparator result's typed control fields projected via
  ``model_dump(mode='json')`` (typed-shape carries axis_deltas + idempotency_key
  + result_id + scenario_result_id + emitted_at + invalidated_axes +
  overall_confidence -- NOT raw bodies).
* The baseline_refs projected via ``model_dump(mode='json')``
  (typed-ref shape).

The projection NEVER carries raw artifact bodies, raw event payloads,
or any field that requires unbounded scanning. The Slice 13a typed
:class:`GovernanceEvidenceRef` surface itself enforces the
no-raw-body-hydration discipline at construction.

**Digest discipline (doc-18:127-129).** The review projection carries
the results digest (:func:`compute_counterfactual_results_digest`) for
tamper detection: two re-runs against the same result-set MUST produce
byte-identical digests; a digest change signals a re-projection (either
new results, modified results, or a new result_version per the
doc-18:128-129 *"New assumptions require a new result version."*
contract).

**Rollback discipline (doc-18:170-173).** Per *"Rollback disables
simulation commands and leaves replay results as historical review
artifacts. Bad scenario logic is superseded by a new scenario version
rather than rewriting past results."* the writer's typed-shape outputs
are immutable rows: future re-projections emit NEW
:class:`CounterfactualResultWriterInputs` bundles (with NEW per-result
``result_version`` references) rather than mutating historical results.
The :meth:`CounterfactualResultWriter.write_results` surface is a pure
projection; the actual storage mechanism is the caller's responsibility
(e.g. a Postgres ``governance_counterfactual_result:*`` row insert +
a separate ``review:governance-counterfactuals:{corpus_id}`` projection
insert).

**Non-blocking failure routing discipline (doc-14:242-243 inherited
verbatim).** The writer mirrors the Slice 14 2nd sub-slice + Slice 15
2nd + 4th sub-slice + Slice 16 2nd + 3rd-A + 3rd-B + 4th sub-slice +
Slice 17 2nd + 3rd + 4th + 5th + 6th sub-slice + Slice 18 2nd + 3rd +
4th + 5th sub-slice non-blocking observer precedents: structural
failures during persistence project onto the typed
:class:`CounterfactualResultPersistenceGap` shape with the typed
failure id :data:`COUNTERFACTUAL_RESULT_PERSISTENCE_FAILURE_ID`
registered under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` with
the EXISTING NON-blocking :data:`RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action). The writer NEVER raises a failure to the
caller per the auto-memory ``feedback_no_silent_degradation`` rule
(every failure produces a typed
:class:`CounterfactualResultPersistenceGap` projection embedded in the
typed :class:`CounterfactualResultWriterResult` ``gap_records`` field;
the caller observes the gaps via
:attr:`CounterfactualResultWriter.gap_records` property mirror).

**No consumer activation authority (doc-18:123-125 + doc-18:164 AC3
+ doc-17:159-163).** The counterfactual-result writer is a **pure
projection observer**: a persisted result row does NOT activate the
recommendation's proposed policy artifact; activation remains
consumer-owned per doc-17:159-163. The writer GRANTS NO CONSUMER-SIDE
ACTIVATION AUTHORITY -- it is structurally analogous to the Slice 17
4th sub-slice
:class:`~iriai_build_v2.execution_control.decision_record_writer.DecisionRecordWriter`
which persists decisions but does NOT activate. The Slice 18 7th
sub-slice recommendation citation hook + the Slice 17 6th sub-slice
consumer read APIs together pin the activation boundary
operationally.

**Implementation discipline.** Stdlib (``datetime`` + ``hashlib`` +
``json``) + Pydantic v2 + Slice 13a modules
(``..workflows.develop.governance.models``) + Slice 18 1st sub-slice
(``.counterfactual_replay``) + Slice 18 5th sub-slice
(``.counterfactual_metrics_comparator`` for typed-input REUSE only)
only. NO imports from ``governance/`` outside ``governance.models``.
NO imports from ``workflows/develop/execution/phases/`` /
``supervisor`` / ``dashboard``. NO imports from Slice 18 2nd/3rd/4th
sub-slices (the writer is a pure projection over already-emitted
results; the upstream loader / summary-replay engine / event-replay
engine are producers, not writer dependencies). NO mutation of any
existing ``execution_control/`` module (per the implementer prompt
§ "Non-negotiables").

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.decision_record_writer` (Slice
17 4th sub-slice) +
:mod:`iriai_build_v2.execution_control.governance_finding_writer`
(Slice 16 4th sub-slice) +
:mod:`iriai_build_v2.execution_control.governance_scorecard_writer`
(Slice 15 4th sub-slice): ``BaseModel`` subclasses with
``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed as a typed
``ValidationError`` rather than being silently absorbed. Per the
auto-memory ``feedback_flat_structured_output`` rule the typed control
fields are flat primitives. Per the auto-memory
``feedback_no_silent_degradation`` rule every failure produces a typed
:class:`CounterfactualResultPersistenceGap`. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 15 4th + Slice 16 4th + Slice 17 4th sub-slice precedent
verbatim without introducing new abstractions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from iriai_build_v2.execution_control.counterfactual_metrics_comparator import (
    MetricsComparatorResult,
)
from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


__all__ = [
    # Typed inputs / outputs / gap (mirrors Slice 15 4th + Slice 16 4th
    # + Slice 17 4th sub-slice precedent).
    "CounterfactualResultWriterInputs",
    "CounterfactualResultWriterResult",
    "CounterfactualResultPersistenceGap",
    # The NEW typed failure id under EXISTING evidence_corruption
    # failure_class (REUSES Slice 14 2nd sub-slice
    # retry_governance_projection NON-blocking RouteAction).
    "COUNTERFACTUAL_RESULT_PERSISTENCE_FAILURE_ID",
    # Review projection id prefix (per doc-18:116-117 verbatim).
    "REVIEW_PROJECTION_ID_PREFIX",
    # Bounded review-projection cap (per IMPLEMENTATION_PROMPT_GOVERNANCE.md
    # § "Bounded reads"; mirrors Slice 15 4th + Slice 16 4th + Slice 17 4th
    # value verbatim).
    "DEFAULT_REVIEW_PROJECTION_CAP",
    # Pure helpers.
    "compute_counterfactual_result_projection_id",
    "compute_counterfactual_results_digest",
    # The writer class.
    "CounterfactualResultWriter",
]


# --- Typed failure id (doc-18:116-117 + doc-14:242-243 NON-BLOCKING) --------


COUNTERFACTUAL_RESULT_PERSISTENCE_FAILURE_ID: Literal[
    "counterfactual_result_persistence_failed"
] = "counterfactual_result_persistence_failed"
"""Doc-18:116-117 + doc-14:242-243 -- the typed failure id the
counterfactual-result writer projects onto when a persistence step
fails structurally.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18
2nd + 3rd + 4th + 5th sub-slice precedent verbatim).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 18 6th pattern matches the Slice 14 + Slice 15 + Slice 16 +
Slice 17 + Slice 18 2nd-5th sub-slice non-blocking governance projection
observer (the counterfactual-result writer is also a post-checkpoint
governance projection observer + per doc-18:123 replay results are
review/governance artifacts only -- never runtime policy authority).

The Slice 14 + Slice 15 2nd / 4th + Slice 16 2nd / 3rd-A / 3rd-B / 4th
+ Slice 17 2nd / 3rd / 4th / 5th / 6th + Slice 18 2nd / 3rd / 4th / 5th
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
* :mod:`iriai_build_v2.execution_control.governance_finding_writer`
  (Slice 16 4th) defines ``governance_finding_persistence_failed``.
* :mod:`iriai_build_v2.execution_control.recommendation_builder`
  (Slice 17 2nd) defines ``recommendation_builder_emission_failed``.
* :mod:`iriai_build_v2.execution_control.policy_validation_interface`
  (Slice 17 3rd) defines ``policy_validation_failed``.
* :mod:`iriai_build_v2.execution_control.decision_record_writer`
  (Slice 17 4th) defines ``decision_record_persistence_failed``.
* :mod:`iriai_build_v2.execution_control.replay_requirement_hook`
  (Slice 17 5th) defines ``replay_requirement_validation_failed``.
* :mod:`iriai_build_v2.execution_control.consumer_read_api`
  (Slice 17 6th) defines ``consumer_read_api_failed``.
* :mod:`iriai_build_v2.execution_control.counterfactual_replay_loader`
  (Slice 18 2nd) defines ``replay_corpus_or_scenario_load_failed``.
* :mod:`iriai_build_v2.execution_control.counterfactual_summary_replay`
  (Slice 18 3rd) defines ``summary_replay_failed``.
* :mod:`iriai_build_v2.execution_control.counterfactual_event_replay`
  (Slice 18 4th) defines ``event_replay_failed``.
* :mod:`iriai_build_v2.execution_control.counterfactual_metrics_comparator`
  (Slice 18 5th) defines ``metrics_comparator_failed``.

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to counterfactual-
result persistence failures (this slice is also a post-checkpoint
governance projection observer + per doc-18:123 replay results are
review/governance artifacts only).
"""


# --- Review projection id prefix (doc-18:116-117 verbatim) ------------------


REVIEW_PROJECTION_ID_PREFIX: Literal["review:governance-counterfactuals:"] = (
    "review:governance-counterfactuals:"
)
"""Doc-18:116-117 -- the typed prefix for the bounded review projection
id for governance counterfactual result rows.

Per doc-18:116-117 *"Emit counterfactual results as typed governance
rows and review artifacts."* the projection id is constructed by
concatenating this prefix with the
:attr:`CounterfactualResultWriterInputs.corpus_id` field. The
:func:`compute_counterfactual_result_projection_id` helper performs
the concatenation.

The doc-18:48-50 compatible-deviations rule allows additional review
projection id schemes, but the v1 contract mandates this prefix +
``corpus_id`` suffix verbatim so the doc-18:127-129 *"Historical
replay is immutable by corpus id and scenario id."* backward-
compatibility contract holds.

This is INTENTIONALLY DIFFERENT from:

* :data:`~iriai_build_v2.execution_control.governance_scorecard_writer.REVIEW_PROJECTION_ID_PREFIX`
  value ``review:governance-metrics:`` (Slice 15 4th sub-slice emits
  scorecard projections; metrics-keyed).
* :data:`~iriai_build_v2.execution_control.governance_finding_writer.REVIEW_PROJECTION_ID_PREFIX`
  value ``review:governance-findings:`` (Slice 16 4th sub-slice emits
  findings projections; findings-keyed).
* :data:`~iriai_build_v2.execution_control.decision_record_writer.REVIEW_PROJECTION_ID_PREFIX`
  value ``review:governance-recommendations:`` (Slice 17 4th sub-slice
  emits decision-record projections; recommendation-decision-keyed).

All share the ``review:`` artifact-key root + the bounded-read +
refs-only + non-blocking failure-routing discipline.

The Slice 18 6th sub-slice emits counterfactual-result projections;
counterfactual-result-keyed (the typed projection ``results`` list
carries the per-result typed counterfactual rows).
"""


# --- Bounded review-projection cap (IMPLEMENTATION_PROMPT_GOVERNANCE.md §
#     "Bounded reads"; mirrors Slice 15 4th + Slice 16 4th + Slice 17 4th value
#     verbatim) ---------------------------------------------------------------


DEFAULT_REVIEW_PROJECTION_CAP: int = 200
"""IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" -- the default
LIMIT cap for the bounded review projection's results +
comparator_results + per-result policy_provenance_refs + baseline_refs
lists.

Per *"Reuse the typed snapshot's ``LIMIT cap+1`` truncation discipline
and the supervisor's ``SET LOCAL statement_timeout`` pattern. No
artifact-body hydration on the governance read path."* the projection
emits up to ``cap+1`` items so the caller can detect overflow (the
``+1`` is the sentinel that signals "the cap was reached + there may be
more"). The default cap is 200 (mirrors the Slice 15 4th sub-slice
:data:`~iriai_build_v2.execution_control.governance_scorecard_writer.DEFAULT_REVIEW_PROJECTION_CAP`
+ Slice 16 4th sub-slice
:data:`~iriai_build_v2.execution_control.governance_finding_writer.DEFAULT_REVIEW_PROJECTION_CAP`
+ Slice 17 4th sub-slice
:data:`~iriai_build_v2.execution_control.decision_record_writer.DEFAULT_REVIEW_PROJECTION_CAP`
values verbatim), comfortably above the v1 doc-18:98-107 8-scenario
contract; future per-corpus caps may tighten via the
:meth:`CounterfactualResultWriter.write_review_projection` ``cap``
parameter.

The cap applies independently to:

* The results list (default: emit at most ``cap+1`` of
  :attr:`CounterfactualResultWriterInputs.results`).
* The comparator_results list (default: emit at most ``cap+1`` of
  :attr:`CounterfactualResultWriterInputs.comparator_results`).
* The baseline_refs list (default: emit at most ``cap+1`` of
  :attr:`CounterfactualResultWriterInputs.baseline_refs`).
* Each result's policy_provenance_refs list (default: emit at most
  ``cap+1`` per :attr:`CounterfactualResult.policy_provenance_refs`).
"""


def compute_counterfactual_result_projection_id(corpus_id: str) -> str:
    """Construct the typed review-projection id per doc-18:116-117.

    Returns ``review:governance-counterfactuals:{corpus_id}`` -- the
    typed prefix concatenated with the
    :attr:`CounterfactualResultWriterInputs.corpus_id` field.

    Per doc-18:116-117 the review projection id is the typed key the
    governance review artifact store uses to read the bounded review
    projection; the caller is responsible for the actual insert
    operation (the writer is a pure projection per the doc-18:123-125
    *"Replay results are review/governance artifacts only."* discipline).

    The construction is intentionally a pure-string concatenation (NOT
    a typed BaseModel) so the projection id is trivial to serialise +
    consume across process boundaries.

    Mirrors :func:`~iriai_build_v2.execution_control.governance_scorecard_writer.compute_review_projection_id`
    (Slice 15 4th sub-slice) +
    :func:`~iriai_build_v2.execution_control.governance_finding_writer.compute_review_projection_id`
    (Slice 16 4th sub-slice) +
    :func:`~iriai_build_v2.execution_control.decision_record_writer.compute_decision_projection_id`
    (Slice 17 4th sub-slice) verbatim.
    """

    return f"{REVIEW_PROJECTION_ID_PREFIX}{corpus_id}"


# --- Canonical-JSON + SHA-256 digest helpers (mirrors Slice 18 1st-5th
#     sub-slice + Slice 17 4th sub-slice
#     compute_decisions_digest canonical-JSON discipline) -------------------


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Mirrors :func:`iriai_build_v2.execution_control.decision_record_writer._canonical_json`
    + :func:`iriai_build_v2.execution_control.counterfactual_metrics_comparator._canonical_json`
    + :func:`iriai_build_v2.execution_control.counterfactual_replay._canonical_json`
    + :func:`iriai_build_v2.execution_control.governance_finding_writer._canonical_json`
    + :func:`iriai_build_v2.execution_control.governance_scorecard_writer._canonical_json`
    verbatim: ``json.dumps(..., sort_keys=True, separators=(",", ":"))``.

    Per the P3-15-1-1 + P3-16-1-1 + P3-17-1-2 lineage the
    ``default=str`` superset is benign because the canonical
    projections this module computes go through
    :meth:`BaseModel.model_dump` with ``mode='json'`` first, so
    ``datetime`` is already lowered to ISO-8601 strings before this
    helper sees the object; the ``default=str`` is a defence-in-depth
    fallback for any non-JSON-native scalars (e.g. ``Path`` objects in
    test fixtures).
    """

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_hex(payload: str) -> str:
    """Return the SHA-256 hex digest of ``payload`` (UTF-8 encoded).

    Mirrors :func:`iriai_build_v2.execution_control.decision_record_writer._sha256_hex`
    verbatim. Stdlib-only per the implementer prompt § "Non-negotiables"
    (``hashlib`` + ``json`` only).
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_counterfactual_results_digest(
    results: list[CounterfactualResult],
) -> str:
    """Compute the deterministic SHA-256-derived digest for a list of
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
    records.

    Per doc-18:116-117 *"Emit counterfactual results as typed governance
    rows and review artifacts."* the digest is the cross-process
    tamper-detection anchor subsequent re-projections rely on: two
    re-runs against the same result-set MUST produce byte-identical
    digests; a digest change signals a re-projection (either new
    results, modified results, or a new result_version per doc-18:128-129).

    The digest is computed over the canonical-JSON projection of the
    results list; each result's typed shape (via
    :meth:`BaseModel.model_dump` with ``mode='json'``) preserves all 16
    doc-18:79-96 fields including the per-result ``result_id`` +
    ``result_version`` + ``scenario_id`` + ``corpus_id`` +
    ``assumptions`` + ``validity_limits`` +
    ``policy_provenance_refs`` (typed-ref-shape only; NEVER raw
    bodies) + ``safety_guard_class`` + ``estimated_delta_*`` +
    ``estimated_risk_change`` + ``confidence`` + ``invalidated_by`` +
    ``supporting_finding_ids`` + ``recommended_next_step``.

    The results list is NOT sorted before digesting -- the caller's
    upstream result-emission flow is responsible for deterministic
    ordering (per the Slice 17 4th sub-slice
    :func:`~iriai_build_v2.execution_control.decision_record_writer.compute_decisions_digest`
    + Slice 16 4th sub-slice
    :func:`~iriai_build_v2.execution_control.governance_finding_writer.compute_findings_digest`
    + Slice 15 1st sub-slice
    :func:`~iriai_build_v2.execution_control.governance_metrics.compute_scorecard_digest`
    precedent). Subsequent Slice 18 7th sub-slice recommendation
    citation hook that requires order-invariance MAY pre-sort the
    results list before passing to the writer.

    Mirrors the Slice 17 4th sub-slice
    :func:`~iriai_build_v2.execution_control.decision_record_writer.compute_decisions_digest`
    + Slice 16 4th sub-slice
    :func:`~iriai_build_v2.execution_control.governance_finding_writer.compute_findings_digest`
    + Slice 15 1st sub-slice
    :func:`~iriai_build_v2.execution_control.governance_metrics.compute_scorecard_digest`
    canonical-JSON + SHA-256 discipline verbatim.

    :param results: the list of typed
        :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
        records to digest.
    :returns: the SHA-256 hex digest (64-char string) of the
        canonical-JSON projection of the results list.
    """

    canonical_list: list[dict[str, Any]] = [
        result.model_dump(mode="json") for result in results
    ]
    payload: dict[str, Any] = {"results": canonical_list}
    return _sha256_hex(_canonical_json(payload))


# --- Typed inputs (mirrors Slice 15 4th + Slice 16 4th + Slice 17 4th
#     sub-slice precedent) ----------------------------------------------------


class CounterfactualResultWriterInputs(BaseModel):
    """Doc-18:116-117 step 6 + doc-18:79-96 -- typed bundle of all
    inputs the counterfactual-result writer consumes.

    The bundle composes:

    * ``corpus_id`` -- the corpus identifier the result rows group
      against (e.g. ``"8ac124d6"`` for the calibration fixture per
      doc-18:167-168 AC5; future feature ids for production results).
    * ``results`` -- the list of
      :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
      records the writer composes into the typed persistence shape.
      This is the typed output of the upstream replay-emission flow
      (e.g. the Slice 18 3rd sub-slice summary-replay engine + Slice
      18 4th sub-slice event-replay engine producing the typed
      counterfactual result rows); the writer's caller is responsible
      for the upstream emission.
    * ``comparator_results`` -- the optional list of
      :class:`~iriai_build_v2.execution_control.counterfactual_metrics_comparator.MetricsComparatorResult`
      records the results reference (per doc-18:115 the comparator
      emits the per-axis confidence + delta records the writer carries
      alongside the typed result rows as supporting evidence).
      Optional; defaults to empty list when callers persist results
      WITHOUT the typed comparator context (e.g. when only summary-
      replay results are emitted without a comparator pass).
    * ``baseline_refs`` -- the optional list of Slice 13a shared
      :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
      baseline references the result-set grounds on (per
      doc-18:186-249 + doc-13a:18-23). Optional; defaults to empty
      list.
    * ``warnings`` -- the list of warning-reason strings (mirrors the
      Slice 15 4th + Slice 16 4th + Slice 17 4th sub-slice
      ``warnings`` shape).
    * ``incomplete_scopes`` -- the list of incomplete-scope descriptors
      (mirrors the Slice 15 4th + Slice 16 4th + Slice 17 4th
      sub-slice ``incomplete_scopes`` shape; free-form dicts so the
      writer surface can carry rich incomplete-scope shapes).

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-18:186-249).** :attr:`baseline_refs` is a list of Slice 13a
    shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    (imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`; NOT
    redefined here). Per doc-13a:285-287 step 9 the shared model is
    the authority for governance evidence-ref semantics.

    Mirrors :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriterInputs`
    (Slice 15 4th sub-slice) +
    :class:`~iriai_build_v2.execution_control.governance_finding_writer.FindingWriterInputs`
    (Slice 16 4th sub-slice) +
    :class:`~iriai_build_v2.execution_control.decision_record_writer.DecisionWriterInputs`
    (Slice 17 4th sub-slice) verbatim per the chunk-shape decision.
    """

    # ``extra="forbid"`` aligns with the Slice 17 4th sub-slice precedent
    # at src/iriai_build_v2/execution_control/decision_record_writer.py:616
    # + the Slice 16 4th sub-slice precedent at
    # src/iriai_build_v2/execution_control/governance_finding_writer.py:531
    # + the Slice 15 4th sub-slice precedent at
    # src/iriai_build_v2/execution_control/governance_scorecard_writer.py:342
    # -- unknown fields fail closed as a typed ``ValidationError`` rather
    # than being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    corpus_id: str
    """The corpus identifier the result rows group against (e.g.
    ``"8ac124d6"`` for the calibration fixture per doc-18:167-168 AC5;
    future feature ids for production results)."""

    results: list[CounterfactualResult]
    """Doc-18:116-117 step 6 + doc-18:79-96 -- the list of
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
    records the writer composes into the typed persistence shape.

    Per doc-18:79-96 each result carries the 16 typed fields
    (``result_id`` + ``result_version`` + ``scenario_id`` +
    ``corpus_id`` + ``assumptions`` + ``validity_limits`` +
    ``policy_provenance_refs`` + ``safety_guard_class`` +
    ``estimated_delta_*`` + ``estimated_risk_change`` + ``confidence``
    + ``invalidated_by`` + ``supporting_finding_ids`` +
    ``recommended_next_step``); the typed shape itself enforces all
    invariants at construction so typo-d / out-of-range values fail
    closed with a typed ``ValidationError`` per the auto-memory
    ``feedback_no_silent_degradation`` rule.

    The writer preserves all 16 doc-18:79-96 fields verbatim (no
    mutation; the typed shape itself enforces the invariants)."""

    comparator_results: list[MetricsComparatorResult] = Field(
        default_factory=list
    )
    """Doc-18:115 + doc-18:116-117 -- optional list of
    :class:`~iriai_build_v2.execution_control.counterfactual_metrics_comparator.MetricsComparatorResult`
    records the results reference (the typed comparator surface is
    the audit-trail evidence carried alongside).

    Defaults to empty list -- callers MAY persist results WITHOUT
    the typed comparator context (e.g. when only summary-replay
    results are emitted without a comparator pass). When non-empty,
    the writer projects the comparator results onto the review
    projection via ``model_dump(mode='json')`` (refs-only; NEVER raw
    bodies)."""

    baseline_refs: list[GovernanceEvidenceRef] = Field(default_factory=list)
    """Doc-18:186-249 + doc-13a:18-23 -- the list of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    baseline references the result-set grounds on.

    Per doc-18:186-249 + doc-13a:285-287 step 9 the baseline refs cite
    typed evidence-set refs (NOT raw artifact bodies); the
    :class:`GovernanceEvidenceRef` typed surface enforces this
    no-raw-body-hydration discipline at construction."""

    warnings: list[str] = Field(default_factory=list)
    """Free-form warning-reason strings naming non-blocking issues
    encountered during result-set persistence (e.g.
    ``"stale_comparator_reference"`` /
    ``"missing_baseline_for_result"`` /
    ``"policy_provenance_count_exceeds_cap"``). Mirrors the Slice 15
    4th + Slice 16 4th + Slice 17 4th sub-slice
    :attr:`~iriai_build_v2.execution_control.decision_record_writer.DecisionWriterInputs.warnings`
    shape."""

    incomplete_scopes: list[dict[str, Any]] = Field(default_factory=list)
    """The list of incomplete-scope descriptors. Mirrors the Slice 15
    4th + Slice 16 4th + Slice 17 4th sub-slice
    :attr:`~iriai_build_v2.execution_control.decision_record_writer.DecisionWriterInputs.incomplete_scopes`
    shape; free-form dicts so callers can carry rich incomplete-scope
    shapes."""


# --- Typed gap shape (mirrors Slice 15 4th + Slice 16 4th + Slice 17 4th
#     sub-slice precedent) ----------------------------------------------------


class CounterfactualResultPersistenceGap(BaseModel):
    """Typed governance-gap shape produced when the counterfactual-
    result writer fails to persist a result-set or review projection
    structurally.

    Mirrors the Slice 17 4th sub-slice
    :class:`~iriai_build_v2.execution_control.decision_record_writer.DecisionPersistenceGap`
    + Slice 16 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_finding_writer.FindingPersistenceGap`
    + Slice 15 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardPersistenceGap`
    + Slice 14
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    shapes verbatim per the chunk-shape decision.

    Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED here
    per doc-18:116-117 + doc-18:123-125 governance-projection
    discipline) the gap is NON-blocking: the caller MUST NOT propagate
    it to the executor / checkpoint / merge-queue / resume code paths.
    The corresponding typed failure id
    :data:`COUNTERFACTUAL_RESULT_PERSISTENCE_FAILURE_ID`
    (``counterfactual_result_persistence_failed``) registers under the
    EXISTING ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-18:186-249).** :attr:`evidence_refs` is a list of Slice 13a
    shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    (imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`; NOT
    redefined here).
    """

    # ``extra="forbid"`` aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["counterfactual_result_persistence_failed"]
    """Doc-18:116-117 + doc-18:123-125 + doc-14:192-201 -- the typed
    failure id. Registers under the EXISTING ``evidence_corruption``
    failure_class with NON-blocking routing per doc-14:242-243 +
    doc-18:116-117 + doc-18:123-125."""

    corpus_id: str
    """The corpus scope of the failed persistence (same as the
    :attr:`CounterfactualResultWriterInputs.corpus_id`)."""

    results_digest: str | None
    """The results digest (per
    :func:`compute_counterfactual_results_digest`) computed at the
    time of failure, or ``None`` if the failure happened before the
    digest could be computed.

    The digest is the cross-process tamper-detection anchor per
    doc-18:127-129; preserving it in the gap lets downstream consumers
    correlate the gap with the historical result-set whose persistence
    failed (per doc-18:170-173 rollback discipline: historical results
    remain as review artifacts even when newer re-projections fail)."""

    review_projection_id: str | None
    """The bounded review projection id (per
    :func:`compute_counterfactual_result_projection_id`) the failure
    targeted, or ``None`` if the failure happened before the
    projection id could be computed."""

    reason: str
    """Free-form gap reason (e.g.
    ``results_count_exceeds_cap``,
    ``review_projection_digest_mismatch``,
    ``results_construction_failed``,
    ``results_digest_failed``)."""

    observed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    """Doc-18:116-117 + doc-13:201-204 -- the typed observation
    timestamp for the gap. Defaults to UTC now; projects to ISO-8601
    string under :meth:`BaseModel.model_dump` with ``mode='json'``."""

    evidence_refs: list[GovernanceEvidenceRef] = Field(default_factory=list)
    """Optional list of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    evidence references the gap cites.

    Defaults to empty list -- per doc-18:116-117 the gap is advisory;
    it does NOT mandate evidence refs on every gap (the gap is itself
    derived from the typed
    :class:`CounterfactualResultWriterInputs` surface which carries
    its own typed evidence chain)."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap (e.g. the
    result count involved, the projection cap that was exceeded, the
    error detail). Free-form per the doc-14:192-201 + doc-18:116-117
    governance-gap contract."""


# --- Typed writer result (mirrors Slice 17 4th sub-slice
#     DecisionWriterResult three-field result shape) -------------------------


class CounterfactualResultWriterResult(BaseModel):
    """Doc-18:116-117 step 6 -- typed per-call counterfactual-result
    writer result.

    A :class:`CounterfactualResultWriterResult` is the typed audit
    record the :meth:`CounterfactualResultWriter.write_results` method
    emits per call. The shape carries:

    * ``persisted_result_ids`` -- the typed list of result_id strings
      successfully persisted (preserves the per-result
      :attr:`CounterfactualResult.result_id` verbatim; the typed
      surface enforces all doc-18:79-96 invariants at construction).
    * ``gap_records`` -- the typed list of
      :class:`CounterfactualResultPersistenceGap` records emitted when
      a structural failure occurs during persistence (NEVER raises
      per the non-blocking observer contract; mirrors the Slice 17
      4th sub-slice
      :attr:`~iriai_build_v2.execution_control.decision_record_writer.DecisionWriterResult.gap_records`
      pattern).
    * ``truncated`` -- typed boolean flag for whether the results
      list was truncated at the LIMIT cap+1 bounded-read discipline
      (per IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads").
      Defaults to ``False`` (no truncation).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    Per doc-18:116-117 *"Emit counterfactual results as typed
    governance rows and review artifacts."* the result's
    ``persisted_result_ids`` field is the typed surface the (future)
    Slice 18 7th sub-slice recommendation citation hook uses to
    correlate the persisted result rows with the Slice 17 1st
    sub-slice
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs`
    list; per doc-18:164 AC3 *"Replay cannot mutate live workflow
    state."* the persisted result does NOT activate the
    recommendation -- activation remains consumer-owned per
    doc-17:159-163.
    """

    model_config = ConfigDict(extra="forbid")

    persisted_result_ids: list[str] = Field(default_factory=list)
    """Doc-18:80 + doc-18:116-117 -- the list of result_id strings
    successfully persisted. Each entry is the
    :attr:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult.result_id`
    of a persisted result row; the typed list ordering preserves the
    input ordering of :attr:`CounterfactualResultWriterInputs.results`."""

    gap_records: list[CounterfactualResultPersistenceGap] = Field(
        default_factory=list
    )
    """Doc-18:116-117 + doc-14:242-243 -- the typed list of
    :class:`CounterfactualResultPersistenceGap` records emitted when
    a structural failure occurs during persistence.

    Per doc-14:242-243 the writer is non-blocking (NEVER raises a
    failure to the caller); every structural failure projects onto a
    typed gap record accumulated in this list. Empty when no failures
    occurred. Mirrors the Slice 17 4th sub-slice
    :attr:`~iriai_build_v2.execution_control.decision_record_writer.DecisionWriterResult.gap_records`
    pattern."""

    truncated: bool = False
    """IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" -- typed
    boolean flag for whether the results list was truncated at the
    LIMIT cap+1 bounded-read discipline.

    Defaults to ``False`` (no truncation). Set to ``True`` when the
    input results list exceeded the
    :data:`DEFAULT_REVIEW_PROJECTION_CAP` (or the per-call ``cap``
    override) -- the caller can detect overflow by checking this
    flag."""


# --- The counterfactual-result writer (mirrors Slice 17 4th sub-slice
#     DecisionRecordWriter precedent) -----------------------------------------


class CounterfactualResultWriter:
    """Counterfactual-result writer + bounded review projection emitter
    (doc-18:116-117 step 6 + doc-18:79-96).

    Per *"Emit counterfactual results as typed governance rows and
    review artifacts."* (doc-18:116-117) the writer consumes the Slice
    18 1st sub-slice typed-shape foundation
    (:class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`)
    + the optional Slice 18 5th sub-slice typed
    :class:`~iriai_build_v2.execution_control.counterfactual_metrics_comparator.MetricsComparatorResult`
    + the Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    and projects them onto:

    1. A typed :class:`CounterfactualResultWriterResult` carrying the
       ``persisted_result_ids`` list + the ``gap_records`` list +
       the ``truncated`` flag (the typed persistence shape; mirrors
       the Slice 17 4th sub-slice
       :class:`~iriai_build_v2.execution_control.decision_record_writer.DecisionWriterResult`
       shape).
    2. A bounded review projection dict (the typed review-artifact
       shape keyed by
       :func:`compute_counterfactual_result_projection_id`).

    **Bounded-read discipline (IMPLEMENTATION_PROMPT_GOVERNANCE.md §
    "Bounded reads" + doc-18:186-249).** The review projection's
    results list + per-result policy_provenance_refs list +
    comparator_results list + baseline_refs list all use the
    ``LIMIT cap+1`` truncation discipline: the writer emits up to
    ``cap+1`` items so the caller can detect overflow (per the typed
    snapshot pattern). The default cap is
    :data:`DEFAULT_REVIEW_PROJECTION_CAP`.

    **Refs-only discipline (doc-18:186-249).** The review projection
    carries ONLY typed control fields + cited refs
    (:class:`GovernanceEvidenceRef` typed shape; the
    :class:`MetricsComparatorResult` typed shape -- all via
    ``model_dump(mode='json')``) -- NEVER raw artifact bodies. The
    :class:`GovernanceEvidenceRef` typed surface itself enforces the
    no-raw-body discipline at construction.

    **Digest discipline (doc-18:127-129).** The review projection
    carries the results digest (per
    :func:`compute_counterfactual_results_digest`) for tamper
    detection: two re-runs against the same result-set MUST produce
    byte-identical digests.

    **Rollback discipline (doc-18:170-173).** The writer's typed-shape
    outputs are immutable rows: future re-projections emit NEW
    :class:`CounterfactualResultWriterInputs` bundles (with NEW
    per-result ``result_version`` references) rather than mutating
    historical results. The :meth:`write_results` surface is a pure
    projection; the actual storage mechanism (e.g. a Postgres
    ``governance_counterfactual_result:*`` row insert) is the
    caller's responsibility.

    **Non-blocking discipline.** The writer NEVER raises a failure to
    the caller. Any structural failure projects onto a typed
    :class:`CounterfactualResultPersistenceGap` accumulated on the
    typed :attr:`CounterfactualResultWriterResult.gap_records` field
    (mirrored on the :attr:`CounterfactualResultWriter.gap_records`
    property post-call).

    **No consumer activation authority (doc-18:123-125 + doc-18:164
    AC3 + doc-17:159-163).** The writer is a **pure projection
    observer**: a persisted result row does NOT activate the
    recommendation's proposed policy artifact; activation remains
    consumer-owned per doc-17:159-163. The writer GRANTS NO CONSUMER-
    SIDE ACTIVATION AUTHORITY.

    Per the auto-memory ``feedback_no_overengineer_use_library`` rule
    the writer mirrors the Slice 15 4th + Slice 16 4th + Slice 17
    4th sub-slice precedents (single class with one or more public
    projection methods) without introducing new abstractions.

    Example usage::

        from iriai_build_v2.execution_control.counterfactual_result_writer \\
            import CounterfactualResultWriter, CounterfactualResultWriterInputs

        writer = CounterfactualResultWriter()
        result = writer.write_results(
            CounterfactualResultWriterInputs(
                corpus_id="8ac124d6",
                results=[<typed CounterfactualResult records>],
                comparator_results=[<typed MetricsComparatorResult records>],
                baseline_refs=[],
                warnings=[],
                incomplete_scopes=[],
            )
        )
        projection = writer.write_review_projection(
            results=[<...>], corpus_id="8ac124d6"
        )
        # caller persists result + projection.
    """

    def __init__(self) -> None:
        """Construct a counterfactual-result writer.

        The writer is stateless aside from the :attr:`gap_records`
        accumulator the public :meth:`write_results` +
        :meth:`write_review_projection` surfaces populate. Each call
        to a public method RESETS the accumulator (so per-call gap
        records remain bounded; callers that need cross-call gap
        accumulation should snapshot the property after each call).

        Mirrors the Slice 17 4th sub-slice
        :class:`~iriai_build_v2.execution_control.decision_record_writer.DecisionRecordWriter`
        + Slice 16 4th sub-slice
        :class:`~iriai_build_v2.execution_control.governance_finding_writer.GovernanceFindingWriter`
        + Slice 15 4th sub-slice
        :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter`
        constructor verbatim.
        """

        self._gap_records: list[CounterfactualResultPersistenceGap] = []

    @property
    def gap_records(self) -> list[CounterfactualResultPersistenceGap]:
        """The list of :class:`CounterfactualResultPersistenceGap`
        records the most-recent public-method call produced.

        Per the Slice 14
        :class:`~iriai_build_v2.execution_control.commit_provenance_writer.GitProvenanceWriter`
        + Slice 15 4th sub-slice
        :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter`
        + Slice 16 4th sub-slice
        :class:`~iriai_build_v2.execution_control.governance_finding_writer.GovernanceFindingWriter`
        + Slice 17 4th sub-slice
        :class:`~iriai_build_v2.execution_control.decision_record_writer.DecisionRecordWriter`
        precedents the writer NEVER raises a failure to the caller --
        every structural failure projects onto a typed gap record.

        Returns a fresh snapshot list each call so caller mutations do
        not leak into the internal accumulator.
        """

        return list(self._gap_records)

    def write_results(
        self,
        inputs: CounterfactualResultWriterInputs,
        *,
        cap: int | None = None,
    ) -> CounterfactualResultWriterResult:
        """Project the typed :class:`CounterfactualResultWriterInputs`
        bundle into a typed :class:`CounterfactualResultWriterResult`
        (doc-18:116-117 step 6 + doc-18:79-96).

        Returns the typed :class:`CounterfactualResultWriterResult`
        with the ``persisted_result_ids`` list (preserves the per-
        result ``result_id`` verbatim; the typed surface enforces all
        doc-18:79-96 invariants at construction; the writer does NOT
        mutate upstream result rows) + the ``gap_records`` list
        (typed :class:`CounterfactualResultPersistenceGap` rows
        projected on persistence failure per the non-blocking observer
        contract) + the ``truncated`` flag (typed boolean signalling
        whether the results list was truncated at the LIMIT cap+1
        bounded-read discipline).

        Per doc-14:242-243 + doc-18:116-117 + doc-18:123-125 NEVER
        raises a failure to the caller. Any structural failure
        projects onto a typed
        :class:`CounterfactualResultPersistenceGap` accumulated in the
        result's ``gap_records`` field; the persisted_result_ids in
        that case is the most-conservative projection (empty list) so
        the downstream Slice 18 7th sub-slice recommendation citation
        hook can block on the corpus.

        The method RESETS the :attr:`gap_records` accumulator at
        entry; per-call gap records remain bounded.

        Per IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" the
        results list is truncated at ``cap+1`` (the writer emits up to
        ``cap+1`` items so the caller can detect overflow). The
        default cap is :data:`DEFAULT_REVIEW_PROJECTION_CAP`.

        Per doc-18:123-125 + doc-18:164 AC3 + doc-17:159-163 the
        writer GRANTS NO CONSUMER-SIDE ACTIVATION AUTHORITY -- a
        persisted result row does NOT activate the recommendation's
        proposed policy artifact; activation remains consumer-owned
        per doc-17:159-163.

        Mirrors :meth:`~iriai_build_v2.execution_control.decision_record_writer.DecisionRecordWriter.write_decisions`
        (Slice 17 4th sub-slice) +
        :meth:`~iriai_build_v2.execution_control.governance_finding_writer.GovernanceFindingWriter.write_findings`
        (Slice 16 4th sub-slice) +
        :meth:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter.write_scorecard`
        (Slice 15 4th sub-slice) verbatim.

        :param inputs: the typed
            :class:`CounterfactualResultWriterInputs` bundle.
        :param cap: optional override for the LIMIT cap+1 truncation
            threshold; defaults to
            :data:`DEFAULT_REVIEW_PROJECTION_CAP`.
        :returns: the typed :class:`CounterfactualResultWriterResult`
            (NEVER raises; failures project onto the result's
            ``gap_records`` field).
        """

        # Reset per-call accumulator (mirrors Slice 15 4th + Slice 16 4th
        # + Slice 17 4th sub-slice writer pattern).
        self._gap_records = []

        effective_cap = cap if cap is not None else DEFAULT_REVIEW_PROJECTION_CAP

        # ── LIMIT cap+1 truncation of the results list per
        #    IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" ────────────
        # Emit at most cap+1 items so the caller can detect overflow.
        results_limit = effective_cap + 1
        truncated_results = inputs.results[:results_limit]
        results_truncated = len(inputs.results) > effective_cap

        # The projection is a pure pass-through: each
        # CounterfactualResult's typed shape is already validated at
        # construction by the upstream emission flow. Defensive
        # try/except wraps the list construction so any structural
        # failure projects onto a typed gap rather than being raised
        # to the caller (non-blocking observer contract per
        # doc-14:242-243 + doc-18:116-117 + doc-18:123-125).
        try:
            persisted_ids: list[str] = [
                result.result_id for result in truncated_results
            ]
        except Exception as exc:
            # Defensive: list comprehension should never fail given the
            # typed inputs above, but the writer is non-blocking per
            # the doc-14:242-243 contract. Project onto a typed gap +
            # return an empty result so the downstream Slice 18 7th
            # sub-slice recommendation citation hook can block on the
            # corpus.
            self._gap_records.append(
                CounterfactualResultPersistenceGap(
                    failure_id=COUNTERFACTUAL_RESULT_PERSISTENCE_FAILURE_ID,
                    corpus_id=inputs.corpus_id,
                    results_digest=None,
                    review_projection_id=compute_counterfactual_result_projection_id(
                        inputs.corpus_id
                    ),
                    reason=f"results_construction_failed: {type(exc).__name__}",
                    evidence_payload={"error_detail": str(exc)[:500]},
                )
            )
            persisted_ids = []

        return CounterfactualResultWriterResult(
            persisted_result_ids=persisted_ids,
            gap_records=list(self._gap_records),
            truncated=results_truncated,
        )

    def write_review_projection(
        self,
        *,
        results: list[CounterfactualResult],
        corpus_id: str,
        comparator_results: list[MetricsComparatorResult] | None = None,
        baseline_refs: list[GovernanceEvidenceRef] | None = None,
        warnings: list[str] | None = None,
        incomplete_scopes: list[dict[str, Any]] | None = None,
        cap: int | None = None,
    ) -> dict[str, Any]:
        """Build the bounded review projection dict for a list of
        :class:`CounterfactualResult` records at the typed
        :data:`REVIEW_PROJECTION_ID_PREFIX` key prefix per
        doc-18:116-117.

        The projection's structure:

        * ``review_projection_id`` -- the typed key per
          :func:`compute_counterfactual_result_projection_id`.
        * ``corpus_id`` -- the corpus identifier.
        * ``generated_at`` -- ISO-8601 string projection of
          ``datetime.now(timezone.utc)`` (the cross-process freshness
          anchor per the Slice 15 4th + Slice 16 4th + Slice 17 4th
          sub-slice precedent verbatim).
        * ``results_digest`` -- the SHA-256 digest from
          :func:`compute_counterfactual_results_digest` for tamper
          detection per doc-18:127-129.
        * ``results`` -- the result values projected as compact
          dicts carrying ONLY typed control fields + cited refs
          (NEVER raw bodies), truncated at ``cap+1`` per the
          bounded-read discipline.
        * ``comparator_results`` -- the comparator values projected
          via ``model_dump(mode='json')`` (refs-only; NEVER raw
          bodies), truncated at ``cap+1``.
        * ``baseline_refs`` -- the baseline refs projected via
          ``model_dump(mode='json')`` (typed-ref shape; NEVER raw
          bodies), truncated at ``cap+1``.
        * ``incomplete_scopes`` -- carried verbatim from the
          ``incomplete_scopes`` parameter.
        * ``warnings`` -- carried verbatim from the ``warnings``
          parameter.
        * ``results_truncated`` / ``comparator_results_truncated`` /
          ``baseline_refs_truncated`` -- bool flags signalling whether
          the cap was reached (so the caller can detect overflow per
          the LIMIT cap+1 discipline).

        Per IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" the
        projection EMITS at most ``cap+1`` items in each list (so the
        caller can detect overflow by checking
        ``len(projection["results"]) > cap``).

        Per doc-18:186-249 the projection EMITS ONLY REFS (NOT raw
        bodies). The
        :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
        typed surface itself enforces the no-raw-body discipline.

        Per doc-18:127-129 the projection CARRIES the results digest
        (for tamper detection) + the per-result result_id (so
        subsequent consumers can correlate the persisted result rows
        with the historical replay corpora).

        Per doc-14:242-243 + doc-18:116-117 + doc-18:123-125 NEVER
        raises a failure to the caller. Any structural failure
        projects onto a typed
        :class:`CounterfactualResultPersistenceGap` accumulated on
        :attr:`gap_records`; the returned projection in that case
        still carries the typed shape (with the most-conservative
        projection) so the downstream Slice 18 7th sub-slice
        recommendation citation hook can block on the corpus.

        Mirrors :meth:`~iriai_build_v2.execution_control.decision_record_writer.DecisionRecordWriter.write_review_projection`
        (Slice 17 4th sub-slice) +
        :meth:`~iriai_build_v2.execution_control.governance_finding_writer.GovernanceFindingWriter.write_review_projection`
        (Slice 16 4th sub-slice) +
        :meth:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter.write_review_projection`
        (Slice 15 4th sub-slice) verbatim per the chunk-shape
        decision.

        :param results: the typed
            ``list[CounterfactualResult]`` to project.
        :param corpus_id: the corpus identifier the projection groups
            against (keyword-only).
        :param comparator_results: the typed
            ``list[MetricsComparatorResult]`` the results reference
            (defaults to an empty list).
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
        # + Slice 17 4th sub-slice writer pattern).
        self._gap_records = []

        # Coerce optional parameter defaults (never mutates caller lists).
        effective_comparator_results: list[MetricsComparatorResult] = (
            list(comparator_results) if comparator_results is not None else []
        )
        effective_baseline_refs: list[GovernanceEvidenceRef] = (
            list(baseline_refs) if baseline_refs is not None else []
        )
        effective_warnings: list[str] = (
            list(warnings) if warnings is not None else []
        )
        effective_incomplete_scopes: list[dict[str, Any]] = (
            [dict(scope) for scope in incomplete_scopes]
            if incomplete_scopes is not None
            else []
        )

        effective_cap = cap if cap is not None else DEFAULT_REVIEW_PROJECTION_CAP

        # Compute the digest defensively: if
        # compute_counterfactual_results_digest fails (should never
        # happen given the typed shape but the writer is non-blocking)
        # project onto a typed gap record.
        try:
            digest = compute_counterfactual_results_digest(results)
        except Exception as exc:
            self._gap_records.append(
                CounterfactualResultPersistenceGap(
                    failure_id=COUNTERFACTUAL_RESULT_PERSISTENCE_FAILURE_ID,
                    corpus_id=corpus_id,
                    results_digest=None,
                    review_projection_id=compute_counterfactual_result_projection_id(
                        corpus_id
                    ),
                    reason=f"results_digest_failed: {type(exc).__name__}",
                    evidence_payload={"error_detail": str(exc)[:500]},
                )
            )
            digest = ""

        # ── LIMIT cap+1 truncation of the results list per
        #    IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" ───────────
        # Emit at most cap+1 items so the caller can detect overflow.
        results_limit = effective_cap + 1
        truncated_results = results[:results_limit]
        results_truncated = len(results) > effective_cap

        # ── LIMIT cap+1 truncation of the comparator_results list ───────
        comparator_results_limit = effective_cap + 1
        truncated_comparator_results = effective_comparator_results[
            :comparator_results_limit
        ]
        comparator_results_truncated = (
            len(effective_comparator_results) > effective_cap
        )

        # ── LIMIT cap+1 truncation of the baseline_refs list ────────────
        baseline_refs_limit = effective_cap + 1
        truncated_baseline_refs = effective_baseline_refs[:baseline_refs_limit]
        baseline_refs_truncated = (
            len(effective_baseline_refs) > effective_cap
        )

        # Project the result values to bounded review-projection dicts.
        # The projection carries ONLY typed control fields + cited
        # refs (NEVER raw bodies). The policy_provenance_refs list
        # per result is also truncated at cap+1 per the bounded-read
        # discipline.
        projected_results: list[dict[str, Any]] = []
        for result in truncated_results:
            policy_provenance_refs_limit = effective_cap + 1
            result_provenance_refs = result.policy_provenance_refs[
                :policy_provenance_refs_limit
            ]
            result_provenance_refs_truncated = (
                len(result.policy_provenance_refs) > effective_cap
            )
            projected_results.append(
                {
                    # Doc-18:80-96 typed control fields (15 of 16
                    # fields; policy_provenance_refs is projected
                    # separately to apply per-result cap+1 truncation).
                    "result_id": result.result_id,
                    "result_version": result.result_version,
                    "scenario_id": result.scenario_id,
                    "corpus_id": result.corpus_id,
                    "assumptions": list(result.assumptions),
                    "validity_limits": list(result.validity_limits),
                    "safety_guard_class": result.safety_guard_class,
                    "estimated_delta_hours": result.estimated_delta_hours,
                    "estimated_delta_repair_cycles": (
                        result.estimated_delta_repair_cycles
                    ),
                    "estimated_delta_commit_failures": (
                        result.estimated_delta_commit_failures
                    ),
                    "estimated_risk_change": result.estimated_risk_change,
                    "confidence": result.confidence,
                    "invalidated_by": list(result.invalidated_by),
                    "supporting_finding_ids": list(
                        result.supporting_finding_ids
                    ),
                    "recommended_next_step": result.recommended_next_step,
                    # policy_provenance_refs use mode='json' to project
                    # to cross-process stable dict shapes per the Slice
                    # 13a GovernanceEvidenceRef typed contract; this
                    # preserves the typed ref shape (authority +
                    # ref_id + digest + quality + completeness)
                    # WITHOUT inserting any raw body field. The
                    # mode='json' projection lowers datetime to
                    # ISO-8601 string per the Pydantic v2
                    # canonical-projection contract.
                    "policy_provenance_refs": [
                        ref.model_dump(mode="json")
                        for ref in result_provenance_refs
                    ],
                    "policy_provenance_refs_truncated": (
                        result_provenance_refs_truncated
                    ),
                }
            )

        # Project the comparator_results to typed-shape dicts per
        # doc-18:115 + doc-18:116-117 (NEVER raw bodies). Mode='json'
        # projection per the Slice 18 5th sub-slice
        # MetricsComparatorResult contract.
        projected_comparator_results = [
            comp.model_dump(mode="json")
            for comp in truncated_comparator_results
        ]

        # Project the baseline_refs to cited refs per doc-18:186-249 +
        # doc-13a:18-23 (NEVER raw bodies). Mode='json' projection per
        # the Slice 13a GovernanceEvidenceRef contract.
        projected_baseline_refs = [
            ref.model_dump(mode="json") for ref in truncated_baseline_refs
        ]

        # The full review projection dict.
        review_projection_id = compute_counterfactual_result_projection_id(
            corpus_id
        )
        # generated_at projects to ISO-8601 string per the
        # doc-13:201-204 canonical-JSON discipline (mirrors Slice 15
        # 4th + Slice 16 4th + Slice 17 4th sub-slice generated_at
        # projection pattern verbatim).
        generated_at_iso = datetime.now(timezone.utc).isoformat()
        projection: dict[str, Any] = {
            "review_projection_id": review_projection_id,
            "corpus_id": corpus_id,
            "generated_at": generated_at_iso,
            "results_digest": digest,
            "results": projected_results,
            "results_truncated": results_truncated,
            "comparator_results": projected_comparator_results,
            "comparator_results_truncated": comparator_results_truncated,
            "baseline_refs": projected_baseline_refs,
            "baseline_refs_truncated": baseline_refs_truncated,
            "incomplete_scopes": effective_incomplete_scopes,
            "warnings": effective_warnings,
        }
        return projection
