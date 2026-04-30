from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Iterable

from ...models.outputs import ImplementationDAG, ImplementationTask


DAG_PATH_CANONICALIZATION_ENV = "IRIAI_DAG_PATH_CANONICALIZATION"

_BACKEND_PATH_REWRITES: tuple[tuple[str, str, str], ...] = (
    (
        "backend-src-prefixed",
        "iriai-studio-backend/src/iriai_studio_backend/",
        "iriai-studio-backend/iriai_studio_backend/",
    ),
    (
        "backend-src-py-prefixed",
        "iriai-studio-backend/src-py/iriai_studio_backend/",
        "iriai-studio-backend/iriai_studio_backend/",
    ),
    (
        "bare-src",
        "src/iriai_studio_backend/",
        "iriai-studio-backend/iriai_studio_backend/",
    ),
    (
        "bare-src-py",
        "src-py/iriai_studio_backend/",
        "iriai-studio-backend/iriai_studio_backend/",
    ),
)


@dataclass(frozen=True)
class DagPathRewrite:
    task_id: str
    field: str
    original: str
    canonical: str
    rule: str


def dag_path_canonicalization_enabled() -> bool:
    return os.environ.get(DAG_PATH_CANONICALIZATION_ENV, "1").strip() != "0"


def canonicalize_dag_path(path: str) -> tuple[str, str | None]:
    """Return (canonical_path, rule) for known retired DAG backend prefixes."""
    normalized = (path or "").strip().replace("\\", "/")
    if not normalized:
        return path, None
    for rule, retired_prefix, canonical_prefix in _BACKEND_PATH_REWRITES:
        if normalized.startswith(retired_prefix):
            return canonical_prefix + normalized[len(retired_prefix):], rule
        marker = f"/{retired_prefix}"
        if marker in normalized:
            return (
                normalized.replace(marker, f"/{canonical_prefix}", 1),
                rule,
            )
    return normalized, None


def has_retired_backend_path_prefix(path: str) -> bool:
    normalized = (path or "").strip().replace("\\", "/")
    if not normalized:
        return False
    return any(
        normalized.startswith(retired_prefix) or f"/{retired_prefix}" in normalized
        for _rule, retired_prefix, _canonical_prefix in _BACKEND_PATH_REWRITES
    )


def canonicalize_implementation_task(
    task: ImplementationTask,
) -> tuple[ImplementationTask, list[DagPathRewrite]]:
    rewrites: list[DagPathRewrite] = []
    updated_scope = []
    for idx, scope in enumerate(task.file_scope):
        canonical, rule = canonicalize_dag_path(scope.path)
        if rule:
            rewrites.append(DagPathRewrite(
                task_id=task.id,
                field=f"file_scope[{idx}].path",
                original=scope.path,
                canonical=canonical,
                rule=rule,
            ))
            updated_scope.append(scope.model_copy(update={"path": canonical}))
        else:
            updated_scope.append(scope)

    updated_files: list[str] = []
    for idx, path in enumerate(task.files):
        canonical, rule = canonicalize_dag_path(path)
        if rule:
            rewrites.append(DagPathRewrite(
                task_id=task.id,
                field=f"files[{idx}]",
                original=path,
                canonical=canonical,
                rule=rule,
            ))
        updated_files.append(canonical)

    if not rewrites:
        return task, rewrites
    return task.model_copy(update={
        "file_scope": updated_scope,
        "files": updated_files,
    }), rewrites


def canonicalize_implementation_tasks(
    tasks: Iterable[ImplementationTask],
) -> tuple[list[ImplementationTask], list[DagPathRewrite]]:
    canonical_tasks: list[ImplementationTask] = []
    rewrites: list[DagPathRewrite] = []
    for task in tasks:
        canonical_task, task_rewrites = canonicalize_implementation_task(task)
        canonical_tasks.append(canonical_task)
        rewrites.extend(task_rewrites)
    return canonical_tasks, rewrites


def canonicalize_implementation_dag(
    dag: ImplementationDAG,
) -> tuple[ImplementationDAG, list[DagPathRewrite]]:
    tasks, rewrites = canonicalize_implementation_tasks(dag.tasks)
    if not rewrites:
        return dag, rewrites
    return dag.model_copy(update={"tasks": tasks}), rewrites


def find_retired_backend_path_references(
    tasks: Iterable[ImplementationTask],
) -> list[DagPathRewrite]:
    references: list[DagPathRewrite] = []
    for task in tasks:
        for idx, scope in enumerate(task.file_scope):
            if has_retired_backend_path_prefix(scope.path):
                references.append(DagPathRewrite(
                    task_id=task.id,
                    field=f"file_scope[{idx}].path",
                    original=scope.path,
                    canonical=canonicalize_dag_path(scope.path)[0],
                    rule="retired-backend-prefix",
                ))
        for idx, path in enumerate(task.files):
            if has_retired_backend_path_prefix(path):
                references.append(DagPathRewrite(
                    task_id=task.id,
                    field=f"files[{idx}]",
                    original=path,
                    canonical=canonicalize_dag_path(path)[0],
                    rule="retired-backend-prefix",
                ))
    return references


def dag_path_rewrites_to_records(
    rewrites: Iterable[DagPathRewrite],
) -> list[dict[str, str]]:
    return [asdict(rewrite) for rewrite in rewrites]
