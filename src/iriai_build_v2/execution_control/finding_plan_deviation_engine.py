"""Slice 16 3rd-A sub-slice -- implementation-plan deviation engine for the
``accepted_plan_deviation`` + ``implementation_journal_gap`` finding classes
(per ``docs/execution-control-plane/16-finding-engine-and-taxonomy.md`` §
Refactoring Steps **step 5** at doc-16:164-165: *"Add implementation-plan
deviation rules over journal anchors, reviewer findings, accepted deviations,
and late test failures."*).

This module owns the **journal-anchor consumption code path** that bridges
the Slice 13c
:func:`~iriai_build_v2.workflows.develop.governance.journal_parser.parse_implementation_journal`
typed output (``list[ImplementationArtifactAnchor]`` per
``journal_parser.py:139`` + ``journal_parser.py:320-324``) into the Slice 16
2nd sub-slice
:class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine`
emitter surface (per ``finding_rule_engine.py:740`` + ``finding_rule_engine.py:923``).

**Chunk-shape SPLIT decision (3rd-A vs 3rd-B).** Per the STATUS.md §
"Next safe action" the 3rd sub-slice SPLITS (mirrors the Slice 12a-1 /
12a-2 / 12a-3 + Slice 15 3rd + 4th + 5th sub-slice precedent):

* **Slice 16 3rd-A sub-slice (THIS MODULE)**: implementation-plan
  deviation rules for ``accepted_plan_deviation`` (doc-16:135) +
  ``implementation_journal_gap`` (doc-16:134 + doc-16:191-192) classes.
  Consumes the Slice 13c parser's typed output verbatim.
* **Slice 16 3rd-B sub-slice (DEFERRED)**: reviewer-findings rules (from
  the journal parser's ``event="finding"`` anchors) + late-test-failure
  rules consuming the 13c + 13d JSONL decision-log parser typed output
  (``test_result`` event in the :data:`JournalEventName` 7-value taxonomy).

The SPLIT keeps each sub-slice within the 600-1000 line target band + the
30+ test minimum and avoids coupling the 13d JSONL parser surface into
the same diff as the 13c markdown parser surface.

**REUSE discipline (the auto-memory ``feedback_no_overengineer_use_library``
rule).** This module REUSES (NOT redefines):

* Slice 13a
  :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
  (per ``models.py:786``) -- the typed-surface contract both Slice 13c
  + 13d parsers populate. The 7-value
  :data:`~iriai_build_v2.workflows.develop.governance.models.JournalEventName`
  Literal (per ``models.py:168-182``) is the typed event taxonomy this
  module consumes.
* Slice 13a
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  (per ``models.py:622``) -- the typed evidence-ref contract the
  Slice 16 1st sub-slice
  :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
  carries.
* Slice 13c
  :func:`~iriai_build_v2.workflows.develop.governance.journal_parser.parse_implementation_journal`
  (per ``journal_parser.py:320``) -- the typed parser function that
  projects the implementation journal markdown to a
  ``list[ImplementationArtifactAnchor]``.
* Slice 16 1st sub-slice
  :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
  + :data:`~iriai_build_v2.execution_control.finding_engine.FindingKind`
  + :data:`~iriai_build_v2.execution_control.finding_engine.FindingSeverity`
  + :data:`~iriai_build_v2.execution_control.finding_engine.FindingCausalRole`
  (per ``finding_engine.py:394`` + 207 + 176 + 263) -- the typed-shape
  foundation.
* Slice 16 2nd sub-slice
  :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine`
  (per ``finding_rule_engine.py:740``) -- the 7-guard emitter logic
  (suppression + expiry + at-least-one-primary + product/workflow
  separation + confidence threshold + idempotency + construction).
  This module does NOT re-implement the 7-guard logic; the
  :class:`FindingPlanDeviationEngine.process_anchors` surface builds
  a typed :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs`
  bundle from each parsed anchor and DELEGATES to
  :meth:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine.emit_finding`.
* Slice 16 2nd sub-slice
  :data:`~iriai_build_v2.execution_control.finding_rule_engine.CLASS_NAME_TO_FINDING_KIND`
  (per ``finding_rule_engine.py:228``) -- the typed mapping from
  ``accepted_plan_deviation`` -> ``implementation_plan_deviation`` and
  ``implementation_journal_gap`` -> ``provenance_gap``.
* Slice 16 2nd sub-slice
  :data:`~iriai_build_v2.execution_control.finding_rule_engine.EVIDENCE_GAP_FINDING_KINDS`
  (per ``finding_rule_engine.py:173``) -- the 3-value tuple naming the
  kinds allowed to emit with empty ``primary_evidence_refs``
  (per doc-16:159-161). Both rules this sub-slice owns
  (``accepted_plan_deviation`` -> ``implementation_plan_deviation``;
  ``implementation_journal_gap`` -> ``provenance_gap``) emit kinds in
  this tuple, so the at-least-one-primary invariant is satisfied
  by the construction.
* Slice 16 2nd sub-slice
  :data:`~iriai_build_v2.execution_control.finding_rule_engine.REQUIRED_V1_FINDING_RULES`
  (per ``finding_rule_engine.py:695``) -- the 16-entry typed rule
  tuple. This module's
  :class:`FindingPlanDeviationEngine.process_anchors` surface looks up
  the rules by ``rule_id`` (``"accepted_plan_deviation_v1"`` +
  ``"implementation_journal_gap_v1"``) from this tuple.

**Per the auto-memory ``feedback_no_silent_degradation`` rule** every
failure mode projects onto a typed gap finding rather than raising into
the caller:

* Anchor-parse failures (missing file / unparseable markdown / Pydantic
  validation error on a parsed anchor) -> typed
  :class:`PlanDeviationParseGap` accumulated on the
  :class:`PlanDeviationAnchorBundle.parse_gaps` list; the bundle's
  ``anchors`` list is returned empty.
* Rule-emission failures (suppression / expiry / at-least-one-primary
  invariant / product-workflow separation / confidence threshold /
  idempotency-key computation / construction) -> typed
  :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionGap`
  accumulated on the REUSED
  :attr:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine.gap_findings`
  property (the engine's own per-call accumulator); the engine NEVER
  raises per doc-14:242-243.

**The NEW typed failure id**
:data:`FINDING_PLAN_DEVIATION_FAILURE_ID` (``"finding_plan_deviation_parse_failed"``)
registers in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` under
the EXISTING ``evidence_corruption`` failure_class with the EXISTING
NON-blocking ``retry_governance_projection`` RouteAction (REUSED from
Slice 14 2nd sub-slice + Slice 15 2nd + 4th sub-slices + Slice 16 2nd
sub-slice; NOT a new RouteAction). This mirrors the Slice 16 2nd
sub-slice precedent verbatim.

**Implementation discipline.** Stdlib (``datetime`` + ``pathlib``) +
Pydantic v2 + Slice 13a ``governance.models`` + Slice 13c
``journal_parser`` + Slice 16 1st sub-slice ``finding_engine`` + Slice 16
2nd sub-slice ``finding_rule_engine`` only. NO imports from other parts
of ``execution_control/`` (this module is foundational for the Slice 16
3rd-B sub-slice + the Slice 16 4th sub-slice that persists findings).
NO imports from ``workflows/develop/execution/phases/`` / ``supervisor``
/ ``dashboard`` (those would be downstream consumers, not dependencies).
NO imports from
:mod:`iriai_build_v2.workflows.develop.governance.decision_log_parser`
(Slice 13d; the 3rd-B sub-slice consumes that surface).

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.finding_rule_engine` (Slice 16
2nd sub-slice) + :mod:`iriai_build_v2.execution_control.finding_engine`
(Slice 16 1st sub-slice): ``BaseModel`` subclasses with
``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed as a typed
``ValidationError`` rather than being silently absorbed.

Per the auto-memory ``feedback_flat_structured_output`` rule the typed
control fields are flat primitives (``str`` / ``float`` / ``bool`` /
``list`` / ``dict``). Per the auto-memory
``feedback_no_silent_degradation`` rule every Pydantic field validates
at construction; unknown values fail closed via Literal range +
``extra="forbid"`` discipline.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from iriai_build_v2.execution_control.finding_engine import (
    FindingCausalRole,
    FindingKind,
    FindingSeverity,
    GovernanceFinding,
)
from iriai_build_v2.execution_control.finding_rule_engine import (
    CLASS_NAME_TO_FINDING_KIND,
    EVIDENCE_GAP_FINDING_KINDS,
    FINDING_RULE_EMISSION_FAILURE_ID,
    REQUIRED_V1_FINDING_RULES,
    FindingRule,
    FindingRuleEmissionGap,
    FindingRuleEmissionInputs,
    FindingRuleEngine,
)
from iriai_build_v2.workflows.develop.governance.journal_parser import (
    parse_implementation_journal,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
    ImplementationArtifactAnchor,
    JournalEventName,
)


__all__ = [
    # NEW typed failure id under EXISTING evidence_corruption failure_class
    # (registers in failure_router with REUSED retry_governance_projection
    # NON-blocking RouteAction; mirrors Slice 16 2nd sub-slice precedent).
    "FINDING_PLAN_DEVIATION_FAILURE_ID",
    # Typed inputs / outputs (mirrors Slice 16 2nd sub-slice typed-bundle pattern).
    "PlanDeviationAnchorBundle",
    "PlanDeviationEmissionPlan",
    "PlanDeviationParseGap",
    # Pure helpers (mirrors Slice 16 2nd sub-slice load_required_v1_finding_rules
    # + Slice 15 4th sub-slice compute_review_projection_id pattern).
    "parse_plan_deviation_anchors",
    "compute_accepted_plan_deviation_inputs",
    "compute_implementation_journal_gap_inputs",
    # The engine class (per chunk-shape point 6).
    "FindingPlanDeviationEngine",
]


# --- Typed failure id (doc-16:155-169 + doc-14:242-243 NON-BLOCKING) ---------


FINDING_PLAN_DEVIATION_FAILURE_ID: Literal["finding_plan_deviation_parse_failed"] = (
    "finding_plan_deviation_parse_failed"
)
"""Doc-16:164-165 + doc-14:242-243 -- the typed failure id the
implementation-plan deviation engine projects onto when a structural
anchor-parse failure occurs (e.g. missing journal file; unparseable
markdown body; Pydantic validation error on a candidate
:class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`).

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice +
Slice 15 2nd + 4th sub-slices + Slice 16 2nd sub-slice; NOT a new route
action).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 16 pattern (this id + the Slice 16 2nd sub-slice
``finding_rule_emission_failed``) matches the Slice 14 + Slice 15
non-blocking governance projection observer.

The Slice 14 + Slice 15 + Slice 16 2nd sub-slice precedents are the
source-of-truth for the non-blocking governance-projection
failure-routing discipline:

* :mod:`iriai_build_v2.execution_control.commit_provenance_writer`
  (Slice 14 2nd) defines ``line_provenance_gap`` +
  ``governance_evidence_conflict``.
* :mod:`iriai_build_v2.execution_control.governance_metric_extractor`
  (Slice 15 2nd) defines ``governance_metric_extraction_failed``.
* :mod:`iriai_build_v2.execution_control.governance_scorecard_writer`
  (Slice 15 4th) defines ``governance_scorecard_persistence_failed``.
* :mod:`iriai_build_v2.execution_control.finding_rule_engine`
  (Slice 16 2nd) defines ``finding_rule_emission_failed``.
* This module (Slice 16 3rd-A) defines
  ``finding_plan_deviation_parse_failed``.

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to implementation-
plan deviation parsing failures (this slice is also a post-checkpoint
governance projection observer).
"""


# --- Typed bundle for parsed journal anchors --------------------------------


class PlanDeviationParseGap(BaseModel):
    """Typed governance-gap finding produced when the
    :func:`parse_plan_deviation_anchors` surface fails to parse a
    journal (e.g. missing file; unparseable markdown body; Pydantic
    validation error on a candidate
    :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`).

    Mirrors the Slice 14
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    + Slice 15 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_metric_extractor.GovernanceMetricExtractionGap`
    + Slice 15 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardPersistenceGap`
    + Slice 16 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionGap`
    shapes verbatim per the chunk-shape contract.

    Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED here
    per doc-15:140-145 + doc-16 governance-projection discipline) the
    gap finding is NON-blocking: the caller MUST NOT propagate it to
    the executor / checkpoint / merge-queue / resume code paths. The
    corresponding typed failure id :data:`FINDING_PLAN_DEVIATION_FAILURE_ID`
    (``finding_plan_deviation_parse_failed``) registers under the
    EXISTING ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
    NOT a new route action).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`ValidationError` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["finding_plan_deviation_parse_failed"]
    """Doc-16:164-165 + doc-14:242-243 -- the typed failure id. Registers
    under the EXISTING ``evidence_corruption`` failure_class with NON-
    blocking routing per doc-14:242-243."""

    journal_path: str
    """The journal source path the parse attempt targeted; recorded so
    downstream consumers can correlate the gap with the source artifact.
    Always a non-empty string -- empty paths fail closed with a typed
    Pydantic ``ValidationError``."""

    reason: str
    """Free-form gap reason naming the specific failure mode (e.g.
    ``"journal_file_missing"`` / ``"journal_parse_validation_error"`` /
    ``"anchor_construction_failed"`` /
    ``"unexpected_parser_exception"``)."""

    anchor_kind: str = ""
    """Optional anchor-kind annotation when the parse failure occurred on
    a specific anchor candidate (e.g. the anchor's
    :attr:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor.event`
    value if known). Empty string when the failure occurred before any
    anchor was constructed (e.g. file-missing case)."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding (e.g.
    the parser exception type, the line number of the failing anchor,
    the truncated error detail). Free-form per the doc-14:192-201 +
    doc-15 + doc-16 governance-finding contract."""


class PlanDeviationAnchorBundle(BaseModel):
    """Typed bundle of parsed implementation-journal anchors the
    :class:`FindingPlanDeviationEngine` consumes per doc-16:164-165 step 5.

    Per doc-16:164-165 *"Add implementation-plan deviation rules over
    journal anchors, reviewer findings, accepted deviations, and late
    test failures."* the engine's primary input is the parsed
    ``list[ImplementationArtifactAnchor]`` produced by the Slice 13c
    :func:`~iriai_build_v2.workflows.develop.governance.journal_parser.parse_implementation_journal`
    function (per ``journal_parser.py:320``). The bundle composes:

    * ``journal_path`` -- the journal source path the anchors were
      parsed from. Always a non-empty string.
    * ``anchors`` -- the typed
      ``list[ImplementationArtifactAnchor]`` (validly empty when the
      parser found no recognised anchors OR when the parser failed and
      projected a typed gap onto :attr:`parse_gaps`).
    * ``parse_gaps`` -- the typed ``list[PlanDeviationParseGap]``
      accumulated during parsing (defaults to empty). Per the
      ``feedback_no_silent_degradation`` rule every parser failure
      projects onto a typed gap rather than silently dropping the
      anchor.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. The
    :attr:`PlanDeviationAnchorBundle.anchors` field is a ``list`` of the
    Slice 13a typed
    :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
    (REUSED via direct import; NOT redefined here per doc-13a:285-287
    step 9 + doc-16:201-291).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`ValidationError` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    journal_path: str
    """The journal source path the anchors were parsed from (always a
    non-empty string; empty paths fail closed with a typed
    :class:`ValidationError`)."""

    anchors: list[ImplementationArtifactAnchor] = Field(default_factory=list)
    """The typed ``list[ImplementationArtifactAnchor]`` produced by the
    Slice 13c :func:`~iriai_build_v2.workflows.develop.governance.journal_parser.parse_implementation_journal`
    function (per ``journal_parser.py:320``). Validly empty when the
    parser found no recognised anchors OR when the parser failed and
    projected a typed gap onto :attr:`parse_gaps`."""

    parse_gaps: list[PlanDeviationParseGap] = Field(default_factory=list)
    """The typed list of
    :class:`PlanDeviationParseGap` findings accumulated during parsing.
    Defaults to empty. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every parser failure
    projects onto a typed gap rather than silently dropping the
    anchor."""


