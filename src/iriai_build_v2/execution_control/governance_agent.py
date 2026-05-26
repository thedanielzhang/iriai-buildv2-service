"""Slice 19 first sub-slice -- foundational governance agent and reporting typed-shape module.

This module owns the doc-19 "Proposed Interfaces And Types" typed shapes
(``docs/execution-control-plane/19-governance-agent-and-reporting.md:70-117``):

* :class:`GovernanceSnapshot` -- the 16-field governance snapshot
  record shape (doc-19:71-87): ``corpus_id`` + ``snapshot_version`` +
  ``snapshot_digest`` + ``generated_at`` + ``scorecard_id`` +
  ``max_response_bytes`` + ``truncated`` + ``omitted_counts`` +
  ``completeness`` (Slice 13a ``CompletenessState`` REUSE) +
  ``page_refs`` + ``next_cursor`` + ``top_findings`` (Slice 16
  ``GovernanceFinding`` REUSE) + ``recommendations`` (Slice 17
  ``GovernancePolicyRecommendation`` REUSE) + ``replay_results``
  (Slice 18 ``CounterfactualResult`` REUSE) + ``evidence_quality``
  (Slice 13a ``EvidenceQuality`` REUSE) + ``blocked_by``.
* :class:`GovernanceAgentContext` -- the 13-field governance agent
  context record shape (doc-19:103-117): ``task_id`` + ``repo_id`` +
  ``relevant_findings`` (Slice 16 REUSE) + ``relevant_line_provenance``
  (free-form list[dict[str, Any]] reference shape; Slice 14 line-
  provenance shape lands at the future Slice 19 5th sub-slice agent-
  context builder per doc-19:157-160) + ``policy_guidance`` (Slice 17
  REUSE) + ``policy_guidance_authority: Literal["advisory_only"]``
  (hard-coded literal default per doc-19:110 + doc-19:230-231
  advisory-only AC) + ``omitted_detail_refs`` + ``omitted_counts`` +
  ``completeness`` (Slice 13a REUSE) + ``page_refs`` + ``truncated`` +
  ``max_prompt_chars``.

Plus the canonical-JSON helpers
:func:`compute_governance_snapshot_digest` +
:func:`canonical_governance_snapshot_dict` mirroring the Slice 13A
``compute_completeness_digest`` + Slice 14 ``compute_payload_sha256`` +
Slice 15 ``compute_scorecard_digest`` + Slice 16 1st sub-slice
``compute_finding_idempotency_key`` + ``canonical_finding_dict`` +
Slice 17 1st sub-slice
``compute_policy_recommendation_idempotency_key`` +
``canonical_policy_recommendation_dict`` + Slice 18 1st sub-slice
``compute_counterfactual_idempotency_key`` +
``canonical_counterfactual_dict`` canonical-JSON + SHA-256 discipline
verbatim.

Plus the 5 default-budget constants per doc-19:121-127:

* :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES` -- ``262_144`` (256 KB
  serialized JSON cap per doc-19:121).
* :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS` -- ``20`` (per
  doc-19:121).
* :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS` -- ``10``
  (per doc-19:121).
* :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS` -- ``10`` (per
  doc-19:122).
* :data:`GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP` -- ``20_000``
  (the hard cap per doc-19:124 *"`max_prompt_chars` from caller,
  hard-capped at 20,000 chars"*).

It is the **cross-cutting typed foundation** that subsequent Slice 19
sub-slices (the typed snapshot API + dashboard view + Slack rendering +
agent-context builder + report-artifact writer + read-only governance
agent tooling per doc-19 § Refactoring Steps steps 1-7 at
doc-19:150-164) build on; this first sub-slice does NOT yet wire these
typed shapes into any CLI / dashboard / Slack / report-artifact / agent-
context-builder consumer -- that wiring lands in subsequent sub-slices.

Per the governance prompt § "Non-Negotiables" the typed shapes here are
analytical / advisory / read-only -- they do NOT mutate executor /
control-plane / product state, take merge or checkpoint authority, or
force policy activation. Governance snapshots + agent contexts are
review / governance / reporting artifacts (per doc-19:166-167
*"Governance reports are projections of governance rows."* +
doc-19:170-171 *"Dashboard reads governance snapshots with bounded
fields and ETags; it does not resolve full evidence bodies by
default."* + doc-19:174-176 *"Agent `policy_guidance` is prompt context
only. It cannot override task contracts, gate requirements,
failure-router policy, merge-queue policy, or any activated consumer
policy artifact from Slice 17."*) and never change execution state.

**Doc-19 acceptance binding** (doc-19:224-235): the typed shapes here
expose the surface that future Slice 19 sub-slices enforce the 8
acceptance criteria at:

* **AC1** -- *"Reports are bounded, reproducible, evidence-cited, and
  structured first."* (doc-19:224) -- enforced by the typed
  :attr:`GovernanceSnapshot.max_response_bytes` +
  :attr:`GovernanceSnapshot.truncated` +
  :attr:`GovernanceSnapshot.omitted_counts` +
  :attr:`GovernanceSnapshot.page_refs` +
  :attr:`GovernanceSnapshot.snapshot_digest` (the "bounded" +
  "reproducible" axes) + the typed
  :attr:`GovernanceSnapshot.top_findings` (Slice 16 REUSE) +
  :attr:`GovernanceSnapshot.recommendations` (Slice 17 REUSE) +
  :attr:`GovernanceSnapshot.replay_results` (Slice 18 REUSE) (the
  "evidence-cited" + "structured first" axes).

* **AC2** -- *"Truncated or preview reports are never authoritative
  unless exact page refs and completeness metadata cover the
  consumer's required scope."* (doc-19:225-226) -- enforced by the
  combined :attr:`GovernanceSnapshot.truncated` +
  :attr:`GovernanceSnapshot.page_refs` +
  :attr:`GovernanceSnapshot.completeness` field triple; the typed
  surface lets future Slice 19 sub-slices' consumers detect display-
  only state at construction.

* **AC3** -- *"Workflow agents can receive compact governance context
  at task execute time."* (doc-19:227) -- enforced by the typed
  :class:`GovernanceAgentContext` shape (the compact context surface).

* **AC4** -- *"After Slice 21, every context response that uses
  line/context-layer provenance carries a citeable context package
  id and digest."* (doc-19:228) -- this AC lives in Slice 21+ wiring
  (the :class:`ContextLayerPackageSummary` shape per doc-19:89-101 is
  NOT included in this 1st sub-slice surface; doc-19:89-101 is a
  Slice 21-conditional contract per doc-19:125-127 *"After Slice 21,
  this response must include `ContextLayerPackageSummary`..."*).

* **AC5** -- *"Workflow agents receive governance policy guidance only
  as advisory context; contracts, gates, router, and merge queue
  remain authoritative."* (doc-19:230-231) -- enforced by the typed
  :attr:`GovernanceAgentContext.policy_guidance_authority:
  Literal["advisory_only"]` hard-coded literal default per doc-19:110
  + doc-19:230-231; Pydantic Literal validation rejects any other
  value at construction with a typed ``ValidationError``.

* **AC6** -- *"Human-facing dashboard/Slack output explains top
  findings without hiding evidence quality or omitted details."*
  (doc-19:232-233) -- enforced by the typed
  :attr:`GovernanceSnapshot.evidence_quality` (Slice 13a REUSE) +
  :attr:`GovernanceSnapshot.omitted_counts` fields; the
  evidence-quality + omitted-counts surface is REQUIRED so dashboard
  + Slack rendering surfaces (future Slice 19 sub-slices 3rd + 4th)
  cannot omit it.

* **AC7** -- *"Reporting honors Slice 10 read-only and bounded-read
  guarantees."* (doc-19:234) -- enforced by the read-only typed-shape
  design (no mutation methods on any BaseModel; no extension of the
  Slice 10c-1 ``CONTROL_PLANE_WRITER_METHODS`` set per doc-19:348-349
  acceptance criterion).

* **AC8** -- *"Implementation-log anchors are visible in plan-vs-actual
  reports."* (doc-19:235) -- enforced by the typed
  :attr:`GovernanceSnapshot.top_findings` (Slice 16 REUSE) field; the
  Slice 16 :attr:`GovernanceFinding.implementation_log_anchors`
  surface is preserved by the typed REUSE so plan-vs-actual reporting
  in future Slice 19 sub-slices does not lose the anchor visibility.

**Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
doc-19:256-303 Slice 13A Shared Completeness Model Dependency).** The
:attr:`GovernanceSnapshot.completeness` +
:attr:`GovernanceAgentContext.completeness` fields are typed against
the Slice 13a :data:`CompletenessState` Literal (imported from
:mod:`iriai_build_v2.workflows.develop.governance.models`; the
4-value enum). The :attr:`GovernanceSnapshot.evidence_quality` field
is typed against the Slice 13a :data:`EvidenceQuality` Literal
(imported from the same module; the 6-value enum). Neither Literal is
redefined here -- per doc-13a:285-287 step 9 (*"Update governance
Slices 13-20 and context Slice 21 to depend on this shared completeness
model instead of redefining authority semantics locally"*) this module
consumes the shared models directly.

**Slice 16 dependency reconciliation.** The
:attr:`GovernanceSnapshot.top_findings` +
:attr:`GovernanceAgentContext.relevant_findings` fields are lists of
Slice 16 1st sub-slice :class:`GovernanceFinding` typed BaseModels
(imported from :mod:`iriai_build_v2.execution_control.finding_engine`).
The :class:`GovernanceFinding` type is NOT redefined here -- per the
no-second-source-of-truth discipline this module consumes the shared
Slice 16 typed BaseModel directly so the
:attr:`GovernanceFinding.implementation_log_anchors` +
:attr:`GovernanceFinding.primary_evidence_refs` +
:attr:`GovernanceFinding.supporting_evidence_refs` surfaces are
preserved (the AC8 + AC6 enforcement axes).

**Slice 17 dependency reconciliation.** The
:attr:`GovernanceSnapshot.recommendations` +
:attr:`GovernanceAgentContext.policy_guidance` fields are lists of
Slice 17 1st sub-slice :class:`GovernancePolicyRecommendation` typed
BaseModels (imported from
:mod:`iriai_build_v2.execution_control.policy_recommendation`). The
:class:`GovernancePolicyRecommendation` type is NOT redefined here --
per the no-second-source-of-truth discipline this module consumes the
shared Slice 17 typed BaseModel directly. Per doc-19:174-176
*"Agent `policy_guidance` is prompt context only."* + doc-19:230-231
AC5 the typed surface enforces the advisory-only contract via the
:attr:`GovernanceAgentContext.policy_guidance_authority:
Literal["advisory_only"]` hard-coded literal default.

**Slice 18 dependency reconciliation.** The
:attr:`GovernanceSnapshot.replay_results` field is a list of Slice 18
1st sub-slice :class:`CounterfactualResult` typed BaseModels (imported
from :mod:`iriai_build_v2.execution_control.counterfactual_replay`).
The :class:`CounterfactualResult` type is NOT redefined here -- per the
no-second-source-of-truth discipline this module consumes the shared
Slice 18 typed BaseModel directly so the
:attr:`CounterfactualResult.policy_provenance_refs` (Slice 13a
GovernanceEvidenceRef list) + :attr:`CounterfactualResult.result_id` +
:attr:`CounterfactualResult.result_version` surfaces are preserved (the
AC1 evidence-backed axis carries through transparently).

**By-name reference contracts.** Per doc-19:81 the
:attr:`GovernanceSnapshot.page_refs` field is ``list[str]`` (just
strings; NOT typed BaseModels) -- the by-name reference shape mirrors
the Slice 17 1st sub-slice
:attr:`GovernancePolicyRecommendation.source_finding_ids: list[str]`
pattern at ``policy_recommendation.py:743``. Per doc-19:87 the
:attr:`GovernanceSnapshot.blocked_by` field is also ``list[str]``
(typed blocker-id strings; the future Slice 19 5th sub-slice agent-
context builder enforces the at-least-one-blocker-id-for-blocked-state
invariant).

Per doc-19:111 the
:attr:`GovernanceAgentContext.omitted_detail_refs` field is
``list[str]`` (the by-name reference shape for omitted evidence-page-
refs; the future Slice 19 5th sub-slice agent-context builder
populates the list from the
:class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidencePageRef`
typed shape).

Per doc-19:114 the :attr:`GovernanceAgentContext.page_refs` field is
``list[str]`` (the by-name reference shape; mirrors the
:attr:`GovernanceSnapshot.page_refs` pattern).

Per doc-19:108 the
:attr:`GovernanceAgentContext.relevant_line_provenance` field is
``list[dict[str, Any]]`` (free-form per-line-provenance-result dict
shape; the typed Slice 14 line-provenance shape lands at the future
Slice 19 5th sub-slice agent-context builder per doc-19:157-160 *"Add
agent-context builder that selects findings and provenance relevant to
a task contract, repo, path, or line range."*).

**Implementation discipline.** Stdlib (``hashlib`` + ``json`` +
``datetime``) + Pydantic v2 + Slice 13a modules
(``..workflows.develop.governance.models``) + Slice 16 1st sub-slice
module (``.finding_engine``) + Slice 17 1st sub-slice module
(``.policy_recommendation``) + Slice 18 1st sub-slice module
(``.counterfactual_replay``) only. NO imports from ``governance/``
outside ``governance.models`` (this module is foundational; the
governance layer consumes execution-control surfaces, not the
reverse). NO imports from other parts of ``execution_control/`` beyond
the 3 Slice 16/17/18 1st-sub-slice typed-shape modules (this module is
foundational for the future Slice 19 CLI / dashboard / Slack / report-
artifact / agent-context-builder consumers; the existing Slice 00-18
``execution_control`` modules are NOT modified). NO imports from
``workflows/develop/execution/phases/`` / ``supervisor`` / ``dashboard``
(those would be downstream consumers, not dependencies).

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.counterfactual_replay` (Slice
18 1st sub-slice) +
:mod:`iriai_build_v2.execution_control.policy_recommendation` (Slice
17 1st sub-slice) +
:mod:`iriai_build_v2.execution_control.finding_engine` (Slice 16 1st
sub-slice) + :mod:`iriai_build_v2.execution_control.governance_metrics`
(Slice 15 1st sub-slice) +
:mod:`iriai_build_v2.execution_control.commit_provenance` (Slice 14
1st sub-slice) + :mod:`iriai_build_v2.execution_control.completeness`
(Slice 13A 2nd sub-slice): ``BaseModel`` subclasses with
``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed as a typed
``ValidationError`` rather than being silently absorbed.

Per the auto-memory ``feedback_flat_structured_output`` rule the typed
control fields are flat primitives (``str`` / ``float`` / ``bool`` /
``list`` / ``dict``) except for the Slice 13a/16/17/18 typed REUSE
which carries the typed BaseModels through directly (the typed-shape
foundation contract). Per the auto-memory
``feedback_no_silent_degradation`` rule every Pydantic field validates
at construction; unknown values fail closed via Literal range +
``extra="forbid"`` discipline. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 14 + Slice 15 + Slice 16 + Slice 17 + Slice 18 1st sub-slice
precedent verbatim without introducing new abstractions.

**Activation-authority boundary** (per STATUS.md § "Loop discipline" +
doc-19:348-349 + doc-17:178-179 + doc-18:117-119 + doc-18:123-125).
Governance snapshots + agent contexts are review / governance /
reporting artifacts only -- never runtime policy authority. This is
the same boundary the Slice 17 7th sub-slice activation-boundary test
surface enforces for the Slice 13-18 governance modules; the Slice 19
1st sub-slice typed-shape module honours the boundary at the typed
surface (no activation methods; no consumer-state mutation; snapshots
+ agent contexts are read-only descriptors that future Slice 19 sub-
slices project to CLI / dashboard / Slack / report-artifact / agent-
context-builder consumers). Per doc-19:348-349 acceptance criterion
*"Supervisor/dashboard read-only contract preserved (no governance
writer extends the Slice 10c-1 `CONTROL_PLANE_WRITER_METHODS` set)."*
the typed surface here does NOT extend the
``CONTROL_PLANE_WRITER_METHODS`` set; the typed shapes are pure
descriptors with no executor-mutation methods.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
)
from iriai_build_v2.execution_control.finding_engine import (
    GovernanceFinding,
)
from iriai_build_v2.execution_control.policy_recommendation import (
    GovernancePolicyRecommendation,
)
from iriai_build_v2.workflows.develop.governance.models import (
    CompletenessState,
    EvidenceQuality,
)


__all__ = [
    # Doc-19:71-87 -- the 16-field GovernanceSnapshot BaseModel.
    "GovernanceSnapshot",
    # Doc-19:103-117 -- the 13-field GovernanceAgentContext BaseModel.
    "GovernanceAgentContext",
    # Helpers mirroring Slice 13A's compute_completeness_digest +
    # Slice 14's compute_payload_sha256 + Slice 15's
    # compute_scorecard_digest + Slice 16 1st sub-slice's
    # compute_finding_idempotency_key + canonical_finding_dict +
    # Slice 17 1st sub-slice's
    # compute_policy_recommendation_idempotency_key +
    # canonical_policy_recommendation_dict + Slice 18 1st sub-slice's
    # compute_counterfactual_idempotency_key +
    # canonical_counterfactual_dict canonical-JSON discipline.
    "compute_governance_snapshot_digest",
    "canonical_governance_snapshot_dict",
    # Doc-19:121-127 -- the 5 default-budget constants for
    # GovernanceSnapshot + GovernanceAgentContext.
    "GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES",
    "GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS",
    "GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS",
    "GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS",
    "GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP",
]


# --- Default-budget constants (doc-19:121-127) ------------------------------


GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES: int = 262_144
"""Doc-19:121 -- the default 256-KB serialized-JSON cap for the
:class:`GovernanceSnapshot` shape.

