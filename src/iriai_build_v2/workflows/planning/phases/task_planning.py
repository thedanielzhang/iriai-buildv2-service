from __future__ import annotations

import asyncio
import hashlib
import json as _json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable
from pathlib import Path

from pydantic import BaseModel, Field as PydanticField
from iriai_compose import AgentActor, Ask, Feature, Phase, WorkflowRunner
from iriai_compose.actors import Role

from ....models.outputs import (
    ArtifactAuditIssue,
    ArtifactAuditReport,
    ArtifactBackfillStatus,
    ArtifactBackfillSubfeatureStatus,
    DagPathResolution,
    DesignDecisions,
    DecisionLedger,
    DecisionRecord,
    ImplementationDAG,
    ImplementationTask,
    ProjectContext,
    TaskAcceptanceCriterion,
    TaskReference,
    SharedPlanningIndex,
    PRD,
    RevisionPlan,
    RevisionRequest,
    StructuredArtifact,
    SubfeatureDecomposition,
    SubfeaturePlanningIndex,
    SystemDesign,
    TechnicalPlan,
    TestAcceptanceCriterion,
    TestPlan,
    TestScenario,
    WorkstreamDecomposition,
)
from ....models.state import BuildState
from ....services.artifacts import structured_artifact_key
from ....services.markdown import to_markdown
from ....roles import (
    InterviewActor,
    dag_compiler,
    dag_path_resolver_role,
    planning_lead_ask_role,
    planning_lead_review_role,
    planning_lead_role,
)
from .._decisions import GLOBAL_DECISIONS_KEY, _decision_sort_key, parse_decision_ledger
from .._sidecars import (
    SHARED_SOURCE_ARTIFACT_KEYS,
    build_shared_planning_index,
    build_subfeature_planning_index,
    canonicalize_subfeature_sidecars,
    canonicalize_acceptance_ids,
    load_source_artifact_text,
    load_structured_artifact,
    normalize_and_persist_source_artifact,
    persist_json_artifact,
    render_structured_markdown,
    update_backfill_status,
)
from ..._common import (
    compile_artifacts,
    get_existing_artifact,
    integration_review,
    interview_gate_review,
    Notify,
)
from ..._common._autonomy import autonomous_remainder_enabled
from ..._common._helpers import (
    _clear_agent_session,
    _artifact_digest,
    _is_root_dag_surface_revision_request,
    _is_model_boundary_failure,
    _text_overlap,
    ContextPackage,
    ContextPackageItem,
    build_context_package,
    targeted_revision,
)
from ..._common._dag_paths import (
    AmbiguousDagPath,
    apply_path_resolution,
    build_dag_path_resolver_prompt,
    canonicalize_implementation_dag,
    dag_path_agentic_resolver_enabled,
    dag_path_canonicalization_enabled,
    dag_path_rewrites_to_records,
    feature_repos_root,
    feature_workspace_root,
    find_retired_backend_path_references,
    planned_new_file_paths,
    resolution_covers_unresolved,
    unresolved_dag_paths,
)

logger = logging.getLogger(__name__)


# Matches AC-id tokens like "AC-1", "AC-auth-flow-3". Accepts alphanumeric
# slug segments and optional trailing numeric suffix. Non-greedy on the slug
# to avoid over-matching into adjacent prose.
_AC_ID_PATTERN = re.compile(r"\bAC-[A-Za-z0-9][A-Za-z0-9-]*\b")
_AC_DEFINITION_PATTERN = re.compile(
    r"(?m)^\s*(?:"
    r"(?:[-*]|\d+[.)])\s*(?:\[[ xX]\]\s*)?(?:\*\*)?"  # list-item: `- **AC-x** — …`
    r"|#{1,6}\s+(?:\*\*)?"  # heading: `### AC-x — …` / `#### AC-x — …`
    r"|\*\*"  # bold paragraph: `**AC-x — …** · refs` / `**AC-x** — …`
    r")(AC-[A-Za-z0-9][A-Za-z0-9-]*)(?:\*\*)?\b"
)

# Matches a top-level "## Acceptance Criteria" (or "## Acceptance Criteria ...")
# heading. Used to scope AC-id extraction to the section where criteria are
# *defined* — the test plan's other sections (test_scenarios,
# verification_checklist, edge_cases) frequently cite AC-ids in prose,
# which would otherwise cause false negatives in coverage checks when a
# typo AC-id happens to appear in narrative.
_AC_SECTION_HEADING = re.compile(r"(?m)^##\s+Acceptance Criteria\b.*$")
_NEXT_H2_HEADING = re.compile(r"(?m)^##\s+\S")
_DECISION_ID_PATTERN = re.compile(r"\bD-[A-Za-z0-9][A-Za-z0-9-]*\b")
_STEP_HEADING_PATTERN = re.compile(r"(?m)^###\s+(STEP-[A-Za-z0-9-]+)\s*:?\s*(.*)$")
_REQ_ID_PATTERN = re.compile(r"\bREQ-[A-Za-z0-9][A-Za-z0-9-]*\b")
_NFR_ID_PATTERN = re.compile(r"\bNFR-[A-Za-z0-9][A-Za-z0-9-]*\b")
_JOURNEY_ID_PATTERN = re.compile(r"\bJ-[A-Za-z0-9][A-Za-z0-9-]*\b")
_STEP_ID_PATTERN = re.compile(r"\bSTEP-[A-Za-z0-9][A-Za-z0-9-]*\b")
_VERIFIABLE_STATE_ID_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9_-]*#[A-Za-z0-9_-]+\b")
_MARKDOWN_METADATA_LINE_PATTERN = re.compile(
    r"(?m)^\s*-\s+[`*]*([A-Za-z_][A-Za-z0-9_]*)[`*]*:[`*]*\s*(.+?)\s*$"
)
_MARKDOWN_AC_BLOCK_PATTERN = re.compile(
    r"(?ms)^\s*-\s+\*\*(AC-[A-Za-z0-9][A-Za-z0-9-]*)\*\*\s*[—–-]\s*(.*?)\s*$"
)
# `**AC-x — Title…** · refs` or `**AC-x** — desc` definitions written as bold
# paragraphs (no list marker).
_MARKDOWN_AC_BOLD_PARAGRAPH_BLOCK_PATTERN = re.compile(
    r"(?m)^\s*\*\*(AC-[A-Za-z0-9][A-Za-z0-9-]*)(?:\*\*)?\s*[—–-]\s*(.*?)\s*$"
)
# `### AC-x — title` / `#### AC-x — title` definitions written as headings.
_MARKDOWN_AC_HEADING_BLOCK_PATTERN = re.compile(
    r"(?m)^\s*#{2,6}\s+(AC-[A-Za-z0-9][A-Za-z0-9-]*)\b(?:\s*[—–:-]\s*(.*?))?\s*$"
)
_MARKDOWN_SCENARIO_HEADING_PATTERN = re.compile(r"(?m)^###\s+(.+?)\s*$")
_EMBEDDED_STEP_HEADING_PATTERN = re.compile(r"(?<!\n)(###\s+STEP-[A-Za-z0-9][^\n]*)")
_STEP_MARKDOWN_HEADING_PATTERN = re.compile(r"(?m)^###\s+(STEP-[A-Za-z0-9-]+)\b[^\n]*$")
_STEP_SECTION_METADATA_PATTERN = re.compile(r"(?m)^-\s+\*\*([^*]+)\*\*\s*(.+?)\s*$")
_TRACE_TOKEN_PATTERN = re.compile(r"`([^`]+)`|\b([A-Za-z][A-Za-z0-9_]*(?:Service|API|Entity|Repository|Workspace|Shell|Runtime)?)\b")
_SLICE_SOURCE_BUDGET = 35_000
_SLICE_MAX_STEPS = 4
_ROOT_DAG_GATE_SURFACES_START = "<!-- BEGIN GENERATED DAG GATE SURFACES -->"
_ROOT_DAG_GATE_SURFACES_END = "<!-- END GENERATED DAG GATE SURFACES -->"
_ROOT_DAG_GATE_MAX_SAME_DIGEST_ATTEMPTS = 1
_ROOT_DAG_FORBIDDEN_HEADER_PATTERNS = (
    r"KeychainBridge",
    r"STUDIO_MAIN_IPC_SOCK",
    r"SecretsResolver",
    r"ipc_protocol.*Keychain",
    r"Main IPC.*Keychain",
    r"Keychain.*Main IPC",
)
_SF14_DEFAULT_VARIANT_TASK_IDS = (
    "review-phase-views-slice-10-TASK-SF14-S10-default-variant",
    "review-phase-views-slice-10-TASK-SF14-S10-default-variant-tests",
)
_SLICE_MANIFEST_DERIVATION_VERSION = 2
_PLANNING_SIDECAR_NORMALIZER_VERSION = "2026-04-22-sidecar-rewrite-v5"


def _cap_bytes_from_env(env_var: str, default: int) -> int:
    """Read a byte-cap knob from the environment at module import.

    Mirrors the CONTEXT_PACKAGE_ITEM_MAX_CHARS style in
    workflows/_common/_helpers.py. Invalid or non-positive values fall back
    to the default (never silently disable the budget guard)."""
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer — using default %d", env_var, raw, default)
        return default
    if value <= 0:
        logger.warning("%s=%d is not positive — using default %d", env_var, value, default)
        return default
    return value


_SLICE_CONTEXT_SOFT_CAP_BYTES = _cap_bytes_from_env(
    "IRIAI_TASK_PLANNING_SLICE_CONTEXT_CAP_BYTES", 180_000
)
_SLICE_PEER_CAP_BYTES = _cap_bytes_from_env(
    "IRIAI_TASK_PLANNING_SLICE_PEER_CAP_BYTES", 60_000
)
_WORKSTREAM_CONTEXT_SOFT_CAP_BYTES = 180_000
_WORKSTREAM_SUBFEATURE_DIGEST_BUDGET = 8_000
_WORKSTREAM_CLUSTER_TARGET_BYTES = 100_000
_SLICE_RETRY_MODES: tuple[tuple[str, bool], ...] = (
    ("all-workstream-peers", False),
    ("direct-peers-only", True),
    ("target-only", True),
)
_BFS_SLUG = "backend-foundation-setup"
_BFS_EFFECTIVE_COVERAGE_WAIVERS: dict[str, str] = {
    "AC-backend-foundation-setup-26": "`recheck_setup` is superseded by `rerun_setup_check`.",
    "AC-backend-foundation-setup-35": "`setup_check_probing` was rescinded in the later authoritative plan.",
    "AC-backend-foundation-setup-36": "`setup_check_probing` was rescinded in the later authoritative plan.",
    "AC-backend-foundation-setup-73": "single-file `decision.key` semantics are superseded by the keyring migration.",
    "AC-backend-foundation-setup-74": "single-file `decision.key` semantics are superseded by the keyring migration.",
}
_BFS_STEP_RECONCILED_OWNED_ACS: dict[str, tuple[str, ...]] = {
    "STEP-2": ("AC-backend-foundation-setup-56",),
    "STEP-8": ("AC-backend-foundation-setup-25",),
    "STEP-9": ("AC-backend-foundation-setup-52",),
    "STEP-13": (
        "AC-backend-foundation-setup-38",
        "AC-backend-foundation-setup-39",
        "AC-backend-foundation-setup-40",
    ),
    "STEP-17": (
        "AC-backend-foundation-setup-76",
        "AC-backend-foundation-setup-77",
        "AC-backend-foundation-setup-78",
        "AC-backend-foundation-setup-79",
        "AC-backend-foundation-setup-80",
        "AC-backend-foundation-setup-81",
        "AC-backend-foundation-setup-82",
    ),
    "STEP-21": (
        "AC-backend-foundation-setup-84",
        "AC-backend-foundation-setup-85",
        "AC-backend-foundation-setup-86",
        "AC-backend-foundation-setup-87",
        "AC-backend-foundation-setup-88",
    ),
}
_BFS_SLICE4_EXPANDED_STEPS: tuple[str, ...] = (
    "STEP-13",
    "STEP-14",
    "STEP-15",
    "STEP-16",
    "STEP-17",
)


@dataclass(slots=True)
class VerificationCoverageResult:
    slug: str
    unknown_gate_refs: list[str] = field(default_factory=list)
    uncovered_ac_ids: list[str] = field(default_factory=list)
    uncovered_owned_ac_ids: list[str] = field(default_factory=list)
    uncovered_global_obligation_ac_ids: list[str] = field(default_factory=list)
    global_obligation_candidate_step_ids: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.unknown_gate_refs and not self.uncovered_ac_ids

    def failure_messages(self) -> list[str]:
        messages = list(self.unknown_gate_refs)
        if self.uncovered_ac_ids:
            messages.append(
                f"test-plan:{self.slug} defines acceptance criteria that no task cites: "
                f"{', '.join(sorted(self.uncovered_ac_ids))}"
            )
        return messages


@dataclass(slots=True)
class RequirementCoverageResult:
    slug: str
    missing_requirement_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_requirement_ids


class PlanningContractError(RuntimeError):
    def __init__(
        self,
        slug: str,
        messages: list[str],
        *,
        report_key: str = "",
    ) -> None:
        self.slug = slug
        self.messages = messages
        self.report_key = report_key
        super().__init__("; ".join(messages))


@dataclass(slots=True)
class DecisionContextBuildResult:
    item: ContextPackageItem | None
    complete: bool = True
    missing_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TaskPlanningFailure:
    workstream_id: str
    slug: str
    reason: str
    invocation_key: str = ""
    context_paths: list[str] = field(default_factory=list)

    def render(self) -> str:
        details: list[str] = []
        if self.invocation_key:
            details.append(f"invocation={self.invocation_key}")
        if self.context_paths:
            details.append(
                "context="
                + ", ".join(f"`{path}`" for path in self.context_paths)
            )
        suffix = f" ({'; '.join(details)})" if details else ""
        return f"{self.workstream_id}/{self.slug}: {self.reason}{suffix}"


@dataclass(slots=True)
class SlicePlanResult:
    slice_id: str
    dag: ImplementationDAG | None = None
    error: str = ""
    retryable: bool = False
    over_budget: bool = False
    attempt: SlicePlanningAttempt | None = None
    context_package: ContextPackage | None = None


class TaskPlanningSlice(BaseModel):
    slice_id: str
    title: str = ""
    step_ids: list[str] = PydanticField(default_factory=list)
    requirement_ids: list[str] = PydanticField(default_factory=list)
    journey_ids: list[str] = PydanticField(default_factory=list)
    acceptance_criterion_ids: list[str] = PydanticField(default_factory=list)
    owned_acceptance_criterion_ids: list[str] = PydanticField(default_factory=list)
    supporting_acceptance_criterion_ids: list[str] = PydanticField(default_factory=list)
    strict_acceptance_criteria: bool = False
    step_titles: list[str] = PydanticField(default_factory=list)
    source_budget_chars: int = _SLICE_SOURCE_BUDGET
    mandatory_source_chars: int = 0
    slice_contract_digest: str = ""
    required_reference_sources: list[str] = PydanticField(default_factory=list)
    global_obligation_ac_ids: list[str] = PydanticField(default_factory=list)


class SlicePlanningStatus(BaseModel):
    slice_id: str
    status: str = "pending"
    retry_mode: str = ""
    context_paths: list[str] = PydanticField(default_factory=list)
    last_error: str = ""
    fragment_key: str = ""


class SlicePlanningAttempt(BaseModel):
    slice_id: str
    mode: str
    attempt: int = 1
    status: str = "failed"
    actor_name: str = ""
    chosen_mode: str = ""
    context_paths: list[str] = PydanticField(default_factory=list)
    attempt_key: str = ""
    error: str = ""
    estimated_context_bytes: int = 0
    size_breakdown: dict[str, int] = PydanticField(default_factory=dict)


class TaskPlanningSliceManifest(BaseModel):
    slug: str
    slices: list[TaskPlanningSlice] = PydanticField(default_factory=list)
    statuses: list[SlicePlanningStatus] = PydanticField(default_factory=list)
    attempts: list[SlicePlanningAttempt] = PydanticField(default_factory=list)
    derivation_version: int = _SLICE_MANIFEST_DERIVATION_VERSION
    plan_digest: str = ""
    test_plan_digest: str = ""
    contract_digest: str = ""
    complete: bool = False


class StepPlanningContract(BaseModel):
    step_id: str
    title: str = ""
    section_digest: str = ""
    requirement_ids: list[str] = PydanticField(default_factory=list)
    journey_ids: list[str] = PydanticField(default_factory=list)
    decision_ids: list[str] = PydanticField(default_factory=list)
    nfr_ids: list[str] = PydanticField(default_factory=list)
    verifiable_state_ids: list[str] = PydanticField(default_factory=list)
    explicit_owned_ac_ids: list[str] = PydanticField(default_factory=list)
    inferred_owned_ac_ids: list[str] = PydanticField(default_factory=list)
    owned_ac_ids: list[str] = PydanticField(default_factory=list)
    supporting_ac_ids: list[str] = PydanticField(default_factory=list)
    required_reference_sources: list[str] = PydanticField(default_factory=list)


class SubfeaturePlanningContract(BaseModel):
    slug: str
    plan_digest: str = ""
    test_plan_digest: str = ""
    contract_digest: str = ""
    canonical_ac_ids: list[str] = PydanticField(default_factory=list)
    waived_ac_ids: list[str] = PydanticField(default_factory=list)
    global_obligation_ac_ids: list[str] = PydanticField(default_factory=list)
    global_obligation_candidate_step_ids: dict[str, list[str]] = PydanticField(default_factory=dict)
    requirement_universe: list[str] = PydanticField(default_factory=list)
    journey_universe: list[str] = PydanticField(default_factory=list)
    decision_universe: list[str] = PydanticField(default_factory=list)
    verifiable_state_universe: list[str] = PydanticField(default_factory=list)
    has_prd_artifact: bool = False
    has_design_artifact: bool = False
    has_system_design_artifact: bool = False
    has_test_plan_artifact: bool = False
    step_contracts: list[StepPlanningContract] = PydanticField(default_factory=list)


async def _preferred_decision_ledger_key(
    runner: WorkflowRunner,
    feature: Feature,
) -> str:
    """Pick the smallest canonical decision ledger that still preserves intent.

    ``decisions`` can become extremely large on mature features. Task planning
    only needs the feature-wide canonical direction, not the entire compiled
    ledger history, so prefer the broad/global ledgers when available and fall
    back to the full compiled ledger only as a last resort.
    """
    for key in ("decisions:broad", "decisions:global", "decisions"):
        text = await runner.artifacts.get(key, feature=feature)
        if text:
            return key
    return "decisions"


def _extract_decision_ids(text: str) -> set[str]:
    if not text:
        return set()
    return set(_DECISION_ID_PATTERN.findall(text))


def _normalize_id_numeric_segments(identifier: str) -> str:
    """Normalize zero-padding drift in id numeric segments for comparison.

    ``REQ-POST-20260606-1`` and ``REQ-POST-20260606-01`` are the same id
    written by different generations of the planning agents. Strip leading
    zeros from purely-numeric dash segments so both sides compare equal.
    Comparison-time only — never used to rewrite stored ids.
    """
    return "-".join(
        (segment.lstrip("0") or "0") if segment.isdigit() else segment
        for segment in identifier.split("-")
    )


def _bare_requirement_family_tokens(
    candidate_ids: Iterable[str],
    known_full_ids: Iterable[str],
) -> set[str]:
    """Identify bare requirement-family shorthand among candidate REQ tokens.

    A bare family token (e.g. ``REQ-POST``) is a digit-less ``REQ-`` token
    that is a dash-prefix of a fuller known id (``REQ-POST-20260606-01``) —
    prose shorthand for the family, not a requirement id itself. Tokens that
    are legitimate alpha-only ids in their own right (e.g. ``REQ-shared``
    with no fuller sibling) are NOT treated as bare.
    """
    full_ids = [full_id for full_id in known_full_ids if full_id.startswith("REQ-")]
    return {
        token
        for token in candidate_ids
        if token.startswith("REQ-")
        and not any(ch.isdigit() for ch in token)
        and any(full_id.startswith(token + "-") for full_id in full_ids)
    }


def _extract_ac_ids(test_plan_text: str) -> set[str]:
    """Extract AC-id tokens from a test-plan artifact (markdown or JSON).

    Tries structured parse first. Falls back to regex scoped to the
    ``## Acceptance Criteria`` section when present, or the whole document
    when no such section is found. Returns AC-ids actually *defined* by the
    test plan — used to validate task ``verification_gates`` against real
    IDs and catch agent typos.
    """
    if not test_plan_text:
        return set()
    # Try structured parse (the agent may have written JSON).
    try:
        data = _json.loads(test_plan_text)
        tp = TestPlan.model_validate(data)
        return {ac.id for ac in tp.acceptance_criteria if ac.id}
    except Exception:
        pass

    def _extract_defined_ids(text: str) -> set[str]:
        return {match.group(1) for match in _AC_DEFINITION_PATTERN.finditer(text)}

    # Scope the regex to the Acceptance Criteria section when we can find
    # it — AC-ids cited in scenarios / checklists / prose are references,
    # not definitions. If the heading is absent (agent wrote free-form
    # markdown without sections), fall back to whole-document regex.
    section_start = _AC_SECTION_HEADING.search(test_plan_text)
    if section_start is None:
        return _extract_defined_ids(test_plan_text) or set(_AC_ID_PATTERN.findall(test_plan_text))
    section_body_start = section_start.end()
    next_heading = _NEXT_H2_HEADING.search(test_plan_text, section_body_start)
    section_body_end = next_heading.start() if next_heading else len(test_plan_text)
    section_text = test_plan_text[section_body_start:section_body_end]
    return _extract_defined_ids(section_text) or set(_AC_ID_PATTERN.findall(section_text))


def _effective_coverage_ids_for_task_planning(
    slug: str,
    canonical_ac_ids: set[str],
    contract_waived_ac_ids: Iterable[str] | None = None,
    store_waived_ac_ids: Iterable[str] | None = None,
) -> tuple[set[str], dict[str, str]]:
    """Resolve the effective (coverage-audited) AC universe for a subfeature.

    Waivers come from three additive sources, all logged loudly (no silent
    waivers):
    - the hardcoded builtin map (backend-foundation-setup only),
    - ``waived_ac_ids`` recorded on the persisted per-SF planning contract
      (data-driven waivers approved at the DAG gate), and
    - the dedicated ``planning-waivers:{slug}`` store key (operator-approved
      waivers written through the artifact-store path; bootstraps waivers
      when no contract row exists yet — see ``_load_planning_waivers``).
    """
    waived: dict[str, str] = {}
    waiver_sources: dict[str, str] = {}
    # Normalized (zero-pad + case drift tolerant) lookup so an operator-
    # recorded waiver id like ``AC-HRDD-039`` still matches the canonical
    # ``AC-hrdd-39``; the CANONICAL spelling is what gets recorded/persisted.
    canonical_by_normalized = {
        _normalize_id_numeric_segments(ac_id).casefold(): ac_id
        for ac_id in canonical_ac_ids
    }

    def _canonical_waiver_id(ac_id: str, source: str) -> str | None:
        resolved = canonical_by_normalized.get(
            _normalize_id_numeric_segments(ac_id).casefold()
        )
        if resolved is None:
            # Fail LOUD (not silent drop): a waiver that matches no canonical
            # AC id never lands in the persisted contract's waived_ac_ids and
            # develop would enforce the AC anyway. Surface it every compile.
            logger.warning(
                "coverage waiver %s (source: %s) matches NO canonical AC id "
                "for %s — waiver NOT applied and NOT persisted; fix the id "
                "(canonical universe has %d ids)",
                ac_id,
                source,
                slug,
                len(canonical_ac_ids),
            )
        return resolved

    if slug == _BFS_SLUG:
        for ac_id, reason in _BFS_EFFECTIVE_COVERAGE_WAIVERS.items():
            if ac_id in canonical_ac_ids:
                waived[ac_id] = reason
                waiver_sources[ac_id] = "builtin"
    for ac_id in contract_waived_ac_ids or []:
        resolved = _canonical_waiver_id(ac_id, "contract")
        if resolved is not None and resolved not in waived:
            waived[resolved] = "waived via planning-contract waived_ac_ids (recorded at the DAG gate)"
            waiver_sources[resolved] = "contract"
    for ac_id in store_waived_ac_ids or []:
        resolved = _canonical_waiver_id(ac_id, "store")
        if resolved is not None and resolved not in waived:
            waived[resolved] = (
                f"waived via planning-waivers:{slug} store key (operator-approved waiver)"
            )
            waiver_sources[resolved] = "store"
    for ac_id in sorted(waived):
        logger.warning(
            "coverage waiver applied: %s (source: %s) — %s [%s]",
            ac_id,
            waiver_sources[ac_id],
            waived[ac_id],
            slug,
        )
    return set(canonical_ac_ids) - set(waived), waived


async def _load_planning_waivers(
    runner: WorkflowRunner,
    feature: Feature,
    slug: str,
) -> list[str]:
    """Load operator-approved AC waivers from the ``planning-waivers:{slug}``
    store key (a SECOND additive waiver source alongside the prior-contract
    ``waived_ac_ids``).

    This bootstraps waivers when no dag-contract row exists yet: contracts
    only persist after a successful compile, but the compile fails closed on
    exactly the unwaived ACs (chicken-and-egg). The key is written through
    the artifact-store path by the operator/driver only — its existence is
    the opt-in; there is no env flag.

    Expected value: JSON ``{"waived_ac_ids": ["AC-x"], "decisions": ["D-377"],
    "reason": "..."}``. Tolerant loader: missing/invalid → ``[]`` with a
    debug log; non-empty → INFO log listing ids + decisions.
    """
    key = f"planning-waivers:{slug}"
    try:
        text = await runner.artifacts.get(key, feature=feature)
    except Exception:
        logger.debug("planning waivers key %s unavailable", key, exc_info=True)
        return []
    if not text:
        logger.debug("no planning waivers recorded at %s", key)
        return []
    try:
        payload = _json.loads(text)
    except Exception:
        logger.debug("planning waivers at %s are not valid JSON; ignoring", key, exc_info=True)
        return []
    if not isinstance(payload, dict) or not isinstance(payload.get("waived_ac_ids"), list):
        logger.debug(
            "planning waivers at %s lack a waived_ac_ids list; ignoring",
            key,
        )
        return []
    waived = [ac_id for ac_id in payload["waived_ac_ids"] if isinstance(ac_id, str) and ac_id]
    if waived:
        decisions = payload.get("decisions")
        decision_ids = (
            [str(decision) for decision in decisions] if isinstance(decisions, list) else []
        )
        logger.info(
            "planning waivers store key %s active: %s (decisions: %s; reason: %s)",
            key,
            ", ".join(waived),
            ", ".join(decision_ids) or "none",
            payload.get("reason") or "none",
        )
    return waived


DAG_PACKING_ENVELOPE_KEY = "dag-packing-envelope"


async def _load_dag_packing_envelope_section(
    runner: WorkflowRunner,
    feature: Feature,
) -> str:
    """Load the operator-pinned DAG packing envelope and render it as a
    delimited prompt section for the task-planning authoring/review agents.

    The ``dag-packing-envelope`` store key is written only by the operator/
    driver through the artifact-store path; key presence is the opt-in —
    there is no env flag (same pattern as ``planning-waivers:{slug}``).
    When present, the text is injected verbatim into the slice-planner,
    workstream-planner, and DAG integration-review prompts so the first
    authored draft fits the operator's packing targets instead of needing
    an oversized-draft revision round.

    Tolerant loader: missing/unreadable/invalid (non-string or blank) →
    ``""`` with a debug log; present → INFO log.
    """
    try:
        text = await runner.artifacts.get(DAG_PACKING_ENVELOPE_KEY, feature=feature)
    except Exception:
        logger.debug(
            "dag packing envelope key %s unavailable",
            DAG_PACKING_ENVELOPE_KEY,
            exc_info=True,
        )
        return ""
    if not isinstance(text, str) or not text.strip():
        logger.debug(
            "no dag packing envelope recorded at %s",
            DAG_PACKING_ENVELOPE_KEY,
        )
        return ""
    body = text.strip()
    logger.info(
        "dag packing envelope store key %s active (%d chars) — injecting "
        "operator-pinned packing constraints into task-planning prompts",
        DAG_PACKING_ENVELOPE_KEY,
        len(body),
    )
    return (
        "## DAG Packing Envelope (operator-pinned)\n"
        "The operator pinned the packing envelope below for this feature's "
        "implementation DAG. Treat it as binding sizing/topology guidance — "
        "it overrides default granularity heuristics. Author (and review) "
        "the DAG to fit it on the first pass.\n\n"
        f"{body}\n\n"
        "(End of operator-pinned DAG packing envelope.)\n\n"
    )


SLICE_CONTRACT_AUGMENTS_KEY_PREFIX = "dag-slice-augments"


class SliceContractAugment(BaseModel):
    """One operator-pinned ADDITIVE augment for a named task-planning slice.

    Used to materialize scope the deterministic plan-derived partition cannot
    see (e.g. a mandated step with no ``### STEP-x`` heading in the plan, or
    PRD requirements cited by no step section). Strictly additive — augments
    can only widen a slice's contract, never remove or replace ids."""

    add_step_ids: list[str] = PydanticField(default_factory=list)
    add_step_titles: list[str] = PydanticField(default_factory=list)
    add_requirement_ids: list[str] = PydanticField(default_factory=list)
    add_journey_ids: list[str] = PydanticField(default_factory=list)


async def _load_slice_contract_augments(
    runner: WorkflowRunner,
    feature: Feature,
    slug: str,
) -> dict[str, SliceContractAugment]:
    """Load operator-pinned slice-contract augments from the
    ``dag-slice-augments:{slug}`` store key.

    The key is written only by the operator/driver through the artifact-store
    path; key presence is the opt-in — there is no env flag (same pattern as
    ``planning-waivers:{slug}`` / ``dag-packing-envelope``). Expected value:
    JSON ``{"slices": {"slice-5": {"add_step_ids": ["STEP-13"],
    "add_requirement_ids": ["REQ-30"]}}, "decisions": ["D-246"],
    "reason": "..."}``.

    Missing/empty key → ``{}``. A PRESENT but malformed payload raises
    RuntimeError: silently dropping an operator pin would burn a full
    planning re-run before anyone noticed (no-silent-degradation)."""
    key = f"{SLICE_CONTRACT_AUGMENTS_KEY_PREFIX}:{slug}"
    try:
        text = await runner.artifacts.get(key, feature=feature)
    except Exception:
        logger.debug("slice contract augments key %s unavailable", key, exc_info=True)
        return {}
    if not text or not text.strip():
        return {}
    try:
        payload = _json.loads(text)
        slices_payload = payload.get("slices") if isinstance(payload, dict) else None
        if not isinstance(slices_payload, dict) or not slices_payload:
            raise ValueError("payload must be an object with a non-empty 'slices' map")
        augments = {
            str(slice_id): SliceContractAugment.model_validate(augment)
            for slice_id, augment in slices_payload.items()
        }
    except Exception as exc:
        raise RuntimeError(
            f"operator slice-contract augments at {key} are malformed and would "
            f"be silently dropped: {exc}. Fix the store key (expected "
            '{"slices": {"<slice-id>": {"add_step_ids": [...], '
            '"add_requirement_ids": [...]}}}) or delete it.'
        ) from exc
    decisions = payload.get("decisions") if isinstance(payload, dict) else None
    logger.info(
        "slice contract augments store key %s active for slices %s "
        "(decisions: %s; reason: %s)",
        key,
        ", ".join(sorted(augments)),
        ", ".join(str(d) for d in decisions) if isinstance(decisions, list) else "none",
        (payload.get("reason") if isinstance(payload, dict) else None) or "none",
    )
    return augments


async def _load_migrated_test_plan_sidecar_ac_ids(
    runner: WorkflowRunner,
    feature: Feature,
    slug: str,
) -> tuple[set[str], bool]:
    """Return canonical AC-ids from the structured test-plan sidecar.

    For migrated slugs, the structured test-plan sidecar is the canonical
    source of truth for AC existence. The persisted dag-contract is derived
    from it and may drift if task planning was interrupted mid-regeneration.
    """
    try:
        backfill_text = await get_existing_artifact(
            runner,
            feature,
            "artifact-backfill-status",
        )
    except Exception:
        backfill_text = ""
    if not backfill_text:
        return set(), False
    try:
        backfill_status = ArtifactBackfillStatus.model_validate_json(backfill_text)
    except Exception:
        logger.warning(
            "Failed to parse artifact-backfill-status while validating verification gates for %s",
            slug,
            exc_info=True,
        )
        return set(), False
    subfeature_status = backfill_status.subfeatures.get(slug)
    if subfeature_status is None or subfeature_status.migration_state != "migrated":
        return set(), False
    sidecar = await load_structured_artifact(runner, feature, f"test-plan:{slug}")
    if sidecar is None:
        return set(), True
    return {
        criterion.id
        for criterion in sidecar.content.acceptance_criteria
        if criterion.id
    }, True


async def _validate_verification_gates_coverage(
    runner: WorkflowRunner,
    feature: Feature,
    slug: str,
    sf_tasks: list[ImplementationTask],
) -> VerificationCoverageResult:
    """Post-decomposition lint: cross-check ``verification_gates`` against
    real AC-ids in the subfeature's test plan.

    Returns a structured result so task planning can fail closed on
    verification coverage drift instead of only logging diagnostics.
    """
    result = VerificationCoverageResult(slug=slug)
    contract_text = await get_existing_artifact(runner, feature, f"dag-contract:{slug}")
    contract: SubfeaturePlanningContract | None = None
    if contract_text:
        try:
            contract = SubfeaturePlanningContract.model_validate_json(contract_text)
        except Exception:
            logger.warning("Failed to parse planning contract for %s while validating coverage", slug)
    sidecar_ac_ids, migrated_slug = await _load_migrated_test_plan_sidecar_ac_ids(
        runner,
        feature,
        slug,
    )
    tp_text = await get_existing_artifact(runner, feature, f"test-plan:{slug}")
    if not tp_text and contract is None and not sidecar_ac_ids:
        return result  # No test plan or contract for this SF — nothing to validate against.
    real_ac_ids = set(sidecar_ac_ids)
    if not real_ac_ids and contract is not None:
        real_ac_ids = set(contract.canonical_ac_ids)
    if not real_ac_ids:
        real_ac_ids = _extract_ac_ids(tp_text)
    if not real_ac_ids:
        logger.warning(
            "Test plan for %s has no extractable AC-ids; verification_gates cannot be validated",
            slug,
        )
        return result
    contract_matches_canonical_ac_ids = (
        contract is not None and set(contract.canonical_ac_ids) == real_ac_ids
    )
    if migrated_slug and contract is not None and not contract_matches_canonical_ac_ids:
        logger.warning(
            "Planning contract for %s has stale canonical_ac_ids; using test-plan sidecar AC universe for verification gate validation",
            slug,
        )
    store_waived_ac_ids = await _load_planning_waivers(runner, feature, slug)
    waived_ac_ids = (
        {ac_id: "contract waiver" for ac_id in contract.waived_ac_ids}
        if contract_matches_canonical_ac_ids
        else _effective_coverage_ids_for_task_planning(
            slug,
            real_ac_ids,
            contract.waived_ac_ids if contract is not None else None,
            store_waived_ac_ids=store_waived_ac_ids,
        )[1]
    )
    if contract_matches_canonical_ac_ids:
        # The contract fast-path bypasses the effective-coverage resolver;
        # union the store-key waivers in here too (additive, loudly logged).
        # Same normalized matching + loud-miss rule as the resolver path.
        real_by_normalized = {
            _normalize_id_numeric_segments(real_id).casefold(): real_id
            for real_id in real_ac_ids
        }
        for ac_id in store_waived_ac_ids:
            resolved = real_by_normalized.get(
                _normalize_id_numeric_segments(ac_id).casefold()
            )
            if resolved is None:
                logger.warning(
                    "coverage waiver %s (source: store) matches NO canonical AC id "
                    "for %s — waiver NOT applied; fix the id in planning-waivers:%s",
                    ac_id,
                    slug,
                    slug,
                )
                continue
            if resolved not in waived_ac_ids:
                waived_ac_ids[resolved] = (
                    f"waived via planning-waivers:{slug} store key (operator-approved waiver)"
                )
                logger.warning(
                    "coverage waiver applied: %s (source: store) — %s [%s]",
                    resolved,
                    waived_ac_ids[resolved],
                    slug,
                )
    effective_ac_ids = set(real_ac_ids) - set(waived_ac_ids)
    if waived_ac_ids:
        logger.info(
            "Task planning coverage for %s waives superseded AC-ids: %s",
            slug,
            ", ".join(sorted(waived_ac_ids)),
        )
    cited_ac_ids: set[str] = set()
    for task in sf_tasks:
        for gate in task.verification_gates:
            if not gate:
                continue
            cited_ac_ids.add(gate)
            if gate not in real_ac_ids:
                message = (
                    f"Task {task.id} ({slug}) cites verification_gate {gate!r} "
                    f"not found in test-plan:{slug}"
                )
                result.unknown_gate_refs.append(message)
                logger.warning("%s — blocking DAG persistence", message)
    uncovered = effective_ac_ids - cited_ac_ids
    if uncovered:
        result.uncovered_ac_ids = sorted(uncovered)
        logger.warning(
            "Test plan for %s defines %d AC-ids not cited by any task's verification_gates: %s "
            "— blocking DAG persistence",
            slug, len(uncovered), result.uncovered_ac_ids,
        )
    if contract_matches_canonical_ac_ids:
        global_obligation_ids = set(contract.global_obligation_ac_ids)
        result.uncovered_global_obligation_ac_ids = sorted(uncovered & global_obligation_ids)
        result.uncovered_owned_ac_ids = sorted(uncovered)
        result.global_obligation_candidate_step_ids = {
            ac_id: step_ids
            for ac_id, step_ids in contract.global_obligation_candidate_step_ids.items()
            if ac_id in result.uncovered_global_obligation_ac_ids
        }
    else:
        result.uncovered_owned_ac_ids = result.uncovered_ac_ids[:]
    return result

# ── Actors ──────────────────────────────────────────────────────────────────

_workstream_planner = AgentActor(
    name="workstream-planner",
    # Ask-only structured output (WorkstreamDecomposition) — read-only role
    # variant so claude-pool dispatch never demands a runtime workspace
    # binding (W-4; see roles._ask_only_role).
    role=planning_lead_ask_role,
    context_keys=["project", "scope", "decomposition"],
)


def _slice_planner_actor(name: str) -> AgentActor:
    """Build a dag-ws slice-planner Ask actor.

    Slice planners return ONLY structured output (ImplementationDAG) and
    never write workspace files, so they use the ask-only planning-lead role
    variant: with Write/Bash present, claude_pool._role_is_write_producing()
    classifies the job write-producing and pool dispatch raises
    RuntimeError('Claude pool write-producing job requires runtime workspace
    binding') (W-4). Role.name stays 'planning-lead' for economy mapping."""
    return AgentActor(
        name=name,
        role=planning_lead_ask_role,
        context_keys=["project", "scope"],
    )


_sf_task_planner_gate_reviewer = InterviewActor(
    name="sf-task-planner-gate-reviewer",
    role=planning_lead_review_role,
    context_keys=["project", "scope", "decomposition"],
)

_sf_task_planner_reviewer = InterviewActor(
    name="sf-task-planner-reviewer",
    role=planning_lead_review_role,
    context_keys=["project", "scope", "decomposition"],
)


# ── Phase ────────────────────────────────────────────────────────────────────


