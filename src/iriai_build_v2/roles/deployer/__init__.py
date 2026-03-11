from iriai_compose import Role

from .._loader import load_prompt
from ...config import BUDGET_TIERS, mcp_servers_for

role = Role(
    name="deployer",
    prompt=load_prompt(__file__),
    tools=["Read", "Bash", "Glob", "Grep"],
    model=BUDGET_TIERS["opus"],
    metadata={
        "mcp_servers": mcp_servers_for("github", "preview"),
    },
)
