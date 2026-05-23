from __future__ import annotations

import importlib
import inspect
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from iriai_build_v2.models.outputs import ImplementationTask, TaskFileScope


MODULE_NAME = "iriai_build_v2.workflows.develop.execution.workspace_authority"
FEATURE_ID = "feature-slice-02"
FEATURE_SLUG = "slice-02"


@pytest.fixture
def workspace_authority_module() -> Any:
    try:
        return importlib.import_module(MODULE_NAME)
    except ModuleNotFoundError as exc:
        if exc.name == MODULE_NAME:
            pytest.fail(
                f"{MODULE_NAME} is missing; these Slice 02 public API tests "
                "are expected to fail until workspace_authority.py is implemented."
            )
        raise


def _new_authority(module: Any, **kwargs: Any) -> Any:
    cls = module.WorkspaceAuthority
    constructor_attempts: list[dict[str, Any]] = []
    if kwargs:
        constructor_attempts.append(kwargs)
    if "feature_root" in kwargs:
        constructor_attempts.append({"feature_root": kwargs["feature_root"]})
    if "workspace_root" in kwargs:
        constructor_attempts.append({"workspace_root": kwargs["workspace_root"]})
    constructor_attempts.append({})

    for candidate in constructor_attempts:
        try:
            return cls(**candidate)
        except TypeError:
            continue
    return cls


