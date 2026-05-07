from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .models import (
    ArtifactRecord,
    BridgeProbe,
    CurrentWorkflowSnapshot,
    EventRecord,
    FeatureSnapshot,
    GitPathFact,
    SupervisorObservation,
    WorktreeProbe,
)

KEY_PREFIXES = (
    "dag-verify:",
    "dag-repair-preflight:",
    "dag-authority-gate:",
    "dag-direct-repair-route:",
    "dag-repair-expanded-verify:",
    "dag-repair-lens:",
    "dag-verify-rca:",
    "dag-repair-dispatch:",
    "dag-fix:",
    "dag-task-reconcile:",
    "dag-task-spec-reconcile:",
    "dag-task-product-reconcile:",
    "dag-commit-failure:",
    "dag-group:",
    "dag-path-canonicalization:",
    "dag-writeability-preflight:",
    "bug-",
    "finding-ledger",
)
_CURRENT_EVENT_TYPES = {
    "dag_task_dispatch",
    "dag_task_start",
    "dag_task_finish",
    "agent_start",
    "agent_done",
    "agent_invocation_start",
    "agent_invocation_done",
    "dag_verify_start",
    "dag_verify_finish",
    "dag_commit_failed",
}


class FeatureStoreReader(Protocol):
    async def get_feature(self, feature_id: str) -> Any | None: ...

    async def get_events(self, feature_id: str) -> list[dict[str, Any]]: ...


class ArtifactStoreReader(Protocol):
    async def get_record(self, key: str, *, feature: Any) -> dict[str, Any] | None: ...

    async def list_records(
        self,
        *,
        feature_id: str,
        prefixes: tuple[str, ...],
        after_id: int,
        limit: int = 500,
        order: str = "asc",
    ) -> list[dict[str, Any]]: ...


class DashboardClient(Protocol):
    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...

    async def post_json(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]: ...


async def collect_evidence(
    *,
    feature_id: str,
    feature_store: FeatureStoreReader,
    artifact_store: ArtifactStoreReader | Any,
    cursor: int = 0,
    event_cursor: int | None = None,
    artifact_cursor: int | None = None,
    dashboard_url: str | None = None,
    dashboard_client: DashboardClient | None = None,
    bridge_log_cursor: int = 0,
    worktree_roots: list[Path | str] | None = None,
    forbidden_paths: list[str] | None = None,
) -> SupervisorObservation:
    feature = await feature_store.get_feature(feature_id)
    feature_snapshot = _feature_snapshot(feature, feature_id)
    event_start_cursor = cursor if event_cursor is None else event_cursor
    artifact_start_cursor = cursor if artifact_cursor is None else artifact_cursor
    all_events = await _load_all_events(feature_store, feature_id)
    events = [
        record
        for record in all_events
        if (record.id or 0) > event_start_cursor
    ]
    artifacts = await _load_artifacts(
        artifact_store,
        feature,
        feature_id,
        artifact_start_cursor,
    )
    bridge = None
    if dashboard_url or dashboard_client:
        bridge = await probe_bridge(
            dashboard_url=dashboard_url,
            client=dashboard_client,
            after=bridge_log_cursor,
        )
    current = build_current_workflow_snapshot(
        events=all_events,
        artifacts=artifacts,
        bridge=bridge,
        phase=feature_snapshot.phase,
    )
    worktrees = [
        probe_worktree(Path(root), forbidden_paths=forbidden_paths or [])
        for root in (worktree_roots or [])
    ]
    next_event_cursor = max(
        [event_start_cursor] + [event.id or event_start_cursor for event in events]
    )
    next_artifact_cursor = max(
        [artifact_start_cursor] + [artifact.id or artifact_start_cursor for artifact in artifacts]
    )
    next_cursor = max(cursor, next_event_cursor, next_artifact_cursor)
    return SupervisorObservation(
        feature_id=feature_id,
        phase=feature_snapshot.phase,
        event_cursor=event_start_cursor,
        next_event_cursor=next_event_cursor,
        artifact_cursor=artifact_start_cursor,
        next_artifact_cursor=next_artifact_cursor,
        bridge_log_cursor=bridge.log_cursor if bridge is not None else bridge_log_cursor,
        cursor=cursor,
        next_cursor=next_cursor,
        feature=feature_snapshot,
        events=events,
        artifacts=artifacts,
        bridge=bridge,
        current=current,
        worktrees=worktrees,
        query_labels=["feature", "events", "artifacts", "bridge", "worktrees"],
    )


