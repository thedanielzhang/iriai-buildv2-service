from __future__ import annotations

from typing import Any, ClassVar

from iriai_compose import AgentActor, InteractionActor

_ENVELOPE_INSTRUCTIONS = """\

## Structured Output Format

Your responses use the Envelope format with these fields:
- `question`: Your next question for the user (set this while interviewing)
- `options`: Optional suggested answers for the user to choose from
- `complete`: Set to `true` when the interview is finished and the artifact is ready
- `artifact_path`: The file path where you wrote the artifact (set alongside `complete`)
- `output`: Optional — leave as null when writing file-based artifacts

**Rules:**
- During the interview: set `question`, leave `complete` as false
- When done: write artifact to the file path specified in your prompt, then set `complete = true` and `artifact_path` to the path you wrote
- NEVER set `complete` while still asking questions — this terminates the interview immediately

**Critical — text vs structured output:**
- Your text response is internal reasoning. The user NEVER sees it — only the `question` field is displayed.
- Do NOT describe, summarize, or present the artifact in your text response.
"""


class InterviewActor(AgentActor):
    """AgentActor for interview phases. Appends Envelope usage instructions to the role prompt."""

    _envelope_instructions: ClassVar[str] = _ENVELOPE_INSTRUCTIONS

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        self.role = self.role.model_copy(update={
            "prompt": self.role.prompt + self._envelope_instructions,
        })

# ── Role imports ────────────────────────────────────────────────────────────
from .pm import role as pm_role
from .designer import role as designer_role
from .architect import role as architect_role
from .plan_compiler import role as plan_compiler_role
from .planning_lead import role as planning_lead_role
from .feature_lead import role as feature_lead_role
from .orchestrator import role as orchestrator_role
from .operator import role as operator_role
from .backend_implementer import role as backend_implementer_role
from .frontend_implementer import role as frontend_implementer_role
from .database_implementer import role as database_implementer_role
from .package_implementer import role as package_implementer_role
from .implementer import role as implementer_role
from .test_author import role as test_author_role
from .integration_tester import role as integration_tester_role
from .regression_tester import role as regression_tester_role
from .smoke_tester import role as smoke_tester_role
from .code_reviewer import role as code_reviewer_role
from .security_auditor import role as security_auditor_role
from .accessibility_auditor import role as accessibility_auditor_role
from .performance_analyst import role as performance_analyst_role
from .verifier import role as verifier_role
from .analytics_engineer import role as analytics_engineer_role
from .deployer import role as deployer_role
from .observability_engineer import role as observability_engineer_role
from .release_manager import role as release_manager_role
from .documentation import role as documentation_role
from .ui_designer import role as ui_designer_role
from .ux_designer import role as ux_designer_role
from .scoper import role as scoper_role
from .bug_interviewer import role as bug_interviewer_role
from .bug_reproducer import role as bug_reproducer_role
from .root_cause_analyst import role as root_cause_analyst_role
from .bug_fixer import role as bug_fixer_role
from .lead_pm import role as lead_pm_role
from .lead_designer import role as lead_designer_role
from .lead_architect import role as lead_architect_role
from .lead_task_planner import role as lead_task_planner_role
from .compiler import role as compiler_role
from .summarizer import role as summarizer_role
from .citation_reviewer import role as citation_reviewer_role

# Backward compat aliases
task_planner_role = planning_lead_role
qa_engineer_role = smoke_tester_role
reviewer_role = code_reviewer_role