async def _call(authority: Any, method_name: str, *args: Any, **kwargs: Any) -> Any:
    result = getattr(authority, method_name)(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_dump(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _field(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _walk_strings(value: Any) -> list[str]:
    value = _dump(value)
    if isinstance(value, dict):
        strings: list[str] = []
        for key, item in value.items():
            strings.append(str(key))
            strings.extend(_walk_strings(item))
        return strings
    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(_walk_strings(item))
        return strings
    if value is None:
        return []
    return [str(value)]


def _joined_text(value: Any) -> str:
    return "\n".join(_walk_strings(value))


def _values_for_key(value: Any, key: str) -> list[Any]:
    value = _dump(value)
    if isinstance(value, dict):
        found = [value[key]] if key in value else []
        for item in value.values():
            found.extend(_values_for_key(item, key))
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_values_for_key(item, key))
        return found
    return []


def _has_nearest_parent(rows: list[Any], expected: Path) -> bool:
    for row in rows:
        nearest = _field(row, "nearest_existing_parent")
        if nearest and Path(nearest).resolve() == expected.resolve():
            return True
    return False


def _feature_paths(tmp_path: Path) -> tuple[Path, Path]:
    workspace_root = tmp_path / "workspace"
    feature_root = workspace_root / ".iriai" / "features" / FEATURE_SLUG / "repos"
    feature_root.mkdir(parents=True)
    return workspace_root, feature_root


def _configure_workspace_hints(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    workspace_root: Path,
    feature_root: Path,
) -> None:
    monkeypatch.chdir(workspace_root)
    monkeypatch.setenv("IRIAI_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("IRIAI_FEATURE_ROOT", str(feature_root))
    monkeypatch.setenv("IRIAI_FEATURE_REPOS_ROOT", str(feature_root))
    monkeypatch.setenv("IRIAI_WORKSPACE_AUTHORITY_FEATURE_ROOT", str(feature_root))
    monkeypatch.setattr(module, "_TEST_WORKSPACE_ROOT", workspace_root, raising=False)
    monkeypatch.setattr(module, "_TEST_FEATURE_ROOT", feature_root, raising=False)


def _task(
    task_id: str,
    repo_path: str | Path,
    *files: str,
    action: str = "modify",
) -> ImplementationTask:
    file_list = list(files) or [f"{Path(repo_path).name}/README.md"]
    return ImplementationTask(
        id=task_id,
        name=task_id,
        description=task_id,
        repo_path=str(repo_path),
        files=file_list,
        file_scope=[
            TaskFileScope(path=file_path, action=action) for file_path in file_list
        ],
    )


def _path_target(
    module: Any,
    raw_path: str | Path,
    *,
    action: str = "modify",
    task_id: str | None = "TASK-1",
    source: str = "task",
) -> Any:
    return module.PathTarget(
        raw_path=str(raw_path),
        action=action,
        task_id=task_id,
        contract_id=None,
        source=source,
    )


def _repo_identity(
    module: Any,
    feature_root: Path,
    repo_name: str,
    *,
    canonical_path: Path | None = None,
    alias_paths: list[Path] | None = None,
    repo_id: str | None = None,
    branch: str | None = "main",
    head_sha: str | None = "0" * 40,
    source_path: Path | None = None,
    remote_url: str | None = None,
    remote_fingerprint: str | None = None,
    writable_task_ids: list[str] | None = None,
) -> Any:
    canonical = canonical_path or feature_root / repo_name
    return module.RepoIdentity(
        repo_id=repo_id or f"repo-{repo_name.replace('/', '-')}",
        repo_name=repo_name,
        role="primary",
        workspace_relative_path=repo_name,
        canonical_path=str(canonical),
        source_path=str(source_path) if source_path is not None else None,
        alias_paths=[str(path) for path in alias_paths or []],
        remote_url=remote_url,
        remote_fingerprint=remote_fingerprint,
        branch=branch,
        head_sha=head_sha,
        git_common_dir=str(canonical / ".git"),
        source_git_common_dir=None,
        identity_kind="source_path",
        identity_value=str(source_path or canonical),
        writable_task_ids=writable_task_ids or ["TASK-1"],
        read_only_task_ids=[],
        safety_status="ok",
        safety_reasons=[],
        identity_evidence_digest=f"identity-evidence:{repo_name}",
    )


def _registry(
    module: Any,
    feature_root: Path,
    repos: list[Any],
    *,
    aliases: dict[Path | str, Path | str] | None = None,
    blocked: bool = False,
    blockers: list[dict[str, str]] | None = None,
    registry_digest: str = "registry-digest:test",
) -> Any:
    return module.CanonicalRepoRegistry(
        feature_id=FEATURE_ID,
        feature_slug=FEATURE_SLUG,
        feature_root=str(feature_root),
        registry_version="workspace-authority-v1",
        repos=repos,
        aliases={str(key): str(value) for key, value in (aliases or {}).items()},
        collisions=[],
        blocked=blocked,
        blockers=blockers or [],
        registry_digest=registry_digest,
    )


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return proc.stdout.strip()


def _init_git_repo(path: Path, *, remote_url: str | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "config", "user.email", "workspace-authority@example.test")
    _git(path, "config", "user.name", "Workspace Authority Tests")
    (path / "README.md").write_text(f"# {path.name}\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "initial")
    if remote_url:
        _git(path, "remote", "add", "origin", remote_url)
    return path


def _status_failure_git_runner(module: Any, failure: Any) -> Any:
    def _runner(_cwd: Path, args: list[str]) -> Any:
        if args and args[0] == "status":
            if isinstance(failure, Exception):
                raise failure
            return module._GitResult(returncode=failure[0], stdout="", stderr=failure[1])
        if args == ["rev-parse", "--git-common-dir"]:
            return module._GitResult(returncode=0, stdout=".git\n")
        if args == ["branch", "--show-current"]:
            return module._GitResult(returncode=0, stdout="main\n")
        if args == ["rev-parse", "HEAD"]:
            return module._GitResult(returncode=0, stdout=f"{'1' * 40}\n")
        if args == ["diff", "--cached", "--name-status"]:
            return module._GitResult(returncode=0, stdout="")
        return module._GitResult(returncode=0, stdout="")

    return _runner


async def _build_registry(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    workspace_root: Path,
    feature_root: Path,
    tasks: list[ImplementationTask],
) -> Any:
    _configure_workspace_hints(module, monkeypatch, workspace_root, feature_root)
    authority = _new_authority(
        module,
        workspace_root=workspace_root,
        feature_root=feature_root,
    )
    return await _call(authority, "build_registry", FEATURE_ID, tasks)


@pytest.mark.asyncio
async def test_registry_digest_is_stable_independent_of_task_order(
    workspace_authority_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _feature_paths(tmp_path)
    _init_git_repo(
        feature_root / "backend",
        remote_url="https://github.com/iriai/backend.git",
    )
    _init_git_repo(
        feature_root / "frontend",
        remote_url="git@github.com:iriai/frontend.git",
    )
    tasks = [
        _task("TASK-backend", "backend", "backend/app.py"),
        _task("TASK-frontend", "frontend", "frontend/ui.ts"),
    ]

    first = await _build_registry(
        workspace_authority_module,
        monkeypatch,
        workspace_root,
        feature_root,
        tasks,
    )
    second = await _build_registry(
        workspace_authority_module,
        monkeypatch,
        workspace_root,
        feature_root,
        list(reversed(tasks)),
    )

    assert _field(first, "registry_digest")
    assert _field(first, "registry_digest") == _field(second, "registry_digest")
    assert [_field(repo, "repo_id") for repo in _field(first, "repos")] == [
        _field(repo, "repo_id") for repo in _field(second, "repos")
    ]


@pytest.mark.asyncio
async def test_remote_fingerprint_normalizes_https_and_ssh_forms(
    workspace_authority_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _feature_paths(tmp_path)
    _init_git_repo(
        feature_root / "https-widget",
        remote_url="https://token@example.com/OpenAI/Widget.git",
    )
    _init_git_repo(
        feature_root / "ssh-widget",
        remote_url="git@example.com:OpenAI/Widget.git",
    )
    _init_git_repo(
        feature_root / "other-widget",
        remote_url="ssh://git@example.com/OpenAI/OtherWidget.git",
    )

    registry = await _build_registry(
        workspace_authority_module,
        monkeypatch,
        workspace_root,
        feature_root,
        [
            _task("TASK-https", "https-widget", "https-widget/README.md"),
            _task("TASK-ssh", "ssh-widget", "ssh-widget/README.md"),
            _task("TASK-other", "other-widget", "other-widget/README.md"),
        ],
    )
    by_name = {_field(repo, "repo_name"): repo for repo in _field(registry, "repos")}

    https_fingerprint = _field(by_name["https-widget"], "remote_fingerprint")
    ssh_fingerprint = _field(by_name["ssh-widget"], "remote_fingerprint")
    other_fingerprint = _field(by_name["other-widget"], "remote_fingerprint")

    assert https_fingerprint == ssh_fingerprint
    assert https_fingerprint != other_fingerprint
    assert "token@" not in https_fingerprint
    assert not https_fingerprint.endswith(".git")


@pytest.mark.asyncio
async def test_legacy_multi_segment_repo_root_is_valid_workspace_identity(
    workspace_authority_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _feature_paths(tmp_path)
    _init_git_repo(
        feature_root / "services" / "newsvc",
        remote_url="git@example.com:iriai/newsvc.git",
    )

    registry = await _build_registry(
        workspace_authority_module,
        monkeypatch,
        workspace_root,
        feature_root,
        [
            _task(
                "TASK-newsvc",
                "services/newsvc",
                "services/newsvc/app.py",
            )
        ],
    )

    assert _field(registry, "blocked") is False
    assert _field(registry, "blockers") == []
    assert len(_field(registry, "repos")) == 1
    assert _field(_field(registry, "repos")[0], "workspace_relative_path") == "services/newsvc"


@pytest.mark.asyncio
async def test_registry_backed_wt_alias_maps_to_canonical_but_registered_wt_repo_does_not(
    workspace_authority_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _feature_paths(tmp_path)
    source_repo = _init_git_repo(
        workspace_root / "sources" / "iriai-studio-backend",
        remote_url="git@example.com:iriai/iriai-studio-backend.git",
    )
    canonical = feature_root / "iriai-studio-backend"
    alias = feature_root / "iriai-studio-backend-wt"
    _init_git_repo(canonical, remote_url="git@example.com:iriai/iriai-studio-backend.git")
    _init_git_repo(alias, remote_url="git@example.com:iriai/iriai-studio-backend.git")

    legitimate_wt = _init_git_repo(
        feature_root / "analytics-wt",
        remote_url="git@example.com:iriai/analytics-wt.git",
    )

    _configure_workspace_hints(
        workspace_authority_module,
        monkeypatch,
        workspace_root,
        feature_root,
    )
    authority = _new_authority(
        workspace_authority_module,
        workspace_root=workspace_root,
        feature_root=feature_root,
        registry_repos=[
            {
                "repo_id": "shared-backend",
                "repo_path": "iriai-studio-backend",
                "canonical_path": str(canonical),
                "source_path": str(source_repo),
                "task_ids": ["TASK-canonical"],
                "action": "extend",
            },
            {
                "repo_id": "shared-backend",
                "repo_path": "iriai-studio-backend-wt",
                "canonical_path": str(canonical),
                "source_path": str(source_repo),
                "task_ids": ["TASK-alias"],
                "action": "extend",
            },
        ],
    )
    registry = await _call(
        authority,
        "build_registry",
        FEATURE_ID,
        [
            _task(
                "TASK-canonical",
                "iriai-studio-backend",
                "iriai-studio-backend/app.py",
            ),
            _task(
                "TASK-alias",
                "iriai-studio-backend-wt",
                "iriai-studio-backend-wt/app.py",
            ),
            _task("TASK-legit-wt", "analytics-wt", "analytics-wt/app.py"),
        ],
    )

    aliases = _field(registry, "aliases", {})
    assert any(
        Path(alias_path).name == alias.name
        and Path(canonical_path).name == canonical.name
        for alias_path, canonical_path in aliases.items()
    )
    assert all(Path(alias_path).name != legitimate_wt.name for alias_path in aliases)

    authority = _new_authority(
        workspace_authority_module,
        workspace_root=workspace_root,
        feature_root=feature_root,
    )
    resolution = await _call(
        authority,
        "resolve_path",
        str(legitimate_wt / "README.md"),
        registry,
    )

    assert _field(resolution, "path_kind") == "canonical"
    assert Path(_field(resolution, "canonical_path")).parts[-2] == "analytics-wt"


@pytest.mark.asyncio
async def test_registry_rejects_external_git_metadata_before_dispatch(
    workspace_authority_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _feature_paths(tmp_path)
    repo = feature_root / "app"
    outside_git = tmp_path / "outside-git"
    repo.mkdir(parents=True)
    outside_git.mkdir()
    (repo / ".git").write_text(f"gitdir: {outside_git}\n", encoding="utf-8")

    registry = await _build_registry(
        workspace_authority_module,
        monkeypatch,
        workspace_root,
        feature_root,
        [_task("TASK-app", "app", "app/src/main.py")],
    )
    text = _joined_text(registry)

    assert _field(registry, "blocked") is True
    assert _field(registry, "repos") == []
    assert "repo_git_metadata_outside_feature_root" in text
    assert "operator_required" in text


@pytest.mark.asyncio
async def test_basename_only_wt_sibling_is_not_alias_without_identity_evidence(
    workspace_authority_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _feature_paths(tmp_path)
    _init_git_repo(
        feature_root / "service",
        remote_url="git@example.com:iriai/service.git",
    )
    _init_git_repo(
        feature_root / "service-wt",
        remote_url="git@example.com:iriai/different-service.git",
    )

    registry = await _build_registry(
        workspace_authority_module,
        monkeypatch,
        workspace_root,
        feature_root,
        [_task("TASK-service", "service", "service/src/app.py")],
    )

    authority = _new_authority(workspace_authority_module, feature_root=feature_root)
    resolution = await _call(
        authority,
        "resolve_path",
        feature_root / "service-wt" / "README.md",
        registry,
    )

    assert _field(registry, "aliases", {}) == {}
    assert _field(resolution, "path_kind") == "canonical"
    assert Path(_field(resolution, "canonical_path")).parts[-2] == "service-wt"
    assert _field(resolution, "alias_path") is None
    assert _field(resolution, "alias_exists") is False


@pytest.mark.asyncio
async def test_repo_id_collision_blocks_registry_before_dispatch(
    workspace_authority_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _feature_paths(tmp_path)
    _init_git_repo(feature_root / "primary")
    _init_git_repo(feature_root / "secondary")
    shared_repo_id = "legacy-colliding-repo-id"
    legacy_rows = [
        {
            "repo_id": shared_repo_id,
            "repo_path": "primary",
            "canonical_path": str(feature_root / "primary"),
            "source_path": str(workspace_root / "source-primary"),
            "task_ids": ["TASK-primary"],
            "action": "extend",
        },
        {
            "repo_id": shared_repo_id,
            "repo_path": "secondary",
            "canonical_path": str(feature_root / "secondary"),
            "source_path": str(workspace_root / "source-secondary"),
            "task_ids": ["TASK-secondary"],
            "action": "extend",
        },
    ]
    _configure_workspace_hints(
        workspace_authority_module,
        monkeypatch,
        workspace_root,
        feature_root,
    )
    authority = _new_authority(
        workspace_authority_module,
        feature_root=feature_root,
        registry_repos=legacy_rows,
    )

    registry = await _call(
        authority,
        "build_registry",
        FEATURE_ID,
        [
            _task("TASK-primary", "primary", "primary/src/app.py"),
            _task("TASK-secondary", "secondary", "secondary/src/app.py"),
        ],
    )
    report = await _call(
        authority,
        "preflight_targets",
        [_path_target(workspace_authority_module, "primary/src/app.py")],
        registry,
    )
    routes = await _call(authority, "route_preflight", report)
    text = _joined_text([registry, report, routes])

    assert _field(registry, "blocked") is True
    assert _field(registry, "collisions")
    assert _field(report, "approved") is False
    assert "repo_identity_collision" in text
    assert "operator_required" in text


@pytest.mark.asyncio
async def test_longest_prefix_alias_resolution_preserves_suffix(
    workspace_authority_module: Any,
    tmp_path: Path,
) -> None:
    _workspace_root, feature_root = _feature_paths(tmp_path)
    canonical = feature_root / "app"
    nested_canonical = canonical / "packages" / "nested"
    alias = feature_root / "app-wt"
    nested_alias = alias / "nested"
    (nested_canonical / "src").mkdir(parents=True)
    (nested_alias / "src").mkdir(parents=True)

    registry = _registry(
        workspace_authority_module,
        feature_root,
        [
            _repo_identity(
                workspace_authority_module,
                feature_root,
                "app",
                canonical_path=canonical,
                alias_paths=[alias, nested_alias],
            )
        ],
        aliases={alias: canonical, nested_alias: nested_canonical},
    )
    authority = _new_authority(workspace_authority_module, feature_root=feature_root)

    resolution = await _call(
        authority,
        "resolve_path",
        nested_alias / "src" / "module.py",
        registry,
    )

    assert _field(resolution, "path_kind") == "alias"
    assert Path(_field(resolution, "alias_path")) == nested_alias
    assert Path(_field(resolution, "canonical_path")) == (
        nested_canonical / "src" / "module.py"
    )


@pytest.mark.asyncio
async def test_outside_traversal_and_symlink_targets_reject_before_mutation(
    workspace_authority_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _workspace_root, feature_root = _feature_paths(tmp_path)
    repo = feature_root / "app"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "src" / "linked").symlink_to(outside)
    registry = _registry(
        workspace_authority_module,
        feature_root,
        [
            _repo_identity(
                workspace_authority_module,
                feature_root,
                "app",
                canonical_path=repo,
            )
        ],
    )
    targets = [
        _path_target(workspace_authority_module, outside / "secret.py"),
        _path_target(workspace_authority_module, "app/../../escape.py"),
        _path_target(workspace_authority_module, repo / "src" / "linked" / "file.py"),
    ]
    mutation_attempts: list[Path] = []

    def _forbid_chmod(path: str | os.PathLike[str], mode: int) -> None:
        mutation_attempts.append(Path(path))
        raise AssertionError(f"preflight safety blockers must not chmod {path}:{mode:o}")

    monkeypatch.setattr(os, "chmod", _forbid_chmod)
    authority = _new_authority(workspace_authority_module, feature_root=feature_root)

    report = await _call(authority, "preflight_targets", targets, registry)
    normalized = await _call(authority, "normalize_acl", report)
    routes = await _call(authority, "route_preflight", report)
    text = _joined_text([report, normalized])
    route_payloads = _values_for_key(routes, "payload")
    symlink_payloads = [
        payload for payload in route_payloads
        if isinstance(payload, dict) and payload.get("raw_path") == str(repo / "src" / "linked" / "file.py")
    ]

    assert _field(report, "approved") is False
    assert "outside_root" in text
    assert "symlink" in text
    assert "operator_required" in text
    assert symlink_payloads
    assert symlink_payloads[0]["reason"] == "symlink_blocker"
    assert mutation_attempts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("symlink_kind", ["feature_root", "repos_root"])
async def test_registry_blocks_symlinked_feature_or_repos_root_before_discovery(
    workspace_authority_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    symlink_kind: str,
) -> None:
    workspace_root = tmp_path / "workspace"
    feature_parent = workspace_root / ".iriai" / "features"
    feature_parent.mkdir(parents=True)
    feature_dir = feature_parent / FEATURE_SLUG
    feature_root = feature_dir / "repos"

    if symlink_kind == "feature_root":
        outside_feature = tmp_path / "outside-feature"
        outside_repos = outside_feature / "repos"
        _init_git_repo(outside_repos / "app")
        feature_dir.symlink_to(outside_feature, target_is_directory=True)
    else:
        outside_repos = tmp_path / "outside-repos"
        _init_git_repo(outside_repos / "app")
        feature_dir.mkdir()
        feature_root.symlink_to(outside_repos, target_is_directory=True)

    registry = await _build_registry(
        workspace_authority_module,
        monkeypatch,
        workspace_root,
        feature_root,
        [_task("TASK-app", "app", "app/src/main.py")],
    )
    text = _joined_text(registry)

    assert _field(registry, "blocked") is True
    assert _field(registry, "repos") == []
    assert "symlink" in text
    assert "operator_required" in text
    assert str(outside_repos / "app") not in _values_for_key(registry, "canonical_path")


@pytest.mark.asyncio
async def test_normalize_acl_blocks_symlinked_repos_root_without_chmodding_outside(
    workspace_authority_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    feature_dir = workspace_root / ".iriai" / "features" / FEATURE_SLUG
    feature_dir.mkdir(parents=True)
    outside_repos = tmp_path / "outside-repos"
    outside_repo = outside_repos / "app"
    outside_target = outside_repo / "src" / "locked.py"
    (outside_repo / ".git").mkdir(parents=True)
    outside_target.parent.mkdir(parents=True)
    outside_target.write_text("do not chmod through symlink\n", encoding="utf-8")
    outside_repo.chmod(0o700)
    outside_target.parent.chmod(0o700)
    outside_target.chmod(0o600)

    feature_root = feature_dir / "repos"
    feature_root.symlink_to(outside_repos, target_is_directory=True)
    target_path = feature_root / "app" / "src" / "locked.py"
    report = workspace_authority_module.WorkspacePreflight(
        approved=True,
        acl_targets=[
            workspace_authority_module.AclTarget(
                repo_id="repo-app",
                raw_path=str(target_path),
                canonical_path=str(target_path),
                action="modify",
                nearest_existing_parent=str(target_path.parent),
                repo_root=str(feature_root / "app"),
            )
        ],
        feature_root=str(feature_root),
    )
    chmod_attempts: list[Path] = []
    original_modes = {
        outside_repo: stat.S_IMODE(outside_repo.stat().st_mode),
        outside_target.parent: stat.S_IMODE(outside_target.parent.stat().st_mode),
        outside_target: stat.S_IMODE(outside_target.stat().st_mode),
    }

    def _forbid_chmod(path: str | os.PathLike[str], mode: int) -> None:
        chmod_attempts.append(Path(path))
        raise AssertionError(f"root symlink blocker must not chmod {path}:{mode:o}")

    monkeypatch.setattr(os, "chmod", _forbid_chmod)
    authority = _new_authority(
        workspace_authority_module,
        workspace_root=workspace_root,
        feature_root=feature_root,
    )

    normalized = await _call(authority, "normalize_acl", report)
    text = _joined_text(normalized)

    assert _field(normalized, "approved") is False
    assert _field(normalized, "repair_route") == "operator_required"
    assert "repos_root_symlink" in text
    assert "operator_required" in text
    assert chmod_attempts == []
    assert {
        path: stat.S_IMODE(path.stat().st_mode)
        for path in original_modes
    } == original_modes


@pytest.mark.asyncio
async def test_acl_chmod_failure_does_not_replace_product_files(
    workspace_authority_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _workspace_root, feature_root = _feature_paths(tmp_path)
    repo = feature_root / "app"
    product_file = repo / "src" / "locked.py"
    product_file.parent.mkdir(parents=True)
    (repo / ".git").mkdir()
    product_file.write_text("important bytes\n", encoding="utf-8")
    repo.chmod(0o777)
    product_file.parent.chmod(0o777)
    product_file.chmod(0o444)
    registry = _registry(
        workspace_authority_module,
        feature_root,
        [
            _repo_identity(
                workspace_authority_module,
                feature_root,
                "app",
                canonical_path=repo,
            )
        ],
    )
    original_chmod = os.chmod
    replace_attempts: list[tuple[Path, Path]] = []

    def _fail_file_chmod(path: str | os.PathLike[str], mode: int) -> None:
        if Path(path) == product_file:
            raise PermissionError("simulated chmod denial")
        original_chmod(path, mode)

    def _forbid_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        replace_attempts.append((Path(src), Path(dst)))
        raise AssertionError("workspace authority must not replace product files")

    monkeypatch.setattr(os, "chmod", _fail_file_chmod)
    monkeypatch.setattr(os, "replace", _forbid_replace)
    authority = _new_authority(workspace_authority_module, feature_root=feature_root)

    try:
        report = await _call(
            authority,
            "preflight_targets",
            [_path_target(workspace_authority_module, product_file, action="modify")],
            registry,
        )
        normalized = await _call(authority, "normalize_acl", report)

        assert _field(normalized, "approved") is False
        assert "chmod_failed" in _joined_text(normalized)
        assert replace_attempts == []
        assert product_file.read_text(encoding="utf-8") == "important bytes\n"
    finally:
        original_chmod(product_file, 0o644)


def test_agent_writable_requires_configured_shared_gid_for_feature_group_write(
    workspace_authority_module: Any,
    tmp_path: Path,
) -> None:
    _workspace_root, feature_root = _feature_paths(tmp_path)
    repo = feature_root / "app"
    target = repo / "src"
    target.mkdir(parents=True)
    target.chmod(0o770)

    outside_repo = tmp_path / "plain-repo"
    outside_target = outside_repo / "src"
    outside_target.mkdir(parents=True)
    outside_target.chmod(0o700)

    assert workspace_authority_module.path_agent_writable(
        target,
        repo_path=repo,
        shared_gid=None,
    ) is False
    assert workspace_authority_module.path_agent_writable(
        target,
        repo_path=repo,
        shared_gid=target.stat().st_gid,
    ) is True
    assert workspace_authority_module.path_agent_writable(
        outside_target,
        repo_path=outside_repo,
        shared_gid=None,
    ) is True


@pytest.mark.asyncio
async def test_preflight_rejects_group_writable_feature_acl_without_shared_gid(
    workspace_authority_module: Any,
    tmp_path: Path,
) -> None:
    _workspace_root, feature_root = _feature_paths(tmp_path)
    repo = feature_root / "app"
    parent = repo / "src"
    parent.mkdir(parents=True)
    repo.chmod(0o770)
    parent.chmod(0o770)
    registry = _registry(
        workspace_authority_module,
        feature_root,
        [
            _repo_identity(
                workspace_authority_module,
                feature_root,
                "app",
                canonical_path=repo,
            )
        ],
    )
    target = _path_target(
        workspace_authority_module,
        parent / "new_file.py",
        action="create",
    )

    authority = _new_authority(workspace_authority_module, feature_root=feature_root)
    report = await _call(authority, "preflight_targets", [target], registry)

    shared_authority = _new_authority(
        workspace_authority_module,
        feature_root=feature_root,
        shared_gid=repo.stat().st_gid,
    )
    shared_report = await _call(
        shared_authority,
        "preflight_targets",
        [target],
        registry,
    )

    assert _field(report, "approved") is False
    assert "unwritable_runtime_path" in _joined_text(report)
    assert _field(shared_report, "approved") is True


@pytest.mark.asyncio
async def test_alias_only_and_divergent_aliases_route_without_operator_required(
    workspace_authority_module: Any,
    tmp_path: Path,
) -> None:
    _workspace_root, feature_root = _feature_paths(tmp_path)
    canonical = feature_root / "app"
    alias = feature_root / "app-wt"
    (canonical / "src").mkdir(parents=True)
    (alias / "src").mkdir(parents=True)
    (alias / "src" / "alias_only.py").write_text("alias only\n", encoding="utf-8")
    (canonical / "src" / "divergent.py").write_text("canonical\n", encoding="utf-8")
    (alias / "src" / "divergent.py").write_text("alias\n", encoding="utf-8")
    registry = _registry(
        workspace_authority_module,
        feature_root,
        [
            _repo_identity(
                workspace_authority_module,
                feature_root,
                "app",
                canonical_path=canonical,
                alias_paths=[alias],
            )
        ],
        aliases={alias: canonical},
    )
    authority = _new_authority(workspace_authority_module, feature_root=feature_root)

    report = await _call(
        authority,
        "preflight_targets",
        [
            _path_target(workspace_authority_module, alias / "src" / "alias_only.py"),
            _path_target(workspace_authority_module, alias / "src" / "divergent.py"),
        ],
        registry,
    )
    routes = await _call(authority, "route_preflight", report)
    text = _joined_text([report, routes])

    assert _field(report, "approved") is False
    assert "worktree_alias" in text
    assert "alias_only_canonical_missing" in text
    assert "alias_canonical_divergent" in text
    assert "run_canonicalization_repair" in text
    assert True not in _values_for_key([report, routes], "operator_required")
    assert "operator_clearance_required" not in text


@pytest.mark.asyncio
async def test_missing_create_parent_normalizes_nearest_existing_contained_parent(
    workspace_authority_module: Any,
    tmp_path: Path,
) -> None:
    _workspace_root, feature_root = _feature_paths(tmp_path)
    repo = feature_root / "app"
    nearest_parent = repo / "src"
    nearest_parent.mkdir(parents=True)
    nearest_parent.chmod(0o555)
    registry = _registry(
        workspace_authority_module,
        feature_root,
        [
            _repo_identity(
                workspace_authority_module,
                feature_root,
                "app",
                canonical_path=repo,
            )
        ],
    )
    target = repo / "src" / "generated" / "deep" / "new_file.py"
    authority = _new_authority(
        workspace_authority_module,
        feature_root=feature_root,
        shared_gid=nearest_parent.stat().st_gid,
    )

    try:
        report = await _call(
            authority,
            "preflight_targets",
            [
                _path_target(
                    workspace_authority_module,
                    target,
                    action="create",
                )
            ],
            registry,
        )
        normalized = await _call(authority, "normalize_acl", report)

        assert _has_nearest_parent(_field(report, "acl_targets", []), nearest_parent)
        assert _field(normalized, "approved") is True
        assert nearest_parent.stat().st_mode & stat.S_IWGRP
        assert not target.exists()
    finally:
        nearest_parent.chmod(0o755)


@pytest.mark.asyncio
async def test_acl_denied_target_records_evidence_and_normalization_repairs_parent(
    workspace_authority_module: Any,
    tmp_path: Path,
) -> None:
    _workspace_root, feature_root = _feature_paths(tmp_path)
    repo = feature_root / "app"
    denied_parent = repo / "src"
    denied_parent.mkdir(parents=True)
    denied_parent.chmod(0o555)
    registry = _registry(
        workspace_authority_module,
        feature_root,
        [
            _repo_identity(
                workspace_authority_module,
                feature_root,
                "app",
                canonical_path=repo,
            )
        ],
    )
    authority = _new_authority(
        workspace_authority_module,
        feature_root=feature_root,
        shared_gid=denied_parent.stat().st_gid,
    )

    try:
        report = await _call(
            authority,
            "preflight_targets",
            [
                _path_target(
                    workspace_authority_module,
                    denied_parent / "blocked.py",
                    action="create",
                )
            ],
            registry,
        )
        normalized = await _call(authority, "normalize_acl", report)

        text = _joined_text([report, normalized])
        assert "denied" in text or "unwritable_runtime_path" in text
        assert _has_nearest_parent(
            _field(normalized, "denied_targets", []),
            denied_parent,
        )
        assert _field(normalized, "approved") is True
        assert denied_parent.stat().st_mode & stat.S_IWGRP
    finally:
        denied_parent.chmod(0o755)


@pytest.mark.asyncio
async def test_snapshot_captures_git_state_and_idempotency_changes_with_status_and_stage(
    workspace_authority_module: Any,
    tmp_path: Path,
) -> None:
    _workspace_root, feature_root = _feature_paths(tmp_path)
    repo = _init_git_repo(
        feature_root / "app",
        remote_url="git@example.com:iriai/app.git",
    )
    (repo / "README.md").write_text("# app\n\nmodified\n", encoding="utf-8")
    (repo / "staged.py").write_text("print('staged')\n", encoding="utf-8")
    _git(repo, "add", "staged.py")
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    head_sha = _git(repo, "rev-parse", "HEAD")
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    registry = _registry(
        workspace_authority_module,
        feature_root,
        [
            _repo_identity(
                workspace_authority_module,
                feature_root,
                "app",
                canonical_path=repo,
                branch=branch,
                head_sha=head_sha,
                remote_url="git@example.com:iriai/app.git",
                remote_fingerprint="ssh://example.com/iriai/app",
            )
        ],
    )
    authority = _new_authority(workspace_authority_module, feature_root=feature_root)
    targets = [
        _path_target(workspace_authority_module, repo / "README.md"),
        _path_target(workspace_authority_module, repo / "staged.py"),
        _path_target(workspace_authority_module, repo / "untracked.txt"),
    ]

    first_snapshots = await _call(
        authority,
        "snapshot",
        FEATURE_ID,
        "dag-sha-for-slice-02",
        2,
        "dispatch",
        7,
        registry,
        targets,
        task_ids=["TASK-1"],
    )
    first = first_snapshots[0]
    _git(repo, "add", "README.md")
    staged_snapshots = await _call(
        authority,
        "snapshot",
        FEATURE_ID,
        "dag-sha-for-slice-02",
        2,
        "dispatch",
        7,
        registry,
        targets,
        task_ids=["TASK-1"],
    )
    verify_snapshots = await _call(
        authority,
        "snapshot",
        FEATURE_ID,
        "dag-sha-for-slice-02",
        2,
        "verify",
        7,
        registry,
        targets,
        task_ids=["TASK-1"],
    )

    assert _field(first, "head_sha") == head_sha
    assert _field(first, "branch") == branch
    assert "README.md" in _field(first, "dirty_paths")
    assert "staged.py" in _field(first, "staged_paths")
    assert "untracked.txt" in _field(first, "untracked_paths")
    assert _field(first, "no_dirty") is False
    assert _field(first, "index_digest")
    assert _field(first, "worktree_status_digest")
    assert _field(first, "idempotency_key") != _field(
        staged_snapshots[0], "idempotency_key"
    )
    assert _field(staged_snapshots[0], "idempotency_key") != _field(
        verify_snapshots[0], "idempotency_key"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_kind"),
    [
        ((128, "fatal: index file corrupt"), "corrupt_repo"),
        (
            subprocess.TimeoutExpired(["git", "status"], timeout=5),
            "status_timeout",
        ),
        (PermissionError("permission denied opening .git/index"), "permission_denied"),
    ],
)
async def test_preflight_rejects_status_unavailable_as_deterministic_blocker(
    workspace_authority_module: Any,
    tmp_path: Path,
    failure: Any,
    expected_kind: str,
) -> None:
    _workspace_root, feature_root = _feature_paths(tmp_path)
    repo = feature_root / "app"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "index").write_bytes(b"index bytes")
    (repo / "src").mkdir()
    registry = _registry(
        workspace_authority_module,
        feature_root,
        [
            _repo_identity(
                workspace_authority_module,
                feature_root,
                "app",
                canonical_path=repo,
            )
        ],
    )
    authority = _new_authority(
        workspace_authority_module,
        feature_root=feature_root,
        git_runner=_status_failure_git_runner(workspace_authority_module, failure),
    )

    first = await _call(
        authority,
        "preflight_targets",
        [_path_target(workspace_authority_module, repo / "src" / "main.py", action="read")],
        registry,
    )
    second = await _call(
        authority,
        "preflight_targets",
        [_path_target(workspace_authority_module, repo / "src" / "main.py", action="read")],
        registry,
    )
    routes = await _call(authority, "route_preflight", first)

    evidence = _field(first, "status_unavailable")[0]
    assert _field(first, "approved") is False
    assert _field(evidence, "reason") == "status_unavailable"
    assert _field(evidence, "status_failure_kind") == expected_kind
    assert _field(evidence, "evidence_digest") == _field(
        _field(second, "status_unavailable")[0],
        "evidence_digest",
    )
    assert _field(routes[0], "failure_type") == "status_unavailable"
    assert _field(routes[0], "route") == "quiesce"


@pytest.mark.asyncio
async def test_snapshot_status_unavailable_is_not_recorded_as_clean(
    workspace_authority_module: Any,
    tmp_path: Path,
) -> None:
    _workspace_root, feature_root = _feature_paths(tmp_path)
    repo = feature_root / "app"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "index").write_bytes(b"index bytes")
    (repo / "src").mkdir()
    registry = _registry(
        workspace_authority_module,
        feature_root,
        [
            _repo_identity(
                workspace_authority_module,
                feature_root,
                "app",
                canonical_path=repo,
            )
        ],
    )
    authority = _new_authority(
        workspace_authority_module,
        feature_root=feature_root,
        git_runner=_status_failure_git_runner(
            workspace_authority_module,
            (128, "fatal: unable to read .git/index"),
        ),
    )

    snapshots = await _call(
        authority,
        "snapshot",
        FEATURE_ID,
        "dag-sha-for-slice-06",
        6,
        "dispatch",
        7,
        registry,
        [_path_target(workspace_authority_module, repo / "src" / "main.py")],
        task_ids=["TASK-1"],
    )

    snapshot = snapshots[0]
    evidence = _field(snapshot, "status_unavailable")
    assert _field(snapshot, "no_dirty") is False
    assert _field(snapshot, "dirty_paths") == []
    assert _field(snapshot, "warnings") == ["status_unavailable"]
    assert _field(snapshot, "safety_status") == "status_unavailable"
    assert _field(evidence, "reason") == "status_unavailable"
    assert _field(snapshot, "worktree_status_digest") == workspace_authority_module.stable_digest(evidence)


@pytest.mark.asyncio
async def test_route_preflight_idempotency_key_is_stable(
    workspace_authority_module: Any,
    tmp_path: Path,
) -> None:
    _workspace_root, feature_root = _feature_paths(tmp_path)
    canonical = feature_root / "app"
    alias = feature_root / "app-wt"
    (canonical / "src").mkdir(parents=True)
    (alias / "src").mkdir(parents=True)
    (alias / "src" / "alias_only.py").write_text("alias only\n", encoding="utf-8")
    registry = _registry(
        workspace_authority_module,
        feature_root,
        [
            _repo_identity(
                workspace_authority_module,
                feature_root,
                "app",
                canonical_path=canonical,
                alias_paths=[alias],
            )
        ],
        aliases={alias: canonical},
    )
    authority = _new_authority(workspace_authority_module, feature_root=feature_root)
    report = await _call(
        authority,
        "preflight_targets",
        [_path_target(workspace_authority_module, alias / "src" / "alias_only.py")],
        registry,
    )

    first_routes = await _call(authority, "route_preflight", report)
    second_routes = await _call(authority, "route_preflight", report)

    first_keys = _values_for_key(first_routes, "idempotency_key")
    second_keys = _values_for_key(second_routes, "idempotency_key")
    assert first_keys
    assert first_keys == second_keys
    assert _joined_text(first_routes) == _joined_text(second_routes)


@pytest.mark.asyncio
async def test_implementer_start_ambiguity_is_unapproved_preflight_with_blockers(
    workspace_authority_module: Any,
    tmp_path: Path,
) -> None:
    _workspace_root, feature_root = _feature_paths(tmp_path)
    backend = feature_root / "backend"
    frontend = feature_root / "frontend"
    (backend / "src").mkdir(parents=True)
    (frontend / "src").mkdir(parents=True)
    registry = _registry(
        workspace_authority_module,
        feature_root,
        [
            _repo_identity(
                workspace_authority_module,
                feature_root,
                "backend",
                canonical_path=backend,
                repo_id="repo-backend",
            ),
            _repo_identity(
                workspace_authority_module,
                feature_root,
                "frontend",
                canonical_path=frontend,
                repo_id="repo-frontend",
            ),
        ],
    )
    authority = _new_authority(workspace_authority_module, feature_root=feature_root)

    report = await _call(
        authority,
        "preflight_targets",
        [
            _path_target(
                workspace_authority_module,
                "src/shared_name.py",
                action="modify",
                source="task",
            )
        ],
        registry,
    )

    assert _field(report, "approved") is False
    assert _field(report, "blockers")
    assert "ambiguous" in _joined_text(report)
