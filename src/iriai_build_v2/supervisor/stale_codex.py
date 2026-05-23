from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from .models import (
    BridgeProbe,
    CurrentWorkflowSnapshot,
    EventRecord,
    StaleCodexInvocation,
)

_HEARTBEAT_RE = re.compile(
    r"Codex heartbeat pid=(?P<pid>\d+)\s+"
    r"elapsed=(?P<elapsed>\d+(?:\.\d+)?)s\s+"
    r"trace=(?P<trace>\S+)\s+"
    r"stdout_events=(?P<stdout_events>\d+)\s+"
    r"stderr_lines=(?P<stderr_lines>\d+)\s+"
    r"output_bytes=(?P<output_bytes>\d+)\s+"
    r"last_event=(?P<last_event>\S+)\s+"
    r"last_item=(?P<last_item>\S+)"
)
_TRACE_ACTOR_RE = re.compile(
    r"^\d{8}T[\d.]+Z-(?P<actor>.+)-(?P<suffix>[0-9a-f]{8})\.jsonl$"
)
_GROUP_RE = re.compile(r"(?:^|[^A-Za-z0-9])g(?P<group>\d+)(?:[^A-Za-z0-9]|$)")
_RETRY_RE = re.compile(r"\br(?P<retry>\d+)\b|retry[-_:=\s]+(?P<retry2>\d+)", re.IGNORECASE)
_OUTPUT_ARG_RE = re.compile(r"(?:^|\s)-o\s+(?P<path>\S+)")
_DEFAULT_LIVENESS_TIMEOUT = 600
_MIN_STALE_SECONDS = 1_800


@dataclass(frozen=True)
class _Heartbeat:
    pid: int
    elapsed_seconds: float
    trace_path: str
    stdout_events: int
    stderr_lines: int
    output_bytes: int
    last_event: str
    last_item: str
    raw: str

    @property
    def stable_signature(self) -> tuple[Any, ...]:
        return (
            self.trace_path,
            self.stdout_events,
            self.stderr_lines,
            self.output_bytes,
            self.last_event,
            self.last_item,
        )


@dataclass(frozen=True)
class _ProcessProbe:
    pid: int
    parent_pid: int | None
    child_pids: list[int]
    cpu_percent: float | None
    mem_percent: float | None
    command: str


def detect_stale_codex_invocations(
    *,
    feature_id: str,
    bridge: BridgeProbe | None,
    events: list[EventRecord],
    current: CurrentWorkflowSnapshot | None,
) -> list[StaleCodexInvocation]:
    if bridge is None:
        return []
    heartbeats = [_parse_heartbeat(line) for line in bridge.log_lines]
    grouped: dict[tuple[int, str], list[_Heartbeat]] = {}
    for heartbeat in heartbeats:
        if heartbeat is None:
            continue
        grouped.setdefault((heartbeat.pid, heartbeat.trace_path), []).append(heartbeat)

    invocations: list[StaleCodexInvocation] = []
    for (_pid, _trace), records in grouped.items():
        candidate = _candidate_from_heartbeats(
            feature_id=feature_id,
            heartbeats=records,
            events=events,
            current=current,
        )
        if candidate is not None:
            invocations.append(candidate)
    return sorted(invocations, key=lambda item: item.elapsed_seconds, reverse=True)


