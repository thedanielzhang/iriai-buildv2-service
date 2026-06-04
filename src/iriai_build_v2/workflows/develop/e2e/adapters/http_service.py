"""HTTP service (API) adapter — the second adapter that validates the abstraction.

Boots the service via the profile's ``start_cmd`` (with ``{port}`` injected),
probes readiness via the declared ``ready_probe`` (typically ``http_get`` on a
``/healthz`` route), and drives request scenarios with a plain HTTP client. No
MCP/preview needed — pure subprocess + requests, proving the agnostic core +
per-surface boot-smoke generalize beyond the browser adapter.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
from pathlib import Path
from typing import Any

from ..models import BootSmoke, E2ESpecRecord, E2EVerdictRecord, ProjectProfile
from . import Instance, Surface, probe_http_get, register_adapter


class HttpServiceAdapter:
    adapter_id = "http_service"

    async def provision(
        self, profile: ProjectProfile, checkout_dir: Path, *, runner: Any = None,
        feature: Any = None,
    ) -> Instance:
        checkout = Path(checkout_dir)
        env = {**os.environ, **{k: os.environ.get(k, "") for k in profile.env_keys}}
        if profile.install_cmd:
            await self._sh(profile.install_cmd, cwd=checkout, env=env, timeout=900)
        port = _alloc_port()
        base_url = (profile.base_url_template or "http://127.0.0.1:{port}").format(
            port=port
        )
        env["PORT"] = str(port)
        start = (profile.start_cmd or "").format(port=port)
        proc = None
        if start:
            proc = await asyncio.create_subprocess_exec(
                *shlex.split(start), cwd=str(checkout), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
        target = profile.ready_probe_target or "/healthz"
        surface = Surface(
            name="api", probe_kind=profile.ready_probe_kind or "http_get",
            probe_target=(base_url + target if target.startswith("/") else target),
            base_url=base_url, process=proc,
        )
        return Instance(profile=profile, checkout_dir=checkout, surfaces=[surface],
                        env=env)

    async def seed(self, instance: Instance, profile: ProjectProfile) -> None:
        if profile.seed_cmd:
            await self._sh(profile.seed_cmd, cwd=instance.checkout_dir,
                           env=instance.env, timeout=300)

    async def smoke(
        self, instance: Instance, profile: ProjectProfile
    ) -> list[BootSmoke]:
        out: list[BootSmoke] = []
        for s in instance.surfaces:
            ok, detail = await probe_http_get(s.probe_target, timeout_s=60)
            out.append(BootSmoke(status="pass" if ok else "fail", surface=s.name,
                                 probe_kind="http_get", probe_target=s.probe_target,
                                 detail=detail))
        return out

    async def run(
        self, instance: Instance, specs: list[E2ESpecRecord], *, runner: Any = None,
        feature: Any = None, requests: list[tuple[str, int]] | None = None,
    ) -> list[E2EVerdictRecord]:
        """Drive request scenarios: each (path, expected_status) -> a verdict."""
        base = instance.surfaces[0].base_url if instance.surfaces else ""
        verdicts: list[E2EVerdictRecord] = []
        for path, expected in (requests or []):
            url = base + path if path.startswith("/") else path
            ok, detail = await probe_http_get(url, timeout_s=10,
                                              accept=range(expected, expected + 1))
            verdicts.append(E2EVerdictRecord(
                spec_id=path, source_commit="", status="pass" if ok else "fail",
                summary=detail))
        return verdicts

    async def author(
        self, instance: Instance, scenarios: list[Any], *, runner: Any, feature: Any
    ) -> list[E2ESpecRecord]:
        return []

    async def teardown(self, instance: Instance) -> None:
        for s in instance.surfaces:
            proc = getattr(s, "process", None)
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=10)
                    continue
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        if instance.substrate is not None:
            with contextlib.suppress(Exception):
                await instance.substrate.teardown()

    @staticmethod
    async def _sh(cmd: str, *, cwd: Path, env: dict, timeout: float) -> None:
        proc = await asyncio.create_subprocess_exec(
            *shlex.split(cmd), cwd=str(cwd), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.communicate(), timeout=timeout)


def _alloc_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


register_adapter(HttpServiceAdapter())
