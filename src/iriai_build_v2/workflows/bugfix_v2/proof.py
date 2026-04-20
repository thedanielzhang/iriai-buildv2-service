from __future__ import annotations

import json
import mimetypes
import re
import shutil
from html import escape
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, urlsplit
from uuid import uuid4

from iriai_compose import Feature

from ...models.outputs import Check, EvidenceArtifact, EvidenceBundle
from .models import BugflowProofRecord

_TRACE_KINDS = {"trace", "playwright-trace"}
_SCREENSHOT_KINDS = {"screenshot", "image"}
_API_KINDS = {
    "api", "api-request", "api-response", "api-capture", "api-headers", "http", "network",
    "api_request", "api_response", "api_capture", "api_headers",
}
_DATABASE_KINDS = {
    "database", "database-query", "database_query", "query", "query-result", "query_result", "sql",
}
_LOG_KINDS = {
    "logs", "log", "command", "console-log", "console_log",
    "healthcheck", "network-log", "network_log", "test-output", "test_output",
}
_REPO_KINDS = {
    "repo", "repo-reference", "repo_excerpt", "repo-excerpt", "repo_reference",
    "diff", "code", "code_excerpt", "code-excerpt", "code-reference", "code_reference",
}
_UI_STATE_KINDS = {"ui-state", "ui_state"}
_SNAPSHOT_KINDS = {"snapshot"}
_COMMAND_OUTPUT_KINDS = {"command-output", "command_output"}
_CORE_EVIDENCE_MODES = frozenset({"ui", "api", "database", "logs", "repo"})
_DIRECTIVE_TO_CORE_SURFACE = {
    "ui": "ui",
    "trace": "ui",
    "screenshot": "ui",
    "image": "ui",
    "api": "api",
    "api_request": "api",
    "api_response": "api",
    "api_capture": "api",
    "api_headers": "api",
    "http": "api",
    "network": "api",
    "database": "database",
    "database_query": "database",
    "query": "database",
    "query_result": "database",
    "sql": "database",
    "logs": "logs",
    "log": "logs",
    "console_log": "logs",
    "network_log": "logs",
    "test_output": "logs",
    "healthcheck": "logs",
    "command_output": "logs",
    "repo": "repo",
    "repo_reference": "repo",
    "repo_excerpt": "repo",
    "diff": "repo",
    "code": "repo",
    "code_excerpt": "repo",
    "code_reference": "repo",
}
_GENERIC_API_SOURCE_HINTS = ("api", "curl", "http", "request", "response")
_GENERIC_DATABASE_SOURCE_HINTS = ("database", "postgres", "sql", "query")
_GENERIC_LOG_SOURCE_HINTS = ("log", "console", "vitest", "pytest", "health", "network capture")
_GENERIC_REPO_SOURCE_HINTS = ("repo", "git", "diff", "code excerpt", "source tree")
_ALLOWED_SUFFIXES = {
    ".zip", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".json", ".txt", ".log",
    ".md", ".html", ".csv", ".har", ".mp4",
}
_MAX_PROOF_BYTES = 64 * 1024 * 1024


def feature_root_from_workspace(workspace_path: str, feature_slug: str) -> Path:
    return Path(workspace_path).expanduser() / ".iriai" / "features" / feature_slug


def proof_root_for_main_root(main_root: Path) -> Path:
    return main_root.parent / "proof"


def proof_stage_dir(proof_root: Path, report_id: str, stage: str) -> Path:
    return proof_root / report_id / stage


def dashboard_base_url(feature: Feature) -> str:
    for key in ("dashboard_base_url", "dashboard_url"):
        raw = str(feature.metadata.get(key, "") or "").strip()
        if not raw:
            continue
        parsed = urlsplit(raw)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return ""


def proof_public_url(base_url: str, feature_id: str, report_id: str, stage: str, filename: str) -> str:
    route = (
        f"/proof/{quote(feature_id)}/{quote(report_id)}/{quote(stage)}/{quote(filename)}"
    )
    if not base_url:
        return route
    return f"{base_url.rstrip('/')}{route}"


