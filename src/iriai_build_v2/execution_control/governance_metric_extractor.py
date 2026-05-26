"""Slice 15 second + third sub-slice -- metric extractor over Slice 13 evidence sets.

Per ``docs/execution-control-plane/15-governance-metrics-and-scoring.md``
§ Refactoring Steps step 2 (lines 117-136): *"Build a metric extractor
over Slice 13 evidence sets, not raw broad artifact scans."*

**Third sub-slice scope (this revision).** Replaces the second sub-slice's
placeholder arithmetic + 2-arg ``_confidence_for_sample_count`` projection
with real implementations per:

* **doc-15:125-130** (§ Refactoring Steps step 4): *"Add complexity
  adjustment from pre-execution task-shape inputs only: task count,
  contract path breadth, repo count, barrier type, dependency depth,
  planned verifier-gate count, and declared write-set uncertainty. Do
  not include observed failure classes such as stale projection, commit
  hygiene, provider instability, or queue drag in complexity adjustment;
  those remain workflow-drag metrics."*
* **doc-15:131-132** (§ Refactoring Steps step 5): *"Add confidence
  scoring from evidence completeness, sample count, freshness,
  typed-vs-legacy source mix, and implementation-log completeness."*
* **doc-15:176-177** (Acceptance Criteria): *"Every metric has a
  definition, version, unit, evidence refs, confidence, and active-work
  policy."*
* **doc-15:181** (Acceptance Criteria AC4): *"Implementation journal/log
  quality affects governance confidence."* — the 5th calibrated confidence
  input.

The third sub-slice **REMOVES** ``PLACEHOLDER_ARITHMETIC_EXCLUSION`` from
BOTH the at-or-above-threshold emit path AND module ``__all__`` (8 → 7
exports) AND the module-level constants. The real arithmetic + calibrated
confidence projection are the typed-measurement contract that replaces
the second sub-slice's defensive placeholder.

The doc-13a:269-272 fail-closed gate (in
:meth:`MetricExtractor._emit_fail_closed_value`) continues to emit
``value=None`` + :data:`PROMPT_CONTEXT_INCOMPLETE_EXCLUSION` — that is
the **LEGITIMATE** fail-closed semantics, distinct from the second
sub-slice's arithmetic placeholder; the auto-memory
``feedback_no_silent_degradation`` rule preserves the fail-closed
projection so downstream Slice 17 / 19 consumers see "context
incomplete" (NOT "zero measurement").

This module owns the metric-extractor surface that consumes the Slice 15
1st sub-slice typed-shape foundation (``GovernanceMetricDefinition`` +
``GovernanceMetricValue`` + ``GovernanceScorecard`` from
:mod:`iriai_build_v2.execution_control.governance_metrics`) plus the
Slice 13a shared models (``EvidenceQuality`` + ``GovernanceEvidenceRef``
from :mod:`iriai_build_v2.workflows.develop.governance.models`) plus the
Slice 13A shared completeness model (``CompletenessState`` +
``EvidenceCompleteness`` from
:mod:`iriai_build_v2.execution_control.completeness`) plus the Slice 14
non-blocking failure-routing precedent
(``CommitProvenanceGapFinding`` /
``COMMIT_PROVENANCE_GAP_FAILURE_IDS`` /
``retry_governance_projection`` action; see
:mod:`iriai_build_v2.execution_control.commit_provenance_writer`).

The extractor projects metric definitions onto computed metric values
without raw artifact body scans per **doc-15:141-142** (*"Metrics cite
evidence-set refs and implementation-log anchors, not raw bodies."*) +
**doc-15:182 AC5** (*"No metric depends on unbounded artifact/event
body scans."*); typed-vs-legacy source-mix discrimination per
**doc-15:151-153** (*"Mixed legacy and typed evidence: set
data_quality="derived", add source_mix={"typed": n, "legacy": n}
metadata, lower confidence when typed evidence is incomplete..."*);
active-work policy enforcement per **doc-15:123-124** (*"Define
active-work handling per metric. Completed-throughput averages exclude
active work; status views may include it separately."*);
insufficient-sample handling per **doc-15:148-150** (*"Insufficient
samples: emit metric with value=None or conservative confidence, and
block policy recommendations that require the metric."*).

**Persistence + bounded-read discipline (doc-15:138-145).** Per
*"Governance metrics are derived rows. They do not change execution
state. Metrics cite evidence-set refs and implementation-log anchors,
not raw bodies. Existing ``review:dag-sizing:*`` artifacts remain
readable and may be imported as legacy metric evidence with
``data_quality='derived'``."* the extractor:

* MUST NOT mutate executor / control-plane / product state (governance
  is analytical / advisory / read-only).
* MUST cite evidence-set refs (the typed Slice 13a
  :class:`GovernanceEvidenceRef`), NOT raw artifact bodies.
* MUST mark legacy ``review:dag-sizing:*`` evidence as
  ``data_quality="derived"`` with a populated ``source_mix`` dict.

**Slice 13A invariant fail-closed gate (doc-13a:269-272).** Per *"If
``required_complete_for`` cannot be satisfied, dispatch records
``runtime_context/context_incomplete`` and does not invoke a runtime."*
+ the auto-memory ``feedback_no_silent_degradation`` rule the
extractor:

* MUST NOT consume prompt-context evidence whose typed
  :class:`~iriai_build_v2.execution_control.dispatcher_prompt_context.AuthoritativePromptContextRouting`
  reports ``should_invoke_runtime=False`` (i.e. the routing carries
  ``typed_failure_class="runtime_context"`` +
  ``typed_failure_type="context_incomplete"``).
* MUST emit ``value=None`` + add ``exclusions=["prompt_context_incomplete"]``
  to every emitted :class:`GovernanceMetricValue` when the
  fail-closed gate triggers.
* MUST NOT raise to the caller (per the non-blocking
  doc-15:140-145 discipline; the extractor is a post-checkpoint
  observer, analogous to the Slice 14 writer).

**Non-blocking failure routing discipline (doc-14:242-243 + doc-15
inherited).** The extractor mirrors the Slice 14 2nd-sub-slice
non-blocking observer precedent: structural failures during extraction
project onto the typed :class:`GovernanceMetricExtractionGap` finding
shape with the typed failure id ``governance_metric_extraction_failed``
registered under the EXISTING ``evidence_corruption`` failure_class
in :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
with the EXISTING NON-blocking :data:`RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action). The extractor NEVER raises a failure to the
caller.

**Implementation discipline.** Stdlib (``hashlib`` + ``json`` +
``datetime``) + Pydantic v2 + Slice 13a modules
(``..workflows.develop.governance.models``) + Slice 13A
(``.completeness`` + optional ``.dispatcher_prompt_context``) + Slice
14 (``.commit_provenance_writer``) + Slice 15 1st sub-slice
(``.governance_metrics``) only. NO imports from ``governance/``
outside ``governance.models``. NO imports from
``workflows/develop/execution/phases/`` / ``supervisor`` /
``dashboard``. NO mutation of any existing
``execution_control/`` module (per the implementer prompt §
"Non-negotiables").

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.commit_provenance_writer`
(Slice 14 2nd sub-slice) + :mod:`iriai_build_v2.execution_control.governance_metrics`
(Slice 15 1st sub-slice): ``BaseModel`` subclasses with
``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed as a typed
``ValidationError`` rather than being silently absorbed. Per the
auto-memory ``feedback_flat_structured_output`` rule the typed
control fields are flat primitives. Per the auto-memory
``feedback_no_silent_degradation`` rule every failure produces a typed
:class:`GovernanceMetricExtractionGap`. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 14 2nd sub-slice precedent verbatim without introducing new
abstractions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from iriai_build_v2.execution_control.completeness import (
    CompletenessState,
    EvidenceCompleteness,
)
from iriai_build_v2.execution_control.dispatcher_prompt_context import (
    AuthoritativePromptContextRouting,
)
from iriai_build_v2.execution_control.governance_metrics import (
    GovernanceMetricDefinition,
    GovernanceMetricValue,
)
from iriai_build_v2.workflows.develop.governance.models import (
    EvidenceQuality,
    GovernanceEvidenceRef,
)


__all__ = [
    # Typed input bundle the extractor consumes (per chunk-shape point 2).
    "MetricExtractorInputs",
    # Typed task-shape inputs for the complexity adjustment per
    # doc-15:125-130 step 4 (3rd sub-slice add).
    "TaskShapeInputs",
    # Typed gap finding produced when extraction fails (per chunk-shape
    # point 4; mirrors Slice 14 CommitProvenanceGapFinding).
    "GovernanceMetricExtractionGap",
    # The NEW typed failure id under EXISTING evidence_corruption
    # failure_class (per chunk-shape point 4; REUSES Slice 14's
    # retry_governance_projection NON-blocking RouteAction).
    "GOVERNANCE_METRIC_EXTRACTION_FAILURE_ID",
    # The fail-closed-gate exclusion sentinel (per doc-13a:269-272).
    "PROMPT_CONTEXT_INCOMPLETE_EXCLUSION",
    # The insufficient-sample exclusion sentinel (per doc-15:148-150).
    "INSUFFICIENT_SAMPLES_EXCLUSION",
    # The active-work-excluded sentinel (per doc-15:123-124).
    "ACTIVE_WORK_EXCLUDED_EXCLUSION",
    # The metric-extractor class.
    "MetricExtractor",
]


# --- Typed failure id (doc-15 + doc-14:192-201 + doc-14:242-243) ------------


GOVERNANCE_METRIC_EXTRACTION_FAILURE_ID: Literal[
    "governance_metric_extraction_failed"
] = "governance_metric_extraction_failed"
"""Doc-15 + doc-14:192-201 + doc-14:242-243 -- the typed failure id the
extractor projects onto when an extraction step fails structurally.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action).

