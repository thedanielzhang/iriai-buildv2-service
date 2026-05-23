from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
from pydantic import BaseModel

from iriai_compose import ArtifactStore, Feature

from ..public_dashboard import PublicDashboardOutbox

_SPILL_MARKER = "__iriai_spill_v1__"
_SPILL_SQL_PREFIX = "'{\"__iriai_spill_v1__\"%'"
_DEFAULT_SPILL_BYTES = 1_000_000
_SPILL_ENV = "IRIAI_ARTIFACT_SPILL_MAX_BYTES"
_SPILL_DIR_ENV = "IRIAI_ARTIFACT_SPILL_DIR"
_MIRROR_TIMEOUT_ENV = "IRIAI_PUBLIC_DASHBOARD_MIRROR_TIMEOUT_SECONDS"
_DEFAULT_MIRROR_TIMEOUT_SECONDS = 0.75
_LIST_RECORDS_MAX_TOTAL_BYTES_ENV = "IRIAI_ARTIFACT_LIST_RECORDS_MAX_TOTAL_BYTES"
_DEFAULT_LIST_RECORDS_MAX_TOTAL_BYTES = 16 * 1024 * 1024
_LOSSLESS_CHECKPOINT_PREFIXES = (
    "dag-group:",
    "dag-task:",
    "dag-verify:",
    "dag-commit-failure:",
)
_LOSSLESS_CHECKPOINT_KEYS = {"dag"}
_SPILL_CANDIDATE_KEYS = {
    "bug-fix-attempts",
    "finding-ledger",
    "implementation",
    "handover",
}
_PREVIEW_PREFIXES = (
    "dag-verify:",
    "dag-verify-graph:",
    "dag-repair:",
    "dag-repair-preflight:",
    "dag-authority-gate:",
    "dag-direct-repair-route:",
    "dag-repair-expanded-verify:",
    "dag-repair-lens:",
    "dag-verify-rca:",
    "dag-repair-dispatch:",
    "dag-fix:",
    "dag-task-contract:",
    "dag-contract-verdict:",
    "dag-sandbox-patch:",
    "dag-task-reconcile:",
    "dag-task-spec-reconcile:",
    "dag-task-product-reconcile:",
    "dag-commit-failure:",
    "dag-group:",
    "dag-path-canonicalization:",
    "dag-worktree-alias-preflight:",
    "dag-worktree-alias-canonicalization:",
    "dag-workspace-acl-normalization:",
    "dag-workspace-permission-repair:",
    "dag-writeability-preflight:",
    "runtime-workspace-binding:",
    "dag-runtime-workspace-binding:",
    "dag-runtime-failure:",
    "workflow-blocker:",
    "workspace-authority-",
    "checkpoint:",
    "supervisor-thread-context:",
)
_PREVIEW_CHARS = 2_000


class ArtifactValueHydrationLimitExceeded(RuntimeError):
    """Raised when a broad full-value artifact read would hydrate too much data."""


class ArtifactSpillHydrationError(RuntimeError):
    """Raised when a spilled artifact cannot be hydrated losslessly."""


