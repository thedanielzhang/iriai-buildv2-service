# HANDOVER — SUCCESSOR DRIVER for Kaya Submittal 5b280bb4 (driver-v11)
# Written 2026-06-12 ~10:0x PDT by driver-fable-20260611-2235 at operator-ruled
# context wind-down. Trust NOTHING here as current — derive live state first.

You are the AUTONOMOUS DRIVER for the iriai-build-v2 run (repo
~/src/iriai/iriai-build-v2 branch fix/cli-resume-command, tip 1d05e64 PUSHED;
workspace ~/src/kaya/kaya-main; feature 5b280bb4 "Submittal Management").
Store: psql postgresql://danielzhang@localhost:5431/iriai_build_v2
(psql binary: /opt/homebrew/opt/postgresql@17/bin/psql).
DEVELOP era: 39/53 tasks terminal (g0-g9 sealed). Wave-10 was streaming at
handover (effective g10 = TASK-S2-SL2-3, TASK-S5-06-TRACKER-HANDOVER-UI,
TASK-S5-07-PUBLIC-UPLOAD-VENDOR-LINK, s3a-router-append-endpoints) under
develop37. Remaining after: wave-11 [TASK-S3B-04, TASK-S5-05-START-CLOSEOUT-UI,
s3a-frontend-review-cover-send, s3a-slice5-amber-delegated-auth-client-edge],
wave-12 [TASK-S5-03-REQUEST-PLANE-GUARDS], wave-13 [TASK-S6-04].

## BOOT SEQUENCE
1. READ: this doc → docs/operator-driver-runbook.md (§2 seat, §4 monitors,
   §17 ceremonies, §13 store) → memory project_kaya_submittal_planreview_state
   (TOP entries) → <ws>/.iriai/{ANALYST-DIRECTIVES.md, OPERATOR-ACTIONS.md}
   newest-first → <ws>/.iriai/WORKFLOW-BUG-BACKLOG.md →
   <ws>/.iriai/runtime/findings-closure-checklist.md.
2. TAKE THE SEAT: rewrite <ws>/.iriai/driver.lock (REAL clock; the v10 lock is
   my stand-down marker).
3. DERIVE STATE: real python PID via
   `ps -axo pid,command | grep "python.*bin/iriai-build-v2 resume" | grep -v grep`
   (NEVER bare pgrep -f — the WRAPPER SELF-MATCH GOTCHA: your own monitor
   shells contain the pattern and you will kill them instead of the engine);
   newest artifacts rows; tail the newest /tmp/kaya_developN.log.
   develop37 (and all my monitors + in-flight workers) DIED WITH MY SESSION.

## LAUNCH RECIPE (developN+1; run the SWEEP first, ALWAYS)
SWEEP: <ws>/.iriai/runtime/prelaunch_sweep.sh 5b280bb4 /Users/danielzhang/src/kaya/kaya-main
  (clears dead-owner DB leases, non-terminal sandbox manifests, TEMPLATE
  .building LOCKDIRS + dead-PID .staging dirs — every class has bitten tonight)
ALSO CHECK: feature worktree porcelain == 0
  (FW=<ws>/.iriai/features/submittal-management-for-kaya-build-strictly-from-docs-submittal-prd-as-the-sour-5b280bb4/repos)
  and host swap (sysctl vm.swapusage; >85% used → operator app-close/reboot ask
  — swap exhaustion mimics engine bugs: 600s rungs, silent SIGKILL deaths).
ENV (every flag explicit; = the develop37 set):
  CODEX_HOME=/Users/danielzhang/.codex-iriai-fast        # operator 09:2x fast-mode ruling
  IRIAI_OPERATOR_QUERY_TIMEOUT_S=21600 IRIAI_ECONOMY_MODE=1
  IRIAI_CONTEXT_PACKAGE_ITEM_MAX_CHARS=200000
  IRIAI_TASK_PLANNING_SLICE_CONTEXT_CAP_BYTES=260000 IRIAI_BORN_ADOPTED_RESUME=1
  IRIAI_DEVELOP_CONTRACT_AC_WAIVERS=1 IRIAI_CONTRACT_LEGACY_FILES_TOLERANT=1
  IRIAI_CONTRACT_TOLERATED_READ_WRITE_PAIRS="TASK-HRDD-S2-04:TASK-RCAN-01-BACKEND-SERVICES"
  IRIAI_DAG_WORKSPACE_PERMISSION_REPAIR=0 IRIAI_KNOWN_FLAKY_LEDGER=1
  IRIAI_E2E_REPAIR_RETRIES=1 IRIAI_E2E_REQUIRE_PROFILE=1
  IRIAI_STRICT_VERDICT_DISPOSITION=1 IRIAI_LEDGER_FAIL_LOUD=1
  IRIAI_PROJECT_CONSTRAINTS_PROMPT=1
  IRIAI_DAG_EXPANDED_VERIFY=0          # OPERATOR RULING 08:2x — code default is ON; =0 REQUIRED every recipe
  IRIAI_DAG_QUIESCE_OPERATOR_GATE=1 IRIAI_QUIESCE_GROUP_INDEXES=6   # verify EXACTLY "6"; that quiesce already executed
  IRIAI_CLAUDE_POOL_RECENT_USAGE_WINDOW_SECONDS=900   # selector cost-spreader horizon (6h default repels claude-2)
  IRIAI_E2E_TRIAGE_CLASSIFY=1 IRIAI_E2E_CRITICAL_BINDING=1 IRIAI_E2E_STRICT_GREEN=1
  IRIAI_E2E_RUN_GATE=1 IRIAI_E2E_CRITICAL_QUIESCE=0 IRIAI_E2E_BOUNDARY_REPAIR=1
  IRIAI_E2E_MAX_LOAD=400 IRIAI_VERIFIER_EVIDENCE_TRANSCRIPTION=1
  IRIAI_SANDBOX_REUSE_ON_RETRY=0 IRIAI_DAG_REGROUP_AUTHORED_SOFT_BARRIERS=1
  IRIAI_DB_COMMAND_TIMEOUT_SECONDS=120
