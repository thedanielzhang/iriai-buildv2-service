"""Git primitives for the durable merge queue (Slice 08) and the legacy
commit path (Slice 11b).

This module is the merge queue's git layer plus the legacy commit path's
git primitives + commit-failure parsing family. It is deliberately self-
contained — it imports no workflow code — so the git behavior is unit-
testable against temporary repositories without a database or the
implementation monolith.

The Slice-08 surface (durable merge queue) lives at the top of the
module. The Slice-11b extension (legacy commit path subprocess wrappers
+ commit-failure parsing) lives at the bottom, after a clear section
divider. Splitting the monolith so the legacy commit path also routes
through these primitives is the doc-11 § "Boundary-level API contracts"
row-12 contract.
"""

from __future__ import annotations

import asyncio
import asyncio as _asyncio
import json
import re
from pathlib import Path

from pydantic import BaseModel

from .types import CommitFailureLocation, CommitRepoOutcome

# Bounded capture for hook/commit failure context so failure evidence stays
# within journal payload limits.
OUTPUT_LIMIT = 4000


def _bounded(text: str, limit: int = OUTPUT_LIMIT) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


def _split_z(text: str) -> list[str]:
    """Split a git ``-z`` (NUL-delimited) field list into non-empty entries."""

    return [entry for entry in text.split("\0") if entry]


class GitError(RuntimeError):
    """A git command exited non-zero where success was required."""

    def __init__(
        self,
        cwd: Path | str,
        args: tuple[str, ...],
        returncode: int,
        stderr: str,
    ) -> None:
        self.cwd = str(cwd)
        self.args = tuple(args)
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"git {' '.join(args)} failed in {cwd} "
            f"(exit {returncode}): {stderr.strip()}"
        )


class GitCommandResult(BaseModel):
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


async def run_git(
    cwd: Path | str,
    *args: str,
    check: bool = True,
    stdin: str | bytes | None = None,
) -> GitCommandResult:
    """Run one git command in *cwd*.

    With ``check=True`` a non-zero exit raises :class:`GitError`. Callers that
    expect a non-zero exit to be meaningful (``apply --check``,
    ``merge-base --is-ancestor``, a hook-rejected commit) pass ``check=False``
    and inspect ``returncode``.
    """

    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    input_bytes: bytes | None
    if stdin is None:
        input_bytes = None
    elif isinstance(stdin, str):
        input_bytes = stdin.encode()
    else:
        input_bytes = stdin
    stdout_b, stderr_b = await proc.communicate(input=input_bytes)
    result = GitCommandResult(
        args=list(args),
        returncode=proc.returncode if proc.returncode is not None else 0,
        stdout=stdout_b.decode(errors="replace"),
        stderr=stderr_b.decode(errors="replace"),
    )
    if check and not result.ok:
        raise GitError(cwd, args, result.returncode, result.stderr)
    return result


# ── Revision facts ──────────────────────────────────────────────────────────


async def head_commit(cwd: Path | str) -> str:
    return (await run_git(cwd, "rev-parse", "HEAD")).stdout.strip()


async def head_tree(cwd: Path | str) -> str:
    return (await run_git(cwd, "rev-parse", "HEAD^{tree}")).stdout.strip()


async def resolve_commit(cwd: Path | str, rev: str) -> str:
    """Resolve *rev* to a full commit sha, raising GitError if it is unknown."""

    return (
        await run_git(cwd, "rev-parse", "--verify", f"{rev}^{{commit}}")
    ).stdout.strip()