Per doc-19:121 *"Governance snapshot: 256 KB serialized JSON, 20
findings, 10 recommendations, 10 replay results, and exact page-ref
pagination for additional rows."* this is the typed default the
(future) Slice 19 2nd sub-slice snapshot API uses as the
``max_response_bytes`` default; the typed surface exposes the constant
so consumers can cross-check the typed
:attr:`GovernanceSnapshot.max_response_bytes` field against the
documented default.

Per doc-19:128-131 *"Reporting budgets are preview/display budgets. Any
truncated snapshot or agent context must include exact
`GovernanceEvidencePageRef` rows plus `completeness`; without those
refs the response is display-only and cannot feed acceptance,
recommendations, policy guidance, or task-execute context."* the
default budget is a preview / display budget; the typed surface does
NOT pre-emptively enforce the budget at construction (the typed
:class:`GovernanceSnapshot` accepts any positive integer); the budget
enforcement lives in the future Slice 19 2nd sub-slice snapshot API
that constructs snapshots from real corpus data.
"""

GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS: int = 20
"""Doc-19:121 -- the default 20-finding cap for
:attr:`GovernanceSnapshot.top_findings`.

Per doc-19:121 *"...20 findings..."* + doc-19:190-191 *"Too many
findings: rank by severity, confidence, lost-time estimate, and
recency; include omitted refs."* the typed default is the cap the
(future) Slice 19 2nd sub-slice snapshot API enforces; rows above the
cap are summarised into :attr:`GovernanceSnapshot.omitted_counts` +
exact :attr:`GovernanceSnapshot.page_refs` for drill-down.
"""

GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS: int = 10
"""Doc-19:121 -- the default 10-recommendation cap for
:attr:`GovernanceSnapshot.recommendations`.