def normalize_evidence_modes(*mode_sets: Iterable[str], ui_involved: bool = False) -> list[str]:
    ordered: list[str] = []
    if ui_involved:
        ordered.append("ui")
    for modes in mode_sets:
        for mode in modes:
            value = str(mode or "").strip().lower()
            if not value or value in ordered:
                continue
            ordered.append(value)
    return ordered


def normalize_evidence_directives(*mode_sets: Iterable[str], ui_involved: bool = False) -> list[str]:
    ordered: list[str] = []
    if ui_involved:
        ordered.append("ui")
    for modes in mode_sets:
        for mode in modes:
            value = re.sub(r"[-\s]+", "_", str(mode or "").strip().lower())
            if not value or value in ordered:
                continue
            ordered.append(value)
    return ordered


def core_evidence_modes(*mode_sets: Iterable[str], ui_involved: bool = False) -> list[str]:
    return [
        mode
        for mode in normalize_evidence_modes(*mode_sets, ui_involved=ui_involved)
        if mode in _CORE_EVIDENCE_MODES
    ]


def directive_core_surface(directive: str) -> str | None:
    normalized = normalize_evidence_directives([directive])
    if not normalized:
        return None
    return _DIRECTIVE_TO_CORE_SURFACE.get(normalized[0])


def core_surfaces_for_directives(*mode_sets: Iterable[str], ui_involved: bool = False) -> list[str]:
    surfaces: list[str] = []
    for directive in normalize_evidence_directives(*mode_sets, ui_involved=ui_involved):
        surface = directive_core_surface(directive)
        if surface and surface not in surfaces:
            surfaces.append(surface)
    return surfaces


def required_evidence_modes(*, ui_involved: bool, evidence_modes: list[str]) -> list[str]:
    required = normalize_evidence_modes(evidence_modes, ui_involved=ui_involved)
    return required or (["ui"] if ui_involved else [])


def canonical_artifact_surface(artifact: EvidenceArtifact) -> str:
    token = re.sub(r"[_\s]+", "-", str(artifact.kind or "").strip().lower())
    if token in _TRACE_KINDS:
        return "trace"
    if token in _SCREENSHOT_KINDS:
        return "screenshot"
    if token in _API_KINDS:
        return "api"
    if token in _DATABASE_KINDS:
        return "database"
    if token in _LOG_KINDS:
        return "logs"
    if token in _REPO_KINDS:
        return "repo"
    if token in _UI_STATE_KINDS:
        return "ui-state"
    if token in _SNAPSHOT_KINDS:
        return "snapshot"
    if token in _COMMAND_OUTPUT_KINDS:
        return "command-output"

    hint = " ".join(
        value.strip().lower()
        for value in [artifact.source, artifact.label]
        if str(value or "").strip()
    )
    if token in {"json", "text", "result", "response"}:
        if any(word in hint for word in _GENERIC_DATABASE_SOURCE_HINTS):
            return "database"
        if any(word in hint for word in _GENERIC_API_SOURCE_HINTS):
            return "api"
        if any(word in hint for word in _GENERIC_LOG_SOURCE_HINTS):
            return "logs"
        if any(word in hint for word in _GENERIC_REPO_SOURCE_HINTS):
            return "repo"
    return token


def bundle_artifact_surfaces(bundle: EvidenceBundle | None) -> list[str]:
    if bundle is None:
        return []
    return sorted({canonical_artifact_surface(artifact) for artifact in bundle.artifacts if artifact.kind})


def bundle_provided_evidence_modes(bundle: EvidenceBundle | None) -> list[str]:
    surfaces = set(bundle_artifact_surfaces(bundle))
    provided: list[str] = []
    if {"trace", "screenshot"}.issubset(surfaces):
        provided.append("ui")
    if "api" in surfaces:
        provided.append("api")
    if "database" in surfaces:
        provided.append("database")
    if "logs" in surfaces or "command-output" in surfaces:
        provided.append("logs")
    if "repo" in surfaces:
        provided.append("repo")
    return provided


def bundle_declared_core_modes(bundle: EvidenceBundle | None) -> list[str]:
    if bundle is None:
        return []
    return core_surfaces_for_directives(bundle.evidence_modes, ui_involved=bundle.ui_involved)


