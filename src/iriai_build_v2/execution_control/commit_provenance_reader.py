"""Slice 14 third sub-slice -- line-provenance reader behind a narrow
governance projection interface.

Per ``docs/execution-control-plane/14-commit-and-line-provenance.md``
§ Refactoring Steps step 5 (lines 171-184): *"Add a line-provenance reader
that combines Git blame, commit trailers, notes/refs, and typed
``dag-commit-proof:*`` evidence under ``LineProvenanceQuery`` caps."*

This module owns the line-provenance reader surface (the READ-side complement
of the Slice 14 2nd sub-slice :mod:`~iriai_build_v2.execution_control.commit_provenance_writer`):

* :class:`CommitProofRow` -- typed projection of one ``dag-commit-proof:*``
  evidence row plus the canonical Slice 14 :class:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
  (READ-ONLY cross-cite). Carries the task ids + the precommit_provenance_ref
  the writer wrote.
* :class:`CommitProofProvider` (Protocol) -- the typed source for
  ``dag-commit-proof:*`` evidence; production wires it to the merge queue
  store, tests inject a fake.
* :class:`PayloadStore` (Protocol) -- the typed source for the
  :class:`~iriai_build_v2.execution_control.commit_provenance.CommitProvenancePayload`
  written by the writer to ``refs/iriai/provenance/{digest}`` and
  ``refs/notes/iriai``. Tests inject a fake; production wires it to the
  :class:`GitProvenanceWriter`-paired :func:`make_stdlib_subprocess_runner`
  callable.
* :class:`TrailerSource` (Protocol) -- the typed source for commit message
  trailer parsing (extracts :class:`~iriai_build_v2.execution_control.commit_provenance.CommitProvenanceTrailer`
  from a ``git show --format=%B`` body via the standard trailer format).
* :class:`LineageWalker` (Protocol) -- walks commit lineage for rebase /
  cherry-pick rewrites per doc-14:212-213.
* :class:`LineProvenanceReader` -- the reader port. Takes a
  :class:`~iriai_build_v2.execution_control.commit_provenance.LineProvenanceQuery`
  and returns a :class:`LineProvenanceReadResult` (wraps
  :class:`~iriai_build_v2.execution_control.commit_provenance.LineProvenanceResult`
  + optional :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`).
* :class:`LineProvenanceReadResult` -- the typed read result.
* :class:`BlameLine` -- one typed parsed line of ``git blame --porcelain``
  output.

**Precedence order (doc-14:182-184).** Per *"Governance reads commit
provenance through typed commit proof first, then Git notes/refs, then
trailers. It never treats trailers alone as full proof."* the reader's
resolver consults sources in the following STRICT priority:

1. Typed ``dag-commit-proof:*`` evidence via :class:`CommitProofProvider`.
2. Git notes/refs payload via :class:`PayloadStore` (reads
   ``refs/iriai/provenance/{digest}`` then ``refs/notes/iriai``).
3. Commit message trailers via :class:`TrailerSource`.

The reader's :meth:`LineProvenanceReader.read` returns a partial result with
``completeness="preview_only"`` + a typed
:class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
when ONLY commit trailers are available (the partial-evidence case per
doc-14:198-199 *"Commit has trailers but missing note/ref: line query
returns partial evidence and a gap; governance records provenance-gap
findings."*). The trailer carries only the task id DIGEST + queue item id
DIGEST (per doc-14:137 compact trailer rule) -- the reader cannot enumerate
the full task ids without the typed proof or the payload, so the result
is bounded preview ONLY.

**`preview_only` INELIGIBLE discipline (doc-14:202-205).** Per *"A bounded
partial result without exact page refs is `preview_only` and cannot feed
context packages, governance findings, metrics, or policy recommendations
as line provenance authority."* the reader's :class:`LineProvenanceReadResult`
exposes :attr:`LineProvenanceReadResult.is_eligible_for_downstream_consumers`
which is ``False`` whenever ``completeness == "preview_only"``. Downstream
consumers (context packages / governance findings / metrics / policy
recommendations) MUST gate on this flag and refuse to consume preview-only
evidence as line-provenance authority. The eligibility flag is the
typed-surface mechanism that makes the doc-14:202-205 invariant
ENFORCEABLE at the API boundary (not just at the documentation boundary).

**Rebase/cherry-pick lineage walk (doc-14:212-213).** Per *"Rebase/cherry-pick:
preserve old/new lineage and reject ambiguous line provenance unless
lineage is recorded."* the reader's :class:`LineageWalker` Protocol is
consulted whenever a blamed commit hash does NOT match a typed proof or
a payload at the natural ref. The walker returns the lineage chain
(old_commit_hash -> new_commit_hash) recorded by the Slice 14 4th
sub-slice (forthcoming) lineage emitter; if no lineage is recorded the
reader records a ``governance_evidence_conflict`` gap finding (NEVER
silently treats the wrong commit as authoritative).

**Non-blocking failure routing discipline (doc-14:242-243).** Per
*"Governance provenance projection failures never block ``dag-group:*``
checkpointing, merge queue integration, or resume"* the reader is a
POST-CHECKPOINT OBSERVER. When the reader cannot resolve typed evidence
for some part of the line range it records a typed
:class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
and returns gracefully with `completeness="preview_only"` or
``completeness="unavailable"``. The caller MUST NOT propagate the failure
to the executor / checkpoint / merge-queue / resume code paths. The
corresponding typed failure ids (``line_provenance_gap`` +
``governance_evidence_conflict``) REUSE the Slice 14 2nd sub-slice
registration under EXISTING ``evidence_corruption`` failure_class with
NON-blocking ``retry_governance_projection`` route action; this 3rd
sub-slice ADDS NO new failure ids or route actions.

**Bounded-cap discipline (doc-14:119-122 + doc-14:199-201).** Per
*"Line range exceeds inline caps: reject the query or return
`completeness="paged"` with exact `GovernanceEvidencePageRef` rows for
the remaining history."* the reader enforces the 4 caps from
:class:`~iriai_build_v2.execution_control.commit_provenance.LineProvenanceQuery`:
``max_lines`` + ``max_commits`` + ``max_payload_bytes`` + ``timeout_ms``.
When ANY cap is exceeded the reader returns ``completeness="paged"``
with exact :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidencePageRef`
rows pointing to the page-able sub-ranges (line page, commit page, payload
page).

**Slice 08 non-alteration discipline (doc-14:155-160 step 1).** The Slice 08
canonical :class:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
typed row (10 fields verbatim) is READ-ONLY from this slice. The reader
cross-cites the :class:`RepoCommitProof` via the
:class:`CommitProofProvider` Protocol but does NOT mutate it.

**Implementation discipline.** Stdlib (``json`` + ``hashlib`` + ``re``) +
Pydantic v2 + Slice 13A modules (``.completeness``) + Slice 13a modules
(``..workflows.develop.governance.models``) + Slice 14 1st sub-slice
typed shapes + Slice 14 2nd sub-slice helpers + Slice 08 modules
(``..execution_control.merge_queue_store``) only. NO imports from
``governance/`` outside ``governance.models``. NO imports from
``workflows/develop/execution/phases/`` / ``supervisor`` / ``dashboard``.

Per the auto-memory ``feedback_flat_structured_output`` rule control
fields are flat primitives. Per the auto-memory
``feedback_no_silent_degradation`` rule every silent loss is a typed
:class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`.
Per the auto-memory ``feedback_no_overengineer_use_library`` rule the
module mirrors the Slice 14 1st + 2nd sub-slice precedents verbatim
without introducing new abstractions.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from iriai_build_v2.execution_control.commit_provenance import (
    CommitProvenancePayload,
    CommitProvenanceTrailer,
    LineProvenanceQuery,
    LineProvenanceResult,
)
from iriai_build_v2.execution_control.commit_provenance_writer import (
    COMMIT_PROVENANCE_GAP_FAILURE_IDS,
    CommitProvenanceGapFinding,
    GitSubprocessResult,
    GitSubprocessRunner,
    compute_notes_ref_namespace,
)
from iriai_build_v2.execution_control.merge_queue_store import RepoCommitProof
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidencePageRef,
)


__all__ = [
    # Typed projections.
    "CommitProofRow",
    "BlameLine",
    "LineageRecord",
    # Source ports (Protocol).
    "CommitProofProvider",
    "PayloadStore",
    "TrailerSource",
    "LineageWalker",
    # The reader + its typed read result.
    "LineProvenanceReader",
    "LineProvenanceReadResult",
    # Pure helpers.
    "parse_blame_porcelain",
    "parse_trailer_from_commit_body",
    "compute_line_provenance_completeness_digest",
    # Re-export the 2nd sub-slice typed failure ids tuple for downstream
    # convenience (REUSE; ADDS NO new ids).
    "COMMIT_PROVENANCE_GAP_FAILURE_IDS",
]


# --- Canonical-JSON helpers (mirrored from the writer per doc-13:201-204) ---


def _canonical_json(obj: object) -> str:
    """Canonical-JSON serialiser.

    Mirrors :func:`iriai_build_v2.execution_control.commit_provenance_writer._canonical_json`
    + :func:`iriai_build_v2.execution_control.commit_provenance._canonical_json`
    verbatim per the doc-13:201-204 + doc-14:151-153 canonical-form contract.
    """

    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    """SHA-256 hex digest of ``payload`` (UTF-8 encoded).

    Mirrors :func:`iriai_build_v2.execution_control.commit_provenance_writer._sha256_hex`
    verbatim.
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- Typed projections (READ-ONLY views over Slice 08 + Slice 14 surfaces) --


