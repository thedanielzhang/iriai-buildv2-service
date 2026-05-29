from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.workflows.develop.execution import dispatcher as dispatcher_module
from iriai_build_v2.workflows.develop.execution.dispatcher import (
    ActorMetadata,
    DispatchAttemptRecord,
    DispatchIdempotencyConflict,
    DispatchOutcome,
    DispatchRequest,
    DispatchRetryIdentity,
    PatchCaptureRecord,
    PromptBuildResult,
    PromptContextBundle,
    RuntimeDispatcher,
    RuntimeFailureRecord,
    RuntimeInvocationRequest,
    RuntimeInvocationResponse,
    RuntimeWorkspaceBinding,
    StructuredOutputRecord,
    actor_metadata_digest,
    dispatch_idempotency_key,
    dispatch_request_digest,
    validate_dispatch_transition,
)


def _actor_metadata() -> ActorMetadata:
    data = {
        "actor_id": "actor-1",
        "actor_name": "Implementer",
        "actor_role": "implementer",
        "runtime": "codex",
        "runtime_policy": "standard",
        "runtime_policy_digest": "policy-sha",
        "model": "gpt-test",
        "tool_profile": "sandboxed-tools",
        "sandbox_required": True,
        "approval_profile": "no_canonical_writes",
        "metadata_digest": "pending",
    }
    data["metadata_digest"] = actor_metadata_digest(data)
    return ActorMetadata(**data)


def _request(**overrides: object) -> DispatchRequest:
    actor = _actor_metadata()
    data: dict[str, object] = {
        "feature_id": "feature-1",
        "dag_sha256": "dag-sha",
        "group_idx": 2,
        "task_id": "TASK-1",
        "task_name": "Implement task",
        "retry": 0,
        "retry_identity": DispatchRetryIdentity(
            retry=0,
            dispatch_retry_id="dispatch-retry-1",
        ),
        "contract_ids": [11],
        "sandbox_id": "sandbox-1",
        "workspace_snapshot_ids": [21],
        "base_commit_by_repo": {"repo": "abc123"},
        "runtime_policy": actor.runtime_policy,
        "runtime_policy_digest": actor.runtime_policy_digest,
        "actor_role": actor.actor_role,
        "actor_metadata": actor,
        "prior_evidence_ids": [31],
        "cancellation_token": None,
        "request_digest": "pending",
        "idempotency_key": "pending",
    }
    data.update(overrides)
    data["request_digest"] = dispatch_request_digest(data)
    data["idempotency_key"] = dispatch_idempotency_key(data)
    return DispatchRequest.model_validate(data)


def _bundle() -> PromptContextBundle:
    return PromptContextBundle(
        prompt_ref=41,
        prompt_sha256="prompt-sha",
        prompt_summary="bounded prompt",
        context_file_refs=[42],
        context_file_paths=["context/TASK-1.md"],
        context_sha256="context-sha",
        included_contract_ids=[11],
        included_evidence_ids=[31],
        excluded_evidence_ids=[],
        truncation_notes=[],
    )


def _binding() -> RuntimeWorkspaceBinding:
    return RuntimeWorkspaceBinding(
        sandbox_id="sandbox-1",
        runtime="codex",
        cwd="/tmp/sandbox-1/repo",
        workspace_override="/tmp/sandbox-1/repo",
        repo_roots={"repo": "/tmp/sandbox-1/repo"},
        writable_roots=["/tmp/sandbox-1/repo"],
        readonly_roots=[],
        blocked_roots=["/tmp/canonical"],
        env={},
        role_metadata={"sandbox": True},
    )


def _success_response(invocation: RuntimeInvocationRequest) -> RuntimeInvocationResponse:
    return RuntimeInvocationResponse(
        invocation_id=invocation.invocation_id,
        status="completed",
        terminal_reason="completed",
        process_started=True,
        structured_output={
            "task_id": "TASK-1",
            "status": "completed",
            "summary": "done",
            "files": [],
        },
        raw_text='{"status":"completed"}',
        raw_artifact_id=61,
        provider_request_id="provider-1",
        provider_error_code=None,
        elapsed_ms=15,
    )


def _failure_response(invocation: RuntimeInvocationRequest) -> RuntimeInvocationResponse:
    return RuntimeInvocationResponse(
        invocation_id=invocation.invocation_id,
        status="failed",
        terminal_reason="provider_error",
        process_started=False,
        structured_output=None,
        raw_text="provider unavailable",
        raw_artifact_id=62,
        provider_request_id="provider-err",
        provider_error_code="provider_500",
        elapsed_ms=19,
    )


