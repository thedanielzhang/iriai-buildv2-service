from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field, model_validator

T = TypeVar("T", bound=BaseModel)


# ── Citation model ───────────────────────────────────────────────────────────


class Citation(BaseModel):
    """A justification for a decision in a planning artifact."""

    type: str  # code | decision | research
    reference: str  # file path, decision ID (D-*), or URL/description
    excerpt: str = ""
    reasoning: str = ""


# ── Shared sub-models ────────────────────────────────────────────────────────


class Check(BaseModel):
    """A single criterion-level check result."""

    criterion: str
    result: str  # PASS | FAIL
    detail: str = ""


class Issue(BaseModel):
    """A structured concern or finding with severity."""

    severity: str  # blocker | major | minor | nit
    description: str
    file: str = ""
    line: int = 0


class Gap(BaseModel):
    """Something missing or not covered."""

    category: str
    description: str
    severity: str  # blocker | major | minor
    plan_reference: str = ""


class Deviation(BaseModel):
    """A deviation from the plan."""

    plan_said: str
    i_did: str
    reason: str
    source: str = ""
    task_id: str = ""


class Risk(BaseModel):
    """A self-reported risk."""

    description: str
    severity: str  # blocker | major | minor
    file: str = ""
    source: str = ""
    task_id: str = ""


class CoverageItem(BaseModel):
    """A single item in the coverage matrix."""

    plan_item: str
    status: str  # implemented_verified | implemented_unverified | not_implemented
    evidence_ref: str = ""


class CrossTeamInterface(BaseModel):
    """A cross-team integration surface entry."""

    interface: str
    producer_team: str
    consumer_team: str
    status: str  # verified | unverified


class ReviewerComments(BaseModel):
    """Reviewer's assessment of gate evidence."""

    verdict: str = ""  # convinced | not_convinced
    reasoning: str = ""
    concerns: list[str] = Field(default_factory=list)


# ── Structured planning sub-models (traceability) ───────────────────────────


class Requirement(BaseModel):
    """A single numbered requirement with traceability ID."""

    id: str  # REQ-1, REQ-2, ...
    category: str  # functional | non-functional | security | performance
    description: str
    priority: str = "must"  # must | should | could
    citations: list[Citation] = Field(default_factory=list)


class AcceptanceCriterion(BaseModel):
    """A single user-grounded acceptance criterion."""

    id: str  # AC-1, AC-2, ...
    user_action: str
    expected_observation: str
    not_criteria: str = ""
    requirement_ids: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class JourneyStep(BaseModel):
    """A single step in a user journey."""

    step_number: int
    action: str
    observes: str
    not_criteria: str = ""
    citations: list[Citation] = Field(default_factory=list)


class Journey(BaseModel):
    """A complete user journey with traceability."""

    id: str  # J-1, J-2, ...
    name: str
    actor: str
    preconditions: str
    path_type: str = "happy"  # happy | failure
    failure_trigger: str = ""
    steps: list[JourneyStep] = Field(default_factory=list)
    outcome: str
    related_journey_id: str = ""
    requirement_ids: list[str] = Field(default_factory=list)


class SecurityProfile(BaseModel):
    """Security and risk assessment from the PRD."""

    compliance_requirements: str = ""
    data_sensitivity: str = ""
    pii_handling: str = ""
    auth_requirements: str = ""
    data_retention: str = ""
    third_party_exposure: str = ""
    data_residency: str = ""
    risk_mitigation_notes: str = ""


class DataEntity(BaseModel):
    """A data model entity definition."""

    name: str
    fields: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    is_new: bool = True


class CrossServiceImpact(BaseModel):
    """Impact on a specific service or package."""

    service: str
    impact: str
    action_needed: str


# ── Scoping sub-models ──────────────────────────────────────────────────────


class RepoSpec(BaseModel):
    """A single repository specification from scoping."""

    name: str  # e.g., "iriai-api"
    github_url: str = ""  # e.g., "github.com/org/repo"
    local_path: str = ""  # fallback local path
    action: str = "extend"  # "extend" | "new" | "read_only"
    template: str = ""  # for new repos
    relevance: str = ""  # why this repo is needed