def proof_requirement_diagnostics(
    *,
    required_modes: list[str],
    bundle: EvidenceBundle | None,
    require_ui_proof: bool,
    state_change: bool,
) -> dict[str, object]:
    if bundle is None:
        return {
            "required_modes": core_evidence_modes(required_modes, ui_involved=require_ui_proof),
            "provided_modes": [],
            "artifact_surfaces": [],
            "declared_modes": [],
            "declared_without_artifacts": [],
            "has_postcondition": False,
            "missing": ["proof bundle"],
        }

    required = core_evidence_modes(required_modes, ui_involved=require_ui_proof)
    artifact_surfaces = bundle_artifact_surfaces(bundle)
    provided_modes = bundle_provided_evidence_modes(bundle)
    declared_modes = bundle_declared_core_modes(bundle)
    missing: list[str] = []
    if require_ui_proof and "ui" in required and "ui" not in provided_modes:
        surface_set = set(artifact_surfaces)
        if "trace" not in surface_set:
            missing.append("Playwright trace")
        if "screenshot" not in surface_set:
            missing.append("screenshot")
    for mode in required:
        if mode == "ui":
            continue
        if mode == "api" and "api" not in provided_modes:
            missing.append("API request/response evidence")
        if mode == "database" and "database" not in provided_modes:
            missing.append("database query/result evidence")
        if mode == "logs" and "logs" not in provided_modes:
            missing.append("logs or health evidence")
        if mode == "repo" and "repo" not in provided_modes:
            missing.append("repo/static diagnostic evidence")

    has_postcondition = any(
        artifact.role.strip().lower() in {"postcondition", "verification"}
        for artifact in bundle.artifacts
    )
    if state_change and not has_postcondition:
        missing.append("independent postcondition evidence")

    return {
        "required_modes": required,
        "provided_modes": provided_modes,
        "artifact_surfaces": artifact_surfaces,
        "declared_modes": declared_modes,
        "declared_without_artifacts": [mode for mode in declared_modes if mode not in provided_modes],
        "has_postcondition": has_postcondition,
        "missing": missing,
    }


def bundle_primary_artifact_url(bundle: EvidenceBundle) -> str:
    screenshots = [
        artifact.public_url
        for artifact in bundle.artifacts
        if artifact.public_url and canonical_artifact_surface(artifact) == "screenshot"
    ]
    if screenshots:
        return screenshots[0]
    for artifact in bundle.artifacts:
        if artifact.public_url:
            return artifact.public_url
    return ""


def evidence_missing_requirements(
    *,
    required_modes: list[str],
    bundle: EvidenceBundle | None,
    require_ui_proof: bool,
    state_change: bool,
) -> list[str]:
    diagnostics = proof_requirement_diagnostics(
        required_modes=required_modes,
        bundle=bundle,
        require_ui_proof=require_ui_proof,
        state_change=state_change,
    )
    return list(diagnostics["missing"])