class FakeStore:
    def __init__(
        self,
        log: list[str],
        *,
        terminal_outcome: DispatchOutcome | None = None,
        terminal_outcome_needs_finish: bool = False,
        late_recovery_outcome: DispatchOutcome | None = None,
        conflict: bool = False,
        duplicate_state: dispatcher_module.DispatcherState | None = None,
        duplicate_request_digest: str | None = None,
        duplicate_replay_recovery_evidence: object | None = None,
    ) -> None:
        self.log = log
        self.terminal_outcome = terminal_outcome
        self.terminal_outcome_needs_finish = terminal_outcome_needs_finish
        self.late_recovery_outcome = late_recovery_outcome
        self.conflict = conflict
        self.duplicate_state = duplicate_state
        self.duplicate_request_digest = duplicate_request_digest
        self.duplicate_replay_recovery_evidence = duplicate_replay_recovery_evidence
        self.failures: list[RuntimeFailureRecord] = []
        self.projections: list[tuple[StructuredOutputRecord, PatchCaptureRecord]] = []
        self.finished: list[DispatchOutcome] = []
        self.prompt_contexts: list[PromptContextBundle] = []

    async def start_dispatch_attempt(self, request: DispatchRequest) -> DispatchAttemptRecord:
        self.log.append("start")
        if self.conflict:
            raise DispatchIdempotencyConflict(
                request.idempotency_key,
                "stored-digest",
                request.request_digest,
            )
        return DispatchAttemptRecord(
            attempt_id=101,
            state=self.duplicate_state or "attempt_started",
            request_digest=self.duplicate_request_digest or request.request_digest,
            created=self.terminal_outcome is None and self.duplicate_state is None,
            terminal_outcome=self.terminal_outcome,
            terminal_outcome_needs_finish=self.terminal_outcome_needs_finish,
            duplicate_replay_recovery_evidence=(
                self.duplicate_replay_recovery_evidence
            ),
        )

    async def record_start_idempotency_conflict(
        self,
        request: DispatchRequest,
        failure: RuntimeFailureRecord,
    ) -> tuple[int, RuntimeFailureRecord]:
        del request
        self.log.append("record_start_conflict")
        stored = failure.model_copy(update={"failure_id": 501})
        self.failures.append(stored)
        return 101, stored

    async def record_prompt_context(
        self,
        attempt_id: int,
        request: DispatchRequest,
        prompt: str,
        bundle: PromptContextBundle,
    ) -> int:
        del attempt_id, request, prompt
        self.log.append("record_prompt")
        self.prompt_contexts.append(bundle)
        return 201

    async def record_runtime_invocation(
        self,
        attempt_id: int,
        invocation: RuntimeInvocationRequest,
        response: RuntimeInvocationResponse | None = None,
    ) -> int:
        del attempt_id, invocation
        self.log.append("record_invocation_response" if response else "record_invocation")
        return 202

    async def record_raw_output(
        self,
        attempt_id: int,
        invocation: RuntimeInvocationRequest,
        response: RuntimeInvocationResponse,
    ) -> int | None:
        del attempt_id, invocation, response
        self.log.append("record_raw_output")
        return 203

    async def record_structured_output(
        self,
        attempt_id: int,
        record: StructuredOutputRecord,
    ) -> StructuredOutputRecord:
        del attempt_id
        self.log.append("record_structured")
        return record.model_copy(update={"evidence_id": 301})

    async def record_runtime_failure(
        self,
        attempt_id: int,
        failure: RuntimeFailureRecord,
    ) -> RuntimeFailureRecord:
        del attempt_id
        self.log.append("record_failure")
        stored = failure.model_copy(update={"failure_id": 501})
        self.failures.append(stored)
        return stored

    async def project_task_result_from_attempt(
        self,
        attempt_id: int,
        request: DispatchRequest,
        structured_output: StructuredOutputRecord,
        patch_capture: PatchCaptureRecord,
    ) -> list[int]:
        del attempt_id, request
        assert structured_output.evidence_id == 301
        assert patch_capture.captured is True
        self.log.append("project_task")
        self.projections.append((structured_output, patch_capture))
        return [401]

    async def finish_dispatch_attempt(self, outcome: DispatchOutcome) -> DispatchOutcome:
        if outcome.status == "succeeded":
            structured = SimpleNamespace(evidence_id=outcome.structured_result_evidence_id)
            patch_capture = PatchCaptureRecord(
                sandbox_id="sandbox-1",
                patch_summary_ids=list(outcome.patch_summary_ids),
            )
            self.log.append("project_task")
            self.projections.append((structured, patch_capture))  # type: ignore[arg-type]
            outcome = outcome.model_copy(update={"compatibility_artifact_ids": [401]})
        self.log.append(f"finish:{outcome.status}")
        self.finished.append(outcome)
        return outcome

    async def recover_late_runtime_completion(
        self,
        attempt: DispatchAttemptRecord,
        request: DispatchRequest,
        *,
        invocation_id: str,
        output_schema: dict[str, object],
        output_schema_digest: str,
        output_type_name: str,
    ) -> DispatchOutcome | None:
        del attempt, request, invocation_id, output_schema, output_schema_digest, output_type_name
        self.log.append("recover_late_runtime_completion")
        return self.late_recovery_outcome

    async def reserve_route_budget(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("dispatcher must not reserve route budget")

    async def create_repair_request(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("dispatcher must not create repair requests")

    async def project_group_checkpoint(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("dispatcher must not project group checkpoints")


class FakePromptBuilder:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.calls = 0

    async def build_prompt_context(self, request: DispatchRequest) -> PromptBuildResult:
        del request
        self.calls += 1
        self.log.append("prompt")
        return PromptBuildResult(prompt="Do the bounded task.", bundle=_bundle())


class FakeProviderPayloadPromptBuilder(FakePromptBuilder):
    async def build_prompt_context(self, request: DispatchRequest) -> PromptBuildResult:
        del request
        self.calls += 1
        self.log.append("prompt")
        bundle_data = _bundle().model_dump(mode="json")
        bundle_data["provider_records"] = [{"body": "must not enter bundle"}]
        return PromptBuildResult(
            prompt="Do the bounded task.",
            bundle=PromptContextBundle.model_validate(bundle_data),
        )


class FakeSandbox:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.bind_calls = 0
        self.capture_calls = 0
        self.capture_idempotency_keys: list[str] = []
        self.capture_responses: list[RuntimeInvocationResponse] = []

    async def bind_runtime(
        self,
        request: DispatchRequest,
        attempt_id: int,
    ) -> RuntimeWorkspaceBinding:
        del request, attempt_id
        self.bind_calls += 1
        self.log.append("bind")
        return _binding()

    async def capture_patch(
        self,
        request: DispatchRequest,
        attempt_id: int,
        binding: RuntimeWorkspaceBinding,
        response: RuntimeInvocationResponse,
        *,
        idempotency_key: str,
    ) -> PatchCaptureRecord:
        del request, attempt_id, binding
        self.capture_calls += 1
        self.capture_idempotency_keys.append(idempotency_key)
        self.capture_responses.append(response)
        self.log.append("capture_patch")
        return PatchCaptureRecord(
            sandbox_id="sandbox-1",
            captured=True,
            patch_summary_ids=[701],
            compatibility_artifact_ids=[],
            empty=False,
        )


class FakeRuntime:
    def __init__(self, log: list[str], *, mode: str = "success") -> None:
        self.log = log
        self.mode = mode
        self.calls = 0
        self.invocations: list[RuntimeInvocationRequest] = []

    async def invoke(self, request: RuntimeInvocationRequest) -> RuntimeInvocationResponse:
        self.calls += 1
        self.invocations.append(request)
        self.log.append("runtime")
        if self.mode == "failure":
            return _failure_response(request)
        return _success_response(request)


def _dispatcher(
    log: list[str],
    *,
    store: FakeStore | None = None,
    runtime: FakeRuntime | None = None,
    prompt_builder: FakePromptBuilder | None = None,
    sandbox: FakeSandbox | None = None,
    context_layer_builder: object | None = None,
) -> RuntimeDispatcher:
    return RuntimeDispatcher(
        store=store or FakeStore(log),
        sandbox=sandbox or FakeSandbox(log),
        runtime=runtime or FakeRuntime(log),
        prompt_builder=prompt_builder or FakePromptBuilder(log),
        output_schema_digest="schema-sha",
        context_layer_builder=context_layer_builder,
    )


class FakeContextLayerBuilder:
    async def build_context_package(
        self,
        *,
        request: DispatchRequest,
        binding: RuntimeWorkspaceBinding,
        prompt_result: PromptBuildResult,
    ) -> SimpleNamespace:
        assert binding.sandbox_id == "sandbox-1"
        assert prompt_result.bundle.context_sha256 == "context-sha"
        return SimpleNamespace(
            package_id="ctxpkg-1",
            package_digest="ctxpkg-digest",
            review_ref="context-package://ctxpkg-1",
            package_kind="dispatcher_prompt_context",
            completeness="complete",
            source_dag_artifact_id=501,
            dag_sha256=request.dag_sha256,
            request=SimpleNamespace(
                feature_id=request.feature_id,
                task_id=request.task_id,
            ),
            evidence_snapshot=SimpleNamespace(
                typed_evidence_digest="typed-evidence-digest"
            ),
            provider_state_digest="provider-state-digest",
            advisory_only=True,
            provider_records=[{"body": "must not enter PromptContextBundle"}],
            provider_state_refs=[{"id": "provider-state"}],
            rendered_preview="provider body",
        )


class FakeMalformedContextLayerBuilder(FakeContextLayerBuilder):
    async def build_context_package(
        self,
        *,
        request: DispatchRequest,
        binding: RuntimeWorkspaceBinding,
        prompt_result: PromptBuildResult,
    ) -> SimpleNamespace:
        package = await super().build_context_package(
            request=request,
            binding=binding,
            prompt_result=prompt_result,
        )
        return SimpleNamespace(
            **{
                **package.__dict__,
                "feature_id": request.feature_id,
                "request": SimpleNamespace(
                    feature_id="other-feature",
                    task_id=request.task_id,
                ),
            }
        )


class FakeCompletenessContextLayerBuilder(FakeContextLayerBuilder):
    def __init__(self, *, completeness: str, page_refs: list[object] | None = None) -> None:
        self._completeness = completeness
        self._page_refs = [] if page_refs is None else page_refs

    async def build_context_package(
        self,
        *,
        request: DispatchRequest,
        binding: RuntimeWorkspaceBinding,
        prompt_result: PromptBuildResult,
    ) -> SimpleNamespace:
        package = await super().build_context_package(
            request=request,
            binding=binding,
            prompt_result=prompt_result,
        )
        return SimpleNamespace(
            **{
                **package.__dict__,
                "completeness": self._completeness,
                "page_refs": self._page_refs,
            }
        )


@pytest.mark.asyncio
async def test_dispatch_starts_attempt_before_side_effects() -> None:
    log: list[str] = []
    outcome = await _dispatcher(log).dispatch(_request())

    assert outcome.status == "succeeded"
    assert log[0] == "start"
    assert log.index("start") < log.index("prompt")
    assert log.index("start") < log.index("bind")
    assert log.index("start") < log.index("runtime")


@pytest.mark.asyncio
async def test_dispatcher_records_context_layer_identity_without_provider_payloads() -> None:
    log: list[str] = []
    store = FakeStore(log)
    runtime = FakeRuntime(log)

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=runtime,
        prompt_builder=FakeProviderPayloadPromptBuilder(log),
        context_layer_builder=FakeContextLayerBuilder(),
    ).dispatch(_request())

    assert outcome.status == "succeeded"
    bundle = store.prompt_contexts[0]
    assert bundle.context_package_id == "ctxpkg-1"
    assert bundle.context_package_digest == "ctxpkg-digest"
    assert bundle.context_package_ref == "context-package://ctxpkg-1"
    assert bundle.context_package_completeness == "complete"
    assert bundle.context_package_feature_id == "feature-1"
    assert bundle.context_package_task_id == "TASK-1"
    assert bundle.context_package_provider_state_digest == "provider-state-digest"
    dumped = bundle.model_dump(mode="json")
    assert "provider_records" not in dumped
    assert "provider_state_refs" not in dumped
    assert "rendered_preview" not in dumped
    assert runtime.invocations[0].metadata["context_package_id"] == "ctxpkg-1"
    assert runtime.invocations[0].metadata["context_package_digest"] == "ctxpkg-digest"
    assert runtime.invocations[0].metadata["context_package_completeness"] == "complete"


@pytest.mark.asyncio
async def test_dispatcher_rejects_malformed_context_layer_identity_before_runtime() -> None:
    log: list[str] = []
    store = FakeStore(log)
    runtime = FakeRuntime(log)

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=runtime,
        context_layer_builder=FakeMalformedContextLayerBuilder(),
    ).dispatch(_request())

    assert outcome.status == "failed"
    assert runtime.calls == 0
    assert store.prompt_contexts == []
    assert store.failures[0].failure_class == "runtime_context"
    assert store.failures[0].failure_type == "context_materialization_failed"
    assert "context layer package identity is malformed" in store.failures[0].details["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("completeness", ["preview_only", "unavailable", "paged"])
async def test_dispatcher_rejects_non_executable_context_layer_completeness_before_runtime(
    completeness: str,
) -> None:
    log: list[str] = []
    store = FakeStore(log)
    runtime = FakeRuntime(log)

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=runtime,
        context_layer_builder=FakeCompletenessContextLayerBuilder(
            completeness=completeness,
        ),
    ).dispatch(_request())

    assert outcome.status == "failed"
    assert runtime.calls == 0
    assert store.prompt_contexts == []
    assert store.failures[0].failure_type == "context_materialization_failed"
    assert "context_package_completeness" in store.failures[0].details["message"]


@pytest.mark.asyncio
async def test_dispatcher_passes_paged_context_layer_refs_to_runtime_metadata() -> None:
    log: list[str] = []
    store = FakeStore(log)
    runtime = FakeRuntime(log)
    page_ref = {
        "ref_id": "review:context-package:ctxpkg-1:page:0:abc",
        "digest": "page-digest",
        "completeness": "complete",
        "preview_only": False,
        "provider_records": [{"body": "must not enter metadata"}],
        "rendered_preview": "must not enter metadata",
    }

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=runtime,
        context_layer_builder=FakeCompletenessContextLayerBuilder(
            completeness="paged",
            page_refs=[page_ref],
        ),
    ).dispatch(_request())

    assert outcome.status == "succeeded"
    bundle = store.prompt_contexts[0]
    assert bundle.context_package_completeness == "paged"
    assert bundle.context_package_page_refs[0]["ref_id"] == page_ref["ref_id"]
    assert "provider_records" not in bundle.context_package_page_refs[0]
    assert "rendered_preview" not in bundle.context_package_page_refs[0]
    assert runtime.invocations[0].metadata["context_package_completeness"] == "paged"
    runtime_page_ref = runtime.invocations[0].metadata["context_package_page_refs"][0]
    assert runtime_page_ref["digest"] == "page-digest"
    assert "provider_records" not in runtime_page_ref
    assert "rendered_preview" not in runtime_page_ref


