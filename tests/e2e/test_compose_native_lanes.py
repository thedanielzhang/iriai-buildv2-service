"""Item 11a (G1/G4): compose-path browser/Playwright lanes.

Profile-content gated (native_test_cmd + native_test_configs), NO env flag: the
arm lives only in the compose branch; the studio path is untouched. Covers the
"not_built" green semantics (kaya profile notes (4): browser lanes not yet built
is NOT "passed"), the lanes-ran path, lane harness-boot failure honesty, the
run_native_config env/argv contract (Auth0 key NAMES resolved from the injected
secret env file), parse_env_file, and the studio status-card/digest parity.
"""

from __future__ import annotations

import json

import pytest

from iriai_build_v2.workflows.develop.e2e import pass_ as pass_mod
from iriai_build_v2.workflows.develop.e2e.adapters import Instance
from iriai_build_v2.workflows.develop.e2e.adapters import compose as compose_mod
from iriai_build_v2.workflows.develop.e2e.adapters.browser import NativeRun
from iriai_build_v2.workflows.develop.e2e.adapters.compose import (
    ComposeAdapter,
    parse_env_file,
)
from iriai_build_v2.workflows.develop.e2e.adapters.playwright_report import (
    PwRunResult,
    PwTestResult,
)
from iriai_build_v2.workflows.develop.e2e.models import BootSmoke, E2EStatus, ProjectProfile
from iriai_build_v2.workflows.develop.e2e.pass_ import run_full_pass
from iriai_build_v2.workflows.develop.e2e.registry import BLOCKER_KEY
from iriai_build_v2.workflows.develop.e2e.status import (
    CapturingPoster,
    build_status,
    material_digest,
    status_blocks,
)

from .test_compose_pass import (
    FakeComposeAdapter,
    FakeRegistry,
    FakeSubstrate,
    _checkpoint,
    _wire,
)


def _lane_profile(configs: list[str]) -> ProjectProfile:
    return ProjectProfile(
        project_kind="full_stack", adapter_id="compose", repo_path="kaya-main",
        compose_project_prefix="kaya-e2e", compose_file="docker-compose.yaml",
        native_test_cmd="pnpm exec playwright test",
        native_test_configs=configs,
    )


def _pw_test(title: str, status: str, error: str = "") -> PwTestResult:
    return PwTestResult(title=title, file="e2e/spec.ts", status=status,
                        flaky=False, duration_ms=10, error=error)


def _native_run(config: str, result: PwRunResult, stderr_tail: str = "") -> NativeRun:
    return NativeRun(config=config, result=result, returncode=0,
                     stderr_tail=stderr_tail, report_path="")


class LaneFakeAdapter(FakeComposeAdapter):
    """FakeComposeAdapter + a scripted run_native_config (config -> NativeRun)."""

    def __init__(self, smokes, verdicts, lane_runs=None):
        super().__init__(smokes, verdicts)
        self.lane_runs = lane_runs or {}
        self.lane_calls: list[str] = []

    async def run_native_config(self, instance, config, **_kw):
        self.lane_calls.append(config)
        return self.lane_runs[config]


_SMOKES_UP = [BootSmoke(status="pass", surface="spend-client", probe_kind="http_get")]


# --------------------------------------------------------------------------- #
# not_built green semantics (G4)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_declared_cmd_no_configs_is_not_built_not_silent(monkeypatch):
    # kaya today: native_test_cmd set, configs EMPTY -> dormant lanes surface as
    # a distinguishable "not_built" (NOT silence, NOT "passed"); boot+host green
    # is unaffected (the profile-notes contract).
    adapter = LaneFakeAdapter(smokes=_SMOKES_UP, verdicts=[])
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(None)
    logs: list[str] = []

    out = await run_full_pass(_checkpoint(), feature_id="f", registry=reg,
                              live_dsn="x", profile=_lane_profile([]),
                              on_log=logs.append)

    assert out.browser_lanes == "not_built"
    assert out.boot_smoke == "pass" and out.green is True
    assert adapter.lane_calls == []  # nothing executed
    assert reg.status.browser_lanes == "not_built"  # carried on the status row
    assert any("not_built" in m for m in logs)  # loud, not silent
    assert "browser_lanes=not_built" in out.detail