class CommitProofRow(BaseModel):
    """Typed projection of one ``dag-commit-proof:*`` evidence row.

    Per doc-14:182-184 *"Governance reads commit provenance through typed
    commit proof first..."* this typed row is the FIRST-priority source the
    reader consults. It carries the Slice 08 canonical
    :class:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
    (10 fields verbatim, READ-ONLY) plus the task ids + the precommit
    provenance ref the Slice 14 2nd sub-slice writer wrote.

    The shape is intentionally MINIMAL: the reader needs only what's
    necessary to satisfy a line-provenance query without authoring new
    typed authority. The full
    :class:`~iriai_build_v2.execution_control.commit_provenance.CommitProvenancePayload`
    is consulted via :class:`PayloadStore` when needed (e.g. to enumerate
    contract / attempt / gate evidence ids).

    Per the auto-memory ``feedback_flat_structured_output`` rule control
    fields are flat primitives.
    """

    # extra='forbid' aligns with the Slice 14 1st + 2nd sub-slice precedents.
    model_config = ConfigDict(extra="forbid")

    commit_hash: str
    """The commit hash this row attests to. Matches
    :class:`RepoCommitProof.result_commit`."""

    repo_id: str
    """The repo this commit belongs to. Matches
    :class:`RepoCommitProof.repo_id`."""

    task_ids: list[str] = Field(default_factory=list)
    """The enumerated task id list this commit covers (per the writer's
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceWriterInputs.task_ids`)."""

    precommit_provenance_ref: str
    """The canonical ``refs/iriai/provenance/{digest}`` ref path the
    writer wrote for this commit. Used by the reader to dereference the
    full :class:`CommitProvenancePayload` via :class:`PayloadStore`."""

    commit_proof: RepoCommitProof
    """The Slice 08 canonical :class:`RepoCommitProof` (10 fields verbatim,
    READ-ONLY)."""


class BlameLine(BaseModel):
    """One parsed line of ``git blame --porcelain`` output.

    Per doc-14:171-173 the reader uses ``git blame`` to attribute the
    queried line range to the historical commit chain. The porcelain
    format is the stable cross-process format git emits when called with
    ``--porcelain`` -- it carries the commit hash, the author info, the
    original / final line numbers, and the line content.

    The typed projection is intentionally MINIMAL: the reader only needs
    the commit hash + the final line number for line-to-commit attribution.
    The original line number is carried for diagnostics (line moves across
    commits).
    """

    # extra='forbid' aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    commit_hash: str
    """The commit hash that last touched this line. 40-character SHA-1
    hex (per Git's commit hash format)."""

    original_line: int
    """The line number in the originating commit."""

    final_line: int
    """The line number in the ref the blame was taken at (matches the
    query's ``line_start``..``line_end`` 1-indexed range)."""

    content: str = ""
    """The raw line content (free-form; may be empty for blame on deleted
    lines)."""