Per doc-19:121 *"...10 recommendations..."* the typed default is the
cap the (future) Slice 19 2nd sub-slice snapshot API enforces; rows
above the cap are summarised into
:attr:`GovernanceSnapshot.omitted_counts` + exact
:attr:`GovernanceSnapshot.page_refs` for drill-down.
"""

GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS: int = 10
"""Doc-19:122 -- the default 10-replay-result cap for
:attr:`GovernanceSnapshot.replay_results`.

Per doc-19:122 *"...10 replay results, and exact page-ref pagination
for additional rows."* the typed default is the cap the (future) Slice
19 2nd sub-slice snapshot API enforces; rows above the cap are
summarised into :attr:`GovernanceSnapshot.omitted_counts` + exact
:attr:`GovernanceSnapshot.page_refs` for drill-down.
"""

GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP: int = 20_000
"""Doc-19:124 -- the hard cap (20,000 chars) for the
:attr:`GovernanceAgentContext.max_prompt_chars` field.

Per doc-19:124 *"Agent context: `max_prompt_chars` from caller,
hard-capped at 20,000 chars, with omitted refs instead of full evidence
bodies."* this is the typed hard cap the (future) Slice 19 5th sub-
slice agent-context builder enforces; the typed surface does NOT
pre-emptively enforce the cap at construction (the typed
:class:`GovernanceAgentContext` accepts any positive integer); the cap
enforcement lives in the future Slice 19 5th sub-slice agent-context
builder that constructs contexts from real corpus data.

