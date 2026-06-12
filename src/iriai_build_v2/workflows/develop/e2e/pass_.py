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
import logging
import os
import re
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
from .triage import (
    bind_specs_from_scenarios,
    classify_verdicts,
    native_results_to_verdicts,
)

# Studio-default live-repo path template. Item-6: env-overridable via the SAME
# variable e2e_cmd.py already honors (IRIAI_E2E_LIVE_REPO_TMPL); the default is
# the previous literal byte-for-byte, so behavior is unchanged when unset.
LIVE_REPO_TMPL = os.environ.get(
    "IRIAI_E2E_LIVE_REPO_TMPL",
    "/Users/danielzhang/src/iriai/.iriai/features/"
    "visual-studio-code-frontend-for-project-workflow-manager-{feature}/repos/{repo}",
)

REQUIRE_PROFILE_ENV = "IRIAI_E2E_REQUIRE_PROFILE"

logger = logging.getLogger(__name__)


class E2EProfileRequiredError(RuntimeError):
    """Item-6: a required ProjectProfile is missing or structurally invalid.

    Raised (flag-gated, IRIAI_E2E_REQUIRE_PROFILE, default OFF) instead of
    silently falling back to the hardcoded iriai-studio electron default — a
    wrong-product e2e run or a confusing studio-path clone failure. The
    develop-side auto-infer flow is untouched: inference is the designed path
    there; this guard covers only the e2e pass/CLI resolution chain.
    """


def require_profile_enabled() -> bool:
    """Item-6 flag (default OFF = today's studio-default fallback chain)."""
    return os.environ.get(REQUIRE_PROFILE_ENV, "").strip().lower() in (
        "1", "true", "yes", "on",
    )


class E2EPassRefused(RuntimeError):
    """Item-11 G2: the compose preflight refused the pass — NOTHING ran.

    Raised instead of returning a normal PassSummary so callers can never
    mistake "the pass was refused" for "the pass ran": ``poll_once`` and the CLI
    ``--once`` path hold the cursor (the SAME sealed checkpoint is retried on
    the next poll once mutex/disk pressure clears), mirroring how the item-6
    typed profile error already propagates past the cursor write.

    UN-GATED bug-fix class (with regression tests): the raise lives ONLY in the
    compose-preflight branch of ``_run_compose_pass`` — the studio path can
    never hit it — and the prior behavior (silently consuming a sealed
    checkpoint that was never tested) is a defect, not a behavior to preserve.
    """

# ── Item-10 (R3 e2e feedback routing) flags — ALL default OFF = today ─────────
TRIAGE_CLASSIFY_ENV = "IRIAI_E2E_TRIAGE_CLASSIFY"
CRITICAL_BINDING_ENV = "IRIAI_E2E_CRITICAL_BINDING"
BOUNDARY_REPAIR_ENV = "IRIAI_E2E_BOUNDARY_REPAIR"
# ATTACH mode (operator ruling, kaya): the compose pass ATTACHES to the
# operator's already-running dev stack (single host, fixed ports) instead of
# clone + `compose up`. Default OFF = today's behavior byte-for-byte.
ATTACH_ENV = "IRIAI_E2E_ATTACH"


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def attach_enabled() -> bool:
    """ATTACH mode flag (IRIAI_E2E_ATTACH, default OFF = clone + compose up)."""
    return _env_on(ATTACH_ENV)


def triage_classify_enabled() -> bool:
    """Item-10 (a): wire triage.classify into both pass paths (default OFF)."""
    return _env_on(TRIAGE_CLASSIFY_ENV)


def critical_binding_enabled() -> bool:
    """Item-10 (b): bind spec/suite criticality (test-plan p0 scenarios on the
    studio path; profile.critical_service_names on the compose path). Default
    OFF = critical structurally always False, exactly today."""
    return _env_on(CRITICAL_BINDING_ENV)


def boundary_repair_enabled() -> bool:
    """Item-10 tier-ii: when ON, e2e regressions bridge to the enhancement
    backlog at severity='major' so the develop-side boundary repair wave can
    pick them up. Default OFF = severity='minor' end-of-DAG, exactly today."""
    return _env_on(BOUNDARY_REPAIR_ENV)


def _scenario_critical_for(sc: Any) -> tuple[bool, str]:
    """Test-plan p0 scenarios are critical (item-10 b, studio bind path)."""
    priority = str(getattr(sc, "priority", "") or "").strip().lower()
    if priority == "p0":
        return True, "test-plan p0 scenario"
    return False, ""


