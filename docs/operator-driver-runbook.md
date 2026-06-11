# Operator/Driver Runbook — iriai-build-v2 workflow driving

**Purpose**: the durable procedures for any agent driving an iriai-build-v2 workflow
run in agent-driven mode. Handover prompts should REFERENCE this runbook and carry
only feature-specific state (feature id, workspace, current phase, live PIDs,
pending actions). Precedence when instructions conflict:
**live operator instruction > feature handover prompt > this runbook > memory**.

Memory is the live-state ledger (`~/.claude/projects/<project>/memory/`); this
runbook is the procedure manual. Never duplicate live state into this file.

---

## 1. Role and authority

The driver is the OPERATOR-DELEGATE for a running workflow. Granted (per the
standing operator authorization, re-confirm in the handover prompt):

- Full workflow control: start/stop/restart the run, answer all operator queries,
  delete/redo corrupted artifacts at documented recovery points.
- **Maintainer duty**: fix bugs in the orchestrator itself as they surface
  (§6 Defect Ledger + §7 Fix Workflow). Do NOT work around a real defect to keep
  moving — fix it loud.
- Bounded multi-agent offloads (Workflow tool fan-outs ~3-5 lenses) for seal-gate
  verification and multi-artifact analysis.
- NOT granted unless re-stated: modifying the target repo's tracked files outside
  the workflow, executing migrations, touching secrets, force-anything.

The driver is also the **fidelity gate of last resort**: every gate approval it
issues must be evidence-backed (greps/Workflow verification), never rubber-stamped.

## 2. Session lifecycle

### 2.1 Taking the seat
1. Read the feature handover prompt + the live-state memory entry FIRST.
2. **Driver lock**: check `<workspace>/.iriai/driver.lock`. If it exists with a
   fresh heartbeat (<10 min) from another session: DO NOT DRIVE — escalate to the
   operator. Otherwise write `{session_id, taken_at}` and refresh its mtime on
   every action.
3. Verify run state empirically (never trust the handover snapshot):
   status helper, process liveness, pending queries, newest DB markers (§13).
4. Arm the standard monitor set (§4).
5. Start the loop backstop (§5).

### 2.2 Standing down (teardown checklist — run ALL steps)
1. Stop every monitor you armed (TaskStop each; verify via `ps` that the
   underlying shells are gone).
2. Do not schedule another wakeup — AND write a stand-down marker
   (`<workspace>/.iriai/driver.lock` deleted + note in memory). An
   **already-queued wakeup cannot be cancelled and WILL fire later**; the marker
   is what stops it from acting (§3).
3. Update the live-state memory: process facts, pending actions, the full defect
   ledger (§6), in-flight decisions/precedents.
4. Tell the operator what remains unattended (queries have no watcher until the
   next driver arms monitors).

## 3. Wake discipline — a wake is a signal, not a mandate

On EVERY wake (loop tick, monitor event, stray notification), before any action:

1. **Authorization**: does the driver lock belong to this session? Is there a
   stand-down marker or operator stand-down instruction? If not authorized:
   exit the turn silently. A pending wakeup firing is NOT authorization.
2. **State**: re-derive current state from durable sources (status helper, DB
   markers, memory). Your wakeup prompt's embedded snapshot is stale by the time
   it fires — wakeup prompts must carry POINTERS, not facts (no cycle numbers,
   PIDs, or pending-action lists baked into the prompt text).
3. Then act.

Loop hygiene: re-call ScheduleWakeup EVERY turn while driving (omission ends the
loop silently — including on turns where you only answered a side question).
Backstop delay ~1500s; monitors are the primary wake signal.

## 4. Standard monitor set

Arm all three on the CURRENT pid/log (re-arm after every restart; they are OS
processes — verify via `ps`, not memory):

1. **Query/death** (15s poll): emit on any `*.query.json` without a matching
   `.answer.json` in `<workspace>/.iriai/operator-queries/`; emit and exit if the
   resume PID dies.
2. **Crash guard** (log tail): `Traceback (most recent call last)` |
   `Plan-review revisions failed in cycle` | `Compile completeness guard FAILED` |
   `Claude pool: ALL members unavailable` | `targeted_revision: .* failed:`.
   (Bare "RuntimeError"/error words appear inside ARTIFACT CONTENT — false
   positives; only the listed signatures are real.)