@pytest.mark.asyncio
async def test_dispatcher_rejects_malformed_paged_context_layer_refs_before_runtime() -> None:
    log: list[str] = []
    store = FakeStore(log)
    runtime = FakeRuntime(log)

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=runtime,
        context_layer_builder=FakeCompletenessContextLayerBuilder(
            completeness="paged",
            page_refs=[{"ref_id": "page-without-digest"}],
        ),
    ).dispatch(_request())

    assert outcome.status == "failed"
    assert runtime.calls == 0
    assert store.prompt_contexts == []
    assert "page refs require ref_id and digest" in store.failures[0].details["message"]


@pytest.mark.asyncio
async def test_dispatcher_rejects_preview_only_paged_context_layer_refs_before_runtime() -> None:
    log: list[str] = []
    store = FakeStore(log)
    runtime = FakeRuntime(log)

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=runtime,
        context_layer_builder=FakeCompletenessContextLayerBuilder(
            completeness="paged",
            page_refs=[
                {
                    "ref_id": "review:context-package:ctxpkg-1:page:0:preview",
                    "digest": "page-digest",
                    "completeness": "complete",
                    "preview_only": "true",
                }
            ],
        ),
    ).dispatch(_request())

    assert outcome.status == "failed"
    assert runtime.calls == 0
    assert store.prompt_contexts == []
    assert "must not be preview_only" in store.failures[0].details["message"]