def _strict_green_counts(verdicts: list[Any]) -> dict[str, int]:
    """Item-10 (c): counts the strict green oracle blocks on (kwargs for
    ``green_pointer_for``; ignored there unless IRIAI_E2E_STRICT_GREEN is ON)."""
    return {
        "open_failures": sum(
            1 for v in verdicts
            if v.status == "fail"
            and v.failure_class not in ("flaky", "intended_change")
        ),
        "open_errors": sum(1 for v in verdicts if v.status == "error"),
        "open_skipped": sum(1 for v in verdicts if v.status == "skipped"),
    }


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
    # Operator standing rule (17:2x item 5): True iff the lane's OWN auth
    # bootstrap (Playwright globalSetup credentials / Auth0 sign-in) broke —
    # an environment/credential problem, NEVER a product regression. The lane
    # is loudly skipped; boot_smoke/page_critical are untouched.
    auth_blocked: bool = False

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
    # Item-11 G4 (flat): "" = studio/non-browser-lane product (unchanged);
    # "not_built" = profile declares native_test_cmd but no configs yet;
    # "ran" = declared compose browser lanes executed in this pass;
    # "skipped_attach" = ATTACH mode probed the running stack, lanes skipped;
    # "auth_blocked" = at least one lane's auth bootstrap (harness credentials)
    # broke — lane(s) loudly skipped, green covers boot+host ONLY (operator
    # standing rule: a broken e2e credential must never quiesce dispatch).
    browser_lanes: str = ""


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


# ── AUTH-class browser-lane failures (operator standing rule, 17:2x item 5) ──
# A broken e2e CREDENTIAL must NEVER quiesce the workflow: loud lane skip + an
# OPERATOR-ACTIONS entry naming the broken env(s); dispatch continues.
# IRIAI_E2E_CRITICAL_QUIESCE stays reserved for regressions in the feature's
# own surfaces. The marker strings below are the harness's OWN error shapes
# (kaya spend-client e2e/global-setup.ts): the missing-env throw, the Auth0
# sign-in locator failures, the distinct-users guard, and the post-login
# profile assertions. Matched case-insensitively against the lane's
# globalSetup errors + stderr tail — per-TEST failures are never auth-classed
# (a product auth regression must still page).
_AUTH_LANE_MARKERS = (
    "missing auth0 e2e environment variables",
    "auth0 e2e role credentials must use distinct users",
    "was not visible during auth0 login",
    "auth0 profile check failed",
    "auth0 profile did not include",
)

_AUTH_ENV_NAME_RE = re.compile(r"\bE2E_[A-Z0-9_]+\b")
_AUTH_MISSING_ENV_RE = re.compile(
    r"Missing Auth0 e2e environment variables:\s*([A-Za-z0-9_,\s]+)")


def lane_auth_error(run: Any, stderr_tail: str = "") -> str:
    """The AUTH-class failure text for a lane, or '' if not auth-class.

    Auth-class = the lane's own auth bootstrap (globalSetup credential read /
    Auth0 sign-in / profile assertion) failed — an environment problem, never a
    product regression. Only globalSetup-level evidence (report top-level
    errors + stderr) is consulted; failed TESTS keep today's regression path.
    """
    for text in [*run.global_errors, stderr_tail or ""]:
        low = text.lower()
        if any(m in low for m in _AUTH_LANE_MARKERS):
            return text.strip()
    return ""


def auth_broken_env_names(auth_error: str, profile: Any = None) -> list[str]:
    """NAME the broken credential env var(s) for the operator entry.

    Prefers the explicit names in the harness's missing-env throw; falls back
    to any E2E_* tokens in the error, then to the profile's declared
    test-account key names (creds present but sign-in/profile failed).
    """
    m = _AUTH_MISSING_ENV_RE.search(auth_error)
    if m:
        names = [t for t in re.split(r"[,\s]+", m.group(1).strip()) if t]
        if names:
            return names
    names = sorted(set(_AUTH_ENV_NAME_RE.findall(auth_error)))
    if names:
        return names
    return [
        k for k in (
            getattr(profile, "e2e_test_account_user_key", ""),
            getattr(profile, "e2e_test_account_pass_key", ""),
        ) if k
    ]