class PostgresArtifactStore(ArtifactStore):
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        public_dashboard: PublicDashboardOutbox | None = None,
    ) -> None:
        self._pool = pool
        self._public_dashboard = public_dashboard

    async def get(self, key: str, *, feature: Feature) -> Any | None:
        row = await self._pool.fetchrow(
            "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
            "ORDER BY id DESC LIMIT 1",
            feature.id,
            key,
        )
        if row is None:
            return None
        return self._deserialize_stored_value(row["value"])

    async def get_record(self, key: str, *, feature: Feature) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT id, created_at, value FROM artifacts "
            "WHERE feature_id = $1 AND key = $2 ORDER BY id DESC LIMIT 1",
            feature.id,
            key,
        )
        if row is None:
            return None
        value = self._deserialize_stored_value(row["value"])
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "value": value,
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        }

    async def list_records(
        self,
        *,
        feature_id: str,
        prefixes: tuple[str, ...] | list[str] = (),
        after_id: int = 0,
        limit: int = 500,
        order: str = "asc",
        max_total_value_bytes: int | None = None,
    ) -> list[dict[str, Any]]:
        direction = "DESC" if str(order).lower() == "desc" else "ASC"
        after_id = max(0, int(after_id or 0))
        limit = max(1, min(500, int(limit or 500)))
        args: list[Any] = [feature_id, after_id, limit]
        prefix_clause = ""
        if prefixes:
            prefix_clause = " AND (" + " OR ".join(
                f"key LIKE ${idx + 4}" for idx, _prefix in enumerate(prefixes)
            ) + ")"
            args.extend(f"{prefix}%" for prefix in prefixes)
        summary_rows = await self._pool.fetch(
            f"""
            SELECT id, key, created_at, pg_column_size(value)::bigint AS stored_bytes,
                   CASE WHEN value LIKE {_SPILL_SQL_PREFIX} THEN value ELSE NULL END AS content_ref
            FROM artifacts
            WHERE feature_id = $1 AND id > $2{prefix_clause}
            ORDER BY id {direction}
            LIMIT $3
            """,
            *args,
        )
        logical_bytes = 0
        for row in summary_rows:
            content_ref = _content_ref_from_stored_value(
                row["content_ref"],
                verify_hash=False,
            )
            logical_bytes += _summary_stored_bytes(row["stored_bytes"], content_ref)
        max_bytes = _list_records_max_total_bytes(max_total_value_bytes)
        if max_bytes is not None and logical_bytes > max_bytes:
            raise ArtifactValueHydrationLimitExceeded(
                "PostgresArtifactStore.list_records would hydrate "
                f"{logical_bytes} bytes across {len(summary_rows)} artifact rows "
                f"(cap {max_bytes}); use list_record_summaries() plus get_slice() "
                "or a narrower exact artifact read."
            )
        ids = [int(row["id"]) for row in summary_rows]
        if not ids:
            return []
        rows = await self._pool.fetch(
            f"""
            SELECT id, key, created_at, value
            FROM artifacts
            WHERE feature_id = $1 AND id = ANY($2::bigint[])
            ORDER BY id {direction}
            """,
            feature_id,
            ids,
        )
        records: list[dict[str, Any]] = []
        for row in rows:
            value = row["value"]
            full_value = self._deserialize_stored_value(value)
            records.append(
                {
                    "id": row["id"],
                    "key": row["key"],
                    "created_at": row["created_at"],
                    "value": full_value,
                    "sha256": hashlib.sha256(full_value.encode("utf-8")).hexdigest(),
                }
            )
        return records

    async def list_record_summaries(
        self,
        *,
        feature_id: str,
        prefixes: tuple[str, ...] | list[str] = (),
        after_id: int = 0,
        limit: int = 500,
        order: str = "asc",
    ) -> list[dict[str, Any]]:
        """List artifact row metadata without loading the large value column."""

        direction = "DESC" if str(order).lower() == "desc" else "ASC"
        after_id = max(0, int(after_id or 0))
        limit = max(1, min(500, int(limit or 500)))
        args: list[Any] = [feature_id, after_id, limit]
        prefix_clause = ""
        if prefixes:
            prefix_clause = " AND (" + " OR ".join(
                f"key LIKE ${idx + 4}" for idx, _prefix in enumerate(prefixes)
            ) + ")"
            args.extend(f"{prefix}%" for prefix in prefixes)
        rows = await self._pool.fetch(
            f"""
            SELECT id, key, created_at, pg_column_size(value)::bigint AS stored_bytes,
                   CASE WHEN value LIKE {_SPILL_SQL_PREFIX} THEN value ELSE NULL END AS content_ref,
                   {_value_preview_sql()}
            FROM artifacts
            WHERE feature_id = $1 AND id > $2{prefix_clause}
            ORDER BY id {direction}
            LIMIT $3
            """,
            *args,
        )
        records: list[dict[str, Any]] = []
        for row in rows:
            content_ref = _content_ref_from_stored_value(
                row["content_ref"],
                verify_hash=False,
            )
            records.append({
                "id": row["id"],
                "key": row["key"],
                "created_at": row["created_at"],
                "stored_bytes": _summary_stored_bytes(row["stored_bytes"], content_ref),
                "content_ref": content_ref,
                "value_preview": _summary_value_preview(row, content_ref),
                "value": "",
                "summary_only": True,
            })
        return records

    async def list_summaries(
        self,
        *,
        feature_id: str,
        prefixes: tuple[str, ...] | list[str] = (),
        after_id: int = 0,
        limit: int = 500,
        order: str = "asc",
    ) -> list[dict[str, Any]]:
        return await self.list_record_summaries(
            feature_id=feature_id,
            prefixes=prefixes,
            after_id=after_id,
            limit=limit,
            order=order,
        )

    async def latest_summary(self, key: str, *, feature: Feature) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            f"""
            SELECT id, key, created_at, pg_column_size(value)::bigint AS stored_bytes,
                   CASE WHEN value LIKE {_SPILL_SQL_PREFIX} THEN value ELSE NULL END AS content_ref,
                   {_value_preview_sql()}
            FROM artifacts
            WHERE feature_id = $1 AND key = $2
            ORDER BY id DESC
            LIMIT 1
            """,
            feature.id,
            key,
        )
        if row is None:
            return None
        content_ref = _content_ref_from_stored_value(
            row["content_ref"],
            verify_hash=False,
        )
        return {
            "id": row["id"],
            "key": row["key"],
            "created_at": row["created_at"],
            "stored_bytes": _summary_stored_bytes(row["stored_bytes"], content_ref),
            "content_ref": content_ref,
            "value_preview": _summary_value_preview(row, content_ref),
            "value": "",
            "summary_only": True,
        }

    async def get_slice(
        self,
        *,
        feature_id: str,
        artifact_id: int,
        start: int = 0,
        chars: int = 20_000,
    ) -> dict[str, Any] | None:
        start = max(0, int(start or 0))
        chars = max(1, min(120_000, int(chars or 20_000)))
        row = await self._pool.fetchrow(
            f"""
            SELECT id, key, created_at,
                   char_length(value)::bigint AS stored_chars,
                   pg_column_size(value)::bigint AS stored_bytes,
                   substring(value from $3 + 1 for $4) AS value_slice,
                   CASE WHEN value LIKE {_SPILL_SQL_PREFIX} THEN value ELSE NULL END AS content_ref
            FROM artifacts
            WHERE feature_id = $1 AND id = $2
            """,
            feature_id,
            int(artifact_id),
            start,
            chars,
        )
        if row is None:
            return None
        spill_marker = row["content_ref"] is not None
        content_ref = _content_ref_from_stored_value(row["content_ref"], verify_hash=False)
        if content_ref:
            total_chars = int(content_ref.get("chars") or content_ref.get("bytes") or 0)
            if not _spilled_ref_size_matches(content_ref):
                text = ""
                total_chars = 0
            elif start >= total_chars:
                text = ""
            else:
                text = _read_spilled_slice(content_ref, start=start, chars=chars)
        elif spill_marker:
            text = ""
            total_chars = 0
        else:
            text = row["value_slice"] or ""
            total_chars = int(row["stored_chars"] or len(text))
        return {
            "id": row["id"],
            "key": row["key"],
            "created_at": row["created_at"],
            "char_start": start,
            "char_end": min(start + len(text), total_chars),
            "total_chars": total_chars,
            "text": text,
            "stored_bytes": _summary_stored_bytes(row["stored_bytes"], content_ref),
            "content_ref": content_ref,
        }

    async def get_records_by_ids(
        self,
        feature_id: str,
        ids: list[int],
    ) -> list[dict[str, Any]]:
        if not ids:
            return []
        rows = await self._pool.fetch(
            """
            SELECT id, key, created_at, value
            FROM artifacts
            WHERE feature_id = $1 AND id = ANY($2::bigint[])
            ORDER BY id
            """,
            feature_id,
            ids,
        )
        records: list[dict[str, Any]] = []
        for row in rows:
            value = self._deserialize_stored_value(row["value"])
            records.append(
                {
                    "id": row["id"],
                    "key": row["key"],
                    "created_at": row["created_at"],
                    "value": value,
                    "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                    "summary_only": False,
                }
            )
        return records

    async def put(self, key: str, value: Any, *, feature: Feature) -> None:
        serialized = self._serialize(value)
        stored_value = self._storage_value(key, serialized, feature=feature)
        artifact_id = await self._pool.fetchval(
            "INSERT INTO artifacts (feature_id, key, value) VALUES ($1, $2, $3) RETURNING id",
            feature.id,
            key,
            stored_value,
        )
        if self._public_dashboard is not None:
            await _best_effort_mirror(
                self._public_dashboard.mirror_artifact_write(
                    source_artifact_id=artifact_id,
                    feature=feature,
                    key=key,
                    value=serialized,
                    visibility="internal",
                )
            )

    async def write_artifact_bytes(
        self,
        key: str,
        data: bytes,
        metadata: dict[str, Any] | None = None,
        *,
        feature: Feature | None = None,
    ) -> int:
        del metadata
        if feature is None:
            raise ValueError("feature is required when writing artifact bytes")
        value = data.decode("utf-8", "surrogateescape")
        stored_value = self._storage_value(key, value, feature=feature)
        artifact_id = await self._pool.fetchval(
            "INSERT INTO artifacts (feature_id, key, value) VALUES ($1, $2, $3) RETURNING id",
            feature.id,
            key,
            stored_value,
        )
        if self._public_dashboard is not None:
            await _best_effort_mirror(
                self._public_dashboard.mirror_artifact_write(
                    source_artifact_id=artifact_id,
                    feature=feature,
                    key=key,
                    value=value,
                    visibility="internal",
                )
            )
        return int(artifact_id)

    async def delete(self, key: str, *, feature: Feature) -> None:
        await self._pool.execute(
            "DELETE FROM artifacts WHERE feature_id = $1 AND key = $2",
            feature.id,
            key,
        )

    @staticmethod
    def _serialize(value: Any) -> str:
        if isinstance(value, BaseModel):
            return value.model_dump_json()
        if isinstance(value, str):
            return value
        return json.dumps(value)

    def _storage_value(self, key: str, serialized: str, *, feature: Feature) -> str:
        if _artifact_policy(key) != "lossless_spill":
            return serialized
        max_bytes = _spill_max_bytes()
        serialized_bytes = len(serialized.encode("utf-8"))
        if serialized_bytes <= max_bytes:
            return serialized
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        spill_root = _spill_dir()
        spill_dir = spill_root / feature.id
        spill_dir.mkdir(parents=True, exist_ok=True)
        relative_path = f"{feature.id}/{digest}.txt"
        path = spill_root / relative_path
        if path.exists():
            try:
                if _hash_file_sha256(path) != digest:
                    _atomic_write_text(path, serialized)
            except OSError:
                _atomic_write_text(path, serialized)
        else:
            _atomic_write_text(path, serialized)
        envelope = {
            _SPILL_MARKER: True,
            "feature_id": feature.id,
            "path": relative_path,
            "sha256": digest,
            "bytes": serialized_bytes,
            "chars": len(serialized),
            "content_type": _guess_content_type(key, serialized),
            "policy": "lossless_spill",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(envelope, sort_keys=True)

    def _deserialize_stored_value(self, stored: str) -> str:
        ref = _content_ref_from_stored_value(stored, verify_hash=True)
        if not ref:
            if _raw_spill_envelope(stored) is not None:
                raise ArtifactSpillHydrationError(
                    "Spilled artifact content is missing, invalid, or hash-mismatched"
                )
            return stored
        path = _validated_spill_path(ref)
        if path is None:
            raise ArtifactSpillHydrationError(
                "Spilled artifact content path is invalid"
            )
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            raise ArtifactSpillHydrationError(
                "Spilled artifact content could not be read"
            )
        return text


def _artifact_policy(key: str) -> str:
    if key in _LOSSLESS_CHECKPOINT_KEYS or key.startswith(_LOSSLESS_CHECKPOINT_PREFIXES):
        return "lossless_checkpoint"
    if key in _SPILL_CANDIDATE_KEYS:
        return "lossless_spill"
    return "lossless_spill"


def _spill_max_bytes() -> int:
    raw = os.environ.get(_SPILL_ENV, "")
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = _DEFAULT_SPILL_BYTES
    return max(100_000, parsed)


def _list_records_max_total_bytes(override: int | None = None) -> int | None:
    if override is not None:
        parsed = int(override)
    else:
        raw = os.environ.get(_LIST_RECORDS_MAX_TOTAL_BYTES_ENV, "")
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = _DEFAULT_LIST_RECORDS_MAX_TOTAL_BYTES
    if parsed <= 0:
        return None
    return max(1_000_000, parsed)


def _spill_dir() -> Path:
    return Path(os.environ.get(_SPILL_DIR_ENV, Path.home() / ".iriai" / "artifact-spill"))


async def _best_effort_mirror(awaitable: Any) -> None:
    try:
        await asyncio.wait_for(awaitable, timeout=_mirror_timeout_seconds())
    except Exception:
        return


def _mirror_timeout_seconds() -> float:
    raw = os.environ.get(_MIRROR_TIMEOUT_ENV, "")
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        parsed = _DEFAULT_MIRROR_TIMEOUT_SECONDS
    return max(0.05, parsed)


def _content_ref_from_stored_value(
    stored: Any,
    *,
    verify_hash: bool = True,
) -> dict[str, Any] | None:
    payload = _raw_spill_envelope(stored)
    if payload is None:
        return None
    if not isinstance(payload, dict) or not payload.get(_SPILL_MARKER):
        return None
    if not _valid_spill_ref(payload, verify_hash=verify_hash):
        return None
    if not verify_hash:
        payload = dict(payload)
        payload["hash_verified"] = False
    return payload


def _raw_spill_envelope(stored: Any) -> dict[str, Any] | None:
    if not isinstance(stored, str) or _SPILL_MARKER not in stored[:128]:
        return None
    try:
        payload = json.loads(stored)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and payload.get(_SPILL_MARKER):
        return payload
    return None


def _read_spilled_slice(ref: dict[str, Any], *, start: int, chars: int) -> str:
    path = _validated_spill_path(ref)
    if path is None:
        return ""
    target_start = max(0, int(start or 0))
    target_chars = max(1, int(chars or 1))
    total_chars = max(0, int(ref.get("chars") or 0))
    if total_chars and target_start >= total_chars:
        return ""
    target_end = min(target_start + target_chars, total_chars) if total_chars else target_start + target_chars
    try:
        if not _spilled_ref_size_matches(ref):
            return ""
        if int(ref.get("bytes") or -1) == total_chars:
            with path.open("rb") as handle:
                handle.seek(target_start)
                return handle.read(max(0, target_end - target_start)).decode("utf-8")
        return _read_text_char_slice(path, target_start, target_end)
    except (OSError, UnicodeDecodeError):
        return ""


def _spilled_ref_size_matches(ref: dict[str, Any]) -> bool:
    path = _validated_spill_path(ref)
    if path is None:
        return False
    try:
        expected_bytes = int(ref.get("bytes") or -1)
    except (TypeError, ValueError):
        return False
    if expected_bytes < 0:
        return False
    try:
        return path.stat().st_size == expected_bytes
    except OSError:
        return False


def _valid_spill_ref(ref: dict[str, Any], *, verify_hash: bool = True) -> bool:
    if not ref.get("feature_id") or not isinstance(ref.get("feature_id"), str):
        return False
    if not ref.get("sha256"):
        return False
    try:
        int(ref.get("bytes"))
        int(ref.get("chars"))
    except (TypeError, ValueError):
        return False
    if not ref.get("content_type") or not isinstance(ref.get("content_type"), str):
        return False
    if not ref.get("created_at") or not isinstance(ref.get("created_at"), str):
        return False
    path = _validated_spill_path(ref)
    if path is None or not path.exists():
        return False
    if not verify_hash:
        return True
    try:
        return _hash_file_sha256(path) == ref.get("sha256")
    except OSError:
        return False


def _validated_spill_path(ref: dict[str, Any]) -> Path | None:
    raw_path = str(ref.get("path") or "")
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    root = _spill_dir().resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _read_text_char_slice(path: Path, start: int, end: int) -> str:
    import codecs

    decoder = codecs.getincrementaldecoder("utf-8")()
    seen = 0
    out: list[str] = []
    with path.open("rb") as handle:
        while seen < end:
            chunk = handle.read(64 * 1024)
            if not chunk:
                text = decoder.decode(b"", final=True)
                if text:
                    next_seen = seen + len(text)
                    if next_seen > start:
                        out.append(text[max(0, start - seen) : max(0, end - seen)])
                    seen = next_seen
                break
            text = decoder.decode(chunk)
            if not text:
                continue
            next_seen = seen + len(text)
            if next_seen > start:
                out.append(text[max(0, start - seen) : max(0, end - seen)])
            seen = next_seen
    return "".join(out)[: max(0, end - start)]


def _hash_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)


