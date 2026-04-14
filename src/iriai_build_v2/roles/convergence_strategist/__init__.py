from iriai_compose import Role

from .._loader import load_prompt
from ...config import BUDGET_TIERS, mcp_servers_for

role = Role(
    name="convergence-strategist",
    prompt=load_prompt(__file__),
    tools=["Read", "Bash", "Glob", "Grep"],
    model=BUDGET_TIERS["opus_1m"],
    metadata={
        "mcp_servers": mcp_servers_for("context7", "sequential-thinking", "preview"),
    },
)