@pytest.mark.asyncio
async def test_dispatch_accepts_duck_typed_request_values() -> None:
    log: list[str] = []
    duck_request = SimpleNamespace(**_request().model_dump(mode="json"))

    outcome = await _dispatcher(log).dispatch(duck_request)

    assert outcome.status == "succeeded"
    assert log[0] == "start"


def test_dispatch_state_machine_rejects_invalid_transition() -> None:
    with pytest.raises(dispatcher_module.DispatchStateTransitionError):
        validate_dispatch_transition("runtime_invoking", "succeeded")


@pytest.mark.asyncio
async def test_duplicate_terminal_dispatch_returns_stored_outcome_without_runtime_call() -> None:
    log: list[str] = []
    terminal = DispatchOutcome(
        attempt_id=55,
        state="succeeded",
        status="succeeded",
        runtime_terminal_reason="completed",
        structured_result_evidence_id=301,
        raw_text_ref=61,
        patch_summary_ids=[701],
        compatibility_artifact_ids=[401],
        runtime_failure_id=None,
        typed_failure_id=None,
        idempotency_key=_request().idempotency_key,
    )
    runtime = FakeRuntime(log)
    prompt_builder = FakePromptBuilder(log)
    sandbox = FakeSandbox(log)
    store = FakeStore(log, terminal_outcome=terminal)

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=runtime,
        prompt_builder=prompt_builder,
        sandbox=sandbox,
    ).dispatch(_request())

    assert outcome == terminal
    assert log == ["start"]
    assert runtime.calls == 0
    assert prompt_builder.calls == 0
    assert sandbox.bind_calls == 0
    assert sandbox.capture_calls == 0


