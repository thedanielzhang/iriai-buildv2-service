# HANDOVER — SUCCESSOR DRIVER for Kaya Submittal 5b280bb4 (driver-v12)
# Written 2026-06-12 ~14:2x PDT by driver-fable-20260612-1030 (driver-v11), staged EARLY
# per the context tripwire — calm, not dying. Trust NOTHING as current — derive live
# state first. Where this doc and the store conflict, THE STORE WINS.

You are the AUTONOMOUS DRIVER for the iriai-build-v2 run (repo
~/src/iriai/iriai-build-v2 branch fix/cli-resume-command — tip at writing 347f97a
PUSHED; workspace ~/src/kaya/kaya-main; feature 5b280bb4 "Submittal Management").
Store: psql postgresql://danielzhang@localhost:5431/iriai_build_v2
(/opt/homebrew/opt/postgresql@17/bin/psql).

## STATE AT WRITING (verify everything)
- **DAG COMPLETE**: 14/14 groups (dag-group:13 sealed 13:24:26), 53/53 tasks.
  POST-DAG phase (gate battery → end-gate e2e → PR) runs under developN
  (develop42 at writing, PID via the §BOOT pattern — NEVER bare pgrep).
- ⚠️ POST-DAG SLOW-COMPUTE GOTCHA: the phase grinds 15-20 min at ~43% CPU with
  ZERO store writes between artifact bursts (dag-regroup-observation rows +
  phase pairs). This is WORK, not the develop34 loop — only kill on >25 min
  with zero artifact writes (BUG-16; the develop41 kill at 13:46 was premature).
- FW tip at writing: 18399b7ba + (batch-1 UI riders pending apply — see RIDERS).
  Worktree must be porcelain-0 before any engine activity.

## BOOT SEQUENCE
1. READ: this doc → docs/operator-driver-runbook.md (§2/§4/§17/§18/§19, heal-lane,
   code-first) → <ws>/.iriai/{ANALYST-DIRECTIVES.md, OPERATOR-ACTIONS.md} newest-first
   (operator rulings of 06-12: OFFLOAD-BY-DEFAULT, LANE MAP, clone-authored riders,
   AUTOMATED VISUAL QA, dispatch-findings-parallel) → WORKFLOW-BUG-BACKLOG.md →
   runtime/findings-closure-checklist.md (Rounds 1-7 + OPF) →
   operator-ui-findings/FINDINGS.md (the operator's own channel — closure notes
   go IN that file with fixed SHAs).
2. SEAT: rewrite <ws>/.iriai/driver.lock (real clock, your id).
3. PID: ps -axo pid,command | grep "python.*bin/iriai-build-v2 resume" | grep -v grep.
4. SWEEP before any relaunch: <ws>/.iriai/runtime/prelaunch_sweep.sh 5b280bb4 <ws>.

## RECIPE (develop42's exact env = v10's set PLUS):
  IRIAI_DAG_PARTIAL_ENQUEUE_DRIVER_MARKER=1   # 1aa7f50 — partials write dag-partial-triage:{task}, self-describing park
  IRIAI_MCP_DISABLE=context7                  # 347f97a — context7 is quota-dead; wedged S6-04 50 min (BUG-14)
  (everything else identical to v10's recipe block; EXPANDED_VERIFY=0 + CODEX_HOME=
  ~/.codex-iriai-fast [now effort=HIGH — tripwire reverted; do NOT lower] mandatory)
CMD: iriai-build-v2 resume --feature-id 5b280bb4 --workspace <ws> --driver agent
  --agent-runtime agent_pool --from-phase implementation >> /tmp/kaya_developN.log 2>&1

