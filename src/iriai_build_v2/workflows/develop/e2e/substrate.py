"""Isolated checkpoint substrate via clone (never worktree).

`CloneSubstrate` stands up an isolated checkout of a sealed checkpoint by
``git clone --no-local`` from each live repo into an OUT-OF-TREE scratch dir
(``/tmp/iriai-e2e/<role>/<run_id>/repos/<repo>``), then ``checkout --detach``
the per-repo ``result_commit``. It NEVER ``git worktree add``s against the
canonical repo — the live merge queue runs ``reset --hard``/``clean``/``commit``
in those repos and shares ``.git`` across worktrees, which would race index/ref
locks and trip the worktree drift detector. ``--no-local`` gives a fully
independent object store (no alternates), so the live ``.git`` is untouched.

Two modes share the same clone machinery: ``automated`` (headless harness /
webServer for scenario replay) and ``operator`` (full-app headed launch). The
e2e track and the operator preview use SEPARATE run dirs so they never collide.

Lifecycle safety: ephemeral ports, tracked child PIDs persisted to a pidfile,
``finally``/atexit teardown, and idempotent startup GC keyed on ``run_id`` (never
a broad ``pkill`` that could hit live work).
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import os
import shutil
import signal
import socket
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE = Path(os.environ.get("IRIAI_E2E_SCRATCH", "/tmp/iriai-e2e"))
_GIT_TIMEOUT_S = float(os.environ.get("IRIAI_E2E_GIT_TIMEOUT_S", "1200"))


class SubstrateError(RuntimeError):
    pass


@dataclass
class RepoCheckout:
    repo_key: str
    source_path: str
    commit: str
    checkout_dir: Path


@dataclass
class CloneSubstrate:
    run_id: str = ""
    role: str = "track"  # track | preview
    mode: str = "automated"  # automated | operator
    base_dir: Path = DEFAULT_BASE
    nice: bool = True
    persist: bool = False  # if True, survive process exit (caller owns teardown)
    _pids: set[int] = field(default_factory=set, init=False, repr=False)
    _torn_down: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        self.base_dir = Path(self.base_dir)
        if not self.persist:
            atexit.register(self._sync_teardown)

    # ------------------------------------------------------------------ paths
    @property
    def run_dir(self) -> Path:
        return self.base_dir / self.role / self.run_id

    @property
    def repos_dir(self) -> Path:
        return self.run_dir / "repos"

    @property
    def _pidfile(self) -> Path:
        return self.run_dir / "pids.json"

    # ----------------------------------------------------------- subprocess
    async def _run(
        self, *args: str, cwd: Path | None = None, timeout: float = _GIT_TIMEOUT_S
    ) -> tuple[int, str, str]:
        prefix: tuple[str, ...] = ("nice", "-n", "10") if self.nice else ()
        proc = await asyncio.create_subprocess_exec(
            *prefix,
            *args,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise SubstrateError(f"timeout after {timeout}s: {' '.join(args)}")
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(
            errors="replace"
        )

    # --------------------------------------------------------------- cloning
    async def clone_checkpoint(
        self, sources: dict[str, str], commits: dict[str, str]
    ) -> dict[str, RepoCheckout]:
        """Clone + detach-checkout each repo of a checkpoint. Out-of-tree only."""
        self._assert_out_of_tree(sources)
        self.gc_stale(role=self.role, base_dir=self.base_dir, keep_run_id=self.run_id)
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        out: dict[str, RepoCheckout] = {}
        for key, commit in commits.items():
            src = sources[key]
            dst = self.repos_dir / key
            rc, _, err = await self._run(
                "git", "clone", "--no-local", "--quiet", src, str(dst)
            )
            if rc:
                raise SubstrateError(f"clone {key} failed: {err.strip()[:400]}")
            # Ensure the commit is present (clone transfers ref-reachable objects;
            # fetch it explicitly into the independent object store if missing).
            rc, _, _ = await self._run(
                "git", "cat-file", "-e", f"{commit}^{{commit}}", cwd=dst
            )
            if rc:
                rc, _, err = await self._run(
                    "git", "fetch", "--no-tags", "--quiet", src, commit, cwd=dst
                )
                if rc:
                    raise SubstrateError(
                        f"commit {commit[:12]} unreachable for {key}: {err.strip()[:300]}"
                    )
            rc, _, err = await self._run(
                "git", "checkout", "--detach", "--quiet", commit, cwd=dst
            )
            if rc:
                raise SubstrateError(
                    f"checkout {commit[:12]} for {key} failed: {err.strip()[:300]}"
                )
            out[key] = RepoCheckout(key, src, commit, dst)
        return out

    def _assert_out_of_tree(self, sources: dict[str, str]) -> None:
        run = self.run_dir.resolve()
        for src in sources.values():
            srcp = Path(src).resolve()
            if run == srcp or run.is_relative_to(srcp):
                raise SubstrateError(
                    f"refusing to provision inside a live repo: {run} under {srcp}"
                )

    # ------------------------------------------------------------ resources
    @staticmethod
    def alloc_port() -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])
        finally:
            s.close()

    def register_pid(self, pid: int) -> None:
        self._pids.add(int(pid))
        self._persist_pids()

    def _persist_pids(self) -> None:
        with contextlib.suppress(OSError):
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self._pidfile.write_text(json.dumps(sorted(self._pids)))

    # ------------------------------------------------------------- teardown
    async def teardown(self) -> None:
        if self._torn_down:
            return
        self._torn_down = True
        for pid in sorted(self._pids):
            _kill_pid(pid)
        await asyncio.sleep(0)  # let signals propagate
        with contextlib.suppress(OSError):
            shutil.rmtree(self.run_dir, ignore_errors=True)

    def _sync_teardown(self) -> None:
        if self._torn_down:
            return
        self._torn_down = True
        for pid in sorted(self._pids):
            _kill_pid(pid)
        shutil.rmtree(self.run_dir, ignore_errors=True)

    async def __aenter__(self) -> "CloneSubstrate":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.teardown()

    # ------------------------------------------------------------------- GC
    @classmethod
    def gc_stale(
        cls,
        *,
        role: str | None = None,
        base_dir: Path = DEFAULT_BASE,
        keep_run_id: str | None = None,
        max_age_s: float = 6 * 3600,
    ) -> list[str]:
        """Idempotent GC of stale run dirs (kill recorded PIDs, rmtree). Safe."""
        base = Path(base_dir)
        roles = [role] if role else [p.name for p in base.glob("*") if p.is_dir()]
        removed: list[str] = []
        now = time.time()
        for r in roles:
            root = base / r
            if not root.is_dir():
                continue
            for run in root.glob("*"):
                if not run.is_dir() or run.name == keep_run_id:
                    continue
                try:
                    age = now - run.stat().st_mtime
                except OSError:
                    age = max_age_s + 1
                # Only GC genuinely STALE (old) run dirs — never recent siblings.
                # Keying on run_id just protects the current run; it must not make
                # GC delete recent, in-use sibling clones.
                if age < max_age_s:
                    continue
                pidfile = run / "pids.json"
                if pidfile.exists():
                    with contextlib.suppress(Exception):
                        for pid in json.loads(pidfile.read_text()):
                            _kill_pid(int(pid))
                shutil.rmtree(run, ignore_errors=True)
                removed.append(str(run))
        return removed


def _kill_pid(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.kill(pid, signal.SIGTERM)
