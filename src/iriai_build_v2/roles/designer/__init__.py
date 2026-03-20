from iriai_compose import Role

from .._loader import load_prompt
from ...config import BUDGET_TIERS

role = Role(
    name="ux-designer",
    prompt=load_prompt(__file__),
    tools=["Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch"],
    model=BUDGET_TIERS["opus_1m"],
    metadata={
        "max_session_chars": 600_000,
        "keep_recent_messages": 6,
    },
)
