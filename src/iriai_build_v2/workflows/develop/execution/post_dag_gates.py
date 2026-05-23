"""Pure post-DAG-gate proof primitives for the develop workflow.

This module is the canonical home for the PURE post-DAG-gate-specific
primitives used by the feature-level business gates that run after
effective DAG completion (code review, security, test authoring, QA,
integration, final verifier, source push, implementation report,
backlog report, and completion notification) inside
``ImplementationPhase.execute``. Per
``docs/execution-control-plane/11-refactor-map.md`` § "Boundary-level
API contracts" row for ``execution/post_dag_gates.py``
("``PostDagGateService.run``, ``resume``, ``record_gate_result``,
``assert_feature_ready_for_observation``. Feature-level code review,
security, test authoring, QA, integration, final verifier, source
push, implementation report, backlog report, and completion
notification orchestration. Must not own: Group dispatch, sandbox
patch generation, merge queue internals, root DAG mutation."), this
module owns the pure proof-payload builders + proof-digest hashers +
prior-proof matchers + proof-records self-consistency validators +
implementation-report metadata builder + notify-delivery ID hasher,
each stdlib-only (plus ``EnhancementBacklog`` for the implementation-
report metadata builder).

The module is CREATED by Slice 11l (no pre-existing surface to
preserve). Mirrors the Slice 11a ``types.py`` CREATE pattern. Each
helper here was moved byte-for-byte from
``workflows/develop/phases/implementation.py``; bodies preserved
exactly, no logic / signature / hash-input / dict-shape /
artifact-key / artifact-schema change.

This module must NOT import from ``workflows.develop.phases.
implementation`` (compatibility flows point IN, never OUT — locked by
a back-import guard test). Modules ARE allowed to depend on
``..models.outputs`` for the ``EnhancementBacklog`` type contract.

Every public name here is re-exported from
``workflows/develop/phases/implementation.py`` via a shim import, so
every existing ``from iriai_build_v2.workflows.develop.phases.
implementation import X`` keeps resolving to the same object after
the Slice-11l extraction (the doc-11 § "How To Use This Map" four-
question contract).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from iriai_compose import Feature

from ....models.outputs import EnhancementBacklog


__all__ = [
    "_finalize_source_push_proof",
    "_implementation_report_metadata",
    "_notify_delivery_id",
    "_source_push_base_proof",
    "_source_push_prior_proof_matches",
    "_source_push_proof_digest",
    "_source_push_proof_key",
    "_source_push_proof_records_are_self_consistent",
]


# --- Implementation-report metadata builder -----------------------------------


def _implementation_report_metadata(
    *,
    tree_digest: str,
    report_url: str,
    backlog_url: str,
    backlog: "EnhancementBacklog",
    report_body_sha256: str = "",
    publish_status: str = "complete",
) -> dict[str, Any]:
    return {
        "artifact_schema": "implementation-report-metadata-v1",
        "tree_digest": tree_digest,
        "report_url": report_url,
        "backlog_url": backlog_url,
        "backlog_items": [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in backlog.items
        ],
        "report_body_sha256": report_body_sha256,
        "publish_status": publish_status,
    }


# --- Notify-delivery ID hasher ------------------------------------------------


def _notify_delivery_id(feature: Feature, tree_digest: str, notification: str) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "feature_id": str(getattr(feature, "id", "")),
                "tree_digest": tree_digest,
                "notification_sha256": hashlib.sha256(
                    notification.encode("utf-8")
                ).hexdigest(),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


# --- Source-push proof primitives ---------------------------------------------


def _source_push_proof_key() -> str:
    return "dag-source-push-proof"


def _source_push_base_proof(
    prior: dict[str, Any] | None,
    *,
    repos_root: Path,
    tree_digest: str,
    expected_origins: dict[str, str] | None,
) -> dict[str, Any]:
    payload = dict(prior or {})
    if payload:
        proof_digest = str(payload.get("proof_digest") or "")
        if not proof_digest or proof_digest != _source_push_proof_digest(payload):
            payload = {}
    if str(payload.get("tree_digest") or "") != tree_digest:
        payload = {}
    payload["artifact_schema"] = "dag-source-push-proof-v1"
    payload["tree_digest"] = tree_digest
    payload["repos_root"] = str(repos_root.resolve(strict=False))
    payload["expected_origins"] = dict(sorted((expected_origins or {}).items()))
    repos = payload.get("repos")
    payload["repos"] = dict(repos) if isinstance(repos, dict) else {}
    return payload


def _source_push_proof_digest(payload: dict[str, Any]) -> str:
    material = {
        key: value
        for key, value in payload.items()
        if key not in {"proof_digest"}
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _finalize_source_push_proof(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["proof_digest"] = _source_push_proof_digest(result)
    return result


def _source_push_prior_proof_matches(
    prior_record: Any,
    *,
    repo: str,
    tree_digest: str,
    branch: str,
    local_head: str,
    remote_ref: str,
    expected_origin: str,
    actual_origin: str,
) -> bool:
    if not isinstance(prior_record, dict):
        return False
    status = str(prior_record.get("status") or "")
    if status not in {"intent", "pushed", "recovered"}:
        return False
    if str(prior_record.get("repo") or "") != repo:
        return False
    if str(prior_record.get("tree_digest") or "") != tree_digest:
        return False
    if str(prior_record.get("branch") or "") != branch:
        return False
    if str(prior_record.get("local_head") or "") != local_head:
        return False
    if str(prior_record.get("remote_ref") or "") != remote_ref:
        return False
    if str(prior_record.get("expected_origin") or "") != expected_origin:
        return False
    if str(prior_record.get("actual_origin") or "") != actual_origin:
        return False
    if str(prior_record.get("remote_before") or "") == local_head:
        return False
    if status == "intent":
        return bool(prior_record.get("remote_before")) and not prior_record.get("remote_after")
    return str(prior_record.get("remote_after") or "") == local_head


def _source_push_proof_records_are_self_consistent(
    proof: dict[str, Any],
    tree_digest: str,
) -> bool:
    repos = proof.get("repos")
    if str(proof.get("tree_digest") or "") != tree_digest:
        return False
    if not isinstance(repos, dict) or not repos:
        return False
    for repo, record in repos.items():
        if not isinstance(record, dict):
            return False
        if str(record.get("repo") or "") != str(repo):
            return False
        if str(record.get("tree_digest") or "") != tree_digest:
            return False
        status = str(record.get("status") or "")
        if status not in {"pushed", "recovered", "unchanged"}:
            return False
        local_head = str(record.get("local_head") or "")
        remote_after = str(record.get("remote_after") or "")
        if status in {"pushed", "recovered"}:
            if not local_head or remote_after != local_head:
                return False
            if not str(record.get("branch") or ""):
                return False
            if not str(record.get("remote_ref") or ""):
                return False
        if status == "unchanged":
            if record.get("mutation_required") is not False:
                return False
            if not local_head or remote_after != local_head:
                return False
            if not str(record.get("branch") or ""):
                return False
            if not str(record.get("remote_ref") or ""):
                return False
    return True