async def probe_bridge(
    *,
    dashboard_url: str | None = None,
    client: DashboardClient | None = None,
    after: int = 0,
) -> BridgeProbe:
    probe = BridgeProbe(dashboard_url=dashboard_url, log_cursor=after)
    try:
        if client is None:
            if not dashboard_url:
                return probe.model_copy(update={"errors": ["dashboard_url missing"]})
            client = UrlDashboardClient(dashboard_url)
        status = await client.get_json("/api/bridge/status")
        logs = await client.get_json("/api/bridge/logs", {"after": after})
        lines = [str(line) for line in logs.get("lines", [])]
        errors = [line for line in lines if _bridge_log_is_error(line)]
        return probe.model_copy(
            update={
                "ok": True,
                "status": status,
                "log_cursor": int(logs.get("cursor", after) or 0),
                "log_lines": lines,
                "errors": errors,
            }
        )
    except Exception as exc:
        return probe.model_copy(update={"errors": [f"{type(exc).__name__}: {exc}"]})


class UrlDashboardClient:
    def __init__(self, base_url: str, *, timeout: float = 2.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self._request_json, "GET", path, params, None)

    async def post_json(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self._request_json, "POST", path, None, payload or {})

    def _request_json(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}{query}",
            data=data,
            method=method,
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw or "{}")


def probe_worktree(
    root: Path,
    *,
    forbidden_paths: list[str] | None = None,
    direct_repo_roots: list[Path] | None = None,
) -> WorktreeProbe:
    root = root.resolve()
    probe = WorktreeProbe(root=str(root))
    if not root.exists():
        return probe.model_copy(update={"ok": False, "errors": [f"{root} does not exist"]})
    direct_repo_set = {root}
    direct_repo_set.update(path.resolve() for path in (direct_repo_roots or []))
    embedded = _find_embedded_git_dirs(root, direct_repo_set)
    pending, proposed = _find_pending_or_proposed(root)
    unwritable = _find_unwritable_targets(root, forbidden_paths or [])
    dirty, gitlinks, branch, git_errors = _git_facts(root)
    forbidden = _forbidden_facts(root, forbidden_paths or [], dirty)
    return WorktreeProbe(
        root=str(root),
        ok=not git_errors,
        branch=branch,
        dirty_paths=dirty,
        embedded_git_paths=embedded,
        gitlinks=gitlinks,
        forbidden_paths=forbidden,
        pending_paths=pending,
        proposed_paths=proposed,
        unwritable_paths=unwritable,
        errors=git_errors,
    )


async def _load_all_events(
    feature_store: FeatureStoreReader,
    feature_id: str,
) -> list[EventRecord]:
    rows = await feature_store.get_events(feature_id)
    records = [_event_record(row) for row in rows]
    return sorted(records, key=lambda record: record.id or 0)


