from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any

import pytest


FEATURE_ID = "8ac124d6"
FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "execution_control_plane"
    / f"feature_{FEATURE_ID}"
)
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"
METRICS_PATH = FIXTURE_DIR / "derived_metrics.json"
FINDINGS_PATH = FIXTURE_DIR / "drag_findings.json"
ARTIFACT_SUMMARIES_PATH = FIXTURE_DIR / "artifact_summaries.jsonl"
COLLECTOR_AUDIT_PATH = FIXTURE_DIR / "collector_audit.jsonl"
EVENT_SUMMARIES_PATH = FIXTURE_DIR / "event_summaries.jsonl"
SELECTED_ARTIFACT_SLICES_PATH = FIXTURE_DIR / "selected_artifact_slices.jsonl"

REQUIRED_FAILURE_CLASSES = {
    "worktree_alias",
    "acl_writeability",
    "stale_projection",
    "commit_hygiene",
    "commit_only_routing",
    "runtime_provider",
    "queue_recovery",
    "checkpoint_contradiction",
    "product_contract_drift",
    "regroup_overlay_readiness",
    "broad_read_legacy_consumer",
}
MAX_SELECTED_SLICE_CHARS = 8_000
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FULL_BODY_KEYS = {
    "artifact_body",
    "body",
    "content",
    "full_artifact_body",
    "full_body",
    "payload",
    "raw_body",
    "value",
}
BODY_PREVIEW_KEYS = {
    "content_preview",
    "preview",
    "summary",
    "summary_preview",
    "value_hash",
    "value_sha256",
}


def _require_file(path: Path) -> None:
    assert path.exists(), f"missing required Slice 00 fixture file: {path}"
    assert path.is_file(), f"Slice 00 fixture path is not a file: {path}"


