"""Filesystem artifact mirror.

Writes artifacts to a directory structure that the artifact portal and plan
compiler can read.  The canonical store remains PostgreSQL (via
``PostgresArtifactStore``); this module provides a parallel filesystem view.

Directory layout::

    {base_dir}/features/{feature_id}/
    ├── prd.md
    ├── design-decisions.md
    ├── context.md
    ├── plan.yaml
    ├── mockup.html
    ├── manifest.json          # feature metadata for the portal
    └── journeys/
        └── *.md
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class ArtifactMirror:
    """Mirrors artifacts from the in-memory/DB store to the filesystem."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def feature_dir(self, feature_id: str) -> Path:
        d = self._base / "features" / feature_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_artifact(
        self,
        feature_id: str,
        key: str,
        content: str | BaseModel | dict,
    ) -> Path:
        """Write an artifact to the filesystem mirror.

        ``key`` is mapped to a filename:
        - ``prd`` → ``prd.md``
        - ``design`` → ``design-decisions.md``
        - ``plan`` → ``plan.yaml``
        - anything else → ``{key}.md``
        """
        fdir = self.feature_dir(feature_id)

        filename = _key_to_filename(key)
        path = fdir / filename

        if isinstance(content, BaseModel):
            text = content.model_dump_json(indent=2)
        elif isinstance(content, dict):
            text = json.dumps(content, indent=2)
        else:
            text = str(content)

        path.write_text(text, encoding="utf-8")
        return path

    def write_manifest(
        self,
        feature_id: str,
        *,
        title: str,
        phase: str = "pm",
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Write or update ``manifest.json`` for the artifact portal."""
        fdir = self.feature_dir(feature_id)
        manifest_path = fdir / "manifest.json"

        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        manifest.update(
            {
                "id": feature_id,
                "title": title,
                "phase": phase,
                **(metadata or {}),
            }
        )

        manifest_path.write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return manifest_path

    def list_features(self) -> list[dict[str, Any]]:
        """Return metadata for all features with a manifest."""
        features_dir = self._base / "features"
        if not features_dir.exists():
            return []

        result = []
        for fdir in sorted(features_dir.iterdir()):
            manifest = fdir / "manifest.json"
            if manifest.exists():
                result.append(
                    json.loads(manifest.read_text(encoding="utf-8"))
                )
        return result


# ── Helpers ──────────────────────────────────────────────────────────────────

_KEY_MAP = {
    "prd": "prd.md",
    "design": "design-decisions.md",
    "plan": "plan.yaml",
    "context": "context.md",
    "mockup": "mockup.html",
}


def _key_to_filename(key: str) -> str:
    return _KEY_MAP.get(key, f"{key}.md")
