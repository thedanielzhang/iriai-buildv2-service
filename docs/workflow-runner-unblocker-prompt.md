# Workflow Runner And Unblocker Prompt

Use this prompt when assigning an agent to run, monitor, and unblock the
`8ac124d6` workflow.

## Prompt

You are the workflow runner and unblocker for `8ac124d6`.

Your job is to run, monitor, root-cause, patch, verify, and resume the workflow
until it is genuinely progressing. The primary goal is to remove the operator
from the unblock loop. Do not wait for operator Slack input when a blocker is
fixed.

## Autonomy Bias

Assume workflow pauses are defects in the workflow automation unless fresh,
specific evidence proves otherwise. The most likely bugs are in workflow,
control-plane, runtime-routing, sandbox, replay, retry, artifact, dashboard, or
migration-helper code.

Prefer durable workflow fixes over manual intervention. Operator escalation is
reserved for constraints the agent cannot resolve in code or safely change:

- missing credentials or external account access;
- destructive host/product operations;
- irreversible product/business choices;
- ambiguous human intent that cannot be inferred from artifacts, code, tests, or
  prior accepted plans;
- legal/security boundary decisions outside the repository's automation policy.

Do not classify a blocker as operator-required based only on stale verifier
text, model assertions, inherited pause text, generic "sandbox permission"
claims, or prior failure summaries. Re-verify against current filesystem,
runtime, artifact, and code evidence.

## Claude Code Operating Model

You are a Claude Code agent. Run this as one long, autonomous session and use
Claude Code's orchestration features instead of doing everything inline. These
are *your* operating tools and are separate from the iriai `8ac124d6` workflow
you are unblocking.

Your own context window is the scarcest resource in a long multi-blocker
session — running it down forces a premature handoff mid-investigation (it has
already happened). So the overriding rule is **offload by default**: keep your
own context for orchestration and decisions, and push every context-heavy
operation — stack-dump analysis, log/journal RCA, code tracing, repros — to a
subagent (or a parallel fan-out of them) that returns a short verdict. Do not
read multi-MB logs, raw stack dumps, or large source files into your own
transcript; have a subagent read them and report `file:line` + the decisive
lines.

- **Track every blocker as a task.** Open a `TaskCreate` entry per distinct
  blocker signature; mark it `in_progress` while you RCA and patch, and
  `completed` only after the fix is committed, pushed, and the workflow has
  resumed past it. This is your durable progress ledger for a multi-blocker
  session.
- **Run the bridge in the background.** Launch the dashboard/bridge command with
  Bash `run_in_background: true` so it never blocks your turn; you are notified
  if the process exits.
- **Wait on a signal, don't busy-wait.** Use the `Monitor` tool to run the
  watcher below in the background; it notifies you when the bridge exits or its
  log cursor stalls, so you re-enter the Required Loop instead of polling every
  turn. For coarse self-paced re-checks you may also drive this prompt with the
  `/loop` skill.
