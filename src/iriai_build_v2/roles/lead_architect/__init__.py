from iriai_compose import Role

from .._loader import load_prompt
from ...config import BUDGET_TIERS, mcp_servers_for

role = Role(
    name="lead-architect",
    prompt=load_prompt(__file__),
    tools=["Read", "Write", "Glob", "Grep", "Bash", "WebSearch", "WebFetch"],
    model=BUDGET_TIERS["opus_1m"],
    metadata={
        "mcp_servers": mcp_servers_for("context7"),
        "max_session_chars": 800_000,
        "keep_recent_messages": 6,
    },
)