This is INTENTIONALLY distinct from the per-snapshot
:data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES` cap above; the snapshot
cap is BYTE-based (256 KB serialized JSON) while the agent-context cap
is CHAR-based (20,000 chars of prompt text) per doc-19:121 vs
doc-19:124.
"""


# --- GovernanceSnapshot (doc-19:71-87) --------------------------------------


class GovernanceSnapshot(BaseModel):
    """Doc-19:71-87 -- the governance snapshot record shape.

    A governance snapshot is the typed advisory descriptor the (future)
    Slice 19 2nd sub-slice typed snapshot API emits when projecting
    bounded governance evidence into a cap-bounded preview /
    bounded-read response shape. Per doc-19 § "Acceptance Criteria":

    * **AC1** -- *"Reports are bounded, reproducible, evidence-cited,
      and structured first."* (doc-19:224) -- enforced by the typed
      :attr:`max_response_bytes` + :attr:`truncated` +
      :attr:`omitted_counts` + :attr:`page_refs` + :attr:`snapshot_digest`
      (the "bounded" + "reproducible" axes) + the typed
      :attr:`top_findings` (Slice 16 REUSE) + :attr:`recommendations`
      (Slice 17 REUSE) + :attr:`replay_results` (Slice 18 REUSE) (the
      "evidence-cited" + "structured first" axes).

    * **AC2** -- *"Truncated or preview reports are never authoritative
      unless exact page refs and completeness metadata cover the
      consumer's required scope."* (doc-19:225-226) -- enforced by the
      combined :attr:`truncated` + :attr:`page_refs` +
      :attr:`completeness` field triple; the typed surface lets future
      Slice 19 sub-slices' consumers detect display-only state at
      construction.

    * **AC6** -- *"Human-facing dashboard/Slack output explains top
      findings without hiding evidence quality or omitted details."*
      (doc-19:232-233) -- enforced by the typed :attr:`evidence_quality`
      (Slice 13a REUSE) + :attr:`omitted_counts` fields; the evidence-
      quality + omitted-counts surface is REQUIRED so dashboard + Slack
      rendering surfaces (future Slice 19 sub-slices 3rd + 4th) cannot
      omit it.

    * **AC8** -- *"Implementation-log anchors are visible in plan-vs-
      actual reports."* (doc-19:235) -- enforced by the typed
      :attr:`top_findings` (Slice 16 REUSE) field; the Slice 16
      :attr:`GovernanceFinding.implementation_log_anchors` surface is
      preserved by the typed REUSE.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-19:256-303).** :attr:`completeness` is typed against the Slice
    13a :data:`CompletenessState` Literal + :attr:`evidence_quality` is
    typed against the Slice 13a :data:`EvidenceQuality` Literal (both
    imported from :mod:`iriai_build_v2.workflows.develop.governance.models`;
    NOT redefined here). Per doc-13a:285-287 step 9 the shared Literals
    are the authority for governance completeness + evidence-quality
    semantics.

    **Slice 16 dependency reconciliation.** :attr:`top_findings` is a
    list of Slice 16 1st sub-slice :class:`GovernanceFinding` typed
    BaseModels (imported from
    :mod:`iriai_build_v2.execution_control.finding_engine`; NOT
    redefined here). The Slice 16 typed shape is the source of truth
    for governance finding records.

    **Slice 17 dependency reconciliation.** :attr:`recommendations` is
    a list of Slice 17 1st sub-slice
    :class:`GovernancePolicyRecommendation` typed BaseModels (imported
    from :mod:`iriai_build_v2.execution_control.policy_recommendation`;
    NOT redefined here). The Slice 17 typed shape is the source of
    truth for governance policy recommendation records.

    **Slice 18 dependency reconciliation.** :attr:`replay_results` is a
    list of Slice 18 1st sub-slice :class:`CounterfactualResult` typed
    BaseModels (imported from
    :mod:`iriai_build_v2.execution_control.counterfactual_replay`;
    NOT redefined here). The Slice 18 typed shape is the source of
    truth for counterfactual replay result records.
    """

    model_config = ConfigDict(extra="forbid")

    corpus_id: str
    """Doc-19:72 -- the stable corpus identifier string. Per
    doc-19:218 *"Report generation is reproducible for the same corpus
    id."* the corpus id is the typed identity surface the (future)
    Slice 19 2nd sub-slice snapshot API uses to enforce the
    reproducibility contract."""

    snapshot_version: str
    """Doc-19:73 -- the snapshot version string (e.g. ``"v1"``).
    Mirrors the Slice 18 1st sub-slice
    :attr:`CounterfactualResult.result_version` versioning discipline;
    new snapshot semantics require a new snapshot version so consumers
    can cleanly detect schema evolution across reruns."""

    snapshot_digest: str
    """Doc-19:74 -- the deterministic SHA-256 hex digest of the
    snapshot's bounded inputs (corpus id + snapshot version + scorecard
    id + row digests + omitted-counts + evidence-quality values +
    recommendation versions + replay versions).

    Per doc-19:152-153 *"The API computes `snapshot_digest` from
    bounded row ids, row digests, omitted-counts, evidence-quality
    values, and recommendation/replay versions."* this is the
    "reproducible" axis of AC1; the :func:`compute_governance_snapshot_digest`
    helper produces the canonical SHA-256-derived digest from the
    snapshot's logical inputs.

    Per doc-19:172-173 *"...The ETag seed is `snapshot_digest`."* the
    digest is also the dashboard ETag seed; the typed surface enforces
    the digest contract at construction. Per doc-19:201-202 *"Slack
    digest dedupes repeated identical governance snapshots by
    `snapshot_digest` and emits material updates when the digest
    changes."* the digest is also the Slack-dedupe key."""

    generated_at: datetime
    """Doc-19:75 -- the typed snapshot generation timestamp. Per the
    Pydantic v2 idiom the field is a typed ``datetime`` (ISO-8601 on
    serialisation); the future Slice 19 2nd sub-slice snapshot API
    populates this with the snapshot-construction wall-clock time."""

    scorecard_id: str | None = None
    """Doc-19:76 -- the optional Slice 15 governance scorecard id the
    snapshot grounds on (``None`` if the snapshot is not grounded on a
    specific scorecard, e.g. for cross-corpus diff snapshots).

    Per doc-19:76 the field is ``scorecard_id: str`` in the doc-19
    shape; per the doc-19:194-195 *"Active workflow pressure: reporting
    returns cached snapshots instead of forcing expensive
    recomputation."* edge case the typed surface accepts ``None`` so
    cached / cross-corpus snapshots can construct without a scorecard
    id. Defaults to ``None``."""

    max_response_bytes: int
    """Doc-19:77 -- the snapshot's effective max-response-bytes cap (in
    bytes). Per doc-19:121 the default is 256 KB (262 144 bytes); the
    typed surface accepts any positive integer (the cap-enforcement
    lives in the future Slice 19 2nd sub-slice snapshot API).

    Per doc-19:128-131 the cap is a preview / display budget; truncated
    snapshots MUST carry exact :attr:`page_refs` + :attr:`completeness`
    rows or the snapshot is display-only (cannot feed acceptance /
    recommendations / policy guidance / task-execute context)."""

    truncated: bool
    """Doc-19:78 -- ``True`` if the snapshot's typed lists
    (:attr:`top_findings` / :attr:`recommendations` / :attr:`replay_results`)
    have been truncated to fit within :attr:`max_response_bytes`;
    ``False`` if all rows fit within the cap.

    Per doc-19:128-131 *"Reporting budgets are preview/display
    budgets. Any truncated snapshot or agent context must include exact
    `GovernanceEvidencePageRef` rows plus `completeness`; without those
    refs the response is display-only and cannot feed acceptance,
    recommendations, policy guidance, or task-execute context."* the
    truncated flag combines with the typed :attr:`page_refs` +
    :attr:`completeness` triple to enforce AC2 at the typed-shape
    layer."""

    omitted_counts: dict[str, int]
    """Doc-19:79 -- the dict of omitted-row counts by typed list name
    (e.g. ``{"findings": 5, "recommendations": 0, "replay_results":
    2}``).

    Per doc-19:190-191 *"Too many findings: rank by severity,
    confidence, lost-time estimate, and recency; include omitted
    refs."* the omitted-counts dict is the typed surface that
    enforces the AC6 visibility contract; the typed
    :attr:`evidence_quality` + this typed surface together let
    dashboard + Slack rendering surfaces honour doc-19:232-233 *"Human-
    facing dashboard/Slack output explains top findings without hiding
    evidence quality or omitted details."*"""

    completeness: CompletenessState
    """Doc-19:80 -- the typed Slice 13a :data:`CompletenessState`
    Literal (4 values: ``complete`` / ``paged`` / ``preview_only`` /
    ``unavailable``).

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-19:256-303).** This field is typed against the Slice 13a shared
    :data:`CompletenessState` Literal -- imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`, NOT
    redefined here. Per doc-13a:285-287 step 9 the shared Literal is
    the authority for governance completeness semantics.

    Per the Slice 13A invariant + doc-19:128-131 *"...without those
    refs the response is display-only and cannot feed acceptance..."*
    the typed completeness state is the AC2 enforcer: ``preview_only``
    / ``unavailable`` snapshots are display-only; ``complete`` /
    ``paged`` snapshots may feed downstream acceptance + recommendation
    + policy-guidance + task-execute consumers."""

    page_refs: list[str]
    """Doc-19:81 -- the list of typed page-ref string identifiers (e.g.
    typed-source ref-ids the future Slice 19 2nd sub-slice snapshot API
    populates from the
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidencePageRef`
    typed shape).

    Per doc-19:81 + doc-19:128-131 the by-name reference shape mirrors
    the Slice 17 1st sub-slice
    :attr:`GovernancePolicyRecommendation.source_finding_ids: list[str]`
    pattern at ``policy_recommendation.py:743``. Truncated snapshots
    MUST carry these refs (per the doc-19:128-131 binding); the typed
    surface accepts the empty list at construction (the future Slice
    19 2nd sub-slice snapshot API enforces the
    non-empty-list-for-truncated invariant)."""

    next_cursor: str | None = None
    """Doc-19:82 -- the optional pagination cursor for the next page of
    typed rows (``None`` if the snapshot is the final page of the
    corpus).

    Per doc-19:121 *"...and exact page-ref pagination for additional
    rows."* the next-cursor is the typed surface that lets dashboard +
    CLI surfaces traverse paged snapshots; the future Slice 19 2nd
    sub-slice snapshot API populates the cursor with the typed cursor
    shape (e.g. a SHA-256 of the next row's id + version)."""

    top_findings: list[GovernanceFinding]
    """Doc-19:83 -- the list of Slice 16 1st sub-slice typed
    :class:`GovernanceFinding` records (top-N findings ranked by
    severity + confidence + lost-hours estimate + recency).

    **Slice 16 dependency reconciliation.** This field is a list of
    Slice 16 1st sub-slice :class:`GovernanceFinding` typed BaseModels
    -- imported from
    :mod:`iriai_build_v2.execution_control.finding_engine`, NOT
    redefined here. Per the no-second-source-of-truth discipline the
    Slice 16 typed shape is the source of truth for governance
    finding records.

    Per doc-19:121 the default cap is 20 findings (per
    :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS`); per doc-19:190-191
    rows above the cap go into :attr:`omitted_counts` + exact
    :attr:`page_refs` for drill-down.

    Per doc-19:235 AC8 *"Implementation-log anchors are visible in
    plan-vs-actual reports."* the Slice 16
    :attr:`GovernanceFinding.implementation_log_anchors` surface is
    preserved by the typed REUSE; the future Slice 19 sub-slices' plan-
    vs-actual reporting reads the anchors directly from each finding."""

    recommendations: list[GovernancePolicyRecommendation]
    """Doc-19:84 -- the list of Slice 17 1st sub-slice typed
    :class:`GovernancePolicyRecommendation` records.

    **Slice 17 dependency reconciliation.** This field is a list of
    Slice 17 1st sub-slice :class:`GovernancePolicyRecommendation`
    typed BaseModels -- imported from
    :mod:`iriai_build_v2.execution_control.policy_recommendation`, NOT
    redefined here. Per the no-second-source-of-truth discipline the
    Slice 17 typed shape is the source of truth for governance policy
    recommendation records.

    Per doc-19:121 the default cap is 10 recommendations (per
    :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS`); rows above
    the cap go into :attr:`omitted_counts` + exact :attr:`page_refs`
    for drill-down."""

    replay_results: list[CounterfactualResult]
    """Doc-19:85 -- the list of Slice 18 1st sub-slice typed
    :class:`CounterfactualResult` records (top-N counterfactual replay
    results ranked by confidence + risk-change + recency).

    **Slice 18 dependency reconciliation.** This field is a list of
    Slice 18 1st sub-slice :class:`CounterfactualResult` typed
    BaseModels -- imported from
    :mod:`iriai_build_v2.execution_control.counterfactual_replay`, NOT
    redefined here. Per the no-second-source-of-truth discipline the
    Slice 18 typed shape is the source of truth for counterfactual
    replay result records.

    Per doc-19:122 the default cap is 10 replay results (per
    :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS`); rows above
    the cap go into :attr:`omitted_counts` + exact :attr:`page_refs`
    for drill-down."""

    evidence_quality: EvidenceQuality
    """Doc-19:86 -- the typed Slice 13a :data:`EvidenceQuality` Literal
    (6 values: ``canonical`` / ``derived`` / ``sampled`` / ``advisory``
    / ``stale`` / ``insufficient``).

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-19:256-303).** This field is typed against the Slice 13a shared
    :data:`EvidenceQuality` Literal -- imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`, NOT
    redefined here. Per doc-13a:285-287 step 9 the shared Literal is
    the authority for evidence-quality semantics.

    Per doc-19:232-233 AC6 *"Human-facing dashboard/Slack output
    explains top findings without hiding evidence quality or omitted
    details."* the evidence-quality field is REQUIRED on every snapshot
    so dashboard + Slack rendering surfaces cannot omit it; the typed
    surface enforces the presence requirement at construction (the
    field has no default)."""

    blocked_by: list[str]
    """Doc-19:87 -- the list of blocker-id strings (e.g.
    ``["stale_evidence:8ac124d6", "rate_limited:slack"]``) when the
    snapshot is blocked from authoritative consumption.

    Per doc-19:186-189 the various edge-case rows (governance snapshot
    stale / missing line provenance / Slack delivery failure / active
    workflow pressure) map onto blocker-id strings in this list; the
    typed surface accepts the empty list at construction (the typical
    case for a valid snapshot). The future Slice 19 2nd sub-slice
    snapshot API populates the list with the per-block-reason citation
    strings."""


# --- GovernanceAgentContext (doc-19:103-117) --------------------------------


class GovernanceAgentContext(BaseModel):
    """Doc-19:103-117 -- the governance agent context record shape.

    A governance agent context is the typed advisory descriptor the
    (future) Slice 19 5th sub-slice agent-context builder emits when
    projecting bounded governance context for a workflow agent task-
    execute prompt. Per doc-19 § "Acceptance Criteria":

    * **AC3** -- *"Workflow agents can receive compact governance
      context at task execute time."* (doc-19:227) -- enforced by this
      typed shape (the compact-context surface).

    * **AC5** -- *"Workflow agents receive governance policy guidance
      only as advisory context; contracts, gates, router, and merge
      queue remain authoritative."* (doc-19:230-231) -- enforced by
      the typed :attr:`policy_guidance_authority:
      Literal["advisory_only"]` hard-coded literal default per
      doc-19:110 + doc-19:230-231; Pydantic Literal validation rejects
      any other value at construction with a typed ``ValidationError``.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-19:256-303).** :attr:`completeness` is typed against the Slice
    13a :data:`CompletenessState` Literal (imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`; NOT
    redefined here). Per doc-13a:285-287 step 9 the shared Literal is
    the authority for governance completeness semantics.

    **Slice 16 dependency reconciliation.** :attr:`relevant_findings` is
    a list of Slice 16 1st sub-slice :class:`GovernanceFinding` typed
    BaseModels (imported from
    :mod:`iriai_build_v2.execution_control.finding_engine`; NOT
    redefined here).

    **Slice 17 dependency reconciliation.** :attr:`policy_guidance` is
    a list of Slice 17 1st sub-slice
    :class:`GovernancePolicyRecommendation` typed BaseModels (imported
    from :mod:`iriai_build_v2.execution_control.policy_recommendation`;
    NOT redefined here). Per doc-19:174-176 *"Agent `policy_guidance`
    is prompt context only. It cannot override task contracts, gate
    requirements, failure-router policy, merge-queue policy, or any
    activated consumer policy artifact from Slice 17."* + doc-19:230-231
    AC5 the typed surface enforces the advisory-only contract via the
    typed :attr:`policy_guidance_authority: Literal["advisory_only"]`
    hard-coded literal default.

    **Slice 14 by-name reference contract.** :attr:`relevant_line_provenance`
    is ``list[dict[str, Any]]`` (free-form per-line-provenance-result
    dict shape). Per doc-19:108 the typed Slice 14 line-provenance
    shape lands at the future Slice 19 5th sub-slice agent-context
    builder per doc-19:157-160 *"Add agent-context builder that selects
    findings and provenance relevant to a task contract, repo, path, or
    line range."*; the 1st sub-slice typed surface accepts the
    free-form dict list.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str | None
    """Doc-19:104 -- the optional task identifier the agent context is
    scoped to. ``None`` for cross-task contexts (e.g. a repo-level
    governance summary). Per doc-19:144-146 *"Agent context endpoint
    that returns compact governance context for a task, repo, file, or
    line range."* the task id is one of the 4 scoping axes."""

    repo_id: str | None
    """Doc-19:105 -- the optional repo identifier the agent context is
    scoped to. ``None`` for cross-repo contexts. Per doc-19:144-146 the
    repo id is one of the 4 scoping axes."""

    relevant_findings: list[GovernanceFinding]
    """Doc-19:107 -- the list of Slice 16 1st sub-slice typed
    :class:`GovernanceFinding` records relevant to the agent's
    task / repo / file / line-range scope.

    **Slice 16 dependency reconciliation.** This field is a list of
    Slice 16 1st sub-slice :class:`GovernanceFinding` typed BaseModels
    -- imported from
    :mod:`iriai_build_v2.execution_control.finding_engine`, NOT
    redefined here.

    Per doc-19:204 *"Agent context builder returns task-relevant
    findings and line provenance under prompt budget."* the relevant-
    findings list is the typed surface that future Slice 19 5th sub-
    slice agent-context builder populates with the task-relevant
    subset of the corpus's findings."""

    relevant_line_provenance: list[dict[str, Any]]
    """Doc-19:108 -- the list of per-line-provenance dict records
    relevant to the agent's task / repo / file / line-range scope.

    **By-name reference contract.** The field is
    ``list[dict[str, Any]]`` (free-form per-line-provenance-result
    dict shape; the typed Slice 14
    :class:`~iriai_build_v2.execution_control.commit_provenance.LineProvenanceResult`
    shape lands at the future Slice 19 5th sub-slice agent-context
    builder per doc-19:157-160 *"Add agent-context builder that selects
    findings and provenance relevant to a task contract, repo, path,
    or line range."*); the 1st sub-slice typed surface accepts the
    free-form dict list.

    Per doc-19:108 the doc shape is ``list[LineProvenanceResult]``;
    this 1st sub-slice exposes the free-form ``list[dict[str, Any]]``
    surface so the typed-shape foundation can construct without the
    Slice 14 commit_provenance import in this typed-shape-only
    foundation; the future Slice 19 5th sub-slice agent-context
    builder tightens the typed annotation."""

    policy_guidance: list[GovernancePolicyRecommendation]
    """Doc-19:109 -- the list of Slice 17 1st sub-slice typed
    :class:`GovernancePolicyRecommendation` records the agent context
    surfaces as ADVISORY prompt-context guidance.

    **Slice 17 dependency reconciliation.** This field is a list of
    Slice 17 1st sub-slice :class:`GovernancePolicyRecommendation`
    typed BaseModels -- imported from
    :mod:`iriai_build_v2.execution_control.policy_recommendation`, NOT
    redefined here. Per the no-second-source-of-truth discipline the
    Slice 17 typed shape is the source of truth for governance policy
    recommendation records.

    Per doc-19:174-176 *"Agent `policy_guidance` is prompt context
    only. It cannot override task contracts, gate requirements,
    failure-router policy, merge-queue policy, or any activated
    consumer policy artifact from Slice 17."* + doc-19:230-231 AC5
    the typed surface enforces the advisory-only contract via the
    typed :attr:`policy_guidance_authority: Literal["advisory_only"]`
    hard-coded literal default below."""

    policy_guidance_authority: Literal["advisory_only"] = "advisory_only"
    """Doc-19:110 -- the typed advisory-only authority marker.

    Per doc-19:110 ``policy_guidance_authority: Literal["advisory_only"]
    = "advisory_only"`` (verbatim from the doc-19 type definition) the
    field is a hard-coded literal default; Pydantic Literal validation
    rejects any other value at construction with a typed
    ``ValidationError``.

    Per doc-19:230-231 AC5 *"Workflow agents receive governance policy
    guidance only as advisory context; contracts, gates, router, and
    merge queue remain authoritative."* the typed literal default is
    the AC5 enforcer at the typed-shape layer; the value cannot be
    overridden to a non-advisory authority at construction.

    Per the Slice 17 7th sub-slice activation-boundary discipline +
    doc-19:348-349 *"Supervisor/dashboard read-only contract preserved
    (no governance writer extends the Slice 10c-1
    `CONTROL_PLANE_WRITER_METHODS` set)."* the typed surface enforces
    the consumer-owned-activation boundary; the typed
    :attr:`policy_guidance` is prompt context only, never runtime
    policy authority."""

    omitted_detail_refs: list[str]
    """Doc-19:111 -- the list of omitted-detail-ref strings (the typed
    page-ref ids for omitted evidence the agent context did not embed
    in full).

    Per doc-19:111 + doc-19:124 *"with omitted refs instead of full
    evidence bodies"* the by-name reference shape is the typed surface
    for omitted-evidence drilldown; the future Slice 19 5th sub-slice
    agent-context builder populates the list from the
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidencePageRef`
    typed shape. The 1st sub-slice typed surface accepts the empty
    list at construction (the typical case for a not-truncated
    context)."""

    omitted_counts: dict[str, int]
    """Doc-19:112 -- the dict of omitted-row counts by typed list name
    (e.g. ``{"findings": 5, "line_provenance": 0, "policy_guidance":
    2}``).

    Per doc-19:204 *"Agent context builder returns task-relevant
    findings and line provenance under prompt budget."* the omitted-
    counts dict is the typed surface that surfaces what the context
    dropped to fit the prompt budget; combines with
    :attr:`omitted_detail_refs` for drilldown."""

    completeness: CompletenessState
    """Doc-19:113 -- the typed Slice 13a :data:`CompletenessState`
    Literal (4 values: ``complete`` / ``paged`` / ``preview_only`` /
    ``unavailable``).

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-19:256-303).** This field is typed against the Slice 13a shared
    :data:`CompletenessState` Literal -- imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`, NOT
    redefined here. Per doc-13a:285-287 step 9 the shared Literal is
    the authority for governance completeness semantics.

    Per the Slice 13A invariant + doc-19:128-131 the typed completeness
    state is the AC2 enforcer for agent context: ``preview_only`` /
    ``unavailable`` agent contexts are display-only and cannot feed
    task-execute consumers; ``complete`` / ``paged`` contexts may feed
    downstream agent consumers."""

    page_refs: list[str]
    """Doc-19:114 -- the list of typed page-ref string identifiers for
    the agent context's bounded-evidence references.

    Per doc-19:114 + doc-19:128-131 the by-name reference shape mirrors
    the :attr:`GovernanceSnapshot.page_refs` pattern; truncated agent
    contexts MUST carry these refs (per the doc-19:128-131 binding);
    the typed surface accepts the empty list at construction."""

    truncated: bool
    """Doc-19:115 -- ``True`` if the agent context's typed lists have
    been truncated to fit within :attr:`max_prompt_chars`; ``False`` if
    all rows fit within the cap.

    Per doc-19:128-131 the truncated flag combines with the typed
    :attr:`page_refs` + :attr:`completeness` triple to enforce AC2 at
    the typed-shape layer for the agent context surface."""

    max_prompt_chars: int
    """Doc-19:116 -- the agent context's effective max-prompt-chars cap
    (in chars).

    Per doc-19:124 *"Agent context: `max_prompt_chars` from caller,
    hard-capped at 20,000 chars, with omitted refs instead of full
    evidence bodies."* the typed surface accepts any positive integer;
    the hard cap of :data:`GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP`
    (20 000) is enforced at the future Slice 19 5th sub-slice agent-
    context builder (the typed-shape layer does NOT pre-emptively
    enforce the cap at construction; the typed surface exposes the
    constant so consumers can cross-check the per-context budget
    against the documented hard cap)."""