async def _load_artifacts(
    artifact_store: Any,
    feature: Any,
    feature_id: str,
    cursor: int,
) -> list[ArtifactRecord]:
    if hasattr(artifact_store, "list_records"):
        rows = await _list_artifact_records(
            artifact_store,
            feature_id=feature_id,
            prefixes=KEY_PREFIXES,
            after_id=cursor,
            limit=500,
            order="asc",
        )
        latest_rows: list[dict[str, Any]] = []
        for prefix in KEY_PREFIXES:
            latest_rows.extend(
                await _list_artifact_records(
                    artifact_store,
                    feature_id=feature_id,
                    prefixes=(prefix,),
                    after_id=0,
                    limit=50,
                    order="desc",
                )
            )
        return sorted(
            _dedupe_artifact_records(
                [*_artifact_records(rows), *_artifact_records(latest_rows)]
            ),
            key=lambda item: item.id or 0,
        )
    if hasattr(artifact_store, "list_artifacts"):
        rows = await artifact_store.list_artifacts(feature_id, prefixes=KEY_PREFIXES, after_id=cursor)
        return sorted(_artifact_records(rows), key=lambda item: item.id or 0)
    pool = getattr(artifact_store, "_pool", None)
    if pool is not None:
        clauses = " OR ".join(f"key LIKE ${idx + 3}" for idx, _prefix in enumerate(KEY_PREFIXES))
        rows = await pool.fetch(
            f"""
            SELECT id, key, created_at, value
            FROM artifacts
            WHERE feature_id = $1 AND id > $2 AND ({clauses})
            ORDER BY id
            """,
            feature_id,
            cursor,
            *[f"{prefix}%" for prefix in KEY_PREFIXES],
        )
        return [_artifact_record(dict(row)) for row in rows]
    if feature is None or not hasattr(artifact_store, "get_record"):
        return []
    records: list[ArtifactRecord] = []
    for key in _candidate_keys(feature):
        row = await artifact_store.get_record(key, feature=feature)
        if row is None:
            continue
        row = {**row, "key": key}
        record = _artifact_record(row)
        if (record.id or 0) > cursor:
            records.append(record)
    return sorted(records, key=lambda item: item.id or 0)


async def _list_artifact_records(
    artifact_store: Any,
    *,
    feature_id: str,
    prefixes: tuple[str, ...],
    after_id: int,
    limit: int,
    order: str,
) -> list[dict[str, Any]]:
    try:
        return await artifact_store.list_records(
            feature_id=feature_id,
            prefixes=prefixes,
            after_id=after_id,
            limit=limit,
            order=order,
        )
    except TypeError:
        try:
            return await artifact_store.list_records(
                feature_id=feature_id,
                prefixes=prefixes,
                after_id=after_id,
                limit=limit,
            )
        except TypeError:
            return await artifact_store.list_records(
                feature_id=feature_id,
                prefixes=prefixes,
                after_id=after_id,
            )


def _candidate_keys(feature: Any) -> list[str]:
    metadata = getattr(feature, "metadata", {}) or {}
    groups = metadata.get("supervisor_groups") or metadata.get("dag_groups") or []
    retries = metadata.get("supervisor_retries") or ["initial", "retry-initial", "retry-0", "retry-1"]
    keys: list[str] = []
    for group in groups:
        group_text = str(group).removeprefix("g")
        keys.append(f"dag-group:{group_text}")
        for retry in retries:
            keys.extend(
                [
                    f"dag-verify:g{group_text}:{retry}",
                    f"dag-repair-preflight:g{group_text}:{retry}",
                    f"dag-authority-gate:g{group_text}:{retry}",
                    f"dag-direct-repair-route:g{group_text}:{retry}",
                    f"dag-repair-expanded-verify:g{group_text}:{retry}",
                    f"dag-verify-rca:g{group_text}:{retry}",
                    f"dag-repair-dispatch:g{group_text}:{retry}",
                    f"dag-fix:g{group_text}:{retry}",
                    f"dag-task-reconcile:g{group_text}:{retry}",
                    f"dag-task-spec-reconcile:g{group_text}:{retry}",
                    f"dag-task-product-reconcile:g{group_text}:{retry}",
                    f"dag-commit-failure:g{group_text}:{retry}",
                ]
            )
    return keys


def _feature_snapshot(feature: Any, feature_id: str) -> FeatureSnapshot:
    if feature is None:
        return FeatureSnapshot(feature_id=feature_id)
    metadata = dict(getattr(feature, "metadata", {}) or {})
    return FeatureSnapshot(
        feature_id=getattr(feature, "id", feature_id),
        name=getattr(feature, "name", ""),
        slug=getattr(feature, "slug", ""),
        workflow_name=getattr(feature, "workflow_name", ""),
        workspace_id=getattr(feature, "workspace_id", ""),
        phase=str(metadata.get("_db_phase") or metadata.get("phase") or ""),
        metadata=metadata,
    )