@pytest.mark.asyncio
async def test_duplicate_terminal_dispatch_needing_finish_uses_finish_path() -> None:
    log: list[str] = []
    terminal = DispatchOutcome(
        attempt_id=55,
        state="succeeded",
        status="succeeded",
        runtime_terminal_reason="completed",
        structured_result_evidence_id=301,
        raw_text_ref=61,
        patch_summary_ids=[701],
        compatibility_artifact_ids=[],
        runtime_failure_id=None,
        typed_failure_id=None,
        idempotency_key=_request().idempotency_key,
    )
    runtime = FakeRuntime(log)
    prompt_builder = FakePromptBuilder(log)
    sandbox = FakeSandbox(log)
    store = FakeStore(
        log,
        terminal_outcome=terminal,
        terminal_outcome_needs_finish=True,
    )

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=runtime,
        prompt_builder=prompt_builder,
        sandbox=sandbox,
    ).dispatch(_request())

    assert outcome.status == "succeeded"
    assert outcome.compatibility_artifact_ids == [401]
    assert store.finished == [outcome]
    assert log == ["start", "project_task", "finish:succeeded"]
    assert runtime.calls == 0
    assert prompt_builder.calls == 0
    assert sandbox.bind_calls == 0
    assert sandbox.capture_calls == 0