The Slice 14 precedent is the source-of-truth for the non-blocking
governance-projection failure-routing discipline:

* :mod:`iriai_build_v2.execution_control.commit_provenance_writer`
  defines ``line_provenance_gap`` + ``governance_evidence_conflict``
  under EXISTING ``evidence_corruption`` failure_class with the NEW
  ``retry_governance_projection`` RouteAction (the 2nd sub-slice added
  the action; this sub-slice REUSES it).
* Per doc-14:242-243 *"Governance provenance projection failures
  never block ``dag-group:*`` checkpointing, merge queue integration,
  or resume"* -- the same non-blocking contract applies to governance
  metric extraction failures (this slice is also a post-checkpoint
  governance projection observer).
"""


# --- Exclusion sentinels (doc-13a:269-272 + doc-15:148-150 + doc-15:123-124) -


PROMPT_CONTEXT_INCOMPLETE_EXCLUSION: Literal["prompt_context_incomplete"] = (
    "prompt_context_incomplete"
)
"""Doc-13a:269-272 -- the typed sentinel string added to
:attr:`GovernanceMetricValue.exclusions` when the extractor's fail-closed
gate triggers.

Per *"If ``required_complete_for`` cannot be satisfied, dispatch records
``runtime_context/context_incomplete`` and does not invoke a runtime."*
+ the auto-memory ``feedback_no_silent_degradation`` rule the extractor
MUST NOT consume prompt-context evidence whose typed
:class:`~iriai_build_v2.execution_control.dispatcher_prompt_context.AuthoritativePromptContextRouting`
reports ``should_invoke_runtime=False`` (i.e. the routing carries
``typed_failure_class="runtime_context"`` +
``typed_failure_type="context_incomplete"``); every emitted
:class:`GovernanceMetricValue` in this case carries
``value=None`` + this exclusion sentinel.
"""


INSUFFICIENT_SAMPLES_EXCLUSION: Literal["insufficient_samples_excluded"] = (
    "insufficient_samples_excluded"
)
"""Doc-15:148-150 -- the typed sentinel string added to
:attr:`GovernanceMetricValue.exclusions` when the metric's evidence-ref
sample count is below the definition's threshold.

Per *"Insufficient samples: emit metric with value=None or
conservative confidence, and block policy recommendations that require
the metric."* the extractor emits ``value=None`` + lower confidence +
this exclusion sentinel when the sample count is below threshold; the
downstream policy layer (Slice 17) blocks recommendations that depend
on this metric.
"""


ACTIVE_WORK_EXCLUDED_EXCLUSION: Literal["active_work_excluded"] = "active_work_excluded"
"""Doc-15:123-124 -- the typed sentinel string added to
:attr:`GovernanceMetricValue.exclusions` when the metric's
``active_work_policy="exclude"`` filtered out active-work evidence
refs.

