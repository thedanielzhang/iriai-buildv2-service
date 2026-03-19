"""Document hosting service for artifact review sessions.

Wraps ArtifactMirror + FeedbackService into a single service that manages
hosted artifact sessions.  Created per-workflow, torn down on completion.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from ..models.outputs import (
    DesignDecisions,
    ImplementationDAG,
    PRD,
    ScopeOutput,
    SystemDesign,
    TechnicalPlan,
)
from .markdown import to_markdown
from .system_design_html import render_system_design_html

if TYPE_CHECKING:
    from .artifacts import ArtifactMirror
    from .tunnel import CloudflareTunnel
    from ..tasks.feedback import FeedbackService, SessionInfo

logger = logging.getLogger(__name__)

# Explicit key → model mapping.  Most output models have all-default fields,
# so *any* JSON dict validates against *any* of them.  Using the artifact key
# to pick the correct model avoids cross-contamination (e.g. ScopeOutput JSON
# being rendered as an empty PRD).
_KEY_TO_MODEL: dict[str, type[BaseModel]] = {
    "prd": PRD,
    "design": DesignDecisions,
    "plan": TechnicalPlan,
    "scope": ScopeOutput,
}

# Fallback list for unknown keys (order shouldn't matter when key is known).
_ARTIFACT_MODELS: list[type[BaseModel]] = [PRD, DesignDecisions, TechnicalPlan, ImplementationDAG]


class DocHostingService:
    """Manages artifact hosting sessions. Created per-workflow, torn down on completion."""

    def __init__(
        self,
        mirror: ArtifactMirror,
        feedback: FeedbackService,
        *,
        tunnel: CloudflareTunnel | None = None,
    ) -> None:
        self._mirror = mirror
        self._feedback = feedback
        self._tunnel = tunnel
        self._sessions: dict[str, SessionInfo] = {}
        self._urls: dict[str, str] = {}  # key → public URL (tunneled if available)
        self._labels: dict[str, str] = {}  # key → label (for restarts)

    async def push(self, feature_id: str, key: str, content: str, label: str) -> str:
        """Write artifact to disk and start hosting. Returns URL. Raises on failure."""
        display_content = self._to_display_content(content, key)
        path = self._mirror.write_artifact(feature_id, key, display_content)
        base_path = f"/features/{feature_id}/{key}"
        info = await self._feedback.start_doc_review(
            str(path), title=label, base_path=base_path,
        )

        url = info.url
        if self._tunnel:
            try:
                public_base = await self._tunnel.tunnel(info.port)
                url = f"{public_base}{base_path}"
                logger.info("Tunneled %s: %s → %s", key, info.url, url)
            except Exception:
                logger.warning("Tunnel failed for %s, using local URL", key, exc_info=True)

        self._sessions[key] = info
        self._urls[key] = url  # Store the (possibly tunneled) URL
        self._labels[key] = label
        logger.info("Hosted %s at %s", key, url)
        return url

    async def update(self, feature_id: str, key: str, content: str) -> None:
        """Re-write artifact and restart its hosting session.

        The iriai-feedback server reads the file once at startup, so a
        disk-only write won't be picked up.  Stop the old session and
        re-push to start a fresh server with the new content.
        ``push()`` handles display-format conversion, so callers pass raw
        content (JSON / text) — same as ``push()``.
        """
        old_info = self._sessions.get(key)
        if old_info:
            try:
                await self._feedback.stop(old_info.session_id)
            except Exception:
                logger.warning("Failed to stop old session for %s", key, exc_info=True)

        label = self._labels.get(key, key)
        await self.push(feature_id, key, content, label)

    def get_url(self, key: str) -> str | None:
        return self._urls.get(key)

    async def try_collect(self, key: str) -> list[dict[str, Any]]:
        """Non-blocking: return annotations if available, else []."""
        info = self._sessions.get(key)
        if not info:
            logger.warning("[diag] try_collect: no session for key %r (known: %s)", key, list(self._sessions.keys()))
            return []
        logger.warning("[diag] try_collect(%r): session_id=%s", key, info.session_id)
        annotations = await self._feedback.get_annotations(info.session_id)
        logger.warning("[diag] try_collect(%r): got %d annotations", key, len(annotations))
        return annotations

    async def rehost_existing(self, feature_id: str, label_prefix: str = "") -> int:
        """Re-host all artifact files on disk for a feature. Returns count hosted.

        Scans the artifact mirror's feature directory for known artifact files
        and pushes each one to start a fresh hosting session.  Used after a
        bridge restart to restore browser review URLs.
        """
        from .artifacts import _KEY_MAP

        fdir = self._mirror.feature_dir(feature_id)
        hosted = 0

        # Reverse map: filename → artifact key
        filename_to_key = {v: k for k, v in _KEY_MAP.items()}

        for path in sorted(fdir.iterdir()):
            if path.name == "manifest.json" or path.is_dir():
                continue
            key = filename_to_key.get(path.name)
            if not key:
                continue

            content = path.read_text(encoding="utf-8")
            if not content.strip():
                continue

            label = f"{label_prefix}{key}".strip() if label_prefix else key
            try:
                url = await self.push(feature_id, key, content, label)
                logger.info("Re-hosted %s at %s", key, url)
                hosted += 1
            except Exception:
                logger.warning("Failed to re-host %s for feature %s", key, feature_id, exc_info=True)

        return hosted

    async def stop_all(self) -> None:
        """Best-effort cleanup — logs warnings but doesn't raise."""
        for key, info in self._sessions.items():
            try:
                await self._feedback.stop(info.session_id)
            except Exception:
                logger.warning("Failed to stop session for %s", key, exc_info=True)
        self._sessions.clear()

    @staticmethod
    def _to_display_content(content: str, key: str = "") -> str:
        """Convert JSON-serialized Pydantic models to display format.

        Uses the artifact *key* to select the correct model, avoiding
        false-positive validation (most models have all-default fields).
        SystemDesign → HTML; everything else → markdown.
        If *content* is not valid JSON or doesn't match a known model, it is
        returned as-is (already markdown/HTML/text).
        """
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content

        # SystemDesign renders to interactive HTML.
        if key == "system-design":
            try:
                sd = SystemDesign.model_validate(data)
                return render_system_design_html(sd)
            except ValidationError:
                pass

        # Try the model mapped to this specific key first.
        if key in _KEY_TO_MODEL:
            try:
                model = _KEY_TO_MODEL[key].model_validate(data)
                return to_markdown(model)
            except ValidationError:
                pass

        # Fallback: try all known models for unknown keys.
        for model_cls in _ARTIFACT_MODELS:
            try:
                model = model_cls.model_validate(data)
                return to_markdown(model)
            except ValidationError:
                continue

        return content
