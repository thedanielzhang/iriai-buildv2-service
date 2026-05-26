"""Governance package for the develop workflow (Slices 13a-13m + Slice 13A first sub-slice).

Per ``docs/execution-control-plane/13-governance-evidence-model.md:70-71``
the governance package lives at
``src/iriai_build_v2/workflows/develop/governance/`` and per
``docs/execution-control-plane/13-governance-evidence-model.md:178-190``
the first six sub-slices land the package with pure model definitions,
bounded readers, the implementation-journal markdown parser, the
JSONL decision-log parser, the evidence-set digester, and the typed-row
store + bounded review-artifact projection, with no executor hooks:

- **Sub-slice 13a** (``models.py``): the 8 doc-13:68-151 typed shapes from
  § "Proposed Interfaces And Types" -- 3 ``Literal`` enums
  (``EvidenceAuthority`` / ``EvidenceQuality`` / ``CompletenessState``)
  plus 5 ``BaseModel`` classes (``GovernanceReadBudget`` /
  ``GovernanceEvidencePageRef`` / ``GovernanceEvidenceRef`` /
  ``GovernanceEvidenceSet`` / ``ImplementationArtifactAnchor``).
- **Sub-slice 13b** (``ingestor.py``): the doc-13:153-171
  ``GovernanceEvidenceIngestor`` ABC with 3 abstract async methods
  (``ingest_feature_window`` / ``ingest_implementation_artifacts`` /
  ``resolve_ref``) plus a ``DefaultGovernanceEvidenceIngestor``
  pure-typed implementation. Plus 2 supporting shapes the ABC signatures
  require (``GovernanceWindow`` for ``ingest_feature_window``;
  ``GovernanceEvidenceSlice`` for ``resolve_ref``) and the
  ``BoundedReader`` / ``BoundedReadResult`` constructor-injection
  surface for the default implementation.
- **Sub-slice 13c** (``journal_parser.py``): the doc-13:182-183 §
  "Refactoring Steps" step 3 deliverable -- a pure-typed markdown parser
  that takes a path or string body of ``implementation-journal.md`` and
  produces ``ImplementationArtifactAnchor`` rows. Surface: the free
  function ``parse_implementation_journal``. Every 13c-emitted anchor
  carries ``line_start=<markdown-line-no>`` + ``decision_log_line=None``.
- **Sub-slice 13d** (``decision_log_parser.py``): the doc-13:184-185 §
  "Refactoring Steps" step 4 deliverable -- a pure-typed JSONL parser
  that takes a path or string body of ``implementation-decisions.jsonl``
  and produces ``ImplementationArtifactAnchor`` rows. Surface: the free
  function ``parse_implementation_decision_log``. Fills the
  previously-always-``None`` ``decision_log_line`` field on the 13a
  ``ImplementationArtifactAnchor`` shape; every 13d-emitted anchor
  carries ``line_start=None`` + ``decision_log_line=<jsonl-row-no>``
  (the bidirectional ``13c⊕13d`` distinction keeps the two anchor
  types distinguishable at the typed-surface level). Rejects malformed
  JSON rows with a typed ``ValueError`` per doc-13:184 verbatim.

- **Sub-slice 13e** (``evidence_set.py``): the doc-13:186-187 §
  "Refactoring Steps" step 5 deliverable -- a pure-typed evidence-set
  digester that takes the four input lanes (13c journal anchors + 13d
  decision-log anchors + supervisor-digest refs + resource-snapshot
  refs) and produces a fully populated 13a
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceSet`.
  Surface: the free function ``compose_governance_evidence_set``.
  Per doc-13:186 verbatim ("The digest must include source ids and
  content digests, not full artifact bodies") the per-ref digest is
  over ``(ref_id, digest, page_refs)`` -- never over artifact bodies.
  The set-level :attr:`idempotency_key` is the SHA-256 of the sorted
  list of per-ref SHA-256 hex digests (sort-invariant by construction).
  Completeness / quality / source_mix / blockers projections fire
  per doc-13:215-220 / doc-13:173-175 / doc-13a:24, 109-118.

- **Sub-slice 13g** (``store.py``): the doc-13:188-190 § "Refactoring
  Steps" step 6 deliverable -- typed-row storage of
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceSet`
  via :class:`GovernanceEvidenceStore` (ABC) +
  :class:`InMemoryGovernanceEvidenceStore` (in-memory concrete impl) +
  the :func:`project_review_artifact` bounded review-artifact projection
  helper that returns the doc-13:189-190 verbatim
  ``review:governance-evidence:{corpus_id}`` key + canonical-sorted JSON
  body. The store is idempotent on
  :attr:`GovernanceEvidenceSet.idempotency_key` (fail-closed on a
  ``corpus_id`` collision with a different idempotency_key per the
  auto-memory ``feedback_no_silent_degradation`` rule). The store is
  READ-only authority until Slice 13A's evidence-completeness invariant
  lands; it NEVER writes to the ``dag-*`` execution / checkpoint /
  regroup activation / merge artifact spaces per doc-13:201-203
  verbatim.

- **Sub-slice 13i** (``postgres_store.py``): the doc-13:188-190 §
  "Refactoring Steps" step 6 deliverable closing the production-stack
  half -- :class:`PostgresGovernanceEvidenceStore`, the asyncpg-backed
  concrete that implements the 13g
  :class:`GovernanceEvidenceStore` ABC over the new
  ``governance_evidence_sets`` table (``schema.sql:898-957``). The
  Postgres concrete satisfies the same 13g ABC contract identically
  (idempotent on ``(corpus_id, idempotency_key)``; fail-closed on a
  ``corpus_id`` collision with a different ``idempotency_key`` via the
  typed :class:`GovernanceEvidenceStoreIdempotencyConflict`;
  ``put(None)`` raises :class:`TypeError`). Async by necessity
  (:mod:`asyncpg` cannot be invoked synchronously); the 13g ABC's
  sync method signatures are overridden with ``async def`` here per
  the :class:`abc.ABCMeta`-presence rule.

Re-exports total: **8 doc-13:68-151 typed shapes** (3 enums + 5 models)
from ``models.py`` + **2 Slice 13m TypedDict tightenings** (the
``EvidenceSetSourceWindow`` + ``EvidencePageRefStaleCheck`` TypedDicts
that tighten the doc-13:113-126 + doc-13:128-141 ``dict[str, Any]``
shapes; P3-13a-4 closure) from ``models.py`` + **6 ingestor-surface
shapes** (the ABC + the default impl + 2 ABC-signature shapes + 2
bounded-reader port shapes) from ``ingestor.py`` + **1 journal-parser
surface** (the ``parse_implementation_journal`` free function) from
``journal_parser.py`` + **1 decision-log-parser surface** (the
``parse_implementation_decision_log`` free function) from
``decision_log_parser.py`` + **1 evidence-set-digester surface** (the
``compose_governance_evidence_set`` free function) from
``evidence_set.py`` + **4 typed-row store + projection surfaces** (the
``GovernanceEvidenceStore`` ABC + the
``InMemoryGovernanceEvidenceStore`` concrete impl + the
``project_review_artifact`` helper + the
``GovernanceEvidenceStoreIdempotencyConflict`` typed error) from
``store.py`` + **1 Postgres-backed concrete** (the
``PostgresGovernanceEvidenceStore`` class) from ``postgres_store.py``
+ **2 Slice 13A first sub-slice completeness scanner surfaces** (the
``scan_governance_completeness`` free function + the
``CompletenessScanReport`` typed report) from
``completeness_scanner.py`` = **26 exports** in this package's public
``__all__``.

This package contains zero executor hooks, zero runtime adapters, and
zero ingestor *wiring* (the default ingestor takes bounded readers via
constructor injection so it never imports
``ExecutionControlStore`` / ``ControlPlaneSnapshot`` directly; the 13c
journal parser, the 13d decision-log parser, and the 13e evidence-set
digester are also pure -- they take typed shapes in and emit typed
shapes out, never touching the executor or the artifact body store).
Per the governance prompt § "Slice 13A invariant for downstream slices"
no governance ingestor that influences dispatch / verify / merge /
checkpoint / route / scheduler / policy may consume this typed model as
**execution authority** until Slice 13A's evidence-completeness
invariant lands.
"""