@pytest.mark.asyncio
async def test_terminal_timeout_attempt_can_recover_late_runtime_completion() -> None:
    log: list[str] = []
    request = _request()
    terminal = DispatchOutcome(
        attempt_id=55,
        state="failed",
        status="failed",
        runtime_terminal_reason="timeout",
        structured_result_evidence_id=None,
        raw_text_ref=None,
        patch_summary_ids=[],
        compatibility_artifact_ids=[],
        runtime_failure_id=501,
        typed_failure_id=501,
        idempotency_key=request.idempotency_key,
    )
    recovered = DispatchOutcome(
        attempt_id=55,
        state="succeeded",
        status="succeeded",
        runtime_terminal_reason="completed",
        structured_result_evidence_id=301,
        raw_text_ref=203,
        patch_summary_ids=[701],
        compatibility_artifact_ids=[401],
        runtime_failure_id=None,
        typed_failure_id=None,
        idempotency_key=request.idempotency_key,
    )
    runtime = FakeRuntime(log)
    prompt_builder = FakePromptBuilder(log)
    sandbox = FakeSandbox(log)
    store = FakeStore(log, terminal_outcome=terminal, late_recovery_outcome=recovered)

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=runtime,
        prompt_builder=prompt_builder,
        sandbox=sandbox,
    ).dispatch(request)

    assert outcome == recovered
    assert log == ["start", "recover_late_runtime_completion"]
    assert runtime.calls == 0
    assert prompt_builder.calls == 0
    assert sandbox.bind_calls == 0
    assert sandbox.capture_calls == 0


@pytest.mark.asyncio
async def test_same_key_different_digest_conflict_fails_before_side_effects() -> None:
    log: list[str] = []
    runtime = FakeRuntime(log)
    prompt_builder = FakePromptBuilder(log)
    sandbox = FakeSandbox(log)
    store = FakeStore(log, conflict=True)

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=runtime,
        prompt_builder=prompt_builder,
        sandbox=sandbox,
    ).dispatch(_request())

    assert outcome.status == "failed"
    assert outcome.runtime_failure_id == 501
    assert outcome.typed_failure_id == 501
    assert outcome.attempt_id == 101
    assert store.failures[0].failure_class == "dispatcher_internal"
    assert store.failures[0].failure_type == "idempotency_conflict"
    assert store.failures[0].retryable is False
    assert log == ["start", "record_start_conflict"]
    assert runtime.calls == 0
    assert prompt_builder.calls == 0
    assert sandbox.bind_calls == 0


@pytest.mark.asyncio
async def test_duplicate_attempt_with_different_digest_preserves_conflict_behavior() -> None:
    log: list[str] = []
    runtime = FakeRuntime(log)
    prompt_builder = FakePromptBuilder(log)
    sandbox = FakeSandbox(log)
    store = FakeStore(
        log,
        duplicate_state="context_prepared",
        duplicate_request_digest="stored-different-digest",
    )

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=runtime,
        prompt_builder=prompt_builder,
        sandbox=sandbox,
    ).dispatch(_request())

    assert outcome.status == "failed"
    assert outcome.runtime_failure_id == 501
    assert outcome.typed_failure_id == 501
    assert store.failures[0].failure_class == "dispatcher_internal"
    assert store.failures[0].failure_type == "idempotency_conflict"
    assert store.failures[0].retryable is False
    assert store.failures[0].operator_required is False
    assert store.failures[0].details["existing_digest"] == "stored-different-digest"
    assert log == ["start", "record_start_conflict"]
    assert runtime.calls == 0
    assert prompt_builder.calls == 0
    assert sandbox.bind_calls == 0


@pytest.mark.asyncio
async def test_success_requires_patch_and_structured_evidence_before_projection() -> None:
    log: list[str] = []
    store = FakeStore(log)

    outcome = await _dispatcher(log, store=store).dispatch(_request())

    assert outcome.status == "succeeded"
    assert outcome.structured_result_evidence_id == 301
    assert outcome.raw_text_ref == 203
    assert outcome.patch_summary_ids == [701]
    assert outcome.compatibility_artifact_ids == [401]
    assert store.projections
    assert log.index("capture_patch") < log.index("record_structured")
    assert log.index("record_raw_output") < log.index("record_structured")
    assert log.index("record_structured") < log.index("finish:succeeded")
    assert log.index("project_task") < log.index("finish:succeeded")


@pytest.mark.asyncio
async def test_duplicate_attempt_started_replays_and_finishes_attempt() -> None:
    log: list[str] = []
    runtime = FakeRuntime(log)
    prompt_builder = FakePromptBuilder(log)
    sandbox = FakeSandbox(log)
    store = FakeStore(log, duplicate_state="attempt_started")

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=runtime,
        prompt_builder=prompt_builder,
        sandbox=sandbox,
    ).dispatch(_request())

    assert outcome.status == "succeeded"
    assert outcome.structured_result_evidence_id == 301
    assert outcome.compatibility_artifact_ids == [401]
    assert store.finished == [outcome]
    assert not [
        failure
        for failure in store.failures
        if failure.failure_type == "idempotency_conflict"
    ]
    assert "finish:succeeded" in log
    assert runtime.calls == 1
    assert prompt_builder.calls == 1
    assert sandbox.bind_calls == 1
    assert sandbox.capture_calls == 1


