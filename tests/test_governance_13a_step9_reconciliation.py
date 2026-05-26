"""Slice 13A 8th sub-slice 13An-1 -- step 9 dependency reconciliation tests.

Per **doc-13a:285-287 § Refactoring Steps step 9** --
*"Update governance Slices 13-20 and context Slice 21 to depend on this
shared completeness model instead of redefining authority semantics
locally."* -- this test file PINS the per-doc references appended to the
9 plan docs by the Slice 13A 8th sub-slice 13An-1 (this iteration).

The references are APPEND-only (no rewrites of accepted Slice 13
content; Slice 13 is the only ACCEPTED governance doc -- Slices 14-20
are PENDING per
``docs/execution-control-plane/IMPLEMENTATION_PROMPT_GOVERNANCE.md``
§ "Remaining"; Slice 21 is the context-layer plan doc whose acceptance
pre-dates the governance phase).

Per the auto-memory
``feedback_verify_changes`` + ``feedback_no_silent_degradation`` rules:

- Every assertion is real; no soft assertions, no skipped tests.
- A missing reference causes the test to fail closed.

Per the auto-memory ``feedback_cite_everything`` rule: every claim in
each per-doc reference must cite ``doc-13a:285-287`` so the dependency
chain is auditable.

Per the user-prompt **non-negotiables** + the **P3-13A-6-3** binding
statement at ``docs/execution-control-plane/13a-acceptance.md:193-227``,
the references pin the dead-until-wired status of the composite
``LegacyGateConsumerSnapshotAdapter`` chain -- the binding closure is
the **Slice 13A 8th sub-slice 13An-2** deliverable, NOT this iteration.

Author: Slice 13A 8th sub-slice 13An-1 (implementer).
"""

from __future__ import annotations

import pathlib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs" / "execution-control-plane"

ACCEPTANCE_ARTIFACT_PATH = DOCS_DIR / "13a-acceptance.md"
AUTHORITY_DOC_PATH = DOCS_DIR / "13a-lossless-context-and-evidence-completeness.md"

# The 9 plan docs touched by step 9: governance Slices 13-20 + context
# Slice 21.
TOUCHED_DOCS: dict[str, pathlib.Path] = {
    "13": DOCS_DIR / "13-governance-evidence-model.md",
    "14": DOCS_DIR / "14-commit-and-line-provenance.md",
    "15": DOCS_DIR / "15-governance-metrics-and-scoring.md",
    "16": DOCS_DIR / "16-finding-engine-and-taxonomy.md",
    "17": DOCS_DIR / "17-policy-recommendation-interface.md",
    "18": DOCS_DIR / "18-counterfactual-replay-and-simulation.md",
    "19": DOCS_DIR / "19-governance-agent-and-reporting.md",
    "20": DOCS_DIR / "20-governance-acceptance-and-adoption.md",
    "21": DOCS_DIR / "21-iriai-context-layer.md",
}


# ---------------------------------------------------------------------------
# (a) File existence + section presence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slice_id,doc_path", TOUCHED_DOCS.items())
def test_touched_doc_exists(slice_id: str, doc_path: pathlib.Path) -> None:
    """Every doc named by doc-13a:285-287 must exist before/after step 9."""
    assert doc_path.is_file(), (
        f"Slice {slice_id} plan doc missing at {doc_path}; required by "
        f"doc-13a:285-287 step 9 dependency reconciliation."
    )


_SHARED_COMPLETENESS_SECTION_HEADER = (
    "## Slice 13A Shared Completeness Model Dependency"
)


@pytest.mark.parametrize("slice_id,doc_path", TOUCHED_DOCS.items())
def test_touched_doc_has_shared_completeness_section(
    slice_id: str, doc_path: pathlib.Path
) -> None:
    """Every touched doc must carry the canonical step-9 section header.

    Per doc-13a:285-287, each of the 9 plan docs must depend on the
    shared completeness model rather than redefining authority semantics
    locally. The dependency reference is recorded as a sub-section with
    the canonical header (uniform across all 9 docs) so future
    maintenance passes can find it deterministically.
    """
    text = doc_path.read_text(encoding="utf-8")
    assert _SHARED_COMPLETENESS_SECTION_HEADER in text, (
        f"Slice {slice_id} doc {doc_path.name} is missing the canonical "
        f"section header {_SHARED_COMPLETENESS_SECTION_HEADER!r}. Per "
        f"doc-13a:285-287 step 9, every governance Slice 13-20 plan doc "
        f"and the context Slice 21 plan doc MUST depend on the shared "
        f"completeness model instead of redefining authority semantics "
        f"locally. The section was added by Slice 13A 8th sub-slice "
        f"13An-1; reintroducing the doc without it is a step-9 regression."
    )