from __future__ import annotations

from .completeness_scanner import (
    CompletenessScanReport,
    scan_governance_completeness,
)
from .decision_log_parser import parse_implementation_decision_log
from .evidence_set import compose_governance_evidence_set
from .ingestor import (
    BoundedReadResult,
    BoundedReader,
    DefaultGovernanceEvidenceIngestor,
    GovernanceEvidenceIngestor,
    GovernanceEvidenceSlice,
    GovernanceWindow,
)
from .journal_parser import parse_implementation_journal
from .models import (
    CompletenessState,
    EvidenceAuthority,
    EvidencePageRefStaleCheck,
    EvidenceQuality,
    EvidenceSetSourceWindow,
    GovernanceEvidencePageRef,
    GovernanceEvidenceRef,
    GovernanceEvidenceSet,
    GovernanceReadBudget,
    ImplementationArtifactAnchor,
)
from .postgres_store import PostgresGovernanceEvidenceStore
from .store import (
    GovernanceEvidenceStore,
    GovernanceEvidenceStoreIdempotencyConflict,
    InMemoryGovernanceEvidenceStore,
    project_review_artifact,
)


__all__ = [
    # Literal enums (doc-13:74-87).
    "EvidenceAuthority",
    "EvidenceQuality",
    "CompletenessState",
    # The 5 doc-13:89-151 typed shapes.
    "GovernanceReadBudget",
    "GovernanceEvidencePageRef",
    "GovernanceEvidenceRef",
    "GovernanceEvidenceSet",
    "ImplementationArtifactAnchor",
    # Sub-slice 13m (P3-13a-4 closure) -- TypedDict tightenings of the
    # 13a ``source_window`` (on ``GovernanceEvidenceSet``) +
    # ``stale_check`` (on ``GovernanceEvidencePageRef``) fields. Doc-13:
    # 113-126 + doc-13:128-141 spell these as ``dict[str, Any]``; the
    # TypedDicts (``total=False``) name the documented producer-side
    # keys for static-analysis discoverability without changing the
    # permissive runtime behavior. Available at the package surface so
    # consumers can ``from iriai_build_v2.workflows.develop.governance
    # import EvidenceSetSourceWindow`` for type annotations on their
    # own producer-side helpers.
    "EvidenceSetSourceWindow",
    "EvidencePageRefStaleCheck",
    # Sub-slice 13b -- ABC + default implementation (doc-13:153-171).
    "GovernanceEvidenceIngestor",
    "DefaultGovernanceEvidenceIngestor",
    # Sub-slice 13b -- ABC-signature shapes the ABC requires.
    "GovernanceWindow",
    "GovernanceEvidenceSlice",
    # Sub-slice 13b -- bounded-reader constructor-injection port.
    "BoundedReader",
    "BoundedReadResult",
    # Sub-slice 13c -- implementation-journal markdown parser
    # (doc-13:182-183 step 3).
    "parse_implementation_journal",
    # Sub-slice 13d -- JSONL decision-log parser
    # (doc-13:184-185 step 4). Fills the previously-always-None
    # decision_log_line field on ImplementationArtifactAnchor;
    # 13c anchors have decision_log_line=None, 13d anchors have
    # line_start=None and decision_log_line=<row#>.
    "parse_implementation_decision_log",
    # Sub-slice 13e -- evidence-set digester (doc-13:186-187 step 5).
    # Per doc-13:186 verbatim the per-ref digest is over (ref_id,
    # digest, page_refs); the set-level idempotency_key is the
    # SHA-256 of the sorted list of per-ref SHA-256 hex digests
    # (sort-invariant by construction). Pure-typed: typed-shapes-in
    # / typed-shape-out; no executor wiring; no consumption as
    # execution authority (still gated on Slice 13A).
    "compose_governance_evidence_set",
    # Sub-slice 13g -- typed-row store ABC + in-memory concrete impl +
    # bounded review-artifact projection helper + typed conflict error
    # (doc-13:188-190 step 6). The store is idempotent on
    # GovernanceEvidenceSet.idempotency_key (fail-closed on a corpus_id
    # collision with a different idempotency_key per the auto-memory
    # feedback_no_silent_degradation rule -- the typed conflict error is
    # raised by ``put`` on the collision and is re-exported at the
    # package surface so consumers can ``except
    # GovernanceEvidenceStoreIdempotencyConflict`` without reaching into
    # the sibling ``.store`` module; mirrors the failure_router precedent
    # at ``src/iriai_build_v2/workflows/develop/execution/failure_router.py:1912``
    # where ``IdempotencyConflict`` is also in ``__all__``). The
    # project_review_artifact helper returns the doc-13:189-190 verbatim
    # review:governance-evidence:{corpus_id} key + canonical-sorted JSON
    # body. The store is READ-only authority until Slice 13A; it NEVER
    # writes dag-* execution / checkpoint / regroup activation / merge
    # artifacts per doc-13:201-203 verbatim.
    "GovernanceEvidenceStore",
    "GovernanceEvidenceStoreIdempotencyConflict",
    "InMemoryGovernanceEvidenceStore",
    "project_review_artifact",
    # Sub-slice 13i -- Postgres-backed concrete that implements the 13g
    # GovernanceEvidenceStore ABC over the governance_evidence_sets
    # table (schema.sql:898-957). Mirrors the
    # src/iriai_build_v2/execution_control/regroup_overlay_store.py
    # async asyncpg-backed precedent (connection-bound constructor;
    # canonical-JSON _jsonb serializer; transactional INSERT idempotency
    # + fail-closed conflict raise + asyncpg.UniqueViolationError
    # fallback). The Postgres concrete satisfies the same 13g ABC
    # contract identically (put + get) but is async (asyncpg cannot be
    # invoked synchronously); callers must ``await`` the methods.
    "PostgresGovernanceEvidenceStore",
    # Slice 13A first sub-slice -- completeness scanner surface. Per
    # STATUS.md § "Next safe action" point 4 + the Slice 13A
    # invariant at doc-13a:24, 109-118 the scanner detects (a) missing
    # Slice 00-12 acceptance markers, (b) unresolved P1/P2 findings in
    # the journal tail, and (c) any ``governance_evidence_gap``
    # blocker (non-legacy class) in a consumed GovernanceEvidenceSet.
    # Emits a typed CompletenessScanReport with ``is_complete`` set to
    # True iff every detection list is empty. Pure-typed free
    # function; stdlib + governance siblings + Pydantic only; no
    # executor wiring; no consumption as execution authority OUTSIDE
    # Slice 13A's own acceptance tests per the implementer prompt
    # § non-negotiables.
    "CompletenessScanReport",
    "scan_governance_completeness",
]