CMD: iriai-build-v2 resume --feature-id 5b280bb4
  --workspace /Users/danielzhang/src/kaya/kaya-main --driver agent
  --agent-runtime agent_pool --from-phase implementation >> /tmp/kaya_developN.log 2>&1
(run_in_background tracked task; capture the real python PID immediately.)

## MONITORS (v47 set — re-arm at EVERY relaunch; arm-verify by heartbeat
## CONTENTS advancing, two reads; heartbeats /tmp/kaya_monitor_heartbeats/)
a. CRASH/INFRA 15s: log grep '^Traceback|crashed \(attempt|sandbox_binding_failed|
   ResolutionImpossible|FATAL|Cannot install -r requirements|timed out after 900s|
   Compile completeness guard FAILED|Claude pool: ALL members unavailable'
   EXCLUDING 'result:error' (implementer echo) + claude-2 usage_limited/auth_failed
   (= FULL Claude outage → operator chat immediately) + unanswered operator-queries.
b. ATTEMPT-BUDGET 60s: journal count(dispatch_attempt) >= 3 on any non-terminal
   task → LAST-ATTEMPT alarm (preempts everything).
c. CHANNEL 30s: ANALYST-DIRECTIVES.md mtime → read+ACK new entries ≤15 min (SLO).
   NOTE your own STATUS flips trigger it — re-arm with post-flip baseline.
d. BOUNDARY 45s: new dag-group:/dag-quiesce:/%blocker% rows past the latest id.
e. DRAIN TRIPWIRE 60s: zero new journal max(id) for ~8 cycles WHILE engine CPU
   >= 20% (the silent-CPU-loop wedge signature; plain journal silence is NORMAL
   during implementer streaming — do not alarm on silence alone).
SLOs: crash response ≤5 min; P0 channel engagement ≤15 min; ceremony start
≤5 min post-park. Triage order EVERY wake: crash sigs → budgets → channel → work.

## STANDING OPERATOR RULES (all operator-ruled, do not relitigate)
- NO-PERMANENT-HALT + velocity: classify ≤10 min; engine defect → manual
  advance via sanctioned lanes (psql heals, journal surgery w/ CSV backup,
  manifest flips, key-renames for FK-pinned rows, riders, W-OG override);
  product defect → normal flow. Bypass-permissions execution lane w/ self-audit
  (evidence-preserve BEFORE, one OPERATOR-ACTIONS line AFTER).
- NO MID-WAVE BOUNCES (config batches at seal exits; emergencies only — note a
  kill orphans the template .building lockdir → next boot wedges in 900s waits;
  the sweep now clears it).
- EXPANDED VERIFY STAYS 0; verification is DRIVER-DRIVEN: ONE 5-lens review
  worker per sealed wave (async, never blocking), findings → riders (lens-tag
  pattern 9e7fe9c6e) / P-14 amendments (dag-task-amendments:{task} store key)
  by future-owner check / backlog; two-wave max lag; one audit line per wave.
  WAVE-11 IS THE FAST-MODE QUALITY TRIPWIRE: compare its findings profile vs
  the xhigh baseline (~0-2 majors/wave); step-change → revert ~/.codex-iriai-fast
  effort to "high" for waves 12-13 and say so. If wave-11 clean AND fast,
  MAY trial effort=low on the two tail singletons.
- POOL TOPOLOGY: codex carries ALL implementation (claude-2 weight 0.01 in
  /Users/Shared/iriai/claude-pool/profiles.json enforces); claude-1/3 BENCHED
  (profile_state probe_after 2026-06-16); claude-2 = review/escalation only.
  Driver review workers are Agent-tool (session-side), unaffected by pool.
- E2E PROGRAM (driver-owned): continuous journey passes (each seal or better),
  TWO-DEV-SERVER method: clonefile FW → /tmp, pnpm warm install, next dev :3001
  (env: copy main spend-client/.env + .env.local overrides) + NEW feature-backend
  leg: worktree supply-chain uvicorn :8001 against the SAME dev DB (env from
  docker inspect docker-supply-chain-1), frontend API base → :8001. Playwright
  deps staged /tmp/e2e1-pwdeps. CHK gates env: CHK_S1_CORE_OPERATOR_CONFIRMED=true
  S2_SUBMITTAL_HANDOVER... (see specs; S2_SUBMITTAL_HANDOFF_AVAILABLE=true).
  Amber spec HELD OUT until grant minting exists (table landed d33f56444).
  ZERO-test pass = pass FAILURE; never advance the green pointer on it.
  e2e accounts: org_id=1 claims LIVE (JWTs verified); creds
  ~/.iriai-secrets/kaya-e2e/e2e-auth.env (never print values).