class ScopeOutput(BaseModel):
    """Structured output from the scoping interview."""

    summary: str = ""
    scope_type: str = ""  # "new_application" | "service_change" | "package_update" | "cross_cutting"
    repos: list[RepoSpec] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    user_decisions: list[str] = Field(default_factory=list)
    complete: bool = False


class ProjectContext(BaseModel):
    """Structured project context replacing the flat string artifact."""

    feature_name: str
    scope_type: str = ""
    repos: list[RepoSpec] = Field(default_factory=list)
    worktree_root: str = ""
    workspace_path: str = ""
    outputs_path: str = ""  # .iriai/features/{slug}/outputs/ — for agent-generated files
    directory_map: str = ""


# ── Design sub-models ────────────────────────────────────────────────────────


class ComponentDef(BaseModel):
    """A UI component definition from the designer."""

    id: str  # CMP-1, CMP-2, ...
    name: str
    status: str  # new | extending
    location: str = ""
    description: str = ""
    props_variants: str = ""
    states: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class VerifiableState(BaseModel):
    """A visually/semantically distinguishable state for a component."""

    component_id: str
    state_name: str
    visual_description: str


class JourneyUXAnnotation(BaseModel):
    """UX annotations layered on top of a PRD journey."""

    journey_id: str
    step_annotations: list[str] = Field(default_factory=list)
    error_path_ux: str = ""
    empty_state_ux: str = ""
    not_criteria: list[str] = Field(default_factory=list)


# ── Architecture sub-models ──────────────────────────────────────────────────


class FileScope(BaseModel):
    """A file with read/write distinction and existence status."""

    path: str
    action: str  # create | modify | read


class ImplementationStep(BaseModel):
    """A structured implementation step from the architect."""

    id: str  # STEP-1, STEP-2, ...
    objective: str
    scope: list[FileScope] = Field(default_factory=list)
    instructions: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    counterexamples: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    journey_ids: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class VerifyBlock(BaseModel):
    """A structured verification expectation."""

    type: str  # browser | api | database
    expectation: str


class JourneyVerifyStep(BaseModel):
    """A journey step with technical verification."""

    step_number: int
    verify_blocks: list[VerifyBlock] = Field(default_factory=list)
    data_testids: list[str] = Field(default_factory=list)


class JourneyVerification(BaseModel):
    """Architect's structured journey with verify blocks."""

    journey_id: str
    steps: list[JourneyVerifyStep] = Field(default_factory=list)


class ArchitecturalRisk(BaseModel):
    """A risk identified by the architect."""

    id: str  # RISK-1, RISK-2, ...
    description: str
    severity: str  # high | medium | low
    mitigation: str = ""
    affected_step_ids: list[str] = Field(default_factory=list)


# ── Task planning sub-models ─────────────────────────────────────────────────


class TaskAcceptanceCriterion(BaseModel):
    """Acceptance criteria specific to an implementation task."""

    description: str
    not_criteria: str = ""


class TaskFileScope(BaseModel):
    """File scope for an implementation task with read/write distinction."""

    path: str
    action: str  # create | modify | read_only


# ── Subfeature decomposition models ──────────────────────────────────────────


class SubfeatureEdge(BaseModel):
    """A directed interface between two subfeatures."""

    from_subfeature: str  # slug of the producing subfeature
    to_subfeature: str  # slug of the consuming subfeature
    interface_type: str  # data_flow | event | shared_state | api_call | ui_navigation
    description: str  # what crosses the boundary
    data_contract: str = ""  # shape/schema of what's exchanged
    owner: str = ""  # which subfeature owns the contract
    citations: list[Citation] = Field(default_factory=list)


class Subfeature(BaseModel):
    """A decomposed unit of work identified by the Lead."""

    id: str  # SF-1, SF-2, ...
    slug: str  # e.g., "visual-workflow-canvas"
    name: str
    description: str
    rationale: str = ""
    requirement_ids: list[str] = Field(default_factory=list)
    journey_ids: list[str] = Field(default_factory=list)


class SubfeatureDecomposition(BaseModel):
    """Output of the Lead's decomposition step."""

    subfeatures: list[Subfeature] = Field(default_factory=list)
    edges: list[SubfeatureEdge] = Field(default_factory=list)
    decomposition_rationale: str = ""
    complete: bool = False


