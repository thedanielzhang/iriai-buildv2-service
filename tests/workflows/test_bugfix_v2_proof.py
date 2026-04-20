from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iriai_build_v2.models.outputs import Check, EvidenceArtifact, EvidenceBundle
from iriai_build_v2.workflows.bugfix_v2.proof import (
    bundle_artifact_surfaces,
    bundle_provided_evidence_modes,
    evidence_missing_requirements,
    persist_proof_record,
    snapshot_proof_record,
)


def _feature() -> SimpleNamespace:
    return SimpleNamespace(
        id="bf123456",
        metadata={"dashboard_url": "https://dashboard.example.test/feature/bf123456"},
    )


def test_persist_proof_record_rejects_artifacts_outside_context_root(tmp_path: Path):
    context_root = tmp_path / "repos"
    context_root.mkdir(parents=True, exist_ok=True)
    inside_file = context_root / "trace.zip"
    inside_file.write_text("trace", encoding="utf-8")
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")

    record = persist_proof_record(
        feature=_feature(),
        feature_proof_root=tmp_path / "proof",
        report_id="BR-1",
        stage="validate",
        context_root=context_root,
        bundle=EvidenceBundle(
            summary="validation",
            artifacts=[
                EvidenceArtifact(kind="trace", label="inside", local_path=str(inside_file)),
                EvidenceArtifact(kind="logs", label="outside", local_path=str(outside_file)),
            ],
        ),
    )

    assert record.bundle.artifacts[0].public_url.endswith(".zip")
    assert record.bundle.artifacts[1].local_path == ""
    assert record.bundle.artifacts[1].public_url == ""


def test_snapshot_proof_record_materializes_terminal_copy(tmp_path: Path):
    context_root = tmp_path / "repos"
    context_root.mkdir(parents=True, exist_ok=True)
    screenshot = context_root / "shot.png"
    screenshot.write_text("png", encoding="utf-8")
    proof_root = tmp_path / "proof"

    source = persist_proof_record(
        feature=_feature(),
        feature_proof_root=proof_root,
        report_id="BR-2",
        stage="validate",
        context_root=context_root,
        bundle=EvidenceBundle(
            summary="validate proof",
            artifacts=[EvidenceArtifact(kind="screenshot", label="shot", local_path=str(screenshot))],
        ),
        checks=[
            Check(
                criterion="evidence:command_output",
                result="satisfied",
                detail="Attached command log.",
            )
        ],
    )

    terminal = snapshot_proof_record(
        feature=_feature(),
        feature_proof_root=proof_root,
        source=source,
        stage="terminal",
    )

    assert "/validate-" in source.bundle_url
    assert "/terminal-" in terminal.bundle_url
    assert "/terminal-" in terminal.bundle.artifacts[0].public_url
    assert Path(terminal.bundle.artifacts[0].local_path).exists()
    assert "terminal-" in Path(terminal.bundle.artifacts[0].local_path).parts[-2]
    assert terminal.checks == source.checks


def test_canonical_proof_surfaces_accept_live_alias_kinds():
    bundle = EvidenceBundle(
        ui_involved=True,
        evidence_modes=["ui", "api", "database", "logs", "repo"],
        summary="promotion proof",
        state_change=True,
        artifacts=[
            EvidenceArtifact(kind="trace", label="trace"),
            EvidenceArtifact(kind="screenshot", label="shot"),
            EvidenceArtifact(kind="api_response", role="postcondition", label="api"),
            EvidenceArtifact(kind="database_query", role="verification", label="db"),
            EvidenceArtifact(kind="network_log", role="verification", label="network"),
            EvidenceArtifact(kind="repo_excerpt", role="verification", label="repo"),
            EvidenceArtifact(kind="command_output", role="verification", label="command"),
            EvidenceArtifact(kind="snapshot", role="verification", label="snapshot"),
            EvidenceArtifact(kind="ui_state", role="verification", label="ui-state"),
        ],
    )

    surfaces = bundle_artifact_surfaces(bundle)
    provided = bundle_provided_evidence_modes(bundle)
    missing = evidence_missing_requirements(
        required_modes=["ui", "api", "database", "logs", "repo"],
        bundle=bundle,
        require_ui_proof=True,
        state_change=True,
    )

    assert set(surfaces) >= {"trace", "screenshot", "api", "database", "logs", "repo", "command-output", "snapshot", "ui-state"}
    assert set(provided) == {"ui", "api", "database", "logs", "repo"}
    assert missing == []


def test_generic_file_artifacts_do_not_satisfy_repo_proof_by_default():
    bundle = EvidenceBundle(
        summary="generic attachments",
        artifacts=[
            EvidenceArtifact(kind="file", label="artifact.txt"),
            EvidenceArtifact(kind="static", label="diagram.yaml"),
            EvidenceArtifact(kind="source", label="source.txt"),
            EvidenceArtifact(kind="yaml", label="config.yaml"),
        ],
    )

    surfaces = bundle_artifact_surfaces(bundle)
    provided = bundle_provided_evidence_modes(bundle)

    assert "repo" not in surfaces
    assert "repo" not in provided


def test_ambiguous_text_artifacts_need_strong_repo_hints():
    weak_bundle = EvidenceBundle(
        summary="weak hints",
        artifacts=[EvidenceArtifact(kind="text", label="artifact.txt", source="other")],
    )
    strong_bundle = EvidenceBundle(
        summary="strong hints",
        artifacts=[EvidenceArtifact(kind="text", label="diff excerpt", source="git diff")],
    )

    weak_surfaces = bundle_artifact_surfaces(weak_bundle)
    strong_surfaces = bundle_artifact_surfaces(strong_bundle)

    assert "repo" not in weak_surfaces
    assert "repo" in strong_surfaces
