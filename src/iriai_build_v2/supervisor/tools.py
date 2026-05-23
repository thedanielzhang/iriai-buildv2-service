from __future__ import annotations

import json
import re
import hashlib
from pathlib import Path
from typing import Any

from .evidence import DashboardClient, probe_bridge, probe_worktree
from .models import (
    ArtifactEvidenceChunk,
    ArtifactEvidenceSummary,
    ArtifactRecord,
    EventRecord,
    SupervisorEvidenceBundle,
    SupervisorInvestigationRequest,
)

_MUTATING_SQL_RE = re.compile(
    r"\b("
    r"alter|analyze|call|commit|copy|create|delete|drop|execute|grant|insert|"
    r"lock|merge|refresh|reset|revoke|rollback|set|truncate|update|vacuum"
    r")\b",
    re.IGNORECASE,
)
_PATH_RE = re.compile(r"(?P<path>(?:[\w.-]+/)+[\w.@+-]+(?:\.[\w+-]+)?)")
_ARTIFACT_CHUNK_CHARS = 20_000
_SUMMARY_TEXT_CHARS = 700
_SUMMARY_LIST_ITEMS = 8
_MAX_SQL_ROWS = 200
_MAX_SQL_RESPONSE_CHARS = 100_000
_SQL_TIMEOUT_SECONDS = 3.0


