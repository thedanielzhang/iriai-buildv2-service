from __future__ import annotations

import json
import logging
import os
import posixpath
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ...models.outputs import (
    DagPathResolution,
    ImplementationDAG,
    ImplementationTask,
)

logger = logging.getLogger(__name__)


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


def feature_workspace_root(runner: Any, feature: Any) -> str:
    """Absolute workspace base directory, or "".

    Some planned deliverables live at the WORKSPACE level rather than inside
    any per-repo checkout (e.g. authored-not-executed migration documents
    under the feature's ``docs/`` tree). Grounding such create-class entries
    needs the workspace base, which never appears under ``repos_root``."""
    services = getattr(runner, "services", None)
    getter = getattr(services, "get", None) if services is not None else None
    wm = getter("workspace_manager") if callable(getter) else None
    base = getattr(wm, "_base", None) if wm is not None else None
    if not base:
        return ""
    root = Path(base)
    return str(root) if root.exists() else ""


def _normalize_planned_path(path: str) -> str:
    """Comparison-time normalization for planned-new-file matching (never used
    to rewrite stored paths): backslashes -> '/', strip whitespace and a
    leading './'."""
    normalized = (path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def planned_new_file_paths(dag: ImplementationDAG) -> set[str]:
    """Every NET-NEW file this DAG/fragment plans to author itself.

    The set is built from all ``create``-action ``file_scope`` paths across the
    DAG, in BOTH prefix conventions (the raw ``path`` value and the
    ``<repo_path>/<path>`` join) since the DAG mixes conventions. A
    non-existing path in this set is expected — the plan creates it — so the
    resolver must treat it as ``create_ok``, never ``ambiguous``."""
    planned: set[str] = set()
    for task in dag.tasks:
        repo_path = _normalize_planned_path(task.repo_path or "")
        for _field, path, action in _file_scope_path_fields(task):
            if action != "create":
                continue
            normalized = _normalize_planned_path(path)
            if not normalized:
                continue
            planned.add(normalized)
            if repo_path:
                planned.add(f"{repo_path}/{normalized}")
    return planned


def _entry_index(
    dag: ImplementationDAG,
) -> dict[tuple[str, str], tuple[str, str, str]]:
    """{(task_id, field_key): (action, path, repo_path)} for every ``file_scope``
    entry — the disposition (CREATE vs MODIFY class) lookup for the backstop."""
    index: dict[tuple[str, str], tuple[str, str, str]] = {}
    for task in dag.tasks:
        for field, path, action in _file_scope_path_fields(task):
            index[(task.id, field)] = (action, path, task.repo_path or "")
    return index


def _create_parent_grounded(
    repos_root: str,
    repo_path: str,
    path: str,
    planned: set[str],
    *,
    exists: Callable[[str], bool],
    workspace_root: str = "",
) -> bool:
    """CREATE-class grounding: the parent directory resolves — it EXISTS under
    either join, or it is ITSELF created by the fragment (another planned-new
    file, not this entry, lives in or under it). A solo new file in a solo new
    directory with no on-disk parent stays UNgrounded (conservative fail-safe).

    With ``repos_root`` unavailable (pure/unit mode) the on-disk half is
    skipped and only the fragment-created half applies; callers in that mode
    treat create-class entries permissively (the prepass already established
    the file does not exist)."""
    normalized = _normalize_planned_path(path)
    repo_norm = _normalize_planned_path(repo_path or "")
    self_forms = {normalized}
    if repo_norm:
        self_forms.add(f"{repo_norm}/{normalized}")
    parents = {posixpath.dirname(form) for form in self_forms}
    parents.discard("")
    # Fragment-created parent: a DIFFERENT planned-new file at or under it.
    for candidate in planned - self_forms:
        candidate_dir = posixpath.dirname(candidate)
        for parent in parents:
            if candidate_dir == parent or candidate_dir.startswith(parent + "/"):
                return True
    if repos_root:
        for parent in parents:
            if exists(os.path.join(repos_root, parent)):
                return True
        # Nearest-existing-ancestor (mkdir -p semantics): creating a file
        # implicitly creates its directory chain, so a create-class entry whose
        # nearest EXISTING ancestor sits at most two levels above the parent is
        # grounded (e.g. new supply-chain/tests/submittals/ under the existing
        # supply-chain/tests/). Deeper orphans stay ungrounded — a wholly novel
        # tree is exactly the typo class the conservative fail-safe exists for.
        for parent in parents:
            ancestor = parent
            for _ in range(2):
                ancestor = posixpath.dirname(ancestor)
                if not ancestor:
                    break
                if exists(os.path.join(repos_root, ancestor)):
                    return True
    if workspace_root:
        # Workspace-level deliverable (e.g. an authored-not-executed migration
        # document under the feature's docs/ tree): the location never exists
        # under repos_root, but the same parent/ancestor walk against the
        # WORKSPACE base grounds it. Same conservatism: parent exists, or the
        # nearest existing ancestor sits at most two levels above it.
        for parent in parents:
            if exists(os.path.join(workspace_root, parent)):
                return True
            ancestor = parent
            for _ in range(2):
                ancestor = posixpath.dirname(ancestor)
                if not ancestor:
                    break
                if exists(os.path.join(workspace_root, ancestor)):
                    return True
    return False


_BASENAME_SCAN_PRUNE_DIRS = frozenset({
    ".git", "node_modules", "dist", "build", "out", ".venv", "venv",
    "__pycache__", ".next", ".turbo", "coverage", "vendor", ".cache",
    ".iriai",  # a checkout may embed workspace mirrors — never scan them
})


def count_basename_matches(repos_root: str, basename: str) -> int:
    """Count existing files under ``repos_root`` named ``basename`` (pruned
    walk, early exit after 2 — only zero/nonzero matters to the backstop)."""
    if not repos_root or not basename:
        return 0
    matches = 0
    for dirpath, dirnames, filenames in os.walk(repos_root):
        dirnames[:] = [d for d in dirnames if d not in _BASENAME_SCAN_PRUNE_DIRS]
        if basename in filenames:
            matches += 1
            if matches >= 2:
                return matches
    return matches


def resolution_covers_unresolved(
    resolution: DagPathResolution,
    unresolved: list[dict[str, str]],
) -> bool:
    """True when every CURRENT unresolved entry has a decision addressing it
    (same task_id + field, same recorded original path).

    Resume gate: a persisted ``dag-path-resolution`` artifact is replay-stable
    ONLY for the fragment it was produced for. After a slice re-plan the new
    fragment's entries differ; reusing the stale resolution would silently skip
    resolution of the new paths. Callers re-dispatch when this returns False."""
    have = {
        (d.task_id, d.field, _normalize_planned_path(getattr(d, "original", "") or ""))
        for d in resolution.decisions
    }
    return all(
        (entry["task_id"], entry["field"], _normalize_planned_path(entry["path"]))
        in have
        for entry in unresolved
    )


def build_dag_path_resolver_prompt(
    dag: ImplementationDAG,
    unresolved: list[dict[str, str]],
    repos_root: str,
    *,
    extra_planned: set[str] | None = None,
) -> str:
    """Shared resolver prompt (planning seam + execution migration).

    The DAG mixes path conventions: a candidate file lives at EITHER
    ``<repos_root>/<path>`` (the path already embeds the repo-name prefix) OR
    ``<repos_root>/<repo_path>/<path>`` (the path is repo-internal). ``repo_path``
    is also unreliable, so the agent must Glob/Grep to find the real file. It
    returns ``resolved`` in the SAME prefix-convention as the input ``path`` so
    the deterministic apply step can match the value back."""
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
    planned_new = sorted(planned_new_file_paths(dag) | (extra_planned or set()))
    planned_section = (
        "PLANNED NEW FILES (action `create` — authored by THIS plan OR by an "
        "earlier subfeature's plan in the same feature; do not expect them on "
        "disk):\n"
        f"```json\n{json.dumps(planned_new, indent=2)}\n```\n"
        "DISPOSITION RULES — branch on each candidate's `action`:\n"
        "- `create` entries: the file is authored by this plan, so 'not on "
        "disk' is EXPECTED and is never by itself grounds for `ambiguous`. "
        "Instead validate that (a) the path does NOT already exist, and (b) "
        "its PARENT DIRECTORY resolves: the directory exists under either "
        "join, or the plan itself creates it (other planned new files live in "
        "or under it). When both hold return `create_ok`, NOT `ambiguous` — "
        "even though the file does not exist yet. If the parent location is a "
        "phantom (no such directory anywhere and the plan does not create "
        "it), Glob for the intended real location and return `correct` with "
        "evidence, else `ambiguous`.\n"
        "- `modify`/`read_only`/`read` entries: must resolve to a real, "
        "uniquely-located file on disk (current rules below). ONE exception: "
        "when the path EXACTLY matches a planned new file above it is an "
        "intra-plan dependency (a later task touching a file an earlier task "
        "creates) — return `create_ok` ONLY IF no same-basename file exists "
        "anywhere under repos_root. If one or more same-basename files DO "
        "exist on disk, the citation may mean one of those existing files: "
        "return `ambiguous`, NEVER guess between an existing file and the "
        "planned new one.\n\n"
    ) if planned_new else ""
    return (
        "Resolve these implementation-DAG task paths against the REAL repository "
        f"checkouts under `{repos_root}`.\n\n"
        "PATH CONVENTION (MIXED — important): this DAG was authored with two "
        "conventions and `repo_path` is unreliable. For each candidate the real "
        "file is at EITHER `<repos_root>/<path>` (the `path` already includes the "
        "repo-name prefix, e.g. `iriai-studio/src/vs/...`) OR "
        "`<repos_root>/<repo_path>/<path>` (the `path` is repo-internal, e.g. "
        "`src/vs/workbench/...`). Try BOTH joins, and use Glob/Read/Grep to locate "
        "the actual file by name (a basename Glob like `**/<filename>` is the most "
        "reliable way to find where it truly lives).\n\n"
        f"repos_root: {repos_root}\n\n"
        f"{planned_section}"
        "UNRESOLVED candidate paths (JSON):\n"
        f"```json\n{json.dumps(candidates, indent=2)}\n```\n\n"
        "For EACH entry return exactly one DagPathDecision (copy task_id and field "
        "verbatim). When you find a UNIQUE real match, return `correct` with "
        "`resolved` = the path in the SAME prefix-convention as the input `path` "
        "(if the input embedded the repo-name prefix, keep that prefix in "
        "`resolved`; if it was repo-internal, keep it repo-internal) plus "
        "`evidence` (the Glob/Grep hit). Return `keep` when the path is already "
        "correct (it resolves under one of the two joins); `create_ok` for a "
        "legitimate NEW file whose target directory is a real location or is "
        "created by this plan (follow the DISPOSITION RULES above when "
        "present); `ambiguous` when you cannot find a unique answer — NEVER "
        "guess. "
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


def _file_scope_path_fields(
    task: ImplementationTask,
) -> Iterable[tuple[str, str, str]]:
    """Yield (field_key, path, action) for every ``file_scope`` entry on a task.

    field_key matches the convention the resolver returns in DagPathDecision so
    apply_path_resolution can match decisions back deterministically. The legacy
    ``files[]`` list is intentionally NOT flagged by the prepass — those entries
    carry no action and are reference/aux paths; apply_path_resolution still
    corrects any ``files[]`` value that matches a corrected ``file_scope`` path."""
    for idx, scope in enumerate(task.file_scope):
        yield f"file_scope[{idx}].path", scope.path, (scope.action or "").strip().lower()


def _path_resolves(
    repos_root: str,
    repo_path: str,
    path: str,
    *,
    exists: Callable[[str], bool],
) -> bool:
    """True when ``path`` exists under EITHER prefix convention.

    The DAG mixes conventions: some ``file_scope[].path`` values already embed
    the repo-name prefix (``iriai-studio/src/vs/...``) while others are
    repo-internal (``src/vs/workbench/...``); ``task.repo_path`` is unreliable
    (one task records ``repo_path='iriai'`` for an ``iriai-studio-backend/...``
    file). So a path RESOLVES if either ``<repos_root>/<path>`` or
    ``<repos_root>/<repo_path>/<path>`` exists on disk."""
    if exists(os.path.join(repos_root, path)):
        return True
    if repo_path and exists(os.path.join(repos_root, repo_path, path)):
        return True
    return False


def unresolved_dag_paths(
    dag: ImplementationDAG,
    repos_root: str,
    *,
    exists: Callable[[str], bool] = os.path.exists,
    isdir: Callable[[str], bool] = os.path.isdir,
) -> list[dict[str, str]]:
    """Cheap deterministic existence prepass. Returns the ``file_scope`` paths
    that do NOT resolve against the real repo; an empty result means the agent
    can be SKIPPED.

    A path resolves when EITHER ``<repos_root>/<path>`` OR
    ``<repos_root>/<task.repo_path>/<path>`` exists (the DAG mixes
    repo-prefixed and repo-internal conventions, and ``task.repo_path`` is
    unreliable — see :func:`_path_resolves`).

    A ``file_scope`` entry is flagged when it does NOT resolve:
      - for ``modify``/``read_only``/``read`` (a must-exist file is missing);
      - for ``create`` the file is missing *by definition* — flag it and let the
        resolver decide ``create_ok`` vs ``correct``. Callers bound the create
        blast radius by SCOPING which tasks are passed (e.g. the one-time
        migration restricts the DAG view to unsealed groups).

    ``isdir`` is retained for signature compatibility / future use; the
    both-convention existence check is the authority here. NOTE: a path whose
    stub file was written by a prior failed attempt will look "resolved" — the
    one-time migration scopes to unsealed groups (whose ``create`` files do not
    yet exist) so it still surfaces the phantom for the resolver."""
    del isdir  # signature-compatible; existence under either prefix is authoritative
    unresolved: list[dict[str, str]] = []
    for task in dag.tasks:
        repo_path = task.repo_path or ""
        for field, path, action in _file_scope_path_fields(task):
            if not path:
                continue
            if _path_resolves(repos_root, repo_path, path, exists=exists):
                continue
            unresolved.append(
                {"task_id": task.id, "field": field, "path": path, "action": action}
            )
    return unresolved


def apply_path_resolution(
    dag: ImplementationDAG,
    resolution: DagPathResolution,
    *,
    raise_on_ambiguous: bool = True,
    repos_root: str = "",
    exists: Callable[[str], bool] = os.path.exists,
    find_basename_matches: Callable[[str], int] | None = None,
    extra_planned: set[str] | None = None,
    workspace_root: str = "",
) -> tuple[ImplementationDAG, list[DagPathRewrite]]:
    """Apply the resolver's ``correct`` decisions to the DAG (pure, deterministic).

    ``keep``/``create_ok`` leave the path untouched. ``ambiguous`` decisions are
    NEVER applied (we never guess a path — that is what produced the 199-attempt
    loop):

      - ``raise_on_ambiguous=True`` (default): any ``ambiguous`` decision raises
        AmbiguousDagPath so the PLANNING seam fails-safe before persistence.
      - ``raise_on_ambiguous=False``: ambiguous decisions are simply left
        unchanged and ALL ``correct`` decisions are still applied — so a
        confident phantom fix lands even when a sibling ``create`` is uncertain.
        The one-time migration uses this so an unresolved sibling cannot block
        re-persisting the corrected phantom.

    Deterministic disposition-branch backstop (BEFORE any raise), keyed on the
    flagged entry's OWN ``file_scope`` action:

      - CREATE-class entries (``action == "create"``): "not on disk" is
        expected — the plan authors the file. Convert ``ambiguous`` ->
        ``create_ok`` (WARN, path untouched) when the path does NOT exist and
        its PARENT DIRECTORY is grounded: it exists under either join, or the
        fragment itself creates it (another planned-new file lives in/under
        it — see :func:`planned_new_file_paths` / :func:`_create_parent_grounded`).
        With ``repos_root`` unavailable the fs half is skipped and create-class
        entries convert permissively (the prepass already established
        non-existence at flag time).
      - MODIFY-class entries (everything else): must uniquely resolve against
        the repo. NEVER converted when one or more same-basename files exist
        on disk (the true-ambiguity subcase — the citation may mean an
        existing file). Converted ONLY when the path exactly matches a
        planned-new ``create`` path of the same fragment AND zero on-disk
        basename matches exist (checked via ``find_basename_matches`` /
        :func:`count_basename_matches` when ``repos_root`` is provided).
      - STALE decisions (no current entry at (task_id, field), or the recorded
        ``original`` no longer matches the live path — e.g. a persisted
        resolution reused after a slice re-plan) are logged and SKIPPED, never
        raised: there is nothing left to guard.

    Anything else stays genuinely ambiguous and still raises (the 199-attempt
    repair-loop caution stays binding — we never guess).

    Decisions are matched first by (task_id, field_key) for ``file_scope`` entries
    and then by VALUE for ``files[]`` entries: any ``files[]`` value equal to a
    corrected ``file_scope`` ``original`` is rewritten to the same ``resolved``
    target, so a phantom that also appears in the ``files[]``/reference list is
    fully corrected. Every rewrite is idempotent — only applied when the current
    value still equals the recorded ``original``."""
    decisions = {(d.task_id, d.field): d for d in resolution.decisions}
    ambiguous = [
        d for d in resolution.decisions
        if (d.decision or "").strip().lower() == "ambiguous"
    ]
    if ambiguous:
        planned = planned_new_file_paths(dag) | (extra_planned or set())
        entries = _entry_index(dag)
        genuinely_ambiguous = []
        for d in ambiguous:
            entry = entries.get((d.task_id, d.field))
            original = _normalize_planned_path(getattr(d, "original", "") or "")
            if entry is None or _normalize_planned_path(entry[1]) != original:
                # Stale decision: the entry it judged no longer exists in this
                # DAG (e.g. persisted resolution reused after a re-plan).
                logger.warning(
                    "DAG path resolver backstop: skipping STALE ambiguous "
                    "decision %s:%s=%r (no matching current file_scope entry)",
                    d.task_id, d.field, d.original,
                )
                continue
            action, _entry_path, repo_path = entry
            repo_norm = _normalize_planned_path(repo_path)
            if action == "create":
                # CREATE-class: ambiguity checks do not apply; validate
                # not-exists + grounded parent instead (fs half only when
                # repos_root is available).
                still_missing = not repos_root or not _path_resolves(
                    repos_root, repo_path, d.original, exists=exists,
                )
                grounded = (
                    not repos_root
                    or _create_parent_grounded(
                        repos_root, repo_path, d.original, planned, exists=exists,
                        workspace_root=workspace_root,
                    )
                )
                if still_missing and grounded:
                    logger.warning(
                        "DAG path resolver backstop: %s:%s=%r is a planned NEW file "
                        "created by this DAG itself — auto-converting ambiguous -> "
                        "create_ok (path left untouched)",
                        d.task_id, d.field, d.original,
                    )
                    d.decision = "create_ok"
                else:
                    genuinely_ambiguous.append(d)
                continue
            # MODIFY-class: convert ONLY for an exact planned-new match with
            # zero on-disk basename matches; never when existing same-basename
            # candidates exist (the citation may mean one of them).
            joined = f"{repo_norm}/{original}" if repo_norm and original else ""
            in_planned = bool(original) and (
                original in planned or (joined and joined in planned)
            )
            if not in_planned:
                # READ-ONLY-class reference pointer with ZERO on-disk basename
                # matches: nothing exists to confuse it with and it is never an
                # edit target — a corpus/doc citation the planner put in
                # file_scope (e.g. docs/submittal-prd/*). Leave the path
                # untouched with a WARN instead of failing the slice. Read
                # scopes WITH matches still raise (picking context wrong is
                # real); modify scopes are never relaxed.
                if (
                    repos_root
                    and action in ("read_only", "read")
                    and (find_basename_matches or (
                        lambda name: count_basename_matches(repos_root, name)
                    ))(posixpath.basename(original)) == 0
                ):
                    logger.warning(
                        "DAG path resolver backstop: %s:%s=%r is a read-only "
                        "reference with zero on-disk basename matches — leaving "
                        "unresolved (non-fatal pointer, not an edit target)",
                        d.task_id, d.field, d.original,
                    )
                    continue
                genuinely_ambiguous.append(d)
                continue
            if repos_root:
                counter = find_basename_matches or (
                    lambda name: count_basename_matches(repos_root, name)
                )
                if counter(posixpath.basename(original)) > 0:
                    if action in ("read_only", "read"):
                        # READ-class citation that EXACTLY matches a planned-new
                        # path of this DAG: the task reads a file a sibling task
                        # creates. The exact full-path match is the intent;
                        # unrelated same-basename files elsewhere on disk do not
                        # make a verbatim planned-path citation ambiguous. It is
                        # never an edit target — leave the path untouched
                        # (non-fatal pointer). Modify scopes keep the
                        # never-guess rule below.
                        logger.warning(
                            "DAG path resolver backstop: %s:%s=%r is a read-only "
                            "reference exactly matching a planned NEW file created "
                            "by this DAG itself — leaving unresolved (non-fatal "
                            "pointer to a sibling-created file)",
                            d.task_id, d.field, d.original,
                        )
                        continue
                    logger.warning(
                        "DAG path resolver backstop: %s:%s=%r matches a planned "
                        "NEW file but same-basename files exist on disk — "
                        "keeping ambiguous (never guess between an existing "
                        "file and a planned one)",
                        d.task_id, d.field, d.original,
                    )
                    genuinely_ambiguous.append(d)
                    continue
            logger.warning(
                "DAG path resolver backstop: %s:%s=%r is a planned NEW file "
                "created by this DAG itself — auto-converting ambiguous -> "
                "create_ok (path left untouched)",
                d.task_id, d.field, d.original,
            )
            d.decision = "create_ok"
        if genuinely_ambiguous and raise_on_ambiguous:
            raise AmbiguousDagPath(genuinely_ambiguous)

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
        # Build this task's {original -> resolved} map from its applied
        # file_scope corrects so files[] entries with the same value follow.
        value_map: dict[str, str] = {}
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
                value_map[scope.path] = resolved
                updated_scope[idx] = scope.model_copy(update={"path": resolved})
                changed = True
        for idx, path in enumerate(task.files):
            # Prefer an explicit files[idx] decision; otherwise follow a
            # file_scope correction with the same value.
            resolved = _corrected(f"files[{idx}]", path, task.id)
            if resolved is None:
                resolved = value_map.get(path) if path else None
            if resolved is not None and resolved != path:
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
