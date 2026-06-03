"""Parser for the native ``@playwright/test`` JSON reporter.

The ``@playwright/test`` JSON schema (``--reporter=json``) is NOT the
``pytest-json-report`` schema that ``tasks/playwright.py:_parse_report`` parses
(``data['summary']`` + flat ``data['tests']``). This reporter nests
``suites -> (suites) -> specs -> tests -> results`` and reports webServer/global
failures in a top-level ``errors`` array. A webServer that fails to start surfaces
ONLY there (zero specs run) — so a naive "no failed tests" check would be a false
green. We detect it explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# A result status -> our normalized status.
_FAIL_STATUSES = {"failed", "timedOut", "interrupted"}
_WEBSERVER_MARKERS = (
    "config.webserver",
    "web server",
    "webserver",
    "was not able to start",
    "timed out waiting",
)


@dataclass
class PwTestResult:
    title: str
    file: str
    status: str  # passed | failed | timedOut | skipped | interrupted | flaky
    flaky: bool
    duration_ms: int
    error: str
    screenshots: list[str] = field(default_factory=list)
    retries: int = 0


@dataclass
class PwRunResult:
    tests: list[PwTestResult] = field(default_factory=list)
    passed: int = 0
    failed: int = 0
    flaky: int = 0
    skipped: int = 0
    global_errors: list[str] = field(default_factory=list)
    web_server_ok: bool = True
    started: bool = False  # True if any spec actually ran

    def summary(self) -> str:
        return (
            f"passed={self.passed} failed={self.failed} flaky={self.flaky} "
            f"skipped={self.skipped} webServer_ok={self.web_server_ok}"
        )


def _error_text(obj: Any) -> str:
    if isinstance(obj, dict):
        return str(obj.get("message") or obj.get("value") or obj.get("stack") or "")
    return str(obj or "")


def _iter_specs(suite: dict[str, Any], prefix: str):
    title = suite.get("title") or suite.get("file") or ""
    here = f"{prefix} > {title}".strip(" >") if title else prefix
    for spec in suite.get("specs", []) or []:
        yield here, spec
    for child in suite.get("suites", []) or []:
        yield from _iter_specs(child, here)


def parse_playwright_json(report: dict[str, Any]) -> PwRunResult:
    out = PwRunResult()

    for err in report.get("errors", []) or []:
        txt = _error_text(err)
        out.global_errors.append(txt)
        if any(m in txt.lower() for m in _WEBSERVER_MARKERS):
            out.web_server_ok = False

    for suite in report.get("suites", []) or []:
        for prefix, spec in _iter_specs(suite, ""):
            out.started = True
            spec_file = spec.get("file", "")
            spec_title = f"{prefix} > {spec.get('title', '')}".strip(" >")
            for test in spec.get("tests", []) or []:
                results = test.get("results", []) or []
                retries = max(0, len(results) - 1)
                final = results[-1] if results else {}
                rstatus = final.get("status", "skipped")
                test_status = test.get("status", "")  # expected|unexpected|flaky|skipped
                is_flaky = test_status == "flaky"
                screenshots: list[str] = []
                errors: list[str] = []
                for r in results:
                    for att in r.get("attachments", []) or []:
                        if att.get("name") == "screenshot" and att.get("path"):
                            screenshots.append(att["path"])
                    if r.get("error"):
                        errors.append(_error_text(r["error"]))
                    for e in r.get("errors", []) or []:
                        errors.append(_error_text(e))

                if is_flaky:
                    norm = "flaky"
                    out.flaky += 1
                elif rstatus == "passed":
                    norm = "passed"
                    out.passed += 1
                elif rstatus == "skipped":
                    norm = "skipped"
                    out.skipped += 1
                elif rstatus in _FAIL_STATUSES:
                    norm = rstatus
                    out.failed += 1
                else:
                    norm = rstatus or "skipped"
                    out.skipped += 1

                out.tests.append(
                    PwTestResult(
                        title=spec_title,
                        file=spec_file,
                        status=norm,
                        flaky=is_flaky,
                        duration_ms=int(final.get("duration", 0) or 0),
                        error="; ".join(e for e in errors if e)[:1000],
                        screenshots=screenshots,
                        retries=retries,
                    )
                )

    return out


def boot_smoke_from_run(run: PwRunResult, *, surface: str = "web") -> tuple[str, str]:
    """Derive a boot-smoke (status, detail) from a native run.

    The harness "came up" iff the webServer didn't error AND at least one spec
    executed. A webServer failure is a precise blocker — never a false green.
    """
    if not run.web_server_ok:
        return "fail", "webServer failed to start: " + " | ".join(run.global_errors)[:400]
    # Playwright only begins executing specs once every webServer `url` returns
    # ready, so a spec having run proves the harness came up — even if the run
    # then stopped early (e.g. --max-failures) or had test-level failures, which
    # are replay results, NOT a boot-smoke failure.
    if run.started:
        note = ""
        if run.global_errors:
            note = " (note: " + "; ".join(run.global_errors)[:150] + ")"
        return "pass", f"harness up; {run.summary()}{note}"
    if run.global_errors:
        return "fail", "harness did not produce a runnable surface: " + " | ".join(
            run.global_errors
        )[:400]
    return "fail", "no specs executed (harness did not produce a runnable surface)"