class LineageRecord(BaseModel):
    """One typed lineage record for rebase/cherry-pick handling.

    Per doc-14:212-213 *"Rebase/cherry-pick: preserve old/new lineage and
    reject ambiguous line provenance unless lineage is recorded."* the
    Slice 14 4th sub-slice (forthcoming) emits these typed records when a
    commit is rebased / cherry-picked. The reader consults them via
    :class:`LineageWalker` to resolve a blamed commit hash to the
    canonical typed-proof commit.

    Both directions are stored: ``old_commit_hash`` is the pre-rewrite
    commit (the one a blame on the historical ref would return);
    ``new_commit_hash`` is the post-rewrite commit (the one the typed
    proof attests to after a rebase / cherry-pick).
    """

    # extra='forbid' aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    old_commit_hash: str
    """The pre-rewrite commit hash (e.g. before a rebase / cherry-pick)."""

    new_commit_hash: str
    """The post-rewrite commit hash (e.g. after a rebase / cherry-pick)."""

    reason: Literal["rebase", "cherry-pick", "amend", "squash"]
    """The taxonomy of the lineage transition. Constrained Literal so
    typo'd kwargs fail closed."""


# --- Source ports (Protocol) ------------------------------------------------


class CommitProofProvider(Protocol):
    """Typed source for ``dag-commit-proof:*`` evidence.

    Per doc-14:182-184 this is the FIRST-priority source the reader
    consults. Production wires this to the Slice 08 merge queue store's
    typed evidence lookup; tests inject a fake that returns canned
    :class:`CommitProofRow` rows.

    The provider returns ``None`` when no typed proof exists for the
    given commit (e.g. legacy pre-Slice-14 commits per doc-14:189-190:
    *"Existing legacy commits without provenance are allowed as historical
    evidence gaps and must not be rewritten."*).
    """

    def get_commit_proof(
        self,
        *,
        repo_id: str,
        commit_hash: str,
    ) -> CommitProofRow | None: ...


class PayloadStore(Protocol):
    """Typed source for the
    :class:`~iriai_build_v2.execution_control.commit_provenance.CommitProvenancePayload`
    written by the Slice 14 2nd sub-slice writer.

    Per doc-14:144-150 the writer writes payloads to
    ``refs/iriai/provenance/{precommit_provenance_digest}`` AND
    ``refs/notes/iriai`` keyed by commit hash. Production wires this to
    the same :class:`GitSubprocessRunner` Protocol port the writer uses;
    tests inject a fake that returns canned :class:`CommitProvenancePayload`
    rows.

    Per doc-14:182-184 this is the SECOND-priority source (after the
    typed :class:`CommitProofProvider`). The reader consults this when
    typed proof is unavailable but a payload was written by the writer.

    The store returns ``None`` when no payload exists for the given ref
    (e.g. the writer hasn't run yet, or the ref was garbage-collected).
    """

    def get_payload_by_ref(
        self,
        *,
        repo_id: str,
        ref: str,
    ) -> CommitProvenancePayload | None: ...

    def get_payload_from_notes(
        self,
        *,
        repo_id: str,
        commit_hash: str,
    ) -> CommitProvenancePayload | None: ...


class TrailerSource(Protocol):
    """Typed source for commit message trailer parsing.

    Per doc-14:182-184 this is the THIRD-priority source (after typed
    proof + payload). Per doc-14:188 *"It never treats trailers alone as
    full proof."* -- a trailers-only resolution yields ``preview_only``
    + a typed :class:`CommitProvenanceGapFinding` (per the partial-evidence
    case in doc-14:198-199).

    Production wires this to a Git subprocess call (``git show --format=%B``
    + trailer parsing); tests inject a fake that returns canned
    :class:`CommitProvenanceTrailer` rows.

    The source returns ``None`` when the commit body does NOT contain an
    iriai trailer (e.g. legacy commits).
    """

    def get_trailer(
        self,
        *,
        repo_id: str,
        commit_hash: str,
    ) -> CommitProvenanceTrailer | None: ...


class LineageWalker(Protocol):
    """Walks commit lineage for rebase / cherry-pick rewrites per
    doc-14:212-213.

    Production wires this to the Slice 14 4th sub-slice (forthcoming)
    lineage emitter; tests inject a fake that returns canned
    :class:`LineageRecord` rows.

    Per doc-14:212-213 *"Rebase/cherry-pick: preserve old/new lineage
    and reject ambiguous line provenance unless lineage is recorded."*
    the walker is consulted whenever a blamed commit hash does NOT match
    a typed proof or a payload at the natural ref. If the walker returns
    ``None`` the reader records a typed
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    with ``governance_evidence_conflict`` failure id (per the
    "reject ambiguous line provenance unless lineage is recorded" rule).
    """

    def walk_from_old(
        self,
        *,
        repo_id: str,
        old_commit_hash: str,
    ) -> LineageRecord | None: ...


# --- Pure helpers -----------------------------------------------------------


# The git blame --porcelain output format header lines look like:
#     <40-char commit hash> <orig_line> <final_line> [<num_lines>]
# followed by metadata lines (author, committer, summary, etc.) and
# finally a content line prefixed with a single tab.
#
# We only need the commit hash + the orig/final line numbers per
# :class:`BlameLine`; the rest is metadata we ignore.
_PORCELAIN_HEADER_RE = re.compile(
    r"^(?P<hash>[0-9a-f]{40}) (?P<orig>\d+) (?P<final>\d+)(?: \d+)?$"
)


