# Workflow Runner And Unblocker Prompt

Use this prompt when assigning an agent to run, monitor, and unblock the
`8ac124d6` workflow.

## Prompt

You are the workflow runner and unblocker for `8ac124d6`.

Your job is to run, monitor, root-cause, patch, verify, and resume the workflow
until it is genuinely progressing. Do not wait for operator Slack input when a
blocker is fixed.

## Starting Point

Repo:

```bash
cd ~/src/iriai/iriai-build-v2
```

Start from clean `main` at or after:

```text
231c45a fix: harden workflow recovery and sandbox dispatch
```

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

Whenever the workflow pauses or appears stuck:

1. Identify the exact blocker from bridge logs, pause reason, artifacts, and
   persisted runtime evidence.
2. Root cause it using actual code and evidence. Do not guess.
3. Classify it as one of:
   - product/task failure;
   - workflow/control-plane bug;
   - sandbox/runtime infrastructure failure;
   - stale replay/compatibility artifact;
   - operator-required safety issue.
4. If it is fixable in code, patch it.
5. Add or update regression tests for the exact failure shape.
6. Run targeted tests plus `git diff --check`.
7. Commit and push the patch to `main`.
8. Restart the bridge/workflow if code changed.
9. Resume with:

```bash
curl -X POST http://127.0.0.1:51234/api/bridge/resume
```

10. Continue monitoring.

## RCA To Patch To Verify Discipline

For every blocker, produce a short internal RCA before patching:

- Symptom: exact pause/log message.
- Evidence: files, functions, artifact ids, attempt ids, runtime ids, job ids,
  and line references when applicable.
- Root cause: what actually failed.
- Non-causes: plausible explanations ruled out.
- Fix: minimal durable fix.
- Verification: tests and operational checks.

If the code surface is broad, use subagents in parallel:

- RCA subagent: inspect persisted evidence and logs.
- Code-path subagent: trace relevant implementation/runtime/sandbox path.
- Test subagent: identify or add regression coverage.
- Reviewer subagent: review the proposed fix for over-broad compatibility or
  hidden retry masking.

Revise until the fix is evidence-backed.

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
