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
from .bridge import bridge_findings
from .checkpoint import SealedCheckpoint
from .models import E2ESpecRecord, ProjectProfile
from .status import CapturingPoster, build_status, emit_status, green_pointer_for
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
            lr = LaneResult(config=cfg, web_server_ok=run.web_server_ok,
                            passed=run.passed, failed=run.failed, flaky=run.flaky,
                            started=run.started, detail=run.summary())
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

        summary.boot_smoke = "pass" if any_started else "fail"
        summary.open_regressions = [v.spec_id for v in all_verdicts if v.status == "fail"]

        br = await bridge_findings(
            registry, [v for v in all_verdicts if v.failure_class == "regression"],
            {s.spec_id: s for s in specs}, checkpoint_label=f"group {checkpoint.group_idx}")
        summary.backlog_appended = len(br.appended)

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