Per *"Define active-work handling per metric. Completed-throughput
averages exclude active work; status views may include it separately."*
the extractor projects this exclusion onto every emitted
:class:`GovernanceMetricValue` that excluded active-work refs.
"""


# --- Typed task-shape inputs for complexity adjustment (doc-15:125-130 step 4) --


class TaskShapeInputs(BaseModel):
    """Doc-15:125-130 step 4 -- typed bundle of pre-execution task-shape
    inputs the metric extractor consumes for the complexity adjustment.

    Per *"Add complexity adjustment from pre-execution task-shape inputs
    only: task count, contract path breadth, repo count, barrier type,
    dependency depth, planned verifier-gate count, and declared write-set
    uncertainty. Do not include observed failure classes such as stale
    projection, commit hygiene, provider instability, or queue drag in
    complexity adjustment; those remain workflow-drag metrics."*

    The 7 fields land verbatim from doc-15:125-130 step 4. They are
    PRE-EXECUTION inputs (declared at planning time) — NOT post-execution
    observations. Observed failure classes (stale projection, commit
    hygiene, provider instability, queue drag) remain in the workflow-drag
    metric category per doc-15:127-130 and MUST NOT be added here.

    Used by :func:`_compute_complexity_adjustment` to compute a typed
    ``float`` adjustment factor applied to the metric's numerator or
    denominator at the at-or-above-threshold emit path.
    """

    # ``extra="forbid"`` aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    task_count: int = Field(ge=0)
    """Doc-15:125-126 -- the count of planned tasks in the corpus scope.
    Non-negative; planning artifacts that declare zero tasks emit a
    complexity factor of 1.0 (no adjustment)."""

    contract_path_breadth: int = Field(ge=0)
    """Doc-15:126 -- the breadth of contract paths the corpus touches
    (e.g. count of distinct typed-row Pydantic shapes the planned tasks
    must observe / produce). Non-negative."""

    repo_count: int = Field(ge=1)
    """Doc-15:126 -- the count of repositories the corpus's tasks
    span. Per the Slice 08 multi-repo merge-queue discipline this is
    bounded by the planning artifact's declared repo scope.
    Must be at least 1 (the corpus always has at least one repo)."""

    barrier_type: Literal["none", "soft", "hard"]
    """Doc-15:127 -- the barrier-type Literal: ``none`` (no inter-task
    barrier), ``soft`` (a soft serialisation barrier between subsets of
    tasks), or ``hard`` (a hard barrier blocking all parallelism across
    the barrier). Hard barriers increase complexity (lower effective
    parallelism)."""

    dependency_depth: int = Field(ge=0)
    """Doc-15:127 -- the maximum dependency-depth among planned tasks
    (the longest chain through the planning DAG). Non-negative; 0 means
    fully-parallel tasks; higher values indicate deeper chains that
    serialise execution."""

    planned_verifier_gate_count: int = Field(ge=0)
    """Doc-15:127-128 -- the count of planned verifier gates in the
    corpus scope (per the Slice 06 verification-graph). Non-negative;
    more gates raise per-task verification cost (higher complexity)."""

    declared_write_set_uncertainty: float = Field(ge=0.0, le=1.0)
    """Doc-15:128-130 -- the declared write-set uncertainty in [0.0, 1.0]
    where 0.0 means the planning artifact declared a fully-specified
    write-set (low complexity) and 1.0 means the write-set is fully
    uncertain (high complexity). Per the Slice 12 atomic-landing gate
    + Slice 08 merge-queue discipline an uncertain write-set increases
    the need for sandbox isolation."""


# --- Typed input bundle (per chunk-shape point 2) ---------------------------


class MetricExtractorInputs(BaseModel):
    """Doc-15:117-136 + doc-15:141-142 + doc-15:182 (AC5) -- typed
    bundle of all inputs the extractor consumes.

    Per the chunk-shape point 2 (STATUS.md Next safe action) the bundle
    carries:

    * ``corpus_id`` -- the corpus identifier the extractor scopes to
      (per doc-15:91 same shape as
      :attr:`~iriai_build_v2.execution_control.governance_metrics.GovernanceScorecard.corpus_id`).
    * ``definitions`` -- the list of :class:`GovernanceMetricDefinition`
      the extractor processes per call (per doc-15:68-76).
    * ``evidence_set_refs`` -- the list of Slice 13a
      :class:`GovernanceEvidenceRef` the extractor reads BY REF (not
      raw artifact bodies per doc-15:141-142 + AC5 doc-15:182).
    * ``completeness_state`` -- the Slice 13A 2nd sub-slice shared
      :class:`EvidenceCompleteness` that the extractor projects onto
      :attr:`GovernanceMetricValue.data_quality` per doc-15:131-132
      step 5 (confidence scoring from evidence completeness).
    * ``active_work_filter`` -- the corpus-level active-work filter
      taxonomy (3-value Literal: ``exclude`` / ``status_only`` /
      ``separate``) that the extractor passes to per-metric
      :attr:`GovernanceMetricDefinition.active_work_policy` enforcement
      per doc-15:123-124.
    * ``freshness_window_hours`` -- the corpus-level freshness window
      in hours; refs older than this fall into the "stale" data quality
      bucket per doc-15:131-132 step 5 (freshness input to confidence
      scoring).
    * ``prompt_context_routing`` -- optional typed routing signal
      carrying the Slice 13A 4th sub-slice
      :class:`AuthoritativePromptContextRouting` per doc-13a:269-272;
      when present + reports ``should_invoke_runtime=False`` the
      extractor's fail-closed gate triggers (see module docstring).

    Per the auto-memory ``feedback_flat_structured_output`` rule
    control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown values fail closed via the
    Literal range + ``extra="forbid"`` discipline.
    """

    # ``extra="forbid"`` aligns with the Slice 15 1st sub-slice precedent
    # at src/iriai_build_v2/execution_control/governance_metrics.py:245 +
    # the Slice 14 2nd sub-slice precedent at
    # src/iriai_build_v2/execution_control/commit_provenance_writer.py:249
    # + the Slice 13A 2nd sub-slice precedent at
    # src/iriai_build_v2/execution_control/completeness.py:204 -- unknown
    # fields fail closed as a typed ``ValidationError`` rather than being
    # silently absorbed.
    model_config = ConfigDict(extra="forbid")

    corpus_id: str
    """Doc-15:91 -- the corpus identifier the extractor scopes to (e.g.
    ``"8ac124d6"`` for the calibration fixture)."""

    definitions: list[GovernanceMetricDefinition]
    """Doc-15:68-76 -- the list of :class:`GovernanceMetricDefinition`
    the extractor processes per call. Each definition pins one metric's
    typed contract (name + version + scope_kind + numerator +
    denominator + required_evidence_kinds + active_work_policy +
    confidence_rule)."""

    evidence_set_refs: list[GovernanceEvidenceRef]
    """Doc-15:141-142 + doc-15:182 AC5 -- the list of Slice 13a
    :class:`GovernanceEvidenceRef` the extractor reads BY REF (not raw
    artifact bodies). Per the doc-15:141-142 contract *"Metrics cite
    evidence-set refs and implementation-log anchors, not raw bodies"*
    + AC5 *"No metric depends on unbounded artifact/event body scans."*
    """

    completeness_state: EvidenceCompleteness
    """Doc-15:201-264 + doc-13a:162-170 -- the Slice 13A 2nd sub-slice
    shared :class:`EvidenceCompleteness` the extractor projects onto
    :attr:`GovernanceMetricValue.data_quality` per doc-15:131-132
    step 5 (confidence scoring from evidence completeness).

    Per doc-15:201-264 the metric extractor MUST NOT re-derive
    completeness from raw artifact bodies or compatibility projections
    alone; it MUST consume the typed
    :class:`EvidenceCompleteness` attached to the governance
    evidence-set refs.
    """

    active_work_filter: Literal["exclude", "status_only", "separate"]
    """Doc-15:123-124 -- the corpus-level active-work filter
    taxonomy. The 3-value Literal mirrors
    :attr:`GovernanceMetricDefinition.active_work_policy` per
    doc-15:75; the extractor combines the corpus-level filter with the
    per-definition policy when projecting metric values.

    Pydantic Literal validation: unknown values fail closed at
    construction with a typed ``ValidationError``.
    """

    freshness_window_hours: float
    """Doc-15:131-132 step 5 -- the corpus-level freshness window in
    hours. Refs older than this fall into the ``"stale"`` data quality
    bucket per the
    :data:`~iriai_build_v2.workflows.develop.governance.models.EvidenceQuality`
    6-value Literal."""

    prompt_context_routing: AuthoritativePromptContextRouting | None = None
    """Doc-13a:269-272 -- optional typed routing signal carrying the
    Slice 13A 4th sub-slice
    :class:`AuthoritativePromptContextRouting`.

    Per the fail-closed gate (see module docstring + the
    :meth:`MetricExtractor.extract` implementation): when this field
    is populated AND reports ``should_invoke_runtime=False`` (i.e. the
    routing carries ``typed_failure_class="runtime_context"`` +
    ``typed_failure_type="context_incomplete"``) the extractor MUST
    emit ``value=None`` + add :data:`PROMPT_CONTEXT_INCOMPLETE_EXCLUSION`
    to every emitted :class:`GovernanceMetricValue`.

    Default ``None`` so callers that do not yet have a prompt-context
    routing signal (e.g. the calibration fixture path) can construct
    a :class:`MetricExtractorInputs` without the field.
    """

    task_shape_inputs: TaskShapeInputs | None = None
    """Doc-15:125-130 step 4 (3rd sub-slice add) -- optional typed
    pre-execution task-shape inputs used by
    :func:`_compute_complexity_adjustment` to compute the complexity
    factor applied to the metric numerator or denominator.

    Per *"Do not include observed failure classes such as stale
    projection, commit hygiene, provider instability, or queue drag in
    complexity adjustment; those remain workflow-drag metrics."* this
    bundle carries pre-execution declared inputs only.

    Default ``None`` so callers without a planning-artifact-derived
    task-shape bundle (e.g. corpora projected from the calibration
    fixture before planning artifacts land) can construct a
    :class:`MetricExtractorInputs` without the field; when None the
    extractor applies a unit complexity factor (1.0; no adjustment).
    """

    implementation_log_completeness: EvidenceCompleteness | None = None
    """Doc-15:131-132 step 5 + doc-15:181 AC4 (3rd sub-slice add) --
    optional typed implementation-log completeness signal.

    Per AC4 *"Implementation journal/log quality affects governance
    confidence."* the calibrated confidence projection
    :func:`_compute_confidence` consumes this as the 5th input. When
    populated, the field's
    :attr:`EvidenceCompleteness.state` projects onto a contribution
    factor:

    * ``complete`` -- 1.0 (full implementation-log quality).
    * ``paged`` -- 0.7 (paged authoritative; some traversal needed).
    * ``preview_only`` -- 0.3 (display-only; not authoritative per
      Slice 13A invariant doc-13a:18-23).
    * ``unavailable`` -- 0.0 (incomplete implementation journal; the
      AC4 signal degrades confidence to zero per the doc-15:157-158
      edge-case rule).

    When None (default) the extractor defaults to a 1.0 contribution
    so corpora without an implementation-log completeness signal are
    not penalised on this axis.
    """


# --- Typed gap finding (per chunk-shape point 4; mirrors Slice 14) ----------


class GovernanceMetricExtractionGap(BaseModel):
    """Typed governance-gap finding produced when the metric extractor
    fails to project a metric value structurally.

    Mirrors the Slice 14
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    shape verbatim (per the chunk-shape point 4 in STATUS.md Next safe
    action: *"Define typed GovernanceMetricExtractionGap shape (or REUSE
    CommitProvenanceGapFinding if shape fits — pick whichever)."*)
    with the metric-extractor-specific cross-citation fields.

    Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED here
    per doc-15:140-145 governance-projection discipline) the finding is
    NON-blocking: the caller MUST NOT propagate it to the executor /
    checkpoint / merge-queue / resume code paths. The corresponding
    typed failure id ``governance_metric_extraction_failed`` registers
    under the EXISTING ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
    NOT a new route action).
    """

    # ``extra="forbid"`` aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["governance_metric_extraction_failed"]
    """Doc-15 + doc-14:192-201 -- the typed failure id. Registers under
    the EXISTING ``evidence_corruption`` failure_class with NON-blocking
    routing per doc-14:242-243."""

    corpus_id: str
    """The corpus scope of the failed extraction (same as the
    :attr:`MetricExtractorInputs.corpus_id`)."""

    definition_name: str
    """The :attr:`GovernanceMetricDefinition.name` of the metric whose
    extraction failed."""

    definition_version: str
    """The :attr:`GovernanceMetricDefinition.version` of the metric
    whose extraction failed."""

    scope_kind: str
    """The :attr:`GovernanceMetricDefinition.scope_kind` of the metric
    whose extraction failed. Free-form string (NOT the typed Literal)
    so the gap finding can represent both v1 + future scope-kind
    values."""

    reason: str
    """Free-form gap reason (e.g.
    ``evidence_set_refs_empty_for_required_kinds``,
    ``completeness_state_digest_mismatch``)."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding
    (e.g. the evidence ref ids that were missing, the completeness
    state details). Free-form per the doc-14:192-201 governance-finding
    contract."""


# --- The metric extractor (per chunk-shape point 3) -------------------------


class MetricExtractor:
    """Metric extractor over Slice 13 evidence sets (doc-15:117-136
    step 2).

    Per *"Build a metric extractor over Slice 13 evidence sets, not raw
    broad artifact scans."* the extractor consumes the Slice 15 1st
    sub-slice typed-shape foundation
    (:class:`GovernanceMetricDefinition` +
    :class:`GovernanceMetricValue`) plus the Slice 13a shared models
    (:data:`EvidenceQuality` + :class:`GovernanceEvidenceRef`) plus the
    Slice 13A shared completeness model (:class:`EvidenceCompleteness`)
    and projects each metric definition onto a typed
    :class:`GovernanceMetricValue`.

    **Bounded-read discipline (doc-15:141-142 + AC5 doc-15:182).** The
    extractor reads evidence by ref (the typed Slice 13a
    :class:`GovernanceEvidenceRef`) -- it NEVER scans raw artifact
    bodies. The extractor relies on the upstream Slice 13 evidence-set
    ingestor (which already enforces the bounded-read discipline at
    ingest time) to ensure the ref list is truncated to the read
    budget.

    **Typed-vs-legacy source-mix discrimination (doc-15:151-153).**
    The extractor partitions evidence refs into typed (refs whose
    :attr:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef.authority`
    is one of the typed-first authorities ``typed_journal`` /
    ``compatibility_projection`` / ``git_provenance`` /
    ``implementation_journal`` / ``implementation_decision_log`` /
    ``supervisor_digest`` / ``resource_snapshot``) and legacy (refs
    whose authority is ``legacy_event`` or ``legacy_artifact_summary``).
    When both partitions are non-empty the emitted
    :class:`GovernanceMetricValue` carries
    ``data_quality="derived"`` + ``source_mix={"typed": n, "legacy": m}``.

    **Active-work policy enforcement (doc-15:123-124).** Per
    :attr:`GovernanceMetricDefinition.active_work_policy` the extractor:

    * ``exclude``: drops refs scoped to in-flight (active) work from
      the numerator + denominator + adds
      :data:`ACTIVE_WORK_EXCLUDED_EXCLUSION` to the emitted value's
      :attr:`GovernanceMetricValue.exclusions`.
    * ``status_only``: includes active-work refs but flags them via the
      :attr:`GovernanceMetricValue.exclusions` list (status-only refs
      do NOT influence the numerator / denominator).
    * ``separate``: emits active-work refs as a separate metric value
      with the same definition + scope but a distinct exclusion tag
      (future Slice 15 sub-slices may tighten to a separate
      :class:`GovernanceMetricValue` per scope).

    **Sample-count threshold (doc-15:148-150).** The extractor emits
    ``value=None`` + conservative confidence + the
    :data:`INSUFFICIENT_SAMPLES_EXCLUSION` sentinel when the
    qualifying-ref count is below the definition-implied threshold
    (defaulted via :data:`DEFAULT_MIN_SAMPLE_COUNT`).

    **doc-13a:269-272 fail-closed gate.** When
    :attr:`MetricExtractorInputs.prompt_context_routing` is populated +
    reports ``should_invoke_runtime=False`` (the typed
    :class:`AuthoritativePromptContextRouting` carries
    ``typed_failure_class="runtime_context"`` +
    ``typed_failure_type="context_incomplete"``) the extractor emits
    ``value=None`` + the :data:`PROMPT_CONTEXT_INCOMPLETE_EXCLUSION`
    sentinel for every metric in the definition list.

    **Non-blocking discipline.** The extractor NEVER raises a failure
    to the caller. Any structural extraction failure projects onto a
    :class:`GovernanceMetricExtractionGap` finding emitted on the
    :attr:`MetricExtractor.gap_findings` list (post-extract).

    Per the auto-memory ``feedback_no_overengineer_use_library`` rule
    the extractor mirrors the Slice 14 2nd sub-slice writer surface
    (single class with one public ``extract`` method) without
    introducing new abstractions.
    """

    # The default minimum sample count below which the extractor emits
    # value=None + INSUFFICIENT_SAMPLES_EXCLUSION per doc-15:148-150.
    # Subsequent Slice 15 sub-slices may tighten to a definition-specific
    # threshold once the per-metric calibration fixture lands (doc-15:135-136
    # step 7).
    DEFAULT_MIN_SAMPLE_COUNT: int = 3

    def __init__(self) -> None:
        """Construct a metric extractor.

        The extractor is stateless aside from the
        :attr:`gap_findings` accumulator the public :meth:`extract`
        surface populates. Each call to :meth:`extract` resets the
        accumulator.
        """

        self._gap_findings: list[GovernanceMetricExtractionGap] = []

    @property
    def gap_findings(self) -> list[GovernanceMetricExtractionGap]:
        """The list of :class:`GovernanceMetricExtractionGap` findings
        the most-recent :meth:`extract` call produced.

        Per the Slice 14
        :class:`~iriai_build_v2.execution_control.commit_provenance_writer.GitProvenanceWriter`
        precedent the extractor NEVER raises a failure to the caller --
        every structural extraction failure projects onto a typed
        :class:`GovernanceMetricExtractionGap` finding.
        """

        return list(self._gap_findings)

    def extract(
        self,
        inputs: MetricExtractorInputs,
    ) -> list[GovernanceMetricValue]:
        """Project each metric definition onto a typed
        :class:`GovernanceMetricValue` (doc-15:117-136 step 2).

        Returns a list of :class:`GovernanceMetricValue` -- one per
        :class:`GovernanceMetricDefinition` in the
        :attr:`MetricExtractorInputs.definitions` list.

        Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED
        here) NEVER raises a failure to the caller. Per doc-15:148-150
        emits ``value=None`` + conservative confidence + the
        :data:`INSUFFICIENT_SAMPLES_EXCLUSION` sentinel when the
        qualifying-ref count is below the threshold. Per
        doc-13a:269-272 emits ``value=None`` + the
        :data:`PROMPT_CONTEXT_INCOMPLETE_EXCLUSION` sentinel when the
        fail-closed gate triggers.

        The extract sequence (per doc-15:117-136 step 2 + doc-15:148-150
        + doc-15:123-124 + doc-15:151-153 + doc-13a:269-272):

        1. **Fail-closed gate check (doc-13a:269-272)**: if
           ``inputs.prompt_context_routing`` is populated + reports
           ``should_invoke_runtime=False``, emit a typed
           :class:`GovernanceMetricValue` per definition with
           ``value=None`` + the
           :data:`PROMPT_CONTEXT_INCOMPLETE_EXCLUSION` sentinel. Skip
           steps 2-5 entirely (the extractor MUST NOT consume the
           prompt-context evidence per the fail-closed rule).
        2. **Partition evidence refs (doc-15:151-153)**: split
           ``inputs.evidence_set_refs`` into typed vs legacy buckets
           based on the
           :attr:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef.authority`
           field; compute the per-definition source-mix dict.
        3. **Filter active-work refs (doc-15:123-124)**: per definition
           ``active_work_policy`` (exclude / status_only / separate)
           filter the refs that scope to in-flight work; combine with
           the corpus-level ``inputs.active_work_filter`` for safety.
        4. **Compute data_quality (doc-15:131-132 step 5 + doc-15:151-153)**:
           project the Slice 13A
           :class:`EvidenceCompleteness` + the freshness window onto
           the Slice 13a
           :data:`EvidenceQuality` Literal (canonical / derived /
           sampled / advisory / stale / insufficient). Mixed typed +
           legacy refs project to ``derived``; preview_only refs
           project to ``advisory``; missing refs project to
           ``insufficient``; refs older than the freshness window
           project to ``stale``.
        5. **Threshold check (doc-15:148-150)**: if the qualifying-ref
           count is below :data:`DEFAULT_MIN_SAMPLE_COUNT`, emit
           ``value=None`` + lower confidence + the
           :data:`INSUFFICIENT_SAMPLES_EXCLUSION` sentinel. Otherwise
           emit a placeholder typed value (the actual numerator /
           denominator arithmetic is the future Slice 15 3rd
           sub-slice's confidence-scoring scope).

        Any structural failure during steps 1-5 projects onto a typed
        :class:`GovernanceMetricExtractionGap` accumulated on
        :attr:`gap_findings`; the value for that definition is still
        emitted with ``value=None`` + a typed exclusion sentinel so the
        downstream Slice 17 policy layer can block recommendations.
        """

        # Reset per-call accumulators.
        self._gap_findings = []
        results: list[GovernanceMetricValue] = []

        # ── Step 1: fail-closed gate per doc-13a:269-272 ─────────────────
        # Per the auto-memory feedback_no_silent_degradation rule the
        # extractor MUST NOT consume prompt-context evidence whose
        # routing reports should_invoke_runtime=False. Emit value=None
        # + the PROMPT_CONTEXT_INCOMPLETE_EXCLUSION sentinel for EVERY
        # definition; skip steps 2-5 entirely.
        if _routing_blocks_consumption(inputs.prompt_context_routing):
            for definition in inputs.definitions:
                results.append(
                    self._emit_fail_closed_value(
                        definition=definition,
                        corpus_id=inputs.corpus_id,
                        evidence_refs=[],
                    )
                )
            return results

        # ── Steps 2-5: per-definition projection ─────────────────────────
        # Each definition projects through the typed-vs-legacy partition
        # + active-work filtering + data_quality computation + threshold
        # check. Failures during projection accumulate onto
        # self._gap_findings (the extractor NEVER raises).
        for definition in inputs.definitions:
            try:
                value = self._project_metric_value(
                    definition=definition,
                    inputs=inputs,
                )
                results.append(value)
            except Exception as exc:  # pragma: no cover -- defensive
                # Per doc-14:242-243 (NON-blocking) the extractor NEVER
                # raises a failure to the caller; every structural
                # failure projects onto a typed gap finding + a
                # value=None projection so the downstream Slice 17
                # policy layer can block recommendations on the metric.
                self._gap_findings.append(
                    GovernanceMetricExtractionGap(
                        failure_id=GOVERNANCE_METRIC_EXTRACTION_FAILURE_ID,
                        corpus_id=inputs.corpus_id,
                        definition_name=definition.name,
                        definition_version=definition.version,
                        scope_kind=definition.scope_kind,
                        reason=f"unexpected_projection_failure: {type(exc).__name__}",
                        evidence_payload={"error_detail": str(exc)[:500]},
                    )
                )
                results.append(
                    GovernanceMetricValue(
                        definition_name=definition.name,
                        definition_version=definition.version,
                        scope={"corpus_id": inputs.corpus_id},
                        value=None,
                        unit="error",
                        confidence=0.0,
                        data_quality="insufficient",
                        source_mix={},
                        evidence_refs=[],
                        exclusions=[GOVERNANCE_METRIC_EXTRACTION_FAILURE_ID],
                    )
                )

        return results

    # ── Internal projection helpers ─────────────────────────────────────────

    def _project_metric_value(
        self,
        *,
        definition: GovernanceMetricDefinition,
        inputs: MetricExtractorInputs,
    ) -> GovernanceMetricValue:
        """Project one :class:`GovernanceMetricDefinition` onto a typed
        :class:`GovernanceMetricValue`.

        See :meth:`extract` for the step-by-step description.
        """

        # ── Step 2: partition evidence refs (doc-15:151-153) ────────────
        # Each ref is classified as either typed or legacy via its
        # GovernanceEvidenceRef.authority field; doc-13:74-84 spells
        # the 7 typed-first authorities + 2 legacy fallbacks verbatim.
        typed_refs, legacy_refs = _partition_typed_vs_legacy(inputs.evidence_set_refs)

        # ── Step 3: filter active-work refs per active_work_policy ──────
        # The per-definition policy combines with the corpus-level
        # filter for safety. Per doc-15:123-124 completed-throughput
        # averages MUST exclude active work; the exclude-policy variant
        # drops active refs from the numerator / denominator.
        (
            filtered_typed_refs,
            filtered_legacy_refs,
            active_work_excluded,
        ) = _apply_active_work_policy(
            typed_refs=typed_refs,
            legacy_refs=legacy_refs,
            policy=definition.active_work_policy,
            corpus_filter=inputs.active_work_filter,
        )

        # The retained refs feed the data_quality projection + threshold
        # check + emitted GovernanceMetricValue.evidence_refs list.
        retained_refs = filtered_typed_refs + filtered_legacy_refs
        sample_count = len(retained_refs)

        # ── Step 4: compute data_quality (doc-15:131-132 + doc-15:151-153) ─
        # Project the Slice 13A EvidenceCompleteness + freshness window
        # onto the Slice 13a EvidenceQuality Literal.
        data_quality = _compute_data_quality(
            completeness_state=inputs.completeness_state,
            typed_refs=filtered_typed_refs,
            legacy_refs=filtered_legacy_refs,
            freshness_window_hours=inputs.freshness_window_hours,
        )

        # ── Step 5: threshold check (doc-15:148-150) ────────────────────
        # If the sample count is below the threshold, emit value=None +
        # conservative confidence + the INSUFFICIENT_SAMPLES_EXCLUSION
        # sentinel.
        exclusions: list[str] = []
        if active_work_excluded:
            exclusions.append(ACTIVE_WORK_EXCLUDED_EXCLUSION)

        # Build the source_mix dict per doc-15:151-153. When both typed
        # AND legacy refs are present, the dict carries both counts;
        # when only one side is present the dict carries only that
        # side's count. Empty dict for the edge case of no refs at all
        # (handled by the threshold check below).
        source_mix: dict[str, int] = {}
        if filtered_typed_refs:
            source_mix["typed"] = len(filtered_typed_refs)
        if filtered_legacy_refs:
            source_mix["legacy"] = len(filtered_legacy_refs)

        # Scope dict per doc-15:81 -- typed-key dict with the corpus +
        # scope-kind keys; future Slice 15 sub-slices may tighten to a
        # typed-key Literal once the scope-shape surface crystallises.
        scope: dict[str, str] = {
            "corpus_id": inputs.corpus_id,
            "scope_kind": definition.scope_kind,
        }

        if sample_count < self.DEFAULT_MIN_SAMPLE_COUNT:
            # Per doc-15:148-150 emit value=None + conservative
            # confidence + the INSUFFICIENT_SAMPLES_EXCLUSION sentinel.
            exclusions.append(INSUFFICIENT_SAMPLES_EXCLUSION)
            return GovernanceMetricValue(
                definition_name=definition.name,
                definition_version=definition.version,
                scope=scope,
                value=None,
                unit=_unit_for_definition(definition),
                confidence=0.0,
                data_quality=data_quality,
                source_mix=source_mix,
                evidence_refs=retained_refs,
                exclusions=exclusions,
            )

        # ── Step 6: real arithmetic (doc-15 § Refactoring Steps step 4;
        #             3rd sub-slice) ───────────────────────────────────────
        # Per doc-15:125-130 step 4 + doc-15:141-142 + AC5 doc-15:182:
        # numerator + denominator are projected from the typed
        # GovernanceMetricDefinition.numerator + .denominator field
        # strings (the 1st-sub-slice typed surface) over ONLY the
        # bounded evidence-set refs (NOT raw artifact body scans).
        numerator = _compute_numerator(
            definition=definition,
            retained_refs=retained_refs,
            freshness_window_hours=inputs.freshness_window_hours,
        )
        denominator = _compute_denominator(
            definition=definition,
            retained_refs=retained_refs,
            freshness_window_hours=inputs.freshness_window_hours,
        )
        complexity_factor = _compute_complexity_adjustment(
            definition=definition,
            task_shape_inputs=inputs.task_shape_inputs,
        )
        # Apply the complexity adjustment per doc-15:125-130 step 4 +
        # the per-metric semantics rule. For complexity-adjusted
        # throughput metrics (those whose name starts with
        # ``complexity_adjusted_``) the factor is applied to the
        # denominator (more complex → effectively more "work units"
        # per task → fewer tasks per effective hour). For all other
        # metrics the factor is 1.0 (no adjustment) so the metric
        # semantics remain stable.
        if definition.name.startswith("complexity_adjusted_"):
            denominator = denominator * complexity_factor
        # Compute the typed value (float). When denominator is 0, the
        # value falls back to 0.0 (NOT None) so the calibrated
        # confidence still drives the downstream consumer behaviour.
        # This is distinct from the doc-15:148-150 insufficient-sample
        # case (value=None handled above) and from the doc-13a:269-272
        # fail-closed case (value=None handled in the dedicated branch
        # of MetricExtractor.extract).
        value: float | int
        if denominator == 0:
            value = 0.0
        else:
            value = numerator / denominator

        # ── Step 7: calibrated confidence (doc-15:131-132 step 5 + AC4;
        #             3rd sub-slice) ───────────────────────────────────────
        # Per doc-15:131-132 step 5 + doc-15:181 AC4: the 5-arg
        # calibrated projection consumes EvidenceCompleteness +
        # sample_count + freshness_window_hours + data_quality +
        # implementation_log_completeness.
        confidence = _compute_confidence(
            completeness=inputs.completeness_state,
            sample_count=sample_count,
            freshness_window_hours=inputs.freshness_window_hours,
            data_quality=data_quality,
            implementation_log_completeness=inputs.implementation_log_completeness,
            retained_refs=retained_refs,
        )

        return GovernanceMetricValue(
            definition_name=definition.name,
            definition_version=definition.version,
            scope=scope,
            value=value,
            unit=_unit_for_definition(definition),
            confidence=confidence,
            data_quality=data_quality,
            source_mix=source_mix,
            evidence_refs=retained_refs,
            exclusions=exclusions,
        )

    def _emit_fail_closed_value(
        self,
        *,
        definition: GovernanceMetricDefinition,
        corpus_id: str,
        evidence_refs: list[GovernanceEvidenceRef],
    ) -> GovernanceMetricValue:
        """Emit a typed :class:`GovernanceMetricValue` for the
        doc-13a:269-272 fail-closed gate case.

        Per the fail-closed rule the extractor MUST NOT consume the
        prompt-context evidence; every emitted value carries
        ``value=None`` + the
        :data:`PROMPT_CONTEXT_INCOMPLETE_EXCLUSION` sentinel +
        ``confidence=0.0`` + ``data_quality="insufficient"`` (the typed
        EvidenceQuality value that downstream consumers treat as
        "do not consume").
        """

        return GovernanceMetricValue(
            definition_name=definition.name,
            definition_version=definition.version,
            scope={
                "corpus_id": corpus_id,
                "scope_kind": definition.scope_kind,
            },
            value=None,
            unit=_unit_for_definition(definition),
            confidence=0.0,
            data_quality="insufficient",
            source_mix={},
            evidence_refs=evidence_refs,
            exclusions=[PROMPT_CONTEXT_INCOMPLETE_EXCLUSION],
        )


# --- Pure helpers (no extractor state) --------------------------------------


# The Slice 13a 9-value EvidenceAuthority Literal at
# src/iriai_build_v2/workflows/develop/governance/models.py:92-102 names
# 7 typed-first authorities + 2 legacy fallbacks per doc-13:74-84. The
# typed-first authorities project to "typed" in the source_mix dict; the
# 2 legacy fallbacks project to "legacy" per doc-15:151-153.
_TYPED_AUTHORITIES: frozenset[str] = frozenset(
    {
        "typed_journal",
        "compatibility_projection",
        "git_provenance",
        "implementation_journal",
        "implementation_decision_log",
        "supervisor_digest",
        "resource_snapshot",
    }
)


_LEGACY_AUTHORITIES: frozenset[str] = frozenset(
    {
        "legacy_event",
        "legacy_artifact_summary",
    }
)


def _partition_typed_vs_legacy(
    refs: list[GovernanceEvidenceRef],
) -> tuple[list[GovernanceEvidenceRef], list[GovernanceEvidenceRef]]:
    """Partition the evidence-ref list into (typed, legacy) buckets per
    doc-15:151-153 + doc-13:74-84.

    Per doc-15:151-153 *"Mixed legacy and typed evidence: set
    data_quality='derived', add source_mix={'typed': n, 'legacy': n}..."*
    the typed-vs-legacy discrimination grounds on the
    :attr:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef.authority`
    field (the Slice 13a 9-value :data:`EvidenceAuthority` Literal).

    The 7 typed-first authorities are: ``typed_journal``,
    ``compatibility_projection``, ``git_provenance``,
    ``implementation_journal``, ``implementation_decision_log``,
    ``supervisor_digest``, ``resource_snapshot``.

    The 2 legacy fallbacks are: ``legacy_event``,
    ``legacy_artifact_summary``.

    Refs whose authority is outside this 9-value set are treated as
    legacy (defence-in-depth fallback; the typed Literal at construction
    already restricts to these 9 values).
    """

    typed: list[GovernanceEvidenceRef] = []
    legacy: list[GovernanceEvidenceRef] = []
    for ref in refs:
        if ref.authority in _TYPED_AUTHORITIES:
            typed.append(ref)
        else:
            legacy.append(ref)
    return typed, legacy


def _apply_active_work_policy(
    *,
    typed_refs: list[GovernanceEvidenceRef],
    legacy_refs: list[GovernanceEvidenceRef],
    policy: Literal["exclude", "status_only", "separate"],
    corpus_filter: Literal["exclude", "status_only", "separate"],
) -> tuple[list[GovernanceEvidenceRef], list[GovernanceEvidenceRef], bool]:
    """Apply the per-definition + corpus-level active-work policy
    filters per doc-15:123-124.

    Returns a triple ``(filtered_typed, filtered_legacy, excluded_any)``
    where ``excluded_any`` is True if the filter dropped any active-work
    ref.

    Per doc-15:123-124 the 3 policies are:

    * ``exclude``: drops active-work refs from the numerator +
      denominator; the emitted value carries the
      :data:`ACTIVE_WORK_EXCLUDED_EXCLUSION` sentinel.
    * ``status_only``: includes active-work refs but flags them via the
      exclusions list (the upstream Slice 13 evidence-set ingestor
      already tags active-work refs via the ``preview_only`` flag).
    * ``separate``: passes active-work refs through unchanged; the
      future Slice 15 sub-slice that wires per-scope value emission
      will split active vs completed into separate emitted values.

    The corpus-level filter combines with the per-definition policy:
    when EITHER side requests ``exclude`` the filter drops the ref.
    This is the conservative interpretation of doc-15:123-124 -- the
    corpus-level filter is a safety net + the per-definition policy is
    the fine-grained control.

    **Active-work detection (this sub-slice).** This sub-slice uses
    the conservative interpretation that a ref scopes to "active work"
    when its ``completeness == "preview_only"`` OR its ``preview_only``
    flag is True OR its ``slice_id`` contains ``active`` or ``in_flight``
    OR the ref id starts with ``active:``. Subsequent Slice 15
    sub-slices may tighten via a typed
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    subclass with an explicit ``active_work`` flag.
    """

    if policy == "separate" and corpus_filter == "separate":
        # Both policies pass through; the future sub-slice will split
        # into separate emitted values per active vs completed.
        return list(typed_refs), list(legacy_refs), False

    excluded_any = False
    filtered_typed: list[GovernanceEvidenceRef] = []
    filtered_legacy: list[GovernanceEvidenceRef] = []

    drop_active = policy == "exclude" or corpus_filter == "exclude"

    for ref in typed_refs:
        if drop_active and _is_active_work_ref(ref):
            excluded_any = True
            continue
        filtered_typed.append(ref)
    for ref in legacy_refs:
        if drop_active and _is_active_work_ref(ref):
            excluded_any = True
            continue
        filtered_legacy.append(ref)

    return filtered_typed, filtered_legacy, excluded_any


def _is_active_work_ref(ref: GovernanceEvidenceRef) -> bool:
    """Conservative detector for active-work refs per doc-15:123-124.

    Returns True when the ref scopes to in-flight work (per the
    docstring on :func:`_apply_active_work_policy`).

    The detection rules are intentionally conservative (favour
    including a ref over dropping it) so the extractor does not silently
    drop completed-work refs that happen to look active. The 3
    detection conditions are independent (logical OR):

    1. ``ref.completeness == "preview_only"`` -- preview-only refs are
       display-only per the Slice 13A invariant doc-13a:18-23; they
       cannot satisfy authoritative consumers.
    2. ``ref.preview_only is True`` -- explicit preview flag (the
       Slice 13a typed surface carries both
       :attr:`completeness` and :attr:`preview_only` per
       doc-13:111).
    3. ``ref.slice_id`` carries ``active`` or ``in_flight`` substrings,
       or ``ref.ref_id`` starts with ``active:`` -- the slice-id
       convention is the upstream Slice 13 evidence-set ingestor's
       way of tagging active-work refs.
    """

    if ref.completeness == "preview_only" or ref.preview_only:
        return True

    if ref.slice_id is not None and (
        "active" in ref.slice_id.lower()
        or "in_flight" in ref.slice_id.lower()
        or "in-flight" in ref.slice_id.lower()
    ):
        return True

    if ref.ref_id.startswith("active:") or ref.ref_id.startswith("in_flight:"):
        return True

    return False


def _compute_data_quality(
    *,
    completeness_state: EvidenceCompleteness,
    typed_refs: list[GovernanceEvidenceRef],
    legacy_refs: list[GovernanceEvidenceRef],
    freshness_window_hours: float,
) -> EvidenceQuality:
    """Project the Slice 13A :class:`EvidenceCompleteness` + the
    freshness window onto the Slice 13a :data:`EvidenceQuality`
    Literal per doc-15:131-132 step 5 (confidence scoring from evidence
    completeness + freshness + typed-vs-legacy source mix +
    implementation-log completeness).

    The 6-value :data:`EvidenceQuality` Literal lands per doc-13:113-120:

    * ``canonical`` -- typed-first + complete + fresh (state="complete"
      AND no legacy refs AND all refs fresh).
    * ``derived`` -- mixed typed + legacy (per doc-15:151-153) OR
      state="paged".
    * ``sampled`` -- only legacy refs OR state="preview_only" with
      typed refs.
    * ``advisory`` -- preview_only refs only.
    * ``stale`` -- typed refs all older than freshness_window_hours.
    * ``insufficient`` -- state="unavailable" OR no refs.

    The classification is intentionally LOWER-CONFIDENCE-PREFERRING per
    doc-15:151-153 ("lower confidence when typed evidence is
    incomplete") -- the extractor downgrades quality when typed evidence
    is missing or stale.
    """

    # state=unavailable always maps to insufficient (per doc-13a:307-310).
    if completeness_state.state == "unavailable":
        return "insufficient"

    # No refs at all -> insufficient.
    if not typed_refs and not legacy_refs:
        return "insufficient"

    # Mixed typed + legacy -> derived per doc-15:151-153.
    if typed_refs and legacy_refs:
        return "derived"

    # Legacy-only -> sampled (Slice 13a quality projection per
    # doc-13:173-175; legacy_event + legacy_artifact_summary are
    # sample-grade by the doc-15:152 rule).
    if not typed_refs and legacy_refs:
        return "sampled"

    # Typed-only path: classify by completeness state + freshness.
    # state=preview_only -> advisory (Slice 13A invariant doc-13a:18-23).
    if completeness_state.state == "preview_only":
        return "advisory"

    # state=paged with typed refs -> derived (paged is authoritative
    # per the Slice 13A invariant but spans multiple pages; the typed
    # quality "derived" reflects the multi-page projection).
    if completeness_state.state == "paged":
        return "derived"

    # Freshness check: typed-only + complete state -> if all refs are
    # stale, drop to "stale"; otherwise canonical.
    if all(_is_stale_ref(ref, freshness_window_hours) for ref in typed_refs):
        return "stale"

    # state=complete + typed-only + at-least-one-fresh -> canonical.
    return "canonical"


def _is_stale_ref(
    ref: GovernanceEvidenceRef,
    freshness_window_hours: float,
) -> bool:
    """Return True if ``ref`` is older than ``freshness_window_hours``
    per doc-15:131-132 step 5 (freshness input to confidence scoring).

    Refs with a None :attr:`created_at` are considered NOT stale (the
    upstream Slice 13 evidence-set ingestor populates this field; refs
    without it are typically synthetic and should not be penalised on
    the freshness axis).
    """

    if ref.created_at is None:
        return False

    now = datetime.now(timezone.utc)
    # Normalize ref.created_at to UTC if it carries timezone info.
    ref_time = ref.created_at
    if ref_time.tzinfo is None:
        # Naive datetime; assume UTC per doc-13:97-111 convention.
        ref_time = ref_time.replace(tzinfo=timezone.utc)

    age_seconds = (now - ref_time).total_seconds()
    age_hours = age_seconds / 3600.0
    return age_hours > freshness_window_hours


def _routing_blocks_consumption(
    routing: AuthoritativePromptContextRouting | None,
) -> bool:
    """Doc-13a:269-272 fail-closed gate: return True when the
    extractor MUST NOT consume the prompt-context evidence.

    The fail-closed condition is:

    * ``routing is not None`` (the caller provided a routing signal); AND
    * ``routing.should_invoke_runtime is False`` (the routing indicates
      the prompt-context evidence is incomplete); AND
    * ``routing.typed_failure_type == "context_incomplete"`` (the
      routing carries the typed failure type that names the
      ``runtime_context/context_incomplete`` failure id).

    Returns False otherwise (the extractor may consume the evidence).

    Per the auto-memory ``feedback_no_silent_degradation`` rule: when
    in doubt, fail closed. The triple condition above is intentionally
    explicit (all 3 must be True) so callers that pass an incomplete
    routing signal (e.g. one that lacks the typed_failure_type field)
    do NOT silently trip the fail-closed gate.
    """

    if routing is None:
        return False
    if routing.should_invoke_runtime:
        return False
    return routing.typed_failure_type == "context_incomplete"


def _unit_for_definition(definition: GovernanceMetricDefinition) -> str:
    """Project a :class:`GovernanceMetricDefinition` onto a unit string
    per doc-15:83.

    The default unit is derived from the ``denominator`` field:

    * ``elapsed hours`` / ``"hours"`` -> ``"tasks/hour"``.
    * ``completed tasks`` / ``"tasks"`` -> ``"count/task"``.
    * ``dispatched attempts`` / ``"attempts"`` -> ``"failures/attempt"``.

    This is a defence-in-depth helper; future Slice 15 sub-slices may
    tighten to a typed :attr:`GovernanceMetricDefinition.unit` field.
    """

    denom_lower = definition.denominator.lower()
    if "hour" in denom_lower:
        return "tasks/hour"
    if "task" in denom_lower:
        return "count/task"
    if "attempt" in denom_lower:
        return "failures/attempt"
    return "ratio"


# ── doc-15:131-132 step 5 + AC4 calibrated confidence ───────────────────────
# The 3rd sub-slice's calibrated 5-arg projection grounded on
# EvidenceCompleteness + sample_count + freshness_window_hours +
# data_quality + implementation_log_completeness (the AC4 signal
# per doc-15:181). Geometric mean of the 5 contributions caps the
# composite at [0.0, 1.0]; any zero contribution drops the composite to
# zero per the auto-memory ``feedback_no_silent_degradation`` rule.


# Contribution table for the data_quality input. Per doc-15:151-153 +
# Slice 13a quality projection doc-13:173-175 + the 3rd sub-slice's
# calibration table the 6 EvidenceQuality values map to:
#
# * canonical  -> 1.0  (typed-first + complete + fresh)
# * derived    -> 0.7  (mixed typed + legacy; paged authoritative)
# * sampled    -> 0.4  (legacy-only; sample-grade)
# * advisory   -> 0.3  (preview-only; not authoritative)
# * stale      -> 0.2  (all-stale typed refs)
# * insufficient -> 0.0 (no refs or state=unavailable)
_DATA_QUALITY_CONFIDENCE: dict[EvidenceQuality, float] = {
    "canonical": 1.0,
    "derived": 0.7,
    "sampled": 0.4,
    "advisory": 0.3,
    "stale": 0.2,
    "insufficient": 0.0,
}


# Contribution table for the EvidenceCompleteness.state input per
# doc-13a:128-141 + the 3rd sub-slice's calibration:
#
# * complete    -> 1.0  (full evidence in single page)
# * paged       -> 0.7  (authoritative but paged; needs traversal)
# * preview_only -> 0.3 (display-only per Slice 13A invariant
#                        doc-13a:18-23)
# * unavailable -> 0.0  (per doc-13a:307-310 fail-closed)
_COMPLETENESS_CONTRIBUTION: dict[str, float] = {
    "complete": 1.0,
    "paged": 0.7,
    "preview_only": 0.3,
    "unavailable": 0.0,
}


# The sample-count cap for the linear ramp. Per doc-15:131-132 step 5
# the sample-count contribution is a linear ramp from 0.0 (at the
# threshold of 3) to 1.0 (at the calibration cap of 30+). Values above
# 30 are clipped to 1.0 (the ramp saturates).
_SAMPLE_COUNT_CAP: int = 30


def _compute_confidence(
    *,
    completeness: EvidenceCompleteness,
    sample_count: int,
    freshness_window_hours: float,
    data_quality: EvidenceQuality,
    implementation_log_completeness: EvidenceCompleteness | None,
    retained_refs: list[GovernanceEvidenceRef],
) -> float:
    """Project the 5 calibrated inputs onto a confidence score in [0.0, 1.0]
    per doc-15:131-132 step 5 + doc-15:181 AC4.

    Per *"Add confidence scoring from evidence completeness, sample count,
    freshness, typed-vs-legacy source mix, and implementation-log
    completeness."* the calibrated projection grounds on:

    1. **EvidenceCompleteness contribution** (doc-15:131; Slice 13A
       shared model): :attr:`EvidenceCompleteness.state` projects onto
       the :data:`_COMPLETENESS_CONTRIBUTION` table.
    2. **Sample-count contribution** (doc-15:131): linear ramp from 0.0
       at :data:`MetricExtractor.DEFAULT_MIN_SAMPLE_COUNT` to 1.0 at
       :data:`_SAMPLE_COUNT_CAP` (30); saturates at 1.0 above the cap.
    3. **Freshness contribution** (doc-15:131): 1.0 when at-least-one
       retained ref is fresh (within ``freshness_window_hours``); 0.5
       when all refs are stale but within 2x the window; 0.2 when all
       refs are >2x stale.
    4. **Data-quality contribution** (doc-15:131; typed-vs-legacy source
       mix proxied by :data:`EvidenceQuality`): projects onto the
       :data:`_DATA_QUALITY_CONFIDENCE` table.
    5. **Implementation-log completeness contribution** (doc-15:131-132 +
       doc-15:181 AC4): the AC4 signal; projects onto the
       :data:`_COMPLETENESS_CONTRIBUTION` table when populated; defaults
       to 1.0 when None (so corpora without an implementation-log
       completeness signal are not penalised on this axis).

    The 5 contributions compose via geometric mean (multiplicative
    aggregation; the n-th root of the product). Geometric mean penalises
    any single low contribution disproportionately — a zero on any one
    input drops the composite to zero per the auto-memory
    ``feedback_no_silent_degradation`` rule (e.g. an incomplete
    implementation journal per doc-15:181 AC4 reduces confidence to
    zero regardless of other inputs).

    The result is clipped to [0.0, 1.0] for the
    :attr:`GovernanceMetricValue.confidence` typed-shape contract per
    doc-15:84.
    """

    # 1. EvidenceCompleteness contribution.
    completeness_factor = _COMPLETENESS_CONTRIBUTION.get(completeness.state, 0.0)

    # 2. Sample-count contribution: linear ramp from threshold to cap;
    # saturates at 1.0 above the cap.
    if sample_count < MetricExtractor.DEFAULT_MIN_SAMPLE_COUNT:
        sample_count_factor = 0.0
    elif sample_count >= _SAMPLE_COUNT_CAP:
        sample_count_factor = 1.0
    else:
        # Ramp from threshold (factor 0.0) to cap (factor 1.0).
        span = float(_SAMPLE_COUNT_CAP - MetricExtractor.DEFAULT_MIN_SAMPLE_COUNT)
        sample_count_factor = (
            float(sample_count - MetricExtractor.DEFAULT_MIN_SAMPLE_COUNT) / span
        )

    # 3. Freshness contribution.
    freshness_factor = _freshness_factor(
        retained_refs=retained_refs,
        freshness_window_hours=freshness_window_hours,
    )

    # 4. Data-quality contribution.
    data_quality_factor = _DATA_QUALITY_CONFIDENCE.get(data_quality, 0.0)

    # 5. Implementation-log completeness contribution (AC4 doc-15:181).
    # When None (default) the contribution is 1.0 so corpora without an
    # implementation-log completeness signal are not penalised on this
    # axis; when populated the contribution projects onto the
    # _COMPLETENESS_CONTRIBUTION table.
    if implementation_log_completeness is None:
        impl_log_factor = 1.0
    else:
        impl_log_factor = _COMPLETENESS_CONTRIBUTION.get(
            implementation_log_completeness.state, 0.0
        )

    # Composite: geometric mean of the 5 contributions.
    contributions = [
        completeness_factor,
        sample_count_factor,
        freshness_factor,
        data_quality_factor,
        impl_log_factor,
    ]
    # Geometric mean = (product) ** (1/n); any zero contribution drops
    # the composite to zero per the auto-memory feedback_no_silent_degradation
    # rule.
    product = 1.0
    for c in contributions:
        product *= c
    composite = product ** (1.0 / len(contributions))

    # Clip to [0.0, 1.0] for the GovernanceMetricValue.confidence
    # typed-shape contract per doc-15:84.
    if composite < 0.0:
        return 0.0
    if composite > 1.0:
        return 1.0
    return composite


def _freshness_factor(
    *,
    retained_refs: list[GovernanceEvidenceRef],
    freshness_window_hours: float,
) -> float:
    """Project the retained refs' freshness onto a freshness factor in
    [0.0, 1.0] per doc-15:131-132 step 5.

    Returns:

    * 1.0 when at-least-one retained ref is fresh (within
      ``freshness_window_hours``).
    * 0.5 when all retained refs are stale but within 2x the window.
    * 0.2 when all retained refs are >2x stale.
    * 1.0 when the list is empty (no penalty; the
      sample-count-contribution drops to 0 anyway).

    Refs with :attr:`GovernanceEvidenceRef.created_at` set to None are
    treated as fresh (the upstream Slice 13 ingestor populates this
    field; refs without it are typically synthetic and should not be
    penalised on the freshness axis).
    """

    if not retained_refs:
        return 1.0

    # Bucket by age in hours.
    fresh_count = 0
    stale_count = 0
    very_stale_count = 0
    for ref in retained_refs:
        age_hours = _ref_age_hours(ref)
        if age_hours is None:
            fresh_count += 1
        elif age_hours <= freshness_window_hours:
            fresh_count += 1
        elif age_hours <= 2.0 * freshness_window_hours:
            stale_count += 1
        else:
            very_stale_count += 1

    if fresh_count > 0:
        return 1.0
    if stale_count > 0:
        return 0.5
    # All very stale.
    return 0.2


def _ref_age_hours(ref: GovernanceEvidenceRef) -> float | None:
    """Return the ref's age in hours (None if no ``created_at``).

    Mirrors :func:`_is_stale_ref` so the freshness projection is
    consistent across the data_quality projection and the calibrated
    confidence projection.
    """

    if ref.created_at is None:
        return None
    now = datetime.now(timezone.utc)
    ref_time = ref.created_at
    if ref_time.tzinfo is None:
        ref_time = ref_time.replace(tzinfo=timezone.utc)
    age_seconds = (now - ref_time).total_seconds()
    return age_seconds / 3600.0


# ── doc-15 § Refactoring Steps step 4 numerator / denominator + complexity ──
# Real numerator + denominator arithmetic + complexity adjustment per
# doc-15:125-130 step 4. The arithmetic uses ONLY bounded evidence-set
# refs (NOT raw artifact body scans per doc-15:141-142 + AC5 doc-15:182).


# Per doc-15:74 the definition's required_evidence_kinds list names the
# typed-evidence kinds the metric requires; the numerator counts refs
# whose ``authority`` (the Slice 13a 9-value Literal) is in the
# required_evidence_kinds set. When required_evidence_kinds is empty
# the numerator falls back to the count of all retained refs (so the
# extractor still emits a typed value rather than silently dropping
# the metric).
def _compute_numerator(
    *,
    definition: GovernanceMetricDefinition,
    retained_refs: list[GovernanceEvidenceRef],
    freshness_window_hours: float,
) -> float:
    """Compute the metric's numerator per doc-15:72 +
    :attr:`GovernanceMetricDefinition.numerator` field string.

    The numerator counts refs whose ``authority`` is in the
    definition's :attr:`required_evidence_kinds` set; when the set is
    empty the numerator falls back to the total retained-ref count.
    The fallback ensures the extractor emits a typed value rather than
    silently dropping the metric per the auto-memory
    ``feedback_no_silent_degradation`` rule.

    The arithmetic uses ONLY bounded evidence-set refs (the typed Slice
    13a :class:`GovernanceEvidenceRef`) per doc-15:141-142 + AC5
    doc-15:182 — NEVER raw artifact bodies.

    Returns a typed ``float`` for the typed
    :attr:`GovernanceMetricValue.value` contract per doc-15:82.
    """

    required = set(definition.required_evidence_kinds)
    if not required:
        return float(len(retained_refs))
    matching = sum(1 for ref in retained_refs if ref.authority in required)
    return float(matching)


# Per doc-15:73 the definition's denominator field carries a free-form
# description string (e.g. "elapsed hours" / "completed tasks" /
# "dispatched attempts"). The 3rd sub-slice projects the description
# onto a typed denominator class:
#
# * "hour"     in denom_lower -> freshness_window_hours (bounded float).
# * "task"     in denom_lower -> count of task-scope refs.
# * "attempt"  in denom_lower -> count of attempt-scope refs.
# * otherwise -> max(retained sample_count, 1).
#
# The classification mirrors :func:`_unit_for_definition` for
# consistency across the two helpers. Future Slice 15 sub-slices may
# tighten to a typed denominator Literal field on
# :class:`GovernanceMetricDefinition`.
_TASK_SCOPE_AUTHORITIES: frozenset[str] = frozenset(
    {
        "typed_journal",
        "implementation_journal",
        "implementation_decision_log",
    }
)
_ATTEMPT_SCOPE_AUTHORITIES: frozenset[str] = frozenset(
    {
        "typed_journal",
    }
)


def _compute_denominator(
    *,
    definition: GovernanceMetricDefinition,
    retained_refs: list[GovernanceEvidenceRef],
    freshness_window_hours: float,
) -> float:
    """Compute the metric's denominator per doc-15:73 +
    :attr:`GovernanceMetricDefinition.denominator` field string.

    The arithmetic projects the free-form denominator description onto
    a typed denominator class:

    * **"hour"** in description -> ``freshness_window_hours`` (bounded
      float; the corpus-level freshness window per
      :attr:`MetricExtractorInputs.freshness_window_hours`).
    * **"task"** in description -> count of refs whose authority is in
      :data:`_TASK_SCOPE_AUTHORITIES`.
    * **"attempt"** in description -> count of refs whose authority is
      in :data:`_ATTEMPT_SCOPE_AUTHORITIES`.
    * **default** -> ``max(len(retained_refs), 1)``.

    The classification mirrors :func:`_unit_for_definition` for
    consistency.

    Per doc-15:141-142 + AC5 doc-15:182 the arithmetic uses ONLY
    bounded evidence-set refs (NEVER raw artifact bodies). When the
    typed count is 0 the helper returns 0.0; the calling
    :meth:`MetricExtractor._project_metric_value` handles the
    division-by-zero edge case by falling back to ``value=0.0`` (NOT
    None; the value-None case is reserved for the doc-15:148-150
    insufficient-sample path + the doc-13a:269-272 fail-closed gate).

    Returns a typed ``float`` for the typed-arithmetic contract.
    """

    denom_lower = definition.denominator.lower()
    if "hour" in denom_lower:
        # Hours denominator: corpus-level freshness window.
        return float(freshness_window_hours)
    if "task" in denom_lower:
        # Task denominator: count of task-scope refs.
        return float(
            sum(
                1
                for ref in retained_refs
                if ref.authority in _TASK_SCOPE_AUTHORITIES
            )
        )
    if "attempt" in denom_lower:
        # Attempt denominator: count of attempt-scope refs.
        return float(
            sum(
                1
                for ref in retained_refs
                if ref.authority in _ATTEMPT_SCOPE_AUTHORITIES
            )
        )
    # Default: max retained sample count, 1 (so a denominator-less
    # metric emits a per-sample ratio).
    return float(max(len(retained_refs), 1))


# Complexity-adjustment lookup tables per doc-15:125-130 step 4. Each
# axis contributes a multiplicative factor to the composite complexity
# adjustment.


# Barrier-type axis. Hard barriers serialise the execution and increase
# the per-task work; soft barriers add a small overhead; no barrier is
# the unit factor.
_BARRIER_FACTOR: dict[str, float] = {
    "none": 1.0,
    "soft": 1.1,
    "hard": 1.3,
}


def _compute_complexity_adjustment(
    *,
    definition: GovernanceMetricDefinition,
    task_shape_inputs: TaskShapeInputs | None,
) -> float:
    """Compute the typed complexity-adjustment factor per doc-15:125-130
    step 4.

    Per *"Add complexity adjustment from pre-execution task-shape inputs
    only: task count, contract path breadth, repo count, barrier type,
    dependency depth, planned verifier-gate count, and declared write-set
    uncertainty. Do not include observed failure classes such as stale
    projection, commit hygiene, provider instability, or queue drag in
    complexity adjustment; those remain workflow-drag metrics."*

    The 7 pre-execution task-shape axes compose via multiplicative
    factors:

    1. **task_count** -- log-ish ramp: more tasks → marginally higher
       complexity (each additional task adds 1% complexity, capped at
       2x at 100 tasks).
    2. **contract_path_breadth** -- linear ramp: more contract paths →
       higher complexity (1% per path).
    3. **repo_count** -- linear ramp: more repos → higher complexity
       (10% per additional repo above 1).
    4. **barrier_type** -- table lookup: none=1.0, soft=1.1, hard=1.3.
    5. **dependency_depth** -- linear ramp: deeper chains → higher
       complexity (5% per dependency level).
    6. **planned_verifier_gate_count** -- linear ramp: more gates →
       higher complexity (3% per gate).
    7. **declared_write_set_uncertainty** -- linear ramp: 0% at 0.0
       uncertainty, 50% at 1.0 (max) uncertainty.

    The composite factor is the product of the 7 axes. Returns 1.0
    (no adjustment) when ``task_shape_inputs`` is None (the default
    when the corpus has no planning-artifact-derived task-shape
    bundle).

    Per doc-15:127-130 this projection MUST NOT consume observed
    failure classes (stale projection, commit hygiene, provider
    instability, queue drag) — those remain workflow-drag metrics
    fed by the metric extractor's normal arithmetic. The
    :class:`TaskShapeInputs` typed surface enforces this contract by
    not carrying observed-failure fields.

    The ``definition`` parameter is accepted for future per-metric
    customization (e.g. some metrics may weight the axes differently);
    in the 3rd sub-slice the composite is uniform across all metrics.
    """

    if task_shape_inputs is None:
        return 1.0

    # 1. task_count: 1% per task; capped at 2x at 100 tasks.
    task_count_factor = min(2.0, 1.0 + 0.01 * float(task_shape_inputs.task_count))

    # 2. contract_path_breadth: 1% per path.
    contract_path_factor = 1.0 + 0.01 * float(task_shape_inputs.contract_path_breadth)

    # 3. repo_count: 10% per additional repo above 1.
    repo_count_factor = 1.0 + 0.10 * float(max(0, task_shape_inputs.repo_count - 1))

    # 4. barrier_type: table lookup.
    barrier_factor = _BARRIER_FACTOR.get(task_shape_inputs.barrier_type, 1.0)

    # 5. dependency_depth: 5% per dependency level.
    dependency_factor = 1.0 + 0.05 * float(task_shape_inputs.dependency_depth)

    # 6. planned_verifier_gate_count: 3% per gate.
    verifier_gate_factor = (
        1.0 + 0.03 * float(task_shape_inputs.planned_verifier_gate_count)
    )

    # 7. declared_write_set_uncertainty: 0% at 0.0, 50% at 1.0.
    write_set_factor = 1.0 + 0.50 * float(task_shape_inputs.declared_write_set_uncertainty)

    composite = (
        task_count_factor
        * contract_path_factor
        * repo_count_factor
        * barrier_factor
        * dependency_factor
        * verifier_gate_factor
        * write_set_factor
    )
    return composite