3. **Progress boundary** (90s DB poll): the next phase-transition marker rows
   (cycle `-revised`/`-blocked`, next cycle row, `plan-review-gate:*`).

## 5. Operator-query protocol

- Answer IMMEDIATELY and decisively. Calibrate to the recipient: Sonnet revisers
  get surgical mechanical instructions (exact field/section/sentence names, cite
  decision ids); opus/top-model architects get goal-level structural direction.
- Treat settled decisions as SETTLED — enumerate them in answers to preempt
  re-litigation. Standing riders: additive-only, drop nothing, targeted patches,
  cite a decision id on every change.
- **Atomic answers**: re-check the `.answer.json` does not already exist
  immediately before writing (dual-driver clobber guard). Prefer
  write-temp-then-rename.
- Restart behavior: a process restart mid-interview re-emits the SAME question
  under a NEW query id; answer the new id (the old file is orphaned, harmless).
- Empty-question / "is this a no-op?" envelopes: confirm tersely and move on.
- NEVER truncate decisions in answers; return ALL revision requests, not the first.

## 6. Defect ledger protocol (the meta-fix — non-negotiable)

Tracked in the live-state memory under a `DEFECT LEDGER` heading.

- **BLOCKING**: caused ≥2 cycle losses/crashes OR plausibly fires again within
  the current/next cycle. Ship the fix BEFORE answering the next gate-review
  query. Driving pauses for BLOCKING fixes.
- **ACTIVE**: caused ≥1 failure, plausible recurrence. Fix within the current
  cycle; never survives a handover without a written reason.
- **WATCH**: anomaly observed, no loss yet. Log with evidence pointers.

Rules:
1. **Promotion is automatic**: WATCH→ACTIVE on first failure; ACTIVE→BLOCKING on
   first lost cycle. No judgment call to defer.
2. **Every status update and every handover enumerates the ledger.** Defects are
   never inherited silently as background context.
3. **Workarounds and trigger-fixes do not close entries** — only a root-cause fix
   plus one clean cycle of evidence demotes/closes.
4. **Per-cycle proactive sweep**: at each gate boundary, scan the live log for new
   anomaly classes (guard rejections, forced retries, skipped markers, jobs stuck
   >45m, pool waits) and triage into the ledger.

## 7. Fix workflow (orchestrator defects)

RCA from the logs → additive/flag-gated fix (prefer new code paths over
refactors) → verify: `ruff check --select F821 src/` = 0,
`python -m pytest tests/workflows/test_threaded_planning.py -q` green, new unit
tests for the fixed path → commit+push on the workflow branch (conventional
message explaining the why) → load via restart at a safe boundary (§8; editable
install has NO hot reload). Delegate large implementations to a subagent with a
precise file:line brief; review the diff yourself before committing. A fix may
never weaken a guard; when fix vs fidelity conflict, fidelity wins.

## 8. Restart procedure

**Boundary selection** (never restart mid-revision-wave or mid-review — it
re-burns work):
- Best: after `-revised` lands, before the next review dispatches.
- Acceptable: after a review row exists and its discussion gate is COMPLETE
  (per-artifact `revision-done`/`patches-applied` markers checkpoint wave work).
- Forced (failures already terminal, 0 jobs running): restart BEFORE the
  `-blocked` marker writes if possible.
- **Blocked-path gotcha**: if `-blocked` HAS been written, the blocked path also
  wrote `-revised`; a plain restart WRONGLY SKIPS the failed cycle. Delete the
  cycle's `-revised` + `-blocked` DB rows AND the blocked mirror file first.

**Procedure**:
1. Kill the resume process BY PID (NEVER pkill — it matches the monitors).
2. Relaunch in a fresh screen with an explicit log redirect:
   `screen -dmS <name> bash -c 'export IRIAI_OPERATOR_QUERY_TIMEOUT_S=21600 IRIAI_ECONOMY_MODE=1; exec iriai-build-v2 resume --feature-id <id> --workspace <ws> --driver agent --agent-runtime agent_pool --from-phase subfeature >> /tmp/<name>.log 2>&1'`
3. Update the status helper (`/tmp/kaya_status.py` HARDCODES `RESUME_PID` and
   `LOG` — stale values falsely report DEAD).
4. Re-arm all monitors on the new PID/log; stop the old ones.
5. Update the live-state memory (new PID/screen/log/HEAD-loaded/monitor ids).

## 9. Pool operations