def render_proof_index(record: BugflowProofRecord) -> str:
    rows = []
    for artifact in record.bundle.artifacts:
        link = (
            f'<a href="{escape(artifact.public_url)}">{escape(artifact.label or artifact.kind)}</a>'
            if artifact.public_url
            else escape(artifact.label or artifact.kind)
        )
        meta_bits = [
            value
            for value in [artifact.kind, artifact.role, artifact.source, artifact.mime_type]
            if value
        ]
        excerpt = (
            f"<pre>{escape(artifact.excerpt)}</pre>"
            if artifact.excerpt
            else ""
        )
        rows.append(
            "<li>"
            f"<strong>{link}</strong>"
            + (f"<div>{escape(' | '.join(meta_bits))}</div>" if meta_bits else "")
            + excerpt
            + "</li>"
        )
    artifacts_html = "<ul>" + "".join(rows) + "</ul>" if rows else "<p>No files were attached to this proof bundle.</p>"
    modes = ", ".join(record.bundle.evidence_modes) or "none"
    steps = "".join(f"<li>{escape(step)}</li>" for step in record.bundle.steps_executed)
    steps_html = f"<ul>{steps}</ul>" if steps else "<p>No explicit steps were recorded.</p>"
    checks = "".join(
        (
            "<li>"
            f"<strong>{escape(check.criterion)}</strong>: {escape(check.result)}"
            + (f"<div>{escape(check.detail)}</div>" if check.detail else "")
            + "</li>"
        )
        for check in record.checks
    )
    checks_html = f"<ul>{checks}</ul>" if checks else "<p>No structured evidence coverage was recorded.</p>"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Bugflow Proof — {escape(record.report_id)} — {escape(record.stage)}</title>"
        "<style>body{font-family:ui-sans-serif,system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 24px;line-height:1.55}"
        "pre{white-space:pre-wrap;background:#f5f5f5;padding:12px;border-radius:8px}"
        "code{background:#f5f5f5;padding:2px 6px;border-radius:4px}</style></head><body>"
        f"<h1>{escape(record.report_id)} — {escape(record.stage)}</h1>"
        f"<p><strong>Summary:</strong> {escape(record.bundle.summary or 'No summary provided.')}</p>"
        f"<p><strong>Evidence modes:</strong> {escape(modes)}</p>"
        f"<p><strong>UI involved:</strong> {'yes' if record.bundle.ui_involved else 'no'}</p>"
        + (
            f"<p><strong>Environment notes:</strong> {escape(record.bundle.environment_notes)}</p>"
            if record.bundle.environment_notes
            else ""
        )
        + (
            f"<p><strong>Principal context:</strong> {escape(record.bundle.principal_context)}</p>"
            if record.bundle.principal_context
            else ""
        )
        + "<h2>Steps Executed</h2>"
        + steps_html
        + "<h2>Structured Coverage</h2>"
        + checks_html
        + "<h2>Artifacts</h2>"
        + artifacts_html
        + "</body></html>"
    )


