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
SCRATCH_DSN = os.environ.get(
    "IRIAI_E2E_SCRATCH_DSN",
    "postgresql://danielzhang@localhost:5431/iriai_build_v2_e2e_scratch",
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


async def _open_scratch(feature: str):
    from ...workflows.develop.e2e.registry import open_scratch_registry, scratch_feature

    try:
        return await open_scratch_registry(SCRATCH_DSN, scratch_feature(feature))
    except Exception as exc:  # noqa: BLE001
        click.echo(f"[warn] scratch registry unavailable: {exc}")
        return None, None


async def _preview(feature: str, checkpoint: str, build: bool, launch: bool) -> None:
    from ...workflows.develop.e2e.substrate import CloneSubstrate

    pool, registry = await _open_scratch(feature)
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
        profile = (await registry.get_profile() if registry else None) \
            or _default_electron_profile()
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
    return (f"/Users/danielzhang/src/iriai/.iriai/features/"
            f"visual-studio-code-frontend-for-project-workflow-manager-{feature}/"
            f"repos/{repo_key}")


async def _run(cmd: str, *, cwd: str, stream: bool = False) -> int:
    import shlex
    proc = await asyncio.create_subprocess_exec(
        "nice", "-n", "10", *shlex.split(cmd), cwd=cwd,
        stdout=None if stream else asyncio.subprocess.PIPE,
        stderr=None if stream else asyncio.subprocess.STDOUT,
    )
    await proc.wait()
    return proc.returncode or 0


async def _e2e(feature: str, loop: bool) -> None:
    from ...workflows.develop.e2e.runner_loop import AsyncE2ETrack, host_preflight

    pool, registry = await _open_scratch(feature)
    try:
        track = AsyncE2ETrack(feature_id=feature, live_dsn=LIVE_DSN, registry=registry)
        pf = host_preflight()
        click.echo(f"preflight: ok={pf.ok} load={pf.load1:.1f} "
                   f"free_mem={pf.free_mem_gb:.1f}GB free_disk={pf.free_disk_gb:.0f}GB")
        if loop:
            click.echo("Polling latest sealed checkpoint (read-only, no lock). Ctrl-C to stop.")
            await track.run_loop(do_pass=False)
        else:
            res = await track.poll_once(do_pass=False)
            cp = res.checkpoint
            click.echo(f"latest sealed: group {cp.group_idx if cp else '?'} "
                       f"advanced={res.advanced} {res.skipped_reason}")
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
def preview(feature: str, checkpoint: str, build: bool, launch: bool) -> None:
    """Operator run-at-checkpoint: build + launch the real app (headed) in an
    isolated clone, independent of the workflow and the e2e track."""
    asyncio.run(_preview(feature, checkpoint, build, launch))


@click.command("e2e")
@click.option("--feature", required=True, help="Feature id, e.g. 8ac124d6")
@click.option("--loop/--once", default=False)
def e2e(feature: str, loop: bool) -> None:
    """Run the async e2e track (read-only against sealed checkpoints)."""
    asyncio.run(_e2e(feature, loop))


def register_e2e_commands(cli) -> None:
    cli.add_command(preview)
    cli.add_command(e2e)
