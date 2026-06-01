from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ...models.outputs import (
    DagPathResolution,
    ImplementationDAG,
    ImplementationTask,
)


DAG_PATH_CANONICALIZATION_ENV = "IRIAI_DAG_PATH_CANONICALIZATION"
DAG_PATH_AGENTIC_RESOLVER_ENV = "IRIAI_DAG_PATH_AGENTIC_RESOLVER"


def feature_repos_root(runner: Any, feature: Any) -> str:
    """Absolute directory containing the feature's per-repo checkouts, or "".

    Layout (mirrors implementation._get_feature_root):
    ``<workspace_base>/.iriai/features/<feature.slug>/repos/<repo_path>/<file>``.
    A task path resolves to ``<repos_root>/<task.repo_path>/<file_scope path>``.

    Returns "" when the workspace manager or the on-disk checkout is unavailable
    (e.g. planning before checkout, or unit tests) so callers SKIP resolution
    rather than mis-resolve against the wrong base."""
    services = getattr(runner, "services", None)
    getter = getattr(services, "get", None) if services is not None else None
    wm = getter("workspace_manager") if callable(getter) else None
    base = getattr(wm, "_base", None) if wm is not None else None
    slug = getattr(feature, "slug", None)
    if not base or not slug:
        return ""
    root = Path(base) / ".iriai" / "features" / slug / "repos"
    return str(root) if root.exists() else ""


def build_dag_path_resolver_prompt(
    dag: ImplementationDAG,
    unresolved: list[dict[str, str]],
    repos_root: str,
) -> str:
    """Shared resolver prompt (planning seam + execution migration).

    The agent confirms each candidate at ``<repos_root>/<repo_path>/<path>`` via
    Glob/Read/Grep and returns the path RELATIVE TO the repo root
    (``<repos_root>/<repo_path>``) — the same repo-relative form the DAG uses."""
    repo_by_task = {task.id: (task.repo_path or "") for task in dag.tasks}
    candidates = [
        {
            "task_id": entry["task_id"],
            "repo_path": repo_by_task.get(entry["task_id"], ""),
            "field": entry["field"],
            "path": entry["path"],
            "action": entry.get("action", ""),
        }
        for entry in unresolved
    ]
    return (
        "Resolve these implementation-DAG task paths against the REAL repository "
        f"checkouts under `{repos_root}`. Each candidate file is expected at "
        "`<repos_root>/<repo_path>/<path>` (absolute, readable). Use Glob/Read/Grep "
        "to find the real file and return the path RELATIVE TO the repo root "
        "(`<repos_root>/<repo_path>`), i.e. the same repo-relative form as `path`.\n\n"
        f"repos_root: {repos_root}\n\n"
        "UNRESOLVED candidate paths (JSON):\n"
        f"```json\n{json.dumps(candidates, indent=2)}\n```\n\n"
        "For EACH entry return exactly one DagPathDecision (copy task_id and field "
        "verbatim): `correct` + `resolved` (the real repo-relative path) + `evidence` "
        "(the Glob/Grep hit) when there is a UNIQUE real match; `keep` when the path "
        "is already correct; `create_ok` for a legitimate NEW file in a real "
        "directory; `ambiguous` when you cannot find a unique match — NEVER guess. "
        "Set corrected_count and ambiguous_count accordingly."
    )

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


def dag_path_agentic_resolver_enabled() -> bool:
    """Agentic DAG path resolution is the default (the static shim is the fallback)."""
    return os.environ.get(DAG_PATH_AGENTIC_RESOLVER_ENV, "1").strip() != "0"


class AmbiguousDagPath(Exception):
    """Raised when the agentic resolver could not uniquely resolve one or more
    paths. Callers MUST fail-safe (surface to operator, non-retryable) rather
    than guess — guessing a wrong path is exactly what produced the 199-attempt
    repair loop."""

    def __init__(self, decisions: Iterable) -> None:
        self.decisions = list(decisions)
        detail = ", ".join(
            f"{getattr(d, 'task_id', '?')}:{getattr(d, 'field', '?')}="
            f"{getattr(d, 'original', '?')!r}"
            for d in self.decisions
        )
        super().__init__(
            f"agentic DAG path resolver could not uniquely resolve: {detail}"
        )