def persist_proof_record(
    *,
    feature: Feature,
    feature_proof_root: Path,
    report_id: str,
    stage: str,
    bundle: EvidenceBundle,
    checks: list[Check] | None = None,
    context_root: Path | None = None,
) -> BugflowProofRecord:
    storage_stage = _storage_stage_name(stage)
    stage_dir = proof_stage_dir(feature_proof_root, report_id, storage_stage)
    stage_dir.mkdir(parents=True, exist_ok=True)

    copied_artifacts: list[EvidenceArtifact] = []
    base_url = dashboard_base_url(feature)
    for index, artifact in enumerate(bundle.artifacts, start=1):
        copied_artifacts.append(
            _copy_artifact(
                artifact,
                stage_dir=stage_dir,
                feature_id=feature.id,
                report_id=report_id,
                storage_stage=storage_stage,
                base_url=base_url,
                index=index,
                context_root=context_root,
            )
        )

    stored_bundle = bundle.model_copy(
        update={
            "ui_involved": bundle.ui_involved,
            "evidence_modes": normalize_evidence_modes(
                bundle.evidence_modes,
                ui_involved=bundle.ui_involved,
            ),
            "artifacts": copied_artifacts,
        }
    )
    record = BugflowProofRecord(
        report_id=report_id,
        stage=stage,
        storage_stage=storage_stage,
        bundle=stored_bundle,
        checks=list(checks or []),
        bundle_url=proof_public_url(base_url, feature.id, report_id, storage_stage, "index.html"),
        primary_artifact_url=bundle_primary_artifact_url(stored_bundle),
    )
    (stage_dir / "bundle.json").write_text(
        record.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (stage_dir / "index.html").write_text(
        render_proof_index(record),
        encoding="utf-8",
    )
    return record


def snapshot_proof_record(
    *,
    feature: Feature,
    feature_proof_root: Path,
    source: BugflowProofRecord,
    stage: str,
) -> BugflowProofRecord:
    storage_stage = _storage_stage_name(stage)
    stage_dir = proof_stage_dir(feature_proof_root, source.report_id, storage_stage)
    stage_dir.mkdir(parents=True, exist_ok=True)

    copied_artifacts: list[EvidenceArtifact] = []
    base_url = dashboard_base_url(feature)
    for index, artifact in enumerate(source.bundle.artifacts, start=1):
        copied_artifacts.append(
            _copy_snapshot_artifact(
                artifact,
                stage_dir=stage_dir,
                feature_id=feature.id,
                report_id=source.report_id,
                storage_stage=storage_stage,
                base_url=base_url,
                index=index,
                proof_root=feature_proof_root,
            )
        )

    stored_bundle = source.bundle.model_copy(update={"artifacts": copied_artifacts})
    record = BugflowProofRecord(
        report_id=source.report_id,
        stage=stage,
        storage_stage=storage_stage,
        bundle=stored_bundle,
        checks=list(source.checks),
        bundle_url=proof_public_url(base_url, feature.id, source.report_id, storage_stage, "index.html"),
        primary_artifact_url=bundle_primary_artifact_url(stored_bundle),
    )
    (stage_dir / "bundle.json").write_text(
        record.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (stage_dir / "index.html").write_text(
        render_proof_index(record),
        encoding="utf-8",
    )
    return record


def _copy_artifact(
    artifact: EvidenceArtifact,
    *,
    stage_dir: Path,
    feature_id: str,
    report_id: str,
    storage_stage: str,
    base_url: str,
    index: int,
    context_root: Path | None,
) -> EvidenceArtifact:
    resolved = _resolve_local_path(artifact.local_path, context_root)
    if resolved is None:
        return artifact.model_copy(
            update={
                "local_path": "",
                "public_url": "",
                "mime_type": artifact.mime_type or "",
            }
        )

    filename = _artifact_filename(index, artifact, resolved)
    dest = stage_dir / filename
    shutil.copy2(resolved, dest)
    mime_type = artifact.mime_type or mimetypes.guess_type(dest.name)[0] or "application/octet-stream"
    return artifact.model_copy(
        update={
            "local_path": str(dest),
            "public_url": proof_public_url(base_url, feature_id, report_id, storage_stage, dest.name),
            "mime_type": mime_type,
        }
    )


def _copy_snapshot_artifact(
    artifact: EvidenceArtifact,
    *,
    stage_dir: Path,
    feature_id: str,
    report_id: str,
    storage_stage: str,
    base_url: str,
    index: int,
    proof_root: Path,
) -> EvidenceArtifact:
    resolved = _resolve_snapshot_path(artifact.local_path, proof_root)
    if resolved is None:
        return artifact.model_copy(
            update={
                "local_path": "",
                "public_url": "",
            }
        )
    filename = _artifact_filename(index, artifact, resolved)
    dest = stage_dir / filename
    shutil.copy2(resolved, dest)
    mime_type = artifact.mime_type or mimetypes.guess_type(dest.name)[0] or "application/octet-stream"
    return artifact.model_copy(
        update={
            "local_path": str(dest),
            "public_url": proof_public_url(base_url, feature_id, report_id, storage_stage, dest.name),
            "mime_type": mime_type,
        }
    )


def _resolve_local_path(raw_path: str, context_root: Path | None) -> Path | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        if context_root is None:
            return None
        candidate = context_root / candidate
    try:
        resolved = candidate.resolve()
    except Exception:
        resolved = candidate
    if not resolved.exists() or not resolved.is_file():
        return None
    if context_root is None:
        return None
    try:
        root = context_root.resolve()
    except Exception:
        root = context_root
    if resolved != root and root not in resolved.parents:
        return None
    if resolved.suffix.lower() not in _ALLOWED_SUFFIXES:
        return None
    if resolved.stat().st_size > _MAX_PROOF_BYTES:
        return None
    return resolved


def _resolve_snapshot_path(raw_path: str, proof_root: Path) -> Path | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    try:
        resolved = candidate.resolve()
    except Exception:
        resolved = candidate
    try:
        root = proof_root.resolve()
    except Exception:
        root = proof_root
    if not resolved.exists() or not resolved.is_file():
        return None
    if resolved != root and root not in resolved.parents:
        return None
    if resolved.suffix.lower() not in _ALLOWED_SUFFIXES:
        return None
    if resolved.stat().st_size > _MAX_PROOF_BYTES:
        return None
    return resolved


def _artifact_filename(index: int, artifact: EvidenceArtifact, source_path: Path) -> str:
    base = source_path.name or artifact.label or artifact.kind or f"artifact-{index}"
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-") or f"artifact-{index}"
    prefix = re.sub(r"[^A-Za-z0-9_-]+", "-", artifact.kind or "artifact").strip("-") or "artifact"
    return f"{index:02d}-{prefix}-{sanitized}"


def _storage_stage_name(stage: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", stage or "proof").strip("-") or "proof"
    return f"{normalized}-{uuid4().hex[:8]}"
