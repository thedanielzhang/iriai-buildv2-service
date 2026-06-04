"""Standalone CLI subcommands for the async e2e-testing subsystem.

`iriai-build-v2 preview` — operator run-at-checkpoint (headed full-app launch in
an isolated clone). `iriai-build-v2 e2e` — run the e2e pass/loop. Both are
read-only against checkpoints, use a DEDICATED DB pool, never touch the live
workflow, and never acquire the feature advisory lock.
"""

from __future__ import annotations

import asyncio
import os

import click

LIVE_DSN = os.environ.get(
    "IRIAI_E2E_DSN", "postgresql://danielzhang@localhost:5431/iriai_build_v2"
)
# Studio-default live-repo path template (env-overridable so it is not an absolute
# iriai-studio hardcode). Only a FALLBACK — a real checkpoint carries each repo's
# actual on-disk path; this is used only when that is absent.
_LIVE_REPO_TMPL = os.environ.get(
    "IRIAI_E2E_LIVE_REPO_TMPL",
    "/Users/danielzhang/src/iriai/.iriai/features/"
    "visual-studio-code-frontend-for-project-workflow-manager-{feature}/repos/{repo}",
)


def _default_electron_profile():
    from ...workflows.develop.e2e.models import ProjectProfile

    return ProjectProfile(
        project_kind="electron", repo_path="iriai-studio", adapter_id="browser",
        install_cmd="npm install", build_cmd="npm run compile",
        start_cmd="./scripts/code.sh", native_test_cmd="npx playwright test",
        native_test_configs=["playwright.config.badge.ts",
                             "playwright.config.chat.ts",
                             "playwright.config.lifecycle.ts"],
        ready_probe_kind="http_get", ready_probe_target="http://127.0.0.1:4174",
    )


async def _resolve_checkpoint(feature: str, ref: str, registry):
    from ...workflows.develop.e2e.checkpoint import fetch_latest_sealed_checkpoint

    if ref == "latest":
        return await fetch_latest_sealed_checkpoint(feature, dsn=LIVE_DSN)
    if ref == "latest-green":
        gp = await registry.get_green_pointer() if registry else None
        if gp is None:
            return None
        # Reconstruct a checkpoint view from the green pointer.
        from ...workflows.develop.e2e.checkpoint import RepoCheckpoint, SealedCheckpoint

        repos = [RepoCheckpoint(repo_id=k, repo_path=k, result_commit=v)
                 for k, v in gp.result_commits.items()]
        return SealedCheckpoint(feature_id=feature, group_idx=gp.group_idx, repos=repos)
    if ref.isdigit():
        return await fetch_latest_sealed_checkpoint(
            feature, dsn=LIVE_DSN, max_group_idx=int(ref))
    raise click.ClickException(f"unsupported --checkpoint {ref!r}")


async def _open_live(feature: str):
    """Open the registry against the LIVE workflow DB under the develop feature id.

    Profile/status/verdicts then read+write the SAME artifact store the develop
    workflow + ``SandboxRunner`` use (no scratch DB, no scratch_feature remap), so
    the inferred ProjectProfile is readable by both sides (gates AC-K-3). The read
    path (``fetch_latest_sealed_checkpoint``) already uses ``LIVE_DSN``.
    """
    import asyncpg
    from iriai_compose import Feature

    from ...workflows.develop.e2e.registry import open_scratch_registry

    try:
        # Refuse to attach to a non-existent feature — against the LIVE DB this
        # would otherwise INSERT a phantom feature row (the old scratch DB could
        # never touch live). Fail honestly instead of silently polluting.
        conn = await asyncpg.connect(LIVE_DSN)
        try:
            exists = await conn.fetchval(
                "SELECT 1 FROM features WHERE id=$1", feature)
        finally:
            await conn.close()
        if not exists:
            click.echo(
                f"[error] no develop feature {feature!r} in the workflow DB; "
                f"run `iriai-build-v2 develop` first.")
            return None, None
        feat = Feature(id=feature, name=feature, slug=feature,
                       workflow_name="full-develop", workspace_id="main")
        # open_scratch_registry is DSN-generic; ensure_feature_row is a no-op when
        # the real feature row already exists (INSERT ... ON CONFLICT DO NOTHING).
        return await open_scratch_registry(LIVE_DSN, feat)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"[warn] registry unavailable: {exc}")
        return None, None