def _task_path_fields(task: ImplementationTask) -> Iterable[tuple[str, str, str]]:
    """Yield (field_key, path, action) for every addressable path on a task.

    field_key matches the convention the resolver returns in DagPathDecision so
    apply_path_resolution can match decisions back deterministically."""
    for idx, scope in enumerate(task.file_scope):
        yield f"file_scope[{idx}].path", scope.path, (scope.action or "").strip().lower()
    for idx, path in enumerate(task.files):
        yield f"files[{idx}]", path, ""  # legacy list carries no action


def unresolved_dag_paths(
    dag: ImplementationDAG,
    workspace_root: str,
    *,
    exists: Callable[[str], bool] = os.path.exists,
    isdir: Callable[[str], bool] = os.path.isdir,
) -> list[dict[str, str]]:
    """Cheap deterministic existence prepass. Returns the paths that do NOT
    resolve against the real repo; an empty result means the agent can be SKIPPED.

    Conservative on purpose: ``modify``/``read_only`` (and legacy) entries must
    point at an existing file; ``create`` entries only require their parent
    directory to already exist (the file itself does not yet). The resolver is the
    authority for anything this can't confirm. NOTE: a path whose stub file was
    written by a prior failed attempt will look "resolved" here — callers that
    must not trust on-disk stubs (the one-time migration) bypass this prepass and
    invoke the resolver unconditionally."""
    unresolved: list[dict[str, str]] = []
    for task in dag.tasks:
        repo_root = (
            os.path.join(workspace_root, task.repo_path)
            if task.repo_path
            else workspace_root
        )
        for field, path, action in _task_path_fields(task):
            if not path:
                continue
            abs_path = os.path.join(repo_root, path)
            if action == "create":
                ok = isdir(os.path.dirname(abs_path) or repo_root)
            elif field.startswith("files["):
                # legacy list may include to-be-created files: lenient
                ok = exists(abs_path) or isdir(os.path.dirname(abs_path) or repo_root)
            else:
                ok = exists(abs_path)
            if not ok:
                unresolved.append(
                    {"task_id": task.id, "field": field, "path": path, "action": action}
                )
    return unresolved


def apply_path_resolution(
    dag: ImplementationDAG,
    resolution: DagPathResolution,
) -> tuple[ImplementationDAG, list[DagPathRewrite]]:
    """Apply the resolver's ``correct`` decisions to the DAG (pure, deterministic).

    ``keep``/``create_ok`` leave the path untouched; any ``ambiguous`` decision
    raises AmbiguousDagPath so the caller fails-safe. Decisions are matched by
    (task_id, field_key) and only applied when the recorded ``original`` still
    matches the live path, so re-applying the same resolution is idempotent."""
    decisions = {(d.task_id, d.field): d for d in resolution.decisions}
    ambiguous = [
        d for d in resolution.decisions
        if (d.decision or "").strip().lower() == "ambiguous"
    ]
    if ambiguous:
        raise AmbiguousDagPath(ambiguous)

    rewrites: list[DagPathRewrite] = []

    def _corrected(field_key: str, current: str, task_id: str) -> str | None:
        d = decisions.get((task_id, field_key))
        if (
            d is not None
            and (d.decision or "").strip().lower() == "correct"
            and d.original == current
            and d.resolved
            and d.resolved != current
        ):
            return d.resolved
        return None

    new_tasks: list[ImplementationTask] = []
    for task in dag.tasks:
        updated_scope = list(task.file_scope)
        updated_files = list(task.files)
        changed = False
        for idx, scope in enumerate(task.file_scope):
            resolved = _corrected(f"file_scope[{idx}].path", scope.path, task.id)
            if resolved is not None:
                rewrites.append(DagPathRewrite(
                    task_id=task.id,
                    field=f"file_scope[{idx}].path",
                    original=scope.path,
                    canonical=resolved,
                    rule="agentic-resolver",
                ))
                updated_scope[idx] = scope.model_copy(update={"path": resolved})
                changed = True
        for idx, path in enumerate(task.files):
            resolved = _corrected(f"files[{idx}]", path, task.id)
            if resolved is not None:
                rewrites.append(DagPathRewrite(
                    task_id=task.id,
                    field=f"files[{idx}]",
                    original=path,
                    canonical=resolved,
                    rule="agentic-resolver",
                ))
                updated_files[idx] = resolved
                changed = True
        new_tasks.append(
            task.model_copy(update={"file_scope": updated_scope, "files": updated_files})
            if changed
            else task
        )

    if not rewrites:
        return dag, rewrites
    return dag.model_copy(update={"tasks": new_tasks}), rewrites


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
