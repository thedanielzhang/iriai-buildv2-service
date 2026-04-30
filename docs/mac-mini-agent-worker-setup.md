# Mac Mini Agent Worker Setup Guide

This guide is written for Codex running on a fresh Mac mini. The goal is to
bootstrap a free-software-friendly worker that can run `iriai-build-v2` tasks
today and can later become a distributed `iriai-compose` agent runtime with
containerized agent slots, profile-based authentication, and provider-neutral
task routing.

## Operating Model

Set up the Mac mini as a worker node, not as the permanent source of truth.

- Use a dedicated macOS user for the worker.
- Keep source repos under one root directory.
- Keep provider credentials in named profiles, outside repos and images.
- Use persistent Git mirrors and per-job worktrees instead of recloning every job.
- Use Colima plus the Docker CLI as the default container runtime.
- Treat Docker Desktop as optional, not required.
- Run long-lived worker daemons with `launchd`.

The current `iriai-build-v2` code supports `claude` and `codex` runtimes. Codex
invocations already create per-invocation `CODEX_HOME` directories and symlink
auth from the selected source profile. Claude support currently goes through the
Claude Agent SDK. Future distributed execution should keep that shape: a
provider-neutral job envelope gets matched to a runtime profile, then the worker
materializes an isolated workspace and launches the provider CLI/SDK.

## Source Research Summary