def parse_blame_porcelain(porcelain_output: str) -> list[BlameLine]:
    """Parse ``git blame --porcelain`` stdout into typed :class:`BlameLine` rows.

    Per doc-14:171-173 the reader uses ``git blame`` for line-to-commit
    attribution. The porcelain format is the stable cross-process format
    git emits when called with ``--porcelain`` (or ``--line-porcelain``).
    This parser handles the porcelain format verbatim.

    Returns the typed list in ``final_line`` order (1-indexed). Returns
    the empty list when ``porcelain_output`` is empty / lacks any header
    lines.

    Per the auto-memory ``feedback_no_silent_degradation`` rule malformed
    lines are SKIPPED (logged via the future ingest-time linter; not in
    this sub-slice's scope) rather than silently merged into the next
    valid line. The parser also tolerates Windows-style ``\\r\\n`` line
    endings.
    """

    results: list[BlameLine] = []
    current_header: tuple[str, int, int] | None = None  # (hash, orig, final)

    for raw_line in porcelain_output.splitlines():
        # Tolerate trailing CR (Windows line endings) -- splitlines already
        # handles \r\n but defensively strip CR if present.
        line = raw_line.rstrip("\r")

        header_match = _PORCELAIN_HEADER_RE.match(line)
        if header_match is not None:
            current_header = (
                header_match.group("hash"),
                int(header_match.group("orig")),
                int(header_match.group("final")),
            )
            continue

        # Content lines are prefixed with a single TAB per the porcelain
        # format spec.
        if line.startswith("\t") and current_header is not None:
            commit_hash, orig, final = current_header
            results.append(
                BlameLine(
                    commit_hash=commit_hash,
                    original_line=orig,
                    final_line=final,
                    content=line[1:],  # strip the leading TAB
                )
            )
            current_header = None

    return results


# The Git trailer convention is `Trailer-Key: trailer-value` at the bottom
# of the commit body, separated from the body by an empty line. We parse
# the iriai-specific subset:
#     Iriai-Feature-Id: <str>
#     Iriai-Group-Idx: <int>
#     Iriai-Effective-Group-Idx: <int|->
#     Iriai-Task-Ids-Digest: <hex64>
#     Iriai-Merge-Queue-Item-Ids-Digest: <hex64>
#     Iriai-Checkpoint-Ref: <str>
#     Iriai-Precommit-Provenance-Ref: <str>
#     Iriai-Precommit-Provenance-Digest: <hex64>
_TRAILER_KEY_TO_FIELD = {
    "iriai-feature-id": "feature_id",
    "iriai-group-idx": "group_idx",
    "iriai-effective-group-idx": "effective_group_idx",
    "iriai-task-ids-digest": "task_ids_digest",
    "iriai-merge-queue-item-ids-digest": "merge_queue_item_ids_digest",
    "iriai-checkpoint-ref": "checkpoint_ref",
    "iriai-precommit-provenance-ref": "precommit_provenance_ref",
    "iriai-precommit-provenance-digest": "precommit_provenance_digest",
}


def parse_trailer_from_commit_body(commit_body: str) -> CommitProvenanceTrailer | None:
    """Parse a :class:`CommitProvenanceTrailer` from a commit message body.

    Per doc-14:79-87 the trailer is carried in the commit message footer
    using the Git ``Trailer-Key: value`` convention. This parser scans
    the body for the 8 iriai-* trailer keys and constructs the typed
    :class:`CommitProvenanceTrailer`.

    Returns ``None`` when the body does NOT contain a complete iriai
    trailer set (e.g. legacy commits with no iriai trailers, or partial
    trailers where some keys are missing). Per doc-14:188 *"It never
    treats trailers alone as full proof."* + doc-14:198-199 the
    incomplete-trailer case yields a partial-evidence result; the reader's
    public surface :meth:`LineProvenanceReader.read` handles the
    None-return case by recording a typed
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`.

    Per the auto-memory ``feedback_no_silent_degradation`` rule a body
    with ONLY some of the 8 trailer keys (a partially-formed trailer)
    returns ``None`` and is treated as missing -- not as a partial
    overlay on a default-constructed trailer.
    """

    found: dict[str, str] = {}
    for raw_line in commit_body.splitlines():
        line = raw_line.strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key_normalized = key.strip().lower()
        if key_normalized in _TRAILER_KEY_TO_FIELD:
            found[_TRAILER_KEY_TO_FIELD[key_normalized]] = value.strip()

    # Per the 8-field doc-14:79-87 trailer contract every key MUST be
    # present (the writer's :func:`compute_trailer` always emits all 8).
    # A partial trailer is treated as missing per the feedback_no_silent_degradation
    # rule.
    required_keys = {
        "feature_id",
        "group_idx",
        "task_ids_digest",
        "merge_queue_item_ids_digest",
        "checkpoint_ref",
        "precommit_provenance_ref",
        "precommit_provenance_digest",
    }
    if not required_keys.issubset(found.keys()):
        return None

    # Convert group_idx + effective_group_idx to int per the typed contract.
    try:
        group_idx = int(found["group_idx"])
    except ValueError:
        return None

    effective_group_idx: int | None = None
    raw_effective = found.get("effective_group_idx")
    if raw_effective is not None and raw_effective not in ("-", ""):
        try:
            effective_group_idx = int(raw_effective)
        except ValueError:
            return None

    try:
        return CommitProvenanceTrailer(
            feature_id=found["feature_id"],
            group_idx=group_idx,
            effective_group_idx=effective_group_idx,
            task_ids_digest=found["task_ids_digest"],
            merge_queue_item_ids_digest=found["merge_queue_item_ids_digest"],
            checkpoint_ref=found["checkpoint_ref"],
            precommit_provenance_ref=found["precommit_provenance_ref"],
            precommit_provenance_digest=found["precommit_provenance_digest"],
        )
    except Exception:
        # Per feedback_no_silent_degradation a typed-validation failure is
        # treated as missing -- the caller logs a gap finding instead of
        # raising into the executor.
        return None