@pytest.mark.asyncio
async def test_no_native_cmd_stays_blank_studio_unchanged(monkeypatch):
    # A compose profile WITHOUT native_test_cmd (no browser-lane product):
    # browser_lanes stays "" — status/digest byte-identical to pre-item-11.
    adapter = LaneFakeAdapter(smokes=_SMOKES_UP, verdicts=[])
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(None)
    profile = _lane_profile([])
    profile.native_test_cmd = ""

    out = await run_full_pass(_checkpoint(), feature_id="f", registry=reg,
                              live_dsn="x", profile=profile)

    assert out.browser_lanes == ""
    assert reg.status.browser_lanes == ""


# --------------------------------------------------------------------------- #
# lanes ran (G1)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_declared_lanes_run_and_count(monkeypatch):
    cfg = "playwright.e2e.config.ts"
    run = PwRunResult(
        tests=[_pw_test("t-pass", "passed"), _pw_test("t-fail", "failed", "boom")],
        passed=1, failed=1, started=True, web_server_ok=True)
    adapter = LaneFakeAdapter(smokes=_SMOKES_UP, verdicts=[],
                              lane_runs={cfg: _native_run(cfg, run)})
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(None)

    out = await run_full_pass(_checkpoint(), feature_id="f", registry=reg,
                              live_dsn="x", profile=_lane_profile([cfg]))

    assert adapter.lane_calls == [cfg]
    assert out.browser_lanes == "ran"
    assert out.passed == 1 and out.failed == 1
    assert any(lr.config == cfg for lr in out.lanes)
    # Lane verdicts flow into the registry / spec_count / open_regressions.
    assert f"{cfg}:t-pass" in [v.spec_id for v in reg.verdicts]
    assert f"{cfg}:t-fail" in out.open_regressions
    assert out.spec_count == 2
    assert reg.status.browser_lanes == "ran"
    # Non-critical lane regression: green stays (same oracle as studio).
    assert out.boot_smoke == "pass" and out.green is True


@pytest.mark.asyncio
async def test_lane_harness_boot_failure_blocks_green_and_pages(monkeypatch):
    # lane_boot_error filtering: a webServer/globalSetup failure is an honest
    # infra error — boot_smoke=fail, green blocked, bridged + paged. The
    # stack-boot paging (keyed on the PRE-lane value) must NOT fire.
    cfg = "playwright.e2e.config.ts"
    run = PwRunResult(global_errors=["webServer exited early"], web_server_ok=False,
                      started=False)
    adapter = LaneFakeAdapter(smokes=_SMOKES_UP, verdicts=[],
                              lane_runs={cfg: _native_run(cfg, run)})
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(None)
    poster = CapturingPoster()

    out = await run_full_pass(_checkpoint(), feature_id="f", registry=reg,
                              live_dsn="x", profile=_lane_profile([cfg]),
                              poster=poster)

    assert out.browser_lanes == "ran"
    assert out.boot_smoke == "fail" and out.green is False
    assert reg.green is None
    lane = next(lr for lr in out.lanes if lr.config == cfg)
    assert lane.boot_failed and "webServer exited early" in lane.boot_error
    # Paged on the LANE surface, not the misleading "no surfaces came up" page.
    blocker = reg.raw[BLOCKER_KEY]
    assert any(b.get("surface") == cfg for b in blocker["blockers"])
    assert all("no boot-smoke surfaces" not in (b.get("detail") or "")
               for b in blocker["blockers"])
    assert out.backlog_appended >= 1  # bridge_build_failures recorded it


@pytest.mark.asyncio
async def test_lanes_skipped_when_stack_boot_failed(monkeypatch):
    # Dead stack: declared lanes must NOT run against nothing.
    cfg = "playwright.e2e.config.ts"
    smokes = [BootSmoke(status="fail", surface="db", probe_kind="tcp_connect",
                        detail="connection refused")]
    adapter = LaneFakeAdapter(smokes=smokes, verdicts=[], lane_runs={})
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(None)

    out = await run_full_pass(_checkpoint(), feature_id="f", registry=reg,
                              live_dsn="x", profile=_lane_profile([cfg]))

    assert adapter.lane_calls == []
    assert out.boot_smoke == "fail" and out.browser_lanes == ""


