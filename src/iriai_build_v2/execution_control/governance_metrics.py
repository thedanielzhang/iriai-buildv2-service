"""Slice 15 first sub-slice -- foundational governance metrics + scoring typed-shape module.

This module owns the 3 doc-15 "Proposed Interfaces And Types" typed shapes
(``docs/execution-control-plane/15-governance-metrics-and-scoring.md:64-97``):

* :class:`GovernanceMetricDefinition` -- the metric definition shape:
  name + version + scope_kind + numerator + denominator +
  required_evidence_kinds + active_work_policy + confidence_rule
  (doc-15:68-76; 9 fields).
* :class:`GovernanceMetricValue` -- the computed metric-value shape:
  definition_name + definition_version + scope + value + unit +
  confidence + data_quality + source_mix + evidence_refs + exclusions
  (doc-15:78-88; 10 fields).
* :class:`GovernanceScorecard` -- the corpus-level scorecard container:
  corpus_id + generated_at + metrics + baseline_refs + incomplete_scopes +
  warnings (doc-15:90-97; 6 fields).

Plus the :data:`MetricScopeKind` 8-value Literal (doc-15:66) and the
:data:`REQUIRED_V1_METRIC_NAMES` 15-name tuple (doc-15:99-115). The 15-name
tuple is the typed contract subsequent metric-extractor sub-slices ground
metric extraction on.

It is the **cross-cutting typed foundation** that subsequent Slice 15
sub-slices (the metric extractor + confidence scoring + scorecard
persistence per doc-15 § Refactoring Steps steps 2-7) build on; this
first sub-slice does NOT yet wire these typed shapes into any
executor / checkpoint / merge-queue / governance-projection consumer --
that wiring lands in subsequent sub-slices per doc-15:117-136.

Per the governance prompt § "Non-Negotiables" the typed shapes here are
analytical / advisory / read-only -- they do NOT mutate executor /
control-plane / product state, take merge or checkpoint authority, or
force policy activation. Governance metrics are derived rows (per
doc-15:140) and never change execution state.

**Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
doc-15:201-264 Slice 13A Shared Completeness Model Dependency).** The
:class:`GovernanceMetricValue.data_quality` field is the Slice 13a shared
:data:`EvidenceQuality` Literal (imported from
:mod:`iriai_build_v2.workflows.develop.governance.models`). The
:class:`GovernanceMetricValue.evidence_refs` + :class:`GovernanceScorecard.baseline_refs`
fields are lists of Slice 13a :class:`GovernanceEvidenceRef` (imported
from the same module). NONE of these types is redefined here -- per
doc-13a:285-287 step 9 ("Update governance Slices 13-20 and context
Slice 21 to depend on this shared completeness model instead of
redefining authority semantics locally") this module consumes the
shared models directly.

Per doc-15:201-264 the future metric-extractor sub-slices will additionally
consume the Slice 13A shared :data:`CompletenessState` +
:class:`EvidenceCompleteness` + :class:`ExactEvidenceManifest` +
:class:`AuthoritativeContextRef` typed shapes from
:mod:`iriai_build_v2.execution_control.completeness`. This first
sub-slice does NOT yet pre-empt that wiring (the typed-shape foundation
exposes the surface that future sub-slices consume); the REUSE
discipline is enforced at the test-file level by asserting no local
``CompletenessState`` redefinition.

**Implementation discipline.** Stdlib (``hashlib`` + ``json`` +
``datetime``) + Pydantic v2 + Slice 13a modules
(``..workflows.develop.governance.models``) only. NO imports from
``governance/`` outside ``governance.models`` (this module is foundational;
the governance layer consumes execution-control surfaces, not the
reverse). NO imports from other parts of ``execution_control/`` (this
module is foundational for the future Slice 15 extractor; the existing
Slice 00-14 ``execution_control`` modules are NOT modified). NO imports
from ``workflows/develop/execution/phases/`` / ``supervisor`` /
``dashboard`` (those would be downstream consumers, not dependencies).

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.commit_provenance` (Slice 14
1st sub-slice) + :mod:`iriai_build_v2.execution_control.completeness`
(Slice 13A 2nd sub-slice): ``BaseModel`` subclasses with
``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed as a typed
``ValidationError`` rather than being silently absorbed.

Per the auto-memory ``feedback_flat_structured_output`` rule the typed
control fields are flat primitives (``str`` / ``int`` / ``float`` /
``list`` / ``dict``). Per the auto-memory ``feedback_no_silent_degradation``
rule every Pydantic field validates at construction; unknown values fail
closed via Literal range + ``extra="forbid"`` discipline. Per the
auto-memory ``feedback_no_overengineer_use_library`` rule the module
mirrors the Slice 14 1st sub-slice precedent verbatim without introducing
new abstractions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from iriai_build_v2.workflows.develop.governance.models import (
    EvidenceQuality,
    GovernanceEvidenceRef,
)


__all__ = [
    # Doc-15:66 -- the 8-value scope-kind Literal.
    "MetricScopeKind",
    # Doc-15:68-97 -- the 3 typed Pydantic BaseModels.
    "GovernanceMetricDefinition",
    "GovernanceMetricValue",
    "GovernanceScorecard",
    # Doc-15:99-115 -- the 15-name required-v1-metrics tuple.
    "REQUIRED_V1_METRIC_NAMES",
    # Helpers mirroring Slice 13A's compute_completeness_digest +
    # Slice 14's compute_payload_sha256 canonical-JSON discipline.
    "compute_scorecard_digest",
    "canonical_scorecard_dict",
]


# --- MetricScopeKind 8-value Literal (doc-15:66) ----------------------------


MetricScopeKind = Literal[
    "feature",
    "effective_group",
    "task",
    "lane",
    "repo",
    "runtime",
    "verifier",
    "policy",
]
"""Doc-15:66 -- the 8-value scope-kind Literal for governance metric
definitions.

