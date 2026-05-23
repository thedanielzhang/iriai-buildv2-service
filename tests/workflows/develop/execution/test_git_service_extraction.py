"""Slice 11b — extraction proof for `execution/git_service.py` extension.

Verifies the doc-11 § "How To Use This Map" four-question contract for
the legacy-commit-path git extraction:

1. What behavior moved: the async git subprocess wrappers
   (`_run_git_for_commit`, `_git_status_for_commit`) used by the legacy
   commit path + the commit-failure parsing family
   (`_commit_failure_output`, `_looks_like_file_path`,
   `_normalize_commit_failure_path`, `_parse_commit_failure_location`,
   `_parse_commit_failure_locations`, `_commit_failure_manifest_entries`,
   `_commit_repo_relative_path`, `_commit_path_matches_forbidden_entry`,
   `_commit_status_paths`, `_commit_deletion_only_status_paths`,
   `_is_repo_hygiene_outcome`) moved from
   `workflows/develop/phases/implementation.py` to
   `workflows/develop/execution/git_service.py`. The Slice-08b durable-
   merge-queue git layer that was already in `git_service.py` (run_git,
   apply_patch, commit, working_tree_clean, etc.) is UNTOUCHED — Slice
   11b EXTENDS, never modifies.
2. Which legacy import names still work: every existing
   `from iriai_build_v2.workflows.develop.phases.implementation import X`
   keeps resolving to the SAME object as the canonical definition in
   `execution/git_service.py` (the shim is `is`-equivalent, not a copy).
3. Which targeted tests prove the new facade and the compatibility shim:
   THIS file is one of them; it pins every moved name's shim equivalence
   and behaviorally smoke-tests representative helpers.
4. Why is the PR still refactor-only: nothing else moves. The pure git
   subprocess wrappers + the pure commit-failure parsing family moved
   byte-for-byte. The `_commit_repos_in_root` callsite (doc-11 row-12
   `commit_repos_in_root`) STAYS in `implementation.py` for now because
   it has workspace-authority dependencies (`_dag_repo_hygiene_problems`
   + `_direct_source_push_repos`) that move in Slice 11d
   (workspace_authority); moving it would either create a forbidden
   upward import from `git_service.py` to `implementation.py` or
   broaden 11b's scope into workspace_authority territory. The
   `_commit_forbidden_path_matches` / `_commit_forbidden_operator_
   reasons` helpers stay for the same reason (they depend on the
   workspace permission/ACL family).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest


# Each entry is a name moved from `implementation.py` to
# `execution/git_service.py` in Slice 11b. The order is the import-line
# order in the shim block so a grep over either file lists the names in
# the same order.
MOVED_NAMES = [
    "_commit_deletion_only_status_paths",
    "_commit_failure_manifest_entries",
    "_commit_failure_output",
    "_commit_path_matches_forbidden_entry",
    "_commit_repo_relative_path",
    "_commit_status_paths",
    "_git_status_for_commit",
    "_is_repo_hygiene_outcome",
    "_looks_like_file_path",
    "_normalize_commit_failure_path",
    "_parse_commit_failure_location",
    "_parse_commit_failure_locations",
    "_run_git_for_commit",
]


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_shim_re_export_is_same_object_as_new_canonical(name: str) -> None:
    """Every moved helper imported via the OLD path is the SAME object
    as the import via the NEW canonical path. Proves the shim is a re-
    export, not a copy. Locks the monkeypatch target equivalence —
    `monkeypatch.setattr(implementation_module, name, ...)` will mutate
    the SAME function object that any direct
    `from execution.git_service import name` reader sees.
    """

    from iriai_build_v2.workflows.develop import execution as execution_pkg
    from iriai_build_v2.workflows.develop.execution import (
        git_service as git_service_mod,
    )
    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    legacy = getattr(impl_mod, name)
    canonical = getattr(git_service_mod, name)
    assert legacy is canonical, (
        f"shim drift: implementation.{name} is not the same object as "
        f"execution.git_service.{name}"
    )
    # `execution_pkg` is imported only to ensure the package import chain
    # works end-to-end (no side-effect import errors).
    _ = execution_pkg


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_canonical_module_is_git_service(name: str) -> None:
    """The moved function objects' `__module__` is the new canonical
    `iriai_build_v2.workflows.develop.execution.git_service` — not the
    legacy `…phases.implementation`. Proves the definition genuinely
    moved rather than being re-aliased from the old module.
    """

    from iriai_build_v2.workflows.develop.execution import (
        git_service as git_service_mod,
    )

    canonical = getattr(git_service_mod, name)
    assert canonical.__module__ == (
        "iriai_build_v2.workflows.develop.execution.git_service"
    ), (
        f"{name}.__module__ = {canonical.__module__!r}; expected the new "
        "git_service-module path"
    )


def test_looks_like_file_path_smoke() -> None:
    """The path heuristic recognizes file-shaped tokens (slash-bearing,
    leading `/`, `./`, `../`, or a trailing `.ext`) and rejects URLs +
    empty strings. Pinned by parsing of pre-commit hook output.
    """

    from iriai_build_v2.workflows.develop.execution.git_service import (
        _looks_like_file_path,
    )

    # Positive cases.
    assert _looks_like_file_path("src/foo.py")
    assert _looks_like_file_path("./relative/thing.ts")
    assert _looks_like_file_path("/absolute/path.txt")
    assert _looks_like_file_path("../parent/file.md")
    assert _looks_like_file_path("just_filename.json")
    # Negative cases.
    assert not _looks_like_file_path("")
    assert not _looks_like_file_path("https://example.com/path")
    assert not _looks_like_file_path("hello")  # no slash, no extension
    assert not _looks_like_file_path("CONSTANT")  # no slash, no extension


def test_commit_path_matches_forbidden_entry_positive_and_negative() -> None:
    """`_commit_path_matches_forbidden_entry` returns True iff the
    normalized candidate path EQUALS the forbidden rule OR is a
    descendant of the forbidden rule. Pinned by
    `tests/workflows/test_dag_expanded_verify.py:491` (the legacy test
    accesses this via the implementation shim).
    """

    from iriai_build_v2.workflows.develop.execution.git_service import (
        _commit_path_matches_forbidden_entry,
    )

    entry = {"path": "src/forbidden"}
    # Positive: exact match + descendant match.
    assert _commit_path_matches_forbidden_entry("src/forbidden", entry)
    assert _commit_path_matches_forbidden_entry("src/forbidden/inside.ts", entry)
    # Backslash + leading/trailing slashes normalize.
    assert _commit_path_matches_forbidden_entry("src\\forbidden\\inner.py", entry)
    assert _commit_path_matches_forbidden_entry("/src/forbidden/", entry)
    # Negative: sibling, different prefix, empty.
    assert not _commit_path_matches_forbidden_entry("src/forbidden_sibling", entry)
    assert not _commit_path_matches_forbidden_entry("other/path.ts", entry)
    assert not _commit_path_matches_forbidden_entry("", entry)
    assert not _commit_path_matches_forbidden_entry("src/forbidden", {"path": ""})


def test_parse_commit_failure_locations_known_payload() -> None:
    """`_parse_commit_failure_locations` parses three patterns:
    1. `path(line,col)` (e.g. tsc compiler output),
    2. `path:line:col` (e.g. eslint, ruff),
    3. `path:line` (terser locator).

    A round-trip over a synthetic stderr containing all three formats
    yields the deduped union of locations, each repo-prefixed with the
    outcome's `repo_name`.
    """

    from iriai_build_v2.workflows.develop.execution.git_service import (
        _parse_commit_failure_locations,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        CommitFailureLocation,
        CommitRepoOutcome,
    )

    stderr = (
        "tsc errors:\n"
        "src/index.ts(42,7): error TS1234: Bad type.\n"
        "eslint:\n"
        "src/app.js:99:3: error: not allowed\n"
        "ruff:\n"
        "tests/test_x.py:1\n"
        "duplicate that must dedupe:\n"
        "src/app.js:99:3: error: dup line\n"
    )
    outcome = CommitRepoOutcome(
        repo_path="/abs/repo-canonical",
        repo_name="repo-canonical",
        message="commit",
        stderr=stderr,
    )

    locs = _parse_commit_failure_locations(outcome)
    # All three formats produced a hit; the duplicate of src/app.js:99
    # is collapsed by the (path, line) seen-set.
    paths = {(loc.file, loc.line) for loc in locs}
    assert ("repo-canonical/src/index.ts", 42) in paths
    assert ("repo-canonical/src/app.js", 99) in paths
    assert ("repo-canonical/tests/test_x.py", 1) in paths
    # Dedup: exactly 3 distinct (file, line) entries (the second
    # src/app.js:99 line is filtered).
    assert len({(loc.file, loc.line) for loc in locs}) == 3
    # Each entry is a `CommitFailureLocation` Pydantic instance.
    for loc in locs:
        assert isinstance(loc, CommitFailureLocation)

    # None / empty-payload fast path.
    assert _parse_commit_failure_locations(None) == []
    empty = CommitRepoOutcome(repo_path="/abs/x", repo_name="x", message="m")
    assert _parse_commit_failure_locations(empty) == []


def test_commit_failure_manifest_entries_reads_repo_manifest(
    tmp_path: Path,
) -> None:
    """`_commit_failure_manifest_entries` reads the
    `scripts/verify-file-scope.expected-files.json` manifest from
    `outcome.repo_path`. Both the dict-shape (with `source`) and the
    string-shape entries normalize into the same `{path, source,
    config_path}` dicts.
    """

    from iriai_build_v2.workflows.develop.execution.git_service import (
        _commit_failure_manifest_entries,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        CommitRepoOutcome,
    )

    repo = tmp_path / "repo-x"
    repo.mkdir()
    scripts = repo / "scripts"
    scripts.mkdir()
    manifest = scripts / "verify-file-scope.expected-files.json"
    manifest.write_text(
        json.dumps(
            {
                "forbidden_files": [
                    {"path": "legacy/dead.py", "source": "audit-2026-01"},
                    "old/stale.ts",  # string shape
                    {"path": "  /trim/me/  ", "source": "trim test"},
                    {"path": "", "source": "must be skipped"},  # empty
                ]
            }
        ),
        encoding="utf-8",
    )
    outcome = CommitRepoOutcome(
        repo_path=str(repo),
        repo_name="repo-x",
        message="m",
    )
    entries = _commit_failure_manifest_entries(outcome)
    paths = [e["path"] for e in entries]
    assert paths == ["legacy/dead.py", "old/stale.ts", "trim/me"]
    # `config_path` is populated absolute path of the manifest.
    for e in entries:
        assert e["config_path"] == str(manifest)
    # Source preserved (or empty for the string-shape entry).
    sources = [e["source"] for e in entries]
    assert sources[0] == "audit-2026-01"
    assert sources[1] == ""
    assert sources[2] == "trim test"

    # Missing manifest → empty list (not an exception).
    empty_repo = tmp_path / "no-manifest"
    empty_repo.mkdir()
    missing = CommitRepoOutcome(
        repo_path=str(empty_repo),
        repo_name="no-manifest",
        message="m",
    )
    assert _commit_failure_manifest_entries(missing) == []

    # None / empty-path fast path.
    assert _commit_failure_manifest_entries(None) == []
    no_path = CommitRepoOutcome(
        repo_path="",
        repo_name="x",
        message="m",
    )
    assert _commit_failure_manifest_entries(no_path) == []


def test_is_repo_hygiene_outcome_branches() -> None:
    """`_is_repo_hygiene_outcome` returns True for the
    workflow-repo-hygiene-check command sentinel AND for outcomes whose
    error/stderr/stdout text mentions a hygiene blocker. False
    otherwise.
    """

    from iriai_build_v2.workflows.develop.execution.git_service import (
        _is_repo_hygiene_outcome,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        CommitRepoOutcome,
    )

    # None → False fast path.
    assert _is_repo_hygiene_outcome(None) is False

    # Command sentinel → True.
    sentinel = CommitRepoOutcome(
        repo_path="/x",
        repo_name="x",
        message="m",
        command=["workflow-repo-hygiene-check"],
    )
    assert _is_repo_hygiene_outcome(sentinel) is True

    # Text match in error.
    err = CommitRepoOutcome(
        repo_path="/x",
        repo_name="x",
        message="m",
        error="Refusing to commit workflow repos with hygiene blockers",
    )
    assert _is_repo_hygiene_outcome(err) is True

    # Text match in stdout — "embedded .git".
    stdout = CommitRepoOutcome(
        repo_path="/x",
        repo_name="x",
        message="m",
        stdout="found embedded .git at services/foo/.git",
    )
    assert _is_repo_hygiene_outcome(stdout) is True

    # Text match in stderr — "gitlink".
    stderr = CommitRepoOutcome(
        repo_path="/x",
        repo_name="x",
        message="m",
        stderr="gitlink entry detected",
    )
    assert _is_repo_hygiene_outcome(stderr) is True

    # Unrelated commit failure → False.
    other = CommitRepoOutcome(
        repo_path="/x",
        repo_name="x",
        message="m",
        stderr="tsc error: not allowed",
    )
    assert _is_repo_hygiene_outcome(other) is False


def test_commit_repo_relative_path_strips_prefix() -> None:
    """`_commit_repo_relative_path` normalizes a raw path to its
    repo-relative form: drops a leading absolute path inside the repo,
    strips backslashes and quoting, and drops a leading `repo_name/`
    prefix if present.
    """

    from iriai_build_v2.workflows.develop.execution.git_service import (
        _commit_repo_relative_path,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        CommitRepoOutcome,
    )

    outcome = CommitRepoOutcome(
        repo_path="/abs/canonical",
        repo_name="canonical",
        message="m",
    )

    # Repo-prefix stripped.
    assert _commit_repo_relative_path("canonical/src/x.py", outcome) == "src/x.py"
    # Backslashes + quoting normalized.
    assert _commit_repo_relative_path('"src\\inner\\y.py"', outcome) == "src/inner/y.py"
    # Leading/trailing slashes trimmed.
    assert _commit_repo_relative_path("/src/foo.ts/", outcome) == "src/foo.ts"
    # Already-relative path passes through.
    assert _commit_repo_relative_path("src/foo.ts", outcome) == "src/foo.ts"


def test_commit_status_paths_skips_pure_deletions() -> None:
    """`_commit_status_paths` collects (path, git_state, source) records
    from a porcelain status block, skipping pure-deletion rows (those
    are reported separately by `_commit_deletion_only_status_paths`).
    """

    from iriai_build_v2.workflows.develop.execution.git_service import (
        _commit_deletion_only_status_paths,
        _commit_status_paths,
    )

    status = (
        " M src/foo.py\n"     # modified, kept
        "?? new/file.ts\n"    # untracked, kept
        " D removed.py\n"     # deletion, skipped here
        "A  added.py\n"       # staged add, kept
        "R  src/old.py -> src/new.py\n"  # rename, keep only target
        "D  also_removed.py\n"  # staged deletion, skipped here
        "\n"                    # blank, skipped
        "x\n"                   # too short, skipped
    )

    rows = _commit_status_paths(status, source="status_after")
    paths = [r["path"] for r in rows]
    # Pure deletions are filtered out.
    assert "removed.py" not in paths
    assert "also_removed.py" not in paths
    # Rename keeps the target.
    assert "src/new.py" in paths
    assert "src/old.py" not in paths
    # All retained.
    assert "src/foo.py" in paths
    assert "new/file.ts" in paths
    assert "added.py" in paths
    # `source` is propagated.
    for row in rows:
        assert row["source"] == "status_after"

    # Deletion-only sibling: returns the deletion paths the main parser
    # filtered out.
    deletions = _commit_deletion_only_status_paths(status)
    assert "removed.py" in deletions
    assert "also_removed.py" in deletions
    assert "src/foo.py" not in deletions


@pytest.mark.asyncio
async def test_run_git_for_commit_against_real_temp_repo(
    tmp_path: Path,
) -> None:
    """`_run_git_for_commit` is a thin async wrapper around
    `asyncio.create_subprocess_exec("git", ...)`. Initialize a real
    temporary git repo, run status, add, commit, and assert each
    invocation returns the expected `(returncode, stdout, stderr)`
    tuple. Also exercises `_git_status_for_commit` (which is a
    fixed-args wrapper around the same primitive).
    """

    from iriai_build_v2.workflows.develop.execution.git_service import (
        _git_status_for_commit,
        _run_git_for_commit,
    )

    # Skip if git isn't installed.
    if not _git_available():
        pytest.skip("git not available")

    repo = tmp_path / "real-repo"
    repo.mkdir()
    # Initialize a repo with a deterministic identity.
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "test@example.com"),
        ("config", "user.name", "Test User"),
    ):
        rc, out, err = await _run_git_for_commit(repo, *args)
        assert rc == 0, f"git {args} failed: {err}"

    # Empty repo status is clean.
    rc, out, err = await _git_status_for_commit(repo)
    assert rc == 0
    assert out.strip() == ""

    # Create a file and confirm porcelain status shows it as untracked.
    (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
    rc, out, err = await _git_status_for_commit(repo)
    assert rc == 0
    assert "?? hello.txt" in out

    # Add + commit.
    rc, out, err = await _run_git_for_commit(repo, "add", "hello.txt")
    assert rc == 0
    rc, out, err = await _run_git_for_commit(
        repo, "commit", "-m", "first commit", "--no-gpg-sign"
    )
    assert rc == 0
    # Tree is clean post-commit.
    rc, out, err = await _git_status_for_commit(repo)
    assert rc == 0
    assert out.strip() == ""


def _git_available() -> bool:
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def test_normalize_commit_failure_path_repo_relativizes() -> None:
    """`_normalize_commit_failure_path` resolves a raw failure-path
    token relative to `outcome.repo_path`: absolute paths inside the
    repo become repo-relative; absolute paths outside the repo return
    as-is; relative paths get the `repo_name/` prefix.
    """

    from iriai_build_v2.workflows.develop.execution.git_service import (
        _normalize_commit_failure_path,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        CommitRepoOutcome,
    )

    outcome = CommitRepoOutcome(
        repo_path="/abs/canonical",
        repo_name="canonical",
        message="m",
    )

    # Empty / quoted-only → empty.
    assert _normalize_commit_failure_path("", outcome) == ""
    assert _normalize_commit_failure_path("``", outcome) == ""

    # Relative path → repo-name-prefixed.
    assert (
        _normalize_commit_failure_path("src/x.py", outcome) == "canonical/src/x.py"
    )

    # Absolute path inside the repo → repo-relative (NOT prefixed,
    # since after relative_to the path text is already the relative
    # form, then the repo_name/ branch prepends the prefix).
    inside = _normalize_commit_failure_path("/abs/canonical/src/y.py", outcome)
    assert inside == "canonical/src/y.py"

    # Absolute path OUTSIDE the repo → returned as-is.
    outside = _normalize_commit_failure_path("/other/place/z.py", outcome)
    assert outside == "/other/place/z.py"

    # Backslashes normalize to forward slashes.
    backslash = _normalize_commit_failure_path("src\\inner\\a.py", outcome)
    assert backslash == "canonical/src/inner/a.py"

    # Leading ./ stripped (looped).
    leading_dot = _normalize_commit_failure_path("./././src/b.py", outcome)
    assert leading_dot == "canonical/src/b.py"


def test_commit_failure_output_extraction_order() -> None:
    """`_commit_failure_output` returns the first non-empty of
    `stderr` / `stdout` / `error`, each stripped.
    """

    from iriai_build_v2.workflows.develop.execution.git_service import (
        _commit_failure_output,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        CommitRepoOutcome,
    )

    # None → empty.
    assert _commit_failure_output(None) == ""

    # Stderr first.
    out = CommitRepoOutcome(
        repo_path="/x",
        repo_name="x",
        message="m",
        stderr="  stderr text  ",
        stdout="stdout text",
        error="error text",
    )
    assert _commit_failure_output(out) == "stderr text"

    # Falls through to stdout when stderr is empty.
    out = CommitRepoOutcome(
        repo_path="/x",
        repo_name="x",
        message="m",
        stderr="",
        stdout="stdout only",
        error="error",
    )
    assert _commit_failure_output(out) == "stdout only"

    # Falls through to error when both stderr and stdout are empty.
    out = CommitRepoOutcome(
        repo_path="/x",
        repo_name="x",
        message="m",
        error="error only",
    )
    assert _commit_failure_output(out) == "error only"


def test_parse_commit_failure_location_returns_first_or_empty() -> None:
    """`_parse_commit_failure_location` returns the FIRST location from
    `_parse_commit_failure_locations` or an empty
    `CommitFailureLocation()` if none match.
    """

    from iriai_build_v2.workflows.develop.execution.git_service import (
        _parse_commit_failure_location,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        CommitFailureLocation,
        CommitRepoOutcome,
    )

    # No match → default-empty location.
    empty_outcome = CommitRepoOutcome(
        repo_path="/x",
        repo_name="x",
        message="m",
        stderr="just text with no path",
    )
    loc = _parse_commit_failure_location(empty_outcome)
    assert isinstance(loc, CommitFailureLocation)
    assert loc.file == ""
    assert loc.line == 0

    # None → default-empty location.
    none_loc = _parse_commit_failure_location(None)
    assert isinstance(none_loc, CommitFailureLocation)
    assert none_loc.file == ""

    # Match → first-only. Use a no-leading-text payload so the first
    # location is the first line. (The path regex is non-greedy from
    # any non-whitespace character, so leading text would be captured
    # into the path token.)
    outcome = CommitRepoOutcome(
        repo_path="/abs/repo",
        repo_name="repo",
        message="m",
        stderr=(
            "src/a.py:1:2: error\n"
            "src/b.py:3:4: error\n"
        ),
    )
    first = _parse_commit_failure_location(outcome)
    assert first.file == "repo/src/a.py"
    assert first.line == 1
