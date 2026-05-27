"""Slice 18 fourth sub-slice -- event replay engine for typed event-transition counterfactuals.

This module implements doc-18 § Refactoring Steps step 4 (line 114):
*"Add event replay where typed attempt, gate, failure, queue, and
checkpoint transitions are available."*

Per doc-18:114 vs doc-18:113 the event replay engine projects
counterfactual deltas at HIGHER fidelity than the Slice 18 3rd sub-
slice summary-replay engine -- because it consumes the typed Slice
00-12 event-transition shapes (typed
:class:`~iriai_build_v2.workflows.develop.execution.snapshots.ExecutionAttemptSummary`
attempt transitions + typed
:class:`~iriai_build_v2.workflows.develop.execution.snapshots.GateStatusSummary`
gate transitions + typed
:class:`~iriai_build_v2.workflows.develop.execution.snapshots.TypedFailureSummary`
failure transitions + typed
:class:`~iriai_build_v2.workflows.develop.execution.snapshots.MergeQueueSummary`
queue transitions + typed
:class:`~iriai_build_v2.workflows.develop.execution.snapshots.EvidenceRef`
checkpoint refs) directly rather than relying on metric medians.

This module owns:

* :data:`EVENT_REPLAY_FAILURE_ID` -- the typed failure id Literal
  (``event_replay_failed``) registering under the EXISTING
  ``evidence_corruption`` failure_class with the REUSED Slice 14 2nd
  sub-slice ``retry_governance_projection`` NON-blocking RouteAction
  (mirrors Slice 17 2nd/3rd/4th/5th/6th + Slice 18 2nd/3rd sub-slice
  precedent verbatim).
* :data:`DEFAULT_MAX_EVENT_TRANSITIONS` -- the bounded-input default
  cap on the total event-transition list count (512; higher than the
  Slice 18 3rd sub-slice ``DEFAULT_MAX_BASELINE_METRICS=256`` because
  event-replay surfaces more granular event-transition records per
  doc-18:114).
* :data:`EVENT_REPLAY_CONFIDENCE_CEILING` -- the per-mode confidence
  ceiling for the typed event-replay mode (0.90; HIGHER than the
  Slice 18 3rd sub-slice ``SUMMARY_REPLAY_CONFIDENCE_CEILING=0.65``
  because event replay carries higher-fidelity typed-event projection
  per doc-18:114 vs doc-18:113).
* :class:`EventReplayInputs` -- the typed bundle of all inputs the
  engine consumes (Slice 18 1st sub-slice :class:`ReplayCorpus` +
  :class:`CounterfactualScenario` + Slice 00-12 typed event-transition
  list refs + optional Slice 15 :class:`GovernanceMetricValue`
  baseline list for hybrid delta projection + Slice 18 1st sub-slice
  :data:`ReplayMode`).
* :class:`EventReplayResult` -- the typed bundle of all outputs the
  engine produces (Slice 18 1st sub-slice :class:`CounterfactualResult`
  + optional :class:`EventReplayGap` list + idempotency key).
* :class:`EventReplayGap` -- the typed gap projection emitted when
  the engine fails structurally (NEVER raises per
  ``feedback_no_silent_degradation``).
* :class:`CounterfactualEventReplayEngine` -- the engine class with a
  single :meth:`replay` method (READ-ONLY by structural design; no
  consumer-side mutation methods).
* :func:`compute_event_replay_idempotency_key` -- the deterministic
  SHA-256 idempotency-key helper (mirrors the Slice 18 3rd sub-slice
  :func:`compute_summary_replay_idempotency_key` canonical-JSON +
  SHA-256 discipline verbatim).

**Activation-authority boundary (per doc-18:123-125 + doc-18:164 AC3 +
STATUS.md § "Loop discipline").** Counterfactual replay results are
review / governance artifacts only -- never runtime policy authority.
The engine exposes **only** the :meth:`replay` read method; it does
NOT expose any mutation surface, does NOT import any consumer-side
module (dispatcher / scheduler / merge queue / supervisor / dashboard
/ failure router runtime / commit_provenance writer / etc.), and does
NOT write any ``dag-*`` execution-authority artifact key. The
structural test surface enforces this discipline by inspecting the
module's import graph + the class's public method list.

**Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
doc-18:186-249).** The engine consumes the typed Slice 00-12 event-
transition shapes refs-only -- no raw artifact bodies are hydrated.
The emitted
:attr:`CounterfactualResult.policy_provenance_refs` field is the list
of Slice 13a typed :class:`GovernanceEvidenceRef` mirrored from the
typed failure transitions' :attr:`evidence_refs` (where each evidence
ref is built from the typed :class:`EvidenceRef` carried on the
:class:`TypedFailureSummary` shape).

**Refs-only invariant (doc-18:186-249).** The engine never hydrates
raw artifact bodies; the typed Slice 00-12 event-transition shapes
carry only summary fields (ids / digests / counts / bounded samples /
citations) per the Slice 10a invariant
(:mod:`iriai_build_v2.workflows.develop.execution.snapshots` § "All
payloads are SUMMARY-ONLY"). The emitted
:class:`CounterfactualResult.policy_provenance_refs` field is the
list of Slice 13a typed :class:`GovernanceEvidenceRef` mirrored from
the typed event-transition refs.

**Implementation discipline.** Stdlib (``hashlib`` + ``json`` +
``datetime``) + Pydantic v2 + Slice 13a typed governance models +
Slice 15 governance metrics + Slice 18 1st sub-slice typed shapes +
Slice 00-12 typed snapshots only. NO imports from ``governance/``
outside ``governance.models``. NO imports from other parts of
``execution_control/`` beyond the prior Slice 14 / 15 / 17 / 18 1st
sub-slice modules (this 4th sub-slice is foundational for the future
Slice 18 5th sub-slice metrics comparator + 6th sub-slice result
writer + 7th sub-slice Slice 17 recommendation citation hook). NO
imports from ``workflows/develop/execution/phases/`` / ``supervisor``
/ ``dashboard`` (those would be downstream consumers, not
dependencies). The Slice 00-12 typed snapshot shapes are imported
from :mod:`iriai_build_v2.workflows.develop.execution.snapshots` (the
Slice 10a typed-snapshot contract module, which is a leaf module
already imported by Slice 14 / 15 / 16 / 17 sub-slices through the
Slice 13a governance-model surface).

Per the auto-memory ``feedback_flat_structured_output`` rule the typed
control fields are flat primitives. Per the auto-memory
``feedback_no_silent_degradation`` rule every Pydantic field validates
at construction; unknown values fail closed via Literal range +
``ConfigDict(extra="forbid")``. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 18 3rd sub-slice precedent verbatim without introducing new
abstractions. Per the auto-memory ``feedback_cite_everything`` rule
every BaseModel field carries a per-field doc-18 PIN cite docstring.

**Per-doc-18 acceptance criteria mapping (doc-18:160-168).** The engine
surface exposes the shape that enforces all 5 acceptance criteria:

* **AC1** -- *"Counterfactuals are deterministic, versioned, and
  evidence-backed."* (doc-18:162) -- enforced by
  :func:`compute_event_replay_idempotency_key` (deterministic axis)
  + the typed :attr:`CounterfactualResult.result_version` (versioned
  axis) + the typed :attr:`CounterfactualResult.policy_provenance_refs`
  list of Slice 13a :class:`GovernanceEvidenceRef` (evidence-backed
  axis).
* **AC2** -- *"Every result lists assumptions and validity limits."*
  (doc-18:163) -- enforced by populating
  :attr:`CounterfactualResult.assumptions` + :attr:`validity_limits`
  with the union of the scenario assumptions + event-replay-specific
  validity limits (e.g. ``"event_replay_mode"``,
  ``"low_event_transition_count"``,
  ``"insufficient_failure_transitions"``,
  ``"governance_only_provenance_chain"``).
* **AC3** -- *"Replay cannot mutate live workflow state."* (doc-18:164)
  -- enforced by the engine's read-only structural design (no mutation
  methods on :class:`CounterfactualEventReplayEngine`; no consumer-
  side module imports; no ``dag-*`` authority artifact-key string
  literals).
* **AC4** -- *"Recommendations that affect runtime behavior cite replay
  results or explicitly say more evidence is needed."* (doc-18:165-166)
  -- enforced by emitting a :class:`CounterfactualResult` with a stable
  :attr:`result_id` that the Slice 17 5th sub-slice
  :class:`~iriai_build_v2.execution_control.replay_requirement_hook`
  cross-references via
  :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs`.
* **AC5** -- *"The replay corpus includes both 8ac124d6 evidence and
  Slice 00-12 implementation artifacts."* (doc-18:167-168) -- enforced
  by REUSING the typed Slice 18 1st sub-slice :class:`ReplayCorpus`
  (the 2nd sub-slice loader populated the corpus with the AC5
  coverage; this 4th sub-slice engine consumes the typed corpus
  directly).

**Per-doc-18 edge-case mapping (doc-18:131-146).** The event-replay
engine handles the doc-18 edge cases at the typed projection layer:

* **Missing typed timing** (doc-18:133): the event-replay engine
  treats missing typed event transitions (e.g. zero failure
  transitions) as a lower-fidelity case + lowers the confidence
  toward the summary-replay ceiling.
* **Policy requires evidence not in corpus** (doc-18:134-135): the
  engine emits an invalidated result via the typed
  :attr:`CounterfactualResult.invalidated_by` list populated with
  ``"missing_evidence:<kind>"`` entries (mirrors the Slice 18 2nd
  sub-slice :class:`ScenarioDefinitionBuilder` precedent + Slice 18
  3rd sub-slice :class:`CounterfactualSummaryReplayEngine` precedent)
  + :attr:`recommended_next_step` set to ``"collect_more_evidence"``.
* **Small sample size** (doc-18:138): the engine emits a result with
  a conservative confidence + :attr:`recommended_next_step` set to
  ``"collect_more_evidence"`` + a typed validity-limit entry
  ``"low_event_transition_count"``.
* **Overfit risk** (doc-18:140-146): when the scenario carries the
  safety-guard sentinel + the scenario corpus is ``8ac124d6``-only,
  the engine sets the typed :attr:`CounterfactualResult.safety_guard_class`
  field per doc-18:87 + emits a validity-limit entry
  ``"governance_only_provenance_chain"``.

**Cross-slice REUSE contract.**

* Slice 13a :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  -- imported, NOT redefined.
* Slice 15 :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
  -- imported, NOT redefined.
* Slice 18 1st sub-slice :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
  + :class:`CounterfactualScenario` + :class:`CounterfactualResult` +
  :data:`ReplayMode` + :data:`RiskChange` + :data:`RecommendedNextStep`
  -- imported, NOT redefined.
* Slice 10a typed snapshot shapes
  :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ExecutionAttemptSummary`
  + :class:`GateStatusSummary` + :class:`TypedFailureSummary` +
  :class:`MergeQueueSummary` + :class:`EvidenceRef` -- imported, NOT
  redefined.
* Slice 14 2nd sub-slice ``retry_governance_projection`` NON-blocking
  RouteAction -- REUSED via the failure_router 4 pure-data add points
  (NOT redefined).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
    CounterfactualScenario,
    RecommendedNextStep,
    ReplayCorpus,
    ReplayMode,
    RiskChange,
    compute_counterfactual_idempotency_key,
)
from iriai_build_v2.execution_control.governance_metrics import (
    GovernanceMetricValue,
)
from iriai_build_v2.workflows.develop.execution.snapshots import (
    EvidenceRef,
    ExecutionAttemptSummary,
    GateStatusSummary,
    MergeQueueSummary,
    TypedFailureSummary,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


__all__ = [
    # Typed failure id (doc-18:114 + doc-14:242-243 NON-BLOCKING).
    "EVENT_REPLAY_FAILURE_ID",
    # Bounded-input default (doc-18:150 + Slice 13A invariant
    # bounded-reads discipline).
    "DEFAULT_MAX_EVENT_TRANSITIONS",
    # Event-replay-specific confidence ceiling per the typed
    # `event_replay` mode (doc-18:114; HIGHER than the 3rd sub-slice
    # summary-replay ceiling per doc-18:133 contrast).
    "EVENT_REPLAY_CONFIDENCE_CEILING",
    # Typed engine inputs / result / gap (doc-18:114).
    "EventReplayInputs",
    "EventReplayResult",
    "EventReplayGap",
    # The engine class (doc-18:114).
    "CounterfactualEventReplayEngine",
    # Pure helper for the deterministic idempotency key (mirrors the
    # Slice 18 3rd sub-slice compute_summary_replay_idempotency_key
    # canonical-JSON + SHA-256 discipline verbatim).
    "compute_event_replay_idempotency_key",
]


# --- Typed failure id (doc-18:114 + doc-14:242-243 NON-BLOCKING) ------------


EVENT_REPLAY_FAILURE_ID: Literal["event_replay_failed"] = "event_replay_failed"
"""Doc-18:114 + doc-14:242-243 -- the typed failure id the
:class:`CounterfactualEventReplayEngine` projects onto when an
event-replay step fails structurally.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18
2nd + 3rd sub-slice precedent verbatim).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 18 pattern matches the Slice 14 + Slice 15 + Slice 16 + Slice 17
non-blocking governance projection observer (the event-replay engine
is also a post-checkpoint governance projection observer).

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to event-replay
failures (this slice is also a post-checkpoint governance projection
observer + per doc-18:123 replay results are review/governance
artifacts only -- never runtime policy authority).
"""


# --- Default bounded-input threshold ----------------------------------------


DEFAULT_MAX_EVENT_TRANSITIONS: int = 512
"""Doc-18:150 + Slice 13A invariant bounded-reads discipline -- the
default upper bound on the COMBINED number of typed event-transition
records (attempt + gate + failure + queue + checkpoint) a single
:class:`EventReplayInputs` may carry.