# --- Typed emission plan ----------------------------------------------------


class PlanDeviationEmissionPlan(BaseModel):
    """Typed emission plan the
    :class:`FindingPlanDeviationEngine.process_anchors` surface builds
    per parsed
    :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`.

    The plan composes the rule-application surface this engine adds on
    top of the REUSED Slice 16 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine`
    emitter. Each plan corresponds to ONE finding the engine attempts
    to emit; the
    :meth:`FindingPlanDeviationEngine.process_anchors` surface builds a
    typed :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs`
    bundle from the plan + the source anchor and DELEGATES to
    :meth:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine.emit_finding`
    (i.e. this module does NOT re-implement the 7-guard logic; per
    ``feedback_no_overengineer_use_library`` we reuse the 2nd sub-slice
    engine verbatim).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`ValidationError` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    rule: FindingRule
    """The typed :class:`~iriai_build_v2.execution_control.finding_engine.FindingRule`
    the engine applies (looked up from
    :data:`~iriai_build_v2.execution_control.finding_rule_engine.REQUIRED_V1_FINDING_RULES`
    by ``rule_id``)."""

    class_name: str
    """The canonical fine-grained class name per
    :data:`~iriai_build_v2.execution_control.finding_engine.REQUIRED_V1_FINDING_CLASS_NAMES`
    (e.g. ``"accepted_plan_deviation"`` or
    ``"implementation_journal_gap"``)."""

    severity: FindingSeverity
    """The typed
    :data:`~iriai_build_v2.execution_control.finding_engine.FindingSeverity`
    classification."""

    confidence: float = Field(ge=0.0, le=1.0)
    """The typed float in ``[0.0, 1.0]``. The REUSED 2nd sub-slice
    engine compares against
    :attr:`~iriai_build_v2.execution_control.finding_engine.FindingRule.min_confidence`
    per doc-16:193."""

    source_anchor: ImplementationArtifactAnchor | None = None
    """The Slice 13a typed
    :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
    the plan derives from (REUSED via direct import; NOT redefined).
    ``None`` for the ``implementation_journal_gap`` plan when the
    bundle has no anchors (the gap rule emits without a source anchor
    per doc-16:191-192)."""

    feature_id: str | None = None
    """The feature scope (``None`` for cross-feature anchors)."""

    affected_scope: dict[str, Any] = Field(default_factory=dict)
    """The scope-dimensions dict per
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.affected_scope`."""

    recommended_action_display: str
    """The non-executable display text per doc-16:115-118."""

    safe_runtime_action: bool = False
    """Per
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.safe_runtime_action`
    -- defaults ``False`` for plan-deviation findings (manual review
    typically required)."""

    requires_policy_artifact: bool = False
    """Per
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.requires_policy_artifact`
    -- defaults ``False`` per doc-16:191-192 ("block plan-vs-actual
    recommendations" implies the gap finding is advisory until the gap
    is resolved); callers may override per per-corpus calibration."""

    causal_role: FindingCausalRole = "contributing"
    """The typed
    :data:`~iriai_build_v2.execution_control.finding_engine.FindingCausalRole`
    classification. Default ``"contributing"`` per doc-16:187-190 (the
    accepted plan deviation is a contributing factor to plan-vs-actual
    drift; the typed surface lets the Slice 17 recommender filter to
    actionable causes)."""


# --- Pure helpers (mirrors Slice 16 2nd sub-slice load_helper pattern) ------


def parse_plan_deviation_anchors(
    journal_path: Path | str,
    *,
    body: str | None = None,
) -> PlanDeviationAnchorBundle:
    """Parse the implementation journal at ``journal_path`` into a typed
    :class:`PlanDeviationAnchorBundle` via the Slice 13c
    :func:`~iriai_build_v2.workflows.develop.governance.journal_parser.parse_implementation_journal`
    function.

    Per the auto-memory ``feedback_no_silent_degradation`` rule every
    parser failure projects onto a typed
    :class:`PlanDeviationParseGap` rather than raising into the caller:

    * Missing file -> ``reason="journal_file_missing"`` gap; returned
      bundle has empty ``anchors``.
    * Pydantic ``ValidationError`` from the parser ->
      ``reason="journal_parse_validation_error"`` gap; returned bundle
      has empty ``anchors``.
    * ``ValueError`` from the parser (e.g. empty slice id; rejected
      finding id; line-number invariant violation) ->
      ``reason="journal_parse_validation_error"`` gap (same reason
      class as Pydantic).
    * Any other unexpected exception (defence-in-depth) ->
      ``reason="unexpected_parser_exception"`` gap; returned bundle
      has empty ``anchors``.

    On success the returned bundle carries the parsed
    ``list[ImplementationArtifactAnchor]`` on :attr:`PlanDeviationAnchorBundle.anchors`
    and an empty :attr:`PlanDeviationAnchorBundle.parse_gaps`.

    :param journal_path: the journal source path; recorded into the
        bundle's :attr:`PlanDeviationAnchorBundle.journal_path` (as
        ``str(journal_path)``) whether the parse succeeded or failed.
    :param body: optional pre-loaded markdown body; passed through to the
        Slice 13c parser. When ``None`` (the production caller path) the
        parser reads from disk. Test-only escape hatch.
    :returns: a typed :class:`PlanDeviationAnchorBundle` -- on success
        carrying the parsed anchors on ``anchors`` + empty
        ``parse_gaps``; on failure carrying empty ``anchors`` + the
        typed gap on ``parse_gaps``. NEVER raises.
    """

    # Doc-13:147 -- journal_path is the stable cross-process freshness
    # anchor. Always coerce to str so the typed surface is uniform.
    journal_path_str = str(journal_path)

    # The bundle's journal_path field is non-empty by Pydantic validation;
    # we coerce-and-validate the input before any parser call so the
    # bundle is always constructible.
    if not journal_path_str.strip():
        # Fail-closed: empty paths cannot be parsed and produce a typed
        # gap finding with a sentinel journal_path so the gap is still
        # constructible. Per the typed surface contract the
        # PlanDeviationAnchorBundle requires a non-empty journal_path,
        # so we use "<unspecified>" as the sentinel.
        return PlanDeviationAnchorBundle(
            journal_path="<unspecified>",
            anchors=[],
            parse_gaps=[
                PlanDeviationParseGap(
                    failure_id=FINDING_PLAN_DEVIATION_FAILURE_ID,
                    journal_path="<unspecified>",
                    reason="journal_path_empty",
                    anchor_kind="",
                    evidence_payload={
                        "supplied_journal_path": journal_path_str,
                    },
                )
            ],
        )

    try:
        anchors = parse_implementation_journal(journal_path, body=body)
    except FileNotFoundError as exc:
        return PlanDeviationAnchorBundle(
            journal_path=journal_path_str,
            anchors=[],
            parse_gaps=[
                PlanDeviationParseGap(
                    failure_id=FINDING_PLAN_DEVIATION_FAILURE_ID,
                    journal_path=journal_path_str,
                    reason="journal_file_missing",
                    anchor_kind="",
                    evidence_payload={
                        "error_type": type(exc).__name__,
                        "error_detail": str(exc)[:500],
                    },
                )
            ],
        )
    except ValueError as exc:
        # Per doc-13:182-183 + journal_parser.py:368-374 the parser
        # raises typed ValueError on Pydantic validation failure (e.g.
        # empty slice id; rejected finding id). The
        # PlanDeviationParseGap projection preserves the typed error
        # context for downstream audit.
        return PlanDeviationAnchorBundle(
            journal_path=journal_path_str,
            anchors=[],
            parse_gaps=[
                PlanDeviationParseGap(
                    failure_id=FINDING_PLAN_DEVIATION_FAILURE_ID,
                    journal_path=journal_path_str,
                    reason="journal_parse_validation_error",
                    anchor_kind="",
                    evidence_payload={
                        "error_type": type(exc).__name__,
                        "error_detail": str(exc)[:500],
                    },
                )
            ],
        )
    except Exception as exc:  # pragma: no cover -- defence-in-depth
        # Per the auto-memory feedback_no_silent_degradation rule we
        # NEVER let an unexpected exception propagate to the caller.
        # The typed gap finding carries the exception type + truncated
        # detail so downstream consumers can audit the failure mode.
        return PlanDeviationAnchorBundle(
            journal_path=journal_path_str,
            anchors=[],
            parse_gaps=[
                PlanDeviationParseGap(
                    failure_id=FINDING_PLAN_DEVIATION_FAILURE_ID,
                    journal_path=journal_path_str,
                    reason="unexpected_parser_exception",
                    anchor_kind="",
                    evidence_payload={
                        "error_type": type(exc).__name__,
                        "error_detail": str(exc)[:500],
                    },
                )
            ],
        )

    return PlanDeviationAnchorBundle(
        journal_path=journal_path_str,
        anchors=list(anchors),
        parse_gaps=[],
    )


def _lookup_rule(rule_id: str) -> FindingRule | None:
    """Return the :class:`FindingRule` from
    :data:`~iriai_build_v2.execution_control.finding_rule_engine.REQUIRED_V1_FINDING_RULES`
    matching ``rule_id``, or ``None`` when no match.

    Pure helper (no side effects). Mirrors the Slice 16 2nd sub-slice
    :meth:`FindingRuleEngine._lookup_suppression` discipline.
    """

    for rule in REQUIRED_V1_FINDING_RULES:
        if rule.rule_id == rule_id:
            return rule
    return None


def compute_accepted_plan_deviation_inputs(
    anchor: ImplementationArtifactAnchor,
    rule: FindingRule,
    *,
    confidence: float = 0.8,
    severity: FindingSeverity = "low",
    feature_id: str | None = None,
    recommended_action_display: str | None = None,
) -> FindingRuleEmissionInputs:
    """Build a typed
    :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs`
    bundle for the ``accepted_plan_deviation`` rule from a parsed
    :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`.

    Per doc-16:135 the ``accepted_plan_deviation`` class maps to the
    :data:`~iriai_build_v2.execution_control.finding_engine.FindingKind`
    ``"implementation_plan_deviation"`` (per
    :data:`~iriai_build_v2.execution_control.finding_rule_engine.CLASS_NAME_TO_FINDING_KIND`).
    Per :data:`~iriai_build_v2.execution_control.finding_rule_engine.EVIDENCE_GAP_FINDING_KINDS`
    the ``implementation_plan_deviation`` kind is explicitly allowed to
    emit with empty :attr:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs.primary_evidence_refs`
    (the journal anchor is recorded on
    :attr:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs.implementation_log_anchors`,
    NOT on :attr:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs.primary_evidence_refs`).

    The implementation log anchor is recorded as the journal-path-plus-
    line-anchor string per doc-16:92-93 (the typed surface mirrors the
    Slice 15
    :data:`~iriai_build_v2.execution_control.governance_metrics.MetricCalibrationFixture.evidence_refs`
    convention: implementation_log_anchors are strings, not typed
    GovernanceEvidenceRef objects, per the doc-16:92-93 contract).

    :param anchor: the typed
        :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
        parsed from the journal (typically carrying
        :attr:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor.accepted` ``=True``
        for accepted plan deviations).
    :param rule: the typed :class:`FindingRule` looked up from
        :data:`~iriai_build_v2.execution_control.finding_rule_engine.REQUIRED_V1_FINDING_RULES`
        by ``rule_id="accepted_plan_deviation_v1"`` (or a future
        versioned rule). Caller-supplied so callers may exercise
        version-supersede paths.
    :param confidence: the typed float in ``[0.0, 1.0]``. Default ``0.8``
        per the conservative v1 calibration; the
        :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine`
        compares against the rule's
        :attr:`~iriai_build_v2.execution_control.finding_engine.FindingRule.min_confidence`
        per doc-16:193.
    :param severity: the typed
        :data:`~iriai_build_v2.execution_control.finding_engine.FindingSeverity`
        classification. Default ``"low"`` per the conservative v1
        calibration for accepted (i.e. already-agreed-to) deviations.
    :param feature_id: the feature scope; default ``None`` (cross-
        feature deviation).
    :param recommended_action_display: optional non-executable display
        text override; default builds a deterministic message from the
        anchor's :attr:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor.slice_id`
        + :attr:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor.event`.
    :returns: a typed
        :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs`
        bundle ready for
        :meth:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine.emit_finding`.
    """

    # Per doc-16:92-93 implementation_log_anchors are strings (the
    # journal-path-plus-line-anchor canonical form), NOT typed
    # GovernanceEvidenceRef objects. We construct the canonical anchor
    # string from the source ImplementationArtifactAnchor's journal_path
    # + line_start (when present) so downstream auditors can correlate
    # the emitted finding with the source journal line.
    if anchor.line_start is not None:
        log_anchor = f"{anchor.journal_path}:{anchor.line_start}"
    elif anchor.decision_log_line is not None:
        log_anchor = f"{anchor.journal_path}#L{anchor.decision_log_line}"
    else:
        log_anchor = anchor.journal_path

    if recommended_action_display is None:
        recommended_action_display = (
            f"Review accepted plan deviation in slice "
            f"{anchor.slice_id!r} (event={anchor.event!r}) "
            f"and confirm the deviation is captured in the slice's "
            f"acceptance record."
        )

    return FindingRuleEmissionInputs(
        rule=rule,
        class_name="accepted_plan_deviation",
        severity=severity,
        confidence=confidence,
        feature_id=feature_id,
        affected_scope={
            "slice_id": anchor.slice_id,
            "event": anchor.event,
        },
        # Doc-16:159-161 + EVIDENCE_GAP_FINDING_KINDS -- the
        # implementation_plan_deviation kind is explicitly allowed to
        # emit with empty primary_evidence_refs. The journal anchor is
        # recorded on implementation_log_anchors, NOT on
        # primary_evidence_refs (per doc-16:135 + doc-16:92-93).
        primary_evidence_refs=[],
        supporting_evidence_refs=[],
        implementation_log_anchors=[log_anchor],
        metric_refs=[],
        recommended_action_display=recommended_action_display,
        safe_runtime_action=False,
        requires_policy_artifact=False,
        product_defect_related=False,
        workflow_related=True,
        causal_role="contributing",
    )


def compute_implementation_journal_gap_inputs(
    journal_path: Path | str,
    rule: FindingRule,
    *,
    confidence: float = 0.9,
    severity: FindingSeverity = "high",
    feature_id: str | None = None,
    missing_event: JournalEventName | None = None,
    recommended_action_display: str | None = None,
) -> FindingRuleEmissionInputs:
    """Build a typed
    :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs`
    bundle for the ``implementation_journal_gap`` rule per doc-16:134 +
    doc-16:191-192 (*"Missing implementation logs: emit
    `implementation_journal_gap` and block plan-vs-actual
    recommendations."*).

    Per :data:`~iriai_build_v2.execution_control.finding_rule_engine.CLASS_NAME_TO_FINDING_KIND`
    the ``implementation_journal_gap`` class maps to the
    :data:`~iriai_build_v2.execution_control.finding_engine.FindingKind`
    ``"provenance_gap"``. Per :data:`~iriai_build_v2.execution_control.finding_rule_engine.EVIDENCE_GAP_FINDING_KINDS`
    the ``provenance_gap`` kind is explicitly allowed to emit with
    empty :attr:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs.primary_evidence_refs`
    (the missing log IS the gap by construction).

    :param journal_path: the journal source path the gap refers to;
        recorded on the implementation_log_anchors list as the
        canonical anchor string.
    :param rule: the typed :class:`FindingRule` looked up from
        :data:`~iriai_build_v2.execution_control.finding_rule_engine.REQUIRED_V1_FINDING_RULES`
        by ``rule_id="implementation_journal_gap_v1"``.
    :param confidence: the typed float in ``[0.0, 1.0]``. Default ``0.9``
        per the conservative v1 calibration (a missing log is a
        high-confidence signal per doc-16:191-192).
    :param severity: the typed
        :data:`~iriai_build_v2.execution_control.finding_engine.FindingSeverity`
        classification. Default ``"high"`` per doc-16:191-192 (the gap
        blocks plan-vs-actual recommendations until resolved).
    :param feature_id: the feature scope; default ``None``.
    :param missing_event: optional
        :data:`~iriai_build_v2.workflows.develop.governance.models.JournalEventName`
        annotation when the gap was identified by the absence of a
        specific event class (e.g. ``"accepted"`` for a slice that has
        no acceptance heading). Recorded on
        :attr:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs.affected_scope`.
    :param recommended_action_display: optional non-executable display
        text override; default builds a deterministic message naming
        the journal path + missing event (when present).
    :returns: a typed
        :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs`
        bundle ready for
        :meth:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine.emit_finding`.
    """

    journal_path_str = str(journal_path)

    if recommended_action_display is None:
        if missing_event is not None:
            recommended_action_display = (
                f"Implementation journal at {journal_path_str!r} is "
                f"missing the expected {missing_event!r} anchor; "
                f"block plan-vs-actual recommendations per "
                f"doc-16:191-192 until the journal is updated."
            )
        else:
            recommended_action_display = (
                f"Implementation journal at {journal_path_str!r} is "
                f"missing or empty; block plan-vs-actual "
                f"recommendations per doc-16:191-192 until the journal "
                f"is updated."
            )

    affected_scope: dict[str, Any] = {
        "journal_path": journal_path_str,
    }
    if missing_event is not None:
        affected_scope["missing_event"] = missing_event

    return FindingRuleEmissionInputs(
        rule=rule,
        class_name="implementation_journal_gap",
        severity=severity,
        confidence=confidence,
        feature_id=feature_id,
        affected_scope=affected_scope,
        # Doc-16:159-161 + EVIDENCE_GAP_FINDING_KINDS -- the
        # provenance_gap kind is explicitly allowed to emit with empty
        # primary_evidence_refs (the missing log IS the gap).
        primary_evidence_refs=[],
        supporting_evidence_refs=[],
        implementation_log_anchors=[journal_path_str],
        metric_refs=[],
        recommended_action_display=recommended_action_display,
        safe_runtime_action=False,
        # Doc-16:191-192 verbatim "block plan-vs-actual recommendations"
        # -- the gap finding is advisory until the gap is resolved; we
        # set requires_policy_artifact=False so the REUSED 2nd sub-slice
        # engine's confidence-threshold guard does NOT gate the emission
        # (the gap signal MUST surface even if confidence is below
        # threshold, per doc-16:191-192).
        requires_policy_artifact=False,
        product_defect_related=False,
        workflow_related=True,
        causal_role="primary",
    )


# --- The plan-deviation engine (per chunk-shape point 6) --------------------


class FindingPlanDeviationEngine:
    """Implementation-plan deviation engine for the
    ``accepted_plan_deviation`` (doc-16:135) +
    ``implementation_journal_gap`` (doc-16:134 + doc-16:191-192) finding
    classes (doc-16:164-165 § Refactoring Steps step 5; THIS SUB-SLICE
    owns these 2 classes; the 3rd-B sub-slice owns reviewer-findings +
    late-test-failure rules).

    Per doc-16:164-165 *"Add implementation-plan deviation rules over
    journal anchors, reviewer findings, accepted deviations, and late
    test failures."* the engine consumes:

    1. A typed :class:`PlanDeviationAnchorBundle` (built via
       :func:`parse_plan_deviation_anchors` from the Slice 13c
       :func:`~iriai_build_v2.workflows.develop.governance.journal_parser.parse_implementation_journal`
       output).
    2. A typed
       :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine`
       from the Slice 16 2nd sub-slice (REUSED via direct delegation;
       NOT re-implemented per ``feedback_no_overengineer_use_library``).

    And projects them onto:

    1. A list of typed
       :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
       records (one per emitted finding; the REUSED 2nd sub-slice
       engine's 7-guard logic ensures each emission is deterministic +
       deduped + invariant-respecting).
    2. A list of typed
       :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionGap`
       records (carried from the REUSED 2nd sub-slice engine's
       :attr:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine.gap_findings`
       property after each ``emit_finding`` call).
    3. A list of typed :class:`PlanDeviationParseGap` records
       (carried from the bundle's
       :attr:`PlanDeviationAnchorBundle.parse_gaps`).

    **Rule lookup (doc-16:155-156 step 1 carry).** The engine looks up
    rules by ``rule_id`` from the REUSED Slice 16 2nd sub-slice
    :data:`~iriai_build_v2.execution_control.finding_rule_engine.REQUIRED_V1_FINDING_RULES`
    tuple. The default ``rule_id`` map covers the 2 classes this
    sub-slice owns:

    * ``"accepted_plan_deviation"`` ->
      ``rule_id="accepted_plan_deviation_v1"``.
    * ``"implementation_journal_gap"`` ->
      ``rule_id="implementation_journal_gap_v1"``.

    Future rule versions (``v2`` / ``v3``) supplied via the
    ``rule_lookup`` constructor argument override the default; the
    REUSED 2nd sub-slice engine's suppression / expiry policies still
    apply.

    **Non-blocking observer contract (doc-14:242-243 inherited via
    Slice 14 + 15 + 16 2nd sub-slice precedent).** The engine NEVER
    raises a structural failure to the caller; every failure projects
    onto a typed
    :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionGap`
    (rule-emission failures; via the REUSED 2nd sub-slice engine) OR
    :class:`PlanDeviationParseGap` (parser failures; via this module's
    :func:`parse_plan_deviation_anchors` helper).

    **Per-call gap_findings accumulator reset (mirrors Slice 15 2nd +
    4th sub-slice + Slice 16 2nd sub-slice pattern).** Each call to
    :meth:`process_anchors` RESETS the local
    :attr:`gap_findings` + :attr:`parse_gaps` accumulators so per-call
    gap findings remain bounded; callers that need cross-call
    accumulation should snapshot the properties after each call.

    Example usage::

        from iriai_build_v2.execution_control.finding_plan_deviation_engine \\
            import (
                FindingPlanDeviationEngine,
                parse_plan_deviation_anchors,
            )
        from iriai_build_v2.execution_control.finding_rule_engine import (
            FindingRuleEngine,
        )

        bundle = parse_plan_deviation_anchors(
            "docs/execution-control-plane/implementation-journal.md"
        )
        rule_engine = FindingRuleEngine()
        plan_engine = FindingPlanDeviationEngine()
        findings = plan_engine.process_anchors(bundle, rule_engine)
        for finding in findings:
            # finding emitted; caller persists it.
            ...
        for gap in plan_engine.gap_findings:
            # rule failed to emit; caller logs the gap.
            ...
        for parse_gap in plan_engine.parse_gaps:
            # parser failed; caller logs the parse gap.
            ...
    """

    # Default class_name -> rule_id mapping for the 2 classes this
    # sub-slice owns; overridable via the rule_lookup constructor arg.
    DEFAULT_RULE_ID_MAP: dict[str, str] = {
        "accepted_plan_deviation": "accepted_plan_deviation_v1",
        "implementation_journal_gap": "implementation_journal_gap_v1",
    }

    def __init__(
        self,
        *,
        rule_lookup: dict[str, str] | None = None,
    ) -> None:
        """Construct a finding plan-deviation engine.

        :param rule_lookup: optional override map of
            ``class_name -> rule_id``. Defaults to
            :attr:`DEFAULT_RULE_ID_MAP` covering the 2 classes this
            sub-slice owns. Callers may override per per-corpus
            calibration (e.g. to route to a v2 rule).

        The engine is stateless aside from the :attr:`gap_findings` +
        :attr:`parse_gaps` accumulators the :meth:`process_anchors`
        surface populates. Each call RESETS both accumulators per the
        Slice 15 2nd + 4th sub-slice + Slice 16 2nd sub-slice
        precedent.
        """

        self._rule_lookup: dict[str, str] = dict(
            rule_lookup if rule_lookup is not None else self.DEFAULT_RULE_ID_MAP
        )
        self._gap_findings: list[Any] = []
        self._parse_gaps: list[PlanDeviationParseGap] = []

    @property
    def gap_findings(self) -> list[Any]:
        """The list of
        :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionGap`
        findings the most-recent :meth:`process_anchors` call produced
        via the REUSED 2nd sub-slice engine.

        Per the Slice 14 + Slice 15 2nd + 4th sub-slice + Slice 16 2nd
        sub-slice precedents the engine NEVER raises a failure to the
        caller -- every structural failure projects onto a typed gap
        finding.
        """

        return list(self._gap_findings)

    @property
    def parse_gaps(self) -> list[PlanDeviationParseGap]:
        """The list of :class:`PlanDeviationParseGap` records carried
        from the input :class:`PlanDeviationAnchorBundle.parse_gaps`
        the most-recent :meth:`process_anchors` call processed.

        Per the auto-memory ``feedback_no_silent_degradation`` rule
        every parser failure projects onto a typed gap rather than
        silently dropping the anchor.
        """

        return list(self._parse_gaps)

    @property
    def rule_lookup(self) -> dict[str, str]:
        """The configured ``class_name -> rule_id`` map (read-only
        view)."""

        return dict(self._rule_lookup)

    def process_anchors(
        self,
        bundle: PlanDeviationAnchorBundle,
        engine: FindingRuleEngine,
        *,
        now: datetime | None = None,
        confidence_accepted: float = 0.8,
        confidence_gap: float = 0.9,
    ) -> list[GovernanceFinding]:
        """Process the parsed journal anchor ``bundle`` through the
        REUSED Slice 16 2nd sub-slice ``engine`` and return the list of
        emitted :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
        records.

        Per doc-16:164-165 step 5 the surface emits:

        1. **One ``accepted_plan_deviation`` finding per heading anchor**
           with :attr:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor.accepted` ``=True``
           and :attr:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor.event` ``=="accepted"``
           (per doc-16:135). The accepted-deviation classifier is the
           accepted-heading signal: when a slice is recorded as accepted
           in the journal it represents an accepted (i.e. agreed-to)
           deviation from the original plan.
        2. **One ``implementation_journal_gap`` finding when the bundle
           has no anchors** (per doc-16:134 + doc-16:191-192 verbatim
           *"Missing implementation logs: emit `implementation_journal_gap`
           and block plan-vs-actual recommendations."*). The gap rule
           emits when the parser found NO recognised anchors -- the
           absence of any acceptance signal IS the gap.

        Per the auto-memory ``feedback_no_silent_degradation`` rule:

        * Rule lookup failures (no rule found for the class_name) ->
          gap finding accumulated on :attr:`gap_findings` via the REUSED
          2nd sub-slice engine; no finding emitted.
        * Per the REUSED 2nd sub-slice engine's 7-guard logic: every
          suppression / expiry / at-least-one-primary / product-workflow
          separation / confidence-threshold / idempotency / construction
          failure projects onto a typed gap finding on
          :attr:`gap_findings`; the engine NEVER raises.

        Per doc-14:242-243 NEVER raises a failure to the caller. Any
        structural failure projects onto a typed gap finding.

        The method RESETS the :attr:`gap_findings` + :attr:`parse_gaps`
        accumulators at entry; per-call gap findings remain bounded.

        :param bundle: the typed
            :class:`PlanDeviationAnchorBundle` produced by
            :func:`parse_plan_deviation_anchors`.
        :param engine: the typed REUSED Slice 16 2nd sub-slice
            :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine`.
            The plan-deviation engine builds typed
            :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs`
            bundles and DELEGATES to ``engine.emit_finding`` for the
            7-guard logic.
        :param now: optional datetime override for the expiry check
            (defaults to live clock). Passed through to
            :meth:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine.emit_finding`.
        :param confidence_accepted: optional confidence override for
            ``accepted_plan_deviation`` emissions. Default ``0.8`` per
            the conservative v1 calibration.
        :param confidence_gap: optional confidence override for
            ``implementation_journal_gap`` emissions. Default ``0.9``
            per the conservative v1 calibration (a missing log is a
            high-confidence signal per doc-16:191-192).
        :returns: the list of emitted typed
            :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
            records. Validly empty when no rules emit (e.g. the bundle
            has no accepted heading anchors AND the bundle has at least
            one anchor of any kind -- which means the gap rule does not
            fire).
        """

        # Reset per-call accumulators (mirrors Slice 15 2nd + 4th
        # sub-slice + Slice 16 2nd sub-slice pattern).
        self._gap_findings = []
        self._parse_gaps = list(bundle.parse_gaps)

        findings: list[GovernanceFinding] = []

        # ── Pass 1: accepted_plan_deviation rule ────────────────────────────
        # Per doc-16:135 + doc-16:164-165 -- emit one finding per
        # accepted-heading anchor (event == "accepted"). The accepted
        # heading is the canonical acceptance signal in the journal
        # markdown per the Slice 13c parser (journal_parser.py:118-130).
        accepted_rule_id = self._rule_lookup.get("accepted_plan_deviation")
        if accepted_rule_id is not None:
            accepted_rule = _lookup_rule(accepted_rule_id)
            if accepted_rule is None:
                # Per feedback_no_silent_degradation: a missing rule
                # projects onto a typed gap finding rather than raising.
                # The REUSED 2nd sub-slice FindingRuleEmissionGap shape
                # is the typed surface for rule-emission failures.
                self._gap_findings.append(
                    FindingRuleEmissionGap(
                        failure_id=FINDING_RULE_EMISSION_FAILURE_ID,
                        rule_id=accepted_rule_id,
                        rule_version="<unknown>",
                        class_name="accepted_plan_deviation",
                        attempted_idempotency_key=None,
                        reason="rule_id_not_found_in_v1_rules",
                        evidence_payload={
                            "rule_lookup_class_name": "accepted_plan_deviation",
                            "rule_lookup_rule_id": accepted_rule_id,
                        },
                    )
                )
            else:
                for anchor in bundle.anchors:
                    if not anchor.accepted:
                        continue
                    if anchor.event != "accepted":
                        # Only the "accepted" event class is the
                        # acceptance signal per the Slice 13c parser.
                        continue
                    inputs = compute_accepted_plan_deviation_inputs(
                        anchor,
                        accepted_rule,
                        confidence=confidence_accepted,
                    )
                    finding = engine.emit_finding(inputs, now=now)
                    # Carry the REUSED 2nd sub-slice engine's per-call
                    # gap findings onto this engine's accumulator so the
                    # caller sees all rule-application gaps in one place.
                    self._gap_findings.extend(engine.gap_findings)
                    if finding is not None:
                        findings.append(finding)

        # ── Pass 2: implementation_journal_gap rule ─────────────────────────
        # Per doc-16:134 + doc-16:191-192 verbatim -- emit when the
        # bundle has NO anchors (the parser found no recognised
        # acceptance / starting / complete / finding / test_result /
        # subagent / decision anchors; the journal is empty OR has no
        # recognised pattern). The gap finding blocks plan-vs-actual
        # recommendations per the doc-16:191-192 contract.
        gap_rule_id = self._rule_lookup.get("implementation_journal_gap")
        if gap_rule_id is not None and len(bundle.anchors) == 0:
            gap_rule = _lookup_rule(gap_rule_id)
            if gap_rule is None:
                self._gap_findings.append(
                    FindingRuleEmissionGap(
                        failure_id=FINDING_RULE_EMISSION_FAILURE_ID,
                        rule_id=gap_rule_id,
                        rule_version="<unknown>",
                        class_name="implementation_journal_gap",
                        attempted_idempotency_key=None,
                        reason="rule_id_not_found_in_v1_rules",
                        evidence_payload={
                            "rule_lookup_class_name": "implementation_journal_gap",
                            "rule_lookup_rule_id": gap_rule_id,
                        },
                    )
                )
            else:
                inputs = compute_implementation_journal_gap_inputs(
                    bundle.journal_path,
                    gap_rule,
                    confidence=confidence_gap,
                )
                finding = engine.emit_finding(inputs, now=now)
                self._gap_findings.extend(engine.gap_findings)
                if finding is not None:
                    findings.append(finding)

        return findings
