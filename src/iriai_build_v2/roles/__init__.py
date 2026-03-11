from __future__ import annotations

from iriai_compose import AgentActor, InteractionActor

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
from .bug_interviewer import role as bug_interviewer_role
from .bug_reproducer import role as bug_reproducer_role
from .root_cause_analyst import role as root_cause_analyst_role
from .bug_fixer import role as bug_fixer_role

# Backward compat aliases
task_planner_role = planning_lead_role
qa_engineer_role = smoke_tester_role
reviewer_role = code_reviewer_role

# ── Actors ──────────────────────────────────────────────────────────────────
pm = AgentActor(name="pm", role=pm_role, context_keys=["project"])
designer = AgentActor(name="designer", role=designer_role, context_keys=["project", "prd"])
architect = AgentActor(
    name="architect",
    role=architect_role,
    context_keys=["project", "prd", "design"],
    persistent=True,
)
plan_compiler = AgentActor(
    name="plan-compiler",
    role=plan_compiler_role,
    context_keys=["plan", "prd", "design"],
)
planning_lead = AgentActor(
    name="planning-lead",
    role=planning_lead_role,
    context_keys=["project", "plan", "prd", "design"],
)
feature_lead = AgentActor(
    name="feature-lead",
    role=feature_lead_role,
    context_keys=["project", "plan", "prd", "design"],
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
    context_keys=["project", "plan"],
)
frontend_implementer = AgentActor(
    name="frontend-implementer",
    role=frontend_implementer_role,
    context_keys=["project", "plan"],
)
database_implementer = AgentActor(
    name="database-implementer",
    role=database_implementer_role,
    context_keys=["project", "plan"],
)
package_implementer = AgentActor(
    name="package-implementer",
    role=package_implementer_role,
    context_keys=["project", "plan"],
)
implementer = AgentActor(
    name="implementer",
    role=implementer_role,
    context_keys=["project", "plan"],
)
test_author = AgentActor(
    name="test-author",
    role=test_author_role,
    context_keys=["project", "plan"],
)
integration_tester = AgentActor(
    name="integration-tester",
    role=integration_tester_role,
    context_keys=["project", "plan", "prd"],
)
regression_tester = AgentActor(
    name="regression-tester",
    role=regression_tester_role,
    context_keys=["project", "plan"],
)
smoke_tester = AgentActor(
    name="smoke-tester",
    role=smoke_tester_role,
    context_keys=["project", "plan", "prd"],
)
code_reviewer = AgentActor(
    name="code-reviewer",
    role=code_reviewer_role,
    context_keys=["project", "plan"],
)
security_auditor = AgentActor(
    name="security-auditor",
    role=security_auditor_role,
    context_keys=["project", "plan"],
)
accessibility_auditor = AgentActor(
    name="accessibility-auditor",
    role=accessibility_auditor_role,
    context_keys=["project", "plan"],
)
performance_analyst = AgentActor(
    name="performance-analyst",
    role=performance_analyst_role,
    context_keys=["project", "plan"],
)
verifier = AgentActor(
    name="verifier",
    role=verifier_role,
    context_keys=["project", "plan", "prd"],
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

bug_interviewer = AgentActor(name="bug-interviewer", role=bug_interviewer_role, context_keys=["project"])
bug_reproducer = AgentActor(name="bug-reproducer", role=bug_reproducer_role, context_keys=["project"])
root_cause_analyst = AgentActor(name="root-cause-analyst", role=root_cause_analyst_role, context_keys=["project"])
rca_symptoms_analyst = AgentActor(name="rca-symptoms", role=root_cause_analyst_role, context_keys=["project"])
rca_architecture_analyst = AgentActor(name="rca-architecture", role=root_cause_analyst_role, context_keys=["project"])
bug_fixer = AgentActor(name="bug-fixer", role=bug_fixer_role, context_keys=["project"])

user = InteractionActor(name="user", resolver="terminal")