Per the governance prompt § "Bounded reads" *"Reuse the typed
snapshot's `LIMIT cap+1` truncation discipline and the supervisor's
`SET LOCAL statement_timeout` pattern."* the engine rejects inputs
that exceed the bound (typed gap projection; NEVER raises). The
default value 512 is deliberately HIGHER than the Slice 18 3rd
sub-slice :data:`~iriai_build_v2.execution_control.counterfactual_summary_replay.DEFAULT_MAX_BASELINE_METRICS`
(256) because event-replay surfaces more granular event-transition
records than summary-replay surfaces metric medians -- the 5
event-transition lists each can carry up to roughly the Slice 10a
:data:`~iriai_build_v2.workflows.develop.execution.snapshots.SnapshotBudget.max_attempts`
+ :data:`max_gate_results` + :data:`max_failures` + :data:`max_merge_items`
+ :data:`max_evidence_refs` budgets (20 + 40 + 40 + 40 + 80 = 220);
512 leaves headroom for callers that stitch multiple snapshot windows
together. The caller MAY override via
:attr:`EventReplayInputs.max_event_transitions` (e.g. a large
historical-replay caller may raise to 2048).

Per doc-18:150 § Tests *"Replay corpus loader rejects malformed or
unbounded fixture inputs."* the bound applies symmetrically to the
event-replay engine (the engine is also a typed-input consumer).
"""


# --- Event-replay confidence ceiling ----------------------------------------


EVENT_REPLAY_CONFIDENCE_CEILING: float = 0.90
"""Doc-18:114 + doc-18:133 (contrast) -- the per-mode confidence
ceiling for the event-replay engine.

Per doc-18:114 *"Add event replay where typed attempt, gate, failure,
queue, and checkpoint transitions are available."* the event-replay
engine carries a HIGHER confidence ceiling than the Slice 18 3rd
sub-slice
:data:`~iriai_build_v2.execution_control.counterfactual_summary_replay.SUMMARY_REPLAY_CONFIDENCE_CEILING`
(0.65) -- because the typed event-transition projection is higher-
fidelity than the typed metric-median summary-replay projection.

The ceiling 0.90 reflects the doc-18:114 vs doc-18:133 contrast:

* **Summary replay** (Slice 18 3rd sub-slice; doc-18:113) -- typed
  baseline metric medians; lower-fidelity; ceiling 0.65.
* **Event replay** (Slice 18 4th sub-slice; doc-18:114) -- typed
  attempt + gate + failure + queue + checkpoint transitions; higher-
  fidelity; ceiling 0.90.

The ceiling is INTENTIONALLY NOT 1.0 because per doc-18:50
*"Counterfactual duration estimates may be ranges rather than exact
values."* the typed-shape layer carries the central estimate while
the confidence carries the breadth; even with full typed-event
fidelity the central estimate is a counterfactual projection (NOT a
direct measurement) so confidence stays at or below 0.90.

Per doc-18:138 *"Small sample size: report confidence and avoid
policy recommendations."* the engine further reduces confidence below
this ceiling when the event-transition sample size is small
(heuristic: if the failure-transition count is < 3, the emitted
confidence is multiplied by 0.5 -- a defensive floor mirroring the
Slice 18 3rd sub-slice
:class:`CounterfactualSummaryReplayEngine._project_confidence`
discipline).