@pytest.mark.parametrize(
    "duplicate_state",
    [
        "context_prepared",
        "runtime_invoking",
        "runtime_returned",
        "patch_capturing",
        "output_normalizing",
        "evidence_recording",
    ],
)
@pytest.mark.asyncio
async def test_live_duplicate_nonterminal_after_start_defers_recovery_without_finishing(
    duplicate_state: dispatcher_module.DispatcherState,
) -> None:
    log: list[str] = []
    runtime = FakeRuntime(log)
    prompt_builder = FakePromptBuilder(log)
    sandbox = FakeSandbox(log)
    store = FakeStore(log, duplicate_state=duplicate_state)

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=runtime,
        prompt_builder=prompt_builder,
        sandbox=sandbox,
    ).dispatch(_request())

    assert outcome.status == "incomplete"
    assert outcome.runtime_failure_id is None
    assert outcome.typed_failure_id is None
    assert outcome.recovery_decision is not None
    assert outcome.recovery_decision.failure_class == "dispatcher_internal"
    assert (
        outcome.recovery_decision.failure_type
        == "nonterminal_replay_requires_recovery"
    )
    assert outcome.recovery_decision.operator_required is False
    assert outcome.recovery_decision.retryable is True
    assert outcome.recovery_decision.details["stored_state"] == duplicate_state
    assert outcome.recovery_decision.details["recovery_evidence"] == {
        "durable_crash_recovery_proof": False,
        "required": (
            "durable stale-owner, stale-heartbeat, or recovery evidence "
            "that the original runtime crashed"
        ),
    }
    assert outcome.recovery_decision.details["patch_capture"] == {
        "attempted": False,
        "captured": False,
        "capturable": (
            duplicate_state
            in dispatcher_module._DUPLICATE_NONTERMINAL_PATCH_CAPTURABLE_STATES
        ),
        "diagnostic_only": True,
        "reason": "requires_durable_crash_recovery_evidence",
        "stored_state": duplicate_state,
    }
    assert store.failures == []
    assert store.finished == []
    assert log == ["start"]
    assert runtime.calls == 0
    assert prompt_builder.calls == 0
    assert sandbox.bind_calls == 0
    assert sandbox.capture_calls == 0


@pytest.mark.asyncio
async def test_duplicate_crash_claim_without_durable_evidence_does_not_finish() -> None:
    log: list[str] = []
    runtime = FakeRuntime(log)
    sandbox = FakeSandbox(log)
    store = FakeStore(
        log,
        duplicate_state="runtime_invoking",
        duplicate_replay_recovery_evidence={"runtime_crashed": True},
    )

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=runtime,
        sandbox=sandbox,
    ).dispatch(_request())

    assert outcome.status == "incomplete"
    assert outcome.recovery_decision is not None
    assert outcome.recovery_decision.details["recovery_evidence"][
        "durable_crash_recovery_proof"
    ] is False
    assert store.failures == []
    assert store.finished == []
    assert runtime.calls == 0
    assert sandbox.bind_calls == 0
    assert sandbox.capture_calls == 0
    assert log == ["start"]


@pytest.mark.asyncio
async def test_duplicate_runtime_invoking_retry_finishes_after_durable_crash_recovery() -> None:
    log: list[str] = []
    runtime = FakeRuntime(log)
    sandbox = FakeSandbox(log)
    recovery_evidence = {
        "evidence_id": 8801,
        "owner_stale": True,
        "heartbeat_stale": True,
        "runtime_crashed": True,
        "recovery_reason": "stale heartbeat after runtime crash",
    }
    store = FakeStore(
        log,
        duplicate_state="runtime_invoking",
        duplicate_replay_recovery_evidence=recovery_evidence,
    )
    request = _request()

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=runtime,
        sandbox=sandbox,
    ).dispatch(request)

    assert outcome.status == "incomplete"
    assert outcome.patch_summary_ids == [701]
    assert outcome.runtime_failure_id == 501
    assert store.finished == [outcome]
    assert runtime.calls == 0
    assert sandbox.capture_calls == 1
    assert sandbox.capture_idempotency_keys == [
        dispatcher_module.DefaultIdempotencyKeyFactory().patch_capture_key(request, 101)
    ]
    assert outcome.recovery_decision == store.failures[0]
    assert store.failures[0].deterministic is True
    assert store.failures[0].evidence_ids == [701]
    assert store.failures[0].details["patch_capture"]["captured"] is True
    assert store.failures[0].details["recovery_evidence"] == {
        **recovery_evidence,
        "durable_crash_recovery_proof": True,
    }
    assert log == [
        "start",
        "bind",
        "capture_patch",
        "record_failure",
        "finish:incomplete",
    ]

    second_log: list[str] = []
    second_store = FakeStore(
        second_log,
        duplicate_state="runtime_invoking",
        duplicate_replay_recovery_evidence=recovery_evidence,
    )
    second_sandbox = FakeSandbox(second_log)
    await _dispatcher(
        second_log,
        store=second_store,
        runtime=FakeRuntime(second_log),
        sandbox=second_sandbox,
    ).dispatch(request)

    assert second_store.failures[0].signature_hash == store.failures[0].signature_hash