class SupervisorEvidenceToolbox:
    """Read-only evidence surface used by the agent investigator."""

    def __init__(
        self,
        *,
        feature_id: str,
        feature_store: Any,
        artifact_store: Any,
        dashboard_url: str | None = None,
        dashboard_client: DashboardClient | None = None,
        worktree_roots: list[str | Path] | None = None,
        forbidden_paths: list[str] | None = None,
    ) -> None:
        self.feature_id = feature_id
        self.feature_store = feature_store
        self.artifact_store = artifact_store
        self.dashboard_url = dashboard_url
        self.dashboard_client = dashboard_client
        self.worktree_roots = [Path(root) for root in (worktree_roots or [])]
        self.forbidden_paths = forbidden_paths or []

    async def gather(
        self,
        request: SupervisorInvestigationRequest,
    ) -> SupervisorEvidenceBundle:
        bundle = SupervisorEvidenceBundle(request=request)
        feature = await self.feature_store.get_feature(self.feature_id)
        try:
            artifacts, summaries, chunks = await self._artifact_evidence(request, feature)
            bundle.artifacts.extend(artifacts)
            bundle.artifact_summaries.extend(summaries)
            bundle.artifact_chunks.extend(chunks)
            bundle.events.extend(await self._event_rows(request))
            if request.include_bridge and (self.dashboard_url or self.dashboard_client):
                bundle.bridge = await probe_bridge(
                    dashboard_url=self.dashboard_url,
                    client=self.dashboard_client,
                    after=0,
                )
            if request.include_worktrees:
                bundle.worktrees = [
                    probe_worktree(root, forbidden_paths=self.forbidden_paths)
                    for root in self.worktree_roots
                ]
            sql_results, rejected = await self._sql_results(request.sql)
            bundle.sql_results.extend(sql_results)
            bundle.rejected_sql.extend(rejected)
        except Exception as exc:
            bundle.errors.append(f"{type(exc).__name__}: {exc}")
        return bundle

    async def gather_many(
        self,
        requests: list[SupervisorInvestigationRequest],
    ) -> list[SupervisorEvidenceBundle]:
        return [await self.gather(request) for request in requests]

    async def _artifact_evidence(
        self,
        request: SupervisorInvestigationRequest,
        feature: Any | None,
    ) -> tuple[list[ArtifactRecord], list[ArtifactEvidenceSummary], list[ArtifactEvidenceChunk]]:
        detail_records: list[ArtifactRecord] = []
        summary_records: list[ArtifactRecord] = []
        if feature is not None and hasattr(self.artifact_store, "latest_summary"):
            for key in request.artifact_keys[:20]:
                row = await self.artifact_store.latest_summary(key, feature=feature)
                if row is not None:
                    summary_records.append(_artifact_record({**row, "key": key}))
        elif feature is not None and hasattr(self.artifact_store, "get_record"):
            for key in request.artifact_keys[:20]:
                row = await self.artifact_store.get_record(key, feature=feature)
                if row is not None:
                    detail_records.append(_artifact_record({**row, "key": key}))
        if request.artifact_prefixes and (
            hasattr(self.artifact_store, "list_record_summaries")
            or hasattr(self.artifact_store, "list_records")
        ):
            after_id = request.artifact_after_id
            if after_id is None:
                after_id = (
                    max(0, int(request.artifact_ids[0]) - 1)
                    if request.artifact_ids
                    else 0
                )
            rows = await self._list_artifact_summaries(
                prefixes=tuple(request.artifact_prefixes[:20]),
                after_id=after_id,
                limit=200,
                order="desc",
            )
            summary_records.extend(_artifact_record(row) for row in rows)
        detail_records.extend(await self._artifact_rows_by_id(request.artifact_ids[:50]))
        chunks = await self._artifact_chunks(request.artifact_chunks[:20])
        all_records = _dedupe_artifacts([*summary_records, *detail_records])
        return (
            _dedupe_artifacts(detail_records),
            _dedupe_summaries([artifact_summary(record) for record in all_records]),
            chunks,
        )

    async def _list_artifact_summaries(
        self,
        *,
        prefixes: tuple[str, ...],
        after_id: int,
        limit: int,
        order: str,
    ) -> list[dict[str, Any]]:
        if hasattr(self.artifact_store, "list_record_summaries"):
            try:
                return await self.artifact_store.list_record_summaries(
                    feature_id=self.feature_id,
                    prefixes=prefixes,
                    after_id=after_id,
                    limit=limit,
                    order=order,
                )
            except TypeError:
                return await self.artifact_store.list_record_summaries(
                    feature_id=self.feature_id,
                    prefixes=prefixes,
                    after_id=after_id,
                    limit=limit,
                )
        pool = getattr(self.artifact_store, "_pool", None)
        if pool is None:
            return []
        direction = "DESC" if str(order).lower() == "desc" else "ASC"
        args: list[Any] = [self.feature_id, after_id, limit]
        prefix_clause = ""
        if prefixes:
            prefix_clause = " AND (" + " OR ".join(
                f"key LIKE ${idx + 4}" for idx, _prefix in enumerate(prefixes)
            ) + ")"
            args.extend(f"{prefix}%" for prefix in prefixes)
        rows = await pool.fetch(
            f"""
            SELECT id, key, created_at,
                   pg_column_size(value)::bigint AS stored_bytes,
                   substring(value from 1 for 2000) AS value_preview,
                   TRUE AS summary_only
            FROM artifacts
            WHERE feature_id = $1 AND id > $2{prefix_clause}
            ORDER BY id {direction}
            LIMIT $3
            """,
            *args,
        )
        return [dict(row) for row in rows]

    async def _artifact_rows_by_id(self, ids: list[int]) -> list[ArtifactRecord]:
        if not ids:
            return []
        if hasattr(self.artifact_store, "get_slice"):
            records: list[ArtifactRecord] = []
            for artifact_id in ids[:50]:
                row = await self.artifact_store.get_slice(
                    feature_id=self.feature_id,
                    artifact_id=int(artifact_id),
                    start=0,
                    chars=_ARTIFACT_CHUNK_CHARS,
                )
                if row is not None:
                    records.append(_artifact_record(_slice_record(row)))
            return records
        pool = getattr(self.artifact_store, "_pool", None)
        if pool is None:
            return []
        rows = await pool.fetch(
            """
            SELECT id, key, created_at,
                   substring(value from 1 for $3) AS value,
                   pg_column_size(value)::bigint AS stored_bytes
            FROM artifacts
            WHERE feature_id = $1 AND id = ANY($2::bigint[])
            ORDER BY id
            """,
            self.feature_id,
            ids,
            _ARTIFACT_CHUNK_CHARS,
        )
        return [_artifact_record(dict(row)) for row in rows]

    async def _artifact_chunks(self, refs: list[str]) -> list[ArtifactEvidenceChunk]:
        parsed: list[tuple[int, int]] = []
        for ref in refs:
            match = re.fullmatch(r"\s*(\d+):(\d+)\s*", str(ref))
            if not match:
                continue
            parsed.append((int(match.group(1)), int(match.group(2))))
        if not parsed:
            return []
        chunks: list[ArtifactEvidenceChunk] = []
        for artifact_id, chunk_index in parsed:
            start = max(0, chunk_index) * _ARTIFACT_CHUNK_CHARS
            if hasattr(self.artifact_store, "get_slice"):
                row = await self.artifact_store.get_slice(
                    feature_id=self.feature_id,
                    artifact_id=artifact_id,
                    start=start,
                    chars=_ARTIFACT_CHUNK_CHARS,
                )
            else:
                row = await self._artifact_slice_from_pool(
                    artifact_id=artifact_id,
                    start=start,
                    chars=_ARTIFACT_CHUNK_CHARS,
                )
            if row is None:
                continue
            text = str(row.get("text") or "")
            total_chars = int(row.get("total_chars") or len(text))
            end = int(row.get("char_end") or min(total_chars, start + len(text)))
            if start >= total_chars:
                continue
            chunks.append(
                ArtifactEvidenceChunk(
                    artifact_id=artifact_id,
                    key=str(row.get("key") or ""),
                    citation=f"artifact:{row.get('key')} id={artifact_id}",
                    chunk_ref=f"{artifact_id}:{chunk_index}",
                    chunk_index=chunk_index,
                    char_start=start,
                    char_end=end,
                    total_chars=total_chars,
                    text=text,
                )
            )
        return chunks

    async def _event_rows(self, request: SupervisorInvestigationRequest) -> list[EventRecord]:
        after_id = request.event_after_id
        if after_id is None:
            return []
        if hasattr(self.feature_store, "list_event_summaries"):
            rows = await self.feature_store.list_event_summaries(
                self.feature_id,
                after_id=after_id,
                limit=max(1, request.event_limit),
                order="asc",
            )
            return [_event_record(row) for row in rows]
        return []

    async def _artifact_slice_from_pool(
        self,
        *,
        artifact_id: int,
        start: int,
        chars: int,
    ) -> dict[str, Any] | None:
        pool = getattr(self.artifact_store, "_pool", None)
        if pool is None:
            return None
        sql = """
        SELECT id, key, created_at,
               substring(value from $3 + 1 for $4) AS text,
               char_length(value)::bigint AS total_chars,
               LEAST(char_length(value), $3 + char_length(substring(value from $3 + 1 for $4)))::bigint AS char_end,
               pg_column_size(value)::bigint AS stored_bytes
        FROM artifacts
        WHERE feature_id = $1 AND id = $2
        """
        args = (
            self.feature_id,
            int(artifact_id),
            max(0, int(start or 0)),
            max(1, int(chars or _ARTIFACT_CHUNK_CHARS)),
        )
        if hasattr(pool, "fetchrow"):
            row = await pool.fetchrow(sql, *args)
        else:
            rows = await pool.fetch(sql, *args)
            row = rows[0] if rows else None
        if row is None:
            return None
        payload = dict(row)
        if "text" not in payload and "value" in payload:
            text = _artifact_text(_maybe_json(payload.get("value")))
            payload["text"] = text[start : start + chars]
            payload["total_chars"] = len(text)
            payload["char_end"] = min(len(text), start + len(payload["text"]))
        return payload

    async def _sql_results(
        self,
        statements: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        results: list[dict[str, Any]] = []
        rejected = [
            {
                "sql": sql,
                "reason": "operator SQL is disabled; use named supervisor evidence APIs",
            }
            for sql in statements
        ]
        return results, rejected


def validate_read_only_sql(sql: str) -> str:
    stripped = sql.strip().rstrip(";").strip()
    lowered = stripped.lower()
    if not stripped:
        return "empty SQL"
    if ";" in stripped:
        return "multiple SQL statements are not allowed"
    if not (lowered.startswith("select ") or lowered.startswith("with ")):
        return "only SELECT/WITH statements are allowed"
    if _MUTATING_SQL_RE.search(lowered):
        return "mutating SQL keywords are not allowed"
    select_clause = _select_clause(stripped)
    wildcard_clause = re.sub(r"\bcount\s*\(\s*\*\s*\)", "count()", select_clause, flags=re.I)
    if "*" in wildcard_clause or re.search(r"\b[a-z_][\w.]*\.\*", select_clause, re.I):
        return "wildcard selection is not allowed; use named summary APIs"
    if "from artifacts" in lowered and re.search(r"\bselect\b.*\bvalue\b", lowered, re.DOTALL) and not re.search(
        r"\b(substring|left|pg_column_size|char_length|octet_length)\s*\(\s*value\b",
        lowered,
    ):
        return "raw artifact value selection is not allowed; use artifact slice APIs"
    if "from events" in lowered and re.search(r"\bselect\b.*\bcontent\b", lowered, re.DOTALL) and not re.search(
        r"\b(substring|left|pg_column_size|char_length|octet_length)\s*\(\s*content\b",
        lowered,
    ):
        return "raw event content selection is not allowed; use event summary APIs"
    if not re.search(r"\blimit\b", lowered) and not re.search(r"\bcount\s*\(", lowered):
        return "read-only SQL must include LIMIT or COUNT"
    limit_match = re.search(r"\blimit\s+(\d+)\b", lowered)
    if limit_match and int(limit_match.group(1)) > _MAX_SQL_ROWS:
        return f"read-only SQL LIMIT must be <= {_MAX_SQL_ROWS}"
    return ""


def _select_clause(sql: str) -> str:
    match = re.search(r"\bselect\b(?P<select>.*?)\bfrom\b", sql, re.I | re.S)
    if match:
        return match.group("select")
    return sql


def _slice_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "key": row.get("key"),
        "created_at": row.get("created_at"),
        "value": row.get("text", ""),
        "stored_bytes": row.get("stored_bytes"),
        "value_preview": row.get("text", ""),
        "summary_only": False,
    }


