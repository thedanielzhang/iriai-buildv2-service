"""Pure dispatcher/runtime boundary for implementation task attempts.

The dispatcher owns one bounded side effect: turning a task runtime request into
durable attempt evidence through injected ports.  It deliberately does not
import the workflow monolith, merge/checkpoint code, repair routing, or concrete
runtime adapters.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from iriai_compose import AgentActor

from ....runtime_policy import (
    PRIMARY_IMPL_SECONDARY_REVIEW_POLICY,
    RuntimePolicy,
)


DispatcherState = Literal[
    "requested",
    "attempt_started",
    "context_prepared",
    "runtime_invoking",
    "runtime_returned",
    "patch_capturing",
    "output_normalizing",
    "evidence_recording",
    "succeeded",
    "failed",
    "cancelled",
    "incomplete",
]

RuntimeName = Literal["claude", "codex", "claude_pool"]

RuntimeTerminalReason = Literal[
    "completed",
    "cancelled",
    "provider_error",
    "timeout",
    "watchdog_stall",
    "process_failed",
    "prompt_too_large",
    "context_materialization_failed",
    "structured_output_invalid",
    "sandbox_binding_failed",
    "patch_capture_failed",
]

DispatchStatus = Literal["succeeded", "failed", "cancelled", "incomplete"]

RuntimeFailureClass = Literal[
    "runtime_provider",
    "runtime_timeout",
    "runtime_cancelled",
    "runtime_context",
    "runtime_structured_output",
    "sandbox_binding",
    "sandbox_capture",
    "dispatcher_internal",
]

DEFAULT_PROVIDER_RETRY_LIMIT = 3


class DispatcherError(RuntimeError):
    """Base exception for dispatcher boundary failures."""


class DispatchStateTransitionError(DispatcherError):
    """Raised when a dispatch state transition violates the state machine."""


class DispatchIdempotencyConflict(DispatcherError):
    """Raised by the store port when one idempotency key has two digests."""

    def __init__(
        self,
        idempotency_key: str,
        existing_digest: str,
        requested_digest: str,
    ) -> None:
        super().__init__(
            "dispatch idempotency key was reused with a different request digest"
        )
        self.idempotency_key = idempotency_key
        self.existing_digest = existing_digest
        self.requested_digest = requested_digest


class _DispatcherModel(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        arbitrary_types_allowed=True,
        from_attributes=True,
    )


class ActorMetadata(_DispatcherModel):
    actor_id: str
    actor_name: str
    actor_role: str
    runtime: RuntimeName
    runtime_policy: str
    runtime_policy_digest: str
    model: str | None = None
    tool_profile: str
    sandbox_required: bool = True
    approval_profile: str = "no_canonical_writes"
    metadata_digest: str

    @field_validator(
        "actor_id",
        "actor_name",
        "actor_role",
        "runtime_policy",
        "runtime_policy_digest",
        "tool_profile",
        "approval_profile",
        "metadata_digest",
    )
    @classmethod
    def _non_empty_string(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value cannot be empty")
        return value

    @model_validator(mode="after")
    def _requires_sandbox(self) -> "ActorMetadata":
        if not self.sandbox_required:
            raise ValueError("dispatcher runtime actors must require a sandbox")
        return self


class DispatchRetryIdentity(_DispatcherModel):
    retry: int
    dispatch_retry_id: str
    retry_of_attempt_id: int | None = None
    failure_retry_of_id: int | None = None
    route_decision_id: int | None = None
    route_request_id: int | None = None

    @field_validator("retry")
    @classmethod
    def _retry_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("retry cannot be negative")
        return value

    @field_validator("dispatch_retry_id")
    @classmethod
    def _retry_id_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("dispatch_retry_id cannot be empty")
        return value


class DispatchRequest(_DispatcherModel):
    feature_id: str
    dag_sha256: str
    group_idx: int
    task_id: str
    task_name: str
    retry: int
    retry_identity: DispatchRetryIdentity
    contract_ids: list[int]
    sandbox_id: str
    workspace_snapshot_ids: list[int]
    base_commit_by_repo: dict[str, str]
    runtime_policy: str
    runtime_policy_digest: str
    actor_role: str
    actor_metadata: ActorMetadata
    prior_evidence_ids: list[int]
    cancellation_token: str | None = None
    request_digest: str
    idempotency_key: str

    @field_validator(
        "feature_id",
        "dag_sha256",
        "task_id",
        "task_name",
        "sandbox_id",
        "runtime_policy",
        "runtime_policy_digest",
        "actor_role",
        "request_digest",
        "idempotency_key",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value cannot be empty")
        return value

    @field_validator("retry", "group_idx")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("value cannot be negative")
        return value

    @model_validator(mode="after")
    def _stable_request_invariants(self) -> "DispatchRequest":
        if self.retry != self.retry_identity.retry:
            raise ValueError("retry and retry_identity.retry must match")
        if not self.contract_ids:
            raise ValueError("contract_ids cannot be empty")
        if len(set(self.contract_ids)) != len(self.contract_ids):
            raise ValueError("contract_ids must be unique")
        if self.actor_role != self.actor_metadata.actor_role:
            raise ValueError("actor_role must match actor_metadata.actor_role")
        if self.runtime_policy_digest != self.actor_metadata.runtime_policy_digest:
            raise ValueError(
                "runtime_policy_digest must match actor_metadata.runtime_policy_digest"
            )
        return self


class PromptContextBundle(_DispatcherModel):
    prompt_ref: int
    prompt_sha256: str
    prompt_summary: str
    context_file_refs: list[int]
    context_file_paths: list[str]
    context_sha256: str
    included_contract_ids: list[int]
    included_evidence_ids: list[int]
    excluded_evidence_ids: list[int]
    truncation_notes: list[str]


class RuntimeWorkspaceBinding(_DispatcherModel):
    sandbox_id: str
    runtime: RuntimeName
    cwd: str
    workspace_override: str
    repo_roots: dict[str, str]
    writable_roots: list[str]
    readonly_roots: list[str] = Field(default_factory=list)
    blocked_roots: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    role_metadata: dict[str, Any] = Field(default_factory=dict)
    sandbox_lease_id: int | None = None
    manifest_path: str | None = None
    expires_at: str | None = None
    binding_digest: str | None = None


class RuntimeInvocationRequest(_DispatcherModel):
    attempt_id: int
    invocation_id: str
    runtime: RuntimeName
    actor_name: str
    actor_role: str
    actor_metadata: ActorMetadata
    workspace_binding: RuntimeWorkspaceBinding
    prompt: str
    prompt_ref: int
    output_schema: str
    output_schema_digest: str
    output_type_name: str
    timeout_seconds: int
    retry_within_invocation: bool = True
    cancellation_token: str | None = None
    metadata: dict[str, Any]

    @field_validator("attempt_id", "timeout_seconds")
    @classmethod
    def _positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be positive")
        return value


class RuntimeInvocationResponse(_DispatcherModel):
    invocation_id: str
    status: Literal["completed", "failed", "cancelled"]
    terminal_reason: RuntimeTerminalReason
    process_started: bool = False
    structured_output: dict[str, Any] | None
    raw_text: str | None
    raw_text_ref: int | None = None
    raw_artifact_id: int | None
    provider_request_id: str | None
    provider_error_code: str | None
    stdout_artifact_id: int | None = None
    stderr_artifact_id: int | None = None
    adapter_retry_ids: list[str] = Field(default_factory=list)
    adapter_retry_count: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: int

    @field_validator("elapsed_ms", "adapter_retry_count")
    @classmethod
    def _non_negative_int(cls, value: int) -> int:
        if value < 0:
            raise ValueError("value cannot be negative")
        return value

    @model_validator(mode="after")
    def _status_reason_coherence(self) -> "RuntimeInvocationResponse":
        if self.status == "completed" and self.terminal_reason != "completed":
            raise ValueError("completed responses must use terminal_reason='completed'")
        if self.status != "completed" and self.terminal_reason == "completed":
            raise ValueError("non-completed responses cannot use terminal_reason='completed'")
        if self.status == "cancelled" and self.terminal_reason != "cancelled":
            raise ValueError("cancelled responses must use terminal_reason='cancelled'")
        return self


class StructuredOutputRecord(_DispatcherModel):
    evidence_id: int
    schema_name: str
    schema_digest: str
    valid: bool
    original_payload: dict[str, Any] | None
    normalized_payload: dict[str, Any] | None
    validation_errors: list[str]
    corrected_fields: dict[str, Any]
    task_id_matches_request: bool


class RuntimeFailureRecord(_DispatcherModel):
    failure_id: int
    failure_class: RuntimeFailureClass
    failure_type: str
    retryable: bool
    deterministic: bool
    operator_required: bool
    provider_request_id: str | None
    provider_error_code: str | None = None
    runtime: RuntimeName | str = ""
    terminal_reason: RuntimeTerminalReason | None = None
    evidence_ids: list[int]
    signature_hash: str
    summary: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class DispatchOutcome(_DispatcherModel):
    attempt_id: int
    state: DispatcherState
    status: DispatchStatus
    runtime_terminal_reason: RuntimeTerminalReason | None
    structured_result_evidence_id: int | None
    raw_text_ref: int | None
    patch_summary_ids: list[int]
    compatibility_artifact_ids: list[int]
    runtime_failure_id: int | None
    typed_failure_id: int | None
    idempotency_key: str
    recovery_decision: RuntimeFailureRecord | None = None

    @model_validator(mode="after")
    def _terminal_invariants(self) -> "DispatchOutcome":
        if self.state != self.status:
            raise ValueError("terminal outcome state must match status")
        if self.status == "succeeded":
            if self.structured_result_evidence_id is None:
                raise ValueError("successful dispatch requires structured output evidence")
            if self.runtime_failure_id is not None or self.typed_failure_id is not None:
                raise ValueError("successful dispatch cannot carry failure ids")
        if self.status == "failed" and self.typed_failure_id is None:
            raise ValueError("failed dispatch requires a typed failure id")
        return self


class PromptBuildResult(_DispatcherModel):
    prompt: str
    bundle: PromptContextBundle


class PatchCaptureRecord(_DispatcherModel):
    sandbox_id: str
    captured: bool = True
    patch_summary_ids: list[int] = Field(default_factory=list)
    compatibility_artifact_ids: list[int] = Field(default_factory=list)
    empty: bool = False
    diagnostic_only: bool = False
    failure_type: str | None = None
    failure_message: str | None = None


class DispatchAttemptRecord(_DispatcherModel):
    attempt_id: int
    state: DispatcherState = "attempt_started"
    request_digest: str
    created: bool = True
    terminal_outcome: DispatchOutcome | None = None
    terminal_outcome_needs_finish: bool = False
    duplicate_replay_recovery_evidence: Any = None


class DispatchJournalPort(Protocol):
    """Narrow persistence facade; intentionally excludes route/merge authority."""

    async def start_dispatch_attempt(
        self,
        request: DispatchRequest,
    ) -> DispatchAttemptRecord: ...

    async def record_start_idempotency_conflict(
        self,
        request: DispatchRequest,
        failure: RuntimeFailureRecord,
    ) -> tuple[int, RuntimeFailureRecord]: ...

    async def record_prompt_context(
        self,
        attempt_id: int,
        request: DispatchRequest,
        prompt: str,
        bundle: PromptContextBundle,
    ) -> int: ...

    async def record_runtime_invocation(
        self,
        attempt_id: int,
        invocation: RuntimeInvocationRequest,
        response: RuntimeInvocationResponse | None = None,
    ) -> int: ...

    async def record_raw_output(
        self,
        attempt_id: int,
        invocation: RuntimeInvocationRequest,
        response: RuntimeInvocationResponse,
    ) -> int | None: ...

    async def record_structured_output(
        self,
        attempt_id: int,
        record: StructuredOutputRecord,
    ) -> StructuredOutputRecord: ...

    async def record_runtime_failure(
        self,
        attempt_id: int,
        failure: RuntimeFailureRecord,
    ) -> RuntimeFailureRecord: ...

    async def project_task_result_from_attempt(
        self,
        attempt_id: int,
        request: DispatchRequest,
        structured_output: StructuredOutputRecord,
        patch_capture: PatchCaptureRecord,
    ) -> list[int]: ...

    async def finish_dispatch_attempt(
        self,
        outcome: DispatchOutcome,
    ) -> DispatchOutcome: ...


class ExecutionControlStore(DispatchJournalPort, Protocol):
    """Alias protocol for the dispatch-only execution-control store facade."""


class SandboxRunnerPort(Protocol):
    async def bind_runtime(
        self,
        request: DispatchRequest,
        attempt_id: int,
    ) -> RuntimeWorkspaceBinding | Mapping[str, Any] | Any: ...

    async def capture_patch(
        self,
        request: DispatchRequest,
        attempt_id: int,
        binding: RuntimeWorkspaceBinding,
        response: RuntimeInvocationResponse,
        *,
        idempotency_key: str,
    ) -> PatchCaptureRecord | Mapping[str, Any] | Any: ...


class RuntimeClientPort(Protocol):
    async def invoke(
        self,
        request: RuntimeInvocationRequest,
    ) -> RuntimeInvocationResponse: ...


class ContractPromptBuilderPort(Protocol):
    async def build_prompt_context(
        self,
        request: DispatchRequest,
    ) -> PromptBuildResult | Mapping[str, Any] | Any: ...


class StructuredOutputNormalizerPort(Protocol):
    def normalize(
        self,
        *,
        request: DispatchRequest,
        response: RuntimeInvocationResponse,
        schema_name: str,
        schema_digest: str,
        patch_capture: PatchCaptureRecord,
    ) -> StructuredOutputRecord: ...


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class IdempotencyKeyFactoryPort(Protocol):
    def invocation_id(self, request: DispatchRequest, attempt_id: int) -> str: ...

    def patch_capture_key(self, request: DispatchRequest, attempt_id: int) -> str: ...


class CancellationRegistryPort(Protocol):
    def is_cancelled(self, token: str | None) -> bool: ...


_VALID_TRANSITIONS: dict[DispatcherState, frozenset[DispatcherState]] = {
    "requested": frozenset({"attempt_started"}),
    "attempt_started": frozenset({"context_prepared", "failed", "cancelled", "incomplete"}),
    "context_prepared": frozenset({"runtime_invoking", "failed", "cancelled", "incomplete"}),
    "runtime_invoking": frozenset({
        "runtime_returned",
        "patch_capturing",
        "evidence_recording",
        "cancelled",
        "incomplete",
    }),
    "runtime_returned": frozenset({"patch_capturing", "incomplete"}),
    "patch_capturing": frozenset({"output_normalizing", "evidence_recording", "incomplete"}),
    "output_normalizing": frozenset({"evidence_recording", "incomplete"}),
    "evidence_recording": frozenset({"succeeded", "failed", "cancelled", "incomplete"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "incomplete": frozenset(),
}

_DUPLICATE_NONTERMINAL_PATCH_CAPTURABLE_STATES: frozenset[DispatcherState] = frozenset(
    {
        "runtime_invoking",
        "runtime_returned",
        "patch_capturing",
        "output_normalizing",
        "evidence_recording",
    }
)

_DUPLICATE_REPLAY_RECOVERY_FIELDS = (
    "duplicate_replay_recovery_evidence",
    "runtime_recovery_evidence",
    "stale_recovery_evidence",
    "recovery_evidence",
)

_DURABLE_RECOVERY_REF_KEYS = frozenset(
    {
        "artifact_id",
        "artifact_ids",
        "event_id",
        "event_ids",
        "evidence_id",
        "evidence_ids",
        "heartbeat_evidence_id",
        "journal_row_id",
        "lease_version",
        "owner_evidence_id",
        "projection_id",
        "recovery_event_id",
        "recovery_evidence_id",
        "runtime_invocation_evidence_id",
    }
)

_CRASH_RECOVERY_SIGNAL_KEYS = frozenset(
    {
        "crash_recovery",
        "heartbeat_missing",
        "heartbeat_stale",
        "owner_stale",
        "process_crashed",
        "process_dead",
        "recovered_after_crash",
        "recovery_claimed",
        "recovery_started",
        "runtime_crash_detected",
        "runtime_crashed",
        "runtime_recovered",
        "stale_heartbeat",
        "stale_owner",
    }
)

_LIVE_RUNTIME_SIGNAL_KEYS = frozenset(
    {
        "heartbeat_current",
        "heartbeat_fresh",
        "owner_active",
        "process_alive",
        "process_running",
        "runtime_alive",
        "runtime_live",
    }
)


def validate_dispatch_transition(
    from_state: DispatcherState,
    to_state: DispatcherState,
) -> None:
    allowed = _VALID_TRANSITIONS[from_state]
    if to_state not in allowed:
        raise DispatchStateTransitionError(
            f"invalid dispatcher transition: {from_state} -> {to_state}"
        )


def stable_json(value: Any) -> str:
    return json.dumps(_to_jsonable(value), sort_keys=True, separators=(",", ":"), default=str)


def stable_digest(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def actor_metadata_digest(material: ActorMetadata | Mapping[str, Any]) -> str:
    data = _to_jsonable(material)
    if not isinstance(data, dict):
        data = {}
    return stable_digest(
        {
            "actor_name": data.get("actor_name", ""),
            "actor_role": data.get("actor_role", ""),
            "runtime": data.get("runtime", ""),
            "runtime_policy_digest": data.get("runtime_policy_digest", ""),
            "model": data.get("model"),
            "tool_profile": data.get("tool_profile", ""),
            "sandbox_required": bool(data.get("sandbox_required", True)),
            "approval_profile": data.get("approval_profile", "no_canonical_writes"),
        }
    )


def dispatch_request_digest(request: DispatchRequest | Mapping[str, Any]) -> str:
    data = _to_jsonable(request)
    if not isinstance(data, dict):
        raise TypeError("request must be a DispatchRequest or mapping")
    data.pop("request_digest", None)
    data.pop("idempotency_key", None)
    return stable_digest(data)


def dispatch_idempotency_key(request: DispatchRequest | Mapping[str, Any]) -> str:
    data = _to_jsonable(request)
    if not isinstance(data, dict):
        raise TypeError("request must be a DispatchRequest or mapping")
    retry_identity = data.get("retry_identity") or {}
    if not isinstance(retry_identity, dict):
        retry_identity = {}
    material = {
        "feature_id": data.get("feature_id", ""),
        "dag_sha256": data.get("dag_sha256", ""),
        "group_idx": data.get("group_idx"),
        "task_id": data.get("task_id", ""),
        "retry": data.get("retry", 0),
        "actor_role": data.get("actor_role", ""),
        "contract_ids": sorted(data.get("contract_ids") or []),
        "sandbox_id": data.get("sandbox_id", ""),
        "workspace_snapshot_ids": sorted(data.get("workspace_snapshot_ids") or []),
        "base_commit_by_repo": data.get("base_commit_by_repo") or {},
        "runtime_policy_digest": data.get("runtime_policy_digest", ""),
        "prior_evidence_ids": sorted(data.get("prior_evidence_ids") or []),
        "prompt_material_digest": data.get("prompt_material_digest", ""),
        "output_schema_digest": data.get("output_schema_digest", ""),
        "failure_retry_of_id": retry_identity.get("failure_retry_of_id"),
        "route_decision_id": retry_identity.get("route_decision_id"),
        "route_request_id": retry_identity.get("route_request_id"),
    }
    return f"idem:dispatch:{stable_digest(material)}"


class DefaultClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class DefaultIdempotencyKeyFactory:
    def invocation_id(self, request: DispatchRequest, attempt_id: int) -> str:
        return "runtime-invocation:" + stable_digest(
            {
                "attempt_id": attempt_id,
                "dispatch_retry_id": request.retry_identity.dispatch_retry_id,
                "idempotency_key": request.idempotency_key,
                "request_digest": request.request_digest,
            }
        )

    def patch_capture_key(self, request: DispatchRequest, attempt_id: int) -> str:
        return "patch-capture:" + stable_digest(
            {
                "attempt_id": attempt_id,
                "idempotency_key": request.idempotency_key,
                "sandbox_id": request.sandbox_id,
            }
        )


class NullCancellationRegistry:
    def is_cancelled(self, token: str | None) -> bool:
        return False


class DefaultStructuredOutputNormalizer:
    def normalize(
        self,
        *,
        request: DispatchRequest,
        response: RuntimeInvocationResponse,
        schema_name: str,
        schema_digest: str,
        patch_capture: PatchCaptureRecord,
    ) -> StructuredOutputRecord:
        del patch_capture
        payload = response.structured_output
        if payload is None:
            return StructuredOutputRecord(
                evidence_id=0,
                schema_name=schema_name,
                schema_digest=schema_digest,
                valid=False,
                original_payload=None,
                normalized_payload=None,
                validation_errors=["structured_output is missing"],
                corrected_fields={},
                task_id_matches_request=False,
            )

        normalized = dict(payload)
        corrected: dict[str, Any] = {}
        task_id_matches = normalized.get("task_id") == request.task_id
        if not task_id_matches:
            corrected["task_id"] = request.task_id
            normalized["task_id"] = request.task_id

        return StructuredOutputRecord(
            evidence_id=0,
            schema_name=schema_name,
            schema_digest=schema_digest,
            valid=True,
            original_payload=dict(payload),
            normalized_payload=normalized,
            validation_errors=[],
            corrected_fields=corrected,
            task_id_matches_request=task_id_matches,
        )


class RuntimeDispatcher:
    def __init__(
        self,
        *,
        store: DispatchJournalPort,
        sandbox: SandboxRunnerPort,
        runtime: RuntimeClientPort,
        prompt_builder: ContractPromptBuilderPort,
        output_normalizer: StructuredOutputNormalizerPort | None = None,
        clock: ClockPort | None = None,
        key_factory: IdempotencyKeyFactoryPort | None = None,
        cancellation_registry: CancellationRegistryPort | None = None,
        output_schema: str = "ImplementationResult",
        output_schema_digest: str | None = None,
        output_type_name: str = "ImplementationResult",
        timeout_seconds: int = 1_800,
        # Slice 13A fourth sub-slice -- doc-13a:269-272 +
        # auto-memory feedback_no_refactor. The new optional opt-in
        # constructor port is a duck-typed
        # AuthoritativePromptBuilderPort (defined in
        # iriai_build_v2.execution_control.dispatcher_prompt_context).
        # The parameter type is Any | None so the dispatcher module
        # does NOT import the new execution_control module (avoids a
        # circular import: dispatcher_prompt_context.py imports from
        # dispatcher.py). When None (default), _build_prompt falls
        # through to the legacy path BYTE-IDENTICAL (preserves the
        # Slice 05 22-passed baseline + the Slice 00-12 frozen V4
        # spot-check baseline per the chunk shape point 4 invariant).
        authoritative_prompt_builder: Any | None = None,
    ) -> None:
        """Create a dispatcher from narrow, fake-friendly execution ports.

        Required ports are the dispatch journal facade, a sandbox bind/capture
        port, a runtime client port, and a prompt builder port.  All are
        structural protocols; no workflow implementation module is imported.

        Optional ``authoritative_prompt_builder`` (Slice 13A fourth
        sub-slice; doc-13a:269-272): when set, the dispatcher routes
        the prompt/context build through the 13A authoritative
        adapter and projects the typed routing decision onto
        ``runtime_context/context_incomplete`` when the adapter signals
        ``state="unavailable"``. When ``None`` (default), the dispatcher
        falls through to the legacy prompt-builder path BYTE-IDENTICAL
        per the auto-memory ``feedback_no_refactor`` rule.
        """

        self._store = store
        self._sandbox = sandbox
        self._runtime = runtime
        self._prompt_builder = prompt_builder
        self._output_normalizer = output_normalizer or DefaultStructuredOutputNormalizer()
        self._clock = clock or DefaultClock()
        self._key_factory = key_factory or DefaultIdempotencyKeyFactory()
        self._cancellation_registry = cancellation_registry or NullCancellationRegistry()
        self._output_schema = output_schema
        self._output_schema_digest = output_schema_digest or stable_digest(output_schema)
        self._output_type_name = output_type_name
        self._timeout_seconds = timeout_seconds
        # Slice 13A fourth sub-slice -- doc-13a:269-272. Defaults to
        # None so the legacy Slice 05 _build_prompt path is BYTE-
        # IDENTICAL (per auto-memory feedback_no_refactor).
        self._authoritative_prompt_builder = authoritative_prompt_builder

    async def dispatch(self, request: DispatchRequest | Mapping[str, Any] | Any) -> DispatchOutcome:
        request = DispatchRequest.model_validate(request)
        try:
            attempt = await self._store.start_dispatch_attempt(request)
        except DispatchIdempotencyConflict as exc:
            return await self._finish_start_idempotency_conflict(request, exc)
        if attempt.terminal_outcome is not None:
            if attempt.terminal_outcome_needs_finish:
                return await self._store.finish_dispatch_attempt(attempt.terminal_outcome)
            return attempt.terminal_outcome
        if not attempt.created and attempt.request_digest != request.request_digest:
            return await self._finish_start_idempotency_conflict(
                request,
                DispatchIdempotencyConflict(
                    request.idempotency_key,
                    attempt.request_digest,
                    request.request_digest,
                ),
            )

        if not attempt.created and attempt.state != "attempt_started":
            return await self._handle_duplicate_nonterminal_replay(
                attempt=attempt,
                request=request,
            )

        # Same-digest duplicates that never crossed the first durable boundary
        # may replay. Later nonterminal rows require typed recovery so runtime
        # side effects are not duplicated.
        state: DispatcherState = "attempt_started"
        attempt_id = attempt.attempt_id

        if self._cancellation_registry.is_cancelled(request.cancellation_token):
            validate_dispatch_transition(state, "cancelled")
            return await self._finish_cancelled(
                attempt_id=attempt_id,
                request=request,
                reason="cancelled",
            )

        try:
            raw_binding = await self._sandbox.bind_runtime(request, attempt_id)
            binding = _coerce_runtime_binding(raw_binding)
        except Exception as exc:
            return await self._finish_failure(
                attempt_id=attempt_id,
                request=request,
                state=state,
                failure_class="sandbox_binding",
                failure_type="runtime_workspace_binding_failed",
                terminal_reason="sandbox_binding_failed",
                retryable=False,
                deterministic=True,
                operator_required=False,
                provider_request_id=None,
                evidence_ids=[],
                details={"exception": exc.__class__.__name__, "message": str(exc)},
            )

        try:
            prompt_result = await self._build_prompt(request, binding)
            await self._store.record_prompt_context(
                attempt_id,
                request,
                prompt_result.prompt,
                prompt_result.bundle,
            )
            validate_dispatch_transition(state, "context_prepared")
            state = "context_prepared"
        except Exception as exc:
            # Slice 13A fourth sub-slice -- doc-13a:269-272 +
            # auto-memory feedback_no_silent_degradation. The new opt-in
            # AuthoritativePromptContextIncompleteSignal is the typed
            # control-flow marker the 13A wired path raises when the
            # adapter reports state="unavailable"; route it to the
            # typed failure id runtime_context/context_incomplete
            # WITHOUT invoking the runtime. The legacy code path is
            # BYTE-IDENTICAL when the new opt-in port is None (this
            # branch is never entered; the signal class is never
            # imported in that case). The legacy
            # runtime_context/context_materialization_failed route is
            # preserved verbatim for ALL other exceptions.
            #
            # Slice 13A fourth sub-slice finalizer (P3-13A-4-1) --
            # use a true isinstance check via the same local-import
            # pattern that the helper at _build_prompt_authoritatively
            # already uses (see dispatcher.py:1210-1212). The local
            # import scope avoids the module-level circular dependency
            # (the new module imports from this module). The prior
            # `exc.__class__.__name__ == "..."` string comparison was
            # brittle to typos / subclasses / refactor renames and is
            # replaced here with the typed-isinstance form.
            from iriai_build_v2.execution_control.dispatcher_prompt_context import (
                AuthoritativePromptContextIncompleteSignal,
            )
            if isinstance(exc, AuthoritativePromptContextIncompleteSignal):
                routing = getattr(exc, "routing", None)
                legacy_result = getattr(exc, "legacy_result", None)
                # Persist the legacy prompt context BEFORE recording the
                # typed failure; the Slice 05 persistence invariant
                # (record_prompt_context is called before _finish_failure)
                # is preserved per the doc-13a:269-272 wording "dispatch
                # records runtime_context/context_incomplete" -- the
                # legacy bundle is the record. If persistence itself
                # fails the existing legacy except-handler at this same
                # block routes the secondary failure via
                # context_materialization_failed below.
                if legacy_result is not None:
                    try:
                        await self._store.record_prompt_context(
                            attempt_id,
                            request,
                            legacy_result.prompt,
                            legacy_result.bundle,
                        )
                    except Exception as record_exc:
                        return await self._finish_failure(
                            attempt_id=attempt_id,
                            request=request,
                            state=state,
                            failure_class="runtime_context",
                            failure_type="context_materialization_failed",
                            terminal_reason="context_materialization_failed",
                            retryable=True,
                            deterministic=True,
                            operator_required=False,
                            provider_request_id=None,
                            evidence_ids=[],
                            details={
                                "exception": record_exc.__class__.__name__,
                                "message": str(record_exc),
                            },
                        )
                missing_field_names = (
                    list(getattr(routing, "missing_field_names", ()) or ())
                    if routing is not None
                    else []
                )
                unavailable_reason = (
                    getattr(routing, "unavailable_reason", None)
                    if routing is not None
                    else None
                )
                return await self._finish_failure(
                    attempt_id=attempt_id,
                    request=request,
                    state=state,
                    failure_class="runtime_context",
                    failure_type="context_incomplete",
                    # The legacy RuntimeTerminalReason Literal (defined at
                    # dispatcher.py:44-56) does NOT carry a dedicated
                    # context_incomplete value; reuse the closest
                    # semantic match (context_materialization_failed)
                    # so the Slice 05 typed enum remains BYTE-IDENTICAL
                    # per doc-13a:42-46 + 124-126. The typed failure id
                    # (failure_class/failure_type) is the authoritative
                    # routing signal for the Slice 07 typed-failure
                    # router; the terminal_reason is the legacy
                    # observability tag.
                    terminal_reason="context_materialization_failed",
                    retryable=False,
                    deterministic=True,
                    operator_required=False,
                    provider_request_id=None,
                    evidence_ids=[],
                    details={
                        "exception": exc.__class__.__name__,
                        "message": str(exc),
                        "missing_field_names": missing_field_names,
                        "unavailable_reason": unavailable_reason,
                        "slice_13a_typed_failure": True,
                    },
                )
            return await self._finish_failure(
                attempt_id=attempt_id,
                request=request,
                state=state,
                failure_class="runtime_context",
                failure_type="context_materialization_failed",
                terminal_reason="context_materialization_failed",
                retryable=True,
                deterministic=True,
                operator_required=False,
                provider_request_id=None,
                evidence_ids=[],
                details={"exception": exc.__class__.__name__, "message": str(exc)},
            )

        invocation = self._build_invocation_request(
            attempt_id=attempt_id,
            request=request,
            prompt_result=prompt_result,
            binding=binding,
        )
        await self._store.record_runtime_invocation(attempt_id, invocation, None)
        validate_dispatch_transition(state, "runtime_invoking")
        state = "runtime_invoking"

        response = RuntimeInvocationResponse.model_validate(
            await self._runtime.invoke(invocation)
        )
        raw_text_ref = await self._record_raw_output(
            attempt_id=attempt_id,
            invocation=invocation,
            response=response,
        )
        if raw_text_ref is not None:
            response = response.model_copy(update={"raw_text_ref": raw_text_ref})
        await self._store.record_runtime_invocation(attempt_id, invocation, response)

        if response.status == "cancelled":
            validate_dispatch_transition(state, "cancelled")
            return await self._finish_cancelled(
                attempt_id=attempt_id,
                request=request,
                reason=response.terminal_reason,
                raw_text_ref=response.raw_text_ref or response.raw_artifact_id,
            )

        if response.status == "completed":
            validate_dispatch_transition(state, "runtime_returned")
            state = "runtime_returned"
            patch_capture = await self._capture_patch(
                request=request,
                attempt_id=attempt_id,
                binding=binding,
                response=response,
                from_state=state,
            )
            if not patch_capture.captured:
                return await self._finish_incomplete_from_patch_failure(
                    attempt_id=attempt_id,
                    request=request,
                    response=response,
                    patch_capture=patch_capture,
                )

            validate_dispatch_transition("patch_capturing", "output_normalizing")
            state = "output_normalizing"
            structured = self._output_normalizer.normalize(
                request=request,
                response=response,
                schema_name=self._output_type_name,
                schema_digest=self._output_schema_digest,
                patch_capture=patch_capture,
            )
            structured = await self._store.record_structured_output(attempt_id, structured)
            validate_dispatch_transition(state, "evidence_recording")
            state = "evidence_recording"
            if not structured.valid:
                return await self._finish_failure(
                    attempt_id=attempt_id,
                    request=request,
                    state=state,
                    failure_class="runtime_structured_output",
                    failure_type="malformed_structured_output",
                    terminal_reason="structured_output_invalid",
                    retryable=True,
                    deterministic=True,
                    operator_required=False,
                    provider_request_id=response.provider_request_id,
                    evidence_ids=[structured.evidence_id],
                    details={"validation_errors": structured.validation_errors},
                    raw_text_ref=response.raw_text_ref or response.raw_artifact_id,
                )

            validate_dispatch_transition(state, "succeeded")
            outcome = DispatchOutcome(
                attempt_id=attempt_id,
                state="succeeded",
                status="succeeded",
                runtime_terminal_reason=response.terminal_reason,
                structured_result_evidence_id=structured.evidence_id,
                raw_text_ref=response.raw_text_ref or response.raw_artifact_id,
                patch_summary_ids=list(patch_capture.patch_summary_ids),
                compatibility_artifact_ids=list(patch_capture.compatibility_artifact_ids),
                runtime_failure_id=None,
                typed_failure_id=None,
                idempotency_key=request.idempotency_key,
            )
            return await self._store.finish_dispatch_attempt(outcome)

        if response.process_started:
            validate_dispatch_transition(state, "patch_capturing")
            patch_capture = await self._capture_patch(
                request=request,
                attempt_id=attempt_id,
                binding=binding,
                response=response,
                from_state=state,
                diagnostic_only=True,
            )
            if not patch_capture.captured:
                return await self._finish_incomplete_from_patch_failure(
                    attempt_id=attempt_id,
                    request=request,
                    response=response,
                    patch_capture=patch_capture,
                )
            evidence_ids = list(patch_capture.patch_summary_ids)
        else:
            validate_dispatch_transition(state, "evidence_recording")
            evidence_ids = []

        return await self._finish_runtime_response_failure(
            attempt_id=attempt_id,
            request=request,
            response=response,
            evidence_ids=evidence_ids,
            raw_text_ref=response.raw_text_ref or response.raw_artifact_id,
        )

    def _build_invocation_request(
        self,
        *,
        attempt_id: int,
        request: DispatchRequest,
        prompt_result: PromptBuildResult,
        binding: RuntimeWorkspaceBinding,
    ) -> RuntimeInvocationRequest:
        invocation_id = self._key_factory.invocation_id(request, attempt_id)
        return RuntimeInvocationRequest(
            attempt_id=attempt_id,
            invocation_id=invocation_id,
            runtime=request.actor_metadata.runtime,
            actor_name=request.actor_metadata.actor_name,
            actor_role=request.actor_role,
            actor_metadata=request.actor_metadata,
            workspace_binding=binding,
            prompt=prompt_result.prompt,
            prompt_ref=prompt_result.bundle.prompt_ref,
            output_schema=self._output_schema,
            output_schema_digest=self._output_schema_digest,
            output_type_name=self._output_type_name,
            timeout_seconds=self._timeout_seconds,
            retry_within_invocation=True,
            cancellation_token=request.cancellation_token,
            metadata={
                "created_at": self._clock.now().isoformat(),
                "dispatch_retry_id": request.retry_identity.dispatch_retry_id,
                "request_digest": request.request_digest,
                "idempotency_key": request.idempotency_key,
                "prompt_sha256": prompt_result.bundle.prompt_sha256,
                "context_sha256": prompt_result.bundle.context_sha256,
                "contract_ids": list(request.contract_ids),
                "sandbox_id": request.sandbox_id,
                "workspace_snapshot_ids": list(request.workspace_snapshot_ids),
                "runtime_policy_digest": request.runtime_policy_digest,
            },
        )

    async def _build_prompt(
        self,
        request: DispatchRequest,
        binding: RuntimeWorkspaceBinding,
    ) -> PromptBuildResult:
        # Slice 13A fourth sub-slice -- doc-13a:269-272 +
        # auto-memory feedback_no_refactor + feedback_no_silent_degradation.
        # When the opt-in authoritative prompt builder port is set,
        # route through it; when None (default), fall through to the
        # legacy Slice 05 path BYTE-IDENTICAL (preserves the 22-passed
        # Slice 05 dispatcher baseline + the Slice 00-12 frozen V4
        # spot-check baseline per the chunk shape point 4 invariant).
        if self._authoritative_prompt_builder is not None:
            return await self._build_prompt_authoritatively(request)
        try:
            result = await self._prompt_builder.build_prompt_context(request, binding)
        except TypeError:
            result = await self._prompt_builder.build_prompt_context(request)
        return _coerce_prompt_result(result)

    async def _build_prompt_authoritatively(
        self,
        request: DispatchRequest,
    ) -> PromptBuildResult:
        # Slice 13A fourth sub-slice -- doc-13a:269-272 +
        # auto-memory feedback_no_silent_degradation. This helper is
        # ONLY called when the opt-in authoritative prompt builder
        # port is set (verified in _build_prompt above). Per
        # doc-13a:269-272: when routing.should_invoke_runtime=True,
        # the runtime PROCEEDS with the authoritative companion record
        # attached; when False, the dispatcher raises a typed sentinel
        # exception (AuthoritativePromptContextIncompleteSignal) that
        # the dispatch() except-block catches and routes onto the typed
        # failure id runtime_context/context_incomplete WITHOUT
        # invoking the runtime.
        #
        # The local import avoids a circular import (the new module
        # iriai_build_v2.execution_control.dispatcher_prompt_context
        # imports from this module). The module-level Any | None
        # parameter typing on the constructor avoids needing the type
        # at module load.
        from iriai_build_v2.execution_control.dispatcher_prompt_context import (
            AuthoritativePromptContextIncompleteSignal,
        )

        authoritative_result = await self._authoritative_prompt_builder.build_prompt_context(
            request
        )
        if not authoritative_result.routing.should_invoke_runtime:
            raise AuthoritativePromptContextIncompleteSignal(
                routing=authoritative_result.routing,
                legacy_result=authoritative_result.legacy_result,
            )
        # Runtime proceeds with the authoritative companion record
        # attached (the legacy result is still consumed by the Slice 05
        # persistence path at record_prompt_context(...) immediately
        # after _build_prompt returns; the authoritative bundle is
        # available via getattr(result, '_authoritative_result', None)
        # for future Slice 13A sub-slices that wire downstream
        # consumers per doc-13a § Refactoring Steps steps 5-7).
        return authoritative_result.legacy_result

    async def _capture_patch(
        self,
        *,
        request: DispatchRequest,
        attempt_id: int,
        binding: RuntimeWorkspaceBinding,
        response: RuntimeInvocationResponse,
        from_state: DispatcherState,
        diagnostic_only: bool = False,
        validate_transition: bool = True,
    ) -> PatchCaptureRecord:
        if validate_transition:
            validate_dispatch_transition(from_state, "patch_capturing")
        try:
            result = await self._sandbox.capture_patch(
                request,
                attempt_id,
                binding,
                response,
                idempotency_key=self._key_factory.patch_capture_key(request, attempt_id),
            )
        except Exception as exc:
            return PatchCaptureRecord(
                sandbox_id=request.sandbox_id,
                captured=False,
                patch_summary_ids=[],
                compatibility_artifact_ids=[],
                empty=False,
                diagnostic_only=diagnostic_only,
                failure_type="patch_capture_failed",
                failure_message=str(exc),
            )
        patch_capture = _coerce_patch_capture(result, default_sandbox_id=request.sandbox_id)
        if diagnostic_only:
            patch_capture = patch_capture.model_copy(update={"diagnostic_only": True})
        return patch_capture

    async def _record_raw_output(
        self,
        *,
        attempt_id: int,
        invocation: RuntimeInvocationRequest,
        response: RuntimeInvocationResponse,
    ) -> int | None:
        recorder = getattr(self._store, "record_raw_output", None)
        if not callable(recorder):
            return response.raw_artifact_id
        return await recorder(attempt_id, invocation, response)

    async def _finish_runtime_response_failure(
        self,
        *,
        attempt_id: int,
        request: DispatchRequest,
        response: RuntimeInvocationResponse,
        evidence_ids: Sequence[int],
        raw_text_ref: int | None,
    ) -> DispatchOutcome:
        failure_class, failure_type, retryable, deterministic = _classify_runtime_response(response)
        return await self._finish_failure(
            attempt_id=attempt_id,
            request=request,
            state="evidence_recording",
            failure_class=failure_class,
            failure_type=failure_type,
            terminal_reason=response.terminal_reason,
            retryable=retryable,
            deterministic=deterministic,
            operator_required=False,
            provider_request_id=response.provider_request_id,
            evidence_ids=list(evidence_ids),
            details={
                "provider_error_code": response.provider_error_code,
                "invocation_id": response.invocation_id,
                "adapter_retry_ids": response.adapter_retry_ids,
            },
            raw_text_ref=raw_text_ref,
        )

    async def _finish_failure(
        self,
        *,
        attempt_id: int,
        request: DispatchRequest,
        state: DispatcherState,
        failure_class: RuntimeFailureClass,
        failure_type: str,
        terminal_reason: RuntimeTerminalReason,
        retryable: bool,
        deterministic: bool,
        operator_required: bool,
        provider_request_id: str | None,
        evidence_ids: Sequence[int],
        details: Mapping[str, Any],
        raw_text_ref: int | None = None,
    ) -> DispatchOutcome:
        if state != "evidence_recording":
            if "evidence_recording" in _VALID_TRANSITIONS[state]:
                validate_dispatch_transition(state, "evidence_recording")
            else:
                validate_dispatch_transition(state, "failed")
        failure_details = _runtime_failure_details_with_route(
            failure_class=failure_class,
            failure_type=failure_type,
            retryable=retryable,
            request=request,
            details=details,
        )
        signature = stable_digest(
            {
                "failure_class": failure_class,
                "failure_type": failure_type,
                "runtime": request.actor_metadata.runtime,
                "task_id": request.task_id,
                "contract_ids": sorted(request.contract_ids),
                "sandbox_id": request.sandbox_id,
                "details": _stable_failure_signature_details(failure_details),
            }
        )
        failure = RuntimeFailureRecord(
            failure_id=0,
            failure_class=failure_class,
            failure_type=failure_type,
            retryable=retryable,
            deterministic=deterministic,
            operator_required=operator_required,
            provider_request_id=provider_request_id,
            provider_error_code=str(details.get("provider_error_code") or "")
            if details.get("provider_error_code")
            else None,
            runtime=request.actor_metadata.runtime,
            terminal_reason=terminal_reason,
            evidence_ids=list(evidence_ids),
            signature_hash=signature,
            summary=f"{failure_class}/{failure_type}",
            details=failure_details,
        )
        failure = await self._store.record_runtime_failure(attempt_id, failure)
        validate_dispatch_transition("evidence_recording", "failed")
        outcome = DispatchOutcome(
            attempt_id=attempt_id,
            state="failed",
            status="failed",
            runtime_terminal_reason=terminal_reason,
            structured_result_evidence_id=None,
            raw_text_ref=raw_text_ref,
            patch_summary_ids=[],
            compatibility_artifact_ids=[],
            runtime_failure_id=failure.failure_id,
            typed_failure_id=failure.failure_id,
            idempotency_key=request.idempotency_key,
        )
        return await self._store.finish_dispatch_attempt(outcome)

    async def _finish_start_idempotency_conflict(
        self,
        request: DispatchRequest,
        conflict: DispatchIdempotencyConflict,
    ) -> DispatchOutcome:
        details = {
            "reason": "dispatch idempotency key reused with a different request digest",
            "existing_digest": conflict.existing_digest,
            "requested_digest": conflict.requested_digest,
        }
        failure = RuntimeFailureRecord(
            failure_id=0,
            failure_class="dispatcher_internal",
            failure_type="idempotency_conflict",
            retryable=False,
            deterministic=True,
            operator_required=False,
            provider_request_id=None,
            provider_error_code=None,
            runtime=request.actor_metadata.runtime,
            terminal_reason="process_failed",
            evidence_ids=[],
            signature_hash=stable_digest(
                {
                    "failure_class": "dispatcher_internal",
                    "failure_type": "idempotency_conflict",
                    "idempotency_key": request.idempotency_key,
                    "runtime": request.actor_metadata.runtime,
                    "task_id": request.task_id,
                    "contract_ids": sorted(request.contract_ids),
                    "sandbox_id": request.sandbox_id,
                    "details": details,
                }
            ),
            summary="dispatcher_internal/idempotency_conflict",
            details=details,
        )
        attempt_id, failure = await self._store.record_start_idempotency_conflict(
            request,
            failure,
        )
        return DispatchOutcome(
            attempt_id=attempt_id,
            state="failed",
            status="failed",
            runtime_terminal_reason="process_failed",
            structured_result_evidence_id=None,
            raw_text_ref=None,
            patch_summary_ids=[],
            compatibility_artifact_ids=[],
            runtime_failure_id=failure.failure_id,
            typed_failure_id=failure.failure_id,
            idempotency_key=request.idempotency_key,
        )

    async def _handle_duplicate_nonterminal_replay(
        self,
        *,
        attempt: DispatchAttemptRecord,
        request: DispatchRequest,
    ) -> DispatchOutcome:
        recovery_evidence = _duplicate_replay_crash_recovery_evidence(attempt)
        if recovery_evidence is None:
            return self._defer_duplicate_nonterminal_replay(
                attempt_id=attempt.attempt_id,
                request=request,
                stored_state=attempt.state,
            )
        return await self._finish_duplicate_nonterminal_replay(
            attempt_id=attempt.attempt_id,
            request=request,
            stored_state=attempt.state,
            recovery_evidence=recovery_evidence,
        )

    def _defer_duplicate_nonterminal_replay(
        self,
        *,
        attempt_id: int,
        request: DispatchRequest,
        stored_state: DispatcherState,
    ) -> DispatchOutcome:
        patch_capture_details = {
            "attempted": False,
            "captured": False,
            "capturable": (
                stored_state in _DUPLICATE_NONTERMINAL_PATCH_CAPTURABLE_STATES
            ),
            "diagnostic_only": True,
            "reason": "requires_durable_crash_recovery_evidence",
            "stored_state": stored_state,
        }
        recovery_decision = self._duplicate_nonterminal_replay_failure(
            request=request,
            stored_state=stored_state,
            patch_summary_ids=[],
            patch_capture_details=patch_capture_details,
            recovery_evidence=None,
        )
        return DispatchOutcome(
            attempt_id=attempt_id,
            state="incomplete",
            status="incomplete",
            runtime_terminal_reason="process_failed",
            structured_result_evidence_id=None,
            raw_text_ref=None,
            patch_summary_ids=[],
            compatibility_artifact_ids=[],
            runtime_failure_id=None,
            typed_failure_id=None,
            idempotency_key=request.idempotency_key,
            recovery_decision=recovery_decision,
        )

    async def _finish_duplicate_nonterminal_replay(
        self,
        *,
        attempt_id: int,
        request: DispatchRequest,
        stored_state: DispatcherState,
        recovery_evidence: Mapping[str, Any],
    ) -> DispatchOutcome:
        validate_dispatch_transition(stored_state, "incomplete")
        patch_capture, patch_capture_details = (
            await self._capture_duplicate_nonterminal_replay_patch(
                attempt_id=attempt_id,
                request=request,
                stored_state=stored_state,
            )
        )
        patch_summary_ids = (
            list(patch_capture.patch_summary_ids)
            if patch_capture is not None and patch_capture.captured
            else []
        )
        compatibility_artifact_ids = (
            list(patch_capture.compatibility_artifact_ids)
            if patch_capture is not None and patch_capture.captured
            else []
        )
        failure = self._duplicate_nonterminal_replay_failure(
            request=request,
            stored_state=stored_state,
            patch_summary_ids=patch_summary_ids,
            patch_capture_details=patch_capture_details,
            recovery_evidence=recovery_evidence,
        )
        failure = await self._store.record_runtime_failure(attempt_id, failure)
        outcome = DispatchOutcome(
            attempt_id=attempt_id,
            state="incomplete",
            status="incomplete",
            runtime_terminal_reason="process_failed",
            structured_result_evidence_id=None,
            raw_text_ref=None,
            patch_summary_ids=patch_summary_ids,
            compatibility_artifact_ids=compatibility_artifact_ids,
            runtime_failure_id=failure.failure_id,
            typed_failure_id=failure.failure_id,
            idempotency_key=request.idempotency_key,
            recovery_decision=failure,
        )
        return await self._store.finish_dispatch_attempt(outcome)

    def _duplicate_nonterminal_replay_failure(
        self,
        *,
        request: DispatchRequest,
        stored_state: DispatcherState,
        patch_summary_ids: Sequence[int],
        patch_capture_details: Mapping[str, Any],
        recovery_evidence: Mapping[str, Any] | None,
    ) -> RuntimeFailureRecord:
        details = {
            "reason": (
                "same-digest dispatch replay found an already-started "
                "nonterminal attempt; runtime invocation is suppressed to "
                "avoid duplicate provider side effects"
            ),
            "stored_state": stored_state,
            "request_digest": request.request_digest,
            "idempotency_key": request.idempotency_key,
            "patch_capture": dict(patch_capture_details),
            "recovery_evidence": dict(recovery_evidence)
            if recovery_evidence is not None
            else {
                "durable_crash_recovery_proof": False,
                "required": (
                    "durable stale-owner, stale-heartbeat, or recovery evidence "
                    "that the original runtime crashed"
                ),
            },
        }
        return RuntimeFailureRecord(
            failure_id=0,
            failure_class="dispatcher_internal",
            failure_type="nonterminal_replay_requires_recovery",
            retryable=True,
            deterministic=True,
            operator_required=False,
            provider_request_id=None,
            provider_error_code=None,
            runtime=request.actor_metadata.runtime,
            terminal_reason="process_failed",
            evidence_ids=list(patch_summary_ids),
            signature_hash=stable_digest(
                {
                    "failure_class": "dispatcher_internal",
                    "failure_type": "nonterminal_replay_requires_recovery",
                    "idempotency_key": request.idempotency_key,
                    "stored_state": stored_state,
                    "runtime": request.actor_metadata.runtime,
                    "task_id": request.task_id,
                    "contract_ids": sorted(request.contract_ids),
                    "sandbox_id": request.sandbox_id,
                    "request_digest": request.request_digest,
                    "patch_capture": _stable_failure_signature_details(
                        patch_capture_details
                    ),
                    "recovery_evidence": _stable_failure_signature_details(
                        dict(recovery_evidence or {})
                    ),
                }
            ),
            summary="dispatcher_internal/nonterminal_replay_requires_recovery",
            details=details,
        )

    async def _capture_duplicate_nonterminal_replay_patch(
        self,
        *,
        attempt_id: int,
        request: DispatchRequest,
        stored_state: DispatcherState,
    ) -> tuple[PatchCaptureRecord | None, dict[str, Any]]:
        if stored_state not in _DUPLICATE_NONTERMINAL_PATCH_CAPTURABLE_STATES:
            return None, {
                "attempted": False,
                "captured": False,
                "capturable": False,
                "diagnostic_only": True,
                "reason": "stored_state_precedes_runtime_invocation",
                "stored_state": stored_state,
            }

        try:
            raw_binding = await self._sandbox.bind_runtime(request, attempt_id)
            binding = _coerce_runtime_binding(raw_binding)
        except Exception as exc:
            return None, {
                "attempted": True,
                "captured": False,
                "capturable": False,
                "diagnostic_only": True,
                "reason": "sandbox_binding_failed",
                "stored_state": stored_state,
                "exception": exc.__class__.__name__,
                "message": str(exc),
            }

        replay_response = RuntimeInvocationResponse(
            invocation_id=self._key_factory.invocation_id(request, attempt_id),
            status="failed",
            terminal_reason="process_failed",
            process_started=True,
            structured_output=None,
            raw_text=None,
            raw_artifact_id=None,
            provider_request_id=None,
            provider_error_code=None,
            elapsed_ms=0,
        )
        patch_capture = await self._capture_patch(
            request=request,
            attempt_id=attempt_id,
            binding=binding,
            response=replay_response,
            from_state=stored_state,
            diagnostic_only=True,
            validate_transition=False,
        )
        return patch_capture, {
            "attempted": True,
            "captured": patch_capture.captured,
            "capturable": patch_capture.captured,
            "diagnostic_only": patch_capture.diagnostic_only,
            "reason": None
            if patch_capture.captured
            else patch_capture.failure_type or "patch_capture_not_captured",
            "stored_state": stored_state,
            "sandbox_id": patch_capture.sandbox_id,
            "patch_summary_ids": list(patch_capture.patch_summary_ids),
            "compatibility_artifact_ids": list(patch_capture.compatibility_artifact_ids),
            "empty": patch_capture.empty,
            "failure_type": patch_capture.failure_type,
            "message": patch_capture.failure_message,
        }

    async def _finish_incomplete_from_patch_failure(
        self,
        *,
        attempt_id: int,
        request: DispatchRequest,
        response: RuntimeInvocationResponse,
        patch_capture: PatchCaptureRecord,
    ) -> DispatchOutcome:
        failure = RuntimeFailureRecord(
            failure_id=0,
            failure_class="sandbox_capture",
            failure_type=patch_capture.failure_type or "patch_capture_failed",
            retryable=True,
            deterministic=False,
            operator_required=False,
            provider_request_id=response.provider_request_id,
            provider_error_code=response.provider_error_code,
            runtime=request.actor_metadata.runtime,
            terminal_reason="patch_capture_failed",
            evidence_ids=[],
            signature_hash=stable_digest(
                {
                    "failure_class": "sandbox_capture",
                    "failure_type": patch_capture.failure_type or "patch_capture_failed",
                    "sandbox_id": request.sandbox_id,
                    "task_id": request.task_id,
                    "message": patch_capture.failure_message,
                }
            ),
            summary=patch_capture.failure_message or "patch capture failed",
            details={
                "message": patch_capture.failure_message,
                "runtime_terminal_reason": response.terminal_reason,
            },
        )
        failure = await self._store.record_runtime_failure(attempt_id, failure)
        validate_dispatch_transition("patch_capturing", "incomplete")
        outcome = DispatchOutcome(
            attempt_id=attempt_id,
            state="incomplete",
            status="incomplete",
            runtime_terminal_reason="patch_capture_failed",
            structured_result_evidence_id=None,
            raw_text_ref=response.raw_text_ref or response.raw_artifact_id,
            patch_summary_ids=[],
            compatibility_artifact_ids=[],
            runtime_failure_id=failure.failure_id,
            typed_failure_id=failure.failure_id,
            idempotency_key=request.idempotency_key,
        )
        return await self._store.finish_dispatch_attempt(outcome)

    async def _finish_cancelled(
        self,
        *,
        attempt_id: int,
        request: DispatchRequest,
        reason: RuntimeTerminalReason,
        raw_text_ref: int | None = None,
    ) -> DispatchOutcome:
        failure = RuntimeFailureRecord(
            failure_id=0,
            failure_class="runtime_cancelled",
            failure_type="runtime_cancelled",
            retryable=False,
            deterministic=False,
            operator_required=False,
            provider_request_id=None,
            provider_error_code=None,
            runtime=request.actor_metadata.runtime,
            terminal_reason=reason,
            evidence_ids=[],
            signature_hash=stable_digest(
                {
                    "failure_class": "runtime_cancelled",
                    "failure_type": "runtime_cancelled",
                    "task_id": request.task_id,
                    "sandbox_id": request.sandbox_id,
                    "reason": reason,
                }
            ),
            summary="runtime dispatch cancelled",
            details={"reason": reason},
        )
        failure = await self._store.record_runtime_failure(attempt_id, failure)
        outcome = DispatchOutcome(
            attempt_id=attempt_id,
            state="cancelled",
            status="cancelled",
            runtime_terminal_reason=reason,
            structured_result_evidence_id=None,
            raw_text_ref=raw_text_ref,
            patch_summary_ids=[],
            compatibility_artifact_ids=[],
            runtime_failure_id=failure.failure_id,
            typed_failure_id=failure.failure_id,
            idempotency_key=request.idempotency_key,
        )
        return await self._store.finish_dispatch_attempt(outcome)


def _classify_runtime_response(
    response: RuntimeInvocationResponse,
) -> tuple[RuntimeFailureClass, str, bool, bool]:
    reason = response.terminal_reason
    if reason == "provider_error":
        return "runtime_provider", _canonical_provider_failure_type(response.provider_error_code), True, False
    if reason in {"timeout", "watchdog_stall"}:
        return "runtime_timeout", "watchdog_timeout", True, False
    if reason == "process_failed":
        return "runtime_provider", "process_failed", True, False
    if reason in {"prompt_too_large", "context_materialization_failed"}:
        return "runtime_context", reason, True, True
    if reason == "structured_output_invalid":
        return "runtime_structured_output", "malformed_structured_output", True, True
    if reason == "sandbox_binding_failed":
        return "sandbox_binding", "runtime_workspace_binding_failed", False, True
    if reason == "patch_capture_failed":
        return "sandbox_capture", "patch_capture_failed", True, False
    if reason == "cancelled":
        return "runtime_cancelled", "runtime_cancelled", False, False
    return "dispatcher_internal", f"unexpected_terminal_reason:{reason}", False, True


def _canonical_provider_failure_type(provider_error_code: str | None) -> str:
    code = (provider_error_code or "").strip().lower()
    if code == "429" or code.startswith("429 ") or "too_many_requests" in code:
        return "provider_rate_limited"
    if "rate" in code or "quota" in code or "limit" in code:
        return "provider_rate_limited"
    if any(
        token in code
        for token in ("transport", "network", "connection", "connect", "timeout")
    ):
        return "provider_transport_error"
    return "provider_internal_error"


def _duplicate_replay_crash_recovery_evidence(
    attempt: DispatchAttemptRecord,
) -> dict[str, Any] | None:
    for candidate in _duplicate_replay_recovery_candidates(attempt):
        for evidence in _duplicate_replay_recovery_items(candidate):
            if _is_durable_crash_recovery_evidence(evidence):
                return {
                    **evidence,
                    "durable_crash_recovery_proof": True,
                }
    return None


def _duplicate_replay_recovery_candidates(attempt: DispatchAttemptRecord) -> list[Any]:
    candidates = [
        getattr(attempt, field, None)
        for field in _DUPLICATE_REPLAY_RECOVERY_FIELDS
    ]
    data = _to_jsonable(attempt)
    if isinstance(data, Mapping):
        candidates.extend(
            data.get(field) for field in _DUPLICATE_REPLAY_RECOVERY_FIELDS
        )
        for container_key in ("payload", "metadata", "details", "recovery"):
            container = data.get(container_key)
            if isinstance(container, Mapping):
                candidates.extend(
                    container.get(field)
                    for field in _DUPLICATE_REPLAY_RECOVERY_FIELDS
                )
    return candidates


def _duplicate_replay_recovery_items(value: Any) -> list[dict[str, Any]]:
    data = _to_jsonable(value)
    if isinstance(data, Mapping):
        return [dict(data)]
    if isinstance(data, list):
        items: list[dict[str, Any]] = []
        for item in data:
            item_data = _to_jsonable(item)
            if isinstance(item_data, Mapping):
                items.append(dict(item_data))
        return items
    return []


def _is_durable_crash_recovery_evidence(evidence: Mapping[str, Any]) -> bool:
    if any(
        _truthy_evidence_value(evidence.get(key))
        for key in _LIVE_RUNTIME_SIGNAL_KEYS
    ):
        return False
    return _has_durable_recovery_reference(evidence) and _has_crash_recovery_signal(
        evidence
    )


def _has_durable_recovery_reference(evidence: Mapping[str, Any]) -> bool:
    if _truthy_evidence_value(evidence.get("durable")):
        return True
    if _truthy_evidence_value(evidence.get("durable_evidence")):
        return True
    return any(
        _truthy_evidence_value(evidence.get(key))
        for key in _DURABLE_RECOVERY_REF_KEYS
    )


def _has_crash_recovery_signal(evidence: Mapping[str, Any]) -> bool:
    if any(
        _truthy_evidence_value(evidence.get(key))
        for key in _CRASH_RECOVERY_SIGNAL_KEYS
    ):
        return True
    for key in (
        "classification",
        "failure_type",
        "reason",
        "recovery_reason",
        "signal",
    ):
        value = str(evidence.get(key) or "").lower()
        if "crash" in value:
            return True
        if "stale" in value and ("heartbeat" in value or "owner" in value):
            return True
    return False


def _truthy_evidence_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {
            "",
            "0",
            "false",
            "no",
            "none",
            "null",
            "unknown",
        }
    return bool(value)


def _runtime_failure_details_with_route(
    *,
    failure_class: RuntimeFailureClass,
    failure_type: str,
    retryable: bool,
    request: DispatchRequest,
    details: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(details)
    if failure_class == "runtime_provider" and retryable:
        retry_id = _retry_identity_value(request.retry_identity, "retry_id") or _retry_identity_value(
            request.retry_identity,
            "dispatch_retry_id",
        )
        retry_limit_raw = _first_retry_identity_value(
            request.retry_identity,
            "max_retries",
            "max_attempts",
            "retry_limit",
        )
        try:
            max_retries = int(retry_limit_raw)
        except (TypeError, ValueError):
            max_retries = DEFAULT_PROVIDER_RETRY_LIMIT
        max_retries = max(0, max_retries)
        remaining_attempts = max(0, max_retries - int(request.retry) - 1)
        retry_budget = {
            "route": "retry_dispatch",
            "retry": request.retry,
            "retry_id": retry_id,
            "max_retries": max_retries,
            "max_attempts": max_retries,
            "remaining_attempts": remaining_attempts,
            "idempotency_key": request.idempotency_key,
        }
        result.setdefault("route", "retry_dispatch")
        result.setdefault("retry_budget", retry_budget)
        result.setdefault(
            "route_decision",
            {
                "route": "retry_dispatch",
                "failure_class": failure_class,
                "failure_type": failure_type,
                "source": "dispatcher_runtime_failure",
                "retryable": True,
                "retry": request.retry,
                "retry_id": retry_id,
                "retry_budget": retry_budget,
            },
        )
    return result


def _retry_identity_value(identity: Any, key: str) -> Any:
    if isinstance(identity, Mapping):
        return identity.get(key)
    return getattr(identity, key, None)


def _first_retry_identity_value(identity: Any, *keys: str) -> Any:
    for key in keys:
        value = _retry_identity_value(identity, key)
        if value is not None:
            return value
    return None


def _stable_failure_signature_details(details: Mapping[str, Any]) -> dict[str, Any]:
    unstable = {
        "adapter_retry_ids",
        "elapsed_ms",
        "invocation_id",
        "provider_request_id",
        "request_id",
    }
    return {
        str(key): value
        for key, value in details.items()
        if str(key) not in unstable
    }


def _coerce_prompt_result(value: PromptBuildResult | Mapping[str, Any] | Any) -> PromptBuildResult:
    if isinstance(value, PromptBuildResult):
        return value
    data = _to_jsonable(value)
    if isinstance(data, dict) and "prompt" in data and "bundle" in data:
        return PromptBuildResult.model_validate(data)
    if isinstance(value, tuple) and len(value) == 2:
        prompt, bundle = value
        return PromptBuildResult(prompt=str(prompt), bundle=PromptContextBundle.model_validate(bundle))
    raise TypeError("prompt builder must return PromptBuildResult")


def _coerce_runtime_binding(value: RuntimeWorkspaceBinding | Mapping[str, Any] | Any) -> RuntimeWorkspaceBinding:
    if isinstance(value, RuntimeWorkspaceBinding):
        return value
    data = _to_jsonable(value)
    if not isinstance(data, dict):
        raise TypeError("sandbox bind_runtime must return a runtime workspace binding")
    if "runtime" not in data and "runtime_name" in data:
        data["runtime"] = data["runtime_name"]
    return RuntimeWorkspaceBinding.model_validate(data)


def _coerce_patch_capture(
    value: PatchCaptureRecord | Mapping[str, Any] | Any,
    *,
    default_sandbox_id: str,
) -> PatchCaptureRecord:
    if isinstance(value, PatchCaptureRecord):
        return value
    data = _to_jsonable(value)
    if not isinstance(data, dict):
        raise TypeError("sandbox capture_patch must return a patch capture record")
    data.setdefault("sandbox_id", default_sandbox_id)
    data.setdefault("captured", True)
    data.setdefault("compatibility_artifact_ids", [])
    data.setdefault("diagnostic_only", False)
    return PatchCaptureRecord.model_validate(data)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except TypeError:
            return model_dump()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(_to_jsonable(item) for item in value)
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, Mapping):
        return {
            str(key): _to_jsonable(item)
            for key, item in attrs.items()
            if not str(key).startswith("_")
        }
    return value


# ── Slice 11e — pure runtime-selection / parallel-actor primitives ─────────
#
# Per docs/execution-control-plane/11-refactor-map.md § "Compatibility Shims"
# row 3 (Dispatcher/runtime), the names below live in
# `workflows/develop/execution/dispatcher.py` (this module) as pure
# primitives. They are moved BYTE-FOR-BYTE from
# `workflows/develop/phases/implementation.py`; the legacy import names
# are preserved via a parallel sibling re-export block at the head of
# `implementation.py` (the Slice-11e block, mirroring the Slice-11a /
# Slice-11b / Slice-11c / Slice-11d blocks already in place).
#
# Scope split: only the PURE primitives move here — the static
# role→runtime map, the per-group / per-stage runtime-pair selectors,
# the diagnostic-runtime selector, and the parallel-safe actor factory.
# The phase-level dispatcher PORT surface (the
# `_dispatch_task_attempt_via_runtime_dispatcher` orchestrator + the
# `_ImplementationSandboxPort` / `_ImplementationRuntimeClient` /
# `_ImplementationPromptBuilder` / `_ImplementationOutputNormalizer` /
# `_ExecutionControlDispatchJournalPort` / `_ArtifactDispatchJournalPort`
# adapter classes + the `_dispatcher_request_for_task` /
# `_dispatcher_actor_metadata` builders + `_runner_runtime_policy` /
# `_runtime_instance_name_for_hint`) STAYS in `implementation.py` per
# the prompt hard rule against splitting non-pure helpers — each one
# depends on `WorkflowRunner` / `Feature` /
# `_execution_control_store_for_runner` / the implementation.py-
# namespaced module `logger` / `_model_json_dict` / a domain helper
# (`_task_contract_id`, `_snapshot_id_for_repo`, `_git_text`,
# `_sha256_text`, `_contract_repo_id`, `_sandbox_blocker`) or holds
# `runner`+`feature`+`task_contract`+`task` as instance attributes.


DAG_REPAIR_ROLE_RUNTIMES: dict[str, str] = {
    # Under --bridge-claude-pool-codex-review, primary=Claude pool and
    # secondary=Codex. Keep this intentionally static so runtime balance is
    # role-based rather than a fragile per-run counter.
    "dag-normal-verify": "secondary",
    "dag-final-verify": "secondary",
    "dag-triage": "primary",
    "dag-rca": "primary",
    "dag-fix": "primary",
    "dag-focused-reverify": "primary",
    "dag-contradiction-resolve": "secondary",
    "lens:acceptance-coverage": "secondary",
    "lens:contract-protocol": "secondary",
    "lens:build-dependency": "primary",
    "lens:runtime-composition": "primary",
    "lens:security-boundary": "primary",
    "lens:regression-downstream": "primary",
}


def _dag_repair_runtime_for(
    role_or_lens: str,
    fallback: str | None = None,
) -> str | None:
    return DAG_REPAIR_ROLE_RUNTIMES.get(role_or_lens, fallback)


def _dag_group_runtime_pair(
    group_idx: int,
    runtime_policy: RuntimePolicy,
) -> tuple[str, str]:
    """Return ``(implementation_runtime, review_runtime)`` for a DAG group."""
    if runtime_policy == PRIMARY_IMPL_SECONDARY_REVIEW_POLICY:
        return "primary", "secondary"
    return (
        ("primary", "secondary")
        if group_idx % 2 == 0
        else ("secondary", "primary")
    )


def _post_dag_runtime_pair(
    last_group_idx: int,
    runtime_policy: RuntimePolicy,
) -> tuple[str, str]:
    """Return ``(gate_runtime, fix_runtime)`` for post-DAG gates."""
    if runtime_policy == PRIMARY_IMPL_SECONDARY_REVIEW_POLICY:
        return "secondary", "primary"
    return (
        ("secondary", "primary")
        if last_group_idx % 2 == 0
        else ("primary", "secondary")
    )


def _diagnostic_runtime_for_policy(runtime_policy: RuntimePolicy) -> str | None:
    """Return the runtime for RCA/triage/regression analysis under a policy."""
    if runtime_policy == PRIMARY_IMPL_SECONDARY_REVIEW_POLICY:
        return "secondary"
    return None


def _make_parallel_actor(
    base: AgentActor,
    suffix: str,
    *,
    runtime: str | None = None,
    workspace_path: str | None = None,
    runtime_workspace_binding: Any | None = None,
    sandbox_required: bool = False,
) -> AgentActor:
    """Create a parallel-safe copy of an AgentActor with a unique name.

    When *runtime* is set (``"primary"`` or ``"secondary"``), the actor's
    role metadata is updated so ``TrackedWorkflowRunner.resolve()`` routes
    it to the correct runtime for adversarial multi-model execution.

    When *workspace_path* is set, it overrides the agent's ``cwd`` so
    it operates within a specific repo worktree (not the main workspace).
    """
    metadata = dict(base.role.metadata)
    if runtime:
        metadata["runtime"] = runtime
    if workspace_path:
        metadata["workspace_override"] = workspace_path
    if runtime_workspace_binding is not None:
        binding_payload = (
            runtime_workspace_binding.model_dump(mode="json")
            if hasattr(runtime_workspace_binding, "model_dump")
            else dict(runtime_workspace_binding)
        )
        metadata["runtime_workspace_binding"] = binding_payload
        metadata["sandbox_required"] = sandbox_required or True
        metadata["write_producing"] = True
        metadata["workspace_override"] = str(
            binding_payload.get("cwd") or binding_payload.get("workspace_override") or workspace_path or ""
        )
    elif sandbox_required:
        metadata["sandbox_required"] = True
    role = base.role.model_copy(update={"metadata": metadata})
    return AgentActor(
        name=f"{base.name}-{suffix}",
        role=role,
        context_keys=base.context_keys,
        persistent=base.persistent,
    )


__all__ = [
    "ActorMetadata",
    "CancellationRegistryPort",
    "ClockPort",
    "DAG_REPAIR_ROLE_RUNTIMES",
    "DefaultClock",
    "DefaultIdempotencyKeyFactory",
    "DefaultStructuredOutputNormalizer",
    "DispatchAttemptRecord",
    "DispatchIdempotencyConflict",
    "DispatchJournalPort",
    "DispatchOutcome",
    "DispatchRequest",
    "DispatchRetryIdentity",
    "DispatchStateTransitionError",
    "DispatchStatus",
    "DispatcherState",
    "ExecutionControlStore",
    "IdempotencyKeyFactoryPort",
    "PatchCaptureRecord",
    "PromptBuildResult",
    "PromptContextBundle",
    "RuntimeClientPort",
    "RuntimeDispatcher",
    "RuntimeFailureClass",
    "RuntimeFailureRecord",
    "RuntimeInvocationRequest",
    "RuntimeInvocationResponse",
    "RuntimeName",
    "RuntimeTerminalReason",
    "RuntimeWorkspaceBinding",
    "SandboxRunnerPort",
    "StructuredOutputNormalizerPort",
    "StructuredOutputRecord",
    "_dag_group_runtime_pair",
    "_dag_repair_runtime_for",
    "_diagnostic_runtime_for_policy",
    "_make_parallel_actor",
    "_post_dag_runtime_pair",
    "actor_metadata_digest",
    "dispatch_idempotency_key",
    "dispatch_request_digest",
    "stable_digest",
    "stable_json",
    "validate_dispatch_transition",
]