# ---------------------------------------------------------------------------
# (b) Each reference must cite doc-13a:285-287
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slice_id,doc_path", TOUCHED_DOCS.items())
def test_touched_doc_cites_doc_13a_step_9(
    slice_id: str, doc_path: pathlib.Path
) -> None:
    """Per ``feedback_cite_everything``: every shared-completeness
    reference must cite ``doc-13a:285-287`` (the step-9 line range).
    """
    text = doc_path.read_text(encoding="utf-8")
    assert "doc-13a:285-287" in text, (
        f"Slice {slice_id} doc {doc_path.name} shared-completeness "
        f"reference does not cite ``doc-13a:285-287``. Per the "
        f"auto-memory feedback_cite_everything rule, every claim must "
        f"be backed by its authority citation."
    )


# ---------------------------------------------------------------------------
# (c) Each reference must name the shared module
# ---------------------------------------------------------------------------


_SHARED_COMPLETENESS_MODULE = (
    "src/iriai_build_v2/execution_control/completeness.py"
)


@pytest.mark.parametrize("slice_id,doc_path", TOUCHED_DOCS.items())
def test_touched_doc_names_shared_completeness_module(
    slice_id: str, doc_path: pathlib.Path
) -> None:
    """Each reference must name the source-of-truth module.

    The shared completeness model lives at
    ``src/iriai_build_v2/execution_control/completeness.py`` (Slice
    13A 2nd sub-slice; 7 ``__all__`` surfaces). The reference must
    name the module explicitly so consumers can find it without
    indirection.
    """
    text = doc_path.read_text(encoding="utf-8")
    assert _SHARED_COMPLETENESS_MODULE in text, (
        f"Slice {slice_id} doc {doc_path.name} shared-completeness "
        f"reference does not name the source-of-truth module "
        f"{_SHARED_COMPLETENESS_MODULE!r}. Per doc-13a:285-287 step 9, "
        f"the reference must name the shared module explicitly."
    )


_REQUIRED_TYPED_SHAPES: tuple[str, ...] = (
    "CompletenessState",
    "EvidenceCompleteness",
    "AuthoritativeContextRef",
    "EvidencePageRef",
    "ExactEvidenceManifest",
)


@pytest.mark.parametrize("slice_id,doc_path", TOUCHED_DOCS.items())
@pytest.mark.parametrize("typed_shape", _REQUIRED_TYPED_SHAPES)
def test_touched_doc_names_required_typed_shape(
    slice_id: str, doc_path: pathlib.Path, typed_shape: str
) -> None:
    """Each reference must name every required shared typed shape.

    The 5 typed shapes pinned by the user-prompt non-negotiables
    (``EvidenceCompleteness``, ``AuthoritativeContextRef``,
    ``EvidencePageRef``, ``ExactEvidenceManifest``, plus
    ``CompletenessState`` -- the underlying Literal alias) are the
    Slice 13A 2nd sub-slice's source-of-truth shapes. Each reference
    must enumerate them so consumers know which names are part of the
    shared model.
    """
    text = doc_path.read_text(encoding="utf-8")
    assert typed_shape in text, (
        f"Slice {slice_id} doc {doc_path.name} shared-completeness "
        f"reference does not name typed shape {typed_shape!r}. Per "
        f"doc-13a:285-287 step 9 + the user-prompt non-negotiables, "
        f"every reference must enumerate the 5 shared typed shapes."
    )


# ---------------------------------------------------------------------------
# (d) Each reference must name the per-purpose adapter modules
# ---------------------------------------------------------------------------


_ADAPTER_MODULES: tuple[str, ...] = (
    "gate_companion",
    "snapshot_companion",
    "dispatcher_prompt_context",
)


