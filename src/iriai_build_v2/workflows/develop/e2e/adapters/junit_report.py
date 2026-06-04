"""Parser for the JUnit-XML reporter (vitest ``--reporter=junit``, pytest
``--junitxml``).

Reuses the :class:`PwRunResult` / :class:`PwTestResult` shapes from
``playwright_report`` so JUnit host-unit-test results flow through the SAME
verdict/triage/status path as the browser adapter. JUnit has no webServer
concept, so ``web_server_ok`` stays True; ``started`` is True iff any testcase
was parsed (an empty/garbage report is an honest "nothing ran", never a false
green).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .playwright_report import PwRunResult, PwTestResult

_MAX_ERR = 1000


def _case_message(node: ET.Element) -> str:
    msg = node.get("message") or ""
    text = (node.text or "").strip()
    joined = "\n".join(part for part in (msg, text) if part)
    return joined[:_MAX_ERR]


def parse_junit_xml(xml_text: str) -> PwRunResult:
    """Parse a JUnit-XML document into a normalized run result.

    Accepts either a ``<testsuites>`` root or a single ``<testsuite>`` root
    (vitest emits the former, pytest either). A parse error is recorded as a
    global error (and ``started`` stays False), so a corrupt report fails the
    boot-smoke / verdict path rather than reading as zero failures.
    """
    out = PwRunResult()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        out.global_errors.append(f"junit parse error: {exc}")
        return out

    for suite in root.iter("testsuite"):
        # A suite-level error (e.g. a pytest collection error) means tests could
        # not even run — surface it so it can't pass as green.
        suite_err = suite.get("errors")
        if suite_err and suite_err not in ("0", ""):
            out.global_errors.append(
                f"testsuite {suite.get('name', '')!r} reports errors={suite_err}"
            )
        for case in suite.findall("testcase"):
            out.started = True
            name = case.get("name", "")
            classname = case.get("classname", "") or case.get("file", "")
            title = f"{classname} > {name}".strip(" >") if classname else name
            try:
                duration_ms = int(float(case.get("time", "0") or 0) * 1000)
            except ValueError:
                duration_ms = 0
            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")
            if failure is not None or error is not None:
                node = failure if failure is not None else error
                status, message = "failed", _case_message(node)
                out.failed += 1
            elif skipped is not None:
                status, message = "skipped", _case_message(skipped)
                out.skipped += 1
            else:
                status, message = "passed", ""
                out.passed += 1
            out.tests.append(
                PwTestResult(
                    title=title,
                    file=classname,
                    status=status,
                    flaky=False,
                    duration_ms=duration_ms,
                    error=message,
                )
            )
    return out