async def is_ancestor(
    cwd: Path | str, ancestor: str, descendant: str
) -> bool:
    """True when *ancestor* is an ancestor of *descendant* (deterministic rebase
    is allowed only in that case)."""

    result = await run_git(
        cwd, "merge-base", "--is-ancestor", ancestor, descendant, check=False
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise GitError(
        cwd, ("merge-base", "--is-ancestor"), result.returncode, result.stderr
    )


# ── Working-tree state ──────────────────────────────────────────────────────


async def porcelain_status(cwd: Path | str) -> list[str]:
    """Raw ``status --porcelain=v2`` records, untracked files included.

    Newline-delimited rather than ``-z``: in ``-z`` mode a rename record spans
    two NUL-separated path fields, so NUL splitting would break one rename into
    two bogus records. The v2 line format keeps each record on a single line
    (paths with special characters are C-quoted), so newline splitting is
    record-accurate.
    """

    result = await run_git(
        cwd, "status", "--porcelain=v2", "--untracked-files=all"
    )
    return [line for line in result.stdout.splitlines() if line]


async def working_tree_clean(cwd: Path | str) -> bool:
    """No-dirty git proof: empty porcelain status and both diff gates pass."""

    if await porcelain_status(cwd):
        return False
    unstaged = await run_git(cwd, "diff", "--quiet", check=False)
    staged = await run_git(cwd, "diff", "--cached", "--quiet", check=False)
    return unstaged.ok and staged.ok


async def staged_paths(cwd: Path | str) -> list[str]:
    return _split_z(
        (await run_git(cwd, "diff", "--cached", "--name-only", "-z")).stdout
    )


async def unstaged_paths(cwd: Path | str) -> list[str]:
    return _split_z(
        (await run_git(cwd, "diff", "--name-only", "-z")).stdout
    )


async def untracked_paths(cwd: Path | str) -> list[str]:
    return _split_z(
        (
            await run_git(
                cwd, "ls-files", "--others", "--exclude-standard", "-z"
            )
        ).stdout
    )


async def changed_path_set(cwd: Path | str) -> set[str]:
    """Repo-relative paths changed in the index or worktree, untracked included.

    This is the applied path set the queue validates against task contracts.
    """

    return (
        set(await staged_paths(cwd))
        | set(await unstaged_paths(cwd))
        | set(await untracked_paths(cwd))
    )


# ── Patch apply ─────────────────────────────────────────────────────────────


class ApplyResult(BaseModel):
    applied: bool
    returncode: int
    stderr: str


async def apply_check(cwd: Path | str, patch_text: str) -> ApplyResult:
    """Fail-closed dry-run of ``git apply`` against the current HEAD.

    ``applied=False`` means the patch cannot be applied at all — missing file,
    corrupt hunk, or blobs unavailable for a 3-way fallback. It is a preflight
    only: ``git apply --check --3way`` reports success even for a patch that
    would 3-way-merge *with conflict markers*, so the queue treats a non-zero
    :func:`apply_patch` exit — not this check — as the authoritative
    ``merge_conflict`` signal. Never raises on a non-applying patch.
    """

    result = await run_git(
        cwd,
        "apply",
        "--check",
        "--index",
        "--3way",
        "--binary",
        check=False,
        stdin=patch_text,
    )
    return ApplyResult(
        applied=result.ok,
        returncode=result.returncode,
        stderr=_bounded(result.stderr),
    )


async def apply_patch(cwd: Path | str, patch_text: str) -> ApplyResult:
    """Apply patch evidence to the index and worktree.

    ``applied=False`` (non-zero git exit) is the authoritative ``merge_conflict``
    signal — it covers a 3-way apply that produced conflict markers. A failed
    apply may leave the worktree partially mutated, so the caller must reset to
    the recorded pre-apply head before retrying.
    """

    result = await run_git(
        cwd,
        "apply",
        "--index",
        "--3way",
        "--binary",
        check=False,
        stdin=patch_text,
    )
    return ApplyResult(
        applied=result.ok,
        returncode=result.returncode,
        stderr=_bounded(result.stderr),
    )


def patch_path_set(patch_text: str) -> list[str]:
    """Sorted, deduped repo-relative paths a unified git diff touches.

    Reads the ``+++ b/`` / ``--- a/`` headers and rename/copy directives.
    ``/dev/null`` (pure add/delete) is skipped on the missing side.
    """

    paths: set[str] = set()

    def _strip_prefix(token: str) -> str:
        token = token.strip()
        if token in ("a", "b") or token.startswith(("a/", "b/")):
            token = token[2:] if token.startswith(("a/", "b/")) else ""
        return token

    for raw in patch_text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("+++ ") or line.startswith("--- "):
            token = line[4:].split("\t", 1)[0].strip()
            if token in ("/dev/null", ""):
                continue
            cleaned = _strip_prefix(token)
            if cleaned:
                paths.add(cleaned)
        elif line.startswith(
            ("rename from ", "rename to ", "copy from ", "copy to ")
        ):
            token = line.split(" ", 2)[2].strip()
            if token:
                paths.add(token)
    return sorted(paths)


# ── Mutation / reset ────────────────────────────────────────────────────────


async def reset_hard(cwd: Path | str, commit: str) -> None:
    """Reset the repo to *commit* (recovery to a recorded pre-apply head)."""

    await run_git(cwd, "reset", "--hard", commit)


async def clean_untracked(cwd: Path | str, paths: list[str]) -> None:
    """Remove only the named untracked paths.

    Recovery must never delete untracked files outside the failed patch's path
    set, so cleanup is always explicitly scoped (doc 08 patch-apply step 7).
    Callers pass file paths from the patch path set; ``git clean`` without
    ``-d`` intentionally does not recurse into untracked directories.
    """

    for path in paths:
        await run_git(cwd, "clean", "-f", "-q", "--", path, check=False)


async def stage_paths(cwd: Path | str, paths: list[str]) -> None:
    """Stage only the validated paths — never the whole feature root."""

    if not paths:
        return
    await run_git(cwd, "add", "--all", "--", *paths)


# ── Commit ──────────────────────────────────────────────────────────────────


def build_commit_message(
    group_idx: int,
    task_names: list[str],
    trailers: dict[str, str] | None = None,
) -> str:
    """Stable queue commit message: ``feat: group N - names`` plus trailers."""

    names = ", ".join(name for name in task_names if name)
    title = f"feat: group {group_idx}"
    if names:
        title = f"{title} - {names}"
    lines = [title]
    if trailers:
        lines.append("")
        for key, value in trailers.items():
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


class HookFailure(BaseModel):
    """Bounded capture of a commit rejected by a hook (pre-commit/husky)."""

    returncode: int
    stdout: str
    stderr: str
    status_before: str = ""
    status_after: str = ""


class CommitResult(BaseModel):
    committed: bool
    commit: str = ""
    tree: str = ""
    hook_failure: HookFailure | None = None


async def commit(
    cwd: Path | str,
    message: str,
    *,
    allow_empty: bool = False,
) -> CommitResult:
    """Commit staged changes.

    A hook-rejected commit is not raised — it returns ``committed=False`` with a
    bounded :class:`HookFailure` so the queue routes it as ``commit_hygiene``
    rather than entering broad implementation repair.
    """

    status_before = "\n".join(await porcelain_status(cwd))
    args = ["commit", "-m", message]
    if allow_empty:
        args.append("--allow-empty")
    result = await run_git(cwd, *args, check=False)
    if not result.ok:
        status_after = "\n".join(await porcelain_status(cwd))
        return CommitResult(
            committed=False,
            hook_failure=HookFailure(
                returncode=result.returncode,
                stdout=_bounded(result.stdout),
                stderr=_bounded(result.stderr),
                status_before=_bounded(status_before),
                status_after=_bounded(status_after),
            ),
        )
    return CommitResult(
        committed=True,
        commit=await head_commit(cwd),
        tree=await head_tree(cwd),
    )


# ── Legacy commit path: subprocess wrappers (Slice 11b) ─────────────────────
#
# The legacy commit path in `workflows/develop/phases/implementation.py`
# uses a small async subprocess wrapper that is byte-for-byte preserved
# here. These wrappers DO NOT share state with `run_git` above (which is
# the merge-queue git layer). They are kept as a parallel surface so the
# legacy commit path can be lifted into this module without behavior
# change. A future sub-slice may unify the two surfaces; doing so now
# would broaden Slice 11b beyond a refactor-only extraction.


async def _run_git_for_commit(repo_path: Path, *args: str) -> tuple[int, str, str]:
    proc = await _asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(repo_path),
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def _git_status_for_commit(repo_path: Path) -> tuple[int, str, str]:
    return await _run_git_for_commit(repo_path, "status", "--porcelain")


# ── Commit-failure parsing (parse_commit_failure per doc-11 row 12) ─────────
#
# These helpers normalize and parse the failure output of a rejected
# commit (pre-commit hook / husky output, git porcelain status, forbidden-
# file manifest lookups). They take a `CommitRepoOutcome` (or its
# components) as input and return typed records / primitive shapes. They
# do not perform any git mutation and do not depend on workflow runner
# state.


def _commit_failure_output(outcome: CommitRepoOutcome | None) -> str:
    if outcome is None:
        return ""
    return outcome.stderr.strip() or outcome.stdout.strip() or outcome.error.strip()


def _looks_like_file_path(value: str) -> bool:
    if not value or "://" in value:
        return False
    normalized = value.replace("\\", "/")
    if normalized.startswith(("/", "../", "./")):
        return True
    if "/" in normalized:
        return True
    return bool(re.search(r"\.[A-Za-z0-9]{1,8}$", normalized))


def _normalize_commit_failure_path(
    raw_path: str,
    outcome: CommitRepoOutcome,
) -> str:
    path_text = raw_path.strip().strip("`'\"")
    while path_text.startswith("./"):
        path_text = path_text[2:]
    if not path_text:
        return ""
    repo_path = Path(outcome.repo_path)
    try:
        path_obj = Path(path_text).expanduser()
        if path_obj.is_absolute():
            try:
                path_text = path_obj.relative_to(repo_path).as_posix()
            except ValueError:
                return path_obj.as_posix()
    except (OSError, RuntimeError, ValueError):
        pass
    path_text = path_text.replace("\\", "/")
    repo_name = outcome.repo_name.strip()
    if repo_name and path_text != repo_name and not path_text.startswith(f"{repo_name}/"):
        return f"{repo_name}/{path_text}"
    return path_text


def _parse_commit_failure_location(
    outcome: CommitRepoOutcome | None,
) -> CommitFailureLocation:
    locations = _parse_commit_failure_locations(outcome)
    return locations[0] if locations else CommitFailureLocation()


def _parse_commit_failure_locations(
    outcome: CommitRepoOutcome | None,
) -> list[CommitFailureLocation]:
    if outcome is None:
        return []
    output = "\n".join(
        part
        for part in [
            outcome.stderr,
            outcome.stdout,
            outcome.error,
            outcome.status_after,
            outcome.status_before,
        ]
        if part
    )
    locations: list[CommitFailureLocation] = []
    seen: set[tuple[str, int]] = set()
    patterns = [
        re.compile(r"(?P<path>[^\n()]+?)\((?P<line>\d+),(?P<column>\d+)\)"),
        re.compile(r"(?P<path>[^\s:\n][^:\n]*?):(?P<line>\d+):(?P<column>\d+)(?::|\s|$)"),
        re.compile(r"(?P<path>[^\s:\n][^:\n]*?):(?P<line>\d+)(?::|\s|$)"),
    ]
    for pattern in patterns:
        for match in pattern.finditer(output):
            path = match.group("path").strip()
            if not _looks_like_file_path(path):
                continue
            normalized = _normalize_commit_failure_path(path, outcome)
            if not normalized:
                continue
            line = int(match.group("line") or 0)
            key = (normalized, line)
            if key in seen:
                continue
            seen.add(key)
            locations.append(CommitFailureLocation(file=normalized, line=line))
    return locations


def _commit_failure_manifest_entries(
    outcome: CommitRepoOutcome | None,
) -> list[dict[str, str]]:
    if outcome is None or not outcome.repo_path:
        return []
    config_path = (
        Path(outcome.repo_path)
        / "scripts"
        / "verify-file-scope.expected-files.json"
    )
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    entries: list[dict[str, str]] = []
    for item in data.get("forbidden_files", []):
        path = ""
        source = ""
        if isinstance(item, str):
            path = item
        elif isinstance(item, dict):
            raw_path = item.get("path")
            raw_source = item.get("source")
            path = raw_path if isinstance(raw_path, str) else ""
            source = raw_source if isinstance(raw_source, str) else ""
        path = path.strip().replace("\\", "/").strip("/")
        if not path:
            continue
        entries.append({
            "path": path,
            "source": source.strip(),
            "config_path": str(config_path),
        })
    return entries


def _commit_repo_relative_path(path: str, outcome: CommitRepoOutcome) -> str:
    normalized = path.strip().strip("`'\"").replace("\\", "/").strip("/")
    repo_path = Path(outcome.repo_path)
    if normalized:
        try:
            path_obj = Path(normalized).expanduser()
            if path_obj.is_absolute():
                normalized = path_obj.relative_to(repo_path).as_posix()
        except Exception:
            pass
    repo_name = outcome.repo_name.strip().strip("/")
    if repo_name and normalized.startswith(f"{repo_name}/"):
        normalized = normalized[len(repo_name) + 1:]
    return normalized.strip("/")


def _commit_path_matches_forbidden_entry(
    path: str,
    entry: dict[str, str],
) -> bool:
    normalized = path.strip().replace("\\", "/").strip("/")
    forbidden = str(entry.get("path", "")).strip().replace("\\", "/").strip("/")
    if not normalized or not forbidden:
        return False
    return normalized == forbidden or normalized.startswith(f"{forbidden}/")


def _commit_status_paths(
    status_text: str,
    *,
    source: str,
) -> list[dict[str, str]]:
    paths: list[dict[str, str]] = []
    for raw_line in status_text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if len(line) < 4:
            continue
        xy = line[:2]
        path_text = line[3:].strip()
        if not path_text:
            continue
        deletion_only = "D" in xy and all(char in {" ", "D"} for char in xy)
        if deletion_only:
            continue
        if " -> " in path_text:
            _, path_text = path_text.rsplit(" -> ", 1)
        paths.append({
            "path": path_text.strip().strip('"'),
            "git_state": xy.strip() or xy,
            "source": source,
        })
    return paths


def _commit_deletion_only_status_paths(status_text: str) -> set[str]:
    paths: set[str] = set()
    for raw_line in status_text.splitlines():
        line = raw_line.rstrip()
        if len(line) < 4:
            continue
        xy = line[:2]
        if "D" not in xy or not all(char in {" ", "D"} for char in xy):
            continue
        path_text = line[3:].strip()
        if not path_text:
            continue
        if " -> " in path_text:
            _, path_text = path_text.rsplit(" -> ", 1)
        paths.add(path_text.strip().strip('"'))
    return paths


def _is_repo_hygiene_outcome(outcome: CommitRepoOutcome | None) -> bool:
    if outcome is None:
        return False
    if outcome.command == ["workflow-repo-hygiene-check"]:
        return True
    text = f"{outcome.error}\n{outcome.stderr}\n{outcome.stdout}".lower()
    return (
        "workflow repos with hygiene blockers" in text
        or "embedded .git" in text
        or "gitlink" in text
    )


__all__ = [
    # Slice 08 (durable merge queue) surface.
    "OUTPUT_LIMIT",
    "GitError",
    "GitCommandResult",
    "run_git",
    "head_commit",
    "head_tree",
    "resolve_commit",
    "is_ancestor",
    "porcelain_status",
    "working_tree_clean",
    "staged_paths",
    "unstaged_paths",
    "untracked_paths",
    "changed_path_set",
    "ApplyResult",
    "apply_check",
    "apply_patch",
    "patch_path_set",
    "reset_hard",
    "clean_untracked",
    "stage_paths",
    "build_commit_message",
    "HookFailure",
    "CommitResult",
    "commit",
    # Slice 11b (legacy commit path) surface — async subprocess wrappers +
    # commit-failure parsing family.
    "_run_git_for_commit",
    "_git_status_for_commit",
    "_commit_failure_output",
    "_looks_like_file_path",
    "_normalize_commit_failure_path",
    "_parse_commit_failure_location",
    "_parse_commit_failure_locations",
    "_commit_failure_manifest_entries",
    "_commit_repo_relative_path",
    "_commit_path_matches_forbidden_entry",
    "_commit_status_paths",
    "_commit_deletion_only_status_paths",
    "_is_repo_hygiene_outcome",
]
