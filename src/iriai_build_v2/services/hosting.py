"""Document hosting service for artifact review sessions.

With ``iriai-feedback serve``, the hosting service is a thin URL manager:
write artifacts to disk, construct deterministic URLs, read co-located
feedback.  The single ``iriai-feedback serve`` process handles rendering,
overlay injection, and annotation storage.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
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

logger = logging.getLogger(__name__)

_KEY_TO_MODEL: dict[str, type[BaseModel]] = {
    "prd": PRD,
    "design": DesignDecisions,
    "plan": TechnicalPlan,
    "scope": ScopeOutput,
}

_ARTIFACT_MODELS: list[type[BaseModel]] = [PRD, DesignDecisions, TechnicalPlan, ImplementationDAG]

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
        logger.info("QA hosted %s at %s", key, url)
        return url

    async def update(self, feature_id: str, key: str, content: str) -> None:
        """Re-write artifact file. iriai-feedback serve watches for changes and auto-refreshes."""
        display_content = self._to_display_content(content, key)
        self._mirror.write_artifact(feature_id, key, display_content)
        logger.info("Updated %s (auto-refresh via SSE)", key)

    def get_url(self, key: str) -> str | None:
        return self._urls.get(key)

    async def try_collect(self, key: str) -> list[dict[str, Any]]:
        """Read annotations from co-located .feedback/ directory."""
        if not self._current_feature_id:
            return []

        feedback_dir = (
            self._mirror.feature_dir(self._current_feature_id)
            / ".feedback"
            / key
            / "annotations"
        )

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

    async def rehost_existing(self, feature_id: str, label_prefix: str = "") -> int:
        """Register URLs for existing artifacts. No subprocess restart needed.

        iriai-feedback serve auto-discovers all artifacts in the directory.
        This just populates the URL cache so get_url() works after recovery.
        """
        from .artifacts import _KEY_MAP

        fdir = self._mirror.feature_dir(feature_id)
        self._current_feature_id = feature_id
        hosted = 0

        filename_to_key = {v: k for k, v in _KEY_MAP.items()}

        for path in sorted(fdir.iterdir()):
            if path.name == "manifest.json" or path.is_dir():
                continue
            key = filename_to_key.get(path.name)
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

    @staticmethod
    def _to_display_content(content: str, key: str = "") -> str:
        """Convert JSON-serialized Pydantic models to display format."""
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content

        if key == "system-design":
            try:
                sd = SystemDesign.model_validate(data)
                return render_system_design_html(sd)
            except ValidationError:
                pass

        if key in _KEY_TO_MODEL:
            try:
                model = _KEY_TO_MODEL[key].model_validate(data)
                return to_markdown(model)
            except ValidationError:
                pass

        for model_cls in _ARTIFACT_MODELS:
            try:
                model = model_cls.model_validate(data)
                return to_markdown(model)
            except ValidationError:
                continue

        return content