# --------------------------------------------------------------------------- #
# ComposeAdapter.run_native_config — argv/env contract (real adapter, fake _run)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_native_config_argv_env_and_report(monkeypatch, tmp_path):
    secret = tmp_path / ".env.local"
    secret.write_text(
        "# kaya secret env\n"
        "E2E_USER='qa@kaya.test'\n"
        'E2E_PASS="hunter2"\n'
        "UNRELATED=zzz\n"
    )
    profile = _lane_profile(["playwright.e2e.config.ts"])
    profile.e2e_test_account_user_key = "E2E_USER"
    profile.e2e_test_account_pass_key = "E2E_PASS"
    instance = Instance(profile=profile, checkout_dir=tmp_path, surfaces=[])
    instance.secret_env_file = str(secret)

    captured: dict = {}

    async def fake_run(argv, *, cwd=None, timeout, env=None):
        captured["argv"] = argv
        captured["env"] = env
        report = {
            "suites": [{"title": "s", "specs": [{
                "title": "logs in", "file": "e2e/login.spec.ts",
                "tests": [{"results": [{"status": "passed", "duration": 5}]}],
            }]}],
            "errors": [],
        }
        with open(env["PLAYWRIGHT_JSON_OUTPUT_NAME"], "w") as fh:
            json.dump(report, fh)
        return 0, "", ""

    monkeypatch.setattr(compose_mod, "_run", fake_run)
    nr = await ComposeAdapter().run_native_config(
        instance, "playwright.e2e.config.ts")

    # argv: profile.native_test_cmd + --config + JSON reporter.
    assert captured["argv"][:4] == ["pnpm", "exec", "playwright", "test"]
    assert "--config=playwright.e2e.config.ts" in captured["argv"]
    assert "--reporter=json" in captured["argv"]
    # env: Auth0 key NAMES resolved from the injected secret env file (values
    # land in the subprocess env only); unrelated keys are not cherry-picked in.
    assert captured["env"]["E2E_USER"] == "qa@kaya.test"
    assert captured["env"]["E2E_PASS"] == "hunter2"
    assert captured["env"]["CI"] == "1"
    # report parsed through the existing Playwright machinery.
    assert nr.result.passed == 1 and nr.result.started is True


@pytest.mark.asyncio
async def test_run_native_config_no_report_is_honest_boot_fail(monkeypatch, tmp_path):
    instance = Instance(profile=_lane_profile(["c.ts"]), checkout_dir=tmp_path,
                        surfaces=[])

    async def fake_run(argv, *, cwd=None, timeout, env=None):
        return 1, "", "pnpm: command failed"

    monkeypatch.setattr(compose_mod, "_run", fake_run)
    nr = await ComposeAdapter().run_native_config(instance, "c.ts")
    assert nr.result.web_server_ok is False
    assert any("no JSON report" in e for e in nr.result.global_errors)
    assert pass_mod.lane_boot_error(nr.result, nr.stderr_tail)  # honest infra error


def test_parse_env_file(tmp_path):
    f = tmp_path / "env"
    f.write_text(
        "# comment\n"
        "\n"
        "PLAIN=value\n"
        "export EXPORTED=yes\n"
        "SQ='single quoted'\n"
        'DQ="double quoted"\n'
        "WITH_EQ=a=b=c\n"
        "  SPACED = padded \n"
        "noequals\n"
        "1BAD=skipped\n"
    )
    parsed = parse_env_file(f)
    assert parsed["PLAIN"] == "value"
    assert parsed["EXPORTED"] == "yes"
    assert parsed["SQ"] == "single quoted"
    assert parsed["DQ"] == "double quoted"
    assert parsed["WITH_EQ"] == "a=b=c"
    assert parsed["SPACED"] == "padded"
    assert "noequals" not in parsed and "1BAD" not in parsed
    assert parse_env_file(tmp_path / "missing") == {}


# --------------------------------------------------------------------------- #
# Studio parity (G4): blank browser_lanes leaves card + digest byte-identical
# --------------------------------------------------------------------------- #


