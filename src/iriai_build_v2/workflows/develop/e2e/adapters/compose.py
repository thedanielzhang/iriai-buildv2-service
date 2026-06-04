"""docker-compose adapter — boots a multi-container product stack for e2e.

Drives the product's OWN compose stack from an out-of-tree clone, but never
trusts its env_file-only instance override for isolation: this adapter writes a
per-run override that (a) optionally bumps host ports and (b) remaps read-write
relative data binds (e.g. ``./postgres/data``) to per-run NAMED volumes, so
``down -v`` gives clean per-run state + deterministic teardown without touching
the operator's own running dev stack. Read-only binds (``:ro`` seed scripts /
source) are left untouched.

The pure helpers (:func:`build_compose_override`, :func:`build_surfaces`,
:func:`resolve_secret_source`) are unit-tested without Docker; the bring-up /
host-test execution is exercised live in P6 (operator-gated). Boot is the
``spend-client`` compose profile only — mobile is out of boot, ``amber-service``
only if explicitly added to ``compose_profiles``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import shlex
from pathlib import Path
from typing import Any

import yaml

from ..models import BootSmoke, E2ESpecRecord, E2EVerdictRecord, ProjectProfile
from . import Instance, Surface, probe_surface, register_adapter
from .junit_report import parse_junit_xml

_COMPOSE_UP_TIMEOUT_S = float(os.environ.get("IRIAI_E2E_COMPOSE_UP_TIMEOUT_S", "1800"))
_HOST_TEST_TIMEOUT_S = float(os.environ.get("IRIAI_E2E_HOST_TEST_TIMEOUT_S", "1200"))


class ComposeAdapterError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Pure helpers (no Docker) — unit-tested.
# --------------------------------------------------------------------------- #


def _sanitize(name: str) -> str:
    """Docker name-safe token ([a-zA-Z0-9._-])."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", name)


def _port_offset(run_id: str) -> int:
    """Deterministic per-run host-port offset (10000..29999) from the run id."""
    digest = int(hashlib.sha256(run_id.encode()).hexdigest()[:8], 16)
    return 10000 + (digest % 20000)


def _bump_port(spec: Any, offset: int) -> Any:
    """Add ``offset`` to the published HOST port of a compose port spec."""
    if isinstance(spec, dict):
        published = spec.get("published")
        if published is not None:
            with contextlib.suppress(ValueError, TypeError):
                return {**spec, "published": int(published) + offset}
        return spec
    if isinstance(spec, str):
        parts = spec.split(":")
        if len(parts) == 2:  # host:container
            with contextlib.suppress(ValueError):
                return f"{int(parts[0]) + offset}:{parts[1]}"
        if len(parts) == 3:  # ip:host:container
            with contextlib.suppress(ValueError):
                return f"{parts[0]}:{int(parts[1]) + offset}:{parts[2]}"
    return spec


def build_compose_override(
    base_compose: dict,
    *,
    run_id: str,
    port_strategy: str = "fixed",
    project_prefix: str = "e2e",
    named_volume_targets: list[str] | None = None,
) -> dict:
    """Pure: build a per-run compose override dict (YAML-serializable).

    - Named volumes: a host bind is replaced by a per-run named volume ONLY when
      its CONTAINER TARGET is in ``named_volume_targets`` (the profile's declared
      data dirs, e.g. ``/var/lib/postgresql/data``). This is OPT-IN by target —
      NOT a "remap every relative bind" heuristic, which would wrongly clobber
      seed-script binds (kaya's ``init_scripts`` has no ``:ro``), ``*.conf`` config
      binds, and ``../../`` source mounts. Data isolation for any remaining
      relative bind comes from the per-run clone (rmtree'd on teardown); ``down
      -v`` removes the named volumes + anonymous volumes.
    - Ports: ``fixed``/``""`` => unchanged (the single-stack mutex serialises
      passes so the product's own fixed ports are free). ``bump`` => add a
      deterministic ``hash(run_id)`` offset to each published host port.
    """
    services = base_compose.get("services") or {}
    targets = set(named_volume_targets or [])
    offset = _port_offset(run_id) if port_strategy == "bump" else 0
    override_services: dict[str, dict] = {}
    named_volumes: dict[str, Any] = {}

    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        svc_override: dict[str, Any] = {}

        new_volumes: list[Any] = []
        remapped = False
        for idx, vol in enumerate(svc.get("volumes") or []):
            if isinstance(vol, str):
                parts = vol.split(":")
                # parts[1] is the container target of a host:container[:mode] bind.
                container = parts[1] if len(parts) >= 2 else ""
                if container and container in targets:
                    vol_name = _sanitize(
                        f"{project_prefix}_{run_id}_{svc_name}_{idx}"
                    )
                    named_volumes[vol_name] = None  # default driver, per-run
                    tail = ":".join(parts[1:])  # container[:mode]
                    new_volumes.append(f"{vol_name}:{tail}")
                    remapped = True
                    continue
            new_volumes.append(vol)
        if remapped:
            svc_override["volumes"] = new_volumes

        if offset:
            new_ports = [_bump_port(p, offset) for p in (svc.get("ports") or [])]
            if new_ports and new_ports != list(svc.get("ports") or []):
                svc_override["ports"] = new_ports

        if svc_override:
            override_services[svc_name] = svc_override

    override: dict[str, Any] = {}
    if override_services:
        override["services"] = override_services
    if named_volumes:
        override["volumes"] = named_volumes
    return override


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() or (parent / ".git").exists():
            return parent
    return here.parents[6]