def _bounded_row(row: dict[str, Any]) -> dict[str, Any]:
    remaining = _MAX_SQL_RESPONSE_CHARS
    bounded: dict[str, Any] = {}
    for key, value in row.items():
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        if len(text) > remaining:
            bounded[key] = f"{text[: max(0, remaining)]}... [truncated]"
            bounded["_truncated"] = True
            break
        bounded[key] = value
        remaining -= len(text)
    return bounded


def _artifact_record(row: dict[str, Any]) -> ArtifactRecord:
    value = row.get("value", row.get("value_preview", ""))
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
    return ArtifactRecord(
        id=row.get("id"),
        key=str(row.get("key") or ""),
        value=value,
        created_at=row.get("created_at"),
        sha256=row.get("sha256")
        or (
            None
            if row.get("summary_only", False)
            else hashlib.sha256(_artifact_text(value).encode("utf-8")).hexdigest()
        ),
        stored_bytes=row.get("stored_bytes"),
        value_preview=row.get("value_preview"),
        summary_only=bool(row.get("summary_only", False)),
    )


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


def _dedupe_artifacts(records: list[ArtifactRecord]) -> list[ArtifactRecord]:
    by_id_key: dict[tuple[int | None, str], ArtifactRecord] = {}
    for record in records:
        by_id_key[(record.id, record.key)] = record
    return sorted(by_id_key.values(), key=lambda record: record.id or 0)


