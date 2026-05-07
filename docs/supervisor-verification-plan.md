# Supervisor Verification Plan

This plan verifies that the supervisor would catch the issues that required manual intervention across G30, G37, and G38, while avoiding false intervention on legitimate product repair loops.

## Strategy

Start with replay before live action.

1. Extract bounded fixture records from real artifacts, events, logs, and git probes.
2. Run the deterministic evidence kernel in dry-run mode against those fixtures.
3. Feed the resulting evidence packets to the supervisor agent and assert both the chosen action and the Slack answer quality.
4. Only then enable live read-only monitoring, then quiet Slack overlay, then guarded actions.

The replay harness should not mutate workflow artifacts, product repos, or bridge state. It should use fake stores and checked-in JSON fixtures derived from cited local evidence.

## Fixture Suite

| Fixture | Source evidence | Expected classification | Expected action |
|---|---|---|---|
| G30 stale derived state | `artifact:dag-repair-preflight:g30:retry-initial id=1044830`, `id=1049990`, `id=1052604`; `artifact:dag-verify:g30:initial id=1084035` | `deterministic_unblock` | Explain stale state across task result, product workspace/index, DAG/task spec, and projection layers; recommend closure/reconcile, not repeated product RCA. |
| G37 checkpoint contradiction | `artifact:dag-verify:g37:initial id=1273016`, `artifact:dag-group:37 id=1273018`, `event:23309`, `event:23310` | `pipeline_bug_suspected` | Alert that raw failed verifier/preflight cannot checkpoint; recommend pipeline patch and stop/escalate. |
| G38 stale then product | `artifact:dag-task-reconcile:g38:retry-initial id=1351096`, `artifact:dag-task-spec-reconcile:g38:retry-initial id=1351097`, `artifact:dag-repair-preflight:g38:retry-initial id=1351098`, `artifact:dag-verify:g38:initial id=1351629` | `normal_product_repair` after stale-state clean | State that stale metadata is contained and remaining issues are product verifier concerns. |
| G38 commit direct route | `artifact:dag-commit-failure:g38:retry-0 id=1353600`, `event:24286`, `event:24288` | `deterministic_unblock` | Recommend focused commit-hygiene repair and flag broad expanded verify as avoidable for commit-only failure. |
| G38 active retry progress | `event:24289` through `event:24318`; `artifact:dag-verify:g38:retry-1 id=1355772` | `healthy_progress` then `normal_product_repair` | Summarize progress from expanded lenses to smoke verify and product-level failure without calling it wedged. |
| Embedded `.git` hygiene | Commit guard code and remediation notes (`src/iriai_build_v2/workflows/develop/phases/implementation.py:4099-4248`) | `operator_required` | Report exact path and cleanup requirement; do not use product implementer or host auto-delete. |
| Permission blocker | G38 backend permission failure (`event:23395`) | `operator_required` | Pause before dispatch and explain target writeability. |
| Agent stall | Feature-wide `agent_stalled` count and runner watchdog events (`docs/dag-pipeline-retrospective.md:53`, `src/iriai_build_v2/workflows/_runner.py:491-535`) | `watch_only` or `safe_restart_candidate` | Recommend restart only if no live progress and safe-boundary rules pass. |
| Slack noise | `SlackStreamer` and silent observer code (`src/iriai_build_v2/interfaces/slack/streamer.py:95-180`, `src/iriai_build_v2/interfaces/slack/orchestrator.py:160-176`) | `digest` | Suppress low-level messages and emit one supervisor-written summary. |
| Product verifier negative | G38 materialization, accessibility, backend compile/regression (`artifact:dag-verify:g38:retry-1 id=1326086`, `artifact:dag-verify:g38:initial id=1351629`) | `normal_product_repair` | Do not pipeline-patch; explain that existing verify/repair loop should handle it. |

## Dry-Run Assertions

### Classification Assertions

- The same fixture always maps to the same classification and action level.
- Classifier output includes artifact ids, event ids, source references, and false-positive checks.
- Product semantic failures never become `pipeline_bug_suspected` unless there is a raw gate contradiction or workflow state inconsistency.
- Commit-only failures become direct repair candidates; mixed commit plus semantic failures remain in normal repair.
- Slack reconnect/noise alone never implies workflow failure.

### Agent-Driven Slack Assertions

For each evidence packet, run the supervisor agent in a deterministic test harness with mocked retrieval. Assert:

- The response cites at least one artifact/event/source reference.
- The response separates fact from inference.
- The response names current action and next expected transition.
- The response is concise enough for Slack.
- The response does not expose raw prompt dumps, full tool logs, or noisy agent completion chatter.

Example query fixture:

```text
User: how is it looking?
Expected answer shape:
- Current group/retry and phase.
- Active or latest agents.
- Latest material artifact ids.
- Health classification.
- Whether intervention is recommended.
- One-sentence risk assessment.
```

