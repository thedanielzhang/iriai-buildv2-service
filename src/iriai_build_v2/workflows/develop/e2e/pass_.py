"""The integrated e2e pass: one invocation chaining every stage.

provision -> boot-smoke -> select -> bind/author -> native replay (ALL webview
e2e lanes for the journeys completed at the checkpoint) -> real-app e2e
(release-checkpoint, notarized DMG) -> triage -> bridge -> status rollup + green
pointer + cursor. Read-only against the sealed checkpoint; all writes go to the
scratch registry.

This is what ``iriai-build-v2 e2e --once`` runs (``do_pass=True``). It runs the
project's OWN native e2e suites at the checkpoint (every discovered
``playwright.config.*`` — badge/chat/lifecycle/projectSurface/planning-phase-view,
each self-booting its Vite/Mock harness), plus the real Electron app specs when a
notarized DMG is available.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg

from iriai_build_v2.models.outputs import TestPlan

from .adapters import get_adapter
from .bridge import LaneBuildFailure, bridge_build_failures, bridge_findings
from .checkpoint import SealedCheckpoint
from .models import E2ESpecRecord, ProjectProfile
from .status import (
    CapturingPoster,
    build_status,
    emit_status,
    green_pointer_for,
    page_critical,
)
from .substrate import CloneSubstrate
from .triage import bind_specs_from_scenarios, native_results_to_verdicts

LIVE_REPO_TMPL = (
    "/Users/danielzhang/src/iriai/.iriai/features/"
    "visual-studio-code-frontend-for-project-workflow-manager-{feature}/repos/{repo}"
)


@dataclass
class LaneResult:
    config: str
    web_server_ok: bool = False
    passed: int = 0
    failed: int = 0
    flaky: int = 0
    started: bool = False
    detail: str = ""
    boot_error: str = ""  # set ONLY on a real build/webServer failure

    @property
    def boot_failed(self) -> bool:
        """True iff a real build/webServer failure was recorded. A genuinely-empty
        lane (0 spec files, clean report) leaves ``boot_error`` empty and is NOT a
        boot failure."""
        return bool(self.boot_error)


@dataclass
class PassSummary:
    group_idx: int = -1
    boot_smoke: str = ""
    passed: int = 0
    failed: int = 0
    flaky: int = 0
    spec_count: int = 0
    testable_ac_count: int = 0
    lanes: list[LaneResult] = field(default_factory=list)
    real_app: str = ""  # status/skip-reason for the notarized real-app step
    open_regressions: list[str] = field(default_factory=list)
    backlog_appended: int = 0
    green: bool = False
    preview_url: str = ""
    detail: str = ""


def _live_repo(feature: str, repo_key: str) -> str:
    return LIVE_REPO_TMPL.format(feature=feature, repo=repo_key)


# A genuinely-empty lane (0 spec files) surfaces ONLY this in the native report —
# it is NOT a build failure and must never become a finding.
_EMPTY_LANE_MARKERS = ("no tests found", "no tests to run")


def lane_boot_error(run: Any, stderr_tail: str = "") -> str:
    """Build/webServer failure text for a lane, or '' if the lane is healthy OR
    genuinely empty.

    A real failure is a webServer that didn't come up, or a globalSetup error
    captured in the native report. A genuinely-empty lane (0 spec files) reports
    only "No tests found" — Playwright puts that in the report's top-level errors
    too, so we must filter it out, or the empty lane would be misreported as a
    build regression.
    """
    real_errors = [
        e for e in run.global_errors
        if not any(m in e.lower() for m in _EMPTY_LANE_MARKERS)
    ]
    if (not run.web_server_ok) or (not run.started and real_errors):
        return (
            "; ".join(real_errors).strip()
            or stderr_tail[-400:].strip()
            or "harness did not produce a runnable surface"
        )
    return ""


async def _load_latest(conn, feature: str, key: str):
    row = await conn.fetchval(
        "SELECT value FROM artifacts WHERE feature_id=$1 AND key=$2 "
        "ORDER BY id DESC LIMIT 1", feature, key)
    if row is None:
        return None
    v = json.loads(row)
    return json.loads(v) if isinstance(v, str) else v


def _spec_for_test(t, adapter_id, commit):
    return E2ESpecRecord(spec_id=t.title, title=t.title, adapter_id=adapter_id,
                         author_commit=commit, source_commit=commit)


def _default_profile() -> ProjectProfile:
    return ProjectProfile(
        project_kind="electron", repo_path="iriai-studio", adapter_id="browser",
        native_test_cmd="npx playwright test",
        ready_probe_kind="http_get", ready_probe_target="http://127.0.0.1:8787/healthz")


async def run_full_pass(
    checkpoint: SealedCheckpoint,
    *,
    feature_id: str,
    registry: Any,
    live_dsn: str,
    profile: ProjectProfile | None = None,
    configs: list[str] | None = None,
    include_real_app: bool = True,
    bind_slug: str = "chat-sidepane-shell",
    poster: Any = None,
    on_log=lambda m: None,
) -> PassSummary:
    """Run one integrated e2e pass at ``checkpoint`` across all webview lanes."""
    poster = poster or CapturingPoster()
    summary = PassSummary(group_idx=checkpoint.group_idx)
    commits = checkpoint.result_commits()
    studio_commit = commits.get("iriai-studio") or next(iter(commits.values()), "")

    profile = profile or (await registry.get_profile() if registry else None) or _default_profile()
    if profile.adapter_id == "compose":
        # Compose-stack products (kaya) take a dedicated pass; the studio
        # browser/electron path below is left UNTOUCHED.
        return await _run_compose_pass(
            checkpoint,
            feature_id=feature_id,
            registry=registry,
            profile=profile,
            poster=poster,
            on_log=on_log,
        )
    sub = CloneSubstrate(role="track", mode="automated", persist=False)
    on_log(f"provisioning @ group {checkpoint.group_idx} ...")
    checkouts = await sub.clone_checkpoint(
        sources={"iriai-studio": _live_repo(feature_id, "iriai-studio")},
        commits={"iriai-studio": studio_commit})
    checkout = checkouts["iriai-studio"].checkout_dir
    # Mirror the deps the webview lanes need (root + projectSurface); each lane
    # self-builds its dist + self-boots its server in globalSetup/webServer.
    # Mirror node_modules + the prebuilt webview dist(s) the `vite preview` lanes
    # serve (badge/lifecycle/projectSurface), discovered from the source checkout.
    live_studio = _live_repo(feature_id, "iriai-studio")
    dep_dirs = ["node_modules", "src/webviews/projectSurface/node_modules"]
    import glob as _glob
    for d in _glob.glob(f"{live_studio}/src/webviews/*/dist") + _glob.glob(
        f"{live_studio}/test/*/dist"
    ):
        dep_dirs.append(str(Path(d).relative_to(live_studio)))
    await sub.reuse_prebuilt_deps(
        checkout, live_studio, dep_dirs=tuple(dep_dirs), include_build=False)
    # Reconcile node_modules with the project's own package.json `file:` deps
    # (the clonefile'd node_modules predates them). This is the npm-equivalent
    # link the production webview build needs to resolve workspace packages
    # (e.g. @iriai-studio/markdown-sanitizer); it does NOT patch the product, so
    # a genuine build defect still fails the lane honestly.
    linked = await sub.link_file_deps(checkout)
    if linked:
        on_log(f"linked {len(linked)} file: workspace dep(s): {', '.join(linked)}")
    adapter = get_adapter(profile.adapter_id)
    instance = await adapter.provision(profile, Path(checkout))
    instance.substrate = sub

    try:
        lanes = configs or adapter.discover_configs(checkout)
        on_log(f"discovered {len(lanes)} native e2e lanes")

        # selection (informational provenance) for the bind slug
        conn = await asyncpg.connect(live_dsn)
        try:
            tp_raw = await _load_latest(conn, feature_id, f"test-plan-structured:{bind_slug}")
        finally:
            await conn.close()
        tp = TestPlan.model_validate(tp_raw["content"]) if tp_raw else TestPlan()
        ac_by_id = {a.id: a for a in tp.acceptance_criteria}
        summary.testable_ac_count = sum(
            1 for a in tp.acceptance_criteria
            if a.verification_method in {"e2e", "visual", "integration"})
        specs = bind_specs_from_scenarios(
            tp.test_scenarios, ac_by_id, adapter_id=profile.adapter_id,
            author_commit=studio_commit, source_commit=studio_commit)
        summary.spec_count = len(specs)

        # run every webview lane (each self-boots its harness)
        all_verdicts = []
        any_started = False
        for cfg in lanes:
            on_log(f"running lane {cfg} ...")
            nr = await adapter.run_native_config(instance, cfg, timeout=900)
            run = nr.result
            boot_error = lane_boot_error(run, nr.stderr_tail)
            lr = LaneResult(config=cfg, web_server_ok=run.web_server_ok,
                            passed=run.passed, failed=run.failed, flaky=run.flaky,
                            started=run.started, detail=run.summary(),
                            boot_error=boot_error)
            summary.lanes.append(lr)
            summary.passed += run.passed
            summary.failed += run.failed
            summary.flaky += run.flaky
            any_started = any_started or run.started
            verdicts = native_results_to_verdicts(
                [_spec_for_test(t, profile.adapter_id, studio_commit) for t in run.tests],
                run.tests, source_commit=studio_commit)
            for v in verdicts:
                await registry.put_verdict(v)
            all_verdicts.extend(verdicts)
            on_log(f"  {cfg}: {lr.detail}")

        # real-app (release-checkpoint, notarized DMG) — not_applicable until a DMG exists
        if include_real_app:
            ra = await adapter.run_real_app_e2e(instance)
            if not ra.applicable:
                summary.real_app = f"not_applicable: {ra.skip_reason}"
            else:
                summary.real_app = (f"ran: {ra.result.summary()}")
            on_log(f"real-app e2e: {summary.real_app}")

        label = f"group {checkpoint.group_idx}"
        # A lane whose globalSetup production build / webServer never came up is a
        # boot-smoke failure (NOT a genuinely-empty 0-spec lane). It blocks green
        # even if a lighter lane (chat via vite dev) booted fine.
        boot_failed = [lr for lr in summary.lanes if lr.boot_failed]
        summary.boot_smoke = "pass" if (any_started and not boot_failed) else "fail"
        summary.open_regressions = [v.spec_id for v in all_verdicts if v.status == "fail"]

        br = await bridge_findings(
            registry, [v for v in all_verdicts if v.failure_class == "regression"],
            {s.spec_id: s for s in specs}, checkpoint_label=label)
        summary.backlog_appended = len(br.appended)

        # Build/boot failures -> precise backlog finding (deduped) + a NON-deduped
        # operator page (the critical tier), and they keep latest-green from
        # advancing via the open_critical count below.
        if boot_failed:
            bf = await bridge_build_failures(
                registry,
                [LaneBuildFailure(lane=lr.config, error=lr.boot_error[:500])
                 for lr in boot_failed],
                checkpoint_label=label,
                file="src/webviews/projectSurface/vite.config.ts")
            summary.backlog_appended += len(bf.appended)
            await page_critical(
                registry, poster=poster, checkpoint_label=label,
                boot_smoke_failures=[
                    type("BS", (), {"surface": lr.config,
                                    "detail": lr.boot_error[:300]})()
                    for lr in boot_failed])
            on_log(f"  boot-smoke FAIL on {len(boot_failed)} lane(s); "
                   f"backlog+={len(bf.appended)} (paged)")

        preview_url = (f"iriai-build-v2 preview --feature {feature_id} "
                       f"--checkpoint {checkpoint.group_idx}")
        summary.preview_url = preview_url
        gp = green_pointer_for(checkpoint, boot_smoke=summary.boot_smoke,
                               open_critical_regressions=len(br.critical))
        if gp:
            await registry.put_green_pointer(gp)
            summary.green = True
        status = build_status(
            checkpoint=checkpoint,
            smokes=[type("S", (), {"status": lr.web_server_ok and "pass" or "fail",
                                   "surface": lr.config})() for lr in summary.lanes],
            verdicts=all_verdicts, green_pointer=gp, preview_url=preview_url)
        await emit_status(registry, status, poster=poster)
        summary.detail = (f"lanes={len(summary.lanes)} pass/fail/flaky="
                          f"{summary.passed}/{summary.failed}/{summary.flaky} "
                          f"real_app=[{summary.real_app}]")
        return summary
    finally:
        await adapter.teardown(instance)


async def _run_compose_pass(
    checkpoint: SealedCheckpoint,
    *,
    feature_id: str,
    registry: Any,
    profile: ProjectProfile,
    poster: Any,
    on_log=lambda m: None,
) -> PassSummary:
    """Compose-stack e2e pass (kaya): clone -> compose up -> per-service boot-smoke
    -> host unit-test verdicts -> status/green -> down -v. Resource-bounded by the
    compose preflight + single-stack mutex; uses the SAME green oracle as studio
    (boot-smoke pass AND no open critical regressions). The studio path is untouched.
    """
    import contextlib

    from .runner_loop import compose_preflight  # deferred — avoid an import cycle

    summary = PassSummary(group_idx=checkpoint.group_idx)
    label = f"group {checkpoint.group_idx}"
    commits = checkpoint.result_commits()
    repo_key = profile.repo_path or next(iter(commits), "")
    commit = commits.get(repo_key) or next(iter(commits.values()), "")
    slug = profile.compose_project_prefix or repo_key or "default"

    # Resource bound + single-stack mutex BEFORE standing anything up.
    pf = compose_preflight(project_prefix=(profile.compose_project_prefix or "e2e"))
    if not pf.ok:
        summary.boot_smoke = "fail"
        summary.detail = f"compose preflight refused: {pf.reason}"
        on_log(summary.detail)
        return summary  # nothing was brought up — no teardown, no false green

    sub = CloneSubstrate(role="track", mode="automated", persist=False)
    on_log(f"compose provisioning @ group {checkpoint.group_idx} ...")
    sources = {key: _live_repo(feature_id, key) for key in commits}
    if repo_key and repo_key not in sources:
        sources[repo_key] = _live_repo(feature_id, repo_key)
    checkouts = await sub.clone_checkpoint(sources=sources, commits=commits)
    checkout = checkouts[repo_key].checkout_dir

    adapter = get_adapter("compose")
    instance = None
    try:
        try:
            instance = await adapter.provision(
                profile, Path(checkout), substrate=sub,
                run_id=sub.run_id, project_slug=slug)
        except Exception as exc:  # noqa: BLE001 - a bring-up failure is an honest boot fail
            summary.boot_smoke = "fail"
            summary.detail = f"compose provision failed: {exc}"
            on_log(summary.detail)
            await page_critical(
                registry, poster=poster, checkpoint_label=label,
                boot_smoke_failures=[
                    type("BS", (), {"surface": "compose", "detail": str(exc)[:300]})()
                ])
            status = build_status(
                checkpoint=checkpoint, smokes=[], verdicts=[],
                green_pointer=None, preview_url="")
            await emit_status(registry, status, poster=poster)
            return summary

        smokes = await adapter.smoke(instance, profile)
        boot_failed = [s for s in smokes if s.status == "fail"]
        any_up = any(s.status == "pass" for s in smokes)
        summary.boot_smoke = "pass" if (any_up and not boot_failed) else "fail"
        for s in smokes:
            summary.lanes.append(LaneResult(
                config=s.surface,
                web_server_ok=(s.status == "pass"),
                started=(s.status == "pass"),
                detail=s.detail,
                boot_error="" if s.status != "fail" else s.detail))
        on_log(f"boot-smoke: {summary.boot_smoke} "
               f"({sum(1 for s in smokes if s.status == 'pass')}/{len(smokes)} up)")

        # Host unit tests only once the stack is up — a dead stack is a boot fail,
        # not a flood of misleading test failures.
        all_verdicts = []
        if not boot_failed:
            all_verdicts = await adapter.run(instance, [], source_commit=commit)
            for v in all_verdicts:
                if v.status == "pass":
                    summary.passed += 1
                elif v.status in ("fail", "error"):
                    summary.failed += 1
                await registry.put_verdict(v)
        summary.spec_count = len(all_verdicts)
        summary.open_regressions = [v.spec_id for v in all_verdicts if v.status == "fail"]

        br = await bridge_findings(
            registry,
            [v for v in all_verdicts if v.failure_class == "regression"],
            {}, checkpoint_label=label)
        summary.backlog_appended = len(br.appended)

        if boot_failed:
            await page_critical(
                registry, poster=poster, checkpoint_label=label,
                boot_smoke_failures=[
                    type("BS", (), {"surface": s.surface, "detail": s.detail[:300]})()
                    for s in boot_failed])
            on_log(f"  boot-smoke FAIL on {len(boot_failed)} service(s) (paged)")

        gp = green_pointer_for(
            checkpoint, boot_smoke=summary.boot_smoke,
            open_critical_regressions=len(br.critical))
        if gp:
            await registry.put_green_pointer(gp)
            summary.green = True
        status = build_status(
            checkpoint=checkpoint,
            smokes=[type("S", (), {"status": s.status, "surface": s.surface})()
                    for s in smokes],
            verdicts=all_verdicts, green_pointer=gp, preview_url="")
        await emit_status(registry, status, poster=poster)
        summary.detail = (f"compose boot={summary.boot_smoke} "
                          f"pass/fail={summary.passed}/{summary.failed}")
        return summary
    finally:
        if instance is not None:
            await adapter.teardown(instance)
        else:
            # provision raised after registering the project (or before): the
            # substrate down -v + rmtree still reaps any partial stack.
            with contextlib.suppress(Exception):
                await sub.teardown()