def _dedupe_summaries(records: list[ArtifactEvidenceSummary]) -> list[ArtifactEvidenceSummary]:
    by_id_key: dict[tuple[int | None, str], ArtifactEvidenceSummary] = {}
    for record in records:
        by_id_key[(record.id, record.key)] = record
    return sorted(by_id_key.values(), key=lambda record: record.id or 0)


def artifact_summary(record: ArtifactRecord) -> ArtifactEvidenceSummary:
    value = record.value
    if record.summary_only and record.value_preview and value in ("", None):
        value = record.value_preview
    text = _artifact_text(value)
    parsed = value if isinstance(value, dict) else _maybe_json(value)
    status = approved = route = reason = None
    summary = ""
    concerns: list[str] = []
    gaps: list[str] = []
    if isinstance(parsed, dict):
        status = _stringish(parsed.get("status") or parsed.get("verdict") or parsed.get("classification"))
        approved_value = parsed.get("approved")
        approved = approved_value if isinstance(approved_value, bool) else None
        route = _stringish(parsed.get("route") or parsed.get("repair_route"))
        reason = _stringish(parsed.get("reason") or parsed.get("fallback_reason"))
        summary = _first_text(
            parsed,
            (
                "summary",
                "message",
                "error",
                "reason",
                "inference",
                "status_summary",
                "result",
            ),
        )
        concerns = _list_snippets(parsed, ("concerns", "issues", "findings", "blockers", "errors"))
        gaps = _list_snippets(parsed, ("gaps", "missing", "skipped", "path_problems"))
    elif isinstance(parsed, list):
        summary = _shorten(" ".join(_shorten(item, 120) for item in parsed[:3]), _SUMMARY_TEXT_CHARS)
    else:
        summary = _shorten(text, _SUMMARY_TEXT_CHARS)
    path_snippets = _extract_paths(text)
    chunk_count = max(1, (len(text) + _ARTIFACT_CHUNK_CHARS - 1) // _ARTIFACT_CHUNK_CHARS)
    chunk_refs = [f"{record.id}:{idx}" for idx in range(chunk_count) if record.id is not None]
    return ArtifactEvidenceSummary(
        id=record.id,
        key=record.key,
        citation=record.citation,
        created_at=record.created_at,
        size_chars=record.stored_bytes or len(text),
        sha256=record.sha256
        or (
            None
            if record.summary_only
            else hashlib.sha256(text.encode("utf-8")).hexdigest()
        ),
        status=status,
        approved=approved,
        route=route,
        reason=reason,
        summary=summary,
        concerns=concerns,
        gaps=gaps,
        path_snippets=path_snippets,
        chunk_refs=chunk_refs[:20],
        detail_available=record.id is not None,
    )


def _artifact_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def _maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _stringish(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return None


def _first_text(value: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        item = value.get(key)
        if item:
            return _shorten(item, _SUMMARY_TEXT_CHARS)
    return ""


def _list_snippets(value: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for key in keys:
        item = value.get(key)
        if item is None:
            continue
        if isinstance(item, list):
            result.extend(_shorten(entry, 260) for entry in item[:_SUMMARY_LIST_ITEMS])
        elif isinstance(item, dict):
            result.extend(
                f"{_shorten(k, 80)}={_shorten(v, 180)}"
                for k, v in list(item.items())[:_SUMMARY_LIST_ITEMS]
            )
        else:
            result.append(_shorten(item, 260))
        if len(result) >= _SUMMARY_LIST_ITEMS:
            break
    return result[:_SUMMARY_LIST_ITEMS]


def _extract_paths(text: str) -> list[str]:
    if "/" not in text:
        return []
    if len(text) > 80_000:
        windows: list[str] = []
        start = 0
        while len(windows) < 80:
            slash = text.find("/", start)
            if slash < 0:
                break
            windows.append(text[max(0, slash - 180) : min(len(text), slash + 320)])
            start = slash + 1
        text = "\n".join(windows)
    paths: list[str] = []
    seen: set[str] = set()
    for match in _PATH_RE.finditer(text):
        path = match.group("path").strip("`'\".,)")
        if path in seen or path.startswith(("http://", "https://")):
            continue
        seen.add(path)
        paths.append(path)
        if len(paths) >= 20:
            break
    return paths


def _shorten(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 24)]}... [truncated]"