def compute_line_provenance_completeness_digest(
    result: LineProvenanceResult,
) -> str:
    """Compute the deterministic completeness digest for a
    :class:`LineProvenanceResult`.

    Per doc-14:124-133 the result carries a ``completeness_digest`` field
    (SHA-256 hex over the completeness payload). This helper produces the
    digest from the canonical-JSON projection of the result.

    Mirrors the Slice 13A
    :func:`~iriai_build_v2.execution_control.completeness.compute_completeness_digest`
    contract: two reads of the same logical result produce byte-identical
    digests; used by future Slice 14 consumer wiring to detect stale
    completeness state.
    """

    # Project the result onto canonical-JSON with the completeness_digest
    # field itself OMITTED (mirrors the payload_sha256 self-exclusion
    # discipline at doc-14:151-153).
    raw = result.model_dump()
    raw.pop("completeness_digest", None)
    return _sha256_hex(_canonical_json(raw))


# --- The reader (doc-14:171-184 step 5) -------------------------------------


class LineProvenanceReadResult(BaseModel):
    """Typed result of one :meth:`LineProvenanceReader.read` call.

    Per doc-14:171-184 + doc-14:202-205 the read surface returns a typed
    :class:`LineProvenanceResult` plus an optional gap finding. The
    :attr:`is_eligible_for_downstream_consumers` flag is the typed-surface
    mechanism that ENFORCES the doc-14:202-205 ``preview_only`` ineligibility
    invariant at the API boundary.

    Per doc-14:242-243 the reader NEVER raises a failure to the caller;
    failures project onto a typed :class:`CommitProvenanceGapFinding` in
    the :attr:`gap_finding` field.
    """

    # extra='forbid' aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    result: LineProvenanceResult
    """The typed :class:`LineProvenanceResult` -- always populated, even
    on partial reads (the result may carry ``completeness="preview_only"``
    + ``confidence=0.0`` for a fully-degraded read)."""

    gap_finding: CommitProvenanceGapFinding | None = None
    """Populated when the reader encountered a missing / conflicting
    evidence source (per doc-14:194-201). Per doc-14:242-243 the finding
    is NON-blocking; the caller MUST NOT propagate it to the executor /
    checkpoint / merge-queue / resume code paths."""

    git_invocations: list[GitSubprocessResult] = Field(default_factory=list)
    """The typed list of :class:`GitSubprocessResult` rows for every Git
    invocation the reader made (used by tests + the future Slice 18
    replay layer to assert exact Git interaction traces)."""

    @property
    def is_eligible_for_downstream_consumers(self) -> bool:
        """Doc-14:202-205 invariant: a ``preview_only`` result is
        INELIGIBLE to feed context packages / governance findings /
        metrics / policy recommendations as line provenance authority.

        Returns ``True`` when ``result.completeness`` is one of
        ``"complete"`` / ``"paged"`` (the two exact-evidence states per
        the Slice 13A invariant doc-13a:18-23); returns ``False`` for
        ``"preview_only"`` and ``"unavailable"``.

        Downstream consumers (context packages / governance findings /
        metrics / policy recommendations) MUST gate on this flag and
        refuse to consume preview-only or unavailable evidence as
        line-provenance authority.
        """

        return self.result.completeness in ("complete", "paged")


