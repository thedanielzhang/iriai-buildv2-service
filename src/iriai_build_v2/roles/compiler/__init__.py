from iriai_compose import Role

from .._loader import load_prompt
from ...config import BUDGET_TIERS

role = Role(
    name="compiler",
    prompt=load_prompt(__file__),
    tools=["Read", "Glob", "Grep", "Write"],
    model=BUDGET_TIERS["opus_1m"],
    metadata={
        "liveness_timeout": 0,
    },
)