def _status(**kw) -> E2EStatus:
    return E2EStatus(
        latest_checkpoint="group 7", latest_checkpoint_commit="abc",
        latest_green_checkpoint="group 6", boot_smoke="pass",
        passed=3, failed=1, flaky=0, open_regressions=["s1"],
        preview_url="p", **kw)


def test_material_digest_parity_when_blank():
    # Pre-item-11 payload recomputed inline: blank browser_lanes must hash
    # byte-for-byte the same (no studio card-digest churn / re-posts).
    import hashlib

    s = _status()
    old_payload = "|".join(str(x) for x in (
        s.latest_checkpoint_commit, s.boot_smoke, s.passed, s.failed, s.flaky,
        sorted(s.open_regressions), s.latest_green_checkpoint))
    assert material_digest(s) == hashlib.sha256(old_payload.encode()).hexdigest()
    # ...and a non-empty value DOES change the digest (material change).
    assert material_digest(_status(browser_lanes="not_built")) != material_digest(s)
    assert material_digest(_status(browser_lanes="ran")) != \
        material_digest(_status(browser_lanes="not_built"))


def test_status_blocks_only_show_browser_lanes_when_set():
    blank = status_blocks(_status())
    assert all("Browser lanes" not in f["text"] for f in blank[1]["fields"])
    shown = status_blocks(_status(browser_lanes="not_built"))
    assert any("Browser lanes:* not_built" in f["text"] for f in shown[1]["fields"])


def test_build_status_default_is_blank():
    class _CP:
        group_idx = 7

        @staticmethod
        def result_commits():
            return {"r": "abc"}

    s = build_status(checkpoint=_CP(), smokes=[], verdicts=[])
    assert s.browser_lanes == ""


# --------------------------------------------------------------------------- #
# AUTH-class lane failures (operator standing rule, 17:2x item 5):
# loud lane skip + operator-actions side-effect, dispatch continues — a broken
# e2e credential must NEVER quiesce (no boot_smoke fail, no page_critical, so
# CRITICAL_QUIESCE/BLOCKER_KEY never fire). Non-auth failures byte-for-byte.
# --------------------------------------------------------------------------- #

from iriai_build_v2.workflows.develop.e2e.checkpoint import (  # noqa: E402
    RepoCheckpoint,
    SealedCheckpoint,
)
from iriai_build_v2.workflows.develop.e2e.pass_ import (  # noqa: E402
    auth_broken_env_names,
    lane_auth_error,
)
from iriai_build_v2.workflows.develop.e2e.registry import (  # noqa: E402
    AUTH_BLOCKED_KEY,
)

_MISSING_ENV_ERR = (
    "Error: Missing Auth0 e2e environment variables: "
    "E2E_AUTH0_WRITE_COORDINATOR_EMAIL, E2E_AUTH0_REVIEWER_GATE_PASSWORD"
)
_SIGNIN_ERR = "Error: Auth0 identifier was not visible during Auth0 login."


def _ws_checkpoint(tmp_path) -> tuple:
    """Checkpoint whose repo_path carries the real develop-flow layout
    (<workspace>/.iriai/features/<feature>/repos/<repo>) so the e2e layer can
    locate <workspace>/.iriai/OPERATOR-ACTIONS.md."""
    ws = tmp_path / "kaya-main-ws"
    repo = ws / ".iriai" / "features" / "feat-5b280bb4" / "repos" / "kaya-main"
    repo.mkdir(parents=True)
    cp = SealedCheckpoint(
        feature_id="f", group_idx=5,
        repos=[RepoCheckpoint(repo_id="r", repo_path=str(repo),
                              result_commit="abc123")])
    return ws, cp


def _auth_failed_run(global_error: str = _MISSING_ENV_ERR) -> PwRunResult:
    # globalSetup threw before any test: no tests, harness never started.
    return PwRunResult(global_errors=[global_error], web_server_ok=False,
                       started=False)


