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
import logging
import os
import shutil
import signal
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_BASE = Path(os.environ.get("IRIAI_E2E_SCRATCH", "/tmp/iriai-e2e"))
_GIT_TIMEOUT_S = float(os.environ.get("IRIAI_E2E_GIT_TIMEOUT_S", "1200"))
_COMPOSE_DOWN_TIMEOUT_S = float(os.environ.get("IRIAI_E2E_COMPOSE_DOWN_TIMEOUT_S", "300"))


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
    _compose_projects: list[dict] = field(
        default_factory=list, init=False, repr=False
    )
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

    @property
    def _composefile(self) -> Path:
        return self.run_dir / "compose.json"

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

    # Standard dep dirs for a multi-package JS project (VS Code fork has several
    # nested node_modules — the build tooling deps live in build/node_modules).
    DEFAULT_DEP_DIRS: tuple[str, ...] = (
        "node_modules",
        "build/node_modules",
        "remote/node_modules",
        "remote/web/node_modules",
        "src/webviews/projectSurface/node_modules",
    )

    async def reuse_prebuilt_deps(
        self,
        checkout: Path,
        source_repo: str,
        *,
        dep_dirs: tuple[str, ...] | None = None,
        include_build: bool = True,
    ) -> list[str]:
        """Mirror the source checkout's installed deps into ``checkout`` via APFS
        clonefile (``cp -Rc``: copy-on-write — fast, space-free, and writes go COW
        so the live source is never mutated). This reuses the checkpoint's
        already-installed ``node_modules`` (every nested one) + the prebuilt
        ``.build`` instead of a fresh ``npm install``, which is the heavy build
        cost the plan calls out. Returns the relative dirs that were mirrored.
        """
        src = Path(source_repo)
        dst = Path(checkout)
        if dep_dirs is not None:
            targets = list(dep_dirs)
        else:
            # Discover EVERY top-level-per-package node_modules (a JS monorepo like
            # a VS Code fork has many: root, build/, remote/, extensions/<each>/...).
            # Exclude node_modules nested inside another node_modules (deps' deps).
            res = subprocess.run(
                ["find", str(src), "-type", "d", "-name", "node_modules",
                 "-not", "-path", "*/node_modules/*"],
                capture_output=True, text=True,
            )
            targets = [str(Path(line).relative_to(src))
                       for line in res.stdout.splitlines() if line.strip()]
        if include_build:
            targets.append(".build")
        copied: list[str] = []
        for rel in targets:
            s = src / rel
            d = dst / rel
            if s.exists() and not d.exists():
                d.parent.mkdir(parents=True, exist_ok=True)
                rc, _, err = await self._run("cp", "-Rc", str(s), str(d), timeout=600)
                if rc == 0:
                    copied.append(rel)
                else:
                    raise SubstrateError(f"clonefile {rel} failed: {err.strip()[:200]}")
        return copied

    async def link_file_deps(
        self, checkout: Path, *, package_jsons: tuple[str, ...] = ("package.json",),
    ) -> list[str]:
        """Recreate the symlinks ``npm install`` makes for ``file:`` deps.

        The clonefile'd ``node_modules`` is reused from the source checkout for
        speed, but it predates any ``file:`` workspace dependency the project
        declared later (the source's own ``node_modules`` may simply lack them).
        npm materializes ``"<name>": "file:./path"`` as a symlink
        ``node_modules/<name> -> <path>``; we reproduce exactly that link from the
        project's OWN ``package.json`` so the project's OWN production build can
        resolve its workspace packages via Node resolution. We never install,
        build, or patch those packages — ONLY the link npm itself would have made
        — so a genuine product build defect (e.g. a CJS/ESM interop break) still
        surfaces honestly as a lane boot failure instead of being masked.

        Returns the dep names that were freshly linked.
        """
        checkout = Path(checkout)
        linked: list[str] = []
        for pj in package_jsons:
            pkg = checkout / pj
            if not pkg.is_file():
                continue
            try:
                data = json.loads(pkg.read_text())
            except (OSError, ValueError):
                continue
            deps = {**(data.get("dependencies") or {}),
                    **(data.get("devDependencies") or {})}
            nm_root = pkg.parent / "node_modules"
            for name, spec in deps.items():
                if not isinstance(spec, str) or not spec.startswith("file:"):
                    continue
                target = pkg.parent / spec[len("file:"):]
                if not target.exists():
                    continue
                link = nm_root / name  # name may be scoped: @scope/pkg
                if link.is_symlink() or link.exists():
                    continue  # a real install / prior link already satisfies it
                link.parent.mkdir(parents=True, exist_ok=True)
                rel = os.path.relpath(target, link.parent)
                with contextlib.suppress(OSError):
                    os.symlink(rel, link)
                    linked.append(name)
        return linked

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

    # ---------------------------------------------------------------- secrets
    def inject_secret_file(
        self, checkout: Path, src_path: str | Path, rel_dst: str
    ) -> Path:
        """Copy an orchestrator-side secret env file INTO the clone.

        The source is the build-system secret store — NEVER the product checkout
        (which is never read for secrets nor modified). Copies content only (no
        ``git add``, never log the contents), chmod 0600, and returns the dest.
        A missing source RAISES so the caller surfaces an honest ``BootSmoke``
        fail rather than booting un-authenticated / false-green (AC-K-10).
        """
        src = Path(src_path)
        if not src.is_file():
            raise SubstrateError(
                f"secret source not found: {src} — copy the env file into the "
                f"orchestrator secret store (never read the product repo for it)"
            )
        dst = Path(checkout) / rel_dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)  # content only — never copy mode/owner metadata
        with contextlib.suppress(OSError):
            dst.chmod(0o600)
        return dst

    # ----------------------------------------------------- compose lifecycle
    def register_compose_project(
        self,
        project: str,
        *,
        workdir: str | Path,
        compose_files: list[str],
        env_file: str | None = None,
    ) -> None:
        """Record a docker-compose project so EVERY teardown path tears it down.

        ``compose down -v --remove-orphans`` runs in :meth:`teardown` (in-process),
        :meth:`_sync_teardown` (atexit), AND cross-process :meth:`gc_stale` — the
        last reads a ``compose.json`` sidecar (next to ``pids.json``), so a stack
        leaked by a crash is still reaped, leaving zero stray containers/volumes
        (AC-K-9). Persisted on registration.
        """
        entry = {
            "project": str(project),
            "workdir": str(workdir),
            "compose_files": [str(f) for f in compose_files],
            "env_file": str(env_file) if env_file else "",
        }
        if entry not in self._compose_projects:
            self._compose_projects.append(entry)
        self._persist_compose()

    def _persist_compose(self) -> None:
        with contextlib.suppress(OSError):
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self._composefile.write_text(json.dumps(self._compose_projects))

    @staticmethod
    def _compose_down_argv(entry: dict) -> list[str]:
        argv = ["docker", "compose", "-p", str(entry.get("project", ""))]
        for compose_file in entry.get("compose_files") or []:
            argv += ["-f", str(compose_file)]
        env_file = entry.get("env_file")
        if env_file:
            argv += ["--env-file", str(env_file)]
        argv += ["down", "-v", "--remove-orphans"]
        return argv

    # ------------------------------------------------------------- teardown
    async def teardown(self) -> None:
        if self._torn_down:
            return
        self._torn_down = True
        # Compose stacks first (before pid-kill/rmtree) so `down -v` can read the
        # still-present compose files and remove the per-run named volumes.
        for entry in list(self._compose_projects):
            argv = self._compose_down_argv(entry)
            workdir = entry.get("workdir") or None
            try:
                rc, _, err = await self._run(
                    *argv,
                    cwd=Path(workdir) if workdir else None,
                    timeout=_COMPOSE_DOWN_TIMEOUT_S,
                )
                if rc != 0:
                    logger.warning(
                        "compose down rc=%s for project %s: %s",
                        rc, entry.get("project"), err.strip()[-300:],
                    )
            except Exception as exc:  # noqa: BLE001 - teardown is best-effort
                logger.warning(
                    "compose down errored for project %s: %s",
                    entry.get("project"), exc,
                )
        for pid in sorted(self._pids):
            _kill_pid(pid)
        await asyncio.sleep(0)  # let signals propagate
        with contextlib.suppress(OSError):
            shutil.rmtree(self.run_dir, ignore_errors=True)

    def _sync_teardown(self) -> None:
        if self._torn_down:
            return
        self._torn_down = True
        for entry in list(self._compose_projects):
            _compose_down_sync(entry)
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
                # Tear down any leaked compose stack BEFORE rmtree (the down needs
                # the still-present compose files); reaps a stack a crash left up.
                composefile = run / "compose.json"
                if composefile.exists():
                    with contextlib.suppress(Exception):
                        for entry in json.loads(composefile.read_text()):
                            _compose_down_sync(entry)
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


def _compose_down_sync(entry: dict) -> None:
    """Best-effort synchronous ``docker compose down -v`` (atexit / GC paths)."""
    argv = CloneSubstrate._compose_down_argv(entry)
    workdir = entry.get("workdir") or None
    try:
        result = subprocess.run(
            argv,
            cwd=workdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=_COMPOSE_DOWN_TIMEOUT_S,
        )
        if result.returncode != 0:
            logger.warning(
                "compose down rc=%s for project %s: %s",
                result.returncode,
                entry.get("project"),
                result.stderr.decode(errors="replace").strip()[-300:],
            )
    except Exception as exc:  # noqa: BLE001 - GC/atexit cleanup is best-effort
        logger.warning(
            "compose down errored for project %s: %s", entry.get("project"), exc
        )