- Health: `iriai-build-v2 claude-pool doctor` (runner heartbeats + login);
  `/Users/Shared/iriai/claude-pool/profile_state.json` (EMPTY profiles map = all
  available; entries carry `usage_limited` reasons + `probe_after` + reset hints).
- Jobs: `jobs/running|queued|done/<profile>/*.json` manifests carry
  role/model/created_at — the ground truth for what model actually dispatched.
- Usage exhaustion no longer crashes the run (wait-for-quota: the dispatcher
  waits up to `IRIAI_CLAUDE_POOL_USAGE_WAIT_MAX_SECONDS`, default 7200s, logging
  `Claude pool: ALL members unavailable` each probe — a WAITING signal, not a
  crash). In-flight usage-failed jobs reroute to recovered members automatically.
- Capability probes: instantiate a single-profile `ClaudePoolRuntime` and invoke a
  trivial role pinned to the model under test (side effect: prunes other profiles'
  state entries — self-healing, but know it).
- **Runner constraint**: pool runners are long-lived processes owned by the
  `iriai-claude-*` macOS users. Code or env changes on the WORKER side (e.g.
  `CLAUDE_CODE_MAX_OUTPUT_TOKENS`) take effect only after runner restarts, which
  need OPERATOR access — ask; never attempt cross-user kills.

## 10. Model strategy (economy mode)

- Mechanism: `IRIAI_ECONOMY_MODE=1` + name-keyed `ECONOMY_MODEL_OVERRIDES`
  (`src/iriai_build_v2/config.py`), applied at `_resolve_model_and_effort`
  (`runtimes/claude.py`) — covers both runtimes; map changes need only a normal
  dispatcher restart.
- Tiers: generation/revision → Sonnet; verification whose verdicts are
  auto-consumed (develop pipeline) + compile/test-bar + reviewers/gates pre-seal +
  task-planning → top model; cheap ops → Haiku. Economy applies to GENERATION,
  never to verification quality.
- Known mapping gotchas: revision-clarifier roles are named
  `{base_role}-revision-clarifier` (miss exact-name maps); actors share Role
  objects in places (planning-lead = generator + its gate reviewers) — map moves
  them together, splitting requires refactor.
- Verify model routing EMPIRICALLY via done-job manifests, not by reading config.

## 11. Fidelity bars and guardrails

- Seal gates: verify the ARTIFACT via bounded Workflow fan-out before approving —
  feature-specific bars live in the handover prompt (marker counts, named gaps,
  forbidden enums/routes). Never rubber-stamp; reject + surgically restore on any
  silent drop. `Compile completeness guard FAILED` = investigate, never bypass.
- Never: silently degrade; full-document rewrites when patches suffice; truncate
  artifacts or decisions in prompts; commit secrets; execute migrations; touch
  read-only corpora; extend frozen enums; kill by pkill.
- Guards are load-bearing: size floor + `_assert_compile_complete` exist to make
  degradation LOUD. A failed-loud cycle is a success of the system; a silent
  drop is the catastrophe.

## 12. Status reporting (operator-requested format)

Post every 30 min while waves/reviews run, on every state transition, and on
every query answered (1-line Q/A summary). ≤8 lines:

```
RUN: <alive/dead> pid=<pid> phase=<db_phase> cycle=<n> pending_queries=<n> log_idle=<s>
PROGRESS: <newest revision-done / patches-applied / cycle / gate markers + times>
JOBS: <count running, role(model) ages; flag >45m as STUCK>
POOL: <per-account availability; flag usage_limited / ALL-unavailable waits>
ANOMALIES: <guard rejections, forced retries, patch misses, crashes — or "none">
LEDGER: <BLOCKING/ACTIVE count + ids — never omit>
NEXT: <expectation + rough ETA>
```