class EdgeCheck(BaseModel):
    """Lead's assessment of one edge between subfeatures."""

    edge_from: str
    edge_to: str
    interface_type: str
    status: str = ""  # consistent | contradiction | missing | underspecified
    detail: str = ""


class IntegrationReview(BaseModel):
    """Output of the Lead's integration review of per-subfeature artifacts."""

    needs_revision: bool = Field(
        default=False,
        description="Set to true if ANY subfeature artifact needs changes. When true, revision_instructions MUST be non-empty.",
    )
    summary: str = ""
    edge_consistency: list[EdgeCheck] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    revision_instructions: dict[str, str] = Field(
        default_factory=dict,
        description="Map of subfeature slug to revision instruction. Keys must be exact subfeature slugs from the decomposition. Required when needs_revision is true.",
    )
    complete: bool = False


class RevisionRequest(BaseModel):
    """A single change requested during gate review."""

    description: str  # what the user wants changed
    reasoning: str  # why (new decision)
    affected_subfeatures: list[str] = Field(
        default_factory=list
    )  # slugs of subfeatures that need to change
    affected_requirement_ids: list[str] = Field(default_factory=list)
    cross_subfeature: bool = False  # true if the change spans multiple subfeatures


class RevisionPlan(BaseModel):
    """Lead agent's plan for routing revisions to subfeature agents."""

    requests: list[RevisionRequest] = Field(default_factory=list)
    new_decisions: list[str] = Field(default_factory=list)  # decisions captured during review
    complete: bool = False


class ReviewOutcome(BaseModel):
    """Output of the interview-based gate review."""

    approved: bool = Field(
        default=False,
        description="Set to true when the user explicitly approves. Do NOT set true if the user requested changes.",
    )
    revision_plan: RevisionPlan = Field(
        default_factory=RevisionPlan,
        description="Required when approved is false. Contains the specific revision requests from the user.",
    )
    complete: bool = False

    @model_validator(mode="after")
    def _revisions_override_approval(self) -> ReviewOutcome:
        """If revision_plan has requests, approved must be False.

        Agents sometimes set approved=True after discussing changes with the
        user, conflating 'user agreed changes are needed' with 'user approves
        the artifact as-is'.  Revisions always take priority.
        """
        if self.approved and self.revision_plan.requests:
            self.approved = False
        return self


class GlobalImplementationStrategy(BaseModel):
    """Global implementation strategy established before per-subfeature task planning."""

    subfeature_execution_order: list[str] = Field(default_factory=list)  # sf slugs in order
    shared_infrastructure_tasks: list[str] = Field(default_factory=list)
    cross_subfeature_dependencies: list[str] = Field(default_factory=list)
    parallel_opportunities: list[str] = Field(default_factory=list)
    execution_constraints: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    complete: bool = False


# ── System Design sub-models (for interactive HTML artifact) ─────────────────


class ServiceNode(BaseModel):
    """A service/component in the system topology."""

    id: str
    name: str
    kind: str  # service | database | queue | cache | external | frontend
    description: str
    technology: str = ""
    port: str = ""
    journeys: list[str] = Field(default_factory=list)


class ServiceConnection(BaseModel):
    """A directed connection between two services."""

    from_id: str
    to_id: str
    label: str
    protocol: str = ""  # REST | gRPC | WebSocket | AMQP | SQL | Redis
    journeys: list[str] = Field(default_factory=list)


class APIEndpoint(BaseModel):
    """A single API endpoint."""

    method: str  # GET | POST | PUT | DELETE | PATCH
    path: str
    service_id: str
    description: str
    request_body: str = ""
    response_body: str = ""
    auth: str = ""


class APICallStep(BaseModel):
    """A single step in an API call path (sequence diagram entry)."""

    sequence: int
    from_service: str
    to_service: str
    action: str
    description: str
    returns: str = ""


class APICallPath(BaseModel):
    """A sequence of API calls for a specific operation."""

    id: str
    name: str
    description: str
    journey_id: str = ""
    steps: list[APICallStep] = Field(default_factory=list)


class EntityField(BaseModel):
    """A field in an entity/table."""

    name: str
    type: str
    constraints: str = ""
    description: str = ""


