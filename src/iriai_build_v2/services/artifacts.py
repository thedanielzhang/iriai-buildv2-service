"""Filesystem artifact mirror.

Writes artifacts to a directory structure that the artifact portal and plan
compiler can read.  The canonical store remains PostgreSQL (via
``PostgresArtifactStore``); this module provides a parallel filesystem view.

Directory layout::

    {base_dir}/features/{feature_id}/
    ├── prd.md
    ├── design-decisions.md
    ├── context.md
    ├── plan.md
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

        ``key`` is mapped to a relative path via ``_key_to_path()``:
        - ``prd`` → ``prd.md``
        - ``prd:broad`` → ``broad/prd.md``
        - ``prd:visual-workflow-canvas`` → ``subfeatures/visual-workflow-canvas/prd.md``
        - ``integration-review:pm`` → ``reviews/pm.md``
        """
        fdir = self.feature_dir(feature_id)

        rel_path = _key_to_path(key)
        path = fdir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)

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

    def delete_artifact(self, feature_id: str, key: str) -> None:
        """Delete a mirrored artifact file if it exists."""
        rel_path = _key_to_path(key)
        path = self.feature_dir(feature_id) / rel_path
        path.unlink(missing_ok=True)

        parent = path.parent
        feature_root = self.feature_dir(feature_id)
        while parent != feature_root and parent.exists():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

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
    "scope": "scope.md",
    "decisions": "decisions.md",
    "prd": "prd.md",
    "design": "design-decisions.md",
    "plan": "plan.md",
    "context": "context.md",
    "mockup": "mockup.html",
    "system-design": "system-design.html",
    # Drives the subfeatures/{slug}/test-plan.md suffix rule; per-subfeature-only
    # scope — no top-level test-plan.md is written.
    "test-plan": "test-plan.md",
}


def _sd_source_path(key: str) -> str | None:
    """Return the source markdown path for a system-design key, or None.

    ``hosting.push`` writes rendered HTML to ``system-design.html``,
    overwriting the raw prose the architect originally wrote.  This
    companion path stores the raw source so it survives the overwrite.
    """
    html_path = _key_to_path(key)
    if not html_path.endswith("system-design.html"):
        return None
    return html_path.replace("system-design.html", "system-design-source.md")


def _key_to_path(key: str) -> str:
    """Map an artifact key to a relative file path within the feature directory.

    Standard keys (prd, design, plan, etc.) map to root-level files.
    Namespaced keys map to subdirectories:
      prd:visual-workflow-canvas  → subfeatures/visual-workflow-canvas/prd.md
      prd-summary:visual-workflow  → subfeatures/visual-workflow/prd-summary.md
      design:broad                → broad/design-system.md
      plan:broad                  → broad/architecture.md
      dag:strategy                → broad/strategy.md
      integration-review:pm       → reviews/pm.md
      mockup:visual-workflow      → subfeatures/visual-workflow/mockup.html
    """
    # 1. Exact matches for standard compiled artifacts
    if key in _KEY_MAP:
        return _KEY_MAP[key]

    # 2. Parse namespaced key
    if ":" not in key:
        return f"{key}.md"

    prefix, slug = key.split(":", 1)

    # 3. Broad-phase artifacts
    _BROAD_MAP = {
        "decisions:broad": "broad/decisions.md",
        "decisions:global": "global/decisions.md",
        "prd:broad": "broad/prd.md",
        "design:broad": "broad/design-system.md",
        "plan:broad": "broad/architecture.md",
        "dag:strategy": "broad/strategy.md",
        "design:decomp-alignment": "broad/design-decomp-alignment.md",
        "plan:decomp-alignment": "broad/plan-decomp-alignment.md",
    }
    if key in _BROAD_MAP:
        return _BROAD_MAP[key]

    # 4. Integration reviews and gate reviews
    if prefix == "integration-review":
        return f"reviews/{slug}.md"
    if prefix == "gate-review":
        return f"reviews/{slug}-gate-review.md"
    if prefix == "gate-review-ledger":
        return f"reviews/{slug}-gate-ledger.json"
    if prefix == "gate-enhancement-backlog":
        return f"reviews/{slug}-gate-enhancements.json"
    if prefix == "dag-slices":
        return f"subfeatures/{slug}/dag-slices.json"
    if prefix == "dag-fragment":
        sf_slug, slice_id = slug.split(":", 1)
        return f"subfeatures/{sf_slug}/dag-fragments/{slice_id}.json"
    if prefix == "dag-fragment-attempt":
        sf_slug, attempt_id = slug.split(":", 1)
        return f"subfeatures/{sf_slug}/dag-fragment-attempts/{attempt_id}.md"

    # 5. Subfeature artifacts
    base_key = prefix.replace("-summary", "")  # prd-summary → prd
    is_summary = prefix.endswith("-summary")
    if prefix == "decisions-summary":
        return f"subfeatures/{slug}/decisions-summary.md"
    filename = _KEY_MAP.get(base_key, f"{base_key}.md")
    if is_summary:
        name, ext = filename.rsplit(".", 1)
        filename = f"{name}-summary.{ext}"

    return f"subfeatures/{slug}/{filename}"


def _path_to_key(path: str | Path) -> str | None:
    """Best-effort inverse of ``_key_to_path`` for mirrored artifacts."""
    rel = Path(path).as_posix().lstrip("./")
    if not rel or rel == "manifest.json":
        return None
    parts = Path(rel).parts
    if any(part.startswith(".") for part in parts):
        return None

    top_level = {v: k for k, v in _KEY_MAP.items()}
    if rel in top_level:
        return top_level[rel]

    broad_map = {
        "broad/decisions.md": "decisions:broad",
        "global/decisions.md": "decisions:global",
        "broad/prd.md": "prd:broad",
        "broad/design-system.md": "design:broad",
        "broad/architecture.md": "plan:broad",
        "broad/strategy.md": "dag:strategy",
        "broad/design-decomp-alignment.md": "design:decomp-alignment",
        "broad/plan-decomp-alignment.md": "plan:decomp-alignment",
    }
    if rel in broad_map:
        return broad_map[rel]

    if rel.startswith("reviews/"):
        name = Path(rel).name
        if name.endswith("-gate-review.md"):
            return f"gate-review:{name.removesuffix('-gate-review.md')}"
        if name.endswith("-gate-ledger.json"):
            return f"gate-review-ledger:{name.removesuffix('-gate-ledger.json')}"
        if name.endswith("-gate-enhancements.json"):
            return f"gate-enhancement-backlog:{name.removesuffix('-gate-enhancements.json')}"
        if name.endswith(".md"):
            return f"integration-review:{name.removesuffix('.md')}"
        return None

    if len(parts) == 3 and parts[0] == "subfeatures":
        slug, filename = parts[1], parts[2]
        if filename == "decisions-summary.md":
            return f"decisions-summary:{slug}"
        if filename == "system-design-source.md":
            return None
        inverse = {v: k for k, v in _KEY_MAP.items()}
        base_filename = filename
        is_summary = False
        if filename.endswith("-summary.md") or filename.endswith("-summary.html"):
            is_summary = True
            base_filename = filename.replace("-summary", "", 1)
        base_key = inverse.get(base_filename)
        if not base_key:
            stem = Path(base_filename).stem
            if stem:
                base_key = stem
        if not base_key:
            return None
        prefix = f"{base_key}-summary" if is_summary else base_key
        return f"{prefix}:{slug}"

    if len(parts) == 1 and Path(rel).suffix == ".md":
        return Path(rel).stem

    return None
