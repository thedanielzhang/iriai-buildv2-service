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
    "fable": "claude-fable-5",
}

# Opt-in: economy-mode model tiering. When ON, roles named in
# ECONOMY_MODEL_OVERRIDES resolve to the mapped model instead of their declared
# one; unmapped roles are untouched. Default OFF — when off, model resolution
# is byte-identical to today.
ECONOMY_MODE = _env_flag("IRIAI_ECONOMY_MODE", default=False)

# Keyed by Role.name. Generation/revision roles move to Sonnet; verification
# whose verdicts are auto-consumed with no operator gate (the develop-phase
# pipeline) moves to the top model. Roles with an operator (driver) backstop —
# plan-review reviewers, gate reviewers, software-architect pre-seal — are
# deliberately NOT mapped. Task-planning (planning-lead/lead-task-planner)
# IS mapped to the top model: it produces the develop-phase DAG, the
# highest-leverage artifact after the seal.
ECONOMY_MODEL_OVERRIDES = {
    # generation / revision → Sonnet (revision-wave actors inherit these names)
    "product-manager": BUDGET_TIERS["sonnet"],
    "ux-designer": BUDGET_TIERS["sonnet"],
    "lead-product-manager": BUDGET_TIERS["sonnet"],
    "lead-designer": BUDGET_TIERS["sonnet"],
    "test-planner": BUDGET_TIERS["sonnet"],
    "spec-author": BUDGET_TIERS["sonnet"],
    "scoper": BUDGET_TIERS["sonnet"],
    "senior-engineer": BUDGET_TIERS["sonnet"],
    "backend-implementer": BUDGET_TIERS["sonnet"],
    "frontend-implementer": BUDGET_TIERS["sonnet"],
    "database-implementer": BUDGET_TIERS["sonnet"],
    "package-implementer": BUDGET_TIERS["sonnet"],
    "bug-fixer": BUDGET_TIERS["sonnet"],
    "bug-reproducer": BUDGET_TIERS["sonnet"],
    "root-cause-analyst": BUDGET_TIERS["sonnet"],
    "documentation": BUDGET_TIERS["sonnet"],
    "deployer": BUDGET_TIERS["sonnet"],
    "release-manager": BUDGET_TIERS["sonnet"],
    "analytics-engineer": BUDGET_TIERS["sonnet"],
    "observability-engineer": BUDGET_TIERS["sonnet"],
    "ui-designer": BUDGET_TIERS["sonnet"],
    # auto-consumed verification (no operator backstop) + fidelity-critical
    # compile/test-bar generation → top model
    "code-reviewer": BUDGET_TIERS["fable"],
    "security-auditor": BUDGET_TIERS["fable"],
    "verifier": BUDGET_TIERS["fable"],
    "integration-tester": BUDGET_TIERS["fable"],
    "smoke-tester": BUDGET_TIERS["fable"],
    "regression-tester": BUDGET_TIERS["fable"],
    "accessibility-auditor": BUDGET_TIERS["fable"],
    "performance-analyst": BUDGET_TIERS["fable"],
    "test-author": BUDGET_TIERS["fable"],
    "compiler": BUDGET_TIERS["fable"],
    # task-planning (DAG production) → top model; "planning-lead" is the single
    # Role shared by the workstream-planner and both task-planning gate
    # reviewers, so mapping the name moves all three together.
    "planning-lead": BUDGET_TIERS["fable"],
    "lead-task-planner": BUDGET_TIERS["fable"],
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
