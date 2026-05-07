# Supervisor Failure Taxonomy

Feature basis: `8ac124d6`. This taxonomy defines what the supervisor should detect, how it should respond, and what false positives must be avoided.

## Classification Principles

- Classify by authority and write locus. Product repo truth, source DAG truth, latest valid `dag-task:*` truth, generated projections, repair evidence, and Slack UI state have different authority (`docs/dag-pipeline-retrospective.md:140-150`, `docs/dag-pipeline-improvement-opportunities.md:250-259`).
- Do not treat repeated failure as a pipeline bug by itself. Repetition can mean a real product defect; G38 continued to surface materialization, pytest, accessibility, and backend compile/regression issues after stale-state repair was working (`artifact:dag-verify:g38:retry-1 id=1326086`, `artifact:dag-verify:g38:initial id=1351629`).
- Act faster on deterministic workflow contradictions. G37 checkpointed after a failed raw verifier/preflight, which is a process correctness leak (`artifact:dag-verify:g37:initial id=1273016`, `artifact:dag-group:37 id=1273018`, `event:23309`, `event:23310`).
- Slack delivery state is not workflow truth. The runner and artifact store are primary; Slack streamer errors should be observable symptoms only (`src/iriai_build_v2/workflows/_runner.py:801-813`, `src/iriai_build_v2/interfaces/slack/streamer.py:196-220`).

## Failure Classes

| Class | Signature | Evidence | Supervisor action | False-positive guard |
|---|---|---|---|---|
| Stale derived DAG/task state | `dag-repair-preflight:*` path problems mention retired paths in task results, task specs, generated snapshots, or changed-files while product files are canonical. | G30 stale chat paths (`artifact:dag-repair-preflight:g30:retry-initial id=1044830`, `artifact:dag-repair-preflight:g30:retry-initial id=1052604`); G38 task/spec reconcile (`artifact:dag-task-reconcile:g38:retry-initial id=1351096`, `artifact:dag-task-spec-reconcile:g38:retry-initial id=1351097`). | Classify `deterministic_unblock`; verify source authority, projection authority, and latest task row authority; propose host reconciliation or maintainer patch if recurrence escapes existing code. | Do not block on canonical replacement paths or advisory historical docs that do not feed current task specs. |
| Product workspace drift | Manifest-forbidden path exists on disk, tracked, or staged as add; generated metadata alone is not the source. | G30 retired chat prefix on disk/index (`artifact:dag-repair-preflight:g30:retry-initial id=1049990`). | Classify `operator_required` or `product_cleanup_required`; require product cleanup preserving coverage before artifact repair. | Staged deletion plus absent disk file is cleanup evidence, not live drift. |
| Source DAG stale | Authoritative DAG fragment/path fields contain retired paths. | G30 stale DAG/task specs (`artifact:dag-repair-preflight:g30:retry-initial id=1052604`). | Classify `deterministic_unblock`; route typed artifact closure repair or pipeline-maintainer patch if closure is incomplete. | Do not make every historical mention blocking; only task-bearing/source-generation artifacts block. |
| Raw gate/checkpoint contradiction | A raw preflight/verifier artifact is failed, but `dag-group:*` or checkpoint event is written. | G37 (`artifact:dag-verify:g37:initial id=1273016`, `artifact:dag-group:37 id=1273018`). | Classify `pipeline_bug_suspected`; alert immediately; propose pipeline patch; do not recommend continuing blindly. | Require exact group and temporal ordering evidence before declaring contradiction. |
| Commit/husky failure | `dag_commit_failed`, `WorkflowCommitError`, or `dag-commit-failure:*` with hook output. | G38 commit failures (`artifact:dag-commit-failure:g38:retry-0 id=1316714`, `artifact:dag-commit-failure:g38:retry-0 id=1353600`, `event:24113`, `event:24286`). | If commit-only, classify `deterministic_unblock` and expect focused repair; if embedded repo/gitlink, classify `operator_required`; if mixed with semantic verifier concerns, stay in normal repair. | Do not bypass hooks with `--no-verify`; do not launch broad expanded verify for commit-only signatures once direct route exists. |
| Embedded repo/gitlink hygiene | Direct repo commit discovers nested `.git` or gitlink. | Commit guard refuses embedded `.git` and gitlinks (`src/iriai_build_v2/workflows/develop/phases/implementation.py:4099-4248`); G37/G38 remediation notes in retrospective (`docs/dag-pipeline-retrospective.md:123-138`). | Classify `operator_required`; report exact path and cleanup options. | Valid direct repos under `feature_root/repos/<name>` must not be blocked. |
| Permission/writeability blocker | Canonical target parent is not writable, or agent creates `_pending_*`/`.PROPOSED` fallback. | G38 backend permission failures (`event:23395`, `docs/dag-pipeline-retrospective.md:127-138`). | Classify `operator_required`; pause before dispatch when detected. | If product agent can write canonical target and failure is compile/test, leave to product repair. |
| External infra/resource blocker | pgserver exhaustion, DB/socket outage, missing credentials, bridge process dead. | G30 pgserver shared-memory exhaustion (`artifact:dag-repair-triage:g30:retry-0 id=1078324`); dashboard bridge status API (`dashboard.py:3310-3337`). | Classify `operator_required` or `safe_restart_candidate`; notify user; restart only if safe-boundary policy passes. | Do not restart during active agent invocation unless process is dead or wedged beyond policy. |
| Agent stall or usage exhaustion | `agent_stalled`, long active invocation with no progress, provider usage failure, missing `agent_done`. | Feature-wide event stream had one `agent_stalled`; runner logs invocations and retries (`docs/dag-pipeline-retrospective.md:53`, `src/iriai_build_v2/workflows/_runner.py:491-535`). | Classify `watch_only`, `safe_restart_candidate`, or `operator_required` based on elapsed time, retry count, and active process state. | Long expanded verify with activity is not a stall. |
| Bridge/Slack noise | Many low-value per-agent Slack updates, "Done", silent-run notices, reconnect stack traces. | Slack streamer and silent invocation observer (`src/iriai_build_v2/interfaces/slack/streamer.py:95-180`, `src/iriai_build_v2/interfaces/slack/streamer.py:238-270`, `src/iriai_build_v2/interfaces/slack/orchestrator.py:160-176`). | Suppress in quiet mode; supervisor agent posts digest only on meaningful state changes or user query. | Required human approval/interactions must remain visible until supervisor can proxy them. |
| Normal product verifier failure | Verifier reports acceptance, runtime, security, import/type/test, or regression issue with current product files. | G38 materialization/symlink, pytest, accessibility, backend compile/regression (`artifact:dag-verify:g38:retry-1 id=1326086`, `artifact:dag-verify:g38:initial id=1351629`). | Classify `normal_product_repair`; monitor but do not pipeline-patch. | If product failure co-occurs with deterministic gate failure, classify as mixed and preserve expanded verify unless a deterministic direct route is proven safe. |