@pytest.mark.asyncio
async def test_auth_failure_skips_lane_no_quiesce_writes_operator_actions(
    monkeypatch, tmp_path,
):
    cfg = "playwright.e2e.config.ts"
    adapter = LaneFakeAdapter(
        smokes=_SMOKES_UP, verdicts=[],
        lane_runs={cfg: _native_run(cfg, _auth_failed_run())})
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(None)
    poster = CapturingPoster()
    logs: list[str] = []
    ws, cp = _ws_checkpoint(tmp_path)

    out = await run_full_pass(cp, feature_id="f", registry=reg, live_dsn="x",
                              profile=_lane_profile([cfg]), poster=poster,
                              on_log=logs.append)

    # Lane is skipped + distinctly marked; NEVER a false green claim about
    # browser coverage (excluded like "not_built": green covers boot+host only).
    assert out.browser_lanes == "auth_blocked"
    lane = next(lr for lr in out.lanes if lr.config == cfg)
    assert lane.auth_blocked and lane.boot_error.startswith("auth_blocked:")
    # No quiesce machinery: boot_smoke stays pass, green pointer advances,
    # NOTHING was paged and NO e2e-blocker row exists (CRITICAL_QUIESCE keys
    # off BLOCKER_KEY — it must never fire for a broken credential).
    assert out.boot_smoke == "pass" and out.green is True
    assert reg.green is not None
    assert BLOCKER_KEY not in reg.raw
    assert poster.cards and all(
        "BLOCKER" not in text for _blocks, text in poster.cards)
    assert out.backlog_appended == 0  # not bridged as a build failure either
    # Loud WARN naming the broken credential env(s).
    assert any("AUTH-BLOCKED" in m and "E2E_AUTH0_WRITE_COORDINATOR_EMAIL" in m
               for m in logs)
    # OPERATOR-ACTIONS entry in the workspace names the broken env(s).
    actions = (ws / ".iriai" / "OPERATOR-ACTIONS.md").read_text()
    assert f"AUTH-BLOCKED ({cfg}) @ group 5" in actions
    assert "E2E_AUTH0_WRITE_COORDINATOR_EMAIL" in actions
    assert "E2E_AUTH0_REVIEWER_GATE_PASSWORD" in actions
    assert "[PENDING]" in actions
    # ...and the fallback registry row was NOT used.
    assert AUTH_BLOCKED_KEY not in reg.raw
    assert "browser_lanes=auth_blocked" in out.detail
    assert reg.status.browser_lanes == "auth_blocked"


@pytest.mark.asyncio
async def test_auth_failure_without_workspace_writes_durable_registry_row(
    monkeypatch,
):
    # Checkpoint repo paths without a .iriai component (workspace unreachable
    # from the e2e layer): the durable e2e-auth-blocked row is the fallback —
    # still NOT the BLOCKER_KEY row, which would feed CRITICAL_QUIESCE.
    cfg = "playwright.e2e.config.ts"
    adapter = LaneFakeAdapter(
        smokes=_SMOKES_UP, verdicts=[],
        lane_runs={cfg: _native_run(cfg, _auth_failed_run())})
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(None)
    logs: list[str] = []

    out = await run_full_pass(_checkpoint(), feature_id="f", registry=reg,
                              live_dsn="x", profile=_lane_profile([cfg]),
                              on_log=logs.append)

    assert out.browser_lanes == "auth_blocked"
    assert out.boot_smoke == "pass" and out.green is True
    assert BLOCKER_KEY not in reg.raw
    row = reg.raw[AUTH_BLOCKED_KEY]
    assert row["lanes"][0]["lane"] == cfg
    assert row["lanes"][0]["checkpoint"] == "group 5"
    assert "E2E_AUTH0_WRITE_COORDINATOR_EMAIL" in row["lanes"][0]["broken_env_names"]
    assert any("durable" in m and AUTH_BLOCKED_KEY in m for m in logs)