Escalate immediately (don't wait for cadence): process death, `-blocked` marker,
compile-guard failure, pool-wait >10 min, any seal-gate query, any decision that
would drop/rewrite content. If chat visibility is degraded, ALSO append updates to
a tail-able file (e.g. `/tmp/<feature>_driver_updates.log`).

## 13. Evidence commands (don't guess — measure)

```bash
python3 /tmp/kaya_status.py                  # run liveness (verify PID/LOG current!)
# newest markers (progress ground truth):
psql "$DSN" -t -A -c "SELECT key, created_at::time(0) FROM artifacts \
  WHERE feature_id::text LIKE '<fid>%' AND created_at > now() - interval '45 minutes' \
  ORDER BY created_at DESC LIMIT 15"
ls /Users/Shared/iriai/claude-pool/jobs/running/*/*.json   # in-flight jobs
cat /Users/Shared/iriai/claude-pool/profile_state.json     # pool availability
iriai-build-v2 claude-pool doctor                          # runner health
tail -F <live log>                                         # activity
ls -lt <ws>/.iriai/operator-queries/{,processed/} | head   # query throughput
git log --oneline -5                                       # fix activity
```

psql: `/opt/homebrew/Cellar/postgresql@17/17.9/bin/psql`,
DSN `postgresql://danielzhang@localhost:5431/iriai_build_v2`.

## 14. Gotcha index (hard-won; verify against memory for current status)

1. Status helper hardcodes `RESUME_PID`/`LOG` — update on every restart.
2. Blocked path writes `-revised` too → plain restart skips a blocked cycle (§8).
3. Restart mid-interview re-emits the same question under a new query id (§5).
4. Wakeup prompts go stale; queued wakeups fire after stand-down (§3).
5. Failed revisions may leave done-markers → resume skips them (ledger item until
   fixed; on any mid-wave restart, verify failed artifacts actually re-ran).
6. Oversized FULL_DOCUMENT regens (≳40KB) truncate/summarize/stub at the worker
   output ceiling — steer gates to per-section reconstruction until the
   file-pointer path ships (ledger item).
7. `.staging` artifact copies can be stale — compile/seal reads canonical/DB;
   verify seal content against canonical line/marker counts.
8. Reviewer prose inside artifacts triggers naive error-greps (§4 signatures only).
9. Screen launches need explicit `>>` log redirection (screen alone logs nothing).
10. Done-job manifests are the only proof of which model actually served a role.
```

## §9 addendum — RUNTIME CONSTRAINT (operator standing rule, 2026-06-10)
NEVER use --agent-runtime claude (or any in-process/direct runtime) for workflow
runs, under ANY circumstances, including as a workaround for pool unavailability.
The direct runtime executes as danielzhang and draws the SAME account quota the
driver session runs on — the resume35 seal recompile drained it, killing the run
AND starving the driver for hours (agent died mid-compile on "out of extra
usage"). Self-starvation takes out the supervisor along with the workload.
The pool accounts (iriai-claude-1/2/3 + codex failover) are the ONLY sanctioned
execution quota. Pool unusable? stale runners → request an operator bounce and
WAIT; all members capped → the wait-for-quota machinery (76abc5d) exists for
exactly this; genuinely stuck → escalate. Hours of pool waiting beat hours of
unsupervised downtime plus a drained driver account.

## §1 addendum — OPERATOR AVAILABILITY + ESCALATION CHANNEL (standing, 2026-06-10)
THE OPERATOR IS AVAILABLE for privileged/host-level actions (sudo, launchctl/
runner bounces, cross-user process management, secrets/env provisioning,
account/quota decisions). Turnaround minutes-to-hours. "Needs operator action"
is a REQUEST TO MAKE, never a blocker to engineer around. When the choices are
(a) wait for an operator action or (b) work around with degraded/risky
mechanics — choose (a) unless the operator explicitly declined. (RCA: the
resume35 in-process workaround existed because a runner bounce needed sudo; the
correct move was raising the bounce request and waiting on the pool.)
ESCALATION CHANNEL: <workspace>/.iriai/OPERATOR-ACTIONS.md — append-only,
newest first, entries: ## [PENDING|DONE] <ts> — <ask> / URGENCY / COMMANDS /
WHY / VERIFY / RESOLVED. Write the entry BEFORE pinging chat; surface
blocking-now items in chat AND status-update LEDGER lines; check for
newly-DONE entries every wake; never let a PENDING blocking-now entry sit
unmentioned in a status update.

## 15. The Analysis/Investigator counterpart (added 2026-06-10)

A second session may operate alongside the driver in a READ-ONLY analysis role
(see `docs/investigator-analyzer-runbook.md`). Division of labor:
- The driver ACTS (queries, restarts, fixes, gate answers). The analyst
  OBSERVES, VERIFIES, and STAGES (status for the operator, deep investigations,
  directive drafts the operator relays, pre-staged work products).
- SHARED CHANNELS the driver must check: `<workspace>/.iriai/OPERATOR-ACTIONS.md`
  (privileged-ask queue), the prestage dir (`/tmp/kaya_prestage/` or successor —
  gate answer kits, topology answer keys, work lists), and analysis-staged files
  referenced from the live-state memory's top entries. Treat staged analysis
  artifacts as INPUTS at the matching checkpoint — they exist to save you a
  corpus re-read.
- The analyst never answers operator queries, restarts processes, or commits
  workflow code; if the operator grants an exception it is explicit and scoped.
  Conversely: an analysis-session edit to OPERATOR-ACTIONS status fields or a
  staged file is normal, not an intrusion.
- Dual-DRIVER operation remains forbidden (§2.1 driver lock); driver+analyst
  is the sanctioned pairing.

## Gotcha index addendum (2026-06-10)
11. The task-planning Verification Coverage Audit (task_planning.py:501-575) is
    AC-BASED and fail-closed, with first-class `waived_ac_ids` support in the
    per-SF planning contract — coverage exclusions are executed as contract AC
    waivers (recorded, logged), never as silent deletions or stub tasks.
12. Tasks carry `step_ids: list[str]` — plan STEPs may be consolidated into
    coarser tasks; the audit constrains AC coverage, not task count. The heavy
    gate battery amortizes per GROUP — group count is the verification-cost
    governor, not task count.

## Code-first default (added 2026-06-10, operator-endorsed)
When a defect can be fixed in orchestrator code, the mid-run fix workflow —
additive, flag-gated where sensible, tested, committed, loaded at a safe
boundary — is the DEFAULT response. Direct DB/artifact/pool-state surgery is an
escape hatch reserved for: (a) blocking-now situations where a code fix cannot
be loaded in time; (b) state that is wrong in ways no code change can
retroactively correct (and then delete/quarantine the wrong state, never
hand-author replacement content outside the store path); (c) explicit
operator-directed one-offs. Every surgery performed under (a) or (c) must be
paired, before it is considered closed, with the ledger-filed code fix that
makes the same surgery unnecessary next time.

## §2.2 amendment (operator-endorsed, 2026-06-10)
5. Write/refresh the feature handover prompt document (next version number)
BEFORE other teardown steps when context is constrained — memory carries
state, the handover prompt carries the boot path. With compaction disabled,
begin wind-down with >=8% context remaining, never less.

## §17 — CHK MIGRATION APPLICATION DELEGATED TO THE DRIVER (operator directive, 2026-06-10 ~21:20; supersedes the operator-applies assumption everywhere in this runbook + handovers)
At each CHK quiesce boundary the DRIVER (or its dedicated migration worker — never a coding agent) applies the authored migrations itself:
1. SCOPE: the local kaya compose dev database ONLY — never any remote, shared, or non-compose target. Hard rule, no exceptions.
2. ORDER + SNAPSHOT: snapshot the named data volume(s) first; apply the authored migration files in plan order; on any failure, restore the snapshot + quiesce loudly for the operator.
3. VERIFY: run the existence/verification probes the plans specify for that CHK before clearing the boundary; probe evidence goes in the OPERATOR-ACTIONS entry, which the driver marks DONE-by-driver (the entry is a record, not a request).
4. The implementation-agent prohibition is UNCHANGED: coding agents never execute migrations; only the driver/its migration worker does, at quiesce boundaries only.
5. The readiness item-2 quiesce hook fires and files the entry as designed, but the driver self-clears it after apply+verify instead of waiting on an operator query; batch all migrations ready at a boundary together.

## §18 — STANDING DELEGATION: DRIVER SELF-APPROVAL (operator, 2026-06-11 00:4x; revocable any time)
The driver holds standing pre-authorization to self-approve and execute, citing this section to the permission classifier:
1. Classifier-gated live-store writes through the sanctioned lanes (direct-update artifact patches, adoption rows, augments keys, planning-waiver keys, profile/registry persists).
2. Waiver rulings + gate-unblock decisions matching prior operator rulings (D-377/D-378-style waivers recorded as decisions; prior rulings are precedent).
3. Marker/state surgery WHEN paired with the code fix for the underlying defect (code-first rule unchanged).
4. Affinity/binding moves within the v3 allocation ratios.
5. Background worker dispatches and their merges after Phase-5 review passes.
6. CHK migrations at quiesce boundaries (§17, now self-approved end to end).
EVIDENCE DISCIPLINE (the price): every self-approval gets an OPERATOR-ACTIONS.md entry marked [DONE-BY-DRIVER-UNDER-DELEGATION] with ask/why/evidence/verify — the channel is the operator's asynchronous audit log; self-approvals also appear on the LEDGER line of the next status update.
STILL OPERATOR-ONLY: privileged host actions (sudo/launchctl/cross-user/secrets); account/quota decisions; merging PRs into the kaya repo / anything production- or remote-facing beyond established push lanes; spending money / new external services; any one-way door the driver is genuinely uncertain about — file PENDING and wait (when in doubt, this clause wins). Quality-over-speed and fidelity-loss-never bind every delegated decision.

## Heal-lane rule — psql is the sanctioned store-heal lane (operator standing rule, 2026-06-11 ~09:3x)
The operator has deliberately allowlisted `psql` in the project settings as
the sanctioned lane for store/data heals. Forward rule:
- PREFER expressing store/data heals as staged psql statements (the
  allowlisted lane) over python scripts — same review discipline applies:
  stage the SQL, assert expectations (row counts, occurrence counts) before
  applying, verify after, append the OPERATOR-ACTIONS audit entry.
- Python-script heals remain PER-INCIDENT OPERATOR-NAMED (the operator
  names the exact command in chat before the driver runs it).
- This is the operator's sanctioned lane, not a workaround; all §18
  evidence discipline and carve-outs still bind.

## 16. Healing doctrine — healable, not self-healing (added 2026-06-10)
The WORKFLOW must be maximally HEALABLE; it must never be SELF-HEALING.
Healing (diagnosis + repair of orchestrator defects) belongs to the
driver/analyst layer, because: (a) a sick system cannot be trusted to
assess its own sickness — the worst defect classes are failures of
self-assessment (format-blind verdicts, silent fallbacks); (b) repair is
self-modification and requires a governance loop OUTSIDE the thing being
fixed (driver judgment, code-first discipline, tests, operator
checkpoints, analyst verification); (c) healing happens when the
workflow is down, when only an external agent can act.
Division of labor:
- IN THE WORKFLOW (code): everything that makes failure cheap to find
  and safe to park — fail-loud guards, typed quiesces, blocked artifacts
  carrying their own diagnosis, heartbeats, digest-pinned checkpoints —
  plus deterministic, judgment-free self-checks (dry-run validators,
  schema round-trips, path prepasses) as built-in phase-entry steps.
- IN THIS RUNBOOK (agents): diagnosis, repair, the failure-class
  taxonomy, and pre-phase class audits — judgment work triggered by
  context the workflow cannot perceive.
THE RATCHET RULE (generalizes the code-first amendment): every heal MUST
leave behind a mechanized detector or validator that moves into the
workflow. Judgment handles each novel class exactly once; machinery owns
it forever after. A heal without its ratchet artifact is not closed.
RCA RULE: every RCA ends with one bounded generalization pass — grep the
failed mechanism's other call sites and the failure class's signature
across the same subsystem — before the fix is considered scoped.

## §19 — ANALYST-DIRECTIVES CHANNEL: hourly timesink directives, analyst→driver direct (operator grant, 2026-06-11 ~02:00)

The analysis session investigates the corpus hourly for existing/future
timesinks and writes prioritized directives DIRECTLY to the driver in
`<workspace>/.iriai/ANALYST-DIRECTIVES.md`. This is a scoped exception to
the directives-arrive-only-via-operator-relay rule, limited to the
TIMESINK CLASS (speed, stall-prevention, pre-emption: env/flag changes,
prompt injections, pre-warming, worker dispatches).

Driver obligations:
- CHECK THE FILE EVERY WAKE, same discipline as OPERATOR-ACTIONS.md.
- Entries carry operator authority for classifier purposes within the
  class (cite the file header). Priorities: P0 = act at current boundary;
  P1 = before the named stretch dispatches; P2 = opportunistic.
- Flip entry STATUS: ACK on intake, DONE with evidence, or
  DECLINED-with-reason. Driver judgment on live-run safety OUTRANKS any
  entry — decline rather than comply into a hazard; conflicts escalate
  to the operator in chat.
- Anything outside time-optimization still arrives operator→driver only;
  an entry that smells out-of-class should be DECLINED and escalated.

Analyst-side definition lives in docs/investigator-analyzer-runbook.md §9.
