"""Mechanical read-only supervisor contract (Slice 10c-1).

Doc 10 ("Supervisor And Dashboard Integration") § "Read-Only And Audit
Exception Policy" is the SPEC for this module. In default v1 read-only mode the
supervisor is **advisory**: it may read typed control-plane state and write only
its own append-only audit / dedupe / display-outbox records. It may NOT mutate
executor / control-plane / product authority.

doc 10 § "Read-Only And Audit Exception Policy":

    Read-only supervisor means it may not mutate executor/control-plane
    authority: no product file edits, no execution artifact projection writes,
    no checkpoints, no merge queue rows, no typed failures, no retry budgets,
    no task contracts, no workspace snapshots, and no attempt state
    transitions.

    Service wiring must make that contract MECHANICAL. In default v1 read-only
    mode, supervisor code receives only read/query handles plus supervisor-owned
    audit, dedupe, and display outbox writers. Any MCP tool or action policy
    path that would call an execution-authority writer is ABSENT or DENIED
    before runtime parameters are inspected.

    Denied writes fail closed and produce a blocked action audit row rather
    than a best-effort mutation.

This module makes that contract mechanical, NOT a runtime parameter check:

1. :data:`CONTROL_PLANE_WRITER_METHODS` — the canonical, doc-10-derived set of
   *execution-authority* writer method names. Every name is a writer that is
   EXCLUSIVE to ``execution_control.store.ExecutionControlStore`` (the typed
   journal / attempt / merge-queue / checkpoint / contract / workspace-snapshot
   / sandbox-lease store). A handle exposing ANY of them IS the
   execution-authority store and a read-only supervisor must never hold it.
2. :data:`FEATURE_TIMELINE_WRITER_METHODS` — feature/artifact-store writers
   that are *dual-use*: the store CLASS legitimately carries them (the MCP
   service needs ``get_feature`` / ``list_event_summaries`` from the same
   class), so they are tracked separately. The supervisor must never *invoke*
   them — for the artifact ``put`` that is enforced by
   :class:`ReadOnlyAuditArtifactSink`; the MCP service exposes no write tool at
   all.
3. :data:`EXECUTION_AUTHORITY_WRITER_METHODS` — the union of the two sets
   above: the full doc-10 mutation surface, used by the static coverage test.
4. :func:`assert_no_control_plane_writer` — a CONSTRUCTION-TIME guard: given a
   store/handle a read-only supervisor is about to hold, it raises
   :class:`ReadOnlySupervisorViolation` (FAILS CLOSED) if that handle exposes a
   :data:`CONTROL_PLANE_WRITER_METHODS` member. The supervisor never even
   *holds* the execution-authority store — the writer path is structurally
   ABSENT, not runtime-gated.
5. :data:`SUPERVISOR_OWNED_AUDIT_KEY_PREFIXES` — the artifact key prefixes the
   supervisor *is* allowed to write (append-only audit/decision/digest rows).
6. :func:`is_supervisor_owned_audit_key` — classifies an artifact key as a
   supervisor-owned audit write (ALLOWED) vs. an execution-authority /
   product artifact write (DENIED).
7. :class:`ReadOnlyAuditArtifactSink` — a mechanical wrapper around an
   ``ArtifactStore``: a ``put`` to a supervisor-owned audit key passes through;
   a ``put`` to any other key FAILS CLOSED — it never performs the mutation.
   The blocked-action audit row is written by the caller's ``ActionPolicy``
   (which owns audit-row formatting); this sink raises so the deny is
   non-bypassable.

This is ADDITIVE (Slice 10c-1): no working supervisor code path is rewritten.
The construction guards are wired in :class:`SupervisorEvidenceMcpService`
(``mcp_server.py``) and :class:`ActionPolicy` (``actions.py``); the audit-scoped
sink is an opt-in wrapper a read-only deployment can hand to ``ActionPolicy``.

The later Slice 10 Slack-dedupe sub-slice adds the ``supervisor_digest_state`` /
``supervisor_digest_audit`` tables — those are supervisor-OWNED audit state, so
their key prefixes are pre-registered here (doc 10 § "Slack Dedupe And
Suppression": "They are audit state, not execution authority").
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ReadOnlySupervisorViolation",
    "BlockedExecutionWrite",
    "CONTROL_PLANE_WRITER_METHODS",
    "FEATURE_TIMELINE_WRITER_METHODS",
    "EXECUTION_AUTHORITY_WRITER_METHODS",
    "SUPERVISOR_OWNED_AUDIT_KEY_PREFIXES",
    "is_supervisor_owned_audit_key",
    "assert_no_control_plane_writer",
    "assert_read_only_supervisor_handles",
    "ReadOnlyAuditArtifactSink",
]


class ReadOnlySupervisorViolation(RuntimeError):
    """A read-only supervisor was wired with an execution-authority writer.

    Raised at CONSTRUCTION time (fail-closed): the read-only contract is
    violated structurally — a writer handle is present where doc 10 § "Read-Only
    And Audit Exception Policy" requires it to be absent. This is never caught
    and degraded; an unsafe wiring must stop the supervisor.
    """


class BlockedExecutionWrite(RuntimeError):
    """A read-only supervisor attempted an execution-authority / product write.

    Raised by :class:`ReadOnlyAuditArtifactSink` when a ``put`` targets a key
    outside :data:`SUPERVISOR_OWNED_AUDIT_KEY_PREFIXES`. doc 10: "Denied writes
    fail closed and produce a blocked action audit row rather than a best-effort
    mutation." The mutation is NOT performed; the caller records the blocked
    action audit row.
    """

    def __init__(self, action: str, reason: str) -> None:
        super().__init__(f"blocked execution-authority write [{action}]: {reason}")
        self.action = action
        self.reason = reason


# ── Control-plane (execution-authority) writer surface ──────────────────────
#
# doc 10 § "Read-Only And Audit Exception Policy" enumerates the mutation
# classes a read-only supervisor must not reach: "no product file edits, no
# execution artifact projection writes, no checkpoints, no merge queue rows, no
# typed failures, no retry budgets, no task contracts, no workspace snapshots,
# and no attempt state transitions."
#
# Every name below is a real public writer method that is EXCLUSIVE to
# ``execution_control.store.ExecutionControlStore`` — the typed-journal /
# attempt / merge-queue / checkpoint / contract / workspace-snapshot /
# sandbox-lease store (verified at file:line in the Slice 10c journal entry).
# Because these names appear on no read-only store the supervisor legitimately
# holds, a handle exposing ANY of them IS the execution-authority store, and
# ``assert_no_control_plane_writer`` fails closed on it.
CONTROL_PLANE_WRITER_METHODS: frozenset[str] = frozenset(
    {
        # ── typed journal rows + attempt state transitions ──────────────────
        "record",
        "record_success",
        "start_dispatch_attempt",
        "finish_dispatch_attempt",
        "record_prompt_context",
        "record_runtime_invocation",
        "record_raw_output",
        "record_structured_output",
        # ── typed failures / route decisions / retry budgets ────────────────
        "record_runtime_failure",
        # ── verification graph (gate evidence) ──────────────────────────────
        "record_verification_graph_node",
        "record_verification_graph_projection",
        # ── execution-artifact projection writes / checkpoints (the legacy
        #    dag-* compatibility projections) ──────────────────────────────
        "project_task_result",
        "project_task_result_from_attempt",
        "project_verify_result",
        "project_commit_failure",
        "project_group_checkpoint",
        "project_regroup_overlay",
        "project_regroup_active",
        # ── task deliverable contracts ──────────────────────────────────────
        "put_task_contract",
        "record_contract_verdict",
        "record_patch_summary",
        # ── workspace authority / workspace snapshots ───────────────────────
        "record_workspace_registry",
        "record_workspace_preflight",
        "record_workspace_snapshot",
        # ── sandbox leases / runtime workspace bindings ─────────────────────
        "allocate_sandbox_lease",
        "update_sandbox_lease",
        "record_sandbox_repo_binding",
        "record_runtime_workspace_binding",
    }
)

# ── Feature/artifact timeline writer surface (dual-use) ─────────────────────
#
# These writers live on ``PostgresFeatureStore`` / ``PostgresArtifactStore`` —
# the SAME store classes whose read methods (``get_feature``,
# ``list_event_summaries``, ``list_record_summaries``, ``get_slice``) the
# supervisor's MCP evidence service legitimately calls. So a "no writer methods
# on the handle" assertion would wrongly reject the read surface.
#
# They are tracked separately and the contract is enforced at the *call* site,
# not by absence of the method: the supervisor MCP service exposes NO write
# tool (every ``@mcp.tool`` is a read/query), and an artifact ``put`` goes
# through :class:`ReadOnlyAuditArtifactSink`, which denies any non-audit key.
# A feature-timeline mutation (``transition_phase`` / ``log_event`` /
# ``update_metadata``) has no supervisor caller at all.
FEATURE_TIMELINE_WRITER_METHODS: frozenset[str] = frozenset(
    {
        # ── FeatureStore: feature timeline / phase authority ────────────────
        "create",
        "transition_phase",
        "update_metadata",
        "log_event",
        # ── ArtifactStore: raw artifact / binary mutation ───────────────────
        "put",
        "write_artifact_bytes",
        "delete",
    }
)

# The full doc-10 mutation surface (union). The static read-only coverage test
# asserts this equals the two subsets combined, so a new writer added to either
# subset is forced into the contract.
EXECUTION_AUTHORITY_WRITER_METHODS: frozenset[str] = (
    CONTROL_PLANE_WRITER_METHODS | FEATURE_TIMELINE_WRITER_METHODS
)


# ── Supervisor-owned audit / dedupe / outbox write surface ──────────────────
#
# doc 10 § "Read-Only And Audit Exception Policy" — "Allowed writes":
#   * append-only supervisor observation/decision/digest/action audit records
#   * Slack dedupe/suppression records
#   * public dashboard display outbox events derived from typed snapshots
#
# An artifact `put` whose key starts with one of these prefixes is a
# supervisor-owned audit write (ALLOWED). Any other key is execution-authority
# or product state (DENIED). The Slice-10 Slack-dedupe sub-slice's tables
# (`supervisor_digest_state` / `supervisor_digest_audit`) carry these prefixes
# when projected for operator review (doc 10: artifacts "may be projected for
# operator review only after the table write succeeds").
SUPERVISOR_OWNED_AUDIT_KEY_PREFIXES: tuple[str, ...] = (
    "supervisor-observation:",
    "supervisor-decision:",
    "supervisor-agent-assessment:",
    "supervisor-thread-context:",
    "supervisor-action:",
    "supervisor-digest:",
    "supervisor-digest-audit:",
    "supervisor-digest-state:",
)


def is_supervisor_owned_audit_key(key: str) -> bool:
    """Return True iff ``key`` is a supervisor-owned audit/dedupe/outbox key.

    A supervisor-owned key is the ONLY artifact key a read-only supervisor may
    ``put`` (doc 10 § "Read-Only And Audit Exception Policy" "Allowed writes").
    Any other key — a ``dag-*`` execution projection, a product artifact, a
    checkpoint — is an execution-authority write and is DENIED.
    """

    candidate = str(key or "")
    return candidate.startswith(SUPERVISOR_OWNED_AUDIT_KEY_PREFIXES)


def _control_plane_writers_present(handle: Any) -> list[str]:
    """Return the control-plane writer method names a handle exposes.

    A method is "present" when ``getattr(handle, name)`` resolves to something
    callable. This inspects the *handle itself* — the structural test doc 10
    requires ("absent ... before runtime parameters are inspected"), not a
    runtime mode flag.
    """

    present: list[str] = []
    for name in sorted(CONTROL_PLANE_WRITER_METHODS):
        attr = getattr(handle, name, None)
        if attr is not None and callable(attr):
            present.append(name)
    return present


def assert_no_control_plane_writer(
    handle: Any,
    *,
    role: str,
) -> None:
    """Fail closed if ``handle`` is/exposes the execution-authority store.

    CONSTRUCTION-TIME guard (doc 10 § "Read-Only And Audit Exception Policy":
    "Service wiring must make that contract mechanical"). A read-only supervisor
    calls this for every store/handle it is about to hold. If the handle
    exposes any :data:`CONTROL_PLANE_WRITER_METHODS` member it IS the
    execution-authority ``ExecutionControlStore`` (those writers are exclusive
    to it); the guard raises :class:`ReadOnlySupervisorViolation`, so the
    supervisor never holds the handle and the execution-authority writer path
    is STRUCTURALLY ABSENT.

    ``None`` handles pass (an absent handle is the safest possible state).
    """

    if handle is None:
        return
    present = _control_plane_writers_present(handle)
    if present:
        raise ReadOnlySupervisorViolation(
            f"read-only supervisor {role!r} was wired with an "
            f"execution-authority (control-plane) writer handle "
            f"({type(handle).__name__}); it exposes control-plane writer "
            f"methods {present!r}. doc 10 § 'Read-Only And Audit Exception "
            f"Policy' requires every execution-authority writer path to be "
            f"absent in default v1 read-only mode."
        )


def assert_read_only_supervisor_handles(
    *,
    feature_store: Any = None,
    artifact_store: Any = None,
    execution_control_store: Any = None,
    extra_handles: dict[str, Any] | None = None,
) -> None:
    """Fail closed unless every supervisor handle satisfies the read-only contract.

    The single mechanical construction guard for a read-only supervisor service
    (doc 10 § "Read-Only And Audit Exception Policy" / § "Refactoring Steps"
    step 8: "Enforce read-only policy in ... MCP service construction").

    Asserts NONE of the supplied handles is/exposes the execution-authority
    ``ExecutionControlStore`` (``assert_no_control_plane_writer`` on each).
    ``execution_control_store`` must therefore be ``None`` for a read-only
    supervisor — an explicit, named slot proving the handle is structurally
    absent, not merely unused. ``feature_store`` / ``artifact_store`` are the
    permitted read surfaces; the guard still rejects them if they somehow
    expose a control-plane writer.

    Raises :class:`ReadOnlySupervisorViolation` on any violation; never
    degrades.
    """

    handles: dict[str, Any] = {
        "feature_store": feature_store,
        "artifact_store": artifact_store,
        "execution_control_store": execution_control_store,
    }
    if extra_handles:
        handles.update(extra_handles)
    for role, handle in handles.items():
        assert_no_control_plane_writer(handle, role=role)


class ReadOnlyAuditArtifactSink:
    """An ``ArtifactStore`` wrapper that allows ONLY supervisor-owned audit puts.

    doc 10 § "Read-Only And Audit Exception Policy": the supervisor receives
    "supervisor-owned audit, dedupe, and display outbox writers" — not an
    unrestricted artifact writer. This wrapper IS that audit-scoped writer:

    * ``put`` to a key in :data:`SUPERVISOR_OWNED_AUDIT_KEY_PREFIXES` passes
      through to the wrapped store (an ALLOWED append-only audit write).
    * ``put`` to ANY other key raises :class:`BlockedExecutionWrite` and does
      NOT perform the mutation — a denied write FAILS CLOSED (doc 10: "Denied
      writes fail closed ... rather than a best-effort mutation").

    The wrapped store's mutating ``write_artifact_bytes`` / ``delete`` are NOT
    re-exposed — a supervisor never reaches them. Read methods are intentionally
    absent: this is a *write* sink (the ``ArtifactSink`` Protocol in
    ``actions.py``), not a general store proxy.

    The caller (``ActionPolicy``) is responsible for writing the blocked-action
    audit ROW after catching :class:`BlockedExecutionWrite`; this sink only
    guarantees the deny is mechanical and non-bypassable.
    """

    def __init__(self, artifact_store: Any) -> None:
        # The wrapped store is the real write surface; a denied key never
        # reaches it. We deliberately do not call
        # ``assert_no_execution_authority_writer`` here — the wrapped store IS
        # an artifact writer (that is the point); this wrapper is what makes
        # it audit-scoped.
        self._artifact_store = artifact_store

    async def put(self, key: str, value: Any, *, feature: Any) -> None:
        """Write a supervisor-owned audit artifact; deny anything else.

        Fails closed (raises :class:`BlockedExecutionWrite`, no mutation) for
        any non-supervisor-owned key.
        """

        if not is_supervisor_owned_audit_key(key):
            raise BlockedExecutionWrite(
                action="artifact_put",
                reason=(
                    f"artifact key {key!r} is not a supervisor-owned audit key; "
                    f"a read-only supervisor may write only "
                    f"{SUPERVISOR_OWNED_AUDIT_KEY_PREFIXES!r}-prefixed audit "
                    f"records (doc 10 § 'Read-Only And Audit Exception "
                    f"Policy')."
                ),
            )
        await self._artifact_store.put(key, value, feature=feature)
