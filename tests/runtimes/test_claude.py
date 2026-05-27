from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from iriai_compose.actors import Role

from iriai_build_v2.config import BUDGET_TIERS
from iriai_build_v2.runtimes.claude import ClaudeAgentRuntime


class _FakeClaudeAgentOptions:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakePermissionResultAllow:
    pass


class _FakePermissionResultDeny:
    def __init__(self, *, message: str):
        self.message = message


def _write_sandbox_manifest(
    sandbox_root: Path,
    cwd: Path,
    *,
    sandbox_id: str = "sandbox-04",
    writable_roots: list[Path] | None = None,
) -> Path:
    manifest_path = sandbox_root / "sandbox-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": "sandbox-runner-v1",
                "sandbox_id": sandbox_id,
                "root": str(sandbox_root),
                "repo_roots": {"app": str(cwd)},
                "writable_roots": [
                    str(path) for path in (writable_roots if writable_roots is not None else [cwd])
                ],
                "blocked_roots": [],
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def test_budget_tiers_use_opus_4_7_native_1m_context():
    assert BUDGET_TIERS["opus"] == "claude-opus-4-7"
    assert BUDGET_TIERS["opus_1m"] == "claude-opus-4-7"


def test_build_options_default_to_opus_4_7(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )

    runtime = object.__new__(ClaudeAgentRuntime)
    role = Role(name="pm", prompt="Plan the work", tools=["Read"])

    options = runtime._build_options(role, workspace=None)

    assert options.model == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_bound_write_role_forces_sandbox_and_blocks_escapes(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk.types",
        SimpleNamespace(
            PermissionResultAllow=_FakePermissionResultAllow,
            PermissionResultDeny=_FakePermissionResultDeny,
        ),
    )

    cwd = tmp_path / "sandbox"
    repo = cwd / "repo"
    outside = tmp_path / "outside"
    repo.mkdir(parents=True)
    outside.mkdir()
    (repo / "link-out").symlink_to(outside, target_is_directory=True)
    manifest_path = _write_sandbox_manifest(cwd, repo)

    runtime = object.__new__(ClaudeAgentRuntime)
    role = Role(
        name="implementer",
        prompt="Implement safely.",
        tools=["Read", "Write", "Edit"],
        metadata={
            "sandbox": False,
            "runtime_workspace_binding": {
                "sandbox_id": "sandbox-04",
                "cwd": str(repo),
                "workspace_override": str(repo),
                "writable_roots": [str(repo)],
                "readonly_roots": [],
                "blocked_roots": [],
                "manifest_path": str(manifest_path),
                "expires_at": "2999-01-01T00:00:00+00:00",
                "runtime": "claude",
            },
        },
    )

    options = runtime._build_options(role, workspace=SimpleNamespace(path=str(outside)))

    assert options.cwd == str(repo)
    assert options.sandbox == {"enabled": True}
    assert options.can_use_tool is not None
    assert not getattr(options, "add_dirs", None)
    allowed = await options.can_use_tool("Write", {"file_path": "src/new.py"}, None)
    absolute_escape = await options.can_use_tool("Write", {"file_path": str(outside / "x.py")}, None)
    relative_escape = await options.can_use_tool("Edit", {"file_path": "../outside/x.py"}, None)
    symlink_escape = await options.can_use_tool("Write", {"file_path": "link-out/x.py"}, None)

    assert isinstance(allowed, _FakePermissionResultAllow)
    assert isinstance(absolute_escape, _FakePermissionResultDeny)
    assert isinstance(relative_escape, _FakePermissionResultDeny)
    assert isinstance(symlink_escape, _FakePermissionResultDeny)


