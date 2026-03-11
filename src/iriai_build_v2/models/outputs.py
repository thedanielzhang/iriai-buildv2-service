from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


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
    file: str | None = None
    line: int | None = None


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

    verdict: str  # convinced | not_convinced
    reasoning: str
    concerns: list[str] = Field(default_factory=list)


# ── Core output models ───────────────────────────────────────────────────────


class PRD(BaseModel):
    title: str
    overview: str
    requirements: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    complete: bool = False


class DesignDecisions(BaseModel):
    approach: str
    components: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    rationale: str = ""


class TechnicalPlan(BaseModel):
    architecture: str
    files_to_create: list[str] = Field(default_factory=list)
    files_to_modify: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    implementation_steps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class Verdict(BaseModel):
    """Review verdict used by QA roles, reviewers, and the plan compiler."""

    approved: bool
    summary: str
    concerns: list[Issue] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)


# ── Generic envelope for Interview phases ────────────────────────────────────


class Envelope(BaseModel, Generic[T]):
    """Structured output for every interview turn.

    Populate ``question``/``options`` while gathering info.
    Populate ``output`` with the final artifact when done.
    """

    question: str | None = None
    options: list[str] = Field(default_factory=list)
    output: T | None = None


def envelope_done(response: object) -> bool:
    """Interview done-predicate: true when the envelope's output is populated."""
    return isinstance(response, Envelope) and response.output is not None


# ── Implementation DAG models ────────────────────────────────────────────────


class ImplementationTask(BaseModel):
    """A single unit of work in the implementation DAG."""

    id: str
    name: str
    description: str
    files: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    team: int = 0


class ImplementationDAG(BaseModel):
    """Directed acyclic graph of implementation tasks with team assignments."""

    tasks: list[ImplementationTask]
    num_teams: int
    execution_order: list[list[str]]


class ImplementationResult(BaseModel):
    """Structured output for the implementer's work summary."""

    task_id: str
    summary: str
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    notes: str = ""
    deviations: list[Deviation] = Field(default_factory=list)
    self_reported_risks: list[Risk] = Field(default_factory=list)


class BugFixResult(BaseModel):
    """Structured output for the bug fixer's work summary."""

    summary: str
    root_cause: str
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    notes: str = ""


# ── Orchestration output models ──────────────────────────────────────────────


class OrchestratorVerdict(BaseModel):
    """Structured output for orchestrator gate reviews."""

    verdict: str  # APPROVE | REJECT
    summary: str
    coverage_matrix: list[CoverageItem] = Field(default_factory=list)
    deviations: list[Deviation] = Field(default_factory=list)
    self_reported_risks: list[Risk] = Field(default_factory=list)
    reviewer_comments: ReviewerComments | None = None


class BugReport(BaseModel):
    """Structured output from the bug intake interview."""

    title: str
    description: str
    steps_to_reproduce: list[str] = Field(default_factory=list)
    expected_behavior: str
    actual_behavior: str
    severity: str  # blocker | major | minor
    affected_area: str  # which service/area of the platform
    error_messages: list[str] = Field(default_factory=list)
    additional_context: str = ""


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
    reviewer_comments: ReviewerComments | None = None
