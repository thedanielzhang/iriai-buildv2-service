"""Provider ports and the mandatory NativeGit provider for Slice 21."""

from __future__ import annotations

import asyncio
import os
import re
import select
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from iriai_build_v2.workflows.develop.execution.workspace_authority import RepoIdentity

from .models import (
    CodeSpanRef,
    ContextLayerBudget,
    ContextProviderName,
    ProviderAvailability,
    ProviderIndexResult,
    ProviderLineageRecord,
    ProviderStateRef,
    digest_payload,
    sha256_hex,
)


_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
_TRAILER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):\s*(.+)$")


class ProvenanceProvider(Protocol):
    """Read-only provider contract from the Slice 21 source document."""

    name: ContextProviderName

    async def available(self) -> ProviderAvailability:
        """Return provider availability without blocking workflow authority."""

    async def index_repo(
        self,
        repo: RepoIdentity,
        *,
        budget: ContextLayerBudget,
    ) -> ProviderIndexResult:
        """Return a bounded provider-state ref for one repo."""

    async def query_spans(
        self,
        repo: RepoIdentity,
        spans: Sequence[CodeSpanRef],
        *,
        budget: ContextLayerBudget,
    ) -> list[ProviderLineageRecord]:
        """Return bounded lineage records for the selected spans."""