class TaskPlanningPhase(Phase):
    name = "task-planning"

    @staticmethod
    def _context_paths(*packages: ContextPackage | None) -> list[str]:
        paths: list[str] = []
        for package in packages:
            if package is None:
                continue
            for path in (package.index_path, package.manifest_path):
                if path and path not in paths:
                    paths.append(path)
        return paths

    @staticmethod
    def _context_layer_for_key(key: str) -> str:
        if key in {
            "plan",
            "prd",
            "design",
            "system-design",
            "test-plan",
            "subfeature-decisions",
            "contract",
        }:
            return "target"
        if key == "decision-pack":
            return "decision"
        if key in {"broad-prd", "broad-design", "broad-plan"}:
            return "broad"
        if key in {"metadata", "neighborhood", "edges"}:
            return "neighborhood"
        if key == "peer-context":
            return "peer"
        return "other"

    @classmethod
    def _estimate_context_package(
        cls,
        package: ContextPackage | None,
    ) -> tuple[int, dict[str, int]]:
        if package is None:
            return 0, {}

        breakdown: dict[str, int] = {}
        total = 0
        for key, path in package.item_paths.items():
            try:
                size = Path(path).stat().st_size
            except OSError:
                continue
            total += size
            layer = cls._context_layer_for_key(key)
            breakdown[layer] = breakdown.get(layer, 0) + size
        return total, breakdown

    @classmethod
    def _slice_context_over_budget(
        cls,
        total_bytes: int,
        size_breakdown: dict[str, int],
        *,
        mode_label: str,
    ) -> bool:
        if total_bytes > _SLICE_CONTEXT_SOFT_CAP_BYTES:
            return True
        if mode_label != "target-only" and size_breakdown.get("peer", 0) > _SLICE_PEER_CAP_BYTES:
            return True
        return False

    @staticmethod
    def _slice_over_budget_error(
        total_bytes: int,
        size_breakdown: dict[str, int],
    ) -> str:
        """Build the over-budget attempt error with the full layer breakdown.

        The per-layer sizes (and the active caps, which are env-tunable via
        IRIAI_TASK_PLANNING_SLICE_CONTEXT_CAP_BYTES /
        IRIAI_TASK_PLANNING_SLICE_PEER_CAP_BYTES) make the next overflow
        diagnosable from the attempt record alone."""
        breakdown = ", ".join(
            f"{layer}={size}" for layer, size in sorted(size_breakdown.items())
        )
        return (
            "estimated context exceeds task-planning budget "
            f"({total_bytes} bytes, peer={size_breakdown.get('peer', 0)}, "
            f"cap={_SLICE_CONTEXT_SOFT_CAP_BYTES}, "
            f"peer_cap={_SLICE_PEER_CAP_BYTES}, "
            f"size_breakdown=[{breakdown}])"
        )

    @staticmethod
    async def _clear_stale_blocked_artifact(
        runner: WorkflowRunner,
        feature: Feature,
    ) -> None:
        delete = getattr(runner.artifacts, "delete", None)
        if callable(delete):
            await delete("task-planning-blocked", feature=feature)
        mirror = runner.services.get("artifact_mirror")
        if mirror is not None:
            mirror.delete_artifact(feature.id, "task-planning-blocked")

    @staticmethod
    async def _put_artifact(
        runner: WorkflowRunner,
        feature: Feature,
        key: str,
        text: str,
    ) -> None:
        await runner.artifacts.put(key, text, feature=feature)
        mirror = runner.services.get("artifact_mirror")
        if mirror:
            mirror.write_artifact(feature.id, key, text)

    @classmethod
    async def _load_backfill_status(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
    ) -> ArtifactBackfillStatus | None:
        status_text = await get_existing_artifact(runner, feature, "artifact-backfill-status") or ""
        if not status_text:
            return None
        try:
            payload = _json.loads(status_text)
            if payload.get("normalizer_version") != _PLANNING_SIDECAR_NORMALIZER_VERSION:
                logger.info(
                    "Discarding stale artifact-backfill-status for %s due to normalizer version mismatch",
                    feature.id,
                )
                return None
            return ArtifactBackfillStatus.model_validate_json(status_text)
        except Exception:
            logger.warning("Failed to parse artifact-backfill-status", exc_info=True)
            return None

    @classmethod
    async def _save_backfill_status(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        status: ArtifactBackfillStatus,
    ) -> None:
        status.normalizer_version = _PLANNING_SIDECAR_NORMALIZER_VERSION
        await persist_json_artifact(runner, feature, "artifact-backfill-status", status)

    @classmethod
    async def _load_shared_planning_index(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
    ) -> SharedPlanningIndex | None:
        payload = await get_existing_artifact(runner, feature, "planning-index:shared") or ""
        if not payload:
            return None
        try:
            return SharedPlanningIndex.model_validate_json(payload)
        except Exception:
            logger.warning("Failed to parse planning-index:shared", exc_info=True)
            return None

    @classmethod
    async def _load_subfeature_planning_index(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
    ) -> SubfeaturePlanningIndex | None:
        payload = await get_existing_artifact(runner, feature, f"planning-index:{slug}") or ""
        if not payload:
            return None
        try:
            return SubfeaturePlanningIndex.model_validate_json(payload)
        except Exception:
            logger.warning("Failed to parse planning-index:%s", slug, exc_info=True)
            return None

    @staticmethod
    def _shared_bootstrap_keys() -> tuple[str, ...]:
        return (
            "decomposition",
            "prd:broad",
            "design:broad",
            "plan:broad",
            "decisions:broad",
            "decisions:global",
        )

    @staticmethod
    def _subfeature_source_keys(slug: str) -> tuple[str, ...]:
        return (
            f"prd:{slug}",
            f"design:{slug}",
            f"plan:{slug}",
            f"system-design:{slug}",
            f"test-plan:{slug}",
            f"decisions:{slug}",
        )

    @staticmethod
    def _subfeature_sidecar_family_map(
        sidecars: dict[str, StructuredArtifact[Any]],
    ) -> dict[str, StructuredArtifact[Any]]:
        return {
            sidecar.meta.artifact_family: sidecar
            for sidecar in sidecars.values()
        }

    @classmethod
    def _slug_is_migrated(
        cls,
        status: ArtifactBackfillStatus | None,
        slug: str,
    ) -> bool:
        if status is None:
            return False
        subfeature_status = status.subfeatures.get(slug)
        if subfeature_status is None:
            return False
        return (
            subfeature_status.migration_state == "migrated"
            and subfeature_status.join_complete
            and bool(subfeature_status.planning_index_digest)
        )

    @classmethod
    def _shared_artifacts_are_migrated(
        cls,
        status: ArtifactBackfillStatus | None,
    ) -> bool:
        if status is None or not status.shared_statuses:
            return False
        return all(
            artifact_status.status == "migrated"
            for artifact_status in status.shared_statuses.values()
        )

    @classmethod
    def _shared_artifact_is_migrated(
        cls,
        status: ArtifactBackfillStatus | None,
        artifact_key: str,
    ) -> bool:
        if status is None:
            return False
        artifact_status = status.shared_statuses.get(artifact_key.split(":", 1)[0])
        return artifact_status is not None and artifact_status.status == "migrated"

    @classmethod
    async def _shared_sidecars_are_usable(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        status: ArtifactBackfillStatus | None,
    ) -> bool:
        if not cls._shared_artifacts_are_migrated(status):
            return False
        if await cls._load_shared_planning_index(runner, feature) is None:
            return False
        for artifact_key in cls._shared_bootstrap_keys():
            if await load_structured_artifact(runner, feature, artifact_key) is None:
                return False
        return True

    @classmethod
    async def _subfeature_sidecars_are_usable(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
        status: ArtifactBackfillStatus | None,
    ) -> bool:
        if not cls._slug_is_migrated(status, slug):
            return False
        if await cls._load_subfeature_planning_index(runner, feature, slug) is None:
            return False
        for artifact_key in cls._subfeature_source_keys(slug):
            sidecar = await load_structured_artifact(runner, feature, artifact_key)
            if sidecar is None:
                return False
            # STALENESS: a sidecar is a projection of its source markdown.
            # Sanctioned direct-update patches (the T-1 ownership lane) write
            # the markdown; a sidecar generated from an older source would
            # silently feed pre-patch content to slice derivation forever
            # (resume51: the settings AC-ownership patch reached the contract
            # but never the plan sidecar -> slices owned 0 ACs). source_hash
            # exists exactly for this — mismatch means re-backfill the slug.
            try:
                current_text = await load_source_artifact_text(runner, feature, artifact_key)
            except Exception:
                continue
            if not current_text.strip():
                continue
            current_hash = hashlib.sha256(current_text.encode("utf-8")).hexdigest()
            if current_hash != sidecar.meta.source_hash:
                logger.warning(
                    "planning sidecar for %s is STALE (source markdown changed "
                    "since backfill: %s != %s) — re-backfilling %s",
                    artifact_key,
                    current_hash[:12],
                    (sidecar.meta.source_hash or "")[:12],
                    slug,
                )
                return False
        return True

    @classmethod
    async def _render_structured_markdown_for_artifact(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        artifact_key: str,
    ) -> str:
        sidecar = await load_structured_artifact(runner, feature, artifact_key)
        if sidecar is None:
            raise RuntimeError(f"structured sidecar missing for migrated artifact {artifact_key}")
        return render_structured_markdown(sidecar)

    @classmethod
    async def _load_artifact_text_for_planning(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        artifact_key: str,
        *,
        backfill_status: ArtifactBackfillStatus | None = None,
    ) -> str:
        status = backfill_status or await cls._load_backfill_status(runner, feature)
        artifact_scope = artifact_key.split(":", 1)
        if len(artifact_scope) == 2:
            scope = artifact_scope[1]
            if scope in {"broad", "global"}:
                if cls._shared_artifact_is_migrated(status, artifact_key):
                    return await cls._render_structured_markdown_for_artifact(
                        runner,
                        feature,
                        artifact_key,
                    )
            elif cls._slug_is_migrated(status, scope):
                return await cls._render_structured_markdown_for_artifact(
                    runner,
                    feature,
                    artifact_key,
                )
        elif artifact_key == "decomposition" and cls._shared_artifact_is_migrated(status, artifact_key):
            return await cls._render_structured_markdown_for_artifact(
                runner,
                feature,
                artifact_key,
            )
        return await runner.artifacts.get(artifact_key, feature=feature) or ""

    @classmethod
    async def _ensure_shared_sidecar_bootstrap(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        status: ArtifactBackfillStatus | None = None,
    ) -> tuple[SharedPlanningIndex | None, ArtifactBackfillStatus]:
        status = status or ArtifactBackfillStatus()
        if await cls._shared_sidecars_are_usable(runner, feature, status):
            return await cls._load_shared_planning_index(runner, feature), status
        shared_sidecars: dict[str, StructuredArtifact[Any]] = {}
        shared_parity_failed = False
        for artifact_key in cls._shared_bootstrap_keys():
            artifact_text = await load_source_artifact_text(runner, feature, artifact_key)
            if not artifact_text.strip():
                continue
            result = await normalize_and_persist_source_artifact(
                runner,
                feature,
                artifact_key,
                artifact_text,
                generated_from="markdown_backfill",
            )
            status = update_backfill_status(
                status,
                artifact_family=artifact_key.split(":", 1)[0],
                source_hash=result.sidecar.meta.source_hash,
                sidecar_key_name=result.sidecar_key,
                sidecar_digest=result.sidecar.meta.content_digest,
                parity_messages=result.parity_messages,
                shared=True,
            )
            shared_sidecars[artifact_key] = result.sidecar
            shared_parity_failed = shared_parity_failed or bool(result.parity_messages)

        if shared_parity_failed:
            await cls._delete_artifact_key(runner, feature, "planning-index:shared")
            await cls._save_backfill_status(runner, feature, status)
            return None, status

        shared_index = build_shared_planning_index(
            shared_sidecars.get("decomposition"),
            shared_sidecars.get("prd:broad"),
            [
                sidecar
                for artifact_key, sidecar in shared_sidecars.items()
                if artifact_key in {"decisions:broad", "decisions:global"}
            ],
        )
        await persist_json_artifact(runner, feature, "planning-index:shared", shared_index)
        for artifact_key, sidecar in shared_sidecars.items():
            status = update_backfill_status(
                status,
                artifact_family=artifact_key.split(":", 1)[0],
                source_hash=sidecar.meta.source_hash,
                sidecar_key_name=structured_artifact_key(artifact_key),
                sidecar_digest=sidecar.meta.content_digest,
                parity_messages=[],
                shared=True,
                migrated=True,
            )
        await cls._save_backfill_status(runner, feature, status)
        return shared_index, status

    @classmethod
    async def _ensure_subfeature_sidecar_backfill(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
        *,
        shared_index: SharedPlanningIndex | None,
        status: ArtifactBackfillStatus | None = None,
    ) -> ArtifactBackfillStatus:
        status = status or ArtifactBackfillStatus()
        if await cls._subfeature_sidecars_are_usable(runner, feature, slug, status):
            return status
        sidecars: dict[str, StructuredArtifact[Any]] = {}
        source_hashes: dict[str, str] = {}
        generated_sidecars: list[str] = []
        audit_issues: list[ArtifactAuditIssue] = []
        parity_failed = False
        missing_families: list[str] = []

        for artifact_key in cls._subfeature_source_keys(slug):
            artifact_text = await load_source_artifact_text(runner, feature, artifact_key)
            if not artifact_text.strip():
                missing_families.append(artifact_key.split(":", 1)[0])
                continue
            result = await normalize_and_persist_source_artifact(
                runner,
                feature,
                artifact_key,
                artifact_text,
                generated_from="markdown_backfill",
            )
            status = update_backfill_status(
                status,
                slug=slug,
                artifact_family=artifact_key.split(":", 1)[0],
                source_hash=result.sidecar.meta.source_hash,
                sidecar_key_name=result.sidecar_key,
                sidecar_digest=result.sidecar.meta.content_digest,
                parity_messages=result.parity_messages,
            )
            sidecars[artifact_key] = result.sidecar
            source_hashes[artifact_key] = result.sidecar.meta.source_hash
            generated_sidecars.append(result.sidecar_key)
            audit_issues.extend(result.issues)
            parity_failed = parity_failed or bool(result.parity_messages)

        subfeature_status = status.subfeatures.setdefault(
            slug,
            ArtifactBackfillSubfeatureStatus(slug=slug),
        )
        if missing_families:
            audit_issues.extend(
                ArtifactAuditIssue(
                    classification="true_missing_canonical_ref",
                    artifact_family=family,
                    artifact_key=f"{family}:{slug}",
                    message=f"missing source artifact family {family}",
                )
                for family in missing_families
            )
        if audit_issues:
            await persist_json_artifact(
                runner,
                feature,
                f"artifact-audit:{slug}",
                ArtifactAuditReport(
                    slug=slug,
                    source_hashes=source_hashes,
                    generated_sidecars=sorted(dict.fromkeys(generated_sidecars)),
                    issues=audit_issues,
                    complete=not parity_failed and not missing_families,
                ),
            )
        if missing_families:
            subfeature_status.migration_state = "backfilled" if sidecars else "not_started"
            subfeature_status.join_complete = False
            subfeature_status.planning_index_digest = ""
            await cls._save_backfill_status(runner, feature, status)
            return status

        if parity_failed:
            subfeature_status.migration_state = "parity_failed"
            subfeature_status.join_complete = False
            subfeature_status.planning_index_digest = ""
            await cls._save_backfill_status(runner, feature, status)
            return status

        index, report = build_subfeature_planning_index(
            slug,
            cls._subfeature_sidecar_family_map(sidecars),
            shared_index=shared_index,
        )
        await persist_json_artifact(runner, feature, f"artifact-audit:{slug}", report)
        await persist_json_artifact(runner, feature, f"planning-index:{slug}", index)
        subfeature_status.join_complete = True
        subfeature_status.planning_index_digest = index.index_digest
        subfeature_status.migration_state = "migrated" if not report.issues else "backfilled"
        await cls._save_backfill_status(runner, feature, status)
        return status

    @classmethod
    async def _write_artifact_audit_summary(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        status: ArtifactBackfillStatus | None,
    ) -> None:
        status = status or ArtifactBackfillStatus()
        migrated_slugs: list[str] = []
        parity_failed_slugs: list[str] = []
        blocked_slugs: list[str] = []
        repair_required_slugs: list[str] = []
        for subfeature in decomposition.subfeatures:
            if not subfeature.slug:
                continue
            sub_status = status.subfeatures.get(subfeature.slug)
            migration_state = sub_status.migration_state if sub_status is not None else "not_started"
            audit_text = await runner.artifacts.get(f"artifact-audit:{subfeature.slug}", feature=feature) or ""
            if audit_text:
                try:
                    report = ArtifactAuditReport.model_validate_json(audit_text)
                except Exception:
                    report = None
                if report is not None and report.issues:
                    repair_required_slugs.append(subfeature.slug)
            if migration_state == "migrated":
                migrated_slugs.append(subfeature.slug)
                continue
            if migration_state == "parity_failed":
                parity_failed_slugs.append(subfeature.slug)
                continue
            blocked_slugs.append(subfeature.slug)

        payload = {
            "shared_bootstrap": {
                artifact_family: artifact_status.status
                for artifact_family, artifact_status in sorted(status.shared_statuses.items())
            },
            "migrated_slugs": migrated_slugs,
            "parity_failed_slugs": parity_failed_slugs,
            "blocked_slugs": blocked_slugs,
            "source_repairs_required": sorted(set(repair_required_slugs)),
        }
        await cls._put_artifact(
            runner,
            feature,
            "artifact-audit-summary",
            _json.dumps(payload, indent=2, sort_keys=True),
        )

    @classmethod
    async def _ensure_planning_sidecar_migration(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
    ) -> ArtifactBackfillStatus:
        status = await cls._load_backfill_status(runner, feature)
        shared_index, status = await cls._ensure_shared_sidecar_bootstrap(
            runner,
            feature,
            status=status,
        )
        for subfeature in decomposition.subfeatures:
            if not subfeature.slug:
                continue
            status = await cls._ensure_subfeature_sidecar_backfill(
                runner,
                feature,
                subfeature.slug,
                shared_index=shared_index,
                status=status,
            )
        await cls._write_artifact_audit_summary(runner, feature, decomposition, status)
        return status

    @classmethod
    async def _load_slice_manifest(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
    ) -> TaskPlanningSliceManifest | None:
        manifest_text = await runner.artifacts.get(f"dag-slices:{slug}", feature=feature)
        if not manifest_text:
            return None
        try:
            return TaskPlanningSliceManifest.model_validate_json(manifest_text)
        except Exception:
            logger.warning("Failed to parse existing slice manifest for %s", slug)
            return None

    @classmethod
    async def _save_slice_manifest(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        manifest: TaskPlanningSliceManifest,
    ) -> None:
        await cls._put_artifact(
            runner,
            feature,
            f"dag-slices:{manifest.slug}",
            manifest.model_dump_json(indent=2),
        )

    @staticmethod
    def _slice_status_map(
        manifest: TaskPlanningSliceManifest,
    ) -> dict[str, SlicePlanningStatus]:
        return {status.slice_id: status for status in manifest.statuses}

    @staticmethod
    def _content_digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def _json_digest(cls, payload: Any) -> str:
        return cls._content_digest(
            _json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True)
        )

    @staticmethod
    def _contract_artifact_key(slug: str) -> str:
        return f"dag-contract:{slug}"

    @staticmethod
    def _contract_report_artifact_key(slug: str) -> str:
        return f"dag-contract-report:{slug}"

    @classmethod
    async def _load_subfeature_planning_contract(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
    ) -> SubfeaturePlanningContract | None:
        contract_text = await runner.artifacts.get(cls._contract_artifact_key(slug), feature=feature)
        if not contract_text:
            return None
        try:
            return SubfeaturePlanningContract.model_validate_json(contract_text)
        except Exception:
            logger.warning("Failed to parse planning contract for %s", slug)
            return None

    @classmethod
    async def _save_subfeature_planning_contract(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        contract: SubfeaturePlanningContract,
    ) -> None:
        await cls._put_artifact(
            runner,
            feature,
            cls._contract_artifact_key(contract.slug),
            contract.model_dump_json(indent=2),
        )

    @classmethod
    async def _save_subfeature_planning_contract_report(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        *,
        slug: str,
        messages: list[str],
    ) -> str:
        key = cls._contract_report_artifact_key(slug)
        lines = [
            f"# Planning Contract Report — {slug}",
            "",
            "The deterministic planning-contract compiler found inconsistencies before slice planning began.",
            "",
            "## Findings",
            "",
            *[f"- {message}" for message in messages],
            "",
        ]
        await cls._put_artifact(runner, feature, key, "\n".join(lines).rstrip() + "\n")
        return key

    @classmethod
    async def _clear_subfeature_planning_contract_report(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
    ) -> None:
        existing = await runner.artifacts.get(
            cls._contract_report_artifact_key(slug),
            feature=feature,
        )
        if not existing:
            return
        await cls._delete_artifact_key(
            runner,
            feature,
            cls._contract_report_artifact_key(slug),
        )

    @staticmethod
    def _reference_source_family(source: str) -> str:
        lowered = source.strip().lower()
        if "test-plan" in lowered or "test plan" in lowered:
            return "test-plan"
        if (
            "system-design" in lowered
            or "system design" in lowered
            or "systemdesign" in lowered
            or "entity:" in lowered
            or "service:" in lowered
        ):
            return "system-design"
        if "design" in lowered or "cmp-" in lowered or "#state" in lowered:
            return "design"
        if "decision" in lowered or re.search(r"\bD-[A-Za-z0-9-]+\b", source):
            return "decisions"
        if "prd" in lowered or re.search(r"\bREQ-[A-Za-z0-9-]+\b", source):
            return "prd"
        if "plan" in lowered or re.search(r"\bSTEP-[A-Za-z0-9-]+\b", source):
            return "plan"
        return "unknown"

    @staticmethod
    def _context_item_text(
        context_package: ContextPackage | None,
        item_key: str,
    ) -> str:
        if context_package is None:
            return ""
        path = context_package.item_paths.get(item_key)
        if not path:
            return ""
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    @classmethod
    def _reference_from_context_package(
        cls,
        context_package: ContextPackage | None,
        source_family: str,
    ) -> TaskReference | None:
        family_candidates: dict[str, list[tuple[str, str]]] = {
            "plan": [
                ("plan", "Plan slice context"),
                ("broad-plan", "Broad plan context"),
                ("contract", "Plan slice contract"),
            ],
            "prd": [
                ("prd", "PRD slice context"),
                ("broad-prd", "Broad PRD context"),
            ],
            "design": [
                ("design", "Design slice context"),
                ("broad-design", "Broad design context"),
            ],
            "system-design": [
                ("system-design", "System Design slice context"),
            ],
            "test-plan": [
                ("test-plan", "Test Plan slice context"),
            ],
            "decisions": [
                ("subfeature-decisions", "Target decision context"),
                ("decision-pack", "Decision pack context"),
            ],
        }
        for item_key, source_label in family_candidates.get(source_family, []):
            text = cls._context_item_text(context_package, item_key)
            if text:
                return TaskReference(
                    source=source_label,
                    content=text,
                )
        return None

    @classmethod
    def _hydrate_slice_reference_material(
        cls,
        dag: ImplementationDAG,
        slice_info: TaskPlanningSlice,
        context_package: ContextPackage | None,
    ) -> ImplementationDAG:
        if context_package is None or not dag.tasks:
            return dag

        hydrated = dag.model_copy(deep=True)
        required_families = [
            family
            for family in slice_info.required_reference_sources
            if family and family != "unknown"
        ]
        available_refs = {
            family: cls._reference_from_context_package(context_package, family)
            for family in required_families
        }
        seen_families = {
            cls._reference_source_family(reference.source)
            for task in hydrated.tasks
            for reference in task.reference_material
        }
        missing_families = [
            family
            for family in required_families
            if family not in seen_families and available_refs.get(family) is not None
        ]

        anchor_task = next(
            (
                task
                for task in hydrated.tasks
                if not task.reference_material and (not slice_info.step_ids or set(task.step_ids) & set(slice_info.step_ids))
            ),
            None,
        )
        if anchor_task is None and hydrated.tasks:
            anchor_task = hydrated.tasks[0]
        if anchor_task is not None:
            existing_anchor_families = {
                cls._reference_source_family(reference.source)
                for reference in anchor_task.reference_material
            }
            for family in missing_families:
                if family in existing_anchor_families:
                    continue
                reference = available_refs.get(family)
                if reference is None:
                    continue
                anchor_task.reference_material.append(reference.model_copy(deep=True))
                existing_anchor_families.add(family)

        fallback_family_order = required_families + [
            family
            for family in ("plan", "prd", "design", "system-design", "test-plan", "decisions")
            if family not in required_families
        ]
        for task in hydrated.tasks:
            if task.reference_material:
                continue
            for family in fallback_family_order:
                reference = available_refs.get(family) or cls._reference_from_context_package(context_package, family)
                if reference is None:
                    continue
                task.reference_material.append(reference.model_copy(deep=True))
                break
        return hydrated

    @classmethod
    def _slice_contract_digest(
        cls,
        *,
        step_ids: list[str],
        requirement_ids: list[str],
        journey_ids: list[str],
        owned_acceptance_criterion_ids: list[str],
        supporting_acceptance_criterion_ids: list[str],
        global_obligation_ac_ids: list[str],
        required_reference_sources: list[str],
    ) -> str:
        return cls._json_digest(
            {
                "step_ids": sorted(step_ids),
                "requirement_ids": sorted(requirement_ids),
                "journey_ids": sorted(journey_ids),
                "owned_acceptance_criterion_ids": sorted(owned_acceptance_criterion_ids),
                "supporting_acceptance_criterion_ids": sorted(supporting_acceptance_criterion_ids),
                "global_obligation_ac_ids": sorted(global_obligation_ac_ids),
                "required_reference_sources": sorted(required_reference_sources),
            }
        )

    @classmethod
    def _legacy_slice_owned_acceptance_ids(
        cls,
        slice_info: TaskPlanningSlice,
    ) -> list[str]:
        if slice_info.owned_acceptance_criterion_ids:
            return sorted(set(slice_info.owned_acceptance_criterion_ids))
        if slice_info.strict_acceptance_criteria:
            return sorted(set(slice_info.acceptance_criterion_ids))
        return []

    @classmethod
    def _slice_reopen_digest(
        cls,
        *,
        step_ids: list[str],
        owned_acceptance_criterion_ids: list[str],
        supporting_acceptance_criterion_ids: list[str],
    ) -> str:
        return cls._json_digest(
            {
                "step_ids": sorted(step_ids),
                "owned_acceptance_criterion_ids": sorted(owned_acceptance_criterion_ids),
                "supporting_acceptance_criterion_ids": sorted(supporting_acceptance_criterion_ids),
            }
        )

    @classmethod
    def _legacy_slice_reopen_digest(
        cls,
        slice_info: TaskPlanningSlice,
    ) -> str:
        return cls._slice_reopen_digest(
            step_ids=slice_info.step_ids,
            owned_acceptance_criterion_ids=cls._legacy_slice_owned_acceptance_ids(slice_info),
            supporting_acceptance_criterion_ids=cls._slice_supporting_acceptance_ids(slice_info),
        )

    @classmethod
    async def _delete_artifact_key(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        key: str,
    ) -> None:
        delete = getattr(runner.artifacts, "delete", None)
        if callable(delete):
            await delete(key, feature=feature)
        mirror = runner.services.get("artifact_mirror")
        if mirror is not None:
            mirror.delete_artifact(feature.id, key)

    @classmethod
    async def _clear_slice_manifest_artifacts(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        manifest: TaskPlanningSliceManifest,
    ) -> None:
        fragment_keys = {
            cls._slice_fragment_key(manifest.slug, slice_info.slice_id)
            for slice_info in manifest.slices
        }
        fragment_keys.update(
            status.fragment_key
            for status in manifest.statuses
            if status.fragment_key
        )
        for fragment_key in sorted(fragment_keys):
            await cls._delete_artifact_key(runner, feature, fragment_key)
        for attempt in manifest.attempts:
            if attempt.attempt_key:
                await cls._delete_artifact_key(runner, feature, attempt.attempt_key)

    @classmethod
    async def _clear_slice_attempt_artifacts(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        manifest: TaskPlanningSliceManifest,
        slice_ids: set[str],
    ) -> bool:
        if not slice_ids:
            return False

        changed = False
        retained_attempts: list[SlicePlanningAttempt] = []
        for attempt in manifest.attempts:
            if attempt.slice_id not in slice_ids:
                retained_attempts.append(attempt)
                continue
            changed = True
            if attempt.attempt_key:
                await cls._delete_artifact_key(runner, feature, attempt.attempt_key)
        manifest.attempts = retained_attempts
        return changed

    @classmethod
    def _ensure_slice_status(
        cls,
        manifest: TaskPlanningSliceManifest,
        slice_id: str,
    ) -> SlicePlanningStatus:
        status_map = cls._slice_status_map(manifest)
        status = status_map.get(slice_id)
        if status is None:
            status = SlicePlanningStatus(slice_id=slice_id)
            manifest.statuses.append(status)
        return status

    @classmethod
    async def _normalize_pending_slice_manifest(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        manifest: TaskPlanningSliceManifest,
        *,
        contract: SubfeaturePlanningContract | None = None,
    ) -> None:
        backfill_status = await cls._load_backfill_status(runner, feature)
        migrated = cls._slug_is_migrated(backfill_status, manifest.slug)
        contract = contract or await cls._load_subfeature_planning_contract(
            runner,
            feature,
            manifest.slug,
        )
        if contract is None:
            contract = await cls._compile_subfeature_planning_contract(
                runner,
                feature,
                manifest.slug,
            )
        if migrated:
            plan_sidecar = await load_structured_artifact(runner, feature, f"plan:{manifest.slug}")
            planning_index = await cls._load_subfeature_planning_index(runner, feature, manifest.slug)
            if plan_sidecar is None or planning_index is None:
                raise RuntimeError(f"migrated subfeature {manifest.slug} is missing plan sidecar or planning index")
            atomic_slices = cls._derive_atomic_slices_from_planning_index(plan_sidecar, planning_index)
        else:
            plan_text = await runner.artifacts.get(f"plan:{manifest.slug}", feature=feature) or ""
            test_plan_text = await runner.artifacts.get(f"test-plan:{manifest.slug}", feature=feature) or ""
            normalized_plan = cls._normalize_plan_markdown_for_slice_derivation(
                cls._normalize_artifact_markdown(plan_text, f"plan:{manifest.slug}")
            )
            test_plan = cls._parse_test_plan(test_plan_text)
            fallback_ac_ids = sorted(_extract_ac_ids(test_plan_text))
            atomic_slices = cls._derive_atomic_slices_from_markdown_plan(
                normalized_plan,
                test_plan,
                fallback_ac_ids=fallback_ac_ids,
                contract=contract,
            )
        atomic_by_step_id = {
            atomic_slice.step_ids[0]: atomic_slice
            for atomic_slice in atomic_slices
            if len(atomic_slice.step_ids) == 1
        }

        forced_step_ids: dict[str, list[str]] = {}
        if manifest.slug == _BFS_SLUG:
            manifest_step_ids = {
                step_id
                for slice_info in manifest.slices
                for step_id in slice_info.step_ids
            }
            if "STEP-17" in atomic_by_step_id and "STEP-17" not in manifest_step_ids:
                forced_step_ids["slice-4"] = list(_BFS_SLICE4_EXPANDED_STEPS)

        # Operator-pinned ADDITIVE slice-contract augments (generic successor
        # of the _BFS_SLUG forced-steps hardcode above): widen named slices
        # with steps/requirements the deterministic partition cannot derive
        # from the plan markdown (phantom mandated steps, orphan PRD REQs).
        # Applied AFTER the atomic rebuild below so the rebuild never wipes
        # them; idempotent (pure set-union) across normalize passes.
        augments = await _load_slice_contract_augments(runner, feature, manifest.slug)
        augments = cls._resolve_step_keyed_augments(augments, manifest)
        unknown_augment_slice_ids = sorted(
            set(augments) - {slice_info.slice_id for slice_info in manifest.slices}
        )
        if unknown_augment_slice_ids:
            raise RuntimeError(
                f"operator slice-contract augments for {manifest.slug} target "
                f"unknown slice id(s) {unknown_augment_slice_ids}; manifest has "
                f"{sorted(slice_info.slice_id for slice_info in manifest.slices)}. "
                "Fix the dag-slice-augments store key before re-running."
            )

        manifest_changed = False
        semantic_changes = False
        normalized_slice_ids: set[str] = set()
        if contract is not None and manifest.contract_digest != contract.contract_digest:
            manifest.contract_digest = contract.contract_digest
            manifest_changed = True
        for idx, slice_info in enumerate(manifest.slices):
            status = cls._ensure_slice_status(manifest, slice_info.slice_id)
            fragment_key = status.fragment_key or cls._slice_fragment_key(manifest.slug, slice_info.slice_id)
            status.fragment_key = fragment_key
            fragment_text = await runner.artifacts.get(fragment_key, feature=feature)

            source_slice = slice_info
            if slice_info.slice_id in forced_step_ids:
                desired_step_ids = forced_step_ids[slice_info.slice_id]
                if slice_info.step_ids != desired_step_ids:
                    source_slice = slice_info.model_copy(update={"step_ids": desired_step_ids})
                else:
                    source_slice = slice_info.model_copy(deep=True)

            augment = augments.get(slice_info.slice_id)
            if augment is not None and augment.add_step_ids:
                missing_step_ids = [
                    step_id
                    for step_id in augment.add_step_ids
                    if step_id not in source_slice.step_ids
                ]
                if missing_step_ids:
                    # Step additions go through source_slice so the existing
                    # reopen logic (step_ids changed -> fragment invalidated)
                    # treats them exactly like the forced-step path.
                    source_slice = source_slice.model_copy(
                        update={"step_ids": source_slice.step_ids + missing_step_ids}
                    )

            rebuilt_slice = cls._merge_atomic_slices_for_existing_slice(source_slice, atomic_by_step_id)
            if augment is not None:
                rebuilt_slice = cls._apply_slice_contract_augment(rebuilt_slice, augment)
                if rebuilt_slice.model_dump() != slice_info.model_dump():
                    logger.warning(
                        "operator slice-contract augment applied to %s/%s: "
                        "+steps %s, +requirements %s, +journeys %s",
                        manifest.slug,
                        slice_info.slice_id,
                        augment.add_step_ids or "[]",
                        augment.add_requirement_ids or "[]",
                        augment.add_journey_ids or "[]",
                    )
            slice_changed = rebuilt_slice.model_dump() != slice_info.model_dump()
            ownership_changed = (
                set(cls._legacy_slice_owned_acceptance_ids(source_slice))
                != set(rebuilt_slice.owned_acceptance_criterion_ids)
                or set(cls._slice_supporting_acceptance_ids(source_slice))
                != set(rebuilt_slice.supporting_acceptance_criterion_ids)
            )
            augment_widens_traceability = augment is not None and (
                not set(augment.add_requirement_ids).issubset(set(slice_info.requirement_ids))
                or not set(augment.add_journey_ids).issubset(set(slice_info.journey_ids))
            )
            reopen_required = (
                slice_info.step_ids != source_slice.step_ids
                # A requirement/journey augment widens the slice's coverage
                # obligation — an already-planned fragment cannot satisfy it.
                or augment_widens_traceability
                or (
                    ownership_changed
                    and bool(
                        rebuilt_slice.owned_acceptance_criterion_ids
                        or rebuilt_slice.supporting_acceptance_criterion_ids
                    )
                )
            )

            preserve_completed = (
                status.status == "completed"
                and bool(fragment_text)
                and not reopen_required
            )
            if preserve_completed:
                if slice_changed:
                    manifest.slices[idx] = rebuilt_slice
                    manifest_changed = True
                continue

            if reopen_required and fragment_text:
                await cls._delete_artifact_key(runner, feature, fragment_key)
                manifest_changed = True
                semantic_changes = True

            if slice_changed:
                manifest.slices[idx] = rebuilt_slice
                manifest_changed = True
                semantic_changes = True

            if (
                status.status != "pending"
                or status.retry_mode
                or status.context_paths
                or status.last_error
            ):
                manifest_changed = True
                semantic_changes = True
            status.status = "pending"
            status.retry_mode = ""
            status.context_paths = []
            status.last_error = ""
            status.fragment_key = fragment_key
            normalized_slice_ids.add(slice_info.slice_id)

        attempts_changed = await cls._clear_slice_attempt_artifacts(
            runner,
            feature,
            manifest,
            normalized_slice_ids,
        )
        manifest_changed = attempts_changed or manifest_changed
        if attempts_changed and normalized_slice_ids:
            semantic_changes = True

        if manifest_changed:
            if semantic_changes:
                manifest.complete = False
            await cls._save_slice_manifest(runner, feature, manifest)

    @staticmethod
    def _normalize_artifact_markdown(text: str, artifact_key: str) -> str:
        if not text:
            return ""
        try:
            payload = _json.loads(text)
        except Exception:
            return text
        try:
            if artifact_key.startswith("prd"):
                return to_markdown(PRD.model_validate(payload))
            if artifact_key.startswith("design"):
                return to_markdown(DesignDecisions.model_validate(payload))
            if artifact_key.startswith("plan"):
                return to_markdown(TechnicalPlan.model_validate(payload))
            if artifact_key.startswith("system-design"):
                return to_markdown(SystemDesign.model_validate(payload))
            if artifact_key.startswith("test-plan"):
                return to_markdown(TestPlan.model_validate(payload))
        except Exception:
            logger.debug("Failed to normalize %s as structured markdown", artifact_key, exc_info=True)
        return text

    @staticmethod
    def _normalize_plan_markdown_for_slice_derivation(plan_markdown: str) -> str:
        if not plan_markdown:
            return ""
        return _EMBEDDED_STEP_HEADING_PATTERN.sub(r"\n\1", plan_markdown)

    @staticmethod
    def _markdown_sections(text: str) -> list[tuple[str, str]]:
        if not text.strip():
            return []
        matches = list(re.finditer(r"(?m)^#{1,6}\s+.+$", text))
        if not matches:
            return [("", text.strip())]
        sections: list[tuple[str, str]] = []
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            heading = match.group(0).strip()
            section_text = text[start:end].strip()
            sections.append((heading, section_text))
        return sections

    @classmethod
    def _extract_matching_sections(
        cls,
        text: str,
        tokens: set[str],
        *,
        fallback_headings: tuple[str, ...] = (),
        max_chars: int = 14_000,
    ) -> str:
        markdown = text.strip()
        if not markdown:
            return ""
        lowered_tokens = {token.lower() for token in tokens if token}
        sections = cls._markdown_sections(markdown)
        selected: list[str] = []
        for heading, section_text in sections:
            haystack = section_text.lower()
            if any(token in haystack for token in lowered_tokens):
                selected.append(section_text)
                continue
            if fallback_headings and any(heading.startswith(prefix) for prefix in fallback_headings):
                selected.append(section_text)
        if not selected:
            selected = [section_text for _heading, section_text in sections[:2]]
        excerpt = "\n\n".join(selected).strip()
        if len(excerpt) <= max_chars:
            return excerpt
        return excerpt[:max_chars].rstrip() + "\n\n...[truncated]\n"

    @classmethod
    def _extract_exact_step_sections(
        cls,
        plan_text: str,
        step_ids: list[str],
        *,
        max_chars: int = 14_000,
    ) -> str:
        markdown = cls._normalize_plan_markdown_for_slice_derivation(plan_text).strip()
        if not markdown or not step_ids:
            return ""

        matches = list(_STEP_MARKDOWN_HEADING_PATTERN.finditer(markdown))
        if not matches:
            return ""

        sections_by_step: dict[str, str] = {}
        for idx, match in enumerate(matches):
            step_id = match.group(1).strip()
            section_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
            section_text = markdown[match.start():section_end].strip()
            if section_text and step_id not in sections_by_step:
                sections_by_step[step_id] = section_text

        selected = [sections_by_step[step_id] for step_id in step_ids if step_id in sections_by_step]
        if not selected:
            return ""

        excerpt = "\n\n".join(selected).strip()
        if len(excerpt) <= max_chars:
            return excerpt
        return excerpt[:max_chars].rstrip() + "\n\n...[truncated]\n"

    @staticmethod
    def _extract_trace_tokens(*texts: str) -> set[str]:
        tokens: set[str] = set()
        for text in texts:
            for match in _TRACE_TOKEN_PATTERN.finditer(text):
                token = match.group(1) or match.group(2) or ""
                token = token.strip()
                if len(token) >= 3:
                    tokens.add(token)
        return tokens

    @classmethod
    def _parse_technical_plan(
        cls,
        plan_text: str,
    ) -> TechnicalPlan | None:
        if not plan_text:
            return None
        try:
            return TechnicalPlan.model_validate(_json.loads(plan_text))
        except Exception:
            return None

    @classmethod
    def _parse_test_plan(
        cls,
        test_plan_text: str,
    ) -> TestPlan | None:
        if not test_plan_text:
            return None
        try:
            payload = _json.loads(test_plan_text)
            if isinstance(payload, dict) and isinstance(payload.get("content"), dict):
                payload = payload["content"]
            return TestPlan.model_validate(payload)
        except Exception:
            pass

        markdown = cls._normalize_artifact_markdown(test_plan_text, "test-plan")
        acceptance_section = cls._markdown_h2_body(markdown, "Acceptance Criteria")
        if not acceptance_section:
            return None

        acceptance_criteria = cls._parse_markdown_acceptance_criteria(acceptance_section)
        if not acceptance_criteria:
            return None

        return TestPlan(
            overview=cls._markdown_h2_body(markdown, "Overview"),
            acceptance_criteria=acceptance_criteria,
            test_scenarios=cls._parse_markdown_test_scenarios(
                cls._markdown_h2_body(markdown, "Test Scenarios")
            ),
            verification_checklist=cls._parse_markdown_bullets(
                cls._markdown_h2_body(markdown, "Verification Checklist")
            ),
            edge_cases=cls._parse_markdown_bullets(
                cls._markdown_h2_body(markdown, "Edge Cases")
            ),
            mocking_strategy=cls._markdown_h2_body(markdown, "Mocking Strategy"),
            test_environment=cls._parse_markdown_bullets(
                cls._markdown_h2_body(markdown, "Test Environment")
            ),
            decisions=cls._parse_markdown_bullets(
                cls._markdown_h2_body(markdown, "Decisions")
            ),
            complete=True,
        )

    @staticmethod
    def _markdown_h2_body(markdown: str, heading_title: str) -> str:
        if not markdown.strip():
            return ""
        heading_pattern = re.compile(
            rf"(?m)^##\s+{re.escape(heading_title)}\b.*$"
        )
        match = heading_pattern.search(markdown)
        if match is None:
            return ""
        body_start = match.end()
        next_heading = _NEXT_H2_HEADING.search(markdown, body_start)
        body_end = next_heading.start() if next_heading else len(markdown)
        return markdown[body_start:body_end].strip()

    @staticmethod
    def _strip_markdown_ticks(value: str) -> str:
        stripped = value.strip()
        if stripped.startswith("`") and stripped.endswith("`") and len(stripped) >= 2:
            return stripped[1:-1].strip()
        return stripped

    @classmethod
    def _parse_markdown_metadata_map(cls, block_text: str) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for key, raw_value in _MARKDOWN_METADATA_LINE_PATTERN.findall(block_text):
            metadata[key.strip().lower()] = cls._strip_markdown_ticks(raw_value)
        return metadata

    @classmethod
    def _parse_markdown_acceptance_criteria(
        cls,
        section_text: str,
    ) -> list[TestAcceptanceCriterion]:
        # Test plans in the wild define criteria in three shapes: list items
        # (`- **AC-x** — …`), bold paragraphs (`**AC-x — Title** · refs`),
        # and headings (`### AC-x — title`). Use whichever shape yields the
        # most definitions; ties prefer the list-item form.
        match_sets = [
            list(pattern.finditer(section_text))
            for pattern in (
                _MARKDOWN_AC_BLOCK_PATTERN,
                _MARKDOWN_AC_BOLD_PARAGRAPH_BLOCK_PATTERN,
                _MARKDOWN_AC_HEADING_BLOCK_PATTERN,
            )
        ]
        matches = max(match_sets, key=len)
        if not matches:
            return []
        criteria: list[TestAcceptanceCriterion] = []
        for idx, match in enumerate(matches):
            block_start = match.start()
            block_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(section_text)
            metadata = cls._parse_markdown_metadata_map(section_text[block_start:block_end])
            description = (match.group(2) or "").strip()
            if metadata.get("description"):
                description = "\n".join(
                    part for part in (description, metadata["description"]) if part
                )
            criteria.append(
                TestAcceptanceCriterion(
                    id=match.group(1).strip(),
                    description=description,
                    linked_requirement=metadata.get("linked_requirement", ""),
                    verification_method=metadata.get("verification_method", ""),
                    pass_condition=metadata.get("pass_condition", ""),
                    linked_verifiable_state_id=metadata.get("linked_verifiable_state_id", ""),
                    linked_journey_step_id=metadata.get("linked_journey_step_id", ""),
                )
            )
        return criteria

    @classmethod
    def _expand_shorthand_id_list(cls, raw_value: str) -> list[str]:
        values: list[str] = []
        last_full_id = ""
        for raw_part in raw_value.split(","):
            token = cls._strip_markdown_ticks(raw_part).strip()
            if not token:
                continue
            if token.startswith("-") and last_full_id:
                prefix_match = re.match(r"^(.*-)[A-Za-z0-9]+$", last_full_id)
                if prefix_match is not None:
                    token = prefix_match.group(1) + token[1:].strip()
            values.append(token)
            if "-" in token:
                last_full_id = token
        return values

    @classmethod
    def _parse_markdown_test_scenarios(
        cls,
        section_text: str,
    ) -> list[TestScenario]:
        matches = list(_MARKDOWN_SCENARIO_HEADING_PATTERN.finditer(section_text))
        if not matches:
            return []
        scenarios: list[TestScenario] = []
        for idx, match in enumerate(matches):
            block_start = match.start()
            block_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(section_text)
            block_text = section_text[block_start:block_end]
            metadata = cls._parse_markdown_metadata_map(block_text)
            linked_acceptance = cls._expand_shorthand_id_list(
                metadata.get("linked_acceptance", "")
            )
            scenarios.append(
                TestScenario(
                    name=match.group(1).strip(),
                    priority=(metadata.get("priority", "") or "p1").strip() or "p1",
                    linked_acceptance=linked_acceptance,
                    preconditions=(
                        [cls._strip_markdown_ticks(metadata["preconditions"])]
                        if metadata.get("preconditions")
                        else []
                    ),
                    steps=(
                        [cls._strip_markdown_ticks(metadata["steps"])]
                        if metadata.get("steps")
                        else []
                    ),
                    expected_outcome=metadata.get("expected_outcome", ""),
                )
            )
        return scenarios

    @classmethod
    def _parse_markdown_bullets(cls, section_text: str) -> list[str]:
        if not section_text.strip():
            return []
        items: list[str] = []
        for raw_line in section_text.splitlines():
            line = raw_line.strip()
            if not line.startswith("- "):
                continue
            item = line[2:].strip()
            item = re.sub(r"^\[[ xX]\]\s*", "", item)
            item = cls._strip_markdown_ticks(item)
            if item:
                items.append(item)
        return items

    @classmethod
    def _step_section_metadata(cls, section_text: str) -> dict[str, str]:
        metadata: dict[str, list[str]] = {}
        for raw_label, raw_value in _STEP_SECTION_METADATA_PATTERN.findall(section_text):
            label = raw_label.strip().lower().rstrip(".:").strip()
            value = raw_value.strip()
            if not label or not value:
                continue
            metadata.setdefault(label, []).append(value)
        return {
            key: "\n".join(values).strip()
            for key, values in metadata.items()
            if values
        }

    @classmethod
    def _explicit_ac_ids_from_step_section(
        cls,
        section_text: str,
    ) -> list[str]:
        metadata = cls._step_section_metadata(section_text)
        explicit_ac_ids: set[str] = set()
        raw_ac_refs = metadata.get("ac refs", "")
        if raw_ac_refs:
            explicit_ac_ids.update(cls._expand_shorthand_id_list(raw_ac_refs))
            explicit_ac_ids.update(_AC_ID_PATTERN.findall(raw_ac_refs))
        raw_acceptance = metadata.get("acceptance", "")
        if raw_acceptance:
            explicit_ac_ids.update(_AC_ID_PATTERN.findall(raw_acceptance))
        return sorted(explicit_ac_ids)

    @classmethod
    def _requirement_refs_from_step_section(
        cls,
        section_text: str,
    ) -> list[str]:
        metadata = cls._step_section_metadata(section_text)
        explicit_refs = cls._expand_shorthand_id_list(metadata.get("requirement refs", ""))
        refs = set(explicit_refs) | set(_REQ_ID_PATTERN.findall(section_text))
        bare_family_tokens = _bare_requirement_family_tokens(refs, refs)
        if bare_family_tokens:
            logger.debug(
                "Ignoring bare requirement-family tokens (family prefixes of fuller ids): %s",
                ", ".join(sorted(bare_family_tokens)),
            )
        return sorted(refs - bare_family_tokens)

    @classmethod
    def _journey_refs_from_step_section(
        cls,
        section_text: str,
    ) -> list[str]:
        metadata = cls._step_section_metadata(section_text)
        explicit_refs = cls._expand_shorthand_id_list(metadata.get("journey refs", ""))
        return sorted(set(explicit_refs) | set(_JOURNEY_ID_PATTERN.findall(section_text)))

    @classmethod
    def _step_sections_from_plan(
        cls,
        plan_text: str,
    ) -> list[tuple[str, str, str]]:
        markdown = cls._normalize_plan_markdown_for_slice_derivation(plan_text).strip()
        if not markdown:
            return []
        matches = list(_STEP_HEADING_PATTERN.finditer(markdown))
        sections: list[tuple[str, str, str]] = []
        for idx, match in enumerate(matches):
            step_id = cls._normalize_step_id(match.group(1))
            title = match.group(2).strip() or step_id
            section_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
            section_text = markdown[match.start():section_end].strip()
            sections.append((step_id, title, section_text))
        return sections

    @classmethod
    def _requirement_universe_from_texts(
        cls,
        prd_text: str,
        plan_text: str,
        test_plan_text: str,
    ) -> list[str]:
        requirement_ids: set[str] = set()
        if prd_text:
            try:
                prd = PRD.model_validate(_json.loads(prd_text))
                requirement_ids.update(
                    requirement.id for requirement in prd.structured_requirements if requirement.id
                )
            except Exception:
                pass
        requirement_ids.update(_REQ_ID_PATTERN.findall(prd_text))
        if not requirement_ids:
            requirement_ids.update(_REQ_ID_PATTERN.findall(plan_text))
            requirement_ids.update(_REQ_ID_PATTERN.findall(test_plan_text))
        return sorted(requirement_ids)

    @classmethod
    def _journey_universe_from_texts(
        cls,
        prd_text: str,
        plan_text: str,
        test_plan_text: str,
    ) -> list[str]:
        journey_ids: set[str] = set()
        if prd_text:
            try:
                prd = PRD.model_validate(_json.loads(prd_text))
                journey_ids.update(journey.id for journey in prd.journeys if journey.id)
            except Exception:
                pass
        journey_ids.update(_JOURNEY_ID_PATTERN.findall(prd_text))
        journey_ids.update(_JOURNEY_ID_PATTERN.findall(plan_text))
        journey_ids.update(_JOURNEY_ID_PATTERN.findall(test_plan_text))
        return sorted(journey_ids)

    @classmethod
    def _verifiable_state_universe_from_texts(
        cls,
        design_text: str,
        system_design_text: str,
    ) -> list[str]:
        verifiable_state_ids: set[str] = set()
        if design_text:
            try:
                design = DesignDecisions.model_validate(_json.loads(design_text))
                verifiable_state_ids.update(
                    f"{state.component_id}#{state.state_name}"
                    for state in design.verifiable_states
                    if state.component_id and state.state_name
                )
            except Exception:
                pass
        verifiable_state_ids.update(_VERIFIABLE_STATE_ID_PATTERN.findall(design_text))
        verifiable_state_ids.update(_VERIFIABLE_STATE_ID_PATTERN.findall(system_design_text))
        return sorted(verifiable_state_ids)

    @classmethod
    def _decision_universe_from_texts(
        cls,
        *decision_texts: str,
    ) -> list[str]:
        decision_ids: set[str] = set()
        for text in decision_texts:
            decision_ids.update(_extract_decision_ids(text))
        return sorted(decision_ids)

    @classmethod
    def _required_reference_sources_for_step_contract(
        cls,
        contract: SubfeaturePlanningContract,
        step_contract: StepPlanningContract,
        *,
        target_bundle: dict[str, str] | None = None,
        decision_context_present: bool = False,
    ) -> list[str]:
        required_sources = {"plan"}
        if target_bundle is not None:
            if contract.has_prd_artifact and target_bundle.get("prd", "").strip():
                required_sources.add("prd")
            if contract.has_design_artifact and target_bundle.get("design", "").strip():
                required_sources.add("design")
            if contract.has_system_design_artifact and target_bundle.get("system-design", "").strip():
                required_sources.add("system-design")
            if contract.has_test_plan_artifact and target_bundle.get("test-plan", "").strip():
                required_sources.add("test-plan")
            if decision_context_present:
                required_sources.add("decisions")
            return sorted(required_sources)

        if contract.has_prd_artifact and step_contract.requirement_ids:
            required_sources.add("prd")
        relevant_global_obligations = [
            ac_id
            for ac_id, candidate_step_ids in contract.global_obligation_candidate_step_ids.items()
            if step_contract.step_id in candidate_step_ids
        ]
        if contract.has_test_plan_artifact and (
            step_contract.owned_ac_ids
            or step_contract.supporting_ac_ids
            or relevant_global_obligations
        ):
            required_sources.add("test-plan")
        if contract.has_design_artifact and step_contract.verifiable_state_ids:
            required_sources.add("design")
        if contract.has_system_design_artifact and (
            step_contract.decision_ids
            or step_contract.verifiable_state_ids
        ):
            required_sources.add("system-design")
        if contract.decision_universe and step_contract.decision_ids:
            required_sources.add("decisions")
        return sorted(required_sources)

    @classmethod
    def _criterion_step_candidates(
        cls,
        criterion: TestAcceptanceCriterion,
        step_contracts: dict[str, StepPlanningContract],
        step_sections: dict[str, str],
    ) -> list[str]:
        criterion_trace_ids = cls._criterion_trace_ids(criterion)
        candidates: list[str] = []
        for step_id, step_contract in step_contracts.items():
            step_trace_ids = {
                "requirements": set(step_contract.requirement_ids),
                "journeys": set(step_contract.journey_ids),
                "steps": {step_id},
                "decisions": set(step_contract.decision_ids),
                "nfrs": set(step_contract.nfr_ids),
            }
            if any(
                cls._trace_sets_intersect(
                    criterion_trace_ids[token_family],
                    step_trace_ids[token_family],
                )
                for token_family in ("requirements", "journeys", "steps")
            ):
                candidates.append(step_id)
                continue
            owned, _supporting = cls._classify_criterion_against_slice_trace(
                criterion,
                cls._slice_trace_ids(step_id=step_id, section_text=step_sections.get(step_id, "")),
            )
            if owned:
                candidates.append(step_id)
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _select_criterion_owner_step(
        candidate_step_ids: list[str],
        step_order: list[str],
    ) -> str:
        if not candidate_step_ids:
            return ""
        order = {step_id: index for index, step_id in enumerate(step_order)}
        return min(candidate_step_ids, key=lambda step_id: order.get(step_id, len(order)))

    @classmethod
    async def _compile_subfeature_planning_contract(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
        sf_upstream: dict[str, dict[str, str]] | None = None,
    ) -> SubfeaturePlanningContract:
        backfill_status = await cls._load_backfill_status(runner, feature)
        degenerate_sidecar = False
        if cls._slug_is_migrated(backfill_status, slug):
            # Guard against a degenerate migrated sidecar: if the structured
            # test-plan sidecar yields ZERO acceptance criteria while the
            # markdown twin defines some, the sidecar would silently destroy
            # coverage checking (empty canonical universe validates clean).
            # Fall back to the markdown compile path for this subfeature.
            test_plan_sidecar = await load_structured_artifact(runner, feature, f"test-plan:{slug}")
            sidecar_ac_count = (
                sum(1 for criterion in test_plan_sidecar.content.acceptance_criteria if criterion.id)
                if test_plan_sidecar is not None
                else 0
            )
            if sidecar_ac_count == 0:
                markdown_twin_text = await runner.artifacts.get(f"test-plan:{slug}", feature=feature) or ""
                markdown_ac_ids = _extract_ac_ids(markdown_twin_text)
                if markdown_ac_ids:
                    degenerate_sidecar = True
                    logger.warning(
                        "DEGENERATE migrated test-plan sidecar for %s: sidecar yields 0 "
                        "acceptance criteria while the markdown twin defines %d — "
                        "falling back to the markdown compile path over the raw "
                        "markdown artifacts (coverage checking would otherwise be "
                        "silently destroyed)",
                        slug,
                        len(markdown_ac_ids),
                    )
            if not degenerate_sidecar:
                return await cls._compile_subfeature_planning_contract_from_sidecars(
                    runner,
                    feature,
                    slug,
                )
        if degenerate_sidecar:
            # The sidecar-aware loader would render the degenerate sidecars
            # back at us; compile from the raw markdown twins instead.
            target_texts = {
                prefix: await runner.artifacts.get(f"{prefix}:{slug}", feature=feature) or ""
                for prefix in ("plan", "prd", "design", "system-design", "test-plan", "decisions")
            }
        else:
            target_texts = await cls._load_target_texts(runner, feature, slug, sf_upstream or {})
        normalized_plan = cls._normalize_artifact_markdown(target_texts.get("plan", ""), f"plan:{slug}")
        normalized_plan = cls._normalize_plan_markdown_for_slice_derivation(normalized_plan)
        normalized_test_plan = cls._normalize_artifact_markdown(target_texts.get("test-plan", ""), "test-plan")
        plan_digest = cls._content_digest(normalized_plan)
        test_plan_digest = cls._content_digest(normalized_test_plan)
        test_plan = cls._parse_test_plan(target_texts.get("test-plan", ""))

        using_structured_acceptance = bool(test_plan and test_plan.acceptance_criteria)
        if using_structured_acceptance:
            canonical_ac_ids = sorted(
                criterion.id
                for criterion in test_plan.acceptance_criteria
                if criterion.id
            )
            criterion_map = {
                criterion.id: criterion
                for criterion in test_plan.acceptance_criteria
                if criterion.id
            }
        else:
            canonical_ac_ids = sorted(_extract_ac_ids(target_texts.get("test-plan", "")))
            criterion_map = {
                ac_id: TestAcceptanceCriterion(id=ac_id, description="")
                for ac_id in canonical_ac_ids
            }

        prior_contract = await cls._load_subfeature_planning_contract(runner, feature, slug)
        store_waived_ac_ids = await _load_planning_waivers(runner, feature, slug)
        effective_ac_ids, waived_map = _effective_coverage_ids_for_task_planning(
            slug,
            set(canonical_ac_ids),
            prior_contract.waived_ac_ids if prior_contract is not None else None,
            store_waived_ac_ids=store_waived_ac_ids,
        )
        normalized_prd = cls._normalize_artifact_markdown(target_texts.get("prd", ""), f"prd:{slug}")
        normalized_design = cls._normalize_artifact_markdown(target_texts.get("design", ""), f"design:{slug}")
        normalized_system_design = cls._normalize_artifact_markdown(
            target_texts.get("system-design", ""),
            f"system-design:{slug}",
        )

        step_sections_list = cls._step_sections_from_plan(normalized_plan)
        if not step_sections_list:
            synthetic_section = normalized_plan.strip() or "Whole subfeature"
            step_sections_list = [("STEP-1", "Whole subfeature", synthetic_section)]
        structured_plan = cls._parse_technical_plan(target_texts.get("plan", ""))
        structured_steps = {
            cls._normalize_step_id(step.id): step
            for step in (structured_plan.steps if structured_plan else [])
            if step.id
        }

        step_contracts: dict[str, StepPlanningContract] = {}
        step_sections: dict[str, str] = {}
        explicit_owner_map: dict[str, set[str]] = {}
        for step_id, title, section_text in step_sections_list:
            structured_step = structured_steps.get(step_id)
            requirement_ids = sorted(
                set(cls._requirement_refs_from_step_section(section_text))
                | set(structured_step.requirement_ids if structured_step else [])
            )
            journey_ids = sorted(
                set(cls._journey_refs_from_step_section(section_text))
                | set(structured_step.journey_ids if structured_step else [])
            )
            decision_ids = sorted(set(_extract_decision_ids(section_text)))
            nfr_ids = sorted(set(_NFR_ID_PATTERN.findall(section_text)))
            verifiable_state_ids = sorted(set(_VERIFIABLE_STATE_ID_PATTERN.findall(section_text)))
            explicit_owned_ac_ids = sorted(
                set(cls._explicit_ac_ids_from_step_section(section_text))
                | set(_BFS_STEP_RECONCILED_OWNED_ACS.get(step_id, ()) if slug == _BFS_SLUG else ())
            )
            for ac_id in explicit_owned_ac_ids:
                explicit_owner_map.setdefault(ac_id, set()).add(step_id)
            existing_contract = step_contracts.get(step_id)
            if existing_contract is not None:
                # Addendum-style repeated STEP heading (e.g. "### STEP-2 …
                # Addendum"): merge additively into the parent step contract —
                # union all traces and AC refs, append the section text. Never
                # replace the parent contract or enter the step id twice.
                merged_section = step_sections[step_id] + "\n\n" + section_text
                step_sections[step_id] = merged_section
                existing_contract.section_digest = cls._content_digest(merged_section)
                existing_contract.requirement_ids = sorted(
                    set(existing_contract.requirement_ids) | set(requirement_ids)
                )
                existing_contract.journey_ids = sorted(
                    set(existing_contract.journey_ids) | set(journey_ids)
                )
                existing_contract.decision_ids = sorted(
                    set(existing_contract.decision_ids) | set(decision_ids)
                )
                existing_contract.nfr_ids = sorted(set(existing_contract.nfr_ids) | set(nfr_ids))
                existing_contract.verifiable_state_ids = sorted(
                    set(existing_contract.verifiable_state_ids) | set(verifiable_state_ids)
                )
                existing_contract.explicit_owned_ac_ids = sorted(
                    set(existing_contract.explicit_owned_ac_ids) | set(explicit_owned_ac_ids)
                )
                existing_contract.owned_ac_ids = sorted(
                    set(existing_contract.owned_ac_ids) | set(explicit_owned_ac_ids)
                )
                logger.info(
                    "Merged repeated step heading for %s (%s) into the existing step contract "
                    "(addendum section; traces unioned, text appended)",
                    step_id,
                    slug,
                )
                continue
            step_sections[step_id] = section_text
            step_contracts[step_id] = StepPlanningContract(
                step_id=step_id,
                title=title,
                section_digest=cls._content_digest(section_text),
                requirement_ids=requirement_ids,
                journey_ids=journey_ids,
                decision_ids=decision_ids,
                nfr_ids=nfr_ids,
                verifiable_state_ids=verifiable_state_ids,
                explicit_owned_ac_ids=explicit_owned_ac_ids,
                owned_ac_ids=explicit_owned_ac_ids[:],
            )

        global_obligation_ac_ids: set[str] = set()
        global_obligation_candidate_step_ids: dict[str, list[str]] = {}
        unresolved_ac_ids: list[str] = []

        for ac_id in sorted(effective_ac_ids):
            criterion = criterion_map.get(ac_id)
            if criterion is None:
                unresolved_ac_ids.append(ac_id)
                continue
            explicit_owners = sorted(explicit_owner_map.get(ac_id, set()))
            if explicit_owners:
                continue
            candidate_step_ids = cls._criterion_step_candidates(
                criterion,
                step_contracts,
                step_sections,
            )
            if candidate_step_ids:
                owner_step_id = cls._select_criterion_owner_step(candidate_step_ids, list(step_contracts))
                step_contract = step_contracts[owner_step_id]
                if ac_id not in step_contract.owned_ac_ids:
                    step_contract.inferred_owned_ac_ids.append(ac_id)
                    step_contract.owned_ac_ids.append(ac_id)
                context_step_ids = [step_id for step_id in candidate_step_ids if step_id != owner_step_id]
                if context_step_ids:
                    global_obligation_ac_ids.add(ac_id)
                    global_obligation_candidate_step_ids[ac_id] = context_step_ids
                continue
            criterion_trace_ids = cls._criterion_trace_ids(criterion)
            context_candidate_step_ids = sorted(
                step_id
                for step_id, step_contract in step_contracts.items()
                if cls._trace_sets_intersect(
                    criterion_trace_ids["decisions"],
                    set(step_contract.decision_ids),
                )
                or cls._trace_sets_intersect(
                    criterion_trace_ids["nfrs"],
                    set(step_contract.nfr_ids),
                )
            )
            if context_candidate_step_ids:
                owner_step_id = cls._select_criterion_owner_step(context_candidate_step_ids, list(step_contracts))
                step_contract = step_contracts[owner_step_id]
                if ac_id not in step_contract.owned_ac_ids:
                    step_contract.inferred_owned_ac_ids.append(ac_id)
                    step_contract.owned_ac_ids.append(ac_id)
                context_step_ids = [step_id for step_id in context_candidate_step_ids if step_id != owner_step_id]
                if context_step_ids:
                    global_obligation_ac_ids.add(ac_id)
                    global_obligation_candidate_step_ids[ac_id] = context_step_ids
                continue
            fallback_owner = cls._fallback_owner_step_for_unresolved_ac(
                ac_id,
                step_contracts=list(step_contracts),
                effective_ac_ids=effective_ac_ids,
            )
            if fallback_owner:
                step_contract = step_contracts[fallback_owner]
                if ac_id not in step_contract.owned_ac_ids:
                    step_contract.inferred_owned_ac_ids.append(ac_id)
                    step_contract.owned_ac_ids.append(ac_id)
                continue
            unresolved_ac_ids.append(ac_id)

        for ac_id, criterion in criterion_map.items():
            for step_id, step_contract in step_contracts.items():
                if ac_id in step_contract.owned_ac_ids or ac_id in global_obligation_ac_ids:
                    continue
                _owned, supporting = cls._classify_criterion_against_slice_trace(
                    criterion,
                    cls._slice_trace_ids(step_id=step_id, section_text=step_sections.get(step_id, "")),
                )
                if supporting and ac_id not in step_contract.supporting_ac_ids:
                    step_contract.supporting_ac_ids.append(ac_id)

        for step_contract in step_contracts.values():
            step_contract.inferred_owned_ac_ids = sorted(set(step_contract.inferred_owned_ac_ids))
            step_contract.owned_ac_ids = sorted(set(step_contract.owned_ac_ids))
            step_contract.supporting_ac_ids = sorted(
                set(step_contract.supporting_ac_ids) - set(step_contract.owned_ac_ids)
            )

        contract = SubfeaturePlanningContract(
            slug=slug,
            plan_digest=plan_digest,
            test_plan_digest=test_plan_digest,
            canonical_ac_ids=canonical_ac_ids,
            waived_ac_ids=sorted(waived_map),
            global_obligation_ac_ids=sorted(global_obligation_ac_ids),
            global_obligation_candidate_step_ids={
                ac_id: sorted(step_ids)
                for ac_id, step_ids in sorted(global_obligation_candidate_step_ids.items())
            },
            requirement_universe=cls._requirement_universe_from_texts(
                target_texts.get("prd", ""),
                normalized_plan,
                normalized_test_plan,
            ),
            journey_universe=cls._journey_universe_from_texts(
                target_texts.get("prd", ""),
                normalized_plan,
                normalized_test_plan,
            ),
            decision_universe=cls._decision_universe_from_texts(
                target_texts.get("decisions", ""),
                # The technical plan defines plan-local decision ids (e.g. an
                # in-plan architecture decision log) that step sections cite;
                # admit them like the requirement/journey universes already
                # admit plan-defined ids.
                normalized_plan,
                await runner.artifacts.get("decisions", feature=feature) or "",
                await runner.artifacts.get("decisions:broad", feature=feature) or "",
                await runner.artifacts.get(GLOBAL_DECISIONS_KEY, feature=feature) or "",
            ),
            verifiable_state_universe=cls._verifiable_state_universe_from_texts(
                target_texts.get("design", ""),
                target_texts.get("system-design", ""),
            ),
            has_prd_artifact=bool(normalized_prd.strip()),
            has_design_artifact=bool(normalized_design.strip()),
            has_system_design_artifact=bool(normalized_system_design.strip()),
            has_test_plan_artifact=bool(normalized_test_plan.strip()),
            # dict preserves first-appearance order and guarantees a single
            # entry per step id even when the plan repeats a STEP heading
            # (addendum sections merge into the parent contract above).
            step_contracts=list(step_contracts.values()),
        )

        for step_contract in contract.step_contracts:
            step_slice = TaskPlanningSlice(
                slice_id=f"contract-{step_contract.step_id.lower()}",
                title=step_contract.title,
                step_ids=[step_contract.step_id],
                requirement_ids=step_contract.requirement_ids,
                journey_ids=step_contract.journey_ids,
                acceptance_criterion_ids=step_contract.owned_ac_ids,
                owned_acceptance_criterion_ids=step_contract.owned_ac_ids,
                supporting_acceptance_criterion_ids=step_contract.supporting_ac_ids,
                strict_acceptance_criteria=bool(step_contract.owned_ac_ids),
                global_obligation_ac_ids=sorted(
                    ac_id
                    for ac_id, candidate_step_ids in contract.global_obligation_candidate_step_ids.items()
                    if step_contract.step_id in candidate_step_ids
                ),
            )
            target_bundle = cls._target_slice_bundle(
                slug,
                step_slice,
                target_texts,
            )
            decision_context_ids = set(step_contract.decision_ids) | _extract_decision_ids(
                "\n".join(
                    text
                    for text in target_bundle.values()
                    if text
                )
            )
            step_contract.required_reference_sources = cls._required_reference_sources_for_step_contract(
                contract,
                step_contract,
                target_bundle=target_bundle,
                decision_context_present=bool(
                    target_texts.get("decisions", "").strip() and decision_context_ids
                ),
            )

        validation_messages = cls._validate_subfeature_planning_contract(
            contract,
            unresolved_ac_ids=unresolved_ac_ids,
        )
        if validation_messages:
            report_key = await cls._save_subfeature_planning_contract_report(
                runner,
                feature,
                slug=slug,
                messages=validation_messages,
            )
            raise PlanningContractError(slug, validation_messages, report_key=report_key)

        contract_payload = contract.model_dump(mode="json")
        contract.contract_digest = cls._json_digest(contract_payload)
        await cls._save_subfeature_planning_contract(runner, feature, contract)
        await cls._clear_subfeature_planning_contract_report(runner, feature, slug)
        return contract

    @staticmethod
    def _fallback_owner_step_for_unresolved_ac(
        ac_id: str,
        *,
        step_contracts: list[str],
        effective_ac_ids: set[str],
    ) -> str:
        if len(step_contracts) == 1:
            return step_contracts[0]
        if len(effective_ac_ids) != len(step_contracts):
            return ""
        step_set = set(step_contracts)
        for candidate_ac_id in effective_ac_ids:
            match = re.search(r"(\d+)$", candidate_ac_id)
            if not match or f"STEP-{match.group(1)}" not in step_set:
                return ""
        match = re.search(r"(\d+)$", ac_id)
        return f"STEP-{match.group(1)}" if match else ""

    @classmethod
    def _validate_subfeature_planning_contract(
        cls,
        contract: SubfeaturePlanningContract,
        *,
        unresolved_ac_ids: list[str] | None = None,
    ) -> list[str]:
        messages: list[str] = []
        unresolved = sorted(set(unresolved_ac_ids or []))

        owned_counts: dict[str, int] = {ac_id: 0 for ac_id in contract.canonical_ac_ids}
        # Compare requirement ids with zero-padding drift normalized on BOTH
        # sides (REQ-POST-20260606-1 == REQ-POST-20260606-01) — comparison
        # time only, the stored universe is never rewritten.
        normalized_requirement_universe = {
            _normalize_id_numeric_segments(requirement_id)
            for requirement_id in contract.requirement_universe
        }
        for step_contract in contract.step_contracts:
            step_set = {step_contract.step_id}
            if not step_set:
                messages.append("step contract is missing step_id")
            bare_family_refs = _bare_requirement_family_tokens(
                step_contract.requirement_ids,
                set(step_contract.requirement_ids) | set(contract.requirement_universe),
            )
            if bare_family_refs:
                logger.debug(
                    "%s: ignoring bare requirement-family tokens during universe validation: %s",
                    step_contract.step_id,
                    ", ".join(sorted(bare_family_refs)),
                )
            for requirement_id in step_contract.requirement_ids:
                if requirement_id in bare_family_refs:
                    continue
                if (
                    requirement_id not in contract.requirement_universe
                    and _normalize_id_numeric_segments(requirement_id) not in normalized_requirement_universe
                    and requirement_id not in step_contract.nfr_ids
                ):
                    messages.append(
                        f"{step_contract.step_id} references unknown requirement_id {requirement_id}"
                    )
            for journey_id in step_contract.journey_ids:
                if journey_id not in contract.journey_universe:
                    messages.append(
                        f"{step_contract.step_id} references unknown journey_id {journey_id}"
                    )
            for decision_id in step_contract.decision_ids:
                if contract.decision_universe and decision_id not in contract.decision_universe:
                    messages.append(
                        f"{step_contract.step_id} references unknown decision_id {decision_id}"
                    )
            for state_id in step_contract.verifiable_state_ids:
                if contract.verifiable_state_universe and state_id not in contract.verifiable_state_universe:
                    messages.append(
                        f"{step_contract.step_id} references unknown verifiable_state_id {state_id}"
                    )
            for ac_id in step_contract.owned_ac_ids:
                owned_counts[ac_id] = owned_counts.get(ac_id, 0) + 1

        owned_step_ids_by_ac: dict[str, set[str]] = {ac_id: set() for ac_id in contract.canonical_ac_ids}
        for step_contract in contract.step_contracts:
            for ac_id in step_contract.owned_ac_ids:
                owned_step_ids_by_ac.setdefault(ac_id, set()).add(step_contract.step_id)

        for ac_id in contract.global_obligation_ac_ids:
            candidates = contract.global_obligation_candidate_step_ids.get(ac_id, [])
            if not candidates:
                messages.append(f"{ac_id} is global but has no candidate step ids")
            for step_id in candidates:
                if step_id not in {step_contract.step_id for step_contract in contract.step_contracts}:
                    messages.append(f"{ac_id} references missing candidate step {step_id}")
                if step_id in owned_step_ids_by_ac.get(ac_id, set()):
                    messages.append(f"{ac_id} is both owned and global on {step_id}")

        canonical_effective = set(contract.canonical_ac_ids) - set(contract.waived_ac_ids)
        suffix_fallback_ids = cls._deterministic_suffix_owned_ac_fallback_ids(
            contract,
            canonical_effective,
            owned_counts,
        )
        for ac_id in sorted(canonical_effective):
            owned_count = owned_counts.get(ac_id, 0)
            if ac_id == "AC-id" and contract.canonical_ac_ids:
                messages.append("structured parsing produced fake canonical acceptance id `AC-id`")
            if owned_count == 0:
                if len(contract.step_contracts) == 1 or ac_id in suffix_fallback_ids:
                    continue
                messages.append(f"{ac_id} is not owned by any step")
            if owned_count > 1:
                explicit_multi_owner = sum(
                    1
                    for step_contract in contract.step_contracts
                    if ac_id in step_contract.explicit_owned_ac_ids
                ) > 1
                if explicit_multi_owner:
                    messages.append(f"{ac_id} is explicitly owned by multiple steps")
                else:
                    messages.append(f"{ac_id} is owned by multiple steps without explicit plan ownership")

        if unresolved:
            messages.append(
                "unresolved canonical acceptance criteria: " + ", ".join(unresolved)
            )
        return sorted(dict.fromkeys(messages))

    @staticmethod
    def _deterministic_suffix_owned_ac_fallback_ids(
        contract: SubfeaturePlanningContract,
        canonical_effective: set[str],
        owned_counts: dict[str, int],
    ) -> set[str]:
        unowned = sorted(ac_id for ac_id in canonical_effective if owned_counts.get(ac_id, 0) == 0)
        if not unowned or len(unowned) != len(contract.step_contracts):
            return set()
        step_ids = {step.step_id for step in contract.step_contracts}
        mapped: set[str] = set()
        for ac_id in unowned:
            match = re.search(r"(\d+)$", ac_id)
            if not match:
                return set()
            if f"STEP-{match.group(1)}" not in step_ids:
                return set()
            mapped.add(ac_id)
        return mapped
    @staticmethod
    def _normalize_step_id(step_id: str) -> str:
        candidate = step_id.strip().upper()
        if candidate.startswith("STEP-"):
            return candidate
        return f"STEP-{candidate.removeprefix('#STEP-').removeprefix('#')}"

    @classmethod
    def _extract_trace_ids(cls, text: str) -> dict[str, set[str]]:
        return {
            "requirements": set(_REQ_ID_PATTERN.findall(text)),
            "journeys": set(_JOURNEY_ID_PATTERN.findall(text)),
            "steps": {cls._normalize_step_id(match) for match in _STEP_ID_PATTERN.findall(text)},
            "decisions": set(_DECISION_ID_PATTERN.findall(text)),
            "nfrs": set(_NFR_ID_PATTERN.findall(text)),
        }

    @classmethod
    def _criterion_trace_ids(
        cls,
        criterion: TestAcceptanceCriterion,
    ) -> dict[str, set[str]]:
        criterion_text = "\n".join(
            value
            for value in (
                criterion.description,
                criterion.linked_requirement,
                criterion.linked_journey_step_id,
                criterion.linked_verifiable_state_id,
                criterion.verification_method,
                criterion.pass_condition,
            )
            if value
        )
        trace_ids = cls._extract_trace_ids(criterion_text)
        trace_ids["requirements"].update(criterion.refs.requirement_ids)
        trace_ids["journeys"].update(criterion.refs.journey_ids)
        trace_ids["steps"].update(
            cls._normalize_step_id(step_id)
            for step_id in criterion.refs.journey_step_ids
            if step_id and "STEP-" in step_id.upper()
        )
        trace_ids["decisions"].update(criterion.refs.decision_ids)
        trace_ids["nfrs"].update(criterion.refs.nfr_ids)
        return trace_ids

    @classmethod
    def _slice_trace_ids(
        cls,
        *,
        step_id: str,
        section_text: str,
    ) -> dict[str, set[str]]:
        trace_ids = cls._extract_trace_ids(section_text)
        trace_ids["steps"].add(cls._normalize_step_id(step_id))
        return trace_ids

    @staticmethod
    def _trace_sets_intersect(left: set[str], right: set[str]) -> bool:
        return bool(left and right and left & right)

    @classmethod
    def _classify_criterion_against_slice_trace(
        cls,
        criterion: TestAcceptanceCriterion,
        slice_trace_ids: dict[str, set[str]],
    ) -> tuple[bool, bool]:
        criterion_trace_ids = cls._criterion_trace_ids(criterion)
        owned = any(
            cls._trace_sets_intersect(
                criterion_trace_ids[token_family],
                slice_trace_ids[token_family],
            )
            for token_family in ("requirements", "journeys", "steps")
        )
        supporting = (not owned) and any(
            cls._trace_sets_intersect(
                criterion_trace_ids[token_family],
                slice_trace_ids[token_family],
            )
            for token_family in ("decisions", "nfrs")
        )
        return owned, supporting

    @classmethod
    def _criterion_matches_step_trace(
        cls,
        criterion: TestAcceptanceCriterion,
        slice_trace_ids: dict[str, set[str]],
    ) -> bool:
        owned, supporting = cls._classify_criterion_against_slice_trace(
            criterion,
            slice_trace_ids,
        )
        return owned or supporting

    @staticmethod
    def _slice_owned_acceptance_ids(slice_info: TaskPlanningSlice) -> list[str]:
        return sorted(
            set(
                slice_info.owned_acceptance_criterion_ids
                or slice_info.acceptance_criterion_ids
            )
        )

    @staticmethod
    def _slice_supporting_acceptance_ids(slice_info: TaskPlanningSlice) -> list[str]:
        return sorted(set(slice_info.supporting_acceptance_criterion_ids))

    @classmethod
    def _slice_context_acceptance_ids(
        cls,
        slice_info: TaskPlanningSlice,
        *,
        owned_only: bool = False,
    ) -> list[str]:
        if owned_only:
            owned_ids = cls._slice_owned_acceptance_ids(slice_info)
            if owned_ids:
                return owned_ids
            fallback_ids = sorted(
                set(slice_info.global_obligation_ac_ids)
                | set(slice_info.supporting_acceptance_criterion_ids)
            )
            if fallback_ids:
                return fallback_ids
            return sorted(set(slice_info.acceptance_criterion_ids))
        if (
            slice_info.owned_acceptance_criterion_ids
            or slice_info.supporting_acceptance_criterion_ids
            or slice_info.global_obligation_ac_ids
        ):
            return sorted(
                set(slice_info.owned_acceptance_criterion_ids)
                | set(slice_info.supporting_acceptance_criterion_ids)
                | set(slice_info.global_obligation_ac_ids)
            )
        return sorted(set(slice_info.acceptance_criterion_ids))

    @classmethod
    def _slice_from_step_contract(
        cls,
        *,
        contract: SubfeaturePlanningContract,
        step_contract: StepPlanningContract,
        section_text: str,
        slice_id: str,
    ) -> TaskPlanningSlice:
        global_obligation_ac_ids = sorted(
            ac_id
            for ac_id, candidate_step_ids in contract.global_obligation_candidate_step_ids.items()
            if step_contract.step_id in candidate_step_ids
        )
        required_reference_sources = (
            step_contract.required_reference_sources
            or cls._required_reference_sources_for_step_contract(
                contract,
                step_contract,
            )
        )
        return TaskPlanningSlice(
            slice_id=slice_id,
            title=step_contract.title,
            step_ids=[step_contract.step_id],
            requirement_ids=step_contract.requirement_ids,
            journey_ids=step_contract.journey_ids,
            acceptance_criterion_ids=step_contract.owned_ac_ids,
            owned_acceptance_criterion_ids=step_contract.owned_ac_ids,
            supporting_acceptance_criterion_ids=step_contract.supporting_ac_ids,
            strict_acceptance_criteria=bool(step_contract.owned_ac_ids),
            step_titles=[step_contract.title],
            mandatory_source_chars=len(section_text),
            global_obligation_ac_ids=global_obligation_ac_ids,
            required_reference_sources=required_reference_sources,
            slice_contract_digest=cls._slice_contract_digest(
                step_ids=[step_contract.step_id],
                requirement_ids=step_contract.requirement_ids,
                journey_ids=step_contract.journey_ids,
                owned_acceptance_criterion_ids=step_contract.owned_ac_ids,
                supporting_acceptance_criterion_ids=step_contract.supporting_ac_ids,
                global_obligation_ac_ids=global_obligation_ac_ids,
                required_reference_sources=required_reference_sources,
            ),
        )

    @classmethod
    def _bfs_step_reconciled_owned_ids(
        cls,
        step_ids: list[str],
    ) -> list[str]:
        owned_ids: set[str] = set()
        for step_id in step_ids:
            owned_ids.update(_BFS_STEP_RECONCILED_OWNED_ACS.get(step_id, ()))
        return sorted(owned_ids)

    @classmethod
    def _apply_bfs_step_owned_ac_overrides(
        cls,
        slug: str,
        slice_info: TaskPlanningSlice,
    ) -> TaskPlanningSlice:
        if slug != _BFS_SLUG:
            return slice_info

        forced_owned_ids = cls._bfs_step_reconciled_owned_ids(slice_info.step_ids)
        if not forced_owned_ids:
            return slice_info

        owned_ids = sorted(
            set(cls._slice_owned_acceptance_ids(slice_info)) | set(forced_owned_ids)
        )
        supporting_ids = sorted(
            set(cls._slice_supporting_acceptance_ids(slice_info)) - set(forced_owned_ids)
        )
        return slice_info.model_copy(
            update={
                "owned_acceptance_criterion_ids": owned_ids,
                "supporting_acceptance_criterion_ids": supporting_ids,
                "acceptance_criterion_ids": owned_ids,
                "strict_acceptance_criteria": bool(owned_ids),
            }
        )

    @classmethod
    def _merge_atomic_slices_for_existing_slice(
        cls,
        slice_info: TaskPlanningSlice,
        atomic_by_step_id: dict[str, TaskPlanningSlice],
    ) -> TaskPlanningSlice:
        atomic_children = [
            atomic_by_step_id[step_id]
            for step_id in slice_info.step_ids
            if step_id in atomic_by_step_id
        ]
        if not atomic_children:
            return slice_info

        owned_acceptance_ids = sorted(
            {
                ac_id
                for atomic_slice in atomic_children
                for ac_id in atomic_slice.owned_acceptance_criterion_ids
            }
        )
        supporting_acceptance_ids = sorted(
            {
                ac_id
                for atomic_slice in atomic_children
                for ac_id in atomic_slice.supporting_acceptance_criterion_ids
            }
        )
        global_obligation_ids = sorted(
            {
                ac_id
                for atomic_slice in atomic_children
                for ac_id in atomic_slice.global_obligation_ac_ids
            }
        )
        context_only_legacy_acceptance = (
            bool(slice_info.acceptance_criterion_ids)
            and not slice_info.strict_acceptance_criteria
            and not slice_info.owned_acceptance_criterion_ids
        )
        context_only_existing_obligations = (
            bool(slice_info.global_obligation_ac_ids)
            and not slice_info.strict_acceptance_criteria
            and not slice_info.acceptance_criterion_ids
            and not slice_info.owned_acceptance_criterion_ids
        )
        if context_only_legacy_acceptance or context_only_existing_obligations:
            global_obligation_ids = sorted(
                set(global_obligation_ids)
                | set(owned_acceptance_ids)
                | set(slice_info.global_obligation_ac_ids)
            )
            owned_acceptance_ids = []
            supporting_acceptance_ids = []
        if context_only_legacy_acceptance or context_only_existing_obligations:
            acceptance_criterion_ids = []
            strict_acceptance_criteria = False
        elif owned_acceptance_ids or supporting_acceptance_ids:
            acceptance_criterion_ids = owned_acceptance_ids
            strict_acceptance_criteria = bool(owned_acceptance_ids)
        else:
            acceptance_criterion_ids = sorted(
                {
                    ac_id
                    for atomic_slice in atomic_children
                    for ac_id in atomic_slice.acceptance_criterion_ids
                }
            )
            strict_acceptance_criteria = bool(atomic_children) and all(
                atomic_slice.strict_acceptance_criteria for atomic_slice in atomic_children
            )

        return slice_info.model_copy(
            update={
                "requirement_ids": sorted(
                    {
                        requirement_id
                        for atomic_slice in atomic_children
                        for requirement_id in atomic_slice.requirement_ids
                    }
                ),
                "journey_ids": sorted(
                    {
                        journey_id
                        for atomic_slice in atomic_children
                        for journey_id in atomic_slice.journey_ids
                    }
                ),
                "owned_acceptance_criterion_ids": owned_acceptance_ids,
                "supporting_acceptance_criterion_ids": supporting_acceptance_ids,
                "acceptance_criterion_ids": acceptance_criterion_ids,
                "strict_acceptance_criteria": strict_acceptance_criteria,
                "global_obligation_ac_ids": global_obligation_ids,
                "step_titles": [
                    step_title
                    for atomic_slice in atomic_children
                    for step_title in atomic_slice.step_titles
                ]
                or slice_info.step_titles,
                "mandatory_source_chars": sum(
                    atomic_slice.mandatory_source_chars for atomic_slice in atomic_children
                ),
                "required_reference_sources": sorted(
                    {
                        source_family
                        for atomic_slice in atomic_children
                        for source_family in atomic_slice.required_reference_sources
                    }
                ),
                "slice_contract_digest": cls._slice_contract_digest(
                    step_ids=[
                        step_id
                        for atomic_slice in atomic_children
                        for step_id in atomic_slice.step_ids
                    ],
                    requirement_ids=[
                        requirement_id
                        for atomic_slice in atomic_children
                        for requirement_id in atomic_slice.requirement_ids
                    ],
                    journey_ids=[
                        journey_id
                        for atomic_slice in atomic_children
                        for journey_id in atomic_slice.journey_ids
                    ],
                    owned_acceptance_criterion_ids=owned_acceptance_ids,
                    supporting_acceptance_criterion_ids=supporting_acceptance_ids,
                    global_obligation_ac_ids=[
                        ac_id
                        for ac_id in global_obligation_ids
                    ],
                    required_reference_sources=[
                        source_family
                        for atomic_slice in atomic_children
                        for source_family in atomic_slice.required_reference_sources
                    ],
                ),
            }
        )

    @classmethod
    def _resolve_step_keyed_augments(
        cls,
        augments: dict[str, SliceContractAugment],
        manifest: TaskPlanningSliceManifest,
    ) -> dict[str, SliceContractAugment]:
        """Resolve STEP-keyed augment entries to their owning manifest slices.

        Augment keys may be manifest slice ids (applied verbatim) or plan step
        ids (``STEP-N``): steps are the stable pre-manifest vocabulary, so an
        augments key can be authored BEFORE the slicer has decided how steps
        group into slices. Each step key is resolved to the manifest slice
        whose ``step_ids`` contains it and set-unioned into that slice's
        effective augment (merging with any direct slice-keyed entry). A step
        key no manifest slice owns raises — an operator pin that cannot land
        anywhere must fail loud, never drop silently."""
        step_keys = [key for key in augments if key.upper().startswith("STEP-")]
        if not step_keys:
            return augments
        step_owner = {
            step_id: slice_info.slice_id
            for slice_info in manifest.slices
            for step_id in slice_info.step_ids
        }
        resolved = {key: value for key, value in augments.items() if key not in step_keys}
        for key in step_keys:
            owner = step_owner.get(key)
            if owner is None:
                raise RuntimeError(
                    f"operator slice-contract augments for {manifest.slug} target "
                    f"step {key!r} which no manifest slice owns; manifest steps: "
                    f"{sorted(step_owner)}. Fix the dag-slice-augments store key "
                    "before re-running."
                )
            addition = augments[key]
            existing = resolved.get(owner)
            if existing is None:
                resolved[owner] = addition
            else:
                resolved[owner] = SliceContractAugment(
                    add_step_ids=existing.add_step_ids
                    + [s for s in addition.add_step_ids if s not in existing.add_step_ids],
                    add_step_titles=existing.add_step_titles
                    + [t for t in addition.add_step_titles if t not in existing.add_step_titles],
                    add_requirement_ids=sorted(
                        set(existing.add_requirement_ids) | set(addition.add_requirement_ids)
                    ),
                    add_journey_ids=sorted(
                        set(existing.add_journey_ids) | set(addition.add_journey_ids)
                    ),
                )
            logger.info(
                "slice contract augment key %s resolved to owning slice %s/%s",
                key,
                manifest.slug,
                owner,
            )
        return resolved

    @classmethod
    def _apply_slice_contract_augment(
        cls,
        slice_info: TaskPlanningSlice,
        augment: SliceContractAugment,
    ) -> TaskPlanningSlice:
        """Apply one operator-pinned augment to a (rebuilt) slice — pure
        additive union, deterministic and idempotent. Recomputes the slice
        contract digest from the FINAL field values so contract-drift
        detection sees the augmented scope."""
        step_ids = list(slice_info.step_ids) + [
            step_id for step_id in augment.add_step_ids if step_id not in slice_info.step_ids
        ]
        step_titles = list(slice_info.step_titles) + [
            title for title in augment.add_step_titles if title not in slice_info.step_titles
        ]
        requirement_ids = sorted(set(slice_info.requirement_ids) | set(augment.add_requirement_ids))
        journey_ids = sorted(set(slice_info.journey_ids) | set(augment.add_journey_ids))
        if (
            step_ids == slice_info.step_ids
            and step_titles == slice_info.step_titles
            and requirement_ids == slice_info.requirement_ids
            and journey_ids == slice_info.journey_ids
        ):
            return slice_info
        return slice_info.model_copy(
            update={
                "step_ids": step_ids,
                "step_titles": step_titles,
                "requirement_ids": requirement_ids,
                "journey_ids": journey_ids,
                "slice_contract_digest": cls._slice_contract_digest(
                    step_ids=step_ids,
                    requirement_ids=requirement_ids,
                    journey_ids=journey_ids,
                    owned_acceptance_criterion_ids=slice_info.owned_acceptance_criterion_ids,
                    supporting_acceptance_criterion_ids=slice_info.supporting_acceptance_criterion_ids,
                    global_obligation_ac_ids=slice_info.global_obligation_ac_ids,
                    required_reference_sources=slice_info.required_reference_sources,
                ),
            }
        )

    @classmethod
    def _derive_slices_from_markdown_plan(
        cls,
        plan_markdown: str,
        test_plan: TestPlan | None,
        fallback_ac_ids: list[str] | None = None,
        *,
        contract: SubfeaturePlanningContract | None = None,
    ) -> list[TaskPlanningSlice]:
        plan_markdown = cls._normalize_plan_markdown_for_slice_derivation(plan_markdown)
        all_ac_ids = sorted(
            criterion.id for criterion in (test_plan.acceptance_criteria if test_plan else []) if criterion.id
        ) or sorted(fallback_ac_ids or [])
        matches = list(_STEP_HEADING_PATTERN.finditer(plan_markdown))
        if not matches:
            return [
                TaskPlanningSlice(
                    slice_id="slice-1",
                    title="Whole subfeature",
                    acceptance_criterion_ids=all_ac_ids,
                    strict_acceptance_criteria=False,
                    mandatory_source_chars=len(plan_markdown),
                    slice_contract_digest=cls._slice_contract_digest(
                        step_ids=[],
                        requirement_ids=[],
                        journey_ids=[],
                        owned_acceptance_criterion_ids=[],
                        supporting_acceptance_criterion_ids=[],
                        global_obligation_ac_ids=[],
                        required_reference_sources=[],
                    ),
                )
            ]

        raw_slices = cls._derive_atomic_slices_from_markdown_plan(
            plan_markdown,
            test_plan,
            fallback_ac_ids=fallback_ac_ids,
            contract=contract,
        )

        merged: list[TaskPlanningSlice] = []
        current: TaskPlanningSlice | None = None
        for slice_info in raw_slices:
            if current is None:
                current = slice_info
                continue
            combined_chars = current.mandatory_source_chars + slice_info.mandatory_source_chars
            combined_steps = len(current.step_ids) + len(slice_info.step_ids)
            if combined_chars <= _SLICE_SOURCE_BUDGET and combined_steps <= _SLICE_MAX_STEPS:
                combined_owned_acceptance_ids = sorted(
                    set(
                        current.owned_acceptance_criterion_ids
                        + slice_info.owned_acceptance_criterion_ids
                    )
                )
                combined_supporting_acceptance_ids = sorted(
                    set(
                        current.supporting_acceptance_criterion_ids
                        + slice_info.supporting_acceptance_criterion_ids
                    )
                )
                if combined_owned_acceptance_ids or combined_supporting_acceptance_ids:
                    combined_acceptance_criterion_ids = combined_owned_acceptance_ids
                    combined_strict_acceptance = bool(combined_owned_acceptance_ids)
                else:
                    combined_acceptance_criterion_ids = sorted(
                        set(
                            current.acceptance_criterion_ids
                            + slice_info.acceptance_criterion_ids
                        )
                    )
                    combined_strict_acceptance = (
                        current.strict_acceptance_criteria
                        and slice_info.strict_acceptance_criteria
                    )
                current = TaskPlanningSlice(
                    slice_id=current.slice_id,
                    title=current.title,
                    step_ids=current.step_ids + slice_info.step_ids,
                    requirement_ids=sorted(set(current.requirement_ids + slice_info.requirement_ids)),
                    journey_ids=sorted(set(current.journey_ids + slice_info.journey_ids)),
                    owned_acceptance_criterion_ids=combined_owned_acceptance_ids,
                    supporting_acceptance_criterion_ids=combined_supporting_acceptance_ids,
                    acceptance_criterion_ids=combined_acceptance_criterion_ids,
                    strict_acceptance_criteria=combined_strict_acceptance,
                    step_titles=current.step_titles + slice_info.step_titles,
                    mandatory_source_chars=combined_chars,
                    global_obligation_ac_ids=sorted(
                        set(current.global_obligation_ac_ids + slice_info.global_obligation_ac_ids)
                    ),
                    required_reference_sources=sorted(
                        set(current.required_reference_sources + slice_info.required_reference_sources)
                    ),
                    slice_contract_digest=cls._slice_contract_digest(
                        step_ids=current.step_ids + slice_info.step_ids,
                        requirement_ids=current.requirement_ids + slice_info.requirement_ids,
                        journey_ids=current.journey_ids + slice_info.journey_ids,
                        owned_acceptance_criterion_ids=combined_owned_acceptance_ids,
                        supporting_acceptance_criterion_ids=combined_supporting_acceptance_ids,
                        global_obligation_ac_ids=current.global_obligation_ac_ids + slice_info.global_obligation_ac_ids,
                        required_reference_sources=current.required_reference_sources + slice_info.required_reference_sources,
                    ),
                )
                continue
            merged.append(current)
            current = slice_info.model_copy(
                update={"slice_id": f"slice-{len(merged) + 1}"}
            )
        if current is not None:
            current = current.model_copy(
                update={"slice_id": f"slice-{len(merged) + 1}"}
            )
            merged.append(current)
        return merged

    @classmethod
    def _derive_atomic_slices_from_markdown_plan(
        cls,
        plan_markdown: str,
        test_plan: TestPlan | None,
        fallback_ac_ids: list[str] | None = None,
        *,
        contract: SubfeaturePlanningContract | None = None,
    ) -> list[TaskPlanningSlice]:
        plan_markdown = cls._normalize_plan_markdown_for_slice_derivation(plan_markdown)
        all_ac_ids = sorted(
            criterion.id for criterion in (test_plan.acceptance_criteria if test_plan else []) if criterion.id
        ) or sorted(fallback_ac_ids or [])
        matches = list(_STEP_HEADING_PATTERN.finditer(plan_markdown))
        if not matches:
            return [
                TaskPlanningSlice(
                    slice_id="slice-1",
                    title="Whole subfeature",
                    acceptance_criterion_ids=all_ac_ids,
                    strict_acceptance_criteria=False,
                    mandatory_source_chars=len(plan_markdown),
                    slice_contract_digest=cls._slice_contract_digest(
                        step_ids=[],
                        requirement_ids=[],
                        journey_ids=[],
                        owned_acceptance_criterion_ids=[],
                        supporting_acceptance_criterion_ids=[],
                        global_obligation_ac_ids=[],
                        required_reference_sources=[],
                    ),
                )
            ]

        if contract is not None and contract.step_contracts:
            section_by_step = {
                step_id: section_text
                for step_id, _title, section_text in cls._step_sections_from_plan(plan_markdown)
            }
            raw_slices: list[TaskPlanningSlice] = []
            for step_contract in contract.step_contracts:
                raw_slices.append(
                    cls._slice_from_step_contract(
                        contract=contract,
                        step_contract=step_contract,
                        section_text=section_by_step.get(step_contract.step_id, ""),
                        slice_id=f"slice-{len(raw_slices) + 1}",
                    )
                )
            return raw_slices

        raw_slices: list[TaskPlanningSlice] = []
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(plan_markdown)
            section_text = plan_markdown[start:end]
            step_id = match.group(1).strip()
            title = match.group(2).strip() or step_id
            requirement_ids = sorted(set(_REQ_ID_PATTERN.findall(section_text)))
            journey_ids = sorted(set(_JOURNEY_ID_PATTERN.findall(section_text)))
            slice_trace_ids = cls._slice_trace_ids(step_id=step_id, section_text=section_text)
            owned_ac_ids: list[str] = []
            supporting_ac_ids: list[str] = []
            strict = False
            if test_plan and test_plan.acceptance_criteria:
                for criterion in test_plan.acceptance_criteria:
                    if not criterion.id:
                        continue
                    owned, supporting = cls._classify_criterion_against_slice_trace(
                        criterion,
                        slice_trace_ids,
                    )
                    if owned:
                        owned_ac_ids.append(criterion.id)
                    elif supporting:
                        supporting_ac_ids.append(criterion.id)
                owned_ac_ids = sorted(set(owned_ac_ids))
                supporting_ac_ids = sorted(set(supporting_ac_ids))
                strict = bool(owned_ac_ids)
                acceptance_criterion_ids = owned_ac_ids
            else:
                acceptance_criterion_ids = all_ac_ids
            raw_slices.append(
                TaskPlanningSlice(
                    slice_id=f"slice-{len(raw_slices) + 1}",
                    title=title,
                    step_ids=[step_id],
                    requirement_ids=requirement_ids,
                    journey_ids=journey_ids,
                    acceptance_criterion_ids=acceptance_criterion_ids,
                    owned_acceptance_criterion_ids=owned_ac_ids,
                    supporting_acceptance_criterion_ids=supporting_ac_ids,
                    strict_acceptance_criteria=strict,
                    step_titles=[title],
                    mandatory_source_chars=len(section_text),
                    slice_contract_digest=cls._slice_contract_digest(
                        step_ids=[step_id],
                        requirement_ids=requirement_ids,
                        journey_ids=journey_ids,
                        owned_acceptance_criterion_ids=owned_ac_ids,
                        supporting_acceptance_criterion_ids=supporting_ac_ids,
                        global_obligation_ac_ids=[],
                        required_reference_sources=[],
                    ),
                )
            )
        return raw_slices

    @staticmethod
    def _normalize_subfeature_execution_order(
        dag: ImplementationDAG,
    ) -> tuple[ImplementationDAG, bool]:
        """Return a dependency-safe execution order for a subfeature DAG.

        The planner can return waves that contain tasks whose dependencies are
        scheduled in the same wave. Implementation executes each wave in
        parallel, so same-wave dependency edges are semantically invalid even if
        the model output itself parses cleanly.
        """
        tasks_by_id: dict[str, ImplementationTask] = {}
        task_order: dict[str, int] = {}
        for idx, task in enumerate(dag.tasks):
            if task.id in tasks_by_id:
                raise ValueError(f"duplicate task id in DAG: {task.id}")
            tasks_by_id[task.id] = task
            task_order[task.id] = idx

        wave_index: dict[str, int] = {}
        wave_position: dict[str, int] = {}
        for group_idx, group in enumerate(dag.execution_order):
            for pos_idx, task_id in enumerate(group):
                if task_id not in tasks_by_id:
                    raise ValueError(
                        f"execution_order references unknown task id: {task_id}"
                    )
                if task_id in wave_index:
                    raise ValueError(
                        f"execution_order references task id more than once: {task_id}"
                    )
                wave_index[task_id] = group_idx
                wave_position[task_id] = pos_idx

        fallback_group = len(dag.execution_order)
        missing_task_ids = [task.id for task in dag.tasks if task.id not in wave_index]
        for offset, task_id in enumerate(missing_task_ids):
            # Keep omitted tasks conservative: append them in their own trailing
            # waves instead of coalescing them into one new parallel batch.
            wave_index[task_id] = fallback_group + offset
            wave_position[task_id] = 0

        def _sort_key(task_id: str) -> tuple[int, int, int, str]:
            return (
                wave_index[task_id],
                wave_position[task_id],
                task_order[task_id],
                task_id,
            )

        indegree: dict[str, int] = {task.id: 0 for task in dag.tasks}
        dependents: dict[str, list[str]] = {task.id: [] for task in dag.tasks}
        for task in dag.tasks:
            seen_dependencies: set[str] = set()
            for dep in task.dependencies:
                if dep in seen_dependencies:
                    continue
                seen_dependencies.add(dep)
                if dep not in tasks_by_id:
                    raise ValueError(
                        f"task {task.id} depends on unknown task id: {dep}"
                    )
                indegree[task.id] += 1
                dependents[dep].append(task.id)

        ready = sorted(
            [task_id for task_id, count in indegree.items() if count == 0],
            key=_sort_key,
        )
        topo_order: list[str] = []

        while ready:
            task_id = ready.pop(0)
            topo_order.append(task_id)
            for dependent_id in dependents[task_id]:
                indegree[dependent_id] -= 1
                if indegree[dependent_id] == 0:
                    ready.append(dependent_id)
            ready.sort(key=_sort_key)

        if len(topo_order) != len(dag.tasks):
            remaining = sorted(
                [task_id for task_id, count in indegree.items() if count > 0],
                key=_sort_key,
            )
            raise ValueError(
                "DAG contains cyclic or unsatisfied dependencies: "
                + ", ".join(remaining)
            )

        assigned_wave: dict[str, int] = {}
        for task_id in topo_order:
            task = tasks_by_id[task_id]
            required_wave = wave_index[task_id]
            for dep in dict.fromkeys(task.dependencies):
                required_wave = max(required_wave, assigned_wave[dep] + 1)
            assigned_wave[task_id] = required_wave

        compact_map = {
            original_wave: compact_idx
            for compact_idx, original_wave in enumerate(sorted(set(assigned_wave.values())))
        }
        normalized_order: list[list[str]] = [[] for _ in compact_map]
        for task_id in sorted(tasks_by_id, key=_sort_key):
            normalized_order[compact_map[assigned_wave[task_id]]].append(task_id)

        if normalized_order == dag.execution_order:
            return dag, False

        return (
            ImplementationDAG(
                tasks=dag.tasks,
                num_teams=dag.num_teams,
                execution_order=normalized_order,
                requirement_coverage=dag.requirement_coverage,
                complete=dag.complete,
            ),
            True,
        )

    @classmethod
    def _derive_atomic_slices_from_planning_index(
        cls,
        plan_sidecar: StructuredArtifact[TechnicalPlan],
        planning_index: SubfeaturePlanningIndex,
    ) -> list[TaskPlanningSlice]:
        slice_inputs_by_chunk_id = {
            slice_input.step_chunk_ids[0]: slice_input
            for slice_input in planning_index.slice_inputs
            if len(slice_input.step_chunk_ids) == 1
        }
        slices: list[TaskPlanningSlice] = []
        for step in plan_sidecar.content.steps:
            slice_input = slice_inputs_by_chunk_id.get(step.chunk.chunk_id)
            requirement_ids = sorted(
                set(step.requirement_ids)
                | set(step.refs.requirement_ids)
                | set(slice_input.requirement_ids if slice_input is not None else [])
            )
            journey_ids = sorted(
                set(step.journey_ids)
                | set(step.refs.journey_ids)
                | set(slice_input.journey_ids if slice_input is not None else [])
            )
            owned_acceptance_criterion_ids = sorted(
                set(slice_input.owned_acceptance_criterion_ids if slice_input is not None else step.owned_acceptance_criterion_ids)
            )
            supporting_acceptance_criterion_ids = sorted(
                set(slice_input.supporting_acceptance_criterion_ids if slice_input is not None else [])
            )
            global_obligation_ac_ids = sorted(
                set(slice_input.global_obligation_ac_ids if slice_input is not None else [])
            )
            required_reference_sources = sorted(
                set(slice_input.required_reference_sources if slice_input is not None else ["plan"])
            )
            title = step.title or step.objective or step.id
            slices.append(
                TaskPlanningSlice(
                    slice_id=slice_input.slice_id if slice_input is not None else step.id.lower().replace("step-", "slice-"),
                    title=title,
                    step_ids=[step.id],
                    requirement_ids=requirement_ids,
                    journey_ids=journey_ids,
                    acceptance_criterion_ids=owned_acceptance_criterion_ids,
                    owned_acceptance_criterion_ids=owned_acceptance_criterion_ids,
                    supporting_acceptance_criterion_ids=supporting_acceptance_criterion_ids,
                    strict_acceptance_criteria=bool(owned_acceptance_criterion_ids),
                    step_titles=[title],
                    slice_contract_digest=cls._slice_contract_digest(
                        step_ids=[step.id],
                        requirement_ids=requirement_ids,
                        journey_ids=journey_ids,
                        owned_acceptance_criterion_ids=owned_acceptance_criterion_ids,
                        supporting_acceptance_criterion_ids=supporting_acceptance_criterion_ids,
                        global_obligation_ac_ids=global_obligation_ac_ids,
                        required_reference_sources=required_reference_sources,
                    ),
                    required_reference_sources=required_reference_sources,
                    global_obligation_ac_ids=global_obligation_ac_ids,
                )
            )
        return slices

    @classmethod
    async def _compile_subfeature_planning_contract_from_sidecars(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
    ) -> SubfeaturePlanningContract:
        plan_sidecar = await load_structured_artifact(runner, feature, f"plan:{slug}")
        test_plan_sidecar = await load_structured_artifact(runner, feature, f"test-plan:{slug}")
        prd_sidecar = await load_structured_artifact(runner, feature, f"prd:{slug}")
        design_sidecar = await load_structured_artifact(runner, feature, f"design:{slug}")
        system_design_sidecar = await load_structured_artifact(runner, feature, f"system-design:{slug}")
        decisions_sidecar = await load_structured_artifact(runner, feature, f"decisions:{slug}")
        shared_index = await cls._load_shared_planning_index(runner, feature)
        planning_index = await cls._load_subfeature_planning_index(runner, feature, slug)
        ledger_backfill_status = await cls._load_backfill_status(runner, feature)
        if plan_sidecar is None or test_plan_sidecar is None or planning_index is None:
            raise RuntimeError(f"migrated subfeature {slug} is missing sidecars or planning index")

        async def _decision_ledger_text(artifact_key: str) -> str:
            # Decision-universe admission only — an absent/unsidecared ledger
            # means fewer admitted ids (fail-closed), never a compile failure.
            try:
                return await cls._load_artifact_text_for_planning(
                    runner,
                    feature,
                    artifact_key,
                    backfill_status=ledger_backfill_status,
                )
            except Exception:
                logger.debug(
                    "decision ledger %s unavailable for sidecar decision-universe admission",
                    artifact_key,
                    exc_info=True,
                )
                return ""

        canonical_ac_ids = [
            criterion.id
            for criterion in test_plan_sidecar.content.acceptance_criteria
            if criterion.id
        ]
        prior_contract = await cls._load_subfeature_planning_contract(runner, feature, slug)
        store_waived_ac_ids = await _load_planning_waivers(runner, feature, slug)
        _effective_ac_ids, waived_map = _effective_coverage_ids_for_task_planning(
            slug,
            set(canonical_ac_ids),
            prior_contract.waived_ac_ids if prior_contract is not None else None,
            store_waived_ac_ids=store_waived_ac_ids,
        )
        global_obligation_candidate_step_ids: dict[str, list[str]] = {}
        step_chunk_to_slice_input = {
            slice_input.step_chunk_ids[0]: slice_input
            for slice_input in planning_index.slice_inputs
            if len(slice_input.step_chunk_ids) == 1
        }
        for slice_input in planning_index.slice_inputs:
            step_ids = [
                step.id
                for step in plan_sidecar.content.steps
                if step.chunk.chunk_id in slice_input.step_chunk_ids
            ]
            for ac_id in slice_input.global_obligation_ac_ids:
                global_obligation_candidate_step_ids.setdefault(ac_id, []).extend(step_ids)

        step_contracts: list[StepPlanningContract] = []
        for step in plan_sidecar.content.steps:
            slice_input = step_chunk_to_slice_input.get(step.chunk.chunk_id)
            explicit_owned_ac_ids = canonicalize_acceptance_ids(
                sorted(set(step.owned_acceptance_criterion_ids)),
                canonical_ac_ids,
            )
            owned_ac_ids = sorted(
                set(slice_input.owned_acceptance_criterion_ids if slice_input is not None else explicit_owned_ac_ids)
            )
            inferred_owned_ac_ids = sorted(set(owned_ac_ids) - set(explicit_owned_ac_ids))
            supporting_ac_ids = sorted(
                set(slice_input.supporting_acceptance_criterion_ids if slice_input is not None else [])
            )
            requirement_ids = sorted(
                set(step.requirement_ids)
                | set(step.refs.requirement_ids)
                | set(slice_input.requirement_ids if slice_input is not None else [])
            )
            journey_ids = sorted(
                set(step.journey_ids)
                | set(step.refs.journey_ids)
                | set(slice_input.journey_ids if slice_input is not None else [])
            )
            step_contracts.append(
                StepPlanningContract(
                    step_id=step.id,
                    title=step.title or step.objective,
                    section_digest=step.chunk.content_digest,
                    requirement_ids=requirement_ids,
                    journey_ids=journey_ids,
                    decision_ids=sorted(set(step.refs.decision_ids)),
                    nfr_ids=sorted(set(step.refs.nfr_ids)),
                    verifiable_state_ids=sorted(set(step.refs.verifiable_state_ids)),
                    explicit_owned_ac_ids=explicit_owned_ac_ids,
                    inferred_owned_ac_ids=inferred_owned_ac_ids,
                    owned_ac_ids=owned_ac_ids,
                    supporting_ac_ids=supporting_ac_ids,
                    required_reference_sources=sorted(
                        set(slice_input.required_reference_sources if slice_input is not None else ["plan"])
                    ),
                )
            )

        contract = SubfeaturePlanningContract(
            slug=slug,
            plan_digest=plan_sidecar.meta.content_digest,
            test_plan_digest=test_plan_sidecar.meta.content_digest,
            canonical_ac_ids=sorted(canonical_ac_ids),
            waived_ac_ids=sorted(waived_map),
            global_obligation_ac_ids=sorted(global_obligation_candidate_step_ids),
            global_obligation_candidate_step_ids={
                ac_id: sorted(set(step_ids))
                for ac_id, step_ids in sorted(global_obligation_candidate_step_ids.items())
            },
            requirement_universe=sorted(
                {
                    *(
                        requirement.id
                        for requirement in (prd_sidecar.content.structured_requirements if prd_sidecar is not None else [])
                        if requirement.id
                    ),
                    *((shared_index.requirement_ids if shared_index is not None else [])),
                }
            ),
            journey_universe=sorted(
                {
                    *(
                        journey.id
                        for journey in (prd_sidecar.content.journeys if prd_sidecar is not None else [])
                        if journey.id
                    ),
                    *((shared_index.journey_ids if shared_index is not None else [])),
                }
            ),
            decision_universe=sorted(
                {
                    *(
                        decision.id
                        for decision in (decisions_sidecar.content.decisions if decisions_sidecar is not None else [])
                        if decision.id
                    ),
                    *((shared_index.decision_ids if shared_index is not None else [])),
                    # Mirror the markdown path's admission rules (ea11fd3):
                    # plan-local decision ids defined in the plan itself plus
                    # the feature/global decision ledgers must validate here
                    # too — the sidecar's own decisions list is not the whole
                    # universe steps may legitimately cite. Ledgers load via
                    # the sidecar-aware planning loader so stale raw broad
                    # artifacts are not consumed on migrated features.
                    *cls._decision_universe_from_texts(
                        render_structured_markdown(plan_sidecar),
                        await _decision_ledger_text("decisions"),
                        await _decision_ledger_text("decisions:broad"),
                        await _decision_ledger_text(GLOBAL_DECISIONS_KEY),
                    ),
                }
            ),
            verifiable_state_universe=sorted(
                {
                    (state.id or f"{state.component_id}#{state.state_name}")
                    for state in (design_sidecar.content.verifiable_states if design_sidecar is not None else [])
                    if state.id or state.component_id
                }
            ),
            has_prd_artifact=prd_sidecar is not None,
            has_design_artifact=design_sidecar is not None,
            has_system_design_artifact=system_design_sidecar is not None,
            has_test_plan_artifact=True,
            step_contracts=step_contracts,
        )
        validation_messages = cls._validate_subfeature_planning_contract(contract, unresolved_ac_ids=[])
        if validation_messages:
            report_key = await cls._save_subfeature_planning_contract_report(
                runner,
                feature,
                slug=slug,
                messages=validation_messages,
            )
            raise PlanningContractError(slug, validation_messages, report_key=report_key)
        contract.contract_digest = cls._json_digest(contract.model_dump(mode="json"))
        await cls._save_subfeature_planning_contract(runner, feature, contract)
        await cls._clear_subfeature_planning_contract_report(runner, feature, slug)
        return contract

    @classmethod
    async def _derive_slice_manifest(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        subfeature: Any,
    ) -> TaskPlanningSliceManifest:
        backfill_status = await cls._load_backfill_status(runner, feature)
        migrated = cls._slug_is_migrated(backfill_status, subfeature.slug)
        if migrated:
            plan_sidecar = await load_structured_artifact(runner, feature, f"plan:{subfeature.slug}")
            test_plan_sidecar = await load_structured_artifact(runner, feature, f"test-plan:{subfeature.slug}")
            planning_index = await cls._load_subfeature_planning_index(runner, feature, subfeature.slug)
            if plan_sidecar is None or test_plan_sidecar is None or planning_index is None:
                raise RuntimeError(f"migrated subfeature {subfeature.slug} is missing sidecars or planning index")
            normalized_plan = render_structured_markdown(plan_sidecar)
            normalized_test_plan = render_structured_markdown(test_plan_sidecar)
            plan_digest = plan_sidecar.meta.content_digest
            test_plan_digest = test_plan_sidecar.meta.content_digest
        else:
            plan_text = await runner.artifacts.get(f"plan:{subfeature.slug}", feature=feature) or ""
            test_plan_text = await runner.artifacts.get(f"test-plan:{subfeature.slug}", feature=feature) or ""
            normalized_plan = cls._normalize_artifact_markdown(plan_text, f"plan:{subfeature.slug}")
            normalized_test_plan = cls._normalize_artifact_markdown(test_plan_text, "test-plan")
            plan_digest = cls._content_digest(normalized_plan)
            test_plan_digest = cls._content_digest(normalized_test_plan)
        contract = await cls._compile_subfeature_planning_contract(
            runner,
            feature,
            subfeature.slug,
        )
        if contract is not None and not contract.contract_digest:
            contract.contract_digest = cls._json_digest(contract.model_dump(mode="json"))

        existing = await cls._load_slice_manifest(runner, feature, subfeature.slug)
        if (
            existing is not None
            and existing.slices
            and existing.derivation_version == _SLICE_MANIFEST_DERIVATION_VERSION
            and existing.plan_digest == plan_digest
            and existing.test_plan_digest == test_plan_digest
        ):
            if existing.contract_digest != contract.contract_digest:
                existing.contract_digest = contract.contract_digest
                await cls._save_slice_manifest(runner, feature, existing)
            return existing
        if existing is not None and existing.slices:
            logger.info(
                "Invalidating stale slice manifest for %s (plan/test-plan/contract changed)",
                subfeature.slug,
            )
            await cls._clear_slice_manifest_artifacts(runner, feature, existing)

        if migrated:
            slices = cls._derive_atomic_slices_from_planning_index(plan_sidecar, planning_index)
        else:
            test_plan = cls._parse_test_plan(test_plan_text)
            slices = cls._derive_slices_from_markdown_plan(
                normalized_plan,
                test_plan,
                fallback_ac_ids=sorted(_extract_ac_ids(test_plan_text)),
                contract=contract,
            )
        manifest = TaskPlanningSliceManifest(
            slug=subfeature.slug,
            slices=slices,
            statuses=[SlicePlanningStatus(slice_id=slice_info.slice_id) for slice_info in slices],
            derivation_version=_SLICE_MANIFEST_DERIVATION_VERSION,
            plan_digest=plan_digest,
            test_plan_digest=test_plan_digest,
            contract_digest=contract.contract_digest,
        )
        await cls._save_slice_manifest(runner, feature, manifest)
        return manifest

    @classmethod
    async def _split_oversized_slice(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        manifest: TaskPlanningSliceManifest,
        slice_id: str,
    ) -> bool:
        slice_info = next((item for item in manifest.slices if item.slice_id == slice_id), None)
        if slice_info is None or len(slice_info.step_ids) <= 1:
            return False

        backfill_status = await cls._load_backfill_status(runner, feature)
        migrated = cls._slug_is_migrated(backfill_status, manifest.slug)
        contract = await cls._load_subfeature_planning_contract(
            runner,
            feature,
            manifest.slug,
        )
        if contract is None:
            contract = await cls._compile_subfeature_planning_contract(
                runner,
                feature,
                manifest.slug,
            )
        if contract is not None and not contract.contract_digest:
            contract.contract_digest = cls._json_digest(contract.model_dump(mode="json"))
        if migrated:
            plan_sidecar = await load_structured_artifact(runner, feature, f"plan:{manifest.slug}")
            planning_index = await cls._load_subfeature_planning_index(runner, feature, manifest.slug)
            if plan_sidecar is None or planning_index is None:
                raise RuntimeError(f"migrated subfeature {manifest.slug} is missing plan sidecar or planning index")
            atomic_slices = cls._derive_atomic_slices_from_planning_index(plan_sidecar, planning_index)
        else:
            plan_text = await runner.artifacts.get(f"plan:{manifest.slug}", feature=feature) or ""
            test_plan_text = await runner.artifacts.get(f"test-plan:{manifest.slug}", feature=feature) or ""
            normalized_plan = cls._normalize_artifact_markdown(plan_text, f"plan:{manifest.slug}")
            test_plan = cls._parse_test_plan(test_plan_text)
            atomic_slices = cls._derive_atomic_slices_from_markdown_plan(
                normalized_plan,
                test_plan,
                fallback_ac_ids=sorted(_extract_ac_ids(test_plan_text)),
                contract=contract,
            )
        child_slices = [
            atomic_slice.model_copy(deep=True)
            for atomic_slice in atomic_slices
            if set(atomic_slice.step_ids) & set(slice_info.step_ids)
        ]
        if len(child_slices) <= 1:
            return False

        status_map = cls._slice_status_map(manifest)
        parent_status = status_map.get(slice_id)
        parent_fragment_key = (
            parent_status.fragment_key
            if parent_status and parent_status.fragment_key
            else cls._slice_fragment_key(manifest.slug, slice_id)
        )
        await cls._delete_artifact_key(runner, feature, parent_fragment_key)

        remaining_attempts: list[SlicePlanningAttempt] = []
        for attempt in manifest.attempts:
            if attempt.slice_id != slice_id:
                remaining_attempts.append(attempt)
                continue
            if attempt.attempt_key:
                await cls._delete_artifact_key(runner, feature, attempt.attempt_key)
        manifest.attempts = remaining_attempts

        new_slices: list[TaskPlanningSlice] = []
        new_statuses: list[SlicePlanningStatus] = []
        used_slice_ids = {
            existing_slice.slice_id
            for existing_slice in manifest.slices
            if existing_slice.slice_id != slice_id
        }
        next_child_suffix = 1
        for existing_slice in manifest.slices:
            if existing_slice.slice_id != slice_id:
                new_slices.append(existing_slice)
                existing_status = status_map.get(existing_slice.slice_id)
                if existing_status is not None:
                    new_statuses.append(existing_status.model_copy(deep=True))
                else:
                    new_statuses.append(SlicePlanningStatus(slice_id=existing_slice.slice_id))
                continue
            for child_slice in child_slices:
                child_id = f"{slice_id}-{next_child_suffix}"
                while child_id in used_slice_ids:
                    next_child_suffix += 1
                    child_id = f"{slice_id}-{next_child_suffix}"
                next_child_suffix += 1
                used_slice_ids.add(child_id)
                child = child_slice.model_copy(update={"slice_id": child_id})
                new_slices.append(child)
                new_statuses.append(
                    SlicePlanningStatus(
                        slice_id=child.slice_id,
                        fragment_key=cls._slice_fragment_key(manifest.slug, child.slice_id),
                    )
                )

        manifest.slices = new_slices
        manifest.statuses = new_statuses
        manifest.complete = False
        await cls._save_slice_manifest(runner, feature, manifest)
        return True

    @staticmethod
    def _text_matches_slice_trace(text: str, slice_info: TaskPlanningSlice) -> bool:
        if not text.strip():
            return False
        lowered = text.lower()
        trace_tokens = (
            slice_info.step_ids
            + slice_info.requirement_ids
            + slice_info.journey_ids
            + slice_info.acceptance_criterion_ids
            + slice_info.owned_acceptance_criterion_ids
            + slice_info.supporting_acceptance_criterion_ids
            + slice_info.global_obligation_ac_ids
        )
        return any(token.lower() in lowered for token in trace_tokens if token)

    @staticmethod
    def _markdown_table_cell(value: str) -> str:
        return value.replace("\n", "<br>").replace("|", "\\|").strip()

    @classmethod
    def _criterion_markdown_row(
        cls,
        criterion: TestAcceptanceCriterion,
    ) -> str:
        requirement_ids = sorted(
            set(_REQ_ID_PATTERN.findall(criterion.linked_requirement))
            | set(criterion.refs.requirement_ids)
        )
        journey_ids = sorted(set(criterion.refs.journey_ids))
        step_ids = sorted(
            set(_STEP_ID_PATTERN.findall(criterion.linked_journey_step_id))
            | {
                cls._normalize_step_id(step_id)
                for step_id in criterion.refs.journey_step_ids
                if step_id and "STEP-" in step_id.upper()
            }
        )
        trace_bits = [
            *requirement_ids,
            *journey_ids,
            *step_ids,
            *criterion.refs.decision_ids,
            *criterion.refs.nfr_ids,
            *criterion.refs.verifiable_state_ids,
        ]
        return (
            f"| {cls._markdown_table_cell(criterion.id)} "
            f"| {cls._markdown_table_cell(criterion.description)} "
            f"| {cls._markdown_table_cell(', '.join(dict.fromkeys(trace_bits)))} "
            f"| {cls._markdown_table_cell(criterion.verification_method)} "
            f"| {cls._markdown_table_cell(criterion.pass_condition)} |"
        )

    @classmethod
    def _render_criteria_section(
        cls,
        title: str,
        criteria: list[TestAcceptanceCriterion],
    ) -> list[str]:
        if not criteria:
            return []
        return [
            f"## {title}",
            "",
            "| ID | Description | Trace | Method | Pass Condition |",
            "|---|---|---|---|---|",
            *[cls._criterion_markdown_row(criterion) for criterion in criteria],
            "",
        ]

    @classmethod
    def _test_plan_excerpt_from_model(
        cls,
        test_plan: TestPlan,
        slice_info: TaskPlanningSlice,
        *,
        owned_only: bool = False,
    ) -> str:
        criterion_by_id = {
            criterion.id: criterion
            for criterion in test_plan.acceptance_criteria
            if criterion.id
        }
        owned_ids = cls._slice_owned_acceptance_ids(slice_info)
        supporting_ids = (
            cls._slice_supporting_acceptance_ids(slice_info)
            if not owned_only or not owned_ids
            else []
        )
        global_ids = sorted(set(slice_info.global_obligation_ac_ids))

        owned = [criterion_by_id[ac_id] for ac_id in owned_ids if ac_id in criterion_by_id]
        supporting = [
            criterion_by_id[ac_id]
            for ac_id in supporting_ids
            if ac_id in criterion_by_id and ac_id not in set(owned_ids)
        ]
        global_obligations = [
            criterion_by_id[ac_id]
            for ac_id in global_ids
            if ac_id in criterion_by_id and ac_id not in set(owned_ids) | set(supporting_ids)
        ]
        selected_ids = {
            criterion.id
            for criterion in [*owned, *supporting, *global_obligations]
            if criterion.id
        }

        lines: list[str] = []
        lines.extend(cls._render_criteria_section("Owned Acceptance Criteria (Mandatory)", owned))
        lines.extend(cls._render_criteria_section("Supporting Acceptance Criteria (Context Only)", supporting))
        lines.extend(cls._render_criteria_section("Global Obligation Acceptance Criteria (Optional Context)", global_obligations))

        selected_scenarios = [
            scenario
            for scenario in test_plan.test_scenarios
            if selected_ids
            and (
                set(scenario.linked_acceptance) & selected_ids
                or set(scenario.refs.acceptance_criterion_ids) & selected_ids
            )
        ]
        if selected_scenarios:
            lines.extend(["## Related Test Scenarios", ""])
            for scenario in selected_scenarios:
                linked = sorted(set(scenario.linked_acceptance) | set(scenario.refs.acceptance_criterion_ids))
                lines.extend(
                    [
                        f"### {scenario.id or scenario.name}",
                        "",
                        f"- Name: {scenario.name}",
                        f"- Linked ACs: {', '.join(ac_id for ac_id in linked if ac_id in selected_ids)}",
                        f"- Expected outcome: {scenario.expected_outcome}",
                        "",
                    ]
                )

        checklist_items = [
            item
            for item in test_plan.checklist_items
            if set(item.refs.acceptance_criterion_ids) & selected_ids
            or any(ac_id in item.text for ac_id in selected_ids)
        ]
        if checklist_items:
            lines.extend(["## Related Verification Checklist", ""])
            lines.extend(f"- {item.text}" for item in checklist_items)
            lines.append("")

        edge_case_items = [
            item
            for item in test_plan.edge_case_items
            if set(item.refs.acceptance_criterion_ids) & selected_ids
            or any(ac_id in item.text for ac_id in selected_ids)
        ]
        if edge_case_items:
            lines.extend(["## Related Edge Cases", ""])
            lines.extend(f"- {item.text}" for item in edge_case_items)
            lines.append("")

        return "\n".join(lines).strip()

    @classmethod
    def _test_plan_excerpt_for_slice(
        cls,
        test_plan_text: str,
        slice_info: TaskPlanningSlice,
        *,
        owned_only: bool = False,
        test_plan_model: TestPlan | None = None,
    ) -> str:
        markdown = cls._normalize_artifact_markdown(test_plan_text, "test-plan")
        test_plan = test_plan_model or cls._parse_test_plan(test_plan_text)
        context_ac_ids = cls._slice_context_acceptance_ids(
            slice_info,
            owned_only=owned_only,
        )
        if test_plan and test_plan.acceptance_criteria:
            structured_excerpt = cls._test_plan_excerpt_from_model(
                test_plan,
                slice_info,
                owned_only=owned_only,
            )
            if structured_excerpt:
                return structured_excerpt
            selected_criteria = [
                criterion
                for criterion in test_plan.acceptance_criteria
                if criterion.id in context_ac_ids
            ]
            selected_scenarios = [
                scenario
                for scenario in test_plan.test_scenarios
                if any(ac_id in context_ac_ids for ac_id in scenario.linked_acceptance)
                or cls._text_matches_slice_trace(
                    "\n".join(
                        [
                            scenario.name,
                            *scenario.preconditions,
                            *scenario.steps,
                            scenario.expected_outcome,
                        ]
                    ),
                    slice_info,
                )
            ]
            checklist = [
                item
                for item in test_plan.verification_checklist
                if any(ac_id in item for ac_id in context_ac_ids)
                or cls._text_matches_slice_trace(item, slice_info)
            ]
            edge_cases = [
                item
                for item in test_plan.edge_cases
                if any(ac_id in item for ac_id in context_ac_ids)
                or cls._text_matches_slice_trace(item, slice_info)
            ]
            filtered = TestPlan(
                overview=test_plan.overview,
                acceptance_criteria=selected_criteria,
                test_scenarios=selected_scenarios,
                verification_checklist=checklist,
                edge_cases=edge_cases,
                mocking_strategy=test_plan.mocking_strategy,
                test_environment=test_plan.test_environment,
                decisions=test_plan.decisions,
                complete=test_plan.complete,
            )
            rendered = to_markdown(filtered).strip()
            if rendered:
                return rendered
        return cls._extract_matching_sections(
            markdown,
            set(context_ac_ids + slice_info.step_ids + slice_info.requirement_ids + slice_info.journey_ids),
            fallback_headings=("## Acceptance Criteria", "## Test Scenarios", "## Verification Checklist", "## Edge Cases"),
        )

    @classmethod
    def _target_slice_bundle(
        cls,
        slug: str,
        slice_info: TaskPlanningSlice,
        target_texts: dict[str, str],
        *,
        owned_only_test_plan: bool = False,
        test_plan_model: TestPlan | None = None,
    ) -> dict[str, str]:
        plan_text = cls._normalize_artifact_markdown(target_texts.get("plan", ""), f"plan:{slug}")
        prd_text = cls._normalize_artifact_markdown(target_texts.get("prd", ""), f"prd:{slug}")
        design_text = cls._normalize_artifact_markdown(target_texts.get("design", ""), f"design:{slug}")
        system_design_text = cls._normalize_artifact_markdown(
            target_texts.get("system-design", ""),
            f"system-design:{slug}",
        )
        test_plan_text = target_texts.get("test-plan", "")
        normalized_plan_text = cls._normalize_plan_markdown_for_slice_derivation(plan_text)

        base_tokens = set(
            slice_info.step_ids
            + slice_info.requirement_ids
            + slice_info.journey_ids
            + cls._slice_context_acceptance_ids(slice_info)
        )
        plan_excerpt = cls._extract_exact_step_sections(
            normalized_plan_text,
            slice_info.step_ids,
        ) or cls._extract_matching_sections(
            normalized_plan_text,
            base_tokens or set(slice_info.step_titles),
            fallback_headings=("## Implementation Steps", "## Journey Verifications", "## Architectural Risks"),
        )
        trace_tokens = base_tokens | cls._extract_trace_tokens(plan_excerpt)
        return {
            "plan": plan_excerpt,
            "prd": cls._extract_matching_sections(
                prd_text,
                trace_tokens,
                fallback_headings=("## Requirements", "## Acceptance Criteria", "## User Journeys"),
            ),
            "design": cls._extract_matching_sections(
                design_text,
                trace_tokens,
                fallback_headings=("## Journey UX Annotations", "## Design System", "## Verifiable States", "## Interaction Patterns", "## Accessibility"),
            ),
            "system-design": cls._extract_matching_sections(
                system_design_text,
                trace_tokens,
                fallback_headings=("## Services", "## Entities", "## API", "## Architecture Decisions", "## Risks"),
            ),
            "test-plan": cls._test_plan_excerpt_for_slice(
                test_plan_text,
                slice_info,
                owned_only=owned_only_test_plan,
                test_plan_model=test_plan_model,
            ),
        }

    @classmethod
    def _feature_constraint_bundle(
        cls,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        slice_info: TaskPlanningSlice,
        target_bundle: dict[str, str],
        broad_artifacts: dict[str, str],
        *,
        mode_label: str,
    ) -> dict[str, str]:
        edge_context = cls._edge_context_for_slug(
            decomposition,
            subfeature.slug,
            allowed_peers=set(workstream.subfeature_slugs) - {subfeature.slug},
        )
        target_tokens = set(
            slice_info.step_ids
            + slice_info.requirement_ids
            + slice_info.journey_ids
            + slice_info.step_titles
        ) | cls._extract_trace_tokens(*target_bundle.values())
        direct_peers = sorted(cls._connected_peer_slugs(decomposition, subfeature.slug))
        return {
            "metadata": "\n".join(
                [
                    "## Target Subfeature Metadata",
                    "",
                    f"- ID: {subfeature.id}",
                    f"- Slug: {subfeature.slug}",
                    f"- Name: {subfeature.name}",
                    f"- Slice: {slice_info.slice_id} — {slice_info.title or 'Task Planning Slice'}",
                    f"- Workstream: {workstream.id} — {workstream.name}",
                    f"- Peer context mode: {mode_label}",
                    f"- Workstream depends on: {', '.join(workstream.depends_on or ['none'])}",
                ]
            ),
            "neighborhood": "\n".join(
                [
                    "## Local Neighborhood",
                    "",
                    f"- Workstream subfeatures: {', '.join(workstream.subfeature_slugs)}",
                    f"- Direct peers: {', '.join(direct_peers) if direct_peers else 'none'}",
                    f"- Slice steps: {', '.join(slice_info.step_ids) or 'whole subfeature'}",
                ]
            ),
            "edges": edge_context,
            "broad-prd": cls._extract_matching_sections(
                cls._normalize_artifact_markdown(broad_artifacts.get("prd:broad", ""), "prd:broad"),
                target_tokens,
                fallback_headings=("## Requirements", "## User Journeys"),
                max_chars=6_000,
            ),
            "broad-design": cls._extract_matching_sections(
                cls._normalize_artifact_markdown(broad_artifacts.get("design:broad", ""), "design:broad"),
                target_tokens,
                fallback_headings=("## Design System", "## Verifiable States", "## Interaction Patterns"),
                max_chars=6_000,
            ),
            "broad-plan": cls._extract_matching_sections(
                cls._normalize_artifact_markdown(broad_artifacts.get("plan:broad", ""), "plan:broad"),
                target_tokens,
                fallback_headings=("## Implementation Steps", "## Architectural Risks"),
                max_chars=6_000,
            ),
        }

    @classmethod
    async def _load_target_texts(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
        sf_upstream: dict[str, dict[str, str]],
    ) -> dict[str, str]:
        backfill_status = await cls._load_backfill_status(runner, feature)
        migrated = cls._slug_is_migrated(backfill_status, slug)
        target_texts = {} if migrated else dict(sf_upstream.get(slug, {}))
        for prefix in ("plan", "prd", "design", "system-design", "test-plan", "decisions"):
            if prefix in target_texts:
                continue
            artifact_key = f"{prefix}:{slug}"
            if migrated:
                target_texts[prefix] = await cls._load_artifact_text_for_planning(
                    runner,
                    feature,
                    artifact_key,
                    backfill_status=backfill_status,
                )
            else:
                target_texts[prefix] = await runner.artifacts.get(artifact_key, feature=feature) or ""
        if migrated and not _extract_ac_ids(target_texts.get("test-plan", "")):
            # W-13 (resume50, settings): a degenerate migrated test-plan sidecar
            # renders 0 acceptance criteria into the slice-planner excerpts, so
            # planners never see (or cite) the AC universe — the same L2 class
            # the contract compile path and the owned-AC reconciliation already
            # guard against. Use the raw markdown twin when it defines ACs.
            # Contract digests are unaffected (the degenerate contract branch
            # loads raw texts itself) and manifest digests never pass through
            # this loader.
            try:
                twin_text = await runner.artifacts.get(f"test-plan:{slug}", feature=feature) or ""
            except Exception:
                twin_text = ""
            twin_ac_count = len(_extract_ac_ids(twin_text))
            if twin_ac_count:
                logger.warning(
                    "DEGENERATE test-plan sidecar for %s in slice-context loading: "
                    "rendered text defines 0 acceptance criteria while the markdown "
                    "twin defines %d — using the markdown twin for planner excerpts",
                    slug,
                    twin_ac_count,
                )
                target_texts["test-plan"] = twin_text
        return target_texts

    @classmethod
    async def _persist_slice_attempt(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        manifest: TaskPlanningSliceManifest,
        attempt: SlicePlanningAttempt,
    ) -> None:
        manifest.attempts.append(attempt)
        await cls._put_artifact(
            runner,
            feature,
            attempt.attempt_key,
            "\n".join(
                [
                    f"# Slice Attempt — {attempt.slice_id}",
                    "",
                    f"- Mode: `{attempt.mode}`",
                    f"- Chosen mode: `{attempt.chosen_mode or attempt.mode}`",
                    f"- Attempt: {attempt.attempt}",
                    f"- Status: {attempt.status}",
                    f"- Actor: `{attempt.actor_name}`" if attempt.actor_name else "",
                    f"- Estimated context bytes: {attempt.estimated_context_bytes}" if attempt.estimated_context_bytes else "",
                    f"- Error: {attempt.error}" if attempt.error else "",
                    "",
                    "## Size Breakdown",
                    "",
                    *[
                        f"- {layer}: {size}"
                        for layer, size in sorted(attempt.size_breakdown.items())
                    ],
                    "",
                    "## Context Paths",
                    "",
                    *[f"- `{path}`" for path in attempt.context_paths],
                ]
            ).strip() + "\n",
        )
        await cls._save_slice_manifest(runner, feature, manifest)

    @staticmethod
    def _slice_fragment_key(slug: str, slice_id: str) -> str:
        return f"dag-fragment:{slug}:{slice_id}"

    @staticmethod
    def _slice_fragment_raw_key(slug: str, slice_id: str) -> str:
        """PRE-validation checkpoint of the planner's slice fragment.

        Persisted the moment the slice planner returns, BEFORE
        ``_validate_slice_fragment`` — so a validation-stage failure (e.g. a
        defective path resolver) never drops the planned fragment. Resume
        adopts it by re-running validation (path resolution onward) instead of
        re-planning the slice."""
        return f"dag-fragment-raw:{slug}:{slice_id}"

    @staticmethod
    def _slice_attempt_key(slug: str, slice_id: str, mode_label: str, attempt: int) -> str:
        return f"dag-fragment-attempt:{slug}:{slice_id}:{mode_label}:{attempt}"

    @staticmethod
    def _safe_task_id_part(value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
        return normalized or "task"

    @classmethod
    def _namespace_slice_task_ids(
        cls,
        dag: ImplementationDAG,
        *,
        slug: str,
        slice_id: str,
    ) -> tuple[ImplementationDAG, bool]:
        """Make task ids stable and unique across independently generated slices."""

        prefix = f"{cls._safe_task_id_part(slug)}-{cls._safe_task_id_part(slice_id)}-"
        mapping: dict[str, str] = {}
        used: set[str] = set()
        changed = False
        for index, task in enumerate(dag.tasks, start=1):
            if task.id.startswith(prefix):
                candidate = task.id
            else:
                candidate = f"{prefix}{cls._safe_task_id_part(task.id)}"
                changed = True
            if candidate in used:
                candidate = f"{candidate}-{index}"
                changed = True
            used.add(candidate)
            mapping[task.id] = candidate

        if not changed:
            return dag, False

        return (
            ImplementationDAG(
                tasks=[
                    task.model_copy(
                        update={
                            "id": mapping[task.id],
                            "dependencies": [
                                mapping.get(dep, dep)
                                for dep in task.dependencies
                            ],
                        }
                    )
                    for task in dag.tasks
                ],
                num_teams=dag.num_teams,
                execution_order=[
                    [mapping.get(task_id, task_id) for task_id in wave]
                    for wave in dag.execution_order
                ],
                requirement_coverage={
                    requirement_id: [
                        mapping.get(task_id, task_id)
                        for task_id in task_ids
                    ]
                    for requirement_id, task_ids in dag.requirement_coverage.items()
                },
                complete=dag.complete,
            ),
            True,
        )

    @classmethod
    def _next_slice_attempt_number(
        cls,
        manifest: TaskPlanningSliceManifest,
        *,
        slice_id: str,
        mode_label: str,
    ) -> int:
        max_attempt = 0
        for attempt in manifest.attempts:
            if attempt.slice_id != slice_id or attempt.mode != mode_label:
                continue
            max_attempt = max(max_attempt, attempt.attempt)
        return max_attempt + 1

    @classmethod
    def _slice_traceability_errors(
        cls,
        slice_info: TaskPlanningSlice,
        tasks: list[ImplementationTask],
        feature_requirement_universe: set[str] | None = None,
    ) -> list[str]:
        """Validate per-task traceability against the slice contract.

        ``feature_requirement_universe``: when provided, an out-of-slice
        requirement id that DOES exist in the feature-wide universe is ACCEPTED
        with a WARN (a cross-slice citation, not a coverage claim — the AC
        coverage audit is unaffected); ids in NO universe still fail."""
        errors: list[str] = []
        normalized_feature_universe = {
            _normalize_id_numeric_segments(requirement_id)
            for requirement_id in (feature_requirement_universe or set())
        }
        require_requirement_ids = bool(slice_info.requirement_ids)
        require_journey_ids = bool(slice_info.journey_ids)
        required_reference_sources = set(slice_info.required_reference_sources)
        seen_reference_sources: set[str] = set()
        for task in tasks:
            if not task.step_ids:
                errors.append(f"{task.id} is missing step_ids")
            if require_requirement_ids and not task.requirement_ids:
                errors.append(f"{task.id} is missing requirement_ids")
            if require_journey_ids and not task.journey_ids:
                errors.append(f"{task.id} is missing journey_ids")
            if not task.reference_material:
                errors.append(f"{task.id} is missing reference_material")
            if not task.acceptance_criteria:
                errors.append(f"{task.id} is missing task-level acceptance_criteria")
            if slice_info.step_ids and not set(task.step_ids) & set(slice_info.step_ids):
                errors.append(
                    f"{task.id} references step_ids {task.step_ids} outside slice {slice_info.step_ids}"
                )
            if slice_info.requirement_ids and not set(task.requirement_ids).issubset(set(slice_info.requirement_ids)):
                out_of_slice = sorted(set(task.requirement_ids) - set(slice_info.requirement_ids))
                feature_valid = [
                    requirement_id
                    for requirement_id in out_of_slice
                    if _normalize_id_numeric_segments(requirement_id) in normalized_feature_universe
                ]
                nowhere_valid = sorted(set(out_of_slice) - set(feature_valid))
                if feature_valid:
                    logger.warning(
                        "%s cites out-of-slice but feature-valid requirement_ids %s "
                        "(cross-slice citation accepted; slice scope %s)",
                        task.id, feature_valid, slice_info.requirement_ids,
                    )
                if nowhere_valid:
                    errors.append(
                        f"{task.id} references requirement_ids {nowhere_valid} outside slice {slice_info.requirement_ids}"
                    )
            if slice_info.journey_ids and not set(task.journey_ids).issubset(set(slice_info.journey_ids)):
                errors.append(
                    f"{task.id} references journey_ids {task.journey_ids} outside slice {slice_info.journey_ids}"
                )
            for reference in task.reference_material:
                seen_reference_sources.add(cls._reference_source_family(reference.source))
        missing_reference_sources = sorted(required_reference_sources - seen_reference_sources)
        for source_family in missing_reference_sources:
            errors.append(
                f"slice {slice_info.slice_id} is missing reference_material from required source family {source_family}"
            )
        return errors

    @classmethod
    async def _load_test_plan_model_for_reconciliation(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
    ) -> TestPlan | None:
        if not hasattr(runner, "artifacts"):
            return None
        sidecar = None
        try:
            sidecar = await load_structured_artifact(runner, feature, f"test-plan:{slug}")
        except Exception:
            logger.debug("Unable to load structured test plan for %s", slug, exc_info=True)
        if sidecar is not None and any(
            criterion.id for criterion in sidecar.content.acceptance_criteria
        ):
            return sidecar.content
        try:
            text = await runner.artifacts.get(f"test-plan:{slug}", feature=feature) or ""
        except Exception:
            return sidecar.content if sidecar is not None else None
        parsed = cls._parse_test_plan(text)
        if sidecar is None:
            return parsed
        parsed_ac_count = sum(
            1
            for criterion in (parsed.acceptance_criteria if parsed is not None else [])
            if criterion.id
        )
        if parsed_ac_count == 0:
            return sidecar.content
        # Degenerate migrated sidecar (zero acceptance criteria while the
        # markdown twin defines some): an empty canonical AC universe would
        # refuse every owned-AC reconciliation and fail the slice — the same
        # class the contract compile path already guards against.
        logger.warning(
            "DEGENERATE test-plan sidecar for %s in owned-AC reconciliation: "
            "sidecar yields 0 acceptance criteria while the markdown twin "
            "defines %d — reconciling against the markdown twin instead",
            slug,
            parsed_ac_count,
        )
        return parsed

    @classmethod
    def _test_plan_criterion_reference_content(
        cls,
        criterion: TestAcceptanceCriterion,
    ) -> str:
        trace_ids = cls._criterion_trace_ids(criterion)
        trace_parts = sorted(
            {
                *trace_ids["requirements"],
                *trace_ids["journeys"],
                *trace_ids["steps"],
                *trace_ids["decisions"],
                *trace_ids["nfrs"],
                *criterion.refs.verifiable_state_ids,
            }
        )
        return "\n".join(
            line
            for line in [
                f"Acceptance criterion: {criterion.id}",
                f"Description: {criterion.description}" if criterion.description else "",
                f"Trace: {', '.join(trace_parts)}" if trace_parts else "",
                f"Verification method: {criterion.verification_method}" if criterion.verification_method else "",
                f"Pass condition: {criterion.pass_condition}" if criterion.pass_condition else "",
            ]
            if line
        )

    @classmethod
    def _acceptance_criterion_description(
        cls,
        ac_id: str,
        criterion: TestAcceptanceCriterion | None,
    ) -> str:
        if criterion is None:
            return f"{ac_id}: satisfy the canonical test-plan acceptance criterion for this slice."
        description = criterion.description.strip()
        pass_condition = criterion.pass_condition.strip()
        parts = [f"{ac_id}: {description}" if description else ac_id]
        if pass_condition:
            parts.append(f"Pass: {pass_condition}")
        return " ".join(parts)

    @staticmethod
    def _task_execution_order_map(dag: ImplementationDAG) -> dict[str, int]:
        order: dict[str, int] = {}
        for wave_index, wave in enumerate(dag.execution_order):
            for task_id in wave:
                order.setdefault(task_id, wave_index)
        return order

    @classmethod
    def _score_task_for_acceptance_criterion(
        cls,
        task: ImplementationTask,
        slice_info: TaskPlanningSlice,
        criterion: TestAcceptanceCriterion | None,
    ) -> int:
        score = 0
        task_steps = set(task.step_ids)
        task_requirements = set(task.requirement_ids)
        task_journeys = set(task.journey_ids)
        if task_steps & set(slice_info.step_ids):
            score += 100
        if task_requirements & set(slice_info.requirement_ids):
            score += 25
        if task_journeys & set(slice_info.journey_ids):
            score += 25
        if criterion is None:
            return score

        trace_ids = cls._criterion_trace_ids(criterion)
        if task_requirements & trace_ids["requirements"]:
            score += 80
        if task_journeys & trace_ids["journeys"]:
            score += 70
        if task_steps & trace_ids["steps"]:
            score += 70
        task_text = "\n".join(
            [
                task.name,
                task.description,
                *task.counterexamples,
                *[criterion.description for criterion in task.acceptance_criteria],
                *[reference.content for reference in task.reference_material],
            ]
        )
        if criterion.id and criterion.id in task_text:
            score += 10
        return score

    @classmethod
    def _best_task_for_acceptance_criterion(
        cls,
        dag: ImplementationDAG,
        slice_info: TaskPlanningSlice,
        criterion: TestAcceptanceCriterion | None,
    ) -> ImplementationTask:
        execution_order = cls._task_execution_order_map(dag)
        task_positions = {task.id: index for index, task in enumerate(dag.tasks)}
        return max(
            dag.tasks,
            key=lambda task: (
                cls._score_task_for_acceptance_criterion(task, slice_info, criterion),
                -execution_order.get(task.id, len(dag.tasks)),
                -task_positions.get(task.id, len(dag.tasks)),
            ),
        )

    @staticmethod
    def _recompute_requirement_coverage(dag: ImplementationDAG) -> ImplementationDAG:
        requirement_coverage: dict[str, list[str]] = {}
        for task in dag.tasks:
            for requirement_id in task.requirement_ids:
                bucket = requirement_coverage.setdefault(requirement_id, [])
                if task.id not in bucket:
                    bucket.append(task.id)
        return dag.model_copy(update={"requirement_coverage": requirement_coverage})

    @classmethod
    def _score_task_for_requirement_id(
        cls,
        task: ImplementationTask,
        slice_info: TaskPlanningSlice,
        requirement_id: str,
    ) -> int:
        score = 0
        if set(task.step_ids) & set(slice_info.step_ids):
            score += 100
        if set(task.journey_ids) & set(slice_info.journey_ids):
            score += 50
        if set(task.requirement_ids) & set(slice_info.requirement_ids):
            score += 25
        task_text = "\n".join(
            [
                task.name,
                task.description,
                *task.counterexamples,
                *[criterion.description for criterion in task.acceptance_criteria],
                *[reference.content for reference in task.reference_material],
            ]
        )
        if requirement_id in task_text:
            score += 200
        return score

    @classmethod
    def _best_task_for_requirement_id(
        cls,
        dag: ImplementationDAG,
        slice_info: TaskPlanningSlice,
        requirement_id: str,
    ) -> ImplementationTask:
        execution_order = cls._task_execution_order_map(dag)
        task_positions = {task.id: index for index, task in enumerate(dag.tasks)}
        return max(
            dag.tasks,
            key=lambda task: (
                cls._score_task_for_requirement_id(task, slice_info, requirement_id),
                -execution_order.get(task.id, len(dag.tasks)),
                -task_positions.get(task.id, len(dag.tasks)),
            ),
        )

    @classmethod
    def _reconcile_missing_slice_requirement_ids(
        cls,
        slice_info: TaskPlanningSlice,
        dag: ImplementationDAG,
    ) -> tuple[ImplementationDAG, list[str]]:
        if not slice_info.requirement_ids or not dag.tasks:
            return dag, []

        cited_requirement_ids = {
            requirement_id
            for task in dag.tasks
            for requirement_id in task.requirement_ids
            if requirement_id
        }
        missing_requirement_ids = sorted(set(slice_info.requirement_ids) - cited_requirement_ids)
        if not missing_requirement_ids:
            return cls._recompute_requirement_coverage(dag), []

        reconciled = dag.model_copy(deep=True)
        for requirement_id in missing_requirement_ids:
            task = cls._best_task_for_requirement_id(
                reconciled,
                slice_info,
                requirement_id,
            )
            if requirement_id not in task.requirement_ids:
                task.requirement_ids.append(requirement_id)
        return cls._recompute_requirement_coverage(reconciled), missing_requirement_ids

    @classmethod
    async def _reconcile_missing_owned_acceptance_gates(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
        slice_info: TaskPlanningSlice,
        dag: ImplementationDAG,
        missing_ac_ids: list[str],
    ) -> tuple[ImplementationDAG, bool]:
        if not missing_ac_ids or not dag.tasks:
            return dag, False

        test_plan = await cls._load_test_plan_model_for_reconciliation(runner, feature, slug)
        criterion_by_id = {
            criterion.id: criterion
            for criterion in (test_plan.acceptance_criteria if test_plan is not None else [])
            if criterion.id
        }
        if test_plan is not None:
            missing_from_canonical = sorted(set(missing_ac_ids) - set(criterion_by_id))
            if missing_from_canonical:
                logger.warning(
                    "Slice %s/%s: cannot reconcile owned ACs absent from canonical test-plan sidecar: %s",
                    slug,
                    slice_info.slice_id,
                    ", ".join(missing_from_canonical),
                )
                return dag, False

        reconciled = dag.model_copy(deep=True)
        changed = False
        for ac_id in sorted(dict.fromkeys(missing_ac_ids)):
            criterion = criterion_by_id.get(ac_id)
            task = cls._best_task_for_acceptance_criterion(
                reconciled,
                slice_info,
                criterion,
            )
            if ac_id not in task.verification_gates:
                task.verification_gates.append(ac_id)
                changed = True
            if not any(ac_id in item.description for item in task.acceptance_criteria):
                task.acceptance_criteria.append(
                    TaskAcceptanceCriterion(
                        description=cls._acceptance_criterion_description(ac_id, criterion),
                        not_criteria=(
                            "Do not omit this canonical verification gate when implementing "
                            f"slice {slice_info.slice_id}."
                        ),
                    )
                )
                changed = True
            if criterion is not None and not any(
                reference.source == f"Test Plan {ac_id}" or ac_id in reference.content
                for reference in task.reference_material
            ):
                task.reference_material.append(
                    TaskReference(
                        source=f"Test Plan {ac_id}",
                        content=cls._test_plan_criterion_reference_content(criterion),
                    )
                )
                changed = True

        if changed:
            logger.warning(
                "Slice %s/%s: reconciled missing owned AC gates before persistence: %s",
                slug,
                slice_info.slice_id,
                ", ".join(sorted(missing_ac_ids)),
            )
        return reconciled, changed

    @classmethod
    async def _validate_slice_fragment(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
        slice_info: TaskPlanningSlice,
        dag: ImplementationDAG,
        *,
        context_package: ContextPackage | None = None,
    ) -> tuple[ImplementationDAG | None, str | None, bool]:
        tasks = dag.tasks
        if not tasks:
            return None, "agent returned no tasks for this slice", True
        try:
            normalized, normalized_flag = cls._normalize_subfeature_execution_order(dag)
        except ValueError as exc:
            return None, str(exc), True
        if normalized_flag:
            logger.warning("Slice %s/%s: normalized execution order before persistence", slug, slice_info.slice_id)
        normalized = cls._hydrate_slice_reference_material(
            normalized,
            slice_info,
            context_package,
        )
        contract = (
            await cls._load_subfeature_planning_contract(runner, feature, slug)
            if hasattr(runner, "artifacts")
            else None
        )
        feature_requirement_universe = (
            set(contract.requirement_universe) if contract is not None else set()
        )
        traceability_errors = cls._slice_traceability_errors(
            slice_info,
            normalized.tasks,
            feature_requirement_universe=feature_requirement_universe,
        )
        if traceability_errors:
            return None, "; ".join(traceability_errors), True
        normalized, _path_rewrites, path_errors = await cls._resolve_dag_paths_for_persistence(
            runner,
            feature,
            normalized,
            context=f"slice {slug}/{slice_info.slice_id}",
            resolution_key=f"{slug}:{slice_info.slice_id}",
        )
        if path_errors:
            return None, "; ".join(path_errors), True
        normalized, reconciled_requirement_ids = cls._reconcile_missing_slice_requirement_ids(
            slice_info,
            normalized,
        )
        if reconciled_requirement_ids:
            logger.warning(
                "Slice %s/%s: reconciled missing requirement coverage before persistence: %s",
                slug,
                slice_info.slice_id,
                ", ".join(reconciled_requirement_ids),
            )
        obligation_ac_ids = cls._slice_owned_acceptance_ids(slice_info)
        allowed_gate_ids = set(obligation_ac_ids) | set(slice_info.global_obligation_ac_ids)
        if allowed_gate_ids:
            cited = {
                gate
                for task in normalized.tasks
                for gate in task.verification_gates
                if gate
            }
            unknown = sorted(cited - allowed_gate_ids)
            missing = sorted(set(obligation_ac_ids) - cited) if slice_info.strict_acceptance_criteria else []
            messages: list[str] = []
            if unknown:
                messages.append(
                    "fragment cites acceptance criteria outside slice scope: " + ", ".join(unknown)
                )
            if missing:
                normalized, reconciled = await cls._reconcile_missing_owned_acceptance_gates(
                    runner,
                    feature,
                    slug,
                    slice_info,
                    normalized,
                    missing,
                )
                if reconciled:
                    cited = {
                        gate
                        for task in normalized.tasks
                        for gate in task.verification_gates
                        if gate
                    }
                    missing = sorted(set(obligation_ac_ids) - cited) if slice_info.strict_acceptance_criteria else []
                if missing:
                    messages.append(
                        "fragment leaves slice acceptance criteria uncovered: " + ", ".join(missing)
                    )
            if messages:
                return None, "; ".join(messages), True
        return normalized, None, False

    @staticmethod
    def _canonicalize_dag_paths_for_persistence(
        dag: ImplementationDAG,
        *,
        context: str,
    ) -> tuple[ImplementationDAG, list[dict[str, str]], list[str]]:
        """Legacy static DAG-path shim (the flag-OFF fallback).

        Preserved byte-for-byte from the pre-agentic seam: callable only when
        ``dag_path_agentic_resolver_enabled()`` is false (emergency rollback)."""
        if not dag_path_canonicalization_enabled():
            return dag, [], []
        canonical, rewrites = canonicalize_implementation_dag(dag)
        records = dag_path_rewrites_to_records(rewrites)
        if records:
            logger.warning(
                "%s: canonicalized %d retired backend DAG path(s) before persistence",
                context,
                len(records),
            )
        remaining = find_retired_backend_path_references(canonical.tasks)
        errors = [
            (
                f"{ref.task_id} retains retired backend path {ref.original!r} "
                f"in {ref.field}; expected {ref.canonical!r}"
            )
            for ref in remaining
        ]
        return canonical, records, errors

    @staticmethod
    def _resolver_workspace_root(
        runner: WorkflowRunner,
        feature: Feature,
    ) -> str:
        """Resolve the feature's on-disk per-repo checkout root for path resolution.

        A task path resolves to ``<repos_root>/<task.repo_path>/<file_scope path>``
        (the feature checkout under ``.iriai/features/<slug>/repos``, NOT the bare
        workspace). Returns ``""`` when the checkout isn't present (e.g. planning
        before checkout, or unit tests), so the prepass skips rather than
        mis-resolving against the wrong base."""
        return feature_repos_root(runner, feature)

    @classmethod
    async def _load_upstream_planned_file_paths(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        exclude_slug: str,
    ) -> set[str]:
        """NET-NEW file paths planned by OTHER subfeatures' persisted fragments.

        Subfeatures legitimately modify files an EARLIER subfeature's DAG
        creates (every SF appends endpoints to S1's router file). Those paths
        do not exist on disk during planning and are invisible to the current
        fragment's own planned-new set, so the resolver flags them ambiguous
        (N-8, resume49: handoff slice-1/2 + settings slice-7 all failed on
        S1's submittal_management.py). Aggregate the create-action paths of
        every persisted sibling fragment so cross-SF dependencies ground the
        same way intra-fragment ones do. Tolerant: any missing/unparseable
        manifest or fragment narrows the set (old behavior), never raises."""
        planned: set[str] = set()
        try:
            decomp_text = await runner.artifacts.get("decomposition", feature=feature) or ""
            decomposition = SubfeatureDecomposition.model_validate(_json.loads(decomp_text))
        except Exception:
            logger.debug("upstream planned-paths: decomposition unavailable", exc_info=True)
            return planned
        for subfeature in getattr(decomposition, "subfeatures", []) or []:
            slug = getattr(subfeature, "slug", "")
            if not slug or slug == exclude_slug:
                continue
            try:
                manifest = await cls._load_slice_manifest(runner, feature, slug)
            except Exception:
                continue
            if manifest is None:
                continue
            for slice_info in manifest.slices:
                fragment_key = cls._slice_fragment_key(slug, slice_info.slice_id)
                try:
                    fragment_text = await runner.artifacts.get(fragment_key, feature=feature)
                    if not fragment_text:
                        continue
                    fragment = ImplementationDAG.model_validate_json(fragment_text)
                except Exception:
                    continue
                planned |= planned_new_file_paths(fragment)
        if planned:
            logger.info(
                "upstream planned-paths for %s: %d cross-subfeature planned file form(s)",
                exclude_slug,
                len(planned),
            )
        return planned

    @classmethod
    async def _resolve_dag_paths_for_persistence(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        dag: ImplementationDAG,
        *,
        context: str,
        resolution_key: str,
    ) -> tuple[ImplementationDAG, list[dict[str, str]], list[str]]:
        """Validate/correct DAG task paths against the real repo before persistence.

        Default (agentic) path: a cheap deterministic existence prepass gates a
        single read-only resolver Ask. The resolution is persisted under
        ``dag-path-resolution:{resolution_key}`` so resume reuses it (replay
        stability — the agent is never re-dispatched). Ambiguous decisions
        fail-safe (non-retryable) rather than guess.

        Flag-OFF path: preserves the exact legacy static-shim behavior so the
        flag is a true emergency rollback. Returns a 3-tuple
        ``(dag, records, errors)`` so all call sites unpack unchanged."""
        if not dag_path_agentic_resolver_enabled():
            return cls._canonicalize_dag_paths_for_persistence(dag, context=context)

        repos_root = cls._resolver_workspace_root(runner, feature)
        if not repos_root:
            # No on-disk checkout to resolve against — skip rather than mis-resolve
            # (e.g. planning before the feature's repos are checked out).
            return dag, [], []
        unresolved = unresolved_dag_paths(dag, repos_root)
        if not unresolved:
            # Existence-prepass skip: every path already resolves on disk.
            return dag, [], []

        exclude_slug = resolution_key.split(":", 1)[0]
        extra_planned = await cls._load_upstream_planned_file_paths(
            runner, feature, exclude_slug,
        )
        workspace_root = feature_workspace_root(runner, feature)

        resolution = await cls._load_or_dispatch_path_resolution(
            runner,
            feature,
            dag,
            unresolved=unresolved,
            context=context,
            resolution_key=resolution_key,
            repos_root=repos_root,
            extra_planned=extra_planned,
        )

        try:
            corrected, rewrites = apply_path_resolution(
                dag, resolution, repos_root=repos_root,
                extra_planned=extra_planned,
                workspace_root=workspace_root,
            )
        except AmbiguousDagPath as exc:
            logger.warning(
                "%s: agentic DAG path resolution is ambiguous (non-retryable): %s",
                context,
                exc,
            )
            return dag, [], [str(exc)]

        records = dag_path_rewrites_to_records(rewrites)
        if records:
            logger.warning(
                "%s: agentic resolver corrected %d DAG path(s) before persistence",
                context,
                len(records),
            )
        return corrected, records, []

    @classmethod
    async def _load_or_dispatch_path_resolution(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        dag: ImplementationDAG,
        *,
        unresolved: list[dict[str, str]],
        context: str,
        resolution_key: str,
        repos_root: str,
        extra_planned: set[str] | None = None,
    ) -> DagPathResolution:
        """Return the persisted resolution (replay-stable) or dispatch the agent once."""
        artifact_key = f"dag-path-resolution:{resolution_key}"
        existing = await get_existing_artifact(runner, feature, artifact_key)
        if existing:
            try:
                persisted = DagPathResolution.model_validate_json(existing)
            except Exception:
                logger.warning(
                    "%s: persisted %s is not valid DagPathResolution JSON; re-dispatching",
                    context,
                    artifact_key,
                    exc_info=True,
                )
            else:
                # Replay-stable ONLY for the fragment it was produced for: a
                # stale resolution (e.g. after a slice re-plan) that does not
                # cover the CURRENT unresolved set would silently skip the new
                # paths — re-dispatch instead.
                if resolution_covers_unresolved(persisted, unresolved):
                    return persisted
                logger.warning(
                    "%s: persisted %s does not cover the current unresolved "
                    "path set (stale after re-plan); re-dispatching the resolver",
                    context,
                    artifact_key,
                )

        actor = AgentActor(
            name=f"dag-path-resolver-{resolution_key}",
            role=dag_path_resolver_role,
            context_keys=[],
        )
        prompt = build_dag_path_resolver_prompt(
            dag, unresolved, repos_root, extra_planned=extra_planned,
        )
        await _clear_agent_session(runner, actor, feature)
        resolution: DagPathResolution = await runner.run(
            Ask(
                actor=actor,
                prompt=prompt,
                output_type=DagPathResolution,
            ),
            feature,
            phase_name=getattr(cls, "name", "task_planning"),
        )
        await cls._put_artifact(
            runner,
            feature,
            artifact_key,
            resolution.model_dump_json(indent=2),
        )
        return resolution

    @classmethod
    async def _merge_slice_fragments(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
        manifest: TaskPlanningSliceManifest,
    ) -> ImplementationDAG:
        fragment_dags: list[ImplementationDAG] = []
        for slice_info in manifest.slices:
            fragment_key = cls._slice_fragment_key(slug, slice_info.slice_id)
            fragment_text = await runner.artifacts.get(fragment_key, feature=feature)
            if not fragment_text:
                raise RuntimeError(f"missing fragment {fragment_key}")
            fragment_dags.append(ImplementationDAG.model_validate_json(fragment_text))

        tasks: list[ImplementationTask] = []
        requirement_coverage: dict[str, list[str]] = {}
        seen_task_ids: set[str] = set()
        for fragment in fragment_dags:
            for task in fragment.tasks:
                if task.id in seen_task_ids:
                    raise RuntimeError(f"duplicate task id across slices: {task.id}")
                seen_task_ids.add(task.id)
                tasks.append(task)
                for requirement_id in task.requirement_ids:
                    bucket = requirement_coverage.setdefault(requirement_id, [])
                    if task.id not in bucket:
                        bucket.append(task.id)

        merged = ImplementationDAG(
            tasks=tasks,
            num_teams=max((fragment.num_teams for fragment in fragment_dags), default=0),
            execution_order=[task_ids for fragment in fragment_dags for task_ids in fragment.execution_order],
            requirement_coverage=requirement_coverage,
            complete=True,
        )
        normalized, _normalized_flag = cls._normalize_subfeature_execution_order(merged)
        normalized, _path_rewrites, path_errors = await cls._resolve_dag_paths_for_persistence(
            runner,
            feature,
            normalized,
            context=f"subfeature DAG {slug}",
            resolution_key=f"{slug}:merged",
        )
        if path_errors:
            raise RuntimeError(
                "unresolved DAG path references remain after resolution: "
                + "; ".join(path_errors)
            )
        return normalized

    @classmethod
    async def _validate_requirement_coverage(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
        sf_tasks: list[ImplementationTask],
    ) -> RequirementCoverageResult:
        result = RequirementCoverageResult(slug=slug)
        contract = await cls._load_subfeature_planning_contract(runner, feature, slug)
        if contract is None:
            return result
        required_ids: set[str] = set()
        for step_contract in contract.step_contracts:
            required_ids.update(step_contract.requirement_ids)
        covered_ids = {
            requirement_id
            for task in sf_tasks
            for requirement_id in task.requirement_ids
        }
        result.missing_requirement_ids = sorted(required_ids - covered_ids)
        return result

    @staticmethod
    def _dag_gate_sort_key(value: str) -> tuple[Any, ...]:
        parts: list[Any] = []
        for part in re.split(r"(\d+)", value):
            if not part:
                continue
            parts.append(int(part) if part.isdigit() else part.lower())
        return tuple(parts)

    @classmethod
    def _strip_generated_root_dag_gate_surfaces(cls, compiled_text: str) -> str:
        pattern = re.compile(
            rf"\n*{re.escape(_ROOT_DAG_GATE_SURFACES_START)}.*?"
            rf"{re.escape(_ROOT_DAG_GATE_SURFACES_END)}\n*",
            re.DOTALL,
        )
        return pattern.sub("\n", compiled_text).rstrip()

    @classmethod
    def _recover_requirement_coverage_from_dag_tasks(
        cls,
        dag: ImplementationDAG,
    ) -> dict[str, list[str]]:
        recovered: dict[str, list[str]] = {}
        for task in dag.tasks:
            text_parts = [
                task.name,
                task.description,
                " ".join(task.requirement_ids),
                " ".join(task.step_ids),
            ]
            for reference in task.reference_material:
                text_parts.extend([reference.source, reference.content])
            requirement_ids = sorted(
                set(_REQ_ID_PATTERN.findall("\n".join(text_parts))),
                key=cls._dag_gate_sort_key,
            )
            for requirement_id in requirement_ids:
                bucket = recovered.setdefault(requirement_id, [])
                if task.id not in bucket:
                    bucket.append(task.id)
        return recovered

    @classmethod
    async def _build_root_dag_gate_surfaces(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
    ) -> str:
        feature_requirement_ids = sorted(
            {
                requirement_id
                for subfeature in decomposition.subfeatures
                for requirement_id in subfeature.requirement_ids
            },
            key=cls._dag_gate_sort_key,
        )
        forward_map: dict[str, list[tuple[str, str, list[str]]]] = {
            requirement_id: [] for requirement_id in feature_requirement_ids
        }
        reverse_rows: list[tuple[str, str, list[str], list[str]]] = []
        unmapped_local_requirements: list[tuple[str, str, list[str]]] = []

        for subfeature in decomposition.subfeatures:
            dag_text = await runner.artifacts.get(
                f"dag:{subfeature.slug}",
                feature=feature,
            ) or ""
            if not dag_text:
                raise RuntimeError(
                    f"Cannot build root DAG gate coverage: missing dag:{subfeature.slug}"
                )
            try:
                dag = ImplementationDAG.model_validate_json(dag_text)
            except Exception as exc:
                raise RuntimeError(
                    f"Cannot build root DAG gate coverage: dag:{subfeature.slug} "
                    f"is not valid ImplementationDAG JSON: {exc}"
                ) from exc

            requirement_coverage = {
                requirement_id: list(task_ids)
                for requirement_id, task_ids in dag.requirement_coverage.items()
            }
            if not requirement_coverage:
                requirement_coverage = cls._recover_requirement_coverage_from_dag_tasks(dag)

            parent_ids = sorted(
                set(subfeature.requirement_ids),
                key=cls._dag_gate_sort_key,
            )
            for local_requirement_id, raw_task_ids in sorted(
                requirement_coverage.items(),
                key=lambda item: cls._dag_gate_sort_key(item[0]),
            ):
                task_ids = sorted(
                    dict.fromkeys(raw_task_ids),
                    key=cls._dag_gate_sort_key,
                )
                reverse_rows.append(
                    (subfeature.slug, local_requirement_id, parent_ids, task_ids)
                )
                if parent_ids:
                    for parent_id in parent_ids:
                        forward_map.setdefault(parent_id, []).append(
                            (subfeature.slug, local_requirement_id, task_ids)
                        )
                else:
                    unmapped_local_requirements.append(
                        (subfeature.slug, local_requirement_id, task_ids)
                    )

        uncovered_feature_requirements = [
            requirement_id
            for requirement_id in feature_requirement_ids
            if not any(task_ids for _slug, _local_id, task_ids in forward_map.get(requirement_id, []))
        ]

        lines = [
            _ROOT_DAG_GATE_SURFACES_START,
            "## Aggregated Requirement Coverage (feature-level)",
            "",
            "Generated deterministically from `decomposition-structured` and "
            "the latest per-subfeature `dag:{slug}` JSON rows. This section is "
            "a root DAG review surface only; it does not modify per-subfeature "
            "DAG schemas.",
            "",
            "### Forward Map: feature requirement/NFR -> SF-local REQs -> task IDs",
            "",
        ]
        if forward_map:
            for requirement_id in sorted(forward_map, key=cls._dag_gate_sort_key):
                rows = forward_map[requirement_id]
                if not rows:
                    lines.append(f"- `{requirement_id}` -> _uncovered_")
                    continue
                lines.append(f"- `{requirement_id}`")
                for slug, local_requirement_id, task_ids in rows:
                    task_list = ", ".join(f"`{task_id}`" for task_id in task_ids) or "_no tasks_"
                    lines.append(
                        f"  - `{slug}` / `{local_requirement_id}` -> {task_list}"
                    )
        else:
            lines.append("- _No feature-level requirements were found in decomposition._")

        lines.extend(
            [
                "",
                "### Reverse Map: SF-local REQ -> feature-level parent + owning SF slug",
                "",
            ]
        )
        if reverse_rows:
            for slug, local_requirement_id, parent_ids, task_ids in sorted(
                reverse_rows,
                key=lambda row: (
                    cls._dag_gate_sort_key(row[0]),
                    cls._dag_gate_sort_key(row[1]),
                ),
            ):
                parents = ", ".join(f"`{item}`" for item in parent_ids) or "_none_"
                tasks = ", ".join(f"`{item}`" for item in task_ids) or "_no tasks_"
                lines.append(
                    f"- `{slug}` / `{local_requirement_id}` -> parents: {parents}; tasks: {tasks}"
                )
        else:
            lines.append("- _No local requirement coverage rows were found._")

        lines.extend(
            [
                "",
                "### Coverage Assertion",
                "",
                f"- zero_uncovered: {'true' if not uncovered_feature_requirements else 'false'}",
                "- uncovered_feature_requirements: "
                + (
                    "[]"
                    if not uncovered_feature_requirements
                    else ", ".join(f"`{item}`" for item in uncovered_feature_requirements)
                ),
            ]
        )
        if unmapped_local_requirements:
            lines.extend(["", "### Unmapped Local Requirements", ""])
            for slug, local_requirement_id, task_ids in unmapped_local_requirements:
                tasks = ", ".join(f"`{item}`" for item in task_ids) or "_no tasks_"
                lines.append(f"- `{slug}` / `{local_requirement_id}` -> {tasks}")

        # Contract waivers (operator-approved): surface the ACTIVE waiver set
        # so the gate reviewer never files coverage findings against ACs the
        # operator already waived (churn / digest-stall class).
        lines.extend(
            [
                "",
                "## Contract Waivers (operator-approved)",
                "",
                "Acceptance criteria listed below are operator-waived "
                "(waivers-as-decisions) and EXCLUDED from coverage "
                "obligations. Do NOT file findings demanding task coverage, "
                "stub tasks, or verification_gates for them.",
                "",
            ]
        )
        waiver_rows: list[str] = []
        for subfeature in decomposition.subfeatures:
            slug = (subfeature.slug or "").strip()
            if not slug:
                continue
            contract_waived: list[str] = []
            contract_text = await runner.artifacts.get(
                f"dag-contract:{slug}", feature=feature
            )
            if contract_text:
                try:
                    contract_waived = SubfeaturePlanningContract.model_validate_json(
                        contract_text
                    ).waived_ac_ids
                except Exception:
                    logger.debug(
                        "dag-contract:%s unparsable while collecting gate waiver surfaces",
                        slug,
                        exc_info=True,
                    )
            store_waived = await _load_planning_waivers(runner, feature, slug)
            for ac_id in sorted(set(contract_waived), key=cls._dag_gate_sort_key):
                waiver_rows.append(f"- `{ac_id}` (`{slug}`) — source: dag-contract waived_ac_ids")
            for ac_id in sorted(
                set(store_waived) - set(contract_waived), key=cls._dag_gate_sort_key
            ):
                waiver_rows.append(
                    f"- `{ac_id}` (`{slug}`) — source: planning-waivers:{slug} store key"
                )
        lines.extend(waiver_rows or ["- _No contract waivers recorded._"])

        # Operator-pinned packing envelope: the reviewer must verify against
        # the same envelope the authoring agents were given (5eaf2dc loader).
        envelope_section = await _load_dag_packing_envelope_section(runner, feature)
        if envelope_section:
            lines.extend(["", envelope_section.rstrip()])

        if cls._sf14_surfaces_apply(decomposition):
            lines.extend(
                [
                    "",
                    "## SF-14 Revisit Checkpoints",
                    "",
                    "SF-14 / `review-phase-views` default-variant implementation is "
                    "explicitly linked to the gate-review revisit decision before "
                    "hardening the broader bugflow/Kanban variants.",
                    "",
                    "- Revisit anchors: `D-GR-DAG-1`, `REVISIT-bugflow-kanban`, `D-883`, `D-887`, `D-7`",
                    "",
                ]
            )
            for task_id in _SF14_DEFAULT_VARIANT_TASK_IDS:
                lines.extend(
                    [
                        f"- `{task_id}`",
                        "  - revisit_checkpoint: D-GR-DAG-1",
                        "  - revisit_note: Revisit `REVISIT-bugflow-kanban` and related "
                        "`D-883`, `D-887`, `D-7` decisions before broadening beyond "
                        "the default variant.",
                    ]
                )
        lines.append(_ROOT_DAG_GATE_SURFACES_END)
        return "\n".join(lines).rstrip() + "\n"

    @classmethod
    def _sf14_surfaces_apply(cls, decomposition: SubfeatureDecomposition) -> bool:
        """The SF-14 revisit surfaces are pinned to PRIOR-FEATURE task ids;
        emit/require them only when this feature's decomposition actually
        contains the owning subfeature (derived from the pinned task-id
        prefixes — no project tokens beyond the existing pins)."""
        slugs = {
            (subfeature.slug or "").strip()
            for subfeature in decomposition.subfeatures
        }
        slugs.discard("")
        return any(
            task_id.startswith(slug + "-")
            for slug in slugs
            for task_id in _SF14_DEFAULT_VARIANT_TASK_IDS
        )

    @classmethod
    def _validate_root_dag_gate_surfaces(
        cls,
        compiled_text: str,
        *,
        expect_sf14_surfaces: bool = True,
    ) -> None:
        required_tokens = [
            "## Aggregated Requirement Coverage (feature-level)",
            "zero_uncovered:",
            "uncovered_feature_requirements",
            "## Contract Waivers (operator-approved)",
        ]
        if expect_sf14_surfaces:
            required_tokens.extend(
                [
                    "D-GR-DAG-1",
                    "REVISIT-bugflow-kanban",
                    *_SF14_DEFAULT_VARIANT_TASK_IDS,
                ]
            )
        missing = [token for token in required_tokens if token not in compiled_text]
        if missing:
            raise RuntimeError(
                "Compiled DAG gate surface validation failed; missing token(s): "
                + ", ".join(missing)
            )

    @classmethod
    def _root_dag_header_scope(cls, compiled_text: str) -> str:
        # Generic subfeature-boundary probes (first SF heading or SF marker of
        # ANY slug) — the prior literal probes were prior-feature artifacts
        # ('Cluster 1' / 'vscode-fork-shell') and degenerated to whole-doc
        # scope on other features.
        markers = [
            marker
            for token in ("\n## Subfeature:", "\n<!-- SF: ")
            if (marker := compiled_text.find(token)) >= 0
        ]
        if not markers:
            generated_start = compiled_text.find(_ROOT_DAG_GATE_SURFACES_START)
            if generated_start >= 0:
                return compiled_text[:generated_start]
            return compiled_text
        return compiled_text[: min(markers)]

    @classmethod
    def _validate_root_dag_header_consistency(cls, compiled_text: str) -> None:
        header = cls._root_dag_header_scope(compiled_text)
        hits = [
            pattern
            for pattern in _ROOT_DAG_FORBIDDEN_HEADER_PATTERNS
            if re.search(pattern, header, flags=re.IGNORECASE)
        ]
        if hits:
            raise RuntimeError(
                "Compiled DAG header validation failed; top-level DAG header "
                "contradicts locked CLI-delegated auth decisions. Forbidden "
                "header probe(s): "
                + ", ".join(hits)
            )

    @classmethod
    async def _transform_compiled_dag_for_gate_review(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        compiled_text: str,
    ) -> str:
        base_text = cls._strip_generated_root_dag_gate_surfaces(compiled_text)
        generated = await cls._build_root_dag_gate_surfaces(
            runner,
            feature,
            decomposition,
        )
        transformed = base_text.rstrip() + "\n\n" + generated
        cls._validate_root_dag_gate_surfaces(
            transformed,
            expect_sf14_surfaces=cls._sf14_surfaces_apply(decomposition),
        )
        cls._validate_root_dag_header_consistency(transformed)
        return transformed

    @classmethod
    def _record_root_dag_surface_revision_attempt(
        cls,
        ledger: Any,
        request: Any,
        *,
        cycle: int,
        artifact_digest: str,
    ) -> None:
        from ....models.outputs import GateReviewFinding

        attempt_note = (
            f"cycle-{cycle}: root-surface transform applied "
            f"[digest={artifact_digest}] {request.description}"
        )
        for finding in ledger.findings:
            if finding.source != "dag" or finding.status not in ("open", "fix_attempted"):
                continue
            if _text_overlap(request.description, finding.description) <= 0.5:
                continue
            finding.status = "fix_attempted"
            finding.revision_attempts.append(attempt_note)
            return

        ledger.findings.append(
            GateReviewFinding(
                id=f"GF-{len(ledger.findings) + 1:03d}",
                source="dag",
                description=request.description,
                reasoning=request.reasoning,
                affected_subfeatures=list(request.affected_subfeatures),
                severity=request.severity,
                status="fix_attempted",
                cycle_introduced=cycle,
                revision_attempts=[attempt_note],
            )
        )

    @classmethod
    async def _handle_root_dag_gate_revision_plan(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        revision_plan: RevisionPlan,
        compiled_text: str,
        ledger: Any,
        cycle: int,
    ) -> tuple[RevisionPlan, str]:
        root_requests = [
            request
            for request in revision_plan.requests
            if _is_root_dag_surface_revision_request(request)
        ]
        if not root_requests:
            return revision_plan, compiled_text

        transformed = await cls._transform_compiled_dag_for_gate_review(
            runner,
            feature,
            decomposition,
            compiled_text,
        )
        transformed_digest = _artifact_digest(transformed)
        digest_marker = f"digest={transformed_digest}"
        stalled: list[str] = []
        for request in root_requests:
            for finding in ledger.findings:
                if finding.source != "dag" or finding.status not in ("open", "fix_attempted"):
                    continue
                if _text_overlap(request.description, finding.description) <= 0.5:
                    continue
                same_digest_attempts = [
                    attempt
                    for attempt in finding.revision_attempts
                    if "root-surface transform applied" in attempt
                    and digest_marker in attempt
                ]
                if len(same_digest_attempts) >= _ROOT_DAG_GATE_MAX_SAME_DIGEST_ATTEMPTS:
                    stalled.append(f"{finding.id}: {finding.description}")
                break
        if stalled:
            raise RuntimeError(
                "DAG gate review is not converging: root DAG surface finding(s) "
                "reappeared after deterministic transform with unchanged digest "
                f"{transformed_digest}: "
                + "; ".join(stalled)
            )

        for request in root_requests:
            cls._record_root_dag_surface_revision_attempt(
                ledger,
                request,
                cycle=cycle,
                artifact_digest=transformed_digest,
            )

        remaining_requests = [
            request
            for request in revision_plan.requests
            if not _is_root_dag_surface_revision_request(request)
        ]
        return revision_plan.model_copy(update={"requests": remaining_requests}), transformed

    @classmethod
    async def _build_approved_root_implementation_dag(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
    ) -> ImplementationDAG:
        tasks: list[ImplementationTask] = []
        execution_order: list[list[str]] = []
        requirement_coverage: dict[str, list[str]] = {}
        seen_task_ids: set[str] = set()
        max_teams = 0
        subfeature_dags: list[tuple[str, ImplementationDAG]] = []
        task_id_counts: dict[str, int] = {}

        for subfeature in decomposition.subfeatures:
            slug = (subfeature.slug or "").strip()
            if not slug:
                continue
            dag_text = await runner.artifacts.get(f"dag:{slug}", feature=feature)
            if not dag_text:
                raise RuntimeError(
                    f"Cannot persist approved root DAG JSON: missing dag:{slug}"
                )
            try:
                dag = ImplementationDAG.model_validate_json(dag_text)
            except Exception as exc:
                raise RuntimeError(
                    f"Cannot persist approved root DAG JSON: dag:{slug} "
                    f"is not valid ImplementationDAG JSON: {exc}"
                ) from exc
            subfeature_dags.append((slug, dag))
            for task in dag.tasks:
                task_id_counts[task.id] = task_id_counts.get(task.id, 0) + 1

        for slug, dag in subfeature_dags:
            max_teams = max(max_teams, dag.num_teams)
            id_map = {
                task.id: (
                    task.id
                    if task_id_counts.get(task.id, 0) == 1
                    else f"{slug}-{task.id}"
                )
                for task in dag.tasks
            }
            for task in dag.tasks:
                task_id = id_map[task.id]
                if task_id in seen_task_ids:
                    raise RuntimeError(
                        f"Cannot persist approved root DAG JSON: duplicate task id {task_id}"
                    )
                seen_task_ids.add(task_id)
                tasks.append(
                    task.model_copy(
                        update={
                            "id": task_id,
                            "dependencies": [
                                id_map.get(dependency_id, dependency_id)
                                for dependency_id in task.dependencies
                            ],
                        }
                    )
                )
                for requirement_id in task.requirement_ids:
                    bucket = requirement_coverage.setdefault(requirement_id, [])
                    if task_id not in bucket:
                        bucket.append(task_id)
            execution_order.extend(
                [id_map.get(task_id, task_id) for task_id in wave]
                for wave in dag.execution_order
            )

        merged = ImplementationDAG(
            tasks=tasks,
            num_teams=max_teams,
            execution_order=execution_order,
            requirement_coverage=requirement_coverage,
            complete=True,
        )
        normalized, _normalized_flag = cls._normalize_subfeature_execution_order(merged)
        normalized, _path_rewrites, path_errors = await cls._resolve_dag_paths_for_persistence(
            runner,
            feature,
            normalized,
            context="approved root DAG",
            resolution_key="root",
        )
        if path_errors:
            raise RuntimeError(
                "Cannot persist approved root DAG JSON: unresolved path "
                "references remain after resolution: " + "; ".join(path_errors)
            )
        return normalized

    @classmethod
    async def _persist_approved_root_implementation_dag(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        *,
        approved_text: str | None = None,
    ) -> str:
        try:
            dag = await cls._build_approved_root_implementation_dag(
                runner,
                feature,
                decomposition,
            )
            dag_json = dag.model_dump_json(indent=2)
        except RuntimeError as exc:
            if approved_text is None:
                raise
            # The ONLY tolerated fallback is approved_text that is ITSELF a
            # valid executable ImplementationDAG (e.g. resume from a sealed
            # JSON artifact). Persisting gate MARKDOWN under the 'dag' key
            # silently hands develop an unexecutable artifact — the single
            # most expensive place to discover a planning defect. Fail loud.
            stripped = approved_text.strip()
            dag_json = None
            if stripped.startswith("{"):
                try:
                    dag = ImplementationDAG.model_validate_json(stripped)
                    dag_json = dag.model_dump_json(indent=2)
                except Exception:
                    dag_json = None
            if dag_json is None:
                raise RuntimeError(
                    "Root DAG seal failed and the gate-approved text is not "
                    "valid ImplementationDAG JSON — refusing to persist "
                    "markdown as the executable 'dag' artifact. Fix the "
                    f"underlying seal error and re-run: {exc}"
                ) from exc
            logger.warning(
                "Root DAG rebuild failed (%s) — persisting the gate-approved "
                "text, which IS valid ImplementationDAG JSON",
                exc,
            )
        put_artifact = getattr(runner.artifacts, "put", None)
        if callable(put_artifact):
            await put_artifact("dag", dag_json, feature=feature)
        mirror = runner.services.get("artifact_mirror")
        if mirror:
            mirror.write_artifact(feature.id, "dag", dag_json)
        return dag_json

    @classmethod
    def _dag_gate_review_hooks(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
    ) -> tuple[
        Callable[[str], Awaitable[str]],
        Callable[[RevisionPlan, str, Any, int], Awaitable[tuple[RevisionPlan, str]]],
    ]:
        async def _compiled_transform(compiled_text: str) -> str:
            return await cls._transform_compiled_dag_for_gate_review(
                runner,
                feature,
                decomposition,
                compiled_text,
            )

        async def _revision_plan_handler(
            revision_plan: RevisionPlan,
            compiled_text: str,
            ledger: Any,
            cycle: int,
        ) -> tuple[RevisionPlan, str]:
            return await cls._handle_root_dag_gate_revision_plan(
                runner,
                feature,
                decomposition,
                revision_plan,
                compiled_text,
                ledger,
                cycle,
            )

        return _compiled_transform, _revision_plan_handler

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        # ── Step 1: Resume check ──
        approved_dag = await runner.artifacts.get("dag", feature=feature)
        if approved_dag:
            logger.info("Gate-approved DAG exists — skipping")
            if not approved_dag.lstrip().startswith("{"):
                logger.warning(
                    "Gate-approved root dag artifact is compiled markdown; "
                    "rebuilding executable ImplementationDAG JSON"
                )
                decomposition = await self._load_decomposition(runner, feature, state)
                approved_dag = await self._persist_approved_root_implementation_dag(
                    runner,
                    feature,
                    decomposition,
                    approved_text=approved_dag,
                )
            state.dag = approved_dag
            return state

        await self._clear_stale_blocked_artifact(runner, feature)

        compiled_dag = await get_existing_artifact(runner, feature, "dag")
        if compiled_dag:
            logger.info("Compiled DAG exists but not gate-reviewed — running gate review")
            decomposition = await self._load_decomposition(runner, feature, state)
            compiled_transform, revision_plan_handler = self._dag_gate_review_hooks(
                runner,
                feature,
                decomposition,
            )
            final_text = await interview_gate_review(
                runner, feature, self.name,
                lead_actor=_sf_task_planner_gate_reviewer,
                decomposition=decomposition,
                artifact_prefix="dag",
                compiled_key="dag",
                base_role=planning_lead_role,
                output_type=ImplementationDAG,
                compiler_actor=dag_compiler,
                broad_key="dag:strategy",
                context_keys=["project", "scope", "decomposition"],
                compiled_transform=compiled_transform,
                revision_plan_handler=revision_plan_handler,
                # Lossless recompiles only: the free-merge path has truncated
                # large compiles before (plan regroup S3a/S6 drop; design
                # cycle-5 Bundle-2 drop) — same hardening as subfeature.py /
                # plan_review.py call sites.
                deterministic_final_merge=True,
                incremental_compile=True,
            )
            # DB write now happens inside interview_gate_review() on approval.
            state.dag = await self._persist_approved_root_implementation_dag(
                runner,
                feature,
                decomposition,
                approved_text=final_text,
            )
            return state

        decomposition = await self._load_decomposition(runner, feature, state)
        await self._ensure_planning_sidecar_migration(runner, feature, decomposition)

        # ── Step 2: Workstream Decomposition (one-shot Ask) ──
        ws_decomp = await self._get_or_create_workstreams(
            runner, feature, decomposition,
        )

        # ── Step 3: Parallel Workstream Task Decomposition ──
        sf_upstream = await self._load_sf_upstream(runner, feature, decomposition)
        blocked_failures: list[str] = []

        for round_ids in ws_decomp.execution_order:
            round_workstreams = [
                ws for ws in ws_decomp.workstreams if ws.id in round_ids
            ]
            logger.info(
                "Dispatching %d workstreams in parallel (round: %s)",
                len(round_workstreams),
                round_ids,
            )
            results = await asyncio.gather(
                *[
                    self._decompose_workstream(
                        runner, feature, decomposition, ws, sf_upstream,
                    )
                    for ws in round_workstreams
                ],
                return_exceptions=True,
            )
            for i, res in enumerate(results):
                workstream_id = round_workstreams[i].id
                if isinstance(res, BaseException):
                    failure = f"{workstream_id}: decomposition crashed ({res})"
                    logger.error(
                        "Workstream %s decomposition crashed: %s",
                        workstream_id, res,
                    )
                    blocked_failures.append(failure)
                    continue
                blocked_failures.extend(failure.render() for failure in res)
            if blocked_failures:
                break

        if blocked_failures:
            blocked_report = (
                "# Task Planning Blocked\n\n"
                "Task planning stopped before integration review and DAG compilation "
                "because one or more subfeature DAG decompositions failed validation "
                "or exhausted the retry budget.\n\n"
                "## Failures\n\n"
                + "\n".join(f"- {failure}" for failure in blocked_failures)
            )
            blocked_key = "task-planning-blocked"
            await runner.artifacts.put(blocked_key, blocked_report, feature=feature)
            mirror = runner.services.get("artifact_mirror")
            if mirror:
                mirror.write_artifact(feature.id, blocked_key, blocked_report)
            await runner.run(
                Notify(
                    message=(
                        "## Task Planning Blocked\n\n"
                        "Task planning stopped before integration review and DAG "
                        "compilation because one or more subfeature DAGs were invalid "
                        "or could not be generated within the model budget.\n\n"
                        + "\n".join(f"- {failure}" for failure in blocked_failures)
                    ),
                ),
                feature,
                phase_name=self.name,
            )
            raise RuntimeError(f"Task planning blocked. See `{blocked_key}`.")

        # ── Step 4: DAG Integration Review ──
        ordered_decomp = self._order_decomposition(decomposition, ws_decomp)
        compiled_transform, revision_plan_handler = self._dag_gate_review_hooks(
            runner,
            feature,
            ordered_decomp,
        )
        review = await integration_review(
            runner, feature, self.name,
            lead_actor=_sf_task_planner_reviewer,
            decomposition=ordered_decomp,
            artifact_prefix="dag",
            broad_key="dag:strategy",
            review_key_suffix="dag",
            extra_prompt_section=await _load_dag_packing_envelope_section(
                runner, feature
            ),
        )

        if review.needs_revision:
            if not review.revision_instructions:
                logger.error(
                    "TaskPlanningPhase: integration review needs_revision=True but "
                    "revision_instructions is empty — skipping revision"
                )
            else:
                logger.info(
                    "DAG integration review needs revision — dispatching %d patches",
                    len(review.revision_instructions),
                )
                plan = RevisionPlan(requests=[
                    RevisionRequest(
                        description=instruction,
                        reasoning="DAG integration review finding",
                        affected_subfeatures=[sf_slug],
                    )
                    for sf_slug, instruction in review.revision_instructions.items()
                ])
                revision_result = await targeted_revision(
                    runner, feature, self.name,
                    revision_plan=plan,
                    decomposition=ordered_decomp,
                    base_role=planning_lead_role,
                    output_type=ImplementationDAG,
                    artifact_prefix="dag",
                    context_keys=["project", "scope"],
                )
                if not revision_result.ok:
                    failure_text = "; ".join(
                        f"{failure.artifact_prefix}:{failure.slug} — {failure.reason}"
                        for failure in revision_result.failed
                    )
                    raise RuntimeError(
                        "Task planning integration review targeted revision failed: "
                        + failure_text
                    )

        # ── Step 5: DAG Compilation ──
        dag_text = await compile_artifacts(
            runner, feature, self.name,
            compiler_actor=dag_compiler,
            decomposition=ordered_decomp,
            artifact_prefix="dag",
            broad_key="dag:strategy",
            final_key="dag",
            compiled_transform=compiled_transform,
            # Lossless merge: never let an LLM regroup/final-merge re-emit the
            # whole DAG corpus (truncation incident class — see _helpers.py
            # _assert_compile_complete notes); deterministic concatenation +
            # per-source incremental reuse instead.
            deterministic_final_merge=True,
            incremental_compile=True,
        )

        # ── Step 6: Interview-Based Gate Review ──
        final_text = await interview_gate_review(
            runner, feature, self.name,
            lead_actor=_sf_task_planner_gate_reviewer,
            decomposition=ordered_decomp,
            artifact_prefix="dag",
            compiled_key="dag",
            base_role=planning_lead_role,
            output_type=ImplementationDAG,
            compiler_actor=dag_compiler,
            broad_key="dag:strategy",
            context_keys=["project", "scope", "decomposition"],
            compiled_transform=compiled_transform,
            revision_plan_handler=revision_plan_handler,
            # Lossless gate-cycle recompiles (same rationale as Step 5).
            deterministic_final_merge=True,
            incremental_compile=True,
        )

        # DB write now happens inside interview_gate_review() on approval.
        state.dag = await self._persist_approved_root_implementation_dag(
            runner,
            feature,
            ordered_decomp,
            approved_text=final_text,
        )
        return state

    # ── Step 2 helper ────────────────────────────────────────────────────

    @classmethod
    async def _build_subfeature_planning_digests(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
    ) -> str:
        sections = ["# Subfeature Planning Digests", ""]
        backfill_status = await cls._load_backfill_status(runner, feature)
        for subfeature in decomposition.subfeatures:
            if cls._slug_is_migrated(backfill_status, subfeature.slug):
                prd_sidecar = await load_structured_artifact(runner, feature, f"prd:{subfeature.slug}")
                plan_sidecar = await load_structured_artifact(runner, feature, f"plan:{subfeature.slug}")
                test_plan_sidecar = await load_structured_artifact(runner, feature, f"test-plan:{subfeature.slug}")
                planning_index = await cls._load_subfeature_planning_index(
                    runner,
                    feature,
                    subfeature.slug,
                )
                step_ids = [
                    step.id
                    for step in (plan_sidecar.content.steps if plan_sidecar is not None else [])
                    if step.id
                ]
                requirement_ids = [
                    requirement.id
                    for requirement in (prd_sidecar.content.structured_requirements if prd_sidecar is not None else [])
                    if requirement.id
                ]
                journey_ids = [
                    journey.id
                    for journey in (prd_sidecar.content.journeys if prd_sidecar is not None else [])
                    if journey.id
                ]
                ac_ids = [
                    criterion.id
                    for criterion in (test_plan_sidecar.content.acceptance_criteria if test_plan_sidecar is not None else [])
                    if criterion.id
                ]
                if not ac_ids:
                    # Degenerate migrated sidecar guard: list the markdown
                    # twin's AC universe rather than reporting zero ACs.
                    twin_text = (
                        await runner.artifacts.get(
                            f"test-plan:{subfeature.slug}", feature=feature
                        )
                        or ""
                    )
                    ac_ids = sorted(_extract_ac_ids(twin_text))
                trace_tokens = sorted(
                    {
                        *(
                            ref
                            for step in (plan_sidecar.content.steps if plan_sidecar is not None else [])
                            for ref in (
                                step.refs.requirement_ids
                                + step.refs.journey_ids
                                + step.refs.decision_ids
                                + step.refs.decision_aliases
                                + step.refs.nfr_ids
                                + step.refs.verifiable_state_ids
                            )
                        ),
                        *(
                            node.title
                            for node in (planning_index.nodes if planning_index is not None else [])
                            if node.title and node.chunk_type in {"component", "service", "entity", "state"}
                        ),
                    }
                )[:12]
            else:
                prd_text = await runner.artifacts.get(f"prd:{subfeature.slug}", feature=feature) or ""
                plan_text = await runner.artifacts.get(f"plan:{subfeature.slug}", feature=feature) or ""
                test_plan_text = await runner.artifacts.get(f"test-plan:{subfeature.slug}", feature=feature) or ""
                plan_markdown = cls._normalize_artifact_markdown(plan_text, f"plan:{subfeature.slug}")
                prd_markdown = cls._normalize_artifact_markdown(prd_text, f"prd:{subfeature.slug}")
                test_markdown = cls._normalize_artifact_markdown(test_plan_text, f"test-plan:{subfeature.slug}")

                step_ids = sorted(set(_STEP_ID_PATTERN.findall(plan_markdown)))
                requirement_ids = sorted(set(_REQ_ID_PATTERN.findall(prd_markdown)))
                journey_ids = sorted(set(_JOURNEY_ID_PATTERN.findall(prd_markdown + "\n" + plan_markdown)))
                ac_ids = sorted(_extract_ac_ids(test_plan_text))
                trace_tokens = sorted(
                    token
                    for token in cls._extract_trace_tokens(plan_markdown, prd_markdown)
                    if token not in {subfeature.slug, subfeature.name}
                )[:12]
            digest_lines = [
                f"## {subfeature.name} ({subfeature.slug})",
                "",
                f"- Description: {subfeature.description}",
                f"- Requirement IDs: {', '.join(requirement_ids[:12]) or 'none'}",
                f"- Journey IDs: {', '.join(journey_ids[:12]) or 'none'}",
                f"- Step IDs: {', '.join(step_ids[:12]) or 'none'}",
                f"- Acceptance Criteria: {', '.join(ac_ids[:12]) or 'none'}",
                f"- Trace Tokens: {', '.join(trace_tokens) or 'none'}",
                "",
            ]
            digest = "\n".join(digest_lines)
            if len(digest) > _WORKSTREAM_SUBFEATURE_DIGEST_BUDGET:
                digest = digest[:_WORKSTREAM_SUBFEATURE_DIGEST_BUDGET].rstrip() + "\n...[truncated]\n"
            sections.append(digest)
        return "\n".join(sections).strip() + "\n"

    @classmethod
    async def _build_workstream_planner_context_package(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
    ) -> ContextPackage | None:
        backfill_status = await cls._load_backfill_status(runner, feature)
        shared_sidecars_enabled = (
            cls._shared_artifacts_are_migrated(backfill_status)
            and await cls._load_shared_planning_index(runner, feature) is not None
        )
        broad_items: list[ContextPackageItem] = []
        for artifact_key, label in (
            ("prd:broad", "Broad PRD"),
            ("design:broad", "Broad Design"),
            ("plan:broad", "Broad Technical Plan"),
        ):
            structured = (
                await load_structured_artifact(runner, feature, artifact_key)
                if shared_sidecars_enabled and cls._shared_artifact_is_migrated(backfill_status, artifact_key)
                else None
            )
            text = (
                render_structured_markdown(structured)
                if structured is not None
                else await runner.artifacts.get(artifact_key, feature=feature) or ""
            )
            if not text:
                continue
            normalized = cls._normalize_artifact_markdown(text, artifact_key)
            broad_excerpt = cls._extract_matching_sections(
                normalized,
                {subfeature.slug for subfeature in decomposition.subfeatures},
                fallback_headings=("## Requirements", "## User Journeys", "## Implementation Steps", "## Architectural Risks"),
                max_chars=10_000,
            )
            if broad_excerpt:
                broad_items.append(
                    ContextPackageItem(
                        key=artifact_key,
                        label=label,
                        group="Broad Context",
                        content=broad_excerpt,
                        file_name=f"workstream-planner-{artifact_key.replace(':', '-')}.md",
                    )
                )

        decision_lines = ["# Workstream Planner Decision Context", ""]
        for decision_key, label in (("decisions:broad", "Broad Decisions"), (GLOBAL_DECISIONS_KEY, "Global Decisions")):
            structured = (
                await load_structured_artifact(runner, feature, decision_key)
                if shared_sidecars_enabled
                else None
            )
            decision_text = (
                render_structured_markdown(structured)
                if structured is not None
                else await runner.artifacts.get(decision_key, feature=feature) or ""
            )
            if decision_text:
                decision_lines.extend([f"## {label}", "", decision_text.strip(), ""])

        items = [
            ContextPackageItem(
                key="subfeature-decomposition",
                label="Subfeature Decomposition",
                group="Supporting Context",
                content=_json.dumps(
                    [
                        {
                            "id": subfeature.id,
                            "slug": subfeature.slug,
                            "name": subfeature.name,
                            "description": subfeature.description,
                        }
                        for subfeature in decomposition.subfeatures
                    ],
                    indent=2,
                ),
                file_name="workstream-planner-subfeature-decomposition.json",
            ),
            ContextPackageItem(
                key="subfeature-digests",
                label="Subfeature Planning Digests",
                group="Supporting Context",
                content=await cls._build_subfeature_planning_digests(runner, feature, decomposition),
                file_name="workstream-planner-subfeature-digests.md",
            ),
            ContextPackageItem(
                key="decisions",
                label="Broad / Global Decisions",
                group="Broad Context",
                content="\n".join(decision_lines).strip() + "\n",
                file_name="workstream-planner-decisions.md",
            ),
            *broad_items,
        ]
        return await build_context_package(
            runner,
            feature,
            title="Workstream Planner",
            file_stem="workstream-planner",
            intro_lines=[
                "Decompose the feature into parallel workstreams.",
                "Use the compact broad context, per-subfeature planning digests, and broad/global decisions from the referenced files.",
                "Be aggressive about parallelization and only serialize true data dependencies.",
            ],
            items=items,
        )

    async def _get_or_create_workstreams(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
    ) -> WorkstreamDecomposition:
        """Load existing workstream decomposition or create via one-shot Ask."""
        ws_text = await get_existing_artifact(runner, feature, "dag:strategy")
        if ws_text:
            try:
                ws = WorkstreamDecomposition.model_validate(_json.loads(ws_text))
                logger.info("Loaded existing workstream decomposition")
                return ws
            except Exception:
                logger.warning("Failed to parse existing workstream decomposition — regenerating")
        context_package = await self._build_workstream_planner_context_package(
            runner,
            feature,
            decomposition,
        )
        plan_text = await runner.artifacts.get("plan", feature=feature) or ""
        packing_envelope_section = await _load_dag_packing_envelope_section(
            runner, feature
        )

        ws_decomp: WorkstreamDecomposition = await runner.run(
            Ask(
                actor=_workstream_planner,
                prompt=(
                    "Decompose this feature into parallel workstreams.\n\n"
                    "Each workstream is a group of subfeatures that can be planned together "
                    "because they share domain context or have tight dependencies.\n\n"
                    "Produce execution rounds — workstreams in the same round can run in "
                    "parallel. A workstream enters a round only when all its depends_on "
                    "workstreams are in earlier rounds.\n\n"
                    + packing_envelope_section
                    + (
                        f"Read the context index first: `{context_package.index_path}`\n"
                        f"Then read the context manifest: `{context_package.manifest_path}`\n"
                        "Open the referenced files selectively instead of loading everything eagerly.\n"
                        if context_package is not None
                        else f"## Technical Plan\n\n{plan_text}"
                    )
                ),
                output_type=WorkstreamDecomposition,
            ),
            feature,
            phase_name=self.name,
        )
        await runner.artifacts.put(
            "dag:strategy",
            ws_decomp.model_dump_json(indent=2),
            feature=feature,
        )
        # Mirror to disk
        mirror = runner.services.get("artifact_mirror")
        if mirror:
            from ....services.artifacts import _key_to_path
            from pathlib import Path

            path = Path(mirror.feature_dir(feature.id)) / _key_to_path("dag:strategy")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(ws_decomp.model_dump_json(indent=2), encoding="utf-8")

        logger.info(
            "Workstream decomposition: %d workstreams, %d rounds",
            len(ws_decomp.workstreams),
            len(ws_decomp.execution_order),
        )
        return ws_decomp

    # ── Step 3 helper ────────────────────────────────────────────────────

    async def _decompose_workstream(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,  # Workstream model instance
        sf_upstream: dict[str, dict[str, str]],
    ) -> list[TaskPlanningFailure]:
        """Decompose one workstream into per-subfeature DAGs."""
        pending_slugs = []
        failures: list[TaskPlanningFailure] = []
        for slug in workstream.subfeature_slugs:
            subfeature = next((item for item in decomposition.subfeatures if item.slug == slug), None)
            if subfeature is None:
                failures.append(
                    TaskPlanningFailure(
                        workstream_id=workstream.id,
                        slug=slug,
                        reason="subfeature is missing from decomposition",
                    )
                )
                break
            existing = await runner.artifacts.get(f"dag:{slug}", feature=feature)
            existing_manifest = await self._load_slice_manifest(runner, feature, slug)
            if existing and existing_manifest is not None and existing_manifest.complete:
                try:
                    contract = await self._compile_subfeature_planning_contract(
                        runner,
                        feature,
                        slug,
                    )
                    if (
                        contract.contract_digest
                        and existing_manifest.contract_digest != contract.contract_digest
                    ):
                        existing_manifest.contract_digest = contract.contract_digest
                        await self._save_slice_manifest(runner, feature, existing_manifest)
                except PlanningContractError as exc:
                    failures.append(
                        TaskPlanningFailure(
                            workstream_id=workstream.id,
                            slug=slug,
                            reason=f"planning contract invalid: {'; '.join(exc.messages)}",
                            invocation_key=self._contract_artifact_key(slug),
                            context_paths=[exc.report_key] if exc.report_key else [],
                        )
                    )
                    break
                continue
            try:
                manifest = await self._derive_slice_manifest(runner, feature, subfeature)
                await self._normalize_pending_slice_manifest(runner, feature, manifest)
            except PlanningContractError as exc:
                failures.append(
                    TaskPlanningFailure(
                        workstream_id=workstream.id,
                        slug=slug,
                        reason=f"planning contract invalid: {'; '.join(exc.messages)}",
                        invocation_key=self._contract_artifact_key(slug),
                        context_paths=[exc.report_key] if exc.report_key else [],
                    )
                )
                break
            if existing and manifest.complete:
                continue
            if existing and not manifest.complete:
                await self._delete_artifact_key(runner, feature, f"dag:{slug}")
            pending_slugs.append(slug)
        if failures:
            return failures
        if not pending_slugs:
            logger.info("Workstream %s: all SFs already decomposed — skipping", workstream.id)
            return []

        logger.info(
            "Decomposing workstream %s (%d SFs, %d pending)",
            workstream.id,
            len(workstream.subfeature_slugs),
            len(pending_slugs),
        )

        for slug in pending_slugs:
            failure = await self._decompose_subfeature(
                runner,
                feature,
                decomposition,
                workstream,
                slug,
                sf_upstream,
            )
            if failure is not None:
                failures.append(failure)
                break
        return failures

    async def _plan_slice(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        manifest: TaskPlanningSliceManifest,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        slice_info: TaskPlanningSlice,
        sf_upstream: dict[str, dict[str, str]],
        *,
        mode_label: str,
        direct_peer_only: bool,
    ) -> SlicePlanResult:
        mode_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", mode_label.strip()).strip("-") or "default"
        actor = _slice_planner_actor(
            f"dag-ws-{workstream.id}-{subfeature.slug}-{slice_info.slice_id}-{mode_stem}",
        )
        prompt, context_package = await self._build_subfeature_task_prompt(
            runner,
            feature,
            decomposition,
            workstream,
            subfeature,
            sf_upstream,
            direct_peer_only=direct_peer_only,
            mode_label=mode_label,
            slice_info=slice_info,
        )
        attempt = SlicePlanningAttempt(
            slice_id=slice_info.slice_id,
            mode=mode_label,
            chosen_mode=mode_label,
            actor_name=actor.name,
            context_paths=self._context_paths(context_package),
            attempt=self._next_slice_attempt_number(
                manifest,
                slice_id=slice_info.slice_id,
                mode_label=mode_label,
            ),
        )
        attempt.attempt_key = self._slice_attempt_key(
            subfeature.slug,
            slice_info.slice_id,
            mode_label,
            attempt.attempt,
        )
        total_bytes, size_breakdown = self._estimate_context_package(context_package)
        attempt.estimated_context_bytes = total_bytes
        attempt.size_breakdown = size_breakdown
        if self._slice_context_over_budget(total_bytes, size_breakdown, mode_label=mode_label):
            attempt.status = "failed"
            attempt.error = self._slice_over_budget_error(total_bytes, size_breakdown)
            return SlicePlanResult(
                slice_id=slice_info.slice_id,
                error=attempt.error,
                retryable=True,
                over_budget=True,
                attempt=attempt,
                context_package=context_package,
            )
        try:
            await _clear_agent_session(runner, actor, feature)
            dag: ImplementationDAG = await runner.run(
                Ask(
                    actor=actor,
                    prompt=prompt,
                    output_type=ImplementationDAG,
                ),
                feature,
                phase_name=self.name,
            )
        except Exception as exc:
            attempt.status = "failed"
            attempt.error = str(exc)
            return SlicePlanResult(
                slice_id=slice_info.slice_id,
                error=str(exc),
                retryable=_is_model_boundary_failure(exc),
                attempt=attempt,
                context_package=context_package,
            )

        candidate_tasks = self._extract_subfeature_tasks(dag, decomposition, subfeature.slug)
        slice_task_ids = set(slice_info.step_ids)
        sf_tasks = [
            task
            for task in candidate_tasks
            if not slice_task_ids or set(task.step_ids) & slice_task_ids
        ]
        if not sf_tasks:
            sf_tasks = candidate_tasks
        if not sf_tasks:
            attempt.status = "failed"
            attempt.error = (
                "agent returned no tasks tagged for this subfeature slice; "
                f"slice={slice_info.slice_id}"
            )
            return SlicePlanResult(
                slice_id=slice_info.slice_id,
                error=attempt.error,
                retryable=True,
                attempt=attempt,
                context_package=context_package,
            )

        slice_dag = self._build_subfeature_dag(dag, sf_tasks)
        # Checkpoint the raw fragment BEFORE validation: a validation-stage
        # failure must never drop the planned fragment (resume adopts it and
        # re-runs validation/path-resolution only — no slice re-planning).
        await self._put_artifact(
            runner,
            feature,
            self._slice_fragment_raw_key(subfeature.slug, slice_info.slice_id),
            slice_dag.model_dump_json(indent=2),
        )
        validated_dag, validation_error, retryable = await self._validate_slice_fragment(
            runner,
            feature,
            subfeature.slug,
            slice_info,
            slice_dag,
            context_package=context_package,
        )
        if validated_dag is None:
            attempt.status = "failed"
            attempt.error = validation_error or "slice validation failed"
            return SlicePlanResult(
                slice_id=slice_info.slice_id,
                error=attempt.error,
                retryable=retryable,
                attempt=attempt,
                context_package=context_package,
            )

        attempt.status = "succeeded"
        return SlicePlanResult(
            slice_id=slice_info.slice_id,
            dag=validated_dag,
            attempt=attempt,
            context_package=context_package,
        )

    async def _repair_slice_fragment(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        manifest: TaskPlanningSliceManifest,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        slice_info: TaskPlanningSlice,
        sf_upstream: dict[str, dict[str, str]],
        current_fragment: ImplementationDAG,
        findings: list[str],
        *,
        mode_label: str,
        direct_peer_only: bool,
    ) -> SlicePlanResult:
        mode_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", mode_label.strip()).strip("-") or "default"
        actor = _slice_planner_actor(
            f"dag-ws-{workstream.id}-{subfeature.slug}-{slice_info.slice_id}-repair-{mode_stem}",
        )
        prompt, context_package = await self._build_subfeature_task_prompt(
            runner,
            feature,
            decomposition,
            workstream,
            subfeature,
            sf_upstream,
            direct_peer_only=direct_peer_only,
            mode_label=mode_label,
            slice_info=slice_info,
            repair_fragment=current_fragment,
            repair_findings=findings,
        )
        attempt = SlicePlanningAttempt(
            slice_id=slice_info.slice_id,
            mode=f"repair-{mode_label}",
            chosen_mode=mode_label,
            actor_name=actor.name,
            context_paths=self._context_paths(context_package),
            attempt=self._next_slice_attempt_number(
                manifest,
                slice_id=slice_info.slice_id,
                mode_label=f"repair-{mode_label}",
            ),
        )
        attempt.attempt_key = self._slice_attempt_key(
            subfeature.slug,
            slice_info.slice_id,
            f"repair-{mode_label}",
            attempt.attempt,
        )
        total_bytes, size_breakdown = self._estimate_context_package(context_package)
        attempt.estimated_context_bytes = total_bytes
        attempt.size_breakdown = size_breakdown
        if self._slice_context_over_budget(total_bytes, size_breakdown, mode_label=mode_label):
            attempt.status = "failed"
            attempt.error = "repair: " + self._slice_over_budget_error(
                total_bytes, size_breakdown
            )
            return SlicePlanResult(
                slice_id=slice_info.slice_id,
                error=attempt.error,
                retryable=True,
                over_budget=True,
                attempt=attempt,
                context_package=context_package,
            )
        try:
            await _clear_agent_session(runner, actor, feature)
            dag: ImplementationDAG = await runner.run(
                Ask(
                    actor=actor,
                    prompt=prompt,
                    output_type=ImplementationDAG,
                ),
                feature,
                phase_name=self.name,
            )
        except Exception as exc:
            attempt.status = "failed"
            attempt.error = str(exc)
            return SlicePlanResult(
                slice_id=slice_info.slice_id,
                error=str(exc),
                retryable=_is_model_boundary_failure(exc),
                attempt=attempt,
                context_package=context_package,
            )

        # Same pre-validation checkpoint as _plan_slice (repair path).
        await self._put_artifact(
            runner,
            feature,
            self._slice_fragment_raw_key(subfeature.slug, slice_info.slice_id),
            dag.model_dump_json(indent=2),
        )
        validated_dag, validation_error, retryable = await self._validate_slice_fragment(
            runner,
            feature,
            subfeature.slug,
            slice_info,
            dag,
            context_package=context_package,
        )
        if validated_dag is None:
            attempt.status = "failed"
            attempt.error = validation_error or "slice repair validation failed"
            return SlicePlanResult(
                slice_id=slice_info.slice_id,
                error=attempt.error,
                retryable=retryable,
                attempt=attempt,
                context_package=context_package,
            )

        attempt.status = "succeeded"
        return SlicePlanResult(
            slice_id=slice_info.slice_id,
            dag=validated_dag,
            attempt=attempt,
            context_package=context_package,
        )

    async def _decompose_subfeature(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        slug: str,
        sf_upstream: dict[str, dict[str, str]],
    ) -> TaskPlanningFailure | None:
        sf = next((item for item in decomposition.subfeatures if item.slug == slug), None)
        if sf is None:
            return TaskPlanningFailure(
                workstream_id=workstream.id,
                slug=slug,
                reason="subfeature is missing from decomposition",
            )
        try:
            manifest = await self._derive_slice_manifest(runner, feature, sf)
            await self._normalize_pending_slice_manifest(runner, feature, manifest)
        except PlanningContractError as exc:
            return TaskPlanningFailure(
                workstream_id=workstream.id,
                slug=slug,
                reason=f"planning contract invalid: {'; '.join(exc.messages)}",
                invocation_key=self._contract_artifact_key(slug),
                context_paths=[exc.report_key] if exc.report_key else [],
            )

        for slice_info in manifest.slices:
            fragment_key = self._slice_fragment_key(slug, slice_info.slice_id)
            fragment_text = await runner.artifacts.get(fragment_key, feature=feature)
            status = self._ensure_slice_status(manifest, slice_info.slice_id)
            status.fragment_key = fragment_key
            if fragment_text:
                try:
                    persisted_fragment = ImplementationDAG.model_validate_json(fragment_text)
                    validated_fragment, validation_error, _retryable = await self._validate_slice_fragment(
                        runner,
                        feature,
                        slug,
                        slice_info,
                        persisted_fragment,
                    )
                    if validated_fragment is None:
                        await self._delete_artifact_key(runner, feature, fragment_key)
                        status.status = "pending"
                        status.last_error = (
                            f"existing fragment {fragment_key} failed validation: "
                            f"{validation_error or 'unknown validation error'}"
                        )
                        continue
                    persisted_json = validated_fragment.model_dump_json(indent=2)
                    if persisted_json != fragment_text:
                        await self._put_artifact(
                            runner,
                            feature,
                            fragment_key,
                            persisted_json,
                        )
                    status.status = "completed"
                    status.last_error = ""
                    continue
                except Exception:
                    await self._delete_artifact_key(runner, feature, fragment_key)
                    status.status = "pending"
                    status.last_error = f"existing fragment {fragment_key} is invalid"
            if status.status != "completed":
                # Resume adoption of the PRE-validation checkpoint: a fragment
                # whose validation failed (e.g. defective path resolver) was
                # never persisted under dag-fragment:, but its raw form is.
                # Re-run validation (path resolution onward) only — slice
                # re-planning stays the fallback when this also fails.
                raw_key = self._slice_fragment_raw_key(slug, slice_info.slice_id)
                raw_text = await runner.artifacts.get(raw_key, feature=feature)
                if raw_text:
                    try:
                        raw_fragment = ImplementationDAG.model_validate_json(raw_text)
                        adopted, adoption_error, _retryable = await self._validate_slice_fragment(
                            runner,
                            feature,
                            slug,
                            slice_info,
                            raw_fragment,
                        )
                        if adopted is not None:
                            await self._put_artifact(
                                runner,
                                feature,
                                fragment_key,
                                adopted.model_dump_json(indent=2),
                            )
                            status.status = "completed"
                            status.last_error = ""
                            logger.warning(
                                "Slice %s/%s: adopted raw fragment %s on resume "
                                "(validation re-run only; no re-plan)",
                                slug, slice_info.slice_id, raw_key,
                            )
                            continue
                        status.last_error = (
                            f"raw fragment {raw_key} failed validation: "
                            f"{adoption_error or 'unknown validation error'}"
                        )
                    except Exception:
                        logger.warning(
                            "Slice %s/%s: raw fragment %s is invalid; falling "
                            "back to re-planning",
                            slug, slice_info.slice_id, raw_key,
                            exc_info=True,
                        )
            if status.status != "completed":
                status.status = "pending"
        await self._save_slice_manifest(runner, feature, manifest)

        restart_mode_pass = True
        while restart_mode_pass:
            restart_mode_pass = False
            for mode_label, direct_peer_only in _SLICE_RETRY_MODES:
                pending_slices = [
                    slice_info
                    for slice_info in manifest.slices
                    if self._ensure_slice_status(manifest, slice_info.slice_id).status != "completed"
                ]
                if not pending_slices:
                    break
                results = await asyncio.gather(
                    *[
                        self._plan_slice(
                            runner,
                            feature,
                            manifest,
                            decomposition,
                            workstream,
                            sf,
                            slice_info,
                            sf_upstream,
                            mode_label=mode_label,
                            direct_peer_only=direct_peer_only,
                        )
                        for slice_info in pending_slices
                    ]
                )
                oversized_target_only: list[str] = []
                for result in results:
                    status = self._ensure_slice_status(manifest, result.slice_id)
                    if result.attempt is not None:
                        await self._persist_slice_attempt(runner, feature, manifest, result.attempt)
                    if result.dag is not None:
                        fragment_key = self._slice_fragment_key(slug, result.slice_id)
                        await self._put_artifact(
                            runner,
                            feature,
                            fragment_key,
                            result.dag.model_dump_json(indent=2),
                        )
                        status.status = "completed"
                        status.retry_mode = result.attempt.chosen_mode if result.attempt else mode_label
                        status.context_paths = result.attempt.context_paths if result.attempt else []
                        status.last_error = ""
                        status.fragment_key = fragment_key
                        continue

                    status.status = "failed"
                    status.retry_mode = result.attempt.chosen_mode if result.attempt else mode_label
                    status.context_paths = result.attempt.context_paths if result.attempt else []
                    status.last_error = result.error
                    if result.over_budget and mode_label == "target-only":
                        oversized_target_only.append(result.slice_id)

                if oversized_target_only:
                    split_any = False
                    for slice_id in oversized_target_only:
                        split_any = await self._split_oversized_slice(
                            runner,
                            feature,
                            manifest,
                            slice_id,
                        ) or split_any
                    if split_any:
                        restart_mode_pass = True
                        break
                await self._save_slice_manifest(runner, feature, manifest)

        incomplete = [
            status for status in manifest.statuses
            if status.status != "completed"
        ]
        if incomplete:
            return TaskPlanningFailure(
                workstream_id=workstream.id,
                slug=slug,
                reason="; ".join(
                    f"{status.slice_id} failed in {status.retry_mode or 'unknown-mode'}: {status.last_error}"
                    for status in incomplete
                ),
                invocation_key=f"dag-ws-{workstream.id}-{slug}",
                context_paths=sorted({path for status in incomplete for path in status.context_paths}),
            )

        sf_dag = await self._merge_slice_fragments(runner, feature, slug, manifest)
        coverage = await _validate_verification_gates_coverage(
            runner,
            feature,
            slug,
            sf_dag.tasks,
        )
        requirement_coverage = await self._validate_requirement_coverage(
            runner,
            feature,
            slug,
            sf_dag.tasks,
        )
        if not coverage.ok:
            repaired = await self._attempt_coverage_repair(
                runner,
                feature,
                decomposition,
                workstream,
                sf,
                sf_upstream,
                manifest,
                coverage,
            )
            if repaired is not None:
                sf_dag = repaired
                coverage = await _validate_verification_gates_coverage(
                    runner,
                    feature,
                    slug,
                    sf_dag.tasks,
                )
                requirement_coverage = await self._validate_requirement_coverage(
                    runner,
                    feature,
                    slug,
                    sf_dag.tasks,
                )
        if not coverage.ok:
            bad_statuses = [
                self._ensure_slice_status(manifest, slice_info.slice_id)
                for slice_info in manifest.slices
                if (
                    set(self._slice_owned_acceptance_ids(slice_info)) & set(coverage.uncovered_owned_ac_ids)
                    or set(slice_info.global_obligation_ac_ids)
                    & set(coverage.uncovered_global_obligation_ac_ids)
                )
            ]
            return TaskPlanningFailure(
                workstream_id=workstream.id,
                slug=slug,
                reason="; ".join(coverage.failure_messages()),
                invocation_key=f"dag-ws-{workstream.id}-{slug}",
                context_paths=sorted({path for status in bad_statuses for path in status.context_paths}),
            )
        if not requirement_coverage.ok:
            bad_statuses = [
                self._ensure_slice_status(manifest, slice_info.slice_id)
                for slice_info in manifest.slices
                if set(slice_info.requirement_ids) & set(requirement_coverage.missing_requirement_ids)
            ]
            return TaskPlanningFailure(
                workstream_id=workstream.id,
                slug=slug,
                reason=(
                    "missing merged requirement_coverage for: "
                    + ", ".join(requirement_coverage.missing_requirement_ids)
                ),
                invocation_key=f"dag-ws-{workstream.id}-{slug}",
                context_paths=sorted({path for status in bad_statuses for path in status.context_paths}),
            )

        manifest.complete = True
        await self._save_slice_manifest(runner, feature, manifest)
        await self._put_artifact(
            runner,
            feature,
            f"dag:{slug}",
            sf_dag.model_dump_json(indent=2),
        )
        logger.info(
            "Workstream %s: stored %d tasks across %d slices for %s",
            workstream.id,
            len(sf_dag.tasks),
            len(manifest.slices),
            slug,
        )
        return None

    async def _attempt_coverage_repair(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        sf_upstream: dict[str, dict[str, str]],
        manifest: TaskPlanningSliceManifest,
        coverage: VerificationCoverageResult,
    ) -> ImplementationDAG | None:
        if not autonomous_remainder_enabled(runner, feature, phase_name=self.name):
            return None

        logger.info(
            "Workstream %s/%s: validation drift detected — attempting autonomous slice repair",
            workstream.id,
            subfeature.slug,
        )
        task_to_slice: dict[str, str] = {}
        fragment_by_slice: dict[str, ImplementationDAG] = {}
        for slice_info in manifest.slices:
            fragment_text = await runner.artifacts.get(
                self._slice_fragment_key(subfeature.slug, slice_info.slice_id),
                feature=feature,
            )
            if not fragment_text:
                continue
            fragment = ImplementationDAG.model_validate_json(fragment_text)
            fragment_by_slice[slice_info.slice_id] = fragment
            for task in fragment.tasks:
                task_to_slice[task.id] = slice_info.slice_id

        affected_slice_ids: set[str] = set()
        uncovered_owned = set(coverage.uncovered_owned_ac_ids or coverage.uncovered_ac_ids)
        if uncovered_owned:
            for slice_info in manifest.slices:
                if uncovered_owned & set(self._slice_owned_acceptance_ids(slice_info)):
                    affected_slice_ids.add(slice_info.slice_id)
        uncovered_global = set(coverage.uncovered_global_obligation_ac_ids)
        if uncovered_global:
            for slice_info in manifest.slices:
                candidate_global = set(slice_info.global_obligation_ac_ids)
                if uncovered_global & candidate_global:
                    affected_slice_ids.add(slice_info.slice_id)

        for message in coverage.unknown_gate_refs:
            task_match = re.search(r"Task\s+([A-Za-z0-9._-]+)", message)
            if task_match and task_match.group(1) in task_to_slice:
                affected_slice_ids.add(task_to_slice[task_match.group(1)])

        if not affected_slice_ids:
            return None

        deterministic_repair = False
        for slice_info in manifest.slices:
            if slice_info.slice_id not in affected_slice_ids:
                continue
            fragment = fragment_by_slice.get(slice_info.slice_id)
            if fragment is None:
                continue
            validated_fragment, _validation_error, _retryable = await self._validate_slice_fragment(
                runner,
                feature,
                subfeature.slug,
                slice_info,
                fragment,
            )
            if validated_fragment is None:
                continue
            if validated_fragment.model_dump_json() == fragment.model_dump_json():
                continue
            fragment_key = self._slice_fragment_key(subfeature.slug, slice_info.slice_id)
            await self._put_artifact(
                runner,
                feature,
                fragment_key,
                validated_fragment.model_dump_json(indent=2),
            )
            fragment_by_slice[slice_info.slice_id] = validated_fragment
            status = self._ensure_slice_status(manifest, slice_info.slice_id)
            status.status = "completed"
            status.last_error = ""
            status.fragment_key = fragment_key
            deterministic_repair = True

        if deterministic_repair:
            await self._save_slice_manifest(runner, feature, manifest)
            return await self._merge_slice_fragments(runner, feature, subfeature.slug, manifest)

        findings = coverage.failure_messages() or ["verification coverage drift"]
        pending_slice_ids: set[str] = set(affected_slice_ids)
        while pending_slice_ids:
            restart_mode_pass = False
            remaining = [
                slice_info
                for slice_info in manifest.slices
                if slice_info.slice_id in pending_slice_ids
            ]
            for mode_label, direct_peer_only in _SLICE_RETRY_MODES:
                if not remaining:
                    pending_slice_ids = set()
                    break
                results = await asyncio.gather(
                    *[
                        (
                            self._repair_slice_fragment(
                                runner,
                                feature,
                                manifest,
                                decomposition,
                                workstream,
                                subfeature,
                                slice_info,
                                sf_upstream,
                                fragment_by_slice[slice_info.slice_id],
                                findings,
                                mode_label=mode_label,
                                direct_peer_only=direct_peer_only,
                            )
                            if slice_info.slice_id in fragment_by_slice
                            else self._plan_slice(
                                runner,
                                feature,
                                manifest,
                                decomposition,
                                workstream,
                                subfeature,
                                slice_info,
                                sf_upstream,
                                mode_label=mode_label,
                                direct_peer_only=direct_peer_only,
                            )
                        )
                        for slice_info in remaining
                    ]
                )
                failed_slice_ids: set[str] = set()
                oversized_target_only: list[str] = []
                for result in results:
                    status = self._ensure_slice_status(manifest, result.slice_id)
                    if result.attempt is not None:
                        await self._persist_slice_attempt(runner, feature, manifest, result.attempt)
                    if result.dag is not None:
                        fragment_key = self._slice_fragment_key(subfeature.slug, result.slice_id)
                        await self._put_artifact(
                            runner,
                            feature,
                            fragment_key,
                            result.dag.model_dump_json(indent=2),
                        )
                        fragment_by_slice[result.slice_id] = result.dag
                        status.status = "completed"
                        status.retry_mode = f"repair-{mode_label}"
                        status.context_paths = result.attempt.context_paths if result.attempt else []
                        status.last_error = ""
                        status.fragment_key = fragment_key
                        continue

                    failed_slice_ids.add(result.slice_id)
                    status.status = "failed"
                    status.retry_mode = f"repair-{mode_label}"
                    status.context_paths = result.attempt.context_paths if result.attempt else []
                    status.last_error = result.error
                    if result.over_budget and mode_label == "target-only":
                        oversized_target_only.append(result.slice_id)

                if oversized_target_only:
                    split_any = False
                    child_pending_slice_ids: set[str] = set()
                    for slice_id in oversized_target_only:
                        split_result = await self._split_oversized_slice(
                            runner,
                            feature,
                            manifest,
                            slice_id,
                        )
                        split_any = split_result or split_any
                        if split_result:
                            fragment_by_slice.pop(slice_id, None)
                            failed_slice_ids.discard(slice_id)
                            child_pending_slice_ids.update(
                                child_slice.slice_id
                                for child_slice in manifest.slices
                                if child_slice.slice_id.startswith(f"{slice_id}-")
                            )
                    if split_any:
                        pending_slice_ids = failed_slice_ids | child_pending_slice_ids
                        restart_mode_pass = True
                        break

                await self._save_slice_manifest(runner, feature, manifest)
                pending_slice_ids = set(failed_slice_ids)
                remaining = [
                    slice_info
                    for slice_info in manifest.slices
                    if slice_info.slice_id in pending_slice_ids
                ]
            if not restart_mode_pass:
                break

        if pending_slice_ids:
            return None
        return await self._merge_slice_fragments(runner, feature, subfeature.slug, manifest)

    @staticmethod
    async def _load_directory_map(runner: WorkflowRunner, feature: Feature) -> str:
        """Return the persisted workspace directory map (repo catalog), if any.

        The directory map is carried on the persisted ``ProjectContext`` under the
        ``project`` artifact key (built by ``WorkspaceManager.build_directory_map``).
        Surfacing it into the planner prompt gives the Planning Lead the real repo
        layout so it can ground every emitted path (see Path Discipline).
        """
        artifacts = getattr(runner, "artifacts", None)
        if artifacts is None:
            return ""
        try:
            raw = await artifacts.get("project", feature=feature)
        except Exception:
            return ""
        if not raw:
            return ""
        try:
            return ProjectContext.model_validate_json(raw).directory_map or ""
        except Exception:
            return ""

    async def _build_subfeature_task_prompt(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        sf_upstream: dict[str, dict[str, str]],
        *,
        direct_peer_only: bool,
        mode_label: str,
        slice_info: TaskPlanningSlice | None = None,
        repair_fragment: ImplementationDAG | None = None,
        repair_findings: list[str] | None = None,
    ) -> tuple[str, ContextPackage | None]:
        package = await self._build_subfeature_task_context_package(
            runner,
            feature,
            decomposition,
            workstream,
            subfeature,
            mode_label=mode_label,
            direct_peer_only=direct_peer_only,
            slice_info=slice_info,
        )
        context = ""
        if package is None:
            context = await self._build_subfeature_task_context(
                runner,
                feature,
                decomposition,
                workstream,
                subfeature,
                sf_upstream,
                direct_peer_only=direct_peer_only,
                mode_label=mode_label,
                slice_info=slice_info,
            )
        repo_catalog = await self._load_directory_map(runner, feature)
        repo_catalog_section = ""
        if repo_catalog:
            repo_catalog_section = (
                "## Repo Catalog (real workspace layout)\n"
                "These are the actual repos available at planning time. Per Path "
                "Discipline, ground every `file_scope[].path` and `files[]` entry "
                "against this catalog and confirm the exact location with Glob/Grep "
                "before emitting it.\n\n"
                f"{repo_catalog.strip()}\n\n"
            )
        packing_envelope_section = await _load_dag_packing_envelope_section(
            runner, feature
        )
        slice_note = ""
        if slice_info is not None:
            owned_ac_ids = self._slice_owned_acceptance_ids(slice_info)
            supporting_ac_ids = self._slice_supporting_acceptance_ids(slice_info)
            req_scope_note = ""
            if slice_info.requirement_ids:
                req_scope_note = (
                    "Every emitted task MUST include at least one requirement_id from this in-scope list. "
                    "Tasks missing requirement_ids will fail slice validation.\n"
                )
            journey_scope_note = ""
            if slice_info.journey_ids:
                journey_scope_note = (
                    "Every emitted task MUST include at least one journey_id from this in-scope list. "
                    "Tasks missing journey_ids will fail slice validation.\n"
                )
            slice_note_lines = [
                f"Target only planning slice `{slice_info.slice_id}`.\n"
                f"Step IDs in scope: {', '.join(slice_info.step_ids) or 'none'}.\n",
                f"Requirement IDs in scope: {', '.join(slice_info.requirement_ids) or 'none'}.\n",
                f"Journey IDs in scope: {', '.join(slice_info.journey_ids) or 'none'}.\n",
                f"Owned acceptance criteria in scope: {', '.join(owned_ac_ids) or 'none'}.\n",
                f"Relevant global-obligation acceptance criteria: {', '.join(slice_info.global_obligation_ac_ids) or 'none'}.\n",
                f"Required reference source families: {', '.join(slice_info.required_reference_sources) or 'none'}.\n",
                req_scope_note,
                journey_scope_note,
            ]
            if mode_label != "target-only":
                slice_note_lines.append(
                    f"Supporting acceptance criteria context: {', '.join(supporting_ac_ids) or 'none'}.\n"
                )
            slice_note = "".join(slice_note_lines) + "\n"
        repair_note = ""
        if repair_fragment is not None:
            findings_text = "\n".join(f"- {finding}" for finding in (repair_findings or []))
            repair_note = (
                "You are repairing an existing slice fragment, not replanning from scratch.\n"
                "Keep task IDs, dependencies, file scope, and execution order stable unless a minimal change is required.\n"
                "Repair the fragment so every task has required traceability fields and slice coverage aligns with the canonical test plan.\n\n"
                "## Repair Findings\n"
                f"{findings_text or '- verification coverage drift'}\n\n"
                "## Current Slice Fragment\n"
                f"{repair_fragment.model_dump_json(indent=2)}\n\n"
            )
        prompt = (
            f"You are decomposing subfeature '{subfeature.name}' ({subfeature.slug}) within "
            f"workstream '{workstream.name}' into implementation tasks.\n\n"
            f"Workstream rationale: {workstream.rationale}\n"
            f"Peer context mode: {mode_label}\n"
            f"Workstream depends on: {workstream.depends_on or ['none']}\n\n"
            f"{repo_catalog_section}"
            f"{packing_envelope_section}"
            f"{slice_note}"
            f"{repair_note}"
            "Create tasks ONLY for the target subfeature. Every task.subfeature_id MUST be the "
            f"exact subfeature slug '{subfeature.slug}'. Do not emit tasks for peer subfeatures.\n\n"
            "Break the target subfeature's technical plan into parallelizable implementation tasks. "
            "Each task needs:\n"
            "- file_scope (path + create/modify/read_only)\n"
            "- requirement_ids (if Requirement IDs in scope is non-empty, every task MUST include at least one applicable requirement_id from that list)\n"
            "- step_ids (STEP-* from plan)\n"
            "- journey_ids (if Journey IDs in scope is non-empty, every task MUST include at least one applicable journey_id from that list)\n"
            "- acceptance_criteria\n"
            "- counterexamples\n"
            "- verification_gates populated with exact AC-ids from the subfeature's TEST-PLAN\n"
            "- reference_material with self-contained excerpts from upstream artifacts\n"
            "- subfeature_id set exactly to the target slug\n\n"
            "When a TEST-PLAN section is present, treat its acceptance criteria as the source of "
            "truth for task-level verification_gates and acceptance_criteria. Use the exact AC-id "
            "strings from the test plan; mismatched or invented IDs will block publication. "
            "Never invent placeholder IDs like `AC-STEP20-*`.\n"
            "Owned acceptance criteria are mandatory coverage obligations for this slice. Supporting "
            "acceptance criteria are relevance context only; include them in verification_gates only "
            "when a task truly implements them. Global obligations may be cited only when a task truly "
            "implements them; do not treat them as mandatory unless the slice explicitly owns them.\n\n"
            "Every task must carry step_ids, acceptance_criteria, reference_material, and "
            "verification_gates that are self-contained enough for downstream implementers and verifiers. "
            "If Requirement IDs in scope is non-empty for this slice, every emitted task MUST include at least one "
            "applicable requirement_id from that list; tasks missing requirement_ids will fail slice validation. "
            "Only omit requirement_ids when Requirement IDs in scope is empty for this slice. "
            "If Journey IDs in scope is non-empty for this slice, every emitted task MUST include at least one "
            "applicable journey_id from that list; tasks missing journey_ids will fail slice validation. "
            "Do not cite verification_gates outside the owned or relevant-global gate sets described above.\n\n"
            "execution_order must be topologically valid: if a task depends on another task, "
            "the dependent task must appear in a later execution_order wave.\n\n"
            "Be aggressive about parallelization, but only create task dependencies when one task "
            "truly cannot start until another finishes. Keep dependencies within the target "
            "subfeature task set.\n\n"
        )
        if package is not None:
            prompt += (
                f"Read the context index first: `{package.index_path}`\n"
                f"Then read the context manifest: `{package.manifest_path}`\n"
                "Open the referenced files selectively instead of loading everything eagerly."
            )
        else:
            prompt += context
        return prompt, package

    @staticmethod
    def _cited_peer_slugs(
        decomposition: SubfeatureDecomposition,
        slug: str,
        texts: list[str],
    ) -> set[str]:
        haystack = "\n".join(texts).lower()
        cited: set[str] = set()
        for candidate in decomposition.subfeatures:
            if candidate.slug == slug:
                continue
            if candidate.slug.lower() in haystack or candidate.name.lower() in haystack:
                cited.add(candidate.slug)
        return cited

    async def _build_peer_contract_context(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        slug: str,
        *,
        direct_peer_only: bool,
        mode_label: str,
        target_bundle: dict[str, str],
        feature_bundle: dict[str, str],
    ) -> tuple[str, list[str], list[str]]:
        citation_sources = [text for text in list(target_bundle.values()) + list(feature_bundle.values()) if text]
        peer_slugs = self._peer_slugs_for_context(
            decomposition,
            workstream,
            slug,
            direct_peer_only=direct_peer_only,
            mode_label=mode_label,
            citation_sources=citation_sources,
        )
        if not peer_slugs:
            return "", [], []

        bundle_tokens = set()
        for text in citation_sources:
            bundle_tokens.update(self._extract_trace_tokens(text))
        explicit_decision_ids: set[str] = set()

        sections = ["## Peer Contract Context", ""]
        for peer_slug in peer_slugs:
            peer = next((item for item in decomposition.subfeatures if item.slug == peer_slug), None)
            edge_lines = self._edge_lines_between(decomposition, slug, peer_slug)
            edge_text = "\n".join(edge_lines)
            tokens = set(bundle_tokens)
            tokens.add(peer_slug)
            if peer is not None:
                tokens.add(peer.name)
            tokens.update(self._extract_trace_tokens(edge_text))

            blocks = [
                f"### {peer.name if peer else peer_slug} ({peer_slug})",
                "",
            ]
            if edge_lines:
                blocks.extend(["#### Edge Contract", "", *[f"- {line}" for line in edge_lines], ""])

            for prefix, label, headings in (
                ("prd", "PRD Excerpts", ("## Requirements", "## User Journeys")),
                ("design", "Design Excerpts", ("## Design System", "## Verifiable States", "## Interaction Patterns")),
                ("plan", "Plan Excerpts", ("## Implementation Steps", "## Journey Verifications", "## Architectural Risks")),
                ("test-plan", "Test Plan Excerpts", ("## Acceptance Criteria", "## Test Scenarios", "## Verification Checklist", "## Edge Cases")),
            ):
                artifact_key = f"{prefix}:{peer_slug}"
                artifact_text = await runner.artifacts.get(artifact_key, feature=feature) or ""
                if artifact_text:
                    normalized = self._normalize_artifact_markdown(artifact_text, artifact_key)
                    excerpt = self._extract_matching_sections(
                        normalized,
                        tokens,
                        fallback_headings=headings,
                        max_chars=3_500,
                    )
                else:
                    summary_text = await runner.artifacts.get(f"{prefix}-summary:{peer_slug}", feature=feature) or ""
                    excerpt = summary_text[:3_500].rstrip() if summary_text else ""
                if excerpt:
                    blocks.extend([f"#### {label}", "", excerpt, ""])
                    explicit_decision_ids.update(_extract_decision_ids(excerpt))
            decision_summary = await runner.artifacts.get(f"decisions-summary:{peer_slug}", feature=feature) or ""
            explicit_decision_ids.update(_extract_decision_ids(decision_summary))

            section_text = "\n".join(block for block in blocks if block).strip()
            if section_text:
                sections.append(section_text)

        return (
            "\n\n".join(sections).strip() + "\n" if len(sections) > 2 else "",
            peer_slugs,
            sorted(explicit_decision_ids),
        )

    async def _build_slice_context_parts(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        *,
        mode_label: str,
        direct_peer_only: bool,
        slice_info: TaskPlanningSlice,
        sf_upstream: dict[str, dict[str, str]] | None = None,
        owned_only_test_plan: bool = False,
        minimal_target_only: bool = False,
    ) -> tuple[dict[str, str], dict[str, str], str, str]:
        target_texts = await self._load_target_texts(
            runner,
            feature,
            subfeature.slug,
            sf_upstream or {},
        )
        backfill_status = await self._load_backfill_status(runner, feature)
        test_plan_model: TestPlan | None = None
        if self._slug_is_migrated(backfill_status, subfeature.slug):
            test_plan_sidecar = await load_structured_artifact(
                runner,
                feature,
                f"test-plan:{subfeature.slug}",
            )
            if test_plan_sidecar is not None:
                test_plan_model = test_plan_sidecar.content
        target_bundle = self._target_slice_bundle(
            subfeature.slug,
            slice_info,
            target_texts,
            owned_only_test_plan=owned_only_test_plan,
            test_plan_model=test_plan_model,
        )
        broad_artifacts = {
            key: await self._load_artifact_text_for_planning(
                runner,
                feature,
                key,
                backfill_status=backfill_status,
            )
            for key in ("prd:broad", "design:broad", "plan:broad", "decisions:broad")
        }
        feature_bundle = self._feature_constraint_bundle(
            decomposition,
            workstream,
            subfeature,
            slice_info,
            target_bundle,
            broad_artifacts,
            mode_label=mode_label,
        )
        if minimal_target_only:
            feature_bundle = {
                **feature_bundle,
                "broad-prd": "",
                "broad-design": "",
                "broad-plan": "",
            }
        peer_context, peer_slugs, peer_decision_ids = await self._build_peer_contract_context(
            runner,
            feature,
            decomposition,
            workstream,
            subfeature.slug,
            direct_peer_only=direct_peer_only,
            mode_label=mode_label,
            target_bundle=target_bundle,
            feature_bundle=feature_bundle,
        )
        decision_pack_text = await self._build_scoped_decision_pack(
            runner,
            feature,
            decomposition,
            workstream,
            subfeature,
            mode_label=mode_label,
            direct_peer_only=direct_peer_only,
            slice_info=slice_info,
            target_bundle=target_bundle,
            feature_bundle=feature_bundle,
            peer_context=peer_context,
            peer_slugs=peer_slugs,
            explicit_peer_decision_ids=peer_decision_ids,
        )
        return target_bundle, feature_bundle, decision_pack_text, peer_context

    async def _build_subfeature_task_context_package(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        *,
        mode_label: str,
        direct_peer_only: bool,
        slice_info: TaskPlanningSlice | None = None,
    ) -> ContextPackage | None:
        mode_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", mode_label.strip()).strip("-") or "default"
        active_slice = slice_info or TaskPlanningSlice(slice_id="slice-1", title="Whole subfeature")
        owned_ac_ids = self._slice_owned_acceptance_ids(active_slice)
        supporting_ac_ids = self._slice_supporting_acceptance_ids(active_slice)
        decisions_required = "decisions" in set(active_slice.required_reference_sources)
        async def _package_for_mode(
            *,
            minimal_target_only: bool,
            omit_decision_pack: bool,
            compact_target_decisions: bool,
            omit_target_decisions: bool = False,
        ) -> ContextPackage | None:
            target_bundle, feature_bundle, decision_pack_text, peer_context = await self._build_slice_context_parts(
                runner,
                feature,
                decomposition,
                workstream,
                subfeature,
                mode_label=mode_label,
                direct_peer_only=direct_peer_only,
                slice_info=active_slice,
                owned_only_test_plan=(mode_label == "target-only"),
                minimal_target_only=minimal_target_only,
            )
            if omit_decision_pack:
                decision_pack_text = ""
            decision_context_result = await self._build_target_decision_context_item(
                runner,
                feature,
                workstream,
                subfeature,
                active_slice,
                mode_stem=mode_stem,
                target_bundle=target_bundle,
                feature_bundle=feature_bundle,
                compact=compact_target_decisions,
                required=decisions_required,
                omit=omit_target_decisions,
            )
            target_decision_item = decision_context_result.item
            if omit_decision_pack and decisions_required and not decision_context_result.complete:
                return None
            if decisions_required and target_decision_item is None and not decision_pack_text.strip():
                return None
            intro_lines = [
                f"Plan implementation tasks only for subfeature `{subfeature.slug}`.",
                f"Target slice: `{active_slice.slice_id}` ({', '.join(active_slice.step_ids) or 'whole subfeature'}).",
                f"Owned ACs: {', '.join(owned_ac_ids) or 'none'}.",
                "Use only canonical AC-ids from the test plan; never invent placeholder gate IDs.",
            ]
            if target_decision_item is not None:
                intro_lines.insert(
                    3,
                    "Use slice-local target excerpts, the target decision context provided in the referenced files, compact neighborhood context, and only the cited non-target decision records from the referenced files.",
                )
            else:
                intro_lines.insert(
                    3,
                    "Use slice-local target excerpts, compact neighborhood context, and only the cited non-target decision records from the referenced files.",
                )
            if mode_label != "target-only":
                intro_lines[2] = (
                    f"Owned ACs: {', '.join(owned_ac_ids) or 'none'}; "
                    f"supporting AC context: {', '.join(supporting_ac_ids) or 'none'}."
                )
            global_obligation_note = (
                f"Relevant global obligations: {', '.join(active_slice.global_obligation_ac_ids) or 'none'}."
            )
            intro_lines.append(global_obligation_note)

            items = [
                ContextPackageItem(
                    key="metadata",
                    label="Target Metadata",
                    group="Feature-wide Constraint Layer",
                    content=feature_bundle["metadata"],
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-metadata.md",
                ),
                ContextPackageItem(
                    key="neighborhood",
                    label="Local Neighborhood",
                    group="Feature-wide Constraint Layer",
                    content=feature_bundle["neighborhood"],
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-neighborhood.md",
                ),
                ContextPackageItem(
                    key="edges",
                    label="Interface Edges",
                    group="Feature-wide Constraint Layer",
                    content=feature_bundle["edges"],
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-edges.md",
                ),
            ]
            if not minimal_target_only:
                items.extend(
                    [
                        ContextPackageItem(
                            key="broad-prd",
                            label="Broad PRD Excerpts",
                            group="Feature-wide Constraint Layer",
                            content=feature_bundle["broad-prd"],
                            file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-broad-prd.md",
                        ),
                        ContextPackageItem(
                            key="broad-design",
                            label="Broad Design Excerpts",
                            group="Feature-wide Constraint Layer",
                            content=feature_bundle["broad-design"],
                            file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-broad-design.md",
                        ),
                        ContextPackageItem(
                            key="broad-plan",
                            label="Broad Plan Excerpts",
                            group="Feature-wide Constraint Layer",
                            content=feature_bundle["broad-plan"],
                            file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-broad-plan.md",
                        ),
                    ]
                )
            items.extend(
                [
                    ContextPackageItem(
                        key="contract",
                        label="Slice Contract",
                        group="Target Slice Layer",
                        content="\n".join(
                            [
                                "## Slice Contract",
                                "",
                                f"- Slice: `{active_slice.slice_id}`",
                                f"- Step IDs: {', '.join(active_slice.step_ids) or 'none'}",
                                f"- Requirement IDs: {', '.join(active_slice.requirement_ids) or 'none'}",
                                f"- Journey IDs: {', '.join(active_slice.journey_ids) or 'none'}",
                                f"- Owned AC IDs: {', '.join(owned_ac_ids) or 'none'}",
                                f"- Global obligation AC IDs: {', '.join(active_slice.global_obligation_ac_ids) or 'none'}",
                                f"- Required reference sources: {', '.join(active_slice.required_reference_sources) or 'none'}",
                            ]
                        ),
                        file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-contract.md",
                    ),
                    ContextPackageItem(
                        key="plan",
                        label="Slice Technical Plan",
                        group="Target Slice Layer",
                        content=target_bundle["plan"],
                        file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-plan.md",
                    ),
                    ContextPackageItem(
                        key="prd",
                        label="Slice PRD Excerpts",
                        group="Target Slice Layer",
                        content=target_bundle["prd"],
                        file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-prd.md",
                    ),
                    ContextPackageItem(
                        key="design",
                        label="Slice Design Excerpts",
                        group="Target Slice Layer",
                        content=target_bundle["design"],
                        file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-design.md",
                    ),
                    ContextPackageItem(
                        key="system-design",
                        label="Slice System Design Excerpts",
                        group="Target Slice Layer",
                        content=target_bundle["system-design"],
                        file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-system-design.md",
                    ),
                    ContextPackageItem(
                        key="test-plan",
                        label="Slice Test Plan Excerpts",
                        group="Target Slice Layer",
                        content=target_bundle["test-plan"],
                        file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-test-plan.md",
                    ),
                ]
            )
            if target_decision_item is not None:
                items.append(target_decision_item)
            items.extend(
                [
                    ContextPackageItem(
                        key="decision-pack",
                        label="Referenced Non-target Decisions",
                        group="Peer Contract Layer",
                        content=decision_pack_text,
                        file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-decision-pack.md",
                    ),
                    ContextPackageItem(
                        key="peer-context",
                        label="Peer Contract Excerpts",
                        group="Peer Contract Layer",
                        content=peer_context,
                        file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-peer-context.md",
                    ),
                ]
            )
            return await build_context_package(
                runner,
                feature,
                title=f"Subfeature DAG Planner — {subfeature.slug}",
                file_stem=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}",
                intro_lines=intro_lines,
                items=items,
            )

        package = await _package_for_mode(
            minimal_target_only=False,
            omit_decision_pack=False,
            compact_target_decisions=False,
        )
        if mode_label != "target-only":
            return package
        total_bytes, size_breakdown = self._estimate_context_package(package)
        if not self._slice_context_over_budget(total_bytes, size_breakdown, mode_label=mode_label):
            return package
        minimal_package = await _package_for_mode(
            minimal_target_only=True,
            omit_decision_pack=False,
            compact_target_decisions=False,
        )
        minimal_total, _ = self._estimate_context_package(minimal_package)
        if minimal_package is not None and minimal_total < total_bytes:
            package = minimal_package
            total_bytes = minimal_total
            size_breakdown = self._estimate_context_package(package)[1]
        if not self._slice_context_over_budget(total_bytes, size_breakdown, mode_label=mode_label):
            return package
        decision_light_package = await _package_for_mode(
            minimal_target_only=True,
            omit_decision_pack=True,
            compact_target_decisions=True,
        )
        decision_light_total, decision_light_breakdown = self._estimate_context_package(decision_light_package)
        if decision_light_package is not None and decision_light_total < total_bytes:
            package = decision_light_package
            total_bytes = decision_light_total
            size_breakdown = decision_light_breakdown
        if not self._slice_context_over_budget(total_bytes, size_breakdown, mode_label=mode_label):
            return package
        if not decisions_required:
            decision_minimal_package = await _package_for_mode(
                minimal_target_only=True,
                omit_decision_pack=True,
                compact_target_decisions=True,
                omit_target_decisions=True,
            )
            decision_minimal_total, _ = self._estimate_context_package(decision_minimal_package)
            if decision_minimal_package is not None and decision_minimal_total < total_bytes:
                return decision_minimal_package
        return package

    async def _build_subfeature_task_context(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        sf_upstream: dict[str, dict[str, str]],
        *,
        direct_peer_only: bool,
        mode_label: str,
        slice_info: TaskPlanningSlice | None = None,
    ) -> str:
        sections: list[str] = []
        active_slice = slice_info or TaskPlanningSlice(slice_id="slice-1", title="Whole subfeature")
        target_bundle, feature_bundle, decisions_text, peer_context = await self._build_slice_context_parts(
            runner,
            feature,
            decomposition,
            workstream,
            subfeature,
            mode_label=mode_label,
            direct_peer_only=direct_peer_only,
            slice_info=active_slice,
            sf_upstream=sf_upstream,
            owned_only_test_plan=(mode_label == "target-only"),
        )
        backfill_status = await self._load_backfill_status(runner, feature)
        if decisions_text:
            sections.append(decisions_text)

        sections.append(
            "\n".join(
                [
                    "## Slice Contract",
                    "",
                    f"- Slice: `{active_slice.slice_id}`",
                    f"- Step IDs: {', '.join(active_slice.step_ids) or 'none'}",
                    f"- Requirement IDs: {', '.join(active_slice.requirement_ids) or 'none'}",
                    f"- Journey IDs: {', '.join(active_slice.journey_ids) or 'none'}",
                    f"- Owned AC IDs: {', '.join(self._slice_owned_acceptance_ids(active_slice)) or 'none'}",
                    f"- Global obligation AC IDs: {', '.join(active_slice.global_obligation_ac_ids) or 'none'}",
                    f"- Required reference sources: {', '.join(active_slice.required_reference_sources) or 'none'}",
                ]
            )
        )

        sections.extend(
            section
            for section in (
                feature_bundle["metadata"],
                feature_bundle["neighborhood"],
                feature_bundle["edges"],
                feature_bundle["broad-prd"],
                feature_bundle["broad-design"],
                feature_bundle["broad-plan"],
                target_bundle["plan"],
                target_bundle["prd"],
                target_bundle["design"],
                target_bundle["system-design"],
                target_bundle["test-plan"],
                await self._load_artifact_text_for_planning(
                    runner,
                    feature,
                    f"decisions:{subfeature.slug}",
                    backfill_status=backfill_status,
                ),
            )
            if section
        )
        if peer_context:
            sections.append(peer_context)

        return "\n\n---\n\n".join(section for section in sections if section)

    @classmethod
    def _peer_slugs_for_context(
        cls,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        slug: str,
        *,
        direct_peer_only: bool,
        mode_label: str,
        citation_sources: list[str] | None = None,
    ) -> list[str]:
        if mode_label == "target-only":
            return []
        connected = cls._connected_peer_slugs(decomposition, slug)
        if direct_peer_only:
            eligible = connected
        else:
            cited = cls._cited_peer_slugs(decomposition, slug, citation_sources or [])
            eligible = connected | cited
        return [
            subfeature.slug
            for subfeature in decomposition.subfeatures
            if subfeature.slug != slug and subfeature.slug in eligible
        ]

    async def _build_scoped_decision_pack(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        *,
        mode_label: str,
        direct_peer_only: bool,
        slice_info: TaskPlanningSlice | None = None,
        target_bundle: dict[str, str] | None = None,
        feature_bundle: dict[str, str] | None = None,
        peer_context: str = "",
        peer_slugs: list[str] | None = None,
        explicit_peer_decision_ids: list[str] | None = None,
    ) -> str:
        backfill_status = await self._load_backfill_status(runner, feature)
        compiled_text = await runner.artifacts.get("decisions", feature=feature) or ""
        broad_text = await self._load_artifact_text_for_planning(
            runner,
            feature,
            "decisions:broad",
            backfill_status=backfill_status,
        )
        global_text = await self._load_artifact_text_for_planning(
            runner,
            feature,
            GLOBAL_DECISIONS_KEY,
            backfill_status=backfill_status,
        )
        target_decisions_key = f"decisions:{subfeature.slug}"

        compiled_ledger = parse_decision_ledger(compiled_text)
        broad_ledger = parse_decision_ledger(broad_text)
        global_ledger = parse_decision_ledger(global_text)
        active_slice = slice_info or TaskPlanningSlice(slice_id="slice-1", title="Whole subfeature")
        if target_bundle is None:
            target_texts = await self._load_target_texts(runner, feature, subfeature.slug, {})
            target_bundle = self._target_slice_bundle(subfeature.slug, active_slice, target_texts)
        if feature_bundle is None:
            broad_artifacts = {
                key: await self._load_artifact_text_for_planning(
                    runner,
                    feature,
                    key,
                    backfill_status=backfill_status,
                )
                for key in ("prd:broad", "design:broad", "plan:broad", "decisions:broad")
            }
            feature_bundle = self._feature_constraint_bundle(
                decomposition,
                workstream,
                subfeature,
                active_slice,
                target_bundle,
                broad_artifacts,
                mode_label=mode_label,
            )
        if peer_slugs is None:
            peer_slugs = self._peer_slugs_for_context(
                decomposition,
                workstream,
                subfeature.slug,
                direct_peer_only=direct_peer_only,
                mode_label=mode_label,
                citation_sources=[
                    text for text in list(target_bundle.values()) + list(feature_bundle.values())
                    if text
                ],
            )
        peer_ledgers: list[tuple[str, DecisionLedger]] = []
        peer_reference_sources: list[tuple[str, str]] = []
        for peer_slug in peer_slugs:
            peer_text = await self._load_artifact_text_for_planning(
                runner,
                feature,
                f"decisions:{peer_slug}",
                backfill_status=backfill_status,
            )
            peer_ledgers.append((peer_slug, parse_decision_ledger(peer_text)))
            if peer_context:
                peer_reference_sources.append((f"peer-contract:{peer_slug}", peer_context))

        candidate_ids: set[str] = set(explicit_peer_decision_ids or [])
        referenced_ids: set[str] = set()
        for text in list(target_bundle.values()) + list(feature_bundle.values()):
            referenced_ids.update(_extract_decision_ids(text))

        referenced_ids.update(_extract_decision_ids(peer_context))
        for _source_key, source_text in peer_reference_sources:
            referenced_ids.update(_extract_decision_ids(source_text))

        candidate_ids.update(referenced_ids)
        candidate_ids.difference_update(
            _extract_decision_ids(
                await self._load_artifact_text_for_planning(
                    runner,
                    feature,
                    target_decisions_key,
                    backfill_status=backfill_status,
                )
            )
        )

        selected_by_id: dict[str, DecisionRecord] = {}
        for decision in compiled_ledger.decisions:
            if decision.id in candidate_ids:
                selected_by_id[decision.id] = decision.model_copy(deep=True)

        for ledger in (global_ledger, broad_ledger, *[ledger for _slug, ledger in peer_ledgers]):
            for decision in ledger.decisions:
                if decision.id in candidate_ids and decision.id not in selected_by_id:
                    selected_by_id[decision.id] = decision.model_copy(deep=True)

        missing_ids = sorted(candidate_ids - set(selected_by_id))
        scoped_ledger = DecisionLedger(
            title="Scoped Decision Pack",
            decisions=sorted(selected_by_id.values(), key=_decision_sort_key),
            complete=bool(selected_by_id),
        )

        included_sources: list[str] = []
        if broad_text and any(
            decision.id in selected_by_id for decision in broad_ledger.decisions
        ):
            included_sources.append("- Broad Decision Ledger: `decisions:broad`")
        if global_text and any(
            decision.id in selected_by_id for decision in global_ledger.decisions
        ):
            included_sources.append(f"- Global Decision Ledger: `{GLOBAL_DECISIONS_KEY}`")
        included_sources.append(f"- Target Decision Ledger stays separate: `{target_decisions_key}`")
        if peer_context:
            included_sources.append(
                "- Peer Decision Citation Sources: "
                + ", ".join(f"`peer-contract:{peer_slug}`" for peer_slug in peer_slugs)
            )
        if compiled_text:
            included_sources.append("- Full-record resolution source: `decisions`")

        lines = [
            "# Scoped Decision Pack",
            "",
            f"- Target subfeature: `{subfeature.slug}`",
            f"- Peer context mode: `{mode_label}`",
            f"- Workstream: `{workstream.id}`",
            "",
            "## Included Sources",
            "",
            *(included_sources or ["- _No decision sources available._"]),
            "",
            "## Explicitly Referenced Decision IDs",
            "",
        ]
        if referenced_ids:
            lines.extend(f"- `{decision_id}`" for decision_id in sorted(referenced_ids))
        else:
            lines.append("- _No explicit decision citations found in target or peer inputs._")

        if missing_ids:
            lines.extend(
                [
                    "",
                    "## Missing Referenced IDs",
                    "",
                    *[f"- `{decision_id}`" for decision_id in missing_ids],
                ]
            )

        lines.extend(
            [
                "",
                "## Decision Records",
                "",
                to_markdown(scoped_ledger).rstrip(),
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    async def _build_target_decision_context_item(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        workstream: Any,
        subfeature: Any,
        active_slice: TaskPlanningSlice,
        *,
        mode_stem: str,
        target_bundle: dict[str, str],
        feature_bundle: dict[str, str],
        compact: bool,
        required: bool = False,
        omit: bool = False,
    ) -> DecisionContextBuildResult:
        if omit:
            return DecisionContextBuildResult(item=None, complete=not required)

        backfill_status = await self._load_backfill_status(runner, feature)
        target_decisions_key = f"decisions:{subfeature.slug}"
        target_text = await self._load_artifact_text_for_planning(
            runner,
            feature,
            target_decisions_key,
            backfill_status=backfill_status,
        )
        compiled_text = await runner.artifacts.get("decisions", feature=feature) or ""
        broad_text = await self._load_artifact_text_for_planning(
            runner,
            feature,
            "decisions:broad",
            backfill_status=backfill_status,
        )
        global_text = await self._load_artifact_text_for_planning(
            runner,
            feature,
            GLOBAL_DECISIONS_KEY,
            backfill_status=backfill_status,
        )
        if not compact and not target_text:
            return DecisionContextBuildResult(item=None, complete=not required)
        if not compact:
            return DecisionContextBuildResult(
                item=ContextPackageItem(
                    key="subfeature-decisions",
                    label="Target Decision Ledger",
                    group="Target Slice Layer",
                    artifact_key=target_decisions_key,
                ),
                complete=True,
            )

        summary_text = await runner.artifacts.get(
            f"decisions-summary:{subfeature.slug}",
            feature=feature,
        ) or ""
        contract = await self._load_subfeature_planning_contract(
            runner,
            feature,
            subfeature.slug,
        )
        contract_decision_ids: set[str] = set()
        if contract is not None:
            for step_contract in contract.step_contracts:
                if step_contract.step_id in active_slice.step_ids:
                    contract_decision_ids.update(step_contract.decision_ids)
        referenced_ids = contract_decision_ids | _extract_decision_ids(
            "\n".join(
                text
                for text in [
                    *target_bundle.values(),
                    *feature_bundle.values(),
                ]
                if text
            )
        )
        target_ledger = parse_decision_ledger(target_text)
        compiled_ledger = parse_decision_ledger(compiled_text)
        broad_ledger = parse_decision_ledger(broad_text)
        global_ledger = parse_decision_ledger(global_text)
        selected_by_id: dict[str, DecisionRecord] = {}
        for ledger in (target_ledger, compiled_ledger, global_ledger, broad_ledger):
            for decision in ledger.decisions:
                if decision.id in referenced_ids and decision.id not in selected_by_id:
                    selected_by_id[decision.id] = decision.model_copy(deep=True)
        missing_ids = sorted(referenced_ids - set(selected_by_id))
        sections = ["# Target Decision Context", ""]
        if summary_text.strip():
            sections.extend(["## Summary", "", summary_text.strip(), ""])
        if referenced_ids:
            selected = sorted(selected_by_id.values(), key=_decision_sort_key)
            if selected:
                sections.extend(
                    [
                        "## Referenced Decision Records",
                        "",
                        to_markdown(
                            DecisionLedger(
                                title="Target Decision Context",
                                decisions=selected,
                                complete=True,
                            )
                        ).rstrip(),
                        "",
                    ]
                )
        compact_text = "\n".join(sections).strip()
        if compact_text == "# Target Decision Context":
            if required and not missing_ids and target_text.strip():
                return DecisionContextBuildResult(
                    item=ContextPackageItem(
                        key="subfeature-decisions",
                        label="Target Decision Ledger",
                        group="Target Slice Layer",
                        artifact_key=target_decisions_key,
                    ),
                    complete=True,
                )
            return DecisionContextBuildResult(
                item=None,
                complete=not required and not missing_ids,
                missing_ids=missing_ids,
            )
        return DecisionContextBuildResult(
            item=ContextPackageItem(
                key="subfeature-decisions",
                label="Target Decision Context",
                group="Target Slice Layer",
                content=compact_text + "\n",
                file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-target-decisions.md",
            ),
            complete=not missing_ids,
            missing_ids=missing_ids,
        )

    @staticmethod
    def _connected_peer_slugs(
        decomposition: SubfeatureDecomposition,
        slug: str,
    ) -> set[str]:
        return {
            edge.to_subfeature if edge.from_subfeature == slug else edge.from_subfeature
            for edge in decomposition.edges
            if slug in (edge.from_subfeature, edge.to_subfeature)
        }

    @staticmethod
    def _edge_lines_between(
        decomposition: SubfeatureDecomposition,
        slug: str,
        peer_slug: str,
    ) -> list[str]:
        lines: list[str] = []
        for edge in decomposition.edges:
            participants = {edge.from_subfeature, edge.to_subfeature}
            if {slug, peer_slug} != participants:
                continue
            details = (
                f"{edge.from_subfeature} → {edge.to_subfeature} "
                f"({edge.interface_type}): {edge.description}"
            )
            if edge.data_contract:
                details += f" [contract: {edge.data_contract}]"
            if edge.owner:
                details += f" [owner: {edge.owner}]"
            lines.append(details)
        return lines

    @classmethod
    def _edge_context_for_slug(
        cls,
        decomposition: SubfeatureDecomposition,
        slug: str,
        *,
        allowed_peers: set[str] | None = None,
    ) -> str:
        lines: list[str] = []
        seen_peers: set[str] = set()
        for edge in decomposition.edges:
            if slug not in (edge.from_subfeature, edge.to_subfeature):
                continue
            peer_slug = edge.to_subfeature if edge.from_subfeature == slug else edge.from_subfeature
            if allowed_peers is not None and peer_slug not in allowed_peers:
                continue
            if peer_slug in seen_peers:
                continue
            seen_peers.add(peer_slug)
            lines.extend(f"- {line}" for line in cls._edge_lines_between(decomposition, slug, peer_slug))
        if not lines:
            return ""
        return "## Interface Edges\n\n" + "\n".join(lines)

    @staticmethod
    def _extract_subfeature_tasks(
        dag: ImplementationDAG,
        decomposition: SubfeatureDecomposition,
        slug: str,
    ) -> list[ImplementationTask]:
        sf = next((item for item in decomposition.subfeatures if item.slug == slug), None)
        sf_ids = {slug, slug.lower()}
        if sf is not None:
            sf_ids.update(
                {
                    sf.id,
                    sf.id.lower(),
                    sf.name,
                    sf.name.lower(),
                    sf.id.replace("-", ""),
                }
            )
        sf_tasks = [task for task in dag.tasks if task.subfeature_id in sf_ids]
        if sf_tasks:
            return sf_tasks
        return [
            task
            for task in dag.tasks
            if task.subfeature_id
            and (
                slug in task.subfeature_id.lower()
                or (sf is not None and sf.id.lower() in task.subfeature_id.lower())
            )
        ]

    @staticmethod
    def _build_subfeature_dag(
        dag: ImplementationDAG,
        sf_tasks: list[ImplementationTask],
    ) -> ImplementationDAG:
        sf_task_ids = {task.id for task in sf_tasks}
        requirement_coverage: dict[str, list[str]] = {}
        for task in sf_tasks:
            for requirement_id in task.requirement_ids:
                bucket = requirement_coverage.setdefault(requirement_id, [])
                if task.id not in bucket:
                    bucket.append(task.id)
        return ImplementationDAG(
            tasks=sf_tasks,
            num_teams=dag.num_teams,
            execution_order=[
                [task_id for task_id in round_ids if task_id in sf_task_ids]
                for round_ids in dag.execution_order
                if any(task_id in sf_task_ids for task_id in round_ids)
            ],
            requirement_coverage=requirement_coverage,
            complete=True,
        )

    # ── Shared helpers ───────────────────────────────────────────────────

    @staticmethod
    def _order_decomposition(
        decomposition: SubfeatureDecomposition,
        ws_decomp: WorkstreamDecomposition,
    ) -> SubfeatureDecomposition:
        """Reorder decomposition subfeatures based on workstream execution order."""
        ordered_slugs: list[str] = []
        for round_ids in ws_decomp.execution_order:
            for ws in ws_decomp.workstreams:
                if ws.id in round_ids:
                    ordered_slugs.extend(ws.subfeature_slugs)

        sf_by_slug = {sf.slug: sf for sf in decomposition.subfeatures}
        ordered_sfs = [sf_by_slug[slug] for slug in ordered_slugs if slug in sf_by_slug]
        remaining = [sf for sf in decomposition.subfeatures if sf.slug not in set(ordered_slugs)]

        return SubfeatureDecomposition(
            subfeatures=ordered_sfs + remaining,
            edges=decomposition.edges,
            decomposition_rationale=decomposition.decomposition_rationale,
            complete=decomposition.complete,
        )

    @staticmethod
    async def _load_decomposition(
        runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> SubfeatureDecomposition:
        """Load decomposition from state or artifact store."""
        decomp_text = state.decomposition
        if not decomp_text:
            decomp_text = await runner.artifacts.get("decomposition", feature=feature) or ""
        if decomp_text:
            try:
                return SubfeatureDecomposition.model_validate(_json.loads(decomp_text))
            except Exception:
                pass
        return SubfeatureDecomposition()

    @staticmethod
    async def _load_sf_upstream(
        runner: WorkflowRunner, feature: Feature, decomposition: SubfeatureDecomposition,
    ) -> dict[str, dict[str, str]]:
        """Preload per-SF upstream artifacts (prd, design, plan, system-design, test-plan).

        ``test-plan`` is optional — features planned before the test_planning
        step existed will return empty text; the ``if text:`` guard below
        drops missing artifacts silently.
        """
        result: dict[str, dict[str, str]] = {}
        backfill_status_text = await runner.artifacts.get("artifact-backfill-status", feature=feature) or ""
        backfill_status = None
        if backfill_status_text:
            try:
                backfill_status = ArtifactBackfillStatus.model_validate_json(backfill_status_text)
            except Exception:
                backfill_status = None
        for sf in decomposition.subfeatures:
            sf_artifacts: dict[str, str] = {}
            for prefix in ("prd", "design", "plan", "system-design", "test-plan"):
                artifact_key = f"{prefix}:{sf.slug}"
                if TaskPlanningPhase._slug_is_migrated(backfill_status, sf.slug):
                    structured = await load_structured_artifact(runner, feature, artifact_key)
                    text = render_structured_markdown(structured) if structured is not None else ""
                else:
                    text = await runner.artifacts.get(artifact_key, feature=feature)
                if text:
                    sf_artifacts[prefix] = text
            result[sf.slug] = sf_artifacts
        return result

    @staticmethod
    async def _load_prd_summaries(
        runner: WorkflowRunner, feature: Feature, decomposition: SubfeatureDecomposition,
    ) -> str:
        """Load PRD summaries for all subfeatures."""
        parts: list[str] = []
        for sf in decomposition.subfeatures:
            summary = await runner.artifacts.get(f"prd-summary:{sf.slug}", feature=feature)
            if summary:
                parts.append(f"### {sf.name} ({sf.slug})\n\n{summary}")
        return "\n\n".join(parts)
