# Supervisor Agent Plan

Feature basis: `8ac124d6`, with strongest evidence from G30, G37, and G38.  
Companion docs: `docs/dag-pipeline-retrospective.md`, `docs/dag-pipeline-improvement-opportunities.md`, `docs/supervisor-failure-taxonomy.md`, `docs/supervisor-verification-plan.md`.

## Summary

The supervisor should be a separate long-running process that watches the workflow, classifies pipeline health, owns high-signal Slack communication, and can invoke guarded pipeline-maintainer work when the workflow itself is leaking state.

This is not just a template notifier. The implementation should be an agent-led,
classifier-guarded system:

- A deterministic seed pass collects artifacts, events, git/worktree facts, bridge state, and active agent state.
- A supervisor agent can request bounded read-only evidence rounds, then writes the current assessment, Slack updates, and operator answers with citations.
- Deterministic policies guard mutations such as restarts, workflow instructions, or future pipeline patches.

The need is directly supported by the retrospective: late groups were dominated by retry-loop amplification, stale derived state, checkpoint/commit/preflight leakage, and workspace blockers rather than only product bugs (`docs/dag-pipeline-retrospective.md:11-27`). G30 showed sequential stale artifact discovery (`artifact:dag-repair-preflight:g30:retry-initial id=1044830`, `artifact:dag-repair-preflight:g30:retry-initial id=1052604`), G37 showed a raw preflight/checkpoint leak (`artifact:dag-verify:g37:initial id=1273016`, `artifact:dag-group:37 id=1273018`), and G38 showed commit, permission, and product-verification failures mixed together (`artifact:dag-commit-failure:g38:retry-0 id=1353600`, `event:24286`).

## Goals

- Detect the same workflow/pipeline failure classes that have required manual intervention.
- Distinguish product semantic failures from pipeline/process failures before making changes.
- Reduce Slack noise by moving operator-facing narration to an agent-driven supervisor.
- Let the user chat naturally with the supervisor in Slack, including status questions, diagnosis, recent-change questions, restart/patch requests, and follow-up conversation.
- Provide auditable action history through `supervisor-observation:*`, `supervisor-decision:*`, and `supervisor-action:*` artifacts.

## Architecture

### Supervisor Process

Run a new process alongside the dashboard and Slack bridge. It should not be a phase inside the workflow, because it must remain alive when the bridge crashes, reconnects, or is intentionally stopped.

Core inputs:

- Postgres artifacts and events. Artifacts are append-only and latest-row reads are already supported by `PostgresArtifactStore.get()` and `get_record()` (`src/iriai_build_v2/storage/artifacts.py:25-60`).
- Dashboard bridge APIs for status, restart, and logs (`dashboard.py:3310-3337`).
- Git/worktree probes for direct feature repos, forbidden paths, embedded `.git`, gitlinks, staged deletes, pending/proposed files, and dirty state.
- Runner events such as `agent_start`, `agent_done`, `agent_invocation_start`, `phase_execute_error`, `dag_commit_failed`, `dag_verify_start`, and `dag_verify_finish`. The runner already logs agent and phase events (`src/iriai_build_v2/workflows/_runner.py:341-354`, `src/iriai_build_v2/workflows/_runner.py:491-526`, `src/iriai_build_v2/workflows/_runner.py:801-813`, `src/iriai_build_v2/workflows/_runner.py:907-920`).
- Slack delivery errors and reconnects from bridge logs. Slack UI failures should be observable but should not define workflow truth (`docs/dag-pipeline-improvement-opportunities.md:287-290`).

Core outputs:

- Evidence packets: bounded JSON summaries with citations, raw facts, inferred classification, and candidate actions.
- Supervisor artifacts:
  - `supervisor-observation:{feature}:e{event_cursor}:a{artifact_cursor}:b{bridge_log_cursor}:{timestamp}` for raw health observations.
  - `supervisor-decision:{feature}:e{event_cursor}:a{artifact_cursor}:b{bridge_log_cursor}:{timestamp}` for classification and why it did or did not act.
  - `supervisor-agent-assessment:{feature}:e{event_cursor}:a{artifact_cursor}:b{bridge_log_cursor}:{timestamp}` for agent-authored investigation results.
  - `supervisor-action:{feature}:{cursor}` for restarts, maintainer-agent launches, accepted patches, and operator notices.
- Slack messages authored by the supervisor agent from evidence packets.

### Evidence Kernel

The deterministic kernel should not be the final status authority. It should gather facts and produce compact seed records:

- Current feature state: phase, group, retry, checkpoint status, latest verifier/preflight artifacts.
- Event deltas since last cursor: new starts, finishes, stalls, phase errors, commit failures, bridge restarts.
- Artifact deltas since last cursor: latest `dag-verify:*`, `dag-repair-preflight:*`, `dag-task-reconcile:*`, `dag-task-spec-reconcile:*`, `dag-commit-failure:*`, `dag-group:*`.
- Repo state: direct repo status, forbidden manifest paths, embedded `.git`, gitlinks, staged/unstaged deletes, `_pending_*`, `.PROPOSED`, writeability.
- Bridge state: dashboard status, process pid, exit code, recent log errors, Slack reconnect state.