@pytest.mark.asyncio
async def test_auth_signin_failure_in_stderr_is_auth_classed(monkeypatch, tmp_path):
    # Sign-in flow failure surfaces in stderr (no JSON report at all) — still
    # auth-class; creds exist but don't work, so the profile's declared key
    # NAMES are what the operator entry names.
    cfg = "playwright.e2e.config.ts"
    run = PwRunResult(global_errors=["no JSON report produced (rc=1)"],
                      web_server_ok=False, started=False)
    adapter = LaneFakeAdapter(
        smokes=_SMOKES_UP, verdicts=[],
        lane_runs={cfg: _native_run(cfg, run, stderr_tail=_SIGNIN_ERR)})
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(None)
    profile = _lane_profile([cfg])
    profile.e2e_test_account_user_key = "E2E_USER"
    profile.e2e_test_account_pass_key = "E2E_PASS"
    ws, cp = _ws_checkpoint(tmp_path)

    out = await run_full_pass(cp, feature_id="f", registry=reg, live_dsn="x",
                              profile=profile)

    assert out.browser_lanes == "auth_blocked"
    assert out.boot_smoke == "pass" and out.green is True
    actions = (ws / ".iriai" / "OPERATOR-ACTIONS.md").read_text()
    assert "E2E_USER" in actions and "E2E_PASS" in actions


@pytest.mark.asyncio
async def test_mixed_lanes_auth_blocked_plus_healthy_lane_still_counts(
    monkeypatch, tmp_path,
):
    # A healthy lane still runs and bridges normally; the summary is marked
    # auth_blocked (partial browser coverage is NOT claimed as full "ran").
    bad, good = "playwright.auth.config.ts", "playwright.e2e.config.ts"
    good_run = PwRunResult(tests=[_pw_test("t-pass", "passed")], passed=1,
                           started=True, web_server_ok=True)
    adapter = LaneFakeAdapter(
        smokes=_SMOKES_UP, verdicts=[],
        lane_runs={bad: _native_run(bad, _auth_failed_run()),
                   good: _native_run(good, good_run)})
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(None)
    _ws, cp = _ws_checkpoint(tmp_path)

    out = await run_full_pass(cp, feature_id="f", registry=reg, live_dsn="x",
                              profile=_lane_profile([bad, good]))

    assert adapter.lane_calls == [bad, good]
    assert out.browser_lanes == "auth_blocked"
    assert out.passed == 1
    assert f"{good}:t-pass" in [v.spec_id for v in reg.verdicts]
    assert out.boot_smoke == "pass" and out.green is True


@pytest.mark.asyncio
async def test_non_auth_lane_failure_unchanged(monkeypatch, tmp_path):
    # Non-auth harness failure keeps today's behavior byte-for-byte:
    # boot_smoke fail, green blocked, bridged + paged on BLOCKER_KEY, and NO
    # auth side-effects (no operator-actions entry, no auth row).
    cfg = "playwright.e2e.config.ts"
    run = PwRunResult(global_errors=["webServer exited early"],
                      web_server_ok=False, started=False)
    adapter = LaneFakeAdapter(smokes=_SMOKES_UP, verdicts=[],
                              lane_runs={cfg: _native_run(cfg, run)})
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(None)
    poster = CapturingPoster()
    ws, cp = _ws_checkpoint(tmp_path)

    out = await run_full_pass(cp, feature_id="f", registry=reg, live_dsn="x",
                              profile=_lane_profile([cfg]), poster=poster)

    assert out.browser_lanes == "ran"
    assert out.boot_smoke == "fail" and out.green is False
    lane = next(lr for lr in out.lanes if lr.config == cfg)
    assert lane.boot_failed and not lane.auth_blocked
    assert any(b.get("surface") == cfg
               for b in reg.raw[BLOCKER_KEY]["blockers"])
    assert out.backlog_appended >= 1
    assert not (ws / ".iriai" / "OPERATOR-ACTIONS.md").exists()
    assert AUTH_BLOCKED_KEY not in reg.raw


