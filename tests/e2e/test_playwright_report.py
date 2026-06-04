"""Unit tests for the native @playwright/test JSON parser + boot-smoke derive."""

from __future__ import annotations

from iriai_build_v2.workflows.develop.e2e.adapters.playwright_report import (
    boot_smoke_from_run,
    parse_playwright_json,
)

PASS_FAIL_FLAKY = {
    "suites": [
        {
            "title": "badge.spec.ts",
            "file": "badge.spec.ts",
            "specs": [
                {
                    "title": "shows badge",
                    "file": "badge.spec.ts",
                    "tests": [
                        {"status": "expected",
                         "results": [{"status": "passed", "duration": 120,
                                      "attachments": []}]}
                    ],
                },
                {
                    "title": "badge count",
                    "file": "badge.spec.ts",
                    "tests": [
                        {"status": "unexpected",
                         "results": [{"status": "failed", "duration": 300,
                                      "error": {"message": "expected 3 got 0"},
                                      "attachments": [{"name": "screenshot",
                                                       "path": "/tmp/shot.png",
                                                       "contentType": "image/png"}]}]}
                    ],
                },
                {
                    "title": "flaky one",
                    "file": "badge.spec.ts",
                    "tests": [
                        {"status": "flaky",
                         "results": [{"status": "failed", "duration": 10},
                                     {"status": "passed", "duration": 12}]}
                    ],
                },
            ],
            "suites": [
                {
                    "title": "nested",
                    "specs": [
                        {"title": "deep", "file": "badge.spec.ts",
                         "tests": [{"status": "skipped",
                                    "results": [{"status": "skipped"}]}]}
                    ],
                }
            ],
        }
    ],
    "errors": [],
    "stats": {"expected": 1, "unexpected": 1, "flaky": 1, "skipped": 1},
}

WEBSERVER_FAIL = {
    "suites": [],
    "errors": [
        {"message": "Error: Process from config.webServer was not able to start. "
                    "Exit code: 1"}
    ],
    "stats": {},
}


def test_parses_pass_fail_flaky_skipped():
    run = parse_playwright_json(PASS_FAIL_FLAKY)
    assert run.passed == 1
    assert run.failed == 1
    assert run.flaky == 1
    assert run.skipped == 1
    assert run.web_server_ok is True
    assert run.started is True
    # failing test carries the error + screenshot
    fail = next(t for t in run.tests if t.status == "failed")
    assert "expected 3 got 0" in fail.error
    assert fail.screenshots == ["/tmp/shot.png"]
    # flaky test has retries recorded
    flaky = next(t for t in run.tests if t.flaky)
    assert flaky.retries == 1
    # nested suite spec is reached
    assert any("nested" in t.title for t in run.tests)


def test_boot_smoke_pass_on_healthy_run():
    run = parse_playwright_json(PASS_FAIL_FLAKY)
    status, detail = boot_smoke_from_run(run)
    assert status == "pass"
    assert "harness up" in detail


def test_webserver_failure_is_not_a_false_green():
    run = parse_playwright_json(WEBSERVER_FAIL)
    assert run.web_server_ok is False
    assert run.started is False
    status, detail = boot_smoke_from_run(run)
    assert status == "fail"
    assert "webServer failed" in detail


def test_distinct_from_pytest_json_report_schema():
    # The pytest-json-report schema (data['summary']/data['tests']) yields NOTHING
    # here — proving this is a genuinely different parser.
    pytest_shape = {"summary": {"passed": 5}, "tests": [{"outcome": "passed"}]}
    run = parse_playwright_json(pytest_shape)
    assert run.passed == 0 and run.failed == 0 and not run.started
