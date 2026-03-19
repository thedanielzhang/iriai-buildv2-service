from iriai_compose import Role

from .._loader import load_prompt
from ...config import BUDGET_TIERS

role = Role(
    name="scoper",
    prompt=load_prompt(__file__),
    tools=["Read", "Glob", "Grep", "Bash", "Write"],
    model=BUDGET_TIERS["opus_1m"],
    metadata={
        "max_session_chars": 800_000,
        "keep_recent_messages": 6,
    },
)
