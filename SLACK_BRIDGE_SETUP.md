# Slack Bridge Setup Guide

Step-by-step instructions to run the iriai-build-v2 Slack bridge on a new machine.

## 1. Prerequisites

- **Python 3.11+** — recommended via [pyenv](https://github.com/pyenv/pyenv)
- **Node.js / npm** — required for MCP servers (launched via `npx`)
- **PostgreSQL** — local instance (Homebrew, Docker, or native install)

## 2. Clone Repositories

Clone all repos under a common root directory (e.g. `~/src/iriai/`):

```bash
mkdir -p ~/src/iriai && cd ~/src/iriai

git clone git@github.com:thedanielzhang/iriai-buildv2-service.git iriai-build-v2
git clone git@github.com:thedanielzhang/python-iriai-compose.git iriai-compose
git clone git@github.com:thedanielzhang/iriai-preview-tool.git iriai-preview
git clone git@github.com:thedanielzhang/iriai-feedback-tool.git iriai-feedback
```

> **Note:** The clone directory names matter — use the names shown above. In particular, `iriai-feedback` must be at `$IRIAI_ROOT/iriai-feedback/` for the MCP server config.

## 3. Install Python Packages

From the common root (`~/src/iriai/`):

```bash
pip install -e "./iriai-compose[terminal]"
pip install -e ./iriai-preview
pip install claude-agent-sdk
pip install -e "./iriai-build-v2[slack]"
```

The `[slack]` extra installs `slack-sdk` and `aiohttp`. The `[terminal]` extra on iriai-compose installs the terminal interaction runtime.

### Optional: Codex Runtime

If you want to run the Slack bridge with OpenAI Codex instead of Claude, install the Codex CLI and sign in with your ChatGPT account:

```bash
npm install -g @openai/codex
codex login
codex login status
```

`codex login status` should report that you are logged in before you start the bridge with the Codex runtime.

## 4. Set Up iriai-feedback MCP Server

```bash
cd ~/src/iriai/iriai-feedback
npm install
```

This builds the Node.js MCP server that the QA feedback role uses.

## 5. Set Up PostgreSQL

Create the database. Example using `psql`:

```bash
createdb -p 5431 iriai_build_v2
```

The schema tables (`features`, `events`, `artifacts`, `sessions`) are created automatically on first run — no manual migration needed.

If your Postgres runs on a different port or requires a password, set `DATABASE_URL` accordingly in step 6.

## 6. Environment Variables

Create a `.env` file in the `iriai-build-v2/` directory:

```bash
cd ~/src/iriai/iriai-build-v2
```

```env
# Shared Slack app tokens (get these from Daniel)
SLACK_APP_TOKEN=xapp-...
SLACK_BOT_TOKEN=xoxb-...

# Your own API keys
GITHUB_TOKEN=ghp_...
RAILWAY_TOKEN=...          # optional — only needed for develop/bugfix workflows

# Database — adjust user/port/host to match your local Postgres
DATABASE_URL=postgresql://YOUR_USER@localhost:5431/iriai_build_v2

# Path to the parent directory containing the cloned repos
IRIAI_ROOT=~/src/iriai
```

| Variable | Source |
|---|---|
| `SLACK_APP_TOKEN` | Provided by Daniel (shared Slack app) |
| `SLACK_BOT_TOKEN` | Provided by Daniel (shared Slack app) |
| `GITHUB_TOKEN` | GitHub → Settings → Developer Settings → Personal Access Tokens |
| `RAILWAY_TOKEN` | [Railway Dashboard](https://railway.app/) — optional, only for develop/bugfix workflows |
| `DATABASE_URL` | Your local PostgreSQL connection string |
| `IRIAI_ROOT` | Absolute path to the directory containing your repo clones |

If you run with `--agent-runtime claude`, you also need `ANTHROPIC_API_KEY=...`.

## 7. Install Playwright Browsers

The bootstrap process uses Playwright for browser-based testing:

```bash
python -m playwright install
```

## 8. Run the Slack Bridge

```bash
cd ~/src/iriai/iriai-build-v2
iriai-build-v2 slack --channel CHANNEL_ID --mode multiplayer
```

To use Codex instead of Claude:

```bash
iriai-build-v2 slack --channel CHANNEL_ID --mode multiplayer --agent-runtime codex
```

Replace `CHANNEL_ID` with the Slack planning channel ID (e.g. `C1234567890`) — get this from Daniel.

### CLI Options

| Flag | Default | Description |
|---|---|---|
| `--channel` | *(required)* | Slack planning channel ID |
| `--workspace` | `None` | Default project workspace path (optional — selected per-feature via card) |
| `--mode` | `multiplayer` | `multiplayer`: bot responds only to @mentions. `singleplayer`: bot responds to all messages |
| `--agent-runtime` | `claude` | Agent runtime for workflow agents. Use `codex` to run via the Codex CLI / ChatGPT account |

## 9. Verify It Works

1. Console should print:
   ```
   iriai-build-v2 Slack bridge
     Default mode: multiplayer
     Channel: CHANNEL_ID
     Bot: @<bot_name>
     Listening for [FEATURE] messages...
   ```
2. Post `[FEATURE] Test feature` in the planning channel
3. The bot should respond and create a new workflow channel named `iriai-test-feature-<id>`

## Troubleshooting

| Problem | Fix |
|---|---|
| `SLACK_APP_TOKEN environment variable is required` | Missing or empty `SLACK_APP_TOKEN` in `.env` |
| `SLACK_BOT_TOKEN environment variable is required` | Missing or empty `SLACK_BOT_TOKEN` in `.env` |
| `asyncpg` connection error | Check PostgreSQL is running on the expected port and database exists |
| `ModuleNotFoundError: iriai_compose` | Run `pip install -e ./iriai-compose[terminal]` from the repos root |
| `ClaudeAgentRuntime requires 'claude-agent-sdk'` | Run `pip install claude-agent-sdk` |
| `CodexAgentRuntime requires the Codex CLI on PATH` | Install it with `npm install -g @openai/codex` |
| Codex CLI authentication errors | Run `codex login`, sign in with ChatGPT, then verify with `codex login status` |
| MCP server errors for `qa-feedback` | Ensure `iriai-feedback` is cloned and `npm install` was run, and `IRIAI_ROOT` points to the parent dir |
