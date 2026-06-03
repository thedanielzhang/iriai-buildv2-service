"""Lane boot-failure classification in the integrated pass.

A real build/webServer failure must be flagged (-> finding + page + blocks
green); a genuinely-empty lane (0 spec files, clean report) must NOT be flagged.
"""

from __future__ import annotations

from iriai_build_v2.workflows.develop.e2e.adapters.playwright_report import (
    PwRunResult,
)
from iriai_build_v2.workflows.develop.e2e.pass_ import LaneResult, lane_boot_error


def test_webserver_failure_is_boot_failure():
    lr = LaneResult(config="lifecycle.ts", web_server_ok=False, started=False,
                    boot_error="Timed out waiting 60000ms from config.webServer")
    assert lr.boot_failed


def test_globalsetup_build_error_is_boot_failure():
    lr = LaneResult(config="badge.ts", web_server_ok=True, started=False,
                    boot_error="projectSurface bundle rebuild failed (exit 1)")
    assert lr.boot_failed


def test_genuinely_empty_lane_is_not_a_boot_failure():
    # 0 spec files: clean report (webServer ok, no global errors, nothing ran),
    # boot_error left empty by the pass -> must NOT be a build failure.
    lr = LaneResult(config="planning-phase-view.ts", web_server_ok=True,
                    started=False, boot_error="")
    assert not lr.boot_failed


def test_healthy_lane_that_ran_specs_is_not_a_boot_failure():
    lr = LaneResult(config="chat.ts", web_server_ok=True, started=True,
                    passed=2, failed=1, boot_error="")
    assert not lr.boot_failed


# --- lane_boot_error discriminator over real native-report shapes (observed
#     against 8ac124d6 @ group 80-82) --------------------------------------

def test_globalsetup_build_failure_is_flagged():
    # badge: globalSetup throws on the projectSurface bundle rebuild
    run = PwRunResult(
        web_server_ok=True, started=False,
        global_errors=["Error: [playwright-badge-globalsetup] projectSurface "
                       "bundle rebuild failed (exit code 1)"])
    assert "bundle rebuild failed" in lane_boot_error(run)


def test_webserver_timeout_is_flagged():
    # lifecycle / projectSurface: vite preview never serves a usable bundle
    run = PwRunResult(
        web_server_ok=False, started=False,
        global_errors=["Error: Timed out waiting 60000ms from config.webServer."])
    assert "Timed out" in lane_boot_error(run)


def test_no_tests_found_is_NOT_flagged():
    # planning-phase-view: 0 spec files. Playwright reports "No tests found" in
    # the report errors array — must be treated as an EMPTY lane, not a failure.
    run = PwRunResult(
        web_server_ok=True, started=False,
        global_errors=["Error: No tests found"])
    assert lane_boot_error(run) == ""


def test_healthy_run_returns_empty():
    run = PwRunResult(web_server_ok=True, started=True, passed=2, failed=1)
    assert lane_boot_error(run) == ""
