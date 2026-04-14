from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iriai_build_v2.models.outputs import EvidenceArtifact, EvidenceBundle
from iriai_build_v2.workflows.bugfix_v2.proof import persist_proof_record, snapshot_proof_record


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