@pytest.mark.parametrize("slice_id,doc_path", TOUCHED_DOCS.items())
@pytest.mark.parametrize("adapter_module", _ADAPTER_MODULES)
def test_touched_doc_names_adapter_module(
    slice_id: str, doc_path: pathlib.Path, adapter_module: str
) -> None:
    """Each reference must name the 3 per-purpose adapter modules.

    The Slice 13A 4th-6th sub-slices' adapter modules
    (``dispatcher_prompt_context``, ``gate_companion``,
    ``snapshot_companion``) are the production compatibility paths
    that consumers wire through. Each step-9 reference must enumerate
    them so consumers know where to wire.
    """
    text = doc_path.read_text(encoding="utf-8")
    assert adapter_module in text, (
        f"Slice {slice_id} doc {doc_path.name} shared-completeness "
        f"reference does not name adapter module {adapter_module!r}. "
        f"Per doc-13a:285-287 step 9 + the user-prompt non-negotiables, "
        f"every reference must enumerate the 3 per-purpose adapter "
        f"modules."
    )


# ---------------------------------------------------------------------------
# (e) Each reference must preserve the P3-13A-6-3 binding statement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slice_id,doc_path", TOUCHED_DOCS.items())
def test_touched_doc_preserves_p3_13a_6_3_binding(
    slice_id: str, doc_path: pathlib.Path
) -> None:
    """Per the user-prompt non-negotiables, the P3-13A-6-3 binding
    statement must be preserved verbatim through this sub-slice.

    The binding closure is the **Slice 13A 8th sub-slice 13An-2**
    deliverable, NOT this iteration. The references in each touched
    doc MUST name P3-13A-6-3 so future readers know the wiring is
    still pending.
    """
    text = doc_path.read_text(encoding="utf-8")
    assert "P3-13A-6-3" in text, (
        f"Slice {slice_id} doc {doc_path.name} shared-completeness "
        f"reference does not name P3-13A-6-3. Per the user-prompt "
        f"non-negotiables, the dead-until-wired binding statement "
        f"MUST be preserved verbatim through this sub-slice; the "
        f"binding closure is deferred to 13An-2."
    )
    assert "13An-2" in text, (
        f"Slice {slice_id} doc {doc_path.name} shared-completeness "
        f"reference does not name 13An-2 as the binding-closure "
        f"deliverable. Per doc-13a:285-287 + the P2-13A-7-1 mitigation "
        f"in 13a-acceptance.md:285-306, the SPLIT decision names 13An-2 "
        f"as the wiring closure iteration."
    )


# ---------------------------------------------------------------------------
# (f) Each reference must name the implementing sub-slice (13An-1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slice_id,doc_path", TOUCHED_DOCS.items())
def test_touched_doc_names_implementing_subslice(
    slice_id: str, doc_path: pathlib.Path
) -> None:
    """Each reference must name the implementing sub-slice (13An-1).

    Per the per-sub-slice journal-anchor discipline + the auto-memory
    ``feedback_cite_everything`` rule, every appended doc section must
    record the sub-slice that added it so reviewers can correlate the
    edit with the journal entry.
    """
    text = doc_path.read_text(encoding="utf-8")
    assert "13An-1" in text, (
        f"Slice {slice_id} doc {doc_path.name} shared-completeness "
        f"reference does not name 13An-1 as the implementing sub-slice. "
        f"Per the per-sub-slice journal anchor discipline, every "
        f"appended doc section must record the implementing sub-slice."
    )