@pytest.mark.asyncio
async def test_bound_write_role_accepts_file_level_writable_roots(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk.types",
        SimpleNamespace(
            PermissionResultAllow=_FakePermissionResultAllow,
            PermissionResultDeny=_FakePermissionResultDeny,
        ),
    )

    sandbox_root = tmp_path / "sandbox"
    repo = sandbox_root / "repos" / "app"
    allowed_file = repo / "src" / "allowed.py"
    allowed_file.parent.mkdir(parents=True)
    manifest_path = _write_sandbox_manifest(
        sandbox_root,
        repo,
        writable_roots=[allowed_file],
    )
    runtime = object.__new__(ClaudeAgentRuntime)
    role = Role(
        name="implementer",
        prompt="Implement safely.",
        tools=["Read", "Write"],
        metadata={
            "runtime_workspace_binding": {
                "sandbox_id": "sandbox-04",
                "cwd": str(repo),
                "workspace_override": str(repo),
                "repo_roots": {"app": str(repo)},
                "writable_roots": [str(allowed_file)],
                "readonly_roots": [],
                "blocked_roots": [],
                "manifest_path": str(manifest_path),
                "expires_at": "2999-01-01T00:00:00+00:00",
                "runtime": "claude",
            },
        },
    )

    options = runtime._build_options(role, workspace=SimpleNamespace(path=str(repo)))

    assert options.cwd == str(repo)
    allowed = await options.can_use_tool("Write", {"file_path": "src/allowed.py"}, None)
    sibling = await options.can_use_tool("Write", {"file_path": "src/other.py"}, None)
    assert isinstance(allowed, _FakePermissionResultAllow)
    assert isinstance(sibling, _FakePermissionResultDeny)


def test_bound_write_role_rejects_writable_root_outside_bound_repo(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )

    sandbox_root = tmp_path / "sandbox"
    repo = sandbox_root / "repos" / "app"
    outside_repo_file = sandbox_root / "other" / "allowed.py"
    repo.mkdir(parents=True)
    outside_repo_file.parent.mkdir(parents=True)
    manifest_path = _write_sandbox_manifest(
        sandbox_root,
        repo,
        writable_roots=[outside_repo_file],
    )
    role = Role(
        name="implementer",
        prompt="Implement safely.",
        tools=["Read", "Write"],
        metadata={
            "runtime_workspace_binding": {
                "sandbox_id": "sandbox-04",
                "cwd": str(repo),
                "workspace_override": str(repo),
                "repo_roots": {"app": str(repo)},
                "writable_roots": [str(outside_repo_file)],
                "readonly_roots": [],
                "blocked_roots": [],
                "manifest_path": str(manifest_path),
                "expires_at": "2999-01-01T00:00:00+00:00",
                "runtime": "claude",
            },
        },
    )

    runtime = object.__new__(ClaudeAgentRuntime)
    with pytest.raises(RuntimeError, match="writable root is outside bound repo roots"):
        runtime._build_options(role, workspace=SimpleNamespace(path=str(repo)))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda binding, canonical, root: binding.update({"manifest_path": str(root / "missing.json")}), "sandbox manifest does not exist"),
        (
            lambda binding, canonical, root: (
                (root / "bad-manifest.json").write_text("{", encoding="utf-8"),
                binding.update({"manifest_path": str(root / "bad-manifest.json")}),
            ),
            "unreadable sandbox manifest",
        ),
        (
            lambda binding, canonical, root: binding.update(
                {"cwd": str(canonical), "workspace_override": str(canonical)}
            ),
            "cwd is outside sandbox root",
        ),
        (
            lambda binding, canonical, root: binding.update({"writable_roots": [str(canonical)]}),
            "binding writable root is outside sandbox root|binding writable roots do not match manifest",
        ),
    ],
)
def test_bound_write_role_rejects_unproved_binding(
    monkeypatch,
    tmp_path: Path,
    mutate,
    message: str,
):
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )

    sandbox_root = tmp_path / "sandbox"
    cwd = sandbox_root / "repos" / "app"
    cwd.mkdir(parents=True)
    canonical = tmp_path / "canonical" / "app"
    canonical.mkdir(parents=True)
    manifest_path = _write_sandbox_manifest(sandbox_root, cwd)
    binding = {
        "sandbox_id": "sandbox-04",
        "cwd": str(cwd),
        "workspace_override": str(cwd),
        "writable_roots": [str(cwd)],
        "readonly_roots": [],
        "blocked_roots": [],
        "manifest_path": str(manifest_path),
        "expires_at": "2999-01-01T00:00:00+00:00",
        "runtime": "claude",
    }
    mutate(binding, canonical, tmp_path)
    role = Role(
        name="implementer",
        prompt="Implement safely.",
        tools=["Read", "Write"],
        metadata={"runtime_workspace_binding": binding},
    )

    runtime = object.__new__(ClaudeAgentRuntime)
    with pytest.raises(RuntimeError, match=message):
        runtime._build_options(role, workspace=SimpleNamespace(path=str(cwd)))
