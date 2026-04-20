"""Document hosting service for artifact review sessions.

With ``iriai-feedback serve``, the hosting service is a thin URL manager:
write artifacts to disk, construct deterministic URLs, read co-located
feedback.  The single ``iriai-feedback serve`` process handles rendering,
overlay injection, and annotation storage.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from ..models.outputs import (
    DecisionLedger,
    DesignDecisions,
    ImplementationDAG,
    PRD,
    ScopeOutput,
    SubfeatureDecomposition,
    SystemDesign,
    TechnicalPlan,
    TestPlan,
)
from .artifacts import _key_to_path, _path_to_key
from .markdown import to_markdown
from .system_design_html import render_system_design_html

if TYPE_CHECKING:
    from .artifacts import ArtifactMirror
    from .tunnel import CloudflareTunnel

logger = logging.getLogger(__name__)

_KEY_TO_MODEL: dict[str, type[BaseModel]] = {
    "decisions": DecisionLedger,
    "prd": PRD,
    "design": DesignDecisions,
    "plan": TechnicalPlan,
    "scope": ScopeOutput,
    "decomposition": SubfeatureDecomposition,
    "test-plan": TestPlan,
}

_ARTIFACT_MODELS: list[type[BaseModel]] = [
    PRD,
    DesignDecisions,
    TechnicalPlan,
    SubfeatureDecomposition,
    DecisionLedger,
    ImplementationDAG,
    TestPlan,
]

_SERVE_PORT = 9000


class DocHostingService:
    """Manages artifact hosting. Writes files; iriai-feedback serve handles the rest."""

    def __init__(
        self,
        mirror: ArtifactMirror,
        feedback: Any = None,
        *,
        tunnel: CloudflareTunnel | None = None,
    ) -> None:
        self._mirror = mirror
        self._feedback = feedback  # kept for backward compat, unused in serve mode
        self._tunnel = tunnel
        self._urls: dict[str, str] = {}
        self._labels: dict[str, str] = {}
        self._current_feature_id: str | None = None

    def _artifact_url(self, feature_id: str, key: str) -> str:
        """Build the review URL for an artifact."""
        if self._tunnel:
            return self._tunnel.artifact_url(feature_id, key)
        return f"http://localhost:{_SERVE_PORT}/features/{feature_id}/{key}"

    async def push(self, feature_id: str, key: str, content: str, label: str) -> str:
        """Write artifact to disk and return its review URL."""
        display_content = self._to_display_content(content, key)
        self._mirror.write_artifact(feature_id, key, display_content)
        self._current_feature_id = feature_id

        url = self._artifact_url(feature_id, key)
        self._urls[key] = url
        self._labels[key] = label
        await self._notify_refresh(feature_id, key)
        logger.info("Hosted %s at %s", key, url)
        return url

    async def push_qa(self, feature_id: str, key: str, content: str, label: str) -> str:
        """Write artifact to disk and return its review URL.

        With iriai-feedback serve, HTML artifacts get the same overlay treatment
        as markdown — no separate QA session needed.
        """
        self._mirror.write_artifact(feature_id, key, content)
        self._current_feature_id = feature_id

        url = self._artifact_url(feature_id, key)
        self._urls[key] = url
        self._labels[key] = label
        await self._notify_refresh(feature_id, key)
        logger.info("QA hosted %s at %s", key, url)
        return url

    async def update(self, feature_id: str, key: str, content: str) -> None:
        """Re-write artifact file and notify the serve process to refresh browsers."""
        display_content = self._to_display_content(content, key)
        self._mirror.write_artifact(feature_id, key, display_content)
        await self._notify_refresh(feature_id, key)
        logger.info("Updated %s (%d bytes)", key, len(display_content))

    async def delete(self, feature_id: str, key: str) -> None:
        """Remove a hosted artifact from the mirror and URL cache."""
        delete = getattr(self._mirror, "delete_artifact", None)
        if callable(delete):
            delete(feature_id, key)
        self._urls.pop(key, None)
        self._labels.pop(key, None)
        await self._notify_refresh(feature_id, key)
        logger.info("Deleted hosted artifact %s for %s", key, feature_id)

    def get_url(self, key: str) -> str | None:
        return self._urls.get(key)

    async def try_collect(self, key: str) -> list[dict[str, Any]]:
        """Read annotations from co-located .feedback/ directory.

        For namespaced keys (e.g. ``prd:canvas``), the feedback directory
        lives relative to the artifact's parent directory:
        ``subfeatures/canvas/.feedback/prd/annotations/``.
        """
        if not self._current_feature_id:
            return []

        rel_path = Path(_key_to_path(key))
        artifact_dir = self._mirror.feature_dir(self._current_feature_id) / rel_path.parent
        feedback_key = rel_path.stem

        feedback_dir = artifact_dir / ".feedback" / feedback_key / "annotations"

        if not feedback_dir.is_dir():
            return []

        annotations: list[dict[str, Any]] = []
        for f in sorted(feedback_dir.iterdir()):
            if f.suffix != ".json":
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if not data.get("deleted"):
                    annotations.append(data)
            except (json.JSONDecodeError, OSError):
                continue

        logger.info("try_collect(%r): %d annotations", key, len(annotations))
        return annotations

    async def clear_feedback(self, key: str) -> None:
        """Delete all annotation files and reset the session for an artifact.

        Called after collecting annotations so they don't carry over to the
        next gate iteration.  Uses ``_key_to_path`` so namespaced keys resolve
        to the correct nested ``.feedback/`` directory.
        """
        if not self._current_feature_id:
            return

        rel_path = Path(_key_to_path(key))
        artifact_dir = self._mirror.feature_dir(self._current_feature_id) / rel_path.parent
        feedback_key = rel_path.stem

        fb_dir = artifact_dir / ".feedback" / feedback_key
        if not fb_dir.is_dir():
            return

        # Remove annotation files
        ann_dir = fb_dir / "annotations"
        if ann_dir.is_dir():
            for f in ann_dir.iterdir():
                if f.suffix == ".json":
                    f.unlink(missing_ok=True)

        # Reset session status so the overlay allows new annotations
        session_file = fb_dir / "session.json"
        if session_file.exists():
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                data["status"] = "active"
                data["submitted_at"] = None
                session_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except (json.JSONDecodeError, OSError):
                pass

        logger.info("Cleared feedback for %s", key)

    async def mark_feedback_submitted(self, feature_id: str, key: str) -> None:
        """Mark a hosted review session as submitted/closed.

        This keeps the browser overlay state aligned with workflow state when
        a gate review is approved without requiring another human submission.
        """
        rel_path = Path(_key_to_path(key))
        artifact_dir = self._mirror.feature_dir(feature_id) / rel_path.parent
        feedback_key = rel_path.stem

        session_file = artifact_dir / ".feedback" / feedback_key / "session.json"
        if not session_file.exists():
            return

        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        data["status"] = "submitted"
        data["submitted_at"] = datetime.now(timezone.utc).isoformat()
        try:
            session_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            return

        logger.info("Marked feedback submitted for %s", key)

    async def rehost_existing(self, feature_id: str, label_prefix: str = "") -> int:
        """Register URLs for existing artifacts. No subprocess restart needed.

        iriai-feedback serve auto-discovers all artifacts in the directory.
        This just populates the URL cache so get_url() works after recovery.
        """
        fdir = self._mirror.feature_dir(feature_id)
        self._current_feature_id = feature_id
        hosted = 0

        for path in sorted(fdir.rglob("*")):
            if path.is_dir():
                continue
            rel_path = path.relative_to(fdir)
            key = _path_to_key(rel_path)
            if not key:
                continue

            if not path.read_text(encoding="utf-8").strip():
                continue

            label = f"{label_prefix}{key}".strip() if label_prefix else key
            url = self._artifact_url(feature_id, key)
            self._urls[key] = url
            self._labels[key] = label
            logger.info("Re-registered %s at %s", key, url)
            hosted += 1

        return hosted

    async def stop_all(self) -> None:
        """No-op — the single iriai-feedback serve process is bridge-scoped."""
        pass

    async def _notify_refresh(self, feature_id: str, key: str) -> None:
        """Tell iriai-feedback serve to send SSE refresh to connected browsers."""
        url = f"http://localhost:{_SERVE_PORT}/__qa__/api/{feature_id}/{key}/refresh"
        loop = asyncio.get_running_loop()
        try:
            req = urllib.request.Request(url, method="POST", data=b"")
            await loop.run_in_executor(None, urllib.request.urlopen, req)
        except Exception:
            logger.debug("Refresh notification failed for %s/%s", feature_id, key)

    @staticmethod
    def _has_content(model: BaseModel) -> bool:
        """Check if a model has any non-default content worth rendering."""
        for name, field_info in type(model).model_fields.items():
            if name == "complete":
                continue
            value = getattr(model, name)
            if isinstance(value, str) and value:
                return True
            if isinstance(value, list) and value:
                return True
        return False

    @staticmethod
    def _to_display_content(content: str, key: str = "") -> str:
        """Convert JSON-serialized Pydantic models to display format.

        Only converts if the model has actual content — models with all-default
        fields are rejected to avoid replacing rich content with empty headings.

        Namespaced keys (e.g. ``prd:broad``, ``design:canvas``) are resolved
        to their base key for model matching.
        """
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content

        # Resolve namespaced key to base key for model matching
        base_key = key.split(":")[0] if ":" in key else key

        if base_key == "system-design":
            try:
                sd = SystemDesign.model_validate(data)
                if DocHostingService._has_content(sd):
                    return render_system_design_html(sd)
            except ValidationError:
                pass

        if base_key in _KEY_TO_MODEL:
            try:
                model = _KEY_TO_MODEL[base_key].model_validate(data)
                if DocHostingService._has_content(model):
                    return to_markdown(model)
            except ValidationError:
                pass

        for model_cls in _ARTIFACT_MODELS:
            try:
                model = model_cls.model_validate(data)
                if DocHostingService._has_content(model):
                    return to_markdown(model)
            except ValidationError:
                continue

        # JSON that didn't match any model with content — render as formatted JSON
        return f"```json\n{json.dumps(data, indent=2)}\n```\n"