# ---------------------------------------------------------------------------
# (g) APPEND-only discipline: the appended section comes at/near the end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slice_id,doc_path", TOUCHED_DOCS.items())
def test_appended_section_is_near_doc_end(
    slice_id: str, doc_path: pathlib.Path
) -> None:
    """APPEND-only: the new section is in the LAST 30% of the doc.

    Per the user-prompt non-negotiables + doc-13a:42-46 + 124-126 +
    feedback_no_refactor, the step-9 reference is APPEND-only -- it
    does not rewrite any earlier section of the doc. A structural
    assertion that the canonical section header appears in the last
    30% of the doc catches the obvious regression where someone
    inlines the reference into the middle of an accepted section.
    """
    text = doc_path.read_text(encoding="utf-8")
    section_idx = text.find(_SHARED_COMPLETENESS_SECTION_HEADER)
    assert section_idx >= 0, (
        f"Slice {slice_id} doc {doc_path.name} missing the canonical "
        f"step-9 section header (handled by the dedicated test above)."
    )
    total_len = len(text)
    # The section header MUST live in the last 30% of the doc per
    # APPEND-only discipline. Slice 13 (the largest at 322 lines after
    # append) needs the cutoff loose enough to be insensitive to small
    # per-doc text-length variation, but tight enough to catch a true
    # mid-doc insertion.
    cutoff = int(total_len * 0.70)
    assert section_idx >= cutoff, (
        f"Slice {slice_id} doc {doc_path.name} step-9 section header is "
        f"at offset {section_idx} / total {total_len} (< {cutoff}). The "
        f"section MUST be APPENDED at the end per the user-prompt "
        f"non-negotiables; mid-doc insertion would be a rewrite of "
        f"accepted content."
    )


# ---------------------------------------------------------------------------
# (h) Slice 21 ContextCompleteness alias structural identity
# ---------------------------------------------------------------------------


def test_slice21_context_completeness_matches_shared() -> None:
    """Slice 21's locally-defined ``ContextCompleteness`` alias must be
    structurally identical to the shared ``CompletenessState`` alias.

    Slice 21 historically defines
    ``ContextCompleteness = Literal["complete", "paged", "preview_only",
    "unavailable"]`` at line 92 of
    ``docs/execution-control-plane/21-iriai-context-layer.md``. The
    shared ``CompletenessState`` enum (Slice 13A 2nd sub-slice;
    ``src/iriai_build_v2/execution_control/completeness.py``) defines
    the same 4 values. The two aliases must remain in lock-step; if
    the shared enum gains/loses values, ``ContextCompleteness`` MUST
    track that change.

    This test PINS the structural equality by importing the shared
    enum at runtime and comparing its set of values against the
    literal string asserted in the doc.
    """
    from iriai_build_v2.execution_control.completeness import (  # noqa: WPS433
        CompletenessState,
    )

    # Pull the 4 string values out of the runtime Literal alias.
    # Pydantic models that consume CompletenessState bound this to
    # ``typing.get_args``; we use the same well-known recipe here.
    import typing as _typing
    shared_values = set(_typing.get_args(CompletenessState))

    expected = {"complete", "paged", "preview_only", "unavailable"}
    assert shared_values == expected, (
        f"Shared CompletenessState values drifted from the Slice 21 "
        f"ContextCompleteness literal at "
        f"21-iriai-context-layer.md:92. Shared values: {shared_values}; "
        f"Slice 21 literal expects: {expected}. Per doc-13a:285-287 "
        f"step 9, the two aliases MUST remain in lock-step."
    )

    # The doc must still declare the same literal -- this catches a
    # silent re-write of the Slice 21 alias to drift from the shared
    # values.
    text = TOUCHED_DOCS["21"].read_text(encoding="utf-8")
    expected_literal = (
        'ContextCompleteness = Literal["complete", "paged", '
        '"preview_only", "unavailable"]'
    )
    assert expected_literal in text, (
        f"Slice 21 doc no longer declares the literal "
        f"{expected_literal!r}. The Slice 13A 8th sub-slice 13An-1 "
        f"step-9 reconciliation MUST NOT rewrite the literal; if a "
        f"future maintenance pass collapses the duplicate alias, "
        f"this test must be updated to assert the import path "
        f"instead."
    )


# ---------------------------------------------------------------------------
# (i) Existing 13A acceptance artifact baselines are preserved
# ---------------------------------------------------------------------------


