"""Mechanical isolation guard for the async e2e-testing subsystem build.

The e2e subsystem is built by an autonomous loop that must write ONLY under an
allowlist and must NEVER modify the live workflow/orchestrator code, because a
separate agent is concurrently running and shipping the live ``8ac124d6``
workflow from the same repo. A misclassification that edits orchestrator code
could be picked up live when that agent restarts the bridge from ``main``.

This module is the mechanical enforcement. It is:

* a pure, deterministic classifier (``protected_violations`` / ``is_e2e_commit``)
  exercised by ``tests/test_isolation_guard.py`` (part of ``pytest tests/``), and
* a git-aware CLI used as a pre-commit hook and a manual pre-commit check.

Crucially it does **not** block the concurrent runner agent. The runner commits
*pure* workflow fixes (only protected/workflow files, no e2e-owned files). The
hook only rejects a *mixed* commit — one that stages a protected path together
with an e2e-owned path — which is the signature of THIS build leaking outside
its allowlist. Pure-protected commits (the runner's) pass untouched.
"""

from __future__ import annotations

import subprocess
import sys

# Paths this build is FORBIDDEN to modify (repo-root-relative prefixes).
PROTECTED_PREFIXES: tuple[str, ...] = (
    "src/iriai_build_v2/interfaces/slack/orchestrator.py",
    "src/iriai_build_v2/workflows/develop/execution/",
    "src/iriai_build_v2/workflows/develop/phases/",
    # sandbox.py lives under execution/ (already covered) — listed for clarity.
    "src/iriai_build_v2/workflows/develop/execution/sandbox.py",
)

# Paths that mark a change as belonging to THIS build (the e2e subsystem).
# Used both as the write-allowlist and to detect a "mixed" leaking commit.
E2E_OWNED_PREFIXES: tuple[str, ...] = (
    "src/iriai_build_v2/workflows/develop/e2e/",
    "src/iriai_build_v2/roles/project_profile_inferrer/",
    "src/iriai_build_v2/roles/spec_author/",
    "src/iriai_build_v2/roles/spec_triager/",
    "src/iriai_build_v2/interfaces/cli/",
)

# Full write-allowlist = e2e-owned dirs + the shared role registry (plan-sanctioned
# to register the 3 new actors) + tests/.
ALLOWED_PREFIXES: tuple[str, ...] = E2E_OWNED_PREFIXES + (
    "src/iriai_build_v2/roles/__init__.py",
    "tests/",
)


def _matches(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p) for p in prefixes)


def protected_violations(paths: list[str]) -> list[str]:
    """Return the subset of ``paths`` that touch a protected (forbidden) path."""
    return [p for p in paths if _matches(p, PROTECTED_PREFIXES)]


def allowlist_violations(paths: list[str]) -> list[str]:
    """Return ``paths`` that fall outside the write-allowlist."""
    return [p for p in paths if not _matches(p, ALLOWED_PREFIXES)]


def is_e2e_commit(paths: list[str]) -> bool:
    """True if any staged path belongs to this build (an e2e-owned path)."""
    return any(_matches(p, E2E_OWNED_PREFIXES) for p in paths)


def hook_violations(paths: list[str]) -> list[str]:
    """Pre-commit decision: reject a *mixed* commit (protected + e2e-owned).

    A pure-protected commit (the concurrent runner's workflow fix) returns [].
    A commit that stages a protected path alongside any e2e-owned path is THIS
    build leaking outside its allowlist and is rejected.
    """
    prot = protected_violations(paths)
    if prot and is_e2e_commit(paths):
        return prot
    return []


def _git_lines(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _staged_paths() -> list[str]:
    return _git_lines("diff", "--cached", "--name-only")


def _worktree_paths(base: str) -> list[str]:
    paths = set(_git_lines("diff", "--name-only", base))
    paths.update(_git_lines("diff", "--name-only", "--cached"))
    paths.update(_git_lines("ls-files", "--others", "--exclude-standard"))
    return sorted(paths)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = "hook"
    base = "origin/main"
    if "--staged" in argv:
        mode = "staged"
    if "--worktree" in argv:
        mode = "worktree"
    if "--base" in argv:
        base = argv[argv.index("--base") + 1]

    if mode == "worktree":
        paths = _worktree_paths(base)
        bad = protected_violations(paths)
    elif mode == "staged":
        paths = _staged_paths()
        bad = protected_violations(paths)
    else:  # hook
        paths = _staged_paths()
        bad = hook_violations(paths)

    if bad:
        sys.stderr.write(
            "\n[isolation-guard] REJECTED — e2e build must not modify protected "
            "workflow code:\n"
        )
        for p in bad:
            sys.stderr.write(f"  - {p}\n")
        sys.stderr.write(
            "Protected: orchestrator.py, workflows/develop/execution/, "
            "workflows/develop/phases/, sandbox.py.\n\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
