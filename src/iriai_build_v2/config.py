import os
from pathlib import Path

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://danielzhang@localhost:5431/iriai_build_v2",
)

DASHBOARD_BASE_URL = os.environ.get("IRIAI_DASHBOARD_BASE_URL", "").rstrip("/")


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean env flag. Truthy values: 1/true/yes/on (case-insensitive)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Opt-in: cascade a TARGETED system-design revision to sibling artifacts when an
# in-cycle plan-review request touches a subfeature that has a system-design
# artifact. Default OFF — when off, plan-review behavior is byte-identical to
# today (no extra dispatch). Targeted-only; never triggers full-document regen.
PLAN_REVIEW_SD_CASCADE = _env_flag("IRIAI_PLAN_REVIEW_SD_CASCADE", default=False)

BUDGET_TIERS = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
    # Opus 4.8 ships with a native 1M context window.
    "opus_1m": "claude-opus-4-8",
    "haiku": "claude-haiku-4-5-20251001",
}

# ── MCP Server Definitions ──────────────────────────────────────────────────

IRIAI_ROOT = Path(os.environ.get("IRIAI_ROOT", Path.home() / "src" / "iriai"))
IRIAI_BUILD_V2_SRC = Path(__file__).resolve().parent.parent
IRIAI_BUILD_V2_PYTHONPATH = os.pathsep.join(
    item
    for item in (str(IRIAI_BUILD_V2_SRC), os.environ.get("PYTHONPATH", ""))
    if item
)

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
    "supervisor-evidence": {
        "type": "stdio",
        "command": "python",
        "args": ["-m", "iriai_build_v2.supervisor.mcp_server"],
        "env": {
            "DATABASE_URL": DATABASE_URL,
            "IRIAI_DASHBOARD_BASE_URL": DASHBOARD_BASE_URL,
            "PYTHONPATH": IRIAI_BUILD_V2_PYTHONPATH,
        },
    },
}


def mcp_servers_for(*names: str) -> dict:
    """Return a subset of MCP_SERVERS for the given names."""
    return {n: MCP_SERVERS[n] for n in names if n in MCP_SERVERS}
