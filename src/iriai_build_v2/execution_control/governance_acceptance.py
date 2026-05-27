"""Slice 20 governance acceptance and adoption advisory surface.

This module implements the read-only acceptance gate described by
``docs/execution-control-plane/20-governance-acceptance-and-adoption.md``.
It collects bounded, refs-only evidence about the completed governance
slices and returns typed advisory records. It intentionally has no authority
to mutate runtime policy, feature execution state, persistence tables, or
task-execute agent context.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from iriai_build_v2.workflows.develop.governance.models import (
    ImplementationArtifactAnchor,
)

GovernanceSurface = Literal[
    "new_feature_analysis",
    "agent_context",
    "dashboard",
    "supervisor_digest",
    "cli_reporting",
]
RuntimePolicyMutationAllowed = Literal["never"]
ExecutionStateMutated = Literal[False]
AuditHistoryPreserved = Literal[True]
AcceptanceAxisResult = Literal["passed", "failed"]

REQUIRED_GOVERNANCE_SURFACES: tuple[GovernanceSurface, ...] = (
    "new_feature_analysis",
    "agent_context",
    "dashboard",
    "supervisor_digest",
    "cli_reporting",
)
GOVERNANCE_ACCEPTANCE_ARTIFACT_PREFIX = "review:governance-acceptance"
GOVERNANCE_JOURNAL_AUDIT_ARTIFACT_PREFIX = "review:governance-journal-audit"
GOVERNANCE_REPLAY_CORPUS_ARTIFACT_PREFIX = "review:governance-replay-corpus"
GOVERNANCE_ADOPTION_ARTIFACT_PREFIX = "review:governance-adoption"
REQUIRED_CONTROL_PLANE_SLICE_IDS: tuple[str, ...] = tuple(
    f"{idx:02d}" for idx in range(13)
)
REQUIRED_GOVERNANCE_SLICE_IDS: tuple[str, ...] = (
    "13a",
    "13",
    "14",
    "15",
    "16",
    "17",
    "18",
    "19",
)
REQUIRED_ACCEPTANCE_SLICE_IDS: tuple[str, ...] = (
    *REQUIRED_CONTROL_PLANE_SLICE_IDS,
    *REQUIRED_GOVERNANCE_SLICE_IDS,
)

_BLOCKER_CONTROL_PLANE_INCOMPLETE = "control_plane_landing_incomplete"
_BLOCKER_13A_INCOMPLETE = "required_13a_remediation_incomplete"
_BLOCKER_13A_STEPS = "slice_13a_steps_not_satisfied"
_BLOCKER_13A_AUTHORITY = "slice_13a_authority_boundary_reopened"
_BLOCKER_13A_TESTS = "slice_13a_tests_not_green"
_BLOCKER_19A_INCOMPLETE = "slice_19a_reassessment_incomplete"
_BLOCKER_GOVERNANCE_SLICES_INCOMPLETE = "governance_slices_13_19_incomplete"
_BLOCKER_JOURNAL_REFS_MISSING = "implementation_journal_audit_refs_missing"
_BLOCKER_REQUIRED_TESTS_MISSING = "required_tests_missing"
_BLOCKER_REQUIRED_TESTS = "required_tests_not_passed"
_BLOCKER_RECOMMENDATION_MUTATION = "recommendation_mutation_authority_detected"
_BLOCKER_BODY_SCAN = "unbounded_body_scan_detected"
_BLOCKER_REPLAY_CORPUS = "replay_corpus_incomplete"
_BLOCKER_REPORTING_UNAVAILABLE = "reporting_surface_unavailable"
_BLOCKER_TASK_EXECUTE_CONTEXT = "task_execute_agent_context_pre_slice_21_enabled"

_ACCEPTANCE_AXIS_FIELDS: tuple[str, ...] = (
    "evidence_model_result",
    "provenance_result",
    "metrics_result",
    "findings_result",
    "recommendation_result",
    "replay_result",
    "reporting_result",
    "implementation_journal_audit_result",
)
_BLOCKING_FINDING_RE = re.compile(r"(^|[^A-Z0-9])P[12](?=$|[-_:\s])")


def _non_empty(value: str) -> str:
    if not value.strip():
        raise ValueError("value must be non-empty")
    return value


def _dedupe_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = _non_empty(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _canonicalize_surfaces(values: Iterable[GovernanceSurface]) -> list[GovernanceSurface]:
    provided = set(values)
    return [surface for surface in REQUIRED_GOVERNANCE_SURFACES if surface in provided]


def _unknown_surfaces(values: Iterable[str]) -> list[str]:
    allowed = set(REQUIRED_GOVERNANCE_SURFACES)
    return sorted({value for value in values if value not in allowed})


def _is_blocking_finding(value: str) -> bool:
    return bool(_BLOCKING_FINDING_RE.search(value))


def _covered_required_command(test: "GovernanceRequiredTestCommand") -> str | None:
    if test.exact_required_command:
        return test.command
    return test.required_command


class GovernanceAcceptanceArtifactRefs(BaseModel):
    """Review artifact refs for a Slice 20 acceptance candidate."""

    model_config = ConfigDict(extra="forbid")

    acceptance: str
    journal_audit: str
    replay_corpus: str
    adoption: str

    @field_validator("acceptance", "journal_audit", "replay_corpus", "adoption")
    @classmethod
    def _refs_must_be_review_refs(cls, value: str) -> str:
        normalized = _non_empty(value)
        if not normalized.startswith("review:"):
            raise ValueError("governance acceptance artifacts must be review refs")
        return normalized


class GovernanceAcceptanceResult(BaseModel):
    """Typed result for the all-at-once Slice 20 acceptance gate."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    candidate_commit: str
    passed: bool
    prerequisite_control_plane_landing_id: str
    evidence_model_result: AcceptanceAxisResult
    provenance_result: AcceptanceAxisResult
    metrics_result: AcceptanceAxisResult
    findings_result: AcceptanceAxisResult
    recommendation_result: AcceptanceAxisResult
    replay_result: AcceptanceAxisResult
    reporting_result: AcceptanceAxisResult
    implementation_journal_audit_result: AcceptanceAxisResult
    implementation_journal_audit_refs: list[ImplementationArtifactAnchor] = Field(
        default_factory=list
    )
    missing_journal_items: list[str] = Field(default_factory=list)
    unresolved_review_findings: list[str] = Field(default_factory=list)
    required_tests: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)

    @field_validator(
        "candidate_id",
        "candidate_commit",
        "prerequisite_control_plane_landing_id",
    )
    @classmethod
    def _required_strings_are_non_empty(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator(
        "missing_journal_items",
        "unresolved_review_findings",
        "required_tests",
        "blockers",
    )
    @classmethod
    def _string_lists_are_non_empty_and_deduped(cls, value: list[str]) -> list[str]:
        return _dedupe_preserving_order(value)

    @model_validator(mode="after")
    def _passed_results_have_no_blockers(self) -> "GovernanceAcceptanceResult":
        if self.passed and self.blockers:
            raise ValueError("passed acceptance results cannot carry blockers")
        if not self.passed:
            return self
        for axis_field in _ACCEPTANCE_AXIS_FIELDS:
            if getattr(self, axis_field) != "passed":
                raise ValueError(
                    "passed acceptance results require every axis to pass"
                )
        if not self.implementation_journal_audit_refs:
            raise ValueError(
                "passed acceptance results require journal audit refs"
            )
        if self.missing_journal_items:
            raise ValueError(
                "passed acceptance results cannot carry missing journal items"
            )
        if any(_is_blocking_finding(finding) for finding in self.unresolved_review_findings):
            raise ValueError(
                "passed acceptance results cannot carry unresolved P1/P2 findings"
            )
        if not self.required_tests:
            raise ValueError("passed acceptance results require test commands")
        return self


class GovernanceRequiredTestCommand(BaseModel):
    """A required Slice 20 test command and its exactness/deviation status."""

    model_config = ConfigDict(extra="forbid")

    command: str
    passed: bool
    exact_required_command: bool
    required_command: str | None = None
    accepted_deviation_ref: str | None = None

    @field_validator("command")
    @classmethod
    def _command_is_non_empty(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("accepted_deviation_ref")
    @classmethod
    def _deviation_ref_is_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _non_empty(value)
        if not normalized.startswith("review:"):
            raise ValueError("accepted test deviations must be review refs")
        return normalized

    @field_validator("required_command")
    @classmethod
    def _required_command_is_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _non_empty(value)


class GovernanceSliceImplementationEvidence(BaseModel):
    """Refs-only implementation evidence for one required slice."""

    model_config = ConfigDict(extra="forbid")

    slice_id: str
    accepted: bool
    journal_refs: list[ImplementationArtifactAnchor] = Field(default_factory=list)
    decision_log_refs: list[ImplementationArtifactAnchor] = Field(default_factory=list)
    reviewer_dispatch_refs: list[ImplementationArtifactAnchor] = Field(
        default_factory=list
    )
    test_output_refs: list[ImplementationArtifactAnchor] = Field(default_factory=list)
    accepted_deviations_reviewed: bool
    unresolved_review_findings: list[str] = Field(default_factory=list)

    @field_validator("slice_id")
    @classmethod
    def _slice_id_is_non_empty(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("unresolved_review_findings")
    @classmethod
    def _finding_list_is_non_empty_and_deduped(cls, value: list[str]) -> list[str]:
        return _dedupe_preserving_order(value)


class GovernanceAdoptionRecord(BaseModel):
    """Read-only adoption record for the complete governance surface set."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    adopted_at: datetime
    all_read_only_surfaces_enabled: bool
    enabled_surfaces: list[GovernanceSurface]
    runtime_policy_mutation_allowed: RuntimePolicyMutationAllowed = "never"
    rollback_disposition: str

    @field_validator("candidate_id", "rollback_disposition")
    @classmethod
    def _required_strings_are_non_empty(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("enabled_surfaces")
    @classmethod
    def _enabled_surfaces_are_complete(
        cls, value: list[GovernanceSurface]
    ) -> list[GovernanceSurface]:
        canonical = _canonicalize_surfaces(value)
        if tuple(canonical) != REQUIRED_GOVERNANCE_SURFACES:
            missing = sorted(set(REQUIRED_GOVERNANCE_SURFACES) - set(canonical))
            raise ValueError(
                "governance adoption requires every read-only surface: "
                + ", ".join(missing)
            )
        return canonical

    @model_validator(mode="after")
    def _all_read_only_surfaces_flag_matches_set(self) -> "GovernanceAdoptionRecord":
        if not self.all_read_only_surfaces_enabled:
            raise ValueError("partial governance adoption is unsupported")
        return self


class GovernanceRollbackPlan(BaseModel):
    """Advisory rollback plan that preserves append-only governance history."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    disabled_surfaces: list[GovernanceSurface]
    runtime_policy_mutation_allowed: RuntimePolicyMutationAllowed = "never"
    execution_state_mutated: ExecutionStateMutated = False
    audit_history_preserved: AuditHistoryPreserved = True
    review_artifacts_preserved: AuditHistoryPreserved = True
    rollback_disposition: str

    @field_validator("candidate_id", "rollback_disposition")
    @classmethod
    def _required_strings_are_non_empty(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("disabled_surfaces")
    @classmethod
    def _disabled_surfaces_are_complete(
        cls, value: list[GovernanceSurface]
    ) -> list[GovernanceSurface]:
        canonical = _canonicalize_surfaces(value)
        if tuple(canonical) != REQUIRED_GOVERNANCE_SURFACES:
            raise ValueError("rollback must disable every Slice 20 read-only surface")
        return canonical


class GovernanceAcceptanceInputs(BaseModel):
    """Bounded evidence supplied to the Slice 20 acceptance collector."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    candidate_commit: str
    prerequisite_control_plane_landing_id: str
    slices_00_12_complete: bool
    required_13a_remediation_complete: bool
    thirteen_a_all_steps_satisfied: bool
    thirteen_a_authority_boundary_preserved: bool
    thirteen_a_tests_green: bool
    required_19a_reassessment_complete: bool
    governance_slices_13_19_complete: bool
    slice_evidence: list[GovernanceSliceImplementationEvidence] = Field(
        default_factory=list
    )
    evidence_model_result: AcceptanceAxisResult
    provenance_result: AcceptanceAxisResult
    metrics_result: AcceptanceAxisResult
    findings_result: AcceptanceAxisResult
    recommendation_result: AcceptanceAxisResult
    replay_result: AcceptanceAxisResult
    reporting_result: AcceptanceAxisResult
    implementation_journal_audit_result: AcceptanceAxisResult
    implementation_journal_audit_refs: list[ImplementationArtifactAnchor] = Field(
        default_factory=list
    )
    missing_journal_items: list[str] = Field(default_factory=list)
    unresolved_review_findings: list[str] = Field(default_factory=list)
    required_tests: list[str] = Field(default_factory=list)
    required_test_results: list[GovernanceRequiredTestCommand] = Field(
        default_factory=list
    )
    required_tests_passed: bool
    recommendations_have_mutation_authority: bool
    bounded_read_body_scan_detected: bool
    replay_corpus_complete: bool
    replay_feature_ids: list[str]
    active_feature_mutated: bool
    reporting_surface_available: bool
    task_execute_agent_context_enabled: bool
    read_only_surfaces_available: list[GovernanceSurface]

    @field_validator(
        "candidate_id",
        "candidate_commit",
        "prerequisite_control_plane_landing_id",
    )
    @classmethod
    def _required_strings_are_non_empty(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator(
        "missing_journal_items",
        "unresolved_review_findings",
        "required_tests",
        "replay_feature_ids",
    )
    @classmethod
    def _string_lists_are_non_empty_and_deduped(cls, value: list[str]) -> list[str]:
        return _dedupe_preserving_order(value)

    @field_validator("read_only_surfaces_available")
    @classmethod
    def _available_surfaces_are_deduped(
        cls, value: list[GovernanceSurface]
    ) -> list[GovernanceSurface]:
        return _canonicalize_surfaces(value)


def governance_acceptance_artifact_refs(
    candidate_id: str,
) -> GovernanceAcceptanceArtifactRefs:
    """Return the required Slice 20 review artifact refs for a candidate."""

    normalized_candidate_id = _non_empty(candidate_id)
    return GovernanceAcceptanceArtifactRefs(
        acceptance=f"{GOVERNANCE_ACCEPTANCE_ARTIFACT_PREFIX}:{normalized_candidate_id}",
        journal_audit=(
            f"{GOVERNANCE_JOURNAL_AUDIT_ARTIFACT_PREFIX}:{normalized_candidate_id}"
        ),
        replay_corpus=(
            f"{GOVERNANCE_REPLAY_CORPUS_ARTIFACT_PREFIX}:{normalized_candidate_id}"
        ),
        adoption=f"{GOVERNANCE_ADOPTION_ARTIFACT_PREFIX}:{normalized_candidate_id}",
    )


def build_governance_adoption_record(
    acceptance_result: GovernanceAcceptanceResult,
    *,
    adopted_at: datetime,
    enabled_surfaces: Iterable[GovernanceSurface],
    rollback_disposition: str,
) -> GovernanceAdoptionRecord | None:
    """Build an adoption record only when acceptance and all surfaces are complete."""

    if not acceptance_result.passed:
        return None
    raw_surfaces = list(enabled_surfaces)
    if _unknown_surfaces([str(surface) for surface in raw_surfaces]):
        return None
    canonical_surfaces = _canonicalize_surfaces(raw_surfaces)
    if tuple(canonical_surfaces) != REQUIRED_GOVERNANCE_SURFACES:
        return None
    return GovernanceAdoptionRecord(
        candidate_id=acceptance_result.candidate_id,
        adopted_at=adopted_at,
        all_read_only_surfaces_enabled=True,
        enabled_surfaces=canonical_surfaces,
        runtime_policy_mutation_allowed="never",
        rollback_disposition=rollback_disposition,
    )


def plan_governance_adoption_rollback(
    adoption_record: GovernanceAdoptionRecord,
) -> GovernanceRollbackPlan:
    """Return a rollback plan without mutating execution or audit state."""

    return GovernanceRollbackPlan(
        candidate_id=adoption_record.candidate_id,
        disabled_surfaces=list(REQUIRED_GOVERNANCE_SURFACES),
        runtime_policy_mutation_allowed="never",
        execution_state_mutated=False,
        audit_history_preserved=True,
        review_artifacts_preserved=True,
        rollback_disposition=adoption_record.rollback_disposition,
    )


class GovernanceAcceptanceCollector:
    """Pure collector for refs-only Slice 20 acceptance evidence."""

    def evaluate(self, inputs: GovernanceAcceptanceInputs) -> GovernanceAcceptanceResult:
        """Evaluate supplied evidence and return a fail-closed advisory result."""

        blockers = self._collect_blockers(inputs)
        required_tests = self._required_test_commands(inputs)
        return GovernanceAcceptanceResult(
            candidate_id=inputs.candidate_id,
            candidate_commit=inputs.candidate_commit,
            passed=not blockers,
            prerequisite_control_plane_landing_id=(
                inputs.prerequisite_control_plane_landing_id
            ),
            evidence_model_result=inputs.evidence_model_result,
            provenance_result=inputs.provenance_result,
            metrics_result=inputs.metrics_result,
            findings_result=inputs.findings_result,
            recommendation_result=inputs.recommendation_result,
            replay_result=inputs.replay_result,
            reporting_result=inputs.reporting_result,
            implementation_journal_audit_result=(
                inputs.implementation_journal_audit_result
            ),
            implementation_journal_audit_refs=list(
                inputs.implementation_journal_audit_refs
            ),
            missing_journal_items=list(inputs.missing_journal_items),
            unresolved_review_findings=list(inputs.unresolved_review_findings),
            required_tests=required_tests,
            blockers=blockers,
        )

    def _collect_blockers(self, inputs: GovernanceAcceptanceInputs) -> list[str]:
        blockers: list[str] = []

        if not inputs.slices_00_12_complete:
            blockers.append(_BLOCKER_CONTROL_PLANE_INCOMPLETE)
        if not inputs.required_13a_remediation_complete:
            blockers.append(_BLOCKER_13A_INCOMPLETE)
        if not inputs.thirteen_a_all_steps_satisfied:
            blockers.append(_BLOCKER_13A_STEPS)
        if not inputs.thirteen_a_authority_boundary_preserved:
            blockers.append(_BLOCKER_13A_AUTHORITY)
        if not inputs.thirteen_a_tests_green:
            blockers.append(_BLOCKER_13A_TESTS)
        if not inputs.required_19a_reassessment_complete:
            blockers.append(_BLOCKER_19A_INCOMPLETE)
        if not inputs.governance_slices_13_19_complete:
            blockers.append(_BLOCKER_GOVERNANCE_SLICES_INCOMPLETE)
        if not inputs.implementation_journal_audit_refs:
            blockers.append(_BLOCKER_JOURNAL_REFS_MISSING)
        if not inputs.required_test_results and not inputs.required_tests:
            blockers.append(_BLOCKER_REQUIRED_TESTS_MISSING)
        if not inputs.required_tests_passed:
            blockers.append(_BLOCKER_REQUIRED_TESTS)
        if inputs.recommendations_have_mutation_authority:
            blockers.append(_BLOCKER_RECOMMENDATION_MUTATION)
        if inputs.bounded_read_body_scan_detected:
            blockers.append(_BLOCKER_BODY_SCAN)
        if not inputs.replay_corpus_complete:
            blockers.append(_BLOCKER_REPLAY_CORPUS)
        if "8ac124d6" not in set(inputs.replay_feature_ids):
            blockers.append("replay_missing_feature:8ac124d6")
        if inputs.active_feature_mutated:
            blockers.append("active_feature_mutated:8ac124d6")
        if not inputs.reporting_surface_available:
            blockers.append(_BLOCKER_REPORTING_UNAVAILABLE)
        if inputs.task_execute_agent_context_enabled:
            blockers.append(_BLOCKER_TASK_EXECUTE_CONTEXT)

        for axis_field in _ACCEPTANCE_AXIS_FIELDS:
            if getattr(inputs, axis_field) != "passed":
                blockers.append(f"{axis_field}_failed")
        for item in inputs.missing_journal_items:
            blockers.append(f"missing_journal_item:{item}")
        for finding in inputs.unresolved_review_findings:
            if _is_blocking_finding(finding):
                blockers.append(f"unresolved_review_finding:{finding}")
        for test in inputs.required_test_results:
            if not test.passed:
                blockers.append(f"required_test_failed:{test.command}")
            if not test.exact_required_command and not test.accepted_deviation_ref:
                blockers.append(
                    f"required_test_non_exact_without_deviation:{test.command}"
                )
            if not test.exact_required_command and not test.required_command:
                blockers.append(
                    f"required_test_non_exact_without_required_command:{test.command}"
                )

        covered_commands = {
            covered
            for test in inputs.required_test_results
            if test.passed
            for covered in [_covered_required_command(test)]
            if covered
        }
        for command in inputs.required_tests:
            if command not in covered_commands:
                blockers.append(f"required_test_result_missing:{command}")

        slice_evidence = {evidence.slice_id: evidence for evidence in inputs.slice_evidence}
        for slice_id in REQUIRED_ACCEPTANCE_SLICE_IDS:
            evidence = slice_evidence.get(slice_id)
            if evidence is None:
                blockers.append(f"missing_slice_evidence:{slice_id}")
                continue
            if not evidence.accepted:
                blockers.append(f"slice_not_accepted:{slice_id}")
            if not evidence.journal_refs:
                blockers.append(f"missing_slice_journal_refs:{slice_id}")
            if not evidence.decision_log_refs:
                blockers.append(f"missing_slice_decision_log_refs:{slice_id}")
            if not evidence.reviewer_dispatch_refs:
                blockers.append(f"missing_slice_reviewer_dispatch_refs:{slice_id}")
            if not evidence.test_output_refs:
                blockers.append(f"missing_slice_test_output_refs:{slice_id}")
            if not evidence.accepted_deviations_reviewed:
                blockers.append(f"accepted_deviation_review_missing:{slice_id}")
            for finding in evidence.unresolved_review_findings:
                if _is_blocking_finding(finding):
                    blockers.append(f"unresolved_review_finding:{finding}")

        available = set(inputs.read_only_surfaces_available)
        for surface in REQUIRED_GOVERNANCE_SURFACES:
            if surface not in available:
                blockers.append(f"surface_unavailable:{surface}")

        return _dedupe_preserving_order(blockers)

    @staticmethod
    def _required_test_commands(inputs: GovernanceAcceptanceInputs) -> list[str]:
        if inputs.required_tests:
            return list(inputs.required_tests)
        commands = [
            covered
            for test in inputs.required_test_results
            for covered in [_covered_required_command(test)]
            if covered
        ]
        return _dedupe_preserving_order(commands)


__all__ = [
    "AcceptanceAxisResult",
    "AuditHistoryPreserved",
    "ExecutionStateMutated",
    "GOVERNANCE_ACCEPTANCE_ARTIFACT_PREFIX",
    "GOVERNANCE_ADOPTION_ARTIFACT_PREFIX",
    "GOVERNANCE_JOURNAL_AUDIT_ARTIFACT_PREFIX",
    "GOVERNANCE_REPLAY_CORPUS_ARTIFACT_PREFIX",
    "GovernanceAcceptanceArtifactRefs",
    "GovernanceAcceptanceCollector",
    "GovernanceAcceptanceInputs",
    "GovernanceAcceptanceResult",
    "GovernanceAdoptionRecord",
    "GovernanceRequiredTestCommand",
    "GovernanceRollbackPlan",
    "GovernanceSliceImplementationEvidence",
    "GovernanceSurface",
    "REQUIRED_ACCEPTANCE_SLICE_IDS",
    "REQUIRED_CONTROL_PLANE_SLICE_IDS",
    "REQUIRED_GOVERNANCE_SURFACES",
    "REQUIRED_GOVERNANCE_SLICE_IDS",
    "RuntimePolicyMutationAllowed",
    "build_governance_adoption_record",
    "governance_acceptance_artifact_refs",
    "plan_governance_adoption_rollback",
]