- OpenAI documents Codex CLI as a local terminal coding agent, installed with
  `npm i -g @openai/codex`; first run prompts for ChatGPT or API-key auth.
  Source: [OpenAI Codex CLI](https://developers.openai.com/codex/cli).
- Claude Code supports npm install, but the docs warn not to install it with
  `sudo npm install -g`. Claude Code stores OAuth credentials in macOS Keychain;
  terminal sessions can also use `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
  or `apiKeyHelper`.
  Sources: [Claude Code setup](https://code.claude.com/docs/en/getting-started),
  [Claude Code authentication](https://code.claude.com/docs/en/authentication),
  [Claude Code settings](https://code.claude.com/docs/en/settings).
- Colima is the default container runtime choice here because it is free,
  open-source, Homebrew-installable, and supports Docker commands on macOS.
  Sources: [Colima installation](https://colima.run/docs/installation/),
  [Colima getting started](https://colima.run/docs/getting-started/).
- Docker Desktop works well and is no-cost for personal use, education,
  non-commercial open source, and small businesses under Docker's current
  terms, but it is licensed under the Docker Subscription Service Agreement.
  Docker Engine itself is Apache-2.0 licensed. For this project, default to
  Colima plus Docker CLI; use Docker Desktop only as an explicit convenience
  choice.
  Sources: [Docker Desktop license](https://docs.docker.com/subscription/desktop-license/),
  [Docker Personal](https://www.docker.com/products/personal/),
  [Docker Engine](https://docs.docker.com/engine/).
- Git worktrees are the right primitive for parallel job workspaces because one
  repository can have multiple working trees checked out at the same time.
  Source: [git worktree](https://git-scm.com/docs/git-worktree).
- PostgreSQL `FOR UPDATE SKIP LOCKED` is appropriate for multiple queue
  consumers because it avoids row lock contention for queue-like tables; pair it
  with short transactions and `LISTEN`/`NOTIFY` for wakeups.
  Sources: [PostgreSQL SELECT](https://www.postgresql.org/docs/current/sql-select.html),
  [PostgreSQL LISTEN](https://www.postgresql.org/docs/current/sql-listen.html),
  [PostgreSQL NOTIFY](https://www.postgresql.org/docs/current/sql-notify.html).
- macOS should use built-in Remote Login for SSH, FileVault for disk encryption,
  Firewall for inbound filtering, and `launchd` for long-running services.
  Sources: [Remote Login](https://support.apple.com/guide/mac-help/mchlp1066/mac),
  [FileVault](https://support.apple.com/guide/deployment/intro-to-filevault-dep82064ec40/web),
  [Firewall](https://support.apple.com/guide/mac-help/mh34041/mac),
  [launchd](https://support.apple.com/guide/terminal/script-management-with-launchd-apdc6c1077b-5d5d-4d35-9c19-60f2397b2369/mac).
- MCP is a useful extensibility boundary because it standardizes tools,
  resources, and prompts across clients. It also introduces arbitrary data and
  tool execution risk, so keep MCP servers profile-scoped and least-privilege.
  Source: [Model Context Protocol](https://modelcontextprotocol.io/specification/latest).

## Human Bootstrap

These steps must happen before Codex can take over.

1. Complete macOS setup and create a dedicated user, preferably named
   `iriai-worker`. If you choose a different short name, replace
   `iriai-worker` in every example path and connection string below.
2. Enable FileVault.
3. Enable Remote Login for the worker user only.
4. Enable the macOS Firewall.
5. Install Xcode Command Line Tools:

```bash
xcode-select --install
```

6. Install Homebrew from the official installer:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

7. Add Homebrew to the worker user's shell. On Apple Silicon this is usually:

```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

8. Install the minimum tools needed to launch Codex:

```bash
brew install node git
npm i -g @openai/codex
codex
```

9. Sign in to Codex with the intended OpenAI account or API key.

After Codex opens, paste the rest of this guide into Codex on the Mac mini.

## Phase 1: Verify Machine Baseline

Run these commands and record the output in the setup notes:

```bash
whoami
sw_vers
uname -m
xcode-select -p
brew --version
git --version
node --version
npm --version
codex --version
codex login status
```

Expected:

- `whoami` is the dedicated worker user.
- `uname -m` is usually `arm64` on modern Mac minis.
- `codex login status` reports an authenticated account.

If any command is missing, install the missing package before continuing.

## Phase 2: Install System Packages

Install free CLI packages and runtimes:

```bash
brew update
brew install git gh node python@3.11 postgresql@16 ripgrep jq direnv colima docker docker-compose docker-buildx
```

Create Docker CLI plugin links for Compose and Buildx:

```bash
mkdir -p ~/.docker/cli-plugins
ln -sfn "$(brew --prefix)/opt/docker-compose/bin/docker-compose" ~/.docker/cli-plugins/docker-compose
ln -sfn "$(brew --prefix)/opt/docker-buildx/bin/docker-buildx" ~/.docker/cli-plugins/docker-buildx
```

Start Colima:

```bash
colima start --cpu 4 --memory 8 --disk 100
colima status
docker version
docker ps
```

Optional Docker Desktop branch:

- Use Docker Desktop only if the owner explicitly chooses Docker Personal or
  another valid Docker subscription.
- If Docker Desktop is installed, skip Colima and verify with:

```bash
docker version
docker ps
```

Default recommendation: stay on Colima unless there is a concrete reason to use
Docker Desktop.

## Phase 3: Configure Shell And Directories

Create the worker directory layout:

```bash
mkdir -p ~/src/iriai
mkdir -p ~/.iriai-worker/{profiles,logs,run,cache,git-mirrors,worktrees,job-results}
mkdir -p ~/.config/iriai-worker
chmod 700 ~/.iriai-worker ~/.iriai-worker/profiles ~/.config/iriai-worker
```

Add shell defaults:

```bash
cat >> ~/.zprofile <<'EOF'
eval "$(/opt/homebrew/bin/brew shellenv)"
export IRIAI_ROOT="$HOME/src/iriai"
export IRIAI_WORKER_HOME="$HOME/.iriai-worker"
export PATH="/opt/homebrew/opt/python@3.11/bin:$PATH"
EOF
```

Reload the shell:

```bash
source ~/.zprofile
```

## Phase 4: Clone Repositories

The repo names matter because current MCP config expects `iriai-feedback` under
`$IRIAI_ROOT`.

```bash
cd ~/src/iriai
git clone git@github.com:thedanielzhang/iriai-buildv2-service.git iriai-build-v2
git clone git@github.com:thedanielzhang/python-iriai-compose.git iriai-compose
git clone git@github.com:thedanielzhang/iriai-preview-tool.git iriai-preview
git clone git@github.com:thedanielzhang/iriai-feedback-tool.git iriai-feedback
```

If SSH auth is not configured, stop and ask the owner for repo access. Do not
fall back to embedding tokens in clone URLs.

Create local mirrors for future job workspaces:

```bash
mkdir -p ~/.iriai-worker/git-mirrors
cd ~/.iriai-worker/git-mirrors
git clone --mirror git@github.com:thedanielzhang/iriai-buildv2-service.git iriai-build-v2.git
git clone --mirror git@github.com:thedanielzhang/python-iriai-compose.git iriai-compose.git
git clone --mirror git@github.com:thedanielzhang/iriai-preview-tool.git iriai-preview.git
git clone --mirror git@github.com:thedanielzhang/iriai-feedback-tool.git iriai-feedback.git
```

Future worker jobs should create a branch-specific worktree or clone from these
mirrors with `--reference-if-able`, then clean up with `git worktree remove`.

## Phase 5: Install Python Environment

Create an isolated virtual environment:

```bash
/opt/homebrew/bin/python3.11 -m venv ~/.venvs/iriai-worker
source ~/.venvs/iriai-worker/bin/activate
python -m pip install --upgrade pip setuptools wheel
```

Install the local packages:

```bash
cd ~/src/iriai
pip install -e "./iriai-compose[terminal]"
pip install -e ./iriai-preview
pip install claude-agent-sdk
pip install -e "./iriai-build-v2[slack]"
```

Install Playwright browsers:

```bash
python -m playwright install
```

## Phase 6: Install Node Dependencies

Install agent CLIs and MCP support:

```bash
npm i -g @openai/codex
npm i -g @anthropic-ai/claude-code
```

Do not use `sudo npm install -g`.

Install the local QA feedback MCP server:

```bash
cd ~/src/iriai/iriai-feedback
npm install
```

Verify:

```bash
codex --version
codex login status
claude --version
```

Claude login can be deferred until a Claude profile is needed.

## Phase 7: PostgreSQL

Start PostgreSQL:

```bash
brew services start postgresql@16
```

Create the local database:

```bash
createdb iriai_build_v2
```

If port `5432` is used locally, keep it. If matching Daniel's dev machine is
important, configure PostgreSQL for port `5431` before creating the database.

Set:

```bash
export DATABASE_URL="postgresql://$USER@localhost:5432/iriai_build_v2"
```

For a future multi-machine queue, the coordinator database should live on one
trusted host or managed Postgres instance, and workers should connect over a
restricted network. The queue claim query should use `FOR UPDATE SKIP LOCKED`,
leases, heartbeats, retry limits, and short transactions. Use `LISTEN`/`NOTIFY`
only as a wakeup signal; always recheck the database state after wakeup.

## Phase 8: Environment File

Create the local environment file:

```bash
cd ~/src/iriai/iriai-build-v2
cp /dev/null .env
chmod 600 .env
```

Add values supplied by the owner:

```env
DATABASE_URL=postgresql://iriai-worker@localhost:5432/iriai_build_v2
IRIAI_ROOT=/Users/iriai-worker/src/iriai
GITHUB_TOKEN=...
RAILWAY_TOKEN=...
SLACK_APP_TOKEN=...
SLACK_BOT_TOKEN=...
```

Only include Slack and Railway values on this machine if it will run the bridge
or preview workflows. For a pure worker, prefer worker-scoped credentials.

Never commit `.env`, profile directories, or generated auth files.

## Phase 9: Runtime Profiles

Profiles are logical identities and capacity slots. Containers are execution
isolation. Keep them separate.

Create a profile directory:

```bash
mkdir -p ~/.iriai-worker/profiles/codex-main/CODEX_HOME
chmod 700 ~/.iriai-worker/profiles/codex-main
```

Provision Codex auth into the profile:

```bash
CODEX_HOME="$HOME/.iriai-worker/profiles/codex-main/CODEX_HOME" codex login
CODEX_HOME="$HOME/.iriai-worker/profiles/codex-main/CODEX_HOME" codex login status
```

Create a profile manifest:

```bash
cat > ~/.iriai-worker/profiles/codex-main/profile.json <<'EOF'
{
  "name": "codex-main",
  "provider": "openai",
  "runtime": "codex-cli",
  "auth_kind": "codex_home",
  "auth_path": "~/.iriai-worker/profiles/codex-main/CODEX_HOME",
  "max_parallel": 1,
  "labels": ["codex", "openai", "general"],
  "enabled": true
}
EOF
chmod 600 ~/.iriai-worker/profiles/codex-main/profile.json
```

For additional Codex accounts, create additional profile directories:

- `codex-alt-1`
- `codex-alt-2`
- `codex-reviewer`

Each profile gets its own `CODEX_HOME` and its own explicit lease settings.
Do not mutate global `~/.codex` during worker execution.

Profile leases should be an explicit scheduling decision. If a provider returns
rate-limit, quota, or usage-exhaustion errors, mark that profile cooled down or
exhausted according to policy; do not log out, log in, or silently hop accounts
inside a running invocation.

For Claude API-key or gateway profiles, prefer terminal-only auth:

- `ANTHROPIC_API_KEY` for direct API access.
- `ANTHROPIC_AUTH_TOKEN` for bearer-token gateways.
- `apiKeyHelper` in a profile-local settings file when keys need to rotate.

For Claude.ai OAuth/subscription accounts on macOS, plan for separate macOS
users per profile because OAuth credentials are stored in the macOS Keychain.
Run one worker daemon per macOS user and label each worker with the profiles it
can lease.

## Phase 10: Current iriai-build-v2 Smoke Test

Activate the environment:

```bash
source ~/.venvs/iriai-worker/bin/activate
cd ~/src/iriai/iriai-build-v2
set -a
source .env
set +a
```

Run basic imports:

```bash
python - <<'PY'
import iriai_build_v2
import iriai_compose
print("imports ok")
PY
```

Run targeted tests if available:

```bash
pytest -q tests/runtimes tests/workflows
```

Verify the CLI:

```bash
iriai-build-v2 --help
```

For Codex runtime readiness:

```bash
CODEX_HOME="$HOME/.iriai-worker/profiles/codex-main/CODEX_HOME" codex login status
```

## Phase 11: launchd Skeleton

Use `launchd` for worker daemons once the distributed worker command exists.
Do not install this until there is a real worker command to run.

Template path:

```text
~/Library/LaunchAgents/com.iriai.worker.codex-main.plist
```

Template:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.iriai.worker.codex-main</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/iriai-worker/.venvs/iriai-worker/bin/python</string>
    <string>-m</string>
    <string>iriai_build_v2.worker</string>
    <string>run</string>
    <string>--profile</string>
    <string>codex-main</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>IRIAI_ROOT</key>
    <string>/Users/iriai-worker/src/iriai</string>
    <key>IRIAI_WORKER_HOME</key>
    <string>/Users/iriai-worker/.iriai-worker</string>
    <key>DATABASE_URL</key>
    <string>postgresql://iriai-worker@localhost:5432/iriai_build_v2</string>
  </dict>
  <key>WorkingDirectory</key>
  <string>/Users/iriai-worker/src/iriai/iriai-build-v2</string>
  <key>StandardOutPath</key>
  <string>/Users/iriai-worker/.iriai-worker/logs/codex-main.out.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/iriai-worker/.iriai-worker/logs/codex-main.err.log</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
```

When the command exists, install with:

```bash
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.iriai.worker.codex-main.plist
launchctl kickstart -k "gui/$(id -u)/com.iriai.worker.codex-main"
launchctl print "gui/$(id -u)/com.iriai.worker.codex-main"
```

## Phase 12: Future Runtime Shape

Define the future `iriai-compose` distributed runtime around these objects:

- Worker node: a machine process that advertises labels, free slots, and
  reachable profiles.
- Runtime profile: provider auth plus limits, labels, and command template.
- Job lease: one claimed task with heartbeat, timeout, and cancel token.
- Workspace lease: a per-job worktree or clone backed by a local mirror.
- Execution sandbox: a Colima/Docker container or direct host process depending
  on required provider auth.
- Result bundle: structured response, logs, trace metadata, git diff, and
  artifact pointers.

Provider-neutral profile example:

```json
{
  "name": "codex-main",
  "provider": "openai",
  "runtime": "codex-cli",
  "command": ["codex", "exec", "--json", "--ephemeral", "-C", "{workspace}", "-"],
  "env": {
    "CODEX_HOME": "{profile.auth_path}"
  },
  "max_parallel": 1,
  "lease_cooldown_seconds": 60,
  "labels": ["code", "review", "frontend", "backend"]
}
```

Containers should be warm worker slots, not one cold container per tiny
invocation. The `8ac124d6` feature scale included hundreds of sessions and
thousands of runtime events, so cold-starting a fresh VM/container for every
agent turn would add avoidable latency. Use:

- A small warm pool per provider/runtime image.
- Persistent package caches mounted read-only or per-worker.
- Per-job worktrees mounted into containers.
- Profile credentials mounted read-only from the host, never baked into images.
- Queue leases to prevent over-scheduling a profile.
- Usage, cooldown, and retry policy enforced at the scheduler/profile layer.

## Completion Checklist

Report back with:

- macOS version and CPU architecture.
- Homebrew, Node, Python, Git, Codex, Claude, Colima, and Docker versions.
- Whether Docker is Colima-backed or Docker Desktop-backed.
- `codex login status` result for each configured Codex profile.
- Whether Claude is installed, and which Claude auth mode is planned.
- Repo clone locations.
- Python virtualenv path.
- PostgreSQL connection string without secrets.
- Smoke test results.
- Any manual steps still needed from the owner.
