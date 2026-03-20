from iriai_compose import Role

from .._loader import load_prompt
from ...config import BUDGET_TIERS

role = Role(
    name="lead-task-planner",
    prompt=load_prompt(__file__),
    tools=["Read", "Glob", "Grep", "WebSearch", "WebFetch"],
    model=BUDGET_TIERS["opus"],
)