## QUEUED WORK (in order)
1. g10 BOUNDARY (if I didn't execute it): wave-10 drain → check captures vs
   the lens findings (R2-02 router sweep by s3a-router-append; R2-07 project_id
   Query params + E2E2-02/03 pages by S2-SL2-3; R2-10/13 by S5-06) → seal →
   LAND THE QUEUED BOUNDARY RIDERS for whatever the captures did NOT fix:
   (i) mount/move the orphaned decision router (submittal_decision.py:410-421
   router never imported; server.py mounts only routers/knowledge/
   submittal_management.py); (ii) wire the RCAN-04 release-confirm link
   (submittal_release.py:256-259 links the legacy [accessCode] page; the new
   release-confirm page has zero inbound navigations); (iii) share-link
   input-validation set (R2-06 s3_url namespace check, R2-08 stakeholder↔
   share_link.referenced match, R2-11 server-derived verified_email_hash).
2. Wave-10 LENS PASS worker (the per-wave program + fast-mode tripwire read).
3. Wave-11 dispatch carries P-14 amendments already installed (S5-05, s3a-
   frontend-review-cover-send, s3a-slice5-amber incl. GRANTS MINTING).
4. E2E full-stack pass (if my in-flight worker died unreported, re-dispatch
   with the §E2E method above — it was mid-setup on the :8001 leg at handover).
5. C3: gate-battery evidence pre-assembly (coverage dispositions, AC-waiver
   justifications [AC-hrdd-39, AC-s5-43], per-group verification evidence,
   findings-closure checklist settled) — the post-DAG battery is sequential
   gates; STRICT_VERDICT_DISPOSITION surfaces judgment calls → operator options.
6. C4: PR prep — planning artifacts stay OUT via .git/info/exclude (100e10b
   pattern, NEVER tracked .gitignore); PR body skeleton from audit entries;
   CREATION IS OPERATOR-GATED.
7. End gate: PROVISIONED e2e runs browser lanes natively (profile
   native_test_configs=["spend-client/playwright.config.ts"], row 2249062).

## LEDGER / GOTCHAS (hard-won tonight; full detail in WORKFLOW-BUG-BACKLOG.md)
- Wrapper self-match: NEVER `pgrep -f "bin/iriai-build-v2 resume"` for the PID.
- Template lockdir wedge: ANY engine kill mid-template-build orphans
  <FW-features>/sandbox-template/*.building → all future builders wait 900s
  loops AND DO NOT RETRY after the lock clears — bounce + sweep is the heal.
- Silent-CPU-loop wedge (develop34): dual patch lineage for one task (resume
  re-dispatch created a sibling chain) → drain loops at 32-44% CPU, zero rows;
  heal = reconcile to ONE chain (newest result row's patch_summary_ids is
  canonical; FK-pinned rows get KEY-RENAMED to superseded-*:, never deleted).
- 'partial' result poisons batch enqueue (twice): env-only deviations
  (sandbox DNS, out-of-write-set asks, Context7 quota) → supersede the
  dag-task row partial→completed with in-row triage evidence (backups in
  /tmp/kaya_g2_seal_backups_20260611/).
- Attempt-budget orphans: infra-killed attempts leave started/failed
  dispatch_attempt rows that count against the 4-attempt budget → CSV-backup +
  DELETE the wave's rows to reset.
- Empty-write-set groups (only RCAN-00 existed; none remain) seal via the
  W-OG override-evidence reader branch (commit 729a587).
- Auth0 tenant namespace is dev.pltfm.usekaya.ai (checked-in action file
  says app.pltfm — repo-vs-tenant drift).
- Host swap >85% = the 600s-rung/silent-death mimic; operator closes apps.
- Pool dispatch counts reset whenever profiles.json weights change.
- OPERATOR CARVE-OUTS: product-safety, kaya PR creation/merge, account/quota,
  launchctl (a PENDING runner-restart ask for the stale cap-8 runner remains
  open — wave width ≤4 remaining so non-blocking).

## IN-FLIGHT AT HANDOVER (all DIED with my session)
- develop37 (PID 60918, log /tmp/kaya_develop37.log): wave-10 implementers
  streaming on fast-mode codex (~09:40 exec start). On boot: sweep, check
  wave-10 journal state (captures? orphan attempt rows → reset), relaunch.
- Full-stack e2e worker: mid-setup on the :8001 backend leg; NO report —
  re-dispatch per §E2E (its method is fully specified there).
- W-DL worker (diagnostic-lease reclaim): never reported; its scope is
  covered operationally by the sweep; treat as lost.
- The wave-10 lens pass: NOT yet dispatched (wave wasn't sealed).