def resolve_secret_source(profile: Any, project_slug: str) -> Path | None:
    """Resolve the orchestrator-side secret env file (NEVER the product repo).

    Order: ``profile.secret_source_path`` -> env ``IRIAI_E2E_SECRET_SRC`` ->
    default ``<iriai-build-v2>/.iriai-secrets/<project>/.env.local``. Returns the
    first existing file, else ``None`` (the caller turns that into an honest
    BootSmoke fail rather than a false green).
    """
    default = _repo_root() / ".iriai-secrets" / project_slug / ".env.local"
    for candidate in (
        str(getattr(profile, "secret_source_path", "") or ""),
        os.environ.get("IRIAI_E2E_SECRET_SRC", ""),
        str(default),
    ):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def build_surfaces(profile: Any) -> list[Surface]:
    """Per-service boot-smoke surfaces from the profile (index-aligned lists).

    Probe kind is inferred from the target: an ``http(s)://`` target => http_get
    (frontend ``/`` 200, backends ``/health``); anything else (``host:port``) =>
    tcp_connect (DB/cache readiness). Empty targets are skipped.
    """
    names = list(getattr(profile, "service_names", None) or [])
    targets = list(getattr(profile, "service_probe_targets", None) or [])
    surfaces: list[Surface] = []
    for idx, name in enumerate(names):
        target = targets[idx] if idx < len(targets) else ""
        if not target:
            continue
        kind = (
            "http_get"
            if target.startswith(("http://", "https://"))
            else "tcp_connect"
        )
        surfaces.append(Surface(name=name, probe_kind=kind, probe_target=target))
    return surfaces


def run_to_verdicts(
    run: Any, *, suite: str, source_commit: str = "", critical: bool = False
) -> list[E2EVerdictRecord]:
    """Convert a parsed (PwRunResult-shaped) test run into E2EVerdictRecords."""
    verdicts: list[E2EVerdictRecord] = []
    for test in run.tests:
        if test.status == "passed":
            status, failure_class = "pass", ""
        elif test.status == "skipped":
            status, failure_class = "skipped", ""
        else:
            status = "fail"
            failure_class = "flaky" if test.flaky else "regression"
        verdicts.append(
            E2EVerdictRecord(
                spec_id=f"{suite}:{test.title}" if suite else test.title,
                source_commit=source_commit,
                status=status,
                failure_class=failure_class,
                summary=(test.error or test.status)[:500],
                critical=critical,
            )
        )
    # A harness/collection failure with no tests executed is an honest infra
    # error verdict — never silently zero failures.
    if not run.web_server_ok or (run.global_errors and not run.started):
        verdicts.append(
            E2EVerdictRecord(
                spec_id=f"{suite}:_boot" if suite else "_boot",
                source_commit=source_commit,
                status="error",
                failure_class="infra",
                summary=("; ".join(run.global_errors)[:500] or "no tests executed"),
                critical=critical,
            )
        )
    return verdicts


# --------------------------------------------------------------------------- #
# Adapter (the bring-up + host-test execution are live-validated in P6).
# --------------------------------------------------------------------------- #


