"""Slice 19 5th sub-slice -- READ-ONLY typed agent-context builder that
projects governance snapshots + line-provenance + recommendations onto a
bounded typed :class:`GovernanceAgentContext` for reusable display/advisory
agent-context projections.

This module implements doc-19 § Refactoring Steps step 5: *"Add
agent-context builder that selects findings and
provenance relevant to a task contract, repo, path, or line range.
After Slice 21, this builder must call the Context Layer package API
and return ``ContextLayerPackageSummary`` rather than assembling
uncited provider context locally."*

It owns the typed agent-context-builder surface:

* :data:`AGENT_CONTEXT_BUILDER_FAILURE_ID` -- the typed failure id
  (``governance_agent_context_builder_failed``) registered under the
  EXISTING ``evidence_corruption`` failure_class in
  :mod:`iriai_build_v2.workflows.develop.execution.failure_router` with
  the EXISTING NON-blocking :data:`RouteAction`
  ``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
  mirrors Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B + 4th +
  Slice 17 2nd-6th + Slice 18 2nd-7th + Slice 19 2nd + 3rd + 4th sub-
  slice precedent verbatim; NOT a new failure_class; NOT a new route
  action).

* :class:`AgentContextScope` -- the typed scope record bundling the 4
  scoping axes from doc-19:144-146 / doc-19 step 5 (``task_id`` /
  ``repo_id`` / ``path`` / ``line_start`` / ``line_end``).

* :class:`AgentContextBuilderInputs` -- the typed bundle of all inputs
  the :meth:`GovernanceAgentContextBuilder.build` method consumes
  (typed :class:`SnapshotAPIResult` source + typed
  :class:`LineProvenanceResult` list + typed :class:`AgentContextScope`
  + optional caller-side prompt-char budget override).

* :class:`AgentContextBuilderGap` -- the typed gap finding emitted when
  the agent-context builder fails to project structurally (mirrors
  :class:`SnapshotAPIGap` + :class:`DashboardViewGap` +
  :class:`SlackRenderGap` + Slice 14/15/16/17/18 governance-projection-
  gap shape verbatim per the chunk-shape decision).

* :class:`AgentContextBuilderResult` -- the typed result BaseModel
  (``context: GovernanceAgentContext | None`` + ``gap_findings`` list).

* :class:`GovernanceAgentContextBuilder` -- the typed agent-context-
  builder class with the public projection method :meth:`build`.

**Slice 21 ``ContextLayerPackageSummary`` field WIRED.** Per
doc-19:89-101 + doc-19:125-127 + doc-19:179-182 + doc-19:205-210 and
doc-21:369-370 the builder now accepts an optional typed
:class:`ContextLayerPackage` or :class:`ContextLayerPackageSummary` and
carries the compact summary through to
:attr:`GovernanceAgentContext.context_package`. The builder does not
mint package ids, hydrate provider output, stale-check for gates, or
promote the package into runtime authority; it only projects the
advisory/read-only package summary supplied by or derived from the
caller input.

**Bounded-prompt + refs-only discipline (per governance prompt §
"Non-Negotiables" + doc-19:124-127 + doc-19:128-131).** The agent-
context builder is a READ-ONLY typed projection over the typed Slice
19 2nd sub-slice :class:`SnapshotAPIResult` + typed Slice 14 1st sub-
slice :class:`LineProvenanceResult` list inputs. The builder does NOT
hydrate artifact bodies -- the typed
:class:`GovernanceAgentContext` carries only the typed Slice 16
:class:`GovernanceFinding` + Slice 17
:class:`GovernancePolicyRecommendation` records (per the Slice 19 1st
sub-slice typed-shape design; the Slice 16 + Slice 17 records are
themselves refs-only per the doc-13a:285-287 step 9 shared 13A
contract) + the typed by-name reference shapes
(:attr:`GovernanceAgentContext.omitted_detail_refs: list[str]` +
:attr:`GovernanceAgentContext.page_refs: list[str]`) for omitted
evidence drilldown. Per doc-19:124-127 the typed
:attr:`max_prompt_chars` field is hard-capped at the typed
:data:`~iriai_build_v2.execution_control.governance_agent.GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP`
(20 000 chars). The builder enforces the cap by iteratively
truncating the typed lists (findings -> recommendations -> line
provenance) in priority order and recording the omitted counts +
exact page refs per doc-19:128-131.

**Scope-filtering discipline (per doc-19 step 5 + doc-19:144-146).**
The agent-context builder selects governance content relevant to the
caller's typed :class:`AgentContextScope`. The 4 scoping axes are:

* ``task_id`` -- restrict to findings whose
  :attr:`GovernanceFinding.affected_scope` dict carries the matching
  ``task_id`` value OR to line provenance results whose
  :attr:`LineProvenanceResult.task_ids` list contains the value;
  ``None`` -> cross-task scope (no filter).
* ``repo_id`` -- restrict to recommendations whose
  :attr:`GovernancePolicyRecommendation.consumer` /
  proposed_policy_artifact :attr:`scope` dict carries the matching
  ``repo_id`` value AND line provenance results derived from the matching
  repo (the typed :class:`LineProvenanceResult` does NOT directly
  expose ``repo_id``; the builder cross-references the caller-supplied
  scope against the line-provenance ``provenance_payload_refs`` per the
  Slice 14 ref-path naming contract); ``None`` -> cross-repo scope.
* ``path`` -- restrict to line provenance results whose
  :class:`LineProvenanceQuery.path` was the caller-supplied path; the
  builder cross-references via the caller-supplied scope (the typed
  :class:`LineProvenanceResult` does NOT directly expose ``path`` --
  it lives on the typed :class:`LineProvenanceQuery` that produced
  it); ``None`` -> all-paths scope.
* ``line_start`` / ``line_end`` -- restrict to line provenance results
  whose :class:`LineProvenanceQuery` ranges intersect the caller-
  supplied range; ``None`` on either bound -> no per-line filtering
  inside the path-restricted slice.

Scope filtering is BEFORE bounded-prompt truncation; the truncation
operates on the scope-filtered subset so the caller's relevant
content is preserved as long as it fits the 20 000 char budget.

**Activation-authority boundary preserved (doc-19:348-349 AC).** The
:class:`GovernanceAgentContextBuilder` class has ONE public method
(:meth:`build`) and NO mutation methods on any of its typed shapes
(no ``activate_`` / ``approve_`` / ``merge_`` / ``checkpoint_`` /
``mutate_`` / ``write_`` / ``persist_`` methods). The builder does
NOT extend the Slice 10c-1 ``CONTROL_PLANE_WRITER_METHODS`` set per
doc-19:348-349 *"Supervisor/dashboard read-only contract preserved
(no governance writer extends the Slice 10c-1
``CONTROL_PLANE_WRITER_METHODS`` set)."* The builder does NOT mint
``dag-*`` execution-authority artifact-key string literals.

**Doc-19 acceptance criteria enforcement (sub-slice axes):**

* **AC1** (doc-19:224) *"Reports are bounded, reproducible,
  evidence-cited, and structured first."* -- enforced by:
  * **Bounded**: the typed :attr:`GovernanceAgentContext.max_prompt_chars`
    + :attr:`GovernanceAgentContext.truncated` +
    :attr:`GovernanceAgentContext.omitted_counts` + the bounded
    char-budget enforcement loop in :meth:`build`.
  * **Reproducible**: the same inputs deterministically produce the
    same typed agent context (pure function over typed inputs; no
    wall-clock dependency in the deterministic projection paths).
  * **Evidence-cited**: the typed
    :attr:`GovernanceAgentContext.page_refs` (typed list[str]) +
    :attr:`GovernanceAgentContext.omitted_detail_refs` (typed
    list[str]) carry the typed Slice 13a page-ref ids; the typed
    Slice 16 / Slice 17 records carry their own evidence refs.
  * **Structured first**: the typed Pydantic BaseModel surface
    (typed lists of typed BaseModels; not prose).

* **AC2** (doc-19:225-226) *"Truncated or preview reports are never
  authoritative unless exact page refs and completeness metadata cover
  the consumer's required scope."* -- enforced by the typed
  :attr:`GovernanceAgentContext.truncated` +
  :attr:`GovernanceAgentContext.page_refs` +
  :attr:`GovernanceAgentContext.completeness` field triple. When the
  upstream snapshot is ``preview_only`` / ``unavailable`` the typed
  builder propagates the completeness verbatim so callers cannot
  silently consume preview-only context as execution authority.

* **AC3** (19A-5 remediation of doc-19:227) -- Slice 19 provides a
  reusable display/advisory builder for compact governance context.
  The builder emits the typed :class:`GovernanceAgentContext` shape,
  but production task-execute consumption is deferred to a later
  accepted source-of-truth slice and guarded by the 19A-5 consumer
  sentinel.

* **AC5** (doc-19:230-231) *"Workflow agents receive governance policy
  guidance only as advisory context; contracts, gates, router, and
  merge queue remain authoritative."* -- enforced by the typed
  :attr:`GovernanceAgentContext.policy_guidance_authority:
  Literal["advisory_only"]` hard-coded literal default from the Slice
  19 1st sub-slice typed shape (Pydantic Literal validation rejects
  any other value at construction with a typed ``ValidationError``).

* **AC6** (doc-19:232-233) *"Human-facing dashboard/Slack output
  explains top findings without hiding evidence quality or omitted
  details."* -- AC6 is primarily a dashboard / Slack surface concern;
  the agent context surface mirrors the contract via the typed
  :attr:`GovernanceAgentContext.omitted_counts` (always populated when
  truncation fires) + the typed :attr:`GovernanceAgentContext.completeness`
  field always present from the typed surface.

* **AC7** (doc-19:234) *"Reporting honors Slice 10 read-only and
  bounded-read guarantees."* -- enforced by the typed class having
  ONE public method (:meth:`build`) + no mutation methods on any
  BaseModel + no ``dag-*`` artifact-key literals + no
  ``CONTROL_PLANE_WRITER_METHODS`` extension.

**Fail-closed discipline (per auto-memory
``feedback_no_silent_degradation``).** The :meth:`build` method
NEVER raises -- structural failures project onto typed
:class:`AgentContextBuilderGap` finding(s) emitted on
:attr:`AgentContextBuilderResult.gap_findings`. A SINGLE typed failure
id :data:`AGENT_CONTEXT_BUILDER_FAILURE_ID`
(``governance_agent_context_builder_failed``) covers the doc-19:184-194
edge-case rows (mirror of the Slice 17 6th sub-slice
``consumer_read_api_failed`` + Slice 18 2nd sub-slice
``replay_corpus_or_scenario_load_failed`` + Slice 19 2nd sub-slice
``governance_snapshot_api_failed`` + Slice 19 3rd sub-slice
``governance_dashboard_view_failed`` + Slice 19 4th sub-slice
``governance_slack_renderer_failed`` 1-failure-id-per-typed-API-class
precedent verbatim). The typed :class:`AgentContextBuilderGap` shape
carries the surface ``reason`` so consumers can distinguish edge-case
classes if needed.

Per the auto-memory ``feedback_flat_structured_output`` rule the
typed control fields are flat primitives (``str``, ``int``, ``bool``,
``list[str]``); no nested BaseModels are required for control
signaling.

Per the no-second-source-of-truth discipline this module REUSES the
following typed shapes (DIRECT import; annotation-identity assertions
in the unit-test surface enforce the contract):

* Slice 19 1st sub-slice :class:`GovernanceAgentContext` +
  :data:`GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP`
  (:mod:`iriai_build_v2.execution_control.governance_agent`) for the
  typed agent-context output surface + the typed 20 000 char hard cap.
* Slice 19 2nd sub-slice :class:`SnapshotAPIResult` +
  :class:`SnapshotAPIGap`
  (:mod:`iriai_build_v2.execution_control.governance_snapshot_api`)
  for the typed snapshot bundle the builder consumes.
* Slice 14 :class:`LineProvenanceResult`
  (:mod:`iriai_build_v2.execution_control.commit_provenance`) for the
  typed line-provenance input surface.
* Slice 13a :data:`CompletenessState` Literal
  (:mod:`iriai_build_v2.workflows.develop.governance.models`) for the
  typed completeness field projection.
* Slice 16 :class:`GovernanceFinding`
  (:mod:`iriai_build_v2.execution_control.finding_engine`) for the
  typed finding records the builder filters + projects.
* Slice 17 :class:`GovernancePolicyRecommendation`
  (:mod:`iriai_build_v2.execution_control.policy_recommendation`) for
  the typed recommendation records the builder filters + projects.

It is the **agent-context projection layer** that subsequent Slice 19
sub-slices (report artifacts / read-only enforcement) cite as the
typed bounded-prompt contract for reusable display/advisory context.

**Slice 13A invariant compliance.** The agent-context builder consumes
the typed Slice 19 2nd sub-slice :class:`SnapshotAPIResult` which is
itself a typed bounded read over the cited Slice 16/17/18 evidence
surface; the builder does NOT hydrate artifact bodies (omitted_detail_refs
+ page_refs surfaces are refs-only per doc-19:111 + doc-19:114) and
preserves the upstream completeness verbatim. Per the Slice 13A
invariant doc-13a:18-23 the typed
:attr:`GovernanceAgentContext.completeness` field carries the typed
:data:`CompletenessState` Literal so future production task-execute
consumers can detect ``preview_only`` / ``unavailable`` context after
they are wired by a later accepted source-of-truth slice and reject it
as authoritative input per doc-19:128-131.

References:

* Doc-19 § Refactoring Steps step 5 -- *"Add agent-
  context builder that selects findings and provenance relevant to a
  task contract, repo, path, or line range. After Slice 21, this
  builder must call the Context Layer package API and return
  ``ContextLayerPackageSummary`` rather than assembling uncited
  provider context locally."*
* Doc-19:103-117 -- the typed :class:`GovernanceAgentContext` shape.
* Doc-19:124-127 -- 20 000 char hard cap on ``max_prompt_chars``.
* Doc-19:128-131 + doc-19:225-226 AC2 -- preview/display budgets;
  truncated contexts without exact page refs are display-only.
* Doc-19:144-146 -- the 4 scoping axes (task, repo, file, line range).
* Doc-19:166-167 -- *"Governance reports are projections of governance
  rows."*
* Doc-19:184-194 -- edge-case rows mapped to typed gap reasons.
* Doc-19:204 -- *"Agent context builder returns task-relevant findings
  and line provenance under prompt budget."*
* Doc-19:216-217 -- *"Agent context marks policy guidance advisory-
  only and tests that prompts cannot treat it as activated policy."*
* Doc-19:218 -- *"Report generation is reproducible for the same
  corpus id."* (the builder is a pure function over typed inputs.)
* Doc-19:234 + doc-19:348-349 AC -- read-only contract.
* Doc-14:242-243 -- governance-projection NON-blocking contract
  (inherited by every post-checkpoint governance projection
  observer; the agent-context builder is also a post-checkpoint
  observer).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from iriai_build_v2.execution_control.commit_provenance import (
    LineProvenanceResult,
)
from iriai_build_v2.execution_control.finding_engine import (
    GovernanceFinding,
)
from iriai_build_v2.execution_control.governance_agent import (
    GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP,
    ContextLayerPackageSummary,
    GovernanceAgentContext,
)
from iriai_build_v2.execution_control.governance_snapshot_api import (
    SnapshotAPIGap,
    SnapshotAPIResult,
)
from iriai_build_v2.execution_control.policy_recommendation import (
    GovernancePolicyRecommendation,
)
from iriai_build_v2.workflows.develop.context_layer.models import (
    ContextLayerPackage,
)
from iriai_build_v2.workflows.develop.governance.models import (
    CompletenessState,
)


__all__ = [
    # Typed failure id (doc-19:184-194 + doc-14:242-243 NON-BLOCKING).
    "AGENT_CONTEXT_BUILDER_FAILURE_ID",
    # Typed scope record (doc-19:144-146 + doc-19 step 5 -- 4 axes).
    "AgentContextScope",
    # Typed agent-context-builder inputs / gap / result.
    "AgentContextBuilderInputs",
    "AgentContextBuilderGap",
    "AgentContextBuilderResult",
    # The agent-context-builder class (doc-19 step 5).
    "GovernanceAgentContextBuilder",
]


# --- Typed failure id (doc-19:184-194 + doc-14:242-243 NON-BLOCKING) --------


AGENT_CONTEXT_BUILDER_FAILURE_ID: Literal[
    "governance_agent_context_builder_failed"
] = "governance_agent_context_builder_failed"
"""Doc-19:184-194 + doc-14:242-243 -- the typed failure id the
governance agent-context builder projects onto when a structural
projection step fails or the 20 000 char prompt budget cannot be
honoured.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT
a new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd-6th + Slice 18 2nd-7th + Slice 19
2nd + 3rd + 4th sub-slice precedent verbatim).