@dataclass(frozen=True)
class _GitResult:
    returncode: int
    stdout: str
    stderr: str
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class NativeGitProvider:
    """Mandatory fallback provider backed only by local Git commands.

    The provider reads Git blame/log/message/notes state plus optional typed
    commit-proof refs supplied by the caller. It never shells out during import
    and never writes Git refs, notes, files, workflow state, or provider caches.
    """

    name: ContextProviderName = "native_git"

    def __init__(
        self,
        repos: Sequence[RepoIdentity] | Mapping[str, Path | str],
        *,
        commit_proof_refs: Mapping[str, Sequence[str]] | None = None,
        checked_at: datetime | None = None,
    ) -> None:
        self._repos = _normalize_repos(repos)
        self._commit_proof_refs = {
            commit: tuple(refs) for commit, refs in (commit_proof_refs or {}).items()
        }
        self._checked_at = checked_at

    def repo_identity(self, repo_id: str) -> RepoIdentity | None:
        return self._repos.get(repo_id)

    async def available(self) -> ProviderAvailability:
        result = await asyncio.to_thread(_run_git_version)
        checked_at = self._checked_at or datetime.now(timezone.utc)
        if result.returncode != 0:
            return ProviderAvailability(
                provider=self.name,
                status="unavailable",
                checked_at=checked_at,
                timeout_ms=1_000,
                message=(result.stderr or "git unavailable").strip(),
            )
        version = result.stdout.strip() or "git"
        return ProviderAvailability(
            provider=self.name,
            status="available",
            version=version,
            checked_at=checked_at,
            state_digest=digest_payload({"provider": self.name, "version": version}),
            timeout_ms=1_000,
        )

    async def index_repo(
        self,
        repo: RepoIdentity,
        *,
        budget: ContextLayerBudget,
    ) -> ProviderIndexResult:
        result = await asyncio.to_thread(
            _run_git,
            repo.canonical_path,
            ["rev-parse", "--verify", "HEAD"],
            budget.timeout_ms,
        )
        if result.returncode != 0:
            state_ref = ProviderStateRef(
                provider=self.name,
                repo_id=repo.repo_id,
                ref="HEAD",
                state_digest=digest_payload(
                    {
                        "provider": self.name,
                        "repo_id": repo.repo_id,
                        "status": "unavailable",
                        "stderr": result.stderr,
                    }
                ),
                indexed_at=datetime.now(timezone.utc),
                status="unavailable",
            )
            return ProviderIndexResult(
                provider=self.name,
                repo_id=repo.repo_id,
                state_ref=state_ref,
                indexed=False,
                warnings=["native_git_index_unavailable"],
            )
        head = result.stdout.strip()
        state_digest = digest_payload(
            {
                "provider": self.name,
                "repo_id": repo.repo_id,
                "ref": "HEAD",
                "head": head,
            }
        )
        return ProviderIndexResult(
            provider=self.name,
            repo_id=repo.repo_id,
            state_ref=ProviderStateRef(
                provider=self.name,
                repo_id=repo.repo_id,
                ref="HEAD",
                state_digest=state_digest,
                indexed_at=datetime.now(timezone.utc),
                status="available",
            ),
            indexed=True,
            warnings=[],
            omitted_counts={},
        )

    async def query_spans(
        self,
        repo: RepoIdentity,
        spans: Sequence[CodeSpanRef],
        *,
        budget: ContextLayerBudget,
    ) -> list[ProviderLineageRecord]:
        records: list[ProviderLineageRecord] = []
        state = await self.index_repo(repo, budget=budget)
        provider_state_digest = (
            state.state_ref.state_digest
            if state.state_ref is not None
            else digest_payload({"provider": self.name, "repo_id": repo.repo_id})
        )
        for span in spans:
            if span.repo_id != repo.repo_id:
                continue
            if span.line_count > budget.max_lines_per_span:
                raise ValueError(
                    f"span {span.path}:{span.start_line}-{span.end_line} exceeds "
                    "max_lines_per_span"
                )
            blame = await asyncio.to_thread(
                _run_git_bounded_stdout,
                repo.canonical_path,
                [
                    "blame",
                    "--line-porcelain",
                    f"-L{span.start_line},{span.end_line}",
                    span.ref,
                    "--",
                    span.path,
                ],
                budget.timeout_ms,
                budget.max_provider_payload_bytes,
            )
            commits = _parse_blame_commits(blame.stdout) if blame.returncode == 0 else []
            warnings: list[str] = []
            if blame.stdout_truncated:
                warnings.append("provider_payload_budget_exhausted:git_blame")
            if blame.returncode != 0:
                warnings.append("native_git_blame_unavailable")
            if len(commits) > budget.max_commits:
                warnings.append("commit_budget_exhausted")
                commits = commits[: budget.max_commits]
            content_digest = self._content_digest_from_blame(repo, span, blame)
            provider_refs: list[str] = []
            for commit in commits:
                provider_refs.append(f"git-commit:{commit}")
                message_refs, message_warnings = await self._commit_message_refs(
                    repo, commit, budget
                )
                provider_refs.extend(message_refs)
                warnings.extend(message_warnings)
                note_ref, note_warnings = await self._git_note_ref(repo, commit, budget)
                if note_ref is not None:
                    provider_refs.append(note_ref)
                warnings.extend(note_warnings)
                provider_refs.extend(self._commit_proof_refs.get(commit, ()))
            if not commits:
                warnings.append("line_provenance_gap:no_blame_commit")
            if commits and not any(ref.startswith("dag-commit-proof:") for ref in provider_refs):
                warnings.append("line_provenance_gap:missing_commit_proof")
            record_payload = {
                "provider": self.name,
                "repo_id": repo.repo_id,
                "path": span.path,
                "start_line": span.start_line,
                "end_line": span.end_line,
                "ref": span.ref,
                "commit_hashes": commits,
                "provider_refs": provider_refs,
                "provider_state_digest": provider_state_digest,
                "content_digest": content_digest,
            }
            records.append(
                ProviderLineageRecord(
                    record_id=f"native-git:{digest_payload(record_payload)[:16]}",
                    provider=self.name,
                    repo_id=repo.repo_id,
                    path=span.path,
                    start_line=span.start_line,
                    end_line=span.end_line,
                    code_span=span,
                    commit_hashes=commits,
                    provider_refs=provider_refs,
                    provider_state_digest=provider_state_digest,
                    content_digest=content_digest,
                    confidence=0.92 if commits else 0.2,
                    warnings=warnings,
                )
            )
        return records

    def _content_digest_from_blame(
        self,
        repo: RepoIdentity,
        span: CodeSpanRef,
        blame: _GitResult,
    ) -> str:
        del self
        if span.ref is None:
            ref = "HEAD"
        else:
            ref = span.ref
        if span.line_count <= 0:
            return digest_payload(
                {
                    "repo_id": repo.repo_id,
                    "path": span.path,
                    "start_line": span.start_line,
                    "end_line": span.end_line,
                    "invalid": True,
                }
            )
        return digest_payload(
            {
                "repo_id": repo.repo_id,
                "path": span.path,
                "start_line": span.start_line,
                "end_line": span.end_line,
                "ref": ref,
                "selected_lines_sha256": sha256_hex(
                    "\n".join(_parse_blame_source_lines(span, blame.stdout))
                )
                if blame.returncode == 0 and not blame.stdout_truncated
                else None,
                "unavailable": blame.returncode != 0 or blame.stdout_truncated,
            }
        )

    async def _commit_message_refs(
        self,
        repo: RepoIdentity,
        commit: str,
        budget: ContextLayerBudget,
    ) -> tuple[list[str], list[str]]:
        result = await asyncio.to_thread(
            _run_git_bounded_stdout,
            repo.canonical_path,
            ["show", "-s", "--format=%B", commit],
            budget.timeout_ms,
            budget.max_provider_payload_bytes,
        )
        warnings: list[str] = []
        if result.stdout_truncated:
            warnings.append("provider_payload_budget_exhausted:git_message")
        if result.returncode != 0:
            return [], warnings
        refs = [f"git-message:{commit}"]
        for line in result.stdout.splitlines():
            match = _TRAILER_RE.match(line.strip())
            if match and match.group(1).lower().startswith("iriai-"):
                refs.append(f"git-trailer:{commit}:{match.group(1)}={match.group(2)}")
        return refs, warnings

    async def _git_note_ref(
        self,
        repo: RepoIdentity,
        commit: str,
        budget: ContextLayerBudget,
    ) -> tuple[str | None, list[str]]:
        result = await asyncio.to_thread(
            _run_git_bounded_stdout,
            repo.canonical_path,
            ["notes", "--ref=iriai", "show", commit],
            budget.timeout_ms,
            budget.max_provider_payload_bytes,
        )
        warnings: list[str] = []
        if result.stdout_truncated:
            warnings.append("provider_payload_budget_exhausted:git_note")
        if result.returncode != 0 or not result.stdout.strip() or result.stdout_truncated:
            return None, warnings
        return f"git-notes:refs/notes/iriai:{commit}:{sha256_hex(result.stdout)}", warnings


