"""Playwright E2E test tasks — agent-driven discovery and script-based replay."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from iriai_compose import Task

if TYPE_CHECKING:
    from iriai_compose import Feature, WorkflowRunner


@dataclass
class E2ETestResult:
    """Result of an E2E test run."""

    total: int
    passed: int
    failed: int
    errors: int
    duration_s: float
    tests: list[dict[str, Any]]
    summary: str


class PlaywrightService:
    """Manages browser installation and E2E test execution."""

    def __init__(self, *, headless: bool = True, browser: str = "chromium") -> None:
        self._headless = headless
        self._browser = browser

    async def ensure_browsers(self) -> None:
        """Run ``playwright install {browser}`` if not already installed."""
        proc = await asyncio.create_subprocess_exec(
            "python", "-m", "playwright", "install", self._browser,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"playwright install {self._browser} failed "
                f"(exit {proc.returncode}): {stderr.decode()}"
            )

    async def run_tests(
        self,
        test_dir: str,
        *,
        base_url: str,
        timeout_s: int = 300,
        pattern: str = "test_*.py",
        headed: bool = False,
        extra_args: list[str] | None = None,
    ) -> E2ETestResult:
        """Run pytest-playwright as subprocess, parse JSON report."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            report_path = tmp.name

        cmd: list[str] = [
            "python", "-m", "pytest",
            test_dir,
            f"--base-url={base_url}",
            f"--browser={self._browser}",
            "--screenshot=only-on-failure",
            "--json-report",
            f"--json-report-file={report_path}",
            f"--timeout={timeout_s * 1000}",
            "-x",
        ]

        # Add -k filter only for non-default patterns
        if pattern != "test_*.py":
            k_expr = pattern.replace("test_", "").replace(".py", "")
            if k_expr:
                cmd.extend(["-k", k_expr])

        if headed or not self._headless:
            cmd.append("--headed")

        if extra_args:
            cmd.extend(extra_args)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s + 30,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise RuntimeError(
                f"pytest-playwright timed out after {timeout_s}s"
            )

        # exit code 0 = all passed, 1 = some failed, >1 = internal error
        if proc.returncode is not None and proc.returncode > 1:
            raise RuntimeError(
                f"pytest crashed (exit {proc.returncode}): {stderr.decode()}"
            )

        return self._parse_report(report_path)

    async def close(self) -> None:
        """No-op for now (subprocess-based, no persistent browser)."""

    @staticmethod
    def _parse_report(report_path: str) -> E2ETestResult:
        """Parse pytest-json-report output into E2ETestResult."""
        report_file = Path(report_path)
        if not report_file.exists():
            return E2ETestResult(
                total=0, passed=0, failed=0, errors=0,
                duration_s=0, tests=[], summary="No report file generated",
            )

        data = json.loads(report_file.read_text())
        report_file.unlink(missing_ok=True)

        summary_data = data.get("summary", {})
        tests = data.get("tests", [])

        passed = summary_data.get("passed", 0)
        failed = summary_data.get("failed", 0)
        errors = summary_data.get("error", 0)
        total = summary_data.get("total", len(tests))
        duration = data.get("duration", 0.0)

        parts = []
        if passed:
            parts.append(f"{passed} passed")
        if failed:
            parts.append(f"{failed} failed")
        if errors:
            parts.append(f"{errors} errors")
        summary = ", ".join(parts) if parts else "no tests collected"

        return E2ETestResult(
            total=total,
            passed=passed,
            failed=failed,
            errors=errors,
            duration_s=duration,
            tests=tests,
            summary=summary,
        )


class RunE2ETestTask(Task):
    """Script-based replay — runs pre-existing pytest-playwright scripts."""

    test_dir: str
    base_url: str
    timeout_s: int = 300
    pattern: str = "test_*.py"
    headed: bool = False

    async def execute(self, runner: WorkflowRunner, feature: Feature) -> E2ETestResult:
        service: PlaywrightService = runner.services["playwright"]
        workspace = runner.workspaces["main"].path
        full_path = str(workspace / self.test_dir)
        return await service.run_tests(
            full_path,
            base_url=self.base_url,
            timeout_s=self.timeout_s,
            pattern=self.pattern,
            headed=self.headed,
        )


class DiscoverE2ETestTask(Task):
    """Agent-driven discovery — clicks through app, then captures test scripts."""

    base_url: str
    output_dir: str = "tests/e2e"
    journey_names: list[str] | None = None
    timeout_s: int = 600

    async def execute(self, runner: WorkflowRunner, feature: Feature) -> E2ETestResult:
        from iriai_compose import Ask

        from ..models.outputs import Verdict
        from ..roles import integration_tester

        # Step 1: Agent clicks through and writes test scripts
        prompt = self._build_prompt(feature)
        verdict: Verdict = await runner.run(
            Ask(
                actor=integration_tester,
                prompt=prompt,
                output_type=Verdict,
            ),
            feature,
        )

        # Step 2: If agent succeeded, run the scripts it wrote to verify they pass
        if verdict.approved:
            service: PlaywrightService = runner.services["playwright"]
            workspace = runner.workspaces["main"].path
            full_path = str(workspace / self.output_dir)
            result = await service.run_tests(
                full_path,
                base_url=self.base_url,
                timeout_s=self.timeout_s,
            )
            return result

        # Agent found failures — return result reflecting that
        return E2ETestResult(
            total=len(verdict.checks),
            passed=sum(1 for c in verdict.checks if c.result == "PASS"),
            failed=sum(1 for c in verdict.checks if c.result == "FAIL"),
            errors=0,
            duration_s=0,
            tests=[],
            summary=verdict.summary,
        )

    def _build_prompt(self, feature: Feature) -> str:
        journeys_clause = ""
        if self.journey_names:
            names = ", ".join(self.journey_names)
            journeys_clause = f" Focus on these journeys: {names}."

        return (
            f"Execute all user journeys against {self.base_url} for the "
            f'"{feature.name}" feature.{journeys_clause}\n\n'
            f"For each successful journey, write a corresponding pytest-playwright "
            f"test script to `{self.output_dir}/test_{{journey_slug}}.py`.\n\n"
            f"Scripts must:\n"
            f"- Use `data-testid` selectors where available, falling back to "
            f"accessible roles/labels\n"
            f"- Use `expect()` assertions from playwright\n"
            f"- Use the `page` fixture with `base_url` (provided by pytest-playwright)\n"
            f"- Be deterministic and independent of each other\n"
            f"- Include clear docstrings describing the journey being tested\n\n"
            f"Report your findings as a Verdict with checks for each journey."
        )