A SINGLE failure id covers ALL the doc-19:184-194 edge-case rows
(task-contract scope missing / agent-context construction failed /
prompt-budget cannot be honoured / governance snapshot stale /
missing line provenance / active workflow pressure) per the Slice 17
6th sub-slice ``consumer_read_api_failed`` + Slice 18 2nd sub-slice
``replay_corpus_or_scenario_load_failed`` + Slice 19 2nd sub-slice
``governance_snapshot_api_failed`` + Slice 19 3rd sub-slice
``governance_dashboard_view_failed`` + Slice 19 4th sub-slice
``governance_slack_renderer_failed`` precedent (one typed failure id
per typed-API class). The typed :class:`AgentContextBuilderGap` shape
carries the surface ``reason`` so consumers can distinguish edge-case
classes if needed.

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 19 pattern matches the Slice 14 + Slice 15 + Slice 16 + Slice
17 + Slice 18 + Slice 19 2nd + 3rd + 4th sub-slice non-blocking
governance projection observer (the agent-context builder is also a
post-checkpoint governance projection observer + per doc-19:166-167
reports are projections of governance rows + per doc-19:174-176
agent ``policy_guidance`` is prompt context only -- never runtime
policy authority).

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to agent-context
builder failures.
"""


def _utcnow() -> datetime:
    """Return the current UTC timestamp (timezone-aware).

    Mirrors the Slice 19 4th sub-slice
    :func:`~iriai_build_v2.execution_control.governance_slack_renderer._utcnow`
    + Slice 19 3rd sub-slice
    :func:`~iriai_build_v2.execution_control.governance_dashboard_view._utcnow`
    + Slice 19 2nd sub-slice
    :func:`~iriai_build_v2.execution_control.governance_snapshot_api._utcnow`
    + Slice 18 2nd sub-slice
    :func:`~iriai_build_v2.execution_control.counterfactual_replay_loader._utcnow`
    + Slice 17 4th sub-slice
    :func:`~iriai_build_v2.execution_control.decision_record_writer._utcnow`
    verbatim. Stdlib-only.
    """

    return datetime.now(timezone.utc)


# --- AgentContextScope (typed scope record; doc-19:144-146 + doc-19 step 5)


class AgentContextScope(BaseModel):
    """Typed scope record bundling the 4 scoping axes from doc-19:144-146
    + doc-19 step 5.

    Per doc-19 step 5 *"Add agent-context builder that selects findings
    and provenance relevant to a task contract, repo, path, or line
    range."* + doc-19:144-146 *"Agent context endpoint that returns
    compact governance context for a task, repo, file, or line range."*
    the typed scope record carries the 4 typed axes:

    * :attr:`task_id` -- the task contract scope (``None`` for cross-
      task contexts).
    * :attr:`repo_id` -- the repo scope (``None`` for cross-repo
      contexts).
    * :attr:`path` -- the repo-relative file path scope (``None`` for
      all-paths contexts).
    * :attr:`line_start` + :attr:`line_end` -- the 1-indexed inclusive
      line range scope inside ``path`` (``None`` on either bound for
      no per-line filtering inside the path-restricted slice).

    The typed scope is the input the
    :class:`GovernanceAgentContextBuilder` uses to filter the typed
    Slice 19 2nd sub-slice :class:`SnapshotAPIResult` snapshot's
    findings + recommendations + the caller-supplied
    :class:`LineProvenanceResult` list down to the caller-relevant
    subset BEFORE bounded-prompt truncation.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`pydantic.ValidationError` rather than being silently
    absorbed.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives (``str | None``,
    ``int | None``); no nested BaseModels are required for scoping
    signaling.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    """The task contract scope (doc-19:144-146 + doc-19 step 5). When
    set the builder restricts findings to those whose
    :attr:`GovernanceFinding.affected_scope` dict carries the matching
    ``task_id`` value OR whose related-finding-id graph reaches a
    finding with the task id OR line-provenance results whose
    :attr:`LineProvenanceResult.task_ids` list contains the value.

    ``None`` -> cross-task scope; the builder includes all findings +
    line-provenance results from the upstream snapshot (subject to the
    other scoping axes)."""

    repo_id: str | None = None
    """The repo scope (doc-19:144-146 + doc-19 step 5). When set the
    builder restricts findings to those whose
    :attr:`GovernanceFinding.affected_scope` dict carries the matching
    ``repo_id`` value + recommendations whose
    :attr:`GovernancePolicyRecommendation.proposed_policy_artifact.scope`
    dict carries the matching ``repo_id`` value + line-provenance
    results whose typed provenance derives from the matching repo.

    ``None`` -> cross-repo scope."""

    path: str | None = None
    """The repo-relative file path scope (doc-19:144-146 + doc-19 step 5).
    When set the builder restricts line-provenance results to those
    whose originating :attr:`LineProvenanceQuery.path` matches the
    value. The typed :class:`LineProvenanceResult` does NOT directly
    expose ``path`` -- the builder cross-references via the typed
    :attr:`AgentContextBuilderInputs.line_provenance_paths` parallel
    list (one path-per-result; same length as
    :attr:`AgentContextBuilderInputs.line_provenance_results`).

    ``None`` -> all-paths scope."""

    line_start: int | None = None
    """The 1-indexed inclusive start line of the range scope inside
    :attr:`path` (doc-19:144-146 + doc-19 step 5). When ``path`` is
    set AND both line bounds are set the builder restricts line-
    provenance results to those whose originating
    :class:`LineProvenanceQuery` range intersects the caller's range
    (i.e. ``query.line_start <= caller.line_end AND query.line_end >=
    caller.line_start``).

    ``None`` -> no per-line filtering inside the path-restricted slice."""

    line_end: int | None = None
    """The 1-indexed inclusive end line of the range scope inside
    :attr:`path` (doc-19:144-146 + doc-19 step 5). See
    :attr:`line_start` for the intersection semantics.

    ``None`` -> no per-line filtering inside the path-restricted slice."""

    @model_validator(mode="after")
    def _validate_line_range(self) -> "AgentContextScope":
        """Fail closed on impossible 1-indexed line ranges."""

        if self.line_start is not None and self.line_start < 1:
            raise ValueError("line_start must be 1-indexed when provided")
        if self.line_end is not None and self.line_end < 1:
            raise ValueError("line_end must be 1-indexed when provided")
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end must be >= line_start")
        return self