Each metric definition declares its scope kind so consumers can
disambiguate per-feature throughput vs per-lane throughput vs
per-verifier verification-cost without inferring scope from the metric
name.

The 8 values land verbatim from doc-15:66:

* ``feature`` -- per-feature scope (e.g. per-feature ``tasks_per_hour``).
* ``effective_group`` -- per-effective-group scope (post-regroup-overlay
  grouping per Slice 09).
* ``task`` -- per-task scope (e.g. per-task ``repair_cycles_per_task``).
* ``lane`` -- per-lane scope (per the Slice 09 lane discipline).
* ``repo`` -- per-repo scope (per the Slice 08 multi-repo checkpoint
  discipline).
* ``runtime`` -- per-runtime-provider scope (e.g. ``runtime_failures_per_attempt``
  scoped by provider).
* ``verifier`` -- per-verifier-rule scope (per the Slice 06 verification
  graph).
* ``policy`` -- per-policy-recommendation scope (per the Slice 17 policy
  recommendation interface; future).

Pydantic Literal validation: unknown values fail closed at construction
with a typed ``ValidationError`` per the
``feedback_no_silent_degradation`` rule.
"""


# --- REQUIRED_V1_METRIC_NAMES 15-name tuple (doc-15:99-115) -----------------


REQUIRED_V1_METRIC_NAMES: tuple[str, ...] = (
    # Doc-15:101-103 -- throughput metrics.
    "tasks_per_hour",
    "complexity_adjusted_tasks_per_hour",
    "hours_per_task",
    # Doc-15:104-106 -- repair / verification / commit drag metrics.
    "repair_cycles_per_task",
    "verification_cost_per_task",
    "commit_failures_per_task",
    # Doc-15:107-108 -- context / workspace drag metrics.
    "stale_context_events_per_task",
    "workspace_unblocks_per_task",
    # Doc-15:109 -- runtime/provider drag metrics.
    "runtime_failures_per_attempt",
    # Doc-15:110-112 -- queue / checkpoint / workflow-drag metrics.
    "merge_queue_wait_hours",
    "checkpoint_duration_hours",
    "workflow_drag_hours",
    # Doc-15:113-115 -- governance-confidence metrics.
    "operator_required_escalations",
    "plan_deviation_count",
    "resolved_p1_p2_review_findings",
)
"""Doc-15:99-115 -- the 15-name tuple of required v1 metric names.

