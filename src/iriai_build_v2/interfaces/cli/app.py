"""CLI entry point for iriai-build-v2."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click
from dotenv import load_dotenv

from ...runtime_policy import (
    DEFAULT_RUNTIME_POLICY,
    PRIMARY_IMPL_SECONDARY_REVIEW_POLICY,
    SUPPORTED_RUNTIME_POLICIES,
    normalize_runtime_policy,
)

load_dotenv()

# (W-PR) Exit code for a workflow that QUIESCED (intentional pause on a typed
# blocker) instead of completing. Distinct from 0 (genuine completion) and 1
# (crash) so drivers/orchestrators can route it without text-scraping.
WORKFLOW_QUIESCED_EXIT_CODE = 3


def _print_workflow_outcome(runner: object, *, label: str) -> None:
    """Print the terminal banner for a finished `execute_workflow` /
    `resume_workflow` call.

    FALSE-COMPLETE guard (2026-06-11, feature 5b280bb4 develop12/13/14): the
    runner deliberately swallows ``WorkflowQuiesced`` (a quiesce is a clean,
    resumable park, not a crash) and records it on
    ``runner.last_workflow_quiesce`` — but this CLI used to print
    "Workflow [resume] complete!" unconditionally afterwards, so a
    workflow_blocked implementation phase was reported to the operator as a
    completed run three times in one day. A quiesced workflow now prints a
    loud NOT-COMPLETE banner with the phase + typed reason and exits with
    ``WORKFLOW_QUIESCED_EXIT_CODE``; the genuine-completion banner is
    byte-identical to the legacy one."""
    quiesce = getattr(runner, "last_workflow_quiesce", None)
    print(f"\n{'='*60}")
    if quiesce is None:
        print(f"  {label} complete!")
        print(f"{'='*60}\n")
        return
    metadata = getattr(quiesce, "metadata", None) or {}
    terminal_state = str(metadata.get("terminal_state", "") or "quiesced")
    reason = " ".join(str(getattr(quiesce, "reason", "") or "").split())
    print(f"  {label} QUIESCED — NOT COMPLETE")
    print(f"  Phase: {getattr(quiesce, 'phase_name', '') or '<unknown>'}")
    print(f"  Terminal state: {terminal_state}")
    print(f"  Reason: {reason[:1500] or '(none recorded)'}")
    print(f"  The workflow did NOT finish. Resolve the blocker and resume.")
    print(f"{'='*60}\n")
    raise SystemExit(WORKFLOW_QUIESCED_EXIT_CODE)


async def _run(
    workflow_name: str,
    name: str,
    workspace: str,
    auto: bool,
    *,
    agent_runtime: str = "claude",
    runtime_policy: str = DEFAULT_RUNTIME_POLICY,
    single_agent_runtime: bool = False,
    driver: str | None = None,
    repos: list[str] | None = None,
    project: str = "",
    bug_report: str = "",
) -> None:
    from iriai_compose.runtimes import AutoApproveRuntime

    from ...execution_control.startup import (
        EnvFlagState,
        assert_control_plane_ready_for_workflow_launch,
        read_control_plane_env_flag,
    )
    from ...stream import print_stream
    from .interaction import ThreadAwareTerminalInteractionRuntime
    from .._bootstrap import (
        bootstrap,
        build_runner,
        build_state,
        create_feature,
        maybe_assert_adopted_or_legacy_for_resume,
        select_workflow,
        teardown,
    )

    workspace_path = Path(workspace).resolve()
    env = await bootstrap(workspace_path)

    try:
        # Slice 12c — IRIAI_EXEC_CONTROL_PLANE_ENABLED env-flag + startup
        # guard. The env flag is the SINGLE product-authoritative switch for
        # the typed execution control plane per doc 12 § "Atomic Landing
        # Contract". When the flag is unset/disabled the CLI continues with
        # the legacy executor (preserves backward compatibility during the
        # rollout). When enabled, the Slice-10f assert_control_plane_ready
        # fires BEFORE any workflow runs; missing dependencies / mismatched
        # deploy artifact / missing migrations / forbidden partial controls
        # raise ControlPlaneStartupError (NOT silent fallback to legacy).
        # Malformed env values raise ControlPlaneEnvFlagError from
        # read_control_plane_env_flag (fail closed; never silently default).
        flag_state = read_control_plane_env_flag()
        if flag_state is EnvFlagState.ENABLED:
            await assert_control_plane_ready_for_workflow_launch(
                pool=env.pool,
                require_enabled=True,
            )

        # Runtimes
        if driver == "agent":
            from .agent_driven_interaction import AgentDrivenInteractionRuntime

            interaction_runtime = AgentDrivenInteractionRuntime(
                workspace_root=workspace_path
            )
        elif auto or driver == "auto":
            interaction_runtime = AutoApproveRuntime()
        else:
            interaction_runtime = ThreadAwareTerminalInteractionRuntime()

        runner = build_runner(
            env,
            interaction_runtimes={"terminal": interaction_runtime, "auto": interaction_runtime},
            on_message=print_stream,
            agent_runtime_name=agent_runtime,
            runtime_policy=normalize_runtime_policy(runtime_policy),
            single_agent_runtime=single_agent_runtime,
        )

        # Feature
        feature = await create_feature(env.feature_store, name, workflow_name)

        # Slice 12e -- PR 11.13 final atomic landing: consult the Slice-12d
        # resume guard at the workflow seam. The CLI today always CREATES a
        # fresh feature in `_run` (no CLI resume path exists -- the only
        # production resume seam is Slack's `_resume_workflow` at
        # `interfaces/slack/orchestrator.py:925`), so `is_resume=False` is
        # the existing distinction. Under `IRIAI_EXEC_CONTROL_PLANE_ENABLED=
        # ENABLED` a fresh feature is implicitly under the new control plane
        # -- the adoption marker is only required at the in-flight RESUME
        # boundary per doc 12 § "In-Flight Cutover Policy" lines 73-78.
        # The helper centralizes the env-flag short-circuit + the
        # fresh-vs-resume distinction so the CLI and Slack seams stay in
        # lockstep.
        await maybe_assert_adopted_or_legacy_for_resume(
            feature=feature,
            artifacts=env.artifacts,
            is_resume=False,
        )

        # Workflow + state
        workflow = select_workflow(workflow_name)
        state = build_state(workflow_name, project=project, bug_report=bug_report)

        if workflow_name == "bugfix" and bug_report:
            await env.artifacts.put("bug_report", bug_report, feature=feature)

        if repos:
            from ...models.outputs import ProjectContext, RepoSpec

            repo_specs = []
            for r in repos:
                # Detect if it's a GitHub ref (contains /) or a local path
                if "/" in r and not Path(r).exists():
                    repo_specs.append(RepoSpec(
                        name=r.split("/")[-1],
                        github_url=r if r.startswith("http") else f"https://github.com/{r}",
                    ))
                else:
                    repo_specs.append(RepoSpec(
                        name=Path(r).name,
                        local_path=str(Path(r).resolve()),
                    ))

            project_ctx = ProjectContext(
                feature_name=name,
                repos=repo_specs,
                workspace_path=str(workspace_path),
            )
            await env.artifacts.put(
                "project",
                project_ctx.model_dump_json(indent=2),
                feature=feature,
            )
        else:
            await env.artifacts.put(
                "project",
                f"Project workspace: {workspace_path}\n\nFeature: {name}",
                feature=feature,
            )

        # Execute
        print(f"\n{'='*60}")
        print(f"  iriai-build-v2 — {workflow_name}")
        print(f"  Feature: {name} ({feature.id})")
        print(f"  Workspace: {workspace_path}")
        print(f"{'='*60}\n")

        await runner.execute_workflow(workflow, feature, state)

        # (W-PR) Loud quiesce-vs-complete report; exits non-zero on quiesce.
        _print_workflow_outcome(runner, label="Workflow")

    finally:
        await teardown(env)


async def _run_resume(
    feature_id: str,
    workspace: str,
    *,
    agent_runtime: str = "claude",
    runtime_policy: str = DEFAULT_RUNTIME_POLICY,
    single_agent_runtime: bool = False,
    driver: str | None = None,
    from_phase: str | None = None,
) -> None:
    """Resume an interrupted workflow from its persisted phase.

    Mirrors ``_run``'s bootstrap wiring but loads the EXISTING feature (no
    ``create_feature``) and calls ``resume_workflow`` so completed/sealed work
    is skipped. The fresh-feature path in ``_run`` is left untouched.
    """
    from iriai_compose.runtimes import AutoApproveRuntime

    from ...execution_control.startup import (
        EnvFlagState,
        assert_control_plane_ready_for_workflow_launch,
        read_control_plane_env_flag,
    )
    from ...stream import print_stream
    from .interaction import ThreadAwareTerminalInteractionRuntime
    from .._bootstrap import (
        bootstrap,
        build_runner,
        maybe_assert_adopted_or_legacy_for_resume,
        rebuild_state,
        select_workflow,
        teardown,
    )

    workspace_path = Path(workspace).resolve()
    env = await bootstrap(workspace_path)

    try:
        flag_state = read_control_plane_env_flag()
        if flag_state is EnvFlagState.ENABLED:
            await assert_control_plane_ready_for_workflow_launch(
                pool=env.pool,
                require_enabled=True,
            )

        if driver == "agent":
            from .agent_driven_interaction import AgentDrivenInteractionRuntime

            interaction_runtime = AgentDrivenInteractionRuntime(
                workspace_root=workspace_path
            )
        elif driver == "auto":
            interaction_runtime = AutoApproveRuntime()
        else:
            interaction_runtime = ThreadAwareTerminalInteractionRuntime()

        runner = build_runner(
            env,
            interaction_runtimes={"terminal": interaction_runtime, "auto": interaction_runtime},
            on_message=print_stream,
            agent_runtime_name=agent_runtime,
            runtime_policy=normalize_runtime_policy(runtime_policy),
            single_agent_runtime=single_agent_runtime,
        )

        feature = await env.feature_store.get_feature(feature_id)
        if feature is None:
            raise click.ClickException(
                f"Cannot resume: feature '{feature_id}' not found in database."
            )

        # Resume guard (is_resume=True) -- consult the Slice-12d adoption guard
        # at the workflow seam, identical to Slack's _resume_workflow.
        await maybe_assert_adopted_or_legacy_for_resume(
            feature=feature,
            artifacts=env.artifacts,
            is_resume=True,
        )

        workflow = select_workflow(feature.workflow_name)
        state = await rebuild_state(feature.workflow_name, env.artifacts, feature)

        resume_phase = from_phase or str(feature.metadata.get("_db_phase", "") or "")
        if not resume_phase:
            raise click.ClickException(
                f"Cannot determine resume phase for feature '{feature_id}'; "
                "pass --from-phase explicitly."
            )

        print(f"\n{'='*60}")
        print(f"  iriai-build-v2 — RESUME {feature.workflow_name}")
        print(f"  Feature: {feature.name[:60]} ({feature.id})")
        print(f"  Workspace: {workspace_path}")
        print(f"  Resume from phase: {resume_phase}")
        print(f"{'='*60}\n")

        await runner.resume_workflow(
            workflow, feature, state, resume_from_phase=resume_phase
        )

        # (W-PR) Loud quiesce-vs-complete report; exits non-zero on quiesce.
        # Occurrence 3 (develop14, 2026-06-11 19:27:20): the implementation
        # phase quiesced on a typed SANDBOX_WORKFLOW_BLOCKER yet this command
        # printed "Workflow resume complete!" and exited 0.
        _print_workflow_outcome(runner, label="Workflow resume")

    finally:
        await teardown(env)


async def _override_task_core(
    *,
    artifacts: object,
    feature_store: object,
    feature_id: str,
    task_id: str,
    target_status: str,
    reason: str,
    authorized_by: str,
    echo: object = click.echo,
) -> dict:
    """Operator gate-override (W-OG): validate + write the durable
    ``dag-task-operator-override:{task_id}`` marker via the store layer.

    Pure store-level core (dependency-injected ``artifacts`` /
    ``feature_store`` so tests run against fakes). Rails, all fail-fast:

    1. non-empty ``--reason`` (an override is an audited action);
    2. the feature must exist;
    3. the task must be known to the ACTIVE DAG (artifact key ``"dag"`` — the
       same key the engine reads, ``phases/implementation.py``);
    4. refuse when the task already has a terminal (completed) ``dag-task:*``
       row — there is nothing to override;
    5. idempotent: re-running with the same args is a no-op (no second row).

    Returns a summary dict describing what was (or was not) written.
    """
    from ...models.outputs import ImplementationDAG, ImplementationResult
    from ...workflows.develop.execution.operator_override import (
        new_operator_override,
        operator_override_marker_key,
        overrides_equivalent,
        parse_operator_override,
    )

    task_id = str(task_id or "").strip()
    if not task_id:
        raise click.ClickException("--task-id must be non-empty.")
    if not str(reason or "").strip():
        raise click.ClickException(
            "--reason must be non-empty: an operator override is an audited "
            "action and requires a recorded justification."
        )

    feature = await feature_store.get_feature(feature_id)
    if feature is None:
        raise click.ClickException(
            f"Feature '{feature_id}' not found in the workflow database."
        )

    # Rail: the task must be known to the ACTIVE DAG.
    dag_raw = await artifacts.get("dag", feature=feature)
    if not dag_raw:
        raise click.ClickException(
            f"Feature '{feature_id}' has no active implementation DAG "
            "(artifact key 'dag'); cannot validate the task id — refusing to "
            "record an override for an unknown task."
        )
    try:
        dag = ImplementationDAG.model_validate_json(str(dag_raw))
    except Exception as exc:  # noqa: BLE001 - loud, typed CLI failure.
        raise click.ClickException(
            f"Feature '{feature_id}' active DAG artifact could not be parsed "
            f"({exc}); refusing to record an override without validating the "
            "task id."
        ) from exc
    known_task_ids = {t.id for t in dag.tasks}
    if task_id not in known_task_ids:
        sample = ", ".join(sorted(known_task_ids)[:8])
        raise click.ClickException(
            f"Task '{task_id}' is unknown to the active DAG for feature "
            f"'{feature_id}' ({len(known_task_ids)} known tasks; e.g. "
            f"{sample}). Refusing to record the override."
        )

    # Rail: refuse when the task already has a TERMINAL dag-task row.
    existing_raw = await artifacts.get(f"dag-task:{task_id}", feature=feature)
    existing_status = ""
    if existing_raw:
        try:
            existing_result = ImplementationResult.model_validate_json(
                str(existing_raw)
            )
            existing_status = existing_result.status
        except Exception:  # noqa: BLE001 - unparseable row is not terminal.
            existing_status = ""
        if existing_status == "completed":
            raise click.ClickException(
                f"Task '{task_id}' already has a terminal dag-task row "
                "(status=completed); there is nothing to override. Refusing."
            )

    override = new_operator_override(
        task_id=task_id,
        reason=reason,
        authorized_by=authorized_by,
        feature_id=feature_id,
        target_status=target_status,
    )
    marker_key = operator_override_marker_key(task_id)

    # Rail: idempotency. Same intent already recorded → no second row.
    prior_raw = await artifacts.get(marker_key, feature=feature)
    if prior_raw:
        try:
            prior = parse_operator_override(prior_raw)
        except ValueError:
            prior = None
        if prior is not None and overrides_equivalent(prior, override):
            echo(
                f"OPERATOR-OVERRIDE already recorded for task '{task_id}' "
                f"(key {marker_key}) with the same status/reason/authorizer — "
                "idempotent no-op, nothing written."
            )
            return {
                "written": False,
                "idempotent": True,
                "marker_key": marker_key,
                "override": prior.model_dump(),
            }
        echo(
            f"WARNING: task '{task_id}' already has an override marker with "
            "DIFFERENT content; the new marker row will supersede it (the "
            "engine reads the newest row)."
        )

    await artifacts.put(marker_key, override.model_dump_json(), feature=feature)
    record = None
    get_record = getattr(artifacts, "get_record", None)
    if callable(get_record):
        record = await get_record(marker_key, feature=feature)

    echo("=" * 60)
    echo("  OPERATOR-OVERRIDE MARKER RECORDED")
    echo(f"  feature_id    : {feature_id}")
    echo(f"  task_id       : {task_id}")
    echo(f"  marker key    : {marker_key}")
    if record is not None:
        echo(f"  artifact row  : id={record.get('id')} created_at={record.get('created_at')}")
    echo(f"  target status : {override.target_status}")
    echo(f"  authorized_by : {override.authorized_by}")
    echo(f"  recorded_at   : {override.created_at}")
    if existing_status:
        echo(
            f"  note          : supersedes non-terminal dag-task row "
            f"(status={existing_status})"
        )
    echo(f"  reason        : {override.reason}")
    echo(
        "  The implementation dispatch loop will consume this marker on the "
        "next boot/resume:"
    )
    echo(
        "  it persists the terminal dag-task row with override provenance "
        "and SKIPS executing the task."
    )
    echo("=" * 60)
    return {
        "written": True,
        "idempotent": False,
        "marker_key": marker_key,
        "artifact_row_id": (record or {}).get("id"),
        "override": override.model_dump(),
    }


async def _run_override_task(
    feature_id: str,
    task_id: str,
    target_status: str,
    reason: str,
    authorized_by: str,
) -> None:
    """Wire the real Postgres-backed stores (the same store layer
    ``_bootstrap.bootstrap`` builds) around ``_override_task_core``."""
    from ...config import DATABASE_URL
    from ...db import create_pool, ensure_schema
    from ...storage import PostgresArtifactStore, PostgresFeatureStore

    pool = await create_pool(DATABASE_URL)
    try:
        await ensure_schema(pool)
        artifacts = PostgresArtifactStore(pool)
        feature_store = PostgresFeatureStore(pool)
        await _override_task_core(
            artifacts=artifacts,
            feature_store=feature_store,
            feature_id=feature_id,
            task_id=task_id,
            target_status=target_status,
            reason=reason,
            authorized_by=authorized_by,
        )
    finally:
        await pool.close()


@click.group()
def cli() -> None:
    """iriai-build-v2 — Agent orchestration build system."""
    pass


@cli.command("override-task")
@click.option("--feature-id", required=True, help="Feature ID whose DAG task is being overridden.")
@click.option("--task-id", required=True, help="DAG task ID to override (must exist in the active DAG).")
@click.option(
    "--status",
    "target_status",
    type=click.Choice(["completed"]),
    default="completed",
    show_default=True,
    help="Terminal status the override grants (only 'completed' is supported).",
)
@click.option(
    "--reason",
    required=True,
    help="Non-empty audited justification (recorded verbatim in the marker).",
)
@click.option(
    "--authorized-by",
    default="operator",
    show_default=True,
    help="Who authorized the override (audit provenance).",
)
def override_task(
    feature_id: str,
    task_id: str,
    target_status: str,
    reason: str,
    authorized_by: str,
) -> None:
    """Record an audited OPERATOR OVERRIDE for a DAG task.

    Writes a durable ``dag-task-operator-override:{task_id}`` marker via the
    store layer. The implementation dispatch loop consumes the marker before
    dispatching the task: it persists the terminal ``dag-task`` row with
    operator provenance and skips execution (single-shot; composes with group
    sealing). Refuses unknown tasks, already-terminal tasks, and empty
    reasons; re-running with the same arguments is an idempotent no-op.
    """
    asyncio.run(
        _run_override_task(
            feature_id,
            task_id,
            target_status,
            reason,
            authorized_by,
        )
    )


@cli.command()
@click.option("--name", required=True, help="Feature name")
@click.option("--workspace", default=".", help="Project workspace path")
@click.option("--repo", multiple=True, help="GitHub repo (org/repo) or local path. Repeatable.")
@click.option("--auto", is_flag=True, help="Auto-approve all gates")
@click.option(
    "--driver",
    type=click.Choice(["auto", "agent"]),
    default=None,
    help="Interaction driver: 'auto' (auto-approve) or 'agent' (external driving agent).",
)
@click.option(
    "--agent-runtime",
    default=None,
    help=(
        "Agent runtime to use for workflow agents "
        "(claude, claude_pool, agent_pool, or codex). agent_pool is a flat "
        "heterogeneous pool: N Claude accounts + Codex, co-equal with "
        "usage-limit cooldown, dynamic re-probe, and Claude->Codex spillover."
    ),
)
@click.option(
    "--runtime-policy",
    type=click.Choice(list(SUPPORTED_RUNTIME_POLICIES)),
    default=DEFAULT_RUNTIME_POLICY,
    show_default=True,
    help=(
        "Runtime routing policy. 'alternating' (default) spreads planning "
        "fan-out ~50/50 across the Claude primary and Codex secondary."
    ),
)
@click.option(
    "--claude-only",
    is_flag=True,
    help="When using Claude primary, also use Claude as the secondary runtime (no alternation).",
)
def plan(
    name: str,
    workspace: str,
    repo: tuple[str, ...],
    auto: bool,
    driver: str | None,
    agent_runtime: str | None,
    runtime_policy: str,
    claude_only: bool,
) -> None:
    """Run the planning workflow (Scoping → PM → Design → Architecture → Plan Review)."""
    from ...runtimes import normalize_agent_runtime

    try:
        resolved_runtime = normalize_agent_runtime(agent_runtime)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--agent-runtime") from exc
    try:
        resolved_runtime_policy = normalize_runtime_policy(runtime_policy)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--runtime-policy") from exc
    if claude_only and resolved_runtime not in {"claude", "claude_pool", "agent_pool"}:
        raise click.BadParameter(
            "--claude-only can only be used with Claude, Claude pool, or agent "
            "pool as the primary runtime.",
            param_hint="--claude-only",
        )
    asyncio.run(
        _run(
            "planning",
            name,
            workspace,
            auto,
            agent_runtime=resolved_runtime,
            runtime_policy=resolved_runtime_policy,
            single_agent_runtime=claude_only,
            driver=driver,
            repos=list(repo) or None,
        )
    )


@cli.command()
@click.option("--feature-id", required=True, help="Feature ID to resume")
@click.option("--workspace", default=".", help="Project workspace path")
@click.option(
    "--from-phase",
    default=None,
    help="Phase to resume from (defaults to the feature's persisted phase).",
)
@click.option(
    "--driver",
    type=click.Choice(["auto", "agent"]),
    default=None,
    help="Interaction driver: 'auto' (auto-approve) or 'agent' (external driving agent).",
)
@click.option(
    "--agent-runtime",
    default=None,
    help=(
        "Agent runtime to use for workflow agents "
        "(claude, claude_pool, agent_pool, or codex). agent_pool is a flat "
        "heterogeneous pool: N Claude accounts + Codex, co-equal with "
        "usage-limit cooldown, dynamic re-probe, and Claude->Codex spillover."
    ),
)
@click.option(
    "--runtime-policy",
    type=click.Choice(list(SUPPORTED_RUNTIME_POLICIES)),
    default=DEFAULT_RUNTIME_POLICY,
    show_default=True,
    help=(
        "Runtime routing policy. 'alternating' (default) spreads planning "
        "fan-out ~50/50 across the Claude primary and Codex secondary."
    ),
)
@click.option(
    "--claude-only",
    is_flag=True,
    help="When using Claude primary, also use Claude as the secondary runtime (no alternation).",
)
def resume(
    feature_id: str,
    workspace: str,
    from_phase: str | None,
    driver: str | None,
    agent_runtime: str | None,
    runtime_policy: str,
    claude_only: bool,
) -> None:
    """Resume an interrupted workflow from its last persisted phase.

    Loads the existing feature + reconstructs state from persisted artifacts,
    then skips already-completed phases/steps. Unlike ``plan``/``develop`` it
    does NOT create a fresh feature.
    """
    from ...runtimes import normalize_agent_runtime

    try:
        resolved_runtime = normalize_agent_runtime(agent_runtime)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--agent-runtime") from exc
    try:
        resolved_runtime_policy = normalize_runtime_policy(runtime_policy)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--runtime-policy") from exc
    if claude_only and resolved_runtime not in {"claude", "claude_pool", "agent_pool"}:
        raise click.BadParameter(
            "--claude-only can only be used with Claude, Claude pool, or agent "
            "pool as the primary runtime.",
            param_hint="--claude-only",
        )
    asyncio.run(
        _run_resume(
            feature_id,
            workspace,
            agent_runtime=resolved_runtime,
            runtime_policy=resolved_runtime_policy,
            single_agent_runtime=claude_only,
            driver=driver,
            from_phase=from_phase,
        )
    )


@cli.command()
@click.option("--name", required=True, help="Feature name")
@click.option("--workspace", default=".", help="Project workspace path")
@click.option("--repo", multiple=True, help="GitHub repo (org/repo) or local path. Repeatable.")
@click.option("--auto", is_flag=True, help="Auto-approve all gates")
@click.option(
    "--driver",
    type=click.Choice(["auto", "agent"]),
    default=None,
    help="Interaction driver: 'auto' (auto-approve) or 'agent' (external driving agent).",
)
@click.option(
    "--agent-runtime",
    default=None,
    help=(
        "Agent runtime to use for workflow agents "
        "(claude, claude_pool, agent_pool, or codex). agent_pool is a flat "
        "heterogeneous pool: N Claude accounts + Codex, co-equal with "
        "usage-limit cooldown, dynamic re-probe, and Claude->Codex spillover."
    ),
)
@click.option(
    "--runtime-policy",
    type=click.Choice(list(SUPPORTED_RUNTIME_POLICIES)),
    default=DEFAULT_RUNTIME_POLICY,
    show_default=True,
    help=(
        "Runtime routing policy. 'alternating' (default) spreads planning "
        "fan-out ~50/50 across the Claude primary and Codex secondary; "
        "develop also alternates DAG groups."
    ),
)
@click.option(
    "--claude-only",
    is_flag=True,
    help="When using Claude primary, also use Claude as the secondary runtime (no alternation).",
)
def develop(
    name: str,
    workspace: str,
    repo: tuple[str, ...],
    auto: bool,
    driver: str | None,
    agent_runtime: str | None,
    runtime_policy: str,
    claude_only: bool,
) -> None:
    """Run the full develop workflow (Planning + Implementation)."""
    from ...runtimes import normalize_agent_runtime

    try:
        resolved_runtime = normalize_agent_runtime(agent_runtime)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--agent-runtime") from exc
    try:
        resolved_runtime_policy = normalize_runtime_policy(runtime_policy)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--runtime-policy") from exc
    if claude_only and resolved_runtime not in {"claude", "claude_pool", "agent_pool"}:
        raise click.BadParameter(
            "--claude-only can only be used with Claude, Claude pool, or agent "
            "pool as the primary runtime.",
            param_hint="--claude-only",
        )
    asyncio.run(
        _run(
            "full-develop",
            name,
            workspace,
            auto,
            agent_runtime=resolved_runtime,
            runtime_policy=resolved_runtime_policy,
            single_agent_runtime=claude_only,
            driver=driver,
            repos=list(repo) or None,
        )
    )


@cli.command()
@click.option("--name", required=True, help="Bug description (short)")
@click.option("--project", required=True, help="Project to deploy preview for")
@click.option("--workspace", default=".", help="Project workspace path")
@click.option("--auto", is_flag=True, help="Auto-approve all gates")
@click.option(
    "--bug-report",
    "bug_report_path",
    default=None,
    type=click.Path(exists=True),
    help="Path to bug report JSON (skips intake interview)",
)
@click.option(
    "--agent-runtime",
    default=None,
    help=(
        "Agent runtime to use for workflow agents "
        "(claude, claude_pool, agent_pool, or codex). agent_pool is a flat "
        "heterogeneous pool: N Claude accounts + Codex, co-equal with "
        "usage-limit cooldown, dynamic re-probe, and Claude->Codex spillover."
    ),
)
def bugfix(
    name: str,
    project: str,
    workspace: str,
    auto: bool,
    bug_report_path: str | None,
    agent_runtime: str | None,
) -> None:
    """Run the bug fix workflow (Intake → Reproduce → Diagnose → Fix → Verify)."""
    from ...runtimes import normalize_agent_runtime

    try:
        resolved_runtime = normalize_agent_runtime(agent_runtime)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--agent-runtime") from exc
    bug_report = ""
    if bug_report_path:
        bug_report = Path(bug_report_path).read_text()
    asyncio.run(
        _run(
            "bugfix",
            name,
            workspace,
            auto,
            agent_runtime=resolved_runtime,
            project=project,
            bug_report=bug_report,
        )
    )


@cli.command("slack")
@click.option("--channel", required=True, help="Slack planning channel ID")
@click.option(
    "--workspace",
    default=None,
    help="Default project workspace path (optional — user selects per-feature via card)",
)
@click.option(
    "--mode",
    type=click.Choice(["multiplayer", "singleplayer"]),
    default="multiplayer",
    help="multiplayer: bot responds only to @mentions. singleplayer: bot responds to all messages.",
)
@click.option(
    "--agent-runtime",
    default=None,
    help=(
        "Agent runtime to use for workflow agents "
        "(claude, claude_pool, agent_pool, or codex). agent_pool is a flat "
        "heterogeneous pool: N Claude accounts + Codex, co-equal with "
        "usage-limit cooldown, dynamic re-probe, and Claude->Codex spillover."
    ),
)
@click.option(
    "--runtime-policy",
    type=click.Choice(list(SUPPORTED_RUNTIME_POLICIES)),
    default=DEFAULT_RUNTIME_POLICY,
    help="Runtime routing policy for workflow roles.",
)
@click.option(
    "--claude-pool-codex-review",
    is_flag=True,
    help=(
        "Use claude_pool primary plus Codex secondary for "
        "review/verification roles."
    ),
)
@click.option(
    "--claude-only",
    is_flag=True,
    help="When using Claude primary, also use Claude as the secondary runtime.",
)
@click.option(
    "--budget",
    is_flag=True,
    help="Use Sonnet for implementers, Opus for verifiers.",
)
@click.option(
    "--concurrency-max",
    type=click.IntRange(min=1),
    default=None,
    help="Maximum active agent invocations across the Slack bridge.",
)
@click.option(
    "--autonomous-remainder",
    is_flag=True,
    help="Delegate later-phase human prompts (plan-review through implementation) to an agent.",
)
@click.option(
    "--slack-verbosity",
    type=click.Choice(["normal", "quiet"]),
    default="normal",
    show_default=True,
    help="Slack bridge message verbosity.",
)
@click.option(
    "--ignore-mention-user-id",
    "ignored_mention_user_ids",
    multiple=True,
    help=(
        "Slack user id whose mentions should be ignored by this bridge, "
        "for colocated bots such as the supervisor."
    ),
)
def slack_cmd(
    channel: str,
    workspace: str | None,
    mode: str,
    agent_runtime: str | None,
    runtime_policy: str,
    claude_pool_codex_review: bool,
    claude_only: bool,
    budget: bool,
    concurrency_max: int | None,
    autonomous_remainder: bool,
    slack_verbosity: str,
    ignored_mention_user_ids: tuple[str, ...],
) -> None:
    """Start the Slack bridge (long-lived process)."""
    import logging as _logging

    from ...runtimes import normalize_agent_runtime

    try:
        resolved_runtime = normalize_agent_runtime(agent_runtime)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--agent-runtime") from exc
    try:
        resolved_runtime_policy = normalize_runtime_policy(runtime_policy)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--runtime-policy") from exc

    runtime_policy_override = resolved_runtime_policy != DEFAULT_RUNTIME_POLICY
    if claude_pool_codex_review:
        if agent_runtime is not None and resolved_runtime != "claude_pool":
            raise click.BadParameter(
                "--claude-pool-codex-review cannot be combined with a "
                "non-Claude-pool --agent-runtime.",
                param_hint="--claude-pool-codex-review",
            )
        resolved_runtime = "claude_pool"
        resolved_runtime_policy = PRIMARY_IMPL_SECONDARY_REVIEW_POLICY
        runtime_policy_override = True

    if claude_only and resolved_runtime not in {"claude", "claude_pool", "agent_pool"}:
        raise click.BadParameter(
            "--claude-only can only be used with Claude, Claude pool, or agent "
            "pool as the primary runtime.",
            param_hint="--claude-only",
        )

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from ..slack.app import run_slack_bridge

    asyncio.run(
        run_slack_bridge(
            planning_channel=channel,
            workspace=workspace,
            mode=mode,
            agent_runtime=resolved_runtime,
            agent_runtime_override=agent_runtime is not None or claude_pool_codex_review,
            runtime_policy=resolved_runtime_policy,
            runtime_policy_override=runtime_policy_override,
            single_agent_runtime=claude_only,
            budget=budget,
            concurrency_max=concurrency_max,
            autonomous_remainder=autonomous_remainder,
            slack_verbosity=slack_verbosity,
            ignored_mention_user_ids=set(ignored_mention_user_ids),
        )
    )


@cli.command("supervisor")
@click.option("--channel", required=True, help="Slack channel ID for the supervisor bot.")
@click.option("--feature", default=None, help="Feature ID the supervisor should focus on.")
@click.option("--dashboard-url", default=None, help="Dashboard URL for the supervised feature.")
@click.option(
    "--runtime",
    default="codex",
    show_default=True,
    help="Supervisor service runtime name.",
)
@click.option(
    "--mode",
    type=click.Choice(["multiplayer", "singleplayer"]),
    default="singleplayer",
    show_default=True,
    help="multiplayer: respond to @mentions. singleplayer: respond to all channel messages.",
)
@click.option(
    "--supervisor-mode",
    type=click.Choice(["read_only", "guarded"]),
    default="read_only",
    show_default=True,
    help="Supervisor action authority.",
)
@click.option(
    "--poll-interval",
    type=float,
    default=30.0,
    show_default=True,
    help="Seconds between supervisor evidence polls.",
)
@click.option(
    "--digest-interval",
    type=float,
    default=120.0,
    show_default=True,
    help="Minimum seconds between identical supervisor digests.",
)
@click.option(
    "--worktree-root",
    multiple=True,
    help="Repo/worktree root to probe for hygiene blockers. May be repeated.",
)
@click.option(
    "--forbidden-path",
    multiple=True,
    help="Manifest-forbidden path or prefix to probe in worktree roots. May be repeated.",
)
@click.option(
    "--app-token-env",
    default="SUPERVISOR_SLACK_APP_TOKEN",
    show_default=True,
    help="Environment variable containing the supervisor Socket Mode app token.",
)
@click.option(
    "--bot-token-env",
    default="SUPERVISOR_SLACK_BOT_TOKEN",
    show_default=True,
    help="Environment variable containing the supervisor bot token.",
)
def supervisor_cmd(
    channel: str,
    feature: str | None,
    dashboard_url: str | None,
    runtime: str,
    mode: str,
    supervisor_mode: str,
    poll_interval: float,
    digest_interval: float,
    worktree_root: tuple[str, ...],
    forbidden_path: tuple[str, ...],
    app_token_env: str,
    bot_token_env: str,
) -> None:
    """Start the supervisor Slack bot (long-lived process)."""
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from ...supervisor.slack import run_supervisor_slack_app

    asyncio.run(
        run_supervisor_slack_app(
            channel=channel,
            feature_id=feature,
            dashboard_url=dashboard_url,
            runtime=runtime,
            mode=mode,
            supervisor_mode=supervisor_mode,
            poll_interval_seconds=poll_interval,
            min_digest_interval_seconds=digest_interval,
            worktree_roots=list(worktree_root),
            forbidden_paths=list(forbidden_path),
            app_token_env=app_token_env,
            bot_token_env=bot_token_env,
        )
    )


@cli.group("claude-pool")
def claude_pool_cmd() -> None:
    """Manage the local Claude account pool runtime."""
    pass


@claude_pool_cmd.command("doctor")
@click.option(
    "--root",
    default=None,
    help="Claude pool root directory (defaults to /Users/Shared/iriai/claude-pool).",
)
@click.option(
    "--skip-health-checks",
    is_flag=True,
    help="Only inspect config and heartbeats; do not submit runner health jobs.",
)
@click.option("--timeout", default=60.0, help="Seconds to wait for each health-check job.")
def claude_pool_doctor(root: str | None, skip_health_checks: bool, timeout: float) -> None:
    """Check shared spool, runner heartbeats, and per-profile user context."""
    from ...runtimes.claude_pool import DEFAULT_POOL_ROOT, doctor

    root_path = Path(root) if root else DEFAULT_POOL_ROOT
    lines = asyncio.run(
        doctor(
            root=root_path,
            run_health_checks=not skip_health_checks,
            timeout=timeout,
        )
    )
    for line in lines:
        click.echo(line)


@claude_pool_cmd.command("install-launchagents")
@click.option(
    "--root",
    default=None,
    help="Claude pool root directory (defaults to /Users/Shared/iriai/claude-pool).",
)
@click.option(
    "--runner-command",
    default=None,
    help="Command used by LaunchAgents to start claude-pool-runner.",
)
def claude_pool_install_launchagents(root: str | None, runner_command: str | None) -> None:
    """Write LaunchAgent plist templates and print install commands."""
    from ...runtimes.claude_pool import DEFAULT_POOL_ROOT, install_launchagent_templates

    root_path = Path(root) if root else DEFAULT_POOL_ROOT
    for line in install_launchagent_templates(root=root_path, runner_command=runner_command):
        click.echo(line)


def main() -> None:
    from .e2e_cmd import register_e2e_commands

    register_e2e_commands(cli)
    cli()


if __name__ == "__main__":
    main()