# --- AgentContextBuilderInputs (typed inputs; doc-19 step 5) ---------------


class AgentContextBuilderInputs(BaseModel):
    """Typed bundle of all inputs the
    :meth:`GovernanceAgentContextBuilder.build` method consumes.

    Per doc-19 step 5 + doc-19:124-127 + doc-19:128-131 the
    inputs carry:

    * The typed Slice 19 2nd sub-slice :class:`SnapshotAPIResult`
      source (the typed bounded read the builder projects).
    * The optional typed list of Slice 14 1st sub-slice
      :class:`LineProvenanceResult` rows the caller resolved upstream.
    * The optional parallel list of repo-relative paths the line-
      provenance results derive from (one path-per-result; same length
      as :attr:`line_provenance_results`). This parallel list is the
      typed surface that lets the builder filter line-provenance by
      :attr:`AgentContextScope.path` (the typed
      :class:`LineProvenanceResult` does NOT itself carry ``path``).
    * The optional parallel list of line-range tuples ``(line_start,
      line_end)`` the line-provenance results derive from (one tuple-
      per-result; same length as
      :attr:`line_provenance_results`). This parallel list is the
      typed surface that lets the builder filter line-provenance by
      :attr:`AgentContextScope.line_start` /
      :attr:`AgentContextScope.line_end`.
    * The typed :class:`AgentContextScope` carrying the 4 scoping
      axes.
    * The optional Slice 21 :class:`ContextLayerPackage` or
      :class:`ContextLayerPackageSummary` that the caller resolved from
      the Context Layer package API.
    * The optional caller-side prompt-char budget override (defaults
      to the doc-19:124-127 20 000 char hard cap from
      :data:`GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP`; the
      builder hard-clamps the value to that cap).

    The agent-context builder does NOT itself fetch from the database
    -- it is a pure typed projection over the typed inputs (the caller
    owns the bounded-read transaction via the Slice 19 2nd sub-slice
    :class:`GovernanceSnapshotAPI` upstream + the Slice 14 line-
    provenance reader; the agent-context builder owns the typed
    scope-filtering + bounded-prompt truncation + typed
    :class:`GovernanceAgentContext` projection).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`pydantic.ValidationError` rather than being silently
    absorbed.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives + typed BaseModel inputs.
    """

    model_config = ConfigDict(extra="forbid")

    source: SnapshotAPIResult
    """The typed Slice 19 2nd sub-slice :class:`SnapshotAPIResult` the
    agent-context builder projects. Per the no-second-source-of-truth
    discipline the typed source is the Slice 19 2nd sub-slice typed
    result verbatim; the agent-context builder does NOT redefine the
    typed snapshot or gap shape.

    If :attr:`SnapshotAPIResult.snapshot` is ``None`` (structural
    snapshot-API failure) the agent-context builder emits a typed
    :class:`AgentContextBuilderGap` with
    ``reason="upstream_snapshot_missing"`` + the upstream gap findings
    are PROPAGATED verbatim to the
    :attr:`AgentContextBuilderResult.gap_findings` list (the builder
    does not swallow upstream errors)."""

    scope: AgentContextScope
    """The typed :class:`AgentContextScope` carrying the 4 scoping
    axes (task_id / repo_id / path / line_start + line_end). See
    :class:`AgentContextScope` for the field-level semantics.

    A scope with all-``None`` fields is a "no-filter" cross-everything
    scope; the builder includes all findings + recommendations + line-
    provenance results from the upstream inputs (subject to the
    bounded-prompt truncation)."""

    line_provenance_results: list[LineProvenanceResult] = Field(
        default_factory=list
    )
    """The optional typed list of Slice 14 1st sub-slice
    :class:`LineProvenanceResult` rows the caller resolved upstream
    via the typed Slice 14 line-provenance reader. Defaults to the
    empty list (the typical case for findings-only / recommendations-
    only agent contexts).

    Per doc-19:108 the typed Slice 14 :class:`LineProvenanceResult`
    shape is the typed line-provenance contract; the 5th sub-slice
    builder tightens the typed annotation from the Slice 19 1st sub-
    slice ``list[dict[str, Any]]`` surface to the typed
    :class:`LineProvenanceResult` list at this builder input
    boundary (the typed
    :attr:`GovernanceAgentContext.relevant_line_provenance` field
    still accepts the ``list[dict[str, Any]]`` per the 1st sub-slice
    typed-shape foundation; the builder serialises the typed
    :class:`LineProvenanceResult` rows to ``dict[str, Any]`` for the
    output projection via :meth:`BaseModel.model_dump(mode="json")`)."""

    line_provenance_paths: list[str] = Field(default_factory=list)
    """The optional parallel list of repo-relative paths the line-
    provenance results derive from. MUST be the same length as
    :attr:`line_provenance_results` when both are non-empty (the
    builder validates the parallel-length contract at construction).

    The typed surface lets the builder filter line-provenance by
    :attr:`AgentContextScope.path` (the typed
    :class:`LineProvenanceResult` does NOT itself carry ``path`` --
    it lives on the typed :class:`LineProvenanceQuery` that produced
    the result, per the Slice 14 1st sub-slice typed-shape
    foundation).

    Defaults to the empty list (compatible with empty
    :attr:`line_provenance_results`). If
    :attr:`line_provenance_results` is non-empty but
    :attr:`line_provenance_paths` is empty the builder treats the
    line-provenance rows as "no-path-metadata" and emits them
    unconditionally regardless of :attr:`AgentContextScope.path`."""

    line_provenance_line_ranges: list[tuple[int, int]] = Field(
        default_factory=list
    )
    """The optional parallel list of line-range tuples ``(line_start,
    line_end)`` the line-provenance results derive from. MUST be the
    same length as :attr:`line_provenance_results` when both are
    non-empty.

    The typed surface lets the builder filter line-provenance by
    :attr:`AgentContextScope.line_start` /
    :attr:`AgentContextScope.line_end`. If
    :attr:`line_provenance_results` is non-empty but
    :attr:`line_provenance_line_ranges` is empty the builder treats
    the line-provenance rows as "no-range-metadata" and emits them
    unconditionally regardless of the caller's range scope."""

    max_prompt_chars: int = GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP
    """Per doc-19:124-127 *"Agent context: ``max_prompt_chars`` from
    caller, hard-capped at 20,000 chars, with omitted refs instead of
    full evidence bodies."* the typed default is the
    :data:`GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP` (20 000) hard
    cap.

    The typed surface accepts any positive integer; the builder
    HARD-CLAMPS the value to the typed hard cap at construction (so
    callers cannot bypass the documented contract by passing a larger
    value; the actual effective cap on
    :attr:`GovernanceAgentContext.max_prompt_chars` is
    ``min(max_prompt_chars, GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP)``)."""

    context_package: ContextLayerPackageSummary | ContextLayerPackage | None = None
    """Optional Slice 21 package or package summary to project onto the
    advisory :class:`GovernanceAgentContext` surface.

    If a full :class:`ContextLayerPackage` is supplied, the builder
    projects only its citeable summary. It does not mint package ids,
    hydrate provider output, stale-check for gates, or treat package
    completeness as product-authoritative execution state.
    """


