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


# Keep the imported-but-shared fakes referenced so ruff sees intentional use.
_ = FakeSubstrate
