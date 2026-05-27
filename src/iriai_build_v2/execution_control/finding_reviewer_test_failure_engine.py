"""Slice 16 3rd-B sub-slice -- reviewer-finding + late-test-failure engine
for the doc-16:164-165 step-5 remaining categories (per
``docs/execution-control-plane/16-finding-engine-and-taxonomy.md`` §
Refactoring Steps **step 5** at doc-16:164-165: *"Add implementation-plan
deviation rules over journal anchors, reviewer findings, accepted
deviations, and late test failures."*).

This module owns the **reviewer-finding consumption code path** AND the
**late-test-failure consumption code path** that bridge:

* The Slice 13c
  :func:`~iriai_build_v2.workflows.develop.governance.journal_parser.parse_implementation_journal`
  typed output -- specifically the per-line ``event="finding"`` anchors
  (per ``journal_parser.py:514-535``) the parser emits for every matched
  finding-id in the journal markdown, AND the per-line
  ``event="test_result"`` anchors (per ``journal_parser.py:584-595``) the
  parser emits for every matched ``N passed`` line in the journal
  markdown.
* The Slice 13d
  :func:`~iriai_build_v2.workflows.develop.governance.decision_log_parser.parse_implementation_decision_log`
  typed output (per ``decision_log_parser.py:455``) -- specifically the
  ``event="test_result"`` rows the parser emits for cross-corpus
  late-test-failure signal (the JSONL decision log is the second
  evidence corpus that can carry test-result signal).

Into the Slice 16 2nd sub-slice
:class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine`
emitter surface (per ``finding_rule_engine.py:740``).

**Chunk-shape SPLIT decision (3rd-A vs 3rd-B; no further sub-SPLIT).**
Per the STATUS.md § "Next safe action" the 3rd sub-slice SPLIT into 3rd-A
+ 3rd-B (mirrors Slice 12a-1/12a-2/12a-3 + Slice 15 3rd+4th+5th sub-slice
precedent). The 3rd-A sub-slice landed
:mod:`iriai_build_v2.execution_control.finding_plan_deviation_engine`
covering ``accepted_plan_deviation`` (doc-16:135) +
``implementation_journal_gap`` (doc-16:134 + doc-16:191-192). This 3rd-B
sub-slice covers the two remaining step-5 categories: **reviewer-findings**
+ **late-test-failures**.

**No further SPLIT.** Both categories land together in this module (NOT
3rd-B-1 + 3rd-B-2). Rationale (documented in the 3rd-B STARTING journal
entry):

* Both rule categories share the same engine surface shape (parse anchors
  + lookup rule + delegate to REUSED 2nd sub-slice
  :meth:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine.emit_finding`).
* Both consume the same Slice 13c parsed bundle (different ``event``
  fields on the same typed
  :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`);
  the late-test-failure path ADDITIONALLY consumes the Slice 13d
  decision-log parser typed output for cross-corpus signal.
* Combined size fits the 600-1200 outer band per P3-16-3A-1 + P3-16-2-1 +
  P3-16-1-2 lineage; a further SPLIT would double the journal + STATUS
  overhead for two ~400-line modules that share the same parse + delegate
  pattern + the same typed-failure-id.

**Class-name mapping (both 3rd-B classes emit
``governance_evidence_conflict``).** Per the doc-16 v1 16-class taxonomy
(``REQUIRED_V1_FINDING_CLASS_NAMES`` at doc-16:120-137) BOTH rule
categories the 3rd-B sub-slice owns map to a class name already in the
16-class set. To keep the v1 taxonomy stable + the post-acceptance
conservative-calibration discipline (per doc-16:46 *"conservative enough
to avoid runaway self-improvement"*), this sub-slice uses ``governance_evidence_conflict``
(doc-16:137) for BOTH rules:

* **Reviewer-finding rule** -> ``governance_evidence_conflict`` per
  doc-16:183-184 verbatim: *"Conflicting evidence: lower confidence and
  emit a `governance_evidence_conflict` finding if conflict affects a
  policy decision."* A reviewer finding emitted on the journal is BY
  DEFINITION a conflict between the agent's output + the reviewer's
  independent verdict; emitting ``governance_evidence_conflict`` is the
  conservative v1 calibration.
* **Late-test-failure rule** -> ``governance_evidence_conflict`` per
  doc-16:183-184. A late test failure is BY DEFINITION a conflict
  between the prior acceptance evidence (the heading anchor with
  ``accepted=True`` per the Slice 13c parser) + the post-acceptance
  test-result evidence (the per-line ``event="test_result"`` anchor);
  emitting ``governance_evidence_conflict`` is the conservative v1
  calibration. Subsequent rule versions MAY tighten by introducing a
  distinct ``late_test_failure_*`` class via the doc-16:215-217 rule-
  version supersede path; the v1 calibration keeps the taxonomy
  stable.

Both classes map to the typed :data:`~iriai_build_v2.execution_control.finding_engine.FindingKind`
``"governance_evidence_conflict"`` per
:data:`~iriai_build_v2.execution_control.finding_rule_engine.CLASS_NAME_TO_FINDING_KIND`;
the kind is in :data:`~iriai_build_v2.execution_control.finding_rule_engine.EVIDENCE_GAP_FINDING_KINDS`
so the at-least-one-primary invariant is satisfied by construction (the
conflict itself IS the gap; either side cannot be the primary per
doc-16:185-187).

**Distinct rule_ids per category.** Per doc-16:155-156 step 1 *"Convert
existing process-improvement logic into versioned finding rules"* the
two rules carry distinct ``rule_id`` values so suppression / expiry
policies can target each rule independently per doc-16:168-169 +
doc-16:215-217:

* ``governance_evidence_conflict_v1`` -- the v1 rule for reviewer-finding
  emissions (the class-name -> rule_id map in
  :attr:`FindingReviewerTestFailureEngine.DEFAULT_RULE_ID_MAP`).
* The late-test-failure rule REUSES the same
  ``governance_evidence_conflict_v1`` rule by default (both classes emit
  the same kind); subsequent rule versions MAY introduce a distinct
  ``late_test_failure_v1`` rule per the doc-16:215-217 rule-version
  supersede path. The default map carries a single entry for the
  ``governance_evidence_conflict`` class name; both reviewer-finding +
  late-test-failure helpers build the typed
  :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs`
  bundle with ``class_name="governance_evidence_conflict"`` so the
  REUSED 2nd sub-slice engine's idempotency key (per doc-16:158) carries
  the canonical class identity.

**REUSE discipline (the auto-memory ``feedback_no_overengineer_use_library``
rule).** This module REUSES (NOT redefines):

* Slice 13a
  :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
  -- the typed-surface contract both Slice 13c + 13d parsers populate.
* Slice 13a
  :data:`~iriai_build_v2.workflows.develop.governance.models.JournalEventName`
  -- the 7-value Literal taxonomy this module's engine pivots on
  (``event="finding"`` vs ``event="test_result"`` per
  ``journal_parser.py:171-172`` + ``decision_log_parser.py:217-218``).
* Slice 13a
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  -- the typed evidence-ref contract the Slice 16 1st sub-slice
  :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
  carries.
* Slice 13c
  :func:`~iriai_build_v2.workflows.develop.governance.journal_parser.parse_implementation_journal`
  -- the typed parser function that projects the implementation journal
  markdown to a ``list[ImplementationArtifactAnchor]``.
* Slice 13d
  :func:`~iriai_build_v2.workflows.develop.governance.decision_log_parser.parse_implementation_decision_log`
  -- the typed parser function that projects the implementation
  decision-log JSONL to a ``list[ImplementationArtifactAnchor]``.
* Slice 16 1st sub-slice
  :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
  + :data:`~iriai_build_v2.execution_control.finding_engine.FindingKind`
  + :data:`~iriai_build_v2.execution_control.finding_engine.FindingSeverity`
  + :data:`~iriai_build_v2.execution_control.finding_engine.FindingCausalRole`
  -- the typed-shape foundation.
* Slice 16 2nd sub-slice
  :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine`
  -- the 7-guard emitter logic (suppression + expiry + at-least-one-
  primary + product/workflow separation + confidence threshold +
  idempotency + construction). This module does NOT re-implement the
  7-guard logic; the
  :class:`FindingReviewerTestFailureEngine.process_anchors` surface
  builds typed
  :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs`
  bundles and DELEGATES to
  :meth:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine.emit_finding`
  (mirrors the Slice 16 3rd-A sub-slice
  :class:`~iriai_build_v2.execution_control.finding_plan_deviation_engine.FindingPlanDeviationEngine`
  REUSE pattern verbatim).
* Slice 16 2nd sub-slice
  :data:`~iriai_build_v2.execution_control.finding_rule_engine.CLASS_NAME_TO_FINDING_KIND`
  -- the typed mapping from ``governance_evidence_conflict`` ->
  ``"governance_evidence_conflict"`` (per
  ``finding_rule_engine.py:248``).
* Slice 16 2nd sub-slice
  :data:`~iriai_build_v2.execution_control.finding_rule_engine.EVIDENCE_GAP_FINDING_KINDS`
  -- the 3-value tuple naming the kinds allowed to emit with empty
  ``primary_evidence_refs`` (per doc-16:159-161). The
  ``governance_evidence_conflict`` kind is in this tuple, so BOTH 3rd-B
  rules emit with empty primary refs by construction (the conflict itself
  IS the gap).
* Slice 16 2nd sub-slice
  :data:`~iriai_build_v2.execution_control.finding_rule_engine.REQUIRED_V1_FINDING_RULES`
  -- the 16-entry typed rule tuple. This module's
  :class:`FindingReviewerTestFailureEngine.process_anchors` surface
  looks up the rule by ``rule_id="governance_evidence_conflict_v1"``
  from this tuple.

**Per the auto-memory ``feedback_no_silent_degradation`` rule** every
failure mode projects onto a typed gap finding rather than raising into
the caller:

* Anchor-parse failures from EITHER parser (missing file / unparseable
  markdown / unparseable JSONL / Pydantic validation error on a parsed
  anchor / JSONL row missing required fields) -> typed
  :class:`ReviewerTestFailureParseGap` accumulated on the
  :class:`ReviewerTestFailureAnchorBundle.parse_gaps` list; the bundle's
  ``journal_anchors`` / ``decision_log_anchors`` lists are returned
  empty for the failing parser (the non-failing parser's anchors are
  preserved).
* Rule-emission failures (suppression / expiry / at-least-one-primary
  invariant / product-workflow separation / confidence threshold /
  idempotency-key computation / construction) -> typed
  :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionGap`
  accumulated on the REUSED
  :attr:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine.gap_findings`
  property (the engine's own per-call accumulator); the engine NEVER
  raises per doc-14:242-243.

**The NEW typed failure id**
:data:`FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID`
(``"finding_reviewer_test_failure_parse_failed"``) registers in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` under
the EXISTING ``evidence_corruption`` failure_class with the EXISTING
NON-blocking ``retry_governance_projection`` RouteAction (REUSED from
Slice 14 2nd sub-slice + Slice 15 2nd + 4th sub-slices + Slice 16 2nd +
3rd-A sub-slices; NOT a new RouteAction). This mirrors the Slice 16 2nd
+ 3rd-A sub-slice precedent verbatim.

**Implementation discipline.** Stdlib (``datetime`` + ``pathlib``) +
Pydantic v2 + Slice 13a ``governance.models`` + Slice 13c
``journal_parser`` + Slice 13d ``decision_log_parser`` + Slice 16 1st
sub-slice ``finding_engine`` + Slice 16 2nd sub-slice
``finding_rule_engine`` only. NO imports from other parts of
``execution_control/`` (this module is foundational for the Slice 16
4th sub-slice that persists findings). NO imports from
``workflows/develop/execution/phases/`` / ``supervisor`` / ``dashboard``
(those would be downstream consumers, not dependencies).

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.finding_plan_deviation_engine`
(Slice 16 3rd-A sub-slice) +
:mod:`iriai_build_v2.execution_control.finding_rule_engine` (Slice 16 2nd
sub-slice) + :mod:`iriai_build_v2.execution_control.finding_engine`
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
from iriai_build_v2.workflows.develop.governance.decision_log_parser import (
    parse_implementation_decision_log,
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
    # NON-blocking RouteAction; mirrors Slice 16 2nd + 3rd-A sub-slice
    # precedent verbatim).
    "FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID",
    # Typed inputs / outputs (mirrors Slice 16 3rd-A typed-bundle pattern).
    "ReviewerTestFailureAnchorBundle",
    "ReviewerTestFailureParseGap",
    # Pure helpers (mirrors Slice 16 3rd-A
    # compute_accepted_plan_deviation_inputs +
    # compute_implementation_journal_gap_inputs pattern).
    "parse_reviewer_test_failure_anchors",
    "compute_reviewer_finding_inputs",
    "compute_late_test_failure_inputs",
    # The engine class (mirrors Slice 16 3rd-A
    # FindingPlanDeviationEngine pattern).
    "FindingReviewerTestFailureEngine",
]


# --- Typed failure id (doc-16:164-165 + doc-14:242-243 NON-BLOCKING) --------


FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID: Literal[
    "finding_reviewer_test_failure_parse_failed"
] = "finding_reviewer_test_failure_parse_failed"
"""Doc-16:164-165 + doc-14:242-243 -- the typed failure id the
reviewer-finding + late-test-failure engine projects onto when a
structural anchor-parse failure occurs in EITHER the Slice 13c journal
parser OR the Slice 13d decision-log parser (e.g. missing journal /
decision-log file; unparseable markdown / JSONL body; Pydantic
validation error on a candidate
:class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`).

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice +
Slice 15 2nd + 4th sub-slices + Slice 16 2nd + 3rd-A sub-slices; NOT a
new route action).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 16 pattern (this id + the Slice 16 2nd sub-slice
``finding_rule_emission_failed`` + the Slice 16 3rd-A sub-slice
``finding_plan_deviation_parse_failed``) matches the Slice 14 + Slice 15
non-blocking governance projection observer.

The Slice 14 + Slice 15 + Slice 16 2nd + 3rd-A sub-slice precedents are
the source-of-truth for the non-blocking governance-projection failure-
routing discipline:

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
* This module (Slice 16 3rd-B) defines
  ``finding_reviewer_test_failure_parse_failed``.

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to reviewer-finding +
late-test-failure anchor parsing failures (this slice is also a post-
checkpoint governance projection observer).
"""


# --- Typed bundle for parsed journal + decision-log anchors -----------------


class ReviewerTestFailureParseGap(BaseModel):
    """Typed governance-gap finding produced when the
    :func:`parse_reviewer_test_failure_anchors` surface fails to parse
    EITHER the Slice 13c implementation journal OR the Slice 13d
    implementation decision log (e.g. missing file; unparseable markdown
    / JSONL body; Pydantic validation error on a candidate
    :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`).

    Mirrors the Slice 14
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    + Slice 15 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_metric_extractor.GovernanceMetricExtractionGap`
    + Slice 15 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardPersistenceGap`
    + Slice 16 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionGap`
    + Slice 16 3rd-A sub-slice
    :class:`~iriai_build_v2.execution_control.finding_plan_deviation_engine.PlanDeviationParseGap`
    shapes verbatim per the chunk-shape contract.

    Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED here
    per doc-15:140-145 + doc-16 governance-projection discipline) the
    gap finding is NON-blocking: the caller MUST NOT propagate it to
    the executor / checkpoint / merge-queue / resume code paths. The
    corresponding typed failure id
    :data:`FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID`
    (``finding_reviewer_test_failure_parse_failed``) registers under
    the EXISTING ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
    NOT a new route action).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`ValidationError` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["finding_reviewer_test_failure_parse_failed"]
    """Doc-16:164-165 + doc-14:242-243 -- the typed failure id. Registers
    under the EXISTING ``evidence_corruption`` failure_class with NON-
    blocking routing per doc-14:242-243."""

    source_path: str
    """The source path the parse attempt targeted (the journal markdown
    OR the decision-log JSONL); recorded so downstream consumers can
    correlate the gap with the source artifact. Always a non-empty
    string -- empty paths fail closed with a typed Pydantic
    ``ValidationError``."""

    source_kind: Literal["journal", "decision_log"]
    """Which parser produced the gap. ``"journal"`` for the Slice 13c
    markdown parser; ``"decision_log"`` for the Slice 13d JSONL parser.
    Lets the caller / downstream auditor know which corpus to inspect
    when reviewing the gap."""

    reason: str
    """Free-form gap reason naming the specific failure mode (e.g.
    ``"journal_file_missing"`` / ``"decision_log_file_missing"`` /
    ``"journal_parse_validation_error"`` /
    ``"decision_log_parse_validation_error"`` /
    ``"unexpected_parser_exception"`` /
    ``"source_path_empty"``)."""

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


class ReviewerTestFailureAnchorBundle(BaseModel):
    """Typed bundle of parsed implementation-journal + implementation-
    decision-log anchors the :class:`FindingReviewerTestFailureEngine`
    consumes per doc-16:164-165 step 5.

    Per doc-16:164-165 *"Add implementation-plan deviation rules over
    journal anchors, reviewer findings, accepted deviations, and late
    test failures."* the engine's primary inputs are the parsed
    ``list[ImplementationArtifactAnchor]`` from BOTH parsers:

    * The Slice 13c
      :func:`~iriai_build_v2.workflows.develop.governance.journal_parser.parse_implementation_journal`
      output -- the per-line ``event="finding"`` + ``event="test_result"``
      anchors per ``journal_parser.py:514-535`` + ``journal_parser.py:584-595``.
    * The Slice 13d
      :func:`~iriai_build_v2.workflows.develop.governance.decision_log_parser.parse_implementation_decision_log`
      output -- the per-row ``event="test_result"`` anchors per
      ``decision_log_parser.py:455`` (cross-corpus late-test-failure
      signal).

    The bundle composes:

    * ``journal_path`` -- the journal source path (always a non-empty
      string).
    * ``decision_log_path`` -- the decision-log source path (always a
      non-empty string).
    * ``journal_anchors`` -- the typed ``list[ImplementationArtifactAnchor]``
      from the Slice 13c parser (validly empty when the parser found no
      anchors OR projected a typed gap).
    * ``decision_log_anchors`` -- the typed ``list[ImplementationArtifactAnchor]``
      from the Slice 13d parser (validly empty similarly).
    * ``parse_gaps`` -- the typed ``list[ReviewerTestFailureParseGap]``
      accumulated during parsing (BOTH parsers' failures land here).

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. The
    :attr:`ReviewerTestFailureAnchorBundle.journal_anchors` +
    :attr:`ReviewerTestFailureAnchorBundle.decision_log_anchors` fields
    are ``list``s of the Slice 13a typed
    :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
    (REUSED via direct import; NOT redefined here per doc-13a:285-287
    step 9 + doc-16:201-291).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`ValidationError` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    journal_path: str
    """The journal source path the journal anchors were parsed from
    (always a non-empty string; empty paths fail closed with a typed
    :class:`ValidationError`)."""

    decision_log_path: str
    """The decision-log source path the decision-log anchors were parsed
    from (always a non-empty string; empty paths fail closed with a typed
    :class:`ValidationError`)."""

    journal_anchors: list[ImplementationArtifactAnchor] = Field(default_factory=list)
    """The typed ``list[ImplementationArtifactAnchor]`` from the Slice 13c
    :func:`~iriai_build_v2.workflows.develop.governance.journal_parser.parse_implementation_journal`
    function (per ``journal_parser.py:320``). Validly empty when the
    parser found no recognised anchors OR when the parser failed and
    projected a typed gap onto :attr:`parse_gaps`."""

    decision_log_anchors: list[ImplementationArtifactAnchor] = Field(
        default_factory=list
    )
    """The typed ``list[ImplementationArtifactAnchor]`` from the Slice 13d
    :func:`~iriai_build_v2.workflows.develop.governance.decision_log_parser.parse_implementation_decision_log`
    function (per ``decision_log_parser.py:455``). Validly empty when
    the parser found no recognised rows OR when the parser failed and
    projected a typed gap onto :attr:`parse_gaps`."""

    parse_gaps: list[ReviewerTestFailureParseGap] = Field(default_factory=list)
    """The typed list of
    :class:`ReviewerTestFailureParseGap` findings accumulated during
    parsing. Defaults to empty. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every parser failure (from
    EITHER the journal parser OR the decision-log parser) projects onto
    a typed gap rather than silently dropping the anchor."""


# --- Pure helpers (mirrors Slice 16 3rd-A pure-helper pattern) --------------


def _parse_journal(
    journal_path: Path | str,
    *,
    body: str | None,
    journal_path_str: str,
) -> tuple[list[ImplementationArtifactAnchor], list[ReviewerTestFailureParseGap]]:
    """Run the Slice 13c parser and project any failures onto typed
    gap findings.

    Internal helper used by :func:`parse_reviewer_test_failure_anchors`
    to keep the typed-gap projection isolated per parser. The
    ``journal_path_str`` argument MUST be the already-normalised
    non-empty string the caller built (the helper does not re-validate
    emptiness).

    Returns a 2-tuple ``(anchors, gaps)`` -- on success
    ``anchors == parsed`` + ``gaps == []``; on failure ``anchors == []``
    + ``gaps == [<one typed gap>]``.
    """

    try:
        anchors = parse_implementation_journal(journal_path, body=body)
    except FileNotFoundError as exc:
        return (
            [],
            [
                ReviewerTestFailureParseGap(
                    failure_id=FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID,
                    source_path=journal_path_str,
                    source_kind="journal",
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
        return (
            [],
            [
                ReviewerTestFailureParseGap(
                    failure_id=FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID,
                    source_path=journal_path_str,
                    source_kind="journal",
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
        return (
            [],
            [
                ReviewerTestFailureParseGap(
                    failure_id=FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID,
                    source_path=journal_path_str,
                    source_kind="journal",
                    reason="unexpected_parser_exception",
                    anchor_kind="",
                    evidence_payload={
                        "error_type": type(exc).__name__,
                        "error_detail": str(exc)[:500],
                    },
                )
            ],
        )
    return (list(anchors), [])


def _parse_decision_log(
    decision_log_path: Path | str,
    *,
    body: str | None,
    decision_log_path_str: str,
) -> tuple[list[ImplementationArtifactAnchor], list[ReviewerTestFailureParseGap]]:
    """Run the Slice 13d parser and project any failures onto typed
    gap findings.

    Internal helper used by :func:`parse_reviewer_test_failure_anchors`
    to keep the typed-gap projection isolated per parser. The
    ``decision_log_path_str`` argument MUST be the already-normalised
    non-empty string the caller built (the helper does not re-validate
    emptiness).

    Returns a 2-tuple ``(anchors, gaps)`` -- on success
    ``anchors == parsed`` + ``gaps == []``; on failure ``anchors == []``
    + ``gaps == [<one typed gap>]``.
    """

    try:
        anchors = parse_implementation_decision_log(decision_log_path, body=body)
    except FileNotFoundError as exc:
        return (
            [],
            [
                ReviewerTestFailureParseGap(
                    failure_id=FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID,
                    source_path=decision_log_path_str,
                    source_kind="decision_log",
                    reason="decision_log_file_missing",
                    anchor_kind="",
                    evidence_payload={
                        "error_type": type(exc).__name__,
                        "error_detail": str(exc)[:500],
                    },
                )
            ],
        )
    except ValueError as exc:
        return (
            [],
            [
                ReviewerTestFailureParseGap(
                    failure_id=FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID,
                    source_path=decision_log_path_str,
                    source_kind="decision_log",
                    reason="decision_log_parse_validation_error",
                    anchor_kind="",
                    evidence_payload={
                        "error_type": type(exc).__name__,
                        "error_detail": str(exc)[:500],
                    },
                )
            ],
        )
    except Exception as exc:  # pragma: no cover -- defence-in-depth
        return (
            [],
            [
                ReviewerTestFailureParseGap(
                    failure_id=FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID,
                    source_path=decision_log_path_str,
                    source_kind="decision_log",
                    reason="unexpected_parser_exception",
                    anchor_kind="",
                    evidence_payload={
                        "error_type": type(exc).__name__,
                        "error_detail": str(exc)[:500],
                    },
                )
            ],
        )
    return (list(anchors), [])


def parse_reviewer_test_failure_anchors(
    journal_path: Path | str,
    decision_log_path: Path | str,
    *,
    journal_body: str | None = None,
    decision_log_body: str | None = None,
) -> ReviewerTestFailureAnchorBundle:
    """Parse BOTH the implementation journal at ``journal_path`` AND
    the implementation decision log at ``decision_log_path`` into a
    typed :class:`ReviewerTestFailureAnchorBundle`.

    The function delegates to:

    * Slice 13c
      :func:`~iriai_build_v2.workflows.develop.governance.journal_parser.parse_implementation_journal`
      -- the journal markdown parser; produces per-line ``event="finding"``
      anchors (per ``journal_parser.py:514-535``) + per-line
      ``event="test_result"`` anchors (per ``journal_parser.py:584-595``).
    * Slice 13d
      :func:`~iriai_build_v2.workflows.develop.governance.decision_log_parser.parse_implementation_decision_log`
      -- the decision-log JSONL parser; produces per-row anchors with
      ``event`` resolved per the 13d stage/event mapping (per
      ``decision_log_parser.py:455``).

    Per the auto-memory ``feedback_no_silent_degradation`` rule every
    parser failure projects onto a typed
    :class:`ReviewerTestFailureParseGap` rather than raising into the
    caller. The two parsers are run INDEPENDENTLY -- a failure in ONE
    parser does NOT prevent the OTHER parser from running. The gap list
    accumulates BOTH parsers' failures (one typed gap per failing
    parser).

    On success the returned bundle carries the parsed
    ``list[ImplementationArtifactAnchor]`` from each parser on
    :attr:`ReviewerTestFailureAnchorBundle.journal_anchors` +
    :attr:`ReviewerTestFailureAnchorBundle.decision_log_anchors` and an
    empty :attr:`ReviewerTestFailureAnchorBundle.parse_gaps`.

    :param journal_path: the journal source path; recorded into the
        bundle's :attr:`ReviewerTestFailureAnchorBundle.journal_path`
        (as ``str(journal_path)``) whether the parse succeeded or
        failed. Empty paths fail-closed with a typed gap (the bundle's
        ``journal_path`` carries ``"<unspecified>"`` sentinel).
    :param decision_log_path: the decision-log source path; recorded
        into the bundle's
        :attr:`ReviewerTestFailureAnchorBundle.decision_log_path` (as
        ``str(decision_log_path)``) whether the parse succeeded or
        failed. Empty paths fail-closed similarly with ``"<unspecified>"``.
    :param journal_body: optional pre-loaded markdown body; passed
        through to the Slice 13c parser. When ``None`` (production
        caller path) the parser reads from disk. Test-only escape hatch.
    :param decision_log_body: optional pre-loaded JSONL body; passed
        through to the Slice 13d parser. When ``None`` (production
        caller path) the parser reads from disk. Test-only escape hatch.
    :returns: a typed :class:`ReviewerTestFailureAnchorBundle` -- on
        success carrying the parsed anchors on
        :attr:`ReviewerTestFailureAnchorBundle.journal_anchors` +
        :attr:`ReviewerTestFailureAnchorBundle.decision_log_anchors` +
        empty :attr:`ReviewerTestFailureAnchorBundle.parse_gaps`;
        on failure carrying empty anchors for the failing parser + the
        typed gap on :attr:`ReviewerTestFailureAnchorBundle.parse_gaps`.
        NEVER raises.
    """

    # Doc-13:147 -- both paths are stable cross-process freshness anchors.
    # Always coerce to str so the typed surface is uniform.
    journal_path_str = str(journal_path)
    decision_log_path_str = str(decision_log_path)

    # Per the typed surface contract both path fields are non-empty;
    # fail-closed empty-path handling uses the "<unspecified>" sentinel
    # per the Slice 16 3rd-A sub-slice precedent.
    parse_gaps: list[ReviewerTestFailureParseGap] = []
    journal_anchors: list[ImplementationArtifactAnchor] = []
    decision_log_anchors: list[ImplementationArtifactAnchor] = []

    if not journal_path_str.strip():
        journal_path_str = "<unspecified>"
        parse_gaps.append(
            ReviewerTestFailureParseGap(
                failure_id=FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID,
                source_path="<unspecified>",
                source_kind="journal",
                reason="source_path_empty",
                anchor_kind="",
                evidence_payload={
                    "supplied_journal_path": str(journal_path),
                },
            )
        )
    else:
        journal_anchors, journal_gaps = _parse_journal(
            journal_path,
            body=journal_body,
            journal_path_str=journal_path_str,
        )
        parse_gaps.extend(journal_gaps)

    if not decision_log_path_str.strip():
        decision_log_path_str = "<unspecified>"
        parse_gaps.append(
            ReviewerTestFailureParseGap(
                failure_id=FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID,
                source_path="<unspecified>",
                source_kind="decision_log",
                reason="source_path_empty",
                anchor_kind="",
                evidence_payload={
                    "supplied_decision_log_path": str(decision_log_path),
                },
            )
        )
    else:
        decision_log_anchors, decision_log_gaps = _parse_decision_log(
            decision_log_path,
            body=decision_log_body,
            decision_log_path_str=decision_log_path_str,
        )
        parse_gaps.extend(decision_log_gaps)

    return ReviewerTestFailureAnchorBundle(
        journal_path=journal_path_str,
        decision_log_path=decision_log_path_str,
        journal_anchors=journal_anchors,
        decision_log_anchors=decision_log_anchors,
        parse_gaps=parse_gaps,
    )


def _lookup_rule(rule_id: str) -> FindingRule | None:
    """Return the :class:`FindingRule` from
    :data:`~iriai_build_v2.execution_control.finding_rule_engine.REQUIRED_V1_FINDING_RULES`
    matching ``rule_id``, or ``None`` when no match.

    Pure helper (no side effects). Mirrors the Slice 16 3rd-A sub-slice
    :func:`~iriai_build_v2.execution_control.finding_plan_deviation_engine._lookup_rule`
    discipline verbatim.
    """

    for rule in REQUIRED_V1_FINDING_RULES:
        if rule.rule_id == rule_id:
            return rule
    return None


def _anchor_log_anchor(anchor: ImplementationArtifactAnchor) -> str:
    """Return the canonical journal-path-plus-line-anchor string for
    ``anchor``.

    Per doc-16:92-93 implementation_log_anchors are strings (the
    journal-path-plus-line-anchor canonical form), NOT typed
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    objects. The format depends on which line-anchor field the source
    parser populated:

    * Slice 13c (journal markdown): ``"{journal_path}:{line_start}"``
      when ``line_start`` is present; falls back to ``journal_path``.
    * Slice 13d (decision-log JSONL): ``"{journal_path}#L{decision_log_line}"``
      when ``decision_log_line`` is present; falls back to
      ``journal_path``.

    Per the 13c⊕13d bidirectional invariant (per
    ``decision_log_parser.py:28-42``) at most one of ``line_start`` /
    ``decision_log_line`` is non-None on any given anchor; the helper
    therefore checks ``line_start`` first (the 13c-emitted form is the
    most common production path).
    """

    if anchor.line_start is not None:
        return f"{anchor.journal_path}:{anchor.line_start}"
    if anchor.decision_log_line is not None:
        return f"{anchor.journal_path}#L{anchor.decision_log_line}"
    return anchor.journal_path


def compute_reviewer_finding_inputs(
    anchor: ImplementationArtifactAnchor,
    rule: FindingRule,
    *,
    confidence: float = 0.7,
    severity: FindingSeverity = "medium",
    feature_id: str | None = None,
    recommended_action_display: str | None = None,
) -> FindingRuleEmissionInputs:
    """Build a typed
    :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs`
    bundle for the reviewer-finding rule from a parsed
    :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
    with ``event="finding"``.

    Per doc-16:137 + doc-16:183-184 the reviewer-finding class maps to
    the typed :data:`~iriai_build_v2.execution_control.finding_engine.FindingKind`
    ``"governance_evidence_conflict"`` (per
    :data:`~iriai_build_v2.execution_control.finding_rule_engine.CLASS_NAME_TO_FINDING_KIND`).
    Per :data:`~iriai_build_v2.execution_control.finding_rule_engine.EVIDENCE_GAP_FINDING_KINDS`
    the ``governance_evidence_conflict`` kind is explicitly allowed to
    emit with empty
    :attr:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs.primary_evidence_refs`
    -- a reviewer finding represents a conflict (the agent's output vs
    the reviewer's verdict); either side cannot be the primary by
    construction. The journal anchor (the per-line ``event="finding"``
    anchor) is recorded on
    :attr:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs.implementation_log_anchors`
    so the emitted finding correlates with the source journal line per
    doc-16:92-93.

    The :attr:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor.open_findings`
    list (per the Slice 13c parser at ``journal_parser.py:519-535``) is
    recorded on
    :attr:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs.affected_scope`
    so downstream consumers can see which specific finding-ids the
    journal-line anchor referenced.

    :param anchor: the typed
        :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
        parsed from the journal (typically carrying ``event="finding"``;
        the engine's
        :meth:`FindingReviewerTestFailureEngine.process_anchors` surface
        filters on this).
    :param rule: the typed :class:`FindingRule` looked up from
        :data:`~iriai_build_v2.execution_control.finding_rule_engine.REQUIRED_V1_FINDING_RULES`
        by ``rule_id="governance_evidence_conflict_v1"``. Caller-
        supplied so callers may exercise version-supersede paths.
    :param confidence: the typed float in ``[0.0, 1.0]``. Default ``0.7``
        per the conservative v1 calibration (a reviewer finding is a
        moderate-confidence signal; the conflict warrants attention but
        the rule engine should not auto-feed below-threshold findings
        into policy artifacts).
    :param severity: the typed
        :data:`~iriai_build_v2.execution_control.finding_engine.FindingSeverity`
        classification. Default ``"medium"`` per the conservative v1
        calibration for reviewer findings (the severity is auditable +
        upgradable per per-corpus calibration).
    :param feature_id: the feature scope; default ``None`` (cross-
        feature reviewer finding).
    :param recommended_action_display: optional non-executable display
        text override; default builds a deterministic message from the
        anchor's :attr:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor.slice_id`
        + the
        :attr:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor.open_findings`
        list.
    :returns: a typed
        :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs`
        bundle ready for
        :meth:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine.emit_finding`.
    """

    log_anchor = _anchor_log_anchor(anchor)

    if recommended_action_display is None:
        if anchor.open_findings:
            findings_str = ", ".join(anchor.open_findings)
            recommended_action_display = (
                f"Review reviewer finding(s) {findings_str!r} attached to "
                f"slice {anchor.slice_id!r} per doc-16:183-184 "
                f"(governance_evidence_conflict). Confirm whether the "
                f"conflict affects a policy decision before emitting a "
                f"Slice 17 recommendation."
            )
        else:
            recommended_action_display = (
                f"Review reviewer-finding anchor in slice {anchor.slice_id!r} "
                f"per doc-16:183-184 (governance_evidence_conflict). The "
                f"anchor carried no open finding-ids (the per-line marker "
                f"emitted by journal_parser.py:514-535 may have been a "
                f"resolved-finding marker)."
            )

    return FindingRuleEmissionInputs(
        rule=rule,
        class_name="governance_evidence_conflict",
        severity=severity,
        confidence=confidence,
        feature_id=feature_id,
        affected_scope={
            "slice_id": anchor.slice_id,
            "event": anchor.event,
            "open_findings": list(anchor.open_findings),
            "finding_source": "reviewer",
        },
        # Doc-16:159-161 + EVIDENCE_GAP_FINDING_KINDS -- the
        # governance_evidence_conflict kind is explicitly allowed to
        # emit with empty primary_evidence_refs (the conflict itself IS
        # the gap; either side cannot be primary by construction).
        primary_evidence_refs=[],
        supporting_evidence_refs=[],
        implementation_log_anchors=[log_anchor],
        metric_refs=[],
        recommended_action_display=recommended_action_display,
        safe_runtime_action=False,
        # Doc-16:193 -- low/medium confidence findings cannot feed
        # policy recommendations directly; the gap finding is advisory.
        requires_policy_artifact=False,
        product_defect_related=False,
        workflow_related=True,
        causal_role="contributing",
    )


def compute_late_test_failure_inputs(
    anchor: ImplementationArtifactAnchor,
    rule: FindingRule,
    *,
    confidence: float = 0.8,
    severity: FindingSeverity = "high",
    feature_id: str | None = None,
    recommended_action_display: str | None = None,
) -> FindingRuleEmissionInputs:
    """Build a typed
    :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs`
    bundle for the late-test-failure rule from a parsed
    :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
    with ``event="test_result"`` (from EITHER the Slice 13c journal
    parser per ``journal_parser.py:584-595`` OR the Slice 13d
    decision-log parser per ``decision_log_parser.py:455``).

    Per doc-16:164-165 step 5 *"late test failures"* the late-test-
    failure class maps to the typed
    :data:`~iriai_build_v2.execution_control.finding_engine.FindingKind`
    ``"governance_evidence_conflict"`` per the v1 calibration -- a late
    test failure represents a conflict between the prior acceptance
    evidence (the heading anchor with ``accepted=True``) + the post-
    acceptance test-result evidence (the per-line / per-row
    ``event="test_result"`` anchor); the conflict warrants emitting
    ``governance_evidence_conflict`` per doc-16:183-184. Subsequent rule
    versions MAY tighten by introducing a distinct ``late_test_failure_*``
    class via the doc-16:215-217 rule-version supersede path; the v1
    calibration keeps the taxonomy stable.

    Per :data:`~iriai_build_v2.execution_control.finding_rule_engine.EVIDENCE_GAP_FINDING_KINDS`
    the ``governance_evidence_conflict`` kind is explicitly allowed to
    emit with empty
    :attr:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs.primary_evidence_refs`
    -- the conflict itself IS the gap. The test-result anchor (from
    either parser) is recorded on
    :attr:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs.implementation_log_anchors`
    so the emitted finding correlates with the source line / row per
    doc-16:92-93.

    The anchor's source (journal markdown vs decision-log JSONL) is
    recorded on
    :attr:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs.affected_scope`
    so downstream consumers can see which corpus produced the test-
    result signal.

    :param anchor: the typed
        :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
        parsed from EITHER the journal markdown OR the decision-log
        JSONL (typically carrying ``event="test_result"``; the engine's
        :meth:`FindingReviewerTestFailureEngine.process_anchors` surface
        filters on this).
    :param rule: the typed :class:`FindingRule` looked up from
        :data:`~iriai_build_v2.execution_control.finding_rule_engine.REQUIRED_V1_FINDING_RULES`
        by ``rule_id="governance_evidence_conflict_v1"`` (the v1
        calibration REUSES the same rule as the reviewer-finding
        helper; subsequent rule versions MAY introduce a distinct
        rule_id per doc-16:215-217).
    :param confidence: the typed float in ``[0.0, 1.0]``. Default
        ``0.8`` per the conservative v1 calibration (a late test
        failure is a high-confidence conflict signal -- the test
        result is a deterministic measurement).
    :param severity: the typed
        :data:`~iriai_build_v2.execution_control.finding_engine.FindingSeverity`
        classification. Default ``"high"`` per the conservative v1
        calibration for late test failures (a regression post-
        acceptance warrants attention).
    :param feature_id: the feature scope; default ``None`` (cross-
        feature late test failure).
    :param recommended_action_display: optional non-executable display
        text override; default builds a deterministic message from the
        anchor's :attr:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor.slice_id`
        + the source corpus identification.
    :returns: a typed
        :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs`
        bundle ready for
        :meth:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine.emit_finding`.
    """

    log_anchor = _anchor_log_anchor(anchor)

    # Identify the source corpus per the 13c⊕13d bidirectional invariant
    # (per decision_log_parser.py:28-42): at most one of line_start /
    # decision_log_line is non-None on any given anchor. The journal
    # parser populates line_start; the decision-log parser populates
    # decision_log_line.
    if anchor.decision_log_line is not None:
        source_corpus = "decision_log"
    elif anchor.line_start is not None:
        source_corpus = "journal"
    else:
        source_corpus = "unknown"

    if recommended_action_display is None:
        recommended_action_display = (
            f"Review late test failure signal in slice {anchor.slice_id!r} "
            f"(source={source_corpus!r}) per doc-16:164-165 + doc-16:183-184. "
            f"Confirm whether the test failure conflicts with the prior "
            f"acceptance evidence before emitting a Slice 17 recommendation."
        )

    return FindingRuleEmissionInputs(
        rule=rule,
        class_name="governance_evidence_conflict",
        severity=severity,
        confidence=confidence,
        feature_id=feature_id,
        affected_scope={
            "slice_id": anchor.slice_id,
            "event": anchor.event,
            "source_corpus": source_corpus,
            "finding_source": "late_test_failure",
        },
        # Doc-16:159-161 + EVIDENCE_GAP_FINDING_KINDS -- the
        # governance_evidence_conflict kind is explicitly allowed to
        # emit with empty primary_evidence_refs (the test result IS the
        # observation; the conflict between it + prior acceptance is the
        # finding).
        primary_evidence_refs=[],
        supporting_evidence_refs=[],
        implementation_log_anchors=[log_anchor],
        metric_refs=[],
        recommended_action_display=recommended_action_display,
        safe_runtime_action=False,
        # Doc-16:193 -- the late-test-failure rule emits advisory only;
        # the Slice 17 policy layer decides whether to lift the finding
        # to a recommendation per its own policy.
        requires_policy_artifact=False,
        product_defect_related=False,
        workflow_related=True,
        causal_role="contributing",
    )


# --- The reviewer + late-test-failure engine (mirrors 3rd-A pattern) --------


class FindingReviewerTestFailureEngine:
    """Reviewer-finding + late-test-failure engine for the doc-16:164-165
    step-5 remaining categories (the 3rd-B sub-slice owns these 2
    categories; the 3rd-A sub-slice owns ``accepted_plan_deviation`` +
    ``implementation_journal_gap``).

    Per doc-16:164-165 *"Add implementation-plan deviation rules over
    journal anchors, reviewer findings, accepted deviations, and late
    test failures."* the engine consumes:

    1. A typed :class:`ReviewerTestFailureAnchorBundle` (built via
       :func:`parse_reviewer_test_failure_anchors` from BOTH the Slice
       13c
       :func:`~iriai_build_v2.workflows.develop.governance.journal_parser.parse_implementation_journal`
       output + the Slice 13d
       :func:`~iriai_build_v2.workflows.develop.governance.decision_log_parser.parse_implementation_decision_log`
       output).
    2. A typed
       :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine`
       from the Slice 16 2nd sub-slice (REUSED via direct delegation;
       NOT re-implemented per ``feedback_no_overengineer_use_library``;
       mirrors Slice 16 3rd-A sub-slice
       :class:`~iriai_build_v2.execution_control.finding_plan_deviation_engine.FindingPlanDeviationEngine`
       REUSE pattern verbatim).

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
    3. A list of typed :class:`ReviewerTestFailureParseGap` records
       (carried from the bundle's
       :attr:`ReviewerTestFailureAnchorBundle.parse_gaps`).

    **Rule lookup (doc-16:155-156 step 1 carry).** The engine looks up
    rules by ``rule_id`` from the REUSED Slice 16 2nd sub-slice
    :data:`~iriai_build_v2.execution_control.finding_rule_engine.REQUIRED_V1_FINDING_RULES`
    tuple. The default ``rule_id`` map covers the 1 v1 rule both 3rd-B
    classes share:

    * ``"governance_evidence_conflict"`` ->
      ``rule_id="governance_evidence_conflict_v1"``.

    Both the reviewer-finding helper +
    :func:`compute_reviewer_finding_inputs` AND the late-test-failure
    helper :func:`compute_late_test_failure_inputs` build bundles with
    ``class_name="governance_evidence_conflict"`` so the REUSED 2nd
    sub-slice engine's idempotency key (per doc-16:158) carries the
    canonical class identity. Future rule versions (``v2`` / ``v3``)
    supplied via the ``rule_lookup`` constructor argument override the
    default; the REUSED 2nd sub-slice engine's suppression / expiry
    policies still apply.

    **Non-blocking observer contract (doc-14:242-243 inherited via
    Slice 14 + 15 + 16 2nd + 3rd-A sub-slice precedent).** The engine
    NEVER raises a structural failure to the caller; every failure
    projects onto a typed
    :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionGap`
    (rule-emission failures; via the REUSED 2nd sub-slice engine) OR
    :class:`ReviewerTestFailureParseGap` (parser failures; via this
    module's :func:`parse_reviewer_test_failure_anchors` helper).

    **Per-call gap_findings accumulator reset (mirrors Slice 15 2nd +
    4th sub-slice + Slice 16 2nd + 3rd-A sub-slice pattern).** Each
    call to :meth:`process_anchors` RESETS the local
    :attr:`gap_findings` + :attr:`parse_gaps` accumulators so per-call
    gap findings remain bounded; callers that need cross-call
    accumulation should snapshot the properties after each call.

    Example usage::

        from iriai_build_v2.execution_control.finding_reviewer_test_failure_engine \\
            import (
                FindingReviewerTestFailureEngine,
                parse_reviewer_test_failure_anchors,
            )
        from iriai_build_v2.execution_control.finding_rule_engine import (
            FindingRuleEngine,
        )

        bundle = parse_reviewer_test_failure_anchors(
            "docs/execution-control-plane/implementation-journal.md",
            "docs/execution-control-plane/implementation-decisions.jsonl",
        )
        rule_engine = FindingRuleEngine()
        rev_engine = FindingReviewerTestFailureEngine()
        findings = rev_engine.process_anchors(bundle, rule_engine)
        for finding in findings:
            # finding emitted; caller persists it.
            ...
        for gap in rev_engine.gap_findings:
            # rule failed to emit; caller logs the gap.
            ...
        for parse_gap in rev_engine.parse_gaps:
            # parser failed; caller logs the parse gap.
            ...
    """

    # Default class_name -> rule_id mapping for the 1 v1 rule both 3rd-B
    # classes share. The v1 calibration REUSES the same rule for both
    # categories per the doc-16:46 conservative-calibration discipline;
    # subsequent rule versions MAY introduce a distinct
    # late_test_failure_v1 rule per doc-16:215-217.
    DEFAULT_RULE_ID_MAP: dict[str, str] = {
        "governance_evidence_conflict": "governance_evidence_conflict_v1",
    }

    def __init__(
        self,
        *,
        rule_lookup: dict[str, str] | None = None,
    ) -> None:
        """Construct a reviewer-finding + late-test-failure engine.

        :param rule_lookup: optional override map of
            ``class_name -> rule_id``. Defaults to
            :attr:`DEFAULT_RULE_ID_MAP` covering the 1 v1 rule both
            3rd-B classes share. Callers may override per per-corpus
            calibration (e.g. to route late-test-failures to a v2
            rule).

        The engine is stateless aside from the :attr:`gap_findings` +
        :attr:`parse_gaps` accumulators the :meth:`process_anchors`
        surface populates. Each call RESETS both accumulators per the
        Slice 15 2nd + 4th sub-slice + Slice 16 2nd + 3rd-A sub-slice
        precedent.
        """

        self._rule_lookup: dict[str, str] = dict(
            rule_lookup if rule_lookup is not None else self.DEFAULT_RULE_ID_MAP
        )
        self._gap_findings: list[FindingRuleEmissionGap] = []
        self._parse_gaps: list[ReviewerTestFailureParseGap] = []

    @property
    def gap_findings(self) -> list[FindingRuleEmissionGap]:
        """The list of
        :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionGap`
        findings the most-recent :meth:`process_anchors` call produced
        via the REUSED 2nd sub-slice engine.

        Per the Slice 14 + Slice 15 2nd + 4th sub-slice + Slice 16 2nd
        + 3rd-A sub-slice precedents the engine NEVER raises a failure
        to the caller -- every structural failure projects onto a typed
        gap finding.
        """

        return list(self._gap_findings)

    @property
    def parse_gaps(self) -> list[ReviewerTestFailureParseGap]:
        """The list of :class:`ReviewerTestFailureParseGap` records
        carried from the input
        :class:`ReviewerTestFailureAnchorBundle.parse_gaps` the most-
        recent :meth:`process_anchors` call processed.

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
        bundle: ReviewerTestFailureAnchorBundle,
        engine: FindingRuleEngine,
        *,
        now: datetime | None = None,
        confidence_reviewer: float = 0.7,
        confidence_test_failure: float = 0.8,
    ) -> list[GovernanceFinding]:
        """Process the parsed bundle through the REUSED Slice 16 2nd
        sub-slice ``engine`` and return the list of emitted
        :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
        records.

        Per doc-16:164-165 step 5 the surface emits:

        1. **One ``governance_evidence_conflict`` finding per
           ``event="finding"`` anchor in
           :attr:`ReviewerTestFailureAnchorBundle.journal_anchors`**
           (per doc-16:137 + doc-16:183-184). A reviewer finding
           represents a conflict between the agent's output + the
           reviewer's verdict; emitting
           ``governance_evidence_conflict`` is the conservative v1
           calibration. The journal_parser.py:514-535 per-line anchor
           emission is the canonical signal.
        2. **One ``governance_evidence_conflict`` finding per
           ``event="test_result"`` anchor in EITHER
           :attr:`ReviewerTestFailureAnchorBundle.journal_anchors` OR
           :attr:`ReviewerTestFailureAnchorBundle.decision_log_anchors`**
           (per doc-16:164-165 + doc-16:183-184). A late test failure
           is a conflict between the prior acceptance evidence + the
           post-acceptance test-result evidence; emitting
           ``governance_evidence_conflict`` is the conservative v1
           calibration. The journal_parser.py:584-595 per-line ``N
           passed`` anchor + the decision_log_parser.py:455 per-row
           anchor are the canonical signals.

        Per the auto-memory ``feedback_no_silent_degradation`` rule:

        * Rule lookup failures (no rule found for the class_name) ->
          gap finding accumulated on :attr:`gap_findings` via the
          REUSED 2nd sub-slice engine; no finding emitted.
        * Per the REUSED 2nd sub-slice engine's 7-guard logic: every
          suppression / expiry / at-least-one-primary / product-
          workflow separation / confidence-threshold / idempotency /
          construction failure projects onto a typed gap finding on
          :attr:`gap_findings`; the engine NEVER raises.

        Per doc-14:242-243 NEVER raises a failure to the caller. Any
        structural failure projects onto a typed gap finding.

        The method RESETS the :attr:`gap_findings` + :attr:`parse_gaps`
        accumulators at entry; per-call gap findings remain bounded.

        :param bundle: the typed
            :class:`ReviewerTestFailureAnchorBundle` produced by
            :func:`parse_reviewer_test_failure_anchors`.
        :param engine: the typed REUSED Slice 16 2nd sub-slice
            :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine`.
            The 3rd-B engine builds typed
            :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEmissionInputs`
            bundles and DELEGATES to ``engine.emit_finding`` for the
            7-guard logic.
        :param now: optional datetime override for the expiry check
            (defaults to live clock). Passed through to
            :meth:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine.emit_finding`.
        :param confidence_reviewer: optional confidence override for
            reviewer-finding emissions. Default ``0.7`` per the
            conservative v1 calibration.
        :param confidence_test_failure: optional confidence override
            for late-test-failure emissions. Default ``0.8`` per the
            conservative v1 calibration (the test result is a
            deterministic measurement).
        :returns: the list of emitted typed
            :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
            records. Validly empty when no rules emit (e.g. the bundle
            has no finding / test_result anchors).
        """

        # Reset per-call accumulators (mirrors Slice 15 2nd + 4th +
        # Slice 16 2nd + 3rd-A sub-slice pattern).
        self._gap_findings = []
        self._parse_gaps = list(bundle.parse_gaps)

        findings: list[GovernanceFinding] = []

        # The v1 calibration REUSES the same rule_id for both
        # categories; look up once at entry.
        shared_rule_id = self._rule_lookup.get("governance_evidence_conflict")
        if shared_rule_id is None:
            # No rule registered for the shared class; nothing to emit.
            return findings

        shared_rule = _lookup_rule(shared_rule_id)
        if shared_rule is None:
            # Per feedback_no_silent_degradation: a missing rule
            # projects onto a typed gap finding rather than raising.
            # The REUSED 2nd sub-slice FindingRuleEmissionGap shape is
            # the typed surface for rule-emission failures.
            self._gap_findings.append(
                FindingRuleEmissionGap(
                    failure_id=FINDING_RULE_EMISSION_FAILURE_ID,
                    rule_id=shared_rule_id,
                    rule_version="<unknown>",
                    class_name="governance_evidence_conflict",
                    attempted_idempotency_key=None,
                    reason="rule_id_not_found_in_v1_rules",
                    evidence_payload={
                        "rule_lookup_class_name": "governance_evidence_conflict",
                        "rule_lookup_rule_id": shared_rule_id,
                    },
                )
            )
            return findings

        # Pass 1: reviewer-finding rule over journal_anchors ────────────────
        # Per doc-16:137 + doc-16:183-184 -- emit one finding per
        # event="finding" anchor in journal_anchors. The
        # journal_parser.py:514-535 per-line anchor emission is the
        # canonical signal.
        for anchor in bundle.journal_anchors:
            if anchor.event != "finding":
                continue
            inputs = compute_reviewer_finding_inputs(
                anchor,
                shared_rule,
                confidence=confidence_reviewer,
            )
            finding = engine.emit_finding(inputs, now=now)
            # Carry the REUSED 2nd sub-slice engine's per-call gap
            # findings onto this engine's accumulator so the caller
            # sees all rule-application gaps in one place.
            self._gap_findings.extend(engine.gap_findings)
            if finding is not None:
                findings.append(finding)

        # Pass 2: late-test-failure rule over journal_anchors ──────────────
        # Per doc-16:164-165 + doc-16:183-184 -- emit one finding per
        # event="test_result" anchor in journal_anchors. The
        # journal_parser.py:584-595 per-line "N passed" anchor emission
        # is the canonical signal.
        for anchor in bundle.journal_anchors:
            if anchor.event != "test_result":
                continue
            inputs = compute_late_test_failure_inputs(
                anchor,
                shared_rule,
                confidence=confidence_test_failure,
            )
            finding = engine.emit_finding(inputs, now=now)
            self._gap_findings.extend(engine.gap_findings)
            if finding is not None:
                findings.append(finding)

        # Pass 3: late-test-failure rule over decision_log_anchors ────────
        # Per doc-16:164-165 + doc-16:183-184 -- emit one finding per
        # event="test_result" anchor in decision_log_anchors. The
        # decision_log_parser.py:455 per-row anchor emission is the
        # canonical cross-corpus signal.
        for anchor in bundle.decision_log_anchors:
            if anchor.event != "test_result":
                continue
            inputs = compute_late_test_failure_inputs(
                anchor,
                shared_rule,
                confidence=confidence_test_failure,
            )
            finding = engine.emit_finding(inputs, now=now)
            self._gap_findings.extend(engine.gap_findings)
            if finding is not None:
                findings.append(finding)

        return findings
