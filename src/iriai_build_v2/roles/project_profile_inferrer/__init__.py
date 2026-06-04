from iriai_compose import Role

from .._loader import load_prompt
from ...config import BUDGET_TIERS, mcp_servers_for

role = Role(
    name="project-profile-inferrer",
    prompt=load_prompt(__file__),
    tools=["Read", "Glob", "Grep", "Bash"],
    model=BUDGET_TIERS["sonnet"],
    metadata={
        "mcp_servers": mcp_servers_for("context7"),
    },
)