class ComposeAdapter:
    adapter_id = "compose"

    async def provision(
        self,
        profile: ProjectProfile,
        checkout_dir: Path,
        *,
        runner: Any = None,
        feature: Any = None,
        substrate: Any = None,
        run_id: str | None = None,
        project_slug: str = "",
    ) -> Instance:
        """Inject the secret, write the per-run override, and bring the stack up.

        ``substrate`` (the CloneSubstrate) is required so the injected secret and
        the compose project are recorded for teardown; ``run_id`` keys the
        per-run override/volumes/project name.
        """
        checkout = Path(checkout_dir)
        rid = run_id or getattr(substrate, "run_id", "run")
        project = _sanitize(f"{(profile.compose_project_prefix or 'e2e')}_{rid}")
        slug = project_slug or (profile.compose_project_prefix or "default")

        if substrate is None:
            raise ComposeAdapterError("compose provision requires a substrate")

        # 1. Inject the orchestrator-side secret (honest fail if absent).
        src = resolve_secret_source(profile, slug)
        if src is None:
            raise ComposeAdapterError(
                "no secret source found (profile.secret_source_path / "
                "IRIAI_E2E_SECRET_SRC / .iriai-secrets/<project>/.env.local)"
            )
        rel_dst = profile.secret_rel_dst or "common/docker/.env.local"
        env_file = substrate.inject_secret_file(checkout, src, rel_dst)

        # 2. Generated per-run override (own it — don't trust the product's).
        base_name = profile.compose_file or "docker-compose.yaml"
        base_path = checkout / base_name
        base = yaml.safe_load(base_path.read_text()) or {}
        override = build_compose_override(
            base,
            run_id=rid,
            port_strategy=profile.compose_port_strategy or "fixed",
            project_prefix=profile.compose_project_prefix or "e2e",
            named_volume_targets=list(
                getattr(profile, "compose_named_volume_targets", None) or []),
        )
        override_path = checkout / f"docker-compose.e2e.{_sanitize(rid)}.yaml"
        override_path.write_text(yaml.safe_dump(override, sort_keys=False))

        compose_files = [str(base_path), str(override_path)]
        # Register BEFORE up so a crash mid-build is still reaped by GC (AC-K-9).
        substrate.register_compose_project(
            project,
            workdir=str(checkout),
            compose_files=compose_files,
            env_file=str(env_file),
        )

        # 3. Bring the stack up (detached; never stream container logs on-loop).
        argv = ["docker", "compose", "-p", project, "--env-file", str(env_file)]
        for compose_file in compose_files:
            argv += ["-f", compose_file]
        for prof in profile.compose_profiles or []:
            argv += ["--profile", prof]
        argv += ["up", "-d", "--build"]
        rc, _, err = await _run(argv, cwd=checkout, timeout=_COMPOSE_UP_TIMEOUT_S)
        if rc != 0:
            raise ComposeAdapterError(
                f"compose up failed (rc={rc}) for project {project}: "
                f"{err.strip()[-500:]}"
            )

        instance = Instance(
            profile=profile,
            checkout_dir=checkout,
            surfaces=build_surfaces(profile),
            env={},
        )
        instance.substrate = substrate
        instance.notes = project
        return instance

    async def seed(self, instance: Instance, profile: ProjectProfile) -> None:
        if profile.seed_cmd:
            await _run(
                shlex.split(profile.seed_cmd),
                cwd=instance.checkout_dir,
                timeout=600,
            )

    async def smoke(
        self, instance: Instance, profile: ProjectProfile
    ) -> list[BootSmoke]:
        """Per-service readiness (frontend / 200, backends /health, DB tcp)."""
        out: list[BootSmoke] = []
        for surface in instance.surfaces:
            out.append(await probe_surface(surface, timeout_s=180))
        return out

    async def author(
        self, instance: Instance, scenarios: list[Any], *, runner: Any, feature: Any
    ) -> list[E2ESpecRecord]:
        return []

    async def run(
        self,
        instance: Instance,
        specs: list[E2ESpecRecord],
        *,
        runner: Any = None,
        feature: Any = None,
        source_commit: str = "",
    ) -> list[E2EVerdictRecord]:
        """Run the product's OWN unit tests on the HOST (vitest/pytest --junitxml)
        and parse the JUnit reports into verdicts. Index-aligned with
        ``service_names``; each cmd writes ``<checkout>/.e2e-junit-<i>.xml``.
        """
        profile = instance.profile
        checkout = Path(instance.checkout_dir)
        names = list(profile.service_names or [])
        cmds = list(profile.service_test_cmds or [])
        verdicts: list[E2EVerdictRecord] = []
        for idx, cmd in enumerate(cmds):
            if not cmd:
                continue
            suite = names[idx] if idx < len(names) else f"svc{idx}"
            report_path = checkout / f".e2e-junit-{idx}.xml"
            with contextlib.suppress(OSError):
                report_path.unlink()
            await _run(
                shlex.split(cmd.format(junit=str(report_path))),
                cwd=checkout,
                timeout=_HOST_TEST_TIMEOUT_S,
            )
            if report_path.is_file():
                parsed = parse_junit_xml(report_path.read_text(errors="replace"))
            else:
                from .playwright_report import PwRunResult

                parsed = PwRunResult(
                    global_errors=[f"{suite}: no JUnit report at {report_path.name}"]
                )
            verdicts += run_to_verdicts(
                parsed, suite=suite, source_commit=source_commit
            )
        return verdicts

    async def teardown(self, instance: Instance) -> None:
        # The substrate owns `docker compose down -v` (registered at provision)
        # + rmtree; calling it here covers the in-process success/failure paths.
        if instance.substrate is not None:
            with contextlib.suppress(Exception):
                await instance.substrate.teardown()


async def _run(
    argv: list[str], *, cwd: Path | None = None, timeout: float
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
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
        return 124, "", f"timeout after {timeout:.0f}s: {' '.join(argv)}"
    return (
        proc.returncode or 0,
        out.decode(errors="replace"),
        err.decode(errors="replace"),
    )


register_adapter(ComposeAdapter())