def test_acceptance_artifact_step_9_satisfied_after_13an_split() -> None:
    """The acceptance artifact's per-step status table MUST pin step 9
    as SATISFIED after the 13An SPLIT closes.

    13An-1 (FIRST iteration of the SPLIT) delivered the plan-doc
    references (this test module + the 9 uniform per-doc sub-sections);
    13An-2 (SECOND iteration) landed the P3-13A-6-3 binding closure via
    the production-callsite swap at dashboard.py:1568; 13An-3 (THIRD
    iteration; slice-end finalizer) updates the acceptance artifact's
    per-step status table to SATISFIED per the SPLIT.
    """
    text = ACCEPTANCE_ARTIFACT_PATH.read_text(encoding="utf-8")
    rows = [
        line for line in text.splitlines()
        if line.startswith("| 9 |")
    ]
    assert rows, "Acceptance artifact missing table row for doc-13a step 9."
    row_text = " ".join(rows)
    assert "SATISFIED" in row_text, (
        "Acceptance artifact step-9 row must claim SATISFIED after the "
        "13An SPLIT closes (13An-1 + 13An-2 + 13An-3 slice-end finalizer "
        "per doc-13a:285-287 step 9)."
    )
    assert "13An-1" in row_text, (
        "Step 9 row must reference 13An-1 (step 9 reconciliation)."
    )


def test_p3_13a_6_3_binding_statement_unchanged() -> None:
    """The P3-13A-6-3 dead-until-wired binding statement must be
    preserved verbatim through 13An-1.

    Per the user-prompt non-negotiables, the binding closure is the
    13An-2 deliverable. 13An-1 only adds plan-doc references; it does
    NOT close the binding statement.
    """
    text = ACCEPTANCE_ARTIFACT_PATH.read_text(encoding="utf-8")
    assert "Dead-until-wired binding statement (P3-13A-6-3)" in text, (
        "Acceptance artifact's P3-13A-6-3 binding statement header is "
        "missing. The binding statement MUST be preserved verbatim "
        "through 13An-1; binding closure is the 13An-2 deliverable."
    )
    # The binding statement's key sentence must still claim
    # dead-until-wired (not "WIRED" or "CLOSED").
    assert "dead-until-wired" in text, (
        "Acceptance artifact's P3-13A-6-3 binding statement no longer "
        "claims 'dead-until-wired'. Per the user-prompt non-negotiables, "
        "the binding closure is deferred to 13An-2."
    )


# ---------------------------------------------------------------------------
# (j) Cardinality: exactly 9 docs were touched (no more, no less)
# ---------------------------------------------------------------------------


def test_exactly_nine_docs_touched() -> None:
    """Doc-13a:285-287 names *governance Slices 13-20 and context Slice
    21* -- exactly 9 docs. The TOUCHED_DOCS mapping above must cover
    exactly that set.

    This assertion catches the regression where a future maintenance
    pass mistakenly adds a doc to the step-9 reconciliation scope or
    silently drops one.
    """
    expected_slice_ids = {"13", "14", "15", "16", "17", "18", "19", "20", "21"}
    actual_slice_ids = set(TOUCHED_DOCS.keys())
    assert actual_slice_ids == expected_slice_ids, (
        f"TOUCHED_DOCS cardinality drift: expected {expected_slice_ids}, "
        f"got {actual_slice_ids}. Per doc-13a:285-287, step 9 covers "
        f"exactly governance Slices 13-20 + context Slice 21 = 9 docs."
    )


# ---------------------------------------------------------------------------
# (k) SPLIT pending-iteration discipline pinned in the acceptance artifact
# ---------------------------------------------------------------------------


def test_acceptance_artifact_split_authorization_present() -> None:
    """The acceptance artifact's SPLIT authorization (added by the
    7th-sub-slice finalizer per P2-13A-7-1 mitigation) must still
    name the 13An-1 / 13An-2 / 13An-3 split.

    13An-1 is the FIRST iteration of the SPLIT; 13An-2 (wiring) and
    13An-3 (slice-end review + finalizer) are the remaining
    iterations. The artifact's SPLIT-authorization paragraph must
    name all three so future iterations know what's still pending.
    """
    text = ACCEPTANCE_ARTIFACT_PATH.read_text(encoding="utf-8")
    for tag in ("13An-1", "13An-2", "13An-3"):
        assert tag in text, (
            f"Acceptance artifact's SPLIT authorization paragraph no "
            f"longer names {tag!r}. Per the P2-13A-7-1 mitigation "
            f"recorded at 13a-acceptance.md:285-306, all three SPLIT "
            f"iterations must be named so future iterations know what's "
            f"pending."
        )