@pytest.mark.asyncio
async def test_strict_green_honored_with_auth_blocked_lane(monkeypatch, tmp_path):
    # Flag interactions: an auth-blocked lane contributes NO verdicts, so it
    # never blocks STRICT_GREEN by itself — but a real test failure in another
    # lane still does (the strict oracle stays authoritative).
    monkeypatch.setenv("IRIAI_E2E_STRICT_GREEN", "1")
    bad, good = "playwright.auth.config.ts", "playwright.e2e.config.ts"
    failing = PwRunResult(tests=[_pw_test("t-fail", "failed", "boom")], failed=1,
                          started=True, web_server_ok=True)
    adapter = LaneFakeAdapter(
        smokes=_SMOKES_UP, verdicts=[],
        lane_runs={bad: _native_run(bad, _auth_failed_run()),
                   good: _native_run(good, failing)})
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(None)
    _ws, cp = _ws_checkpoint(tmp_path)

    out = await run_full_pass(cp, feature_id="f", registry=reg, live_dsn="x",
                              profile=_lane_profile([bad, good]))

    assert out.boot_smoke == "pass"  # auth lane never flips boot-smoke
    assert out.green is False and reg.green is None  # strict oracle blocked it
    assert BLOCKER_KEY not in reg.raw  # still nothing paged for auth

    # Auth-blocked alone under STRICT_GREEN: green (boot+host only) advances.
    adapter2 = LaneFakeAdapter(
        smokes=_SMOKES_UP, verdicts=[],
        lane_runs={bad: _native_run(bad, _auth_failed_run())})
    _wire(monkeypatch, adapter2)
    reg2 = FakeRegistry(None)
    out2 = await run_full_pass(cp, feature_id="f", registry=reg2, live_dsn="x",
                               profile=_lane_profile([bad]))
    assert out2.green is True and reg2.green is not None


@pytest.mark.asyncio
async def test_auth_operator_actions_entry_deduped_per_checkpoint_lane(
    monkeypatch, tmp_path,
):
    cfg = "playwright.e2e.config.ts"
    ws, cp = _ws_checkpoint(tmp_path)
    for _ in range(2):
        adapter = LaneFakeAdapter(
            smokes=_SMOKES_UP, verdicts=[],
            lane_runs={cfg: _native_run(cfg, _auth_failed_run())})
        _wire(monkeypatch, adapter)
        await run_full_pass(cp, feature_id="f", registry=FakeRegistry(None),
                            live_dsn="x", profile=_lane_profile([cfg]))
    actions = (ws / ".iriai" / "OPERATOR-ACTIONS.md").read_text()
    assert actions.count(f"AUTH-BLOCKED ({cfg}) @ group 5") == 1


# --------------------------------------------------------------------------- #
# classification unit tests — the harness's own error shapes (global-setup.ts)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text", [
    _MISSING_ENV_ERR,
    "Auth0 e2e role credentials must use distinct users for permission coverage.",
    "Auth0 password was not visible during Auth0 login.",
    "Auth0 profile check failed for write-tier coordinator.",
    "Auth0 profile did not include reviewer-gate user email.",
])
def test_lane_auth_error_matches_harness_shapes(text):
    run = PwRunResult(global_errors=[text], web_server_ok=False, started=False)
    assert lane_auth_error(run) == text
    # ...and via stderr when no report was produced at all.
    run2 = PwRunResult(global_errors=["no JSON report produced (rc=1)"],
                       web_server_ok=False, started=False)
    assert lane_auth_error(run2, stderr_tail=f"...\n{text}\n")


@pytest.mark.parametrize("text", [
    "webServer exited early",
    "TypeError: cannot read properties of undefined",
    "no JSON report produced (rc=1)",
    "",
])
def test_lane_auth_error_never_matches_product_failures(text):
    run = PwRunResult(global_errors=[text] if text else [],
                      web_server_ok=False, started=False)
    assert lane_auth_error(run) == ""


def test_auth_broken_env_names_extraction():
    assert auth_broken_env_names(_MISSING_ENV_ERR) == [
        "E2E_AUTH0_WRITE_COORDINATOR_EMAIL", "E2E_AUTH0_REVIEWER_GATE_PASSWORD"]
    # E2E_* tokens elsewhere in the error are picked up.
    assert auth_broken_env_names(
        "sign-in failed using E2E_AUTH0_READ_ONLY_VIEWER_EMAIL"
    ) == ["E2E_AUTH0_READ_ONLY_VIEWER_EMAIL"]
    # No names in the text -> the profile's declared key NAMES.
    profile = _lane_profile([])
    profile.e2e_test_account_user_key = "U_KEY"
    profile.e2e_test_account_pass_key = "P_KEY"
    assert auth_broken_env_names(
        "Auth0 profile check failed for write-tier coordinator.", profile,
    ) == ["U_KEY", "P_KEY"]
    assert auth_broken_env_names("Auth0 profile check failed.") == []


# Keep the imported-but-shared fakes referenced so ruff sees intentional use.
_ = FakeSubstrate