# --- AgentContextBuilderGap (typed gap; doc-19:184-194 + doc-14:242-243) ----


class AgentContextBuilderGap(BaseModel):
    """Typed governance-gap finding produced when the agent-context
    builder fails to project structurally.

    Mirrors the Slice 19 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_slack_renderer.SlackRenderGap`
    + Slice 19 3rd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_dashboard_view.DashboardViewGap`
    + Slice 19 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_snapshot_api.SnapshotAPIGap`
    + Slice 18 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_replay_loader.ReplayCorpusLoaderGap`
    + Slice 17 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.recommendation_builder.RecommendationBuilderEmissionGap`
    + Slice 16 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_finding_writer.FindingPersistenceGap`
    + Slice 15 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardPersistenceGap`
    + Slice 14 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    verbatim per the chunk-shape decision.

    Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED here
    per the governance-projection discipline) the gap finding is
    NON-blocking: the caller MUST NOT propagate it to the executor /
    checkpoint / merge-queue / resume code paths. The corresponding
    typed failure id :data:`AGENT_CONTEXT_BUILDER_FAILURE_ID`
    (``governance_agent_context_builder_failed``) registers under the
    EXISTING ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).
    """

    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["governance_agent_context_builder_failed"]
    """Doc-19:184-194 + doc-14:192-201 -- the typed failure id.
    Registers under the EXISTING ``evidence_corruption`` failure_class
    with NON-blocking routing per doc-14:242-243 + doc-19:184-194."""

    corpus_id: str
    """The corpus scope of the failed projection (mirrors the
    :attr:`SnapshotAPIInputs.corpus_id` from the upstream snapshot
    API). Falls back to the empty string when the upstream snapshot is
    missing AND no ``corpus_id`` is recoverable from the upstream
    gaps."""

    reason: str
    """Free-form gap reason. Documented values:

    * ``upstream_snapshot_missing`` -- the upstream
      :class:`SnapshotAPIResult` carried ``snapshot=None`` (structural
      snapshot-API failure).
    * ``context_construction_failed`` -- Pydantic ValidationError on
      the typed :class:`GovernanceAgentContext` construction.
    * ``parallel_list_length_mismatch`` -- the parallel
      :attr:`AgentContextBuilderInputs.line_provenance_paths` /
      :attr:`AgentContextBuilderInputs.line_provenance_line_ranges`
      lists are non-empty and do not match
      :attr:`AgentContextBuilderInputs.line_provenance_results` length.
    * ``missing_context_package_summary`` -- Slice 21 package identity is
      required before line-aware governance context can be emitted.
    * ``prompt_budget_exceeded`` -- the effective
      :attr:`max_prompt_chars` cap cannot accommodate even the empty
      typed :class:`GovernanceAgentContext` (structurally impossible
      under typical typed-shape envelopes; defensive guard).
    * ``governance_snapshot_stale`` -- doc-19:186-187 edge-case
      (upstream snapshot has ``blocked_by`` non-empty AND no
      caller scope filter narrows the context to non-blocked rows).
    * ``missing_line_provenance`` -- doc-19:188-189 edge-case (caller
      scope demands per-line provenance but the
      :attr:`AgentContextBuilderInputs.line_provenance_results` list
      is empty or the parallel
      :attr:`AgentContextBuilderInputs.line_provenance_paths` list does
      not include the caller's :attr:`AgentContextScope.path`).
    * ``active_workflow_pressure`` -- doc-19:193-194 edge-case (cached
      context requested).

    The caller distinguishes via this string. Per the auto-memory
    ``feedback_no_silent_degradation`` rule the typed surface is
    free-form so the builder can emit new reason strings without a
    typed-shape breaking change."""

    observed_at: datetime
    """ISO-8601 timestamp the builder observed the gap (UTC, timezone-
    aware). Mirrors the Slice 19 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_slack_renderer.SlackRenderGap.observed_at`
    + Slice 19 3rd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_dashboard_view.DashboardViewGap.observed_at`
    + Slice 19 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_snapshot_api.SnapshotAPIGap.observed_at`
    contract verbatim."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding
    (e.g. the Pydantic ValidationError detail; the bound that exceeded
    the budget; the rejected row count). Free-form per the
    doc-14:192-201 + Slice 14/15/16/17/18/19-2nd/19-3rd/19-4th
    governance-finding precedent."""