Per doc-18:140-141 *"Overfit risk: require at least one non-`8ac124d6`
corpus before marking a general policy high confidence."* the engine
further reduces confidence when the corpus's feature_ids list is
``["8ac124d6"]``-only (multiplied by 0.5).
"""


# --- EventReplayInputs (typed inputs; doc-18:114) ---------------------------


class EventReplayInputs(BaseModel):
    """Doc-18:114 step 4 -- typed bundle of all inputs the
    :class:`CounterfactualEventReplayEngine` consumes.

    The bundle composes:

    * ``corpus`` -- the typed Slice 18 1st sub-slice
      :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
      (per doc-18:63-69; populated by the Slice 18 2nd sub-slice
      :class:`~iriai_build_v2.execution_control.counterfactual_replay_loader.ReplayCorpusLoader`).
    * ``scenario`` -- the typed Slice 18 1st sub-slice
      :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualScenario`
      (per doc-18:71-77; populated by the Slice 18 2nd sub-slice
      :class:`~iriai_build_v2.execution_control.counterfactual_replay_loader.ScenarioDefinitionBuilder`).
    * ``attempt_transitions`` -- the typed list of Slice 10a
      :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ExecutionAttemptSummary`
      attempt-transition records (per doc-18:114 step 4; refs-only
      per the Slice 10a *"All payloads are SUMMARY-ONLY"* invariant).
    * ``gate_transitions`` -- the typed list of Slice 10a
      :class:`GateStatusSummary` gate-transition records (per
      doc-18:114 step 4).
    * ``failure_transitions`` -- the typed list of Slice 10a
      :class:`TypedFailureSummary` failure-transition records (per
      doc-18:114 step 4).
    * ``queue_transitions`` -- the typed list of Slice 10a
      :class:`MergeQueueSummary` queue-transition records (per
      doc-18:114 step 4).
    * ``checkpoint_transitions`` -- the typed list of Slice 10a
      :class:`EvidenceRef` checkpoint-reference records (per
      doc-18:114 step 4; the Slice 10a
      :attr:`ControlPlaneSnapshot.checkpoints` field is `list[EvidenceRef]`).
    * ``baseline_metrics`` -- the OPTIONAL list of Slice 15 typed
      :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
      baseline records for hybrid delta projection (when supplied,
      the engine layers the event-replay typed-transition projection
      on top of the metric-median projection for a higher-fidelity
      composite; when None, the engine projects from the typed
      transitions alone).
    * ``mode`` -- the typed
      :data:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayMode`
      classification (per doc-18:61; defaults to ``"event_replay"``
      because this 4th sub-slice owns the typed event-replay surface).
    * ``result_version`` -- the typed result-version string (per
      doc-18:81).
    * ``result_id`` -- the typed stable result identifier string (per
      doc-18:80).
    * ``max_event_transitions`` -- the typed bounded-input cap for the
      combined event-transition count; defaults to
      :data:`DEFAULT_MAX_EVENT_TRANSITIONS`. Per doc-18:150 + Slice
      13A invariant bounded-reads discipline.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.

    Mirrors :class:`~iriai_build_v2.execution_control.counterfactual_summary_replay.SummaryReplayInputs`
    (Slice 18 3rd sub-slice) verbatim per the chunk-shape decision.
    """

    # ``extra="forbid"`` aligns with the Slice 18 3rd sub-slice
    # precedent at
    # src/iriai_build_v2/execution_control/counterfactual_summary_replay.py:376
    # + the Slice 18 2nd sub-slice precedent at
    # src/iriai_build_v2/execution_control/counterfactual_replay_loader.py:343
    # + the Slice 18 1st sub-slice precedent at
    # src/iriai_build_v2/execution_control/counterfactual_replay.py:418
    # -- unknown fields fail closed as a typed ``ValidationError``
    # rather than being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    corpus: ReplayCorpus
    """Doc-18:63-69 -- the typed Slice 18 1st sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
    the engine projects against. Per doc-18:167-168 AC5 the corpus
    must include both ``8ac124d6`` evidence AND Slice 00-12
    implementation artifacts; the engine consumes the typed corpus
    directly (the 2nd sub-slice loader enforces the AC5 coverage)."""

    scenario: CounterfactualScenario
    """Doc-18:71-77 -- the typed Slice 18 1st sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualScenario`
    the engine evaluates. Per doc-18:127-129 *"Historical replay is
    immutable by corpus id and scenario id. New assumptions require a
    new result version."* the scenario id is the typed identity
    surface paired with :attr:`ReplayCorpus.corpus_id`."""

    attempt_transitions: list[ExecutionAttemptSummary] = Field(default_factory=list)
    """Doc-18:114 step 4 + Slice 10a typed snapshot REUSE -- the typed
    list of attempt-transition records the engine consumes.

    **Slice 10a dependency reconciliation.** The
    :class:`ExecutionAttemptSummary` shape is REUSED from
    :mod:`iriai_build_v2.workflows.develop.execution.snapshots` (NOT
    redefined here). Per the Slice 10a *"All payloads are
    SUMMARY-ONLY"* invariant the typed shape carries only summary
    fields (ids / digests / counts / timestamps / statuses / routes /
    citations) -- no artifact bodies, no raw prompts, no stdout/stderr
    bodies.

    The engine projects the typed transitions onto the emitted
    :attr:`CounterfactualResult.estimated_delta_*` fields by counting
    transition signatures (e.g. attempt statuses that ``failed`` or
    were ``cancelled``) and weighing them by the scenario's typed
    ``expected_*_delta_ratio`` policy keys."""

    gate_transitions: list[GateStatusSummary] = Field(default_factory=list)
    """Doc-18:114 step 4 + Slice 10a typed snapshot REUSE -- the typed
    list of gate-transition records the engine consumes.

    **Slice 10a dependency reconciliation.** The
    :class:`GateStatusSummary` shape is REUSED from
    :mod:`iriai_build_v2.workflows.develop.execution.snapshots` (NOT
    redefined here). Per the Slice 10a *"reject checkpoint display
    when gate evidence is missing"* test (doc-10 § Tests) the typed
    gate shape carries the typed :attr:`GateStatusSummary.evidence_id`
    citation for the gate-evidence node (the per-gate raw body lives
    behind a separate bounded detail endpoint)."""

    failure_transitions: list[TypedFailureSummary] = Field(default_factory=list)
    """Doc-18:114 step 4 + Slice 10a typed snapshot REUSE -- the typed
    list of failure-transition records the engine consumes.

    **Slice 10a dependency reconciliation.** The
    :class:`TypedFailureSummary` shape is REUSED from
    :mod:`iriai_build_v2.workflows.develop.execution.snapshots` (NOT
    redefined here). Per Slice 10a the typed shape carries the
    Slice 07 typed :attr:`failure_class` / :attr:`failure_type` /
    :attr:`route` taxonomies + a list of typed
    :class:`~iriai_build_v2.workflows.develop.execution.snapshots.EvidenceRef`
    citations -- no artifact bodies.

    The engine surfaces the typed failure-transition evidence_refs
    onto the emitted
    :attr:`CounterfactualResult.policy_provenance_refs` list (built
    into typed Slice 13a
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    records) per the Slice 13A refs-only invariant."""

    queue_transitions: list[MergeQueueSummary] = Field(default_factory=list)
    """Doc-18:114 step 4 + Slice 10a typed snapshot REUSE -- the typed
    list of queue-transition records the engine consumes.

    **Slice 10a dependency reconciliation.** The
    :class:`MergeQueueSummary` shape is REUSED from
    :mod:`iriai_build_v2.workflows.develop.execution.snapshots` (NOT
    redefined here). Per Slice 10a the typed shape carries the
    Slice 08 typed :attr:`MergeQueueStatus` state machine + the
    :attr:`failure_id` typed reference back to the failure-transitions
    list + the :attr:`required_gate_evidence_ids` typed ints."""

    checkpoint_transitions: list[EvidenceRef] = Field(default_factory=list)
    """Doc-18:114 step 4 + Slice 10a typed snapshot REUSE -- the typed
    list of checkpoint-reference records the engine consumes.

    **Slice 10a dependency reconciliation.** The :class:`EvidenceRef`
    shape is REUSED from
    :mod:`iriai_build_v2.workflows.develop.execution.snapshots` (NOT
    redefined here). Per the Slice 10a
    :attr:`ControlPlaneSnapshot.checkpoints: list[EvidenceRef]` shape
    the typed checkpoint reference carries only the typed
    :attr:`EvidenceRef.id` + :attr:`citation` + :attr:`kind` fields
    -- no checkpoint body is hydrated."""

    baseline_metrics: list[GovernanceMetricValue] = Field(default_factory=list)
    """Doc-15:78-88 + doc-18:114 + doc-18:115-116 -- the OPTIONAL list
    of Slice 15 typed :class:`GovernanceMetricValue` baseline records
    the engine projects deltas against when typed event transitions
    alone are insufficient.

    When supplied the engine layers the event-replay typed-transition
    projection on top of the metric-median projection for a higher-
    fidelity composite; when empty, the engine projects from the
    typed transitions alone.

    **Slice 13a + Slice 15 dependency reconciliation
    (doc-13a:285-287 step 9; doc-18:186-249).** The
    :class:`GovernanceMetricValue` shape is REUSED from
    :mod:`iriai_build_v2.execution_control.governance_metrics` (NOT
    redefined here). Each baseline metric in turn carries a list of
    Slice 13a typed :class:`GovernanceEvidenceRef` on its
    :attr:`GovernanceMetricValue.evidence_refs` field; the engine
    surfaces the union of those refs onto the emitted
    :attr:`CounterfactualResult.policy_provenance_refs` (refs-only;
    no raw artifact body hydration)."""

    mode: ReplayMode = "event_replay"
    """Doc-18:61 -- the typed replay-mode classification from the
    3-value :data:`ReplayMode` Literal. Defaults to
    ``"event_replay"`` because this 4th sub-slice owns the typed
    event-replay surface (the 3rd sub-slice owns ``"summary_replay"``;
    the typed ``"hybrid"`` mode is composed at a future sub-slice).

    Per Pydantic Literal validation the field accepts only one of the
    3 values; unknown values fail closed with a typed
    ``ValidationError``."""

    result_version: str = "v1"
    """Doc-18:81 -- the typed result-version string. Per doc-18:128-129
    *"New assumptions require a new result version."* the version axis
    lets future sub-slices supersede prior results without rewriting
    them. Defaults to ``"v1"`` because this 4th sub-slice is the first
    event-replay-engine version."""

    result_id: str
    """Doc-18:80 -- the typed stable result identifier string. Per the
    AC4 binding (doc-18:165-166) the result id is the typed cross-
    Slice-17 reference surface; the Slice 17 1st sub-slice
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs`
    field carries this identifier as the by-name reference for
    behavior-changing recommendations.

    Caller is responsible for choosing a stable result id (e.g. the
    scenario id + the result version + the corpus id). The engine
    does NOT generate one because the future Slice 18 6th sub-slice
    result-writer needs control over the identifier discipline."""

    max_event_transitions: int = Field(
        default=DEFAULT_MAX_EVENT_TRANSITIONS,
        ge=1,
    )
    """Doc-18:150 + Slice 13A invariant bounded-reads discipline -- the
    typed bounded-input cap for the COMBINED event-transition count
    (attempt + gate + failure + queue + checkpoint). Defaults to
    :data:`DEFAULT_MAX_EVENT_TRANSITIONS` (512).

    Per the governance prompt § "Bounded reads" the engine rejects
    inputs that exceed the bound (typed gap projection; NEVER raises).
    Must be >= 1 (the Pydantic ``ge=1`` constraint fails closed at
    construction with a typed ``ValidationError`` if the caller passes
    a non-positive bound)."""


# --- EventReplayGap (typed gap projection; doc-18:114 + doc-14:242-243) -----


class EventReplayGap(BaseModel):
    """Typed governance-gap finding produced when the
    :class:`CounterfactualEventReplayEngine` fails to construct a
    :class:`CounterfactualResult` structurally.

    Mirrors the Slice 18 3rd sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_summary_replay.SummaryReplayGap`
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
    typed failure id :data:`EVENT_REPLAY_FAILURE_ID`
    (``event_replay_failed``) registers under the EXISTING
    ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).
    """

    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["event_replay_failed"]
    """Doc-18:114 + doc-14:192-201 -- the typed failure id. Registers
    under the EXISTING ``evidence_corruption`` failure_class with
    NON-blocking routing per doc-14:242-243 + doc-18:114."""

    result_id_attempted: str
    """The :attr:`EventReplayInputs.result_id` the engine attempted
    to compute (so the caller can correlate the gap finding with the
    requesting batch even when the result itself could not be
    constructed)."""

    corpus_id: str
    """The corpus scope of the failed projection (the
    :attr:`EventReplayInputs.corpus.corpus_id`)."""

    scenario_id: str
    """The scenario scope of the failed projection (the
    :attr:`EventReplayInputs.scenario.scenario_id`)."""

    reason: str
    """Free-form gap reason (e.g.
    ``result_construction_failed`` /
    ``event_transitions_exceeded_bound`` /
    ``invalid_replay_mode_for_engine`` /
    ``result_id_empty`` /
    ``all_event_transitions_empty``)."""

    observed_at: datetime
    """ISO-8601 timestamp the engine observed the gap (UTC, timezone-
    aware). Mirrors the Slice 13A
    :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness.observed_at`
    + Slice 14 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding.observed_at`
    + Slice 18 3rd sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_summary_replay.SummaryReplayGap.observed_at`
    contract verbatim."""

    evidence_refs: list[str] = Field(default_factory=list)
    """Optional list of evidence-ref id strings the gap implicates
    (refs-only per the Slice 13A invariant + doc-18:186-249; the typed
    BaseModel form is NOT embedded -- the caller cross-references via
    the typed Slice 13a evidence-ref surface separately)."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding
    (e.g. the Pydantic ValidationError detail, the truncation bound,
    the event-transition count). Free-form per the doc-14:192-201 +
    Slice 14/15/16/17/18-2nd/18-3rd governance-finding precedent."""


# --- EventReplayResult (typed result; doc-18:114) ---------------------------


class EventReplayResult(BaseModel):
    """Doc-18:114 step 4 -- typed bundle of all outputs the
    :class:`CounterfactualEventReplayEngine` produces.

    The bundle composes:

    * ``result`` -- the typed Slice 18 1st sub-slice
      :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
      the engine emitted, OR ``None`` if the projection failed
      structurally (in which case the gap is recorded in
      :attr:`gap_findings`).
    * ``gap_findings`` -- the list of typed :class:`EventReplayGap`
      records emitted when a projection step fails structurally (per
      :data:`EVENT_REPLAY_FAILURE_ID`).
    * ``idempotency_key`` -- the deterministic
      :func:`compute_event_replay_idempotency_key`-derived dedupe
      key.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.
    """

    model_config = ConfigDict(extra="forbid")

    result: CounterfactualResult | None = None
    """The typed Slice 18 1st sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
    the engine emitted, OR ``None`` if the projection failed
    structurally.

    Per the doc-18:114 step 4 contract (*"Add event replay where typed
    attempt, gate, failure, queue, and checkpoint transitions are
    available."*) the engine emits the typed result when inputs are
    valid + the typed projection succeeds; on structural failure the
    result is ``None`` + the gap finding is recorded in
    :attr:`gap_findings`."""

    gap_findings: list[EventReplayGap] = Field(default_factory=list)
    """The list of typed :class:`EventReplayGap` records emitted
    when a projection step fails structurally (per
    :data:`EVENT_REPLAY_FAILURE_ID`).

    Per the auto-memory ``feedback_no_silent_degradation`` rule the
    engine NEVER raises on input -- structural failures are recorded
    as typed gap findings (refs-only; the result id attempted +
    corpus id + scenario id + failure reason + observed timestamp +
    optional evidence-ref ids)."""

    idempotency_key: str
    """The deterministic
    :func:`compute_event_replay_idempotency_key`-derived dedupe
    key. Per doc-18:127-129 *"Historical replay is immutable by
    corpus id and scenario id. New assumptions require a new result
    version."* the idempotency key is the typed identity surface that
    lets subsequent re-runs of the engine against the same inputs
    produce byte-identical results."""


# --- Pure canonical-JSON + SHA-256 helpers (mirrors Slice 18 1st + 2nd + 3rd
#     sub-slice canonical-JSON + SHA-256 discipline verbatim) ----------------


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Mirrors :func:`iriai_build_v2.execution_control.counterfactual_replay._canonical_json`
    + :func:`iriai_build_v2.execution_control.counterfactual_replay_loader._canonical_json`
    + :func:`iriai_build_v2.execution_control.counterfactual_summary_replay._canonical_json`
    + :func:`iriai_build_v2.execution_control.recommendation_builder._canonical_json`
    + :func:`iriai_build_v2.execution_control.finding_engine._canonical_json`
    + :func:`iriai_build_v2.execution_control.policy_recommendation._canonical_json`
    verbatim: ``json.dumps(..., sort_keys=True, separators=(",", ":"))``.

    Per the P3-15-1-1 carry the ``default=str`` superset is benign
    because the canonical projections this module computes go through
    :meth:`BaseModel.model_dump` with ``mode='json'`` first, so
    ``datetime`` is already lowered to ISO-8601 strings before this
    helper sees the object; the ``default=str`` is a defence-in-depth
    fallback for any non-JSON-native scalars (e.g. ``Path`` objects in
    test fixtures).
    """

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_hex(payload: str) -> str:
    """Return the SHA-256 hex digest of ``payload`` (UTF-8 encoded).

    Mirrors :func:`iriai_build_v2.execution_control.counterfactual_replay._sha256_hex`
    + :func:`iriai_build_v2.execution_control.counterfactual_replay_loader._sha256_hex`
    + :func:`iriai_build_v2.execution_control.counterfactual_summary_replay._sha256_hex`
    verbatim. Stdlib-only per the implementer prompt § "Non-negotiables"
    (``hashlib`` + ``json`` only).
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_event_replay_idempotency_key(
    *,
    result_id: str,
    result_version: str,
    corpus_id: str,
    scenario_id: str,
    mode: ReplayMode,
    attempt_transition_count: int,
    gate_transition_count: int,
    failure_transition_count: int,
    queue_transition_count: int,
    checkpoint_transition_count: int,
    assumptions: list[str],
    validity_limits: list[str],
) -> str:
    """Compute the deterministic SHA-256-derived idempotency key for an
    event-replay result.

    Mirrors the Slice 18 3rd sub-slice
    :func:`~iriai_build_v2.execution_control.counterfactual_summary_replay.compute_summary_replay_idempotency_key`
    canonical-JSON + SHA-256 discipline verbatim; the key is computed
    over the 12 logical inputs:

    * ``result_id`` -- the typed stable result identifier string (per
      doc-18:80).
    * ``result_version`` -- the typed result-version string (per
      doc-18:81 + doc-18:128-129 *"New assumptions require a new
      result version."* the version axis is part of the dedupe key so
      a new version cleanly produces a new key + a new row, rather
      than overwriting prior rows).
    * ``corpus_id`` -- the corpus identifier (per doc-18:64 +
      doc-18:83 + doc-18:127-129).
    * ``scenario_id`` -- the scenario identifier (per doc-18:72 +
      doc-18:82 + doc-18:127-129).
    * ``mode`` -- the typed
      :data:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayMode`
      classification (per doc-18:61). The mode is part of the dedupe
      key so a future re-run of the same (corpus, scenario) under a
      different mode cleanly produces a new key.
    * ``attempt_transition_count`` / ``gate_transition_count`` /
      ``failure_transition_count`` / ``queue_transition_count`` /
      ``checkpoint_transition_count`` -- the typed counts of each
      event-transition list. The counts are part of the dedupe key
      so a re-run with a longer event-transition window cleanly
      produces a new key (the typed event-transition data itself is
      refs-only per the Slice 13A invariant; the counts are the
      ref-only summary surface for the idempotency-key projection).
    * ``assumptions`` -- the list of assumption strings (per
      doc-18:77 + doc-18:84). Sorted before digesting (order-
      invariant).
    * ``validity_limits`` -- the list of validity-limit strings (per
      doc-18:69 + doc-18:85). Sorted before digesting (order-
      invariant).

    Per doc-18:128-129 *"Historical replay is immutable by corpus id
    and scenario id. New assumptions require a new result version."*
    the helper is the cross-process freshness contract subsequent
    sub-slices rely on when detecting duplicate results across re-runs
    of the engine.
    """

    payload: dict[str, Any] = {
        "result_id": result_id,
        "result_version": result_version,
        "corpus_id": corpus_id,
        "scenario_id": scenario_id,
        "mode": mode,
        "attempt_transition_count": int(attempt_transition_count),
        "gate_transition_count": int(gate_transition_count),
        "failure_transition_count": int(failure_transition_count),
        "queue_transition_count": int(queue_transition_count),
        "checkpoint_transition_count": int(checkpoint_transition_count),
        # Sort the list-of-str inputs so the key is order-invariant
        # w.r.t. list ordering (per the Slice 18 1st/2nd/3rd sub-slice
        # canonical-JSON discipline).
        "assumptions": sorted(assumptions),
        "validity_limits": sorted(validity_limits),
    }
    return _sha256_hex(_canonical_json(payload))


# --- _utcnow helper (wrapped for monkeypatch testing) ----------------------


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware
    :class:`datetime`.

    Wrapped in a helper so the test surface can monkeypatch a fixed
    clock for deterministic gap-finding ``observed_at`` assertions.
    """

    return datetime.now(timezone.utc)