class Entity(BaseModel):
    """A database entity or data model."""

    id: str
    name: str
    service_id: str
    fields: list[EntityField] = Field(default_factory=list)
    journeys: list[str] = Field(default_factory=list)


class EntityRelation(BaseModel):
    """A relationship between entities."""

    from_entity: str
    to_entity: str
    kind: str  # one-to-many | many-to-many | one-to-one
    label: str = ""


class SystemDesign(BaseModel):
    """Complete system design produced by the architect phase."""

    title: str = ""
    overview: str = ""
    services: list[ServiceNode] = Field(default_factory=list)
    connections: list[ServiceConnection] = Field(default_factory=list)
    api_endpoints: list[APIEndpoint] = Field(default_factory=list)
    call_paths: list[APICallPath] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    entity_relations: list[EntityRelation] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    complete: bool = False


# ── Core output models ───────────────────────────────────────────────────────


class PRD(BaseModel):
    title: str = ""
    overview: str = ""
    problem_statement: str = ""
    target_users: str = ""
    # Structured
    structured_requirements: list[Requirement] = Field(default_factory=list)
    structured_acceptance_criteria: list[AcceptanceCriterion] = Field(
        default_factory=list
    )
    journeys: list[Journey] = Field(default_factory=list)
    security_profile: SecurityProfile = Field(default_factory=SecurityProfile)
    data_entities: list[DataEntity] = Field(default_factory=list)
    cross_service_impacts: list[CrossServiceImpact] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    # Legacy
    requirements: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    complete: bool = False


class DesignDecisions(BaseModel):
    approach: str = ""
    # Structured
    journey_annotations: list[JourneyUXAnnotation] = Field(default_factory=list)
    component_defs: list[ComponentDef] = Field(default_factory=list)
    verifiable_states: list[VerifiableState] = Field(default_factory=list)
    responsive_behavior: str = ""
    interaction_patterns: str = ""
    accessibility_notes: str = ""
    # Legacy
    components: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    rationale: str = ""
    complete: bool = False


class TechnicalPlan(BaseModel):
    architecture: str = ""
    # Structured
    steps: list[ImplementationStep] = Field(default_factory=list)
    journey_verifications: list[JourneyVerification] = Field(default_factory=list)
    file_manifest: list[FileScope] = Field(default_factory=list)
    architectural_risks: list[ArchitecturalRisk] = Field(default_factory=list)
    testid_registry: list[str] = Field(default_factory=list)
    # Legacy
    files_to_create: list[str] = Field(default_factory=list)
    files_to_modify: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    implementation_steps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    complete: bool = False


class ArchitectureOutput(BaseModel):
    """Combined output from the architecture interview."""

    plan: TechnicalPlan = Field(default_factory=TechnicalPlan)
    system_design: SystemDesign = Field(default_factory=SystemDesign)
    complete: bool = False


class Verdict(BaseModel):
    """Review verdict used by QA roles, reviewers, and the plan compiler."""

    approved: bool = Field(
        description="True if the artifact passes review. False if concerns or gaps are blocking.",
    )
    summary: str
    concerns: list[Issue] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)


# ── Generic envelope for Interview phases ────────────────────────────────────


class Envelope(BaseModel, Generic[T]):
    """Structured output for every interview turn.

    Control fields (``question``, ``options``, ``complete``, ``artifact_path``)
    are flat primitives — agents never need to construct nested objects to
    signal completion.  Complex artifacts are written to files; the ``output``
    field is optional when file-based artifacts are used.

    ``output`` stays nullable so the agent can write ``"output": null``
    during the interview.  All inner ``anyOf`` patterns (e.g.
    ``security_profile``, ``question``) have been removed by giving every
    nested field a non-nullable default — the only remaining ``anyOf`` is
    this top-level ``T | null``.
    """

    question: str = Field(
        default="",
        description="Your question for the user. MUST be non-empty when you want user input. Leave empty only when working silently (tool use, investigation).",
    )
    options: list[str] = Field(
        default_factory=list,
        description="Response options for the user to choose from. Include a 'Delegate to you' option when appropriate.",
    )
    output: T | None = None
    complete: bool = Field(
        default=False,
        description="Set to true ONLY when the artifact is fully written to a file and ready for review.",
    )
    artifact_path: str = Field(
        default="",
        description="Absolute path to the artifact file you wrote. Set together with complete=true.",
    )