# ── Actors ──────────────────────────────────────────────────────────────────
scoper = InterviewActor(name="scoper", role=scoper_role, context_keys=["project"])
pm = InterviewActor(name="pm", role=pm_role, context_keys=["project", "scope"])
designer = InterviewActor(name="designer", role=designer_role, context_keys=["project", "prd"])
architect = InterviewActor(
    name="architect",
    role=architect_role,
    context_keys=["project", "prd", "design"],
    persistent=True,
)
plan_compiler = AgentActor(
    name="plan-compiler",
    role=plan_compiler_role,
    context_keys=["plan", "prd", "design", "system-design", "mockup", "scope"],
)
plan_completeness_reviewer = AgentActor(
    name="plan-completeness-reviewer",
    role=plan_compiler_role,
    context_keys=["plan", "prd", "design", "system-design", "mockup", "scope"],
)
plan_security_reviewer = AgentActor(
    name="plan-security-reviewer",
    role=plan_compiler_role,
    context_keys=["plan", "prd", "design", "system-design", "mockup", "scope"],
)
planning_lead = InterviewActor(
    name="planning-lead",
    role=planning_lead_role,
    context_keys=["project", "plan", "prd", "design", "system-design", "mockup"],
)
feature_lead = AgentActor(
    name="feature-lead",
    role=feature_lead_role,
    context_keys=["project", "plan", "prd", "design", "system-design"],
)
orchestrator = AgentActor(
    name="orchestrator",
    role=orchestrator_role,
    context_keys=["project", "plan"],
)
operator = AgentActor(
    name="operator",
    role=operator_role,
    context_keys=["project"],
)
backend_implementer = AgentActor(
    name="backend-implementer",
    role=backend_implementer_role,
    context_keys=["project"],
)
frontend_implementer = AgentActor(
    name="frontend-implementer",
    role=frontend_implementer_role,
    context_keys=["project"],
)
database_implementer = AgentActor(
    name="database-implementer",
    role=database_implementer_role,
    context_keys=["project"],
)
package_implementer = AgentActor(
    name="package-implementer",
    role=package_implementer_role,
    context_keys=["project"],
)
implementer = AgentActor(
    name="implementer",
    role=implementer_role,
    context_keys=["project"],  # plan excluded — task reference_material has relevant excerpts
)
test_author = AgentActor(
    name="test-author",
    role=test_author_role,
    context_keys=["project"],
)
integration_tester = AgentActor(
    name="integration-tester",
    role=integration_tester_role,
    context_keys=["project"],
)
regression_tester = AgentActor(
    name="regression-tester",
    role=regression_tester_role,
    context_keys=["project"],
)
smoke_tester = AgentActor(
    name="smoke-tester",
    role=smoke_tester_role,
    context_keys=["project"],
)
code_reviewer = AgentActor(
    name="code-reviewer",
    role=code_reviewer_role,
    context_keys=["project"],
)
security_auditor = AgentActor(
    name="security-auditor",
    role=security_auditor_role,
    context_keys=["project"],
)
accessibility_auditor = AgentActor(
    name="accessibility-auditor",
    role=accessibility_auditor_role,
    context_keys=["project", "plan", "design", "mockup"],
)
performance_analyst = AgentActor(
    name="performance-analyst",
    role=performance_analyst_role,
    context_keys=["project", "plan", "system-design"],
)
verifier = AgentActor(
    name="verifier",
    role=verifier_role,
    context_keys=["project"],
)
analytics_engineer = AgentActor(
    name="analytics-engineer",
    role=analytics_engineer_role,
    context_keys=["project", "plan"],
)
deployer = AgentActor(
    name="deployer",
    role=deployer_role,
    context_keys=["project", "plan"],
)
observability_engineer = AgentActor(
    name="observability-engineer",
    role=observability_engineer_role,
    context_keys=["project", "plan"],
)
release_manager = AgentActor(
    name="release-manager",
    role=release_manager_role,
    context_keys=["project", "plan"],
)
documentation_writer = AgentActor(
    name="documentation",
    role=documentation_role,
    context_keys=["project", "plan"],
)
ui_designer = AgentActor(
    name="ui-designer",
    role=ui_designer_role,
    context_keys=["project", "prd", "design"],
)
ux_designer = AgentActor(
    name="ux-designer",
    role=ux_designer_role,
    context_keys=["project", "prd", "design"],
)

# Backward compat aliases for actors
task_planner = planning_lead
qa_engineer = smoke_tester
reviewer = code_reviewer