def _workspace_root_from_checkpoint(checkpoint: Any) -> Path | None:
    """Workspace root from the checkpoint's repo paths.

    The develop flow checks feature repos out under
    ``<workspace>/.iriai/features/<feature>/repos/<repo>`` — the same layout
    the implementation-phase OPERATOR-ACTIONS writer keys off
    (``<workspace>/.iriai``). Walk each repo_path for a ``.iriai`` component;
    its parent is the workspace root. None when no repo path carries one (the
    caller falls back to the durable registry row).
    """
    for r in getattr(checkpoint, "repos", []) or []:
        repo_path = getattr(r, "repo_path", "") or ""
        if not repo_path:
            continue
        parts = Path(repo_path).parts
        if ".iriai" in parts:
            idx = parts.index(".iriai")
            if idx > 0:
                return Path(*parts[:idx])
    return None


def append_auth_operator_action(
    checkpoint: Any, *, lane: str, auth_error: str, env_names: list[str],
) -> bool:
    """Best-effort [PENDING] entry in <workspace>/.iriai/OPERATOR-ACTIONS.md.

    Newest-first, same shape as the implementation-phase writer
    (``_append_operator_actions_entry``). Deduped per (checkpoint, lane) via
    the entry title so a re-run pass never spams the queue. Returns True when
    the entry exists (written now or already present); False when the
    workspace root is unreachable from the e2e layer — the caller records the
    durable ``e2e-auth-blocked`` registry row instead. Failures only WARN:
    this entry must never break the pass that is deliberately NOT quiescing.
    """
    try:
        root = _workspace_root_from_checkpoint(checkpoint)
        if root is None or not (root / ".iriai").is_dir():
            return False
        path = root / ".iriai" / "OPERATOR-ACTIONS.md"
        label = f"group {getattr(checkpoint, 'group_idx', '?')}"
        title = f"e2e browser lane AUTH-BLOCKED ({lane}) @ {label}"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if title in existing:
            return True  # already queued for this checkpoint+lane
        from datetime import datetime as _datetime

        ts = _datetime.now().strftime("%Y-%m-%d %H:%M")
        broken = ", ".join(env_names) or "(unknown — see error below)"
        entry = (
            f"## [PENDING] {ts} — {title}\n"
            "URGENCY: non-blocking (lane skipped LOUDLY; dispatch continues — "
            "operator standing rule: auth failures never quiesce)\n"
            f"COMMANDS: fix/rotate the e2e credential env(s) [{broken}] in the "
            "harness secret env file, then re-run the e2e pass for this "
            "checkpoint\n"
            f"WHY: browser lane {lane} auth bootstrap failed: "
            f"{auth_error[:400]}\n"
            f"VERIFY: the lane runs (browser_lanes='ran', no auth_blocked "
            f"lane) on the next e2e pass at {label} or later\n"
            "RESOLVED:\n\n"
        )
        path.write_text(entry + existing, encoding="utf-8")
        return True
    except Exception as exc:  # noqa: BLE001 — never break the non-quiescing pass
        logger.warning(
            "OPERATOR-ACTIONS auth-blocked entry failed for lane %r: %s",
            lane, exc,
        )
        return False


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

    resolved_profile = profile or (await registry.get_profile() if registry else None)
    if require_profile_enabled():
        # Item-6 fail-fast: a missing or structurally-invalid profile is a
        # loud typed error, NEVER the silent studio-default fallback.
        if resolved_profile is None:
            raise E2EProfileRequiredError(
                f"no project-profile artifact for feature {feature_id!r} "
                "(and no explicit profile was passed). Author and persist the "
                "ProjectProfile (P6) or pass --profile-json; unset "
                f"{REQUIRE_PROFILE_ENV} to allow the iriai-studio default."
            )
        alignment_errors = resolved_profile.alignment_errors()
        if alignment_errors:
            raise E2EProfileRequiredError(
                f"project profile for feature {feature_id!r} is structurally "
                f"invalid: {'; '.join(alignment_errors)}. Repair the persisted "
                "profile (or the --profile-json override) before re-running."
            )
    profile = resolved_profile or _default_profile()
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
            author_commit=studio_commit, source_commit=studio_commit,
            critical_for=(
                _scenario_critical_for if critical_binding_enabled() else None
            ))
        summary.spec_count = len(specs)
        specs_by_id = {s.spec_id: s for s in specs}

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
            if triage_classify_enabled():
                # Item-10 (a): principled failure_class via triage.classify —
                # plain fails with unchanged/unbound assertions become
                # 'regression' instead of the invisible ''.
                verdicts = classify_verdicts(verdicts, specs_by_id, ac_by_id)
                on_log(
                    f"  triage.classify: {sum(1 for v in verdicts if v.failure_class == 'regression')}"
                    f" regression(s) in lane {cfg}")
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
            {s.spec_id: s for s in specs}, checkpoint_label=label,
            # Item-10 tier-ii: with boundary repair ON, regressions are major
            # (picked up by the next-group-boundary repair wave); OFF = minor
            # end-of-DAG, exactly today.
            severity="major" if boundary_repair_enabled() else "minor")
        summary.backlog_appended = len(br.appended)
        if br.critical:
            # Item-10 tier-i: critical regressions PAGE (non-deduped) — they
            # never reach the backlog. Structurally unreachable until
            # criticality is bound (IRIAI_E2E_CRITICAL_BINDING).
            await page_critical(
                registry, poster=poster, checkpoint_label=label,
                critical_regressions=br.critical)
            on_log(f"  paged {len(br.critical)} critical regression(s)")

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
                               open_critical_regressions=len(br.critical),
                               **_strict_green_counts(all_verdicts))
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


