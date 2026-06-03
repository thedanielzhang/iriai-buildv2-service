"""Browser/Electron adapter.

Discovers and runs the project's NATIVE ``playwright.config.*`` via
``npx playwright test --reporter=json`` (writing the JSON report to a file via
``PLAYWRIGHT_JSON_OUTPUT_NAME``), parsing it with the native @playwright/test
parser (NOT the pytest-json-report parser). Reuses only the subprocess+timeout
pattern from ``tasks/playwright.py``.

For an Electron / VS Code fork the e2e lanes serve React webview bundles via a
Playwright ``webServer`` harness (Vite + a mock bridge) — they do NOT boot the
full Electron binary — so boot-smoke = "the harness webServer came up", proven by
running the lightest lane and confirming the webServer started.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import BootSmoke, E2ESpecRecord, E2EVerdictRecord, ProjectProfile
from . import Instance, Surface, register_adapter
from .playwright_report import (
    PwRunResult,
    boot_smoke_from_run,
    parse_playwright_json,
)

_NATIVE_TIMEOUT_S = float(os.environ.get("IRIAI_E2E_NATIVE_TIMEOUT_S", "900"))


@dataclass
class NativeRun:
    config: str
    result: PwRunResult
    returncode: int
    stderr_tail: str
    report_path: str


class BrowserAdapter:
    adapter_id = "browser"

    async def provision(
        self, profile: ProjectProfile, checkout_dir: Path, *, runner: Any = None,
        feature: Any = None,
    ) -> Instance:
        checkout = Path(checkout_dir)
        env = {k: os.environ.get(k, "") for k in profile.env_keys}
        # Ensure node_modules (clone reuse is done by the substrate / caller;
        # if genuinely absent, the native run will surface a precise error).
        # Ensure a chromium browser is available (idempotent, fast if present).
        with contextlib.suppress(Exception):
            await self._sh(
                ["npx", "playwright", "install", "chromium"], cwd=checkout,
                timeout=600,
            )
        surfaces = [
            Surface(name=cfg, probe_kind="http_get", probe_target=cfg)
            for cfg in (profile.native_test_configs or [])
        ]
        return Instance(profile=profile, checkout_dir=checkout, surfaces=surfaces,
                        env=env)

    async def seed(self, instance: Instance, profile: ProjectProfile) -> None:
        if profile.seed_cmd:
            await self._sh(profile.seed_cmd.split(), cwd=instance.checkout_dir,
                           env=instance.env, timeout=300)

    async def smoke(
        self, instance: Instance, profile: ProjectProfile, *,
        config: str | None = None, extra_args: tuple[str, ...] = (),
    ) -> list[BootSmoke]:
        """Boot-smoke by running the lightest lane and confirming webServer up."""
        configs = profile.native_test_configs or []
        if not configs:
            return [BootSmoke(status="not_applicable", surface="web",
                              detail="no native_test_configs discovered")]
        chosen = config or _lightest_config(configs)
        run = await self.run_native_config(
            instance, chosen,
            extra_args=("--max-failures=1", "--workers=1") + extra_args,
        )
        status, detail = boot_smoke_from_run(run.result, surface=chosen)
        if status != "pass" and run.returncode != 0 and not run.result.started:
            detail = f"{detail} | rc={run.returncode} {run.stderr_tail[-300:]}"
        return [BootSmoke(status=status, surface=chosen, probe_kind="http_get",
                          probe_target=chosen, detail=detail)]

    async def run(
        self, instance: Instance, specs: list[E2ESpecRecord], *, runner: Any = None,
        feature: Any = None, configs: list[str] | None = None,
    ) -> list[E2EVerdictRecord]:
        """Deterministic native replay across configs -> one verdict per spec/AC."""
        cfgs = configs or instance.profile.native_test_configs or []
        verdicts: list[E2EVerdictRecord] = []
        commit = instance.profile.notes  # caller overrides source_commit downstream
        for cfg in cfgs:
            nr = await self.run_native_config(instance, cfg)
            for t in nr.result.tests:
                if t.status == "skipped":
                    status = "skipped"
                elif t.flaky:
                    status = "pass"  # flaky resolved green; quarantine handled in triage
                elif t.status == "passed":
                    status = "pass"
                else:
                    status = "fail"
                verdicts.append(
                    E2EVerdictRecord(
                        spec_id=t.title or t.file,
                        source_commit="",
                        status=status,
                        failure_class="flaky" if t.flaky else "",
                        summary=(t.error[:300] if status == "fail" else t.title),
                        evidence_path=(t.screenshots[0] if t.screenshots else ""),
                    )
                )
        return verdicts

    async def author(
        self, instance: Instance, scenarios: list[Any], *, runner: Any, feature: Any
    ) -> list[E2ESpecRecord]:
        # Native specs already live in the repo (the project's own configs); the
        # spec_author orchestration in triage.py binds them to ACs + digests.
        return []

    async def teardown(self, instance: Instance) -> None:
        if instance.substrate is not None:
            with contextlib.suppress(Exception):
                await instance.substrate.teardown()

    # --------------------------------------------------------------- internals
    async def run_native_config(
        self, instance: Instance, config: str, *, extra_args: tuple[str, ...] = (),
        timeout: float = _NATIVE_TIMEOUT_S,
    ) -> NativeRun:
        checkout = Path(instance.checkout_dir)
        report = checkout / f".e2e-report-{Path(config).stem}.json"
        with contextlib.suppress(FileNotFoundError):
            report.unlink()
        env = {
            **os.environ,
            **instance.env,
            "PLAYWRIGHT_JSON_OUTPUT_NAME": str(report),
            "CI": "1",
        }
        cmd = ["npx", "playwright", "test", f"--config={config}",
               "--reporter=json", *extra_args]
        rc, _out, err = await self._sh(cmd, cwd=checkout, env=env, timeout=timeout)
        data: dict[str, Any] = {}
        if report.exists():
            with contextlib.suppress(Exception):
                data = json.loads(report.read_text())
        result = parse_playwright_json(data) if data else PwRunResult(
            global_errors=[f"no JSON report produced (rc={rc})"],
            web_server_ok=False,
        )
        return NativeRun(config=config, result=result, returncode=rc,
                         stderr_tail=err[-2000:], report_path=str(report))

    @staticmethod
    async def _sh(
        cmd: list[str], *, cwd: Path, env: dict | None = None,
        timeout: float = 600,
    ) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "nice", "-n", "10", *cmd, cwd=str(cwd),
            env=env or os.environ.copy(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            return 124, "", f"timeout after {timeout}s: {' '.join(cmd)}"
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(
            errors="replace"
        )


def _lightest_config(configs: list[str]) -> str:
    """Prefer a dev-server (no-build) lane for boot-smoke; chat uses vite dev."""
    for pref in ("chat", "badge", "lifecycle"):
        for c in configs:
            if pref in c:
                return c
    return configs[0]


register_adapter(BrowserAdapter())