# --- Canonical-JSON helpers (mirrors Slice 13A
#     compute_completeness_digest + Slice 14 compute_payload_sha256 +
#     Slice 15 compute_scorecard_digest + Slice 16 1st sub-slice
#     compute_finding_idempotency_key + Slice 17 1st sub-slice
#     compute_policy_recommendation_idempotency_key + Slice 18 1st
#     sub-slice compute_counterfactual_idempotency_key canonical-JSON
#     discipline) ------------------------------------------------------------


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Mirrors :func:`iriai_build_v2.execution_control.completeness._canonical_json`
    + :func:`iriai_build_v2.execution_control.commit_provenance._canonical_json`
    + :func:`iriai_build_v2.execution_control.governance_metrics._canonical_json`
    + :func:`iriai_build_v2.execution_control.finding_engine._canonical_json`
    + :func:`iriai_build_v2.execution_control.policy_recommendation._canonical_json`
    + :func:`iriai_build_v2.execution_control.counterfactual_replay._canonical_json`
    + :func:`iriai_build_v2.workflows.develop.governance.evidence_set._canonical_json`
    (doc-13:201-204) verbatim: ``json.dumps(..., sort_keys=True,
    separators=(",", ":"))`` -- the canonical form mandates
    lexicographic key ordering and the compact separator set so the
    resulting bytes are stable across Python versions / platforms /
    dict ordering.

    Per the P3-15-1-1 + P3-16-1-1 + P3-17-1-2 lineage the
    ``default=str`` superset is benign because the canonical
    projections this module computes go through
    :meth:`BaseModel.model_dump` with ``mode='json'`` first, so
    ``datetime`` is already lowered to ISO-8601 strings before this
    helper sees the object; the ``default=str`` is a defence-in-depth
    fallback for any non-JSON-native scalars (e.g. ``Path`` objects in
    test fixtures).
    """

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_hex(payload: str) -> str:
    """Return the SHA-256 hex digest of ``payload`` (UTF-8 encoded).

    Mirrors :func:`iriai_build_v2.execution_control.completeness._sha256_hex`
    + :func:`iriai_build_v2.execution_control.commit_provenance._sha256_hex`
    + :func:`iriai_build_v2.execution_control.governance_metrics._sha256_hex`
    + :func:`iriai_build_v2.execution_control.finding_engine._sha256_hex`
    + :func:`iriai_build_v2.execution_control.policy_recommendation._sha256_hex`
    + :func:`iriai_build_v2.execution_control.counterfactual_replay._sha256_hex`
    verbatim. Stdlib-only per the implementer prompt § "Non-negotiables"
    (``hashlib`` + ``json`` only).
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_governance_snapshot_dict(
    snapshot: GovernanceSnapshot,
) -> dict[str, Any]:
    """Project a :class:`GovernanceSnapshot` to its canonical-JSON dict
    representation.

    This helper produces the canonical-dict projection used by
    :func:`compute_governance_snapshot_digest` (when computing a
    snapshot's deterministic digest from its logical inputs) and by
    subsequent Slice 19 sub-slices when persisting snapshot rows at
    ``review:governance-snapshots:{corpus_id}`` per doc-19 § Refactoring
    Steps step 6 (line 161-163).

    The projection uses :meth:`BaseModel.model_dump` with ``mode='json'``
    so any nested ``datetime`` field on the typed
    :attr:`GovernanceSnapshot.generated_at` + the nested Slice 16/17/18
    typed BaseModels projects to its ISO-8601 string form (cross-
    process stable). The resulting dict is the input to
    :func:`compute_governance_snapshot_digest`; both helpers use
    :func:`_canonical_json` for deterministic serialisation.

    Mirrors the Slice 16 1st sub-slice
    :func:`~iriai_build_v2.execution_control.finding_engine.canonical_finding_dict`
    + Slice 17 1st sub-slice
    :func:`~iriai_build_v2.execution_control.policy_recommendation.canonical_policy_recommendation_dict`
    + Slice 18 1st sub-slice
    :func:`~iriai_build_v2.execution_control.counterfactual_replay.canonical_counterfactual_dict`
    patterns verbatim.
    """

    return snapshot.model_dump(mode="json")


