from iriai_compose import Role

from .._loader import load_prompt
from ...config import BUDGET_TIERS, mcp_servers_for

role = Role(
    name="test-author",
    prompt=load_prompt(__file__),
    tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    model=BUDGET_TIERS["opus_1m"],
    metadata={
        "mcp_servers": mcp_servers_for("playwright", "context7"),
    },
)