def _load_profile_json(path: str):
    """Load a ProjectProfile from a JSON file (the ``--profile-json`` override)."""
    import json

    from ...workflows.develop.e2e.models import ProjectProfile

    from pathlib import Path

    return ProjectProfile.model_validate(json.loads(Path(path).read_text()))


async def _resolve_profile(profile_json: str | None, registry):
    """Profile precedence: ``--profile-json`` -> persisted registry profile ->
    the iriai-studio electron default (so a profile-absent studio run is unchanged)."""
    if profile_json:
        return _load_profile_json(profile_json)
    return (await registry.get_profile() if registry else None) \
        or _default_electron_profile()


async def _preview(
    feature: str, checkpoint: str, build: bool, launch: bool,
    profile_json: str | None = None,
) -> None:
    from ...workflows.develop.e2e.substrate import CloneSubstrate

    pool, registry = await _open_live(feature)
    try:
        cp = await _resolve_checkpoint(feature, checkpoint, registry)
        if cp is None:
            if checkpoint == "latest-green":
                click.echo("No green checkpoint yet. Try --checkpoint latest.")
            else:
                click.echo("No sealed checkpoint found.")
            return
        commits = cp.result_commits()
        click.echo(f"Resolved {checkpoint} -> group {cp.group_idx}: {commits}")

        sources = {r.repo_key: (r.repo_path or _live_repo_path(feature, r.repo_key))
                   for r in cp.repos}
        sub = CloneSubstrate(role="preview", mode="operator", persist=True)
        click.echo(f"Cloning checkpoint into isolated operator dir {sub.repos_dir} ...")
        checkouts = await sub.clone_checkpoint(sources=sources, commits=commits)
        profile = await _resolve_profile(profile_json, registry)
        primary = checkouts.get(profile.repo_path) or next(iter(checkouts.values()))
        click.echo(f"Operator preview checkout: {primary.checkout_dir}")
        click.echo(f"  build_cmd: {profile.build_cmd}")
        click.echo(f"  start_cmd (headed): {profile.start_cmd}")
        if build and profile.build_cmd:
            click.echo("Building (this can take a while)...")
            await _run(profile.build_cmd, cwd=str(primary.checkout_dir))
        if launch and profile.start_cmd:
            click.echo("Launching the real app headed for manual use. Ctrl-C to stop.")
            await _run(profile.start_cmd, cwd=str(primary.checkout_dir), stream=True)
        else:
            click.echo("(skipping launch; --launch to run the headed app)")
    finally:
        if pool is not None:
            await pool.close()


def _live_repo_path(feature: str, repo_key: str) -> str:
    return _LIVE_REPO_TMPL.format(feature=feature, repo=repo_key)


async def _run(cmd: str, *, cwd: str, stream: bool = False) -> int:
    import shlex
    proc = await asyncio.create_subprocess_exec(
        "nice", "-n", "10", *shlex.split(cmd), cwd=cwd,
        stdout=None if stream else asyncio.subprocess.PIPE,
        stderr=None if stream else asyncio.subprocess.STDOUT,
    )
    await proc.wait()
    return proc.returncode or 0