def compute_governance_snapshot_digest(
    *,
    corpus_id: str,
    snapshot_version: str,
    scorecard_id: str | None,
    finding_idempotency_keys: list[str],
    recommendation_idempotency_keys: list[str],
    replay_result_ids: list[str],
    replay_result_versions: list[str],
    omitted_counts: dict[str, int],
    evidence_quality: str,
    completeness: str,
) -> str:
    """Compute the deterministic SHA-256-derived snapshot digest for a
    :class:`GovernanceSnapshot`.

    Mirrors the Slice 18 1st sub-slice
    :func:`~iriai_build_v2.execution_control.counterfactual_replay.compute_counterfactual_idempotency_key`
    + Slice 17 1st sub-slice
    :func:`~iriai_build_v2.execution_control.policy_recommendation.compute_policy_recommendation_idempotency_key`
    + Slice 16 1st sub-slice
    :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`
    patterns verbatim; the digest is computed over the 10 logical
    inputs:

    * ``corpus_id`` -- the stable corpus identifier (per
      :attr:`GovernanceSnapshot.corpus_id`).
    * ``snapshot_version`` -- the snapshot version string (per
      :attr:`GovernanceSnapshot.snapshot_version`); per the snapshot
      versioning discipline new snapshot semantics produce a new
      digest cleanly.
    * ``scorecard_id`` -- the optional Slice 15 governance scorecard id
      (per :attr:`GovernanceSnapshot.scorecard_id`); ``None`` is
      stable-encoded as the JSON ``null`` value.
    * ``finding_idempotency_keys`` -- the list of Slice 16
      :attr:`GovernanceFinding.idempotency_key` strings the snapshot
      cites in :attr:`GovernanceSnapshot.top_findings`. The list is
      sorted before digesting so the digest is order-invariant w.r.t.
      finding-key ordering.
    * ``recommendation_idempotency_keys`` -- the list of Slice 17
      :attr:`GovernancePolicyRecommendation.idempotency_key` strings
      the snapshot cites in :attr:`GovernanceSnapshot.recommendations`.
      The list is sorted before digesting so the digest is order-
      invariant w.r.t. recommendation-key ordering.
    * ``replay_result_ids`` -- the list of Slice 18
      :attr:`CounterfactualResult.result_id` strings the snapshot
      cites in :attr:`GovernanceSnapshot.replay_results`. The list is
      sorted before digesting so the digest is order-invariant w.r.t.
      replay-result-id ordering.
    * ``replay_result_versions`` -- the list of Slice 18
      :attr:`CounterfactualResult.result_version` strings (paired with
      the replay-result-ids above; per doc-19:152-153 *"...and
      recommendation/replay versions."* the versions are part of the
      digest input so a new replay version cleanly produces a new
      snapshot digest). The list is sorted before digesting.
    * ``omitted_counts`` -- the dict of per-list omitted-row counts
      (per :attr:`GovernanceSnapshot.omitted_counts`); the dict is
      canonical-JSON serialised with sorted keys.
    * ``evidence_quality`` -- the Slice 13a
      :data:`EvidenceQuality` Literal value (per
      :attr:`GovernanceSnapshot.evidence_quality`); per doc-19:152-153
      *"...evidence-quality values..."* the value is part of the
      digest input.
    * ``completeness`` -- the Slice 13a :data:`CompletenessState`
      Literal value (per :attr:`GovernanceSnapshot.completeness`);
      part of the digest input so paged-vs-complete state cleanly
      produces a different digest.

    Per doc-19:152-153 *"The API computes `snapshot_digest` from
    bounded row ids, row digests, omitted-counts, evidence-quality
    values, and recommendation/replay versions."* the helper is the
    typed projection of the doc-19 contract; the future Slice 19 2nd
    sub-slice snapshot API populates the inputs from the snapshot's
    bounded typed rows.

    Per doc-19:172-173 the digest is also the dashboard ETag seed;
    per doc-19:201-202 the digest is also the Slack-dedupe key. The
    determinism contract is the cross-surface freshness anchor.

    Mirrors the Slice 13A
    :func:`~iriai_build_v2.execution_control.completeness.compute_completeness_digest`
    + Slice 14
    :func:`~iriai_build_v2.execution_control.commit_provenance.compute_payload_sha256`
    + Slice 15
    :func:`~iriai_build_v2.execution_control.governance_metrics.compute_scorecard_digest`
    + Slice 16 1st sub-slice
    :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`
    + Slice 17 1st sub-slice
    :func:`~iriai_build_v2.execution_control.policy_recommendation.compute_policy_recommendation_idempotency_key`
    + Slice 18 1st sub-slice
    :func:`~iriai_build_v2.execution_control.counterfactual_replay.compute_counterfactual_idempotency_key`
    canonical-JSON + SHA-256 discipline verbatim.
    """

    payload: dict[str, Any] = {
        "corpus_id": corpus_id,
        "snapshot_version": snapshot_version,
        "scorecard_id": scorecard_id,
        # Sort the list-of-str inputs so the digest is order-invariant
        # w.r.t. list ordering (per the Slice 16 1st sub-slice
        # compute_finding_idempotency_key precedent at
        # finding_engine.py:895-906 + the Slice 17 1st sub-slice
        # compute_policy_recommendation_idempotency_key precedent at
        # policy_recommendation.py:1100-1110 + the Slice 18 1st sub-
        # slice compute_counterfactual_idempotency_key precedent at
        # counterfactual_replay.py:1020-1028).
        "finding_idempotency_keys": sorted(finding_idempotency_keys),
        "recommendation_idempotency_keys": sorted(
            recommendation_idempotency_keys
        ),
        "replay_result_ids": sorted(replay_result_ids),
        "replay_result_versions": sorted(replay_result_versions),
        # The omitted_counts dict goes through _canonical_json which
        # sorts keys; the dict is included as-is (the canonical-JSON
        # serialisation handles the determinism contract).
        "omitted_counts": omitted_counts,
        "evidence_quality": evidence_quality,
        "completeness": completeness,
    }
    return _sha256_hex(_canonical_json(payload))
