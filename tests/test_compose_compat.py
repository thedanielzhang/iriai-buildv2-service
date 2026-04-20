from __future__ import annotations

import importlib.metadata as importlib_metadata
from pathlib import Path

import iriai_compose
import iriai_build_v2
import pytest

from iriai_build_v2._compose_compat import (
    _validate_compose_contract,
    canonical_iriai_compose_repo,
    ensure_iriai_compose_compatibility,
)


def test_actual_environment_uses_canonical_iriai_compose_checkout():
    canonical_repo = canonical_iriai_compose_repo(iriai_build_v2.__file__)
    imported_file = Path(iriai_compose.__file__).resolve()

    assert canonical_repo.exists()
    assert imported_file.is_relative_to(canonical_repo)


def test_actual_environment_matches_sibling_compose_version():
    canonical_repo = canonical_iriai_compose_repo(iriai_build_v2.__file__)
    pyproject = canonical_repo / "pyproject.toml"
    text = pyproject.read_text()
    compatibility = ensure_iriai_compose_compatibility(iriai_build_v2.__file__)

    assert 'version = "0.3.0"' in text
    assert compatibility.installed_version == "0.3.0"
    assert importlib_metadata.version("iriai-compose") in {"0.2.0", "0.3.0"}


def test_validate_compose_contract_rejects_noncanonical_checkout(tmp_path: Path):
    canonical_repo = tmp_path / "iriai-compose"
    canonical_repo.mkdir()
    imported_file = tmp_path / ".iriai" / "features" / "lane" / "repos" / "iriai-compose" / "iriai_compose" / "__init__.py"
    imported_file.parent.mkdir(parents=True)
    imported_file.write_text("")

    with pytest.raises(RuntimeError, match="must use the sibling iriai-compose checkout"):
        _validate_compose_contract(
            imported_file=imported_file,
            installed_version="0.3.0",
            effective_version="0.3.0",
            canonical_repo=canonical_repo,
            canonical_version="0.3.0",
            minimum_version="0.3.0",
            has_interaction_ask=True,
            runner_accepts_runtimes=True,
        )


def test_validate_compose_contract_rejects_old_versions(tmp_path: Path):
    canonical_repo = tmp_path / "iriai-compose"
    package_dir = canonical_repo / "iriai_compose"
    package_dir.mkdir(parents=True)
    imported_file = package_dir / "__init__.py"
    imported_file.write_text("")

    with pytest.raises(RuntimeError, match="requires iriai-compose >=0.3.0"):
        _validate_compose_contract(
            imported_file=imported_file,
            installed_version="0.2.9",
            effective_version="0.2.9",
            canonical_repo=canonical_repo,
            canonical_version="0.2.9",
            minimum_version="0.3.0",
            has_interaction_ask=True,
            runner_accepts_runtimes=True,
        )


def test_validate_compose_contract_rejects_missing_new_runner_contract(tmp_path: Path):
    canonical_repo = tmp_path / "iriai-compose"
    package_dir = canonical_repo / "iriai_compose"
    package_dir.mkdir(parents=True)
    imported_file = package_dir / "__init__.py"
    imported_file.write_text("")

    with pytest.raises(RuntimeError, match="InteractionRuntime.ask"):
        _validate_compose_contract(
            imported_file=imported_file,
            installed_version="0.3.0",
            effective_version="0.3.0",
            canonical_repo=canonical_repo,
            canonical_version="0.3.0",
            minimum_version="0.3.0",
            has_interaction_ask=False,
            runner_accepts_runtimes=True,
        )
