"""Project adapters: the only project-type-specific layer.

A ``ProjectAdapter`` knows how to provision a runnable instance from a checkout,
seed it, boot-smoke each surface, author specs against the running instance, and
replay them deterministically. Everything else (selection, triage, bridge,
status) is agnostic and routes through ``ProjectProfile.adapter_id``.

Boot-smoke is PER-SURFACE: web=health 200 / api=health endpoint / cli=`--help`
exit 0 / electron=workbench renders. A library with no runnable surface returns
``not_applicable`` and the gate falls back to build+import+native unit replay, so
fail-fast never halts a healthy library.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..models import BootSmoke, E2ESpecRecord, E2EVerdictRecord, ProjectProfile


@dataclass
class Surface:
    """One runnable surface of an instance (web/api/worker/electron/...)."""

    name: str
    probe_kind: str  # http_get | log_line | exit_zero | file_exists
    probe_target: str
    base_url: str = ""
    process: Any = None
    log_path: str = ""


@dataclass
class Instance:
    profile: ProjectProfile
    checkout_dir: Path
    surfaces: list[Surface] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    substrate: Any = None
    notes: str = ""


@runtime_checkable
class ProjectAdapter(Protocol):
    adapter_id: str

    async def provision(
        self, profile: ProjectProfile, checkout_dir: Path, *, runner: Any = None,
        feature: Any = None,
    ) -> Instance: ...

    async def seed(self, instance: Instance, profile: ProjectProfile) -> None: ...

    async def smoke(
        self, instance: Instance, profile: ProjectProfile
    ) -> list[BootSmoke]: ...

    async def author(
        self, instance: Instance, scenarios: list[Any], *, runner: Any, feature: Any
    ) -> list[E2ESpecRecord]: ...

    async def run(
        self, instance: Instance, specs: list[E2ESpecRecord], *, runner: Any,
        feature: Any,
    ) -> list[E2EVerdictRecord]: ...

    async def teardown(self, instance: Instance) -> None: ...


# --------------------------------------------------------------------------- #
# Generic per-surface readiness probes (used by every adapter's smoke()).
# --------------------------------------------------------------------------- #


async def probe_http_get(
    url: str, *, timeout_s: float = 60.0, interval_s: float = 1.0,
    accept=range(200, 500),
) -> tuple[bool, str]:
    """Poll a URL until it responds with an accepted status, or time out."""
    deadline = time.monotonic() + timeout_s
    last = "no attempt"
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(url, method="GET")
            resp = await asyncio.to_thread(
                urllib.request.urlopen, req, None, 5.0
            )
            code = resp.getcode()
            resp.close()
            if code in accept:
                return True, f"HTTP {code} from {url}"
            last = f"HTTP {code}"
        except urllib.error.HTTPError as exc:  # a response, just not 2xx/3xx
            if exc.code in accept:
                return True, f"HTTP {exc.code} from {url}"
            last = f"HTTP {exc.code}"
        except (urllib.error.URLError, OSError, ConnectionError) as exc:
            last = f"connect: {exc}"
        await asyncio.sleep(interval_s)
    return False, f"timeout after {timeout_s:.0f}s waiting for {url} (last: {last})"


async def probe_log_line(
    log_path: str, needle: str, *, timeout_s: float = 60.0, interval_s: float = 1.0
) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout_s
    p = Path(log_path)
    while time.monotonic() < deadline:
        if p.exists():
            try:
                text = p.read_text(errors="replace")
            except OSError:
                text = ""
            if needle in text:
                return True, f"found {needle!r} in {log_path}"
        await asyncio.sleep(interval_s)
    return False, f"timeout: {needle!r} not seen in {log_path} within {timeout_s:.0f}s"


async def probe_file_exists(
    path: str, *, timeout_s: float = 60.0, interval_s: float = 1.0
) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if Path(path).exists():
            return True, f"{path} exists"
        await asyncio.sleep(interval_s)
    return False, f"timeout: {path} not created within {timeout_s:.0f}s"


async def probe_exit_zero(
    cmd: list[str], *, cwd: str | None = None, env: dict | None = None,
    timeout_s: float = 60.0,
) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, env={**os.environ, **(env or {})},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return False, f"timeout running {' '.join(cmd)}"
    ok = proc.returncode == 0
    tail = (out or b"").decode(errors="replace")[-200:]
    return ok, f"exit {proc.returncode}: {tail.strip()}"


async def probe_surface(surface: Surface, *, timeout_s: float = 60.0) -> BootSmoke:
    """Run the surface's declared probe and return a BootSmoke verdict."""
    kind = surface.probe_kind
    target = surface.probe_target
    if kind == "http_get":
        ok, detail = await probe_http_get(target, timeout_s=timeout_s)
    elif kind == "log_line":
        needle = surface.base_url or target  # base_url holds the needle if split
        ok, detail = await probe_log_line(surface.log_path or target, needle,
                                          timeout_s=timeout_s)
    elif kind == "file_exists":
        ok, detail = await probe_file_exists(target, timeout_s=timeout_s)
    elif kind == "exit_zero":
        ok, detail = await probe_exit_zero(target.split(), timeout_s=timeout_s)
    else:
        return BootSmoke(status="not_applicable", surface=surface.name,
                         probe_kind=kind, probe_target=target,
                         detail=f"unknown probe kind {kind!r}")
    return BootSmoke(
        status="pass" if ok else "fail",
        surface=surface.name,
        probe_kind=kind,
        probe_target=target,
        detail=detail,
    )


# --------------------------------------------------------------------------- #
# Adapter registry
# --------------------------------------------------------------------------- #

_REGISTRY: dict[str, ProjectAdapter] = {}


def register_adapter(adapter: ProjectAdapter) -> None:
    _REGISTRY[adapter.adapter_id] = adapter


def get_adapter(adapter_id: str) -> ProjectAdapter:
    if adapter_id not in _REGISTRY:
        raise KeyError(
            f"no adapter registered for {adapter_id!r}; have {sorted(_REGISTRY)}"
        )
    return _REGISTRY[adapter_id]


def available_adapters() -> list[str]:
    return sorted(_REGISTRY)


def _autoregister() -> None:
    for mod in ("browser", "http_service"):
        with contextlib.suppress(Exception):
            __import__(f"{__name__}.{mod}", fromlist=["*"])


_autoregister()