The kernel classifies observations into a small set of hints:

- `healthy_progress`: new task, verify, repair, commit, or checkpoint activity with no deterministic blocker.
- `normal_product_repair`: verifier concerns are product semantic failures that belong in the existing workflow.
- `deterministic_unblock`: a known direct route exists, such as commit hygiene or generated projection drift.
- `pipeline_bug_suspected`: workflow behavior contradicts raw artifacts, such as checkpoint after failed preflight.
- `operator_required`: external or worktree condition cannot be safely fixed by product agents.
- `watch_only`: no material state change or still within expected runtime.

### Supervisor Agent Loop

The agent loop receives a seed packet and can request 1-3 bounded read-only evidence rounds. It decides what to say or recommend, and must cite source facts and separate fact from inference.

Required behavior:

- Answer direct questions from Slack with current group/retry, active work, latest material artifacts/events, current risk, and next action.
- Query read-only artifacts/events/dashboard/worktree evidence as needed before answering.
- Send unsolicited messages only when state meaningfully changes, a blocker appears, an action completes, or a timeout threshold is crossed.
- Avoid broad "everything is fine" updates unless asked.
- Prefer "I am watching X because Y" over raw tool logs.
- If triggering a maintainer patch, include a cited evidence bundle, expected patch scope, test plan, and whether a restart is needed.

The agent loop should not invent actions beyond policy. For example, a product semantic verifier failure should not become a pipeline patch just because it repeats once; G38 had real product failures after stale-state cleanup was working (`artifact:dag-verify:g38:retry-1 id=1326086`, `artifact:dag-verify:g38:initial id=1351629`). Guarded actions require both an agent proposal and deterministic policy approval.

## Slack Ownership

### Current Noise Sources

Current bridge Slack output is too low-level for operator supervision:

- `_SlackInvocationObserver` posts "still running" messages and updates them to "`actor` finished after a silent run" (`src/iriai_build_v2/interfaces/slack/orchestrator.py:160-176`).
- `SlackStreamer` streams tool/thinking/status lines and updates progress messages to "Done" on completion (`src/iriai_build_v2/interfaces/slack/streamer.py:95-180`, `src/iriai_build_v2/interfaces/slack/streamer.py:238-270`).
- The orchestrator posts direct workflow complete/failure/resume messages (`src/iriai_build_v2/interfaces/slack/orchestrator.py:607-641`, `src/iriai_build_v2/interfaces/slack/orchestrator.py:985-1015`).
- Runtime/runner creation currently couples execution to Slack streaming by passing `SlackStreamer.on_message` into primary and secondary agent runtimes (`src/iriai_build_v2/interfaces/slack/orchestrator.py:1172-1233`).

### Current Inbound Routing Constraint

The existing bridge is not a natural supervisor chat router. `handle_message()` filters planning-channel messages unless they match a workflow trigger, filters multiplayer workflow-channel messages unless the bot is mentioned, then forwards one text event to the orchestrator callback (`src/iriai_build_v2/interfaces/slack/handlers.py:20-61`). The orchestrator then chooses one of four workflow-oriented paths: resolve a pending interaction card, inject into an active agent runtime, resume a recoverable feature, or queue a user note (`src/iriai_build_v2/interfaces/slack/orchestrator.py:283-333`).

Supervisor-owned Slack needs a new arbitration point before the active-agent injection/resume/user-note paths. Otherwise natural questions like "how's it looking?" can be injected into a running implementer or treated as a workflow note instead of being answered by the supervisor.

Preferred implementation: create a separate supervisor Slack app/bot. The bridge bot can keep workflow launch, interaction cards, and low-level execution mechanics while the supervisor bot owns natural conversation, health digests, restart/patch recommendations, and operator Q&A. This avoids overloading the bridge bot's current message callback and makes bot identity clear in noisy channels.

Fallback implementation: use the same Slack app with a supervisor-owned mode and a new `SupervisorSlackRouter` in front of `_on_message()`. This is less operationally complex, but higher risk because every natural chat path must be carefully distinguished from interaction responses, active-agent injection, and resume triggers.

### Quiet Bridge Mode

Add a future `--bridge-slack-verbosity quiet` or `--bridge-supervisor-owned-slack` mode with these defaults:

- Suppress `SlackStreamer` progress messages, tool result snippets, "Done", and final non-interaction text.
- Suppress silent invocation "still running" and "finished after a silent run" notices.
- Keep required interactive cards, approvals, and explicit workflow failure messages until the supervisor can proxy them.
- Keep event and artifact logging unchanged.

This preserves workflow mechanics while moving operator narration to the supervisor.

### Supervisor Slack UX

Outbound messages should be agent-written from evidence, not hardcoded templates. Suggested message types:

- Health digest: "G38 retry-1 progressed from expanded verify to focused verify; latest failure is product-level workspace health, not stale metadata."
- Blocker alert: "Commit failed on `ChatSidepaneShell.test.tsx:149`; this is a deterministic hook failure and should use focused repair."
- Patch action: "I found a pipeline routing leak, patched direct commit-blocker routing, ran tests X/Y, and a bridge restart is needed at the next safe boundary."
- Restart recommendation: "Bridge process is dead and no agent invocation is active; restart is safe."
- Operator-required: "Embedded `.git` exists under a product path; product agents must not delete it automatically."

Inbound behavior should be natural conversation, not command matching. The supervisor agent should answer paraphrases and follow-ups such as:

- "How's it looking?", "are we stuck?", "is this healthy?", "what is running right now?"
- "Why did it restart?", "what failed?", "is this still the stale metadata issue?"
- "What changed since I last checked?", "what did the implementer fix?", "what did you patch?"
- "Should we restart?", "restart if safe", "don't restart yet", "let it keep running."
- "Tell the workflow/implementer X" or "send this note downstream."

Natural-language routing should classify each message as one of:

- `supervisor_question`: answer from artifacts/events/git/bridge evidence.
- `supervisor_action_request`: evaluate policy, then recommend or act with guardrails.
- `workflow_instruction`: forward or queue a note for the active workflow/agent, with a supervisor acknowledgement.
- `interaction_response`: resolve a pending Slack card or approval.
- `workflow_start`: preserve existing planning-channel workflow launch behavior.
- `ignore`: bot messages, unrelated chatter, or messages outside subscribed channels.

Routing precedence:

1. Required interactive card responses remain highest priority until the supervisor can proxy them.
2. Natural operator questions and action requests go to the supervisor agent, even while an implementer/verifier is active.
3. Explicit workflow instructions are forwarded to the active runtime or queued as user notes.
4. Resume of recoverable features should become an explicit supervisor decision, not "any message resumes," in supervisor-owned mode.

This means implementation should add a `SupervisorSlackRouter` or equivalent layer rather than expanding the current `_on_message()` branch table with command strings.

## Guarded Intervention

Default authority is patch-with-guardrails for pipeline/process defects.

Allowed automatically after dry-run confidence:

- Read-only health checks and evidence collection.
- Natural Slack answers and concise updates.
- Restart recommendation, and later automatic restart when no active agent/process work exists and the bridge is clearly wedged.
- Launching a maintainer-agent investigation in dry-run mode with no code changes.

Allowed with guardrails:

- Pipeline code patches when evidence shows workflow leakage, such as stale projection recurrence, raw gate contradiction, commit failure misrouting, bridge wedge, or scheduler/retry bug.
- Narrow product-repo operator cleanup only when explicitly classified as operator-approved remediation and outside normal workflow agents.

Required guardrails for pipeline patching:

- Evidence bundle with artifact ids, event ids, code references, and exact failure signature.
- Minimal patch scope in `iriai-build-v2`.
- Tests or replay fixture showing the known issue would now be caught.
- A visible Slack summary that states what changed, what was verified, and whether the running bridge needs a restart.
- No destructive product source action by default.

Never auto-patch for:

- Acceptance gaps, runtime composition defects, security concerns, import/type/test failures, or other verifier product findings that belong to normal repair.
- Ambiguous mixed failures without evidence of pipeline leakage.
- Historical/advisory stale text that does not feed current task specs or verifier context.

## Rollout

Phase 0: documentation and fixture extraction.

- Land this plan, the taxonomy, and the verification plan.
- Extract real fixture records from G30/G37/G38, including expected classifications and false-positive guards.

Phase 1: read-only supervisor.

- Poll events/artifacts/bridge APIs.
- Persist supervisor observations and decisions.
- Answer natural Slack questions from evidence.
- Emit low-frequency digests while the bridge still owns normal workflow Slack.

Phase 2: quiet Slack overlay.

- Add bridge quiet mode.
- Prefer introducing a separate supervisor Slack app/bot; if unavailable, use same-bot supervisor routing.
- Supervisor becomes the primary Slack narrator for natural status, diagnosis, recent-change, restart, and patch questions.
- Bridge keeps required interaction cards and hard workflow failure messages.

Phase 3: guarded action mode.

- Enable restart recommendations and safe-boundary restarts.
- Enable maintainer-agent dry-run investigations.
- Enable pipeline patches only when replay fixtures and tests prove the failure class.

Phase 4: supervisor-owned Slack.

- Supervisor owns outbound workflow status and operator Q&A.
- Bridge Slack messages are reduced to interactive prompts or delegated through supervisor.

## Acceptance Criteria

- A replay of G30 stale-state artifacts produces one stale-derived-state diagnosis, not repeated product repair.
- A replay of G37 catches the raw preflight/checkpoint contradiction.
- A replay of G38 commit failures routes to deterministic focused repair or operator action, while real product verifier failures stay in normal workflow repair.
- Slack noise replay turns many agent completion/status events into one concise supervisor digest.
- User query replay answers "how is it looking?" with facts, inference, current risk, and citations.
- Every supervisor action is auditable through DB artifacts and Slack summaries.