# ── Subfeature decomposition actors ────────────────────────────────────────
lead_pm = InterviewActor(
    name="lead-pm", role=lead_pm_role, context_keys=["project", "scope"],
)
lead_pm_decomposer = InterviewActor(
    name="lead-pm-decomposer", role=lead_pm_role, context_keys=["project", "scope", "prd:broad"],
)
lead_pm_reviewer = InterviewActor(
    name="lead-pm-reviewer", role=lead_pm_role, context_keys=["project", "scope"],
)
lead_pm_gate_reviewer = InterviewActor(
    name="lead-pm-gate-reviewer", role=lead_pm_role, context_keys=["project", "scope"],
)
pm_compiler = AgentActor(
    name="pm-compiler", role=compiler_role, context_keys=[],
)
artifact_summarizer = AgentActor(
    name="summarizer", role=summarizer_role, context_keys=[],
)
citation_reviewer = AgentActor(
    name="citation-reviewer", role=citation_reviewer_role, context_keys=[],
)

# Lead designer actors
lead_designer = InterviewActor(
    name="lead-designer", role=lead_designer_role, context_keys=["project", "scope", "prd", "decomposition"],
)
lead_designer_reviewer = InterviewActor(
    name="lead-designer-reviewer", role=lead_designer_role, context_keys=["project", "scope", "prd", "decomposition"],
)
lead_designer_gate_reviewer = InterviewActor(
    name="lead-designer-gate-reviewer", role=lead_designer_role, context_keys=["project", "scope", "prd", "decomposition"],
)
design_compiler = AgentActor(
    name="design-compiler", role=compiler_role, context_keys=[],
)

# Lead architect actors
lead_architect = InterviewActor(
    name="lead-architect", role=lead_architect_role, context_keys=["project", "scope", "prd", "design", "decomposition"],
)
lead_architect_reviewer = InterviewActor(
    name="lead-architect-reviewer", role=lead_architect_role, context_keys=["project", "scope", "prd", "design", "decomposition"],
)
lead_architect_gate_reviewer = InterviewActor(
    name="lead-architect-gate-reviewer", role=lead_architect_role, context_keys=["project", "scope", "prd", "design", "decomposition"],
)
plan_arch_compiler = AgentActor(
    name="plan-arch-compiler", role=compiler_role, context_keys=[],
)
sysdesign_compiler = AgentActor(
    name="sysdesign-compiler", role=compiler_role, context_keys=[],
)

# Lead task planner actors
lead_task_planner = InterviewActor(
    name="lead-task-planner", role=lead_task_planner_role,
    context_keys=["project", "scope", "prd", "design", "plan", "system-design", "mockup", "decomposition"],
)
lead_task_planner_reviewer = InterviewActor(
    name="lead-task-planner-reviewer", role=lead_task_planner_role,
    context_keys=["project", "scope", "prd", "design", "plan", "system-design", "mockup", "decomposition"],
)
lead_task_planner_gate_reviewer = InterviewActor(
    name="lead-task-planner-gate-reviewer", role=lead_task_planner_role,
    context_keys=["project", "scope", "prd", "design", "plan", "system-design", "mockup", "decomposition"],
)
dag_compiler = AgentActor(
    name="dag-compiler", role=compiler_role, context_keys=[],
)

bug_interviewer = AgentActor(name="bug-interviewer", role=bug_interviewer_role, context_keys=["project"])
bug_reproducer = AgentActor(name="bug-reproducer", role=bug_reproducer_role, context_keys=["project"])
root_cause_analyst = AgentActor(name="root-cause-analyst", role=root_cause_analyst_role, context_keys=["project"])
rca_symptoms_analyst = AgentActor(name="rca-symptoms", role=root_cause_analyst_role, context_keys=["project"])
rca_architecture_analyst = AgentActor(name="rca-architecture", role=root_cause_analyst_role, context_keys=["project"])
bug_fixer = AgentActor(name="bug-fixer", role=bug_fixer_role, context_keys=["project"])

user = InteractionActor(name="user", resolver="terminal")