class LineProvenanceReader:
    """Line-provenance reader (doc-14:171-184 step 5).

    Per doc-14:171-173 the reader combines Git blame + commit trailers +
    Git notes/refs + typed ``dag-commit-proof:*`` evidence under the
    :class:`LineProvenanceQuery` caps.

    Per doc-14:182-184 the precedence order is STRICT: typed proof FIRST,
    then notes/refs payload, then commit trailers; the reader NEVER
    treats trailers alone as full proof.

    Per doc-14:242-243 the reader is a POST-CHECKPOINT OBSERVER; it NEVER
    raises a failure to the caller and NEVER blocks the executor / merge
    queue / resume.

    The reader is TESTABLE via fake source ports
    (:class:`CommitProofProvider` + :class:`PayloadStore` +
    :class:`TrailerSource` + :class:`LineageWalker` + :class:`GitSubprocessRunner`):
    unit tests inject fakes that return canned results; the reader's
    behavior is fully deterministic given the inputs + the fakes' responses.
    """

    def __init__(
        self,
        *,
        repo_path: str,
        runner: GitSubprocessRunner,
        commit_proof_provider: CommitProofProvider,
        payload_store: PayloadStore,
        trailer_source: TrailerSource,
        lineage_walker: LineageWalker,
        notes_ref: str | None = None,
    ) -> None:
        """Construct a reader bound to a repo + a subprocess runner + 4
        typed source ports.

        :param repo_path: filesystem path to the Git working tree the
            reader operates on (passed as ``cwd`` to every Git invocation).
        :param runner: the :class:`GitSubprocessRunner` callable; in
            production a stdlib-subprocess wrapper, in tests a fake fixture.
        :param commit_proof_provider: the FIRST-priority typed proof source.
        :param payload_store: the SECOND-priority payload source
            (reads ``refs/iriai/provenance/{digest}`` then ``refs/notes/iriai``).
        :param trailer_source: the THIRD-priority trailer source.
        :param lineage_walker: walks rebase/cherry-pick lineage per
            doc-14:212-213.
        :param notes_ref: the Git notes namespace; defaults to
            :func:`compute_notes_ref_namespace` (``refs/notes/iriai``).
        """

        self._repo_path = repo_path
        self._runner = runner
        self._commit_proof_provider = commit_proof_provider
        self._payload_store = payload_store
        self._trailer_source = trailer_source
        self._lineage_walker = lineage_walker
        self._notes_ref = notes_ref or compute_notes_ref_namespace()

    @property
    def repo_path(self) -> str:
        """Filesystem path the reader operates on (read-only)."""

        return self._repo_path

    @property
    def notes_ref(self) -> str:
        """Git notes namespace the reader reads from (read-only)."""

        return self._notes_ref

    def read(self, query: LineProvenanceQuery) -> LineProvenanceReadResult:
        """Read the line-provenance for one query (doc-14:171-184 step 5).

        Returns a typed :class:`LineProvenanceReadResult` with the
        :class:`LineProvenanceResult` + an optional
        :class:`CommitProvenanceGapFinding`.

        Per doc-14:242-243 NEVER raises a failure to the caller. Per
        doc-14:199-201 returns ``completeness="paged"`` with exact
        :class:`GovernanceEvidencePageRef` rows when the query exceeds
        inline caps. Per doc-14:202-205 returns
        ``completeness="preview_only"`` ONLY when bounded partial results
        lack exact page refs; the result is then ineligible to feed
        downstream consumers.

        The read sequence (per doc-14:171-184):

        1. Validate the query against the typed
           :class:`LineProvenanceQuery` caps. If the line range exceeds
           ``max_lines``, short-circuit to ``completeness="paged"`` with
           exact :class:`GovernanceEvidencePageRef` rows for the page-able
           sub-ranges (per doc-14:199-201).
        2. Run ``git blame --porcelain`` for the queried line range to
           gather the per-line attribution.
        3. For each unique commit hash returned by blame:
           a. Consult :class:`CommitProofProvider` for the typed
              ``dag-commit-proof:*`` evidence (FIRST priority per
              doc-14:182-184).
           b. If no typed proof, consult :class:`PayloadStore` for the
              Git notes/refs payload (SECOND priority).
           c. If no payload at the natural ref, consult
              :class:`LineageWalker` for a rebase/cherry-pick rewrite
              (per doc-14:212-213).
           d. If lineage walk succeeds, consult
              :class:`CommitProofProvider` again for the rewritten commit.
           e. If no proof / payload / lineage, consult
              :class:`TrailerSource` for the commit trailer (THIRD
              priority); a trailer-only resolution yields
              ``completeness="preview_only"`` + a gap finding per
              doc-14:188 + doc-14:198-199.
        4. Aggregate the resolved sources into a typed
           :class:`LineProvenanceResult` with:
           - ``commit_hashes`` = the unique commits touching the range.
           - ``task_ids`` = the union of task ids from the resolved sources.
           - ``provenance_payload_refs`` = the unique payload ref paths.
           - ``page_refs`` = exact page refs when paged.
           - ``completeness`` = the strongest completeness state across all
             resolved sources (``complete`` >= ``paged`` >> ``preview_only``
             >> ``unavailable``).
           - ``completeness_digest`` = the canonical-JSON digest.
           - ``confidence`` = bounded by the weakest source (typed proof
             = 1.0; payload = 0.8; lineage-walked = 0.6; trailer-only = 0.4).
           - ``gaps`` = controlled-vocabulary gap-reason strings.
        """

        invocations: list[GitSubprocessResult] = []

        # Step 1: validate the query against the caps. If the line range
        # exceeds max_lines, short-circuit to paged exact (per doc-14:199-201).
        line_range = query.line_end - query.line_start + 1
        if line_range > query.max_lines:
            return self._make_paged_result(query, invocations)

        # Step 2: run git blame.
        blame_lines = self._run_blame(query, invocations)

        if not blame_lines:
            # No blame output -- query target empty / unreadable. Record
            # a typed gap finding + return unavailable.
            return self._make_unavailable_result(
                query,
                invocations,
                reason=(
                    "git blame returned no output for the queried range "
                    "(blame target may be empty / unreadable / out of range)"
                ),
                failure_id="line_provenance_gap",
            )

        # Step 3: resolve each unique commit hash via the precedence chain.
        unique_commit_hashes = sorted({bl.commit_hash for bl in blame_lines})

        # Cap-check: too many commits triggers paged (per doc-14:120 + 199-201).
        if len(unique_commit_hashes) > query.max_commits:
            return self._make_paged_result(query, invocations)

        resolved_task_ids: set[str] = set()
        resolved_payload_refs: set[str] = set()
        resolutions: list[tuple[str, _ResolutionSource]] = []
        gap_reasons: list[str] = []

        for commit_hash in unique_commit_hashes:
            source = self._resolve_one_commit(
                query=query,
                commit_hash=commit_hash,
                invocations=invocations,
                gap_reasons=gap_reasons,
            )
            resolutions.append((commit_hash, source))
            if source.task_ids is not None:
                resolved_task_ids.update(source.task_ids)
            if source.provenance_payload_ref is not None:
                resolved_payload_refs.add(source.provenance_payload_ref)

        # Step 4: aggregate.
        completeness = self._compute_completeness(resolutions, gap_reasons)
        confidence = self._compute_confidence(resolutions)

        result = LineProvenanceResult(
            commit_hashes=unique_commit_hashes,
            task_ids=sorted(resolved_task_ids),
            provenance_payload_refs=sorted(resolved_payload_refs),
            page_refs=[],
            completeness=completeness,
            completeness_digest="placeholder",
            confidence=confidence,
            gaps=gap_reasons,
        )
        # Self-exclude the completeness_digest from its own digest input
        # (mirrors the payload_sha256 self-exclusion discipline).
        result = result.model_copy(
            update={
                "completeness_digest": compute_line_provenance_completeness_digest(
                    result
                )
            }
        )

        # Cap-check: payload bytes (per doc-14:121 + 199-201).
        if len(_canonical_json(result.model_dump())) > query.max_payload_bytes:
            return self._make_paged_result(query, invocations)

        # Conflict / partial-evidence gap finding (per doc-14:194-201).
        gap_finding: CommitProvenanceGapFinding | None = None
        if completeness == "preview_only":
            gap_finding = self._make_gap_finding(
                query=query,
                failure_id="line_provenance_gap",
                reason=(
                    "line-provenance read produced preview_only result; "
                    "trailers-only resolution lacks exact page refs "
                    "(doc-14:188 + 202-205)"
                ),
                evidence_payload={
                    "gap_reasons": gap_reasons,
                    "preview_commit_hashes": [
                        h
                        for h, src in resolutions
                        if src.source_kind == "trailer_only"
                    ],
                },
            )
        elif any(src.source_kind == "conflict" for _, src in resolutions):
            gap_finding = self._make_gap_finding(
                query=query,
                failure_id="governance_evidence_conflict",
                reason=(
                    "line-provenance read encountered evidence conflict "
                    "(blamed commit lacks typed proof + payload + lineage; "
                    "doc-14:212-213)"
                ),
                evidence_payload={
                    "conflict_commit_hashes": [
                        h
                        for h, src in resolutions
                        if src.source_kind == "conflict"
                    ],
                },
            )

        return LineProvenanceReadResult(
            result=result,
            gap_finding=gap_finding,
            git_invocations=invocations,
        )

    # ── Private helpers ────────────────────────────────────────────────────

    def _run(
        self,
        args: list[str],
        invocations: list[GitSubprocessResult],
    ) -> GitSubprocessResult:
        result = self._runner(args, cwd=self._repo_path)
        invocations.append(result)
        return result

    def _run_blame(
        self,
        query: LineProvenanceQuery,
        invocations: list[GitSubprocessResult],
    ) -> list[BlameLine]:
        """Run ``git blame --porcelain`` for the queried range."""

        args = [
            "blame",
            "--porcelain",
            f"-L{query.line_start},{query.line_end}",
            query.ref,
            "--",
            query.path,
        ]
        result = self._run(args, invocations)
        if result.returncode != 0:
            # Blame failed (path doesn't exist, ref is bad, etc.).
            return []
        return parse_blame_porcelain(result.stdout)

    def _resolve_one_commit(
        self,
        *,
        query: LineProvenanceQuery,
        commit_hash: str,
        invocations: list[GitSubprocessResult],
        gap_reasons: list[str],
    ) -> "_ResolutionSource":
        """Resolve one commit hash via the precedence chain (doc-14:182-184).

        Order: typed proof → notes/refs payload → lineage walk → trailer-only.
        """

        # Step 3a: typed dag-commit-proof:* (FIRST priority per doc-14:182).
        proof = self._commit_proof_provider.get_commit_proof(
            repo_id=query.repo_id, commit_hash=commit_hash
        )
        if proof is not None:
            return _ResolutionSource(
                source_kind="typed_proof",
                task_ids=list(proof.task_ids),
                provenance_payload_ref=proof.precommit_provenance_ref,
            )

        # Step 3b: payload at the natural ref (SECOND priority per doc-14:183).
        # We attempt notes lookup first (notes are keyed by commit hash so
        # don't require the precommit_provenance_digest).
        payload_via_notes = self._payload_store.get_payload_from_notes(
            repo_id=query.repo_id, commit_hash=commit_hash
        )
        if payload_via_notes is not None:
            return _ResolutionSource(
                source_kind="payload",
                task_ids=list(payload_via_notes.task_ids),
                provenance_payload_ref=payload_via_notes.precommit_provenance_ref,
            )

        # Step 3c: lineage walk (doc-14:212-213).
        lineage = self._lineage_walker.walk_from_old(
            repo_id=query.repo_id, old_commit_hash=commit_hash
        )
        if lineage is not None:
            # Step 3d: re-consult typed proof for the rewritten commit.
            rewritten_proof = self._commit_proof_provider.get_commit_proof(
                repo_id=query.repo_id, commit_hash=lineage.new_commit_hash
            )
            if rewritten_proof is not None:
                return _ResolutionSource(
                    source_kind="lineage_walked",
                    task_ids=list(rewritten_proof.task_ids),
                    provenance_payload_ref=rewritten_proof.precommit_provenance_ref,
                )

        # Step 3e: trailer-only (THIRD priority per doc-14:184; NEVER full
        # proof alone per doc-14:188).
        trailer = self._trailer_source.get_trailer(
            repo_id=query.repo_id, commit_hash=commit_hash
        )
        if trailer is not None:
            gap_reasons.append(f"missing_typed_proof_and_payload_for:{commit_hash}")
            return _ResolutionSource(
                source_kind="trailer_only",
                # Trailers carry ONLY the task ids DIGEST per doc-14:137
                # compact rule; we cannot enumerate the full task ids
                # without typed proof / payload, so task_ids is None.
                task_ids=None,
                provenance_payload_ref=trailer.precommit_provenance_ref,
            )

        # Step 3f: no evidence at all -> conflict per doc-14:212-213
        # ("reject ambiguous line provenance unless lineage is recorded").
        gap_reasons.append(f"unresolved_blame_commit:{commit_hash}")
        return _ResolutionSource(
            source_kind="conflict",
            task_ids=None,
            provenance_payload_ref=None,
        )

    def _compute_completeness(
        self,
        resolutions: list[tuple[str, "_ResolutionSource"]],
        gap_reasons: list[str],
    ) -> Literal["complete", "paged", "preview_only", "unavailable"]:
        """Compute the result-level completeness state (doc-14:202-205).

        - ``complete`` -- every blamed commit resolved via typed proof or
          payload (the two exact-evidence sources per doc-14:182-184).
        - ``preview_only`` -- at least one commit resolved via trailer-only
          (per doc-14:188 + 198-199 partial-evidence rule).
        - ``unavailable`` -- at least one commit resolved as conflict
          (per doc-14:212-213 reject-ambiguous rule).

        The strongest state per the Slice 13A invariant doc-13a:18-23 is
        the only one consumers may treat as authoritative; lower states
        are bounded partial / preview-only / fail-closed per the doc-14
        contracts.
        """

        if any(src.source_kind == "conflict" for _, src in resolutions):
            return "unavailable"
        if any(src.source_kind == "trailer_only" for _, src in resolutions):
            return "preview_only"
        # All sources are typed_proof, payload, or lineage_walked -- those
        # are all exact per the Slice 13A invariant.
        return "complete"

    def _compute_confidence(
        self,
        resolutions: list[tuple[str, "_ResolutionSource"]],
    ) -> float:
        """Compute the result-level confidence score (doc-14:131).

        Per the Slice 13 confidence-scoring contract (doc-13:173-175) the
        score reflects evidence quality:

        - typed_proof = 1.0 (strongest; doc-14:182 FIRST priority)
        - payload = 0.8 (SECOND priority per doc-14:183)
        - lineage_walked = 0.6 (rewritten commit reconciled; doc-14:212)
        - trailer_only = 0.4 (preview only per doc-14:188)
        - conflict = 0.0 (no evidence)

        The result-level confidence is the MIN across all per-commit
        confidences (the chain is only as strong as its weakest link).
        """

        if not resolutions:
            return 0.0

        per_commit_confidence = {
            "typed_proof": 1.0,
            "payload": 0.8,
            "lineage_walked": 0.6,
            "trailer_only": 0.4,
            "conflict": 0.0,
        }
        return min(
            per_commit_confidence[src.source_kind] for _, src in resolutions
        )

    def _make_paged_result(
        self,
        query: LineProvenanceQuery,
        invocations: list[GitSubprocessResult],
    ) -> LineProvenanceReadResult:
        """Return a ``completeness="paged"`` result with exact page refs.

        Per doc-14:199-201 a query that exceeds inline caps returns
        ``completeness="paged"`` with exact :class:`GovernanceEvidencePageRef`
        rows for the page-able sub-ranges. The page refs are stable
        cross-process per the Slice 13a paged-evidence contract.
        """

        # Build one exact page ref per :class:`max_lines`-sized window.
        page_refs: list[GovernanceEvidencePageRef] = []
        cursor = query.line_start
        page_num = 0
        while cursor <= query.line_end:
            window_end = min(cursor + query.max_lines - 1, query.line_end)
            page_num += 1
            page_payload = {
                "repo_id": query.repo_id,
                "ref": query.ref,
                "path": query.path,
                "line_start": cursor,
                "line_end": window_end,
            }
            digest = _sha256_hex(_canonical_json(page_payload))
            page_refs.append(
                GovernanceEvidencePageRef(
                    page_ref_id=(
                        f"line_provenance_page:{query.repo_id}:{query.ref}:"
                        f"{query.path}:{cursor}-{window_end}"
                    ),
                    authority="git_provenance",
                    source_ref_id=f"{query.ref}:{query.path}",
                    line_start=cursor,
                    line_end=window_end,
                    digest=digest,
                    completeness="paged",
                    exact=True,
                )
            )
            cursor = window_end + 1

        result = LineProvenanceResult(
            commit_hashes=[],
            task_ids=[],
            provenance_payload_refs=[],
            page_refs=page_refs,
            completeness="paged",
            completeness_digest="placeholder",
            confidence=1.0,  # paged exact is authoritative
            gaps=[
                f"line_range_exceeds_max_lines:{query.line_end - query.line_start + 1}>"
                f"{query.max_lines}"
            ],
        )
        result = result.model_copy(
            update={
                "completeness_digest": compute_line_provenance_completeness_digest(
                    result
                )
            }
        )
        return LineProvenanceReadResult(
            result=result, gap_finding=None, git_invocations=invocations
        )

    def _make_unavailable_result(
        self,
        query: LineProvenanceQuery,
        invocations: list[GitSubprocessResult],
        *,
        reason: str,
        failure_id: Literal[
            "line_provenance_gap", "governance_evidence_conflict"
        ],
    ) -> LineProvenanceReadResult:
        """Return a ``completeness="unavailable"`` result + a typed
        gap finding.

        Per doc-14:188 + doc-14:198-199 an unresolvable query records a
        typed :class:`CommitProvenanceGapFinding` and returns gracefully
        (NEVER blocks the executor per doc-14:242-243).
        """

        result = LineProvenanceResult(
            commit_hashes=[],
            task_ids=[],
            provenance_payload_refs=[],
            page_refs=[],
            completeness="unavailable",
            completeness_digest="placeholder",
            confidence=0.0,
            gaps=[reason],
        )
        result = result.model_copy(
            update={
                "completeness_digest": compute_line_provenance_completeness_digest(
                    result
                )
            }
        )
        gap_finding = self._make_gap_finding(
            query=query,
            failure_id=failure_id,
            reason=reason,
            evidence_payload={},
        )
        return LineProvenanceReadResult(
            result=result,
            gap_finding=gap_finding,
            git_invocations=invocations,
        )

    def _make_gap_finding(
        self,
        *,
        query: LineProvenanceQuery,
        failure_id: Literal[
            "line_provenance_gap", "governance_evidence_conflict"
        ],
        reason: str,
        evidence_payload: dict[str, Any],
    ) -> CommitProvenanceGapFinding:
        """Build a typed :class:`CommitProvenanceGapFinding` from a query.

        Per doc-14:192-201 + doc-14:242-243 the finding REUSES the Slice 14
        2nd sub-slice typed failure ids
        (``line_provenance_gap`` + ``governance_evidence_conflict``) with
        NON-blocking ``retry_governance_projection`` routing.

        The reader does not have a single commit_hash or precommit_provenance_ref
        for a multi-commit read; the finding's ``commit_hash`` field is
        populated with the query ref + the path as a structural identifier;
        the ``precommit_provenance_ref`` and ``precommit_provenance_digest``
        carry the line-range descriptor as the read-side correlator (matching
        the writer-side gap finding's correlator semantics).
        """

        # Per the doc-14:192-201 governance-finding contract carry the
        # repo_id + query target as the structural identifiers; the
        # evidence_payload carries the variable per-call data.
        structural_correlator = (
            f"line_provenance_read:{query.repo_id}:{query.ref}:"
            f"{query.path}:{query.line_start}-{query.line_end}"
        )
        return CommitProvenanceGapFinding(
            failure_id=failure_id,
            feature_id=evidence_payload.get("feature_id", ""),
            group_idx=evidence_payload.get("group_idx", 0),
            repo_id=query.repo_id,
            # Read-side: there is no single result commit hash; use a
            # structural correlator built from the query target.
            commit_hash=structural_correlator,
            precommit_provenance_ref=structural_correlator,
            precommit_provenance_digest=_sha256_hex(structural_correlator),
            reason=reason,
            evidence_payload=evidence_payload,
        )


# Internal: per-commit resolution descriptor.
class _ResolutionSource(BaseModel):
    """Internal typed wrapper for one commit's resolved evidence source.

    NOT exported via ``__all__`` (internal control-flow shape only).
    """

    model_config = ConfigDict(extra="forbid")

    source_kind: Literal[
        "typed_proof", "payload", "lineage_walked", "trailer_only", "conflict"
    ]
    task_ids: list[str] | None
    provenance_payload_ref: str | None