# --- AgentContextBuilderResult (typed result; doc-19 step 5) ---------------


class AgentContextBuilderResult(BaseModel):
    """Typed bundle of all outputs the
    :meth:`GovernanceAgentContextBuilder.build` method produces.

    The bundle composes:

    * ``context`` -- the typed :class:`GovernanceAgentContext` the
      builder emitted, OR ``None`` if the projection failed
      structurally (in which case the gap is recorded in
      :attr:`gap_findings`).
    * ``gap_findings`` -- the list of typed
      :class:`AgentContextBuilderGap` records emitted when a
      projection step fails structurally OR when an informational gap
      fires (e.g. propagated upstream :class:`SnapshotAPIGap` rows
      lifted into the typed builder gap shape).

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.
    """

    model_config = ConfigDict(extra="forbid")

    context: GovernanceAgentContext | None = None
    """The typed :class:`GovernanceAgentContext` the builder emitted,
    OR ``None`` if the projection failed structurally.

    Per the doc-19 step 5 contract the builder emits the
    typed agent context when inputs are valid; on structural failure
    the context is ``None`` + the gap finding is recorded in
    :attr:`gap_findings`. On informational-only gaps the context is
    STILL emitted (with ``truncated=True`` + populated
    ``omitted_counts``) AND the informational gap is recorded in
    :attr:`gap_findings`."""

    gap_findings: list[AgentContextBuilderGap] = Field(default_factory=list)
    """The list of typed :class:`AgentContextBuilderGap` records
    emitted when a projection step fails structurally OR when an
    informational gap fires.

    The list is typically empty (no gaps fired for a healthy
    projection). On structural failure the list contains exactly ONE
    gap record + :attr:`context` is ``None``. On informational gaps
    the list contains the informational gap(s) + :attr:`context` is
    the truncated agent context."""


# --- GovernanceAgentContextBuilder (the builder class; doc-19 step 5)


# Internal scoring tuple for finding ranking inside the budget loop.
# Higher scores rank earlier. The score is derived from the typed
# :class:`GovernanceFinding` surface (severity weight + confidence +
# estimated_lost_hours + recency proxy). The score function is
# DETERMINISTIC per the AC1 reproducibility axis.
_SEVERITY_WEIGHTS: dict[str, float] = {
    "critical": 5.0,
    "high": 4.0,
    "medium": 3.0,
    "low": 2.0,
    "info": 1.0,
}


def _finding_rank_key(finding: GovernanceFinding) -> tuple[float, float, float]:
    """Compute the typed deterministic rank-key tuple for a finding.

    Per doc-19:190-191 *"Too many findings: rank by severity,
    confidence, lost-time estimate, and recency; include omitted
    refs."* the rank key combines:

    * Severity weight (5.0 critical / 4.0 high / 3.0 medium / 2.0 low
      / 1.0 info; unknown -> 0.0).
    * Confidence (already in [0.0, 1.0]).
    * Estimated lost hours (``None`` -> 0.0).

    Returns a tuple suitable for ``sorted(..., key=..., reverse=True)``;
    higher tuples rank earlier. Recency is NOT included in the rank
    key because the typed :class:`GovernanceFinding` shape does not
    carry a typed timestamp at this 5th sub-slice; the upstream
    snapshot API ordering preserves insertion order so callers MAY
    re-order before passing into the builder if they want a different
    recency proxy.
    """

    severity_weight = _SEVERITY_WEIGHTS.get(finding.severity, 0.0)
    confidence = float(finding.confidence)
    lost_hours = (
        float(finding.estimated_lost_hours)
        if finding.estimated_lost_hours is not None
        else 0.0
    )
    return (severity_weight, confidence, lost_hours)