This tuple is the typed contract subsequent Slice 15 sub-slices ground
metric extraction on. Each name corresponds to a
:class:`GovernanceMetricDefinition` produced by the (future) metric
extractor; the tuple's 15-name set is the minimum surface a v1 scorecard
must cover.

The 15 names span 5 categories:

1. **Throughput** (3): ``tasks_per_hour``, ``complexity_adjusted_tasks_per_hour``,
   ``hours_per_task`` (doc-15:101-103).
2. **Repair / verification / commit drag** (3): ``repair_cycles_per_task``,
   ``verification_cost_per_task``, ``commit_failures_per_task`` (doc-15:104-106).
3. **Context / workspace drag** (2): ``stale_context_events_per_task``,
   ``workspace_unblocks_per_task`` (doc-15:107-108).
4. **Runtime drag** (1): ``runtime_failures_per_attempt`` (doc-15:109).
5. **Queue / checkpoint / workflow drag** (3): ``merge_queue_wait_hours``,
   ``checkpoint_duration_hours``, ``workflow_drag_hours`` (doc-15:110-112).
6. **Governance confidence** (3): ``operator_required_escalations``,
   ``plan_deviation_count``, ``resolved_p1_p2_review_findings``
   (doc-15:113-115).

Per doc-15:144-145 metric version bumps add a new
:class:`GovernanceMetricDefinition` entry under the same ``name`` with a
different ``version`` rather than mutating this tuple in-place; the
tuple's name set is the cross-version v1 contract.
"""


# --- The 3 doc-15:64-97 typed shapes ----------------------------------------


class GovernanceMetricDefinition(BaseModel):
    """Doc-15:68-76 -- the metric-definition shape.

    Each definition pins the typed contract one metric grounds on:
    name + version + scope_kind + numerator + denominator +
    required_evidence_kinds + active_work_policy + confidence_rule.

    Per doc-15:144-145 scorecards must include metric definition versions
    so later changes do not silently rewrite historical meaning; future
    metric version bumps add a parallel
    :class:`GovernanceMetricDefinition` entry under the same ``name`` with
    a different ``version`` rather than mutating the existing entry
    in-place.

    The 9 fields land verbatim from doc-15:68-76.
    """

    # extra='forbid' aligns with the Slice 13A precedent at
    # src/iriai_build_v2/execution_control/completeness.py:204 + the
    # Slice 14 precedent at
    # src/iriai_build_v2/execution_control/commit_provenance.py:178 +
    # the sibling governance model precedent at
    # src/iriai_build_v2/workflows/develop/governance/models.py:548 --
    # unknown fields fail closed as a typed ``ValidationError`` rather
    # than being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    name: str
    """Doc-15:69 -- the metric's stable name. One of
    :data:`REQUIRED_V1_METRIC_NAMES` for v1 metrics; arbitrary
    project-specific names allowed for non-v1 metric definitions."""

    version: str
    """Doc-15:70 -- the metric definition's version string. Per
    doc-15:144-145 scorecards must include metric definition versions so
    later changes do not silently rewrite historical meaning; metric
    version bumps add a parallel
    :class:`GovernanceMetricDefinition` entry under the same ``name`` with
    a different ``version`` rather than mutating the existing entry
    in-place."""

    scope_kind: MetricScopeKind
    """Doc-15:71 -- the metric's scope kind (one of the 8
    :data:`MetricScopeKind` Literal values). Per doc-15:66 the scope kind
    is one of: ``feature`` / ``effective_group`` / ``task`` / ``lane`` /
    ``repo`` / ``runtime`` / ``verifier`` / ``policy``."""

    numerator: str
    """Doc-15:72 -- the metric's numerator description. Free-form string
    spelling out what the numerator counts (e.g. "completed tasks" /
    "verification minutes" / "repair cycles"). Subsequent metric-extractor
    sub-slices may tighten to a typed Literal once the extractor surface
    crystallises."""

    denominator: str
    """Doc-15:73 -- the metric's denominator description. Free-form string
    spelling out what the denominator counts (e.g. "elapsed hours" /
    "completed tasks" / "dispatched attempts"). Subsequent metric-extractor
    sub-slices may tighten to a typed Literal once the extractor surface
    crystallises."""

    required_evidence_kinds: list[str]
    """Doc-15:74 -- the list of evidence kinds the metric requires to
    compute. Per doc-15:124-126 step 2 the metric extractor consumes
    Slice 13 evidence sets (NOT raw broad artifact scans); the kinds
    listed here are the typed-evidence kinds the extractor must find in
    the corpus before emitting a metric value. Free-form strings; future
    sub-slices may tighten to a typed Literal (e.g.
    :data:`~iriai_build_v2.workflows.develop.governance.models.EvidenceAuthority`)
    once the extractor surface crystallises."""

    active_work_policy: Literal["exclude", "status_only", "separate"]
    """Doc-15:75 -- the metric's active-work handling policy. Per
    doc-15:127-129 step 3 completed-throughput averages MUST exclude
    active work; status views MAY include active work separately. The
    3-value Literal enforces the doc-15:127-129 policy taxonomy:

    * ``exclude`` -- the metric MUST exclude active work entirely
      (completed-throughput averages; doc-15:128).
    * ``status_only`` -- the metric MAY include active work but must
      flag it as status-only (doc-15:129).
    * ``separate`` -- the metric reports active work as a separate
      bucket (e.g. via :attr:`GovernanceScorecard.incomplete_scopes`).

    Pydantic Literal validation: unknown values fail closed at
    construction with a typed ``ValidationError``."""

    confidence_rule: str
    """Doc-15:76 -- the metric's confidence-rule description. Free-form
    string spelling out the confidence-scoring rule per doc-15:131-132
    step 5 (evidence completeness + sample count + freshness +
    typed-vs-legacy source mix + implementation-log completeness).
    Subsequent metric-extractor sub-slices may tighten to a typed
    expression once the confidence-scoring surface crystallises."""


class GovernanceMetricValue(BaseModel):
    """Doc-15:78-88 -- the computed metric-value shape.

    Each value cites its source metric definition (by name + version),
    the scope it computed against, the value + unit + confidence, the
    Slice 13a-shared :data:`EvidenceQuality` data-quality tag, the
    source-mix dict (typed-vs-legacy), the list of Slice 13a-shared
    :class:`GovernanceEvidenceRef` evidence references the value grounds
    on, and the list of exclusion strings (active-work / preview-only /
    insufficient-sample exclusions per doc-15:148-151).

    Per doc-15:148-151 + doc-15:160-163 the metric-value shape supports
    the doc-15 edge cases:

    * **Insufficient samples** (doc-15:149-150): the value MAY be None +
      the confidence MAY be conservative; policy recommendations that
      require the metric MUST be blocked.
    * **Mixed legacy and typed evidence** (doc-15:151-153): the
      ``data_quality`` is ``"derived"`` + the ``source_mix`` dict carries
      the typed/legacy counts; the confidence is lowered when typed
      evidence is incomplete.
    * **Provider outage** (doc-15:154): counted as runtime/provider
      failure (e.g. ``runtime_failures_per_attempt``), NOT product
      repair.
    * **Overlapping failures** (doc-15:155-156): allocated to one
      primary class plus secondary contributing classes via
      ``exclusions`` to avoid double-counting.
    * **Incomplete implementation journal** (doc-15:157-158):
      ``plan_deviation_count`` + governance-confidence metrics are
      INSUFFICIENT until the journal gap is resolved.

    The 10 fields land verbatim from doc-15:78-88.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-15:201-264).** The :attr:`data_quality` field is the Slice 13a
    shared :data:`EvidenceQuality` Literal -- imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`, NOT
    redefined here. The :attr:`evidence_refs` field is the list of
    Slice 13a shared :class:`GovernanceEvidenceRef` -- imported from the
    same module, NOT redefined here. Per doc-13a:285-287 step 9 the
    shared models are the authority for evidence-quality + evidence-ref
    semantics; future Slice 15 metric-extractor sub-slices populate these
    fields using the Slice 13a typed shapes directly.
    """

    # extra='forbid' aligns with the sibling shapes above.
    model_config = ConfigDict(extra="forbid")

    definition_name: str
    """Doc-15:79 -- the source metric definition's :attr:`GovernanceMetricDefinition.name`."""

    definition_version: str
    """Doc-15:80 -- the source metric definition's :attr:`GovernanceMetricDefinition.version`.
    Per doc-15:144-145 scorecards must include metric definition versions
    so later changes do not silently rewrite historical meaning."""

    scope: dict[str, str]
    """Doc-15:81 -- the metric's scope as a typed-key dict (e.g.
    ``{"feature_id": "8ac124d6"}`` / ``{"lane_id": "ml-7"}`` /
    ``{"runtime_provider": "anthropic"}``). Free-form key/value strings
    so the metric-extractor surface can carry rich scope dimensions
    without a frozen schema."""

    value: float | int | None
    """Doc-15:82 -- the metric's computed value. ``None`` when the
    metric is insufficient (per doc-15:149-150 the insufficient-sample
    case emits a metric with ``value=None`` + conservative
    :attr:`confidence`; policy recommendations that require the metric
    MUST be blocked)."""

    unit: str
    """Doc-15:83 -- the metric's unit string (e.g. ``"tasks/hour"`` /
    ``"hours"`` / ``"count"`` / ``"ratio"``). Free-form so the
    metric-extractor surface can carry rich units without a frozen
    schema."""

    confidence: float
    """Doc-15:84 -- the metric's confidence score in [0.0, 1.0]. Per
    doc-15:131-132 step 5 the confidence-scoring rule grounds on
    evidence completeness + sample count + freshness + typed-vs-legacy
    source mix + implementation-log completeness. Per doc-15:149-150 the
    insufficient-sample case emits a conservative confidence (e.g. 0.0)
    so policy recommendations that require the metric MUST be blocked."""

    data_quality: EvidenceQuality
    """Doc-15:85 -- the Slice 13a shared
    :data:`~iriai_build_v2.workflows.develop.governance.models.EvidenceQuality`
    Literal (6 values: ``canonical`` / ``derived`` / ``sampled`` /
    ``advisory`` / ``stale`` / ``insufficient``).

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-15:201-264).** This field is the Slice 13a shared
    :data:`EvidenceQuality` Literal -- imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`, NOT
    redefined here. Per doc-13a:285-287 step 9 the shared model is the
    authority for evidence-quality semantics; future Slice 15
    metric-extractor sub-slices populate this field using the Slice 13a
    typed Literal directly.

    Per doc-15:151-153 mixed legacy and typed evidence sets
    ``data_quality="derived"`` plus a populated :attr:`source_mix` dict
    metadata; the confidence is lowered when typed evidence is
    incomplete."""

    source_mix: dict[str, int] = Field(default_factory=dict)
    """Doc-15:86 -- the per-source-kind count map. Per doc-15:152-153
    the source-mix dict carries typed-vs-legacy counts when the metric
    aggregates mixed evidence (e.g. ``{"typed": 12, "legacy": 3}``); the
    confidence is lowered when typed evidence is incomplete.

    Free-form string keys so the metric-extractor surface can carry
    additional source dimensions (e.g. per-authority counts per the
    Slice 13a
    :data:`~iriai_build_v2.workflows.develop.governance.models.EvidenceAuthority`
    9-value vocabulary). Default empty dict so non-derived metrics
    (e.g. typed-canonical evidence) can omit the field."""

    evidence_refs: list[GovernanceEvidenceRef]
    """Doc-15:87 -- the list of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    evidence references the metric grounds on.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-15:201-264).** This field is the list of Slice 13a shared
    :class:`GovernanceEvidenceRef` -- imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`, NOT
    redefined here. Per doc-13a:285-287 step 9 the shared model is the
    authority for governance evidence-ref semantics; future Slice 15
    metric-extractor sub-slices populate this list using the Slice 13a
    typed shape directly.

    Per doc-15:141-142 metrics cite evidence-set refs and
    implementation-log anchors, NOT raw artifact bodies; the
    :class:`GovernanceEvidenceRef` typed surface enforces this
    no-raw-body-hydration discipline at construction."""

    exclusions: list[str]
    """Doc-15:88 -- the list of exclusion-reason strings. Per
    doc-15:155-156 overlapping failures are allocated to one primary
    class plus secondary contributing classes via this list to avoid
    double-counting; future metric-extractor sub-slices enforce a
    controlled vocabulary (e.g. ``"active_work_excluded"`` /
    ``"preview_only_evidence_excluded"`` /
    ``"insufficient_sample_excluded"``)."""


class GovernanceScorecard(BaseModel):
    """Doc-15:90-97 -- the corpus-level scorecard container.

    Each scorecard groups a list of :class:`GovernanceMetricValue` values
    computed against the same corpus (typically one feature / one wave /
    one calibration fixture per doc-15:135-136 step 7). Scorecards are
    derived rows (per doc-15:140) and never change execution state.

    Per doc-15:144-145 scorecards include metric definition versions
    (carried inside each :class:`GovernanceMetricValue`) so later
    changes do not silently rewrite historical meaning; scorecard
    storage (doc-15:133-134 step 6) preserves historical scorecards as
    bounded review projections (e.g.
    ``review:governance-metrics:{corpus_id}``).

    The 6 fields land verbatim from doc-15:90-97.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-15:201-264).** The :attr:`baseline_refs` field is the list of
    Slice 13a shared :class:`GovernanceEvidenceRef` -- imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`, NOT
    redefined here. Per doc-13a:285-287 step 9 the shared model is the
    authority for governance evidence-ref semantics.
    """

    # extra='forbid' aligns with the sibling shapes above.
    model_config = ConfigDict(extra="forbid")

    corpus_id: str
    """Doc-15:91 -- the corpus identifier the scorecard groups against
    (e.g. ``"8ac124d6"`` for the calibration fixture per doc-15:135-136
    step 7; future feature ids for production scorecards). Free-form
    string so the scorecard surface can carry rich corpus dimensions."""

    generated_at: datetime
    """Doc-15:92 -- the scorecard generation timestamp. Per doc-15:131-132
    step 5 the confidence-scoring rule grounds on freshness; the
    generation timestamp is the cross-process freshness anchor."""

    metrics: list[GovernanceMetricValue]
    """Doc-15:93 -- the list of :class:`GovernanceMetricValue` values
    the scorecard reports. Per doc-15:99-115 the v1 scorecard must
    cover the 15 :data:`REQUIRED_V1_METRIC_NAMES`; the list MAY include
    additional metrics per the doc-15:50-54 compatible-deviations
    rule."""

    baseline_refs: list[GovernanceEvidenceRef]
    """Doc-15:94 -- the list of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    baseline references the scorecard grounds on.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-15:201-264).** This field is the list of Slice 13a shared
    :class:`GovernanceEvidenceRef` -- imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`, NOT
    redefined here.

    Per doc-15:141-142 scorecards cite evidence-set refs and
    implementation-log anchors, NOT raw artifact bodies; the
    :class:`GovernanceEvidenceRef` typed surface enforces this
    no-raw-body-hydration discipline at construction."""

    incomplete_scopes: list[dict[str, Any]]
    """Doc-15:95 -- the list of incomplete-scope descriptors. Per
    doc-15:148-150 + doc-15:163-164 the insufficient-implementation-journal
    case emits a scorecard with :attr:`incomplete_scopes` populated so
    consumers can see which scopes lacked sufficient evidence; future
    metric-extractor sub-slices may tighten to a typed shape (e.g.
    ``IncompleteScopeDescriptor``) once the extractor surface
    crystallises."""

    warnings: list[str]
    """Doc-15:96 -- the list of warning-reason strings. Free-form
    strings naming non-blocking issues (e.g. ``"legacy_heavy_corpus"``
    / ``"stale_baseline"`` / ``"missing_typed_evidence_for_scope"``);
    future metric-extractor sub-slices may tighten to a typed Literal
    once the extractor surface crystallises."""


# --- Scorecard digest helpers (mirrors Slice 13A compute_completeness_digest
#     + Slice 14 compute_payload_sha256 canonical-JSON discipline) ------------


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Mirrors :func:`iriai_build_v2.execution_control.completeness._canonical_json`
    + :func:`iriai_build_v2.execution_control.commit_provenance._canonical_json`
    + :func:`iriai_build_v2.workflows.develop.governance.evidence_set._canonical_json`
    (doc-13:201-204) verbatim: ``json.dumps(..., sort_keys=True,
    separators=(",", ":"))`` -- the canonical form mandates lexicographic
    key ordering and the compact separator set so the resulting bytes
    are stable across Python versions / platforms / dict ordering.
    """

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_hex(payload: str) -> str:
    """Return the SHA-256 hex digest of ``payload`` (UTF-8 encoded).

    Mirrors :func:`iriai_build_v2.execution_control.completeness._sha256_hex`
    + :func:`iriai_build_v2.execution_control.commit_provenance._sha256_hex`
    verbatim. Stdlib-only per the implementer prompt § "Non-negotiables"
    (``hashlib`` + ``json`` only).
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_scorecard_dict(scorecard: GovernanceScorecard) -> dict[str, Any]:
    """Project a :class:`GovernanceScorecard` to its canonical-JSON dict
    representation.

    This helper produces the canonical-dict projection used by
    :func:`compute_scorecard_digest` to compute the deterministic
    SHA-256 digest of a scorecard.

    The projection uses :meth:`BaseModel.model_dump` with ``mode='json'``
    so the ``datetime`` field projects to its ISO-8601 string form
    (cross-process stable). The resulting dict is the input to
    :func:`compute_scorecard_digest`; both helpers use
    :func:`_canonical_json` for deterministic serialisation.
    """

    return scorecard.model_dump(mode="json")


def compute_scorecard_digest(scorecard: GovernanceScorecard) -> str:
    """Compute the deterministic SHA-256 hex digest for a
    :class:`GovernanceScorecard`.

    The digest is computed over the canonical-JSON projection of the
    scorecard. This helper mirrors the Slice 13A
    :func:`~iriai_build_v2.execution_control.completeness.compute_completeness_digest`
    + Slice 14
    :func:`~iriai_build_v2.execution_control.commit_provenance.compute_payload_sha256`
    canonical-JSON + SHA-256 discipline verbatim.

    **Determinism contract.** Two calls with the same logical scorecard
    (regardless of dict-key insertion order on either side of a
    serialisation roundtrip) MUST produce byte-identical hex digests.
    This is the cross-process freshness contract subsequent Slice 15
    scorecard-persistence sub-slices rely on when consumers compare
    scorecard digests to detect changes.

    **List-order sensitivity.** Per the doc-13a:165 + doc-13:201-204
    canonical-JSON discipline the metric / baseline-ref / incomplete-scope /
    warning lists are producer-ordered; the digest IS sensitive to list
    element order (a list re-ordering changes the digest). Producers that
    wish to achieve order-invariance MUST sort their lists canonically
    before calling this helper.

    Mirrors the Slice 13A
    :func:`~iriai_build_v2.execution_control.completeness.compute_completeness_digest`
    + Slice 14
    :func:`~iriai_build_v2.execution_control.commit_provenance.compute_payload_sha256`
    + Slice 13 governance
    :func:`~iriai_build_v2.workflows.develop.governance.evidence_set._sha256_hex`
    helper verbatim.
    """

    return _sha256_hex(_canonical_json(canonical_scorecard_dict(scorecard)))