## MONITORS (v48 script /tmp/kaya_v11_arm_monitors.sh <PID> <LOG> + extras)
- crash/budget/boundary/drain-tripwire via the script; channel watcher
  (ANALYST-DIRECTIVES mtime) + ui-findings watcher (operator-ui-findings/) +
  query watcher (operator-queries/*.query.json without .answer) armed separately
  (alerts → /tmp/kaya_v11_alerts.log + /tmp/kaya_v11_channel_alerts.log).
- Drain-tripwire verdicts need the BUG-16 artifact-lookback before any kill.

## RIDER MACHINERY (operator-ruled, standing)
- ALL code authoring via codex exec (CODEX_HOME=~/.codex-iriai-fast, --full-auto,
  clone-authored in /tmp/kaya-riders-* — NEVER in the FW). Driver reviews diffs +
  applies ATOMICALLY (apply windows: never mid-drain; post-DAG = anytime engine
  isn't writing the FW). Fable subagents only for judgment (lens reads, security).
- Beware the index trap: codex/apply state in clones — generate patches with
  `git add -A && git diff --cached --binary`, or copy files; verify the COMBINED
  tree (tsc + py_compile) before commit; commit-or-revert fully.

## IN-FLIGHT AT WRITING (verify each; reports = /tmp/kaya-riders-<p>.report)
- BATCH-1 (PKG A-E, all reported FIXED+verified): Apryse lazy-init (F2 blocker),
  dashboard set (F4/5/6/10/11/12), handoff table (F7/F22), canonical toolbar
  (F9/F18), minors + figma-crops corpus. COMBINED-TREE assembly was running in
  /tmp/kaya-batch1 (codex; dual-touched files: submittal-dashboard-page.tsx [B+D],
  submittal-kpi-tile-row.tsx [B+E]) → review → ONE batch commit → demo refresh +
  probe → FINDINGS.md closures.
- PKG-F (MANDATORY pre-end-gate): amber write plane — 3/4 tools send
  extra=forbid-impossible bodies (W11-F2/F6/F7; the S6-04 P-14 died silently vs
  read_only scope — BUG-15) + both pinning tests + integration tool-legs +
  channelId join fix (W11-F4). The wave's own integration test
  (submittals.integration.test.ts buildDirectRequest legs) is the contract spec.
- PKG-G: CMP-22 mount + i18n dictionary keys (W13-F4/F5).
- PKG-H (NOT YET DISPATCHED — queued behind batch-1 apply, shares files with B/D):
  F16/F23 real-drift set per the 14:1x descope report (checkboxes+bulk, BIC
  avatars, Revision #, Manage-Columns/Archive/Share, status-column mapping,
  full CMP-13 closeout toolbar + tabs order). Registered testids exist in the
  S5 plan; Figma crops canonical.
- Descoped (closure notes only): dashboard Kanban (G-U10-10), forecast tabs
  (G-DS-FORECAST-CHART), tracker Talk-to-Amber (S6 constraint), F3/F8/F14/F15/
  F19/F26 per the 13:3x not-riders list.

## STANDING DEMO (operator browses it; THE findings loop input)
- :3001 frontend + :8001 backend from /tmp/e2e4-tree; restart one-liners in the
  OPERATOR-ACTIONS 12:2x entry; refresh to FW tip at every rider batch + seal;
  AUTHENTICATED row-click probe after every restart (dashboard.spec grep
  "skeleton|row detail navigation", PLAYWRIGHT_BASE_URL=http://localhost:3001,
  creds ~/.iriai-secrets/kaya-e2e/e2e-auth.env, E2E_ORG_ID=1, traces ON always);
  log probe + served SHA in the demo entry. Port-checks alone HID F1.
- VISUAL QA standing: capture fan-out (sonnet; desktop only — mobile ruled out)
  + Fable per-surface review per refresh on changed surfaces; full re-pass at
  end gate; findings → FINDINGS.md → batched codex riders.

## PENDING OPERATOR ITEMS
- F16 grouped rows scope call (14:1x entry; recommend PR-note) · swap >85% ask ·
  launchctl runner restart (stale, non-blocking) · PR CREATION (gated).

## ENDGAME MAP
develop42 battery (slow passes; STRICT_VERDICT_DISPOSITION may emit operator
queries — answer per runbook §5) → riders F/G/H + batch-1 applied + re-probe →
end-gate e2e (STRICT_GREEN, native lanes profile row 2249062; amber spec can
come OFF holdout once PKG-F lands; FULL traces) → findings-closure SETTLE (the
checklist is the battery's evidence; gate pack at runtime/
gate-battery-evidence-pack.md, judgment calls pre-briefed) → PR: staged at
runtime/pr-staging/ (body + create-pr.sh; push + create BOTH operator-gated;
23-commit base divergence flag needs the operator's rebase-vs-accept call).

## SESSION LEDGER (driver-v11, 10:3x→14:2x)
develop38→42; g11/g12/g13 sealed (DAG complete); rider commits 490496ea3,
678e1eb5c, f93e04c57, 7c2610666, 18399b7ba (+batch-1..H pending); engine fixes
pushed 0f25d1c (BUG-13 codex-config live reload), 1aa7f50 (partial-triage
marker), 347f97a (IRIAI_MCP_DISABLE); 4 partials superseded via precedent lane
(backups /tmp/kaya_*_partial_backup_*.csv); cover-send dual-lineage key-renamed;
fast-mode tripwire fired + reverted (wave-10 STEP-CHANGE, waves 12-13 HIGH
confirmed clean); BUG-13..16 filed (13 fixed, 14/15/16 open); standing demo +
findings loop + visual-QA program live; F1 operator blocker closed same-hour.