def _candidate_from_heartbeats(
    *,
    feature_id: str,
    heartbeats: list[_Heartbeat],
    events: list[EventRecord],
    current: CurrentWorkflowSnapshot | None,
) -> StaleCodexInvocation | None:
    records = sorted(heartbeats, key=lambda item: item.elapsed_seconds)
    if len(records) < 2:
        return None
    latest = records[-1]
    stable_count = 1
    for previous in reversed(records[:-1]):
        if previous.stable_signature != latest.stable_signature:
            break
        stable_count += 1
    if stable_count < 2:
        return None
    if latest.last_event != "item.completed" or latest.last_item != "command_execution":
        return None

    actor = _actor_from_trace(latest.trace_path)
    start_event, done_after_start = _invocation_events_for_actor(actor, events)
    if done_after_start:
        return None
    liveness_timeout = _event_liveness_timeout(start_event)
    threshold = max(_MIN_STALE_SECONDS, 2 * liveness_timeout)
    if latest.elapsed_seconds < threshold:
        return None

    probe = _probe_process(latest.pid)
    if probe is None:
        return None
    if probe.cpu_percent is not None and probe.cpu_percent > 1.0:
        return None
    if not _process_is_feature_scoped(
        feature_id=feature_id,
        command=probe.command,
        trace_path=latest.trace_path,
    ):
        return None
    active_agents = set(current.active_agents if current is not None else [])
    if active_agents and actor and actor not in active_agents:
        return None

    invocation_id = None
    task_id = None
    if start_event is not None:
        metadata = start_event.metadata or {}
        invocation_id = str(metadata.get("invocation_id") or "") or None
        task_id = str(metadata.get("task_id") or "") or None
    output_path = _output_path_from_command(probe.command)
    idle_seconds = latest.elapsed_seconds
    token = _evidence_token(
        feature_id,
        actor,
        invocation_id,
        latest.pid,
        latest.trace_path,
        latest.stable_signature,
    )
    return StaleCodexInvocation(
        actor=actor,
        invocation_id=invocation_id,
        group_idx=_group_from_text(actor) or _event_group(start_event),
        retry=_retry_from_text(actor) or _event_retry(start_event),
        task_id=task_id,
        pid=latest.pid,
        parent_pid=probe.parent_pid,
        child_pids=probe.child_pids,
        cpu_percent=probe.cpu_percent,
        mem_percent=probe.mem_percent,
        command=probe.command,
        trace_path=latest.trace_path,
        output_path=output_path,
        elapsed_seconds=latest.elapsed_seconds,
        idle_seconds=idle_seconds,
        liveness_timeout_seconds=liveness_timeout,
        threshold_seconds=threshold,
        stdout_events=latest.stdout_events,
        stderr_lines=latest.stderr_lines,
        output_bytes=latest.output_bytes,
        last_event=latest.last_event,
        last_item=latest.last_item,
        heartbeat_count=len(records),
        stable_heartbeat_count=stable_count,
        last_activity_at=_trace_last_activity(latest.trace_path),
        evidence_token=token,
        citations=["dashboard:/api/bridge/logs", *( [start_event.citation] if start_event is not None else [] )],
    )


def _parse_heartbeat(line: str) -> _Heartbeat | None:
    match = _HEARTBEAT_RE.search(line)
    if not match:
        return None
    return _Heartbeat(
        pid=int(match.group("pid")),
        elapsed_seconds=float(match.group("elapsed")),
        trace_path=match.group("trace"),
        stdout_events=int(match.group("stdout_events")),
        stderr_lines=int(match.group("stderr_lines")),
        output_bytes=int(match.group("output_bytes")),
        last_event=match.group("last_event"),
        last_item=match.group("last_item"),
        raw=line,
    )


def _actor_from_trace(trace_path: str) -> str:
    name = Path(trace_path).name
    match = _TRACE_ACTOR_RE.match(name)
    if match:
        return match.group("actor")
    return name.removesuffix(".jsonl")


def _invocation_events_for_actor(
    actor: str,
    events: list[EventRecord],
) -> tuple[EventRecord | None, bool]:
    start: EventRecord | None = None
    done_after_start = False
    for event in sorted(events, key=lambda item: item.id or 0):
        event_actor = _event_actor(event)
        if event_actor != actor:
            continue
        if event.event_type == "agent_invocation_start":
            start = event
            done_after_start = False
        elif start is not None and event.event_type in {"agent_done", "agent_invocation_done"}:
            done_after_start = True
    return start, done_after_start


def _event_actor(event: EventRecord) -> str:
    metadata = event.metadata or {}
    for key in ("actor", "actor_name", "agent", "role", "runtime_actor"):
        value = metadata.get(key)
        if value:
            return str(value)
    text = f"{event.source} {event.content or ''}"
    match = re.search(r"\b(?P<actor>[a-z][\w-]*-g\d+[\w-]*)\b", text)
    return match.group("actor") if match else str(event.source or "")


