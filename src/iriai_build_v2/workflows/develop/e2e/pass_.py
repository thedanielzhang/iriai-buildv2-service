"""The integrated e2e pass: one invocation chaining every stage.

provision -> seed -> boot-smoke -> select -> bind/author -> native replay ->
triage -> bridge -> status rollup + green pointer + cursor. Read-only against the
sealed checkpoint; all writes go to the scratch registry.

This is what ``iriai-build-v2 e2e --once`` runs (``do_pass=True``). It runs the
project's OWN native e2e suites at the checkpoint (the browser adapter's
discovered ``playwright.config.*``), so "run e2e tests on the feature at group N"
is a single command.
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
from .models import ProjectProfile
from .status import CapturingPoster, build_status, emit_status, green_pointer_for
from .substrate import CloneSubstrate
from .triage import bind_specs_from_scenarios, native_results_to_verdicts

LIVE_REPO_TMPL = (
    "/Users/danielzhang/src/iriai/.iriai/features/"
    "visual-studio-code-frontend-for-project-workflow-manager-{feature}/repos/{repo}"
)


@dataclass
class PassSummary:
    group_idx: int = -1
    boot_smoke: str = ""
    passed: int = 0
    failed: int = 0
    flaky: int = 0
    spec_count: int = 0
    testable_ac_count: int = 0
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


async def run_full_pass(
    checkpoint: SealedCheckpoint,
    *,
    feature_id: str,
    registry: Any,
    live_dsn: str,
    profile: ProjectProfile | None = None,
    config: str = "playwright.config.chat.ts",
    bind_slug: str = "chat-sidepane-shell",
    poster: Any = None,
    on_log=lambda m: None,
) -> PassSummary:
    """Run one integrated e2e pass at ``checkpoint`` (the project's native suite)."""
    poster = poster or CapturingPoster()
    summary = PassSummary(group_idx=checkpoint.group_idx)
    commits = checkpoint.result_commits()
    studio_commit = commits.get("iriai-studio") or next(iter(commits.values()), "")

    # 1) provision: isolated clone + reuse the checkpoint's deps + adapter
    profile = profile or (await registry.get_profile() if registry else None) or _default_profile()
    sub = CloneSubstrate(role="track", mode="automated", persist=False)
    on_log(f"provisioning {config} harness @ group {checkpoint.group_idx} ...")
    checkouts = await sub.clone_checkpoint(
        sources={"iriai-studio": _live_repo(feature_id, "iriai-studio")},
        commits={"iriai-studio": studio_commit},
    )
    checkout = checkouts["iriai-studio"].checkout_dir
    await sub.reuse_prebuilt_deps(
        checkout, _live_repo(feature_id, "iriai-studio"),
        dep_dirs=("node_modules", "src/webviews/projectSurface/node_modules"),
        include_build=False,
    )
    adapter = get_adapter(profile.adapter_id)
    instance = await adapter.provision(profile, Path(checkout))
    instance.substrate = sub

    try:
        # 2) boot-smoke (per-surface)
        on_log("boot-smoke ...")
        smokes = await adapter.smoke(instance, profile, config=config)
        summary.boot_smoke = smokes[0].status if smokes else "not_applicable"

        # 3) select testable ACs at this checkpoint (informational + provenance)
        conn = await asyncpg.connect(live_dsn)
        try:
            tp_raw = await _load_latest(conn, feature_id, f"test-plan-structured:{bind_slug}")
            done_rows = await conn.fetch(
                """SELECT DISTINCT cov.task_id FROM merge_queue_task_coverage cov
                   JOIN merge_queue_items i ON i.id=cov.queue_item_id
                   WHERE cov.feature_id=$1 AND i.status='done' AND i.group_idx<=$2""",
                feature_id, checkpoint.group_idx)
        finally:
            await conn.close()
        tp = TestPlan.model_validate(tp_raw["content"]) if tp_raw else TestPlan()
        ac_by_id = {a.id: a for a in tp.acceptance_criteria}
        testable = [a for a in tp.acceptance_criteria
                    if a.verification_method in {"e2e", "visual", "integration"}]
        summary.testable_ac_count = len(testable)

        # 4) bind native scenarios -> specs with assertion digests + author_commit
        specs = bind_specs_from_scenarios(
            tp.test_scenarios, ac_by_id, adapter_id=profile.adapter_id,
            author_commit=studio_commit, source_commit=studio_commit,
            test_plan_digest=(tp_raw or {}).get("meta", {}).get("digest", "")
            if tp_raw else "")
        summary.spec_count = len(specs)

        # 5) native replay (the project's own e2e suite)
        on_log(f"replaying native suite {config} ...")
        nr = await adapter.run_native_config(instance, config, timeout=900)
        run = nr.result
        summary.passed, summary.failed, summary.flaky = (
            run.passed, run.failed, run.flaky)

        # 6) verdicts per spec (status + evidence); reds are recorded (cross-
        #    checkpoint regression classification is the two-checkpoint path)
        verdicts = native_results_to_verdicts(
            [_spec_for_test(t, profile.adapter_id, studio_commit) for t in run.tests],
            run.tests, source_commit=studio_commit)
        for v, t in zip(verdicts, run.tests):
            await registry.put_verdict(v)
        summary.open_regressions = [v.spec_id for v in verdicts if v.status == "fail"]

        # 7) bridge: confirmed regressions -> scratch backlog (none auto-confirmed
        #    at a single checkpoint without prior-commit replay; criticals page)
        br = await bridge_findings(
            registry, [v for v in verdicts if v.failure_class == "regression"],
            {s.spec_id: s for s in specs}, checkpoint_label=f"group {checkpoint.group_idx}")
        summary.backlog_appended = len(br.appended)

        # 8) status rollup + green pointer + emit
        preview_url = (f"iriai-build-v2 preview --feature {feature_id} "
                       f"--checkpoint {checkpoint.group_idx}")
        summary.preview_url = preview_url
        gp = green_pointer_for(checkpoint, boot_smoke=summary.boot_smoke,
                               open_critical_regressions=len(br.critical))
        if gp:
            await registry.put_green_pointer(gp)
            summary.green = True
        status = build_status(checkpoint=checkpoint, smokes=smokes, verdicts=verdicts,
                              green_pointer=gp, preview_url=preview_url)
        await emit_status(registry, status, poster=poster)
        summary.detail = (f"smoke={summary.boot_smoke} pass/fail/flaky="
                          f"{summary.passed}/{summary.failed}/{summary.flaky} "
                          f"specs={summary.spec_count} testableACs={summary.testable_ac_count}")
        return summary
    finally:
        await adapter.teardown(instance)


def _spec_for_test(t, adapter_id, commit):
    from .models import E2ESpecRecord
    return E2ESpecRecord(spec_id=t.title, title=t.title, adapter_id=adapter_id,
                         author_commit=commit, source_commit=commit)


def _default_profile() -> ProjectProfile:
    return ProjectProfile(
        project_kind="electron", repo_path="iriai-studio", adapter_id="browser",
        native_test_cmd="npx playwright test",
        native_test_configs=["playwright.config.chat.ts"],
        ready_probe_kind="http_get", ready_probe_target="http://127.0.0.1:8787/healthz")
