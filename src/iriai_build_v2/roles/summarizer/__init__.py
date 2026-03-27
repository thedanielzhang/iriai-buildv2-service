from iriai_compose import Role

from .._loader import load_prompt
from ...config import BUDGET_TIERS

role = Role(
    name="summarizer",
    prompt=load_prompt(__file__),
    tools=[],
    model=BUDGET_TIERS["haiku"],
    effort="high",
)
