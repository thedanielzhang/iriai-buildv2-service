from __future__ import annotations

import asyncio as _asyncio
import collections
import dataclasses
import hashlib
import itertools
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

try:
    import grp
except ImportError:  # pragma: no cover - non-Unix fallback.
    grp = None  # type: ignore[assignment]

from iriai_compose import AgentActor, Ask, Feature, Phase, WorkflowRunner, to_str
from iriai_compose.actors import Role
from pydantic import BaseModel, Field

from ....config import BUDGET_TIERS
from ....runtime_policy import (
    DEFAULT_RUNTIME_POLICY,
    PRIMARY_IMPL_SECONDARY_REVIEW_POLICY,
    RuntimePolicy,
    normalize_runtime_policy,
)
from ....models.outputs import (
    ArtifactRepairResult,
    BugFixAttempt,
    BugGroup,
    BugTriage,
    EnhancementBacklog,
    EnhancementDecomposition,
    EnhancementItem,
    Envelope,
    FindingLedger,
    FindingRecord,
    Gap,
    HandoverDoc,
    ImplementationDAG,
    ImplementationResult,
    ImplementationTask,
    Issue,
    RepairStrategyDecision,
    ReviewOutcome,
    RootCauseAnalysis,
    SubfeatureDecomposition,
    Verdict,
    envelope_done,
)
from ....models.state import BuildState
from ....roles import (
    implementer,
    integration_tester,
    lead_architect_gate_reviewer,
    qa_engineer,
    regression_tester,
    reviewer,
    root_cause_analyst,
    security_auditor,
    test_author,
    user,
    verifier,
)
from ....services.markdown import to_markdown
from ..._common import Gate, Notify
from ..._common._helpers import (
    PROMPT_FILE_THRESHOLD,
    ContextPackage,
    ContextPackageItem,
    _offload_if_large,
    build_context_package,
)
from ..._common._dag_paths import (
    canonicalize_dag_path,
    canonicalize_implementation_tasks,
    dag_path_canonicalization_enabled,
    dag_path_rewrites_to_records,
    find_retired_backend_path_references,
)
from ..._common._autonomy import autonomous_remainder_enabled, interaction_actor_for_phase
from ...public_exhibit import enqueue_public_exhibit_refresh
from ..._common._tasks import HostedInterview

logger = logging.getLogger(__name__)

VERIFY_RETRIES = 2
WARN_AFTER_CYCLES = 3
BLOCKING_SEVERITIES = frozenset({"blocker", "major"})
DAG_EXPANDED_VERIFY_ENV = "IRIAI_DAG_EXPANDED_VERIFY"
DAG_PARALLEL_REPAIR_ENV = "IRIAI_DAG_PARALLEL_REPAIR"
DAG_PREFLIGHT_REPAIR_ENV = "IRIAI_DAG_PREFLIGHT_REPAIR"
DAG_AUTO_RESOLVE_CONTRADICTIONS_ENV = "IRIAI_DAG_AUTO_RESOLVE_CONTRADICTIONS"
DAG_WORKSPACE_PERMISSION_REPAIR_ENV = "IRIAI_DAG_WORKSPACE_PERMISSION_REPAIR"
AGENT_SHARED_GROUP_ENV = "IRIAI_AGENT_SHARED_GROUP"
DEFAULT_AGENT_SHARED_GROUP = "iriai-agents"
CONTRADICTION_DECISIONS_KEY = "contradiction-decisions"
COMMIT_FAILURE_OUTPUT_LIMIT = 12000

DAG_REPAIR_ROLE_RUNTIMES: dict[str, str] = {
    # Under --bridge-claude-pool-codex-review, primary=Claude pool and
    # secondary=Codex. Keep this intentionally static so runtime balance is
    # role-based rather than a fragile per-run counter.
    "dag-normal-verify": "secondary",
    "dag-final-verify": "secondary",
    "dag-triage": "primary",
    "dag-rca": "primary",
    "dag-fix": "primary",
    "dag-focused-reverify": "primary",
    "dag-contradiction-resolve": "secondary",
    "lens:acceptance-coverage": "secondary",
    "lens:contract-protocol": "secondary",
    "lens:build-dependency": "primary",
    "lens:runtime-composition": "primary",
    "lens:security-boundary": "primary",
    "lens:regression-downstream": "primary",
}


def _env_flag_enabled(name: str, *, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _dag_parallel_repair_enabled() -> bool:
    return _env_flag_enabled(DAG_PARALLEL_REPAIR_ENV, default=True)


def _dag_preflight_repair_enabled() -> bool:
    return _env_flag_enabled(DAG_PREFLIGHT_REPAIR_ENV, default=True)


def _dag_auto_resolve_contradictions_enabled() -> bool:
    return _env_flag_enabled(DAG_AUTO_RESOLVE_CONTRADICTIONS_ENV, default=True)


def _dag_repair_runtime_for(
    role_or_lens: str,
    fallback: str | None = None,
) -> str | None:
    return DAG_REPAIR_ROLE_RUNTIMES.get(role_or_lens, fallback)


def _bounded_commit_output(value: str, *, limit: int = COMMIT_FAILURE_OUTPUT_LIMIT) -> str:
    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return f"{value[:limit]}\n\n[... truncated {omitted} chars ...]"


@dataclass(slots=True)
class CommitRepoOutcome:
    repo_path: str
    repo_name: str
    message: str
    status_before: str = ""
    status_after: str = ""
    dirty: bool = False
    command: list[str] = field(default_factory=list)
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    commit_hash: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_path": self.repo_path,
            "repo_name": self.repo_name,
            "message": self.message,
            "status_before": _bounded_commit_output(self.status_before),
            "status_after": _bounded_commit_output(self.status_after),
            "dirty": self.dirty,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": _bounded_commit_output(self.stdout),
            "stderr": _bounded_commit_output(self.stderr),
            "commit_hash": self.commit_hash,
            "error": self.error,
        }


class WorkflowCommitError(RuntimeError):
    """Raised when a dirty workflow repo cannot be committed."""

    def __init__(self, message: str, outcomes: list[CommitRepoOutcome]) -> None:
        self.outcomes = outcomes
        self.successful_hashes = [
            outcome.commit_hash for outcome in outcomes if outcome.commit_hash
        ]
        failed = [outcome for outcome in outcomes if outcome.error or outcome.exit_code]
        failed_repos = ", ".join(
            outcome.repo_name or outcome.repo_path for outcome in failed
        ) or "unknown repo"
        super().__init__(f"{message}: commit failed for {failed_repos}")

    @property
    def failed_outcomes(self) -> list[CommitRepoOutcome]:
        return [
            outcome for outcome in self.outcomes
            if outcome.error or outcome.exit_code
        ]

    def to_payload(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "failed_repo_count": len(self.failed_outcomes),
            "successful_commit_hashes": self.successful_hashes,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }


@dataclass(slots=True)
class CommitFailureLocation:
    file: str = ""
    line: int = 0


@dataclass(slots=True)
class CommitForbiddenPathMatch:
    path: str
    repo_path: str
    repo_name: str
    manifest_rule: str
    config_path: str
    source: str
    git_state: str = ""
    line: int = 0
    operator_required: bool = False
    operator_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "repo_path": self.repo_path,
            "repo_name": self.repo_name,
            "manifest_rule": self.manifest_rule,
            "config_path": self.config_path,
            "source": self.source,
            "git_state": self.git_state,
            "line": self.line,
            "operator_required": self.operator_required,
            "operator_reasons": self.operator_reasons,
        }


@dataclass(slots=True)
class DagDirectRepairRoute:
    route: str
    reason: str
    signature: str
    target_files: list[str] = field(default_factory=list)
    skip_expanded_verify: bool = False
    skip_parallel_repair: bool = False
    skip_rca: bool = False
    operator_required: bool = False
    workspace_permission_repair: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "reason": self.reason,
            "signature": self.signature,
            "target_files": self.target_files,
            "skip_expanded_verify": self.skip_expanded_verify,
            "skip_parallel_repair": self.skip_parallel_repair,
            "skip_rca": self.skip_rca,
            "operator_required": self.operator_required,
            "workspace_permission_repair": self.workspace_permission_repair,
        }


_COMMIT_HYGIENE_ROUTE = "commit_hygiene_focused"
_MANIFEST_FORBIDDEN_CLEANUP_ROUTE = "manifest_forbidden_product_cleanup"
_REPO_HYGIENE_ROUTE = "repo_hygiene_operator"
_NORMAL_VERIFY_ROUTE = "normal_verify_repair"
_MANIFEST_FORBIDDEN_MARKER = "manifest-forbidden product cleanup"
_OPERATOR_REQUIRED_MARKER = "operator_required=true"
_DAG_AUTHORITY_SEMANTIC_ROUTE = "semantic_verify_needed"
_DAG_AUTHORITY_DB_TASK_RESULT_ROUTE = "db_task_result_drift"
_DAG_AUTHORITY_TASK_SPEC_PROJECTION_ROUTE = "task_spec_projection_drift"
_DAG_AUTHORITY_SOURCE_ARTIFACT_ROUTE = "source_dag_artifact_drift"
_DAG_AUTHORITY_PRODUCT_WORKSPACE_ROUTE = "product_workspace_drift"
_DAG_AUTHORITY_REPO_BLOCKER_ROUTE = "repo_or_permission_blocker"


@dataclass(slots=True)
class DagAuthorityGateOutcome:
    route: str = _DAG_AUTHORITY_SEMANTIC_ROUTE
    status: str = "not_applicable"
    reason: str = ""
    repair_results: list[ImplementationResult] = field(default_factory=list)
    blocked_verdict: Verdict | None = None
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def handled(self) -> bool:
        return bool(self.repair_results or self.blocked_verdict is not None)


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
    return (
        normalized == forbidden
        or normalized.startswith(f"{forbidden}/")
        or forbidden.endswith(f"/{normalized}")
    )


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


def _feature_workspace_agent_owner_write_is_trustworthy(repo_path: Path) -> bool:
    parts = repo_path.resolve().parts
    return ".iriai" not in parts or "features" not in parts


def _path_agent_writable(path: Path, *, repo_path: Path) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    mode = st.st_mode
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        return True
    if (
        st.st_uid == os.getuid()
        and mode & stat.S_IWUSR
        and _feature_workspace_agent_owner_write_is_trustworthy(repo_path)
    ):
        return True
    return False


def _commit_forbidden_operator_reasons(
    outcome: CommitRepoOutcome,
    repo_relative_path: str,
) -> list[str]:
    repo_path = Path(outcome.repo_path)
    reasons: list[str] = []
    target = repo_path / repo_relative_path
    parent = target.parent
    if target.exists() and target.is_dir() and not _path_agent_writable(
        target,
        repo_path=repo_path,
    ):
        reasons.append(f"forbidden directory is not writable by repair agent: {target}")
    if parent.exists() and not _path_agent_writable(parent, repo_path=repo_path):
        reasons.append(f"parent directory is not writable by repair agent: {parent}")
    git_index = repo_path / ".git" / "index"
    if git_index.exists() and not _path_agent_writable(git_index, repo_path=repo_path):
        reasons.append(f"git index is not writable by repair agent: {git_index}")
    return reasons


def _commit_forbidden_path_matches(
    outcome: CommitRepoOutcome | None,
) -> list[CommitForbiddenPathMatch]:
    if outcome is None:
        return []
    entries = _commit_failure_manifest_entries(outcome)
    if not entries:
        return []
    candidates: list[dict[str, str | int]] = []
    candidates.extend(_commit_status_paths(outcome.status_after, source="status_after"))
    candidates.extend(_commit_status_paths(outcome.status_before, source="status_before"))
    deletion_only_paths = {
        _commit_repo_relative_path(path, outcome)
        for path in (
            _commit_deletion_only_status_paths(outcome.status_after)
            | _commit_deletion_only_status_paths(outcome.status_before)
        )
    }
    for location in _parse_commit_failure_locations(outcome):
        candidates.append({
            "path": location.file,
            "source": "hook_output",
            "git_state": "",
            "line": location.line,
        })

    matches: list[CommitForbiddenPathMatch] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        raw_path = str(candidate.get("path", ""))
        repo_relative = _commit_repo_relative_path(raw_path, outcome)
        if not repo_relative:
            continue
        if (
            str(candidate.get("source", "")) == "hook_output"
            and repo_relative in deletion_only_paths
            and not (Path(outcome.repo_path) / repo_relative).exists()
        ):
            continue
        for entry in entries:
            if not _commit_path_matches_forbidden_entry(repo_relative, entry):
                continue
            source = str(candidate.get("source", ""))
            key = (repo_relative, str(entry.get("path", "")), source)
            if key in seen:
                continue
            seen.add(key)
            operator_reasons = _commit_forbidden_operator_reasons(
                outcome,
                repo_relative,
            )
            matches.append(CommitForbiddenPathMatch(
                path=(
                    f"{outcome.repo_name}/{repo_relative}"
                    if outcome.repo_name
                    else repo_relative
                ),
                repo_path=outcome.repo_path,
                repo_name=outcome.repo_name,
                manifest_rule=str(entry.get("path", "")),
                config_path=str(entry.get("config_path", "")),
                source=source,
                git_state=str(candidate.get("git_state", "")),
                line=int(candidate.get("line") or 0),
                operator_required=bool(operator_reasons),
                operator_reasons=operator_reasons,
            ))
    return matches


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


def _commit_failure_issue(exc: WorkflowCommitError, *, stage: str) -> Issue:
    failed = exc.failed_outcomes[0] if exc.failed_outcomes else None
    detail = ""
    if failed:
        output = _commit_failure_output(failed)
        if output:
            detail = f" Hook/output excerpt: {_bounded_commit_output(output, limit=2000)}"
    location = _parse_commit_failure_location(failed)
    forbidden_matches = _commit_forbidden_path_matches(failed)
    if forbidden_matches:
        first = forbidden_matches[0]
        operator_required = any(match.operator_required for match in forbidden_matches)
        operator_text = ""
        if operator_required:
            reasons = []
            for match in forbidden_matches:
                reasons.extend(match.operator_reasons)
            unique_reasons = list(dict.fromkeys(reasons))
            operator_text = (
                " Host workspace permission normalization is required before "
                "dispatch because the repair agent may not be able to delete/stage "
                "the forbidden path."
            )
            if unique_reasons:
                operator_text += (
                    " Permission evidence: "
                    + "; ".join(unique_reasons[:3])
                )
        return Issue(
            severity="blocker",
            description=(
                f"{_MANIFEST_FORBIDDEN_MARKER} required during {stage}; "
                "pre-commit/husky output or git status references a path "
                "forbidden by verify-file-scope.expected-files.json. "
                "Do not repair this by adding ignore/suppression rules; "
                "delete or port the forbidden product files and preserve "
                "canonical coverage. "
                f"Matched path: {first.path}; manifest rule: "
                f"{first.manifest_rule}; git_state: {first.git_state or 'n/a'}; "
                f"source: {first.source}.{operator_text}{detail}"
            ),
            file=first.path,
            line=first.line or location.line,
        )
    if _is_repo_hygiene_outcome(failed):
        return Issue(
            severity="blocker",
            description=(
                f"workflow repo hygiene blocker during {stage}; operator/worktree "
                f"cleanup is required before checkpoint.{detail}"
            ),
            file=location.file or (failed.repo_path if failed else ""),
            line=location.line,
        )
    return Issue(
        severity="major",
        description=(
            f"pre-commit/husky failed during {stage}; fix repo hygiene before "
            f"checkpoint.{detail}"
        ),
        file=location.file or (failed.repo_path if failed else ""),
        line=location.line,
    )


def _commit_failure_verdict(
    exc: WorkflowCommitError,
    *,
    group_idx: int,
    stage: str,
) -> Verdict:
    failed_repos = [
        outcome.repo_name or outcome.repo_path for outcome in exc.failed_outcomes
    ]
    repo_summary = ", ".join(failed_repos) if failed_repos else "unknown repo"
    return Verdict(
        approved=False,
        summary=(
            f"Group {group_idx} cannot checkpoint: commit failed during {stage} "
            f"for {repo_summary}."
        ),
        concerns=[_commit_failure_issue(exc, stage=stage)],
    )


def _commit_failure_payload(
    exc: WorkflowCommitError,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = exc.to_payload()
    matches = [
        match.to_dict()
        for outcome in exc.failed_outcomes
        for match in _commit_forbidden_path_matches(outcome)
    ]
    if matches:
        payload["manifest_forbidden_matches"] = matches
    if metadata:
        payload["metadata"] = metadata
    return payload


async def _record_commit_failure_artifact(
    runner: WorkflowRunner,
    feature: Feature,
    key: str,
    exc: WorkflowCommitError,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    await runner.artifacts.put(
        key,
        json.dumps(_commit_failure_payload(exc, metadata=metadata), indent=2),
        feature=feature,
    )


async def _record_dag_commit_failure(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    stage: str,
    exc: WorkflowCommitError,
    *,
    message: str = "",
    extra_metadata: dict[str, Any] | None = None,
) -> None:
    metadata = {
        "group_idx": group_idx,
        "stage": stage,
        "message": message,
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    await _record_commit_failure_artifact(
        runner,
        feature,
        f"dag-commit-failure:g{group_idx}:{stage}",
        exc,
        metadata=metadata,
    )
    await _log_feature_event(
        runner,
        feature.id,
        "dag_commit_failed",
        "implementation",
        content=f"g{group_idx}:{stage}",
        metadata={
            "group_idx": group_idx,
            "stage": stage,
            "failed_repo_count": len(exc.failed_outcomes),
            "successful_commit_hashes": exc.successful_hashes,
        },
    )


def _commit_failure_issue_kind(issue: Issue) -> str:
    text = f"{issue.description}\n{issue.file}".lower()
    if _MANIFEST_FORBIDDEN_MARKER in text:
        return _MANIFEST_FORBIDDEN_CLEANUP_ROUTE
    if (
        "workflow repo hygiene blocker" in text
        or "workflow repos with hygiene blockers" in text
        or "embedded .git" in text
        or "gitlink" in text
    ):
        return _REPO_HYGIENE_ROUTE
    if (
        "pre-commit/husky failed" in text
        or ("commit failed" in text and ("pre-commit" in text or "husky" in text))
    ):
        return _COMMIT_HYGIENE_ROUTE
    return _NORMAL_VERIFY_ROUTE


def _is_deterministic_dag_preflight_issue(issue: Issue) -> bool:
    text = f"{issue.description}\n{issue.file}".lower()
    return bool(
        _MANIFEST_FORBIDDEN_MARKER in text
        or "dag-task:" in text
        or "source artifact:" in text
        or "source artifacts:" in text
        or "manifest-forbidden/stale path" in text
        or "reports changed file that is missing from the feature workspace" in text
        or "repair stale task metadata" in text
        or "programmatic dag preflight" in text
    )


def _direct_route_target(issue: Issue) -> str:
    if not issue.file:
        return ""
    if issue.line:
        return f"{issue.file}:{issue.line}"
    return issue.file


def _direct_route_issue_operator_required(issue: Issue) -> bool:
    return _OPERATOR_REQUIRED_MARKER in issue.description


def _normalize_direct_route_signature(verdict: Verdict, route: str) -> str:
    parts = [route]
    for issue in sorted(
        verdict.concerns,
        key=lambda item: (
            item.file,
            item.line,
            re.sub(r"\s+", " ", item.description.strip().lower()),
        ),
    ):
        description = re.sub(r"\s+", " ", issue.description.strip().lower())
        description = re.sub(r"retry-\d+", "retry-N", description)
        description = re.sub(r"attempt \d+", "attempt N", description)
        parts.append(f"{issue.file}:{issue.line}:{description[:500]}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _classify_dag_direct_repair_route(verdict: object) -> DagDirectRepairRoute:
    if not isinstance(verdict, Verdict) or verdict.approved:
        return DagDirectRepairRoute(
            route=_NORMAL_VERIFY_ROUTE,
            reason="not_a_failed_verdict",
            signature="",
        )
    if verdict.gaps:
        return DagDirectRepairRoute(
            route=_NORMAL_VERIFY_ROUTE,
            reason="verdict_has_gaps",
            signature="",
        )
    failed_checks = [check for check in verdict.checks if check.result == "FAIL"]
    if failed_checks:
        return DagDirectRepairRoute(
            route=_NORMAL_VERIFY_ROUTE,
            reason="verdict_has_failed_checks",
            signature="",
        )
    if not verdict.concerns:
        return DagDirectRepairRoute(
            route=_NORMAL_VERIFY_ROUTE,
            reason="verdict_has_no_concerns",
            signature="",
        )

    kinds = [_commit_failure_issue_kind(issue) for issue in verdict.concerns]
    if any(kind == _MANIFEST_FORBIDDEN_CLEANUP_ROUTE for kind in kinds) and all(
        kind == _MANIFEST_FORBIDDEN_CLEANUP_ROUTE
        or _is_deterministic_dag_preflight_issue(issue)
        for kind, issue in zip(kinds, verdict.concerns)
    ):
        targets = sorted({
            target
            for kind, issue in zip(kinds, verdict.concerns)
            if kind == _MANIFEST_FORBIDDEN_CLEANUP_ROUTE
            if (target := _direct_route_target(issue))
        })
        operator_required = any(
            _direct_route_issue_operator_required(issue)
            for issue in verdict.concerns
        )
        return DagDirectRepairRoute(
            route=_MANIFEST_FORBIDDEN_CLEANUP_ROUTE,
            reason=(
                "manifest_forbidden_cleanup_operator_required"
                if operator_required
                else "manifest_forbidden_cleanup"
            ),
            signature=_normalize_direct_route_signature(
                verdict,
                _MANIFEST_FORBIDDEN_CLEANUP_ROUTE,
            ),
            target_files=targets,
            skip_expanded_verify=True,
            skip_parallel_repair=True,
            skip_rca=True,
            operator_required=operator_required,
        )
    if any(kind == _NORMAL_VERIFY_ROUTE for kind in kinds):
        return DagDirectRepairRoute(
            route=_NORMAL_VERIFY_ROUTE,
            reason="verdict_has_non_commit_concerns",
            signature="",
        )
    if all(kind == _REPO_HYGIENE_ROUTE for kind in kinds):
        targets = sorted({
            target
            for issue in verdict.concerns
            if (target := _direct_route_target(issue))
        })
        return DagDirectRepairRoute(
            route=_REPO_HYGIENE_ROUTE,
            reason="repo_hygiene_only_verdict",
            signature=_normalize_direct_route_signature(verdict, _REPO_HYGIENE_ROUTE),
            target_files=targets,
            skip_expanded_verify=True,
            skip_parallel_repair=True,
            skip_rca=True,
            operator_required=True,
        )
    if all(kind == _COMMIT_HYGIENE_ROUTE for kind in kinds):
        targets = sorted({
            target
            for issue in verdict.concerns
            if (target := _direct_route_target(issue))
        })
        return DagDirectRepairRoute(
            route=_COMMIT_HYGIENE_ROUTE,
            reason="commit_hygiene_only_verdict",
            signature=_normalize_direct_route_signature(verdict, _COMMIT_HYGIENE_ROUTE),
            target_files=targets,
            skip_expanded_verify=True,
            skip_parallel_repair=True,
            skip_rca=True,
        )
    if all(kind == _MANIFEST_FORBIDDEN_CLEANUP_ROUTE for kind in kinds):
        targets = sorted({
            target
            for issue in verdict.concerns
            if (target := _direct_route_target(issue))
        })
        operator_required = any(
            _direct_route_issue_operator_required(issue)
            for issue in verdict.concerns
        )
        return DagDirectRepairRoute(
            route=_MANIFEST_FORBIDDEN_CLEANUP_ROUTE,
            reason=(
                "manifest_forbidden_commit_failure_operator_required"
                if operator_required
                else "manifest_forbidden_commit_failure"
            ),
            signature=_normalize_direct_route_signature(
                verdict,
                _MANIFEST_FORBIDDEN_CLEANUP_ROUTE,
            ),
            target_files=targets,
            skip_expanded_verify=True,
            skip_parallel_repair=True,
            skip_rca=True,
            operator_required=operator_required,
        )
    return DagDirectRepairRoute(
        route=_NORMAL_VERIFY_ROUTE,
        reason="mixed_deterministic_routes",
        signature="",
    )


async def _record_dag_direct_repair_route(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry: int,
    route: DagDirectRepairRoute,
    *,
    status: str,
    source_verdict_key: str,
    guardrail_decision: str,
) -> None:
    payload = route.to_dict()
    payload.update({
        "group_idx": group_idx,
        "retry": retry,
        "status": status,
        "source_verdict_key": source_verdict_key,
        "guardrail_decision": guardrail_decision,
    })
    await runner.artifacts.put(
        f"dag-direct-repair-route:g{group_idx}:retry-{retry}",
        json.dumps(payload, indent=2),
        feature=feature,
    )


async def _direct_route_repeated_signature(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry: int,
    route: DagDirectRepairRoute,
) -> bool:
    if retry <= 0 or not route.signature:
        return False
    if route.route == _MANIFEST_FORBIDDEN_CLEANUP_ROUTE and retry <= 1:
        return False
    previous = await runner.artifacts.get(
        f"dag-direct-repair-route:g{group_idx}:retry-{retry - 1}",
        feature=feature,
    )
    if not previous:
        return False
    try:
        payload = json.loads(previous)
    except (TypeError, ValueError):
        return False
    return (
        payload.get("route") == route.route
        and payload.get("signature") == route.signature
        and payload.get("status") in {"selected", "blocked_repeat"}
    )


def _repeated_direct_route_verdict(
    *,
    group_idx: int,
    retry: int,
    route: DagDirectRepairRoute,
) -> Verdict:
    target = route.target_files[0] if route.target_files else ""
    file = target
    line = 0
    if ":" in target:
        maybe_file, maybe_line = target.rsplit(":", 1)
        if maybe_line.isdigit():
            file = maybe_file
            line = int(maybe_line)
    return Verdict(
        approved=False,
        summary=(
            f"Group {group_idx} cannot continue: deterministic {route.route} "
            f"blocker repeated after focused repair attempt retry-{retry - 1}."
        ),
        concerns=[
            Issue(
                severity="blocker",
                description=(
                    "The same deterministic commit/worktree blocker repeated "
                    "after a focused repair attempt; operator review is required "
                    "before another broad repair cycle."
                ),
                file=file,
                line=line,
            )
        ],
    )


def _bug_commit_failure_verdict(
    exc: WorkflowCommitError,
    *,
    bug_id: str,
    stage: str,
) -> Verdict:
    failed_repos = [
        outcome.repo_name or outcome.repo_path for outcome in exc.failed_outcomes
    ]
    repo_summary = ", ".join(failed_repos) if failed_repos else "unknown repo"
    issue = _commit_failure_issue(exc, stage=stage)
    issue.description = (
        f"pre-commit/husky failed while committing fix for {bug_id}; "
        f"fix repo hygiene before reverify. "
        f"{issue.description}"
    )
    return Verdict(
        approved=False,
        summary=(
            f"Commit failed while applying {bug_id} during {stage} "
            f"for {repo_summary}."
        ),
        concerns=[issue],
    )


async def _record_bug_commit_failure(
    runner: WorkflowRunner,
    feature: Feature,
    source: str,
    bug_id: str,
    attempt_number: int,
    stage: str,
    exc: WorkflowCommitError,
    *,
    message: str = "",
) -> Verdict:
    artifact_key = (
        f"bug-commit-failure:{source}:{bug_id}:attempt-{attempt_number}:{stage}"
    )
    await _record_commit_failure_artifact(
        runner,
        feature,
        artifact_key,
        exc,
        metadata={
            "source": source,
            "bug_id": bug_id,
            "attempt_number": attempt_number,
            "stage": stage,
            "message": message,
        },
    )
    verdict = _bug_commit_failure_verdict(exc, bug_id=bug_id, stage=stage)
    await runner.artifacts.put(
        f"bug-reverify:{source}:{bug_id}",
        to_str(verdict),
        feature=feature,
    )
    if isinstance(verdict, Verdict):
        ledger = await _load_ledger(runner, feature)
        ledger = _update_ledger(ledger, verdict, f"commit:{source}", 0)
        await _save_ledger(runner, feature, ledger)
    await _log_feature_event(
        runner,
        feature.id,
        "bug_commit_failed",
        "implementation",
        content=f"{source}:{bug_id}:{stage}",
        metadata={
            "source": source,
            "bug_id": bug_id,
            "attempt_number": attempt_number,
            "stage": stage,
            "artifact_key": artifact_key,
            "failed_repo_count": len(exc.failed_outcomes),
            "successful_commit_hashes": exc.successful_hashes,
        },
    )
    return verdict


async def _log_feature_event(
    runner: WorkflowRunner,
    feature_id: str,
    event_type: str,
    phase: str,
    *,
    content: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    feature_store = getattr(runner, "feature_store", None)
    log_event = getattr(feature_store, "log_event", None)
    if not callable(log_event):
        return
    try:
        await log_event(
            feature_id,
            event_type,
            phase,
            content=content,
            metadata=metadata or {},
        )
    except Exception:
        logger.warning(
            "Feature event logging failed for feature=%s event=%s phase=%s",
            feature_id,
            event_type,
            phase,
            exc_info=True,
        )


def _runner_runtime_policy(runner: WorkflowRunner) -> RuntimePolicy:
    services = getattr(runner, "services", {}) or {}
    try:
        return normalize_runtime_policy(services.get("runtime_policy"))
    except ValueError:
        logger.warning(
            "Unsupported runtime_policy=%r; falling back to %s",
            services.get("runtime_policy"),
            DEFAULT_RUNTIME_POLICY,
        )
        return DEFAULT_RUNTIME_POLICY


def _dag_group_runtime_pair(
    group_idx: int,
    runtime_policy: RuntimePolicy,
) -> tuple[str, str]:
    """Return ``(implementation_runtime, review_runtime)`` for a DAG group."""
    if runtime_policy == PRIMARY_IMPL_SECONDARY_REVIEW_POLICY:
        return "primary", "secondary"
    return (
        ("primary", "secondary")
        if group_idx % 2 == 0
        else ("secondary", "primary")
    )


def _post_dag_runtime_pair(
    last_group_idx: int,
    runtime_policy: RuntimePolicy,
) -> tuple[str, str]:
    """Return ``(gate_runtime, fix_runtime)`` for post-DAG gates."""
    if runtime_policy == PRIMARY_IMPL_SECONDARY_REVIEW_POLICY:
        return "secondary", "primary"
    return (
        ("secondary", "primary")
        if last_group_idx % 2 == 0
        else ("primary", "secondary")
    )


def _diagnostic_runtime_for_policy(runtime_policy: RuntimePolicy) -> str | None:
    """Return the runtime for RCA/triage/regression analysis under a policy."""
    if runtime_policy == PRIMARY_IMPL_SECONDARY_REVIEW_POLICY:
        return "secondary"
    return None


# ── Inline triage role (lightweight, no tools) ───────────────────────────────

_triage_role = Role(
    name="bug-triager",
    prompt=(
        "You triage bug reports from code review verdicts. Group ALL "
        "issues by their likely root cause. Issues that probably stem from "
        "the same underlying problem (same file, same data flow, same "
        "missing check) go in the same group. Every issue must be assigned "
        "to a group — do not skip or defer any."
    ),
    tools=[],
    model=BUDGET_TIERS["opus"],
)


@dataclass(slots=True)
class PlannedBugGroup:
    group: BugGroup
    rca: RootCauseAnalysis
    issue_text: str
    rca_key: str


@dataclass(slots=True)
class PlannedBugDispatch:
    attempt_number: int
    triage: BugTriage
    groups: list[PlannedBugGroup]
    fixable_groups: list[PlannedBugGroup]
    contradiction_groups: list[PlannedBugGroup]
    schedule: list[list[str]]
    dispatch_key: str
    strategy_mode: str = "ordinary_retry"
    strategy_reason: str = ""
    required_checks: list[str] = field(default_factory=list)
    required_files: list[str] = field(default_factory=list)
    stable_blocker_summary: str = ""
    similar_cluster_hints: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DagTaskDriftRoute:
    task_id: str
    artifact_key: str
    route: str
    reason: str
    path_problems: list[dict[str, Any]] = field(default_factory=list)
    forbidden_workspace_paths: list[dict[str, Any]] = field(default_factory=list)
    candidate_evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class DagArtifactClosureScan:
    stale_signatures: list[str] = field(default_factory=list)
    signature_records: list[dict[str, Any]] = field(default_factory=list)
    affected_task_ids: list[str] = field(default_factory=list)
    affected_subfeatures: list[str] = field(default_factory=list)
    affected_slices: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    blocking_targets: list[dict[str, Any]] = field(default_factory=list)
    advisory_residuals: list[dict[str, Any]] = field(default_factory=list)
    ignored_matches: list[dict[str, Any]] = field(default_factory=list)
    scanned_paths: list[str] = field(default_factory=list)
    suggested_scan_roots: list[str] = field(default_factory=list)

    def target_refs(self) -> list[str]:
        return _dedupe_preserving_order([
            str(item.get("target_ref", ""))
            for item in self.blocking_targets
            if str(item.get("target_ref", "")).strip()
        ])

    def to_record(self) -> dict[str, Any]:
        return {
            "stale_signatures": self.stale_signatures,
            "signature_records": self.signature_records,
            "affected_task_ids": self.affected_task_ids,
            "affected_subfeatures": self.affected_subfeatures,
            "affected_slices": self.affected_slices,
            "source_refs": self.source_refs,
            "blocking_targets": self.blocking_targets,
            "advisory_residuals": self.advisory_residuals,
            "ignored_matches": self.ignored_matches,
            "scanned_paths": self.scanned_paths,
            "suggested_scan_roots": self.suggested_scan_roots,
        }


class DagContradictionResolution(BaseModel):
    """Autonomous adjudication of a DAG repair spec contradiction."""

    resolution: str
    resolution_kind: str = "decision_only"
    authoritative_sources: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    superseded_expectation: str = ""
    implementation_direction: str = ""
    requires_code_change: bool = False
    needs_human: bool = False
    confidence: str = "medium"  # high | medium | low
    rationale: str = ""


@dataclass(slots=True)
class DagContradictionResolutionValidation:
    resolution: DagContradictionResolution | None
    rejection_reasons: list[str] = field(default_factory=list)


# ── Worktree management ─────────────────────────────────────────────────────


def _discover_repo(file_path: str, workspace_root: Path) -> Path | None:
    """Find an EXISTING repo by walking the path for .git directories."""
    parts = Path(file_path).parts
    for depth in range(1, len(parts)):
        candidate = workspace_root / Path(*parts[:depth])
        if (candidate / ".git").exists():
            return Path(*parts[:depth])
    return None


def _normalize_workspace_repo_path(
    repo_path: str,
    workspace_root: Path,
    *,
    feature_root: Path | None = None,
) -> tuple[str, str | None]:
    """Return a safe workspace repo path and the rejected nested request, if any.

    Exact existing repos are valid, even when their workspace-relative path has
    multiple segments. Missing paths nested inside an existing repo are not valid
    repo boundaries; treating them as new repos creates embedded .git directories
    in the feature worktree.
    """
    normalized = str(Path((repo_path or "").strip().replace("\\", "/").strip("/")))
    if not normalized or normalized == ".":
        return "", None

    if (workspace_root / normalized / ".git").exists():
        return normalized, None

    parts = Path(normalized).parts
    search_roots = [workspace_root]
    if feature_root is not None:
        search_roots.append(feature_root)

    for root in search_roots:
        for depth in range(1, len(parts)):
            candidate_rel = Path(*parts[:depth])
            candidate = root / candidate_rel
            if (candidate / ".git").exists():
                return candidate_rel.as_posix(), normalized

    if feature_root is not None and (feature_root / normalized / ".git").exists():
        return normalized, None

    return normalized, None


def _repo_action_rank(action: str) -> int:
    return {"read_only": 0, "new": 1, "extend": 2}.get(action, 0)


def _remember_repo_needed(
    repos_needed: dict[str, str],
    repo_path: str,
    action: str,
    *,
    workspace_root: Path,
    feature_root: Path | None = None,
    task_id: str = "",
) -> str:
    safe_repo_path, nested_request = _normalize_workspace_repo_path(
        repo_path,
        workspace_root,
        feature_root=feature_root,
    )
    if not safe_repo_path:
        return ""
    if nested_request:
        action = "extend"
        logger.warning(
            "Task %s requested nested repo boundary %s inside existing repo %s; "
            "using the existing repo to avoid embedded .git creation",
            task_id or "<unknown>",
            nested_request,
            safe_repo_path,
        )
    elif action == "new" and (workspace_root / safe_repo_path / ".git").exists():
        action = "extend"

    existing = repos_needed.get(safe_repo_path)
    if existing is None or _repo_action_rank(action) > _repo_action_rank(existing):
        repos_needed[safe_repo_path] = action
    return safe_repo_path


def _infer_new_repo_from_tasks(
    tasks: list[ImplementationTask],
) -> dict[str, list[str]]:
    """For tasks whose file paths don't match existing repos, infer new repo
    boundaries from the longest common path prefix per subfeature.

    Returns ``{ws_rel_repo_path: [task_ids]}``.
    """
    sf_paths: dict[str, list[str]] = {}
    for task in tasks:
        sf = task.subfeature_id or "unknown"
        for fs in task.file_scope:
            sf_paths.setdefault(sf, []).append(fs.path)

    new_repos: dict[str, list[str]] = {}
    for sf, paths in sf_paths.items():
        if not paths:
            continue
        split = [p.split("/") for p in paths]
        common: list[str] = []
        for parts in zip(*split):
            if len(set(parts)) == 1:
                common.append(parts[0])
            else:
                break
        if common:
            repo_path = "/".join(common)
            task_ids = [t.id for t in tasks if t.subfeature_id == sf]
            new_repos[repo_path] = task_ids

    return new_repos


async def _ensure_task_worktrees(
    runner: WorkflowRunner,
    feature: Feature,
    tasks: list[ImplementationTask],
) -> None:
    """Ensure worktrees exist for all repos referenced by a group of tasks.

    - Existing repos: discovered by walking ``.git`` directories.
    - New repos: inferred from the longest common path prefix per subfeature,
      then scaffolded inside the feature sandbox.
    - Read-only repos: cloned into the feature sandbox so writes cannot
      escape through symlink resolution.
    - All repo copies mirror workspace-relative paths under
      ``.iriai/features/{slug}/repos/`` so DAG file paths resolve.
    """
    workspace_mgr = runner.services.get("workspace_manager")
    if not workspace_mgr:
        return

    workspace_root: Path = workspace_mgr._base
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    feature_root.mkdir(parents=True, exist_ok=True)

    repos_needed: dict[str, str] = {}  # ws_rel_path → action

    for task in tasks:
        # 1. Explicit repo_path from task planner
        if task.repo_path:
            action = "read_only"
            for fs in task.file_scope:
                if fs.action in ("create", "modify"):
                    action = "extend"
                    break
            safe_repo_path = _remember_repo_needed(
                repos_needed,
                task.repo_path,
                action,
                workspace_root=workspace_root,
                feature_root=feature_root,
                task_id=task.id,
            )
            if safe_repo_path and safe_repo_path != task.repo_path:
                task.repo_path = safe_repo_path
            continue

        # 2. Discover existing repos from file_scope
        for fs in task.file_scope:
            repo_path = _discover_repo(fs.path, workspace_root)
            if repo_path:
                action = "read_only" if fs.action == "read_only" else "extend"
                _remember_repo_needed(
                    repos_needed,
                    str(repo_path),
                    action,
                    workspace_root=workspace_root,
                    feature_root=feature_root,
                    task_id=task.id,
                )

    # 3. Infer new repos from common-prefix for unresolved writable paths
    unresolved = [
        t for t in tasks
        if not t.repo_path and any(
            _discover_repo(fs.path, workspace_root) is None
            and fs.action in ("create", "modify")
            for fs in t.file_scope
        )
    ]
    if unresolved:
        new_repos = _infer_new_repo_from_tasks(unresolved)
        for repo_path in new_repos:
            _remember_repo_needed(
                repos_needed,
                repo_path,
                "new",
                workspace_root=workspace_root,
                feature_root=feature_root,
                task_id=",".join(new_repos.get(repo_path, [])),
            )

    # 4. Create feature-local repo copies
    for ws_rel_path, action in repos_needed.items():
        worktree_dest = feature_root / ws_rel_path
        if _is_isolated_repo_copy(worktree_dest):
            continue
        if worktree_dest.exists():
            _remove_repo_path(worktree_dest)

        source_path = workspace_root / ws_rel_path

        if action == "new":
            safe_repo_path, nested_request = _normalize_workspace_repo_path(
                ws_rel_path,
                workspace_root,
                feature_root=feature_root,
            )
            if nested_request:
                raise RuntimeError(
                    "Refusing to scaffold nested workflow repo "
                    f"{nested_request!r} inside existing repo {safe_repo_path!r}"
                )
            logger.info("Scaffolding new feature-local repo at %s", worktree_dest)
            worktree_dest.parent.mkdir(parents=True, exist_ok=True)
            await _scaffold_repo(worktree_dest)
            continue

        if not (source_path / ".git").exists():
            safe_repo_path, nested_request = _normalize_workspace_repo_path(
                ws_rel_path,
                workspace_root,
                feature_root=feature_root,
            )
            if nested_request:
                raise RuntimeError(
                    "Refusing to scaffold missing workflow repo "
                    f"{nested_request!r} inside existing repo {safe_repo_path!r}"
                )
            logger.info("Scaffolding feature-local repo at %s", worktree_dest)
            worktree_dest.parent.mkdir(parents=True, exist_ok=True)
            await _scaffold_repo(worktree_dest)
            continue

        branch = None if action == "read_only" else f"feature/{feature.slug}"
        await _clone_repo(source_path, worktree_dest, branch=branch)
        logger.info("Cloned %s → %s (branch: %s)", ws_rel_path, worktree_dest, branch or "default")

    # Set the worktree root as a service so ALL agents in this phase
    # automatically get cwd=repos/ via TrackedWorkflowRunner.resolve().
    # Implementers/fixers can still override to a specific repo via
    # workspace_override in metadata for more precision.
    #
    # Filesystem isolation is enforced by ClaudeAgentOptions.sandbox
    # (OS-level Seatbelt/bubblewrap), not by soft instructions.
    runner.services["worktree_root"] = feature_root


def _is_isolated_repo_copy(path: Path) -> bool:
    """Return true when *path* is a standalone git clone, not a linked path."""
    return path.exists() and not path.is_symlink() and (path / ".git").is_dir()


def _remove_repo_path(path: Path) -> None:
    """Remove an existing feature repo path so it can be recreated safely."""
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if not path.exists():
        return
    for attempt in range(3):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 2:
                quarantine = path.with_name(f"{path.name}-stale-{uuid4().hex[:8]}")
                try:
                    path.rename(quarantine)
                except FileNotFoundError:
                    return
                except Exception:
                    logger.warning("Failed to quarantine stale repo path %s", path, exc_info=True)
                    break
                logger.warning("Quarantined stale repo path %s to %s after cleanup failures", path, quarantine)
                shutil.rmtree(quarantine, ignore_errors=True)
                return
            time.sleep(0.1 * (attempt + 1))
    shutil.rmtree(path, ignore_errors=True)


async def _clone_repo(source_path: Path, dest: Path, *, branch: str | None) -> None:
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


def _write_sandbox_settings(feature_root: Path) -> None:
    """Write .claude/settings.json to each repo worktree to sandbox writes.

    Claude Code follows the .git worktree link and can discover the main
    repo. The sandbox filesystem restrictions prevent writes outside the
    worktree directory.
    """
    import json as _json

    settings = {
        "permissions": {
            "allow": [
                "Read(**)",
                "Edit(**)",
                "Write(**)",
                "Glob(**)",
                "Grep(**)",
                "Bash(git *)",
                "Bash(python *)",
                "Bash(pip *)",
                "Bash(npm *)",
                "Bash(npx *)",
                "Bash(node *)",
                "Bash(ls *)",
                "Bash(mkdir *)",
                "Bash(cat *)",
                "Bash(cd *)",
            ],
            "deny": [],
        },
    }

    for worktree_dir in feature_root.rglob(".git"):
        repo_dir = worktree_dir.parent
        if repo_dir == feature_root:
            continue
        # Only handle worktree .git files (not real .git directories)
        if not worktree_dir.is_file():
            continue

        claude_dir = repo_dir / ".claude"
        claude_dir.mkdir(exist_ok=True)

        settings_path = claude_dir / "settings.json"
        if settings_path.exists():
            continue  # Don't overwrite existing settings

        settings_path.write_text(_json.dumps(settings, indent=2), encoding="utf-8")

        # Also write a CLAUDE.md with explicit workspace boundaries
        claude_md = repo_dir / "CLAUDE.md"
        if not claude_md.exists():
            claude_md.write_text(
                "# Workspace Boundaries\n\n"
                "You are working in a git worktree. "
                "ALL file operations must stay within this directory.\n\n"
                "- Do NOT write to any path outside this directory\n"
                "- Do NOT navigate to parent directories to find other repos\n"
                "- Do NOT use absolute paths\n"
                "- All file paths in your task are relative to THIS directory\n",
                encoding="utf-8",
            )

        logger.info("Sandbox settings written to %s", repo_dir)


async def _scaffold_repo(path: Path) -> None:
    """Initialize a new git repo with minimal files."""
    path.mkdir(parents=True, exist_ok=True)
    readme = path / "README.md"
    readme.write_text(f"# {path.name}\n", encoding="utf-8")

    gitignore = path / ".gitignore"
    gitignore.write_text(
        "__pycache__/\n*.pyc\nnode_modules/\n.env\ndist/\nbuild/\n",
        encoding="utf-8",
    )

    await _run_git(path, "init", "-b", "main")
    await _run_git(path, "add", "-A")
    await _run_git(path, "commit", "-m", "chore: scaffold")


async def _run_git(cwd: Path, *args: str) -> str:
    """Run a git command asynchronously."""
    proc = await _asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{stderr.decode().strip()}"
        )
    return stdout.decode().strip()


# ── Parallel actor helpers ──────────────────────────────────────────────────


def _make_parallel_actor(
    base: AgentActor,
    suffix: str,
    *,
    runtime: str | None = None,
    workspace_path: str | None = None,
) -> AgentActor:
    """Create a parallel-safe copy of an AgentActor with a unique name.

    When *runtime* is set (``"primary"`` or ``"secondary"``), the actor's
    role metadata is updated so ``TrackedWorkflowRunner.resolve()`` routes
    it to the correct runtime for adversarial multi-model execution.

    When *workspace_path* is set, it overrides the agent's ``cwd`` so
    it operates within a specific repo worktree (not the main workspace).
    """
    metadata = dict(base.role.metadata)
    if runtime:
        metadata["runtime"] = runtime
    if workspace_path:
        metadata["workspace_override"] = workspace_path
    role = base.role.model_copy(update={"metadata": metadata})
    return AgentActor(
        name=f"{base.name}-{suffix}",
        role=role,
        context_keys=base.context_keys,
        persistent=base.persistent,
    )


async def _load_test_plan_section(
    runner: WorkflowRunner, feature: Feature
) -> str:
    """Load per-subfeature test plans and return a ``## Test Plan`` section.

    Iterates ``decomposition.subfeatures[*].slug`` directly — NOT
    ``dag.tasks[*].subfeature_id`` — because the latter is populated by
    agents in varied formats (slug, SF-id, name) and would silently miss
    test plans written with the canonical slug.

    Returns ``""`` when no test plans exist (pre-test_planning features or
    missing decomposition). Callers splice the return value directly into
    the Ask prompt; the function handles the surrounding heading and newlines
    so an empty return produces no dangling section.

    Large test-plan bodies (e.g. 14-SF feature with detailed plans) are
    handled by the TrackedWorkflowRunner's whole-prompt offload at
    ``workflows/_runner.py::_build_options`` — no per-section offload here,
    since this function runs before ``_implement_dag`` clones repos and
    ``_get_feature_root`` would return None.
    """
    decomp_raw = await runner.artifacts.get("decomposition", feature=feature)
    if not decomp_raw:
        return ""
    try:
        decomposition = SubfeatureDecomposition.model_validate_json(decomp_raw)
    except Exception:
        try:
            decomposition = SubfeatureDecomposition.model_validate(json.loads(decomp_raw))
        except Exception:
            logger.warning("Could not parse decomposition for test plan context")
            return ""

    parts: list[str] = []
    for sf in decomposition.subfeatures:
        slug = (sf.slug or "").strip()
        if not slug:
            continue
        tp = await runner.artifacts.get(f"test-plan:{slug}", feature=feature)
        if tp:
            # Per-SF heading is ### so it nests under the ## Test Plan wrapper.
            # Fall back to slug if sf.name is empty to avoid " (slug)" with
            # double-space.
            heading = sf.name.strip() or slug
            parts.append(f"### {heading} ({slug})\n\n{tp}")
        else:
            logger.debug(
                "No test-plan artifact for subfeature %s (legacy or skipped)",
                slug,
            )
    if not parts:
        return ""
    body = "\n\n---\n\n".join(parts)
    return f"\n\n## Test Plan\n\n{body}"


class ImplementationPhase(Phase):
    name = "implementation"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        dag_json = await runner.artifacts.get("dag", feature=feature)
        dag = ImplementationDAG.model_validate_json(dag_json)

        # Loaded once per execute() call and spliced into 4 of 6 post-DAG gates
        # (test author, QA, integration tester, verifier) AND into the
        # post-fix integration regression re-run in _run_regression. Code
        # review and security audit do NOT receive the test plan — they
        # assess code quality / security posture, not behavior-level
        # acceptance. Returns either a leading "\n\n## Test Plan\n\n..."
        # section or empty string — splice directly.
        test_plan_section = await _load_test_plan_section(runner, feature)

        prior_attempts = _load_prior_attempts(
            await runner.artifacts.get("bug-fix-attempts", feature=feature)
        )
        if prior_attempts:
            logger.info(
                "Restored %d prior fix attempts from artifact store",
                len(prior_attempts),
            )
        bug_counter = itertools.count(
            max((a.attempt_number for a in prior_attempts), default=0) + 1
        )
        cycle = 0

        while True:
            if cycle >= WARN_AFTER_CYCLES:
                logger.warning(
                    "Implementation cycle %d (exceeded %d without approval)",
                    cycle + 1,
                    WARN_AFTER_CYCLES,
                )

            # ── Step 1: Implementation ───────────────────────────────────
            impl_text, dag_failure, handover = await _implement_dag(runner, feature, dag)

            await runner.artifacts.put("implementation", impl_text, feature=feature)
            await runner.artifacts.put("handover", to_str(handover), feature=feature)
            await enqueue_public_exhibit_refresh(
                runner,
                feature,
                reason="implementation-handover-refresh",
                job_types=(
                    "public-summary",
                    "public-current-implementation",
                    "public-artifact-gallery",
                ),
            )
            state.implementation = impl_text
            state.handover = to_str(handover)

            # If the DAG stopped early on a verify failure, go through RCA
            if dag_failure:
                runtime_policy = _runner_runtime_policy(runner)
                diagnostic_runtime = _dag_repair_runtime_for(
                    "dag-rca",
                    _diagnostic_runtime_for_policy(runtime_policy),
                )
                diagnostic_reviewer = (
                    _make_parallel_actor(
                        qa_engineer,
                        "dag-failure-recheck",
                        runtime=_dag_repair_runtime_for(
                            "dag-focused-reverify",
                            diagnostic_runtime,
                        ),
                    )
                    if diagnostic_runtime
                    else qa_engineer
                )
                diagnostic_fixer = _make_parallel_actor(
                    implementer,
                    "dag-failure-fix",
                    runtime=_dag_repair_runtime_for("dag-fix", "primary"),
                )
                attempts = await _diagnose_and_fix(
                    runner, feature, dag_failure, "verify",
                    diagnostic_reviewer, diagnostic_fixer, prior_attempts, bug_counter,
                    test_plan_section=test_plan_section,
                    rca_runtime=diagnostic_runtime,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                cycle += 1
                continue

            # Compress handover before passing to review/QA gates
            handover.compress()
            handover_context = to_markdown(handover)

            # Append enhancement backlog so all gates know what's deferred
            backlog_raw = await runner.artifacts.get(
                "enhancement-backlog", feature=feature,
            )
            if backlog_raw:
                try:
                    backlog = EnhancementBacklog.model_validate_json(backlog_raw)
                    if backlog.items:
                        deferred = "\n".join(
                            f"- [{it.severity}] {it.description}"
                            for it in backlog.items
                        )
                        handover_context += (
                            f"\n\n## Already-Deferred Issues (DO NOT re-report these)\n"
                            f"The following {len(backlog.items)} minor/nit issues are "
                            f"already tracked in the enhancement backlog. Do NOT include "
                            f"them in your verdict — they are intentionally deferred.\n\n"
                            f"{deferred}\n"
                        )
                except Exception:
                    pass

            contradiction_decisions = await _format_contradiction_decisions_context(
                runner, feature,
            )
            if contradiction_decisions:
                handover_context += "\n\n" + contradiction_decisions

            post_dag_context = await _build_prompt_context_package(
                runner,
                feature,
                title="Post-DAG Gates",
                file_stem="post-dag-gates",
                intro_lines=[
                    "Use the implementation handover and test plan files as the source of truth for post-DAG review and verification.",
                    "Cross-check implementation outputs against the referenced planning artifacts and evidence files.",
                ],
                sections=[
                    ("handover", "Implementation Handover", handover_context),
                    ("test-plan", "Test Plan", test_plan_section),
                ],
            )

            # ── Adversarial runtime routing for post-DAG gates ──────────
            # The default policy preserves the existing parity-based
            # adversarial routing.  Other policies can pin implementation
            # and review work to fixed primary/secondary runtimes.
            last_group_idx = len(dag.execution_order) - 1
            runtime_policy = _runner_runtime_policy(runner)
            gate_runtime, fix_runtime = _post_dag_runtime_pair(
                last_group_idx,
                runtime_policy,
            )
            diagnostic_runtime = _diagnostic_runtime_for_policy(runtime_policy)
            logger.info(
                "Post-DAG gates: gate_runtime=%s, fix_runtime=%s, rca_runtime=%s "
                "(last_group=%d, runtime_policy=%s)",
                gate_runtime, fix_runtime, diagnostic_runtime,
                last_group_idx, runtime_policy,
            )

            # ── Step 2: Code Review (static) ─────────────────────────────
            if await runner.artifacts.get("dag-gate:code-review", feature=feature):
                logger.info("Code review gate already passed — skipping")
                review_verdict = Verdict(approved=True, summary="Previously approved")
            else:
                review_verdict = await runner.run(
                    Ask(
                        actor=_make_parallel_actor(
                            reviewer, "gate", runtime=gate_runtime,
                        ),
                        prompt=(
                            _context_package_prompt(post_dag_context)
                            +
                            "Review the implementation for code quality, adherence to "
                            "the technical plan, design decisions, and system design. "
                            "Cross-check against the full upstream artifacts in your context."
                        ),
                        output_type=Verdict,
                    ),
                    feature,
                    phase_name=self.name,
                )
                await runner.artifacts.put(
                    "review-verdict", to_str(review_verdict), feature=feature
                )

            # Ledger dedup + severity partition
            if isinstance(review_verdict, Verdict):
                ledger = await _load_ledger(runner, feature)
                review_verdict, _suppressed = _dedup_findings(review_verdict, ledger, "code_reviewer")
                if _suppressed:
                    logger.info("Suppressed %d duplicate findings from code_reviewer", len(_suppressed))
                review_verdict, _enhancements = _partition_verdict(review_verdict, "code_reviewer", "post-dag-gate")
                await _append_enhancements(runner, feature, _enhancements)
                ledger = _update_ledger(ledger, review_verdict, "code_reviewer", cycle)
                await _save_ledger(runner, feature, ledger)

            if _is_approved(review_verdict):
                await runner.artifacts.put(
                    "dag-gate:code-review", "approved", feature=feature
                )

            if not _is_approved(review_verdict):
                attempts = await _diagnose_and_fix(
                    runner, feature, review_verdict, "code_reviewer",
                    _make_parallel_actor(reviewer, "recheck", runtime=gate_runtime),
                    _make_parallel_actor(implementer, "cr-fix", runtime=fix_runtime),
                    prior_attempts, bug_counter,
                    handover_context=handover_context,
                    rca_runtime=diagnostic_runtime,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                cycle += 1
                continue

            # ── Step 3: Security Audit (static) ──────────────────────────
            if await runner.artifacts.get("dag-gate:security", feature=feature):
                logger.info("Security gate already passed — skipping")
                security_verdict = Verdict(approved=True, summary="Previously approved")
            else:
                security_verdict = await runner.run(
                    Ask(
                        actor=_make_parallel_actor(
                            security_auditor, "gate", runtime=gate_runtime,
                        ),
                        prompt=(
                            _context_package_prompt(post_dag_context)
                            +
                            "Audit the implementation for security vulnerabilities. "
                            "Check OWASP Top 10, auth on every endpoint, secrets in "
                            "code, input validation, and data exposure. Cross-check "
                            "against the security profile in the PRD."
                        ),
                        output_type=Verdict,
                    ),
                    feature,
                    phase_name=self.name,
                )
                await runner.artifacts.put(
                    "security-verdict", to_str(security_verdict), feature=feature
                )

            if isinstance(security_verdict, Verdict):
                ledger = await _load_ledger(runner, feature)
                security_verdict, _suppressed = _dedup_findings(security_verdict, ledger, "security_auditor")
                if _suppressed:
                    logger.info("Suppressed %d duplicate findings from security_auditor", len(_suppressed))
                security_verdict, _enhancements = _partition_verdict(security_verdict, "security_auditor", "post-dag-gate")
                await _append_enhancements(runner, feature, _enhancements)
                ledger = _update_ledger(ledger, security_verdict, "security_auditor", cycle)
                await _save_ledger(runner, feature, ledger)

            if _is_approved(security_verdict):
                await runner.artifacts.put(
                    "dag-gate:security", "approved", feature=feature
                )

            if not _is_approved(security_verdict):
                attempts = await _diagnose_and_fix(
                    runner, feature, security_verdict, "security_auditor",
                    _make_parallel_actor(security_auditor, "recheck", runtime=gate_runtime),
                    _make_parallel_actor(implementer, "sec-fix", runtime=fix_runtime),
                    prior_attempts, bug_counter,
                    handover_context=handover_context,
                    rca_runtime=diagnostic_runtime,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                cycle += 1
                continue

            # ── Step 4: Test Authoring ────────────────────────────────────
            test_checkpoint = await runner.artifacts.get(
                "dag-gate:test-authoring", feature=feature,
            )
            if test_checkpoint:
                logger.info("Test authoring gate already passed — skipping")
                test_result = ImplementationResult.model_validate_json(test_checkpoint)
            else:
                test_result = await runner.run(
                    Ask(
                        actor=_make_parallel_actor(
                            test_author, "gate", runtime=gate_runtime,
                        ),
                        prompt=(
                            _context_package_prompt(post_dag_context)
                            +
                            "Write tests for this implementation. When a Test Plan section is "
                            "provided above, it is the source of truth for acceptance criteria "
                            "and verification methods — write at least one test per AC-id, "
                            "honoring the stated verification_method (unit / integration / e2e / "
                            "visual). For each counterexample in the plan, write a test that "
                            "verifies the wrong thing does NOT happen. Use the project's existing "
                            "test framework and patterns.\n\n"
                            "For web/full-stack projects, write Playwright E2E tests that "
                            "test user journeys via real UI interactions."
                        ),
                        output_type=ImplementationResult,
                    ),
                    feature,
                    phase_name=self.name,
                )
                await runner.artifacts.put("test-authoring", to_str(test_result), feature=feature)
                await runner.artifacts.put(
                    "dag-gate:test-authoring",
                    test_result.model_dump_json(),
                    feature=feature,
                )
                await _commit_repos(
                    runner,
                    feature,
                    "test: add tests",
                    failure_key="dag-commit-failure:test-authoring:commit",
                    failure_metadata={"stage": "test-authoring"},
                )

            # ── Step 5: Full QA (dynamic) ─────────────────────────────────
            if await runner.artifacts.get("dag-gate:qa", feature=feature):
                logger.info("QA gate already passed — skipping")
                qa_verdict = Verdict(approved=True, summary="Previously approved")
            else:
                qa_verdict = await runner.run(
                    Ask(
                        actor=_make_parallel_actor(
                            qa_engineer, "gate", runtime=gate_runtime,
                        ),
                        prompt=(
                            _context_package_prompt(post_dag_context)
                            +
                            "Test the full implementation. Run the test suite, check "
                            "for runtime errors, and verify the acceptance criteria "
                            "from the PRD and design specs are met. When a Test Plan "
                            "section is provided above, march its verification_checklist "
                            "top-to-bottom and cite AC-ids in any failures you report. "
                            "Cross-check implementation against the full upstream "
                            "artifacts in your context."
                        ),
                        output_type=Verdict,
                    ),
                    feature,
                    phase_name=self.name,
                )
                await runner.artifacts.put("qa-verdict", to_str(qa_verdict), feature=feature)

            if isinstance(qa_verdict, Verdict):
                ledger = await _load_ledger(runner, feature)
                qa_verdict, _suppressed = _dedup_findings(qa_verdict, ledger, "qa_engineer")
                if _suppressed:
                    logger.info("Suppressed %d duplicate findings from qa_engineer", len(_suppressed))
                qa_verdict, _enhancements = _partition_verdict(qa_verdict, "qa_engineer", "post-dag-gate")
                await _append_enhancements(runner, feature, _enhancements)
                ledger = _update_ledger(ledger, qa_verdict, "qa_engineer", cycle)
                await _save_ledger(runner, feature, ledger)

            if _is_approved(qa_verdict):
                await runner.artifacts.put("dag-gate:qa", "approved", feature=feature)

            if not _is_approved(qa_verdict):
                attempts = await _diagnose_and_fix(
                    runner, feature, qa_verdict, "qa_engineer",
                    _make_parallel_actor(qa_engineer, "recheck", runtime=gate_runtime),
                    _make_parallel_actor(implementer, "qa-fix", runtime=fix_runtime),
                    prior_attempts, bug_counter,
                    handover_context=handover_context,
                    test_plan_section=test_plan_section,
                    rca_runtime=diagnostic_runtime,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                cycle += 1
                continue

            # ── Step 6: Integration Test (dynamic) ────────────────────────
            if await runner.artifacts.get("dag-gate:integration", feature=feature):
                logger.info("Integration gate already passed — skipping")
                integration_verdict = Verdict(approved=True, summary="Previously approved")
            else:
                integration_verdict = await runner.run(
                    Ask(
                        actor=_make_parallel_actor(
                            integration_tester, "gate", runtime=gate_runtime,
                        ),
                        prompt=(
                            _context_package_prompt(post_dag_context)
                            +
                            "Execute ALL user journeys from the PRD against the "
                            "implementation. Use Playwright for UI journeys, Bash "
                            "for API/CLI journeys. Every journey step must produce "
                            "evidence. Check happy paths, error cases, and boundary "
                            "conditions. When a Test Plan section is provided above, "
                            "run through its test_scenarios and edge_cases lists; for "
                            "any failure, cite the AC-id in your verdict."
                        ),
                        output_type=Verdict,
                    ),
                    feature,
                    phase_name=self.name,
                )
                await runner.artifacts.put(
                    "integration-verdict", to_str(integration_verdict), feature=feature
                )

            if isinstance(integration_verdict, Verdict):
                ledger = await _load_ledger(runner, feature)
                integration_verdict, _suppressed = _dedup_findings(integration_verdict, ledger, "integration_tester")
                if _suppressed:
                    logger.info("Suppressed %d duplicate findings from integration_tester", len(_suppressed))
                integration_verdict, _enhancements = _partition_verdict(integration_verdict, "integration_tester", "post-dag-gate")
                await _append_enhancements(runner, feature, _enhancements)
                ledger = _update_ledger(ledger, integration_verdict, "integration_tester", cycle)
                await _save_ledger(runner, feature, ledger)

            if _is_approved(integration_verdict):
                await runner.artifacts.put(
                    "dag-gate:integration", "approved", feature=feature
                )

            if not _is_approved(integration_verdict):
                attempts = await _diagnose_and_fix(
                    runner, feature, integration_verdict, "integration_tester",
                    _make_parallel_actor(integration_tester, "recheck", runtime=gate_runtime),
                    _make_parallel_actor(implementer, "int-fix", runtime=fix_runtime),
                    prior_attempts, bug_counter,
                    handover_context=handover_context,
                    test_plan_section=test_plan_section,
                    rca_runtime=diagnostic_runtime,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                cycle += 1
                continue

            # ── Step 7: Verifier — confirm all journeys work ─────────────
            if await runner.artifacts.get("dag-gate:verifier", feature=feature):
                logger.info("Verifier gate already passed — skipping")
                verifier_verdict = Verdict(approved=True, summary="Previously approved")
            else:
                verifier_verdict = await runner.run(
                    Ask(
                        actor=_make_parallel_actor(
                            verifier, "gate", runtime=gate_runtime,
                        ),
                        prompt=(
                            _context_package_prompt(post_dag_context)
                            +
                            "Verify that ALL user journeys from the PRD work end-to-end. "
                            "When a Test Plan section is provided above, its "
                            "verification_checklist and acceptance_criteria are the "
                            "authoritative source of truth — cite AC-ids for any failures.\n\n"
                            "**For projects with a frontend/UI:**\n"
                            "- Interact with the UI via real Playwright clicks and form fills "
                            "— do not substitute API calls.\n"
                            "- You MUST capture Playwright screenshots for every journey step. "
                            "Save screenshots to a `screenshots/` directory in the project root "
                            "using descriptive names: `{journey_id}_{step}.png` "
                            "(e.g., `J1_create_workflow.png`, `J2_add_node.png`).\n"
                            "- Use `page.screenshot(path='screenshots/...')` after each step.\n"
                            "- A UI journey without screenshot evidence is NOT verified.\n\n"
                            "**For pure backend/library projects:**\n"
                            "- Run the test suite and verify all tests pass.\n"
                            "- Execute API endpoints or CLI commands and verify responses.\n"
                            "- Capture terminal output as evidence where appropriate.\n\n"
                            "Every journey must produce evidence of working correctly."
                        ),
                        output_type=Verdict,
                    ),
                    feature,
                    phase_name=self.name,
                )
                await runner.artifacts.put(
                    "verifier-verdict", to_str(verifier_verdict), feature=feature
                )

            if isinstance(verifier_verdict, Verdict):
                ledger = await _load_ledger(runner, feature)
                verifier_verdict, _suppressed = _dedup_findings(verifier_verdict, ledger, "verifier")
                if _suppressed:
                    logger.info("Suppressed %d duplicate findings from verifier", len(_suppressed))
                verifier_verdict, _enhancements = _partition_verdict(verifier_verdict, "verifier", "post-dag-gate")
                await _append_enhancements(runner, feature, _enhancements)
                ledger = _update_ledger(ledger, verifier_verdict, "verifier", cycle)
                await _save_ledger(runner, feature, ledger)

            if _is_approved(verifier_verdict):
                await runner.artifacts.put(
                    "dag-gate:verifier", "approved", feature=feature
                )

            if not _is_approved(verifier_verdict):
                attempts = await _diagnose_and_fix(
                    runner, feature, verifier_verdict, "verifier",
                    _make_parallel_actor(verifier, "recheck", runtime=gate_runtime),
                    _make_parallel_actor(implementer, "vfy-fix", runtime=fix_runtime),
                    prior_attempts, bug_counter,
                    handover_context=handover_context,
                    test_plan_section=test_plan_section,
                    rca_runtime=diagnostic_runtime,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                cycle += 1
                continue

            # ── Push clones back to source repos ───────────────────────
            await _push_clones_to_source(runner, feature)

            # ── Step 8: Implementation Report ────────────────────────────
            from ....services.implementation_report import (
                render_implementation_report,
                validate_report,
            )

            # Collect artifact URLs from hosting service
            artifact_urls = _collect_artifact_urls(runner)

            # Collect any Playwright screenshots from the workspace
            screenshot_paths = _collect_screenshots(feature, runner)

            all_verdicts = {
                "qa": qa_verdict,
                "integration": integration_verdict,
                "code_review": review_verdict,
                "security": security_verdict,
                "verifier": verifier_verdict,
            }

            report_html = render_implementation_report(
                feature_name=feature.name,
                handover=handover,
                verdicts=all_verdicts,
                bug_fix_attempts=prior_attempts,
                test_result=test_result,
                artifact_urls=artifact_urls,
                screenshot_paths=screenshot_paths,
            )

            # Validate the report
            validation_errors = validate_report(report_html, handover, all_verdicts)
            if validation_errors:
                logger.warning(
                    "Report validation: %d issues: %s",
                    len(validation_errors),
                    "; ".join(validation_errors[:5]),
                )

            # Host the report
            report_url = ""
            hosting = runner.services.get("hosting")
            if hosting:
                report_url = await hosting.push_qa(
                    feature.id, "implementation-report",
                    report_html, "Implementation Report",
                )
                logger.info("Implementation report hosted at %s", report_url)

            # Store as artifact
            await runner.artifacts.put(
                "implementation-report", report_html, feature=feature
            )

            # Host enhancement backlog as separate artifact
            backlog_url = ""
            backlog_json = await runner.artifacts.get(
                "enhancement-backlog", feature=feature,
            )
            if backlog_json:
                try:
                    backlog = EnhancementBacklog.model_validate_json(backlog_json)
                except Exception:
                    backlog = EnhancementBacklog()
                if backlog.items:
                    backlog_html = _render_enhancement_backlog_html(
                        backlog, feature.name,
                    )
                    if hosting:
                        backlog_url = await hosting.push_qa(
                            feature.id, "enhancement-backlog",
                            backlog_html, "Enhancement Backlog",
                        )
                    await runner.artifacts.put(
                        "enhancement-backlog-report", backlog_html,
                        feature=feature,
                    )

            # Notify user via Slack with report link
            notification = "All quality gates passed. Implementation complete."
            if report_url:
                notification = (
                    f"All quality gates passed. Implementation complete.\n\n"
                    f"**[View Implementation Report]({report_url})**\n\n"
                    f"The report contains journey evidence, gate verdicts, "
                    f"bug fix history, and artifact references."
                )
            if backlog_url:
                notification += (
                    f"\n\n**[View Enhancement Backlog]({backlog_url})** "
                    f"({len(backlog.items)} items deferred)"
                )
            await runner.run(
                Notify(message=notification),
                feature,
                phase_name=self.name,
            )

            return state


async def _push_clones_to_source(
    runner: WorkflowRunner, feature: Feature,
) -> None:
    """Push commits from all cloned repos back to their source repos.

    Each clone has ``origin`` pointing to the source repo on disk.
    We push the feature branch so the source repo has all the changes.
    """
    workspace_mgr = runner.services.get("workspace_manager")
    if not workspace_mgr:
        return

    feature_root = _get_feature_root(runner, feature)
    if not feature_root:
        return

    await _push_clones_to_source_root(feature_root)


async def _push_clones_to_source_root(repos_root: Path) -> None:
    """Push commits from all repo clones rooted under *repos_root*."""
    if not repos_root.exists():
        return

    for git_dir in repos_root.rglob(".git"):
        if not git_dir.is_dir():
            continue  # Skip worktree .git files (shouldn't exist with clones)
        repo_dir = git_dir.parent
        if repo_dir == repos_root:
            continue

        try:
            branch = await _run_git(repo_dir, "branch", "--show-current")
            if not branch:
                continue
            # Check if there are commits to push
            status = await _run_git(repo_dir, "status", "--porcelain")
            if status:
                # Uncommitted changes — commit them first
                await _run_git(repo_dir, "add", "-A")
                await _run_git(repo_dir, "commit", "-m", "feat: final uncommitted changes")

            await _run_git(repo_dir, "push", "origin", branch)
            rel = repo_dir.relative_to(repos_root)
            logger.info("Pushed %s (branch: %s) to source", rel, branch)
        except Exception as e:
            rel = repo_dir.relative_to(repos_root)
            logger.warning("Failed to push %s: %s", rel, e)


# ── DAG execution ────────────────────────────────────────────────────────────


async def _record_dag_path_canonicalization(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    rewrites: list[Any],
    *,
    context_label: str,
) -> None:
    if not rewrites:
        return
    records = dag_path_rewrites_to_records(rewrites)
    counts = collections.Counter(record["rule"] for record in records)
    await runner.artifacts.put(
        f"dag-path-canonicalization:g{group_idx}",
        json.dumps({
            "group_idx": group_idx,
            "context_label": context_label,
            "rewrite_count": len(records),
            "counts": dict(sorted(counts.items())),
            "paths": records,
        }, indent=2),
        feature=feature,
    )


def _build_task_prompt(
    task: ImplementationTask,
    *,
    repo_prefix: str = "",
    context_dir: Path | None = None,
) -> str:
    """Construct a rich prompt from an ImplementationTask's structured fields.

    When *repo_prefix* is set, file_scope paths are stripped of the prefix
    so they're relative to the repo root (matching the agent's cwd).

    When *context_dir* is set, reference material is written to a file
    inside that directory and the prompt includes a Read pointer instead of
    inlining the full content.
    """
    parts: list[str] = [
        f"# {task.name}\n\n"
        f"**Task ID:** `{task.id}` — use this exact value for `task_id` in your output.\n\n"
        f"{task.description}"
    ]

    # ── Workspace directive ──────────────────────────────────────────
    if repo_prefix:
        parts.append(
            "## Working Directory\n"
            "All file paths below are relative to your current working directory.\n"
            "Do NOT use absolute paths. Do NOT navigate outside your working directory.\n"
            "Your cwd is the root of the repository you're working in."
        )

    # ── File Scope ────────────────────────────────────────────────────
    if task.file_scope:
        lines = []
        for fs in task.file_scope:
            path = fs.path
            if repo_prefix and path.startswith(repo_prefix):
                path = path[len(repo_prefix):]
                if path.startswith("/"):
                    path = path[1:]
            lines.append(f"- [{fs.action.upper()}] `{path}`")
        parts.append("## File Scope\n" + "\n".join(lines))
    elif task.files:
        lines = []
        for f in task.files:
            path = f
            if repo_prefix and path.startswith(repo_prefix):
                path = path[len(repo_prefix):]
                if path.startswith("/"):
                    path = path[1:]
            lines.append(f"- `{path}`")
        parts.append("## File Scope\n" + "\n".join(lines))

    # ── Acceptance Criteria ───────────────────────────────────────────
    if task.acceptance_criteria:
        ac_lines: list[str] = []
        for ac in task.acceptance_criteria:
            ac_lines.append(f"- {ac.description}")
            if ac.not_criteria:
                ac_lines.append(f"  - **NOT:** {ac.not_criteria}")
        parts.append("## Acceptance Criteria\n" + "\n".join(ac_lines))

    # ── Counterexamples ──────────────────────────────────────────────
    if task.counterexamples:
        parts.append(
            "## Counterexamples (Do NOT)\n"
            + "\n".join(f"- {ce}" for ce in task.counterexamples)
        )

    # ── Security Concerns ────────────────────────────────────────────
    if task.security_concerns:
        parts.append(
            "## Security Concerns\n"
            + "\n".join(f"- {sc}" for sc in task.security_concerns)
        )

    # ── data-testid Assignments ──────────────────────────────────────
    if task.testid_assignments:
        parts.append(
            "## data-testid Assignments\n"
            + "\n".join(f"- `{tid}`" for tid in task.testid_assignments)
        )

    # ── Reference Material ──────────────────────────────────────────
    if task.reference_material:
        ref_lines = []
        for ref in task.reference_material:
            ref_lines.append(f"### {ref.source}\n{ref.content}")
        ref_content = "\n\n".join(ref_lines)

        if context_dir is not None:
            refs_path = context_dir / "refs.md"
            refs_path.write_text(
                f"# Reference Material — {task.name}\n\n{ref_content}",
                encoding="utf-8",
            )
            rel_path = f".iriai-context/{task.id}/refs.md"
            parts.append(
                f"## Reference Material\n"
                f"Reference material for this task is in `{rel_path}`.\n"
                f"**Read that file before starting implementation.**"
            )
        else:
            parts.append("## Reference Material\n\n" + ref_content)

    # ── Traceability ─────────────────────────────────────────────────
    trace_lines: list[str] = []
    if task.requirement_ids:
        trace_lines.append(f"Requirements: {', '.join(task.requirement_ids)}")
    if task.step_ids:
        trace_lines.append(f"Plan steps: {', '.join(task.step_ids)}")
    if task.journey_ids:
        trace_lines.append(f"Journeys: {', '.join(task.journey_ids)}")
    if trace_lines:
        parts.append("## Traceability\n" + "\n".join(trace_lines))

    return "\n\n".join(parts)


async def _verify_and_fix_group(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    group_tasks: list[ImplementationTask],
    results: list[object],
    all_results: list[object],
    handover: HandoverDoc,
    feature_root: Path | None,
    impl_runtime: str,
    review_runtime: str,
    rca_runtime: str | None = None,
    *,
    verify_fn: Any | None = None,
    fix_context: str = "",
    known_task_ids: set[str] | None = None,
    initial_verdict: object | None = None,
    initial_verdict_key: str | None = None,
) -> tuple[bool, str]:
    """Verify a group's implementation and fix issues via RCA → fix → re-verify.

    Returns ``(approved, failure_message)``.  When *approved* is True the
    group is checkpointed and recorded in the handover.  When False the
    caller decides how to handle the failure (e.g. halt the DAG).

    When *verify_fn* is provided it replaces the default ``_verify()`` call.
    It must accept ``(runner, feature, results, files, tasks, *, runtime)``.

    When *fix_context* is provided it is injected into the fix agent's prompt
    so it has additional context about what needs to be fixed (e.g. the
    original enhancement items for the enhancement group).
    """
    import json as _json

    _do_verify = verify_fn or _verify
    dag_review_runtime = (
        _dag_repair_runtime_for("dag-normal-verify", review_runtime)
        if verify_fn is None else review_runtime
    )
    dag_final_review_runtime = (
        _dag_repair_runtime_for("dag-final-verify", dag_review_runtime)
        if verify_fn is None else dag_review_runtime
    )
    dag_impl_runtime = (
        _dag_repair_runtime_for("dag-fix", impl_runtime)
        if verify_fn is None else impl_runtime
    )
    dag_rca_runtime = (
        _dag_repair_runtime_for("dag-rca", rca_runtime)
        if verify_fn is None else rca_runtime
    )

    # ── Initial verify ────────────────────────────────────────────────
    verify_results_context = list(results)
    if verify_fn is None:
        verify_results_context = await _sanitize_dag_repair_results(
            runner,
            feature,
            group_idx,
            -1,
            verify_results_context,
            feature_root,
            context_label="initial-preflight",
        )
        reconcile = await _reconcile_dag_task_results(
            runner,
            feature,
            group_idx,
            "initial",
            group_tasks,
            results=results,
            verify_results_context=verify_results_context,
            all_results=all_results,
            repair_results=[],
            feature_root=feature_root,
        )
        results = reconcile.results
        verify_results_context = reconcile.verify_results_context
        all_results[:] = reconcile.all_results
        spec_reconcile = await _reconcile_dag_task_specs(
            runner,
            feature,
            group_idx,
            "initial",
            group_tasks,
            feature_root=feature_root,
        )
        group_tasks[:] = spec_reconcile.tasks
    group_files = _collect_files(verify_results_context)
    verdict: object | None = initial_verdict
    if initial_verdict is None:
        logger.info(
            "DAG group verify starting group=%d attempt=initial runtime=%s rca_runtime=%s "
            "tasks=%s files=%d results=%d",
            group_idx,
            dag_review_runtime or "<default>",
            dag_rca_runtime or "<default>",
            [task.id for task in group_tasks],
            len(group_files),
            len(results),
        )
        await _log_feature_event(
            runner,
            feature.id,
            "dag_verify_start",
            "implementation",
            content=f"g{group_idx}:initial",
            metadata={
                "group_idx": group_idx,
                "retry": "initial",
                "runtime": dag_review_runtime,
                "task_ids": [task.id for task in group_tasks],
                "file_count": len(group_files),
                "result_count": len(results),
            },
        )
        if verify_fn is None:
            verdict = await _run_dag_group_preflight(
                runner,
                feature,
                group_idx,
                "initial",
                group_tasks,
                verify_results_context,
                feature_root=feature_root,
                known_task_ids=known_task_ids,
            )
        if verdict is None:
            verdict = await _do_verify(
                runner, feature, verify_results_context, group_files, group_tasks,
                runtime=dag_review_runtime,
            )
        logger.info(
            "DAG group verify finished group=%d attempt=initial approved=%s",
            group_idx,
            _is_approved(verdict),
        )
        await _log_feature_event(
            runner,
            feature.id,
            "dag_verify_finish",
            "implementation",
            content=f"g{group_idx}:initial",
            metadata={
                "group_idx": group_idx,
                "retry": "initial",
                "approved": _is_approved(verdict),
                "runtime": dag_review_runtime,
            },
        )
        await runner.artifacts.put(
            f"dag-verify:g{group_idx}:initial",
            to_str(verdict),
            feature=feature,
        )
        # Ledger dedup + severity partition
        if isinstance(verdict, Verdict):
            ledger = await _load_ledger(runner, feature)
            ledger_verdict, _suppressed = _dedup_findings(verdict, ledger, "verify")
            if _suppressed:
                logger.info("Suppressed %d duplicate findings from verify (group %d)", len(_suppressed), group_idx)
            ledger_verdict, _enhancements = _partition_verdict(
                ledger_verdict,
                "verify",
                f"group-{group_idx}",
            )
            await _append_enhancements(runner, feature, _enhancements)
            ledger = _update_ledger(ledger, ledger_verdict, "verify", 0)
            await _save_ledger(runner, feature, ledger)
    else:
        logger.info(
            "DAG group %d entering repair loop from host verdict key=%s approved=%s",
            group_idx,
            initial_verdict_key or "<unknown>",
            _is_approved(verdict),
        )

    # ── RCA → fix → re-verify loop ───────────────────────────────────
    for retry in range(VERIFY_RETRIES):
        if _is_approved(verdict):
            break

        await _log_feature_event(
            runner,
            feature.id,
            "dag_repair_cycle_start",
            "implementation",
            content=f"g{group_idx}:retry-{retry}",
            metadata={
                "group_idx": group_idx,
                "retry": retry,
                "runtime": dag_impl_runtime,
                "rca_runtime": dag_rca_runtime,
                "final_review_runtime": dag_final_review_runtime,
            },
        )

        direct_route = DagDirectRepairRoute(
            route=_NORMAL_VERIFY_ROUTE,
            reason="not_classified",
            signature="",
        )
        if verify_fn is None and isinstance(verdict, Verdict):
            source_verdict_key = (
                initial_verdict_key
                if retry == 0 and initial_verdict_key
                else f"dag-verify:g{group_idx}:initial"
                if retry == 0
                else f"dag-verify:g{group_idx}:retry-{retry - 1}"
            )
            direct_route = _classify_dag_direct_repair_route(verdict)
            if direct_route.route != _NORMAL_VERIFY_ROUTE:
                direct_route = await _normalize_direct_route_workspace_permissions(
                    runner,
                    feature,
                    group_idx,
                    retry,
                    feature_root,
                    direct_route,
                )
                if await _direct_route_repeated_signature(
                    runner,
                    feature,
                    group_idx,
                    retry,
                    direct_route,
                ):
                    verdict = _repeated_direct_route_verdict(
                        group_idx=group_idx,
                        retry=retry,
                        route=direct_route,
                    )
                    await _record_dag_direct_repair_route(
                        runner,
                        feature,
                        group_idx,
                        retry,
                        direct_route,
                        status="blocked_repeat",
                        source_verdict_key=source_verdict_key,
                        guardrail_decision=(
                            "same deterministic route signature repeated; "
                            "expanded verify and broad repair skipped"
                        ),
                    )
                    await runner.artifacts.put(
                        f"dag-verify:g{group_idx}:retry-{retry}",
                        to_str(verdict),
                        feature=feature,
                    )
                    break
                await _record_dag_direct_repair_route(
                    runner,
                    feature,
                    group_idx,
                    retry,
                    direct_route,
                    status=(
                        "operator_blocked"
                        if direct_route.operator_required
                        else "selected"
                    ),
                    source_verdict_key=source_verdict_key,
                    guardrail_decision=(
                        "operator/worktree blocker detected before repair dispatch"
                        if direct_route.operator_required
                        else "deterministic route selected before expanded verify"
                    ),
                )
                if direct_route.operator_required:
                    logger.warning(
                        "DAG group %d retry %d blocked by operator-required route %s",
                        group_idx,
                        retry,
                        direct_route.route,
                    )
                    await runner.artifacts.put(
                        f"dag-verify:g{group_idx}:retry-{retry}",
                        to_str(verdict),
                        feature=feature,
                    )
                    break

        feedback = _format_feedback("Verify", verdict)
        authority_gate = DagAuthorityGateOutcome()
        if (
            verify_fn is None
            and isinstance(verdict, Verdict)
            and direct_route.route == _NORMAL_VERIFY_ROUTE
        ):
            authority_gate = await _attempt_dag_authority_gate_repair(
                runner,
                feature,
                group_idx,
                retry,
                verdict,
                group_tasks,
                results=results,
                verify_results_context=verify_results_context,
                all_results=all_results,
                feature_root=feature_root,
                runtime=dag_impl_runtime,
                feedback=feedback,
                known_task_ids=known_task_ids,
            )
            if authority_gate.blocked_verdict is not None:
                verdict = authority_gate.blocked_verdict
                await runner.artifacts.put(
                    f"dag-verify:g{group_idx}:retry-{retry}",
                    to_str(verdict),
                    feature=feature,
                )
                break
            if not authority_gate.repair_results:
                verdict = await _run_expanded_dag_verify_lenses(
                    runner,
                    feature,
                    group_idx,
                    retry,
                    verdict,
                    verify_results_context,
                    group_files,
                    group_tasks,
                    runtime=dag_final_review_runtime,
                    feature_root=feature_root,
                )
                feedback = _format_feedback("Verify", verdict)

        workspace_hint = (
            f"\n\n### Workspace\nFeature repos at: `{feature_root}`\n"
            if feature_root else ""
        )
        prior_ctx = ""
        if retry > 0:
            prior_ctx = (
                f"\n\n## Prior Verify Attempt\n"
                f"This is retry {retry + 1}/{VERIFY_RETRIES}. "
                f"The previous fix attempt did not resolve the issue.\n"
            )

        # Extract specific issues from verdict
        verifier_issues_section = ""
        if isinstance(verdict, Verdict) and verdict.concerns:
            flagged_files = sorted({c.file for c in verdict.concerns if c.file})
            issue_lines = []
            for c in verdict.concerns:
                file_ref = f"`{c.file}`" if c.file else "(no file)"
                line_ref = f" line {c.line}" if c.line else ""
                issue_lines.append(f"- **[{c.severity}]** {file_ref}{line_ref}: {c.description}")
            verifier_issues_section = (
                "\n\n## Verifier's Specific Findings (START HERE)\n"
                "The verifier flagged these exact issues. Investigate THESE first:\n\n"
                + "\n".join(issue_lines)
                + "\n\n**Flagged files:** " + ", ".join(f"`{f}`" for f in flagged_files)
                + "\n\nYour `affected_files` output MUST include these files "
                "unless you demonstrate with evidence that the root cause is "
                "entirely in a different file — in which case, explain the chain "
                "from each flagged file to the actual root cause."
            )

        if (
            verify_fn is None
            and isinstance(verdict, Verdict)
            and direct_route.route == _NORMAL_VERIFY_ROUTE
        ):
            try:
                authority_repair = bool(authority_gate.repair_results)
                if authority_repair:
                    parallel_fix_results = list(authority_gate.repair_results)
                else:
                    parallel_fix_results = await _attempt_parallel_dag_repair(
                        runner,
                        feature,
                        group_idx,
                        retry,
                        verdict,
                        group_tasks,
                        feature_root=feature_root,
                        impl_runtime=dag_impl_runtime,
                        rca_runtime=dag_rca_runtime,
                        feedback=feedback,
                        fix_context=fix_context,
                    )
            except WorkflowCommitError as exc:
                await _record_dag_commit_failure(
                    runner,
                    feature,
                    group_idx,
                    f"retry-{retry}",
                    exc,
                    message=f"parallel DAG repair commit failed in retry {retry}",
                )
                verdict = _commit_failure_verdict(
                    exc,
                    group_idx=group_idx,
                    stage=f"retry-{retry}",
                )
                await runner.artifacts.put(
                    f"dag-verify:g{group_idx}:retry-{retry}",
                    to_str(verdict),
                    feature=feature,
                )
                continue
            if parallel_fix_results:
                all_results.extend(parallel_fix_results)
                parallel_fix_task_ids = {result.task_id for result in parallel_fix_results}
                verify_results_context = [
                    *verify_results_context,
                    *parallel_fix_results,
                ]
                verify_results_context = await _sanitize_dag_repair_results(
                    runner,
                    feature,
                    group_idx,
                    retry,
                    verify_results_context,
                    feature_root,
                    context_label=(
                        "authority-final-preflight"
                        if authority_repair else "parallel-final-preflight"
                    ),
                )
                sanitized_by_task_id = {
                    result.task_id: result
                    for result in verify_results_context
                    if result.task_id in parallel_fix_task_ids
                }
                parallel_fix_results = [
                    sanitized_by_task_id.get(result.task_id, result)
                    for result in parallel_fix_results
                ]
                permission_report = _normalize_feature_workspace_cleanup_permissions(
                    feature_root,
                    _implementation_result_permission_targets(parallel_fix_results),
                    reason=(
                        "authority-fix-output"
                        if authority_repair else "parallel-fix-output"
                    ),
                )
                await _record_workspace_permission_repair(
                    runner,
                    feature,
                    group_idx,
                    str(retry),
                    permission_report,
                    context=(
                        "authority-fix-output"
                        if authority_repair else "parallel-fix-output"
                    ),
                )
                reconcile = await _reconcile_dag_task_results(
                    runner,
                    feature,
                    group_idx,
                    str(retry),
                    group_tasks,
                    results=results,
                    verify_results_context=verify_results_context,
                    all_results=all_results,
                    repair_results=parallel_fix_results,
                    feature_root=feature_root,
                )
                results = reconcile.results
                verify_results_context = reconcile.verify_results_context
                all_results[:] = reconcile.all_results
                spec_reconcile = await _reconcile_dag_task_specs(
                    runner,
                    feature,
                    group_idx,
                    str(retry),
                    group_tasks,
                    feature_root=feature_root,
                )
                group_tasks[:] = spec_reconcile.tasks
                created_files = sorted({
                    path
                    for result in parallel_fix_results
                    for path in result.files_created
                })
                modified_files = sorted({
                    path
                    for result in parallel_fix_results
                    for path in result.files_modified
                })
                aggregate_fix = ImplementationResult(
                    task_id=(
                        f"g{group_idx}-authority-repair-{retry}"
                        if authority_repair else f"g{group_idx}-parallel-fix-{retry}"
                    ),
                    summary=(
                        (
                            "DAG authority gate repaired deterministic "
                            "workflow metadata; final aggregate verifier still "
                            "required."
                        )
                        if authority_repair else (
                            f"Parallel DAG repair applied {len(parallel_fix_results)} "
                            "root-cause-group fix(es); final aggregate verifier still required."
                        )
                    ),
                    status=(
                        "completed"
                        if all(result.status == "completed" for result in parallel_fix_results)
                        else "partial"
                    ),
                    files_created=created_files,
                    files_modified=modified_files,
                )
                await runner.artifacts.put(
                    f"dag-fix:g{group_idx}:retry-{retry}",
                    aggregate_fix.model_dump_json(),
                    feature=feature,
                )
                group_files = _collect_files(verify_results_context)
                logger.info(
                    "DAG group verify starting group=%d attempt=retry-%d runtime=%s rca_runtime=%s "
                    "tasks=%s files=%d results=%d parallel_repair=%s authority_repair=%s",
                    group_idx,
                    retry,
                    dag_final_review_runtime or "<default>",
                    dag_rca_runtime or "<default>",
                    [task.id for task in group_tasks],
                    len(group_files),
                    len(verify_results_context),
                    not authority_repair,
                    authority_repair,
                )
                await _log_feature_event(
                    runner,
                    feature.id,
                    "dag_verify_start",
                    "implementation",
                    content=f"g{group_idx}:retry-{retry}",
                    metadata={
                        "group_idx": group_idx,
                        "retry": retry,
                        "runtime": dag_final_review_runtime,
                        "task_ids": [task.id for task in group_tasks],
                        "file_count": len(group_files),
                        "result_count": len(verify_results_context),
                        "parallel_repair": not authority_repair,
                        "authority_repair": authority_repair,
                    },
                )
                preflight_verdict = await _run_dag_group_preflight(
                    runner,
                    feature,
                    group_idx,
                    str(retry),
                    group_tasks,
                    verify_results_context,
                    feature_root=feature_root,
                    known_task_ids=known_task_ids,
                )
                if preflight_verdict is not None:
                    verdict = preflight_verdict
                else:
                    verdict = await _do_verify(
                        runner,
                        feature,
                        verify_results_context,
                        group_files,
                        group_tasks,
                        runtime=dag_final_review_runtime,
                    )
                logger.info(
                    "DAG group verify finished group=%d attempt=retry-%d approved=%s",
                    group_idx,
                    retry,
                    _is_approved(verdict),
                )
                await _log_feature_event(
                    runner,
                    feature.id,
                    "dag_verify_finish",
                    "implementation",
                    content=f"g{group_idx}:retry-{retry}",
                    metadata={
                        "group_idx": group_idx,
                        "retry": retry,
                        "approved": _is_approved(verdict),
                        "runtime": dag_final_review_runtime,
                        "parallel_repair": not authority_repair,
                        "authority_repair": authority_repair,
                    },
                )
                await runner.artifacts.put(
                    f"dag-verify:g{group_idx}:retry-{retry}",
                    to_str(verdict),
                    feature=feature,
                )
                if isinstance(verdict, Verdict):
                    ledger = await _load_ledger(runner, feature)
                    ledger_verdict, _suppressed = _dedup_findings(
                        verdict,
                        ledger,
                        "verify",
                    )
                    if _suppressed:
                        logger.info(
                            "Suppressed %d duplicate findings from verify retry (group %d)",
                            len(_suppressed),
                            group_idx,
                        )
                    ledger_verdict, _enhancements = _partition_verdict(
                        ledger_verdict,
                        "verify",
                        f"group-{group_idx}-retry-{retry}",
                    )
                    await _append_enhancements(runner, feature, _enhancements)
                    ledger = _update_ledger(ledger, ledger_verdict, "verify", 0)
                    await _save_ledger(runner, feature, ledger)
                continue

        rca_result: RootCauseAnalysis | None = None
        if direct_route.route == _COMMIT_HYGIENE_ROUTE:
            rca_result = RootCauseAnalysis(
                hypothesis=(
                    "The group is blocked by a deterministic pre-commit/husky "
                    "failure, not by a broad verifier finding."
                ),
                evidence=[
                    direct_route.reason,
                    "The host skipped expanded verify because the current verdict "
                    "contains only commit-hook hygiene concerns.",
                    *direct_route.target_files,
                ],
                affected_files=[
                    target.rsplit(":", 1)[0]
                    if target.rsplit(":", 1)[-1].isdigit()
                    else target
                    for target in direct_route.target_files
                ],
                proposed_approach=(
                    "Apply the minimal source hygiene change required by the hook "
                    "output, then let the normal commit and verifier path run."
                ),
                confidence="high",
            )
        elif direct_route.route == _MANIFEST_FORBIDDEN_CLEANUP_ROUTE:
            rca_result = RootCauseAnalysis(
                hypothesis=(
                    "The commit hook failure is a symptom of manifest-forbidden "
                    "product drift: forbidden files are present in git status or "
                    "hook output and must be deleted or ported, not suppressed."
                ),
                evidence=[
                    direct_route.reason,
                    "The host skipped expanded verify because the current verdict "
                    "contains only deterministic manifest-forbidden cleanup concerns.",
                    *direct_route.target_files,
                ],
                affected_files=[
                    target.rsplit(":", 1)[0]
                    if target.rsplit(":", 1)[-1].isdigit()
                    else target
                    for target in direct_route.target_files
                ],
                proposed_approach=(
                    "Remove or port the manifest-forbidden files, preserve any "
                    "acceptance coverage in canonical locations, then let the "
                    "normal commit and verifier path run."
                ),
                confidence="high",
            )
        else:
            rca_context = await _build_prompt_context_package(
                runner,
                feature,
                title=f"DAG Verify RCA — Group {group_idx} Retry {retry + 1}",
                file_stem=f"g{group_idx}-rca-{retry}",
                intro_lines=[
                    "Investigate the root cause of the verifier's findings for this DAG group.",
                    "Use the verifier feedback, specific findings, and prior-attempt history from the referenced files.",
                ],
                sections=[
                    ("feedback", "Verifier Feedback", feedback),
                    ("issues", "Verifier Specific Findings", verifier_issues_section),
                    ("prior-attempt", "Prior Verify Attempt", prior_ctx),
                    ("workspace", "Workspace", workspace_hint),
                ],
            )
            rca_prompt = (
                f"## DAG Verify Failed (group {group_idx}, attempt {retry + 1})\n\n"
                f"{_context_package_prompt(rca_context)}"
                "Investigate the root cause of the specific issues listed above. "
                "Read each flagged file and check git history for oscillating changes. "
                "Check if the issue is a spec contradiction (task reference_material says X but a D-GR decision says Y)."
            )
            try:
                rca_result = await runner.run(
                    Ask(
                        actor=_make_parallel_actor(
                            root_cause_analyst, f"dag-rca-g{group_idx}-r{retry}",
                            runtime=dag_rca_runtime,
                            workspace_path=str(feature_root) if feature_root else None,
                        ),
                        prompt=rca_prompt,
                        output_type=RootCauseAnalysis,
                    ),
                    feature,
                    phase_name="implementation",
                )
            except Exception as rca_err:
                logger.warning("DAG verify RCA failed: %s", rca_err)

            if isinstance(rca_result, RootCauseAnalysis):
                await runner.artifacts.put(
                    f"dag-verify-rca:g{group_idx}:retry-{retry}",
                    rca_result.model_dump_json(),
                    feature=feature,
                )

        # If RCA found a contradiction, escalate and use resolution
        fix_direction = ""
        if isinstance(rca_result, RootCauseAnalysis) and rca_result.confidence == "contradiction":
            logger.warning(
                "DAG verify RCA detected contradiction in group %d: %s",
                group_idx, rca_result.contradiction_detail[:200],
            )
            resolution = await _escalate_contradiction(
                runner, feature, "implementation", "verify",
                BugGroup(
                    group_id=f"dag-g{group_idx}-r{retry}",
                    likely_root_cause=rca_result.hypothesis,
                    severity="blocker",
                ),
                rca_result,
            )
            fix_direction = (
                f"\n\n## User Decision (from contradiction resolution)\n"
                f"{resolution}\n\n"
                f"Apply this direction — it overrides any conflicting spec.\n"
            )

        fix_ws_path = str(feature_root) if feature_root else None
        logger.info(
            "DAG verify fix workspace: feature_root=%s, repo_counts=%s, "
            "fix_ws_path=%s, tasks=%s",
            feature_root,
            {t.repo_path: sum(1 for x in group_tasks if x.repo_path == t.repo_path) for t in group_tasks if t.repo_path},
            fix_ws_path,
            [t.id for t in group_tasks[:3]],
        )

        rca_guidance = ""
        if isinstance(rca_result, RootCauseAnalysis) and rca_result.confidence != "contradiction":
            rca_guidance = (
                f"\n\n## RCA Analysis\n"
                f"**Hypothesis:** {rca_result.hypothesis}\n"
                f"**Proposed approach:** {rca_result.proposed_approach}\n"
            )
        direct_route_guidance = ""
        if direct_route.route == _COMMIT_HYGIENE_ROUTE:
            targets = "\n".join(f"- `{target}`" for target in direct_route.target_files)
            direct_route_guidance = (
                "\n\n## Deterministic Commit-Blocker Route\n"
                "The host classified this retry as commit_hygiene_focused. "
                "Expanded verify, parallel repair, and RCA were skipped because "
                "the current verdict contains only commit-hook hygiene concerns.\n\n"
                "Fix only the pre-commit/husky blocker shown in the feedback. "
                "Do not broaden the change into semantic redesign; the normal "
                "commit and verifier path will run after this repair.\n\n"
                f"**Route signature:** `{direct_route.signature}`\n\n"
                f"**Target files:**\n{targets if targets else '- (from hook output)'}\n"
            )
        elif direct_route.route == _MANIFEST_FORBIDDEN_CLEANUP_ROUTE:
            targets = "\n".join(f"- `{target}`" for target in direct_route.target_files)
            direct_route_guidance = (
                "\n\n## Manifest-Forbidden Commit Cleanup Route\n"
                "The host classified this retry as "
                "manifest_forbidden_product_cleanup. Expanded verify, parallel "
                "repair, and RCA were skipped because the preflight or "
                "commit/husky failure references files that are forbidden by "
                "verify-file-scope.expected-files.json.\n\n"
                "Treat the hook failure as a symptom of forbidden product drift. "
                "Delete or port the forbidden files into the canonical tree, and "
                "preserve any acceptance coverage before reporting completion.\n\n"
                "Do NOT fix this by adding `.eslint-ignore`, eslint-disable "
                "comments, test skips, hook bypasses, or other suppression rules. "
                "Suppression is invalid when the target path is manifest-forbidden.\n\n"
                f"**Route signature:** `{direct_route.signature}`\n\n"
                f"**Forbidden cleanup targets:**\n"
                f"{targets if targets else '- (from hook output/git status)'}\n"
            )

        fix_actor = _make_parallel_actor(
            implementer, f"g{group_idx}-fix-{retry}",
            runtime=dag_impl_runtime,
            workspace_path=fix_ws_path,
        )
        workspace_ctx = ""
        if fix_ws_path:
            workspace_ctx = (
                f"\n\n## Workspace\n"
                f"Your working directory is: `{fix_ws_path}`\n"
                f"All file reads and writes MUST use paths within this directory.\n"
                f"Do NOT use absolute paths from search results that point to "
                f"other copies of the same repo.\n"
            )
        contradiction_context = await _format_contradiction_decisions_context(
            runner, feature,
        )

        fix_context_package = await _build_prompt_context_package(
            runner,
            feature,
            title=f"DAG Verify Fix — Group {group_idx} Retry {retry + 1}",
            file_stem=f"g{group_idx}-fix-{retry}",
            intro_lines=[
                "Fix the verifier findings for this DAG group using the referenced RCA and feedback files.",
            ],
            sections=[
                ("direct-route", "Deterministic Repair Route", direct_route_guidance),
                ("feedback", "Verifier Feedback", feedback),
                ("rca-guidance", "RCA Guidance", rca_guidance),
                ("user-direction", "User Decision", fix_direction),
                (
                    "contradiction-decisions",
                    "Resolved Contradiction Decisions",
                    contradiction_context,
                ),
                ("fix-context", "Original Enhancement Items", fix_context),
                ("workspace", "Workspace", workspace_ctx),
            ],
        )
        fix_prompt = (
            f"Verification failed (attempt {retry + 1}/{VERIFY_RETRIES}). "
            "Read the referenced context carefully, then fix the issues.\n\n"
            f"{_context_package_prompt(fix_context_package)}"
            "## Instructions\n"
            "1. Read each affected file listed above\n"
            "2. Identify the root cause of each issue\n"
            "3. Apply targeted fixes — do NOT rewrite files unnecessarily\n"
            "4. Verify your fix addresses the specific concern/gap described"
        )
        fix_result = await runner.run(
            Ask(
                actor=fix_actor,
                prompt=fix_prompt,
                output_type=ImplementationResult,
            ),
            feature,
            phase_name="implementation",
        )
        if isinstance(fix_result, ImplementationResult):
            sanitized = await _sanitize_dag_repair_results(
                runner,
                feature,
                group_idx,
                retry,
                [fix_result],
                feature_root,
                context_label="single-fix",
            )
            fix_result = sanitized[0]
            permission_report = _normalize_feature_workspace_cleanup_permissions(
                feature_root,
                _implementation_result_permission_targets([fix_result]),
                reason="single-fix-output",
            )
            await _record_workspace_permission_repair(
                runner,
                feature,
                group_idx,
                str(retry),
                permission_report,
                context="single-fix-output",
            )
            await runner.artifacts.put(
                f"dag-fix:g{group_idx}:retry-{retry}",
                fix_result.model_dump_json(),
                feature=feature,
            )
        all_results.append(fix_result)
        verify_results_context = [*verify_results_context, fix_result]
        verify_results_context = await _sanitize_dag_repair_results(
            runner,
            feature,
            group_idx,
            retry,
            verify_results_context,
            feature_root,
            context_label="single-final-preflight",
        )
        if direct_route.route == _MANIFEST_FORBIDDEN_CLEANUP_ROUTE:
            remaining, staging_only = _manifest_forbidden_cleanup_remaining_problems(
                feature_root,
                direct_route.target_files,
            )
            await runner.artifacts.put(
                f"dag-manifest-cleanup-gate:g{group_idx}:retry-{retry}",
                json.dumps({
                    "group_idx": group_idx,
                    "retry": retry,
                    "approved": not remaining,
                    "blocking_problems": remaining,
                    "staging_only_deletions": staging_only,
                    "target_files": direct_route.target_files,
                    "route": direct_route.to_dict(),
                }, indent=2),
                feature=feature,
            )
            if remaining:
                verdict = _manifest_cleanup_remaining_verdict(
                    group_idx=group_idx,
                    retry=retry,
                    problems=remaining,
                )
                await runner.artifacts.put(
                    f"dag-verify:g{group_idx}:retry-{retry}",
                    to_str(verdict),
                    feature=feature,
                )
                continue
        try:
            await _commit_repos(
                runner,
                feature,
                f"fix: group {group_idx} verify retry {retry + 1}",
                failure_key=f"dag-commit-failure:g{group_idx}:retry-{retry}",
                failure_metadata={
                    "group_idx": group_idx,
                    "stage": f"retry-{retry}",
                    "retry": retry,
                    "message": f"fix: group {group_idx} verify retry {retry + 1}",
                },
            )
        except WorkflowCommitError as exc:
            await _record_dag_commit_failure(
                runner,
                feature,
                group_idx,
                f"retry-{retry}",
                exc,
                message=f"single DAG repair commit failed in retry {retry}",
            )
            verdict = _commit_failure_verdict(
                exc,
                group_idx=group_idx,
                stage=f"retry-{retry}",
            )
            await runner.artifacts.put(
                f"dag-verify:g{group_idx}:retry-{retry}",
                to_str(verdict),
                feature=feature,
            )
            if isinstance(verdict, Verdict):
                ledger = await _load_ledger(runner, feature)
                ledger = _update_ledger(ledger, verdict, "verify", 0)
                await _save_ledger(runner, feature, ledger)
            continue
        reconcile = await _reconcile_dag_task_results(
            runner,
            feature,
            group_idx,
            str(retry),
            group_tasks,
            results=results,
            verify_results_context=verify_results_context,
            all_results=all_results,
            repair_results=(
                [fix_result]
                if isinstance(fix_result, ImplementationResult)
                else []
            ),
            feature_root=feature_root,
        )
        results = reconcile.results
        verify_results_context = reconcile.verify_results_context
        all_results[:] = reconcile.all_results
        spec_reconcile = await _reconcile_dag_task_specs(
            runner,
            feature,
            group_idx,
            str(retry),
            group_tasks,
            feature_root=feature_root,
        )
        group_tasks[:] = spec_reconcile.tasks
        group_files = _collect_files(verify_results_context)
        logger.info(
            "DAG group verify starting group=%d attempt=retry-%d runtime=%s rca_runtime=%s "
            "tasks=%s files=%d results=%d",
            group_idx,
            retry,
            dag_final_review_runtime or "<default>",
            dag_rca_runtime or "<default>",
            [task.id for task in group_tasks],
            len(group_files),
            len(verify_results_context),
        )
        await _log_feature_event(
            runner,
            feature.id,
            "dag_verify_start",
            "implementation",
            content=f"g{group_idx}:retry-{retry}",
            metadata={
                "group_idx": group_idx,
                "retry": retry,
                "runtime": dag_final_review_runtime,
                "task_ids": [task.id for task in group_tasks],
                "file_count": len(group_files),
                "result_count": len(verify_results_context),
                "parallel_repair": False,
            },
        )
        preflight_verdict = None
        if verify_fn is None:
            preflight_verdict = await _run_dag_group_preflight(
                runner,
                feature,
                group_idx,
                str(retry),
                group_tasks,
                verify_results_context,
                feature_root=feature_root,
                known_task_ids=known_task_ids,
            )
        if preflight_verdict is not None:
            verdict = preflight_verdict
        else:
            verdict = await _do_verify(
                runner, feature, verify_results_context, group_files, group_tasks,
                runtime=dag_final_review_runtime,
            )
        logger.info(
            "DAG group verify finished group=%d attempt=retry-%d approved=%s",
            group_idx,
            retry,
            _is_approved(verdict),
        )
        await _log_feature_event(
            runner,
            feature.id,
            "dag_verify_finish",
            "implementation",
            content=f"g{group_idx}:retry-{retry}",
            metadata={
                "group_idx": group_idx,
                "retry": retry,
                "approved": _is_approved(verdict),
                "runtime": dag_final_review_runtime,
                "parallel_repair": False,
            },
        )
        await runner.artifacts.put(
            f"dag-verify:g{group_idx}:retry-{retry}",
            to_str(verdict),
            feature=feature,
        )

        # Ledger dedup + severity partition for re-verify
        if isinstance(verdict, Verdict):
            ledger = await _load_ledger(runner, feature)
            ledger_verdict, _suppressed = _dedup_findings(verdict, ledger, "verify")
            if _suppressed:
                logger.info("Suppressed %d duplicate findings from verify retry (group %d)", len(_suppressed), group_idx)
            ledger_verdict, _enhancements = _partition_verdict(
                ledger_verdict,
                "verify",
                f"group-{group_idx}-retry-{retry}",
            )
            await _append_enhancements(runner, feature, _enhancements)
            ledger = _update_ledger(ledger, ledger_verdict, "verify", 0)
            await _save_ledger(runner, feature, ledger)

    # ── Record outcomes + checkpoint ──────────────────────────────────
    if _is_approved(verdict):
        for r in results:
            if isinstance(r, ImplementationResult):
                handover.record_success(r)

        try:
            commit_hash = await _commit_group(runner, feature, group_idx, group_tasks)
        except WorkflowCommitError as exc:
            await _record_dag_commit_failure(
                runner,
                feature,
                group_idx,
                "checkpoint",
                exc,
                message="checkpoint commit failed",
                extra_metadata={"task_ids": [task.id for task in group_tasks]},
            )
            verdict = _commit_failure_verdict(
                exc,
                group_idx=group_idx,
                stage="checkpoint",
            )
            await runner.artifacts.put(
                f"dag-verify:g{group_idx}:checkpoint-commit",
                to_str(verdict),
                feature=feature,
            )
            for r in results:
                if isinstance(r, ImplementationResult):
                    handover.record_failure(
                        r.task_id,
                        r.summary,
                        _format_feedback("Commit", verdict),
                    )
            return False, _format_feedback("Commit", verdict)

        checkpoint = {
            "group_idx": group_idx,
            "task_ids": [t.id for t in group_tasks],
            "results": [
                r.model_dump()
                for r in results
                if isinstance(r, ImplementationResult)
            ],
            "verdict": "approved",
            "commit_hash": commit_hash,
        }
        await runner.artifacts.put(
            f"dag-group:{group_idx}",
            _json.dumps(checkpoint),
            feature=feature,
        )
        await _log_feature_event(
            runner,
            feature.id,
            "dag_group_checkpoint",
            "implementation",
            content=f"group {group_idx}",
            metadata={
                "group_idx": group_idx,
                "task_ids": [t.id for t in group_tasks],
                "commit_hash": commit_hash,
                "result_count": len(checkpoint["results"]),
            },
        )
        await enqueue_public_exhibit_refresh(
            runner,
            feature,
            reason=f"dag-group-{group_idx}-checkpoint",
            group_idx=group_idx,
            priority=20,
        )
        logger.info(
            "Group %d checkpointed (commit %s)", group_idx, commit_hash,
        )
        return True, ""
    else:
        for r in results:
            if isinstance(r, ImplementationResult):
                handover.record_failure(
                    r.task_id, r.summary, _format_feedback("Verify", verdict),
                )
        return False, _format_feedback("Verify", verdict)


async def _implement_dag(
    runner: WorkflowRunner, feature: Feature, dag: ImplementationDAG
) -> tuple[str, str, HandoverDoc]:
    """Execute the full DAG with per-group verification, checkpointing, and
    handover tracking.

    **Checkpointing:**
    - ``dag-task:{task_id}`` — per-task result (survives mid-group crash)
    - ``dag-group:{group_idx}`` — group completion marker with commit hash
    - On resume, completed groups and tasks are skipped.

    Returns ``(impl_text, failure, handover)``.  *failure* is empty when every
    group passed verification.
    """
    import json as _json

    tasks_by_id = {t.id: t for t in dag.tasks}
    all_results: list[object] = []
    handover = HandoverDoc()

    # ── Resume: reconstruct state from checkpointed groups ──────────
    start_group = 0
    for g_idx in range(len(dag.execution_order)):
        checkpoint_json = await runner.artifacts.get(
            f"dag-group:{g_idx}", feature=feature,
        )
        if not checkpoint_json:
            break
        try:
            data = _json.loads(checkpoint_json)
        except (ValueError, TypeError):
            break
        for r_data in data.get("results", []):
            try:
                result = ImplementationResult.model_validate(r_data)
                all_results.append(result)
                handover.record_success(result)
            except Exception:
                pass
        start_group = g_idx + 1
        logger.info(
            "Group %d already complete (commit %s) — skipping",
            g_idx, data.get("commit_hash", "?"),
        )

    # ── Execute remaining groups ────────────────────────────────────
    for group_idx, group in enumerate(dag.execution_order):
        if group_idx < start_group:
            continue

        group_tasks = [tasks_by_id[tid] for tid in group]
        if dag_path_canonicalization_enabled():
            group_tasks, path_rewrites = canonicalize_implementation_tasks(group_tasks)
            await _record_dag_path_canonicalization(
                runner,
                feature,
                group_idx,
                path_rewrites,
                context_label="implementation-runtime",
            )
        group_tasks_by_id = {task.id: task for task in group_tasks}

        # Ensure worktrees exist for all repos this group touches
        await _ensure_task_worktrees(runner, feature, group_tasks)

        # Adversarial runtime routing.
        runtime_policy = _runner_runtime_policy(runner)
        impl_runtime, review_runtime = _dag_group_runtime_pair(
            group_idx,
            runtime_policy,
        )
        diagnostic_runtime = _dag_repair_runtime_for(
            "dag-rca",
            _diagnostic_runtime_for_policy(runtime_policy),
        )
        logger.info(
            "Group %d: implement=%s, review=%s, rca=%s (runtime_policy=%s)",
            group_idx, impl_runtime, review_runtime, diagnostic_runtime,
            runtime_policy,
        )

        # Build prompts with handover context from prior groups
        handover_context = ""
        if handover.completed or handover.failed_attempts:
            handover.compress()
            handover_context = f"\n\n## Handover — Prior Work\n\n{to_markdown(handover)}"

        # ── Per-task resume: check which tasks already completed ─────
        pending_tasks: list[ImplementationTask] = []
        completed_results: list[ImplementationResult] = []
        for tid in group:
            task_marker = await runner.artifacts.get(
                f"dag-task:{tid}", feature=feature,
            )
            if task_marker:
                try:
                    result = ImplementationResult.model_validate_json(task_marker)
                    # Only skip if the task actually completed successfully
                    if result.status == "completed":
                        completed_results.append(result)
                        logger.info("Task %s already complete — skipping", tid)
                        continue
                    logger.warning(
                        "Task %s has status %r — re-running", tid, result.status,
                    )
                except Exception:
                    pass
            pending_tasks.append(group_tasks_by_id[tid])

        # ── Resolve worktree paths for each task ────────────────────
        workspace_mgr = runner.services.get("workspace_manager")
        feature_root = (
            Path(workspace_mgr._base) / ".iriai" / "features" / feature.slug / "repos"
            if workspace_mgr
            else None
        )

        if pending_tasks:
            writeability_problems = _dag_workspace_writeability_problems(
                feature_root,
                pending_tasks,
            )
            if writeability_problems:
                await runner.artifacts.put(
                    f"dag-writeability-preflight:g{group_idx}:initial",
                    _json.dumps({
                        "group_idx": group_idx,
                        "approved": False,
                        "problems": writeability_problems,
                    }),
                    feature=feature,
                )
                await _log_feature_event(
                    runner,
                    feature.id,
                    "dag_writeability_preflight_failed",
                    "implementation",
                    content=f"group {group_idx}",
                    metadata={
                        "group_idx": group_idx,
                        "problem_count": len(writeability_problems),
                        "task_ids": sorted({
                            str(problem.get("task_id", ""))
                            for problem in writeability_problems
                            if problem.get("task_id")
                        }),
                    },
                )
                details = "; ".join(
                    f"{problem.get('task_id')}: {problem.get('path')} "
                    f"({problem.get('reason')} at {problem.get('directory')})"
                    for problem in writeability_problems[:10]
                )
                if len(writeability_problems) > 10:
                    details += f"; +{len(writeability_problems) - 10} more"
                failure = (
                    "Workspace writeability preflight failed before task dispatch. "
                    "Fix canonical target permissions instead of allowing agents to "
                    f"park _pending_* fallbacks. Problems: {details}"
                )
                return "\n\n".join(to_str(r) for r in all_results), failure, handover

        # ── Dispatch pending tasks with retry on crash ──────────────
        TASK_MAX_RETRIES = 5
        TASK_WARN_AT = 3  # Send Slack notification at this attempt
        new_results: list[object] = []
        results: list[object] = list(completed_results)
        initial_verdict: object | None = None
        initial_verdict_key: str | None = None
        if pending_tasks:
            await _log_feature_event(
                runner,
                feature.id,
                "dag_task_dispatch",
                "implementation",
                content=f"group {group_idx}",
                metadata={
                    "group_idx": group_idx,
                    "task_ids": [task.id for task in pending_tasks],
                    "skipped_completed_task_ids": [result.task_id for result in completed_results],
                    "runtime": impl_runtime,
                    "dispatch_kind": "implementation",
                },
            )

            async def _run_task(task_idx: int, t: ImplementationTask) -> ImplementationResult:
                """Run a single implementation task with retry on crash."""
                repo_prefix = t.repo_path
                ws_path = None
                if feature_root and repo_prefix:
                    worktree = feature_root / repo_prefix
                    if worktree.exists():
                        ws_path = str(worktree)

                # ── Build prompt, offloading to files if too large ──
                prefix = f"{repo_prefix}/" if repo_prefix else ""
                inline_prompt = _build_task_prompt(t, repo_prefix=prefix) + handover_context

                context_base = ws_path or (str(feature_root) if feature_root else None)
                if len(inline_prompt) > PROMPT_FILE_THRESHOLD and context_base:
                    context_dir = Path(context_base) / ".iriai-context" / t.id
                    context_dir.mkdir(parents=True, exist_ok=True)

                    task_prompt = _build_task_prompt(
                        t, repo_prefix=prefix, context_dir=context_dir,
                    )
                    if handover_context:
                        handover_path = context_dir / "handover.md"
                        handover_path.write_text(
                            handover_context.lstrip(), encoding="utf-8",
                        )
                        rel_handover = f".iriai-context/{t.id}/handover.md"
                        task_prompt += (
                            f"\n\n## Handover — Prior Work\n"
                            f"Prior work context is in `{rel_handover}`.\n"
                            f"**Read that file to understand what has been completed.**"
                        )
                    else:
                        task_prompt += handover_context

                    logger.info(
                        "Task %s: prompt offloaded to files (%d → %d chars)",
                        t.id, len(inline_prompt), len(task_prompt),
                    )
                else:
                    task_prompt = inline_prompt

                for attempt in range(TASK_MAX_RETRIES + 1):
                    try:
                        await _log_feature_event(
                            runner,
                            feature.id,
                            "dag_task_start",
                            "implementation",
                            content=t.id,
                            metadata={
                                "group_idx": group_idx,
                                "task_id": t.id,
                                "task_name": t.name,
                                "repo_path": t.repo_path,
                                "attempt": attempt,
                                "runtime": impl_runtime,
                            },
                        )
                        result = await runner.run(
                            Ask(
                                actor=_make_parallel_actor(
                                    implementer, f"g{group_idx}-t{task_idx}-a{attempt}",
                                    runtime=impl_runtime,
                                    workspace_path=ws_path,
                                ),
                                prompt=task_prompt,
                                output_type=ImplementationResult,
                            ),
                            feature,
                            phase_name="implementation",
                        )
                        # Force correct task_id
                        if isinstance(result, ImplementationResult):
                            if result.task_id != t.id:
                                logger.warning(
                                    "Task reported task_id=%r, expected %r — correcting",
                                    result.task_id, t.id,
                                )
                                result.task_id = t.id
                            # Enrich fallback results that have empty file metadata
                            if not result.files_created and not result.files_modified:
                                await _enrich_fallback_result(result, ws_path, t)
                            await _log_feature_event(
                                runner,
                                feature.id,
                                "dag_task_finish",
                                "implementation",
                                content=t.id,
                                metadata={
                                    "group_idx": group_idx,
                                    "task_id": t.id,
                                    "task_name": t.name,
                                    "status": result.status,
                                    "attempt": attempt,
                                    "runtime": impl_runtime,
                                    "files_created": result.files_created,
                                    "files_modified": result.files_modified,
                                },
                            )
                        return result
                    except Exception as e:
                        await _log_feature_event(
                            runner,
                            feature.id,
                            "dag_task_error",
                            "implementation",
                            content=t.id,
                            metadata={
                                "group_idx": group_idx,
                                "task_id": t.id,
                                "task_name": t.name,
                                "attempt": attempt,
                                "runtime": impl_runtime,
                                "error_type": type(e).__name__,
                                "error": str(e)[:1000],
                            },
                        )
                        logger.warning(
                            "Task %s crashed (attempt %d/%d): %s",
                            t.id, attempt + 1, TASK_MAX_RETRIES + 1, e,
                        )
                        # Prompt overflow is deterministic — retrying is futile
                        err_msg = str(e).lower()
                        if "prompt too long" in err_msg or "input too long" in err_msg:
                            logger.error(
                                "Task %s: prompt exceeds model context — skipping retries",
                                t.id,
                            )
                            return ImplementationResult(
                                task_id=t.id,
                                summary=f"BLOCKED: prompt too large for model context window: {e}",
                                status="blocked",
                            )
                        if attempt + 1 == TASK_WARN_AT:
                            # Notify user via Slack that a task is struggling
                            try:
                                await runner.run(
                                    Notify(
                                        message=(
                                            f"⚠️ Task `{t.id}` ({t.name}) has crashed "
                                            f"{TASK_WARN_AT} times in group {group_idx}.\n"
                                            f"Last error: `{str(e)}`\n"
                                            f"Retrying ({TASK_MAX_RETRIES - attempt} attempts left)..."
                                        ),
                                    ),
                                    feature,
                                    phase_name="implementation",
                                )
                            except Exception:
                                pass  # Don't let notification failure block retries
                        if attempt >= TASK_MAX_RETRIES:
                            logger.error(
                                "Task %s failed after %d attempts: %s",
                                t.id, TASK_MAX_RETRIES + 1, e,
                            )
                            return ImplementationResult(
                                task_id=t.id,
                                summary=f"FAILED after {TASK_MAX_RETRIES + 1} attempts: {e}",
                                status="blocked",
                            )
                # Unreachable but satisfies type checker
                return ImplementationResult(task_id=t.id, summary="FAILED", status="blocked")

            # Dispatch all tasks in parallel with individual error handling
            gathered = await _asyncio.gather(
                *[_run_task(i, t) for i, t in enumerate(pending_tasks)],
            )
            new_results = list(gathered)

            # Save per-task markers
            for r in new_results:
                if isinstance(r, ImplementationResult) and r.task_id:
                    await runner.artifacts.put(
                        f"dag-task:{r.task_id}",
                        r.model_dump_json(),
                        feature=feature,
                    )

            results = list(completed_results) + list(new_results)
            all_results.extend(new_results)  # Don't double-count resumed results

            # Commit after implementation so work is never left uncommitted
            task_ids = [r.task_id for r in new_results if isinstance(r, ImplementationResult) and r.task_id]
            try:
                await _commit_repos(
                    runner,
                    feature,
                    f"feat: group {group_idx} impl — {', '.join(task_ids[:3])}"
                    + (f" (+{len(task_ids) - 3} more)" if len(task_ids) > 3 else ""),
                    failure_key=f"dag-commit-failure:g{group_idx}:implementation",
                    failure_metadata={
                        "group_idx": group_idx,
                        "stage": "implementation",
                        "task_ids": task_ids,
                    },
                )
            except WorkflowCommitError as exc:
                await _log_feature_event(
                    runner,
                    feature.id,
                    "dag_commit_failed",
                    "implementation",
                    content=f"g{group_idx}:implementation",
                    metadata={
                        "group_idx": group_idx,
                        "stage": "implementation",
                        "failed_repo_count": len(exc.failed_outcomes),
                        "successful_commit_hashes": exc.successful_hashes,
                        "task_ids": task_ids,
                    },
                )
                initial_verdict = _commit_failure_verdict(
                    exc,
                    group_idx=group_idx,
                    stage="implementation",
                )
                initial_verdict_key = f"dag-verify:g{group_idx}:implementation-commit"
                await runner.artifacts.put(
                    initial_verdict_key,
                    to_str(initial_verdict),
                    feature=feature,
                )

        # ── Verify + fix loop (shared with enhancement group) ─────────
        approved, failure = await _verify_and_fix_group(
            runner, feature, group_idx, group_tasks,
            results, all_results, handover, feature_root,
            impl_runtime, review_runtime, diagnostic_runtime,
            known_task_ids=set(tasks_by_id),
            initial_verdict=initial_verdict,
            initial_verdict_key=initial_verdict_key,
        )
        if not approved:
            remaining = dag.execution_order[group_idx + 1 :]
            remaining_names = [
                tasks_by_id[tid].name for g in remaining for tid in g
            ]
            if remaining_names:
                failure += (
                    "\n\nThe DAG was halted. Unexecuted tasks: "
                    + ", ".join(remaining_names)
                )
            impl_text = "\n\n".join(to_str(r) for r in all_results)
            return impl_text, failure, handover

    # ── Enhancement group: fix accumulated non-blocking findings ──────
    enh_failure = await _run_enhancement_group(
        runner, feature, dag, all_results, handover,
    )
    if enh_failure:
        return "\n\n".join(to_str(r) for r in all_results), enh_failure, handover

    return "\n\n".join(to_str(r) for r in all_results), "", handover


async def _run_enhancement_group(
    runner: WorkflowRunner,
    feature: Feature,
    dag: ImplementationDAG,
    all_results: list[object],
    handover: HandoverDoc,
) -> str:
    """Run an extra implementation group to fix accumulated enhancements.

    Returns an empty string on success (or when the backlog is empty).
    Returns a failure message string when the enhancement group fails
    verification.
    """
    import json as _json

    backlog_raw = await runner.artifacts.get("enhancement-backlog", feature=feature)
    if not backlog_raw:
        return ""
    try:
        backlog = EnhancementBacklog.model_validate_json(backlog_raw)
    except Exception:
        return ""
    if not backlog.items:
        return ""

    enhancement_group_idx = len(dag.execution_order)

    # ── Resume: skip if enhancement group already passed ──────────
    checkpoint_json = await runner.artifacts.get(
        f"dag-group:{enhancement_group_idx}", feature=feature,
    )
    if checkpoint_json:
        try:
            data = _json.loads(checkpoint_json)
            if data.get("verdict") == "approved":
                logger.info("Enhancement group already complete — skipping")
                return ""
        except (ValueError, TypeError):
            pass

    logger.info(
        "Enhancement group: %d items to fix", len(backlog.items),
    )

    # ── Resolve workspace root (needed by analysis + dispatch) ────
    workspace_mgr = runner.services.get("workspace_manager")
    feature_root = (
        Path(workspace_mgr._base) / ".iriai" / "features" / feature.slug / "repos"
        if workspace_mgr
        else None
    )

    # ── Opus analysis: decompose backlog into per-repo tasks ────────
    known_repos = sorted({t.repo_path for t in dag.tasks if t.repo_path})

    indexed_items = []
    for i, item in enumerate(backlog.items):
        file_hint = f" (file: `{item.file}`)" if item.file else ""
        indexed_items.append(f"[{i}] [{item.severity}] {item.description}{file_hint}")

    # ── Resume: load cached decomposition if available ──────────
    decomposition: EnhancementDecomposition | None = None
    decomp_raw = await runner.artifacts.get(
        "enhancement-decomposition", feature=feature,
    )
    if decomp_raw:
        try:
            decomposition = EnhancementDecomposition.model_validate_json(decomp_raw)
            logger.info(
                "Loaded cached enhancement decomposition: %d tasks, %d already-resolved",
                len(decomposition.tasks), len(decomposition.already_resolved),
            )
        except Exception:
            pass

    if decomposition is None:
        try:
            from ....config import BUDGET_TIERS

            analyst_role = Role(
                name="enhancement-analyst",
                prompt=(
                    "You are a senior engineer analyzing deferred code issues. "
                    "Your job is to route each issue to the correct repository "
                    "so that per-repo implementers can fix them in parallel."
                ),
                tools=["Read", "Glob", "Grep"],
                model=BUDGET_TIERS["opus"],
            )
            analyst = AgentActor(
                name="enhancement-analyst",
                role=analyst_role,
                context_keys=["project"],
            )
            decomposition = await runner.run(
                Ask(
                    actor=_make_parallel_actor(
                        analyst, "decompose",
                        workspace_path=str(feature_root) if feature_root else None,
                    ),
                prompt=(
                    f"## Enhancement Backlog Decomposition\n\n"
                    f"There are {len(backlog.items)} deferred issues to fix. "
                    f"Assign each to a repository so per-repo agents can work "
                    f"in parallel.\n\n"
                    f"### Available Repositories\n"
                    + "\n".join(f"- `{r}`" for r in known_repos)
                    + "\n\n### Enhancement Items\n\n"
                    + "\n".join(indexed_items)
                    + "\n\n### Instructions\n"
                    "1. For each item, determine which repo it belongs to. "
                    "Use the file path if present, otherwise search the codebase "
                    "(Grep for class/function names mentioned in the description).\n"
                    "2. Group items by repo in your output.\n"
                    "3. If an item clearly references work that was completed in "
                    "a later group (the items are ordered by group), mark it as "
                    "`already_resolved`.\n"
                    "4. Every item index (0 to "
                    f"{len(backlog.items) - 1}) must appear in exactly one task "
                    "or in `already_resolved`.\n"
                ),
                output_type=EnhancementDecomposition,
            ),
            feature,
            phase_name="implementation",
        )
        except Exception as e:
            logger.warning("Enhancement decomposition failed: %s — falling back to single task", e)

        # Checkpoint the decomposition so it survives restarts
        if isinstance(decomposition, EnhancementDecomposition):
            await runner.artifacts.put(
                "enhancement-decomposition",
                decomposition.model_dump_json(),
                feature=feature,
            )

    # ── Build tasks from decomposition (or fallback) ──────────────
    if isinstance(decomposition, EnhancementDecomposition) and decomposition.tasks:
        logger.info(
            "Enhancement decomposition: %d repo tasks, %d already-resolved",
            len(decomposition.tasks), len(decomposition.already_resolved),
        )
        # Track which items are assigned to tasks (for verification)
        assigned_indices: set[int] = set()
        for rt in decomposition.tasks:
            assigned_indices.update(rt.item_indices)

        enhancement_tasks: list[ImplementationTask] = []
        for rt in decomposition.tasks:
            desc_lines = []
            for idx in rt.item_indices:
                if 0 <= idx < len(backlog.items):
                    item = backlog.items[idx]
                    file_hint = f" (`{item.file}`)" if item.file else ""
                    desc_lines.append(f"- [{item.severity}] {item.description}{file_hint}")
            if not desc_lines:
                continue
            enhancement_tasks.append(ImplementationTask(
                id=f"enhancement-{rt.repo_path}",
                name=f"Fix enhancements in {rt.repo_path} ({len(desc_lines)} items)",
                description=(
                    f"Fix the following deferred issues in `{rt.repo_path}`.\n\n"
                    "**Important:** Some issues may have been resolved by subsequent "
                    "implementation groups. For each item, **check whether the issue "
                    "still exists** before fixing. If already resolved, skip it and "
                    "note it in your summary.\n\n"
                    + "\n".join(desc_lines)
                ),
                repo_path=rt.repo_path,
            ))

        # Items for verification: only those assigned to tasks
        verify_items = [
            backlog.items[i] for i in sorted(assigned_indices)
            if 0 <= i < len(backlog.items)
        ]
    else:
        # Fallback: single task with all items
        desc_lines = []
        for item in backlog.items:
            file_hint = f" (`{item.file}`)" if item.file else ""
            desc_lines.append(f"- [{item.severity}] {item.description}{file_hint}")

        enhancement_tasks = [
            ImplementationTask(
                id="enhancement-all",
                name=f"Fix enhancement backlog ({len(backlog.items)} items)",
                description=(
                    "Fix the following non-blocking issues that were deferred "
                    "during prior implementation and review passes.\n\n"
                    "**Important:** Some issues may have been resolved by subsequent "
                    "implementation groups. For each item, **check whether the issue "
                    "still exists** before fixing. If already resolved, skip it and "
                    "note it in your summary.\n\n"
                    + "\n".join(desc_lines)
                ),
            ),
        ]
        verify_items = list(backlog.items)

    enh_tasks_by_id = {t.id: t for t in enhancement_tasks}

    # ── Ensure worktrees ──────────────────────────────────────────
    await _ensure_task_worktrees(runner, feature, enhancement_tasks)

    # ── Runtime routing (continue from last DAG group by default) ─
    runtime_policy = _runner_runtime_policy(runner)
    impl_runtime, review_runtime = _dag_group_runtime_pair(
        enhancement_group_idx,
        runtime_policy,
    )
    diagnostic_runtime = _diagnostic_runtime_for_policy(runtime_policy)

    # ── Build handover context ────────────────────────────────────
    handover_context = ""
    if handover.completed or handover.failed_attempts:
        handover.compress()
        handover_context = f"\n\n## Handover — Prior Work\n\n{to_markdown(handover)}"

    # ── Per-task resume ───────────────────────────────────────────
    pending_tasks: list[ImplementationTask] = []
    completed_results: list[ImplementationResult] = []
    for tid in enh_tasks_by_id:
        task_marker = await runner.artifacts.get(
            f"dag-task:{tid}", feature=feature,
        )
        if task_marker:
            try:
                result = ImplementationResult.model_validate_json(task_marker)
                if result.status == "completed":
                    completed_results.append(result)
                    logger.info("Enhancement task %s already complete — skipping", tid)
                    continue
            except Exception:
                pass
        pending_tasks.append(enh_tasks_by_id[tid])

    # ── Dispatch pending tasks with retry on crash ────────────────
    TASK_MAX_RETRIES = 5
    TASK_WARN_AT = 3
    new_results: list[object] = []

    if pending_tasks:

        async def _run_enh_task(task_idx: int, t: ImplementationTask) -> ImplementationResult:
            repo_prefix = t.repo_path
            ws_path = None
            if feature_root and repo_prefix:
                worktree = feature_root / repo_prefix
                if worktree.exists():
                    ws_path = str(worktree)

            # ── Build prompt, offloading to files if too large ──
            prefix = f"{repo_prefix}/" if repo_prefix else ""
            inline_prompt = _build_task_prompt(t, repo_prefix=prefix) + handover_context

            # Use ws_path for context files, falling back to feature_root
            # for tasks without a specific repo (e.g. enhancement-general).
            context_base = ws_path or (str(feature_root) if feature_root else None)
            if len(inline_prompt) > PROMPT_FILE_THRESHOLD and context_base:
                context_dir = Path(context_base) / ".iriai-context" / t.id
                context_dir.mkdir(parents=True, exist_ok=True)

                task_prompt = _build_task_prompt(
                    t, repo_prefix=prefix, context_dir=context_dir,
                )
                if handover_context:
                    handover_path = context_dir / "handover.md"
                    handover_path.write_text(
                        handover_context.lstrip(), encoding="utf-8",
                    )
                    rel_handover = f".iriai-context/{t.id}/handover.md"
                    task_prompt += (
                        f"\n\n## Handover — Prior Work\n"
                        f"Prior work context is in `{rel_handover}`.\n"
                        f"**Read that file to understand what has been completed.**"
                    )
                else:
                    task_prompt += handover_context

                logger.info(
                    "Enhancement task %s: prompt offloaded to files (%d → %d chars)",
                    t.id, len(inline_prompt), len(task_prompt),
                )
            else:
                task_prompt = inline_prompt

            for attempt in range(TASK_MAX_RETRIES + 1):
                try:
                    result = await runner.run(
                        Ask(
                            actor=_make_parallel_actor(
                                implementer,
                                f"enh-t{task_idx}-a{attempt}",
                                runtime=impl_runtime,
                                workspace_path=ws_path,
                            ),
                            prompt=task_prompt,
                            output_type=ImplementationResult,
                        ),
                        feature,
                        phase_name="implementation",
                    )
                    if isinstance(result, ImplementationResult):
                        if result.task_id != t.id:
                            result.task_id = t.id
                        if not result.files_created and not result.files_modified:
                            await _enrich_fallback_result(result, ws_path, t)
                    return result
                except Exception as e:
                    logger.warning(
                        "Enhancement task %s crashed (attempt %d/%d): %s",
                        t.id, attempt + 1, TASK_MAX_RETRIES + 1, e,
                    )
                    err_msg = str(e).lower()
                    if "prompt too long" in err_msg or "input too long" in err_msg:
                        logger.error(
                            "Enhancement task %s: prompt exceeds model context — skipping retries",
                            t.id,
                        )
                        return ImplementationResult(
                            task_id=t.id,
                            summary=f"BLOCKED: prompt too large for model context window: {e}",
                            status="blocked",
                        )
                    if attempt + 1 == TASK_WARN_AT:
                        try:
                            await runner.run(
                                Notify(
                                    message=(
                                        f"⚠️ Enhancement task `{t.id}` ({t.name}) has crashed "
                                        f"{TASK_WARN_AT} times.\n"
                                        f"Last error: `{str(e)}`\n"
                                        f"Retrying ({TASK_MAX_RETRIES - attempt} attempts left)..."
                                    ),
                                ),
                                feature,
                                phase_name="implementation",
                            )
                        except Exception:
                            pass
                    if attempt >= TASK_MAX_RETRIES:
                        return ImplementationResult(
                            task_id=t.id,
                            summary=f"FAILED after {TASK_MAX_RETRIES + 1} attempts: {e}",
                            status="blocked",
                        )
            return ImplementationResult(task_id=t.id, summary="FAILED", status="blocked")

        gathered = await _asyncio.gather(
            *[_run_enh_task(i, t) for i, t in enumerate(pending_tasks)],
        )
        new_results = list(gathered)

        # Save per-task markers
        for r in new_results:
            if isinstance(r, ImplementationResult) and r.task_id:
                await runner.artifacts.put(
                    f"dag-task:{r.task_id}",
                    r.model_dump_json(),
                    feature=feature,
                )

        await _commit_repos(
            runner,
            feature,
            f"feat: enhancement group — {len(backlog.items)} items",
            failure_key=f"dag-commit-failure:g{enhancement_group_idx}:implementation",
            failure_metadata={
                "group_idx": enhancement_group_idx,
                "stage": "enhancement-implementation",
            },
        )

    results = list(completed_results) + list(new_results)
    all_results.extend(new_results)

    # ── Verify + fix loop (custom verify for enhancements) ─────────
    # Use _verify_enhancements instead of _verify so the verifier checks
    # each enhancement item was addressed and doesn't suppress them.
    async def _enh_verify(
        runner: WorkflowRunner,
        feature: Feature,
        results: list[object],
        files: list[str],
        tasks: list[ImplementationTask] | None = None,
        *,
        runtime: str | None = None,
    ) -> Verdict:
        return await _verify_enhancements(
            runner, feature, results, files, verify_items,
            runtime=runtime,
            feature_root=feature_root,
        )

    # Build fix context so the fix agent knows the original enhancement spec
    enh_fix_lines = []
    for item in verify_items:
        file_hint = f" (`{item.file}`)" if item.file else ""
        enh_fix_lines.append(f"- [{item.severity}] {item.description}{file_hint}")
    enh_fix_context = (
        f"\n\n## Original Enhancement Items\n"
        f"These are the deferred issues this group was supposed to fix. "
        f"The verifier checked each one — address the ones it flagged.\n\n"
        + "\n".join(enh_fix_lines)
    )

    approved, failure = await _verify_and_fix_group(
        runner, feature, enhancement_group_idx, enhancement_tasks,
        results, all_results, handover, feature_root,
        impl_runtime, review_runtime,
        diagnostic_runtime,
        verify_fn=_enh_verify,
        fix_context=enh_fix_context,
    )
    if approved:
        # Clear the backlog — enhancements are now fixed
        await runner.artifacts.put(
            "enhancement-backlog",
            EnhancementBacklog().model_dump_json(),
            feature=feature,
        )
        logger.info("Enhancement backlog cleared after successful verification")

    return failure


async def _commit_repos(
    runner: WorkflowRunner,
    feature: Feature,
    msg: str,
    *,
    failure_key: str | None = None,
    failure_metadata: dict[str, Any] | None = None,
) -> str:
    """Commit uncommitted changes in all feature repo clones.

    The repos root (``repos/``) is not a git repo itself — each
    subdirectory is a separate clone. We find repos with uncommitted
    changes and commit in each one.

    Returns a comma-separated list of commit hashes (one per repo).
    """
    repos_root = _get_feature_root(runner, feature)
    try:
        return await _commit_repos_in_root(repos_root, msg)
    except WorkflowCommitError as exc:
        if failure_key:
            await _record_commit_failure_artifact(
                runner,
                feature,
                failure_key,
                exc,
                metadata=failure_metadata,
            )
        raise


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


async def _commit_repos_in_root(
    repos_root: Path | None,
    msg: str,
) -> str:
    """Commit uncommitted changes in all repo clones rooted under *repos_root*."""
    if not repos_root:
        logger.warning("_commit_repos_in_root: no feature workspace found — skipping")
        return ""

    hygiene_problems = _dag_repo_hygiene_problems(repos_root)
    if hygiene_problems:
        details = ", ".join(
            str(problem.get("path", "<unknown>"))
            for problem in hygiene_problems[:10]
        )
        if len(hygiene_problems) > 10:
            details += f", +{len(hygiene_problems) - 10} more"
        raise WorkflowCommitError(
            "Refusing to commit workflow repos with hygiene blockers",
            [
                CommitRepoOutcome(
                    repo_path=str(repos_root),
                    repo_name=repos_root.name,
                    message=msg,
                    dirty=True,
                    command=["workflow-repo-hygiene-check"],
                    exit_code=1,
                    stderr=details,
                    status_after=json.dumps(hygiene_problems[:25], indent=2),
                    error=(
                        "Refusing to commit workflow repos with hygiene blockers: "
                        f"{details}"
                    ),
                )
            ],
        )

    outcomes: list[CommitRepoOutcome] = []

    def _is_workflow_repo(repo_dir: Path) -> bool:
        # Workflow repos live exactly at feature_root/repos/<name>/. Nested
        # .git dirs (e.g. created by `npm create vite`, `git init` inside an
        # agent shell) are accidental embedded repos; committing in them
        # produces orphaned history. Filter them out.
        try:
            return repo_dir.parent == repos_root and repos_root.name == "repos"
        except Exception:
            return False

    discovered = _discover_repo_roots_under(repos_root)
    for repo_dir in discovered:
        if not _is_workflow_repo(repo_dir):
            logger.info("Skipping nested .git at %s (not a workflow repo)", repo_dir)
            continue
        status_rc, status_stdout, status_stderr = await _git_status_for_commit(repo_dir)
        if status_rc != 0:
            outcomes.append(
                CommitRepoOutcome(
                    repo_path=str(repo_dir),
                    repo_name=repo_dir.name,
                    message=msg,
                    dirty=True,
                    command=["git", "status", "--porcelain"],
                    exit_code=status_rc,
                    stdout=status_stdout,
                    stderr=status_stderr,
                    error="git status failed before commit",
                )
            )
            continue
        status_before = status_stdout.strip()
        if not status_before:
            continue

        add_rc, add_stdout, add_stderr = await _run_git_for_commit(
            repo_dir, "add", "--all", ".",
        )
        if add_rc != 0:
            _, status_after, _ = await _git_status_for_commit(repo_dir)
            outcomes.append(
                CommitRepoOutcome(
                    repo_path=str(repo_dir),
                    repo_name=repo_dir.name,
                    message=msg,
                    status_before=status_before,
                    status_after=status_after.strip(),
                    dirty=True,
                    command=["git", "add", "--all", "."],
                    exit_code=add_rc,
                    stdout=add_stdout,
                    stderr=add_stderr,
                    error="git add failed before commit",
                )
            )
            continue

        commit_rc, commit_stdout, commit_stderr = await _run_git_for_commit(
            repo_dir, "commit", "-m", msg,
        )
        if commit_rc != 0:
            _, status_after, _ = await _git_status_for_commit(repo_dir)
            outcomes.append(
                CommitRepoOutcome(
                    repo_path=str(repo_dir),
                    repo_name=repo_dir.name,
                    message=msg,
                    status_before=status_before,
                    status_after=status_after.strip(),
                    dirty=True,
                    command=["git", "commit", "-m", msg],
                    exit_code=commit_rc,
                    stdout=commit_stdout,
                    stderr=commit_stderr,
                    error="git commit failed",
                )
            )
            logger.warning(
                "Failed to commit in %s (exit %s): %s",
                repo_dir,
                commit_rc,
                commit_stderr.strip() or commit_stdout.strip(),
            )
            continue

        rev_rc, rev_stdout, rev_stderr = await _run_git_for_commit(
            repo_dir, "rev-parse", "HEAD",
        )
        if rev_rc != 0:
            _, status_after, _ = await _git_status_for_commit(repo_dir)
            outcomes.append(
                CommitRepoOutcome(
                    repo_path=str(repo_dir),
                    repo_name=repo_dir.name,
                    message=msg,
                    status_before=status_before,
                    status_after=status_after.strip(),
                    dirty=True,
                    command=["git", "rev-parse", "HEAD"],
                    exit_code=rev_rc,
                    stdout=rev_stdout,
                    stderr=rev_stderr,
                    error="git commit succeeded but HEAD lookup failed",
                )
            )
            continue

        commit_hash = rev_stdout.strip()
        _, status_after, _ = await _git_status_for_commit(repo_dir)
        outcomes.append(
            CommitRepoOutcome(
                repo_path=str(repo_dir),
                repo_name=repo_dir.name,
                message=msg,
                status_before=status_before,
                status_after=status_after.strip(),
                dirty=True,
                command=["git", "commit", "-m", msg],
                stdout=commit_stdout,
                stderr=commit_stderr,
                commit_hash=commit_hash,
            )
        )
        logger.info("Committed in %s: %s", repo_dir.name, commit_hash[:8])

    failures = [outcome for outcome in outcomes if outcome.error or outcome.exit_code]
    if failures:
        raise WorkflowCommitError("Failed to commit dirty workflow repos", outcomes)

    hashes = [outcome.commit_hash for outcome in outcomes if outcome.commit_hash]
    return ",".join(hashes) if hashes else ""


async def _commit_group(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    group_tasks: list[ImplementationTask],
) -> str:
    """Commit after a group's verification passes."""
    task_names = [t.name for t in group_tasks[:3]]
    msg = f"feat: group {group_idx} — {', '.join(task_names)}"
    if len(group_tasks) > 3:
        msg += f" (+{len(group_tasks) - 3} more)"
    return await _commit_repos(
        runner,
        feature,
        msg,
        failure_key=f"dag-commit-failure:g{group_idx}:checkpoint",
        failure_metadata={
            "group_idx": group_idx,
            "stage": "checkpoint",
            "task_ids": [task.id for task in group_tasks],
            "message": msg,
        },
    )


async def _verify(
    runner: WorkflowRunner,
    feature: Feature,
    results: list[object],
    files: list[str],
    tasks: list[ImplementationTask] | None = None,
    *,
    runtime: str | None = None,
) -> Verdict:
    """Verify a group's implementation: claimed work exists + basic tests.

    When *runtime* is set, the verifier is routed to that runtime for
    adversarial multi-model review.
    """
    results_summary = "\n\n".join(to_str(r) for r in results)
    file_list = ", ".join(files) if files else "recently changed files"

    # Collect reference material from the tasks being verified so the
    # verifier can check implementation against upstream specs.
    ref_context = ""
    if tasks:
        ref_parts = []
        for t in tasks:
            if t.reference_material:
                for ref in t.reference_material:
                    ref_parts.append(f"**{ref.source}** (task {t.id}):\n{ref.content}")
        if ref_parts:
            ref_context = (
                "\n\n## Upstream Specs (verify implementation against these)\n\n"
                + "\n\n---\n\n".join(ref_parts)
            )

    # Load enhancement backlog so the verifier knows what's already deferred
    known_issues = ""
    backlog_raw = await runner.artifacts.get("enhancement-backlog", feature=feature)
    if backlog_raw:
        try:
            backlog = EnhancementBacklog.model_validate_json(backlog_raw)
            if backlog.items:
                deferred = "\n".join(
                    f"- [{it.severity}] {it.description}"
                    for it in backlog.items
                )
                known_issues = (
                    f"\n\n## Already-Deferred Issues (DO NOT re-report these)\n"
                    f"The following {len(backlog.items)} minor/nit issues are already "
                    f"tracked in the enhancement backlog. Do NOT include them in your "
                    f"verdict — they are intentionally deferred.\n\n{deferred}\n"
                )
        except Exception:
            pass

    contradiction_decisions = await _format_contradiction_decisions_context(
        runner, feature,
    )
    if contradiction_decisions:
        known_issues += "\n\n" + contradiction_decisions

    verifier = _make_parallel_actor(qa_engineer, "verify", runtime=runtime)

    verify_context = await _build_prompt_context_package(
        runner,
        feature,
        title="Group Verification",
        file_stem="verify",
        intro_lines=[
            "Verify this implementation group against the implementation results, upstream specs, and deferred issue ledger.",
        ],
        sections=[
            ("results", "Implementation Results", results_summary),
            ("reference-material", "Upstream Specs", ref_context),
            ("known-issues", "Deferred Issues and User Decisions", known_issues),
        ],
    )

    verify_prompt = (
        f"{_context_package_prompt(verify_context)}"
        "Verify this implementation group.\n\n"
        "For each result, confirm:\n"
        f"1. All claimed files exist on disk: {file_list}\n"
        "2. Files listed as modified were actually changed\n"
        "3. The changes align with the described summary\n"
        "4. The code compiles, imports correctly, and passes any existing tests for these files\n"
        "5. Implementation matches the upstream specs in the referenced context files\n\n"
        "This is a per-group verification, not a full QA pass."
    )

    return await runner.run(
        Ask(
            actor=verifier,
            prompt=verify_prompt,
            output_type=Verdict,
        ),
        feature,
        phase_name="implementation",
    )


async def _verify_enhancements(
    runner: WorkflowRunner,
    feature: Feature,
    results: list[object],
    files: list[str],
    enhancement_items: list[EnhancementItem],
    *,
    runtime: str | None = None,
    feature_root: Path | None = None,
) -> Verdict:
    """Verify that enhancement fixes are correct and don't introduce regressions.

    Unlike ``_verify()``, this function:
    - Uses the enhancement items themselves as the spec to check against
      (instead of ``reference_material``).
    - Does NOT suppress the enhancement backlog findings — the whole point
      is to verify they were fixed.
    - Explicitly checks for regressions in existing functionality.
    """
    results_summary = "\n\n".join(to_str(r) for r in results)
    file_list = ", ".join(files) if files else "recently changed files"

    # Build the enhancement spec for the verifier
    enh_spec_lines = []
    for item in enhancement_items:
        file_hint = f" (file: `{item.file}`)" if item.file else ""
        enh_spec_lines.append(
            f"- **[{item.severity}]** {item.description}{file_hint}"
        )
    enh_spec = "\n".join(enh_spec_lines)

    verify_context = await _build_prompt_context_package(
        runner,
        feature,
        title="Enhancement Verification",
        file_stem="enhancement-verify",
        intro_lines=[
            "Verify enhancement fixes against the referenced implementation results and enhancement spec.",
        ],
        sections=[
            ("results", "Implementation Results", results_summary),
            (
                "spec",
                "Enhancement Spec",
                "### Enhancement Items (the spec)\n\n"
                "Each item below should have been addressed or confirmed as already resolved by prior work. "
                f"Check each one:\n\n{enh_spec}",
            ),
        ],
    )

    verifier = _make_parallel_actor(qa_engineer, "verify-enh", runtime=runtime)

    return await runner.run(
        Ask(
            actor=verifier,
            prompt=(
                f"## Enhancement Group Verification\n\n"
                f"{_context_package_prompt(verify_context)}"
                f"An implementer was tasked with fixing {len(enhancement_items)} "
                f"deferred non-blocking issues. Verify their work.\n\n"
                f"### Verification Checklist\n\n"
                f"For each file in [{file_list}]:\n"
                f"1. The file exists and the changes compile/import correctly\n"
                f"2. Changes address the specific enhancement items listed above\n"
                f"3. **Regression check:** Existing tests still pass. Run any "
                f"test suites that cover modified files. If no tests exist, "
                f"verify the changes don't break imports or existing behavior\n"
                f"4. Items marked as 'already resolved' by the implementer are "
                f"actually resolved — spot-check a sample\n"
                f"5. Fixes are minimal and targeted — no unnecessary rewrites\n\n"
                f"**Do NOT approve if:**\n"
                f"- Any existing test fails after the changes\n"
                f"- A fix introduces a new bug or breaks an import\n"
                f"- The implementer skipped items that are clearly still broken"
            ),
            output_type=Verdict,
        ),
        feature,
        phase_name="implementation",
    )


# ── RCA → Fix → Re-verify pipeline ──────────────────────────────────────────


def _format_indexed_issues(verdict: Verdict) -> str:
    """Format verdict concerns and gaps with indices for the triage agent."""
    lines: list[str] = []
    for i, c in enumerate(verdict.concerns):
        file_hint = f" (file: {c.file})" if c.file else ""
        lines.append(f"[C{i}] ({c.severity}) {c.description}{file_hint}")
    for i, g in enumerate(verdict.gaps):
        lines.append(f"[G{i}] ({g.severity}) {g.description} (category: {g.category})")
    return "\n".join(lines)


def _extract_group_issues(verdict: Verdict, group: object) -> str:
    """Extract the specific issues for a bug group from the verdict."""
    lines: list[str] = []
    for idx in getattr(group, "issue_indices", []):
        if idx < len(verdict.concerns):
            c = verdict.concerns[idx]
            file_hint = f" (file: {c.file})" if c.file else ""
            lines.append(f"- ({c.severity}) {c.description}{file_hint}")
    for idx in getattr(group, "gap_indices", []):
        if idx < len(verdict.gaps):
            g = verdict.gaps[idx]
            lines.append(f"- ({g.severity}) {g.description} (category: {g.category})")
    return "\n".join(lines) if lines else to_str(verdict)


def _compute_fix_schedule(
    rcas: list[tuple[str, RootCauseAnalysis]],
) -> list[list[str]]:
    """Compute parallel-safe fix rounds using greedy graph coloring.

    Groups whose ``affected_files`` don't overlap can fix in the same round.
    Groups with overlapping files are placed in separate sequential rounds.
    """
    file_sets: dict[str, set[str]] = {
        gid: set(rca.affected_files) for gid, rca in rcas
    }
    remaining = set(file_sets.keys())
    schedule: list[list[str]] = []

    while remaining:
        round_ids: list[str] = []
        round_files: set[str] = set()
        for gid in sorted(remaining):
            if not file_sets[gid] & round_files:
                round_ids.append(gid)
                round_files |= file_sets[gid]
        schedule.append(round_ids)
        remaining -= set(round_ids)

    return schedule


def _format_prior_attempts(
    prior_attempts: list[BugFixAttempt],
    context_base: Path | None = None,
) -> str:
    """Format prior attempts as context for RCA/fix agents.

    When the formatted text exceeds *PROMPT_FILE_THRESHOLD* and a
    *context_base* is available, the full content is written to a file
    and a read-pointer is returned instead.
    """
    if not prior_attempts:
        return ""
    prior_lines = []
    for a in prior_attempts:
        prior_lines.append(
            f"### Attempt {a.attempt_number} ({a.bug_id})\n"
            f"- **Source:** {a.source_verdict}\n"
            f"- **Group:** {a.group_id or 'single'}\n"
            f"- **Description:** {a.description}\n"
            f"- **Root Cause:** {a.root_cause}\n"
            f"- **Fix Applied:** {a.fix_applied}\n"
            f"- **Files Modified:** {', '.join(a.files_modified)}\n"
            f"- **Result:** {a.re_verify_result}"
        )
    text = (
        "\n\n## Prior Fix Attempts (DO NOT REPEAT these approaches)\n\n"
        + "\n\n".join(prior_lines)
    )
    return _offload_if_large(text, context_base, "prior-fix-attempts")


def _get_feature_root(runner: WorkflowRunner, feature: Feature) -> Path | None:
    """Resolve the feature worktree root path."""
    workspace_mgr = runner.services.get("workspace_manager")
    if not workspace_mgr:
        return None
    root = Path(workspace_mgr._base) / ".iriai" / "features" / feature.slug / "repos"
    return root if root.exists() else None


async def _build_prompt_context_package(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    title: str,
    file_stem: str,
    intro_lines: list[str],
    sections: list[tuple[str, str, str]],
) -> ContextPackage | None:
    return await build_context_package(
        runner,
        feature,
        title=title,
        file_stem=file_stem,
        intro_lines=intro_lines,
        items=[
            ContextPackageItem(
                key=key,
                label=label,
                group="Prompt Context",
                content=content,
                file_name=f"{file_stem}-{key}.md",
            )
            for key, label, content in sections
            if content.strip()
        ],
    )


def _context_package_prompt(package: ContextPackage | None) -> str:
    if package is None:
        return ""
    return (
        f"Read the context index first: `{package.index_path}`\n"
        f"Then read the context manifest: `{package.manifest_path}`\n"
        "Open the referenced files selectively instead of loading everything eagerly.\n\n"
    )


@dataclass(frozen=True)
class DagVerifyLensSpec:
    slug: str
    label: str
    actor: AgentActor
    focus: str


def _dag_expanded_verify_enabled() -> bool:
    return _env_flag_enabled(DAG_EXPANDED_VERIFY_ENV, default=True)


def _dag_verify_lens_specs() -> list[DagVerifyLensSpec]:
    return [
        DagVerifyLensSpec(
            slug="build-dependency",
            label="Build & Dependency",
            actor=verifier,
            focus=(
                "Inspect clean install/build/typecheck/test dependency availability, "
                "package scripts, gulp/vite/playwright/pytest entrypoints, and import graph risks."
            ),
        ),
        DagVerifyLensSpec(
            slug="runtime-composition",
            label="Runtime Composition",
            actor=verifier,
            focus=(
                "Inspect DI, contribution registration, app factory wiring, router/preload/webview/"
                "sidebar lifecycle, and production consumption paths touched by this group."
            ),
        ),
        DagVerifyLensSpec(
            slug="contract-protocol",
            label="Contract & Protocol",
            actor=verifier,
            focus=(
                "Inspect REST/wire/event/API shapes, bridge ack preservation, fixture parity, "
                "and exception/status mapping for changed surfaces."
            ),
        ),
        DagVerifyLensSpec(
            slug="acceptance-coverage",
            label="Acceptance Coverage",
            actor=verifier,
            focus=(
                "Inspect owned acceptance criteria, task reference material, pass conditions, "
                "and current group verification gates for missing or weak coverage."
            ),
        ),
        DagVerifyLensSpec(
            slug="security-boundary",
            label="Security & Boundary",
            actor=security_auditor,
            focus=(
                "Inspect validation taxonomy, symlink/path traversal defenses, redaction, auth/"
                "nonce checks, markdown/image sanitizer handling, and secret leakage."
            ),
        ),
        DagVerifyLensSpec(
            slug="regression-downstream",
            label="Regression/Downstream",
            actor=regression_tester,
            focus=(
                "Inspect downstream consumers and must-not-break behavior around the surfaces "
                "changed by this group."
            ),
        ),
    ]


def _format_dag_group_task_specs(tasks: list[ImplementationTask]) -> str:
    if not tasks:
        return "_No task specs supplied._"
    parts: list[str] = []
    for task in tasks:
        parts.append(
            f"## {task.id} — {task.name}\n\n"
            "```json\n"
            f"{json.dumps(task.model_dump(mode='json'), indent=2, sort_keys=True)}\n"
            "```"
        )
    return "\n\n".join(parts)


def _safe_context_stem(text: str) -> str:
    stem = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text)
    stem = "-".join(part for part in stem.split("-") if part)
    return stem or "item"


async def _dag_legacy_contradiction_group_limit(
    runner: WorkflowRunner,
    feature: Feature,
) -> int:
    raw = await runner.artifacts.get("dag", feature=feature)
    if raw:
        try:
            dag = ImplementationDAG.model_validate_json(raw)
            return max(len(dag.execution_order) + 1, 10)
        except Exception:
            pass
    return 128


def _legacy_contradiction_record(
    artifact_key: str,
    raw: str,
) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    record: dict[str, Any] = {
        "artifact_key": artifact_key,
        "source": "legacy",
        "resolution": text,
        "authoritative_sources": [],
        "superseded_expectation": "",
        "implementation_direction": text,
        "requires_code_change": False,
        "confidence": "manual",
        "rationale": "",
    }
    try:
        data = json.loads(text)
    except Exception:
        return record
    if not isinstance(data, dict):
        return record
    revision_plan = data.get("revision_plan")
    requests = (
        revision_plan.get("requests", [])
        if isinstance(revision_plan, dict)
        else []
    )
    request_lines = [
        str(req.get("description", "")).strip()
        for req in requests
        if isinstance(req, dict) and str(req.get("description", "")).strip()
    ]
    new_decisions = (
        revision_plan.get("new_decisions", [])
        if isinstance(revision_plan, dict)
        else []
    )
    decision_lines = [
        str(item).strip()
        for item in new_decisions
        if str(item).strip()
    ]
    parts: list[str] = []
    if decision_lines:
        parts.append("New decisions:\n" + "\n".join(f"- {line}" for line in decision_lines))
    if request_lines:
        parts.append("Revision directions:\n" + "\n".join(f"- {line}" for line in request_lines))
    if parts:
        record["resolution"] = "\n\n".join(parts)
        record["implementation_direction"] = record["resolution"]
    return record


async def _load_contradiction_decision_records(
    runner: WorkflowRunner,
    feature: Feature,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    manifest_raw = await runner.artifacts.get(CONTRADICTION_DECISIONS_KEY, feature=feature)
    if manifest_raw:
        try:
            manifest = json.loads(manifest_raw)
            for item in manifest.get("decisions", []):
                if not isinstance(item, dict):
                    continue
                artifact_key = str(item.get("artifact_key", "") or "")
                if artifact_key and artifact_key in seen_keys:
                    continue
                if artifact_key:
                    seen_keys.add(artifact_key)
                records.append(item)
        except Exception:
            logger.debug("Failed to parse %s", CONTRADICTION_DECISIONS_KEY, exc_info=True)

    group_limit = await _dag_legacy_contradiction_group_limit(runner, feature)
    retry_limit = max(VERIFY_RETRIES + 3, 5)
    for group_idx in range(group_limit):
        for retry in range(retry_limit):
            suffix = f"dag-g{group_idx}-r{retry}"
            for prefix in ("contradiction:verify:", "contradiction:regression:"):
                artifact_key = f"{prefix}{suffix}"
                if artifact_key in seen_keys:
                    continue
                raw = await runner.artifacts.get(artifact_key, feature=feature)
                if not raw:
                    continue
                record = _legacy_contradiction_record(artifact_key, raw)
                if record is None:
                    continue
                seen_keys.add(artifact_key)
                records.append(record)

    return records


async def _format_contradiction_decisions_context(
    runner: WorkflowRunner,
    feature: Feature,
) -> str:
    records = await _load_contradiction_decision_records(runner, feature)
    if not records:
        return ""
    sections = [
        "## Resolved Contradiction Decisions (AUTHORITATIVE)",
        (
            "The following decisions resolve prior spec contradictions. They "
            "override conflicting task prose, reference material, and stale tests."
        ),
    ]
    for idx, record in enumerate(records, 1):
        artifact_key = str(record.get("artifact_key", "") or f"decision-{idx}")
        source = str(record.get("source", "") or "unknown")
        resolution = str(record.get("resolution", "") or "").strip()
        resolution_kind = str(record.get("resolution_kind", "") or "").strip()
        implementation_direction = str(
            record.get("implementation_direction", "") or ""
        ).strip()
        superseded = str(record.get("superseded_expectation", "") or "").strip()
        authoritative_sources = [
            str(item).strip()
            for item in record.get("authoritative_sources", []) or []
            if str(item).strip()
        ]
        lines = [f"### {idx}. `{artifact_key}`", f"- **Source:** {source}"]
        if resolution_kind:
            lines.append(f"- **Resolution kind:** {resolution_kind}")
        if resolution:
            lines.append(f"- **Resolution:** {resolution}")
        if implementation_direction and implementation_direction != resolution:
            lines.append(f"- **Implementation direction:** {implementation_direction}")
        if superseded:
            lines.append(f"- **Superseded expectation:** {superseded}")
        if authoritative_sources:
            lines.append(
                "- **Authoritative sources:** "
                + "; ".join(authoritative_sources)
            )
        sections.append("\n".join(lines))
    return "\n\n".join(sections).rstrip() + "\n"


def _dag_contradiction_resolution_usable(
    resolution: DagContradictionResolution,
) -> bool:
    return _validate_dag_contradiction_resolution(resolution).resolution is not None


_DAG_ARTIFACT_REPAIR_REF_RE = re.compile(
    r"`([^`]+)`|\"([^\"]+)\"|'([^']+)'|([A-Za-z0-9._~@:/\\-]+)"
)
_DAG_RESULT_REPORTED_FILE_RE = re.compile(
    r"\b([A-Za-z0-9][A-Za-z0-9_.-]*)\s+reports changed file\b"
)


def _is_dag_artifact_repair_path(path: str) -> bool:
    normalized = path.strip().replace("\\", "/")
    if not normalized:
        return True
    if normalized in {"dag.md"}:
        return True
    if normalized.startswith("(") and normalized.endswith(")"):
        return True
    artifact_markers = (
        "/.iriai/artifacts/features/",
        "/.iriai-context/",
        "/.staging/",
        "/dag/",
        "/dag-fragments/",
        "/outputs/dag-ws-",
        "/subfeatures/",
    )
    if any(marker in normalized for marker in artifact_markers):
        return True
    return normalized.startswith((
        ".iriai/",
        ".iriai-context/",
        ".staging/",
        "compile-",
        "dag/",
        "subfeatures/",
        "dag-fragments/",
        "dag-ws-",
        "outputs/dag-ws-",
    ))


def _normalize_dag_artifact_repair_ref(ref: str) -> str:
    normalized = ref.strip().replace("\\", "/")
    normalized = normalized.strip("`'\"")
    normalized = normalized.strip("[](){}<>")
    normalized = normalized.rstrip(".,;:")
    line_ref = re.match(r"^(.+\.[A-Za-z0-9]+):\d+(?::\d+)?$", normalized)
    if line_ref:
        normalized = line_ref.group(1)
    return normalized


def _is_dag_artifact_repair_key(ref: str) -> bool:
    normalized = ref.strip()
    if not normalized or "/" in normalized or "\\" in normalized:
        return False
    if _is_dag_task_artifact_key(normalized):
        return True
    top_level = {
        "artifact-audit-summary",
        "artifact-backfill-status",
        "context",
        "contradiction-decisions",
        "decisions",
        "decomposition",
        "decomposition-structured",
        "design",
        "dag",
        "plan",
        "prd",
        "scope",
        "system-design",
        "test-plan",
    }
    if normalized in top_level:
        return True
    if ":" not in normalized:
        return False
    prefix, slug = normalized.split(":", 1)
    if not slug:
        return False
    return prefix in {
        "artifact-audit",
        "dag",
        "dag-fragment",
        "dag-fragment-attempt",
        "dag-slices",
        "decisions",
        "decisions-structured",
        "design",
        "design-structured",
        "gate-enhancement-backlog",
        "gate-review-ledger",
        "planning-index",
        "plan",
        "plan-structured",
        "prd",
        "prd-structured",
        "system-design",
        "system-design-structured",
        "test-plan",
        "test-plan-structured",
    }


def _is_dag_task_artifact_key(ref: str) -> bool:
    normalized = ref.strip()
    if "/" in normalized or "\\" in normalized:
        return False
    return normalized.startswith("dag-task:") and bool(
        normalized.removeprefix("dag-task:").strip()
    )


def _dag_task_artifact_refs_from_reported_result_text(text: str) -> list[str]:
    refs: list[str] = []
    if not text:
        return refs
    for match in _DAG_RESULT_REPORTED_FILE_RE.finditer(text):
        task_id = match.group(1).strip("`'\"")
        ref = f"dag-task:{task_id}"
        if _is_dag_task_artifact_key(ref):
            refs.append(ref)
    return _dedupe_preserving_order(refs)


def _planned_non_dag_artifact_refs(
    planned: PlannedBugGroup,
) -> list[str]:
    return _dedupe_preserving_order([
        ref for ref in _dag_artifact_repair_refs_from_planned(planned)
        if not _is_dag_task_artifact_key(ref)
    ])


def _planned_needs_source_artifact_repair(
    planned: PlannedBugGroup,
) -> bool:
    refs = _planned_non_dag_artifact_refs(planned)
    if not refs:
        return False
    text = "\n".join([
        planned.group.likely_root_cause,
        planned.issue_text,
        planned.rca.hypothesis,
        planned.rca.proposed_approach,
        planned.rca.prior_attempt_analysis,
        planned.rca.contradiction_detail,
        "\n".join(planned.rca.evidence or []),
        "\n".join(planned.rca.alternative_hypotheses or []),
    ]).lower()
    source_markers = (
        "dag-fragment",
        "dag fragment",
        "compiled dag",
        "source artifact",
        "task spec",
        "task-spec",
        "file_scope",
        "files array",
        "workflow artifact",
        "orchestration metadata",
        "artifact/context",
        "outside workspace",
        "outside the workspace",
        "outside the write boundary",
        "regenerate",
        "recreate",
        "resurrect",
    )
    return any(marker in text for marker in source_markers) or (
        _dag_metadata_only_repair_candidate(planned)
        and _dag_artifact_repair_paths_safe(planned)
    )


def _dag_artifact_repair_refs_from_text(text: str) -> list[str]:
    refs: list[str] = []
    if not text:
        return refs
    for match in _DAG_ARTIFACT_REPAIR_REF_RE.findall(text):
        token = next((part for part in match if part), "")
        normalized = _normalize_dag_artifact_repair_ref(token)
        if not normalized:
            continue
        if _is_dag_artifact_repair_key(normalized):
            refs.append(normalized)
        elif _is_dag_artifact_repair_path(normalized):
            refs.append(normalized)
    return _dedupe_preserving_order(refs)


def _dag_artifact_repair_target_refs(
    resolution: DagContradictionResolution,
    *,
    planned: PlannedBugGroup | None = None,
) -> list[str]:
    refs: list[str] = []
    for ref in resolution.artifact_paths:
        normalized = _normalize_dag_artifact_repair_ref(ref)
        if normalized:
            refs.append(normalized)
    refs.extend(
        _dag_artifact_repair_refs_from_text(resolution.implementation_direction)
    )
    refs.extend(_dag_artifact_repair_refs_from_text(resolution.resolution))

    safe_refs = [
        ref for ref in refs
        if _is_dag_artifact_repair_key(ref) or _is_dag_artifact_repair_path(ref)
    ]
    if not safe_refs and planned is not None:
        planned_refs = [
            _normalize_dag_artifact_repair_ref(str(path))
            for path in [
                *(planned.rca.affected_files or []),
                *(getattr(planned.group, "affected_files_hint", []) or []),
            ]
        ]
        safe_planned_refs = [
            ref for ref in planned_refs
            if ref and (
                _is_dag_artifact_repair_key(ref)
                or _is_dag_artifact_repair_path(ref)
            )
        ]
        non_empty_planned_refs = [ref for ref in planned_refs if ref]
        if (
            safe_planned_refs
            and len(safe_planned_refs) == len(non_empty_planned_refs)
        ):
            safe_refs.extend(safe_planned_refs)
    return _dedupe_preserving_order(safe_refs)


def _dag_artifact_repair_paths_safe(planned: PlannedBugGroup) -> bool:
    paths = list(planned.rca.affected_files or [])
    paths.extend(getattr(planned.group, "affected_files_hint", []) or [])
    concrete_paths = [
        path for path in paths
        if str(path).strip() and not str(path).strip().startswith("(")
    ]
    return bool(concrete_paths) and all(
        _is_dag_artifact_repair_key(_normalize_dag_artifact_repair_ref(str(path)))
        or _is_dag_artifact_repair_path(str(path))
        for path in concrete_paths
    )


def _dag_affected_file_set(planned: PlannedBugGroup) -> set[str]:
    paths = list(planned.rca.affected_files or [])
    paths.extend(getattr(planned.group, "affected_files_hint", []) or [])
    return {
        str(path).strip().replace("\\", "/")
        for path in paths
        if str(path).strip() and not str(path).strip().startswith("(")
    }


def _dag_artifact_repair_refs_from_planned(
    planned: PlannedBugGroup,
) -> list[str]:
    refs: list[str] = []
    for path in [
        *(planned.rca.affected_files or []),
        *(getattr(planned.group, "affected_files_hint", []) or []),
    ]:
        normalized = _normalize_dag_artifact_repair_ref(str(path))
        if normalized and (
            _is_dag_artifact_repair_key(normalized)
            or _is_dag_artifact_repair_path(normalized)
        ):
            refs.append(normalized)

    text_parts = [
        planned.group.likely_root_cause,
        planned.issue_text,
        planned.rca.hypothesis,
        planned.rca.proposed_approach,
        planned.rca.prior_attempt_analysis,
        planned.rca.contradiction_detail,
        "\n".join(planned.rca.evidence or []),
        "\n".join(planned.rca.alternative_hypotheses or []),
    ]
    for text in text_parts:
        refs.extend(_dag_artifact_repair_refs_from_text(text or ""))
        refs.extend(_dag_task_artifact_refs_from_reported_result_text(text or ""))
    return _dedupe_preserving_order(refs)


def _dag_task_artifact_refs_from_planned(planned: PlannedBugGroup) -> list[str]:
    return _dedupe_preserving_order(
        [
            ref for ref in _dag_artifact_repair_refs_from_planned(planned)
            if _is_dag_task_artifact_key(ref)
        ]
    )


def _safe_dag_task_artifact_refs(refs: list[str]) -> list[str]:
    return _dedupe_preserving_order(
        [
            ref for ref in refs
            if _is_dag_task_artifact_key(ref)
        ]
    )


def _planned_needs_dag_task_artifact_repair(
    planned: PlannedBugGroup,
) -> bool:
    refs = _dag_task_artifact_refs_from_planned(planned)
    if not refs:
        return False
    text = "\n".join([
        planned.group.likely_root_cause,
        planned.issue_text,
        planned.rca.hypothesis,
        planned.rca.proposed_approach,
        planned.rca.prior_attempt_analysis,
        planned.rca.contradiction_detail,
        "\n".join(planned.rca.evidence or []),
        "\n".join(planned.rca.alternative_hypotheses or []),
    ]).lower()
    return any(marker in text for marker in (
        "stale",
        "persisted",
        "postgres",
        "db-backed",
        "database",
        "artifact row",
        "implementationresult",
        "files_created",
        "files_modified",
        "forbidden/stale",
    ))


def _dag_metadata_only_repair_candidate(planned: PlannedBugGroup) -> bool:
    """Return true when a fixable RCA should be artifact-repair routed."""
    refs = _dag_artifact_repair_refs_from_planned(planned)
    if not refs:
        return False
    text = "\n".join([
        planned.group.likely_root_cause,
        planned.issue_text,
        planned.rca.hypothesis,
        planned.rca.proposed_approach,
        planned.rca.prior_attempt_analysis,
        planned.rca.contradiction_detail,
        "\n".join(planned.rca.evidence or []),
    ]).lower()
    metadata_signal = any(marker in text for marker in (
        "metadata-only",
        "metadata only",
        "orchestration metadata",
        "orchestration-artifact",
        "workflow artifact",
        "artifact/context",
        "artifact repair",
        "manifest drift",
        "task spec",
        "task-spec",
        "changed-files",
        "implementation-results",
        "self_reported_risks",
        "stale risk",
    ))
    no_product_signal = any(marker in text for marker in (
        "not a code defect",
        "no code defect",
        "no source-tree edit",
        "no product-code",
        "no product code",
        "do not touch source",
        "do not modify production source",
        "do not modify product source",
        "do not touch any source code",
        "no source code",
        "not product code",
    ))
    boundary_signal = any(marker in text for marker in (
        "outside the write boundary",
        "outside workspace",
        "outside the workspace",
        "permission denied",
        "read-only for the agent",
        "meta layer",
        "orchestrator/meta",
        "orchestrator-layer",
        "host process",
    ))
    return metadata_signal and (
        no_product_signal
        or boundary_signal
        or _dag_artifact_repair_paths_safe(planned)
    )


def _dag_artifact_repair_resolution_from_planned(
    planned: PlannedBugGroup,
    *,
    reason: str,
    blocked_result: ImplementationResult | None = None,
) -> DagContradictionResolution:
    refs = _dag_artifact_repair_refs_from_planned(planned)
    notes = (
        f"\n\nBlocked implementer result:\n{to_str(blocked_result)}"
        if blocked_result is not None else ""
    )
    return DagContradictionResolution(
        resolution=(
            "Route this metadata-only DAG repair through the host-applied "
            f"artifact repair lane ({reason})."
        ),
        resolution_kind="artifact_repair",
        authoritative_sources=[
            planned.rca_key,
            *[item for item in planned.rca.evidence[:6] if item],
        ],
        artifact_paths=refs,
        implementation_direction=(
            f"{planned.rca.proposed_approach}{notes}"
        ).strip(),
        requires_code_change=False,
        needs_human=False,
        confidence=(
            planned.rca.confidence
            if planned.rca.confidence in {"high", "medium"}
            else "medium"
        ),
        rationale=(
            "The RCA identifies artifact/context metadata as the actionable "
            "target. Product files, when mentioned, are evidence only."
        ),
    )


def _dag_blocked_result_should_reroute_to_artifact_repair(
    planned: PlannedBugGroup,
    result: ImplementationResult,
) -> bool:
    if result.status != "blocked":
        return False
    if not _dag_artifact_repair_refs_from_planned(planned):
        return False
    text = "\n".join([
        result.summary,
        result.notes,
        "\n".join(r.description for r in result.self_reported_risks),
        planned.rca.hypothesis,
        planned.rca.proposed_approach,
        planned.rca.prior_attempt_analysis,
    ]).lower()
    return any(marker in text for marker in (
        "outside workspace",
        "outside the workspace",
        "outside the write boundary",
        "outside write boundary",
        "permission denied",
        "read-only",
        "artifact",
        "metadata",
        ".iriai-context",
        ".iriai/artifacts",
        "orchestrator",
    ))


def _validate_dag_contradiction_resolution(
    resolution: DagContradictionResolution | None,
    *,
    planned: PlannedBugGroup | None = None,
) -> DagContradictionResolutionValidation:
    if resolution is None:
        return DagContradictionResolutionValidation(
            resolution=None,
            rejection_reasons=["resolver_returned_no_resolution"],
        )

    rejection_reasons: list[str] = []
    resolution_text = resolution.resolution.strip()
    sources = [
        item.strip() for item in resolution.authoritative_sources if item.strip()
    ]
    confidence = resolution.confidence.strip().lower()
    kind = resolution.resolution_kind.strip().lower()
    valid_kinds = {
        "decision_only",
        "requires_code_change",
        "artifact_repair",
        "stale_not_reproducing",
        "needs_human",
    }
    if not kind:
        kind = (
            "requires_code_change"
            if resolution.requires_code_change
            else "decision_only"
        )
    if resolution.needs_human:
        kind = "needs_human"
    elif resolution.requires_code_change and kind == "decision_only":
        kind = "requires_code_change"
    if kind not in valid_kinds:
        rejection_reasons.append(f"unknown_resolution_kind:{kind}")
    if resolution.needs_human or kind == "needs_human":
        rejection_reasons.append("needs_human")
    if not resolution_text:
        rejection_reasons.append("missing_resolution")
    if not sources:
        rejection_reasons.append("missing_authoritative_sources")
    if confidence == "contradiction":
        confidence = "medium"
    elif confidence not in {"high", "medium", "low"}:
        rejection_reasons.append(f"unknown_confidence:{confidence}")
    if confidence == "low":
        rejection_reasons.append("low_confidence")
    if kind == "artifact_repair" and planned is not None:
        artifact_refs = _dag_artifact_repair_target_refs(resolution, planned=planned)
        unsafe_structured_refs = [
            _normalize_dag_artifact_repair_ref(ref)
            for ref in resolution.artifact_paths
            if ref.strip()
            and not (
                _is_dag_artifact_repair_key(
                    _normalize_dag_artifact_repair_ref(ref)
                )
                or _is_dag_artifact_repair_path(
                    _normalize_dag_artifact_repair_ref(ref)
                )
            )
        ]
        if not artifact_refs or unsafe_structured_refs:
            rejection_reasons.append("artifact_repair_has_non_artifact_paths")
    else:
        artifact_refs = [
            _normalize_dag_artifact_repair_ref(ref)
            for ref in resolution.artifact_paths
            if ref.strip()
        ]

    if rejection_reasons:
        return DagContradictionResolutionValidation(
            resolution=None,
            rejection_reasons=_dedupe_preserving_order(rejection_reasons),
        )

    normalized = resolution.model_copy(update={
        "resolution_kind": kind,
        "authoritative_sources": sources,
        "artifact_paths": artifact_refs,
        "confidence": confidence,
        "needs_human": False,
        "requires_code_change": kind == "requires_code_change",
    })
    return DagContradictionResolutionValidation(
        resolution=normalized,
        rejection_reasons=[],
    )


def _dag_contradiction_groups_overlap(
    planned: PlannedBugGroup,
    quarantined_groups: list[PlannedBugGroup],
) -> bool:
    planned_paths = _dag_affected_file_set(planned)
    if not planned_paths:
        return False
    for quarantined in quarantined_groups:
        quarantined_paths = _dag_affected_file_set(quarantined)
        if quarantined_paths and planned_paths & quarantined_paths:
            return True
    return False


async def _persist_dag_contradiction_rejection(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry: int,
    planned: PlannedBugGroup,
    resolution: DagContradictionResolution | None,
    rejection_reasons: list[str],
) -> dict[str, Any]:
    safe_group = _safe_context_stem(planned.group.group_id)
    artifact_key = (
        f"contradiction-rejected:dag-repair:g{group_idx}:retry-{retry}:{safe_group}"
    )
    record = {
        "artifact_key": artifact_key,
        "source": "dag-repair",
        "group_idx": group_idx,
        "retry": retry,
        "group_id": planned.group.group_id,
        "rca_key": planned.rca_key,
        "rejection_reasons": list(rejection_reasons),
        "raw_resolution": (
            resolution.model_dump(mode="json") if resolution is not None else None
        ),
        "created_at": time.time(),
    }
    await runner.artifacts.put(
        artifact_key,
        json.dumps(record, indent=2),
        feature=feature,
    )
    return record


def _dag_contradiction_needs_fix(resolution: DagContradictionResolution) -> bool:
    return _dag_contradiction_needs_product_fix(resolution)


def _dag_contradiction_needs_product_fix(
    resolution: DagContradictionResolution,
) -> bool:
    return resolution.resolution_kind == "requires_code_change"


def _dag_contradiction_needs_artifact_repair(
    resolution: DagContradictionResolution,
) -> bool:
    return resolution.resolution_kind == "artifact_repair"


def _dag_contradiction_synthetic_result(
    group_idx: int,
    retry: int,
    planned: PlannedBugGroup,
    resolution: DagContradictionResolution,
    record: dict[str, Any],
) -> ImplementationResult:
    return ImplementationResult(
        task_id=(
            f"CONTRADICTION-g{group_idx}-r{retry}-"
            f"{_safe_context_stem(planned.group.group_id)}"
        ),
        summary=(
            "Autonomous contradiction decision applied "
            f"({resolution.resolution_kind}): {resolution.resolution}"
        ),
        files_modified=[],
        notes=json.dumps(record, indent=2),
    )


def _dag_contradiction_fix_guidance(
    resolution: DagContradictionResolution,
) -> str:
    return (
        f"{resolution.implementation_direction or resolution.resolution}\n\n"
        "Authoritative contradiction resolution:\n"
        f"{resolution.resolution}\n\n"
        f"Resolution kind: {resolution.resolution_kind}\n\n"
        "Superseded expectation:\n"
        f"{resolution.superseded_expectation or 'not specified'}"
    )


def _dag_artifact_repair_workspace(
    runner: WorkflowRunner,
    feature: Feature,
    feature_root: Path | None,
) -> str | None:
    mirror = (getattr(runner, "services", {}) or {}).get("artifact_mirror")
    if mirror is not None:
        try:
            return str(Path(mirror.feature_dir(feature.id)))
        except Exception:
            logger.debug(
                "Failed to resolve artifact mirror repair workspace",
                exc_info=True,
            )
    return str(feature_root) if feature_root is not None else None


def _dag_artifact_key_for_repair_file(
    runner: WorkflowRunner,
    feature: Feature,
    reported_path: str,
) -> tuple[str | None, Path | None]:
    mirror = (getattr(runner, "services", {}) or {}).get("artifact_mirror")
    if mirror is None:
        return None, None
    try:
        feature_dir = Path(mirror.feature_dir(feature.id)).resolve()
    except Exception:
        return None, None

    normalized = _normalize_dag_artifact_repair_ref(reported_path)
    if not normalized or _is_artifact_context_path(normalized):
        return None, None
    candidate = Path(normalized)
    try:
        if candidate.is_absolute():
            absolute = candidate.resolve()
            relative = absolute.relative_to(feature_dir)
        else:
            parts = candidate.parts
            feature_parts = (".iriai", "artifacts", "features", feature.id)
            if parts[:4] == feature_parts:
                relative = Path(*parts[4:])
                absolute = feature_dir / relative
            else:
                relative = candidate
                absolute = feature_dir / relative
    except Exception:
        return None, None
    if not absolute.exists() or not absolute.is_file():
        return None, absolute
    try:
        from ....services.artifacts import _path_to_key

        artifact_key = _path_to_key(relative)
    except Exception:
        artifact_key = None
    if artifact_key and _is_dag_artifact_repair_key(artifact_key):
        return artifact_key, absolute
    return None, absolute


def _path_relative_to(path: Path, root: Path) -> Path | None:
    try:
        return path.resolve().relative_to(root.resolve())
    except Exception:
        return None


_DAG_CLOSURE_TEXT_SUFFIXES = {
    ".json",
    ".md",
    ".markdown",
    ".txt",
    ".html",
    ".htm",
    ".yaml",
    ".yml",
}
_DAG_CLOSURE_MAX_SCAN_BYTES = 3_000_000
_DAG_CLOSURE_MAX_IGNORED_MATCHES = 250
_DAG_CLOSURE_BLOCKING_REASONS = {
    "forbidden",
    "forbidden_task_result",
    "forbidden_task_spec",
    "forbidden_task_spec_source_artifact",
    "forbidden_workspace_path",
    "manifest_forbidden_workspace_path",
}


def _dag_artifact_feature_dir(
    runner: WorkflowRunner,
    feature: Feature,
) -> Path | None:
    mirror = (getattr(runner, "services", {}) or {}).get("artifact_mirror")
    if mirror is None:
        return None
    try:
        return Path(mirror.feature_dir(feature.id)).resolve()
    except Exception:
        logger.debug(
            "Failed to resolve DAG artifact closure feature dir",
            exc_info=True,
        )
        return None


def _dag_closure_normalize_path(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    normalized = normalized.strip("`'\"")
    normalized = normalized.strip("[](){}<>")
    normalized = normalized.rstrip(".,;:")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _dag_closure_signature_variants(value: str) -> list[str]:
    normalized = _dag_closure_normalize_path(value)
    if not normalized:
        return []
    variants = {normalized}
    if "/.iriai/artifacts/features/" in normalized:
        marker = "/.iriai/artifacts/features/"
        suffix = normalized.split(marker, 1)[1]
        parts = suffix.split("/", 1)
        if len(parts) == 2 and parts[1]:
            variants.add(parts[1])
    parts = normalized.split("/")
    if "src" in parts:
        src_index = parts.index("src")
        src_relative = "/".join(parts[src_index:])
        if src_relative:
            variants.add(src_relative)
            variants.add(f"iriai-studio/{src_relative}")
    if normalized.startswith("iriai-studio/"):
        variants.add(normalized.removeprefix("iriai-studio/"))
    if normalized.startswith("iriai-studio-backend/"):
        variants.add(normalized.removeprefix("iriai-studio-backend/"))
    canonical, rule = canonicalize_dag_path(normalized)
    if rule and canonical != normalized:
        variants.add(normalized)
    return sorted(variant for variant in variants if variant)


def _dag_closure_problem_is_blocking(problem: dict[str, Any]) -> bool:
    reason = str(problem.get("reason", "") or "").strip()
    if reason not in _DAG_CLOSURE_BLOCKING_REASONS:
        return False
    if reason == "forbidden" and not (
        str(problem.get("forbidden_rule", "") or "").strip()
        or str(problem.get("forbidden_path", "") or "").strip()
    ):
        return False
    return True


def _dag_closure_record_kind(field: str, *, blocking: bool) -> str:
    if not blocking:
        return (
            "candidate_evidence"
            if field == "candidate_evidence"
            else "diagnostic_path"
        )
    if field in {"forbidden_path", "forbidden_rule"}:
        return "forbidden_prefix"
    return "retired_path"


def _dag_closure_signature_records_from_path_problems(
    path_problems: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, bool]] = set()

    def _add_records(
        *,
        value: str,
        field: str,
        problem: dict[str, Any],
        blocking: bool,
    ) -> None:
        for signature in _dag_closure_signature_variants(value):
            if not signature.strip():
                continue
            key = (
                signature,
                field,
                str(problem.get("reason", "") or ""),
                str(problem.get("artifact_key", "") or ""),
                blocking,
            )
            if key in seen:
                continue
            seen.add(key)
            records.append({
                "signature": signature,
                "kind": _dag_closure_record_kind(field, blocking=blocking),
                "source_field": field,
                "source_reason": str(problem.get("reason", "") or ""),
                "source_task_id": str(problem.get("task_id", "") or ""),
                "source_artifact_key": str(problem.get("artifact_key", "") or ""),
                "source_artifact_ref": str(
                    problem.get("source_artifact_ref", "") or ""
                ),
                "blocking": blocking,
            })

    for problem in path_problems or []:
        blocking = _dag_closure_problem_is_blocking(problem)
        fields = (
            ("path", str(problem.get("path", "") or "")),
            ("forbidden_path", str(problem.get("forbidden_path", "") or "")),
            ("forbidden_rule", str(problem.get("forbidden_rule", "") or "")),
        )
        for field, value in fields:
            _add_records(
                value=value,
                field=field,
                problem=problem,
                blocking=blocking,
            )
        for candidate in problem.get("candidate_evidence") or []:
            if isinstance(candidate, dict):
                _add_records(
                    value=str(candidate.get("path", "") or ""),
                    field="candidate_evidence",
                    problem=problem,
                    blocking=False,
                )
    return records


def _dag_closure_blocking_signatures(
    signature_records: list[dict[str, Any]],
) -> list[str]:
    return _dedupe_preserving_order([
        str(record.get("signature", "") or "")
        for record in signature_records
        if record.get("blocking") and str(record.get("signature", "") or "").strip()
    ])


def _dag_closure_task_ref_parts(
    task_id: str,
) -> tuple[str, str]:
    match = re.search(r"^(.+?)-slice-(\d+)\b", task_id)
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def _dag_closure_affected_context(
    group_tasks: list[ImplementationTask],
    path_problems: list[dict[str, Any]] | None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    task_ids: list[str] = []
    subfeatures: list[str] = []
    slices: list[str] = []
    source_refs: list[str] = []
    tasks_by_id = {task.id: task for task in group_tasks}
    for problem in path_problems or []:
        for key in ("artifact_key", "source_artifact_ref"):
            ref = str(problem.get(key, "") or "").strip()
            if ref:
                source_refs.append(ref)
        task_id = str(problem.get("task_id", "") or "").strip()
        if not task_id:
            artifact_key = str(problem.get("artifact_key", "") or "")
            if artifact_key.startswith("dag-task:"):
                task_id = artifact_key.removeprefix("dag-task:")
        if task_id:
            task_ids.append(task_id)
            task = tasks_by_id.get(task_id)
            if task is not None and task.subfeature_id:
                subfeatures.append(task.subfeature_id)
            inferred_subfeature, inferred_slice = _dag_closure_task_ref_parts(task_id)
            if inferred_subfeature:
                subfeatures.append(inferred_subfeature)
            if inferred_slice:
                slices.append(f"slice-{inferred_slice}")
        source_ref = str(problem.get("source_artifact_ref", "") or "")
        match = re.search(r"dag-fragment:([^:]+):slice-(\d+)", source_ref)
        if match:
            subfeatures.append(match.group(1))
            slices.append(f"slice-{match.group(2)}")

    if not task_ids:
        for task in group_tasks:
            task_ids.append(task.id)
            if task.subfeature_id:
                subfeatures.append(task.subfeature_id)
            inferred_subfeature, inferred_slice = _dag_closure_task_ref_parts(task.id)
            if inferred_subfeature:
                subfeatures.append(inferred_subfeature)
            if inferred_slice:
                slices.append(f"slice-{inferred_slice}")
            source_ref = _dag_fragment_artifact_ref_for_task(task)
            if source_ref:
                source_refs.append(source_ref)

    return (
        _dedupe_preserving_order(task_ids),
        _dedupe_preserving_order(subfeatures),
        _dedupe_preserving_order(slices),
        _dedupe_preserving_order(source_refs),
    )


def _dag_planned_uses_full_stale_closure(planned: PlannedBugGroup) -> bool:
    return _safe_context_stem(planned.group.group_id) == "dag-stale-forbidden-paths"


def _dag_closure_planned_text(planned: PlannedBugGroup) -> str:
    return "\n".join([
        planned.group.group_id,
        planned.group.likely_root_cause,
        planned.issue_text,
        planned.rca.hypothesis,
        planned.rca.proposed_approach,
        planned.rca.prior_attempt_analysis,
        planned.rca.contradiction_detail,
        "\n".join(planned.rca.evidence or []),
        "\n".join(planned.rca.alternative_hypotheses or []),
        "\n".join(planned.rca.affected_files or []),
        "\n".join(getattr(planned.group, "affected_files_hint", []) or []),
    ])


def _dag_closure_path_problems_for_planned(
    planned: PlannedBugGroup,
    verifier_path_problems: list[dict[str, Any]] | None,
    group_tasks: list[ImplementationTask],
) -> list[dict[str, Any]]:
    problems = list(verifier_path_problems or [])
    if not problems:
        return []
    if _dag_planned_uses_full_stale_closure(planned):
        return problems

    def _source_ref_from_repair_ref(ref: str) -> str:
        if ref.startswith("dag-fragment:"):
            return ref
        normalized = _dag_closure_normalize_path(ref)
        match = re.search(
            r"(?:^|/)subfeatures/([^/]+)/dag-fragments/slice-(\d+)\.json$",
            normalized,
        )
        if not match:
            return ""
        return f"dag-fragment:{match.group(1)}:slice-{match.group(2)}"

    planned_text = _dag_closure_planned_text(planned)
    planned_text_lower = planned_text.lower()
    refs = _dag_artifact_repair_refs_from_planned(planned)
    artifact_keys = {
        ref for ref in refs
        if _is_dag_task_artifact_key(ref)
    }
    source_refs = {
        ref for ref in refs
        if ref.startswith("dag-fragment:")
    }
    source_refs.update(
        ref for ref in (_source_ref_from_repair_ref(ref) for ref in refs)
        if ref
    )
    task_ids = {
        ref.removeprefix("dag-task:")
        for ref in artifact_keys
    }
    for task in group_tasks:
        if task.id and task.id.lower() in planned_text_lower:
            task_ids.add(task.id)
        source_ref = _dag_fragment_artifact_ref_for_task(task)
        if source_ref and source_ref.lower() in planned_text_lower:
            source_refs.add(source_ref)
        if source_ref and source_ref in source_refs:
            task_ids.add(task.id)

    scoped: list[dict[str, Any]] = []
    seen_problem_ids: set[int] = set()

    def _append(problem: dict[str, Any]) -> None:
        identity = id(problem)
        if identity in seen_problem_ids:
            return
        seen_problem_ids.add(identity)
        scoped.append(problem)

    for problem in problems:
        task_id = str(problem.get("task_id", "") or "")
        artifact_key = str(problem.get("artifact_key", "") or "")
        source_ref = str(problem.get("source_artifact_ref", "") or "")
        path_values = [
            str(problem.get(key, "") or "")
            for key in ("path", "forbidden_path", "forbidden_rule")
        ]
        if artifact_key and artifact_key in artifact_keys:
            _append(problem)
            continue
        if task_id and task_id in task_ids:
            _append(problem)
            continue
        if source_ref and source_ref in source_refs:
            _append(problem)
            continue
        if artifact_key.startswith("dag-task:") and (
            artifact_key.removeprefix("dag-task:") in task_ids
        ):
            _append(problem)
            continue
        if any(
            value
            and _dag_closure_normalize_path(value).lower() in planned_text_lower
            for value in path_values
        ):
            _append(problem)
    return scoped


def _dag_closure_relative_path(path: Path, artifact_root: Path) -> str:
    try:
        return path.resolve().relative_to(artifact_root.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _dag_closure_is_text_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith(".DS_Store"):
        return False
    if path.suffix.lower() not in _DAG_CLOSURE_TEXT_SUFFIXES:
        return False
    try:
        return path.stat().st_size <= _DAG_CLOSURE_MAX_SCAN_BYTES
    except OSError:
        return False


def _dag_closure_read_text(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    except Exception:
        logger.debug("Failed to read DAG closure candidate %s", path, exc_info=True)
        return ""


def _dag_closure_contains_affected_ref(
    text: str,
    *,
    affected_task_ids: list[str],
    affected_subfeatures: list[str],
    affected_slices: list[str],
) -> bool:
    lowered = text.lower()
    for value in [*affected_task_ids, *affected_subfeatures, *affected_slices]:
        if value and value.lower() in lowered:
            return True
    return False


def _dag_closure_class_for_path(
    rel_path: str,
    *,
    group_idx: int,
) -> tuple[str, bool]:
    rel = rel_path.strip().replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.strip("/")
    if rel == "dag.md":
        return "feature_dag", True
    if rel.startswith("compile-") and "dag" in rel and rel.endswith(".md"):
        return "compile_dag", True
    if rel.startswith("dag-ws-"):
        return "root_workspace_dag", True
    if rel.startswith("dag/dag-ws-"):
        return "nested_workspace_dag", True
    if rel.startswith("outputs/dag-ws-"):
        return "output_workspace_dag", True
    if rel.startswith(f".iriai-context/g{group_idx}-expanded-verify-"):
        return "expanded_verify_snapshot", True
    if rel.startswith(".iriai-context/"):
        return "generated_context", False
    if rel.startswith(".staging/"):
        return "staging_artifact", False
    parts = rel.split("/")
    if len(parts) >= 3 and parts[0] == "subfeatures":
        suffix = "/".join(parts[2:])
        if suffix == "dag.md":
            return "subfeature_dag", True
        if suffix.startswith("dag-fragments/") and suffix.endswith(".json"):
            return "dag_fragment", True
        if suffix in {"plan.md", "plan.json"}:
            return "subfeature_plan", True
        if suffix.startswith("dag-fragment-attempts/"):
            return "dag_fragment_attempt", False
        if suffix.startswith((
            "prd",
            "design",
            "system-design",
            "test-plan",
        )):
            return "historical_planning_doc", False
        return "subfeature_artifact", False
    return "historical_artifact", False


def _dag_artifact_closure_scan(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    group_tasks: list[ImplementationTask],
    path_problems: list[dict[str, Any]] | None,
) -> DagArtifactClosureScan:
    signature_records = _dag_closure_signature_records_from_path_problems(
        path_problems
    )
    signatures = _dag_closure_blocking_signatures(signature_records)
    task_ids, subfeatures, slices, source_refs = _dag_closure_affected_context(
        group_tasks,
        path_problems,
    )
    artifact_root = _dag_artifact_feature_dir(runner, feature)
    scan = DagArtifactClosureScan(
        stale_signatures=signatures,
        signature_records=signature_records,
        affected_task_ids=task_ids,
        affected_subfeatures=subfeatures,
        affected_slices=slices,
        source_refs=source_refs,
        suggested_scan_roots=[
            "subfeatures/{subfeature}/dag.md",
            "subfeatures/{subfeature}/dag-fragments/*.json",
            "subfeatures/{subfeature}/plan.md",
            "subfeatures/{subfeature}/plan.json",
            "dag.md",
            "dag-ws-*",
            "dag/dag-ws-*",
            "outputs/dag-ws-*",
            "compile-*dag*.md",
            f".iriai-context/g{group_idx}-expanded-verify-*",
        ],
    )
    if not signatures or artifact_root is None or not artifact_root.exists():
        return scan

    for path in sorted(artifact_root.rglob("*")):
        if not _dag_closure_is_text_file(path):
            continue
        rel_path = _dag_closure_relative_path(path, artifact_root)
        scan.scanned_paths.append(rel_path)
        text = _dag_closure_read_text(path)
        if not text:
            continue
        matched_records = [
            record for record in signature_records
            if str(record.get("signature", "") or "")
            and str(record.get("signature", "")) in text
        ]
        if not matched_records:
            continue
        blocking_records = [
            record for record in matched_records
            if bool(record.get("blocking"))
        ]
        ignored_records = [
            record for record in matched_records
            if not bool(record.get("blocking"))
        ]
        artifact_class, blocking = _dag_closure_class_for_path(
            rel_path,
            group_idx=group_idx,
        )
        contains_affected_ref = _dag_closure_contains_affected_ref(
            text,
            affected_task_ids=task_ids,
            affected_subfeatures=subfeatures,
            affected_slices=slices,
        )
        if (
            ignored_records
            and len(scan.ignored_matches) < _DAG_CLOSURE_MAX_IGNORED_MATCHES
        ):
            scan.ignored_matches.append({
                "target_ref": str(path),
                "relative_path": rel_path,
                "artifact_class": artifact_class,
                "signatures": [
                    str(record.get("signature", "") or "")
                    for record in ignored_records
                ],
                "signature_records": ignored_records,
                "contains_affected_ref": contains_affected_ref,
                "ignored_reason": "non_blocking_closure_evidence",
            })
        if not blocking_records:
            continue
        item = {
            "target_ref": str(path),
            "relative_path": rel_path,
            "artifact_class": artifact_class,
            "stale_signatures": _dedupe_preserving_order([
                str(record.get("signature", "") or "")
                for record in blocking_records
                if str(record.get("signature", "") or "").strip()
            ]),
            "signature_records": blocking_records,
            "contains_affected_ref": contains_affected_ref,
        }
        if blocking:
            scan.blocking_targets.append(item)
        else:
            scan.advisory_residuals.append(item)
    return scan


def _dag_closure_record(
    *,
    artifact_key: str,
    group_idx: int,
    retry: int,
    group_id: str,
    before: DagArtifactClosureScan,
    after: DagArtifactClosureScan | None,
    status: str,
    deleted_snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "artifact_key": artifact_key,
        "source": "dag-artifact-closure",
        "group_idx": group_idx,
        "retry": retry,
        "group_id": group_id,
        "status": status,
        "before": before.to_record(),
        "after": after.to_record() if after is not None else None,
        "blocking_residuals": (
            after.blocking_targets if after is not None else before.blocking_targets
        ),
        "advisory_residuals": (
            after.advisory_residuals if after is not None else before.advisory_residuals
        ),
        "deleted_generated_snapshots": deleted_snapshots or [],
        "created_at": time.time(),
    }


def _dag_artifact_repair_target_path(
    runner: WorkflowRunner,
    feature: Feature,
    target_ref: str,
    feature_root: Path | None,
) -> tuple[Path | None, str, str]:
    normalized = _normalize_dag_artifact_repair_ref(target_ref)
    if not normalized:
        return None, "empty_target_ref", ""
    if not _is_dag_artifact_repair_path(normalized):
        return None, "target_ref_not_artifact_context", normalized

    services = getattr(runner, "services", {}) or {}
    mirror = services.get("artifact_mirror")
    mirror_root: Path | None = None
    if mirror is not None:
        try:
            mirror_root = Path(mirror.feature_dir(feature.id)).resolve()
        except Exception:
            logger.debug(
                "Failed to resolve artifact mirror target root",
                exc_info=True,
            )

    roots: list[tuple[str, Path]] = []
    if mirror_root is not None:
        roots.append(("artifact_mirror", mirror_root))
    if feature_root is not None:
        roots.append(("feature_context", feature_root.resolve()))

    candidate = Path(normalized)
    if candidate.is_absolute():
        absolute = candidate.resolve()
        for kind, root in roots:
            rel = _path_relative_to(absolute, root)
            if rel is None:
                continue
            if kind == "feature_context" and not (
                _is_artifact_context_path(rel.as_posix())
                or _is_artifact_context_path(absolute.as_posix())
            ):
                return None, "target_ref_not_feature_context", normalized
            return absolute, kind, rel.as_posix()
        return None, "target_ref_outside_allowed_roots", normalized

    parts = candidate.parts
    feature_parts = (".iriai", "artifacts", "features", feature.id)
    if mirror_root is not None and parts[:4] == feature_parts:
        relative = Path(*parts[4:]) if len(parts) > 4 else Path()
        absolute = (mirror_root / relative).resolve()
        if _path_relative_to(absolute, mirror_root) is None:
            return None, "target_ref_outside_artifact_mirror", normalized
        return absolute, "artifact_mirror", relative.as_posix()

    if mirror_root is not None and (
        normalized == "dag.md"
        or normalized.startswith((
            ".iriai-context/",
            ".staging/",
            "compile-",
            "dag/",
            "dag-fragments/",
            "dag-ws-",
            "outputs/dag-ws-",
            "subfeatures/",
        ))
    ):
        absolute = (mirror_root / candidate).resolve()
        if _path_relative_to(absolute, mirror_root) is None:
            return None, "target_ref_outside_artifact_mirror", normalized
        return absolute, "artifact_mirror", candidate.as_posix()

    if feature_root is not None and _is_artifact_context_path(normalized):
        absolute = (feature_root.resolve() / candidate).resolve()
        if _path_relative_to(absolute, feature_root.resolve()) is None:
            return None, "target_ref_outside_feature_context", normalized
        return absolute, "feature_context", candidate.as_posix()

    return None, "target_ref_unsupported_artifact_context", normalized


def _prune_empty_artifact_parents(path: Path, stop_root: Path) -> None:
    parent = path.parent
    stop = stop_root.resolve()
    while True:
        try:
            resolved_parent = parent.resolve()
        except Exception:
            return
        if resolved_parent == stop:
            return
        if _path_relative_to(resolved_parent, stop) is None:
            return
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def _dag_artifact_delete_allowed(normalized_ref: str) -> bool:
    normalized = normalized_ref.strip().replace("\\", "/")
    return normalized.startswith((".iriai-context/", ".staging/"))


async def _apply_dag_artifact_repair_updates(
    runner: WorkflowRunner,
    feature: Feature,
    result: ArtifactRepairResult,
    feature_root: Path | None = None,
) -> dict[str, Any]:
    services = getattr(runner, "services", {}) or {}
    mirror = services.get("artifact_mirror")
    applied_updates: list[dict[str, Any]] = []
    skipped_updates: list[dict[str, Any]] = []
    applied_target_updates: list[dict[str, Any]] = []
    deleted_artifacts: list[dict[str, Any]] = []
    skipped_deletes: list[dict[str, Any]] = []
    roots = _dag_candidate_file_roots(feature_root)

    for update in result.artifact_updates:
        artifact_key = update.artifact_key.strip()
        target_ref = update.target_ref.strip()
        if not artifact_key and not target_ref:
            skipped_updates.append({
                "artifact_key": artifact_key,
                "target_ref": target_ref,
                "reason": "missing_artifact_target",
            })
            continue
        if not update.content.strip():
            skipped_updates.append({
                "artifact_key": artifact_key,
                "target_ref": target_ref,
                "reason": "empty_content",
            })
            continue
        if artifact_key:
            if _is_dag_task_artifact_key(artifact_key):
                task_result, reason, validation = _validate_dag_task_artifact_update(
                    artifact_key,
                    update.content,
                    roots,
                    feature_root,
                )
                if task_result is None:
                    skipped_updates.append({
                        "artifact_key": artifact_key,
                        "target_ref": target_ref,
                        "reason": reason,
                        "validation": validation,
                    })
                else:
                    stored_content = task_result.model_dump_json()
                    await runner.artifacts.put(
                        artifact_key,
                        stored_content,
                        feature=feature,
                    )
                    applied_updates.append({
                        "artifact_key": artifact_key,
                        "artifact_kind": "dag_task",
                        "task_id": task_result.task_id,
                        "summary": update.summary,
                        "bytes": len(stored_content.encode("utf-8")),
                        "validated_paths": validation,
                    })
            elif not _is_dag_artifact_repair_key(artifact_key):
                skipped_updates.append({
                    "artifact_key": artifact_key,
                    "target_ref": target_ref,
                    "reason": "unsafe_artifact_key",
                })
            else:
                await runner.artifacts.put(artifact_key, update.content, feature=feature)
                if mirror is not None and hasattr(mirror, "write_artifact"):
                    try:
                        mirror.write_artifact(feature.id, artifact_key, update.content)
                    except Exception:
                        logger.warning(
                            "Failed to mirror artifact repair update for %s",
                            artifact_key,
                            exc_info=True,
                        )
                applied_updates.append({
                    "artifact_key": artifact_key,
                    "summary": update.summary,
                    "bytes": len(update.content.encode("utf-8")),
                })
        if target_ref:
            target_path, target_kind, normalized_ref = (
                _dag_artifact_repair_target_path(
                    runner,
                    feature,
                    target_ref,
                    feature_root,
                )
            )
            if target_path is None:
                skipped_updates.append({
                    "artifact_key": artifact_key,
                    "target_ref": target_ref,
                    "reason": target_kind,
                    "normalized_ref": normalized_ref,
                })
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(update.content, encoding="utf-8")
                applied_target_updates.append({
                    "target_ref": target_ref,
                    "target_kind": target_kind,
                    "path": str(target_path),
                    "summary": update.summary,
                    "bytes": len(update.content.encode("utf-8")),
                })

    synced_files: list[dict[str, str]] = []
    for reported_path in result.artifacts_created + result.artifacts_modified:
        artifact_key, path = _dag_artifact_key_for_repair_file(
            runner,
            feature,
            reported_path,
        )
        if not artifact_key or path is None or not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            logger.warning(
                "Failed to read repaired artifact file %s",
                path,
                exc_info=True,
            )
            continue
        await runner.artifacts.put(artifact_key, text, feature=feature)
        synced_files.append({
            "artifact_key": artifact_key,
            "path": str(path),
        })

    for target_ref in result.artifacts_deleted:
        target_path, target_kind, normalized_ref = _dag_artifact_repair_target_path(
            runner,
            feature,
            target_ref,
            feature_root,
        )
        if target_path is None:
            skipped_deletes.append({
                "target_ref": target_ref,
                "reason": target_kind,
                "normalized_ref": normalized_ref,
            })
            continue
        if target_path.exists() and not target_path.is_file():
            skipped_deletes.append({
                "target_ref": target_ref,
                "reason": "target_ref_not_file",
                "normalized_ref": normalized_ref,
                "path": str(target_path),
            })
            continue
        if not _dag_artifact_delete_allowed(normalized_ref):
            skipped_deletes.append({
                "target_ref": target_ref,
                "reason": "target_ref_delete_not_generated_or_staging_artifact",
                "normalized_ref": normalized_ref,
                "path": str(target_path),
            })
            continue
        if target_path.exists():
            target_path.unlink()
            stop_root = (
                _dag_artifact_feature_dir(runner, feature)
                if target_kind == "artifact_mirror" else feature_root
            )
            if stop_root is not None:
                _prune_empty_artifact_parents(target_path, stop_root)
        deleted_artifacts.append({
            "target_ref": target_ref,
            "target_kind": target_kind,
            "normalized_ref": normalized_ref,
            "path": str(target_path),
        })

    return {
        "applied_updates": applied_updates,
        "applied_target_updates": applied_target_updates,
        "skipped_updates": skipped_updates,
        "synced_files": synced_files,
        "deleted_artifacts": deleted_artifacts,
        "skipped_deletes": skipped_deletes,
    }


def _dag_artifact_repair_synthetic_result(
    group_idx: int,
    retry: int,
    planned: PlannedBugGroup,
    result: ArtifactRepairResult,
    record: dict[str, Any],
) -> ImplementationResult:
    safe_group = _safe_context_stem(planned.group.group_id)
    status = (
        result.status
        if result.status in {"completed", "partial", "blocked"}
        else "partial"
    )
    return ImplementationResult(
        task_id=(
            result.task_id
            or f"ARTIFACT-REPAIR-g{group_idx}-r{retry}-{safe_group}"
        ),
        summary=(
            "DAG artifact repair lane completed for "
            f"{planned.group.group_id}: {result.summary}"
        ),
        status=status,
        files_created=[],
        files_modified=[],
        notes=json.dumps(record, indent=2),
        deviations=result.deviations,
        self_reported_risks=result.self_reported_risks,
    )


async def _run_dag_artifact_repair_lane(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry: int,
    planned: PlannedBugGroup,
    resolution: DagContradictionResolution,
    resolution_record: dict[str, Any],
    *,
    group_tasks: list[ImplementationTask],
    feature_root: Path | None,
    runtime: str | None,
    feedback: str,
    fix_context: str,
    closure_path_problems: list[dict[str, Any]] | None = None,
) -> tuple[ArtifactRepairResult, ImplementationResult, dict[str, Any]]:
    safe_group = _safe_context_stem(planned.group.group_id)
    base_target_refs = _dag_artifact_repair_target_refs(
        resolution,
        planned=planned,
    )
    closure_before = _dag_artifact_closure_scan(
        runner,
        feature,
        group_idx,
        group_tasks,
        closure_path_problems or [],
    )
    closure_target_refs = closure_before.target_refs()
    target_refs = _dedupe_preserving_order([
        *base_target_refs,
        *closure_target_refs,
    ])
    target_ref_prompt = "\n".join(f"- {ref}" for ref in target_refs) or "- (none)"
    closure_prompt = (
        "\n\nHost-required closure targets. These are not optional; a repair "
        "that leaves any blocking stale signature in these DAG/source artifacts "
        "must be reported as blocked or partial:\n"
        + (
            "\n".join(
                f"- {item['target_ref']} ({item['artifact_class']}; "
                f"stale={', '.join(item['stale_signatures'][:3])})"
                for item in closure_before.blocking_targets
            )
            or "- (none)"
        )
        + "\n\nAdvisory residuals found in historical/reference artifacts. "
        "Clean them only when they feed current task-spec/result regeneration:\n"
        + (
            "\n".join(
                f"- {item['target_ref']} ({item['artifact_class']})"
                for item in closure_before.advisory_residuals[:20]
            )
            or "- (none)"
        )
    )
    artifact_key = (
        f"dag-artifact-repair:g{group_idx}:{safe_group}:retry-{retry}"
    )
    closure_artifact_key = (
        f"dag-artifact-closure:g{group_idx}:retry-{retry}:{safe_group}"
    )
    ws_path = _dag_artifact_repair_workspace(runner, feature, feature_root)
    workspace_ctx = (
        f"Your working directory is: `{ws_path}`\n"
        "This lane may edit workflow artifact/context files only. It must not "
        "edit product source, tests, package manifests, or runtime code."
        if ws_path else ""
    )
    context_package = await _build_prompt_context_package(
        runner,
        feature,
        title=(
            f"DAG Artifact Repair — Group {group_idx} "
            f"Retry {retry + 1} Bug Group {planned.group.group_id}"
        ),
        file_stem=f"g{group_idx}-artifact-repair-r{retry}-{safe_group}",
        intro_lines=[
            (
                "Repair stale or contradictory workflow artifacts discovered "
                "during DAG verification."
            ),
            (
                "This lane is separate from product-code repair; keep the work "
                "artifact-only."
            ),
        ],
        sections=[
            ("feedback", "Merged Verifier Feedback", feedback),
            ("issues", "Grouped Issues", planned.issue_text),
            ("rca", "Root Cause Analysis", to_str(planned.rca)),
            (
                "resolution",
                "Accepted Contradiction Resolution",
                json.dumps(resolution_record, indent=2),
            ),
            ("targets", "Artifact Repair Targets", target_ref_prompt),
            ("closure", "Host-Required DAG Artifact Closure", closure_prompt),
            (
                "task-specs",
                "Current DAG Group Task Specs",
                _format_dag_group_task_specs(group_tasks),
            ),
            ("fix-context", "Original Enhancement Items", fix_context),
            ("workspace", "Workspace", workspace_ctx),
        ],
    )
    actor = _make_parallel_actor(
        implementer,
        f"dag-g{group_idx}-r{retry}-artifact-repair-{safe_group}",
        runtime=runtime,
        workspace_path=ws_path,
    )
    try:
        raw_result = await runner.run(
            Ask(
                actor=actor,
                prompt=(
                    f"## DAG Artifact Repair: group {planned.group.group_id}\n\n"
                    f"{_context_package_prompt(context_package)}"
                    "Apply the accepted artifact_repair resolution. You are repairing "
                    "workflow artifacts, manifests, context packages, or derived DAG "
                    "metadata only. Do not modify product source files.\n\n"
                    "Allowed targets are exactly these artifact/context refs:\n"
                    f"{target_ref_prompt}\n\n"
                    f"{closure_prompt}\n\n"
                    "If a target is available as a file in the artifact mirror or "
                    ".iriai-context area, either edit that file directly or return a "
                    "full replacement in artifact_updates with target_ref set to that "
                    "artifact/context path. If a feature-scoped artifact key must be "
                    "updated through the store instead, return a full replacement with "
                    "artifact_key set. For `dag-task:{task_id}` keys, content must be "
                    "a full ImplementationResult JSON object whose task_id matches "
                    "the key suffix and whose reported files are canonical existing "
                    "product paths. Report only artifact/context paths in "
                    "artifacts_created/artifacts_modified/artifacts_deleted."
                ),
                output_type=ArtifactRepairResult,
            ),
            feature,
            phase_name="implementation",
        )
        result = (
            raw_result
            if isinstance(raw_result, ArtifactRepairResult)
            else ArtifactRepairResult.model_validate(raw_result)
        )
        result = result.model_copy(update={
            "task_id": result.task_id or (
                f"ARTIFACT-REPAIR-g{group_idx}-r{retry}-{safe_group}"
            ),
            "group_id": result.group_id or planned.group.group_id,
        })
        update_record = await _apply_dag_artifact_repair_updates(
            runner,
            feature,
            result,
            feature_root,
        )
        applied_any = bool(
            update_record.get("applied_updates")
            or update_record.get("applied_target_updates")
            or update_record.get("synced_files")
            or update_record.get("deleted_artifacts")
        )
        closure_after: DagArtifactClosureScan | None = None
        closure_record: dict[str, Any] | None = None
        if closure_before.stale_signatures:
            closure_after = _dag_artifact_closure_scan(
                runner,
                feature,
                group_idx,
                group_tasks,
                closure_path_problems or [],
            )
            closure_status = (
                "completed" if not closure_after.blocking_targets else "blocked"
            )
            deleted_generated = [
                item for item in update_record.get("deleted_artifacts", [])
                if (
                    f"/.iriai-context/g{group_idx}-expanded-verify-"
                    in str(item.get("path", ""))
                    or str(item.get("normalized_ref", "")).startswith(
                        f".iriai-context/g{group_idx}-expanded-verify-"
                    )
                )
            ]
            closure_record = _dag_closure_record(
                artifact_key=closure_artifact_key,
                group_idx=group_idx,
                retry=retry,
                group_id=planned.group.group_id,
                before=closure_before,
                after=closure_after,
                status=closure_status,
                deleted_snapshots=deleted_generated,
            )
            await runner.artifacts.put(
                closure_artifact_key,
                json.dumps(closure_record, indent=2),
                feature=feature,
            )
            if closure_after.blocking_targets and result.status != "blocked":
                result = result.model_copy(update={
                    "status": "blocked",
                    "summary": (
                        f"{result.summary} Blocking DAG artifact closure "
                        "residuals remain after repair."
                    ).strip(),
                    "notes": (
                        f"{result.notes}\n\nDAG artifact closure residuals:\n"
                        f"{json.dumps(closure_after.to_record(), indent=2)}"
                    ).strip(),
                })
        if not applied_any and result.status != "blocked":
            result = result.model_copy(update={
                "status": "blocked",
                "summary": (
                    f"{result.summary} No artifact updates, synced files, or "
                    "safe generated deletions were applied."
                ).strip(),
            })
        record = {
            "artifact_key": artifact_key,
            "source": "dag-repair",
            "group_idx": group_idx,
            "retry": retry,
            "group_id": planned.group.group_id,
            "resolution_artifact_key": resolution_record.get("artifact_key"),
            "target_refs": target_refs,
            "base_target_refs": base_target_refs,
            "closure_target_refs": closure_target_refs,
            "result": result.model_dump(mode="json"),
            "artifact_update_application": update_record,
            "artifact_closure_key": closure_artifact_key,
            "artifact_closure": closure_record,
            "created_at": time.time(),
        }
        await runner.artifacts.put(
            artifact_key,
            json.dumps(record, indent=2),
            feature=feature,
        )
        return (
            result,
            _dag_artifact_repair_synthetic_result(
                group_idx,
                retry,
                planned,
                result,
                record,
            ),
            record,
        )
    except Exception as exc:
        result = ArtifactRepairResult(
            task_id=f"ARTIFACT-REPAIR-g{group_idx}-r{retry}-{safe_group}",
            group_id=planned.group.group_id,
            summary=(
                "DAG artifact repair lane failed before returning a usable "
                f"ArtifactRepairResult: {type(exc).__name__}: {exc}"
            ),
            status="blocked",
            notes=repr(exc),
        )
        record = {
            "artifact_key": artifact_key,
            "source": "dag-repair",
            "group_idx": group_idx,
            "retry": retry,
            "group_id": planned.group.group_id,
            "resolution_artifact_key": resolution_record.get("artifact_key"),
            "target_refs": target_refs,
            "result": result.model_dump(mode="json"),
            "error": repr(exc),
            "created_at": time.time(),
        }
        await runner.artifacts.put(
            artifact_key,
            json.dumps(record, indent=2),
            feature=feature,
        )
        logger.warning(
            "DAG artifact repair lane failed group=%d retry=%d bug_group=%s: %s",
            group_idx,
            retry,
            planned.group.group_id,
            exc,
        )
        return (
            result,
            _dag_artifact_repair_synthetic_result(
                group_idx,
                retry,
                planned,
                result,
                record,
            ),
            record,
        )


def _dag_filter_fixable_groups_for_quarantine(
    fixable_groups: list[PlannedBugGroup],
    quarantined_groups: list[PlannedBugGroup],
) -> tuple[list[PlannedBugGroup], list[str]]:
    if not quarantined_groups:
        return fixable_groups, []
    runnable: list[PlannedBugGroup] = []
    blocked: list[str] = []
    for planned in fixable_groups:
        if _dag_contradiction_groups_overlap(planned, quarantined_groups):
            blocked.append(planned.group.group_id)
        else:
            runnable.append(planned)
    return runnable, blocked


def _dag_repair_task_failed_result(
    group_idx: int,
    retry: int,
    gid: str,
    exc: BaseException,
) -> ImplementationResult:
    return ImplementationResult(
        task_id=f"DAG-REPAIR-FAILED-g{group_idx}-r{retry}-{_safe_context_stem(gid)}",
        status="blocked",
        summary=(
            f"DAG repair agent for group {gid} failed before returning a usable "
            f"ImplementationResult: {type(exc).__name__}: {exc}"
        ),
        notes=repr(exc),
    )


async def _run_dag_repair_fix_tasks(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry: int,
    round_idx: int,
    runnable_ids: list[str],
    fix_tasks: list[Ask],
) -> list[ImplementationResult | None]:
    if len(fix_tasks) == 1:
        try:
            result = await runner.run(
                fix_tasks[0],
                feature,
                phase_name="implementation",
            )
        except Exception as exc:
            gid = runnable_ids[0] if runnable_ids else "unknown"
            failed = _dag_repair_task_failed_result(group_idx, retry, gid, exc)
            await runner.artifacts.put(
                f"dag-repair-fix-error:g{group_idx}:{gid}:retry-{retry}:round-{round_idx}",
                failed.model_dump_json(),
                feature=feature,
            )
            logger.warning(
                "DAG repair fix task failed group=%d retry=%d bug_group=%s: %s",
                group_idx,
                retry,
                gid,
                exc,
            )
            return [failed]
        return [result if isinstance(result, ImplementationResult) else None]

    gathered = await _asyncio.gather(
        *[
            runner.run(task, feature, phase_name="implementation")
            for task in fix_tasks
        ],
        return_exceptions=True,
    )
    results: list[ImplementationResult | None] = []
    for gid, result in zip(runnable_ids, gathered):
        if isinstance(result, ImplementationResult):
            results.append(result)
            continue
        if isinstance(result, BaseException):
            failed = _dag_repair_task_failed_result(group_idx, retry, gid, result)
            await runner.artifacts.put(
                f"dag-repair-fix-error:g{group_idx}:{gid}:retry-{retry}:round-{round_idx}",
                failed.model_dump_json(),
                feature=feature,
            )
            logger.warning(
                "DAG repair fix task failed group=%d retry=%d bug_group=%s: %s",
                group_idx,
                retry,
                gid,
                result,
            )
            results.append(failed)
            continue
        results.append(None)
    return results


async def _persist_dag_contradiction_resolution(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry: int,
    planned: PlannedBugGroup,
    resolution: DagContradictionResolution,
) -> dict[str, Any]:
    safe_group = _safe_context_stem(planned.group.group_id)
    artifact_key = f"contradiction:dag-repair:g{group_idx}:retry-{retry}:{safe_group}"
    record = {
        "artifact_key": artifact_key,
        "source": "dag-repair",
        "group_idx": group_idx,
        "retry": retry,
        "group_id": planned.group.group_id,
        "rca_key": planned.rca_key,
        "resolution": resolution.resolution,
        "resolution_kind": resolution.resolution_kind,
        "authoritative_sources": list(resolution.authoritative_sources),
        "artifact_paths": list(resolution.artifact_paths),
        "superseded_expectation": resolution.superseded_expectation,
        "implementation_direction": resolution.implementation_direction,
        "requires_code_change": resolution.requires_code_change,
        "needs_human": resolution.needs_human,
        "confidence": resolution.confidence,
        "rationale": resolution.rationale,
        "created_at": time.time(),
    }
    await runner.artifacts.put(artifact_key, json.dumps(record, indent=2), feature=feature)

    manifest_raw = await runner.artifacts.get(CONTRADICTION_DECISIONS_KEY, feature=feature)
    manifest: dict[str, Any]
    try:
        manifest = json.loads(manifest_raw) if manifest_raw else {}
    except Exception:
        manifest = {}
    decisions = [
        item
        for item in manifest.get("decisions", [])
        if isinstance(item, dict) and item.get("artifact_key") != artifact_key
    ]
    decisions.append(record)
    manifest = {"decisions": decisions}
    await runner.artifacts.put(
        CONTRADICTION_DECISIONS_KEY,
        json.dumps(manifest, indent=2),
        feature=feature,
    )
    return record


async def _resolve_dag_contradiction(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry: int,
    planned: PlannedBugGroup,
    *,
    group_tasks: list[ImplementationTask],
    feature_root: Path | None,
    runtime: str | None,
    feedback: str,
) -> DagContradictionResolution | None:
    safe_group = _safe_context_stem(planned.group.group_id)
    file_stem = f"g{group_idx}-contradiction-{safe_group}-r{retry}"
    existing_decisions = await _format_contradiction_decisions_context(runner, feature)
    workspace_ctx = (
        f"Feature repos are rooted at: `{feature_root}`\n"
        "Use this workspace for read-only source inspection."
        if feature_root else ""
    )
    context_package = await build_context_package(
        runner,
        feature,
        title=f"DAG Contradiction Resolution — Group {group_idx} {planned.group.group_id}",
        file_stem=file_stem,
        intro_lines=[
            "Resolve this DAG repair spec contradiction using the provided source evidence.",
            "Read the manifest and open only the files needed to cite authoritative sources.",
        ],
        items=[
            ContextPackageItem(
                key="rca",
                label="Contradiction RCA",
                group="Focused Contradiction Evidence",
                content=to_str(planned.rca),
                file_name=f"{file_stem}-rca.md",
            ),
            ContextPackageItem(
                key="issues",
                label="Grouped Verifier/Lens Issues",
                group="Focused Contradiction Evidence",
                content=planned.issue_text,
                file_name=f"{file_stem}-issues.md",
            ),
            ContextPackageItem(
                key="feedback",
                label="Merged Verifier Feedback",
                group="Focused Contradiction Evidence",
                content=feedback,
                file_name=f"{file_stem}-feedback.md",
            ),
            ContextPackageItem(
                key="task-specs",
                label="Current DAG Group Task Specs",
                group="Focused Contradiction Evidence",
                content=_format_dag_group_task_specs(group_tasks),
                file_name=f"{file_stem}-task-specs.md",
            ),
            ContextPackageItem(
                key="existing-decisions",
                label="Existing Resolved Contradiction Decisions",
                group="Focused Contradiction Evidence",
                content=existing_decisions,
                file_name=f"{file_stem}-existing-decisions.md",
            ),
            ContextPackageItem(
                key="workspace",
                label="Workspace",
                group="Focused Contradiction Evidence",
                content=workspace_ctx,
                file_name=f"{file_stem}-workspace.md",
            ),
            ContextPackageItem(
                key="dag-strategy",
                label="DAG Strategy",
                group="Searchable Source Artifacts",
                artifact_key="dag:strategy",
                file_name=f"{file_stem}-dag-strategy.md",
            ),
            ContextPackageItem(
                key="decisions-global",
                label="Global Decisions",
                group="Searchable Source Artifacts",
                artifact_key="decisions:global",
                file_name=f"{file_stem}-decisions-global.md",
            ),
            ContextPackageItem(
                key="decisions",
                label="Compiled Decisions",
                group="Searchable Source Artifacts",
                artifact_key="decisions",
                file_name=f"{file_stem}-decisions.md",
            ),
        ],
    )
    prompt = (
        f"## Autonomous DAG Contradiction Resolution\n\n"
        f"{_context_package_prompt(context_package)}"
        "Adjudicate the contradiction. This is not a code-fix task.\n\n"
        "Rules:\n"
        "1. Choose the authoritative interpretation only when the cited sources support it.\n"
        "2. Set resolution_kind to exactly one of: decision_only, requires_code_change, "
        "artifact_repair, stale_not_reproducing, needs_human.\n"
        "3. Set confidence to exactly high, medium, or low. Do not set confidence=contradiction.\n"
        "4. If the right answer is only verifier/spec interpretation, use decision_only "
        "and set requires_code_change=false.\n"
        "5. If current source/tests already disprove the finding, use stale_not_reproducing "
        "and set requires_code_change=false.\n"
        "6. If task metadata, manifests, or derived artifacts need repair but product code "
        "does not, use artifact_repair and put only the artifact/context files "
        "or artifact keys in artifact_paths. Product files may appear only as "
        "evidence in authoritative_sources or rationale.\n"
        "7. If implementation must change, use requires_code_change, set requires_code_change=true, "
        "and provide exact implementation_direction.\n"
        "8. If evidence is insufficient or product-risky, use needs_human and set needs_human=true.\n"
        "9. authoritative_sources must cite concrete files/artifacts/decisions.\n"
        "10. Do not waive real implementation failures; only resolve conflicting expectations.\n"
    )
    actor = _make_parallel_actor(
        root_cause_analyst,
        f"dag-g{group_idx}-r{retry}-contradiction-{safe_group}",
        runtime=runtime,
        workspace_path=str(feature_root) if feature_root else None,
    )
    result = await runner.run(
        Ask(actor=actor, prompt=prompt, output_type=DagContradictionResolution),
        feature,
        phase_name="implementation",
    )
    if isinstance(result, DagContradictionResolution):
        return result
    if isinstance(result, BaseModel):
        return DagContradictionResolution.model_validate(result.model_dump())
    return DagContradictionResolution.model_validate(result)


def _dedupe_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _dag_candidate_file_roots(feature_root: Path | None) -> list[Path]:
    if feature_root is None:
        return []
    roots = [feature_root]
    try:
        roots.extend(_discover_repo_roots_under(feature_root))
    except Exception:
        logger.debug("Failed to discover repo roots for DAG preflight", exc_info=True)
    return sorted(set(roots))


def _dag_reported_file_exists(path: str, roots: list[Path]) -> bool:
    if not path or path.startswith("http://") or path.startswith("https://"):
        return True
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.exists()
    return any((root / candidate).exists() for root in roots)


def _dag_existing_path_locations(path: str, roots: list[Path]) -> list[Path]:
    if not path or path.startswith("http://") or path.startswith("https://"):
        return []
    candidate = Path(path)
    locations: list[Path] = []
    if candidate.is_absolute():
        if candidate.exists():
            locations.append(candidate)
        return locations
    seen: set[str] = set()
    for root in roots:
        absolute = root / candidate
        if not absolute.exists():
            continue
        key = absolute.resolve().as_posix()
        if key in seen:
            continue
        seen.add(key)
        locations.append(absolute)
    return locations


def _dag_git_path_state(path: str, roots: list[Path]) -> dict[str, Any]:
    if not path or path.startswith("http://") or path.startswith("https://"):
        return {
            "kind": "absent",
            "states": [],
            "tracked_or_staged": False,
            "blocks_forbidden": False,
            "matches": [],
        }
    if shutil.which("git") is None:
        return {
            "kind": "absent",
            "states": [],
            "tracked_or_staged": False,
            "blocks_forbidden": False,
            "matches": [],
        }
    normalized = path.strip().replace("\\", "/").strip("/")
    if not normalized:
        return {
            "kind": "absent",
            "states": [],
            "tracked_or_staged": False,
            "blocks_forbidden": False,
            "matches": [],
        }

    def _git_lines(root: Path, args: list[str]) -> list[str]:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
                timeout=2,
            )
        except Exception:
            return []
        if result.returncode not in {0, 1}:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    state_matches: list[dict[str, str]] = []
    for root in roots:
        if not (root / ".git").exists():
            continue
        variants = [normalized]
        root_name = root.name.strip("/")
        if root_name and normalized.startswith(f"{root_name}/"):
            variants.append(normalized[len(root_name) + 1:])
        for variant in _dedupe_preserving_order(variants):
            checks = [
                (
                    "unstaged_delete",
                    ["diff", "--name-only", "--diff-filter=D", "--", variant],
                ),
                (
                    "staged_delete",
                    ["diff", "--cached", "--name-only", "--diff-filter=D", "--", variant],
                ),
                (
                    "staged_add",
                    ["diff", "--cached", "--name-only", "--diff-filter=ACMR", "--", variant],
                ),
                ("clean_tracked", ["ls-files", "--", variant]),
                (
                    "untracked",
                    ["ls-files", "--others", "--exclude-standard", "--", variant],
                ),
            ]
            for state_name, args in checks:
                for match in _git_lines(root, args):
                    state_matches.append({
                        "state": state_name,
                        "repo": str(root),
                        "path": match,
                    })

    states = {match["state"] for match in state_matches}
    priority = [
        "untracked",
        "staged_add",
        "unstaged_delete",
        "clean_tracked",
        "staged_delete",
    ]
    kind = next((state for state in priority if state in states), "absent")
    blocks_forbidden = bool(
        states & {"untracked", "staged_add", "unstaged_delete", "clean_tracked"}
    )
    return {
        "kind": kind,
        "states": sorted(states),
        "tracked_or_staged": blocks_forbidden,
        "blocks_forbidden": blocks_forbidden,
        "matches": state_matches[:50],
    }


def _dag_path_tracked_or_staged(path: str, roots: list[Path]) -> bool:
    return bool(_dag_git_path_state(path, roots).get("tracked_or_staged"))


def _dag_git_state_problem_fields(git_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "git_state": git_state.get("kind", "absent"),
        "git_states": git_state.get("states", []),
        "git_matches": git_state.get("matches", []),
    }


def _dag_manifest_path_entries(roots: list[Path]) -> dict[str, list[dict[str, str]]]:
    entries: dict[str, list[dict[str, str]]] = {
        "expected_files": [],
        "forbidden_files": [],
    }
    seen_configs: set[Path] = set()
    for root in roots:
        for config_path in root.rglob("verify-file-scope.expected-files.json"):
            try:
                resolved = config_path.resolve()
            except Exception:
                resolved = config_path
            if resolved in seen_configs:
                continue
            seen_configs.add(resolved)
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                logger.debug(
                    "Failed to load verify-file-scope entries from %s",
                    config_path,
                    exc_info=True,
                )
                continue
            for key in ("expected_files", "forbidden_files"):
                for item in data.get(key, []):
                    path = ""
                    source = ""
                    if isinstance(item, str):
                        path = item
                    elif isinstance(item, dict):
                        raw_path = item.get("path")
                        raw_source = item.get("source")
                        path = raw_path if isinstance(raw_path, str) else ""
                        source = raw_source if isinstance(raw_source, str) else ""
                    if not path.strip():
                        continue
                    entries[key].append({
                        "path": path.strip().replace("\\", "/").strip("/"),
                        "source": source.strip(),
                        "config_path": str(config_path),
                    })
    return entries


def _dag_forbidden_file_entries(roots: list[Path]) -> set[str]:
    return {
        item["path"]
        for item in _dag_manifest_path_entries(roots).get("forbidden_files", [])
    }


def _dag_path_matches_forbidden_file(path: str, forbidden: set[str]) -> bool:
    normalized = path.strip().replace("\\", "/").strip("/")
    if not normalized:
        return False
    for forbidden_path in forbidden:
        if (
            normalized == forbidden_path
            or normalized.startswith(f"{forbidden_path}/")
            or normalized.endswith(f"/{forbidden_path}")
            or f"/{forbidden_path}/" in normalized
            or forbidden_path.endswith(f"/{normalized}")
        ):
            return True
    return False


def _dag_forbidden_match(
    path: str,
    forbidden_entries: list[dict[str, str]],
) -> dict[str, str] | None:
    forbidden = {entry["path"] for entry in forbidden_entries}
    if not _dag_path_matches_forbidden_file(path, forbidden):
        return None
    normalized = path.strip().replace("\\", "/").strip("/")
    for entry in forbidden_entries:
        forbidden_path = entry["path"]
        if (
            normalized == forbidden_path
            or normalized.startswith(f"{forbidden_path}/")
            or normalized.endswith(f"/{forbidden_path}")
            or f"/{forbidden_path}/" in normalized
            or forbidden_path.endswith(f"/{normalized}")
        ):
            return entry
    return {"path": normalized, "source": "", "config_path": ""}


def _is_external_reported_path(path: str) -> bool:
    lowered = path.strip().lower()
    return (
        "://" in lowered
        or lowered.startswith("mailto:")
        or lowered.startswith("artifact:")
    )


def _is_artifact_context_path(path: str) -> bool:
    normalized = path.strip().replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    if not normalized:
        return True
    if normalized.startswith(".iriai-context/") or "/.iriai-context/" in normalized:
        return True
    if normalized.startswith(".iriai/artifacts/") or "/.iriai/artifacts/" in normalized:
        return True
    if normalized.startswith("compile-") or "/compile-" in normalized:
        return True
    if "dag-fragments" in parts:
        return True
    return False


def _feature_relative_path(path: Path, feature_root: Path | None) -> str:
    if feature_root is None:
        return path.as_posix()
    try:
        return path.resolve().relative_to(feature_root.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _existing_product_path(
    reported_path: str,
    roots: list[Path],
    feature_root: Path | None,
) -> str | None:
    if not reported_path:
        return None
    candidate = Path(reported_path)
    if candidate.is_absolute():
        if candidate.exists():
            return _feature_relative_path(candidate, feature_root)
        return None
    for root in roots:
        absolute = root / candidate
        if absolute.exists():
            return _feature_relative_path(absolute, feature_root)
    return None


def _rewrite_legacy_product_path(
    reported_path: str,
    roots: list[Path],
    feature_root: Path | None,
) -> str | None:
    if not dag_path_canonicalization_enabled():
        return None
    canonical, rule = canonicalize_dag_path(reported_path)
    if not rule:
        return None
    existing = _existing_product_path(canonical, roots, feature_root)
    if existing:
        return existing
    candidate = Path(canonical)
    if candidate.is_absolute() and feature_root is not None:
        try:
            return candidate.resolve().relative_to(feature_root.resolve()).as_posix()
        except Exception:
            pass
    return canonical


def _classify_dag_repair_path(
    reported_path: str,
    roots: list[Path],
    feature_root: Path | None,
) -> tuple[str, str | None]:
    path = reported_path.strip()
    if _is_external_reported_path(path):
        return "external_reference", None
    if _is_artifact_context_path(path):
        return "artifact_context", None
    rewritten = _rewrite_legacy_product_path(path, roots, feature_root)
    if rewritten:
        return "rewritten_product", rewritten
    existing = _existing_product_path(path, roots, feature_root)
    if existing:
        return "product", existing
    return "invalid_product", path


def _validate_dag_task_artifact_update(
    artifact_key: str,
    content: str,
    roots: list[Path],
    feature_root: Path | None,
) -> tuple[ImplementationResult | None, str, list[dict[str, Any]]]:
    if not _is_dag_task_artifact_key(artifact_key):
        return None, "not_dag_task_artifact", []
    if feature_root is None or not roots:
        return None, "missing_feature_root", []
    raw_payload: Any = None
    try:
        raw_payload = json.loads(content)
    except Exception:
        raw_payload = None
    try:
        parsed = ImplementationResult.model_validate_json(content)
    except Exception:
        return None, "invalid_dag_task_result_json", []

    expected_task_id = artifact_key.removeprefix("dag-task:")
    if not _dag_task_id_matches_or_alias(expected_task_id, parsed.task_id):
        return None, "dag_task_id_mismatch", [{
            "expected_task_id": expected_task_id,
            "actual_task_id": parsed.task_id,
        }]
    if parsed.task_id != expected_task_id:
        parsed = parsed.model_copy(update={"task_id": expected_task_id})
    if parsed.status not in {"completed", "partial"}:
        return None, "dag_task_status_not_completed_or_partial", [{
            "status": parsed.status,
        }]

    reported_paths = _dedupe_preserving_order(
        parsed.files_created + parsed.files_modified
    )
    if not reported_paths:
        non_authoritative_fields: list[str] = []
        if isinstance(raw_payload, dict):
            non_authoritative_fields = [
                field_name for field_name in (
                    "files",
                    "artifacts_created",
                    "artifacts_modified",
                    "artifacts_deleted",
                )
                if field_name in raw_payload
            ]
        return None, "dag_task_no_reported_files", [{
            "message": (
                "dag-task artifact_updates.content must be an "
                "ImplementationResult JSON object with non-empty "
                "files_created and/or files_modified. ArtifactRepairResult "
                "fields are not authoritative inside dag-task content."
            ),
            "non_authoritative_fields_present": non_authoritative_fields,
        }]

    forbidden_files = _dag_forbidden_file_entries(roots)
    path_records: list[dict[str, Any]] = []
    for path in reported_paths:
        if _dag_path_matches_forbidden_file(path, forbidden_files):
            return None, "dag_task_forbidden_path", [{
                "path": path,
            }]
        category, normalized = _classify_dag_repair_path(
            path,
            roots,
            feature_root,
        )
        path_records.append({
            "path": path,
            "category": category,
            "normalized": normalized or "",
        })
        if category == "rewritten_product":
            return None, "dag_task_noncanonical_path", [{
                "path": path,
                "canonical": normalized or "",
            }]
        if category != "product":
            return None, f"dag_task_{category}", [{
                "path": path,
                "normalized": normalized or "",
            }]

    return parsed, "", path_records


def _sanitize_dag_repair_result(
    result: ImplementationResult,
    roots: list[Path],
    feature_root: Path | None,
) -> tuple[ImplementationResult, list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    updates: dict[str, list[str]] = {}
    for field_name in ("files_created", "files_modified"):
        kept: list[str] = []
        for original in getattr(result, field_name):
            category, normalized = _classify_dag_repair_path(
                original,
                roots,
                feature_root,
            )
            if normalized and category in {
                "product",
                "rewritten_product",
                "invalid_product",
            }:
                kept.append(normalized)
            records.append({
                "task_id": result.task_id,
                "field": field_name,
                "original": original,
                "normalized": normalized or "",
                "category": category,
                "kept_for_preflight": bool(
                    normalized
                    and category in {
                        "product",
                        "rewritten_product",
                        "invalid_product",
                    }
                ),
            })
        updates[field_name] = _dedupe_preserving_order(kept)
    return result.model_copy(update=updates), records


async def _sanitize_dag_repair_results(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry: int,
    results: list[ImplementationResult],
    feature_root: Path | None,
    context_label: str,
) -> list[ImplementationResult]:
    """Normalize DAG repair result paths before they feed deterministic preflight."""
    roots = _dag_candidate_file_roots(feature_root)
    sanitized: list[ImplementationResult] = []
    path_records: list[dict[str, Any]] = []
    for result in results:
        clean_result, records = _sanitize_dag_repair_result(
            result,
            roots,
            feature_root,
        )
        sanitized.append(clean_result)
        path_records.extend(records)

    counts = collections.Counter(record["category"] for record in path_records)
    report = {
        "group_idx": group_idx,
        "retry": retry,
        "context_label": context_label,
        "result_task_ids": [result.task_id for result in results],
        "counts": dict(sorted(counts.items())),
        "ignored_path_count": counts.get("artifact_context", 0)
        + counts.get("external_reference", 0),
        "rewritten_path_count": counts.get("rewritten_product", 0),
        "invalid_product_path_count": counts.get("invalid_product", 0),
        "has_invalid_product_paths": counts.get("invalid_product", 0) > 0,
        "paths": path_records,
    }
    await runner.artifacts.put(
        f"dag-repair-result-sanitize:g{group_idx}:retry-{retry}",
        json.dumps(report),
        feature=feature,
    )
    return sanitized


def _dag_result_path_problems(
    result: ImplementationResult,
    roots: list[Path],
    forbidden_entries: list[dict[str, str]],
    expected_entries: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    candidate_evidence = _dag_candidate_evidence_for_task_id(
        result.task_id,
        expected_entries or [],
        roots,
    )
    for path in _dedupe_preserving_order(
        result.files_created + result.files_modified
    ):
        forbidden = _dag_forbidden_match(path, forbidden_entries)
        exists = _dag_reported_file_exists(path, roots)
        git_state = _dag_git_path_state(path, roots)
        tracked_or_staged = bool(git_state.get("tracked_or_staged"))
        if forbidden is not None:
            problems.append({
                "task_id": result.task_id,
                "artifact_key": f"dag-task:{result.task_id}",
                "path": path,
                "reason": "forbidden",
                "exists": str(bool(exists)).lower(),
                "exists_on_disk": bool(exists),
                "tracked_or_staged": tracked_or_staged,
                **_dag_git_state_problem_fields(git_state),
                "repair_route": (
                    "product_cleanup_required"
                    if exists or tracked_or_staged
                    else "artifact_only"
                ),
                "forbidden_rule": forbidden.get("path", ""),
                "forbidden_path": forbidden.get("path", ""),
                "forbidden_source": forbidden.get("source", ""),
                "candidate_evidence": candidate_evidence,
            })
            continue
        if not exists:
            problems.append({
                "task_id": result.task_id,
                "artifact_key": f"dag-task:{result.task_id}",
                "path": path,
                "reason": "missing",
                "exists": "false",
                "exists_on_disk": False,
                "tracked_or_staged": tracked_or_staged,
                **_dag_git_state_problem_fields(git_state),
                "repair_route": (
                    "product_cleanup_required"
                    if tracked_or_staged
                    else "artifact_only"
                ),
                "forbidden_rule": "",
                "forbidden_path": "",
                "forbidden_source": "",
                "candidate_evidence": candidate_evidence,
            })
    return problems


def _dag_fragment_artifact_ref_for_task(task: ImplementationTask) -> str:
    subfeature = task.subfeature_id.strip()
    if not subfeature:
        return ""
    match = re.search(r"\bslice-(\d+)\b", task.id)
    if not match:
        return ""
    return f"dag-fragment:{subfeature}:slice-{match.group(1)}"


def _dag_task_spec_path_problems(
    task: ImplementationTask,
    roots: list[Path],
    forbidden_entries: list[dict[str, str]],
    expected_entries: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    candidate_evidence = _dag_candidate_evidence_for_task_id(
        task.id,
        expected_entries or [],
        roots,
    )
    seen: set[tuple[str, str]] = set()
    entries: list[tuple[str, str]] = []
    entries.extend(
        (f"file_scope[{idx}].path", scope.path)
        for idx, scope in enumerate(task.file_scope)
        if scope.path
    )
    entries.extend(
        (f"files[{idx}]", path)
        for idx, path in enumerate(task.files)
        if path
    )
    for field, path in entries:
        key = (field, path)
        if key in seen:
            continue
        seen.add(key)
        forbidden = _dag_forbidden_match(path, forbidden_entries)
        if forbidden is None:
            continue
        exists = _dag_reported_file_exists(path, roots)
        git_state = _dag_git_path_state(path, roots)
        tracked_or_staged = bool(git_state.get("tracked_or_staged"))
        problems.append({
            "task_id": task.id,
            "artifact_key": f"dag-task:{task.id}",
            "path": path,
            "field": field,
            "reason": "forbidden_task_spec",
            "exists": str(bool(exists)).lower(),
            "exists_on_disk": bool(exists),
            "tracked_or_staged": tracked_or_staged,
            **_dag_git_state_problem_fields(git_state),
            "repair_route": (
                "product_cleanup_required"
                if exists or tracked_or_staged
                else "artifact_only"
            ),
            "forbidden_rule": forbidden.get("path", ""),
            "forbidden_path": forbidden.get("path", ""),
            "forbidden_source": forbidden.get("source", ""),
            "candidate_evidence": candidate_evidence,
            "source_artifact_ref": _dag_fragment_artifact_ref_for_task(task),
        })
    return problems


def _dag_task_spec_source_ref(task: ImplementationTask) -> str:
    source_ref = _dag_fragment_artifact_ref_for_task(task)
    if source_ref:
        return source_ref
    inferred_subfeature, inferred_slice = _dag_closure_task_ref_parts(task.id)
    if inferred_subfeature and inferred_slice:
        return f"dag-fragment:{inferred_subfeature}:slice-{inferred_slice}"
    return ""


def _dag_fragment_ref_parts(source_ref: str) -> tuple[str, str] | None:
    match = re.match(r"^dag-fragment:([^:]+):slice-(\d+)$", source_ref.strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def _dag_fragment_path_for_ref(
    runner: WorkflowRunner,
    feature: Feature,
    source_ref: str,
) -> Path | None:
    parts = _dag_fragment_ref_parts(source_ref)
    artifact_root = _dag_artifact_feature_dir(runner, feature)
    if parts is None or artifact_root is None:
        return None
    subfeature, slice_num = parts
    return (
        artifact_root
        / "subfeatures"
        / subfeature
        / "dag-fragments"
        / f"slice-{slice_num}.json"
    )


async def _dag_fragment_payload_for_ref(
    runner: WorkflowRunner,
    feature: Feature,
    source_ref: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    record: dict[str, Any] = {
        "source_ref": source_ref,
        "source_kind": "",
        "path": "",
        "sha256": "",
    }
    path = _dag_fragment_path_for_ref(runner, feature, source_ref)
    raw = ""
    if path is not None and path.exists() and path.is_file():
        try:
            raw = path.read_text(encoding="utf-8")
            record.update({
                "source_kind": "artifact_mirror_file",
                "path": str(path),
            })
        except Exception:
            logger.debug("Failed to read DAG fragment %s", path, exc_info=True)
            raw = ""
    if not raw:
        getter = getattr(getattr(runner, "artifacts", None), "get", None)
        if getter is not None:
            try:
                raw = await getter(source_ref, feature=feature)
                if raw:
                    record["source_kind"] = "artifact_store_key"
            except Exception:
                logger.debug(
                    "Failed to read DAG fragment artifact %s", source_ref,
                    exc_info=True,
                )
                raw = ""
    if not raw:
        return None, record
    record["sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    try:
        payload = json.loads(raw)
    except Exception:
        record["parse_error"] = "invalid_json"
        return None, record
    if not isinstance(payload, dict):
        record["parse_error"] = "not_object"
        return None, record
    return payload, record


def _dag_fragment_tasks_from_payload(
    payload: dict[str, Any],
) -> tuple[list[ImplementationTask], list[dict[str, Any]]]:
    tasks: list[ImplementationTask] = []
    raw_tasks = payload.get("tasks", [])
    if isinstance(raw_tasks, list):
        for raw_task in raw_tasks:
            if not isinstance(raw_task, dict):
                continue
            try:
                tasks.append(ImplementationTask.model_validate(raw_task))
            except Exception:
                logger.debug(
                    "Failed to validate task from DAG fragment",
                    exc_info=True,
                )
    retired = [
        item for item in payload.get("_retired_tasks", [])
        if isinstance(item, dict)
    ]
    return tasks, retired


def _dag_retired_task_replacement(
    task: ImplementationTask,
    retired_record: dict[str, Any],
) -> ImplementationTask:
    retired_reason = str(
        retired_record.get("retired_reason")
        or retired_record.get("reason")
        or "Retired by canonical DAG fragment."
    ).strip()
    canonical_paths = [
        str(path).strip()
        for path in retired_record.get("canonical_paths", []) or []
        if str(path).strip()
    ]
    note_lines = [retired_reason]
    if canonical_paths:
        note_lines.append(
            "Canonical replacement paths: "
            + ", ".join(f"`{path}`" for path in canonical_paths)
        )
    return task.model_copy(update={
        "description": f"{task.description}\n\nRetired task projection: {' '.join(note_lines)}",
        "file_scope": [],
        "files": [],
    })


def _dag_task_spec_path_signature(task: ImplementationTask) -> tuple[Any, ...]:
    return (
        tuple((scope.path, scope.action) for scope in task.file_scope),
        tuple(task.files),
    )


def _dag_generated_snapshot_paths(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
) -> list[Path]:
    artifact_root = _dag_artifact_feature_dir(runner, feature)
    if artifact_root is None:
        return []
    context_dir = artifact_root / ".iriai-context"
    if not context_dir.exists():
        return []
    patterns = [
        f"g{group_idx}-expanded-verify-*-task-specs.md",
        f"g{group_idx}-expanded-verify-*-changed-files.md",
    ]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(sorted(context_dir.glob(pattern)))
    return paths


def _dag_delete_stale_generated_snapshots(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    stale_path_problems: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    signature_records = _dag_closure_signature_records_from_path_problems(
        stale_path_problems
    )
    signatures = _dag_closure_blocking_signatures(signature_records)
    if not signatures:
        return []
    artifact_root = _dag_artifact_feature_dir(runner, feature)
    deleted: list[dict[str, Any]] = []
    for path in _dag_generated_snapshot_paths(runner, feature, group_idx):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        matched = [signature for signature in signatures if signature in text]
        if not matched:
            continue
        try:
            path.unlink()
        except OSError:
            logger.debug(
                "Failed to delete stale generated DAG snapshot %s",
                path,
                exc_info=True,
            )
            continue
        deleted.append({
            "path": str(path),
            "relative_path": (
                _dag_closure_relative_path(path, artifact_root)
                if artifact_root is not None else path.as_posix()
            ),
            "matched_signatures": matched,
            "reason": "stale_generated_projection_invalidated",
        })
    return deleted


def _dag_task_bearing_source_artifact_path_problems(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    group_tasks: list[ImplementationTask],
    roots: list[Path],
    forbidden_entries: list[dict[str, str]],
    expected_entries: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    del group_idx
    artifact_root = _dag_artifact_feature_dir(runner, feature)
    if artifact_root is None or not artifact_root.exists():
        return []
    subfeatures = _dedupe_preserving_order([
        value for task in group_tasks
        for value in [
            task.subfeature_id,
            _dag_closure_task_ref_parts(task.id)[0],
        ]
        if value
    ])
    if not subfeatures:
        return []

    problems: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def _append_problem(
        *,
        source_path: Path,
        task_id: str,
        field: str,
        path: str,
        source_ref: str,
    ) -> None:
        forbidden = _dag_forbidden_match(path, forbidden_entries)
        if forbidden is None:
            return
        key = (str(source_path), task_id, field, path)
        if key in seen:
            return
        seen.add(key)
        exists = _dag_reported_file_exists(path, roots)
        git_state = _dag_git_path_state(path, roots)
        tracked_or_staged = bool(git_state.get("tracked_or_staged"))
        problems.append({
            "task_id": task_id,
            "artifact_key": f"dag-task:{task_id}" if task_id else "",
            "path": path,
            "field": field,
            "reason": "forbidden_task_spec_source_artifact",
            "exists": str(bool(exists)).lower(),
            "exists_on_disk": bool(exists),
            "tracked_or_staged": tracked_or_staged,
            **_dag_git_state_problem_fields(git_state),
            "repair_route": (
                "product_cleanup_required"
                if exists or tracked_or_staged
                else "artifact_only"
            ),
            "forbidden_rule": forbidden.get("path", ""),
            "forbidden_path": forbidden.get("path", ""),
            "forbidden_source": forbidden.get("source", ""),
            "candidate_evidence": (
                _dag_candidate_evidence_for_task_id(
                    task_id,
                    expected_entries or [],
                    roots,
                )
                if task_id else []
            ),
            "source_artifact_ref": source_ref,
            "source_artifact_path": str(source_path),
        })

    for subfeature in subfeatures:
        fragment_dir = (
            artifact_root
            / "subfeatures"
            / subfeature
            / "dag-fragments"
        )
        if not fragment_dir.exists():
            continue
        for source_path in sorted(fragment_dir.glob("slice-*.json")):
            try:
                payload = json.loads(source_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            slice_match = re.search(r"slice-(\d+)\.json$", source_path.name)
            source_ref = (
                f"dag-fragment:{subfeature}:slice-{slice_match.group(1)}"
                if slice_match else ""
            )
            tasks, retired = _dag_fragment_tasks_from_payload(payload)
            for task in tasks:
                for idx, scope in enumerate(task.file_scope):
                    if scope.path:
                        _append_problem(
                            source_path=source_path,
                            task_id=task.id,
                            field=f"file_scope[{idx}].path",
                            path=scope.path,
                            source_ref=source_ref,
                        )
                for idx, path in enumerate(task.files):
                    if path:
                        _append_problem(
                            source_path=source_path,
                            task_id=task.id,
                            field=f"files[{idx}]",
                            path=path,
                            source_ref=source_ref,
                        )
            for retired_idx, item in enumerate(retired):
                task_id = str(item.get("id", "") or "")
                for path_idx, path in enumerate(item.get("canonical_paths", []) or []):
                    if str(path).strip():
                        _append_problem(
                            source_path=source_path,
                            task_id=task_id,
                            field=(
                                f"_retired_tasks[{retired_idx}]"
                                f".canonical_paths[{path_idx}]"
                            ),
                            path=str(path),
                            source_ref=source_ref,
                        )
    return problems


async def _reconcile_dag_task_specs(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry_label: str,
    group_tasks: list[ImplementationTask],
    *,
    feature_root: Path | None,
) -> DagTaskSpecReconcileOutcome:
    roots = _dag_candidate_file_roots(feature_root)
    report: dict[str, Any] = {
        "group_idx": group_idx,
        "retry": retry_label,
        "applied": [],
        "skipped": [],
        "blockers": [],
        "deleted_generated_snapshots": [],
        "source_path_blockers": [],
    }
    if not roots or not group_tasks:
        report["skipped"].append({"reason": "missing_roots_or_tasks"})
        await runner.artifacts.put(
            f"dag-task-spec-reconcile:g{group_idx}:retry-{retry_label}",
            json.dumps(report, indent=2),
            feature=feature,
        )
        return DagTaskSpecReconcileOutcome(group_tasks, report)

    manifest_entries = _dag_manifest_path_entries(roots)
    expected_entries = manifest_entries.get("expected_files", [])
    forbidden_entries = manifest_entries.get("forbidden_files", [])
    updated_tasks = list(group_tasks)
    stale_projection_problems: list[dict[str, Any]] = []

    for index, task in enumerate(group_tasks):
        current_problems = _dag_task_spec_path_problems(
            task,
            roots,
            forbidden_entries,
            expected_entries,
        )
        if not current_problems:
            continue
        stale_projection_problems.extend(current_problems)
        source_ref = _dag_task_spec_source_ref(task)
        if not source_ref:
            report["skipped"].append({
                "task_id": task.id,
                "reason": "missing_source_artifact_ref",
                "stale_paths": current_problems,
            })
            continue

        payload, source_record = await _dag_fragment_payload_for_ref(
            runner,
            feature,
            source_ref,
        )
        if payload is None:
            report["skipped"].append({
                "task_id": task.id,
                "source_ref": source_ref,
                "reason": "source_fragment_unavailable",
                "source": source_record,
                "stale_paths": current_problems,
            })
            continue
        source_tasks, retired_tasks = _dag_fragment_tasks_from_payload(payload)
        source_task = next(
            (
                candidate for candidate in source_tasks
                if _dag_task_id_matches_or_alias(task.id, candidate.id)
            ),
            None,
        )
        retired_record = next(
            (
                item for item in retired_tasks
                if _dag_task_id_matches_or_alias(task.id, str(item.get("id", "")))
            ),
            None,
        )
        if source_task is None and retired_record is None:
            report["skipped"].append({
                "task_id": task.id,
                "source_ref": source_ref,
                "reason": "task_not_found_in_source_fragment",
                "source": source_record,
                "stale_paths": current_problems,
            })
            continue

        replacement = (
            source_task if source_task is not None
            else _dag_retired_task_replacement(task, retired_record or {})
        )
        replacement = replacement.model_copy(update={"id": task.id})
        replacement_problems = _dag_task_spec_path_problems(
            replacement,
            roots,
            forbidden_entries,
            expected_entries,
        )
        if replacement_problems:
            report["blockers"].append({
                "task_id": task.id,
                "source_ref": source_ref,
                "reason": "source_fragment_still_forbidden",
                "source": source_record,
                "source_path_problems": replacement_problems,
                "stale_paths": current_problems,
            })
            continue
        if _dag_task_spec_path_signature(task) == _dag_task_spec_path_signature(
            replacement
        ):
            report["applied"].append({
                "task_id": task.id,
                "source_ref": source_ref,
                "action": "already_current",
                "source": source_record,
            })
            continue

        updated_tasks[index] = replacement
        report["applied"].append({
            "task_id": task.id,
            "source_ref": source_ref,
            "action": (
                "retired_task_projection"
                if source_task is None else "rehydrated_from_source_fragment"
            ),
            "source": source_record,
            "before_paths": [
                problem.get("path", "") for problem in current_problems
            ],
            "after_file_scope": [
                scope.model_dump(mode="json")
                for scope in replacement.file_scope
            ],
            "after_files": list(replacement.files),
        })

    source_path_blockers = _dag_task_bearing_source_artifact_path_problems(
        runner,
        feature,
        group_idx,
        updated_tasks,
        roots,
        forbidden_entries,
        expected_entries,
    )
    report["source_path_blockers"] = source_path_blockers
    if stale_projection_problems and report["applied"]:
        report["deleted_generated_snapshots"] = _dag_delete_stale_generated_snapshots(
            runner,
            feature,
            group_idx,
            stale_projection_problems,
        )

    await runner.artifacts.put(
        f"dag-task-spec-reconcile:g{group_idx}:retry-{retry_label}",
        json.dumps(report, indent=2),
        feature=feature,
    )
    return DagTaskSpecReconcileOutcome(updated_tasks, report)


def _dag_forbidden_workspace_path_problems(
    roots: list[Path],
    forbidden_entries: list[dict[str, str]],
    expected_entries: list[dict[str, str]],
    *,
    task_id: str = "",
    artifact_key: str = "",
    context_text: str = "",
    include_all: bool = False,
) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    context_lower = context_text.lower()
    for entry in forbidden_entries:
        path = entry.get("path", "")
        if not path or _dag_path_matches_expected_entry(path, expected_entries):
            continue
        if not include_all:
            aliases = _dag_task_id_aliases(task_id) if task_id else set()
            source = entry.get("source", "")
            relevant_by_task = bool(
                aliases and any(alias and alias in source for alias in aliases)
            )
            relevant_by_text = bool(path and path.lower() in context_lower)
            if not relevant_by_task and not relevant_by_text:
                continue
        exists = bool(_dag_existing_path_locations(path, roots))
        git_state = _dag_git_path_state(path, roots)
        tracked_or_staged = bool(git_state.get("tracked_or_staged"))
        if not exists and not tracked_or_staged:
            continue
        problems.append({
            "task_id": task_id,
            "artifact_key": artifact_key or (f"dag-task:{task_id}" if task_id else ""),
            "path": path,
            "reason": "forbidden_workspace_path",
            "exists": str(bool(exists)).lower(),
            "exists_on_disk": exists,
            "tracked_or_staged": tracked_or_staged,
            **_dag_git_state_problem_fields(git_state),
            "repair_route": "product_cleanup_required",
            "forbidden_rule": entry.get("path", ""),
            "forbidden_path": entry.get("path", ""),
            "forbidden_source": entry.get("source", ""),
            "candidate_evidence": (
                _dag_candidate_evidence_for_task_id(task_id, expected_entries, roots)
                if task_id else []
            ),
        })
    return problems


def _dag_direct_workflow_repo_roots(repos_root: Path | None) -> list[Path]:
    if repos_root is None:
        return []
    direct_repos_dir = repos_root if repos_root.name == "repos" else repos_root / "repos"
    if not direct_repos_dir.exists():
        return []
    repos: list[Path] = []
    try:
        children = sorted(direct_repos_dir.iterdir())
    except Exception:
        return []
    for child in children:
        if child.is_dir() and (child / ".git").exists():
            repos.append(child)
    return repos


def _dag_workspace_permission_repair_enabled() -> bool:
    return _env_flag_enabled(DAG_WORKSPACE_PERMISSION_REPAIR_ENV, default=True)


def _agent_shared_gid() -> int | None:
    if grp is None:
        return None
    group_name = os.environ.get(
        AGENT_SHARED_GROUP_ENV,
        DEFAULT_AGENT_SHARED_GROUP,
    ).strip()
    if not group_name:
        return None
    try:
        return grp.getgrnam(group_name).gr_gid
    except KeyError:
        return None


def _strip_direct_route_line_suffix(value: str) -> str:
    target = str(value or "").strip().strip("`'\"")
    if ":" in target:
        maybe_path, maybe_line = target.rsplit(":", 1)
        if maybe_line.isdigit():
            target = maybe_path
    while target.startswith("./"):
        target = target[2:]
    return target.replace("\\", "/").strip("/")


def _dag_workspace_permission_repo_roots(feature_root: Path | None) -> list[Path]:
    repos = _dag_direct_workflow_repo_roots(feature_root)
    if repos:
        return repos
    if feature_root is None or not feature_root.exists():
        return []
    # Test/fixture fallback: still only direct child repos, never recursive repos.
    try:
        return sorted(
            child for child in feature_root.iterdir()
            if child.is_dir() and (child / ".git").exists()
        )
    except Exception:
        return []


def _repo_relative_cleanup_target(repo: Path, target: str) -> str | None:
    normalized = _strip_direct_route_line_suffix(target)
    if not normalized:
        return None
    try:
        candidate = Path(normalized).expanduser()
        if candidate.is_absolute():
            return candidate.resolve(strict=False).relative_to(
                repo.resolve(strict=False)
            ).as_posix()
    except Exception:
        return None
    repo_name = repo.name.strip("/")
    if repo_name and normalized.startswith(f"{repo_name}/"):
        normalized = normalized[len(repo_name) + 1:]
    if normalized.startswith("../") or normalized == "..":
        return None
    return normalized


def _git_path_has_state(repo: Path, repo_relative_path: str) -> bool:
    state = _dag_git_path_state(repo_relative_path, [repo])
    return bool(state.get("states"))


def _cleanup_target_matches_repo_manifest(
    repo: Path,
    repo_relative_path: str,
) -> bool:
    entries = _dag_manifest_path_entries([repo]).get("forbidden_files", [])
    return _dag_forbidden_match(repo_relative_path, entries) is not None


def _cleanup_permission_candidates(
    feature_root: Path | None,
    target_files: list[str],
) -> tuple[list[tuple[Path, Path, str]], list[str]]:
    repos = _dag_workspace_permission_repo_roots(feature_root)
    candidates: list[tuple[Path, Path, str]] = []
    skipped: list[str] = []
    seen: set[tuple[str, str]] = set()
    for raw_target in target_files:
        target = _strip_direct_route_line_suffix(raw_target)
        if not target:
            continue
        matched = False
        for repo in repos:
            repo_relative = _repo_relative_cleanup_target(repo, target)
            if not repo_relative:
                continue
            absolute = repo / repo_relative
            relevant = (
                absolute.exists()
                or _git_path_has_state(repo, repo_relative)
                or _cleanup_target_matches_repo_manifest(repo, repo_relative)
            )
            if not relevant:
                continue
            key = (str(repo.resolve(strict=False)), repo_relative)
            if key in seen:
                continue
            seen.add(key)
            candidates.append((repo, absolute, repo_relative))
            matched = True
        if repos and not matched:
            skipped.append(f"no direct workflow repo matched target {target}")
    return candidates, skipped


def _path_has_symlink_ancestor(repo: Path, target: Path) -> str:
    try:
        relative = target.resolve(strict=False).relative_to(repo.resolve(strict=False))
    except Exception:
        return "target escapes direct workflow repo"
    current = repo
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                return f"symlink path component is not permission-normalized: {current}"
        except OSError:
            return f"failed to inspect path component: {current}"
    return ""


def _nearest_existing_dir_for_permission(target: Path) -> Path | None:
    current = target if target.exists() and target.is_dir() else target.parent
    while True:
        if current.exists():
            return current if current.is_dir() else current.parent
        if current == current.parent:
            return None
        current = current.parent


def _chmod_for_agent_group(
    path: Path,
    *,
    repo: Path,
    report: dict[str, Any],
    reason: str,
    shared_gid: int | None,
) -> None:
    try:
        st = path.lstat()
    except OSError as exc:
        report["failed"].append({
            "path": str(path),
            "reason": reason,
            "error": f"stat failed: {exc}",
        })
        return
    if stat.S_ISLNK(st.st_mode):
        report["failed"].append({
            "path": str(path),
            "reason": reason,
            "error": "refusing to chmod symlink",
        })
        return

    changed: dict[str, Any] = {
        "path": str(path),
        "reason": reason,
        "old_mode": stat.filemode(st.st_mode),
    }
    try:
        if shared_gid is not None and st.st_gid != shared_gid:
            os.chown(path, -1, shared_gid)
            changed["group_changed"] = True
            st = path.lstat()
    except PermissionError as exc:
        report["skipped"].append({
            "path": str(path),
            "reason": reason,
            "error": f"group change skipped: {exc}",
        })
    except OSError as exc:
        report["skipped"].append({
            "path": str(path),
            "reason": reason,
            "error": f"group change skipped: {exc}",
        })

    mode = stat.S_IMODE(st.st_mode)
    if stat.S_ISDIR(st.st_mode):
        desired = mode | stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP | stat.S_ISGID
    else:
        desired = mode | stat.S_IRGRP | stat.S_IWGRP
    if desired == mode and not changed.get("group_changed"):
        report["already_ok"].append({
            "path": str(path),
            "reason": reason,
            "mode": stat.filemode(st.st_mode),
        })
        return
    try:
        os.chmod(path, desired)
    except OSError as exc:
        report["failed"].append({
            "path": str(path),
            "reason": reason,
            "error": f"chmod failed: {exc}",
            "mode": stat.filemode(st.st_mode),
        })
        return
    try:
        new_mode = stat.filemode(path.lstat().st_mode)
    except OSError:
        new_mode = oct(desired)
    changed["new_mode"] = new_mode
    report["changed"].append(changed)


def _workspace_permission_paths_for_target(repo: Path, target: Path) -> list[Path]:
    paths: list[Path] = []
    try:
        relative = target.resolve(strict=False).relative_to(repo.resolve(strict=False))
    except Exception:
        return paths
    current = repo
    if current.exists():
        paths.append(current)
    parts_for_parents = relative.parts
    if not (target.exists() and target.is_dir()):
        parts_for_parents = relative.parts[:-1]
    for part in parts_for_parents:
        current = current / part
        if current.exists():
            paths.append(current)
        else:
            break
    if target.exists():
        paths.append(target)
    git_dir = repo / ".git"
    git_index = git_dir / "index"
    for path in [git_dir, git_index]:
        if path.exists():
            paths.append(path)
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = path.resolve(strict=False).as_posix()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _cleanup_operator_reasons(repo: Path, target: Path) -> list[str]:
    reasons: list[str] = []
    symlink_reason = _path_has_symlink_ancestor(repo, target)
    if symlink_reason:
        reasons.append(symlink_reason)
        return reasons
    parent = _nearest_existing_dir_for_permission(target)
    if parent is None:
        reasons.append(f"no existing parent directory for cleanup target: {target}")
    elif not _path_agent_writable(parent, repo_path=repo):
        reasons.append(f"parent directory is not writable by repair agent: {parent}")
    if target.exists() and target.is_dir() and not _path_agent_writable(
        target,
        repo_path=repo,
    ):
        reasons.append(f"forbidden directory is not writable by repair agent: {target}")
    git_index = repo / ".git" / "index"
    if git_index.exists() and not _path_agent_writable(git_index, repo_path=repo):
        reasons.append(f"git index is not writable by repair agent: {git_index}")
    return reasons


def _normalize_feature_workspace_cleanup_permissions(
    feature_root: Path | None,
    target_files: list[str],
    *,
    reason: str,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "enabled": _dag_workspace_permission_repair_enabled(),
        "reason": reason,
        "target_files": list(target_files),
        "changed": [],
        "already_ok": [],
        "skipped": [],
        "failed": [],
        "operator_reasons": [],
        "operator_required": False,
    }
    if not report["enabled"]:
        report["operator_required"] = False
        report["skipped"].append({
            "reason": "workspace_permission_repair_disabled",
        })
        return report
    if feature_root is None:
        report["operator_required"] = True
        report["operator_reasons"].append("missing feature workspace root")
        return report

    candidates, skipped_targets = _cleanup_permission_candidates(
        feature_root,
        target_files,
    )
    for skipped in skipped_targets:
        report["skipped"].append({"reason": skipped})
    if target_files and not candidates:
        report["operator_required"] = True
        report["operator_reasons"].append(
            "no direct workflow repo target could be matched for permission repair"
        )
        return report

    shared_gid = _agent_shared_gid()
    for repo, target, repo_relative in candidates:
        symlink_reason = _path_has_symlink_ancestor(repo, target)
        if symlink_reason:
            report["failed"].append({
                "repo": str(repo),
                "path": str(target),
                "repo_relative_path": repo_relative,
                "error": symlink_reason,
            })
            continue
        for path in _workspace_permission_paths_for_target(repo, target):
            _chmod_for_agent_group(
                path,
                repo=repo,
                report=report,
                reason=reason,
                shared_gid=shared_gid,
            )
        for operator_reason in _cleanup_operator_reasons(repo, target):
            report["operator_reasons"].append(operator_reason)

    report["operator_reasons"] = list(dict.fromkeys(report["operator_reasons"]))
    report["operator_required"] = bool(report["failed"] or report["operator_reasons"])
    return report


async def _record_workspace_permission_repair(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry_label: str,
    report: dict[str, Any],
    *,
    context: str,
) -> None:
    payload = dict(report)
    payload.update({
        "group_idx": group_idx,
        "retry": retry_label,
        "context": context,
    })
    await runner.artifacts.put(
        f"dag-workspace-permission-repair:g{group_idx}:retry-{retry_label}:{context}",
        json.dumps(payload, indent=2),
        feature=feature,
    )


async def _normalize_direct_route_workspace_permissions(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry: int,
    feature_root: Path | None,
    route: DagDirectRepairRoute,
) -> DagDirectRepairRoute:
    if route.route != _MANIFEST_FORBIDDEN_CLEANUP_ROUTE:
        return route
    report = _normalize_feature_workspace_cleanup_permissions(
        feature_root,
        route.target_files,
        reason=route.route,
    )
    await _record_workspace_permission_repair(
        runner,
        feature,
        group_idx,
        str(retry),
        report,
        context="direct-route",
    )
    route = dataclasses.replace(
        route,
        operator_required=bool(report.get("operator_required")),
        workspace_permission_repair=report,
    )
    if report.get("operator_required"):
        route.reason = f"{route.reason}_workspace_permission_blocked"
    elif report.get("changed"):
        route.reason = f"{route.reason}_workspace_permission_normalized"
    return route


def _implementation_result_permission_targets(
    results: list[ImplementationResult],
) -> list[str]:
    return _dedupe_preserving_order([
        path for result in results
        for path in [*result.files_created, *result.files_modified]
        if path
    ])


def _manifest_cleanup_scoped_entries(
    feature_root: Path | None,
    target_files: list[str],
) -> tuple[list[Path], list[dict[str, str]], list[dict[str, str]]]:
    roots = _dag_candidate_file_roots(feature_root)
    manifest_entries = _dag_manifest_path_entries(roots)
    expected_entries = manifest_entries.get("expected_files", [])
    forbidden_entries = manifest_entries.get("forbidden_files", [])
    if not target_files:
        return roots, expected_entries, forbidden_entries
    scoped: list[dict[str, str]] = []
    seen: set[str] = set()
    repos = _dag_workspace_permission_repo_roots(feature_root)
    normalized_targets: set[str] = set()
    for target in target_files:
        clean = _strip_direct_route_line_suffix(target)
        if not clean:
            continue
        normalized_targets.add(clean)
        for repo in repos:
            repo_relative = _repo_relative_cleanup_target(repo, clean)
            if repo_relative:
                normalized_targets.add(repo_relative)
    for entry in forbidden_entries:
        forbidden_path = entry.get("path", "")
        if not forbidden_path:
            continue
        if any(
            _dag_path_matches_forbidden_file(target, {forbidden_path})
            or _dag_path_matches_forbidden_file(forbidden_path, {target})
            for target in normalized_targets
        ):
            if forbidden_path not in seen:
                seen.add(forbidden_path)
                scoped.append(entry)
    return roots, expected_entries, scoped


def _manifest_forbidden_cleanup_remaining_problems(
    feature_root: Path | None,
    target_files: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    roots, expected_entries, forbidden_entries = _manifest_cleanup_scoped_entries(
        feature_root,
        target_files,
    )
    problems = _dag_forbidden_workspace_path_problems(
        roots,
        forbidden_entries,
        expected_entries,
        include_all=True,
    )
    blocking: list[dict[str, Any]] = []
    staging_only: list[dict[str, Any]] = []
    for problem in problems:
        if (
            problem.get("git_state") == "unstaged_delete"
            and not problem.get("exists_on_disk")
        ):
            staging_only.append(problem)
            continue
        blocking.append(problem)
    return blocking, staging_only


def _manifest_cleanup_remaining_verdict(
    *,
    group_idx: int,
    retry: int,
    problems: list[dict[str, Any]],
) -> Verdict:
    concerns = [
        Issue(
            severity="blocker",
            description=(
                f"{_MANIFEST_FORBIDDEN_MARKER} remains after focused cleanup "
                f"retry-{retry}; forbidden product files still exist on disk or "
                "in a blocking git state. The workflow will retry focused cleanup "
                "instead of broad expanded verification."
            ),
            file=str(problem.get("path", "")),
        )
        for problem in problems[:10]
    ]
    return Verdict(
        approved=False,
        summary=(
            f"Group {group_idx} manifest-forbidden cleanup is incomplete after "
            f"retry-{retry}."
        ),
        concerns=concerns,
    )


def _dag_repo_hygiene_problems(repos_root: Path | None) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    for repo in _dag_direct_workflow_repo_roots(repos_root):
        repo_name = repo.name

        def _repo_rel(path: Path) -> str:
            try:
                rel = path.relative_to(repo)
            except ValueError:
                rel = path
            return f"{repo_name}/{rel.as_posix()}"

        def _ignored(path: Path) -> bool:
            parts = set(path.parts)
            return bool(
                parts
                & {
                    ".git",
                    ".git.backup-pre-cleanup",
                    ".venv",
                    "node_modules",
                    "__pycache__",
                    ".iriai-context",
                    ".iriai-evidence",
                }
            )

        try:
            for git_dir in repo.rglob(".git"):
                if git_dir == repo / ".git" or _ignored(git_dir.parent):
                    continue
                problems.append({
                    "task_id": "",
                    "artifact_key": "",
                    "path": _repo_rel(git_dir),
                    "reason": "embedded_git",
                    "exists": "true",
                    "exists_on_disk": True,
                    "tracked_or_staged": False,
                    "git_state": "embedded_git",
                    "repair_route": "product_cleanup_required",
                    "forbidden_rule": "",
                    "forbidden_path": "",
                    "forbidden_source": "repo hygiene guard",
                    "candidate_evidence": [],
                })
        except Exception:
            logger.debug("Failed to scan %s for embedded .git directories", repo, exc_info=True)

        try:
            result = subprocess.run(
                ["git", "-C", str(repo), "ls-files", "-s"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if not line.startswith("160000 "):
                        continue
                    path = line.split("\t", 1)[-1].strip()
                    if path:
                        problems.append({
                            "task_id": "",
                            "artifact_key": "",
                            "path": f"{repo_name}/{path}",
                            "reason": "gitlink",
                            "exists": "true",
                            "exists_on_disk": True,
                            "tracked_or_staged": True,
                            "git_state": "gitlink",
                            "repair_route": "product_cleanup_required",
                            "forbidden_rule": "",
                            "forbidden_path": "",
                            "forbidden_source": "repo hygiene guard",
                            "candidate_evidence": [],
                        })
        except Exception:
            logger.debug("Failed to scan %s for gitlinks", repo, exc_info=True)

        try:
            parked = list(repo.rglob("_pending_*.py")) + list(repo.rglob("*.PROPOSED"))
        except Exception:
            parked = []
        for path in sorted(parked):
            if _ignored(path):
                continue
            problems.append({
                "task_id": "",
                "artifact_key": "",
                "path": _repo_rel(path),
                "reason": "parked_implementation_file",
                "exists": "true",
                "exists_on_disk": True,
                "tracked_or_staged": _dag_path_tracked_or_staged(_repo_rel(path), [repo]),
                "git_state": "parked_implementation_file",
                "repair_route": "product_cleanup_required",
                "forbidden_rule": "",
                "forbidden_path": "",
                "forbidden_source": "repo hygiene guard",
                "candidate_evidence": [],
            })
    return problems


def _dag_workspace_writeability_problems(
    repos_root: Path | None,
    tasks: list[ImplementationTask],
) -> list[dict[str, Any]]:
    if repos_root is None:
        return []

    def _task_repo_root(task: ImplementationTask) -> Path:
        if task.repo_path:
            return repos_root / task.repo_path
        return repos_root

    def _target_path(task: ImplementationTask, raw_path: str) -> Path:
        normalized = raw_path.strip().replace("\\", "/").strip("/")
        repo_root = _task_repo_root(task)
        if task.repo_path and normalized.startswith(f"{task.repo_path.strip('/')}/"):
            normalized = normalized[len(task.repo_path.strip("/")) + 1:]
        return repo_root / normalized

    def _nearest_existing_dir(path: Path) -> Path | None:
        current = path if path.is_dir() else path.parent
        while True:
            if current.exists():
                return current if current.is_dir() else current.parent
            if current == current.parent:
                return None
            current = current.parent

    def _can_write_dir(directory: Path) -> bool:
        probe = directory / f".iriai-write-probe-{uuid4().hex}"
        try:
            with probe.open("xb") as fh:
                fh.write(b"")
            probe.unlink(missing_ok=True)
            return True
        except Exception:
            try:
                probe.unlink(missing_ok=True)
            except Exception:
                pass
            return False

    problems: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for task in tasks:
        entries: list[tuple[str, str]] = [
            (scope.path, scope.action)
            for scope in task.file_scope
            if scope.path and scope.action != "read_only"
        ]
        if not entries:
            entries = [(path, "modify") for path in task.files if path]
        for raw_path, action in entries:
            target = _target_path(task, raw_path)
            check_dir = _nearest_existing_dir(target)
            key = (task.id, raw_path, action)
            if key in seen:
                continue
            seen.add(key)
            if check_dir is None:
                problems.append({
                    "task_id": task.id,
                    "path": raw_path,
                    "action": action,
                    "reason": "writeability_missing_parent",
                    "directory": str(target.parent),
                })
                continue
            if not _can_write_dir(check_dir):
                problems.append({
                    "task_id": task.id,
                    "path": raw_path,
                    "action": action,
                    "reason": "writeability_denied",
                    "directory": str(check_dir),
                })
    return problems


def _dag_classify_task_drift(
    task_id: str,
    artifact_key: str,
    *,
    latest_result: ImplementationResult | None,
    in_memory_results: list[ImplementationResult],
    verifier_path_problems: list[dict[str, Any]] | None,
    expected_entries: list[dict[str, str]],
    forbidden_entries: list[dict[str, str]],
    roots: list[Path],
    context_text: str = "",
) -> DagTaskDriftRoute:
    matching_results: list[ImplementationResult] = []
    if latest_result is not None:
        matching_results.append(latest_result)
    matching_results.extend([
        result for result in in_memory_results
        if _dag_task_id_matches_or_alias(task_id, result.task_id)
    ])
    path_problems: list[dict[str, Any]] = []
    for result in matching_results:
        path_problems.extend(_dag_result_path_problems(
            result,
            roots,
            forbidden_entries,
            expected_entries,
        ))

    for problem in verifier_path_problems or []:
        problem_task_id = str(problem.get("task_id", ""))
        problem_key = str(problem.get("artifact_key", ""))
        if (
            problem_key == artifact_key
            or _dag_task_id_matches_or_alias(task_id, problem_task_id)
        ):
            path_problems.append(problem)

    forbidden_workspace_paths = _dag_forbidden_workspace_path_problems(
        roots,
        forbidden_entries,
        expected_entries,
        task_id=task_id,
        artifact_key=artifact_key,
        context_text=context_text,
        include_all=False,
    )
    candidate_evidence = _dag_candidate_evidence_for_task_id(
        task_id,
        expected_entries,
        roots,
    )
    cleanup_required = any(
        str(problem.get("repair_route", "")) == "product_cleanup_required"
        or bool(problem.get("exists_on_disk"))
        or bool(problem.get("tracked_or_staged"))
        or str(problem.get("exists", "")).lower() == "true"
        for problem in path_problems
        if problem.get("reason") in {"forbidden", "forbidden_workspace_path"}
    ) or bool(forbidden_workspace_paths)
    if cleanup_required:
        return DagTaskDriftRoute(
            task_id=task_id,
            artifact_key=artifact_key,
            route="product_cleanup_required",
            reason="forbidden_path_present_in_workspace_or_index",
            path_problems=path_problems,
            forbidden_workspace_paths=forbidden_workspace_paths,
            candidate_evidence=candidate_evidence,
        )

    if path_problems:
        return DagTaskDriftRoute(
            task_id=task_id,
            artifact_key=artifact_key,
            route="artifact_only",
            reason="db_only_dag_task_metadata_drift",
            path_problems=path_problems,
            forbidden_workspace_paths=forbidden_workspace_paths,
            candidate_evidence=candidate_evidence,
        )

    if latest_result is not None:
        reported = latest_result.files_created + latest_result.files_modified
        if latest_result.status in {"completed", "partial"} and reported:
            return DagTaskDriftRoute(
                task_id=task_id,
                artifact_key=artifact_key,
                route="artifact_only",
                reason="latest_dag_task_row_already_valid",
                path_problems=[],
                forbidden_workspace_paths=forbidden_workspace_paths,
                candidate_evidence=candidate_evidence,
            )

    return DagTaskDriftRoute(
        task_id=task_id,
        artifact_key=artifact_key,
        route="artifact_only",
        reason="dag_task_ref_without_detected_product_drift",
        path_problems=path_problems,
        forbidden_workspace_paths=forbidden_workspace_paths,
        candidate_evidence=candidate_evidence,
    )


async def _dag_task_drift_routes_for_refs(
    runner: WorkflowRunner,
    feature: Feature,
    refs: list[str],
    feature_root: Path | None,
    *,
    in_memory_results: list[ImplementationResult] | None = None,
    verifier_path_problems: list[dict[str, Any]] | None = None,
    context_text: str = "",
) -> dict[str, DagTaskDriftRoute]:
    roots = _dag_candidate_file_roots(feature_root)
    if not roots:
        return {}
    manifest_entries = _dag_manifest_path_entries(roots)
    expected_entries = manifest_entries.get("expected_files", [])
    forbidden_entries = manifest_entries.get("forbidden_files", [])
    routes: dict[str, DagTaskDriftRoute] = {}
    for artifact_key in _safe_dag_task_artifact_refs(refs):
        task_id = artifact_key.removeprefix("dag-task:")
        latest_result: ImplementationResult | None = None
        parent_record = await _dag_artifact_record_for_key(
            runner,
            feature,
            artifact_key,
        )
        if parent_record is not None and parent_record.get("value"):
            try:
                latest_result = ImplementationResult.model_validate_json(
                    str(parent_record.get("value", ""))
                )
            except Exception:
                latest_result = None
        routes[artifact_key] = _dag_classify_task_drift(
            task_id,
            artifact_key,
            latest_result=latest_result,
            in_memory_results=in_memory_results or [],
            verifier_path_problems=verifier_path_problems or [],
            expected_entries=expected_entries,
            forbidden_entries=forbidden_entries,
            roots=roots,
            context_text=context_text,
        )
    return routes


def _dag_product_cleanup_guidance(
    routes: dict[str, DagTaskDriftRoute],
) -> str:
    cleanup_routes = [
        route for route in routes.values()
        if route.route == "product_cleanup_required"
    ]
    if not cleanup_routes:
        return ""
    lines = [
        "\n\n## DAG Drift Routing",
        "The host classified this as product_cleanup_required, not DB-only artifact repair.",
        "Clean the product tree first. Do not edit DB artifacts directly; after your product-code repair succeeds, the host will append the corrected dag-task row.",
        "If the RCA also identifies a stale workflow artifact (for example a dag-fragment file_scope), the host will run an artifact-only follow-up after the product tree is clean.",
        "Remove or replace only manifest-forbidden files that are not also expected files, and preserve/port any acceptance coverage before reporting completion.",
        "Forbidden/stale paths that forced product cleanup:",
    ]
    seen_paths: set[str] = set()
    for route in cleanup_routes:
        for problem in [*route.path_problems, *route.forbidden_workspace_paths]:
            path = str(problem.get("path", ""))
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            lines.append(
                f"- `{path}` (artifact `{route.artifact_key}`, "
                f"exists_on_disk={problem.get('exists_on_disk', False)}, "
                f"tracked_or_staged={problem.get('tracked_or_staged', False)}, "
                f"forbidden_rule=`{problem.get('forbidden_rule') or problem.get('forbidden_path') or ''}`)"
            )
    return "\n".join(lines)


def _dag_repo_relative_product_path(path: str, roots: list[Path]) -> str:
    normalized = path.strip().replace("\\", "/").strip("/")
    for root in roots:
        root_name = root.name.strip("/")
        if not root_name or not normalized.startswith(f"{root_name}/"):
            continue
        candidate = normalized[len(root_name) + 1:]
        if _dag_reported_file_exists(candidate, roots):
            return candidate
    return normalized


async def _append_dag_task_rows_from_product_repair(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    source: str,
    bug_id: str,
    routes: dict[str, DagTaskDriftRoute],
    fix_result: ImplementationResult,
    feature_root: Path | None,
) -> dict[str, Any]:
    roots = _dag_candidate_file_roots(feature_root)
    report: dict[str, Any] = {
        "source": source,
        "bug_id": bug_id,
        "applied": [],
        "skipped": [],
    }
    if not roots:
        report["skipped"].append({"reason": "missing_feature_roots"})
    if fix_result.status not in {"completed", "partial"}:
        report["skipped"].append({
            "reason": "fix_result_status_not_completed_or_partial",
            "status": fix_result.status,
        })
    if report["skipped"]:
        await runner.artifacts.put(
            f"dag-task-product-reconcile:{source}:{bug_id}",
            json.dumps(report, indent=2),
            feature=feature,
        )
        return report

    manifest_entries = _dag_manifest_path_entries(roots)
    forbidden_entries = manifest_entries.get("forbidden_files", [])
    created_paths: list[str] = []
    modified_paths: list[str] = []
    for field_name, output in (
        ("files_created", created_paths),
        ("files_modified", modified_paths),
    ):
        for path in getattr(fix_result, field_name):
            if _dag_forbidden_match(path, forbidden_entries) is not None:
                continue
            category, normalized = _classify_dag_repair_path(
                path,
                roots,
                feature_root,
            )
            if category == "product" and normalized:
                output.append(
                    normalized if Path(path).is_absolute()
                    else _dag_repo_relative_product_path(path, roots)
                )
    created_paths = _dedupe_preserving_order(created_paths)
    modified_paths = [
        path for path in _dedupe_preserving_order(modified_paths)
        if path not in created_paths
    ]

    for artifact_key, route in routes.items():
        if route.route != "product_cleanup_required":
            continue
        task_id = artifact_key.removeprefix("dag-task:")
        remaining_forbidden = []
        for problem in [*route.path_problems, *route.forbidden_workspace_paths]:
            path = str(problem.get("path", ""))
            if not path:
                continue
            if (
                _dag_reported_file_exists(path, roots)
                or _dag_path_tracked_or_staged(path, roots)
            ):
                remaining_forbidden.append(problem)
        if remaining_forbidden:
            report["skipped"].append({
                "artifact_key": artifact_key,
                "task_id": task_id,
                "reason": "product_cleanup_still_required",
                "remaining_forbidden_paths": remaining_forbidden,
            })
            continue
        if not created_paths and not modified_paths:
            report["skipped"].append({
                "artifact_key": artifact_key,
                "task_id": task_id,
                "reason": "no_canonical_product_files_reported_by_product_repair",
            })
            continue
        replacement = ImplementationResult(
            task_id=task_id,
            summary=(
                "DAG task metadata reconciled after product cleanup: "
                f"{fix_result.summary}"
            ),
            status=(
                "completed" if fix_result.status == "completed" else "partial"
            ),
            files_created=created_paths,
            files_modified=modified_paths,
            notes=(
                f"Host-appended after product cleanup for {source}:{bug_id}. "
                "The product repair, not the host, handled source-tree cleanup."
            ),
            deviations=fix_result.deviations,
            self_reported_risks=fix_result.self_reported_risks,
        )
        valid, reason, validation = _validate_dag_task_artifact_update(
            artifact_key,
            replacement.model_dump_json(),
            roots,
            feature_root,
        )
        if valid is None:
            report["skipped"].append({
                "artifact_key": artifact_key,
                "task_id": task_id,
                "reason": reason,
                "validation": validation,
            })
            continue
        serialized = valid.model_dump_json()
        parent_record = await _dag_artifact_record_for_key(
            runner,
            feature,
            artifact_key,
        )
        latest_hash = (
            hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        )
        if (
            parent_record is not None
            and parent_record.get("sha256") == latest_hash
        ):
            report["applied"].append({
                "artifact_key": artifact_key,
                "task_id": task_id,
                "action": "already_current",
                "parent": parent_record,
                "validated_paths": validation,
            })
            continue
        await runner.artifacts.put(artifact_key, serialized, feature=feature)
        report["applied"].append({
            "artifact_key": artifact_key,
            "task_id": task_id,
            "action": "appended_dag_task_row",
            "parent": parent_record,
            "validated_paths": validation,
        })

    await runner.artifacts.put(
        f"dag-task-product-reconcile:{source}:{bug_id}",
        json.dumps(report, indent=2),
        feature=feature,
    )
    return report


def _dag_product_cleanup_ready_for_artifact_repair(
    report: dict[str, Any],
) -> bool:
    """Return true when product cleanup is done enough to repair metadata."""
    blocking_reasons = {
        "missing_feature_roots",
        "fix_result_status_not_completed_or_partial",
        "product_cleanup_still_required",
    }
    for item in report.get("skipped", []) or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("reason", "")) in blocking_reasons:
            return False
    return True


@dataclass(slots=True)
class DagTaskReconcileOutcome:
    results: list[object]
    verify_results_context: list[object]
    all_results: list[object]
    report: dict[str, Any]


@dataclass(slots=True)
class DagTaskSpecReconcileOutcome:
    tasks: list[ImplementationTask]
    report: dict[str, Any]


def _dag_task_id_aliases(task_id: str) -> set[str]:
    aliases = {task_id}
    for marker in ("TASK-", "T-"):
        idx = task_id.rfind(marker)
        if idx >= 0:
            aliases.add(task_id[idx:])
    return {alias for alias in aliases if alias}


def _dag_task_id_matches_or_alias(expected: str, actual: str) -> bool:
    actual = (actual or "").strip()
    return bool(actual) and actual in _dag_task_id_aliases(expected)


def _dag_candidate_evidence_for_task_id(
    task_id: str,
    expected_entries: list[dict[str, str]],
    roots: list[Path],
) -> list[dict[str, Any]]:
    aliases = _dag_task_id_aliases(task_id)
    evidence: list[dict[str, Any]] = []
    for entry in expected_entries:
        source = entry.get("source", "")
        if not any(alias and alias in source for alias in aliases):
            continue
        path = entry.get("path", "")
        evidence.append({
            "path": path,
            "source": source,
            "exists": _dag_reported_file_exists(path, roots),
            "config_path": entry.get("config_path", ""),
        })
    return evidence


def _dag_task_for_result(
    result: ImplementationResult,
    tasks_by_id: dict[str, ImplementationTask],
) -> ImplementationTask | None:
    matches = [
        task for task in tasks_by_id.values()
        if _dag_task_id_matches_or_alias(task.id, result.task_id)
    ]
    return matches[0] if len(matches) == 1 else None


def _dag_task_scope_path_variants(task: ImplementationTask) -> set[str]:
    variants: set[str] = set()
    repo_prefix = task.repo_path.strip().replace("\\", "/").strip("/")
    for path in [
        *(scope.path for scope in task.file_scope if scope.path),
        *task.files,
    ]:
        normalized = path.strip().replace("\\", "/").strip("/")
        if not normalized:
            continue
        variants.add(normalized)
        if repo_prefix and normalized.startswith(f"{repo_prefix}/"):
            variants.add(normalized[len(repo_prefix) + 1:])
        elif repo_prefix:
            variants.add(f"{repo_prefix}/{normalized}")
    return variants


def _dag_expected_entries_for_task(
    task: ImplementationTask,
    expected_entries: list[dict[str, str]],
) -> list[dict[str, str]]:
    aliases = _dag_task_id_aliases(task.id)
    matches: list[dict[str, str]] = []
    for entry in expected_entries:
        source = entry.get("source", "")
        if any(alias in source for alias in aliases):
            matches.append(entry)
    return matches


def _dag_path_matches_expected_entry(
    path: str,
    expected_entries: list[dict[str, str]],
) -> bool:
    normalized = path.strip().replace("\\", "/").strip("/")
    for entry in expected_entries:
        expected = entry.get("path", "").strip().replace("\\", "/").strip("/")
        if (
            normalized == expected
            or normalized.endswith(f"/{expected}")
            or expected.endswith(f"/{normalized}")
        ):
            return True
    return False


async def _dag_artifact_record_for_key(
    runner: WorkflowRunner,
    feature: Feature,
    key: str,
) -> dict[str, Any] | None:
    get_record = getattr(runner.artifacts, "get_record", None)
    if callable(get_record):
        record = await get_record(key, feature=feature)
        if record is None:
            return None
        value = str(record.get("value", ""))
        return {
            "id": record.get("id"),
            "created_at": str(record.get("created_at", "")),
            "value": value,
            "sha256": record.get("sha256")
            or hashlib.sha256(value.encode("utf-8")).hexdigest(),
        }
    value = await runner.artifacts.get(key, feature=feature)
    if value is None:
        return None
    text = str(value)
    return {
        "id": None,
        "created_at": "",
        "value": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _dag_artifact_record_changed(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> bool:
    if before is None:
        return after is not None
    if after is None:
        return True
    if before.get("id") is not None and after.get("id") is not None:
        return before.get("id") != after.get("id")
    return before.get("sha256") != after.get("sha256")


def _validate_dag_task_reconcile_candidate(
    task: ImplementationTask,
    candidate: ImplementationResult,
    roots: list[Path],
    feature_root: Path | None,
    *,
    expected_entries: list[dict[str, str]],
    forbidden_entries: list[dict[str, str]],
    source: str,
) -> tuple[ImplementationResult | None, str, list[dict[str, Any]]]:
    if not _dag_task_id_matches_or_alias(task.id, candidate.task_id):
        return None, "task_id_mismatch", [{
            "expected_task_id": task.id,
            "actual_task_id": candidate.task_id,
        }]
    if candidate.status not in {"completed", "partial"}:
        return None, "status_not_completed_or_partial", [{
            "status": candidate.status,
        }]

    reported_paths = _dedupe_preserving_order(
        candidate.files_created + candidate.files_modified
    )
    if not reported_paths:
        return None, "no_reported_files", []

    scope_paths = _dag_task_scope_path_variants(task)
    expected_task_entries = _dag_expected_entries_for_task(task, expected_entries)
    records: list[dict[str, Any]] = []
    for path in reported_paths:
        forbidden = _dag_forbidden_match(path, forbidden_entries)
        if forbidden is not None:
            return None, "forbidden_path", [{
                "path": path,
                "forbidden_path": forbidden.get("path", ""),
                "forbidden_source": forbidden.get("source", ""),
            }]
        category, normalized = _classify_dag_repair_path(
            path,
            roots,
            feature_root,
        )
        records.append({
            "path": path,
            "category": category,
            "normalized": normalized or "",
            "source": source,
        })
        if category == "rewritten_product":
            return None, "noncanonical_path", [{
                "path": path,
                "canonical": normalized or "",
            }]
        if category != "product":
            return None, f"{category}_path", [{
                "path": path,
                "normalized": normalized or "",
            }]
        path_variants = {
            path.strip().replace("\\", "/").strip("/"),
            (normalized or "").strip().replace("\\", "/").strip("/"),
        }
        allowed_by_scope = bool(scope_paths & path_variants)
        allowed_by_expected = any(
            _dag_path_matches_expected_entry(variant, expected_task_entries)
            for variant in path_variants
            if variant
        )
        if scope_paths and not allowed_by_scope and not allowed_by_expected:
            return None, "outside_task_scope", [{
                "path": path,
                "normalized": normalized or "",
                "task_id": task.id,
            }]

    return candidate.model_copy(update={"task_id": task.id}), "", records


def _dag_result_signature(result: ImplementationResult) -> tuple[Any, ...]:
    return (
        result.task_id,
        result.status,
        tuple(result.files_created),
        tuple(result.files_modified),
    )


def _replace_task_result_in_list(
    items: list[object],
    task: ImplementationTask,
    replacement: ImplementationResult,
) -> list[object]:
    replaced = False
    output: list[object] = []
    for item in items:
        if (
            isinstance(item, ImplementationResult)
            and _dag_task_id_matches_or_alias(task.id, item.task_id)
        ):
            if not replaced:
                output.append(replacement)
                replaced = True
            continue
        output.append(item)
    return output


def _dag_existing_valid_paths_by_field(
    result: ImplementationResult,
    roots: list[Path],
    feature_root: Path | None,
    forbidden_entries: list[dict[str, str]],
) -> dict[str, list[str]]:
    kept: dict[str, list[str]] = {"files_created": [], "files_modified": []}
    for field_name in ("files_created", "files_modified"):
        for path in getattr(result, field_name):
            if _dag_forbidden_match(path, forbidden_entries) is not None:
                continue
            category, _normalized = _classify_dag_repair_path(
                path,
                roots,
                feature_root,
            )
            if category == "product":
                kept[field_name].append(path)
    return {
        key: _dedupe_preserving_order(value)
        for key, value in kept.items()
    }


def _normalize_dag_reported_path_key(path: str) -> str:
    return path.strip().replace("\\", "/").strip("/")


def _dag_merge_reconcile_candidate(
    candidate: ImplementationResult,
    stale_results: list[ImplementationResult],
    roots: list[Path],
    feature_root: Path | None,
    forbidden_entries: list[dict[str, str]],
    expected_entries: list[dict[str, str]],
) -> ImplementationResult:
    if not stale_results:
        return candidate
    stale_path_keys = {
        _normalize_dag_reported_path_key(problem["path"])
        for result in stale_results
        for problem in _dag_result_path_problems(
            result,
            roots,
            forbidden_entries,
            expected_entries,
        )
    }
    existing: dict[str, list[str]] = {
        "files_created": [],
        "files_modified": [],
    }
    for stale_result in stale_results:
        valid_paths = _dag_existing_valid_paths_by_field(
            stale_result,
            roots,
            feature_root,
            forbidden_entries,
        )
        for field_name in ("files_created", "files_modified"):
            existing[field_name].extend(valid_paths[field_name])

    updates: dict[str, list[str]] = {}
    for field_name in ("files_created", "files_modified"):
        merged = [
            path for path in [
                *existing[field_name],
                *getattr(candidate, field_name),
            ]
            if _normalize_dag_reported_path_key(path) not in stale_path_keys
        ]
        updates[field_name] = _dedupe_preserving_order(merged)
    return candidate.model_copy(update=updates)


def _dag_expected_manifest_candidate(
    task: ImplementationTask,
    stale_result: ImplementationResult,
    roots: list[Path],
    feature_root: Path | None,
    expected_entries: list[dict[str, str]],
    forbidden_entries: list[dict[str, str]],
) -> ImplementationResult | None:
    task_entries = _dag_expected_entries_for_task(task, expected_entries)
    if not task_entries:
        return None
    existing = _dag_existing_valid_paths_by_field(
        stale_result,
        roots,
        feature_root,
        forbidden_entries,
    )
    expected_paths = _dedupe_preserving_order([
        entry["path"] for entry in task_entries
        if _dag_reported_file_exists(entry["path"], roots)
        and _dag_forbidden_match(entry["path"], forbidden_entries) is None
    ])
    if not expected_paths:
        return None
    created = _dedupe_preserving_order(
        existing["files_created"] + expected_paths
    )
    modified = [
        path for path in existing["files_modified"]
        if path not in created
    ]
    notes = (
        f"{stale_result.notes}\n\n" if stale_result.notes else ""
    ) + (
        "DAG task result metadata reconciled from verify-file-scope "
        "expected_files entries whose source references this task."
    )
    return stale_result.model_copy(update={
        "task_id": task.id,
        "summary": (
            "Reconciled stale DAG task metadata to canonical existing "
            "product paths from verify-file-scope expected_files."
        ),
        "status": "completed" if stale_result.status == "completed" else "partial",
        "files_created": created,
        "files_modified": modified,
        "notes": notes,
    })


async def _reconcile_dag_task_results(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry_label: str,
    group_tasks: list[ImplementationTask],
    *,
    results: list[object],
    verify_results_context: list[object],
    all_results: list[object],
    repair_results: list[ImplementationResult],
    feature_root: Path | None,
) -> DagTaskReconcileOutcome:
    roots = _dag_candidate_file_roots(feature_root)
    report: dict[str, Any] = {
        "group_idx": group_idx,
        "retry": retry_label,
        "applied": [],
        "skipped": [],
        "blockers": [],
    }
    if not roots or not group_tasks:
        report["skipped"].append({"reason": "missing_roots_or_tasks"})
        return DagTaskReconcileOutcome(
            results,
            verify_results_context,
            all_results,
            report,
        )

    manifest_entries = _dag_manifest_path_entries(roots)
    expected_entries = manifest_entries.get("expected_files", [])
    forbidden_entries = manifest_entries.get("forbidden_files", [])
    context_results = [
        item for item in verify_results_context
        if isinstance(item, ImplementationResult)
    ]
    candidate_pool = [
        *context_results,
        *repair_results,
    ]

    updated_results = list(results)
    updated_context = list(verify_results_context)
    updated_all_results = list(all_results)

    for task in group_tasks:
        artifact_key = f"dag-task:{task.id}"
        same_task_results = [
            result for result in candidate_pool
            if _dag_task_id_matches_or_alias(task.id, result.task_id)
        ]
        stale_results = [
            result for result in same_task_results
            if _dag_result_path_problems(
                result,
                roots,
                forbidden_entries,
                expected_entries,
            )
        ]
        parent_record = await _dag_artifact_record_for_key(
            runner,
            feature,
            artifact_key,
        )

        candidates: list[tuple[str, ImplementationResult, list[dict[str, Any]]]] = []
        if parent_record is not None:
            try:
                latest = ImplementationResult.model_validate_json(
                    parent_record["value"]
                )
            except Exception:
                latest = None
            if latest is not None:
                latest_problem = bool(_dag_result_path_problems(
                    latest,
                    roots,
                    forbidden_entries,
                    expected_entries,
                ))
                if not latest_problem:
                    valid, reason, validation = _validate_dag_task_reconcile_candidate(
                        task,
                        latest,
                        roots,
                        feature_root,
                        expected_entries=expected_entries,
                        forbidden_entries=forbidden_entries,
                        source="latest_db",
                    )
                    if valid is not None:
                        updated_results = _replace_task_result_in_list(
                            updated_results,
                            task,
                            valid,
                        )
                        updated_context = _replace_task_result_in_list(
                            updated_context,
                            task,
                            valid,
                        )
                        updated_all_results = _replace_task_result_in_list(
                            updated_all_results,
                            task,
                            valid,
                        )
                        report["applied"].append({
                            "task_id": task.id,
                            "artifact_key": artifact_key,
                            "source": "latest_db",
                            "action": (
                                "already_current"
                                if stale_results else "in_memory_replace_only"
                            ),
                            "parent": parent_record,
                            "validated_paths": validation,
                        })
                        continue
                    if stale_results:
                        report["skipped"].append({
                            "task_id": task.id,
                            "artifact_key": artifact_key,
                            "source": "latest_db",
                            "reason": reason,
                            "validation": validation,
                        })
                latest = _dag_merge_reconcile_candidate(
                    latest,
                    stale_results,
                    roots,
                    feature_root,
                    forbidden_entries,
                    expected_entries,
                )
                valid, reason, validation = _validate_dag_task_reconcile_candidate(
                    task,
                    latest,
                    roots,
                    feature_root,
                    expected_entries=expected_entries,
                    forbidden_entries=forbidden_entries,
                    source="latest_db",
                )
                if valid is not None:
                    candidates.append((
                        "latest_db_stale_drop"
                        if latest_problem and stale_results else "latest_db",
                        valid,
                        validation,
                    ))
                elif stale_results:
                    report["skipped"].append({
                        "task_id": task.id,
                        "artifact_key": artifact_key,
                        "source": "latest_db",
                        "reason": reason,
                        "validation": validation,
                    })

        for candidate in same_task_results:
            candidate_problem = bool(_dag_result_path_problems(
                candidate,
                roots,
                forbidden_entries,
                expected_entries,
            ))
            candidate = _dag_merge_reconcile_candidate(
                candidate,
                stale_results,
                roots,
                feature_root,
                forbidden_entries,
                expected_entries,
            )
            valid, reason, validation = _validate_dag_task_reconcile_candidate(
                task,
                candidate,
                roots,
                feature_root,
                expected_entries=expected_entries,
                forbidden_entries=forbidden_entries,
                source="same_task_result",
            )
            if valid is not None:
                candidates.append((
                    "same_task_stale_drop"
                    if candidate_problem and stale_results
                    else "same_task_result",
                    valid,
                    validation,
                ))
            elif stale_results:
                report["skipped"].append({
                    "task_id": task.id,
                    "artifact_key": artifact_key,
                    "source": "same_task_result",
                    "candidate_task_id": candidate.task_id,
                    "reason": reason,
                    "validation": validation,
                })

        if stale_results:
            manifest_candidate = _dag_expected_manifest_candidate(
                task,
                stale_results[0],
                roots,
                feature_root,
                expected_entries,
                forbidden_entries,
            )
            if manifest_candidate is not None:
                manifest_candidate = _dag_merge_reconcile_candidate(
                    manifest_candidate,
                    stale_results,
                    roots,
                    feature_root,
                    forbidden_entries,
                    expected_entries,
                )
                valid, reason, validation = _validate_dag_task_reconcile_candidate(
                    task,
                    manifest_candidate,
                    roots,
                    feature_root,
                    expected_entries=expected_entries,
                    forbidden_entries=forbidden_entries,
                    source="expected_files",
                )
                if valid is not None:
                    candidates.append(("expected_files", valid, validation))
                else:
                    report["skipped"].append({
                        "task_id": task.id,
                        "artifact_key": artifact_key,
                        "source": "expected_files",
                        "reason": reason,
                        "validation": validation,
                    })

        if stale_results:
            replacement_candidates = [
                candidate for candidate in candidates
                if not candidate[0].endswith("_stale_drop")
            ]
            if replacement_candidates:
                candidates = replacement_candidates

        unique: dict[tuple[Any, ...], tuple[str, ImplementationResult, list[dict[str, Any]]]] = {}
        for source, candidate, validation in candidates:
            unique.setdefault(_dag_result_signature(candidate), (
                source,
                candidate,
                validation,
            ))

        if not stale_results and not unique:
            continue
        if not unique:
            report["skipped"].append({
                "task_id": task.id,
                "artifact_key": artifact_key,
                "reason": "no_valid_candidate",
                "stale_paths": [
                    problem
                    for result in stale_results
                    for problem in _dag_result_path_problems(
                        result,
                        roots,
                        forbidden_entries,
                        expected_entries,
                    )
                ],
            })
            continue
        if len(unique) > 1:
            report["blockers"].append({
                "task_id": task.id,
                "artifact_key": artifact_key,
                "reason": "ambiguous_candidates",
                "candidate_count": len(unique),
            })
            continue

        source, replacement, validation = next(iter(unique.values()))
        updated_results = _replace_task_result_in_list(
            updated_results,
            task,
            replacement,
        )
        updated_context = _replace_task_result_in_list(
            updated_context,
            task,
            replacement,
        )
        updated_all_results = _replace_task_result_in_list(
            updated_all_results,
            task,
            replacement,
        )

        if not stale_results:
            report["applied"].append({
                "task_id": task.id,
                "artifact_key": artifact_key,
                "source": source,
                "action": "in_memory_replace_only",
                "validated_paths": validation,
            })
            continue

        serialized = replacement.model_dump_json()
        parent_latest = await _dag_artifact_record_for_key(
            runner,
            feature,
            artifact_key,
        )
        if _dag_artifact_record_changed(parent_record, parent_latest):
            report["skipped"].append({
                "task_id": task.id,
                "artifact_key": artifact_key,
                "source": source,
                "reason": "parent_artifact_changed",
                "parent": parent_record,
                "latest": parent_latest,
            })
            continue
        if (
            parent_latest is not None
            and parent_latest.get("sha256")
            == hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        ):
            report["applied"].append({
                "task_id": task.id,
                "artifact_key": artifact_key,
                "source": source,
                "action": "already_current",
                "parent": parent_latest,
                "validated_paths": validation,
            })
            continue

        await runner.artifacts.put(artifact_key, serialized, feature=feature)
        report["applied"].append({
            "task_id": task.id,
            "artifact_key": artifact_key,
            "source": source,
            "action": "appended_dag_task_row",
            "parent": parent_latest,
            "validated_paths": validation,
            "stale_paths": [
                problem
                for result in stale_results
                for problem in _dag_result_path_problems(
                    result,
                    roots,
                    forbidden_entries,
                    expected_entries,
                )
            ],
        })

    await runner.artifacts.put(
        f"dag-task-reconcile:g{group_idx}:retry-{retry_label}",
        json.dumps(report, indent=2),
        feature=feature,
    )
    return DagTaskReconcileOutcome(
        updated_results,
        updated_context,
        updated_all_results,
        report,
    )


def _dedupe_task_field(
    task: ImplementationTask,
    field_name: str,
    repairs: list[dict[str, Any]],
    *,
    repair_enabled: bool,
) -> None:
    values = getattr(task, field_name, None)
    if not isinstance(values, list) or not values:
        return
    if not all(isinstance(item, str) for item in values):
        return
    deduped = _dedupe_preserving_order(values)
    if len(deduped) == len(values):
        return
    repairs.append({
        "task_id": task.id,
        "field": field_name,
        "before": list(values),
        "after": deduped,
        "applied": repair_enabled,
    })
    if repair_enabled:
        setattr(task, field_name, deduped)


async def _run_dag_group_preflight(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry_label: str,
    group_tasks: list[ImplementationTask],
    results: list[object],
    *,
    feature_root: Path | None,
    known_task_ids: set[str] | None = None,
) -> Verdict | None:
    """Run deterministic DAG/group checks before spending verifier tokens.

    The preflight can repair only derived list duplication in the in-memory task
    specs used for the current verifier prompt. Anything semantic or ambiguous
    is reported as a blocker and left for the ordinary repair loop.
    """
    repair_enabled = _dag_preflight_repair_enabled()
    concerns: list[Issue] = []
    warnings: list[str] = []
    repairs: list[dict[str, Any]] = []
    path_problems: list[dict[str, Any]] = []

    seen_task_ids: set[str] = set()
    duplicate_task_ids: set[str] = set()
    for task in group_tasks:
        if task.id in seen_task_ids:
            duplicate_task_ids.add(task.id)
        seen_task_ids.add(task.id)
    for task_id in sorted(duplicate_task_ids):
        concerns.append(Issue(
            severity="blocker",
            description=f"duplicate task id in DAG group: {task_id}",
        ))

    group_task_ids = {task.id for task in group_tasks}
    all_known_task_ids = known_task_ids or group_task_ids
    for task in group_tasks:
        for field_name in (
            "requirement_ids",
            "step_ids",
            "journey_ids",
            "verification_gates",
            "dependencies",
            "files",
        ):
            _dedupe_task_field(
                task,
                field_name,
                repairs,
                repair_enabled=repair_enabled,
            )

        same_wave_dependencies = sorted(set(task.dependencies) & group_task_ids)
        if same_wave_dependencies:
            concerns.append(Issue(
                severity="blocker",
                description=(
                    f"{task.id} depends on task(s) in the same execution wave: "
                    f"{', '.join(same_wave_dependencies)}"
                ),
            ))
        dangling_dependencies = sorted(
            dep for dep in set(task.dependencies) if dep not in all_known_task_ids
        )
        if dangling_dependencies:
            concerns.append(Issue(
                severity="blocker",
                description=(
                    f"{task.id} references unknown dependency task id(s): "
                    f"{', '.join(dangling_dependencies)}"
                ),
            ))
        malformed_gates = sorted(
            gate for gate in set(task.verification_gates)
            if gate and not gate.startswith("AC-")
        )
        if malformed_gates:
            concerns.append(Issue(
                severity="major",
                description=(
                    f"{task.id} has malformed or non-canonical verification gate id(s): "
                    f"{', '.join(malformed_gates)}"
                ),
            ))

        scope_paths = {scope.path for scope in task.file_scope if scope.path}
        legacy_paths = set(task.files)
        if scope_paths and legacy_paths and not legacy_paths.issubset(scope_paths):
            warnings.append(
                f"{task.id} legacy files metadata has entries outside file_scope: "
                f"{', '.join(sorted(legacy_paths - scope_paths))}"
            )

    if dag_path_canonicalization_enabled():
        retired_refs = find_retired_backend_path_references(group_tasks)
        for ref in retired_refs:
            concerns.append(Issue(
                severity="major",
                description=(
                    f"{ref.task_id} still references retired backend path "
                    f"{ref.original!r} in {ref.field} after DAG path canonicalization; "
                    f"expected {ref.canonical!r}"
                ),
                file=ref.original,
            ))

    roots = _dag_candidate_file_roots(feature_root)
    if roots:
        hygiene_problems = _dag_repo_hygiene_problems(feature_root)
        path_problems.extend(hygiene_problems)
        for problem in hygiene_problems:
            path = str(problem.get("path", ""))
            reason = str(problem.get("reason", "repo_hygiene"))
            if reason == "embedded_git":
                description = (
                    "embedded .git directory exists inside a workflow repo; "
                    f"remove it before checkpointing: {path}"
                )
            elif reason == "gitlink":
                description = (
                    "gitlink/submodule entry exists inside a workflow repo; "
                    f"convert or remove it before checkpointing: {path}"
                )
            else:
                description = (
                    "parked implementation fallback exists outside its canonical "
                    f"package path; promote or remove it before checkpointing: {path}"
                )
            concerns.append(Issue(
                severity="major",
                description=description,
                file=path,
            ))

        manifest_entries = _dag_manifest_path_entries(roots)
        expected_entries = manifest_entries.get("expected_files", [])
        forbidden_entries = manifest_entries.get("forbidden_files", [])
        workspace_forbidden_problems = _dag_forbidden_workspace_path_problems(
            roots,
            forbidden_entries,
            expected_entries,
            include_all=True,
        )
        path_problems.extend(workspace_forbidden_problems)
        for problem in workspace_forbidden_problems:
            path = problem["path"]
            if (
                problem.get("git_state") == "unstaged_delete"
                and not problem.get("exists_on_disk")
            ):
                description = (
                    f"{_MANIFEST_FORBIDDEN_MARKER} required; "
                    "manifest-forbidden path has been deleted from disk but "
                    "the deletion is not staged; stage the deletion before DAG "
                    f"metadata can be considered repaired: {path}"
                )
            else:
                description = (
                    f"{_MANIFEST_FORBIDDEN_MARKER} required; "
                    "manifest-forbidden path exists in the feature workspace or "
                    "git index; product cleanup is required before DAG metadata "
                    f"can be considered repaired: {path}"
                )
            concerns.append(Issue(
                severity="major",
                description=description,
                file=path,
            ))
        for task in group_tasks:
            task_spec_problems = _dag_task_spec_path_problems(
                task,
                roots,
                forbidden_entries,
                expected_entries,
            )
            path_problems.extend(task_spec_problems)
            for problem in task_spec_problems:
                path = problem["path"]
                field = problem.get("field", "file_scope")
                product_cleanup_prefix = (
                    f"{_MANIFEST_FORBIDDEN_MARKER} required; "
                    if problem.get("repair_route") == "product_cleanup_required"
                    else ""
                )
                source_refs = [
                    f"dag-task:{task.id}",
                    *(
                        [str(problem["source_artifact_ref"])]
                        if problem.get("source_artifact_ref") else []
                    ),
                ]
                concerns.append(Issue(
                    severity="major",
                    description=(
                        f"{product_cleanup_prefix}{task.id} task spec {field} references a "
                        "manifest-forbidden/stale path; source artifacts: "
                        f"{', '.join(source_refs)}; repair the DAG/source artifact "
                        "instead of recreating this path: "
                        f"{path}"
                    ),
                    file=path,
                ))
        source_artifact_problems = _dag_task_bearing_source_artifact_path_problems(
            runner,
            feature,
            group_idx,
            group_tasks,
            roots,
            forbidden_entries,
            expected_entries,
        )
        path_problems.extend(source_artifact_problems)
        for problem in source_artifact_problems:
            path = str(problem.get("path", ""))
            source_ref = str(problem.get("source_artifact_ref", ""))
            source_path = str(problem.get("source_artifact_path", ""))
            field = str(problem.get("field", "file_scope"))
            product_cleanup_prefix = (
                f"{_MANIFEST_FORBIDDEN_MARKER} required; "
                if problem.get("repair_route") == "product_cleanup_required"
                else ""
            )
            concerns.append(Issue(
                severity="major",
                description=(
                    f"{product_cleanup_prefix}DAG source artifact task spec metadata references a "
                    "manifest-forbidden/stale path; source artifact: "
                    f"{source_ref or source_path}; field {field}; repair the "
                    "DAG/source artifact instead of recreating this path: "
                    f"{path}"
                ),
                file=path,
            ))
        for result in results:
            if not isinstance(result, ImplementationResult):
                continue
            if result.status not in {"completed", "partial"}:
                concerns.append(Issue(
                    severity="major",
                    description=(
                        f"{result.task_id} implementation result status is {result.status!r}"
                    ),
                ))
            result_problems = _dag_result_path_problems(
                result,
                roots,
                forbidden_entries,
                expected_entries,
            )
            path_problems.extend(result_problems)
            for problem in result_problems:
                path = problem["path"]
                if problem["reason"] == "forbidden":
                    product_cleanup_prefix = (
                        f"{_MANIFEST_FORBIDDEN_MARKER} required; "
                        if problem.get("repair_route") == "product_cleanup_required"
                        else ""
                    )
                    concerns.append(Issue(
                        severity="major",
                        description=(
                            f"{product_cleanup_prefix}{result.task_id} reports changed file that is "
                            "forbidden/stale by verify-file-scope.expected-files.json; "
                            f"source artifact: dag-task:{result.task_id}; "
                            "repair stale task metadata instead of creating "
                            f"this path: {path}"
                        ),
                        file=path,
                    ))
                else:
                    concerns.append(Issue(
                        severity="major",
                        description=(
                            f"{result.task_id} reports changed file that is missing "
                            f"from the feature workspace; source artifact: "
                            f"dag-task:{result.task_id}; path: {path}"
                        ),
                        file=path,
                    ))

    closure_hints: dict[str, Any] = {}
    if path_problems:
        closure_hints = _dag_artifact_closure_scan(
            runner,
            feature,
            group_idx,
            group_tasks,
            path_problems,
        ).to_record()

    artifact_key = f"dag-repair-preflight:g{group_idx}:retry-{retry_label}"
    await runner.artifacts.put(
        artifact_key,
        json.dumps({
            "group_idx": group_idx,
            "retry": retry_label,
            "repair_enabled": repair_enabled,
            "approved": not concerns,
            "concerns": [issue.model_dump(mode="json") for issue in concerns],
            "warnings": warnings,
            "repairs": repairs,
            "path_problems": path_problems,
            "closure_hints": closure_hints,
            "task_ids": [task.id for task in group_tasks],
            "result_task_ids": [
                result.task_id
                for result in results
                if isinstance(result, ImplementationResult)
            ],
        }),
        feature=feature,
    )
    if not concerns:
        return None
    return Verdict(
        approved=False,
        summary=(
            "Programmatic DAG preflight failed before model verification. "
            "Only deterministic structural checks ran; semantic verification was not attempted."
        ),
        concerns=concerns,
        suggestions=[
            "Fix the structural DAG/group issue, then retry the normal verifier.",
        ],
    )


def _issue_dedupe_key(issue: Issue) -> tuple[str, str, int]:
    return (
        issue.description.strip().lower(),
        issue.file.strip(),
        int(issue.line or 0),
    )


def _gap_dedupe_key(gap: Gap) -> tuple[str, str, str]:
    return (
        gap.description.strip().lower(),
        gap.category.strip().lower(),
        gap.plan_reference.strip(),
    )


_DAG_VERDICT_PATH_RE = re.compile(
    r"(?:path:|this path:|creating this path:)\s+(`?)([^`\s,;]+)\1",
    re.IGNORECASE,
)
_DAG_VERDICT_DAG_TASK_RE = re.compile(r"dag-task:([A-Za-z0-9_.:@-]+)")
_DAG_VERDICT_DAG_FRAGMENT_RE = re.compile(
    r"dag-fragment:([A-Za-z0-9_.@-]+):slice-(\d+)"
)


def _dag_path_problems_from_verdict(
    verdict: Verdict,
    group_tasks: list[ImplementationTask],
) -> list[dict[str, Any]]:
    task_ids = [task.id for task in group_tasks]
    problems: list[dict[str, Any]] = []
    for issue in verdict.concerns:
        description = issue.description or ""
        if not description:
            continue
        artifact_match = _DAG_VERDICT_DAG_TASK_RE.search(description)
        fragment_match = _DAG_VERDICT_DAG_FRAGMENT_RE.search(description)
        task_id = ""
        for candidate in task_ids:
            if candidate and candidate in description:
                task_id = candidate
                break
        artifact_key = ""
        if artifact_match:
            artifact_key = f"dag-task:{artifact_match.group(1)}"
            task_id = task_id or artifact_match.group(1)
        path = issue.file.strip()
        if not path:
            path_match = _DAG_VERDICT_PATH_RE.search(description)
            if path_match:
                path = path_match.group(2).strip()
        if not (path or artifact_key or fragment_match):
            continue
        reason = "verdict_issue"
        lowered = description.lower()
        if "task spec" in lowered and (
            "forbidden" in lowered or "stale" in lowered
        ):
            reason = "forbidden_task_spec"
        elif "forbidden" in lowered or "stale" in lowered:
            reason = "forbidden"
        source_ref = (
            f"dag-fragment:{fragment_match.group(1)}:slice-{fragment_match.group(2)}"
            if fragment_match else ""
        )
        problems.append({
            "task_id": task_id,
            "artifact_key": artifact_key or (f"dag-task:{task_id}" if task_id else ""),
            "path": path,
            "file": issue.file.strip(),
            "reason": reason,
            "exists_on_disk": False,
            "tracked_or_staged": False,
            "repair_route": "artifact_only",
            "source_artifact_ref": source_ref,
            "verdict_description": description,
        })
    return problems


def _dag_deterministic_artifact_only_path_problems(
    problems: list[dict[str, Any]],
    feature_root: Path | None,
) -> list[dict[str, Any]]:
    roots = _dag_candidate_file_roots(feature_root)
    deterministic_reasons = {
        "forbidden",
        "forbidden_task_result",
        "forbidden_task_spec",
        "forbidden_task_spec_source_artifact",
    }
    artifact_only: list[dict[str, Any]] = []
    for problem in problems:
        reason = str(problem.get("reason", "") or "")
        if reason not in deterministic_reasons:
            continue
        path = str(problem.get("path", "") or problem.get("file", "") or "")
        exists = bool(problem.get("exists_on_disk"))
        tracked_or_staged = bool(problem.get("tracked_or_staged"))
        if path and roots:
            exists = exists or _dag_reported_file_exists(path, roots)
            tracked_or_staged = tracked_or_staged or _dag_path_tracked_or_staged(
                path,
                roots,
            )
        if exists or tracked_or_staged:
            return []
        artifact_only.append({
            **problem,
            "exists_on_disk": False,
            "tracked_or_staged": False,
            "repair_route": "artifact_only",
        })
    return artifact_only


def _deterministic_dag_artifact_repair_group(
    group_idx: int,
    retry: int,
    verdict: Verdict,
    group_tasks: list[ImplementationTask],
    verifier_path_problems: list[dict[str, Any]],
    feature_root: Path | None,
) -> PlannedBugGroup | None:
    artifact_only = _dag_deterministic_artifact_only_path_problems(
        verifier_path_problems,
        feature_root,
    )
    if not artifact_only:
        return None

    task_ids = _dedupe_preserving_order([
        str(problem.get("task_id", "") or "")
        for problem in artifact_only
        if str(problem.get("task_id", "") or "").strip()
    ])
    artifact_refs = _dedupe_preserving_order([
        ref for problem in artifact_only
        for ref in [
            str(problem.get("artifact_key", "") or ""),
            str(problem.get("source_artifact_ref", "") or ""),
        ]
        if ref.strip()
    ])
    tasks_by_id = {task.id: task for task in group_tasks}
    for task_id in task_ids:
        task = tasks_by_id.get(task_id)
        if task is None:
            continue
        source_ref = _dag_task_spec_source_ref(task)
        if source_ref:
            artifact_refs.append(source_ref)
    artifact_refs = _dedupe_preserving_order([
        *artifact_refs,
        f".iriai-context/g{group_idx}-expanded-verify-r{retry}-task-specs.md",
        f".iriai-context/g{group_idx}-expanded-verify-r{retry}-changed-files.md",
    ])

    issue_indices = [
        idx for idx, issue in enumerate(verdict.concerns)
        if any(
            str(problem.get("path", "") or "")
            and str(problem.get("path", "") or "") in issue.description
            for problem in artifact_only
        )
    ] or list(range(len(verdict.concerns)))
    group_id = (
        "dag-task-spec-projection-drift"
        if any(
            str(problem.get("reason", "") or "") in {
                "forbidden_task_spec",
                "forbidden_task_spec_source_artifact",
            }
            for problem in artifact_only
        )
        else "dag-task-result-metadata-drift"
    )
    issue_text = "\n".join([
        f"- {problem.get('reason')}: task={problem.get('task_id') or '(unknown)'} "
        f"path={problem.get('path') or problem.get('file') or '(none)'} "
        f"artifact={problem.get('artifact_key') or '(none)'} "
        f"source={problem.get('source_artifact_ref') or '(none)'}"
        for problem in artifact_only
    ])
    rca = RootCauseAnalysis(
        hypothesis=(
            "Deterministic preflight found artifact-only stale DAG metadata or "
            "generated task-spec projection drift; no forbidden product file is "
            "present on disk or in the git index."
        ),
        evidence=[
            "Host preflight path problems are all deterministic stale/forbidden DAG metadata.",
            "Product cleanup is not required because the flagged retired paths are absent from disk/index.",
            *artifact_refs[:8],
        ],
        affected_files=artifact_refs,
        proposed_approach=(
            "Use the artifact repair/projection reconciliation path. Rehydrate "
            "task specs from canonical DAG fragments when possible, repair stale "
            "source DAG artifacts when they still contain retired path fields, "
            "and invalidate generated expanded-verify task-spec/changed-files "
            "snapshots instead of sending a product-code implementer."
        ),
        confidence="high",
    )
    return PlannedBugGroup(
        group=BugGroup(
            group_id=group_id,
            likely_root_cause=rca.hypothesis,
            issue_indices=issue_indices,
            severity="blocker",
            affected_files_hint=artifact_refs,
        ),
        rca=rca,
        issue_text=issue_text,
        rca_key=f"host-deterministic:dag-repair:g{group_idx}:retry-{retry}:{group_id}",
    )


def _dag_authority_preflight_key(
    group_idx: int,
    retry_label: str,
) -> str:
    return f"dag-repair-preflight:g{group_idx}:retry-{retry_label}"


async def _dag_authority_load_preflight_report(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry_label: str,
) -> tuple[str, dict[str, Any]]:
    key = _dag_authority_preflight_key(group_idx, retry_label)
    try:
        raw = await runner.artifacts.get(key, feature=feature)
    except Exception:
        logger.debug("Failed to load DAG authority preflight report %s", key, exc_info=True)
        return key, {}
    if not raw:
        return key, {}
    try:
        payload = json.loads(str(raw))
    except Exception:
        logger.debug("Invalid DAG authority preflight report JSON %s", key, exc_info=True)
        return key, {}
    return key, payload if isinstance(payload, dict) else {}


def _dag_authority_path_problem_route(
    problems: list[dict[str, Any]],
    artifact_only: list[dict[str, Any]],
) -> tuple[str, str]:
    if not problems:
        return _DAG_AUTHORITY_SEMANTIC_ROUTE, "no_path_problems"
    if any(
        str(problem.get("reason", "")) in {"embedded_git", "gitlink", "parked_fallback"}
        for problem in problems
    ):
        return _DAG_AUTHORITY_REPO_BLOCKER_ROUTE, "repo_hygiene_path_problem"
    if any(
        str(problem.get("repair_route", "")) == "product_cleanup_required"
        or bool(problem.get("exists_on_disk"))
        or bool(problem.get("tracked_or_staged"))
        or str(problem.get("git_state", "")) in {
            "clean_tracked",
            "unstaged_delete",
            "staged_add",
            "untracked",
        }
        for problem in problems
    ):
        return _DAG_AUTHORITY_PRODUCT_WORKSPACE_ROUTE, (
            "path_problem_requires_product_workspace_cleanup"
        )
    if not artifact_only:
        return _DAG_AUTHORITY_SEMANTIC_ROUTE, "no_deterministic_artifact_only_problem"
    if any(
        str(problem.get("reason", "")) in {
            "forbidden_task_spec",
            "forbidden_task_spec_source_artifact",
        }
        for problem in artifact_only
    ):
        return _DAG_AUTHORITY_TASK_SPEC_PROJECTION_ROUTE, "task_spec_projection_drift"
    if any(
        str(problem.get("source_artifact_ref", "")).strip()
        and not str(problem.get("artifact_key", "")).startswith("dag-task:")
        for problem in artifact_only
    ):
        return _DAG_AUTHORITY_SOURCE_ARTIFACT_ROUTE, "source_artifact_drift"
    return _DAG_AUTHORITY_DB_TASK_RESULT_ROUTE, "db_task_result_drift"


def _dag_authority_task_refs_from_path_problems(
    problems: list[dict[str, Any]],
) -> list[str]:
    refs: list[str] = []
    for problem in problems:
        artifact_key = str(problem.get("artifact_key", "") or "").strip()
        task_id = str(problem.get("task_id", "") or "").strip()
        if _is_dag_task_artifact_key(artifact_key):
            refs.append(artifact_key)
        elif task_id:
            ref = f"dag-task:{task_id}"
            if _is_dag_task_artifact_key(ref):
                refs.append(ref)
    return _safe_dag_task_artifact_refs(refs)


async def _dag_authority_latest_records(
    runner: WorkflowRunner,
    feature: Feature,
    refs: list[str],
) -> dict[str, dict[str, Any] | None]:
    return {
        ref: await _dag_artifact_record_for_key(runner, feature, ref)
        for ref in refs
    }


def _dag_authority_blocked_verdict(
    group_idx: int,
    retry: int,
    *,
    route: str,
    reason: str,
    target_refs: list[str],
    detail: str,
) -> Verdict:
    refs = ", ".join(target_refs) if target_refs else "(none)"
    return Verdict(
        approved=False,
        summary=(
            f"Group {group_idx} authority gate blocked retry {retry}: "
            f"{route} did not produce a valid authoritative repair."
        ),
        concerns=[
            Issue(
                severity="blocker",
                description=(
                    "DAG authority gate blocked broad repair. "
                    f"Route: {route}; reason: {reason}; target refs: {refs}. "
                    f"{detail}"
                ),
            )
        ],
        suggestions=[
            "Repair the authoritative DAG/task artifact state, then retry verification.",
        ],
    )


async def _record_dag_authority_gate(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry: int,
    report: dict[str, Any],
) -> None:
    await runner.artifacts.put(
        f"dag-authority-gate:g{group_idx}:retry-{retry}",
        json.dumps(report, indent=2),
        feature=feature,
    )


def _dag_authority_synthetic_result(
    group_idx: int,
    retry: int,
    report: dict[str, Any],
) -> ImplementationResult:
    return ImplementationResult(
        task_id=f"DAG-AUTHORITY-REPAIR-g{group_idx}-r{retry}",
        summary=(
            "DAG authority gate repaired deterministic workflow metadata "
            f"via {report.get('status', 'unknown')}."
        ),
        status="completed",
        files_created=[],
        files_modified=[],
        notes=json.dumps(report, indent=2),
    )


def _dag_authority_applied_dag_task_updates(
    repair_result: ImplementationResult,
) -> list[dict[str, Any]]:
    try:
        record = json.loads(repair_result.notes or "{}")
    except Exception:
        return []
    application = record.get("artifact_update_application", {})
    if not isinstance(application, dict):
        return []
    applied = application.get("applied_updates", [])
    if not isinstance(applied, list):
        return []
    return [
        item for item in applied
        if isinstance(item, dict)
        and item.get("artifact_kind") == "dag_task"
        and _is_dag_task_artifact_key(str(item.get("artifact_key", "")))
    ]


async def _attempt_dag_authority_gate_repair(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry: int,
    verdict: Verdict,
    group_tasks: list[ImplementationTask],
    *,
    results: list[object],
    verify_results_context: list[object],
    all_results: list[object],
    feature_root: Path | None,
    runtime: str | None,
    feedback: str,
    known_task_ids: set[str] | None = None,
) -> DagAuthorityGateOutcome:
    preflight_key, preflight_report = await _dag_authority_load_preflight_report(
        runner,
        feature,
        group_idx,
        "initial" if retry == 0 else str(retry - 1),
    )
    path_problems = [
        problem for problem in preflight_report.get("path_problems", [])
        if isinstance(problem, dict)
    ]
    if not path_problems:
        path_problems = _dag_path_problems_from_verdict(verdict, group_tasks)
    artifact_only = _dag_deterministic_artifact_only_path_problems(
        path_problems,
        feature_root,
    )
    route, reason = _dag_authority_path_problem_route(path_problems, artifact_only)
    target_refs = _dag_authority_task_refs_from_path_problems(artifact_only)
    report: dict[str, Any] = {
        "group_idx": group_idx,
        "retry": retry,
        "route": route,
        "reason": reason,
        "status": "no_action",
        "preflight_artifact_key": preflight_key,
        "parallel_repair_enabled": _dag_parallel_repair_enabled(),
        "parallel_repair_affects_authority_gate": False,
        "path_problems": path_problems,
        "artifact_only_path_problems": artifact_only,
        "target_refs": target_refs,
        "task_ids": [task.id for task in group_tasks],
        "latest_records_before": {},
        "latest_records_after": {},
        "reconcile_reports": [],
        "artifact_repair": None,
        "post_repair_preflight_key": "",
    }

    if route == _DAG_AUTHORITY_SEMANTIC_ROUTE:
        await _record_dag_authority_gate(runner, feature, group_idx, retry, report)
        return DagAuthorityGateOutcome(route=route, status="no_action", reason=reason, report=report)
    if route in {
        _DAG_AUTHORITY_PRODUCT_WORKSPACE_ROUTE,
        _DAG_AUTHORITY_REPO_BLOCKER_ROUTE,
    }:
        report["status"] = "delegated_to_existing_product_or_operator_route"
        await _record_dag_authority_gate(runner, feature, group_idx, retry, report)
        return DagAuthorityGateOutcome(route=route, status=report["status"], reason=reason, report=report)
    if route in {
        _DAG_AUTHORITY_TASK_SPEC_PROJECTION_ROUTE,
        _DAG_AUTHORITY_SOURCE_ARTIFACT_ROUTE,
    }:
        spec_reconcile = await _reconcile_dag_task_specs(
            runner,
            feature,
            group_idx,
            f"{retry}-authority-spec",
            group_tasks,
            feature_root=feature_root,
        )
        report["task_spec_reconcile"] = spec_reconcile.report
        post_spec_label = f"{retry}-authority-spec"
        post_spec = await _run_dag_group_preflight(
            runner,
            feature,
            group_idx,
            post_spec_label,
            spec_reconcile.tasks,
            verify_results_context,
            feature_root=feature_root,
            known_task_ids=known_task_ids,
        )
        report["post_repair_preflight_key"] = _dag_authority_preflight_key(
            group_idx,
            post_spec_label,
        )
        if post_spec is None:
            report["status"] = "repaired_by_task_spec_reconcile"
            await _record_dag_authority_gate(runner, feature, group_idx, retry, report)
            return DagAuthorityGateOutcome(
                route=route,
                status=report["status"],
                reason=reason,
                repair_results=[_dag_authority_synthetic_result(group_idx, retry, report)],
                report=report,
            )
        if not target_refs:
            detail = (
                "Task-spec/source artifact drift remained after task-spec "
                "reconciliation and no safe dag-task DB target was available."
            )
            blocked = _dag_authority_blocked_verdict(
                group_idx,
                retry,
                route=route,
                reason=reason,
                target_refs=target_refs,
                detail=detail,
            )
            report["status"] = "blocked_source_artifact_or_projection_drift"
            report["blocked_verdict"] = blocked.model_dump(mode="json")
            await _record_dag_authority_gate(runner, feature, group_idx, retry, report)
            return DagAuthorityGateOutcome(
                route=route,
                status=report["status"],
                reason=reason,
                blocked_verdict=blocked,
                report=report,
            )
        report["status"] = "task_spec_reconcile_incomplete_try_dag_task_refs"
    if not target_refs:
        report["status"] = "blocked_no_dag_task_refs"
        blocked = _dag_authority_blocked_verdict(
            group_idx,
            retry,
            route=route,
            reason=reason,
            target_refs=target_refs,
            detail=(
                "Preflight reported deterministic artifact-only drift, but the "
                "host could not derive any dag-task artifact refs to repair."
            ),
        )
        report["blocked_verdict"] = blocked.model_dump(mode="json")
        await _record_dag_authority_gate(runner, feature, group_idx, retry, report)
        return DagAuthorityGateOutcome(
            route=route,
            status=report["status"],
            reason=reason,
            blocked_verdict=blocked,
            report=report,
        )

    report["latest_records_before"] = await _dag_authority_latest_records(
        runner,
        feature,
        target_refs,
    )
    reconcile = await _reconcile_dag_task_results(
        runner,
        feature,
        group_idx,
        f"{retry}-authority-reconcile",
        group_tasks,
        results=results,
        verify_results_context=verify_results_context,
        all_results=all_results,
        repair_results=[],
        feature_root=feature_root,
    )
    report["reconcile_reports"].append(reconcile.report)
    reconcile_label = f"{retry}-authority-reconcile"
    post_reconcile = await _run_dag_group_preflight(
        runner,
        feature,
        group_idx,
        reconcile_label,
        group_tasks,
        reconcile.verify_results_context,
        feature_root=feature_root,
        known_task_ids=known_task_ids,
    )
    report["post_repair_preflight_key"] = _dag_authority_preflight_key(
        group_idx,
        reconcile_label,
    )
    if post_reconcile is None:
        report["status"] = "repaired_by_reconcile"
        report["latest_records_after"] = await _dag_authority_latest_records(
            runner,
            feature,
            target_refs,
        )
        await _record_dag_authority_gate(runner, feature, group_idx, retry, report)
        return DagAuthorityGateOutcome(
            route=route,
            status=report["status"],
            reason=reason,
            repair_results=[_dag_authority_synthetic_result(group_idx, retry, report)],
            report=report,
        )

    planned = _deterministic_dag_artifact_repair_group(
        group_idx,
        retry,
        post_reconcile,
        group_tasks,
        artifact_only,
        feature_root,
    )
    rca = (
        planned.rca
        if planned is not None else RootCauseAnalysis(
            hypothesis=(
                "Deterministic preflight found stale DB-backed dag-task "
                "ImplementationResult metadata with no product file drift."
            ),
            evidence=[
                "The flagged paths are absent from disk and the git index.",
                *target_refs,
            ],
            affected_files=target_refs,
            proposed_approach=(
                "Append corrected dag-task ImplementationResult rows with "
                "canonical existing product paths."
            ),
            confidence="high",
        )
    )

    def _authority_actor_builder(
        base: AgentActor,
        suffix: str,
        **kwargs: Any,
    ) -> AgentActor:
        return _make_parallel_actor(
            base,
            f"dag-g{group_idx}-r{retry}-authority-{suffix}",
            runtime=runtime,
            workspace_path=kwargs.get("workspace_path"),
        )

    repair_result = await _run_rca_dag_task_artifact_repair(
        runner,
        feature,
        source=f"dag-authority-gate:g{group_idx}:retry-{retry}",
        bug_id=f"g{group_idx}-r{retry}-dag-task-result-drift",
        verdict_text=feedback,
        rca=rca,
        fixer=implementer,
        feature_root=feature_root,
        phase_name="implementation",
        actor_builder=_authority_actor_builder,
        target_refs=target_refs,
    )
    applied_dag_task_updates = _dag_authority_applied_dag_task_updates(repair_result)
    report["artifact_repair"] = {
        "result": repair_result.model_dump(mode="json"),
        "applied_dag_task_updates": applied_dag_task_updates,
    }
    if not applied_dag_task_updates:
        detail = (
            "Artifact repair returned no valid applied dag-task updates. "
            "The repair likely used the wrong nested schema or omitted "
            "ImplementationResult.files_created/files_modified; broad product "
            "repair was intentionally skipped."
        )
        blocked = _dag_authority_blocked_verdict(
            group_idx,
            retry,
            route=route,
            reason="artifact_repair_applied_no_dag_task_updates",
            target_refs=target_refs,
            detail=detail,
        )
        report["status"] = "blocked_artifact_repair_no_applied_updates"
        report["blocked_verdict"] = blocked.model_dump(mode="json")
        report["latest_records_after"] = await _dag_authority_latest_records(
            runner,
            feature,
            target_refs,
        )
        await _record_dag_authority_gate(runner, feature, group_idx, retry, report)
        return DagAuthorityGateOutcome(
            route=route,
            status=report["status"],
            reason="artifact_repair_applied_no_dag_task_updates",
            blocked_verdict=blocked,
            report=report,
        )

    after_reconcile = await _reconcile_dag_task_results(
        runner,
        feature,
        group_idx,
        f"{retry}-authority-artifact",
        group_tasks,
        results=results,
        verify_results_context=verify_results_context,
        all_results=all_results,
        repair_results=[],
        feature_root=feature_root,
    )
    report["reconcile_reports"].append(after_reconcile.report)
    after_label = f"{retry}-authority-artifact"
    post_artifact = await _run_dag_group_preflight(
        runner,
        feature,
        group_idx,
        after_label,
        group_tasks,
        after_reconcile.verify_results_context,
        feature_root=feature_root,
        known_task_ids=known_task_ids,
    )
    report["post_repair_preflight_key"] = _dag_authority_preflight_key(
        group_idx,
        after_label,
    )
    report["latest_records_after"] = await _dag_authority_latest_records(
        runner,
        feature,
        target_refs,
    )
    if post_artifact is None:
        report["status"] = "repaired_by_artifact_repair"
        await _record_dag_authority_gate(runner, feature, group_idx, retry, report)
        return DagAuthorityGateOutcome(
            route=route,
            status=report["status"],
            reason=reason,
            repair_results=[_dag_authority_synthetic_result(group_idx, retry, report)],
            report=report,
        )

    blocked = _dag_authority_blocked_verdict(
        group_idx,
        retry,
        route=route,
        reason="preflight_still_failed_after_authority_repair",
        target_refs=target_refs,
        detail=(
            "A dag-task update was applied, but deterministic preflight still "
            "reported stale/forbidden task metadata."
        ),
    )
    report["status"] = "blocked_preflight_still_failed"
    report["blocked_verdict"] = blocked.model_dump(mode="json")
    await _record_dag_authority_gate(runner, feature, group_idx, retry, report)
    return DagAuthorityGateOutcome(
        route=route,
        status=report["status"],
        reason="preflight_still_failed_after_authority_repair",
        blocked_verdict=blocked,
        report=report,
    )


def _prefix_lens_issue(spec: DagVerifyLensSpec, issue: Issue) -> Issue:
    return issue.model_copy(update={
        "description": f"[{spec.label} Lens] {issue.description}",
    })


def _prefix_lens_gap(spec: DagVerifyLensSpec, gap: Gap) -> Gap:
    return gap.model_copy(update={
        "description": f"[{spec.label} Lens] {gap.description}",
    })


def _merge_dag_expanded_verify_verdicts(
    base: Verdict,
    lens_verdicts: list[tuple[DagVerifyLensSpec, Verdict]],
) -> Verdict:
    """Merge read-only lens findings into the normal verifier verdict.

    The normal verifier remains authoritative: this helper only broadens the
    repair queue after a failed verdict and never turns a failed verdict into an
    approval.
    """
    concerns = list(base.concerns)
    concern_keys = {_issue_dedupe_key(c) for c in concerns}
    gaps = list(base.gaps)
    gap_keys = {_gap_dedupe_key(g) for g in gaps}

    checks = list(base.checks)
    check_keys = {
        (check.criterion.strip().lower(), check.result.strip().upper(), check.detail.strip().lower())
        for check in checks
    }
    suggestions = list(base.suggestions)
    suggestion_keys = {s.strip().lower() for s in suggestions}
    lens_summaries: list[str] = []

    for spec, verdict in lens_verdicts:
        lens_summaries.append(
            f"{spec.label}: {'approved' if _is_approved(verdict) else 'findings'} — {verdict.summary}"
        )
        for concern in verdict.concerns:
            key = _issue_dedupe_key(concern)
            if key in concern_keys:
                continue
            concern_keys.add(key)
            concerns.append(_prefix_lens_issue(spec, concern))
        for gap in verdict.gaps:
            key = _gap_dedupe_key(gap)
            if key in gap_keys:
                continue
            gap_keys.add(key)
            gaps.append(_prefix_lens_gap(spec, gap))
        for check in verdict.checks:
            key = (
                check.criterion.strip().lower(),
                check.result.strip().upper(),
                check.detail.strip().lower(),
            )
            if key in check_keys:
                continue
            check_keys.add(key)
            checks.append(check.model_copy(update={
                "criterion": f"[{spec.label} Lens] {check.criterion}",
            }))
        for suggestion in verdict.suggestions:
            prefixed = f"[{spec.label} Lens] {suggestion}"
            key = prefixed.strip().lower()
            if key in suggestion_keys:
                continue
            suggestion_keys.add(key)
            suggestions.append(prefixed)

    lens_summary_text = (
        "\n".join(f"- {summary}" for summary in lens_summaries)
        if lens_summaries else "- No expanded lens verdicts were produced."
    )
    merged_summary = (
        f"{base.summary}\n\n"
        "Expanded read-only verification ran after the normal verifier failed. "
        "These findings are advisory inputs to repair; checkpoint authority remains "
        "with the final aggregate group verifier.\n"
        f"{lens_summary_text}"
    )
    return base.model_copy(update={
        "approved": False if not _is_approved(base) else all(_is_approved(v) for _, v in lens_verdicts),
        "summary": merged_summary,
        "concerns": concerns,
        "gaps": gaps,
        "checks": checks,
        "suggestions": suggestions,
    })


async def _run_expanded_dag_verify_lenses(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry: int,
    base_verdict: Verdict,
    results: list[object],
    files: list[str],
    tasks: list[ImplementationTask],
    *,
    runtime: str | None = None,
    feature_root: Path | None = None,
) -> Verdict:
    if not _dag_expanded_verify_enabled():
        logger.info(
            "DAG expanded verify disabled by %s=0 for group=%d retry=%d",
            DAG_EXPANDED_VERIFY_ENV,
            group_idx,
            retry,
        )
        return base_verdict

    specs = _dag_verify_lens_specs()
    await _log_feature_event(
        runner,
        feature.id,
        "dag_expanded_verify_start",
        "implementation",
        content=f"g{group_idx}:retry-{retry}",
        metadata={
            "group_idx": group_idx,
            "retry": retry,
            "lens_slugs": [spec.slug for spec in specs],
            "runtime": runtime,
        },
    )
    results_summary = "\n\n".join(to_str(r) for r in results)
    file_list = "\n".join(f"- `{path}`" for path in files) if files else "_No changed files were reported._"
    workspace_hint = (
        f"Feature repos root: `{feature_root}`\n"
        "Read only from this workspace. Do not edit files or run mutating commands."
        if feature_root else
        "No feature workspace path was available. Use artifact context only."
    )

    context = await _build_prompt_context_package(
        runner,
        feature,
        title=f"Expanded DAG Verify — Group {group_idx} Retry {retry}",
        file_stem=f"g{group_idx}-expanded-verify-r{retry}",
        intro_lines=[
            "Run read-only DAG verification lenses after the normal group verifier failed.",
            "The normal verifier remains authoritative; lenses broaden discovery before RCA/fix.",
        ],
        sections=[
            ("normal-verdict", "Normal Verifier Verdict", _format_feedback("Verify", base_verdict)),
            ("implementation-results", "Implementation Results", results_summary),
            ("changed-files", "Changed Files", file_list),
            ("task-specs", "DAG Group Task Specs", _format_dag_group_task_specs(tasks)),
            ("workspace", "Workspace", workspace_hint),
        ],
    )
    context_prompt = _context_package_prompt(context)

    async def _run_lens(spec: DagVerifyLensSpec) -> tuple[DagVerifyLensSpec, Verdict | None, str | None]:
        artifact_key = f"dag-repair-lens:g{group_idx}:{spec.slug}:retry-{retry}"
        lens_runtime = _dag_repair_runtime_for(f"lens:{spec.slug}", runtime)
        actor = _make_parallel_actor(
            spec.actor,
            f"dag-lens-g{group_idx}-r{retry}-{spec.slug}",
            runtime=lens_runtime,
            workspace_path=str(feature_root) if feature_root else None,
        )
        prompt = (
            f"## Expanded DAG Verify Lens: {spec.label}\n\n"
            f"{context_prompt}"
            "You are a read-only verifier lens. Do not modify files. Do not run "
            "formatters, installers, migrations, or write-producing commands. If you "
            "run commands, use inspection-only commands.\n\n"
            f"### Lens Focus\n{spec.focus}\n\n"
            "Return a Verdict for this lens only. Report concrete blocker/major "
            "issues that should be fixed before this group checkpoints. Put lower "
            "severity observations in suggestions unless they invalidate the current "
            "group. Lens approval is advisory and cannot checkpoint the group."
        )
        try:
            verdict = await runner.run(
                Ask(
                    actor=actor,
                    prompt=prompt,
                    output_type=Verdict,
                ),
                feature,
                phase_name="implementation",
            )
            if not isinstance(verdict, Verdict):
                raise TypeError(f"lens returned {type(verdict).__name__}, expected Verdict")
            await runner.artifacts.put(
                artifact_key,
                json.dumps({
                    "lens": spec.slug,
                    "label": spec.label,
                    "status": "completed",
                    "runtime": lens_runtime,
                    "verdict": verdict.model_dump(mode="json"),
                }),
                feature=feature,
            )
            return spec, verdict, None
        except Exception as exc:
            logger.warning(
                "DAG expanded verify lens failed group=%d retry=%d lens=%s: %s",
                group_idx,
                retry,
                spec.slug,
                exc,
            )
            await runner.artifacts.put(
                artifact_key,
                json.dumps({
                    "lens": spec.slug,
                    "label": spec.label,
                    "status": "failed",
                    "runtime": lens_runtime,
                    "error": str(exc),
                }),
                feature=feature,
            )
            return spec, None, str(exc)

    gathered = await _asyncio.gather(*[_run_lens(spec) for spec in specs])
    successful = [(spec, verdict) for spec, verdict, _err in gathered if verdict is not None]
    failures = [
        {
            "lens": spec.slug,
            "label": spec.label,
            "runtime": _dag_repair_runtime_for(f"lens:{spec.slug}", runtime),
            "error": err,
        }
        for spec, verdict, err in gathered
        if verdict is None and err
    ]
    merged = _merge_dag_expanded_verify_verdicts(base_verdict, successful)
    await runner.artifacts.put(
        f"dag-repair-expanded-verify:g{group_idx}:retry-{retry}",
        json.dumps({
            "group_idx": group_idx,
            "retry": retry,
            "enabled": True,
            "normal_approved": _is_approved(base_verdict),
            "merged_approved": _is_approved(merged),
            "successful_lenses": [
                {
                    "lens": spec.slug,
                    "runtime": _dag_repair_runtime_for(f"lens:{spec.slug}", runtime),
                }
                for spec, _verdict in successful
            ],
            "failed_lenses": failures,
            "concerns": len(merged.concerns),
            "gaps": len(merged.gaps),
            "checks": len(merged.checks),
            "merged_verdict": merged.model_dump(mode="json"),
        }),
        feature=feature,
    )
    logger.info(
        "DAG expanded verify completed group=%d retry=%d successful_lenses=%d "
        "failed_lenses=%d merged_concerns=%d merged_gaps=%d",
        group_idx,
        retry,
        len(successful),
        len(failures),
        len(merged.concerns),
        len(merged.gaps),
    )
    await _log_feature_event(
        runner,
        feature.id,
        "dag_expanded_verify_finish",
        "implementation",
        content=f"g{group_idx}:retry-{retry}",
        metadata={
            "group_idx": group_idx,
            "retry": retry,
            "successful_lenses": [spec.slug for spec, _verdict in successful],
            "failed_lenses": [item["lens"] for item in failures],
            "concern_count": len(merged.concerns),
            "gap_count": len(merged.gaps),
        },
    )
    return merged


def _discover_repo_roots_under(repos_root: Path) -> list[Path]:
    repos: list[Path] = []
    for git_dir in repos_root.rglob(".git"):
        repo_dir = git_dir.parent
        if repo_dir == repos_root:
            continue
        if not git_dir.exists():
            continue
        repos.append(repo_dir)
    return sorted(set(repos))


def _resolve_fix_workspace(
    feature_root: Path | None,
    affected_files: list[str],
) -> str | None:
    """Find the worktree path for a fix agent based on affected files."""
    return _resolve_fix_workspace_from_root(feature_root, affected_files)


def _resolve_fix_workspace_from_root(
    repos_root: Path | None,
    affected_files: list[str],
) -> str | None:
    """Find the repo worktree path for an execution agent based on affected files."""
    if not repos_root or not affected_files:
        return None
    for f in affected_files:
        parts = Path(f).parts
        for depth in range(1, min(len(parts), 6)):
            candidate = repos_root / Path(*parts[:depth])
            if (candidate / ".git").exists():
                return str(candidate)
    return None


async def _repo_heads_for_root(repos_root: Path | None) -> dict[str, str]:
    """Return current HEAD commits keyed by repo-relative path for *repos_root*."""
    if not repos_root:
        return {}
    heads: dict[str, str] = {}
    for repo_dir in _discover_repo_roots_under(repos_root):
        try:
            rel_path = str(repo_dir.relative_to(repos_root))
            heads[rel_path] = await _run_git(repo_dir, "rev-parse", "HEAD")
        except Exception:
            logger.warning("Failed to read HEAD for %s", repo_dir, exc_info=True)
    return heads


async def _plan_bug_groups(
    runner: WorkflowRunner,
    feature: Feature,
    verdict: Verdict,
    source: str,
    prior_attempts: list[BugFixAttempt],
    *,
    phase_name: str = "implementation",
    repos_root: Path | None = None,
    rca_runtime: str | None = None,
    actor_factory: Callable[[AgentActor, str], AgentActor] | None = None,
    strategy_context: RepairStrategyDecision | None = None,
) -> PlannedBugDispatch:
    """Plan multi-issue bug work without mutating the codebase."""
    attempt_number = sum(1 for a in prior_attempts if a.source_verdict == source) + 1
    feature_root = repos_root or _get_feature_root(runner, feature)
    prior_context = _format_prior_attempts(prior_attempts, context_base=feature_root)
    workspace_hint = (
        f"\n\n### Workspace\nFeature repos at: `{feature_root}`\n"
        if feature_root else ""
    )
    strategy_prompt = ""
    if strategy_context is not None:
        stable_blockers = "\n".join(
            f"- [{item.severity}] {item.description}{f' ({item.file}:{item.line})' if item.file else ''}"
            for item in strategy_context.stable_blockers
        ) or "- none recorded"
        new_blockers = "\n".join(
            f"- [{item.severity}] {item.description}{f' ({item.file}:{item.line})' if item.file else ''}"
            for item in strategy_context.new_blockers
        ) or "- none recorded"
        failing_checks = "\n".join(
            f"- {item.criterion}: {item.result}{f' — {item.detail}' if item.detail else ''}"
            for item in strategy_context.failing_checks
        ) or "- none recorded"
        required_files = "\n".join(f"- `{path}`" for path in strategy_context.required_files) or "- none recorded"
        required_checks = "\n".join(f"- {item}" for item in strategy_context.required_checks) or "- none recorded"
        similar_hints = "\n".join(f"- {item}" for item in strategy_context.similar_cluster_hints) or "- none recorded"
        strategy_prompt = (
            "\n\n### Current Repair Strategy\n"
            f"Mode: {strategy_context.strategy_mode}\n"
            f"Reasoning: {strategy_context.reasoning}\n"
            f"Why not ordinary retry: {strategy_context.why_not_ordinary_retry or 'not provided'}\n"
            f"Stable failure family: {strategy_context.stable_failure_family or 'not yet named'}\n"
            f"Bundle summary: {strategy_context.bundle_summary or 'not recorded'}\n\n"
            f"Stable blockers:\n{stable_blockers}\n\n"
            f"New blockers:\n{new_blockers}\n\n"
            f"Failing checks:\n{failing_checks}\n\n"
            f"Required files:\n{required_files}\n\n"
            f"Required checks:\n{required_checks}\n\n"
            f"Similar cluster hints:\n{similar_hints}\n\n"
            "Use this strategy context to choose a materially different and better-targeted next approach."
        )

    triage_base = AgentActor(name="bug-triager", role=_triage_role)
    triage_actor = (
        actor_factory(triage_base, "triage")
        if actor_factory is not None
        else _make_parallel_actor(triage_base, "triage", runtime=rca_runtime)
    )
    indexed_issues = _format_indexed_issues(verdict)
    triage: BugTriage = await runner.run(
        Ask(
            actor=triage_actor,
            prompt=(
                f"## Verdict from: {source}\n\n"
                f"### Summary\n{verdict.summary}\n\n"
                f"### Issues (reference by index)\n{indexed_issues}\n\n"
                "Group ALL issues by likely root cause. Every index must appear "
                "in exactly one group. Use issue_indices for [C*] entries and "
                "gap_indices for [G*] entries."
                f"{strategy_prompt}"
            ),
            output_type=BugTriage,
        ),
        feature,
        phase_name=phase_name,
    )

    await runner.artifacts.put(
        f"bug-triage:{source}:attempt-{attempt_number}",
        to_str(triage),
        feature=feature,
    )

    if not triage.groups:
        return PlannedBugDispatch(
            attempt_number=attempt_number,
            triage=triage,
            groups=[],
            fixable_groups=[],
            contradiction_groups=[],
            schedule=[],
            dispatch_key=f"bug-dispatch:{source}:attempt-{attempt_number}",
            strategy_mode=strategy_context.strategy_mode if strategy_context else "ordinary_retry",
            strategy_reason=strategy_context.reasoning if strategy_context else "",
            required_checks=list(strategy_context.required_checks) if strategy_context else [],
            required_files=list(strategy_context.required_files) if strategy_context else [],
            stable_blocker_summary=strategy_context.bundle_summary if strategy_context else "",
            similar_cluster_hints=list(strategy_context.similar_cluster_hints) if strategy_context else [],
        )

    rca_tasks = [
        Ask(
            actor=(
                actor_factory(root_cause_analyst, f"rca-{group.group_id}")
                if actor_factory is not None
                else _make_parallel_actor(
                    root_cause_analyst,
                    f"rca-{group.group_id}",
                    runtime=rca_runtime,
                )
            ),
            prompt=(
                f"## Bug Group: {group.group_id}\n\n"
                f"### Likely Root Cause (from triage)\n{group.likely_root_cause}\n\n"
                f"### Issues in this group\n{_extract_group_issues(verdict, group)}\n\n"
                f"### Full Verdict Summary\n{verdict.summary}\n\n"
                "Investigate the root cause of these specific issues. Read the "
                "relevant code, trace the data flow, and identify the exact "
                "point of failure. Propose a conceptual fix approach — do NOT "
                "implement anything."
                f"{strategy_prompt}{prior_context}{workspace_hint}"
            ),
            output_type=RootCauseAnalysis,
        )
        for group in triage.groups
    ]
    if len(rca_tasks) == 1:
        rca_results = [await runner.run(rca_tasks[0], feature, phase_name=phase_name)]
    else:
        rca_results = await runner.parallel(rca_tasks, feature)

    groups: list[PlannedBugGroup] = []
    fixable_groups: list[PlannedBugGroup] = []
    contradiction_groups: list[PlannedBugGroup] = []
    for group, result in zip(triage.groups, rca_results):
        if not isinstance(result, RootCauseAnalysis):
            continue
        rca_key = f"bug-rca:{source}:{group.group_id}:attempt-{attempt_number}"
        await runner.artifacts.put(rca_key, to_str(result), feature=feature)
        planned = PlannedBugGroup(
            group=group,
            rca=result,
            issue_text=_extract_group_issues(verdict, group),
            rca_key=rca_key,
        )
        groups.append(planned)
        if (
            result.confidence == "contradiction"
            and not _planned_needs_dag_task_artifact_repair(planned)
        ):
            contradiction_groups.append(planned)
        else:
            fixable_groups.append(planned)

    schedule = _compute_fix_schedule([(item.group.group_id, item.rca) for item in fixable_groups])
    dispatch_key = f"bug-dispatch:{source}:attempt-{attempt_number}"
    dispatch_record = {
        "source": source,
        "attempt_number": attempt_number,
        "total_issues": len(verdict.concerns) + len(verdict.gaps),
        "groups": [
            {
                "group_id": item.group.group_id,
                "likely_root_cause": item.group.likely_root_cause,
                "severity": item.group.severity,
                "affected_files_hint": item.group.affected_files_hint,
                "issue_count": len(item.group.issue_indices) + len(item.group.gap_indices),
                "rca": {
                    "hypothesis": item.rca.hypothesis,
                    "evidence": item.rca.evidence,
                    "affected_files": item.rca.affected_files,
                    "proposed_approach": item.rca.proposed_approach,
                    "confidence": item.rca.confidence,
                },
            }
            for item in groups
        ],
        "schedule": [{"round": idx, "group_ids": ids} for idx, ids in enumerate(schedule)],
        "total_rounds": len(schedule),
    }
    await runner.artifacts.put(dispatch_key, json.dumps(dispatch_record), feature=feature)
    return PlannedBugDispatch(
        attempt_number=attempt_number,
        triage=triage,
        groups=groups,
        fixable_groups=fixable_groups,
        contradiction_groups=contradiction_groups,
        schedule=schedule,
        dispatch_key=dispatch_key,
        strategy_mode=strategy_context.strategy_mode if strategy_context else "ordinary_retry",
        strategy_reason=strategy_context.reasoning if strategy_context else "",
        required_checks=list(strategy_context.required_checks) if strategy_context else [],
        required_files=list(strategy_context.required_files) if strategy_context else [],
        stable_blocker_summary=strategy_context.bundle_summary if strategy_context else "",
        similar_cluster_hints=list(strategy_context.similar_cluster_hints) if strategy_context else [],
    )


async def _attempt_parallel_dag_repair(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    retry: int,
    verdict: Verdict,
    group_tasks: list[ImplementationTask],
    *,
    feature_root: Path | None,
    impl_runtime: str | None,
    rca_runtime: str | None,
    feedback: str,
    fix_context: str = "",
) -> list[ImplementationResult] | None:
    """Try the faster DAG repair path: triage, parallel RCA, scheduled fixes.

    This does not approve anything. The caller must still run the final aggregate
    DAG verifier after these fixes.
    """
    if not _dag_parallel_repair_enabled():
        return None
    total_issues = len(verdict.concerns) + len(verdict.gaps)
    if total_issues <= 1:
        return None

    dag_rca_runtime = _dag_repair_runtime_for("dag-rca", rca_runtime)
    dag_fix_runtime = _dag_repair_runtime_for("dag-fix", impl_runtime)
    dag_reverify_runtime = _dag_repair_runtime_for("dag-focused-reverify", rca_runtime)

    def _dag_actor_factory(base: AgentActor, suffix: str) -> AgentActor:
        role = "dag-triage" if suffix == "triage" else "dag-rca"
        return _make_parallel_actor(
            base,
            f"dag-g{group_idx}-r{retry}-{suffix}",
            runtime=_dag_repair_runtime_for(role, dag_rca_runtime),
        )

    source = f"dag-repair:g{group_idx}:retry-{retry}"
    try:
        dispatch = await _plan_bug_groups(
            runner,
            feature,
            verdict,
            source,
            [],
            phase_name="implementation",
            repos_root=feature_root,
            rca_runtime=dag_rca_runtime,
            actor_factory=_dag_actor_factory,
        )
    except Exception as exc:
        logger.warning(
            "DAG parallel repair triage/RCA failed group=%d retry=%d: %s",
            group_idx,
            retry,
            exc,
        )
        return None

    await runner.artifacts.put(
        f"dag-repair-triage:g{group_idx}:retry-{retry}",
        to_str(dispatch.triage),
        feature=feature,
    )
    for planned in dispatch.groups:
        await runner.artifacts.put(
            f"dag-repair-rca:g{group_idx}:{planned.group.group_id}:retry-{retry}",
            to_str(planned.rca),
            feature=feature,
        )
    await _log_feature_event(
        runner,
        feature.id,
        "dag_repair_triage_done",
        "implementation",
        content=f"g{group_idx}:retry-{retry}",
        metadata={
            "group_idx": group_idx,
            "retry": retry,
            "dispatch_key": dispatch.dispatch_key,
            "group_count": len(dispatch.groups),
            "fixable_group_count": len(dispatch.fixable_groups),
            "contradiction_group_count": len(dispatch.contradiction_groups),
            "rca_group_ids": [planned.group.group_id for planned in dispatch.groups],
        },
    )

    fixable_groups = list(dispatch.fixable_groups)
    decision_results: list[ImplementationResult] = []
    resolved_contradictions: list[dict[str, Any]] = []
    rejected_contradictions: list[dict[str, Any]] = []
    artifact_repair_records: list[dict[str, Any]] = []
    human_needed_contradictions: list[str] = []
    quarantined_contradiction_groups: list[PlannedBugGroup] = []
    auto_resolve = False
    verifier_path_problems = _dag_path_problems_from_verdict(verdict, group_tasks)
    deterministic_artifact_group = _deterministic_dag_artifact_repair_group(
        group_idx,
        retry,
        verdict,
        group_tasks,
        verifier_path_problems,
        feature_root,
    )
    if deterministic_artifact_group is not None and not any(
        _planned_needs_dag_task_artifact_repair(planned)
        or _planned_needs_source_artifact_repair(planned)
        or _dag_metadata_only_repair_candidate(planned)
        for planned in dispatch.groups
    ):
        dispatch.groups.append(deterministic_artifact_group)
        dispatch.fixable_groups.append(deterministic_artifact_group)
        dispatch.triage.groups.append(deterministic_artifact_group.group)
        fixable_groups.append(deterministic_artifact_group)
    dag_task_artifact_candidate_groups = [
        planned for planned in fixable_groups
        if _planned_needs_dag_task_artifact_repair(planned)
    ]
    dag_task_artifact_groups: list[PlannedBugGroup] = []
    dag_product_cleanup_routes_by_gid: dict[str, dict[str, DagTaskDriftRoute]] = {}
    dag_source_artifact_followups: set[str] = {
        planned.group.group_id for planned in fixable_groups
        if _planned_needs_source_artifact_repair(planned)
    }
    for planned in dag_task_artifact_candidate_groups:
        target_refs = _dag_task_artifact_refs_from_planned(planned)
        drift_routes = await _dag_task_drift_routes_for_refs(
            runner,
            feature,
            target_refs,
            feature_root,
            verifier_path_problems=verifier_path_problems,
            context_text=f"{planned.issue_text}\n\n{to_str(planned.rca)}",
        )
        cleanup_routes = {
            key: route for key, route in drift_routes.items()
            if route.route == "product_cleanup_required"
        }
        if cleanup_routes:
            dag_product_cleanup_routes_by_gid[
                planned.group.group_id
            ] = cleanup_routes
        elif _planned_needs_source_artifact_repair(planned):
            # This is not DB-only drift. Leave it in fixable_groups so the
            # general artifact lane repairs the source artifact (for example a
            # stale dag-fragment) instead of only appending a dag-task row.
            continue
        else:
            dag_task_artifact_groups.append(planned)
    if dag_task_artifact_groups:
        dag_task_ids = {
            planned.group.group_id for planned in dag_task_artifact_groups
        }
        fixable_groups = [
            planned for planned in fixable_groups
            if planned.group.group_id not in dag_task_ids
        ]
    metadata_artifact_groups = [
        planned for planned in fixable_groups
        if (
            planned.group.group_id not in dag_product_cleanup_routes_by_gid
            and _dag_metadata_only_repair_candidate(planned)
        )
    ]
    if metadata_artifact_groups:
        metadata_ids = {planned.group.group_id for planned in metadata_artifact_groups}
        fixable_groups = [
            planned for planned in fixable_groups
            if planned.group.group_id not in metadata_ids
        ]

    async def _run_planned_dag_task_artifact_repair(
        planned: PlannedBugGroup,
    ) -> None:
        target_refs = _dag_task_artifact_refs_from_planned(planned)

        def _artifact_actor_builder(
            base: AgentActor,
            suffix: str,
            **kwargs: Any,
        ) -> AgentActor:
            return _make_parallel_actor(
                base,
                f"dag-g{group_idx}-r{retry}-{suffix}",
                runtime=dag_fix_runtime,
                workspace_path=kwargs.get("workspace_path"),
            )

        result = await _run_rca_dag_task_artifact_repair(
            runner,
            feature,
            source=source,
            bug_id=planned.group.group_id,
            verdict_text=planned.issue_text,
            rca=planned.rca,
            fixer=implementer,
            feature_root=feature_root,
            phase_name="implementation",
            actor_builder=_artifact_actor_builder,
            target_refs=target_refs,
        )
        decision_results.append(result)
        try:
            artifact_repair_records.append(json.loads(result.notes or "{}"))
        except Exception:
            artifact_repair_records.append({
                "source": source,
                "bug_id": planned.group.group_id,
                "target_refs": target_refs,
                "result_task_id": result.task_id,
                "status": result.status,
            })

    async def _run_planned_artifact_repair(
        planned: PlannedBugGroup,
        resolution: DagContradictionResolution,
    ) -> None:
        validation = _validate_dag_contradiction_resolution(
            resolution,
            planned=planned,
        )
        if validation.resolution is None:
            rejection = await _persist_dag_contradiction_rejection(
                runner,
                feature,
                group_idx,
                retry,
                planned,
                resolution,
                validation.rejection_reasons,
            )
            rejected_contradictions.append(rejection)
            human_needed_contradictions.append(planned.group.group_id)
            quarantined_contradiction_groups.append(planned)
            return
        accepted_resolution = validation.resolution
        record = await _persist_dag_contradiction_resolution(
            runner,
            feature,
            group_idx,
            retry,
            planned,
            accepted_resolution,
        )
        resolved_contradictions.append(record)
        _artifact_result, synthetic_result, artifact_record = (
            await _run_dag_artifact_repair_lane(
                runner,
                feature,
                group_idx,
                retry,
                planned,
                accepted_resolution,
                record,
                group_tasks=group_tasks,
                feature_root=feature_root,
                runtime=dag_fix_runtime,
                feedback=feedback,
                fix_context=fix_context,
                closure_path_problems=_dag_closure_path_problems_for_planned(
                    planned,
                    verifier_path_problems,
                    group_tasks,
                ),
            )
        )
        artifact_repair_records.append(artifact_record)
        decision_results.append(synthetic_result)

    for planned in dag_task_artifact_groups:
        await _run_planned_dag_task_artifact_repair(planned)

    for planned in metadata_artifact_groups:
        await _run_planned_artifact_repair(
            planned,
            _dag_artifact_repair_resolution_from_planned(
                planned,
                reason="metadata-only RCA target",
            ),
        )

    if dispatch.contradiction_groups:
        auto_resolve = (
            _dag_auto_resolve_contradictions_enabled()
            and autonomous_remainder_enabled(
                runner, feature, phase_name="implementation",
            )
        )
        if not auto_resolve:
            logger.info(
                "DAG parallel repair preserving manual contradiction path "
                "group=%d retry=%d contradictions=%d",
                group_idx,
                retry,
                len(dispatch.contradiction_groups),
            )
        else:
            resolver_runtime = _dag_repair_runtime_for(
                "dag-contradiction-resolve",
                dag_rca_runtime,
            )
            for planned in dispatch.contradiction_groups:
                try:
                    resolution = await _resolve_dag_contradiction(
                        runner,
                        feature,
                        group_idx,
                        retry,
                        planned,
                        group_tasks=group_tasks,
                        feature_root=feature_root,
                        runtime=resolver_runtime,
                        feedback=feedback,
                    )
                except Exception as exc:
                    logger.warning(
                        "DAG contradiction resolver failed group=%d retry=%d "
                        "bug_group=%s: %s",
                        group_idx,
                        retry,
                        planned.group.group_id,
                        exc,
                    )
                    resolution = None
                validation = _validate_dag_contradiction_resolution(
                    resolution,
                    planned=planned,
                )
                if validation.resolution is None:
                    rejection = await _persist_dag_contradiction_rejection(
                        runner,
                        feature,
                        group_idx,
                        retry,
                        planned,
                        resolution,
                        validation.rejection_reasons,
                    )
                    rejected_contradictions.append(rejection)
                    human_needed_contradictions.append(planned.group.group_id)
                    quarantined_contradiction_groups.append(planned)
                    continue
                resolution = validation.resolution
                record = await _persist_dag_contradiction_resolution(
                    runner,
                    feature,
                    group_idx,
                    retry,
                    planned,
                    resolution,
                )
                resolved_contradictions.append(record)
                if _dag_contradiction_needs_artifact_repair(resolution):
                    _artifact_result, synthetic_result, artifact_record = (
                        await _run_dag_artifact_repair_lane(
                            runner,
                            feature,
                            group_idx,
                            retry,
                            planned,
                            resolution,
                            record,
                            group_tasks=group_tasks,
                            feature_root=feature_root,
                            runtime=dag_fix_runtime,
                            feedback=feedback,
                            fix_context=fix_context,
                            closure_path_problems=_dag_closure_path_problems_for_planned(
                                planned,
                                verifier_path_problems,
                                group_tasks,
                            ),
                        )
                    )
                    artifact_repair_records.append(artifact_record)
                    decision_results.append(synthetic_result)
                elif _dag_contradiction_needs_fix(resolution):
                    guidance = _dag_contradiction_fix_guidance(resolution)
                    resolved_rca = planned.rca.model_copy(
                        update={
                            "confidence": "high",
                            "proposed_approach": guidance,
                            "prior_attempt_analysis": (
                                f"{planned.rca.prior_attempt_analysis}\n\n"
                                "Autonomous DAG contradiction resolver produced "
                                f"{record['artifact_key']}."
                            ).strip(),
                        }
                    )
                    fixable_groups.append(PlannedBugGroup(
                        group=planned.group,
                        rca=resolved_rca,
                        issue_text=planned.issue_text,
                        rca_key=planned.rca_key,
                    ))
                else:
                    decision_results.append(_dag_contradiction_synthetic_result(
                        group_idx,
                        retry,
                        planned,
                        resolution,
                        record,
                    ))

    blocked_fix_group_ids: list[str] = []
    fixable_groups, blocked_fix_group_ids = _dag_filter_fixable_groups_for_quarantine(
        fixable_groups,
        quarantined_contradiction_groups,
    )

    fallback_reason = ""
    if dispatch.contradiction_groups and (
        not fixable_groups
        and not decision_results
        and (
            human_needed_contradictions
            or (
                not resolved_contradictions
                and _dag_auto_resolve_contradictions_enabled()
                and autonomous_remainder_enabled(
                    runner,
                    feature,
                    phase_name="implementation",
                )
            )
        )
    ):
        logger.info(
            "DAG parallel repair falling back for unresolved contradictions "
            "group=%d retry=%d unresolved=%s fixable=%d blocked_fixable=%s",
            group_idx,
            retry,
            human_needed_contradictions,
            len(fixable_groups),
            blocked_fix_group_ids,
        )
        fallback_reason = "unresolved_contradiction"
    elif dispatch.contradiction_groups and not resolved_contradictions and not auto_resolve:
        logger.info(
            "DAG parallel repair falling back group=%d retry=%d "
            "contradictions=%d fixable=%d",
            group_idx,
            retry,
            len(dispatch.contradiction_groups),
            len(fixable_groups),
        )
        fallback_reason = "manual_contradiction_resolution_required"

    schedule = [] if fallback_reason else _compute_fix_schedule([
        (item.group.group_id, item.rca)
        for item in fixable_groups
    ])

    dispatch_record = {
        "group_idx": group_idx,
        "retry": retry,
        "parallel_repair_enabled": True,
        "generic_dispatch_key": dispatch.dispatch_key,
        "total_issues": total_issues,
        "group_count": len(dispatch.groups),
        "fixable_group_count": len(fixable_groups),
        "contradiction_group_count": len(dispatch.contradiction_groups),
        "resolved_contradiction_count": len(resolved_contradictions),
        "rejected_contradiction_count": len(rejected_contradictions),
        "artifact_repair_group_count": len(artifact_repair_records),
        "dag_task_artifact_repair_group_count": len(dag_task_artifact_groups),
        "dag_task_product_cleanup_group_count": len(dag_product_cleanup_routes_by_gid),
        "dag_source_artifact_followup_count": len(dag_source_artifact_followups),
        "dag_task_product_cleanup_artifact_followup_count": len([
            gid for gid in dag_source_artifact_followups
            if gid in dag_product_cleanup_routes_by_gid
        ]),
        "metadata_artifact_repair_group_count": len(metadata_artifact_groups),
        "human_needed_contradiction_count": len(human_needed_contradictions),
        "resolved_contradictions": resolved_contradictions,
        "rejected_contradictions": rejected_contradictions,
        "artifact_repairs": artifact_repair_records,
        "human_needed_contradictions": human_needed_contradictions,
        "blocked_fix_group_ids": blocked_fix_group_ids,
        "fallback_reason": fallback_reason,
        "schedule": [
            {"round": idx, "group_ids": ids}
            for idx, ids in enumerate(schedule)
        ],
        "groups": [
            {
                "group_id": item.group.group_id,
                "likely_root_cause": item.group.likely_root_cause,
                "issue_count": (
                    len(item.group.issue_indices) + len(item.group.gap_indices)
                ),
                "affected_files": item.rca.affected_files,
                "confidence": item.rca.confidence,
            }
            for item in dispatch.groups
        ],
    }
    await runner.artifacts.put(
        f"dag-repair-dispatch:g{group_idx}:retry-{retry}",
        json.dumps(dispatch_record),
        feature=feature,
    )
    await _log_feature_event(
        runner,
        feature.id,
        "dag_repair_dispatch",
        "implementation",
        content=f"g{group_idx}:retry-{retry}",
        metadata={
            "group_idx": group_idx,
            "retry": retry,
            "dispatch_key": f"dag-repair-dispatch:g{group_idx}:retry-{retry}",
            "schedule": dispatch_record["schedule"],
            "fixable_group_count": dispatch_record["fixable_group_count"],
            "fallback_reason": fallback_reason,
            "human_needed_contradiction_count": len(human_needed_contradictions),
        },
    )

    if fallback_reason:
        return None

    if not fixable_groups or not schedule:
        if decision_results:
            return decision_results
        logger.info(
            "DAG parallel repair falling back group=%d retry=%d "
            "contradictions=%d fixable=%d",
            group_idx,
            retry,
            len(dispatch.contradiction_groups),
            len(fixable_groups),
        )
        return None

    group_by_id = {item.group.group_id: item for item in fixable_groups}
    fix_results: dict[str, ImplementationResult] = {}
    task_specs = _format_dag_group_task_specs(group_tasks)
    contradiction_context = await _format_contradiction_decisions_context(
        runner, feature,
    )
    for round_idx, round_ids in enumerate(schedule):
        fix_tasks: list[Ask] = []
        runnable_ids: list[str] = []
        for gid in round_ids:
            planned = group_by_id.get(gid)
            if planned is None:
                continue
            ws_path = _resolve_fix_workspace(feature_root, planned.rca.affected_files)
            workspace_ctx = (
                f"\n\n## Workspace\n"
                f"Your working directory is: `{ws_path}`\n"
                f"All file reads and writes MUST use paths within this directory.\n"
                f"Do NOT use absolute paths from search results that point to "
                f"other copies of the same repo.\n"
            ) if ws_path else ""
            context_package = await _build_prompt_context_package(
                runner,
                feature,
                title=(
                    f"DAG Parallel Repair — Group {group_idx} "
                    f"Retry {retry + 1} Bug Group {gid}"
                ),
                file_stem=f"g{group_idx}-parallel-fix-r{retry}-{gid}",
                intro_lines=[
                    "Fix one root-cause group from the failed DAG verifier.",
                    "Other repair agents may run concurrently; do not revert unrelated edits.",
                ],
                sections=[
                    ("feedback", "Merged Verifier Feedback", feedback),
                    ("issues", "Grouped Issues", planned.issue_text),
                    ("rca", "Root Cause Analysis", to_str(planned.rca)),
                    (
                        "contradiction-decisions",
                        "Resolved Contradiction Decisions",
                        contradiction_context,
                    ),
                    ("task-specs", "Current DAG Group Task Specs", task_specs),
                    ("fix-context", "Original Enhancement Items", fix_context),
                    ("workspace", "Workspace", workspace_ctx),
                    (
                        "dag-drift-routing",
                        "DAG Drift Routing",
                        _dag_product_cleanup_guidance(
                            dag_product_cleanup_routes_by_gid.get(gid, {})
                        ),
                    ),
                ],
            )
            fix_tasks.append(Ask(
                actor=_make_parallel_actor(
                    implementer,
                    f"dag-g{group_idx}-r{retry}-fix-{gid}",
                    runtime=dag_fix_runtime,
                    workspace_path=ws_path,
                ),
                prompt=(
                    f"## DAG Repair Fix: group {gid}\n\n"
                    f"{_context_package_prompt(context_package)}"
                    "Apply the RCA's proposed fix precisely. You are not alone in "
                    "the codebase: do not revert changes made by other agents, and "
                    "keep the patch scoped to this root-cause group. Report every "
                    "file you create or modify."
                ),
                output_type=ImplementationResult,
            ))
            runnable_ids.append(gid)

        if not fix_tasks:
            continue
        await _log_feature_event(
            runner,
            feature.id,
            "dag_repair_round_start",
            "implementation",
            content=f"g{group_idx}:retry-{retry}:round-{round_idx}",
            metadata={
                "group_idx": group_idx,
                "retry": retry,
                "round_idx": round_idx,
                "bug_group_ids": runnable_ids,
                "runtime": dag_fix_runtime,
            },
        )
        round_results = await _run_dag_repair_fix_tasks(
            runner,
            feature,
            group_idx,
            retry,
            round_idx,
            runnable_ids,
            fix_tasks,
        )
        sanitize_inputs = [
            result for result in round_results
            if isinstance(result, ImplementationResult)
        ]
        sanitized_results = await _sanitize_dag_repair_results(
            runner,
            feature,
            group_idx,
            retry,
            sanitize_inputs,
            feature_root,
            context_label=f"parallel-round-{round_idx}",
        )
        sanitized_iter = iter(sanitized_results)
        for gid, result in zip(runnable_ids, round_results):
            if isinstance(result, ImplementationResult):
                sanitized_result = next(sanitized_iter)
                planned = group_by_id[gid]
                if (
                    gid not in dag_product_cleanup_routes_by_gid
                    and _dag_blocked_result_should_reroute_to_artifact_repair(
                        planned,
                        sanitized_result,
                    )
                ):
                    await _run_planned_artifact_repair(
                        planned,
                        _dag_artifact_repair_resolution_from_planned(
                            planned,
                            reason="blocked implementer artifact-boundary result",
                            blocked_result=sanitized_result,
                        ),
                    )
                    continue
                cleanup_routes = dag_product_cleanup_routes_by_gid.get(gid)
                artifact_followup_ready = (
                    sanitized_result.status in {"completed", "partial"}
                )
                if cleanup_routes:
                    cleanup_report = await _append_dag_task_rows_from_product_repair(
                        runner,
                        feature,
                        source=source,
                        bug_id=gid,
                        routes=cleanup_routes,
                        fix_result=sanitized_result,
                        feature_root=feature_root,
                    )
                    artifact_followup_ready = (
                        artifact_followup_ready
                        and _dag_product_cleanup_ready_for_artifact_repair(
                            cleanup_report
                        )
                    )
                if (
                    gid in dag_source_artifact_followups
                    and artifact_followup_ready
                ):
                    await _run_planned_artifact_repair(
                        planned,
                        _dag_artifact_repair_resolution_from_planned(
                            planned,
                            reason="post-product-cleanup source artifact repair",
                            blocked_result=sanitized_result,
                        ),
                    )
                fix_results[gid] = sanitized_result
        if any(gid in fix_results for gid in runnable_ids):
            await _commit_repos(
                runner,
                feature,
                f"fix: DAG group {group_idx} repair round {round_idx}",
                failure_key=f"dag-commit-failure:g{group_idx}:retry-{retry}",
                failure_metadata={
                    "group_idx": group_idx,
                    "stage": f"retry-{retry}",
                    "retry": retry,
                    "repair_round": round_idx,
                },
            )
        await _log_feature_event(
            runner,
            feature.id,
            "dag_repair_round_finish",
            "implementation",
            content=f"g{group_idx}:retry-{retry}:round-{round_idx}",
            metadata={
                "group_idx": group_idx,
                "retry": retry,
                "round_idx": round_idx,
                "bug_group_ids": runnable_ids,
                "completed_group_ids": [gid for gid in runnable_ids if gid in fix_results],
                "result_count": len(round_results),
            },
        )

    dispatch_record.update({
        "fixable_group_count": len(fixable_groups),
        "resolved_contradiction_count": len(resolved_contradictions),
        "rejected_contradiction_count": len(rejected_contradictions),
        "artifact_repair_group_count": len(artifact_repair_records),
        "dag_task_artifact_repair_group_count": len(dag_task_artifact_groups),
        "dag_task_product_cleanup_group_count": len(dag_product_cleanup_routes_by_gid),
        "dag_source_artifact_followup_count": len(dag_source_artifact_followups),
        "dag_task_product_cleanup_artifact_followup_count": len([
            gid for gid in dag_source_artifact_followups
            if gid in dag_product_cleanup_routes_by_gid
        ]),
        "human_needed_contradiction_count": len(human_needed_contradictions),
        "resolved_contradictions": resolved_contradictions,
        "rejected_contradictions": rejected_contradictions,
        "artifact_repairs": artifact_repair_records,
        "human_needed_contradictions": human_needed_contradictions,
    })
    await runner.artifacts.put(
        f"dag-repair-dispatch:g{group_idx}:retry-{retry}",
        json.dumps(dispatch_record),
        feature=feature,
    )

    if not fix_results and not decision_results:
        return None

    reverify_tasks: list[Ask] = []
    reverify_ids: list[str] = []
    for gid, fix_result in fix_results.items():
        planned = group_by_id[gid]
        reverify_tasks.append(Ask(
            actor=_make_parallel_actor(
                verifier,
                f"dag-g{group_idx}-r{retry}-focused-reverify-{gid}",
                runtime=dag_reverify_runtime,
                workspace_path=str(feature_root) if feature_root else None,
            ),
            prompt=(
                f"## Focused DAG Repair Reverify: group {gid}\n\n"
                "This is an advisory focused reverify for the Claude-discovered "
                "repair group. It cannot checkpoint the DAG; the final aggregate "
                "Codex verifier will still run afterward.\n\n"
                f"### Issues\n{planned.issue_text}\n\n"
                f"### RCA\n{to_str(planned.rca)}\n\n"
                f"### Fix Result\n{to_str(fix_result)}\n\n"
                "Return whether the specific grouped issues appear fixed. Also "
                "call out any new risks introduced by the patch."
            ),
            output_type=Verdict,
        ))
        reverify_ids.append(gid)

    if reverify_tasks:
        await _log_feature_event(
            runner,
            feature.id,
            "dag_focused_reverify_start",
            "implementation",
            content=f"g{group_idx}:retry-{retry}",
            metadata={
                "group_idx": group_idx,
                "retry": retry,
                "bug_group_ids": reverify_ids,
                "runtime": dag_reverify_runtime,
            },
        )
        if len(reverify_tasks) == 1:
            reverify_results = [
                await runner.run(reverify_tasks[0], feature, phase_name="implementation")
            ]
        else:
            reverify_results = await runner.parallel(reverify_tasks, feature)
        for gid, reverify_result in zip(reverify_ids, reverify_results):
            await runner.artifacts.put(
                f"dag-repair-reverify:g{group_idx}:{gid}:retry-{retry}",
                to_str(reverify_result),
                feature=feature,
            )
        await _log_feature_event(
            runner,
            feature.id,
            "dag_focused_reverify_finish",
            "implementation",
            content=f"g{group_idx}:retry-{retry}",
            metadata={
                "group_idx": group_idx,
                "retry": retry,
                "bug_group_ids": reverify_ids,
                "result_count": len(reverify_results),
            },
        )

    return decision_results + list(fix_results.values())


async def _diagnose_and_fix(
    runner: WorkflowRunner,
    feature: Feature,
    verdict: object,
    source: str,
    original_reviewer: AgentActor,
    fixer: AgentActor,
    prior_attempts: list[BugFixAttempt],
    bug_counter: itertools.count,  # type: ignore[type-arg]
    handover_context: str = "",
    test_plan_section: str = "",
    phase_name: str = "implementation",
    rca_runtime: str | None = None,
) -> list[BugFixAttempt]:
    """Structured failure handling: triage → parallel RCA → fix → re-verify.

    For string verdicts or single-issue verdicts, takes the single-bug path.
    For multi-issue Verdicts, triages by root cause and dispatches in parallel
    where file scopes don't overlap.

    Returns a list of BugFixAttempt records (one per bug group).
    """
    verdict_text = to_str(verdict)
    attempt_number = sum(1 for a in prior_attempts if a.source_verdict == source) + 1

    # Resolve workspace path for RCA git access
    feature_root = _get_feature_root(runner, feature)
    prior_context = _format_prior_attempts(prior_attempts, context_base=feature_root)
    workspace_hint = (
        f"\n\n### Workspace\nFeature repos at: `{feature_root}`\n"
        if feature_root else ""
    )

    # ── Short-circuit: string verdict or ≤1 issue ────────────────────
    use_single_path = True
    if isinstance(verdict, Verdict):
        total_issues = len(verdict.concerns) + len(verdict.gaps)
        if total_issues > 1:
            use_single_path = False

    if use_single_path:
        attempt = await _single_rca_fix_verify(
            runner, feature, verdict_text, source,
            original_reviewer, fixer, prior_context,
            bug_id=f"{source.upper().replace(' ', '-')}-FAIL-{next(bug_counter)}",
            attempt_number=attempt_number,
            handover_context=handover_context,
            test_plan_section=test_plan_section,
            phase_name=phase_name,
            rca_runtime=rca_runtime,
        )
        return [attempt]

    # ── Multi-issue path: triage → parallel RCA → fix → re-verify ────
    assert isinstance(verdict, Verdict)

    # 1. Triage: group issues by root cause
    indexed_issues = _format_indexed_issues(verdict)
    triage_actor = AgentActor(name="bug-triager", role=_triage_role)
    if rca_runtime:
        triage_actor = _make_parallel_actor(
            triage_actor, "triage", runtime=rca_runtime,
        )
    triage: BugTriage = await runner.run(
        Ask(
            actor=triage_actor,
            prompt=(
                f"## Verdict from: {source}\n\n"
                f"### Summary\n{verdict.summary}\n\n"
                f"### Issues (reference by index)\n{indexed_issues}\n\n"
                "Group ALL issues by likely root cause. Every index must appear "
                "in exactly one group. Use issue_indices for [C*] entries and "
                "gap_indices for [G*] entries."
            ),
            output_type=BugTriage,
        ),
        feature,
        phase_name=phase_name,
    )

    await runner.artifacts.put(
        f"bug-triage:{source}:attempt-{attempt_number}",
        to_str(triage),
        feature=feature,
    )

    if not triage.groups:
        # Fallback: triage produced no groups — treat as single bug
        attempt = await _single_rca_fix_verify(
            runner, feature, verdict_text, source,
            original_reviewer, fixer, prior_context,
            bug_id=f"{source.upper().replace(' ', '-')}-FAIL-{next(bug_counter)}",
            attempt_number=attempt_number,
            handover_context=handover_context,
            test_plan_section=test_plan_section,
            phase_name=phase_name,
            rca_runtime=rca_runtime,
        )
        return [attempt]

    logger.info(
        "Triage produced %d bug groups from %d issues (source: %s)",
        len(triage.groups), len(verdict.concerns) + len(verdict.gaps), source,
    )

    # 2. Parallel RCA: one per group (read-only, always safe in parallel)
    rca_tasks = [
        Ask(
            actor=_make_parallel_actor(
                root_cause_analyst,
                f"rca-{group.group_id}",
                runtime=rca_runtime,
            ),
            prompt=(
                f"## Bug Group: {group.group_id}\n\n"
                f"### Likely Root Cause (from triage)\n{group.likely_root_cause}\n\n"
                f"### Issues in this group\n{_extract_group_issues(verdict, group)}\n\n"
                f"### Full Verdict Summary\n{verdict.summary}\n\n"
                "Investigate the root cause of these specific issues. Read the "
                "relevant code, trace the data flow, and identify the exact "
                "point of failure. Propose a conceptual fix approach — do NOT "
                "implement anything."
                f"{prior_context}{workspace_hint}"
            ),
            output_type=RootCauseAnalysis,
        )
        for group in triage.groups
    ]

    if len(rca_tasks) == 1:
        rca_results = [await runner.run(rca_tasks[0], feature, phase_name=phase_name)]
    else:
        rca_results = await runner.parallel(rca_tasks, feature)

    # Build group_id → RCA mapping
    group_rcas: list[tuple[str, RootCauseAnalysis]] = []
    for group, rca_result in zip(triage.groups, rca_results):
        if isinstance(rca_result, RootCauseAnalysis):
            group_rcas.append((group.group_id, rca_result))
            await runner.artifacts.put(
                f"bug-rca:{source}:{group.group_id}:attempt-{attempt_number}",
                to_str(rca_result),
                feature=feature,
            )

    if not group_rcas:
        # All RCAs failed — fallback to single bug
        attempt = await _single_rca_fix_verify(
            runner, feature, verdict_text, source,
            original_reviewer, fixer, prior_context,
            bug_id=f"{source.upper().replace(' ', '-')}-FAIL-{next(bug_counter)}",
            attempt_number=attempt_number,
            handover_context=handover_context,
            test_plan_section=test_plan_section,
            phase_name=phase_name,
            rca_runtime=rca_runtime,
        )
        return [attempt]

    # Build lookup dicts early (needed for contradiction handling)
    group_by_id = {g.group_id: g for g in triage.groups}

    # ── Contradiction handling ──────────────────────────────────────
    def _group_dag_task_artifact_refs(
        gid: str,
        rca: RootCauseAnalysis,
    ) -> list[str]:
        return _dag_task_artifact_refs_from_rca(
            rca,
            extra_text=_extract_group_issues(verdict, group_by_id[gid]),
        )

    contradiction_groups = [
        (gid, rca) for gid, rca in group_rcas
        if (
            rca.confidence == "contradiction"
            and not _rca_needs_dag_task_artifact_repair(
                rca,
                extra_text=_extract_group_issues(verdict, group_by_id[gid]),
                target_refs=_group_dag_task_artifact_refs(gid, rca),
            )
        )
    ]
    fixable_groups = [
        (gid, rca) for gid, rca in group_rcas
        if (
            rca.confidence != "contradiction"
            or _rca_needs_dag_task_artifact_repair(
                rca,
                extra_text=_extract_group_issues(verdict, group_by_id[gid]),
                target_refs=_group_dag_task_artifact_refs(gid, rca),
            )
        )
    ]

    contradiction_results: list[BugFixAttempt] = []
    if contradiction_groups:
        logger.warning(
            "%d of %d bug groups are spec contradictions — escalating",
            len(contradiction_groups), len(group_rcas),
        )
        for gid, rca in contradiction_groups:
            group = group_by_id[gid]
            resolution = await _escalate_contradiction(
                runner, feature, phase_name, source, group, rca,
            )
            # User resolved it — add to fixable with their direction
            resolved_rca = rca.model_copy(update={
                "proposed_approach": resolution,
                "confidence": "high",
            })
            fixable_groups.append((gid, resolved_rca))
            contradiction_results.append(BugFixAttempt(
                bug_id=f"{source.upper()}-CONTRADICTION-{gid}",
                group_id=gid,
                source_verdict=source,
                description=rca.hypothesis,
                root_cause=rca.contradiction_detail or rca.hypothesis,
                fix_applied=f"User decision: {resolution}",
                re_verify_result="RESOLVED",
                attempt_number=attempt_number,
            ))

    if not fixable_groups:
        return contradiction_results

    group_rcas = fixable_groups

    # 3. File-overlap scheduling
    schedule = _compute_fix_schedule(group_rcas)
    logger.info(
        "Fix schedule: %d rounds for %d groups",
        len(schedule), len(group_rcas),
    )

    # Build lookup dicts
    rca_by_group = dict(group_rcas)

    # 3b. Store verbose dispatch artifact
    dispatch_record = {
        "source": source,
        "attempt_number": attempt_number,
        "total_issues": len(verdict.concerns) + len(verdict.gaps),
        "groups": [
            {
                "group_id": g.group_id,
                "likely_root_cause": g.likely_root_cause,
                "severity": g.severity,
                "affected_files_hint": g.affected_files_hint,
                "issue_count": len(g.issue_indices) + len(g.gap_indices),
                "rca": {
                    "hypothesis": rca_by_group[g.group_id].hypothesis,
                    "evidence": rca_by_group[g.group_id].evidence,
                    "affected_files": rca_by_group[g.group_id].affected_files,
                    "proposed_approach": rca_by_group[g.group_id].proposed_approach,
                    "confidence": rca_by_group[g.group_id].confidence,
                } if g.group_id in rca_by_group else None,
            }
            for g in triage.groups
        ],
        "schedule": [
            {"round": i, "group_ids": ids}
            for i, ids in enumerate(schedule)
        ],
        "total_rounds": len(schedule),
    }
    await runner.artifacts.put(
        f"bug-dispatch:{source}:attempt-{attempt_number}",
        json.dumps(dispatch_record),
        feature=feature,
    )

    # 4. Fix dispatch: parallel within each round, sequential between rounds
    feature_root = _get_feature_root(runner, feature)
    fix_results: dict[str, ImplementationResult] = {}
    artifact_only_fix_ids: set[str] = set()
    commit_failed_attempts: list[BugFixAttempt] = []
    dag_product_cleanup_routes_by_gid: dict[str, dict[str, DagTaskDriftRoute]] = {}

    for round_idx, round_ids in enumerate(schedule):
        fix_tasks = []
        fix_task_ids: list[str] = []
        for gid in round_ids:
            rca = rca_by_group[gid]
            group_issue_text = _extract_group_issues(verdict, group_by_id[gid])
            dag_task_artifact_refs = _dag_task_artifact_refs_from_rca(
                rca,
                extra_text=group_issue_text,
            )
            if _rca_needs_dag_task_artifact_repair(
                rca,
                extra_text=group_issue_text,
                target_refs=dag_task_artifact_refs,
            ):
                drift_routes = await _dag_task_drift_routes_for_refs(
                    runner,
                    feature,
                    dag_task_artifact_refs,
                    feature_root=feature_root,
                    context_text=f"{group_issue_text}\n\n{to_str(rca)}",
                )
                cleanup_routes = {
                    key: route for key, route in drift_routes.items()
                    if route.route == "product_cleanup_required"
                }
                if cleanup_routes:
                    dag_product_cleanup_routes_by_gid[gid] = cleanup_routes
                else:
                    fix_results[gid] = await _run_rca_dag_task_artifact_repair(
                        runner,
                        feature,
                        source=source,
                        bug_id=gid,
                        verdict_text=group_issue_text,
                        rca=rca,
                        fixer=fixer,
                        feature_root=feature_root,
                        phase_name=phase_name,
                        actor_builder=_make_parallel_actor,
                        target_refs=dag_task_artifact_refs,
                    )
                    artifact_only_fix_ids.add(gid)
                    continue
            ws_path = _resolve_fix_workspace(feature_root, rca.affected_files)
            ws_ctx = (
                f"\n\n## Workspace\n"
                f"Your working directory is: `{ws_path}`\n"
                f"All file reads and writes MUST use paths within this directory.\n"
                f"Do NOT use absolute paths from search results that point to "
                f"other copies of the same repo.\n"
            ) if ws_path else ""
            fix_tasks.append(Ask(
                actor=_make_parallel_actor(
                    fixer, f"fix-{gid}",
                    workspace_path=ws_path,
                ),
                prompt=(
                    f"## Bug Fix: group {gid}\n\n"
                    f"### Root Cause Analysis\n\n"
                    f"**Hypothesis:** {rca.hypothesis}\n\n"
                    f"**Evidence:**\n"
                    + "\n".join(f"- {e}" for e in rca.evidence)
                    + f"\n\n**Affected Files:**\n"
                    + "\n".join(f"- `{f}`" for f in rca.affected_files)
                    + f"\n\n**Proposed Approach:** {rca.proposed_approach}\n\n"
                    f"### Issues\n{_extract_group_issues(verdict, group_by_id[gid])}\n\n"
                    f"{ws_ctx}\n"
                    f"{_dag_product_cleanup_guidance(dag_product_cleanup_routes_by_gid.get(gid, {}))}\n"
                    "## Instructions\n"
                    "1. Read each affected file listed above\n"
                    "2. Apply the fix described in the RCA — be precise\n"
                    "3. Fix only what the root cause analysis identified\n"
                    "4. Report all files modified"
                    f"{prior_context}"
                ),
                output_type=ImplementationResult,
            ))
            fix_task_ids.append(gid)

        if fix_tasks:
            if len(fix_tasks) == 1:
                results = [await runner.run(fix_tasks[0], feature, phase_name=phase_name)]
            else:
                results = await runner.parallel(fix_tasks, feature)

            for gid, result in zip(fix_task_ids, results):
                if isinstance(result, ImplementationResult):
                    fix_results[gid] = result
                    cleanup_routes = dag_product_cleanup_routes_by_gid.get(gid)
                    if cleanup_routes:
                        await _append_dag_task_rows_from_product_repair(
                            runner,
                            feature,
                            source=source,
                            bug_id=gid,
                            routes=cleanup_routes,
                            fix_result=result,
                            feature_root=feature_root,
                        )

        # Commit fixes from this round before re-verification
        fixed_ids = [
            gid for gid in round_ids
            if gid in fix_results and gid not in artifact_only_fix_ids
        ]
        if fixed_ids:
            commit_message = f"fix: round {round_idx} — {', '.join(fixed_ids)}"
            try:
                await _commit_repos(
                    runner,
                    feature,
                    commit_message,
                )
            except WorkflowCommitError as exc:
                verdict = await _record_bug_commit_failure(
                    runner,
                    feature,
                    source,
                    f"round-{round_idx}",
                    attempt_number,
                    f"round-{round_idx}",
                    exc,
                    message=commit_message,
                )
                feedback = _format_feedback("Commit", verdict)
                for gid in fixed_ids:
                    group = group_by_id[gid]
                    fix = fix_results.get(gid)
                    commit_failed_attempts.append(BugFixAttempt(
                        bug_id=(
                            f"{source.upper().replace(' ', '-')}-FAIL-"
                            f"{next(bug_counter)}"
                        ),
                        group_id=gid,
                        source_verdict=source,
                        description=group.likely_root_cause,
                        root_cause=rca_by_group[gid].hypothesis,
                        fix_applied=(
                            (fix.summary if fix else "Fix completed")
                            + f"\n\nCommit failed before reverify:\n{feedback}"
                        ),
                        files_modified=(
                            (fix.files_created + fix.files_modified)
                            if fix else []
                        ),
                        re_verify_result="FAIL",
                        attempt_number=attempt_number,
                    ))
                    fix_results.pop(gid, None)

    blocked_artifact_attempts: list[BugFixAttempt] = []
    for gid in list(fix_results):
        fix = fix_results[gid]
        if gid not in artifact_only_fix_ids or fix.status != "blocked":
            continue
        group = group_by_id[gid]
        blocked_artifact_attempts.append(BugFixAttempt(
            bug_id=f"{source.upper().replace(' ', '-')}-FAIL-{next(bug_counter)}",
            group_id=gid,
            source_verdict=source,
            description=group.likely_root_cause,
            root_cause=rca_by_group[gid].hypothesis,
            fix_applied=fix.summary,
            files_modified=[],
            re_verify_result="FAIL",
            attempt_number=attempt_number,
        ))
        del fix_results[gid]

    if not fix_results:
        return (
            contradiction_results
            + blocked_artifact_attempts
            + commit_failed_attempts
        )

    # 5. Parallel re-verify: one per group (read-only, always safe)
    verify_tasks = [
        Ask(
            actor=_make_parallel_actor(original_reviewer, f"reverify-{gid}"),
            prompt=(
                f"## Re-verification: group {gid}\n\n"
                f"A fix was applied for the following issues.\n\n"
                f"### Issues\n{_extract_group_issues(verdict, group_by_id[gid])}\n\n"
                f"### Root Cause\n{rca_by_group[gid].hypothesis}\n\n"
                f"### Fix Applied\n{fix_results[gid].summary}\n\n"
                f"### Files Modified\n"
                + "\n".join(
                    f"- `{f}`"
                    for f in (fix_results[gid].files_created + fix_results[gid].files_modified)
                )
                + f"{test_plan_section}\n\n"
                "Re-verify that the issues in this group are resolved. "
                "Check that the fix does not introduce new problems. "
                "The verdict must be based on the CURRENT state of the code. "
                "When a Test Plan section is provided above, cite AC-ids in any "
                "remaining failures you find."
            ),
            output_type=Verdict,
        )
        for gid in fix_results
    ]

    if len(verify_tasks) == 1:
        verify_results = [await runner.run(verify_tasks[0], feature, phase_name=phase_name)]
    else:
        verify_results = await runner.parallel(verify_tasks, feature)

    # Persist per-group re-verify verdicts + update ledger
    for gid, rv in zip(fix_results.keys(), verify_results):
        await runner.artifacts.put(
            f"bug-reverify:{source}:{gid}:attempt-{attempt_number}",
            to_str(rv),
            feature=feature,
        )
        if isinstance(rv, Verdict):
            ledger = await _load_ledger(runner, feature)
            ledger = _update_ledger(ledger, rv, f"reverify:{source}", 0)
            await _save_ledger(runner, feature, ledger)

    # 6. Regression test on all modified files from passed groups
    passed_gids = [
        gid for gid, rv in zip(fix_results.keys(), verify_results) if _is_approved(rv)
    ]
    regression_failed_gids: set[str] = set()
    if passed_gids:
        all_modified = []
        for gid in passed_gids:
            fix = fix_results[gid]
            all_modified.extend(fix.files_created + fix.files_modified)
        all_modified = sorted(set(all_modified))
        if all_modified:
            regression_verdict = await _run_regression(
                runner, feature, all_modified, handover_context=handover_context,
                phase_name=phase_name,
                regression_runtime=rca_runtime,
                integration_runtime=rca_runtime,
            )
            if regression_verdict is not None:
                await runner.artifacts.put(
                    f"bug-regression:{source}:attempt-{attempt_number}",
                    to_str(regression_verdict),
                    feature=feature,
                )
                if not _is_approved(regression_verdict):
                    logger.warning("Regression found after multi-group fixes — attempting in-place fix")
                    # Add regression findings to ledger
                    if isinstance(regression_verdict, Verdict):
                        ledger = await _load_ledger(runner, feature)
                        ledger = _update_ledger(
                            ledger, regression_verdict, f"regression:{source}", 0,
                        )
                        await _save_ledger(runner, feature, ledger)
                    # Fix regression in-place
                    regression_attempt = await _single_rca_fix_verify(
                        runner, feature,
                        _format_feedback("Regression", regression_verdict),
                        f"regression:{source}",
                        original_reviewer, fixer,
                        _format_prior_attempts(prior_attempts, context_base=feature_root),
                        bug_id=f"{source.upper()}-REGRESSION-{attempt_number}",
                        attempt_number=attempt_number,
                        handover_context=handover_context,
                        test_plan_section=test_plan_section,
                        skip_regression=True,
                        phase_name=phase_name,
                        rca_runtime=rca_runtime,
                    )
                    if regression_attempt.re_verify_result == "PASS":
                        await _commit_repos(
                            runner, feature,
                            f"fix: regression after {source} attempt {attempt_number}",
                        )
                    else:
                        # Regression fix failed — mark all passed groups as failed
                        regression_failed_gids = set(passed_gids)

    # 7. Collect BugFixAttempt records
    attempts: list[BugFixAttempt] = []
    for gid, re_verdict in zip(fix_results.keys(), verify_results):
        group = group_by_id[gid]
        fix = fix_results[gid]
        passed = _is_approved(re_verdict) and gid not in regression_failed_gids

        description = group.likely_root_cause
        if passed:
            logger.info("Bug group %s fixed: %s", gid, description[:80])
        else:
            logger.warning("Bug group %s re-verify FAILED: %s", gid, description[:80])

        attempts.append(BugFixAttempt(
            bug_id=f"{source.upper().replace(' ', '-')}-FAIL-{next(bug_counter)}",
            group_id=gid,
            source_verdict=source,
            description=description,
            root_cause=rca_by_group[gid].hypothesis,
            fix_applied=fix.summary,
            files_modified=fix.files_created + fix.files_modified,
            re_verify_result="PASS" if passed else "FAIL",
            attempt_number=attempt_number,
        ))

    return (
        contradiction_results
        + blocked_artifact_attempts
        + commit_failed_attempts
        + attempts
    )


def _dag_task_artifact_refs_from_rca(
    rca: RootCauseAnalysis,
    *,
    extra_text: str = "",
) -> list[str]:
    refs: list[str] = []
    text_parts = [
        *list(rca.affected_files or []),
        rca.hypothesis,
        rca.proposed_approach,
        rca.prior_attempt_analysis,
        rca.contradiction_detail,
        "\n".join(rca.evidence or []),
        "\n".join(rca.alternative_hypotheses or []),
        extra_text,
    ]
    for text in text_parts:
        refs.extend(
            ref for ref in _dag_artifact_repair_refs_from_text(text or "")
            if _is_dag_task_artifact_key(ref)
        )
        refs.extend(_dag_task_artifact_refs_from_reported_result_text(text or ""))
    return _dedupe_preserving_order(refs)


def _rca_needs_dag_task_artifact_repair(
    rca: RootCauseAnalysis,
    *,
    extra_text: str = "",
    target_refs: list[str] | None = None,
) -> bool:
    refs = target_refs or _dag_task_artifact_refs_from_rca(
        rca,
        extra_text=extra_text,
    )
    if not refs:
        return False
    text = "\n".join([
        rca.hypothesis,
        rca.proposed_approach,
        rca.prior_attempt_analysis,
        rca.contradiction_detail,
        "\n".join(rca.evidence or []),
        "\n".join(rca.alternative_hypotheses or []),
        extra_text,
    ]).lower()
    return any(marker in text for marker in (
        "stale",
        "persisted",
        "postgres",
        "db-backed",
        "database",
        "artifact row",
        "implementationresult",
        "files_created",
        "files_modified",
        "forbidden/stale",
    ))


async def _run_rca_dag_task_artifact_repair(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    source: str,
    bug_id: str,
    verdict_text: str,
    rca: RootCauseAnalysis,
    fixer: AgentActor,
    feature_root: Path | None,
    phase_name: str,
    actor_builder: Callable[..., AgentActor],
    target_refs: list[str] | None = None,
) -> ImplementationResult:
    refs = _safe_dag_task_artifact_refs(
        target_refs or _dag_task_artifact_refs_from_rca(rca)
    )
    target_ref_prompt = "\n".join(f"- {ref}" for ref in refs) or "- (none)"
    ws_path = _dag_artifact_repair_workspace(runner, feature, feature_root)
    actor = actor_builder(
        fixer,
        f"artifact-repair-{bug_id}",
        workspace_path=ws_path,
    )
    try:
        raw_result = await runner.run(
            Ask(
                actor=actor,
                prompt=(
                    f"## DB-Backed Artifact Repair: {bug_id}\n\n"
                    f"### Failure Source\n{source}\n\n"
                    f"### Original Verdict\n{verdict_text}\n\n"
                    f"### Root Cause Analysis\n{to_str(rca)}\n\n"
                    "Apply this as an artifact repair, not a product-code fix. "
                    "You may update only DB-backed workflow artifacts listed below "
                    "by returning full replacement entries in artifact_updates. "
                    "For each `dag-task:{task_id}` update, content must be a full "
                    "ImplementationResult JSON object whose task_id matches the "
                    "artifact key suffix and whose reported files are canonical "
                    "existing product paths.\n\n"
                    "Allowed artifact keys:\n"
                    f"{target_ref_prompt}\n\n"
                    "Do not edit product source files. Do not report product files "
                    "in artifacts_modified; report DB artifact writes through "
                    "artifact_updates only."
                ),
                output_type=ArtifactRepairResult,
            ),
            feature,
            phase_name=phase_name,
        )
        repair_result = (
            raw_result
            if isinstance(raw_result, ArtifactRepairResult)
            else ArtifactRepairResult.model_validate(raw_result)
        )
        repair_result = repair_result.model_copy(update={
            "task_id": repair_result.task_id or f"ARTIFACT-REPAIR-{bug_id}",
            "group_id": repair_result.group_id or bug_id,
        })
        update_record = await _apply_dag_artifact_repair_updates(
            runner,
            feature,
            repair_result,
            feature_root,
        )
    except Exception as exc:
        repair_result = ArtifactRepairResult(
            task_id=f"ARTIFACT-REPAIR-{bug_id}",
            group_id=bug_id,
            summary=(
                "DB-backed artifact repair failed before returning a usable "
                f"ArtifactRepairResult: {type(exc).__name__}: {exc}"
            ),
            status="blocked",
            notes=repr(exc),
        )
        update_record = {
            "applied_updates": [],
            "applied_target_updates": [],
            "skipped_updates": [],
            "synced_files": [],
            "deleted_artifacts": [],
            "skipped_deletes": [],
        }

    applied = bool(
        update_record.get("applied_updates")
        or update_record.get("applied_target_updates")
        or update_record.get("synced_files")
        or update_record.get("deleted_artifacts")
    )
    status = (
        repair_result.status
        if repair_result.status in {"completed", "partial", "blocked"}
        else "partial"
    )
    if not applied and status != "blocked":
        status = "blocked"
    record = {
        "artifact_key": f"bug-artifact-repair:{source}:{bug_id}",
        "source": source,
        "bug_id": bug_id,
        "target_refs": refs,
        "result": repair_result.model_dump(mode="json"),
        "artifact_update_application": update_record,
        "created_at": time.time(),
    }
    await runner.artifacts.put(
        record["artifact_key"],
        json.dumps(record, indent=2),
        feature=feature,
    )
    return ImplementationResult(
        task_id=repair_result.task_id,
        summary=(
            "DB-backed artifact repair completed: "
            f"{repair_result.summary}"
        ),
        status=status,
        files_created=[],
        files_modified=[],
        notes=json.dumps(record, indent=2),
        deviations=repair_result.deviations,
        self_reported_risks=repair_result.self_reported_risks,
    )


async def _single_rca_fix_verify(
    runner: WorkflowRunner,
    feature: Feature,
    verdict_text: str,
    source: str,
    original_reviewer: AgentActor,
    fixer: AgentActor,
    prior_context: str,
    bug_id: str,
    attempt_number: int,
    handover_context: str = "",
    test_plan_section: str = "",
    skip_regression: bool = False,
    phase_name: str = "implementation",
    workspace_root: Path | None = None,
    rca_runtime: str | None = None,
    actor_factory: Callable[..., AgentActor] | None = None,
) -> BugFixAttempt:
    """Single-bug RCA → fix → re-verify (no triage needed).

    When *skip_regression* is True, the regression test step is skipped.
    Used when this function is called to fix a regression — prevents
    infinite nesting.
    """
    feature_root = workspace_root or _get_feature_root(runner, feature)
    actor_builder = actor_factory or _make_parallel_actor

    # 1. Root Cause Analysis
    rca: RootCauseAnalysis = await runner.run(
        Ask(
            actor=actor_builder(
                root_cause_analyst,
                f"rca-{bug_id}",
                runtime=rca_runtime,
                workspace_path=str(feature_root) if feature_root else None,
            ),
            prompt=(
                f"## Bug Report: {bug_id}\n\n"
                f"### Failure Source: {source}\n\n"
                f"### Verdict\n\n{verdict_text}\n\n"
                "Investigate the root cause of this failure. Read the relevant "
                "code, trace the data flow, and identify the exact point of failure. "
                "Propose a conceptual fix approach — do NOT implement anything."
                f"{prior_context}"
            ),
            output_type=RootCauseAnalysis,
        ),
        feature,
        phase_name=phase_name,
    )
    await runner.artifacts.put(
        f"bug-rca:{source}:{bug_id}",
        to_str(rca),
        feature=feature,
    )

    used_artifact_repair = False
    dag_product_cleanup_routes: dict[str, DagTaskDriftRoute] = {}
    dag_product_cleanup_guidance = ""
    dag_task_artifact_refs = _dag_task_artifact_refs_from_rca(
        rca,
        extra_text=verdict_text,
    )
    if _rca_needs_dag_task_artifact_repair(
        rca,
        extra_text=verdict_text,
        target_refs=dag_task_artifact_refs,
    ):
        drift_routes = await _dag_task_drift_routes_for_refs(
            runner,
            feature,
            dag_task_artifact_refs,
            feature_root,
            context_text=f"{verdict_text}\n\n{to_str(rca)}",
        )
        dag_product_cleanup_routes = {
            key: route for key, route in drift_routes.items()
            if route.route == "product_cleanup_required"
        }
        if dag_product_cleanup_routes:
            dag_product_cleanup_guidance = _dag_product_cleanup_guidance(
                dag_product_cleanup_routes
            )
        else:
            fix_result = await _run_rca_dag_task_artifact_repair(
                runner,
                feature,
                source=source,
                bug_id=bug_id,
                verdict_text=verdict_text,
                rca=rca,
                fixer=fixer,
                feature_root=feature_root,
                phase_name=phase_name,
                actor_builder=actor_builder,
                target_refs=dag_task_artifact_refs,
            )
            used_artifact_repair = True
    if not used_artifact_repair:
        # 2. Fix via implementer (with workspace_path for correct cwd)
        ws_path = _resolve_fix_workspace_from_root(feature_root, rca.affected_files)
        ws_ctx = (
            f"\n\n## Workspace\n"
            f"Your working directory is: `{ws_path}`\n"
            f"All file reads and writes MUST use paths within this directory.\n"
            f"Do NOT use absolute paths from search results that point to "
            f"other copies of the same repo.\n"
        ) if ws_path else ""

        fix_actor = actor_builder(
            fixer, f"fix-{bug_id}",
            workspace_path=ws_path,
        )
        fix_result = await runner.run(
            Ask(
                actor=fix_actor,
                prompt=(
                    f"## Bug Fix: {bug_id}\n\n"
                    f"### Root Cause Analysis\n\n"
                    f"**Hypothesis:** {rca.hypothesis}\n\n"
                    f"**Evidence:**\n"
                    + "\n".join(f"- {e}" for e in rca.evidence)
                    + f"\n\n**Affected Files:**\n"
                    + "\n".join(f"- `{f}`" for f in rca.affected_files)
                    + f"\n\n**Proposed Approach:** {rca.proposed_approach}\n\n"
                    f"### Original Verdict\n\n{verdict_text}\n\n"
                    f"{ws_ctx}\n"
                    f"{dag_product_cleanup_guidance}\n"
                    "## Instructions\n"
                    "1. Read each affected file listed above\n"
                    "2. Apply the fix described in the RCA — be precise\n"
                    "3. Fix only what the root cause analysis identified\n"
                    "4. Report all files modified"
                    f"{prior_context}"
                ),
                output_type=ImplementationResult,
            ),
            feature,
            phase_name=phase_name,
        )
        if dag_product_cleanup_routes:
            await _append_dag_task_rows_from_product_repair(
                runner,
                feature,
                source=source,
                bug_id=bug_id,
                routes=dag_product_cleanup_routes,
                fix_result=fix_result,
                feature_root=feature_root,
            )

    if used_artifact_repair and fix_result.status == "blocked":
        return BugFixAttempt(
            bug_id=bug_id,
            source_verdict=source,
            description=verdict_text,
            root_cause=rca.hypothesis,
            fix_applied=fix_result.summary,
            files_modified=[],
            re_verify_result="FAIL",
            attempt_number=attempt_number,
        )

    # Commit fix before re-verification
    if not used_artifact_repair:
        commit_message = f"fix: {bug_id}"
        try:
            if workspace_root is None:
                await _commit_repos(
                    runner,
                    feature,
                    commit_message,
                )
            else:
                await _commit_repos_in_root(feature_root, commit_message)
        except WorkflowCommitError as exc:
            verdict = await _record_bug_commit_failure(
                runner,
                feature,
                source,
                bug_id,
                attempt_number,
                "fix",
                exc,
                message=commit_message,
            )
            return BugFixAttempt(
                bug_id=bug_id,
                source_verdict=source,
                description=verdict_text,
                root_cause=rca.hypothesis,
                fix_applied=(
                    fix_result.summary
                    + "\n\nCommit failed before reverify:\n"
                    + _format_feedback("Commit", verdict)
                ),
                files_modified=fix_result.files_created + fix_result.files_modified,
                re_verify_result="FAIL",
                attempt_number=attempt_number,
            )

    # 3. Re-verify with the SAME reviewer that found the bug
    re_verdict: Verdict = await runner.run(
        Ask(
            actor=actor_builder(
                original_reviewer,
                f"reverify-{bug_id}",
                workspace_path=str(feature_root) if feature_root else None,
            ),
            prompt=(
                f"## Re-verification: {bug_id}\n\n"
                f"A fix was applied for the following failure.\n\n"
                f"### Original Verdict\n\n{verdict_text}\n\n"
                f"### Root Cause\n\n{rca.hypothesis}\n\n"
                f"### Fix Applied\n\n{fix_result.summary}\n\n"
                f"### Files Modified\n\n"
                + "\n".join(f"- `{f}`" for f in (fix_result.files_created + fix_result.files_modified))
                + f"{test_plan_section}\n\n"
                "Re-verify that the original issues are resolved. "
                "Check that the fix does not introduce new problems. "
                "The verdict must be based on the CURRENT state of the code. "
                "When a Test Plan section is provided above, cite AC-ids in any "
                "remaining failures you find."
            ),
            output_type=Verdict,
        ),
        feature,
        phase_name=phase_name,
    )

    await runner.artifacts.put(
        f"bug-reverify:{source}:{bug_id}",
        to_str(re_verdict),
        feature=feature,
    )

    # Update ledger with re-verify results
    if isinstance(re_verdict, Verdict):
        ledger = await _load_ledger(runner, feature)
        ledger = _update_ledger(ledger, re_verdict, f"reverify:{source}", 0)
        await _save_ledger(runner, feature, ledger)

    # 4. Regression test on modified files (skip if fixing a regression)
    passed = _is_approved(re_verdict)
    if passed and not skip_regression:
        modified = fix_result.files_created + fix_result.files_modified
        regression_verdict = await _run_regression(
            runner, feature, modified, handover_context=handover_context,
            phase_name=phase_name,
            workspace_root=feature_root if workspace_root else None,
            regression_runtime=rca_runtime,
            integration_runtime=rca_runtime,
            actor_factory=actor_factory,
        )
        if regression_verdict is not None:
            await runner.artifacts.put(
                f"bug-regression:{source}:{bug_id}",
                to_str(regression_verdict),
                feature=feature,
            )
            if not _is_approved(regression_verdict):
                logger.warning("Regression found after fix %s — attempting in-place fix", bug_id)
                # Add regression findings to ledger
                if isinstance(regression_verdict, Verdict):
                    ledger = await _load_ledger(runner, feature)
                    ledger = _update_ledger(
                        ledger, regression_verdict, f"regression:{source}", 0,
                    )
                    await _save_ledger(runner, feature, ledger)
                # Fix regression in-place (skip_regression=True prevents recursion)
                regression_attempt = await _single_rca_fix_verify(
                    runner, feature,
                    _format_feedback("Regression", regression_verdict),
                    f"regression:{source}",
                    original_reviewer, fixer, prior_context,
                    bug_id=f"{bug_id}-REGRESSION",
                    attempt_number=attempt_number,
                    handover_context=handover_context,
                    test_plan_section=test_plan_section,
                    skip_regression=True,
                    phase_name=phase_name,
                    workspace_root=feature_root if workspace_root else None,
                    rca_runtime=rca_runtime,
                    actor_factory=actor_factory,
                )
                passed = regression_attempt.re_verify_result == "PASS"
                if passed:
                    commit_message = f"fix: regression after {bug_id}"
                    try:
                        if workspace_root is None:
                            await _commit_repos(
                                runner,
                                feature,
                                commit_message,
                            )
                        else:
                            await _commit_repos_in_root(
                                feature_root, commit_message,
                            )
                    except WorkflowCommitError as exc:
                        verdict = await _record_bug_commit_failure(
                            runner,
                            feature,
                            source,
                            bug_id,
                            attempt_number,
                            "regression",
                            exc,
                            message=commit_message,
                        )
                        passed = False
                        fix_result.summary += (
                            "\n\nRegression fix commit failed:\n"
                            + _format_feedback("Commit", verdict)
                        )

    return BugFixAttempt(
        bug_id=bug_id,
        source_verdict=source,
        description=verdict_text,
        root_cause=rca.hypothesis,
        fix_applied=fix_result.summary,
        files_modified=fix_result.files_created + fix_result.files_modified,
        re_verify_result="PASS" if passed else "FAIL",
        attempt_number=attempt_number,
    )


# ── Persistence ──────────────────────────────────────────────────────────────


async def _escalate_contradiction(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    source: str,
    group: BugGroup,
    rca: RootCauseAnalysis,
) -> str:
    """Interview the user about a spec contradiction. Blocks until resolved."""
    result = await runner.run(
        HostedInterview(
            questioner=lead_architect_gate_reviewer,
            responder=interaction_actor_for_phase(
                runner,
                feature,
                phase_name=phase_name,
                fallback=user,
            ),
            initial_prompt=(
                f"## Specification Contradiction Detected\n\n"
                f"**Source:** {source} verification\n"
                f"**Bug Group:** {group.group_id} — {group.likely_root_cause}\n\n"
                f"### The Contradiction\n{rca.contradiction_detail}\n\n"
                f"### Evidence\n"
                + "\n".join(f"- {e}" for e in rca.evidence)
                + "\n\n"
                f"### Best-Guess Resolution\n{rca.proposed_approach}\n"
                f"*(Based on D-GR-1: most recent authoritative source)*\n\n"
                f"Please confirm the best-guess direction, override with the "
                f"other source, or provide a new decision."
            ),
            output_type=Envelope[ReviewOutcome],
            done=envelope_done,
            artifact_key=f"contradiction:{source}:{group.group_id}",
            artifact_label=f"Contradiction — {group.group_id}",
        ),
        feature,
        phase_name=phase_name,
    )
    if result and result.output:
        outcome = result.output
        if outcome.approved:
            return rca.proposed_approach  # user confirmed best-guess
        # User overrode — extract ALL directions from revision_plan
        if outcome.revision_plan and outcome.revision_plan.requests:
            directions = []
            for i, req in enumerate(outcome.revision_plan.requests, 1):
                directions.append(f"{i}. {req.description}")
            return "\n\n".join(directions)
        return rca.proposed_approach  # fallback
    # Also check the written artifact for the user's response
    discussion = await runner.artifacts.get(
        f"contradiction:{source}:{group.group_id}", feature=feature,
    )
    if discussion:
        return discussion  # user decisions are authoritative — never truncate
    return rca.proposed_approach


def _load_prior_attempts(raw: str | None) -> list[BugFixAttempt]:
    """Reconstruct prior fix attempts from the stored artifact."""
    if not raw:
        return []
    attempts: list[BugFixAttempt] = []
    depth = 0
    start = None
    for i, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(raw[start : i + 1])
                    if isinstance(obj, dict) and "bug_id" in obj:
                        attempts.append(BugFixAttempt.model_validate(obj))
                except Exception:
                    pass
                start = None
    return attempts


async def _store_attempts(
    runner: WorkflowRunner,
    feature: Feature,
    attempts: list[BugFixAttempt],
) -> None:
    """Persist bug fix attempts as an artifact for audit trail."""
    text = "\n\n".join(to_str(a) for a in attempts)
    await runner.artifacts.put("bug-fix-attempts", text, feature=feature)


async def _run_regression(
    runner: WorkflowRunner,
    feature: Feature,
    modified_files: list[str],
    handover_context: str = "",
    phase_name: str = "implementation",
    workspace_root: Path | None = None,
    regression_runtime: str | None = None,
    integration_runtime: str | None = None,
    actor_factory: Callable[..., AgentActor] | None = None,
) -> Verdict | None:
    """Run regression tests on files modified by bug fixes.

    Returns None if no files to test, otherwise a Verdict.
    When *handover_context* is provided, also runs an integration-style
    regression on user journeys touching the modified files.
    """
    if not modified_files:
        return None

    actor_builder = actor_factory or _make_parallel_actor
    deduped_files = sorted(set(modified_files))
    actor_suffix = str(abs(hash("|".join(deduped_files))))[:8]
    file_list = "\n".join(f"- `{f}`" for f in deduped_files)
    # Load test plan once — used by both the smoke regression and the
    # integration-regression gate below so AC-id traceability is symmetric
    # across both post-fix checks.
    test_plan_section = await _load_test_plan_section(runner, feature)
    regression_verdict: Verdict = await runner.run(
        Ask(
            actor=actor_builder(
                regression_tester,
                f"regression-{feature.id}-{actor_suffix}",
                runtime=regression_runtime,
                workspace_path=str(workspace_root) if workspace_root else None,
            ),
            prompt=(
                f"## Regression Check After Bug Fixes\n\n"
                f"The following files were modified during bug fix cycles:\n"
                f"{file_list}"
                f"{test_plan_section}\n\n"
                "Run existing tests covering these files. Then probe the "
                "changed surfaces for regressions the test suite doesn't cover. "
                "Focus on downstream consumers and integration points. "
                "When a Test Plan section is provided above, cite AC-ids for any "
                "regressions you identify against specific acceptance criteria."
            ),
            output_type=Verdict,
        ),
        feature,
        phase_name=phase_name,
    )

    if not _is_approved(regression_verdict):
        return regression_verdict

    # ── Integration regression: re-run affected user journeys ─────────
    if handover_context:
        regression_context = await _build_prompt_context_package(
            runner,
            feature,
            title="Integration Regression",
            file_stem=f"integration-regression-{actor_suffix}",
            intro_lines=[
                "Re-run the affected user journeys after the bug-fix changes.",
                "Use the implementation handover and test plan files as the source of truth for affected journeys.",
            ],
            sections=[
                ("handover", "Implementation Handover", handover_context),
                ("test-plan", "Test Plan", test_plan_section),
            ],
        )
        integration_verdict: Verdict = await runner.run(
            Ask(
                actor=actor_builder(
                    integration_tester,
                    f"integration-regression-{feature.id}-{actor_suffix}",
                    runtime=integration_runtime,
                    workspace_path=str(workspace_root) if workspace_root else None,
                ),
                prompt=(
                    f"## Integration Regression Check\n\n"
                    f"The following files were modified during bug fix cycles:\n"
                    f"{file_list}\n\n"
                    f"{_context_package_prompt(regression_context)}"
                    "Re-execute ONLY the user journeys from the PRD that touch "
                    "the modified files listed above. Use Playwright for UI "
                    "journeys, Bash for API/CLI journeys. This is a targeted "
                    "regression check — verify that existing journeys still "
                    "work correctly after the bug fix changes. When a Test "
                    "Plan section is provided above, cite AC-ids for any "
                    "regressions you find."
                ),
                output_type=Verdict,
            ),
            feature,
            phase_name=phase_name,
        )
        if not _is_approved(integration_verdict):
            return integration_verdict

    return regression_verdict


# ── Helpers ──────────────────────────────────────────────────────────────────



async def _enrich_fallback_result(
    result: ImplementationResult,
    ws_path: str | None,
    task: ImplementationTask,
) -> None:
    """Populate files_created/files_modified from git when agent failed to produce structured output."""
    if result.files_created or result.files_modified:
        return  # Agent reported files — no enrichment needed
    if not ws_path:
        return

    try:
        status_output = await _run_git(Path(ws_path), "status", "--porcelain")
    except Exception:
        logger.warning("Could not run git status for fallback enrichment in %s", ws_path)
        return

    if not status_output:
        return

    # Build expected paths from file_scope for filtering in parallel-task groups.
    # Git status is repo-relative, while DAG scopes are workspace-relative.
    scope_paths: set[str] = set()
    for fs in task.file_scope:
        path = fs.path
        scope_paths.add(path)
        if task.repo_path and path.startswith(f"{task.repo_path}/"):
            scope_paths.add(path[len(task.repo_path) + 1:])

    created: list[str] = []
    modified: list[str] = []
    for line in status_output.splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        path = line[3:].strip().strip('"')

        # Filter to task's file_scope when available
        if scope_paths and path not in scope_paths:
            continue

        if xy in ("??", "A ", "AM"):
            created.append(path)
        elif "M" in xy or "R" in xy:
            modified.append(path)

    if created or modified:
        result.files_created = created
        result.files_modified = modified
        logger.info(
            "Enriched fallback result for %s: %d created, %d modified",
            result.task_id, len(created), len(modified),
        )


def _collect_files(results: list[object]) -> list[str]:
    """Extract file paths from implementation results."""
    files: list[str] = []
    for r in results:
        if isinstance(r, ImplementationResult):
            files.extend(r.files_created)
            files.extend(r.files_modified)
    return _dedupe_preserving_order(files)


def _is_approved(verdict: object) -> bool:
    """Approve if no blocker/major findings exist, regardless of agent opinion."""
    if not isinstance(verdict, Verdict):
        return False
    for c in verdict.concerns:
        if c.severity in BLOCKING_SEVERITIES:
            return False
    for g in verdict.gaps:
        if g.severity in BLOCKING_SEVERITIES:
            return False
    for ch in verdict.checks:
        if ch.result == "FAIL":
            return False
    return True


# ── Finding ledger ──────────────────────────────────────────────────────────


async def _load_ledger(
    runner: WorkflowRunner, feature: Feature,
) -> FindingLedger:
    """Load the finding ledger from the artifact store."""
    raw = await runner.artifacts.get("finding-ledger", feature=feature)
    if raw:
        try:
            return FindingLedger.model_validate_json(raw)
        except Exception:
            logger.warning("Failed to parse finding ledger — starting fresh")
    return FindingLedger()


async def _save_ledger(
    runner: WorkflowRunner, feature: Feature, ledger: FindingLedger,
) -> None:
    """Save the finding ledger to the artifact store."""
    await runner.artifacts.put(
        "finding-ledger", ledger.model_dump_json(), feature=feature,
    )


def _text_overlap(a: str, b: str) -> float:
    """Word-level Jaccard similarity between two strings."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def _dedup_findings(
    verdict: Verdict, ledger: FindingLedger, source: str,
) -> tuple[Verdict, list[FindingRecord]]:
    """Remove findings that match resolved ledger entries (unchanged files).

    Returns (filtered_verdict, list_of_suppressed_records).
    """
    resolved = [
        f for f in ledger.findings
        if f.status == "resolved" and f.source == source
    ]
    if not resolved:
        return verdict, []

    new_concerns = []
    suppressed: list[FindingRecord] = []
    for c in verdict.concerns:
        is_dup = False
        for r in resolved:
            if _text_overlap(c.description, r.description) > 0.5:
                # Same finding — only suppress if the file hasn't changed
                if c.file and c.file == r.file:
                    is_dup = True
                    suppressed.append(r)
                    break
        if not is_dup:
            new_concerns.append(c)

    new_gaps = []
    for g in verdict.gaps:
        is_dup = False
        for r in resolved:
            if _text_overlap(g.description, r.description) > 0.5:
                is_dup = True
                suppressed.append(r)
                break
        if not is_dup:
            new_gaps.append(g)

    filtered = verdict.model_copy(update={
        "concerns": new_concerns,
        "gaps": new_gaps,
    })
    return filtered, suppressed


def _update_ledger(
    ledger: FindingLedger, verdict: Verdict, source: str, cycle: int,
) -> FindingLedger:
    """Add new findings from a verdict, mark resolved ones.

    Findings from the same source that appeared in prior cycles but are
    absent from the current verdict are marked ``resolved``.
    """
    # Collect current verdict descriptions for comparison
    current_descs = {c.description for c in verdict.concerns}
    current_descs |= {g.description for g in verdict.gaps}

    # Mark previously-open findings from this source as resolved
    # if they no longer appear in the current verdict
    for f in ledger.findings:
        if f.source == source and f.status == "open":
            if not any(
                _text_overlap(f.description, d) > 0.5 for d in current_descs
            ):
                f.status = "resolved"
                f.cycle_resolved = cycle

    existing_descs = {f.description for f in ledger.findings}
    next_id = len(ledger.findings) + 1

    # Add new findings
    for c in verdict.concerns:
        if c.description not in existing_descs:
            ledger.findings.append(FindingRecord(
                id=f"F-{next_id:03d}",
                source=source,
                description=c.description,
                file=c.file,
                line=c.line,
                severity=c.severity,
                status="open",
                cycle_introduced=cycle,
            ))
            next_id += 1

    for g in verdict.gaps:
        if g.description not in existing_descs:
            ledger.findings.append(FindingRecord(
                id=f"F-{next_id:03d}",
                source=source,
                description=g.description,
                severity=g.severity,
                category=g.category,
                status="open",
                cycle_introduced=cycle,
            ))
            next_id += 1

    ledger.cycle = cycle
    return ledger


# ── Enhancement backlog ─────────────────────────────────────────────────────


def _partition_verdict(
    verdict: Verdict, source: str, task_context: str = "",
) -> tuple[Verdict, list[EnhancementItem]]:
    """Split a verdict into blocking-only and non-blocking enhancement items."""
    blocking_concerns = [
        c for c in verdict.concerns if c.severity in BLOCKING_SEVERITIES
    ]
    non_blocking_concerns = [
        c for c in verdict.concerns if c.severity not in BLOCKING_SEVERITIES
    ]
    blocking_gaps = [
        g for g in verdict.gaps if g.severity in BLOCKING_SEVERITIES
    ]
    non_blocking_gaps = [
        g for g in verdict.gaps if g.severity not in BLOCKING_SEVERITIES
    ]

    blocking_verdict = verdict.model_copy(update={
        "concerns": blocking_concerns,
        "gaps": blocking_gaps,
    })

    enhancements: list[EnhancementItem] = []
    for c in non_blocking_concerns:
        enhancements.append(EnhancementItem(
            source=source, severity=c.severity,
            description=c.description, file=c.file, line=c.line,
            task_context=task_context,
        ))
    for g in non_blocking_gaps:
        enhancements.append(EnhancementItem(
            source=source, severity=g.severity,
            description=g.description, category=g.category,
            task_context=task_context,
        ))
    for s in verdict.suggestions:
        enhancements.append(EnhancementItem(
            source=source, severity="nit",
            description=s, task_context=task_context,
        ))

    return blocking_verdict, enhancements


async def _append_enhancements(
    runner: WorkflowRunner, feature: Feature,
    items: list[EnhancementItem],
) -> None:
    """Append non-blocking findings to the feature's enhancement backlog."""
    if not items:
        return
    raw = await runner.artifacts.get("enhancement-backlog", feature=feature)
    if raw:
        try:
            backlog = EnhancementBacklog.model_validate_json(raw)
        except Exception:
            backlog = EnhancementBacklog()
    else:
        backlog = EnhancementBacklog()

    # Dedup: skip items that match existing ones (exact or fuzzy)
    existing_descs = [i.description for i in backlog.items]
    new_items = []
    for item in items:
        if item.description in existing_descs:
            continue  # exact match
        if any(_text_overlap(item.description, d) > 0.5 for d in existing_descs):
            continue  # fuzzy match
        new_items.append(item)
        existing_descs.append(item.description)  # prevent intra-batch dupes
    if not new_items:
        return
    backlog.items.extend(new_items)
    await runner.artifacts.put(
        "enhancement-backlog", backlog.model_dump_json(), feature=feature,
    )
    logger.info(
        "Enhancement backlog: +%d items, %d dupes skipped (total: %d)",
        len(new_items), len(items) - len(new_items), len(backlog.items),
    )


def _render_enhancement_backlog_html(
    backlog: EnhancementBacklog, feature_name: str,
) -> str:
    """Render the enhancement backlog as a standalone HTML page."""
    from html import escape

    # Group by source
    by_source: dict[str, list[EnhancementItem]] = {}
    for item in backlog.items:
        by_source.setdefault(item.source, []).append(item)

    rows = []
    for source, items in sorted(by_source.items()):
        for item in items:
            sev_class = "minor" if item.severity == "minor" else "nit"
            file_ref = f"<code>{escape(item.file)}</code>" if item.file else ""
            rows.append(
                f"<tr>"
                f"<td>{escape(source)}</td>"
                f'<td><span class="sev-{sev_class}">{escape(item.severity)}</span></td>'
                f"<td>{escape(item.description)}</td>"
                f"<td>{file_ref}</td>"
                f"</tr>"
            )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Enhancement Backlog — {escape(feature_name)}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; color: #1a1a2e; }}
h1 {{ font-size: 1.5rem; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 0.875rem; }}
th {{ background: #f5f5f5; }}
.sev-minor {{ background: #fef3c7; color: #92400e; padding: 2px 6px; border-radius: 3px; font-size: 0.75rem; }}
.sev-nit {{ background: #e0e7ff; color: #3730a3; padding: 2px 6px; border-radius: 3px; font-size: 0.75rem; }}
code {{ background: #f3f4f6; padding: 1px 4px; border-radius: 2px; font-size: 0.8rem; }}
</style></head><body>
<h1>Enhancement Backlog — {escape(feature_name)}</h1>
<p>{len(backlog.items)} non-blocking findings deferred from implementation verification.</p>
<table>
<thead><tr><th>Source</th><th>Severity</th><th>Description</th><th>File</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
</body></html>"""


def _format_feedback(source: str, verdict: object) -> str:
    """Format a Verdict into human-readable markdown for fix agents."""
    if not isinstance(verdict, Verdict):
        return f"## {source} Feedback\n\n{to_str(verdict)}"

    parts = [f"## {source} Feedback\n"]
    parts.append(f"**Status:** {'APPROVED' if verdict.approved else 'FAILED'}")
    parts.append(f"**Summary:** {verdict.summary}\n")

    if verdict.concerns:
        parts.append("### Issues Found\n")
        for i, c in enumerate(verdict.concerns, 1):
            file_ref = f" in `{c.file}`" if c.file else ""
            line_ref = f" (line {c.line})" if c.line else ""
            parts.append(f"{i}. **[{c.severity}]** {c.description}{file_ref}{line_ref}")
        parts.append("")

    if verdict.gaps:
        parts.append("### Gaps\n")
        for i, g in enumerate(verdict.gaps, 1):
            ref = f" (ref: {g.plan_reference})" if g.plan_reference else ""
            parts.append(f"{i}. **[{g.severity}/{g.category}]** {g.description}{ref}")
        parts.append("")

    if verdict.checks:
        failed_checks = [c for c in verdict.checks if c.result == "FAIL"]
        if failed_checks:
            parts.append("### Failed Checks\n")
            for c in failed_checks:
                detail = f": {c.detail}" if c.detail else ""
                parts.append(f"- **FAIL** {c.criterion}{detail}")
            parts.append("")

    # Collect all affected files for easy reference
    affected_files = sorted({c.file for c in verdict.concerns if c.file})
    if affected_files:
        parts.append("### Affected Files\n")
        for f in affected_files:
            parts.append(f"- `{f}`")
        parts.append("")

    return "\n".join(parts)


def _collect_artifact_urls(runner: WorkflowRunner) -> dict[str, str]:
    """Collect hosted artifact URLs from the hosting service."""
    hosting = runner.services.get("hosting")
    if not hosting:
        return {}
    urls: dict[str, str] = {}
    for key in ("prd", "design", "plan", "system-design", "mockup"):
        url = hosting.get_url(key)
        if url:
            urls[key] = url
    return urls


def _collect_screenshots(feature: Feature, runner: WorkflowRunner | None = None) -> list[str]:
    """Collect Playwright screenshot paths from the feature worktree.

    Searches the feature's worktree repos (not the main workspace) for
    screenshots in common Playwright output locations.
    """
    import glob

    # Primary: search the feature's worktree directory
    search_roots: list[str] = []

    workspace_mgr = runner.services.get("workspace_manager") if runner else None
    if workspace_mgr:
        feature_root = Path(workspace_mgr._base) / ".iriai" / "features" / feature.slug / "repos"
        if feature_root.exists():
            search_roots.append(str(feature_root))

    # Fallback: try workspace_path on feature (for CLI mode)
    if not search_roots:
        workspace = getattr(feature, "workspace_path", "") or ""
        if workspace:
            search_roots.append(workspace)

    if not search_roots:
        return []

    patterns_per_root = [
        "**/screenshots/*.png",
        "**/test-results/**/*.png",
        "**/playwright-report/**/*.png",
        "**/*.screenshot.png",
    ]
    paths: list[str] = []
    for root in search_roots:
        for pattern in patterns_per_root:
            paths.extend(glob.glob(f"{root}/{pattern}", recursive=True))
    return sorted(set(paths))