def _value_preview_sql() -> str:
    clauses = " OR ".join(f"key LIKE '{prefix}%'" for prefix in _PREVIEW_PREFIXES)
    return (
        f"CASE WHEN value LIKE {_SPILL_SQL_PREFIX} THEN NULL "
        "WHEN (" + clauses + f") THEN substring(value from 1 for {_PREVIEW_CHARS}) "
        "ELSE NULL END AS value_preview"
    )


def _summary_value_preview(row: Any, content_ref: dict[str, Any] | None) -> Any:
    if content_ref is not None:
        key = str(_row_get(row, "key") or "")
        if key.startswith(_PREVIEW_PREFIXES):
            preview = _read_spilled_slice(content_ref, start=0, chars=_PREVIEW_CHARS)
            return preview or None
        return None
    preview = _row_get(row, "value_preview")
    if isinstance(preview, str) and _SPILL_MARKER in preview[:128]:
        return None
    return preview


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _summary_stored_bytes(stored_bytes: Any, content_ref: dict[str, Any] | None) -> int:
    if content_ref is not None:
        try:
            return int(content_ref.get("bytes") or stored_bytes or 0)
        except (TypeError, ValueError):
            return 0
    try:
        return int(stored_bytes or 0)
    except (TypeError, ValueError):
        return 0


def _guess_content_type(key: str, value: str) -> str:
    key_lower = key.lower()
    stripped = value.lstrip()
    if key_lower.endswith(".json") or stripped.startswith(("{", "[")):
        return "application/json"
    if key_lower.endswith(".html") or stripped.startswith("<!doctype html") or stripped.startswith("<html"):
        return "text/html"
    if key_lower.endswith(".md") or stripped.startswith("#"):
        return "text/markdown"
    return "text/plain"