def _load_json(path: Path) -> Any:
    _require_file(path)
    with path.open(encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except json.JSONDecodeError as exc:
            pytest.fail(f"{path} is not valid JSON: {exc}")


def _jsonl_paths() -> list[Path]:
    assert FIXTURE_DIR.exists(), (
        "missing Slice 00 feature fixture directory: "
        f"{FIXTURE_DIR}; expected checked-in evidence fixtures for {FEATURE_ID}"
    )
    paths = sorted(FIXTURE_DIR.glob("*.jsonl"))
    assert paths, f"missing JSONL evidence rows under {FIXTURE_DIR}"
    return paths


def _load_jsonl_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _jsonl_paths():
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    pytest.fail(f"{path}:{line_no} is not valid JSONL: {exc}")
                assert isinstance(row, dict), (
                    f"{path}:{line_no} must contain a JSON object, got "
                    f"{type(row).__name__}"
                )
                rows.append(row)
    assert rows, f"JSONL evidence files under {FIXTURE_DIR} contain no rows"
    return rows


def _load_jsonl_file(path: Path) -> list[dict[str, Any]]:
    _require_file(path)
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                pytest.fail(f"{path}:{line_no} is not valid JSONL: {exc}")
            assert isinstance(row, dict), (
                f"{path}:{line_no} must contain a JSON object, got "
                f"{type(row).__name__}"
            )
            rows.append(row)
    assert rows, f"{path} contains no JSONL rows"
    return rows


def _walk(value: Any, path: str = "$") -> list[tuple[str, str, Any]]:
    if isinstance(value, dict):
        items: list[tuple[str, str, Any]] = []
        for key, child in value.items():
            items.extend(_walk(child, f"{path}.{key}"))
            items.append((path, str(key), child))
        return items
    if isinstance(value, list):
        items = []
        for idx, child in enumerate(value):
            items.extend(_walk(child, f"{path}[{idx}]"))
        return items
    return []


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _fixture_hash(rows: list[dict[str, Any]]) -> str:
    canonical_lines = [_canonical_json(row) for row in sorted(rows, key=_canonical_json)]
    return hashlib.sha256(("\n".join(canonical_lines) + "\n").encode()).hexdigest()


def _extract_failure_class(finding: dict[str, Any]) -> str | None:
    for key in ("class", "failure_class", "failureClass", "drag_class"):
        value = finding.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _load_findings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if FINDINGS_PATH.exists():
        findings = _load_json(FINDINGS_PATH)
        if isinstance(findings, dict):
            findings = findings.get("findings", findings.get("workflow_drag_findings"))
        assert isinstance(findings, list), (
            f"{FINDINGS_PATH} must be a list or an object with a findings list"
        )
        assert all(isinstance(finding, dict) for finding in findings), (
            f"{FINDINGS_PATH} must contain only JSON objects"
        )
        return list(findings)

    findings = [
        row
        for row in rows
        if _extract_failure_class(row)
        or row.get("type") == "workflow_drag_finding"
        or row.get("kind") == "workflow_drag_finding"
    ]
    assert findings, (
        f"missing {FINDINGS_PATH} and no workflow drag findings were embedded "
        "in JSONL rows"
    )
    return findings


def _metric_snapshot(manifest: dict[str, Any]) -> dict[str, Any]:
    if METRICS_PATH.exists():
        metrics = _load_json(METRICS_PATH)
    else:
        metrics = manifest.get("derived_metrics")
    assert isinstance(metrics, dict), (
        f"missing derived metrics: expected {METRICS_PATH} or "
        "manifest.derived_metrics"
    )
    return metrics


def _artifact_prefix(key: str) -> str:
    if key.startswith("dag-commit-failure:"):
        return "dag-commit-failure"
    if key.startswith("dag-group:"):
        return "dag-group"
    if key.startswith("dag-regroup:") or key.startswith("dag-regroup-active:"):
        return "dag-regroup"
    if key.startswith("dag-task:"):
        return "dag-task"
    if key.startswith("dag-verify:"):
        return "dag-verify"
    return key.split(":", 1)[0]


def _prefix_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = row.get("key")
        assert isinstance(key, str) and key, f"artifact summary row is missing key: {row}"
        prefix = _artifact_prefix(key)
        counts[prefix] = counts.get(prefix, 0) + 1
    return dict(sorted(counts.items()))


def _failure_class_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        failure_class = _extract_failure_class(finding)
        assert failure_class, f"finding is missing a failure class: {finding}"
        counts[failure_class] = counts.get(failure_class, 0) + 1
    return dict(sorted(counts.items()))


def _evidence_ref_count(findings: list[dict[str, Any]]) -> int:
    total = 0
    for finding in findings:
        refs = finding.get("evidence_refs", finding.get("evidenceRefs", []))
        assert isinstance(refs, list), f"finding evidence_refs must be a list: {finding}"
        assert refs, f"finding must cite at least one evidence ref: {finding}"
        total += len(refs)
    return total


def test_fixture_manifest_freezes_read_only_bounded_evidence_contract():
    manifest = _load_json(MANIFEST_PATH)
    assert isinstance(manifest, dict), f"{MANIFEST_PATH} must contain a JSON object"
    collection_policy = manifest.get("collection_policy", {})
    assert isinstance(collection_policy, dict), "manifest collection_policy must be an object"

    assert manifest.get("feature_id") == FEATURE_ID
    assert collection_policy.get("live_mutation") is False
    assert collection_policy.get("bounded_reads") is True
    assert collection_policy.get("summary_only") is True
    assert "live_workflow_mutation" in collection_policy.get("forbidden_paths", [])
    assert "live_store_mutation" in collection_policy.get("forbidden_paths", [])

    for row in _load_jsonl_file(COLLECTOR_AUDIT_PATH):
        assert row.get("feature_id") == FEATURE_ID
        assert row.get("bounded") is True
        assert row.get("forbidden_broad_hydration_count") == 0
        assert row.get("forbidden_live_mutation_count") == 0

    observed = set(manifest.get("required_failure_classes", []))
    if not observed:
        observed = set(manifest.get("failure_classes", []))
    assert REQUIRED_FAILURE_CLASSES <= observed


def test_jsonl_rows_are_parseable_hashable_bounded_and_order_independent():
    rows = _load_jsonl_rows()
    canonical = [_canonical_json(row) for row in rows]
    metrics = _metric_snapshot(_load_json(MANIFEST_PATH))

    shuffled = list(rows)
    random.Random(0).shuffle(shuffled)
    assert sorted(canonical) == sorted(_canonical_json(row) for row in shuffled)
    assert _fixture_hash(rows) == _fixture_hash(shuffled)
    assert metrics.get("jsonl_sha256") == _fixture_hash(rows)

    for row in rows:
        row_sha_fields = [
            (path, key, value)
            for path, key, value in _walk(row)
            if "sha256" in key.lower()
        ]
        for path, key, value in row_sha_fields:
            assert isinstance(value, str) and SHA256_RE.fullmatch(value), (
                f"{path}.{key} must be a lowercase 64-character sha256 hex digest"
            )

        for path, key, value in _walk(row):
            normalized_key = key.lower()
            if normalized_key in BODY_PREVIEW_KEYS:
                continue
            assert normalized_key not in FULL_BODY_KEYS, (
                f"{path}.{key} stores a full artifact body; store summaries, "
                "hashes, ids, and bounded slices instead"
            )
            if normalized_key.endswith("_bytes") and isinstance(value, int):
                assert value >= 0, f"{path}.{key} must be non-negative"

    for row in _load_jsonl_file(SELECTED_ARTIFACT_SLICES_PATH):
        start = row.get("start")
        end = row.get("end")
        assert isinstance(start, int) and start >= 0, (
            f"selected slice start must be a non-negative integer: {row}"
        )
        assert isinstance(end, int) and end > start, (
            f"selected slice end must be greater than start: {row}"
        )
        assert end - start <= MAX_SELECTED_SLICE_CHARS, (
            f"selected slice range is not bounded to {MAX_SELECTED_SLICE_CHARS} chars: {row}"
        )
        assert isinstance(row.get("artifact_id"), str) and row["artifact_id"], (
            f"selected slice must cite an artifact id: {row}"
        )
        assert isinstance(row.get("key"), str) and row["key"], (
            f"selected slice must cite an artifact key: {row}"
        )
        assert isinstance(row.get("purpose"), str) and row["purpose"].endswith(
            "_evidence_slice"
        ), f"selected slice must record a stable evidence purpose: {row}"


def test_derived_metrics_match_fixture_rows_and_findings():
    manifest = _load_json(MANIFEST_PATH)
    rows = _load_jsonl_rows()
    findings = _load_findings(rows)
    metrics = _metric_snapshot(manifest)

    expected_counts = {
        "artifact_summary_rows": len(_load_jsonl_file(ARTIFACT_SUMMARIES_PATH)),
        "collector_audit_rows": len(_load_jsonl_file(COLLECTOR_AUDIT_PATH)),
        "event_summary_rows": len(_load_jsonl_file(EVENT_SUMMARIES_PATH)),
        "selected_artifact_slice_rows": len(
            _load_jsonl_file(SELECTED_ARTIFACT_SLICES_PATH)
        ),
    }

    for key, value in expected_counts.items():
        assert metrics.get(key) == value, (
            f"derived metric {key!r} drifted: expected {value!r}, "
            f"found {metrics.get(key)!r}"
        )

    assert metrics.get("prefix_counts") == _prefix_counts(
        _load_jsonl_file(ARTIFACT_SUMMARIES_PATH)
    )

    observed_failure_counts = _failure_class_counts(findings)
    metric_failure_counts = metrics.get("failure_class_counts")
    assert isinstance(metric_failure_counts, dict), (
        "derived metric 'failure_class_counts' must be an object"
    )
    assert REQUIRED_FAILURE_CLASSES <= set(observed_failure_counts)
    assert metric_failure_counts == observed_failure_counts
    assert _evidence_ref_count(findings) >= len(findings)
    assert metrics.get("jsonl_sha256") == _fixture_hash(rows)
