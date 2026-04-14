import os
from pathlib import Path

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://danielzhang@localhost:5431/iriai_build_v2",
)

DASHBOARD_BASE_URL = os.environ.get("IRIAI_DASHBOARD_BASE_URL", "").rstrip("/")

BUDGET_TIERS = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "opus_1m": "claude-opus-4-6[1m]",
    "haiku": "claude-haiku-4-5-20251001",
}

# ── MCP Server Definitions ──────────────────────────────────────────────────

IRIAI_ROOT = Path(os.environ.get("IRIAI_ROOT", Path.home() / "src" / "iriai"))

MCP_SERVERS = {
    "playwright": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@playwright/mcp"],
    },
    "context7": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@upstash/context7-mcp@latest"],
    },
    "postgres": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres"],
    },
    "sequential-thinking": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
    },
    "github": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": os.environ.get("GITHUB_TOKEN", "")},
    },
    "qa-feedback": {
        "type": "stdio",
        "command": "node",
        "args": [str(IRIAI_ROOT / "iriai-feedback" / "src" / "mcp" / "index.js")],
    },
    "preview": {
        "type": "stdio",
        "command": "python",
        "args": ["-m", "preview.mcp_server"],
        "env": {"RAILWAY_TOKEN": os.environ.get("RAILWAY_TOKEN", "")},
    },
}


def mcp_servers_for(*names: str) -> dict:
    """Return a subset of MCP_SERVERS for the given names."""
    return {n: MCP_SERVERS[n] for n in names if n in MCP_SERVERS}