# --- The engine class (doc-18:114) -----------------------------------------


class CounterfactualEventReplayEngine:
    """Counterfactual event replay engine (doc-18:114 step 4).

    Per *"Add event replay where typed attempt, gate, failure, queue,
    and checkpoint transitions are available."* the engine consumes
    typed Slice 18 1st sub-slice :class:`ReplayCorpus` +
    :class:`CounterfactualScenario` + typed Slice 00-12 event-
    transition shapes (Slice 10a
    :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ExecutionAttemptSummary`
    + :class:`GateStatusSummary` + :class:`TypedFailureSummary` +
    :class:`MergeQueueSummary` + :class:`EvidenceRef` checkpoints) +
    optional Slice 15 typed :class:`GovernanceMetricValue` baseline
    list (when supplied, the engine layers the event-replay typed-
    transition projection on top of the metric-median projection for
    a higher-fidelity composite) + emits a typed Slice 18 1st sub-
    slice :class:`CounterfactualResult` with all 16 fields populated.

    **Refs-only projection (doc-18:186-249 + Slice 13A invariant).**
    The engine surfaces only typed Slice 13a
    :class:`GovernanceEvidenceRef` records from the typed failure-
    transitions' evidence_refs + the optional baseline metrics'
    evidence_refs onto the emitted
    :attr:`CounterfactualResult.policy_provenance_refs` list (the
    typed BaseModel form is preserved -- doc-18:86 is documented as
    ``list[str]`` but the 1st sub-slice tightened to the typed Slice
    13a ref shape per the implementer-prompt typed-REUSE binding). NO
    raw artifact body hydration.

    **Fail-closed discipline (feedback_no_silent_degradation).** The
    engine NEVER raises a failure to the caller. Any structural
    failure projects onto a typed :class:`EventReplayGap` finding
    emitted on the :attr:`EventReplayResult.gap_findings` list. The
    corresponding typed failure id :data:`EVENT_REPLAY_FAILURE_ID`
    (``event_replay_failed``) registers under the EXISTING
    ``evidence_corruption`` failure_class with the EXISTING NON-
    blocking RouteAction ``retry_governance_projection`` (REUSED from
    Slice 14 2nd sub-slice; NOT a new route action).

    **Activation-authority boundary (doc-18:123-125 + doc-18:164 AC3).**
    The engine exposes ONLY the :meth:`replay` read method; it does
    NOT expose any mutation surface. The structural test surface
    enforces this discipline by inspecting the class's public method
    list. Replay results are review / governance artifacts only;
    never runtime policy authority.

    **Per-axis projection from typed event transitions
    (doc-18:79-96).** The engine projects the four typed delta fields
    per the doc-18:88-90 + 91 surface:

    * :attr:`CounterfactualResult.estimated_delta_hours` -- the
      estimated workflow hours delta. Computed from the typed
      attempt-transition durations (`finished_at - started_at` for
      `failed`/`cancelled`/`incomplete` attempts) scaled by the
      scenario's policy_under_test "expected_hours_delta_ratio" key;
      otherwise ``None`` when no attempt-transition data is present.
    * :attr:`CounterfactualResult.estimated_delta_repair_cycles` --
      the estimated repair-cycle delta. Computed from the typed
      attempt-transition count where `attempt_kind == "repair"`,
      scaled by the scenario's policy_under_test
      "expected_repair_cycles_delta_ratio" key.
    * :attr:`CounterfactualResult.estimated_delta_commit_failures` --
      the estimated commit-failure delta. Computed from the typed
      failure-transition count where `failure_class in
      {"commit", "merge"}` or `failure_type` carries "commit"
      substring, scaled by the scenario's policy_under_test
      "expected_commit_failures_delta_ratio" key.
    * :attr:`CounterfactualResult.estimated_risk_change` -- the
      typed
      :data:`~iriai_build_v2.execution_control.counterfactual_replay.RiskChange`
      Literal. The event-replay engine emits ``"unknown"`` when ALL of
      the typed delta fields are ``None`` (no event-transition data
      AND no baseline metrics) + ``"lower"`` / ``"same"`` / ``"higher"``
      heuristics based on the typed scenario's safety_guard_class +
      the projected delta signs (mirrors the Slice 18 3rd sub-slice
      summary-replay engine).

    Per doc-18:50 *"Counterfactual duration estimates may be ranges
    rather than exact values."* the event-replay engine treats the
    central estimate as the median of the typed event-transition
    derived signals; the breadth is reflected in the typed
    :attr:`CounterfactualResult.confidence` field (HIGHER confidence
    ceiling for event replay per doc-18:114 vs doc-18:133 contrast).

    The engine is stateless (no instance state beyond construction);
    callers MAY reuse a single instance across multiple
    (corpus, scenario) pairs.
    """

    def replay(
        self,
        inputs: EventReplayInputs,
    ) -> EventReplayResult:
        """Run the typed event-replay projection for the typed
        inputs.

        Per doc-18:114 step 4 the method:

        1. Validates the bounded-input contract
           (:attr:`EventReplayInputs.max_event_transitions`).
        2. Validates the typed required-field contract (``result_id``
           non-empty; at least one event-transition list non-empty;
           ``mode`` is ``"event_replay"`` since this engine owns the
           typed event-replay surface).
        3. Computes the deterministic idempotency key.
        4. Projects the typed delta fields per doc-18:79-96 (hours /
           repair cycles / commit failures / risk change) from the
           typed event transitions, optionally composed with baseline
           metrics for a higher-fidelity composite.
        5. Computes the typed confidence per doc-18:50 + doc-18:114 +
           doc-18:138.
        6. Composes the typed assumptions + validity_limits lists per
           doc-18:84-85 (union of scenario assumptions + event-
           replay-specific validity limits like
           ``"event_replay_mode"`` /
           ``"low_event_transition_count"`` /
           ``"insufficient_failure_transitions"``).
        7. Emits the typed :class:`CounterfactualResult` with all 16
           fields populated.
        8. Records projection failures in
           :attr:`EventReplayResult.gap_findings` per
           ``feedback_no_silent_degradation``.

        Per the auto-memory ``feedback_no_silent_degradation`` rule
        the method NEVER raises on input -- structural failures
        produce typed gap findings.
        """

        gap_findings: list[EventReplayGap] = []
        idempotency_key = compute_event_replay_idempotency_key(
            result_id=inputs.result_id,
            result_version=inputs.result_version,
            corpus_id=inputs.corpus.corpus_id,
            scenario_id=inputs.scenario.scenario_id,
            mode=inputs.mode,
            attempt_transition_count=len(inputs.attempt_transitions),
            gate_transition_count=len(inputs.gate_transitions),
            failure_transition_count=len(inputs.failure_transitions),
            queue_transition_count=len(inputs.queue_transitions),
            checkpoint_transition_count=len(inputs.checkpoint_transitions),
            assumptions=inputs.scenario.assumptions,
            validity_limits=inputs.corpus.validity_limits,
        )

        total_transitions = (
            len(inputs.attempt_transitions)
            + len(inputs.gate_transitions)
            + len(inputs.failure_transitions)
            + len(inputs.queue_transitions)
            + len(inputs.checkpoint_transitions)
        )

        # Bounded-input check (per doc-18:150 + Slice 13A invariant
        # bounded-reads discipline).
        if total_transitions > inputs.max_event_transitions:
            gap_findings.append(
                EventReplayGap(
                    failure_id=EVENT_REPLAY_FAILURE_ID,
                    result_id_attempted=inputs.result_id,
                    corpus_id=inputs.corpus.corpus_id,
                    scenario_id=inputs.scenario.scenario_id,
                    reason="event_transitions_exceeded_bound",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "received_count": total_transitions,
                        "max_bound": inputs.max_event_transitions,
                        "attempt_count": len(inputs.attempt_transitions),
                        "gate_count": len(inputs.gate_transitions),
                        "failure_count": len(inputs.failure_transitions),
                        "queue_count": len(inputs.queue_transitions),
                        "checkpoint_count": len(inputs.checkpoint_transitions),
                    },
                )
            )
            return EventReplayResult(
                result=None,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        # Required-field check (per doc-18:80 + doc-18:114).
        if not inputs.result_id or not inputs.result_id.strip():
            gap_findings.append(
                EventReplayGap(
                    failure_id=EVENT_REPLAY_FAILURE_ID,
                    result_id_attempted=inputs.result_id or "<empty>",
                    corpus_id=inputs.corpus.corpus_id,
                    scenario_id=inputs.scenario.scenario_id,
                    reason="result_id_empty",
                    observed_at=_utcnow(),
                )
            )
            return EventReplayResult(
                result=None,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        # All-empty check (per doc-18:114 + doc-18:133): at least one
        # event-transition source OR baseline metrics must be present.
        if total_transitions == 0 and not inputs.baseline_metrics:
            gap_findings.append(
                EventReplayGap(
                    failure_id=EVENT_REPLAY_FAILURE_ID,
                    result_id_attempted=inputs.result_id,
                    corpus_id=inputs.corpus.corpus_id,
                    scenario_id=inputs.scenario.scenario_id,
                    reason="all_event_transitions_empty",
                    observed_at=_utcnow(),
                )
            )
            return EventReplayResult(
                result=None,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        # Mode check (per doc-18:61 + doc-18:114). This 4th sub-slice
        # engine ONLY handles the typed event-replay mode; the 3rd
        # sub-slice engine handles ``summary_replay``; the typed
        # ``hybrid`` mode is composed at a future sub-slice.
        if inputs.mode != "event_replay":
            gap_findings.append(
                EventReplayGap(
                    failure_id=EVENT_REPLAY_FAILURE_ID,
                    result_id_attempted=inputs.result_id,
                    corpus_id=inputs.corpus.corpus_id,
                    scenario_id=inputs.scenario.scenario_id,
                    reason="invalid_replay_mode_for_engine",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "received_mode": inputs.mode,
                        "expected_mode": "event_replay",
                    },
                )
            )
            return EventReplayResult(
                result=None,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        # Project the typed delta fields per doc-18:79-96.
        estimated_delta_hours = self._project_delta_hours(inputs)
        estimated_delta_repair_cycles = self._project_delta_repair_cycles(inputs)
        estimated_delta_commit_failures = self._project_delta_commit_failures(inputs)
        estimated_risk_change = self._project_risk_change(
            inputs,
            estimated_delta_hours=estimated_delta_hours,
            estimated_delta_repair_cycles=estimated_delta_repair_cycles,
            estimated_delta_commit_failures=estimated_delta_commit_failures,
        )

        # Compute the typed confidence per doc-18:50 + doc-18:114 +
        # doc-18:138.
        confidence = self._project_confidence(inputs)

        # Compose the typed assumptions + validity_limits lists per
        # doc-18:84-85.
        composed_assumptions = self._compose_assumptions(inputs)
        composed_validity_limits = self._compose_validity_limits(
            inputs,
            estimated_delta_hours=estimated_delta_hours,
        )

        # Detect missing required evidence per doc-18:134-135.
        invalidated_by = self._compute_invalidated_by(inputs)

        # Pick the typed recommended-next-step per doc-18:95 +
        # doc-18:134-135 + doc-18:138.
        recommended_next_step = self._project_recommended_next_step(
            inputs,
            invalidated_by=invalidated_by,
            confidence=confidence,
        )

        # Project the typed safety-guard-class field per doc-18:87 +
        # doc-18:140-146.
        safety_guard_class = self._project_safety_guard_class(inputs)

        # Surface the typed Slice 13a evidence refs from the typed
        # failure-transitions + baseline metrics onto the emitted
        # result's typed policy_provenance_refs list (refs-only).
        policy_provenance_refs = self._collect_policy_provenance_refs(inputs)

        # Construct the typed CounterfactualResult (per doc-18:79-96).
        try:
            result = CounterfactualResult(
                result_id=inputs.result_id,
                result_version=inputs.result_version,
                scenario_id=inputs.scenario.scenario_id,
                corpus_id=inputs.corpus.corpus_id,
                assumptions=composed_assumptions,
                validity_limits=composed_validity_limits,
                policy_provenance_refs=policy_provenance_refs,
                safety_guard_class=safety_guard_class,
                estimated_delta_hours=estimated_delta_hours,
                estimated_delta_repair_cycles=estimated_delta_repair_cycles,
                estimated_delta_commit_failures=estimated_delta_commit_failures,
                estimated_risk_change=estimated_risk_change,
                confidence=confidence,
                invalidated_by=invalidated_by,
                supporting_finding_ids=[],
                recommended_next_step=recommended_next_step,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            gap_findings.append(
                EventReplayGap(
                    failure_id=EVENT_REPLAY_FAILURE_ID,
                    result_id_attempted=inputs.result_id,
                    corpus_id=inputs.corpus.corpus_id,
                    scenario_id=inputs.scenario.scenario_id,
                    reason="result_construction_failed",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                    },
                )
            )
            return EventReplayResult(
                result=None,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        return EventReplayResult(
            result=result,
            gap_findings=gap_findings,
            idempotency_key=idempotency_key,
        )

    # --- per-axis projection helpers (per doc-18:79-96) ----------------------

    # The _project_delta_* + _project_risk_change + _project_confidence
    # + _compose_assumptions + _compose_validity_limits +
    # _compute_invalidated_by + _project_recommended_next_step +
    # _project_safety_guard_class + _collect_policy_provenance_refs helpers
    # below are leading-underscore (private) methods; the
    # `test_no_mutation_methods` structural test inspects ONLY public
    # method names so these are exempt from the AC3 mutation-prefix
    # check by design.

    def _project_delta_hours(self, inputs: EventReplayInputs) -> float | None:
        """Project the doc-18:88 estimated_delta_hours from the typed
        event-transitions (+ optional baseline metrics fallback).

        The event-replay heuristic: the sum of typed attempt-transition
        durations (`(finished_at - started_at)` for failed / cancelled
        / incomplete attempts; converted to hours) scaled by the
        scenario's policy_under_test "expected_hours_delta_ratio" key.
        Falls back to the baseline metric median for hours-flavored
        metrics when no attempt-transition duration data is present.
        Returns ``None`` when neither source has usable data.
        """

        attempt_hours = _sum_failed_attempt_hours(inputs.attempt_transitions)
        if attempt_hours is None:
            # Fallback: median of baseline hours-flavored metrics.
            hours_metrics = [
                m
                for m in inputs.baseline_metrics
                if m.definition_name in _HOURS_METRIC_NAMES and m.value is not None
            ]
            if not hours_metrics:
                return None
            baseline = _median(
                [float(m.value) for m in hours_metrics if m.value is not None]
            )
            if baseline is None:
                return None
            attempt_hours = baseline
        ratio = _safe_float(
            inputs.scenario.policy_under_test.get("expected_hours_delta_ratio")
        )
        if ratio is None:
            ratio = 0.0
        return attempt_hours * ratio

    def _project_delta_repair_cycles(
        self, inputs: EventReplayInputs
    ) -> float | None:
        """Project the doc-18:89 estimated_delta_repair_cycles from the
        typed event-transitions (+ optional baseline metrics fallback).

        The event-replay heuristic: the count of typed
        attempt-transitions where `attempt_kind == "repair"`, scaled
        by the scenario's policy_under_test
        "expected_repair_cycles_delta_ratio" key. Falls back to the
        baseline metric median for the `repair_cycles_per_task` metric
        when no attempt-transition repair count is present.
        """

        repair_count = sum(
            1 for a in inputs.attempt_transitions if a.attempt_kind == "repair"
        )
        baseline_value: float | None
        if repair_count == 0:
            cycles_metrics = [
                m
                for m in inputs.baseline_metrics
                if m.definition_name == "repair_cycles_per_task"
                and m.value is not None
            ]
            if not cycles_metrics:
                return None
            baseline_value = _median(
                [float(m.value) for m in cycles_metrics if m.value is not None]
            )
            if baseline_value is None:
                return None
        else:
            baseline_value = float(repair_count)
        ratio = _safe_float(
            inputs.scenario.policy_under_test.get(
                "expected_repair_cycles_delta_ratio"
            )
        )
        if ratio is None:
            ratio = 0.0
        return baseline_value * ratio

    def _project_delta_commit_failures(
        self, inputs: EventReplayInputs
    ) -> float | None:
        """Project the doc-18:90 estimated_delta_commit_failures from
        the typed event-transitions (+ optional baseline metrics
        fallback).

        The event-replay heuristic: the count of typed failure-
        transitions whose `failure_class` is `commit_hygiene` or
        whose `failure_type` carries `"commit"` substring, scaled by
        the scenario's policy_under_test
        "expected_commit_failures_delta_ratio" key. Falls back to the
        baseline metric median for the `commit_failures_per_task`
        metric when no commit failure transitions are present.
        """

        commit_failures = sum(
            1
            for f in inputs.failure_transitions
            if _looks_like_commit_failure(f)
        )
        baseline_value: float | None
        if commit_failures == 0:
            failures_metrics = [
                m
                for m in inputs.baseline_metrics
                if m.definition_name == "commit_failures_per_task"
                and m.value is not None
            ]
            if not failures_metrics:
                return None
            baseline_value = _median(
                [float(m.value) for m in failures_metrics if m.value is not None]
            )
            if baseline_value is None:
                return None
        else:
            baseline_value = float(commit_failures)
        ratio = _safe_float(
            inputs.scenario.policy_under_test.get(
                "expected_commit_failures_delta_ratio"
            )
        )
        if ratio is None:
            ratio = 0.0
        return baseline_value * ratio

    def _project_risk_change(
        self,
        inputs: EventReplayInputs,
        *,
        estimated_delta_hours: float | None,
        estimated_delta_repair_cycles: float | None,
        estimated_delta_commit_failures: float | None,
    ) -> RiskChange:
        """Project the doc-18:91 estimated_risk_change Literal from
        the typed deltas + safety-guard class.

        The event-replay heuristic:

        * If ALL 3 typed delta fields are ``None`` (i.e. no data for
          any axis) -> ``"unknown"``.
        * Else if the scenario's safety_guard_class is set (per
          doc-18:140-146) -> ``"lower"`` (safety-guard policies are
          permitted only when they lower risk).
        * Else if all available deltas are <= 0 (saving hours / fewer
          cycles / fewer failures) -> ``"lower"``.
        * Else if all available deltas are >= 0 -> ``"higher"``.
        * Else -> ``"same"`` (mixed signs => no clear direction).
        """

        deltas: list[float] = []
        for d in (
            estimated_delta_hours,
            estimated_delta_repair_cycles,
            estimated_delta_commit_failures,
        ):
            if d is not None:
                deltas.append(d)
        if not deltas:
            return "unknown"
        safety_guard_class = self._project_safety_guard_class(inputs)
        if safety_guard_class is not None:
            return "lower"
        if all(d <= 0.0 for d in deltas):
            return "lower"
        if all(d >= 0.0 for d in deltas):
            return "higher"
        return "same"

    def _project_confidence(self, inputs: EventReplayInputs) -> float:
        """Project the doc-18:92 confidence score from the typed
        event-transitions.

        Per doc-18:114 (vs doc-18:133 contrast) the event-replay
        engine carries a HIGHER confidence ceiling than the summary-
        replay engine (:data:`EVENT_REPLAY_CONFIDENCE_CEILING` = 0.90;
        vs the summary-replay 0.65).

        Per doc-18:138 *"Small sample size: report confidence and
        avoid policy recommendations."* the engine further reduces
        confidence below this ceiling when the failure-transition
        sample size is small (heuristic: if the failure-transition
        count is < 3, the emitted confidence is multiplied by 0.5).

        Per doc-18:140-141 *"Overfit risk: require at least one
        non-`8ac124d6` corpus before marking a general policy high
        confidence."* the engine further reduces confidence when the
        corpus's feature_ids list is ``["8ac124d6"]``-only (multiplied
        by 0.5).

        The confidence is also scaled by the data density of the
        typed event-transition lists -- if zero typed transitions
        across all 5 axes (i.e. fallback to baseline metrics only),
        the engine multiplies by 0.5 (effectively dropping toward the
        summary-replay ceiling).
        """

        confidence = EVENT_REPLAY_CONFIDENCE_CEILING
        failure_count = len(inputs.failure_transitions)
        if failure_count < 3:
            confidence *= 0.5
        if inputs.corpus.feature_ids == ["8ac124d6"]:
            confidence *= 0.5
        # Density: when no typed event transitions at all, drop toward
        # the summary-replay surface.
        total_transitions = (
            len(inputs.attempt_transitions)
            + len(inputs.gate_transitions)
            + len(inputs.failure_transitions)
            + len(inputs.queue_transitions)
            + len(inputs.checkpoint_transitions)
        )
        if total_transitions == 0:
            confidence *= 0.5
        # Clamp to [0.0, 1.0] (defence-in-depth).
        return max(0.0, min(confidence, 1.0))

    def _compose_assumptions(self, inputs: EventReplayInputs) -> list[str]:
        """Compose the doc-18:84 assumptions list.

        Per doc-18:84 the assumptions list is the union of the
        scenario assumptions plus any comparator-time assumptions
        added by the event-replay engine.

        The event-replay engine adds the following typed
        comparator-time assumption strings:

        * ``"event_replay_projection"`` -- always present (this
          engine is the event-replay projection).
        """

        composed = sorted(
            set(inputs.scenario.assumptions) | {"event_replay_projection"}
        )
        return composed

    def _compose_validity_limits(
        self,
        inputs: EventReplayInputs,
        *,
        estimated_delta_hours: float | None,
    ) -> list[str]:
        """Compose the doc-18:85 validity_limits list.

        Per doc-18:85 the validity_limits list is the union of the
        corpus validity limits plus any comparator-time validity
        constraints added by the event-replay engine.

        The event-replay engine adds the following typed
        comparator-time validity-limit strings:

        * ``"event_replay_mode"`` -- always present (doc-18:114
          *"Add event replay where typed attempt, gate, failure,
          queue, and checkpoint transitions are available."*).
        * ``"low_event_transition_count"`` -- when the total typed
          event-transition count is < 3 (doc-18:138 small sample
          discipline).
        * ``"insufficient_failure_transitions"`` -- when the typed
          failure-transition count is < 3 (failure transitions are
          the most signal-dense source for the commit-failures axis).
        * ``"missing_attempt_durations"`` -- when no typed attempt-
          transition duration data is available (i.e. the typed hours
          delta cannot be projected from event transitions alone).
        * ``"governance_only_provenance_chain"`` -- when the corpus
          is ``8ac124d6``-only (doc-18:140-141).
        """

        composed_set = set(inputs.corpus.validity_limits) | {"event_replay_mode"}
        total_transitions = (
            len(inputs.attempt_transitions)
            + len(inputs.gate_transitions)
            + len(inputs.failure_transitions)
            + len(inputs.queue_transitions)
            + len(inputs.checkpoint_transitions)
        )
        if total_transitions < 3:
            composed_set.add("low_event_transition_count")
        if len(inputs.failure_transitions) < 3:
            composed_set.add("insufficient_failure_transitions")
        if estimated_delta_hours is None:
            composed_set.add("missing_attempt_durations")
        if inputs.corpus.feature_ids == ["8ac124d6"]:
            composed_set.add("governance_only_provenance_chain")
        return sorted(composed_set)

    def _compute_invalidated_by(
        self, inputs: EventReplayInputs
    ) -> list[str]:
        """Compute the doc-18:93 invalidated_by list.

        Per doc-18:134-135 *"Policy requires evidence not in corpus:
        mark invalidated and collect more evidence."* the engine
        scans the scenario's required_evidence_kinds against the
        corpus's evidence_set_ids + implementation_anchor_ids; any
        missing required evidence kind produces an entry of the form
        ``"missing_evidence:<kind>"`` (mirrors the Slice 18 2nd sub-
        slice :class:`ScenarioDefinitionBuilder` precedent + Slice 18
        3rd sub-slice :class:`CounterfactualSummaryReplayEngine`
        precedent).

        Per doc-18:136-137 *"Product defect dominates window: do not
        infer workflow policy success from a product-blocked group
        without separate workflow evidence."* the engine also scans
        the corpus's validity_limits for the
        ``"product_defect_window"`` marker; when present + the
        scenario's safety_guard_class is unset, the engine adds
        ``"product_defect_window"`` to invalidated_by.
        """

        invalidated_set: set[str] = set()
        covered_evidence = set(inputs.corpus.evidence_set_ids) | set(
            inputs.corpus.implementation_anchor_ids
        )
        for evidence_kind in inputs.scenario.required_evidence_kinds:
            if evidence_kind not in covered_evidence:
                invalidated_set.add(f"missing_evidence:{evidence_kind}")
        if (
            "product_defect_window" in inputs.corpus.validity_limits
            and self._project_safety_guard_class(inputs) is None
        ):
            invalidated_set.add("product_defect_window")
        return sorted(invalidated_set)

    def _project_recommended_next_step(
        self,
        inputs: EventReplayInputs,
        *,
        invalidated_by: list[str],
        confidence: float,
    ) -> RecommendedNextStep:
        """Project the doc-18:95 recommended_next_step Literal.

        Per doc-18:95 the recommended_next_step is one of:

        * ``"discard"`` -- the scenario should be discarded (e.g.
          confidence < 0.05).
        * ``"collect_more_evidence"`` -- the engine cannot make a
          confident projection (e.g. invalidated_by non-empty;
          confidence < 0.3; or total event-transition count < 3).
        * ``"draft_policy"`` -- the engine can suggest a draft policy
          (e.g. confidence >= 0.5; invalidated_by empty).
        * ``"implementation_plan"`` -- the engine can suggest an
          implementation plan (e.g. confidence >= 0.6 +
          safety_guard_class set; invalidated_by empty).
        """

        total_transitions = (
            len(inputs.attempt_transitions)
            + len(inputs.gate_transitions)
            + len(inputs.failure_transitions)
            + len(inputs.queue_transitions)
            + len(inputs.checkpoint_transitions)
        )
        if confidence < 0.05:
            return "discard"
        if invalidated_by or confidence < 0.3 or total_transitions < 3:
            return "collect_more_evidence"
        safety_guard_class = self._project_safety_guard_class(inputs)
        if confidence >= 0.6 and safety_guard_class is not None:
            return "implementation_plan"
        if confidence >= 0.5:
            return "draft_policy"
        return "collect_more_evidence"

    def _project_safety_guard_class(
        self, inputs: EventReplayInputs
    ) -> str | None:
        """Project the doc-18:87 safety_guard_class field.

        Per doc-18:140-146 *"A safety-guard exception is allowed only
        for policies whose sole effect is to fail closed earlier,
        reduce mutation authority, or add bounded preflight
        evidence. The scenario must set `safety_guard_class`, cite
        non-governance primary evidence, and pass a chain-depth
        check proving it is not derived solely from prior governance
        recommendations."* the safety_guard_class field is propagated
        from the scenario's policy_under_test["safety_guard_class"]
        key (when present); otherwise ``None``.
        """

        raw = inputs.scenario.policy_under_test.get("safety_guard_class")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return None

    def _collect_policy_provenance_refs(
        self, inputs: EventReplayInputs
    ) -> list[GovernanceEvidenceRef]:
        """Surface the typed Slice 13a evidence refs from the typed
        failure-transitions + baseline metrics onto the emitted
        result's typed policy_provenance_refs list.

        Per the Slice 13A invariant + doc-18:186-249 the projection is
        refs-only: the typed
        :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
        BaseModels are constructed from the typed Slice 10a
        :class:`EvidenceRef` summary fields (ids / digests / citations)
        and emitted as Slice 13a refs. No raw artifact body is
        hydrated.

        Sources of evidence refs (in order):

        1. Typed Slice 10a
           :attr:`TypedFailureSummary.evidence_refs` (typed
           :class:`EvidenceRef` summary records).
        2. Typed Slice 15
           :attr:`GovernanceMetricValue.evidence_refs` (typed
           Slice 13a :class:`GovernanceEvidenceRef` records) when
           baseline metrics are supplied.

        Deduplication uses the Slice 13a
        :attr:`GovernanceEvidenceRef.ref_id` as the cross-ref identity
        (refs with the same ref_id are deduped; the first occurrence
        wins).
        """

        seen_ref_ids: set[str] = set()
        out: list[GovernanceEvidenceRef] = []
        for failure in inputs.failure_transitions:
            for ev_ref in failure.evidence_refs:
                gov_ref = _evidence_ref_to_governance_ref(ev_ref)
                if gov_ref is None:
                    continue
                if gov_ref.ref_id in seen_ref_ids:
                    continue
                seen_ref_ids.add(gov_ref.ref_id)
                out.append(gov_ref)
        for metric in inputs.baseline_metrics:
            for ref in metric.evidence_refs:
                if ref.ref_id in seen_ref_ids:
                    continue
                seen_ref_ids.add(ref.ref_id)
                out.append(ref)
        return out


# --- Module-private constants (used by the engine's projection helpers) -----


_HOURS_METRIC_NAMES: frozenset[str] = frozenset(
    {
        # Doc-15:103 + doc-15:110-112 -- the 4 typed v1 metric names
        # whose unit is "hours" (mirrors the Slice 18 3rd sub-slice
        # _HOURS_METRIC_NAMES set verbatim).
        "hours_per_task",
        "merge_queue_wait_hours",
        "checkpoint_duration_hours",
        "workflow_drag_hours",
    }
)


_COMMIT_FAILURE_CLASS_NAMES: frozenset[str] = frozenset(
    {
        # Slice 07 typed failure_class taxonomy that explicitly signals
        # commit-flow failure conditions; the event-replay engine
        # counts failure-transitions matching these classes as
        # "commit failures" for the doc-18:90 estimated_delta_commit_failures
        # projection axis.
        "commit_hygiene",
    }
)


# --- Pure helpers -----------------------------------------------------------


def _median(values: list[float]) -> float | None:
    """Return the median of ``values`` (or ``None`` if empty)."""

    if not values:
        return None
    sorted_values = sorted(values)
    n = len(sorted_values)
    mid = n // 2
    if n % 2 == 1:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2.0


def _safe_float(value: Any) -> float | None:
    """Coerce ``value`` to ``float`` if it is a number; otherwise
    ``None``.

    Used by the engine to read scenario.policy_under_test dict values
    (free-form per doc-18:73) without raising on malformed values.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _sum_failed_attempt_hours(
    attempts: list[ExecutionAttemptSummary],
) -> float | None:
    """Sum the durations (in hours) of typed attempt-transitions whose
    status is ``failed`` / ``cancelled`` / ``incomplete``.

    Returns ``None`` when no attempts have a usable duration (e.g. the
    list is empty or no attempt has both a ``started_at`` and a
    ``finished_at`` timestamp).
    """

    total_seconds = 0.0
    matched = 0
    for a in attempts:
        if a.status not in {"failed", "cancelled", "incomplete"}:
            continue
        if a.finished_at is None:
            continue
        delta = (a.finished_at - a.started_at).total_seconds()
        if delta <= 0:
            continue
        total_seconds += delta
        matched += 1
    if matched == 0:
        return None
    return total_seconds / 3600.0


def _looks_like_commit_failure(failure: TypedFailureSummary) -> bool:
    """Return ``True`` if the typed failure-transition signals a
    commit-flow failure.

    The check is two-pronged (so the engine is robust to either typed
    failure_class or typed failure_type carrying the signal):

    * Typed :attr:`failure_class` matches one of
      :data:`_COMMIT_FAILURE_CLASS_NAMES`.
    * Typed :attr:`failure_type` carries the substring ``"commit"``.
    """

    if failure.failure_class in _COMMIT_FAILURE_CLASS_NAMES:
        return True
    if "commit" in failure.failure_type:
        return True
    return False


def _evidence_ref_to_governance_ref(
    ev_ref: EvidenceRef,
) -> GovernanceEvidenceRef | None:
    """Convert a typed Slice 10a :class:`EvidenceRef` to a typed Slice
    13a :class:`GovernanceEvidenceRef`.

    The Slice 10a typed evidence ref carries (table, id, citation,
    kind, summary, artifact_key) summary fields; the Slice 13a typed
    governance evidence ref carries (authority, ref_id, digest,
    quality, completeness) typed fields. The projection is refs-only
    per the Slice 13A invariant; no raw artifact body is hydrated.

    Returns ``None`` if the typed Slice 10a EvidenceRef cannot be
    projected to a typed Slice 13a GovernanceEvidenceRef without
    fabricating a digest (i.e. the Slice 10a ref has no usable id).
    The defensive None-return mirrors the
    ``feedback_no_silent_degradation`` rule (NEVER raise).
    """

    try:
        # The Slice 13a `ref_id` is the stable cross-process identity;
        # we synthesize it from the typed (table, id) tuple.
        ref_id = f"{ev_ref.table}:{ev_ref.id}"
        # The Slice 13a `digest` is a typed SHA-256 hex string; we
        # synthesize it from the typed (table, id, citation, kind)
        # tuple via the same canonical-JSON + SHA-256 helpers the
        # rest of the module uses (mirrors the doc-13a digest
        # discipline).
        digest = _sha256_hex(
            _canonical_json(
                {
                    "table": ev_ref.table,
                    "id": ev_ref.id,
                    "citation": ev_ref.citation,
                    "kind": ev_ref.kind,
                    "artifact_key": ev_ref.artifact_key,
                }
            )
        )
        return GovernanceEvidenceRef(
            authority="typed_journal",
            ref_id=ref_id,
            digest=digest,
            quality="canonical",
            completeness="complete",
        )
    except (ValidationError, ValueError, TypeError):
        return None


# --- Re-exports (documentation-only; for IDE auto-import discovery) --------

# Documentation-only re-references so the typed Literal aliases the
# engine surfaces to consumers stay discoverable from this module.
# These are NOT in __all__ (per the per-slice no-re-export discipline);
# the consumer imports them from
# `iriai_build_v2.execution_control.counterfactual_replay`.
_REPLAY_MODE_REUSE: type = ReplayMode  # type: ignore[assignment]
_RISK_CHANGE_REUSE: type = RiskChange  # type: ignore[assignment]
_RECOMMENDED_NEXT_STEP_REUSE: type = RecommendedNextStep  # type: ignore[assignment]
_COMPUTE_COUNTERFACTUAL_IDEMPOTENCY_KEY_REUSE = (
    compute_counterfactual_idempotency_key
)
