from iriai_compose import Role

from .._loader import load_prompt
from ...config import BUDGET_TIERS

role = Role(
    name="analytics-engineer",
    prompt=load_prompt(__file__),
    tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    model=BUDGET_TIERS["opus"],
)