def envelope_done(response: object) -> bool:
    """Interview done-predicate: true when the envelope signals completion.

    Checks flat ``complete`` first (preferred), falls back to nested
    ``output.complete`` for backward compatibility.
    """
    if not isinstance(response, Envelope):
        return False
    if response.complete:
        return True
    # Backward compat: nested output.complete
    if response.output is not None and hasattr(response.output, "complete"):
        return response.output.complete
    return False


# ── Implementation DAG models ────────────────────────────────────────────────


class TaskReference(BaseModel):
    """An excerpt from an upstream artifact embedded in a task for implementer context."""

    source: str  # e.g., "PRD REQ-3", "Design CMP-2", "Plan D-SF1-10", "SystemDesign entity:NodeBase"
    content: str  # the actual text/spec the implementer needs


class ImplementationTask(BaseModel):
    """A single unit of work in the implementation DAG."""

    id: str
    name: str
    description: str
    # Structured
    file_scope: list[TaskFileScope] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    step_ids: list[str] = Field(default_factory=list)
    journey_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: list[TaskAcceptanceCriterion] = Field(default_factory=list)
    counterexamples: list[str] = Field(default_factory=list)
    security_concerns: list[str] = Field(default_factory=list)
    testid_assignments: list[str] = Field(default_factory=list)
    reference_material: list[TaskReference] = Field(
        default_factory=list,
        description=(
            "Excerpts from upstream artifacts (PRD requirements, design component "
            "specs, plan decisions, system design entities, mockup component specs) "
            "that the implementer needs. Each entry is self-contained — the "
            "implementer should not need to consult the full artifact."
        ),
    )
    subfeature_id: str = ""  # SF-1, SF-2, ... for traceability
    # Legacy / DAG metadata
    files: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    team: int = 0


class ImplementationDAG(BaseModel):
    """Directed acyclic graph of implementation tasks with team assignments."""

    tasks: list[ImplementationTask] = Field(default_factory=list)
    num_teams: int = 0
    execution_order: list[list[str]] = Field(default_factory=list)
    requirement_coverage: dict[str, list[str]] = Field(default_factory=dict)
    complete: bool = False


class ImplementationResult(BaseModel):
    """Structured output for the implementer's work summary."""

    task_id: str
    summary: str
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    notes: str = ""
    deviations: list[Deviation] = Field(default_factory=list)
    self_reported_risks: list[Risk] = Field(default_factory=list)


class BugFixAttempt(BaseModel):
    """Record of a single bug fix attempt during implementation."""

    bug_id: str  # e.g., "QA-FAIL-1", "REVIEW-FAIL-3"
    group_id: str = ""  # triage group ID (e.g., "BG-1"), empty for single-bug path
    source_verdict: str  # which reviewer found it (qa_engineer, code_reviewer, etc.)
    description: str  # what the bug is
    root_cause: str  # RCA finding
    fix_applied: str  # what the implementer did
    files_modified: list[str] = Field(default_factory=list)
    re_verify_result: str  # PASS or FAIL
    attempt_number: int = 1


class BugGroup(BaseModel):
    """A group of related issues sharing a likely root cause."""

    group_id: str  # e.g., "BG-1"
    likely_root_cause: str  # one-sentence hypothesis
    issue_indices: list[int] = Field(default_factory=list)  # into Verdict.concerns
    gap_indices: list[int] = Field(default_factory=list)  # into Verdict.gaps
    severity: str  # blocker | major | minor (worst in group)
    affected_files_hint: list[str] = Field(default_factory=list)


class BugTriage(BaseModel):
    """Triage output: ALL issues grouped by likely root cause. No deferrals."""

    groups: list[BugGroup] = Field(default_factory=list)
    rationale: str = ""


class BugFixResult(BaseModel):
    """Structured output for the bug fixer's work summary."""

    summary: str
    root_cause: str
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    notes: str = ""


# ── Handover document ────────────────────────────────────────────────────────


