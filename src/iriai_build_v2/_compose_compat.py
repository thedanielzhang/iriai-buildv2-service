from __future__ import annotations

import importlib
import importlib.metadata as importlib_metadata
import inspect
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

_MIN_IRIAI_COMPOSE_VERSION = "0.3.0"


@dataclass(frozen=True)
class ComposeCompatibility:
    canonical_repo: Path | None
    imported_file: Path
    installed_version: str
    canonical_version: str | None


def canonical_iriai_compose_repo(package_file: str | Path) -> Path:
    return Path(package_file).resolve().parents[3] / "iriai-compose"


def prefer_canonical_iriai_compose(package_file: str | Path) -> Path | None:
    canonical = canonical_iriai_compose_repo(package_file)
    if not canonical.exists():
        return None

    canonical_str = str(canonical)
    if canonical_str in sys.path:
        sys.path.remove(canonical_str)
    sys.path.insert(0, canonical_str)
    return canonical


def _parse_version(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for token in version.split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _read_project_version(pyproject_path: Path) -> str | None:
    if not pyproject_path.exists():
        return None
    data = tomllib.loads(pyproject_path.read_text())
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    version = project.get("version")
    return version if isinstance(version, str) and version else None


def _validate_compose_contract(
    *,
    imported_file: Path,
    installed_version: str | None,
    effective_version: str,
    canonical_repo: Path | None,
    canonical_version: str | None,
    minimum_version: str,
    has_interaction_ask: bool,
    runner_accepts_runtimes: bool,
) -> None:
    if canonical_repo is not None and not imported_file.is_relative_to(canonical_repo):
        raise RuntimeError(
            "iriai-build-v2 must use the sibling iriai-compose checkout. "
            f"Imported iriai_compose from {imported_file}, expected under {canonical_repo}.",
        )

    if _parse_version(effective_version) < _parse_version(minimum_version):
        raise RuntimeError(
            "iriai-build-v2 requires iriai-compose "
            f">={minimum_version}, found {effective_version}.",
        )

    if (
        canonical_version is not None
        and installed_version is not None
        and canonical_repo is not None
        and not imported_file.is_relative_to(canonical_repo)
        and installed_version != canonical_version
    ):
        raise RuntimeError(
            "The imported iriai-compose version does not match the sibling checkout. "
            f"Installed {installed_version}, sibling repo declares {canonical_version}.",
        )

    if not has_interaction_ask:
        raise RuntimeError(
            "The imported iriai-compose is too old for iriai-build-v2: "
            "InteractionRuntime.ask() is missing.",
        )

    if not runner_accepts_runtimes:
        raise RuntimeError(
            "The imported iriai-compose is too old for iriai-build-v2: "
            "DefaultWorkflowRunner.__init__() does not accept runtimes=.",
        )


def ensure_iriai_compose_compatibility(
    package_file: str | Path,
    *,
    minimum_version: str = _MIN_IRIAI_COMPOSE_VERSION,
) -> ComposeCompatibility:
    canonical_repo = canonical_iriai_compose_repo(package_file)
    if not canonical_repo.exists():
        canonical_repo = None

    iriai_compose = importlib.import_module("iriai_compose")
    imported_file = Path(iriai_compose.__file__).resolve()

    installed_version: str | None = None
    try:
        installed_version = importlib_metadata.version("iriai-compose")
    except importlib_metadata.PackageNotFoundError:
        installed_version = None

    canonical_version = (
        _read_project_version(canonical_repo / "pyproject.toml")
        if canonical_repo is not None
        else None
    )
    imported_from_canonical = (
        canonical_repo is not None and imported_file.is_relative_to(canonical_repo)
    )
    effective_version = (
        canonical_version
        if imported_from_canonical and canonical_version is not None
        else installed_version or canonical_version
    )
    if effective_version is None:
        raise RuntimeError(
            "Could not determine the active iriai-compose version. "
            "Install the sibling iriai-compose checkout in editable mode.",
        )

    from iriai_compose.runner import DefaultWorkflowRunner, InteractionRuntime

    _validate_compose_contract(
        imported_file=imported_file,
        installed_version=installed_version,
        effective_version=effective_version,
        canonical_repo=canonical_repo,
        canonical_version=canonical_version,
        minimum_version=minimum_version,
        has_interaction_ask=hasattr(InteractionRuntime, "ask"),
        runner_accepts_runtimes="runtimes"
        in inspect.signature(DefaultWorkflowRunner.__init__).parameters,
    )

    return ComposeCompatibility(
        canonical_repo=canonical_repo,
        imported_file=imported_file,
        installed_version=effective_version,
        canonical_version=canonical_version,
    )
