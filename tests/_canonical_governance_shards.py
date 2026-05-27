"""Helpers for canonical governance global-gate shard wrappers."""

from __future__ import annotations

import importlib
import re
from collections.abc import Iterable, MutableMapping
from typing import Any


def export_canonical_shard_tests(
    target_globals: MutableMapping[str, Any],
    module_names: Iterable[str],
) -> tuple[str, ...]:
    """Expose real backing test surfaces under a canonical shard module.

    The governance implementation prompt names shorthand shard files such as
    ``tests/test_governance_evidence.py``. The landed tests are split across
    more specific modules. This helper makes the shorthand files collect the
    real tests without shelling out to nested pytest runs.
    """

    exported: list[str] = []
    for module_name in module_names:
        module = importlib.import_module(_qualified_module_name(module_name))
        _copy_pytest_fixtures(target_globals, module)
        prefix = _test_prefix(module_name)
        class_prefix = _class_prefix(prefix)
        module_exports: list[str] = []
        for name, value in vars(module).items():
            if name.startswith("test_") and callable(value):
                export_name = f"test_{prefix}__{name.removeprefix('test_')}"
            elif name.startswith("Test") and isinstance(value, type):
                export_name = f"Test{class_prefix}{name.removeprefix('Test')}"
            else:
                continue
            if export_name in target_globals:
                raise RuntimeError(
                    f"canonical governance shard export collision: "
                    f"{export_name!r} from {module_name!r}"
                )
            target_globals[export_name] = value
            exported.append(export_name)
            module_exports.append(export_name)
        if not module_exports:
            raise RuntimeError(
                f"canonical governance shard backing module "
                f"{module_name!r} exported no pytest tests"
            )

    if not exported:
        raise RuntimeError("canonical governance shard exported no pytest tests")
    target_globals["__all__"] = tuple(exported)
    return tuple(exported)


def _qualified_module_name(module_name: str) -> str:
    if module_name.startswith("tests."):
        return module_name
    return f"tests.{module_name}"


def _copy_pytest_fixtures(
    target_globals: MutableMapping[str, Any],
    module: Any,
) -> None:
    for name, value in vars(module).items():
        if (
            hasattr(value, "_pytestfixturefunction")
            or hasattr(value, "_fixture_function_marker")
        ) and name not in target_globals:
            target_globals[name] = value


def _test_prefix(module_name: str) -> str:
    bare_name = module_name.rsplit(".", maxsplit=1)[-1]
    bare_name = bare_name.removeprefix("test_")
    return re.sub(r"[^0-9a-zA-Z_]+", "_", bare_name)


def _class_prefix(test_prefix: str) -> str:
    return "".join(part.capitalize() for part in test_prefix.split("_") if part)
