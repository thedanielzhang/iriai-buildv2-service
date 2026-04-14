from iriai_compose import Role

from .._loader import load_prompt
from ...config import BUDGET_TIERS, mcp_servers_for

role = Role(
    name="observation-collector",
    prompt=load_prompt(__file__),
    tools=["Read", "Glob", "Grep"],
    model=BUDGET_TIERS["opus"],
    metadata={
        "mcp_servers": mcp_servers_for("github"),
        "max_session_chars": 800_000,
        "keep_recent_messages": 6,
    },
)