def _event_liveness_timeout(event: EventRecord | None) -> int:
    if event is None:
        return _DEFAULT_LIVENESS_TIMEOUT
    value = (event.metadata or {}).get("liveness_timeout_seconds")
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_LIVENESS_TIMEOUT
    return timeout if timeout > 0 else _DEFAULT_LIVENESS_TIMEOUT


def _event_group(event: EventRecord | None) -> int | None:
    if event is None:
        return None
    for key in ("group_idx", "group", "group_id"):
        value = (event.metadata or {}).get(key)
        if value is None:
            continue
        try:
            return int(str(value).strip().removeprefix("g").removeprefix("G"))
        except ValueError:
            return None
    return None


def _event_retry(event: EventRecord | None) -> int | None:
    if event is None:
        return None
    value = (event.metadata or {}).get("retry")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _group_from_text(text: str) -> int | None:
    match = _GROUP_RE.search(text)
    return int(match.group("group")) if match else None


def _retry_from_text(text: str) -> int | None:
    match = _RETRY_RE.search(text)
    if not match:
        return None
    value = match.group("retry") or match.group("retry2")
    return int(value)


def _probe_process(pid: int) -> _ProcessProbe | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "pid=,ppid=,%cpu=,%mem=,command="],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    line = (result.stdout or "").strip()
    if result.returncode != 0 or not line:
        return None
    parts = line.split(None, 4)
    if len(parts) < 5:
        return None
    try:
        found_pid = int(parts[0])
        parent_pid = int(parts[1])
        cpu = float(parts[2])
        mem = float(parts[3])
    except ValueError:
        return None
    return _ProcessProbe(
        pid=found_pid,
        parent_pid=parent_pid,
        child_pids=_child_pids(found_pid),
        cpu_percent=cpu,
        mem_percent=mem,
        command=parts[4],
    )


def _child_pids(pid: int) -> list[int]:
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(pid)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2,
        )
    except Exception:
        return []
    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        try:
            pids.append(int(line.strip()))
        except ValueError:
            continue
    return sorted(pids)


def _feature_segment_matches(feature_id: str, segment: str) -> bool:
    raw_feature = feature_id.strip()
    raw_segment = segment.strip()
    return bool(
        raw_feature
        and raw_segment
        and (
            raw_segment == raw_feature
            or raw_segment == f"feature-{raw_feature}"
            or raw_segment.endswith(f"-{raw_feature}")
        )
    )


def process_is_feature_scoped(
    *,
    feature_id: str,
    command: str,
    trace_path: str,
) -> bool:
    raw_feature = feature_id.strip()
    if not raw_feature:
        return False
    for value in (command, trace_path):
        normalized = str(value or "").replace("\\", "/")
        parts = [part for part in normalized.split("/") if part]
        for idx, part in enumerate(parts[:-1]):
            if part == "features" and _feature_segment_matches(raw_feature, parts[idx + 1]):
                return True
    return False


def _process_is_feature_scoped(
    *,
    feature_id: str,
    command: str,
    trace_path: str,
) -> bool:
    return process_is_feature_scoped(
        feature_id=feature_id,
        command=command,
        trace_path=trace_path,
    )


def _output_path_from_command(command: str) -> str | None:
    match = _OUTPUT_ARG_RE.search(command)
    return match.group("path") if match else None


def _trace_last_activity(trace_path: str) -> datetime | None:
    try:
        stat = os.stat(trace_path)
    except OSError:
        return None
    return datetime.fromtimestamp(stat.st_mtime, timezone.utc)


def _evidence_token(
    feature_id: str,
    actor: str,
    invocation_id: str | None,
    pid: int,
    trace_path: str,
    signature: tuple[Any, ...],
) -> str:
    raw = jsonable_join((feature_id, actor, invocation_id or "", pid, trace_path, *signature))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def jsonable_join(values: tuple[Any, ...]) -> str:
    return "|".join(str(value) for value in values)
