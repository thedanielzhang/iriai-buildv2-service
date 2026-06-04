"""AsyncE2ETrack — the self-coalescing, non-blocking e2e track.

Three-part non-blocking guarantee:
1. It POLLS ``get_latest_sealed_checkpoint`` (read-only, its OWN pool) and NEVER
   acquires the feature advisory lock.
2. It never attaches to a live ``.git`` — substrate isolation is by clone.
3. "Non-blocking" is also resource-bounded — a host PREFLIGHT aborts a pass when
   load/free-mem/disk are past thresholds, and heavy builds run as one bounded
   subprocess under nice.

Self-coalescing: it keeps a cursor and always pulls the LATEST sealed checkpoint,
skipping intermediates if the DAG outruns it. CLI-invoked during A–D (the
orchestrator auto-spawn is the separate, gated cutover).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .checkpoint import SealedCheckpoint, fetch_latest_sealed_checkpoint
from .models import E2ETrackCursor

# Preflight thresholds (env-overridable).
_MAX_LOAD = float(os.environ.get("IRIAI_E2E_MAX_LOAD", "20"))
_MIN_FREE_GB = float(os.environ.get("IRIAI_E2E_MIN_FREE_MEM_GB", "1.0"))
_MIN_DISK_GB = float(os.environ.get("IRIAI_E2E_MIN_DISK_GB", "10"))
# A ~15-container `up --build` is minutes + many GB — a much higher disk floor
# than the single-process default, and a single-stack mutex (only one e2e compose
# project at a time) so passes never pile concurrent stacks onto the host (P4).
_MIN_COMPOSE_DISK_GB = float(os.environ.get("IRIAI_E2E_COMPOSE_MIN_DISK_GB", "40"))


@dataclass
class Preflight:
    ok: bool
    load1: float
    free_mem_gb: float
    free_disk_gb: float
    reason: str = ""


def host_preflight(*, scratch_dir: str = "/tmp") -> Preflight:
    """Abort heavy work when the host is loaded — protects the live workflow."""
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = 0.0
    free_mem_gb = _free_mem_gb()
    free_disk_gb = shutil.disk_usage(scratch_dir).free / (1024 ** 3)
    reasons = []
    if load1 > _MAX_LOAD:
        reasons.append(f"load {load1:.1f} > {_MAX_LOAD}")
    if free_mem_gb < _MIN_FREE_GB:
        reasons.append(f"free_mem {free_mem_gb:.1f}GB < {_MIN_FREE_GB}")
    if free_disk_gb < _MIN_DISK_GB:
        reasons.append(f"free_disk {free_disk_gb:.0f}GB < {_MIN_DISK_GB}")
    return Preflight(not reasons, load1, free_mem_gb, free_disk_gb, "; ".join(reasons))


@dataclass
class ComposePreflight:
    ok: bool
    free_disk_gb: float
    running_projects: list[str] = field(default_factory=list)
    reason: str = ""


def _running_compose_projects() -> list[str]:
    """Names of currently-up docker compose projects (best-effort; [] on error)."""
    try:
        out = subprocess.run(
            ["docker", "compose", "ls", "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return []
        data = json.loads(out.stdout or "[]")
        return [str(p.get("Name", "")) for p in data if p.get("Name")]
    except Exception:  # noqa: BLE001 - preflight is best-effort
        return []


def compose_preflight(
    *,
    project_prefix: str = "e2e",
    scratch_dir: str = "/tmp",
    running_projects: list[str] | None = None,
) -> ComposePreflight:
    """Compose-specific preflight: higher disk floor + single-stack mutex.

    Refuses if free disk is below the (much higher) compose floor, OR if any
    e2e compose project (name starting with ``project_prefix``) is already up —
    only ONE e2e stack runs at a time, so a pass never stacks ~15 more containers
    onto a host already running one. ``running_projects`` is injectable for tests.
    """
    free_disk_gb = shutil.disk_usage(scratch_dir).free / (1024 ** 3)
    projects = (
        running_projects
        if running_projects is not None
        else _running_compose_projects()
    )
    e2e_up = [p for p in projects if p.startswith(project_prefix)]
    reasons = []
    if free_disk_gb < _MIN_COMPOSE_DISK_GB:
        reasons.append(f"free_disk {free_disk_gb:.0f}GB < {_MIN_COMPOSE_DISK_GB}")
    if e2e_up:
        reasons.append(
            f"single-stack mutex: e2e compose project(s) already up: {e2e_up}"
        )
    return ComposePreflight(not reasons, free_disk_gb, e2e_up, "; ".join(reasons))


def _free_mem_gb() -> float:
    """Best-effort free memory in GB (macOS vm_stat; fallback large)."""
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True,
                             timeout=5).stdout
        page_size = 4096
        free_pages = inactive = 0
        for line in out.splitlines():
            if "page size of" in line:
                page_size = int("".join(ch for ch in line if ch.isdigit()))
            elif line.startswith("Pages free:"):
                free_pages = int(line.split(":")[1].strip().rstrip("."))
            elif line.startswith("Pages inactive:"):
                inactive = int(line.split(":")[1].strip().rstrip("."))
        return (free_pages + inactive) * page_size / (1024 ** 3)
    except Exception:  # noqa: BLE001
        return 999.0


@dataclass
class PollResult:
    checkpoint: SealedCheckpoint | None = None
    advanced: bool = False
    did_pass: bool = False
    skipped_reason: str = ""
    coalesced_from: int | None = None


@dataclass
class AsyncE2ETrack:
    feature_id: str
    live_dsn: str
    registry: Any = None  # E2ERegistry on the SCRATCH store (for cursor/artifacts)
    max_group_idx: int | None = None
    poll_interval_s: float = 10.0
    pass_fn: Callable[[SealedCheckpoint], Awaitable[Any]] | None = None
    _last_checkpoints: list[int] = field(default_factory=list, init=False)

    async def latest_checkpoint(self) -> SealedCheckpoint | None:
        """Read-only, own pool, no feature lock."""
        return await fetch_latest_sealed_checkpoint(
            self.feature_id, dsn=self.live_dsn, max_group_idx=self.max_group_idx
        )

    async def _cursor(self) -> E2ETrackCursor | None:
        if self.registry is None:
            return None
        return await self.registry.get_cursor()

    async def poll_once(self, *, do_pass: bool = True) -> PollResult:
        cp = await self.latest_checkpoint()
        if cp is None:
            return PollResult(skipped_reason="no sealed checkpoint")
        self._last_checkpoints.append(cp.group_idx)
        cursor = await self._cursor()
        cursor_commit = cursor.last_processed_commit if cursor else ""
        head_commit = next(iter(cp.result_commits().values()), "")
        if cursor_commit == head_commit and head_commit:
            return PollResult(checkpoint=cp, advanced=False,
                              skipped_reason="already processed")

        # Coalesce: we always process the LATEST; note if we skipped intermediates.
        coalesced_from = cursor.group_idx if cursor and cursor.group_idx >= 0 else None
        result = PollResult(checkpoint=cp, advanced=True, coalesced_from=coalesced_from)

        if not do_pass:
            return result

        pf = host_preflight()
        if not pf.ok:
            result.skipped_reason = f"preflight abort: {pf.reason}"
            return result

        if self.pass_fn is not None:
            await self.pass_fn(cp)
            result.did_pass = True

        if self.registry is not None:
            await self.registry.put_cursor(
                E2ETrackCursor(last_processed_commit=head_commit,
                               group_idx=cp.group_idx)
            )
        return result

    async def run_loop(
        self, *, iterations: int | None = None, do_pass: bool = True,
        stop: Callable[[], bool] | None = None,
    ) -> list[PollResult]:
        """Poll on an interval, coalescing to the latest checkpoint each tick."""
        results: list[PollResult] = []
        i = 0
        while True:
            if stop is not None and stop():
                break
            results.append(await self.poll_once(do_pass=do_pass))
            i += 1
            if iterations is not None and i >= iterations:
                break
            await asyncio.sleep(self.poll_interval_s)
        return results