async def _e2e(
    feature: str, loop: bool, do_pass: bool, profile_json: str | None = None
) -> None:
    from ...workflows.develop.e2e.checkpoint import fetch_latest_sealed_checkpoint
    from ...workflows.develop.e2e.models import E2ETrackCursor
    from ...workflows.develop.e2e.pass_ import run_full_pass
    from ...workflows.develop.e2e.runner_loop import AsyncE2ETrack, host_preflight

    pool, registry = await _open_live(feature)
    try:
        # --profile-json override; else run_full_pass resolves from the registry.
        override_profile = _load_profile_json(profile_json) if profile_json else None
        pf = host_preflight()
        click.echo(f"preflight: ok={pf.ok} load={pf.load1:.1f} "
                   f"free_mem={pf.free_mem_gb:.1f}GB free_disk={pf.free_disk_gb:.0f}GB")

        def _pass(cp):
            return run_full_pass(cp, feature_id=feature, registry=registry,
                                 live_dsn=LIVE_DSN, profile=override_profile,
                                 on_log=lambda m: click.echo("  " + m))

        if loop:
            track = AsyncE2ETrack(feature_id=feature, live_dsn=LIVE_DSN,
                                  registry=registry,
                                  pass_fn=(_pass if do_pass else None))
            click.echo("Polling latest sealed checkpoint (read-only, no lock). Ctrl-C to stop.")
            await track.run_loop(do_pass=do_pass)
            return

        cp = await fetch_latest_sealed_checkpoint(feature, dsn=LIVE_DSN)
        if cp is None:
            click.echo("No sealed checkpoint found.")
            return
        if not do_pass:
            click.echo(f"latest sealed: group {cp.group_idx} commits={cp.result_commits()}")
            return
        if not pf.ok:
            click.echo(f"preflight abort (resource-bounded): {pf.reason}")
            return
        click.echo(f"running integrated e2e pass @ group {cp.group_idx} ...")
        s = await run_full_pass(cp, feature_id=feature, registry=registry,
                                live_dsn=LIVE_DSN, profile=override_profile,
                                on_log=lambda m: click.echo("  " + m))
        click.echo(f"PASS @ group {s.group_idx}: {s.detail}")
        click.echo(f"  green={s.green} preview_url='{s.preview_url}' "
                   f"backlog+={s.backlog_appended} open_reds={len(s.open_regressions)}")
        head = next(iter(cp.result_commits().values()), "")
        await registry.put_cursor(
            E2ETrackCursor(last_processed_commit=head, group_idx=cp.group_idx))
    finally:
        if pool is not None:
            await pool.close()


@click.command("preview")
@click.option("--feature", required=True, help="Feature id, e.g. 8ac124d6")
@click.option("--checkpoint", default="latest",
              help="latest | latest-green | <group-idx>")
@click.option("--build/--no-build", default=True)
@click.option("--launch/--no-launch", default=True,
              help="Headed full-app launch for manual use")
@click.option("--profile-json", default=None,
              help="Path to a ProjectProfile JSON (overrides the persisted/default "
                   "profile)")
def preview(feature: str, checkpoint: str, build: bool, launch: bool,
            profile_json: str | None) -> None:
    """Operator run-at-checkpoint: build + launch the real app (headed) in an
    isolated clone, independent of the workflow and the e2e track."""
    asyncio.run(_preview(feature, checkpoint, build, launch, profile_json))


@click.command("e2e")
@click.option("--feature", required=True, help="Feature id, e.g. 8ac124d6")
@click.option("--loop/--once", default=False)
@click.option("--pass/--no-pass", "do_pass", default=True,
              help="Run the full e2e pass (provision+smoke+replay+triage+status), "
                   "or --no-pass for a read-only checkpoint poll")
@click.option("--profile-json", default=None,
              help="Path to a ProjectProfile JSON (overrides the persisted/default "
                   "profile)")
def e2e(feature: str, loop: bool, do_pass: bool, profile_json: str | None) -> None:
    """Run the async e2e track (read-only against sealed checkpoints)."""
    asyncio.run(_e2e(feature, loop, do_pass, profile_json))


def register_e2e_commands(cli) -> None:
    cli.add_command(preview)
    cli.add_command(e2e)