def _artifact_record(row: dict[str, Any]) -> ArtifactRecord:
    value = row.get("value")
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return ArtifactRecord(
        id=row.get("id"),
        key=str(row.get("key", "")),
        value=value,
        created_at=row.get("created_at"),
        sha256=row.get("sha256") or hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _artifact_records(rows: list[dict[str, Any]]) -> list[ArtifactRecord]:
    records = [_artifact_record(row) for row in rows]
    return [record for record in records if record.key.startswith(KEY_PREFIXES)]


def _dedupe_artifact_records(records: list[ArtifactRecord]) -> list[ArtifactRecord]:
    seen: set[tuple[int | None, str, str | None]] = set()
    result: list[ArtifactRecord] = []
    for record in records:
        token = (record.id, record.key, record.sha256)
        if token in seen:
            continue
        seen.add(token)
        result.append(record)
    return result


def _event_record(row: dict[str, Any]) -> EventRecord:
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {"raw": metadata}
    return EventRecord(
        id=row.get("id"),
        event_type=str(row.get("event_type") or row.get("type") or ""),
        source=str(row.get("source") or ""),
        content=row.get("content"),
        metadata=metadata,
        created_at=row.get("created_at"),
    )


def build_current_workflow_snapshot(
    *,
    events: list[EventRecord],
    artifacts: list[ArtifactRecord],
    bridge: BridgeProbe | None,
    phase: str,
) -> CurrentWorkflowSnapshot:
    """Infer the live workflow position from current sources, not old artifacts.

    Events are the strongest signal because they record dispatch/invocation state.
    Artifact rows are secondary, and bridge logs are a fallback/supplement for
    active-agent UX when the DB has not emitted a fresh row yet.
    """

    sorted_events = sorted(events, key=lambda event: event.id or 0)
    sorted_artifacts = sorted(artifacts, key=lambda artifact: artifact.id or 0)
    group_idx: int | None = None
    retry: int | None = None
    source = ""
    citations: list[str] = []

    for event in reversed(sorted_events):
        if event.event_type not in _CURRENT_EVENT_TYPES:
            continue
        group = _event_group(event)
        if group is None:
            continue
        group_idx = group
        retry = _event_retry(event)
        source = "event"
        citations.append(event.citation)
        break

    if group_idx is None:
        for artifact in reversed(sorted_artifacts):
            group = _artifact_group(artifact.key)
            if group is None:
                continue
            group_idx = group
            retry = _artifact_retry(artifact.key)
            source = "artifact"
            citations.append(artifact.citation)
            break

    if bridge is not None:
        bridge_group, bridge_retry, bridge_citation = _bridge_current_group(bridge)
        if group_idx is None and bridge_group is not None:
            group_idx = bridge_group
            retry = bridge_retry
            source = "bridge"
            citations.append(bridge_citation)

    active_agents = _active_agents(sorted_events, group_idx)
    queued_agents: list[str] = []
    if bridge is not None:
        bridge_active, bridge_queued = _bridge_agents(bridge, group_idx)
        active_agents = _dedupe_strings([*active_agents, *bridge_active])
        queued_agents = bridge_queued
        if (bridge_active or bridge_queued) and "dashboard:/api/bridge/logs" not in citations:
            citations.append("dashboard:/api/bridge/logs")

    state = _current_state(
        events=sorted_events,
        bridge=bridge,
        group_idx=group_idx,
        active_agents=active_agents,
    )
    latest_artifact_id = max((artifact.id or 0 for artifact in sorted_artifacts), default=0) or None
    latest_event_id = max((event.id or 0 for event in sorted_events), default=0) or None
    return CurrentWorkflowSnapshot(
        group_idx=group_idx,
        retry=retry,
        phase=phase,
        state=state,
        source=source,
        active_agents=active_agents,
        queued_agents=queued_agents,
        latest_event_id=latest_event_id,
        latest_artifact_id=latest_artifact_id,
        citations=_dedupe_strings(citations),
    )


def _current_state(
    *,
    events: list[EventRecord],
    bridge: BridgeProbe | None,
    group_idx: int | None,
    active_agents: list[str],
) -> str:
    if active_agents:
        if any(_actor_is_verifier(actor) for actor in active_agents):
            return "verifying"
        return "implementing"
    for event in reversed(events):
        if group_idx is not None and _event_group(event) != group_idx:
            continue
        if event.event_type in {"dag_verify_start", "dag_verify_finish"}:
            return "verifying"
        if event.event_type in {
            "dag_task_dispatch",
            "dag_task_start",
            "dag_task_finish",
            "agent_start",
            "agent_done",
            "agent_invocation_start",
            "agent_invocation_done",
        }:
            return "implementing"
    if bridge is not None and bridge.process_state == "running":
        return "running"
    return ""


def _active_agents(events: list[EventRecord], group_idx: int | None) -> list[str]:
    active: dict[str, None] = {}
    for event in events:
        group = _event_group(event)
        actor = _event_actor(event)
        actor_group = _group_from_text(actor)
        if group_idx is not None:
            if group is not None and group != group_idx:
                continue
            if group is None and actor_group != group_idx:
                continue
        actor = actor or event.source
        if not actor:
            continue
        if event.event_type == "agent_invocation_start":
            active[actor] = None
        elif event.event_type in {"agent_done", "agent_error", "agent_invocation_done"}:
            active.pop(actor, None)
    return sorted(active)


def _bridge_current_group(bridge: BridgeProbe) -> tuple[int | None, int | None, str]:
    for line in reversed(bridge.log_lines):
        group = _group_from_text(line)
        if group is not None:
            return group, _retry_from_text(line), "dashboard:/api/bridge/logs"
    return None, None, "dashboard:/api/bridge/logs"


def _bridge_agents(
    bridge: BridgeProbe,
    group_idx: int | None,
) -> tuple[list[str], list[str]]:
    active: dict[str, None] = {}
    queued: dict[str, None] = {}
    for line in bridge.log_lines:
        actor = _actor_from_bridge_line(line)
        if not actor:
            continue
        actor_group = _group_from_text(actor) or _group_from_text(line)
        if group_idx is not None and actor_group is not None and actor_group != group_idx:
            continue
        lowered = line.lower()
        if "agent concurrency queued" in lowered:
            queued[actor] = None
        elif "agent concurrency acquired" in lowered:
            active[actor] = None
            queued.pop(actor, None)
        elif "agent concurrency released" in lowered:
            active.pop(actor, None)
    return sorted(active), sorted(queued)


def _actor_from_bridge_line(line: str) -> str | None:
    match = re.search(r"\bactor=(?P<actor>[^\s]+)", line)
    if match:
        return match.group("actor").strip()
    match = re.search(r"\b(?P<actor>[a-z][\w-]*-g\d+[\w-]*)\b", line)
    return match.group("actor") if match else None


def _actor_is_verifier(actor: str) -> bool:
    lowered = actor.lower()
    return any(token in lowered for token in ("verify", "verifier", "smoke", "regression", "security"))


def _event_actor(event: EventRecord) -> str:
    metadata = event.metadata or {}
    for key in ("actor", "actor_name", "agent", "role", "runtime_actor"):
        value = metadata.get(key)
        if value:
            return str(value)
    text = f"{event.source} {event.content or ''}"
    match = re.search(r"\b(?P<actor>[a-z][\w-]*-g\d+[\w-]*)\b", text)
    return match.group("actor") if match else str(event.source or "")


def _event_group(event: EventRecord) -> int | None:
    metadata = event.metadata or {}
    for key in ("group_idx", "group", "group_id"):
        value = metadata.get(key)
        group = _coerce_group(value)
        if group is not None:
            return group
    text = f"{event.event_type} {event.source} {event.content or ''} {json.dumps(metadata, sort_keys=True, default=str)}"
    return _group_from_text(text)


def _event_retry(event: EventRecord) -> int | None:
    metadata = event.metadata or {}
    for key in ("retry", "attempt", "retry_idx"):
        retry = _retry_from_value(metadata.get(key))
        if retry is not None:
            return retry
    text = f"{event.event_type} {event.source} {event.content or ''} {json.dumps(metadata, sort_keys=True, default=str)}"
    return _retry_from_text(text)


def _artifact_group(key: str) -> int | None:
    match = re.search(r"(?:^|:)g(?P<group>\d+)(?::|$|-)", key)
    if match:
        return int(match.group("group"))
    match = re.search(r"^dag-group:(?P<group>\d+)(?::|$)", key)
    return int(match.group("group")) if match else None


def _artifact_retry(key: str) -> int | None:
    if key.endswith(":initial") or key.endswith(":retry-initial"):
        return 0
    return _retry_from_text(key)


def _group_from_text(text: str) -> int | None:
    match = re.search(r"(?:^|[^A-Za-z0-9])g(?P<group>\d+)(?:[^A-Za-z0-9]|$)", text)
    if match:
        return int(match.group("group"))
    match = re.search(r"\bgroup[=:\s-]+(?P<group>\d+)\b", text, re.IGNORECASE)
    return int(match.group("group")) if match else None


def _coerce_group(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip().removeprefix("g").removeprefix("G"))
    except ValueError:
        return None


def _retry_from_text(text: str) -> int | None:
    if re.search(r"(?:^|:)initial(?:$|:)", text):
        return 0
    match = re.search(r"\bretry[-_:=\s]+(?P<retry>\d+)\b", text, re.IGNORECASE)
    return int(match.group("retry")) if match else None


def _retry_from_value(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    if text in {"initial", "retry-initial"}:
        return 0
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _bridge_log_is_error(line: str) -> bool:
    lowered = line.lower()
    return any(token in lowered for token in ("traceback", "error", "exception", "crash", "disconnect"))


def _find_embedded_git_dirs(root: Path, direct_repo_roots: set[Path]) -> list[str]:
    embedded: list[str] = []
    for path in root.rglob(".git"):
        parent = path.parent.resolve()
        if parent in direct_repo_roots:
            continue
        embedded.append(_rel(root, path))
    return sorted(embedded)


def _find_pending_or_proposed(root: Path) -> tuple[list[str], list[str]]:
    pending: list[str] = []
    proposed: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        rel = _rel(root, path)
        if name.startswith("_pending_"):
            pending.append(rel)
        if name.endswith(".PROPOSED") or ".PROPOSED." in name:
            proposed.append(rel)
    return sorted(pending), sorted(proposed)


def _find_unwritable_targets(root: Path, paths: list[str]) -> list[str]:
    unwritable: list[str] = []
    for raw in paths:
        target = root / raw
        parent = target if target.exists() and target.is_dir() else target.parent
        if parent.exists() and not os.access(parent, os.W_OK):
            unwritable.append(raw)
    return sorted(unwritable)


def _git_facts(root: Path) -> tuple[list[GitPathFact], list[str], str | None, list[str]]:
    dirty: list[GitPathFact] = []
    gitlinks: list[str] = []
    errors: list[str] = []
    branch = None
    branch_result = _run_git(root, "branch", "--show-current")
    if branch_result.returncode == 0:
        branch = branch_result.stdout.strip() or None
    status_result = _run_git(root, "status", "--porcelain=v1")
    if status_result.returncode == 0:
        for line in status_result.stdout.splitlines():
            if not line:
                continue
            status = line[:2].strip()
            path = line[3:] if len(line) > 3 else line
            dirty.append(GitPathFact(path=path, reason="git-status", status=status))
    else:
        errors.append(status_result.stderr.strip() or status_result.stdout.strip() or "git status failed")
    ls_result = _run_git(root, "ls-files", "-s")
    if ls_result.returncode == 0:
        for line in ls_result.stdout.splitlines():
            parts = line.split(maxsplit=3)
            if len(parts) == 4 and parts[0] == "160000":
                gitlinks.append(parts[3])
    return dirty, sorted(gitlinks), branch, errors


def _forbidden_facts(
    root: Path,
    paths: list[str],
    dirty: list[GitPathFact],
) -> list[GitPathFact]:
    dirty_by_path = {fact.path: fact for fact in dirty}
    facts: list[GitPathFact] = []
    for raw in paths:
        path = root / raw
        if path.exists():
            facts.append(GitPathFact(path=raw, reason="exists-on-disk"))
        dirty_fact = dirty_by_path.get(raw)
        if dirty_fact is not None and dirty_fact.status != "D":
            facts.append(
                GitPathFact(path=raw, reason="tracked-or-staged", status=dirty_fact.status)
            )
    return facts


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2,
        )
    except Exception as exc:
        return subprocess.CompletedProcess(["git", *args], 1, "", f"{type(exc).__name__}: {exc}")


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