@pytest.mark.asyncio
async def test_failure_outcome_returns_typed_failure_with_retry_route_without_repair_calls() -> None:
    log: list[str] = []
    store = FakeStore(log)
    runtime = FakeRuntime(log, mode="failure")

    outcome = await _dispatcher(log, store=store, runtime=runtime).dispatch(_request())

    assert outcome.status == "failed"
    assert outcome.runtime_terminal_reason == "provider_error"
    assert outcome.runtime_failure_id == 501
    assert outcome.typed_failure_id == 501
    assert store.failures[0].failure_class == "runtime_provider"
    assert store.failures[0].retryable is True
    assert store.failures[0].details["route"] == "retry_dispatch"
    assert store.failures[0].details["route_decision"]["route"] == "retry_dispatch"
    assert store.failures[0].details["retry_budget"]["route"] == "retry_dispatch"
    assert store.failures[0].details["retry_budget"]["max_retries"] == 3
    assert store.failures[0].details["retry_budget"]["remaining_attempts"] == 2
    assert (
        store.failures[0].details["route_decision"]["retry_budget"]["max_retries"]
        == 3
    )
    assert "project_task" not in log
    assert "capture_patch" not in log


@pytest.mark.asyncio
async def test_failure_outcome_preserves_explicit_zero_retry_budget() -> None:
    log: list[str] = []
    store = FakeStore(log)
    runtime = FakeRuntime(log, mode="failure")

    outcome = await _dispatcher(log, store=store, runtime=runtime).dispatch(
        _request(
            retry_identity=DispatchRetryIdentity(
                retry=0,
                dispatch_retry_id="dispatch-retry-zero",
                max_retries=0,
            )
        )
    )

    assert outcome.status == "failed"
    retry_budget = store.failures[0].details["retry_budget"]
    assert retry_budget["max_retries"] == 0
    assert retry_budget["max_attempts"] == 0
    assert retry_budget["remaining_attempts"] == 0


@pytest.mark.asyncio
async def test_failure_outcome_uses_max_attempts_retry_limit_when_retries_missing() -> None:
    log: list[str] = []
    store = FakeStore(log)
    runtime = FakeRuntime(log, mode="failure")

    outcome = await _dispatcher(log, store=store, runtime=runtime).dispatch(
        _request(
            retry=2,
            retry_identity=DispatchRetryIdentity(
                retry=2,
                dispatch_retry_id="dispatch-retry-max-attempts",
                max_attempts=2,
            ),
        )
    )

    assert outcome.status == "failed"
    retry_budget = store.failures[0].details["retry_budget"]
    assert retry_budget["max_retries"] == 2
    assert retry_budget["max_attempts"] == 2
    assert retry_budget["remaining_attempts"] == 0


@pytest.mark.asyncio
async def test_numeric_provider_rate_limit_is_canonicalized() -> None:
    log: list[str] = []
    store = FakeStore(log)

    class RateLimitRuntime(FakeRuntime):
        async def invoke(self, request: RuntimeInvocationRequest) -> RuntimeInvocationResponse:
            response = await super().invoke(request)
            return response.model_copy(update={"provider_error_code": "429"})

    outcome = await _dispatcher(
        log,
        store=store,
        runtime=RateLimitRuntime(log, mode="failure"),
    ).dispatch(_request())

    assert outcome.status == "failed"
    assert store.failures[0].failure_type == "provider_rate_limited"


def test_dispatcher_source_and_port_shape_exclude_forbidden_authority() -> None:
    source_path = Path(dispatcher_module.__file__).resolve()
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_import_parts = {
        "implementation",
        "merge_queue",
        "failure_router",
        "repair",
        "checkpoint",
        "commit_queue",
    }
    assert not {
        module
        for module in imported_modules
        for forbidden in forbidden_import_parts
        if forbidden in module
    }

    port_methods = {
        name
        for name, value in dispatcher_module.DispatchJournalPort.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    forbidden_method_parts = {
        "checkpoint",
        "commit",
        "merge",
        "route",
        "repair",
        "queue",
        "regroup",
    }
    assert not {
        method
        for method in port_methods
        for forbidden in forbidden_method_parts
        if forbidden in method
    }
