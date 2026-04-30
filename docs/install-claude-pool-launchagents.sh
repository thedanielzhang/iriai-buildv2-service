#!/usr/bin/env bash
set -euo pipefail

# Run from the main macOS account after installing iriai-build-v2 into the
# shared venv. The three iriai-claude-* users must have active GUI sessions.

/Users/Shared/iriai/.venv/bin/iriai-build-v2 claude-pool install-launchagents

sudo mkdir -p /Users/iriai-claude-1/Library/LaunchAgents
sudo cp /Users/Shared/iriai/claude-pool/launchagents/com.iriai.claude-pool.iriai-claude-1.plist /Users/iriai-claude-1/Library/LaunchAgents/com.iriai.claude-pool.iriai-claude-1.plist
sudo chown iriai-claude-1:staff /Users/iriai-claude-1/Library/LaunchAgents/com.iriai.claude-pool.iriai-claude-1.plist
sudo launchctl bootout gui/503 /Users/iriai-claude-1/Library/LaunchAgents/com.iriai.claude-pool.iriai-claude-1.plist 2>/dev/null || true
sudo launchctl bootstrap gui/503 /Users/iriai-claude-1/Library/LaunchAgents/com.iriai.claude-pool.iriai-claude-1.plist
sudo launchctl kickstart -k gui/503/com.iriai.claude-pool.iriai-claude-1

sudo mkdir -p /Users/iriai-claude-2/Library/LaunchAgents
sudo cp /Users/Shared/iriai/claude-pool/launchagents/com.iriai.claude-pool.iriai-claude-2.plist /Users/iriai-claude-2/Library/LaunchAgents/com.iriai.claude-pool.iriai-claude-2.plist
sudo chown iriai-claude-2:staff /Users/iriai-claude-2/Library/LaunchAgents/com.iriai.claude-pool.iriai-claude-2.plist
sudo launchctl bootout gui/504 /Users/iriai-claude-2/Library/LaunchAgents/com.iriai.claude-pool.iriai-claude-2.plist 2>/dev/null || true
sudo launchctl bootstrap gui/504 /Users/iriai-claude-2/Library/LaunchAgents/com.iriai.claude-pool.iriai-claude-2.plist
sudo launchctl kickstart -k gui/504/com.iriai.claude-pool.iriai-claude-2

sudo mkdir -p /Users/iriai-claude-3/Library/LaunchAgents
sudo cp /Users/Shared/iriai/claude-pool/launchagents/com.iriai.claude-pool.iriai-claude-3.plist /Users/iriai-claude-3/Library/LaunchAgents/com.iriai.claude-pool.iriai-claude-3.plist
sudo chown iriai-claude-3:staff /Users/iriai-claude-3/Library/LaunchAgents/com.iriai.claude-pool.iriai-claude-3.plist
sudo launchctl bootout gui/505 /Users/iriai-claude-3/Library/LaunchAgents/com.iriai.claude-pool.iriai-claude-3.plist 2>/dev/null || true
sudo launchctl bootstrap gui/505 /Users/iriai-claude-3/Library/LaunchAgents/com.iriai.claude-pool.iriai-claude-3.plist
sudo launchctl kickstart -k gui/505/com.iriai.claude-pool.iriai-claude-3

/Users/Shared/iriai/.venv/bin/iriai-build-v2 claude-pool doctor
