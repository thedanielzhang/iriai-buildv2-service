from __future__ import annotations

from types import SimpleNamespace

import pytest

from iriai_build_v2.execution_control.models import (
    ExecutionJournalRow,
    PromptContextEvidence,
)
from iriai_build_v2.execution_control.store import _prompt_context_fields
from iriai_build_v2.models.outputs import (
    ImplementationResult,
    ImplementationTask,
    TaskFileScope,
)
from iriai_build_v2.workflows.develop.phases import implementation as implementation_module


def test_prompt_context_fields_persist_context_package_identity_only() -> None:
    row = ExecutionJournalRow(
        id=101,
        feature_id="feature-1",
        idempotency_key="dispatch-idem",
        entry_type="dispatch_attempt",
        status="started",
        request_digest="request-digest",
        payload={},
        group_idx=1,
        task_id="TASK-1",
    )
    fields = _prompt_context_fields(
        PromptContextEvidence(
            attempt_id=101,
            prompt_ref=201,
            prompt_sha256="prompt-sha",
            context_sha256="context-sha",
            context_package_id="ctxpkg-1",
            context_package_digest="ctxpkg-digest",
            context_package_ref="context-package://ctxpkg-1",
            context_package_kind="dispatcher_prompt_context",
            context_package_completeness="complete",
            context_package_page_refs=[
                {
                    "ref_id": "review:context-package:ctxpkg-1:page:0:abc",
                    "digest": "page-digest",
                    "preview_only": "false",
                    "provider_records": [{"body": "drop"}],
                    "rendered_preview": "drop",
                }
            ],
            context_package_feature_id="feature-1",
            context_package_task_id="TASK-1",
            context_package_source_dag_artifact_id=501,
            context_package_dag_sha256="dag-sha",
            context_package_evidence_snapshot_digest="typed-evidence-digest",
            context_package_provider_state_digest="provider-state-digest",
            context_package_advisory_only=True,
            payload={
                "context_package_id": "payload-spoof",
                "safe_note": "kept",
                "provider_records": [{"body": "drop"}],
                "provider_state_refs": [{"id": "drop"}],
                "nested": {"rendered_preview": "drop", "kept": "yes"},
            },
            metadata={"page_refs": [{"id": "drop"}], "kept": "yes"},
        ),
        row,
    )

    payload = fields["payload"]
    assert payload["context_package_id"] == "ctxpkg-1"
    assert payload["context_package_digest"] == "ctxpkg-digest"
    assert payload["context_package_completeness"] == "complete"
    assert payload["context_package_page_refs"][0]["ref_id"].endswith(":abc")
    assert payload["context_package_page_refs"][0]["preview_only"] is False
    assert "provider_records" not in payload["context_package_page_refs"][0]
    assert "rendered_preview" not in payload["context_package_page_refs"][0]
    assert payload["context_package_provider_state_digest"] == "provider-state-digest"
    assert payload["safe_note"] == "kept"
    assert payload["nested"] == {"kept": "yes"}
    assert "provider_records" not in payload
    assert "provider_state_refs" not in payload
    assert fields["metadata"] == {"kept": "yes"}
    assert fields["content_hash"] != "context-sha"


@pytest.mark.asyncio
async def test_production_task_dispatch_requests_context_package(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeRuntimeDispatcher:
        def __init__(self, **kwargs):
            captured["dispatcher_kwargs"] = kwargs

        async def dispatch(self, request):
            builder = captured["dispatcher_kwargs"]["context_layer_builder"]
            package = await builder.build_dispatch_context_package(
                request=request,
                binding=None,
                prompt_result=None,
            )
            captured["package"] = package
            return implementation_module.DispatcherOutcome(
                attempt_id=88,
                state="succeeded",
                status="succeeded",
                runtime_terminal_reason="completed",
                structured_result_evidence_id=301,
                raw_text_ref=901,
                patch_summary_ids=[701],
                compatibility_artifact_ids=[401],
                runtime_failure_id=None,
                typed_failure_id=None,
                idempotency_key=request.idempotency_key,
            )

    class FakeNativeGitProvider:
        name = "native_git"

        def __init__(self, repos):
            captured["provider_repos"] = repos

    class FakeContextLayerService:
        def __init__(self, providers, *, repos, lineage_plugin=None, page_store=None):
            captured["service_providers"] = providers
            captured["service_repos"] = repos
            captured["lineage_plugin"] = lineage_plugin
            captured["page_store"] = page_store

        async def build_context_package(self, request):
            captured["context_request"] = request
            return SimpleNamespace(
                package_id="ctxpkg-prod",
                package_digest="ctxpkg-prod-digest",
                package_kind="manifest",
                completeness="complete",
                request=request,
                source_dag_artifact_id=request.source_dag_artifact_id,
                dag_sha256=request.dag_sha256,
                evidence_snapshot=request.evidence_snapshot,
                provider_state_digest="provider-state-digest",
                advisory_only=True,
                review_ref="review:context-package:ctxpkg-prod",
            )

    class FakeArtifacts:
        async def get(self, key, *, feature=None):
            if key == "dag-task:TASK-prod":
                return ImplementationResult(
                    task_id="TASK-prod",
                    summary="done",
                    status="completed",
                ).model_dump_json()
            return None

    monkeypatch.setattr(implementation_module, "RuntimeDispatcher", FakeRuntimeDispatcher)
    monkeypatch.setattr(
        implementation_module,
        "ContextLayerNativeGitProvider",
        FakeNativeGitProvider,
    )
    monkeypatch.setattr(
        implementation_module,
        "ContextLayerService",
        FakeContextLayerService,
    )

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    task = ImplementationTask(
        id="TASK-prod",
        name="prod",
        description="prod",
        repo_path="repo",
        file_scope=[TaskFileScope(path="repo/src/app.py", action="modify")],
    )
    contract = SimpleNamespace(
        id=11,
        repo_id="repo",
        source_dag_artifact_id=21,
    )
    runner = SimpleNamespace(
        artifacts=FakeArtifacts(),
        services={},
        agent_runtime=SimpleNamespace(name="codex"),
    )

    result, outcome = await implementation_module._dispatch_task_attempt_via_runtime_dispatcher(
        runner=runner,
        feature=SimpleNamespace(id="feature-prod"),
        workspace_root=tmp_path,
        feature_root=tmp_path,
        dag_sha256="d" * 64,
        group_idx=3,
        task_idx=4,
        attempt=0,
        task=task,
        task_contract=contract,
        ws_path=str(repo_root),
        snapshots=[],
        runtime_hint="codex",
        runtime_policy=implementation_module.DEFAULT_RUNTIME_POLICY,
        repo_prefix="repo",
        inline_prompt="",
        handover_context="",
        stage="implementation",
        actor_suffix="prod",
        log_label="Prod",
    )

    context_request = captured["context_request"]
    assert outcome.status == "succeeded"
    assert result.task_id == "TASK-prod"
    assert captured["dispatcher_kwargs"]["context_layer_builder"] is not None
    assert context_request.feature_id == "feature-prod"
    assert context_request.task_id == "TASK-prod"
    assert context_request.source_dag_artifact_id == 21
    assert context_request.evidence_snapshot.typed_evidence_digest
    assert context_request.spans[0].path == "src/app.py"
    assert context_request.require_complete is True
    assert captured["page_store"] is not None
    assert captured["page_store"].durable is True
    assert captured["package"].package_id == "ctxpkg-prod"
