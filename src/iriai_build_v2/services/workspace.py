"""Workspace management: directory map building and isolated feature repo setup.

The workspace model centers on GitHub repos (with local-only as fallback).
Features get isolated repo copies at ``.iriai/features/{slug}/repos/{repo}/``.

Directory map lifecycle:
- **Repo catalog** (``## Repos`` section) is built by ``build_directory_map()``
  — a deterministic script run in workflow hooks.
- **Dependency graph** (``## Dependencies`` section) is built by the scoper
  agent during its investigation phase and preserved across catalog rebuilds.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from iriai_compose import Feature

    from ..models.outputs import ProjectContext, RepoSpec, ScopeOutput

# Directories to skip when scanning for git repos
_SKIP_DIRS = frozenset({
    "node_modules", ".iriai", "venv", ".venv", "__pycache__",
    ".git", ".tox", ".mypy_cache", ".pytest_cache", "dist",
    "build", ".eggs", "egg-info", ".next", ".nuxt",
})


# ── Directory Map ────────────────────────────────────────────────────────────


@dataclass
class RepoEntry:
    """A single repo discovered in the workspace."""

    name: str
    path: str  # relative to workspace root
    description: str = ""
    github_url: str = ""
    language: str = ""


@dataclass
class DirectoryMap:
    """Parsed DIRECTORY_MAP.md with repo catalog and dependency graph."""

    repos: dict[str, RepoEntry] = field(default_factory=dict)  # name -> entry
    dependencies: dict[str, list[str]] = field(default_factory=dict)  # name -> [dep names]
    raw_content: str = ""

    @classmethod
    def from_file(cls, path: Path) -> DirectoryMap:
        """Parse a DIRECTORY_MAP.md file."""
        text = path.read_text(encoding="utf-8")
        dm = cls(raw_content=text)

        # Parse ## Repos section
        repos_match = re.search(
            r"## Repos\s*\n(.*?)(?=\n## |\Z)", text, re.DOTALL,
        )
        if repos_match:
            for line in repos_match.group(1).strip().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("<!--"):
                    continue
                # Format: | name | path | description | github_url | language |
                if line.startswith("|"):
                    parts = [p.strip() for p in line.split("|")[1:-1]]
                    if len(parts) >= 2 and parts[0] and parts[0] != "Name":
                        entry = RepoEntry(
                            name=parts[0],
                            path=parts[1] if len(parts) > 1 else "",
                            description=parts[2] if len(parts) > 2 else "",
                            github_url=parts[3] if len(parts) > 3 else "",
                            language=parts[4] if len(parts) > 4 else "",
                        )
                        dm.repos[entry.name] = entry

        # Parse ## Dependencies section
        deps_match = re.search(
            r"## Dependencies\s*\n(.*?)(?=\n## |\Z)", text, re.DOTALL,
        )
        if deps_match:
            for line in deps_match.group(1).strip().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("<!--"):
                    continue
                # Format: repo-name -> dep1, dep2
                if "->" in line:
                    src, targets = line.split("->", 1)
                    src = src.strip()
                    deps = [d.strip() for d in targets.split(",") if d.strip()]
                    if src and deps:
                        dm.dependencies[src] = deps

        return dm

    def get_adjacent(self, repo_names: list[str]) -> list[str]:
        """Return all repos connected to the given repos (deps + dependents).

        Walks one hop in both directions: repos that ``repo_names`` depend on,
        and repos that depend on any of ``repo_names``.
        """
        names_set = set(repo_names)
        adjacent: set[str] = set()

        for name in repo_names:
            # Forward: repos this one depends on
            for dep in self.dependencies.get(name, []):
                if dep not in names_set:
                    adjacent.add(dep)

            # Reverse: repos that depend on this one
            for src, deps in self.dependencies.items():
                if src not in names_set and name in deps:
                    adjacent.add(src)

        return sorted(adjacent)


# ── Directory Map Builder ────────────────────────────────────────────────────


def build_directory_map(workspace_path: Path) -> DirectoryMap:
    """Build/refresh the repo catalog in DIRECTORY_MAP.md.

    Scans the workspace for git repos and writes the ``## Repos`` section.
    The ``## Dependencies`` section is preserved from any existing file (it is
    owned by the scoper agent).

    Returns the parsed DirectoryMap.
    """
    map_path = workspace_path / "DIRECTORY_MAP.md"

    # Preserve existing dependencies section
    existing_deps = ""
    if map_path.exists():
        existing = DirectoryMap.from_file(map_path)
        deps_match = re.search(
            r"(## Dependencies\s*\n.*?)(?=\n## |\Z)",
            existing.raw_content,
            re.DOTALL,
        )
        if deps_match:
            existing_deps = deps_match.group(1)

    # Discover repos
    entries = _scan_for_repos(workspace_path)

    # Build markdown
    lines = ["# Directory Map", "", "## Repos", ""]
    lines.append("| Name | Path | Description | GitHub URL | Language |")
    lines.append("|------|------|-------------|------------|----------|")
    for entry in sorted(entries, key=lambda e: e.path):
        lines.append(
            f"| {entry.name} | {entry.path} | {entry.description} "
            f"| {entry.github_url} | {entry.language} |"
        )

    lines.append("")
    if existing_deps:
        lines.append(existing_deps)
    else:
        lines.append("## Dependencies")
        lines.append("")
        lines.append("<!-- Dependency graph is built by the scoper agent -->")
        lines.append("<!-- Format: repo-name -> dep1, dep2 -->")

    lines.append("")
    map_path.write_text("\n".join(lines), encoding="utf-8")

    return DirectoryMap.from_file(map_path)


def _scan_for_repos(workspace_path: Path, max_depth: int = 3) -> list[RepoEntry]:
    """Recursively scan for git repos up to max_depth levels."""
    entries: list[RepoEntry] = []

    def _scan(path: Path, depth: int) -> None:
        if depth > max_depth:
            return
        if not path.is_dir():
            return

        git_dir = path / ".git"
        if git_dir.exists():
            entry = _build_repo_entry(workspace_path, path)
            if entry:
                entries.append(entry)
            return  # Don't recurse into repos

        for child in sorted(path.iterdir()):
            if child.name in _SKIP_DIRS or child.name.startswith("."):
                continue
            if child.is_dir():
                _scan(child, depth + 1)

    # Don't include the workspace root itself if it's a repo
    for child in sorted(workspace_path.iterdir()):
        if child.name in _SKIP_DIRS or child.name.startswith("."):
            continue
        if child.is_dir():
            _scan(child, 0)

    return entries


def _build_repo_entry(workspace_root: Path, repo_path: Path) -> RepoEntry | None:
    """Build a RepoEntry from a discovered git repo."""
    name = repo_path.name
    rel_path = str(repo_path.relative_to(workspace_root))

    # Get GitHub remote
    github_url = ""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # Normalize SSH URLs to HTTPS
            if url.startswith("git@github.com:"):
                url = url.replace("git@github.com:", "https://github.com/")
            if url.endswith(".git"):
                url = url[:-4]
            github_url = url
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Get description
    description = _get_repo_description(repo_path)

    # Get primary language
    language = _detect_language(repo_path)

    return RepoEntry(
        name=name,
        path=rel_path,
        description=description,
        github_url=github_url,
        language=language,
    )


def _get_repo_description(repo_path: Path) -> str:
    """Try to extract a one-line description from project files."""
    # pyproject.toml
    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
            match = re.search(r'description\s*=\s*"([^"]*)"', text)
            if match:
                return match.group(1)[:100]
        except OSError:
            pass

    # package.json
    pkg_json = repo_path / "package.json"
    if pkg_json.exists():
        try:
            import json
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            desc = data.get("description", "")
            if desc:
                return str(desc)[:100]
        except (OSError, ValueError):
            pass

    # README first non-empty, non-heading line
    readme = repo_path / "README.md"
    if readme.exists():
        try:
            for line in readme.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return line[:100]
        except OSError:
            pass

    return ""


def _detect_language(repo_path: Path) -> str:
    """Detect primary language from config files."""
    if (repo_path / "pyproject.toml").exists() or (repo_path / "setup.py").exists():
        return "python"
    if (repo_path / "package.json").exists():
        return "javascript/typescript"
    if (repo_path / "go.mod").exists():
        return "go"
    if (repo_path / "Cargo.toml").exists():
        return "rust"
    if (repo_path / "pom.xml").exists() or (repo_path / "build.gradle").exists():
        return "java"
    return ""


# ── Workspace Manager ────────────────────────────────────────────────────────


class WorkspaceManager:
    """Manages feature workspaces: repo resolution, worktree creation, cleanup."""

    def __init__(self, base_path: Path) -> None:
        self._base = base_path

    @property
    def directory_map_path(self) -> Path:
        return self._base / "DIRECTORY_MAP.md"

    def build_directory_map(self) -> DirectoryMap:
        """Build/refresh the repo catalog. Preserves the dependency graph."""
        return build_directory_map(self._base)

    def load_directory_map(self) -> DirectoryMap | None:
        """Load existing DIRECTORY_MAP.md without rebuilding."""
        if self.directory_map_path.exists():
            return DirectoryMap.from_file(self.directory_map_path)
        return None

    async def setup_feature_workspace(
        self,
        feature: Feature,
        scope: ScopeOutput,
    ) -> ProjectContext:
        """Resolve repos, expand with adjacent repos, create feature-local repo copies."""
        from ..models.outputs import ProjectContext, RepoSpec

        feature_dir = self._base / ".iriai" / "features" / feature.slug
        feature_root = feature_dir / "repos"
        outputs_dir = feature_dir / "outputs"
        feature_root.mkdir(parents=True, exist_ok=True)
        outputs_dir.mkdir(parents=True, exist_ok=True)

        dir_map = self.load_directory_map()

        # Start with directly-scoped repos
        all_repos = list(scope.repos)

        # Expand with adjacent repos from dependency graph
        if dir_map and dir_map.dependencies:
            scoped_names = [r.name for r in scope.repos]
            adjacent = dir_map.get_adjacent(scoped_names)
            for adj_name in adjacent:
                if adj_name not in scoped_names:
                    adj_entry = dir_map.repos.get(adj_name)
                    local_path = (
                        str(self._base / adj_entry.path)
                        if adj_entry
                        else ""
                    )
                    github_url = adj_entry.github_url if adj_entry else ""
                    all_repos.append(RepoSpec(
                        name=adj_name,
                        local_path=local_path,
                        github_url=github_url,
                        action="read_only",
                        relevance=f"Adjacent to {', '.join(scoped_names)} in dependency graph",
                    ))

        # Create isolated repo copies for all repos
        resolved: list[RepoSpec] = []
        for spec in all_repos:
            resolved_spec = await self._resolve_and_worktree(
                spec, feature_root, feature.slug,
            )
            resolved.append(resolved_spec)

        return ProjectContext(
            feature_name=feature.name,
            scope_type=scope.scope_type,
            repos=resolved,
            worktree_root=str(feature_root),
            workspace_path=str(self._base),
            outputs_path=str(outputs_dir),
            directory_map=dir_map.raw_content if dir_map else "",
        )

    async def _resolve_and_worktree(
        self,
        spec: RepoSpec,
        feature_root: Path,
        slug: str,
    ) -> RepoSpec:
        """Resolve a single repo and create an isolated feature-local copy."""

        worktree_dest = feature_root / spec.name

        # Already set up with an isolated clone (idempotent).
        if self._is_isolated_repo_copy(worktree_dest):
            return spec
        if worktree_dest.exists():
            self._remove_repo_path(worktree_dest)

        # Find the source repo
        source_path = self._find_source_repo(spec)
        if source_path is None:
            if spec.action == "new":
                await self._scaffold_new_repo(worktree_dest, spec)
                return spec.model_copy(update={"local_path": str(worktree_dest)})
            raise RuntimeError(
                f"Cannot resolve repo '{spec.name}': no local path found and "
                f"github_url '{spec.github_url}' is not cloned locally. "
                f"Clone the repo first or provide a valid local_path."
            )

        branch = None if spec.action == "read_only" else f"feature/{slug}"
        await self._clone_repo(source_path, worktree_dest, branch=branch)

        return spec.model_copy(update={"local_path": str(worktree_dest)})

    async def _clone_repo(
        self,
        source_path: Path,
        dest: Path,
        *,
        branch: str | None,
    ) -> None:
        """Clone a repo into the feature sandbox without mutating the source repo."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        await _run_git(
            dest.parent,
            "clone",
            "--no-local",
            str(source_path),
            str(dest),
        )
        if branch:
            await _run_git(dest, "checkout", "-B", branch)

    def _is_isolated_repo_copy(self, path: Path) -> bool:
        """Return true when *path* is a standalone git clone, not a linked worktree."""
        return path.exists() and not path.is_symlink() and (path / ".git").is_dir()

    def _remove_repo_path(self, path: Path) -> None:
        """Remove an existing feature repo path so it can be recreated safely."""
        if path.is_symlink() or path.is_file():
            path.unlink()
            return
        if path.exists():
            shutil.rmtree(path)

    def _find_source_repo(self, spec: RepoSpec) -> Path | None:
        """Find the source git repo on disk."""
        # Try explicit local_path first
        if spec.local_path:
            p = Path(spec.local_path)
            if p.is_absolute() and (p / ".git").exists():
                return p
            # Try relative to workspace
            p = self._base / spec.local_path
            if (p / ".git").exists():
                return p

        # Try to find by name in directory map
        dir_map = self.load_directory_map()
        if dir_map and spec.name in dir_map.repos:
            entry = dir_map.repos[spec.name]
            p = self._base / entry.path
            if (p / ".git").exists():
                return p

        return None

    async def _scaffold_new_repo(self, dest: Path, spec: RepoSpec) -> None:
        """Create a new repo from scratch."""
        dest.mkdir(parents=True, exist_ok=True)

        # Initialize git
        await _run_git(dest, "init", "-b", "main")

        # Create minimal files
        readme = dest / "README.md"
        readme.write_text(f"# {spec.name}\n\n{spec.relevance}\n", encoding="utf-8")

        gitignore = dest / ".gitignore"
        gitignore.write_text(
            "# Generated\n__pycache__/\n*.pyc\nnode_modules/\n.env\n",
            encoding="utf-8",
        )

        await _run_git(dest, "add", "-A")
        await _run_git(dest, "commit", "-m", f"chore: scaffold {spec.name}")

    async def cleanup_feature(self, feature_slug: str) -> None:
        """Remove all feature-local repo copies for a feature."""
        feature_root = self._base / ".iriai" / "features" / feature_slug / "repos"
        if not feature_root.exists():
            return
        shutil.rmtree(feature_root)


async def _run_git(cwd: Path, *args: str) -> str:
    """Run a git command asynchronously."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{stderr.decode().strip()}"
        )
    return stdout.decode().strip()