async def _run_attach_pass(
    checkpoint: SealedCheckpoint,
    *,
    registry: Any,
    profile: ProjectProfile,
    poster: Any,
    on_log,
    attach_project: str,
) -> PassSummary:
    """ATTACH-mode boot-smoke (IRIAI_E2E_ATTACH): probe the operator's RUNNING
    stack instead of clone + compose-up.

    The kaya operator ruling: the running dev stack (fixed ports, single host)
    IS the e2e target — standing up a second stack would collide on ports, so
    the pass probes the profile's 13 surfaces in place. Host unit tests and
    browser lanes are LOUDLY skipped (nothing is checked out); the green
    pointer therefore covers boot-smoke only, with the same
    ``green_pointer_for`` oracle as the provisioned path. Returning a summary
    (never raising) consumes the checkpoint cursor exactly once, like any
    completed pass. Nothing is provisioned, so there is nothing to tear down.
    """
    from .adapters import Instance
    from .adapters.compose import build_surfaces

    summary = PassSummary(group_idx=checkpoint.group_idx)
    label = f"group {checkpoint.group_idx}"
    adapter = get_adapter("compose")
    on_log(
        f"ATTACH mode: probing running stack '{attach_project}' "
        "(no compose up; host tests + browser lanes skipped)"
    )
    instance = Instance(
        profile=profile,
        checkout_dir=Path("."),
        surfaces=build_surfaces(profile),
    )
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
    if profile.native_test_cmd:
        summary.browser_lanes = "skipped_attach"
        on_log("browser lanes: skipped_attach (ATTACH mode probes only — "
               "green covers boot-smoke ONLY)")

    if summary.boot_smoke == "fail":
        failures = boot_failed or [
            type("BS", (), {"surface": "attach",
                            "detail": "no boot-smoke surfaces came up "
                                      "(check profile.service_probe_targets)"})()
        ]
        await page_critical(
            registry, poster=poster, checkpoint_label=label,
            boot_smoke_failures=[
                type("BS", (), {"surface": s.surface,
                                "detail": (s.detail or "")[:300]})()
                for s in failures])
        on_log(f"  boot-smoke FAIL on {len(failures)} surface(s) (paged)")

    gp = green_pointer_for(
        checkpoint, boot_smoke=summary.boot_smoke,
        open_critical_regressions=0,
        **_strict_green_counts([]))
    if gp:
        await registry.put_green_pointer(gp)
        summary.green = True
    status = build_status(
        checkpoint=checkpoint,
        smokes=[type("S", (), {"status": s.status, "surface": s.surface})()
                for s in smokes],
        verdicts=[], green_pointer=gp, preview_url="",
        browser_lanes=summary.browser_lanes)
    await emit_status(registry, status, poster=poster)
    summary.detail = f"attach boot={summary.boot_smoke} pass/fail=0/0"
    return summary


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
    # result_commits()/clone_checkpoint key by repo-dir BASENAME; profile.repo_path
    # may be a multi-segment path (e.g. "services/spend-client") — match on the
    # basename so checkouts[repo_key] never KeyErrors.
    repo_key = (Path(profile.repo_path).name if profile.repo_path else "") or next(
        iter(commits), "")
    commit = commits.get(repo_key) or next(iter(commits.values()), "")
    slug = profile.compose_project_prefix or repo_key or "default"

    # Resource bound + single-stack mutex BEFORE standing anything up.
    # ATTACH mode (IRIAI_E2E_ATTACH): the profile's exact project name is the
    # attach TARGET — exempt from the mutex; other prefix-matches still refuse.
    attach_project = (profile.compose_project_prefix or "") if attach_enabled() else ""
    pf = compose_preflight(
        project_prefix=(profile.compose_project_prefix or "e2e"),
        attach_project=attach_project,
    )
    if not pf.ok:
        # Item-11 G2/G3: a refusal is LOUD (durable blocker + status row) and
        # NON-ADVANCING (typed raise -> callers hold the cursor; the same sealed
        # checkpoint is retried next poll). Nothing was brought up — no teardown,
        # no false green, and the checkpoint is NOT consumed.
        detail = f"compose preflight refused: {pf.reason}"
        on_log(detail + " (cursor held; will retry this checkpoint)")
        if registry is not None:
            from .registry import BLOCKER_KEY  # deferred — mirror local imports

            # Page once per (checkpoint, reason): the 10s poll loop retries the
            # SAME checkpoint, so dedupe against the existing blocker row to
            # avoid page-spam while still re-paging on a NEW reason/checkpoint.
            prior = await registry.get_raw(BLOCKER_KEY) or {}
            already_paged = prior.get("checkpoint") == label and any(
                b.get("kind") == "boot_smoke"
                and b.get("surface") == "compose-preflight"
                and b.get("detail") == detail
                for b in prior.get("blockers", [])
            )
            if not already_paged:
                await page_critical(
                    registry, poster=poster, checkpoint_label=label,
                    boot_smoke_failures=[
                        type("BS", (), {"surface": "compose-preflight",
                                        "detail": detail})()
                    ])
            # Durable e2e-status row (card itself is digest-deduped).
            status = build_status(
                checkpoint=checkpoint, smokes=[], verdicts=[],
                green_pointer=None, preview_url="")
            await emit_status(registry, status, poster=poster)
        raise E2EPassRefused(detail)

    if attach_project:
        if not pf.attach_target_up:
            # The operator's stack is expected to be running in attach mode —
            # a down target is an environment surprise, never a license to
            # stand up our own stack on its fixed ports. Loud, cursor held.
            detail = (
                f"ATTACH mode: target compose project '{attach_project}' is "
                "not running (expected the operator's live stack)"
            )
            on_log(detail + " (cursor held; will retry this checkpoint)")
            raise E2EPassRefused(detail)
        return await _run_attach_pass(
            checkpoint,
            registry=registry,
            profile=profile,
            poster=poster,
            on_log=on_log,
            attach_project=attach_project,
        )

    sub = CloneSubstrate(role="track", mode="automated", persist=False)
    on_log(f"compose provisioning @ group {checkpoint.group_idx} ...")
    # Use each repo's ACTUAL on-disk path from the checkpoint (NOT the studio
    # live-repo template, which is iriai-studio-specific) — falling back to the
    # template only if a checkpoint somehow lacks the path.
    sources = {r.repo_key: (r.repo_path or _live_repo(feature_id, r.repo_key))
               for r in checkpoint.repos}
    checkouts = await sub.clone_checkpoint(sources=sources, commits=commits)
    if repo_key not in checkouts:  # defensive: fall back to the first cloned repo
        repo_key = next(iter(checkouts), repo_key)
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

        # Host unit tests only once the stack is up — a dead stack (or a profile
        # with NO surfaces configured) is a boot fail, not a flood of misleading
        # test failures.
        all_verdicts = []
        if summary.boot_smoke == "pass":
            critical_for = None
            if critical_binding_enabled():
                # Item-10 (b): per-suite criticality from
                # profile.critical_service_names (empty list = no-op).
                from .adapters.compose import compose_critical_for

                critical_for = compose_critical_for(profile)
            # Pass the kwarg only when bound — keeps duck-typed adapters
            # without the new parameter working (flag OFF = today's call).
            if critical_for is not None:
                all_verdicts = await adapter.run(
                    instance, [], source_commit=commit, critical_for=critical_for)
            else:
                all_verdicts = await adapter.run(instance, [], source_commit=commit)
            if triage_classify_enabled():
                # Item-10 (a): same principled classifier as the studio path
                # (compose JUnit fails are already 'regression'; this keeps the
                # green-wash guard authoritative once specs are bound).
                all_verdicts = classify_verdicts(all_verdicts, {}, {})
            for v in all_verdicts:
                if v.status == "pass":
                    summary.passed += 1
                elif v.status in ("fail", "error"):
                    summary.failed += 1
                await registry.put_verdict(v)

        # Item 11a (G1/G4): declared browser/Playwright lanes against the LIVE
        # stack. Activation is double-gated by PROFILE CONTENT ONLY (no env
        # flag): native_test_cmd set AND native_test_configs non-empty. The arm
        # lives only in this compose branch — the studio path is untouched — and
        # kaya's profile keeps the lanes dormant (configs=[]) until the STEP-13
        # harness authors a playwright config and the profile is re-persisted.
        stack_boot_smoke = summary.boot_smoke  # pre-lane value — keeps the
        # stack-boot paging message below honest about WHAT failed.
        lane_boot_failed: list[LaneResult] = []
        if profile.native_test_cmd:
            if not profile.native_test_configs:
                # G4 green semantics (kaya profile notes (4)): "browser lanes
                # not yet built" is NOT "passed" — a loud, distinguishable
                # status, never silence. Boot+host green stays green while the
                # lanes are profile-gated (the documented contract).
                summary.browser_lanes = "not_built"
                on_log(
                    "browser lanes: not_built (native_test_cmd declared, no "
                    "configs yet — green covers boot-smoke + host tests ONLY)")
            elif summary.boot_smoke == "pass":
                from .adapters.compose import run_to_verdicts

                summary.browser_lanes = "ran"
                for cfg in profile.native_test_configs:
                    on_log(f"running browser lane {cfg} ...")
                    nr = await adapter.run_native_config(instance, cfg)
                    run = nr.result
                    boot_error = lane_boot_error(run, nr.stderr_tail)
                    auth_error = (
                        lane_auth_error(run, nr.stderr_tail) if boot_error else ""
                    )
                    if auth_error:
                        # Operator standing rule (17:2x item 5): the lane's OWN
                        # auth bootstrap broke (credential env / Auth0 sign-in)
                        # — an ENVIRONMENT failure, never a product regression.
                        # Loud skip: no boot_smoke fail, no bridge, no
                        # page_critical (so CRITICAL_QUIESCE/page_critical can
                        # never fire for it); dispatch continues. Green keeps
                        # covering boot+host ONLY — browser_lanes=auth_blocked
                        # is the honest non-coverage marker (same exclusion
                        # shape as "not_built").
                        broken = auth_broken_env_names(auth_error, profile)
                        lr = LaneResult(
                            config=cfg, web_server_ok=run.web_server_ok,
                            started=run.started,
                            detail=f"auth_blocked: {auth_error[:200]}",
                            boot_error=f"auth_blocked: {auth_error[:500]}",
                            auth_blocked=True)
                        summary.lanes.append(lr)
                        summary.browser_lanes = "auth_blocked"
                        msg = (
                            f"  WARNING: browser lane {cfg} AUTH-BLOCKED — "
                            f"lane SKIPPED, dispatch continues (broken "
                            f"credential env(s): "
                            f"{', '.join(broken) or 'unknown'}; harness auth "
                            f"bootstrap failure, NOT a product regression): "
                            f"{auth_error[:300]}")
                        logger.warning(msg.strip())
                        on_log(msg)
                        recorded = append_auth_operator_action(
                            checkpoint, lane=cfg, auth_error=auth_error,
                            env_names=broken)
                        if recorded:
                            on_log(
                                "  auth-blocked entry appended to "
                                "<workspace>/.iriai/OPERATOR-ACTIONS.md")
                        else:
                            # Workspace unreachable from the e2e layer: the
                            # durable registry row (NOT BLOCKER_KEY — that
                            # would feed CRITICAL_QUIESCE) is the fallback.
                            from .registry import AUTH_BLOCKED_KEY

                            prior = await registry.get_raw(AUTH_BLOCKED_KEY) or {}
                            rows = [
                                r for r in prior.get("lanes", [])
                                if not (r.get("lane") == cfg
                                        and r.get("checkpoint") == label)
                            ]
                            rows.append({
                                "checkpoint": label, "lane": cfg,
                                "broken_env_names": broken,
                                "error": auth_error[:500],
                            })
                            await registry.put_raw(
                                AUTH_BLOCKED_KEY, {"lanes": rows})
                            on_log(
                                "  workspace OPERATOR-ACTIONS.md unreachable "
                                f"— durable {AUTH_BLOCKED_KEY!r} row recorded "
                                "instead")
                        continue
                    lr = LaneResult(
                        config=cfg, web_server_ok=run.web_server_ok,
                        passed=run.passed, failed=run.failed, flaky=run.flaky,
                        started=run.started, detail=run.summary(),
                        boot_error=boot_error)
                    summary.lanes.append(lr)
                    summary.passed += run.passed
                    summary.failed += run.failed
                    summary.flaky += run.flaky
                    if lr.boot_failed:
                        lane_boot_failed.append(lr)
                    verdicts = run_to_verdicts(run, suite=cfg, source_commit=commit)
                    for v in verdicts:
                        await registry.put_verdict(v)
                    all_verdicts.extend(verdicts)
                    on_log(f"  {cfg}: {lr.detail}")
                if lane_boot_failed:
                    # Mirrors the studio rule: a lane whose harness never came
                    # up is a boot-smoke failure (honest infra error, never a
                    # zero-test green) — blocks green, bridged + paged below.
                    summary.boot_smoke = "fail"

        summary.spec_count = len(all_verdicts)
        summary.open_regressions = [v.spec_id for v in all_verdicts if v.status == "fail"]

        br = await bridge_findings(
            registry,
            [v for v in all_verdicts if v.failure_class == "regression"],
            {}, checkpoint_label=label,
            severity="major" if boundary_repair_enabled() else "minor")
        summary.backlog_appended = len(br.appended)
        if br.critical:
            # Item-10 tier-i: critical regressions PAGE (see studio path note).
            await page_critical(
                registry, poster=poster, checkpoint_label=label,
                critical_regressions=br.critical)
            on_log(f"  paged {len(br.critical)} critical regression(s)")

        if lane_boot_failed:
            bf = await bridge_build_failures(
                registry,
                [LaneBuildFailure(lane=lr.config, error=lr.boot_error[:500])
                 for lr in lane_boot_failed],
                checkpoint_label=label)
            summary.backlog_appended += len(bf.appended)
            await page_critical(
                registry, poster=poster, checkpoint_label=label,
                boot_smoke_failures=[
                    type("BS", (), {"surface": lr.config,
                                    "detail": lr.boot_error[:300]})()
                    for lr in lane_boot_failed])
            on_log(f"  browser-lane boot FAIL on {len(lane_boot_failed)} "
                   f"lane(s); backlog+={len(bf.appended)} (paged)")

        if stack_boot_smoke == "fail":
            # Page on any boot fail — failed services OR an empty-surfaces profile
            # (no service came up), so a misconfig is a loud honest failure.
            failures = boot_failed or [
                type("BS", (), {"surface": "compose",
                                "detail": "no boot-smoke surfaces came up "
                                          "(check profile.service_probe_targets)"})()
            ]
            await page_critical(
                registry, poster=poster, checkpoint_label=label,
                boot_smoke_failures=[
                    type("BS", (), {"surface": s.surface,
                                    "detail": (s.detail or "")[:300]})()
                    for s in failures])
            on_log(f"  boot-smoke FAIL on {len(failures)} surface(s) (paged)")

        gp = green_pointer_for(
            checkpoint, boot_smoke=summary.boot_smoke,
            open_critical_regressions=len(br.critical),
            **_strict_green_counts(all_verdicts))
        if gp:
            await registry.put_green_pointer(gp)
            summary.green = True
        status = build_status(
            checkpoint=checkpoint,
            smokes=[type("S", (), {"status": s.status, "surface": s.surface})()
                    for s in smokes],
            verdicts=all_verdicts, green_pointer=gp, preview_url="",
            browser_lanes=summary.browser_lanes)
        await emit_status(registry, status, poster=poster)
        summary.detail = (f"compose boot={summary.boot_smoke} "
                          f"pass/fail={summary.passed}/{summary.failed}")
        if summary.browser_lanes:
            summary.detail += f" browser_lanes={summary.browser_lanes}"
        return summary
    finally:
        if instance is not None:
            await adapter.teardown(instance)
        else:
            # provision raised after registering the project (or before): the
            # substrate down -v + rmtree still reaps any partial stack.
            with contextlib.suppress(Exception):
                await sub.teardown()