class TaskOutcome(BaseModel):
    """Record of a single completed task or attempt."""

    task_id: str = ""
    task_name: str = ""
    status: str = ""  # completed | failed | partial
    summary: str = ""
    files_changed: list[str] = Field(default_factory=list)
    deviations: list[Deviation] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    failure_reason: str = ""


class HandoverDoc(BaseModel):
    """Cumulative context passed between tasks, groups, and phases.

    Tracks what was done, what worked, what failed, and what the next
    agent needs to know.  Failed attempts are NEVER compressed.
    """

    summary_of_prior_work: str = ""
    completed: list[TaskOutcome] = Field(default_factory=list)
    failed_attempts: list[TaskOutcome] = Field(default_factory=list)
    all_files_changed: list[str] = Field(default_factory=list)
    active_risks: list[Risk] = Field(default_factory=list)
    key_decisions: list[str] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)
    notes: str = ""

    def compress(self, max_chars: int = 100_000, keep_recent: int = 3) -> None:
        """Summarize older completed entries if handover exceeds *max_chars*.

        Failed attempts are never compressed — critical for knowing what
        didn't work and why.
        """
        if len(self.model_dump_json()) <= max_chars:
            return
        if len(self.completed) <= keep_recent:
            return

        old = self.completed[:-keep_recent]
        self.completed = self.completed[-keep_recent:]

        old_text = "\n".join(
            f"- {t.task_id}: {t.summary} (files: {', '.join(t.files_changed[:5])})"
            for t in old
        )
        self.summary_of_prior_work += (
            f"\nPrior completed work ({len(old)} tasks):\n{old_text}\n"
        )

    def record_success(self, result: ImplementationResult) -> None:
        """Record a successful implementation task."""
        files = result.files_created + result.files_modified
        self.completed.append(TaskOutcome(
            task_id=result.task_id,
            task_name=result.task_id,
            status="completed",
            summary=result.summary,
            files_changed=files,
            deviations=result.deviations,
            risks=result.self_reported_risks,
        ))
        self.all_files_changed.extend(files)
        self.active_risks.extend(result.self_reported_risks)

    def record_failure(
        self, task_id: str, summary: str, failure_reason: str
    ) -> None:
        """Record a failed task or fix attempt."""
        self.failed_attempts.append(TaskOutcome(
            task_id=task_id,
            status="failed",
            summary=summary,
            failure_reason=failure_reason,
        ))


# ── Orchestration output models ──────────────────────────────────────────────


class OrchestratorVerdict(BaseModel):
    """Structured output for orchestrator gate reviews."""

    verdict: str  # APPROVE | REJECT
    summary: str
    coverage_matrix: list[CoverageItem] = Field(default_factory=list)
    deviations: list[Deviation] = Field(default_factory=list)
    self_reported_risks: list[Risk] = Field(default_factory=list)
    reviewer_comments: ReviewerComments = Field(default_factory=ReviewerComments)


class BugReport(BaseModel):
    """Structured output from the bug intake interview."""

    title: str = ""
    description: str = ""
    steps_to_reproduce: list[str] = Field(default_factory=list)
    expected_behavior: str = ""
    actual_behavior: str = ""
    severity: str = ""  # blocker | major | minor
    affected_area: str = ""  # which service/area of the platform
    error_messages: list[str] = Field(default_factory=list)
    additional_context: str = ""
    complete: bool = False


class ReproductionResult(BaseModel):
    """Output from the bug reproduction agent."""

    reproduced: bool
    steps_executed: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    error_messages: list[str] = Field(default_factory=list)
    summary: str


class RootCauseAnalysis(BaseModel):
    """Output from a root cause analyst."""

    hypothesis: str
    evidence: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    proposed_approach: str  # conceptual fix strategy, not code
    confidence: str  # high | medium | low
    alternative_hypotheses: list[str] = Field(default_factory=list)


class FeatureLeadVerdict(BaseModel):
    """Structured output for feature lead gate reviews."""

    verdict: str  # APPROVE | REJECT
    summary: str
    coverage_matrix: list[CoverageItem] = Field(default_factory=list)
    cross_team_surface: list[CrossTeamInterface] = Field(default_factory=list)
    deviations: list[Deviation] = Field(default_factory=list)
    reviewer_comments: ReviewerComments = Field(default_factory=ReviewerComments)