## Action Levels

| Level | Meaning | Examples |
|---|---|---|
| Observe | Record evidence, no Slack unless asked. | Active verifier within expected runtime; new artifact with approved preflight. |
| Digest | Send concise Slack update. | Group starts retry, verify completes, material blocker changes class. |
| Recommend | Tell user what should happen and why. | Safe bridge restart, operator repo cleanup, patch-at-boundary. |
| Act guarded | Execute allowed action and report. | Safe-boundary restart, maintainer-agent dry-run, later approved pipeline patch. |
| Stop/escalate | Prevent repeated damage or ask operator. | Same commit signature repeats after focused repair; checkpoint contradiction; embedded `.git` in active repo. |

## Evidence Packet Shape

Each classifier output should include:

- `feature_id`, `group_idx`, `retry`, `phase`, and wall-clock time.
- `classification` and `confidence`.
- `facts`: raw artifact ids, event ids, git paths, process state, and query cursor.
- `inference`: why the facts imply the classification.
- `recommended_action`: one of observe, digest, recommend, act guarded, or stop/escalate.
- `false_positive_checks`: explicit checks that prevented over-action.
- `citations`: artifact ids, event ids, source files, and query labels.

## Current Fixture Seeds

Use these as first replay fixtures:

- `g30-stale-derived-state`: `artifact:dag-repair-preflight:g30:retry-initial id=1044830`, `id=1049990`, `id=1052604`, `artifact:dag-verify:g30:initial id=1084035`.
- `g37-checkpoint-contradiction`: `artifact:dag-verify:g37:initial id=1273016`, `artifact:dag-group:37 id=1273018`, `event:23309`, `event:23310`.
- `g38-stale-then-product`: `artifact:dag-task-reconcile:g38:retry-initial id=1351096`, `artifact:dag-task-spec-reconcile:g38:retry-initial id=1351097`, `artifact:dag-repair-preflight:g38:retry-initial id=1351098`, `artifact:dag-verify:g38:initial id=1351629`.
- `g38-commit-direct-route`: `artifact:dag-commit-failure:g38:retry-0 id=1353600`, `event:24286`, followed by expanded verify launch `event:24288` as historical evidence of wasted broad routing before direct route hardening.
- `slack-noise`: screenshot-backed class plus code citations for `SlackStreamer` and `_SlackInvocationObserver`.