Conversation fixtures should use natural paraphrases rather than exact command strings. For example, "How's it looking?", "are we wedged?", "did it fix the stale metadata thing?", and "what happened since I last checked?" should route to the same evidence-backed supervisor answer family when appropriate.

### Action Assertions

- `observe` writes no Slack message unless the user asked.
- `digest` writes one message for many low-level events.
- `recommend` cannot mutate state.
- `act guarded` writes a `supervisor-action:*` artifact before and after the action.
- Any maintainer-agent patch must include evidence bundle, intended files, test plan, test results, and restart/import guidance.
- Automatic restart is blocked if an active agent invocation is still alive unless bridge process state is dead/wedged beyond policy.

## Test Matrix

### Unit Tests

- Evidence kernel parses latest `dag-verify:*`, `dag-repair-preflight:*`, `dag-task-reconcile:*`, `dag-task-spec-reconcile:*`, and `dag-commit-failure:*` records from fake artifact store.
- Event cursor logic handles duplicate polling and out-of-order but increasing ids.
- Classifier detects G30 stale derived state from typed path-problem fixture.
- Classifier detects G37 checkpoint contradiction from raw failed verifier plus later `dag-group:*`.
- Classifier detects G38 commit-only failure and extracts target file/line from hook output.
- Classifier keeps G38 product verifier concerns in `normal_product_repair`.
- Bridge API probe handles running, stopped, crashed, and restartable states from fake dashboard responses.
- Git probe distinguishes direct feature repos from nested `.git` and gitlinks.

### Workflow Replay Tests

- Replay G30 artifact sequence and assert the supervisor would have escalated stale-derived-state closure before the next repeated repair loop.
- Replay G37 final artifacts and assert no "healthy/checkpointed" message can be emitted.
- Replay G38 from `event:24286` to `event:24318` and assert the supervisor first flags commit direct-route, then later reports healthy progress through retry-1 and product-level verify failure.
- Replay Slack noise burst from many agent starts/dones and assert one digest plus no low-level "Done" messages.

### Slack UX And Routing Tests

- Separate supervisor bot mode: natural messages mentioning or DMing the supervisor bot route to supervisor chat, while bridge workflow cards and launches remain handled by the bridge bot.
- Same-bot fallback mode: natural operator questions route to the supervisor before active-agent injection/user-note/resume paths.
- Natural status questions such as "how's it looking?", "are we healthy?", and "what is running?" return current group, retry, phase, bridge health, active agents, latest key artifacts, and recommendation.
- Natural stuck-diagnosis questions such as "are we wedged?", "why is it still retrying?", and "is this the stale metadata issue again?" explain elapsed time, repeated signatures, or lack of active progress.
- Natural change-summary questions such as "what changed since I last checked?" summarize recent artifacts, commits, repair results, and supervisor actions since a cursor.
- Natural restart questions or requests such as "should we restart?" and "restart if safe" report safe-boundary analysis and refuse restart when active agent work is healthy.
- Natural patch-history questions such as "what did you fix?" report maintainer patch files, tests, and restart status.
- Explicit workflow instructions such as "tell the implementer to focus on the husky error" route to workflow injection or queued notes with supervisor acknowledgement.
- Pending interaction responses still resolve the interaction instead of going to supervisor chat.
- Recoverable feature resume is not triggered by an unrelated natural question in supervisor-owned mode.

### Guarded Intervention Tests

- Pipeline patch proposal requires at least one `pipeline_bug_suspected` or `deterministic_unblock` evidence packet with citations.
- Maintainer patch cannot touch product repos unless explicitly in an operator-approved remediation mode.
- Repeated direct-route signature after focused repair becomes `stop/escalate`.
- Restart action requires bridge dead/wedged or no active agent invocations plus a safe boundary.
- Every action writes before/after `supervisor-action:*` artifacts.

## Live Rollout Gates

Gate 1: Replay confidence.

- All G30/G37/G38 positive fixtures pass.
- Negative product verifier fixtures do not trigger pipeline intervention.
- Slack response snapshots are readable and cited.

Gate 2: Read-only live monitor.

- Supervisor observes one active run without mutating state.
- Observations and decisions persist as artifacts.
- Manual comparison confirms its status answers match direct artifact/event queries.

Gate 3: Slack overlay.

- Bridge remains unchanged or only adds quiet-mode flag.
- Supervisor answers explicit user questions in Slack.
- Unsolicited updates are limited to material state transitions.

Gate 4: Guarded actions.

- Enable restart recommendations first, then safe-boundary restart.
- Enable maintainer-agent dry-run.
- Enable pipeline patch mode only after a fixture-backed test exists for the detected class.

## Success Metrics

- Manual intervention count per group decreases.
- Time from deterministic blocker artifact to correct route decreases.
- Slack messages per group decrease while status answer quality improves.
- False pipeline-patch rate remains zero on product semantic verifier failures.
- Every supervisor action is auditable by artifact id, event id, and source/test citation.