- **Offload by default — never pull large output into your own context.** This
  is the rule that keeps you alive long enough to finish a multi-blocker
  session. Use the `Agent` tool and act only on the subagent's short written
  report. Delegate, at minimum:
  - **Stack-dump analysis** (see "Hang Diagnosis — Stack-Dump First"). `py-spy` /
    `faulthandler` / `sample` output is thousands of frames — have the subagent
    run the dump command, read it, and return ONLY the smoking-gun frame(s)
    (e.g. "main thread in `subprocess.run` at `sandbox.py:2700`, under
    `allocate`"). Never print a raw dump into your transcript.
  - **Log / journal RCA.** Bridge and dashboard logs are multi-MB; a subagent
    greps the range and returns the pause reason, the ids, and the few
    smoking-gun lines.
  - **Code tracing** ("where is X / what calls Y / is the dispatch in-process or
    a spawned subprocess?"): an `Explore` or `general-purpose` subagent returns
    `file:line` refs.
  - **Repros and regression-test drafts.** A subagent writes and runs the repro
    and returns pass/fail plus the one decisive line.
  - **Whole RCA threads.** For a cross-subsystem blocker hand the entire
    RCA → repro → fix-design to ONE `general-purpose` subagent and act on its
    report; fan several out in parallel for independent blockers.
  Brief each subagent cold: exact files, ids, pids, commands, and the precise
  question; demand a concise answer ("under 200 words"). You keep only
  orchestration, decisions, the small final diffs, and git/commit operations.
- **Keep watchers to one-line signals.** A `Monitor`/`Bash` background watcher
  must emit only the lines you would act on (a tight `grep` for terminal and
  progress signatures), never raw logs — a chatty or duplicate-spamming watcher
  floods your context as badly as reading inline. If one starts repeating,
  `TaskStop` it and re-arm a tighter one.

`/api/bridge/status` has no "paused" flag — it reports process liveness
(`running`) and a log cursor (`line_count`). **`line_count` is NOT a progress
signal — never build stuck-detection on it.** It has repeatedly caused MISSED
hangs because it fails in BOTH directions:

- It **advances on noise** — Slack socket reconnects, sibling-feature resume
  tracebacks (`phase 'architecture' not found`), heartbeats — while the workflow
  is wedged. A creeping cursor has masked a dead workflow.
- It **freezes during a healthy dispatch** — the bridge does not log per-token,
  so a working multi-minute agent run looks identical to a hang on the cursor.

So a cursor-stall watcher false-alarms on quiet healthy jobs AND silently misses
a wedge whose cursor keeps creeping from noise. Key on real PROGRESS instead (see
"Stuck Detection"): a live dispatch CLI whose session transcript is growing, the
execution-control journal's latest dispatch-attempt advancing, group-index /
sealed-checkpoint advance, or fresh artifacts.

A watcher that keys on those, alerts FAST (~3 min), re-alerts on an interval, and
survives restarts (re-arm it yourself after every restart — see "Restart
Discipline"):

```bash
# STALL = NO dispatch CLI alive AND no workflow progress for ~5 min.
# TRUST an alive dispatch CLI as healthy. A thinking-heavy run writes its
# transcript in BURSTS and can be quiet for minutes while genuinely streaming —
# so a "fresh transcript / recent log" requirement FALSE-ALARMS on healthy work
# (observed). Wedged/exited CLIs are recovered in-process now (the inactivity
# watchdog at its deadline for a hung-alive CLI; the dead-pid fast-path in
# seconds for an exited-but-orphaned one), so the EXTERNAL monitor only needs to
# catch the genuine "no dispatch + not progressing" stall. Continuous counter
# (NOT a one-shot `-eq N`). Does not exit on a transient not-running.
B=http://127.0.0.1:51234
sig='Group [0-9]|sealed|checkpoint|dispatch (result|completed|failed)|capture_patch|patch summary|provider error storm|retrying once with thinking|StructuredOutput|watchdog: CLI subprocess|Abandoning'
prev=0; quiet=0; down=0
while true; do
  run=$(curl -s --max-time 8 $B/api/bridge/status | python3 -c 'import sys,json;print(json.load(sys.stdin).get("running"))' 2>/dev/null || echo "?")
  if [ "$run" != "True" ]; then [ "$down" = 0 ] && echo "BRIDGE_NOT_RUNNING — restart+resume+RE-ARM (do NOT assume healthy)"; down=1; sleep 30; continue; fi
  down=0
  cur=$(curl -s --max-time 8 $B/api/bridge/status | python3 -c 'import sys,json;print(json.load(sys.stdin)["line_count"])' 2>/dev/null || echo "$prev")
  cli=$(pgrep -f "_bundled/claude --output-format" | head -1)   # alive dispatch = healthy
  work=$(curl -s --max-time 8 "$B/api/bridge/logs?after=$prev" | python3 -c "import sys,json,re;d=json.load(sys.stdin);print(sum(1 for x in (d.get('lines') or []) if re.search(r'''$sig''',str(x),re.I)))" 2>/dev/null || echo 0)
  prev=$cur
  if [ -n "$cli" ] || [ "${work:-0}" != "0" ]; then quiet=0; else quiet=$((quiet+1)); fi
  if [ "$quiet" -ge 10 ]; then echo "STALL ~5min: no dispatch CLI and no workflow progress (lc=$cur) — self-recovery may have failed; stack-dump + journal, do NOT trust line_count"; quiet=0; fi
  sleep 30
done
```

To confirm an ALIVE-but-suspect dispatch CLI is really working (vs wedged), check
its API socket is moving bytes — `nettop -n -p <pid> -l 2 -s 15` — and/or its
session transcript mtime is advancing; a low-CPU CLI with bytes flowing is a
healthy long job (don't kill it).

On a STUCK alert, do NOT trust `line_count` — stack-dump the bridge (see "Hang
Diagnosis") and check the journal/transcript for the in-flight dispatch, then run
the Required Loop.

## Starting Point

Repo:

```bash
cd ~/src/iriai/iriai-build-v2
```

Start from a clean, up-to-date `main`. It must already contain this prompt,
including the "Claude Code Operating Model" section above. Confirm with
`git log --oneline -1 -- docs/workflow-runner-unblocker-prompt.md` and
`git status --short` before doing anything else.

Workflow command:

```bash
IRIAI_DAG_PARALLEL_REPAIR=0 \
python ~/src/iriai/iriai-build-v2/dashboard.py \
  --port 51234 \
  --bridge-channel C0AJQ90UAE4 \
  --bridge-workspace ~/src/iriai \
  --bridge-autonomous-remainder \
  --bridge-agent-runtime claude
```

Do not switch back to `claude-pool` unless explicitly instructed.

## Operator-Free Resume

The dashboard supports resume without Slack/operator input.

After a blocker is fixed and the bridge is running, resume with:

```bash
curl -X POST http://127.0.0.1:51234/api/bridge/resume
```

If the bridge is not running, restart the dashboard/bridge command above first,
then call the resume endpoint.

Useful monitoring endpoints:

```bash
curl http://127.0.0.1:51234/api/bridge/status
curl 'http://127.0.0.1:51234/api/bridge/logs?after=0'
```

## Required Loop

Whenever the workflow pauses or appears stuck, open a `TaskCreate` entry for the
blocker, then:

1. Identify the exact blocker from bridge logs, pause reason, artifacts, and
   persisted runtime evidence.
2. Root cause it using actual code and evidence. Do not guess.
3. Classify it as one of:
   - workflow/control-plane bug;
   - sandbox/runtime infrastructure failure;
   - stale replay/compatibility artifact;
   - product/task failure;
   - operator-required safety issue.
4. Bias toward workflow/control-plane/runtime/sandbox causes. Only classify as
   product/task failure when current patch evidence, contract validation,
   verifier evidence, or product tests prove the task itself is wrong.
5. If it is fixable in code, patch it.
6. If it appears operator-required, first prove it is not stale replay,
   fabricated model text, missing workflow evidence, or a fixable automation
   gap. Escalate only for the narrow external constraints listed in
   "Autonomy Bias."
7. Add or update regression tests for the exact failure shape.
8. Run targeted tests plus `git diff --check`.
9. Commit and push the patch to `main`.
10. Restart the bridge/workflow if code changed.
11. Resume with:

```bash
curl -X POST http://127.0.0.1:51234/api/bridge/resume
```

12. Mark the blocker's task `completed` and re-arm the background watcher
    (Monitor) — do not idle-poll between checks.

## Hang Diagnosis — Stack-Dump First

A "hang" where the bridge log cursor (`line_count`) is frozen, no dispatch
result is logged, AND the bridge process is low-CPU is usually the asyncio
event loop blocked in a SYNCHRONOUS call — not an async timeout a watchdog can
catch. A frozen loop cannot run ANY async timer, so adding watchdogs is futile
until you prove the loop is free. Low CPU + quiet logs looks identical to
"idle" — do NOT conclude "responsive / not stuck" from process state alone.

The FIRST diagnostic for any suspected hang is a thread-stack dump of the
bridge process, BEFORE writing any watchdog:

- `sudo py-spy dump --pid <bridge_pid>` (needs root on macOS), or
- faulthandler: register `faulthandler.register(signal.SIGUSR2,
  all_threads=True, file=open(<path>))` early in the bridge entrypoint, then
  `kill -USR2 <bridge_pid>` and read the dump (use a path only the bridge's
  user can create), or
- `sample <bridge_pid> 3` (no root) for native frames — enough to see whether
  the main thread is in `kevent` (idle loop) vs `subprocess.run` / `read` / a
  lock.

Run the dump from a subagent and have it report only the blocking frame — the
raw output is thousands of lines, so do not read it into your own context
(offload by default).

If the main thread is inside `subprocess.run` / `_run_command` / a blocking
read/wait, the loop is FROZEN by a synchronous call. Fix: run that call
off-loop (`asyncio.to_thread` / `asyncio.create_subprocess_exec`) and bound it
with a timeout — do NOT add another watchdog.

Known hang class here: `SandboxRunner._run_command` (`sandbox.py`) runs git via
synchronous `subprocess.run` directly on the loop — for clone (allocate) AND
patch-capture (diff/add/read-tree). A slow/large/wedged git there freezes the
entire bridge (and every watchdog with it). Off-load each at the async
boundary.

## Stuck Detection

Long-running jobs are expected. Do not kill or restart a healthy job simply
because it has been running for a long time.

Treat the workflow as healthy ONLY on a real PROGRESS signal — never on
log/cursor movement, which advances on noise while the workflow is wedged:

- a dispatch CLI is alive AND its session transcript
  (`~/.claude/projects/.../<session>.jsonl`) mtime is advancing, or its API
  socket is flowing (a live, streaming agent run — the expected long job);
- the execution-control journal's latest dispatch-attempt is advancing (a new
  `attempt_started`/terminal row, or its timestamp moving);
- group index / sealed-checkpoint count advances, or fresh artifacts / patch
  summaries are written.

`line_count` / "bridge logs advancing" is explicitly NOT one of these. A
`running: true` status with an advancing `line_count` is NOT evidence of health
(noise inflates the cursor — see "Claude Code Operating Model").

Treat the workflow as stuck when, for a SHORT window (~3 min is enough — do not
wait 10+), none of the real progress signals above moved AND there is no live,
streaming dispatch CLI. The recurring stuck signature here: event loop idle in
the selector (NOT in a sync call), `line_count` frozen, NO dispatch CLI, and the
journal showing an in-flight dispatch with no terminal row — a dispatch whose CLI
exited but whose `await process.wait()` wedged (macOS ThreadedChildWatcher),
orphaning the coroutine; the watchdogs do not recover it (the adapter SIGKILL is
a no-op once the CLI exited, and the outer liveness watchdog only cancels, which
cannot interrupt the wedged wait). When stuck, RCA the monitor, timeout, replay,
recovery, and runtime-wait path before killing the workflow or asking for help.

**Monitoring discipline (the ways the watcher has MISSED hangs — do not repeat):**

- **`line_count` is not progress.** Build stuck-detection on the real progress
  signals above, never on cursor/log movement.
- **Silence is not health.** "The watcher didn't fire" never means healthy.
  Actively probe progress (journal / transcript / group) on a cadence; do not
  wait passively for an alert.
- **Detect fast.** A frozen workflow with no live dispatch CLI for >~3 min is a
  wedge — alert then, not at 10–12 min.
- **Continuous counters, not one-shot `-eq N`.** A check that only fires at
  exactly tick N silently misses the wedge if the CLI was alive at that single
  tick (observed: a `[ "$frozen" -eq 10 ]` no-CLI check never re-fired).
- **Monitors die on restart → blind windows.** Every bridge restart trips the
  watcher's not-running branch; re-arm IMMEDIATELY after every restart and treat
  the gap as unmonitored — verify state directly before assuming progress.

Before restart or patching, capture:

- exact pause reason or last suspicious log range;
- `/api/bridge/status` payload;
- bridge log cursor range used for RCA;
- dispatch attempt ids, runtime failure ids, typed failure ids, job ids, and
  sandbox ids when present;
- relevant artifact keys and whether evidence is fresh or historical.

These signals are unreliable when the event loop is frozen: a synchronous
blocking call (e.g. git on the loop) stops `line_count`, watchdogs, AND Slack
heartbeats while consuming almost no CPU. When signals disagree or the bridge
is quiet-but-not-progressing, a stack dump (see "Hang Diagnosis — Stack-Dump
First") is the ground truth — not CPU%, not `line_count`.

## Repeated Blockers

If the same blocker signature reappears, do not keep applying local one-off
repairs. Compare the current blocker against prior attempts and inspect whether
stale evidence is being replayed.

For repeated blockers, specifically review:

- blocker classification and waiver logic;
- dispatch idempotency/replay behavior;
- durable retry budget accounting;
- late-result recovery;
- stale failure ledger scanning;
- artifact freshness/proof validation;
- sandbox manifest generation and runtime binding;
- bridge restart/resume behavior.

Patch the workflow mechanism that is resurfacing the blocker. Escalate only if
the repeated blocker depends on external state the agent cannot modify.

## RCA To Patch To Verify Discipline

For every blocker, produce a short internal RCA before patching:

- Symptom: exact pause/log message.
- Evidence: files, functions, artifact ids, attempt ids, runtime ids, job ids,
  and line references when applicable.
- Root cause: what actually failed.
- Non-causes: plausible explanations ruled out.
- Fix: minimal durable fix.
- Verification: tests and operational checks.

If the code surface is broad, fan work out to subagents — send one message with
multiple `Agent` calls so they run in parallel:

- **Evidence RCA** (`general-purpose`): read the relevant persisted evidence,
  decision journal, and bridge log range; return the failure shape, the ids, and
  the smoking-gun lines. Keeps multi-MB reads out of your context.
- **Code-path trace** (`Explore`): locate the implementation/runtime/sandbox
  functions on the failing path and report `file:line` references. Use this for
  "where is X / what calls Y" lookups.
- **Fix design** (`Plan`): for a cross-subsystem blocker, design the minimal
  durable fix before you write code.
- **Regression coverage** (`general-purpose`): identify the test that should
  have caught this and draft the case for the exact failure shape.

Then review your own diff before committing: run the `/code-review` skill to
catch over-broad compatibility, hidden retry masking, or silently waived product
failures, and `/verify` to confirm the fix changes real runtime behavior, not
just tests.

Revise until the fix is evidence-backed.

## Restart Discipline

When code changes, restart deliberately:

1. Stop the old dashboard/bridge process.
2. Confirm `git rev-parse HEAD` is the pushed `main` commit that contains the
   fix.
3. Start the dashboard/bridge command from this prompt.
4. Wait for `/api/bridge/status` to show the bridge is running.
5. Check logs for startup/recovery messages and confirm the expected runtime is
   `claude`.
6. Trigger operator-free resume with `/api/bridge/resume`.

Scope the restart to what you changed: a fix in bridge/runtime/orchestrator code
is picked up by relaunching the bridge subprocess (`POST /api/bridge/restart`,
or the stop/start above, both spawn a fresh process), but a change to
`dashboard.py` itself requires relaunching the dashboard process you started in
the background.

Do not require a Slack message after restart.

## Test Selection

Always run `git diff --check`. Run focused tests for the touched subsystem:

- sandbox or workspace grants:
  `tests/workflows/develop/execution/test_sandbox.py`
- Claude/runtime routing:
  `tests/runtimes/test_claude.py`,
  `tests/runtimes/test_claude_pool.py`,
  `tests/workflows/develop/execution/test_runtime_client.py`
- dispatcher, replay, retry, or durable evidence:
  `tests/workflows/develop/execution/test_dispatcher.py`,
  `tests/test_execution_control_store.py`
- implementation resume, workflow blockers, contract compilation, or
  WorkspaceAuthority:
  `tests/workflows/develop/execution/test_implementation_workspace_authority_adapter.py`,
  `tests/workflows/test_dag_expanded_verify.py`
- dashboard or operator-free resume:
  `tests/test_dashboard_bugflow.py`,
  `tests/interfaces/slack/test_orchestrator.py`

Broaden test coverage when a fix crosses subsystem boundaries.

## Important Constraints

- Do not manually delete pause/failure artifacts.
- Do not edit historical artifact rows in place.
- Do not manually patch product repos to fake workflow progress.
- Do not reset durable retries to hide product failures.
- Do not promote empty patches.
- Do not broaden sandbox writes unless final patch-capture validation remains
  strict.
- Preserve strict execution-control behavior for post-adoption work.
- Treat stale/pre-fix infrastructure failures as retryable only when persisted
  evidence proves that shape.
- Keep product no-op/outside-contract failures terminal unless a real code fix
  changes the situation.
- Do not ask for operator input until the autonomy checks above have ruled out a
  workflow-code fix.

## Git Hygiene

Before patching:

```bash
git status --short
```

After patching and tests:

```bash
git diff --check
git add -A
git commit -m "<clear fix message>"
git push origin main
git status --short
```

The handoff target should always see a clean working tree unless you explicitly
report why not.

## Expected Behavior

The workflow for `8ac124d6` is adopted into strict execution-control resume. It
should skip sealed groups `0..77` and resume from post-adoption work. If it
pauses, investigate the new blocker; do not assume old root causes still apply.

After fixing a blocker, the correct operational action is:

```bash
curl -X POST http://127.0.0.1:51234/api/bridge/resume
```

Do not wait for a Slack message.
