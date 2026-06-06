"""Unit tests for the opt-in remote-PR-on-checkpoint hook
(`_push_feature_branch_and_open_pr`). Pure in-memory — no DB, no agent run, no
real git/gh: `_run_git` / `_run_gh` / `_source_push_expected_origins` /
`_log_feature_event` are monkeypatched on the implementation module.

Coverage (per the plan):
  A. opt-in ON, first checkpoint (gh pr list empty) => one branch push + one
     `gh pr create --draft`.
  B. opt-in ON, subsequent (gh pr list non-empty) => push but NO `gh pr create`.
  C. profile absent / remote_pr_enabled=False => ZERO github push + ZERO gh
     calls (byte-for-byte legacy; AC-K-11).
  D. push/PR failure (_run_git push raises) => helper returns normally (no raise)
     and `_log_feature_event` recorded "dag_checkpoint_remote_pr_failed".
  E. safety: optional_noop repo skipped; HEAD != feature/<slug> skipped;
     non-github remote URL skipped.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from iriai_compose import Feature

from iriai_build_v2.workflows.develop.e2e.models import ProjectProfile
from iriai_build_v2.workflows.develop.phases import implementation as impl


def _feature() -> Feature:
    return Feature(
        id="feat-remote-pr",
        name="My Feature",
        slug="my-feature",
        workflow_name="full-develop",
        workspace_id="main",
    )


class _Runner:
    """Minimal stand-in; the hook only ever touches what we monkeypatch."""


def _install_origins(
    monkeypatch: pytest.MonkeyPatch,
    *,
    expected: dict[str, str] | None,
    optional_noop: set[str],
) -> None:
    async def _fake_origins(runner, feature, feature_root, *, group_idx=None):
        return expected, optional_noop, Path("/ws")

    monkeypatch.setattr(impl, "_source_push_expected_origins", _fake_origins)


class _GitRecorder:
    """Records `_run_git` calls and answers HEAD / remote.url / base queries.

    `head_by_clone` maps a clone-dir basename (the repo `rel`) to its HEAD branch
    name. `remote_url` is returned for any `config --get remote.*.url`. `base`
    is returned for `rev-parse --abbrev-ref HEAD` on a SOURCE path.
    """

    def __init__(
        self,
        *,
        head_branch: str = "feature/my-feature",
        remote_url: str = "https://github.com/acme/webapp.git",
        base: str = "main",
        head_by_rel: dict[str, str] | None = None,
        push_raises: bool = False,
    ) -> None:
        self.head_branch = head_branch
        self.remote_url = remote_url
        self.base = base
        self.head_by_rel = head_by_rel or {}
        self.push_raises = push_raises
        self.calls: list[tuple[str, ...]] = []
        self.pushes: list[tuple[str, ...]] = []

    async def __call__(self, cwd: Path, *args: str) -> str:
        self.calls.append(args)
        cwd = Path(cwd)
        if args[:1] == ("push",):
            if self.push_raises:
                raise RuntimeError("simulated push failure")
            self.pushes.append((str(cwd),) + args)
            return ""
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            # SOURCE-path base query lives under /src; clone HEAD under feature_root.
            rel = cwd.name
            if rel in self.head_by_rel:
                return self.head_by_rel[rel]
            # Heuristic: a SOURCE path (we use /src/...) returns the base; a clone
            # returns the feature branch.
            if "src" in cwd.parts:
                return self.base
            return self.head_branch
        if args[:2] == ("config", "--get") and args[2].startswith("remote."):
            return self.remote_url
        return ""


class _GhRecorder:
    def __init__(self, *, list_returns: str = "[]") -> None:
        self.list_returns = list_returns
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if args[:2] == ("pr", "list"):
            return self.list_returns
        if args[:2] == ("pr", "create"):
            return "https://github.com/acme/webapp/pull/1"
        return ""

    @property
    def creates(self) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[:2] == ("pr", "create")]

    @property
    def lists(self) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[:2] == ("pr", "list")]


class _EventRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []  # (event_type, content)

    async def __call__(
        self, runner, feature_id, event_type, phase, *, content="", metadata=None
    ) -> None:
        self.events.append((event_type, content))


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    git: _GitRecorder,
    gh: _GhRecorder,
    events: _EventRecorder,
) -> None:
    monkeypatch.setattr(impl, "_run_git", git)
    monkeypatch.setattr(impl, "_run_gh", gh)
    monkeypatch.setattr(impl, "_log_feature_event", events)


def _run(profile: ProjectProfile | None, feature_root: Path = Path("/ws/repos")) -> None:
    asyncio.run(
        impl._push_feature_branch_and_open_pr(
            _Runner(),
            _feature(),
            feature_root=feature_root,
            group_idx=3,
            profile=profile,
        )
    )


# --- A: opt-in ON, first checkpoint -------------------------------------------


def test_first_checkpoint_pushes_and_opens_draft_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_origins(
        monkeypatch,
        expected={"webapp": "/src/webapp"},
        optional_noop=set(),
    )
    git = _GitRecorder()
    gh = _GhRecorder(list_returns="[]")
    events = _EventRecorder()
    _wire(monkeypatch, git, gh, events)

    _run(ProjectProfile(remote_pr_enabled=True))

    # Exactly one branch push to the github URL.
    assert len(git.pushes) == 1
    push_args = git.pushes[0]
    assert "push" in push_args
    assert "https://github.com/acme/webapp.git" in push_args
    assert "feature/my-feature" in push_args
    # Exactly one draft PR create.
    assert len(gh.creates) == 1
    create = gh.creates[0]
    assert "--draft" in create
    assert "--head" in create and "feature/my-feature" in create
    assert "--base" in create and "main" in create
    assert "--repo" in create and "acme/webapp" in create
    assert events.events == []


# --- B: opt-in ON, subsequent checkpoint --------------------------------------


def test_subsequent_checkpoint_pushes_but_does_not_create_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_origins(
        monkeypatch,
        expected={"webapp": "/src/webapp"},
        optional_noop=set(),
    )
    git = _GitRecorder()
    gh = _GhRecorder(
        list_returns='[{"url": "https://github.com/acme/webapp/pull/7"}]'
    )
    events = _EventRecorder()
    _wire(monkeypatch, git, gh, events)

    _run(ProjectProfile(remote_pr_enabled=True))

    assert len(git.pushes) == 1  # push still happens (updates the open PR)
    assert gh.lists, "should have queried existing PRs"
    assert gh.creates == []  # but NO new PR is created
    assert events.events == []


# --- C: off-by-default / profile-absent => ZERO git + gh calls (AC-K-11) -------


def test_profile_absent_makes_zero_git_and_gh_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If the gate fails, _source_push_expected_origins would be reached — make it
    # explode so any leakage past the gate is caught.
    async def _must_not_run(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("origins resolver must not run when gate is off")

    monkeypatch.setattr(impl, "_source_push_expected_origins", _must_not_run)
    git = _GitRecorder()
    gh = _GhRecorder()
    events = _EventRecorder()
    _wire(monkeypatch, git, gh, events)

    _run(None)  # profile absent

    assert git.calls == []
    assert gh.calls == []
    assert events.events == []


def test_flag_off_makes_zero_git_and_gh_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _must_not_run(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("origins resolver must not run when gate is off")

    monkeypatch.setattr(impl, "_source_push_expected_origins", _must_not_run)
    git = _GitRecorder()
    gh = _GhRecorder()
    events = _EventRecorder()
    _wire(monkeypatch, git, gh, events)

    _run(ProjectProfile(remote_pr_enabled=False))  # explicit off (the default)

    assert git.calls == []
    assert gh.calls == []
    assert events.events == []


# --- D: push/PR failure => no raise + recorded failure event ------------------


def test_push_failure_is_swallowed_and_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_origins(
        monkeypatch,
        expected={"webapp": "/src/webapp"},
        optional_noop=set(),
    )
    git = _GitRecorder(push_raises=True)
    gh = _GhRecorder()
    events = _EventRecorder()
    _wire(monkeypatch, git, gh, events)

    # Must NOT raise.
    _run(ProjectProfile(remote_pr_enabled=True))

    assert git.pushes == []  # push raised before recording
    assert gh.creates == []  # never got to PR create
    assert any(
        evt == "dag_checkpoint_remote_pr_failed" for evt, _ in events.events
    ), events.events


# --- E: safety skips ----------------------------------------------------------


def test_optional_noop_repo_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_origins(
        monkeypatch,
        expected={"webapp": "/src/webapp", "shared": "/src/shared"},
        optional_noop={"shared"},
    )
    git = _GitRecorder()
    gh = _GhRecorder(list_returns="[]")
    events = _EventRecorder()
    _wire(monkeypatch, git, gh, events)

    _run(ProjectProfile(remote_pr_enabled=True))

    # Only the writeable repo is pushed; the optional_noop (source/read-only) is not.
    assert len(git.pushes) == 1
    assert len(gh.creates) == 1


def test_head_not_feature_branch_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_origins(
        monkeypatch,
        expected={"webapp": "/src/webapp"},
        optional_noop=set(),
    )
    # Clone "webapp" sits on a different branch (e.g. the protected base).
    git = _GitRecorder(head_by_rel={"webapp": "main"})
    gh = _GhRecorder(list_returns="[]")
    events = _EventRecorder()
    _wire(monkeypatch, git, gh, events)

    _run(ProjectProfile(remote_pr_enabled=True))

    assert git.pushes == []  # never push a non-feature branch
    assert gh.calls == []
    assert events.events == []


def test_non_github_remote_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_origins(
        monkeypatch,
        expected={"webapp": "/src/webapp"},
        optional_noop=set(),
    )
    git = _GitRecorder(remote_url="/some/local/path.git")  # not github.com
    gh = _GhRecorder(list_returns="[]")
    events = _EventRecorder()
    _wire(monkeypatch, git, gh, events)

    _run(ProjectProfile(remote_pr_enabled=True))

    assert git.pushes == []  # never push to a non-github remote
    assert gh.calls == []
    assert events.events == []


# --- parser unit coverage (https + ssh + scp-like forms) ----------------------


def test_parse_github_owner_repo_forms() -> None:
    assert (
        impl._parse_github_owner_repo("https://github.com/acme/webapp.git")
        == "acme/webapp"
    )
    assert (
        impl._parse_github_owner_repo("git@github.com:acme/webapp.git")
        == "acme/webapp"
    )
    assert (
        impl._parse_github_owner_repo("ssh://git@github.com/acme/webapp")
        == "acme/webapp"
    )
    assert impl._parse_github_owner_repo("/local/only/repo.git") == ""
    assert impl._parse_github_owner_repo("") == ""