def _parse_blame_source_lines(span: CodeSpanRef, output: str) -> list[str]:
    source_lines = [
        line[1:]
        for line in output.splitlines()
        if line.startswith("\t")
    ]
    return source_lines[: span.line_count]


def _normalize_repos(
    repos: Sequence[RepoIdentity] | Mapping[str, Path | str],
) -> dict[str, RepoIdentity]:
    if isinstance(repos, Mapping):
        return {
            repo_id: RepoIdentity(
                repo_id=repo_id,
                repo_name=repo_id,
                canonical_path=str(path),
                workspace_relative_path=".",
                safety_status="ok",
            )
            for repo_id, path in repos.items()
        }
    return {repo.repo_id: repo for repo in repos}


def _parse_blame_commits(output: str) -> list[str]:
    commits: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        first = line.split(" ", 1)[0]
        if _COMMIT_RE.fullmatch(first) and first not in seen:
            seen.add(first)
            commits.append(first)
    return commits


def _run_git_version() -> _GitResult:
    try:
        completed = subprocess.run(
            ["git", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _GitResult(returncode=1, stdout="", stderr=str(exc))
    return _GitResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _run_git(repo_path: str, args: Sequence[str], timeout_ms: int) -> _GitResult:
    try:
        completed = subprocess.run(
            ["git", "-C", repo_path, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _GitResult(returncode=1, stdout="", stderr=str(exc))
    return _GitResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _run_git_bounded_stdout(
    repo_path: str,
    args: Sequence[str],
    timeout_ms: int,
    max_stdout_bytes: int,
) -> _GitResult:
    argv = ["git", "-C", repo_path, *args]
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return _GitResult(returncode=1, stdout="", stderr=str(exc))

    assert process.stdout is not None
    assert process.stderr is not None
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_size = 0
    stderr_size = 0
    stdout_truncated = False
    stderr_truncated = False
    deadline = time.monotonic() + (timeout_ms / 1000)
    streams = {
        process.stdout.fileno(): "stdout",
        process.stderr.fileno(): "stderr",
    }
    stream_objects = {
        process.stdout.fileno(): process.stdout,
        process.stderr.fileno(): process.stderr,
    }

    while streams:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            process.wait()
            return _GitResult(
                returncode=1,
                stdout=_decode_chunks(stdout_chunks),
                stderr="git command timed out",
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            )
        readable, _, _ = select.select(list(streams.keys()), [], [], min(remaining, 0.05))
        if not readable:
            if process.poll() is not None:
                readable = list(streams.keys())
            else:
                continue
        for fd in readable:
            stream = stream_objects[fd]
            try:
                chunk = os.read(fd, 8192)
            except OSError:
                chunk = b""
            if not chunk:
                streams.pop(fd, None)
                continue
            if streams.get(fd) == "stdout":
                stdout_size += len(chunk)
                if stdout_size > max_stdout_bytes:
                    remaining_bytes = max(max_stdout_bytes - (stdout_size - len(chunk)), 0)
                    if remaining_bytes:
                        stdout_chunks.append(chunk[:remaining_bytes])
                    stdout_truncated = True
                    process.kill()
                    process.wait()
                    return _GitResult(
                        returncode=1,
                        stdout=_decode_chunks(stdout_chunks),
                        stderr="git stdout exceeded context provider payload budget",
                        stdout_truncated=True,
                        stderr_truncated=stderr_truncated,
                    )
                stdout_chunks.append(chunk)
            else:
                stderr_size += len(chunk)
                if stderr_size > 16_384:
                    remaining_bytes = max(16_384 - (stderr_size - len(chunk)), 0)
                    if remaining_bytes:
                        stderr_chunks.append(chunk[:remaining_bytes])
                    stderr_truncated = True
                    continue
                if not stderr_truncated:
                    stderr_chunks.append(chunk)

    return _GitResult(
        returncode=process.wait(),
        stdout=_decode_chunks(stdout_chunks),
        stderr=_decode_chunks(stderr_chunks),
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def _decode_chunks(chunks: Sequence[bytes]) -> str:
    return b"".join(chunks).decode("utf-8", errors="replace")


__all__ = [
    "NativeGitProvider",
    "ProvenanceProvider",
]
