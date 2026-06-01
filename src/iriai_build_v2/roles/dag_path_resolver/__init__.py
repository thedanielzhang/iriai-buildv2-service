from iriai_compose import Role

from .._loader import load_prompt
from ...config import BUDGET_TIERS

role = Role(
    name="dag-path-resolver",
    prompt=load_prompt(__file__),
    tools=["Read", "Glob", "Grep"],
    model=BUDGET_TIERS["sonnet"],
    metadata={},
)