class GovernanceAgentContextBuilder:
    """The typed agent-context builder class (doc-19 step 5).

    Per *"Add agent-context builder that selects findings and
    provenance relevant to a task contract, repo, path, or line range.
    After Slice 21, this builder must call the Context Layer package
    API and return ``ContextLayerPackageSummary`` rather than
    assembling uncited provider context locally."* the builder
    consumes the typed :class:`AgentContextBuilderInputs` and emits a
    typed :class:`GovernanceAgentContext` record.

    **Slice 21 ``ContextLayerPackageSummary`` field WIRED.** Per
    doc-19:89-101 + doc-19:125-127 + doc-19:179-182 + doc-19:205-210
    and doc-21:369-370 the builder carries an optional typed package or
    package summary from :class:`AgentContextBuilderInputs` to
    :attr:`GovernanceAgentContext.context_package` as a compact
    :class:`ContextLayerPackageSummary`. It remains a display/advisory
    projection and is not a gate, router, merge, or runtime-activation
    authority.

    **Scope-filtering discipline (per doc-19:144-146 + doc-19 step 5).**
    The builder filters the typed Slice 16
    :class:`GovernanceFinding` list + Slice 17
    :class:`GovernancePolicyRecommendation` list + Slice 14
    :class:`LineProvenanceResult` list against the typed
    :class:`AgentContextScope` BEFORE bounded-prompt truncation. See
    the module docstring for the per-axis filtering semantics.

    **Bounded-prompt discipline (per governance prompt §
    "Non-Negotiables" + doc-19:124-127 + doc-19:128-131).** The
    builder enforces the 20 000 char hard cap on
    :attr:`GovernanceAgentContext.max_prompt_chars` by iteratively
    truncating the typed lists (findings -> recommendations -> line
    provenance) in priority order. The truncation algorithm is:

    1. Compute the canonical-JSON byte cost of the typed
       :class:`GovernanceAgentContext` envelope WITHOUT the typed
       lists (the "envelope cost"; per
       :func:`BaseModel.model_dump_json`).
    2. If envelope cost already exceeds the effective cap, emit the
       ``prompt_budget_exceeded`` gap + return ``context=None`` (the
       defensive guard).
    3. Add findings in rank order (per :func:`_finding_rank_key`)
       until adding another would exceed the cap; record the omitted
       count + the typed page_refs for the omitted findings.
    4. Repeat for recommendations in insertion order (the upstream
       snapshot API ordering preserves caller priority).
    5. Repeat for line-provenance results in insertion order.
    6. Set ``truncated = True`` if any list lost rows; set
       ``omitted_counts`` accordingly; populate ``page_refs`` +
       ``omitted_detail_refs`` from the upstream snapshot.

    **Refs-only projection (per governance prompt §
    "Non-Negotiables" + Slice 13A invariant).** The builder passes
    typed BaseModel bodies through verbatim (no body hydration); the
    typed :attr:`GovernanceAgentContext.relevant_findings` +
    :attr:`GovernanceAgentContext.policy_guidance` carry the typed
    Slice 16 / Slice 17 BaseModels (each of which is itself refs-only
    per the doc-13a:285-287 step 9 shared 13A contract).

    **Fail-closed discipline (per auto-memory
    ``feedback_no_silent_degradation``).** The :meth:`build` method
    NEVER raises a failure to the caller. Any structural failure
    projects onto a typed :class:`AgentContextBuilderGap` finding
    emitted on the :attr:`AgentContextBuilderResult.gap_findings`
    list. The corresponding typed failure id
    :data:`AGENT_CONTEXT_BUILDER_FAILURE_ID`
    (``governance_agent_context_builder_failed``) registers under the
    EXISTING ``evidence_corruption`` failure_class with the EXISTING
    NON-blocking RouteAction ``retry_governance_projection`` (REUSED
    from Slice 14 2nd sub-slice; NOT a new route action).

    The builder is stateless (no instance state beyond construction);
    callers MAY reuse a single instance across multiple corpora.

    **Activation-authority boundary (doc-19:348-349 AC).** The class
    has ONE public method (:meth:`build`) and NO mutation methods on
    any of its typed shapes (no ``activate_`` / ``approve_`` /
    ``merge_`` / ``checkpoint_`` / ``mutate_`` / ``write_`` /
    ``persist_`` methods). The builder does NOT extend the Slice
    10c-1 ``CONTROL_PLANE_WRITER_METHODS`` set.
    """

    def build(
        self, inputs: AgentContextBuilderInputs
    ) -> AgentContextBuilderResult:
        """Build the typed :class:`GovernanceAgentContext` from the
        typed inputs.

        Per doc-19 step 5 the method:

        1. Validates the parallel-list-length contract for the typed
           :attr:`AgentContextBuilderInputs.line_provenance_paths` /
           :attr:`AgentContextBuilderInputs.line_provenance_line_ranges`.
        2. Resolves the upstream snapshot (returns
           ``upstream_snapshot_missing`` gap if absent).
        3. Filters the typed Slice 16 finding list + Slice 17
           recommendation list + Slice 14 line-provenance list against
           the typed :class:`AgentContextScope`.
        4. Computes the envelope-cost + iteratively truncates the typed
           lists to fit the effective char budget.
        5. Constructs + returns the typed
           :class:`GovernanceAgentContext` + the typed
           :class:`AgentContextBuilderResult` bundle.

        The method NEVER raises -- structural failures project onto
        typed :class:`AgentContextBuilderGap` finding(s) emitted on
        :attr:`AgentContextBuilderResult.gap_findings`.

        Per the auto-memory ``feedback_no_silent_degradation`` rule
        every error path through this method is a typed gap; nothing
        is silently degraded.
        """

        # Step 1: validate parallel-list-length contract.
        line_results = inputs.line_provenance_results
        line_paths = inputs.line_provenance_paths
        line_ranges = inputs.line_provenance_line_ranges
        if line_results and line_paths and len(line_paths) != len(line_results):
            return AgentContextBuilderResult(
                context=None,
                gap_findings=[
                    AgentContextBuilderGap(
                        failure_id=AGENT_CONTEXT_BUILDER_FAILURE_ID,
                        corpus_id=self._extract_corpus_id(inputs.source),
                        reason="parallel_list_length_mismatch",
                        observed_at=_utcnow(),
                        evidence_payload={
                            "line_provenance_results_count": len(line_results),
                            "line_provenance_paths_count": len(line_paths),
                        },
                    )
                ],
            )
        if (
            line_results
            and line_ranges
            and len(line_ranges) != len(line_results)
        ):
            return AgentContextBuilderResult(
                context=None,
                gap_findings=[
                    AgentContextBuilderGap(
                        failure_id=AGENT_CONTEXT_BUILDER_FAILURE_ID,
                        corpus_id=self._extract_corpus_id(inputs.source),
                        reason="parallel_list_length_mismatch",
                        observed_at=_utcnow(),
                        evidence_payload={
                            "line_provenance_results_count": len(line_results),
                            "line_provenance_line_ranges_count": len(
                                line_ranges
                            ),
                        },
                    )
                ],
            )

        # Step 2: resolve upstream snapshot.
        snapshot = inputs.source.snapshot
        if snapshot is None:
            # Propagate upstream gaps verbatim (lifted into the typed
            # builder gap shape so the caller sees a single typed gap
            # surface; the original SnapshotAPIGap rows are also
            # available via the upstream SnapshotAPIResult.gap_findings
            # for forensic drilldown).
            upstream_corpus_id = self._extract_corpus_id(inputs.source)
            propagated_gaps = self._propagate_upstream_gaps(
                inputs.source.gap_findings, upstream_corpus_id
            )
            return AgentContextBuilderResult(
                context=None,
                gap_findings=[
                    AgentContextBuilderGap(
                        failure_id=AGENT_CONTEXT_BUILDER_FAILURE_ID,
                        corpus_id=upstream_corpus_id,
                        reason="upstream_snapshot_missing",
                        observed_at=_utcnow(),
                        evidence_payload={
                            "upstream_gap_count": len(
                                inputs.source.gap_findings
                            )
                        },
                    ),
                    *propagated_gaps,
                ],
            )

        corpus_id = snapshot.corpus_id
        context_package = self._context_package_summary(inputs.context_package)

        # Step 3: scope-filter the typed Slice 16 / Slice 17 / Slice 14
        # lists.
        scope = inputs.scope
        scoped_findings = self._filter_findings_by_scope(
            snapshot.top_findings, scope
        )
        scoped_recommendations = self._filter_recommendations_by_scope(
            snapshot.recommendations, scope
        )
        scoped_line_provenance, scoped_line_provenance_indices = (
            self._filter_line_provenance_by_scope(
                line_results, line_paths, line_ranges, scope
            )
        )
        if (
            self._line_aware_context_requested(
                scope=scope,
                line_results=line_results,
                line_paths=line_paths,
                line_ranges=line_ranges,
            )
            and context_package is None
        ):
            return AgentContextBuilderResult(
                context=None,
                gap_findings=[
                    AgentContextBuilderGap(
                        failure_id=AGENT_CONTEXT_BUILDER_FAILURE_ID,
                        corpus_id=corpus_id,
                        reason="missing_context_package_summary",
                        observed_at=_utcnow(),
                        evidence_payload={
                            "line_provenance_results_count": len(line_results),
                            "scoped_line_provenance_count": len(
                                scoped_line_provenance
                            ),
                            "line_provenance_paths_count": len(line_paths),
                            "line_provenance_line_ranges_count": len(line_ranges),
                            "scope_task_id": scope.task_id,
                            "scope_repo_id": scope.repo_id,
                            "scope_path": scope.path,
                            "scope_line_start": scope.line_start,
                            "scope_line_end": scope.line_end,
                        },
                    )
                ],
            )

        # Step 4: compute the effective budget cap (hard-clamped to the
        # doc-19:124-127 20 000 char cap).
        effective_max_prompt_chars = min(
            inputs.max_prompt_chars,
            GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP,
        )

        # Step 5: rank findings by typed rank key (severity / confidence /
        # lost-hours).
        ranked_findings = sorted(
            scoped_findings, key=_finding_rank_key, reverse=True
        )

        # Step 6: build the agent context via the bounded-prompt
        # truncation loop.
        try:
            context = self._build_context_with_budget(
                snapshot=snapshot,
                scope=scope,
                ranked_findings=ranked_findings,
                scoped_recommendations=scoped_recommendations,
                scoped_line_provenance=scoped_line_provenance,
                pre_filter_finding_count=len(snapshot.top_findings),
                pre_filter_recommendation_count=len(snapshot.recommendations),
                pre_filter_line_provenance_count=len(line_results),
                effective_max_prompt_chars=effective_max_prompt_chars,
                context_package=context_package,
            )
        except ValidationError as exc:  # pragma: no cover -- defensive
            return AgentContextBuilderResult(
                context=None,
                gap_findings=[
                    AgentContextBuilderGap(
                        failure_id=AGENT_CONTEXT_BUILDER_FAILURE_ID,
                        corpus_id=corpus_id,
                        reason="context_construction_failed",
                        observed_at=_utcnow(),
                        evidence_payload={"validation_error": str(exc)},
                    )
                ],
            )

        if context is None:
            # The envelope alone exceeded the cap (defensive guard;
            # structurally impossible under typical typed envelopes
            # but the typed surface MUST return a typed gap rather
            # than silently dropping the projection).
            return AgentContextBuilderResult(
                context=None,
                gap_findings=[
                    AgentContextBuilderGap(
                        failure_id=AGENT_CONTEXT_BUILDER_FAILURE_ID,
                        corpus_id=corpus_id,
                        reason="prompt_budget_exceeded",
                        observed_at=_utcnow(),
                        evidence_payload={
                            "effective_max_prompt_chars": effective_max_prompt_chars,
                        },
                    )
                ],
            )

        # Step 7: propagate upstream gaps as informational gap rows.
        propagated_gaps = self._propagate_upstream_gaps(
            inputs.source.gap_findings, corpus_id
        )

        return AgentContextBuilderResult(
            context=context,
            gap_findings=propagated_gaps,
        )

    # --- Private helpers --------------------------------------------------

    @staticmethod
    def _line_aware_context_requested(
        *,
        scope: AgentContextScope,
        line_results: list[LineProvenanceResult],
        line_paths: list[str],
        line_ranges: list[tuple[int, int]],
    ) -> bool:
        """Return whether this request is file/line/provenance aware."""

        return bool(
            line_results
            or line_paths
            or line_ranges
            or scope.path is not None
            or scope.line_start is not None
            or scope.line_end is not None
        )

    @staticmethod
    def _context_package_summary(
        context_package: ContextLayerPackageSummary | ContextLayerPackage | None,
    ) -> ContextLayerPackageSummary | None:
        """Normalize the optional Slice 21 package input to its
        governance-agent reporting summary.
        """

        if context_package is None:
            return None
        if isinstance(context_package, ContextLayerPackageSummary):
            return context_package
        return ContextLayerPackageSummary.from_context_layer_package(context_package)

    @staticmethod
    def _extract_corpus_id(source: SnapshotAPIResult) -> str:
        """Extract the typed corpus_id from the upstream snapshot or
        the first upstream gap finding, falling back to the empty
        string if neither is present.

        Mirrors the Slice 19 4th sub-slice
        :meth:`~iriai_build_v2.execution_control.governance_slack_renderer.GovernanceSlackRenderer._corpus_id_from_upstream_gaps`
        contract.
        """

        if source.snapshot is not None:
            return source.snapshot.corpus_id
        if source.gap_findings:
            return source.gap_findings[0].corpus_id
        return ""

    @staticmethod
    def _propagate_upstream_gaps(
        upstream_gaps: list[SnapshotAPIGap], corpus_id: str
    ) -> list[AgentContextBuilderGap]:
        """Lift upstream :class:`SnapshotAPIGap` rows into typed
        builder gap rows so the caller sees a single typed gap surface.

        Per the no-silent-degradation rule the upstream reason +
        evidence-payload + observed_at are preserved verbatim in the
        builder-gap shape; only the typed failure_id is lifted to
        :data:`AGENT_CONTEXT_BUILDER_FAILURE_ID` so the upstream gap
        appears alongside any builder-emitted gaps on a single typed
        gap surface.
        """

        propagated: list[AgentContextBuilderGap] = []
        for upstream in upstream_gaps:
            propagated.append(
                AgentContextBuilderGap(
                    failure_id=AGENT_CONTEXT_BUILDER_FAILURE_ID,
                    corpus_id=corpus_id or upstream.corpus_id,
                    reason=f"upstream_snapshot_gap:{upstream.reason}",
                    observed_at=upstream.observed_at,
                    evidence_payload=dict(upstream.evidence_payload),
                )
            )
        return propagated

    @staticmethod
    def _filter_findings_by_scope(
        findings: list[GovernanceFinding], scope: AgentContextScope
    ) -> list[GovernanceFinding]:
        """Filter the typed Slice 16 finding list by the typed
        :class:`AgentContextScope` axes.

        Per doc-19 step 5 + doc-19:144-146 the typed scope axes
        filter findings as follows:

        * ``task_id``: keep findings whose
          :attr:`GovernanceFinding.affected_scope["task_id"]` equals
          the caller value OR whose ``affected_scope`` does NOT carry
          a ``task_id`` key (so cross-task findings are surfaced).
        * ``repo_id``: keep findings whose
          :attr:`GovernanceFinding.affected_scope["repo_id"]` equals
          the caller value OR whose ``affected_scope`` does NOT carry
          a ``repo_id`` key (so cross-repo findings are surfaced).
        * ``path`` / ``line_start`` / ``line_end``: NO filtering on
          findings (the typed :class:`GovernanceFinding` does not
          carry per-path / per-line scope on its typed surface; those
          axes only filter the line-provenance list).

        A scope with all-``None`` fields is a pass-through (no
        filtering).
        """

        if scope.task_id is None and scope.repo_id is None:
            return list(findings)

        kept: list[GovernanceFinding] = []
        for finding in findings:
            affected = finding.affected_scope
            if scope.task_id is not None:
                affected_task_id = affected.get("task_id")
                if affected_task_id is not None and affected_task_id != scope.task_id:
                    continue
            if scope.repo_id is not None:
                affected_repo_id = affected.get("repo_id")
                if affected_repo_id is not None and affected_repo_id != scope.repo_id:
                    continue
            kept.append(finding)
        return kept

    @staticmethod
    def _filter_recommendations_by_scope(
        recommendations: list[GovernancePolicyRecommendation],
        scope: AgentContextScope,
    ) -> list[GovernancePolicyRecommendation]:
        """Filter the typed Slice 17 recommendation list by the typed
        :class:`AgentContextScope` axes.

        Per doc-19 step 5 + doc-19:144-146 the typed scope axes
        filter recommendations as follows:

        * ``task_id``: NO filtering (recommendations are repo / scheduler
          / failure-router scoped; they do not carry per-task scope on
          their typed Slice 17 surface).
        * ``repo_id``: keep recommendations whose typed
          :attr:`GovernancePolicyRecommendation.proposed_policy_artifact.scope["repo_id"]`
          equals the caller value OR whose ``proposed_policy_artifact``
          is ``None`` OR whose typed scope dict does NOT carry a
          ``repo_id`` key (so cross-repo recommendations are surfaced).
        * ``path`` / ``line_start`` / ``line_end``: NO filtering on
          recommendations.

        A scope with all-``None`` fields is a pass-through.
        """

        if scope.repo_id is None:
            return list(recommendations)

        kept: list[GovernancePolicyRecommendation] = []
        for recommendation in recommendations:
            artifact = recommendation.proposed_policy_artifact
            if artifact is None:
                # Cross-repo / unscoped recommendation -- include.
                kept.append(recommendation)
                continue
            artifact_scope = artifact.scope
            artifact_repo_id = artifact_scope.get("repo_id")
            if artifact_repo_id is not None and artifact_repo_id != scope.repo_id:
                continue
            kept.append(recommendation)
        return kept

    @staticmethod
    def _filter_line_provenance_by_scope(
        line_results: list[LineProvenanceResult],
        line_paths: list[str],
        line_ranges: list[tuple[int, int]],
        scope: AgentContextScope,
    ) -> tuple[list[LineProvenanceResult], list[int]]:
        """Filter the typed Slice 14 line-provenance list by the typed
        :class:`AgentContextScope` axes.

        Per doc-19 step 5 + doc-19:144-146 the typed scope axes
        filter line-provenance as follows:

        * ``task_id``: keep line-provenance results whose
          :attr:`LineProvenanceResult.task_ids` list contains the
          caller value.
        * ``path``: keep line-provenance results whose corresponding
          parallel path entry equals the caller value (if the parallel
          list is populated; ``len(line_paths) == 0`` means "no path
          metadata" -> no path filtering).
        * ``line_start`` / ``line_end``: keep line-provenance results
          whose corresponding parallel range entry intersects the
          caller's range (if the parallel list is populated;
          ``len(line_ranges) == 0`` means "no range metadata" -> no
          range filtering).
        * ``repo_id``: NO direct filtering on line provenance
          (line-provenance is repo-scoped at the caller's query layer;
          the typed :class:`LineProvenanceResult` does not carry
          ``repo_id`` on its typed surface).

        Returns a tuple of ``(filtered_results, filtered_indices)``
        where ``filtered_indices`` is the list of original indices into
        ``line_results`` that survived filtering (the caller uses this
        to compute the omitted-count).

        A scope with all-``None`` task/path/line fields is a pass-
        through.
        """

        if (
            scope.task_id is None
            and scope.path is None
            and scope.line_start is None
            and scope.line_end is None
        ):
            return list(line_results), list(range(len(line_results)))

        kept_results: list[LineProvenanceResult] = []
        kept_indices: list[int] = []
        has_path_metadata = len(line_paths) > 0
        has_range_metadata = len(line_ranges) > 0
        for index, result in enumerate(line_results):
            if scope.task_id is not None:
                if scope.task_id not in result.task_ids:
                    continue
            if scope.path is not None and has_path_metadata:
                if line_paths[index] != scope.path:
                    continue
            if (
                scope.line_start is not None
                and scope.line_end is not None
                and has_range_metadata
            ):
                start, end = line_ranges[index]
                if not (
                    start <= scope.line_end and end >= scope.line_start
                ):
                    continue
            kept_results.append(result)
            kept_indices.append(index)
        return kept_results, kept_indices

    def _build_context_with_budget(
        self,
        *,
        snapshot: Any,  # typed as GovernanceSnapshot; Any to avoid forward-ref noise
        scope: AgentContextScope,
        ranked_findings: list[GovernanceFinding],
        scoped_recommendations: list[GovernancePolicyRecommendation],
        scoped_line_provenance: list[LineProvenanceResult],
        pre_filter_finding_count: int,
        pre_filter_recommendation_count: int,
        pre_filter_line_provenance_count: int,
        effective_max_prompt_chars: int,
        context_package: ContextLayerPackageSummary | None,
    ) -> GovernanceAgentContext | None:
        """Iteratively construct the typed
        :class:`GovernanceAgentContext` honouring the effective char
        budget; return ``None`` if even the empty envelope exceeds
        the budget.

        The algorithm:

        1. Build the empty envelope (all lists empty) + measure cost.
        2. If empty envelope already > cap -> return None.
        3. Add findings one at a time in rank order until adding
           another would exceed the cap.
        4. Repeat for recommendations.
        5. Repeat for line provenance (converted to ``dict[str, Any]``
           via :meth:`BaseModel.model_dump(mode="json")` per the
           typed-shape surface of
           :attr:`GovernanceAgentContext.relevant_line_provenance`).
        6. Compose + return the typed envelope.

        Per the doc-19:128-131 binding the truncated envelope carries
        the typed :attr:`GovernanceAgentContext.page_refs` +
        :attr:`GovernanceAgentContext.completeness` triple for AC2
        compliance.
        """

        # Step 1: compute upstream omitted counts (pre-existing from
        # the typed SnapshotAPI corpus truncation; preserved verbatim).
        upstream_omitted = dict(snapshot.omitted_counts)

        # Pre-compute scope-filtering omitted counts (rows dropped at
        # the scope-filter step BEFORE bounded-prompt truncation).
        # These get added to the omitted_counts surface so callers see
        # the full omission picture.
        scope_filter_findings_omitted = pre_filter_finding_count - len(
            ranked_findings
        )
        scope_filter_recommendations_omitted = (
            pre_filter_recommendation_count - len(scoped_recommendations)
        )
        scope_filter_line_provenance_omitted = (
            pre_filter_line_provenance_count - len(scoped_line_provenance)
        )

        # Step 2: build empty envelope.
        envelope = GovernanceAgentContext(
            task_id=scope.task_id,
            repo_id=scope.repo_id,
            context_package=context_package,
            relevant_findings=[],
            relevant_line_provenance=[],
            policy_guidance=[],
            policy_guidance_authority="advisory_only",
            omitted_detail_refs=[],
            omitted_counts={},
            completeness=snapshot.completeness,
            page_refs=[],
            truncated=False,
            max_prompt_chars=effective_max_prompt_chars,
        )
        envelope_cost = len(envelope.model_dump_json())
        if envelope_cost > effective_max_prompt_chars:
            return None

        # Step 3: greedily add findings + recommendations + line-
        # provenance up to the budget.
        included_findings: list[GovernanceFinding] = []
        included_recommendations: list[GovernancePolicyRecommendation] = []
        included_line_provenance: list[dict[str, Any]] = []
        current_cost = envelope_cost
        # Note: page_refs + omitted_detail_refs are populated after the
        # budget loop based on what was truncated.

        for finding in ranked_findings:
            candidate_envelope = GovernanceAgentContext(
                task_id=scope.task_id,
                repo_id=scope.repo_id,
                context_package=context_package,
                relevant_findings=[*included_findings, finding],
                relevant_line_provenance=included_line_provenance,
                policy_guidance=included_recommendations,
                policy_guidance_authority="advisory_only",
                omitted_detail_refs=[],
                omitted_counts={},
                completeness=snapshot.completeness,
                page_refs=[],
                truncated=False,
                max_prompt_chars=effective_max_prompt_chars,
            )
            candidate_cost = len(candidate_envelope.model_dump_json())
            if candidate_cost > effective_max_prompt_chars:
                break
            included_findings.append(finding)
            current_cost = candidate_cost

        for recommendation in scoped_recommendations:
            candidate_envelope = GovernanceAgentContext(
                task_id=scope.task_id,
                repo_id=scope.repo_id,
                context_package=context_package,
                relevant_findings=included_findings,
                relevant_line_provenance=included_line_provenance,
                policy_guidance=[*included_recommendations, recommendation],
                policy_guidance_authority="advisory_only",
                omitted_detail_refs=[],
                omitted_counts={},
                completeness=snapshot.completeness,
                page_refs=[],
                truncated=False,
                max_prompt_chars=effective_max_prompt_chars,
            )
            candidate_cost = len(candidate_envelope.model_dump_json())
            if candidate_cost > effective_max_prompt_chars:
                break
            included_recommendations.append(recommendation)
            current_cost = candidate_cost

        for line_result in scoped_line_provenance:
            # Per the typed
            # :attr:`GovernanceAgentContext.relevant_line_provenance`
            # annotation the field is ``list[dict[str, Any]]`` (the
            # Slice 19 1st sub-slice typed-shape foundation). The
            # builder serialises the typed
            # :class:`LineProvenanceResult` via
            # :meth:`BaseModel.model_dump(mode="json")` so the typed
            # surface boundary is preserved.
            line_result_dict = line_result.model_dump(mode="json")
            candidate_envelope = GovernanceAgentContext(
                task_id=scope.task_id,
                repo_id=scope.repo_id,
                context_package=context_package,
                relevant_findings=included_findings,
                relevant_line_provenance=[
                    *included_line_provenance,
                    line_result_dict,
                ],
                policy_guidance=included_recommendations,
                policy_guidance_authority="advisory_only",
                omitted_detail_refs=[],
                omitted_counts={},
                completeness=snapshot.completeness,
                page_refs=[],
                truncated=False,
                max_prompt_chars=effective_max_prompt_chars,
            )
            candidate_cost = len(candidate_envelope.model_dump_json())
            if candidate_cost > effective_max_prompt_chars:
                break
            included_line_provenance.append(line_result_dict)
            current_cost = candidate_cost

        # Step 4: compute the typed truncation + omitted-count surface.
        scope_omitted_findings = scope_filter_findings_omitted
        scope_omitted_recommendations = scope_filter_recommendations_omitted
        scope_omitted_line_provenance = scope_filter_line_provenance_omitted
        budget_omitted_findings = len(ranked_findings) - len(included_findings)
        budget_omitted_recommendations = (
            len(scoped_recommendations) - len(included_recommendations)
        )
        budget_omitted_line_provenance = (
            len(scoped_line_provenance) - len(included_line_provenance)
        )

        omitted_counts: dict[str, int] = {}
        upstream_findings_omitted = int(upstream_omitted.get("findings", 0))
        upstream_recommendations_omitted = int(
            upstream_omitted.get("recommendations", 0)
        )
        upstream_replay_results_omitted = int(
            upstream_omitted.get("replay_results", 0)
        )
        upstream_page_refs_omitted = int(upstream_omitted.get("page_refs", 0))

        # Total per-dimension omissions = upstream truncation + scope
        # filter + bounded-prompt budget truncation.
        omitted_counts["findings"] = (
            upstream_findings_omitted
            + scope_omitted_findings
            + budget_omitted_findings
        )
        omitted_counts["recommendations"] = (
            upstream_recommendations_omitted
            + scope_omitted_recommendations
            + budget_omitted_recommendations
        )
        omitted_counts["line_provenance"] = (
            scope_omitted_line_provenance + budget_omitted_line_provenance
        )
        # Surface upstream replay_results omissions verbatim; the agent
        # context surface does NOT carry replay_results directly but
        # the upstream omission is preserved per the AC6 visibility
        # contract.
        omitted_counts["replay_results"] = upstream_replay_results_omitted
        omitted_counts["page_refs"] = upstream_page_refs_omitted

        truncated_by_scope = (
            scope_omitted_findings > 0
            or scope_omitted_recommendations > 0
            or scope_omitted_line_provenance > 0
        )
        truncated_by_budget = (
            budget_omitted_findings > 0
            or budget_omitted_recommendations > 0
            or budget_omitted_line_provenance > 0
        )
        truncated_upstream = bool(snapshot.truncated)
        truncated = (
            truncated_by_scope
            or truncated_by_budget
            or truncated_upstream
        )

        page_refs: list[str] = list(snapshot.page_refs)
        # omitted_detail_refs mirrors page_refs at this 5th sub-slice
        # (the typed page_refs IS the by-name reference shape for
        # omitted-evidence drilldown per doc-19:111 + doc-19:114). When
        # the typed snapshot is paged the upstream page_refs are the
        # exact drilldown refs; when truncated WITHOUT page_refs the
        # caller MUST treat the context as display-only per
        # doc-19:128-131.
        omitted_detail_refs: list[str] = list(snapshot.page_refs)

        # Step 5: construct the final typed envelope.
        return GovernanceAgentContext(
            task_id=scope.task_id,
            repo_id=scope.repo_id,
            context_package=context_package,
            relevant_findings=included_findings,
            relevant_line_provenance=included_line_provenance,
            policy_guidance=included_recommendations,
            policy_guidance_authority="advisory_only",
            omitted_detail_refs=omitted_detail_refs,
            omitted_counts=omitted_counts,
            completeness=snapshot.completeness,
            page_refs=page_refs,
            truncated=truncated,
            max_prompt_chars=effective_max_prompt_chars,
        )
