"""Slice 13A seventh sub-slice -- targeted tests for the 13A acceptance artifact.

Covers the Slice 13A seventh sub-slice documentation-only deliverable
per STATUS.md § "Next safe action" + doc-13a:283-285 (step 8):

- The acceptance artifact file exists at the documented path.
- The README index links to the artifact.
- The artifact's section headers match the documented contents
  (doc-13a § Refactoring Steps coverage, invariants, per-sub-slice
  module ``__all__`` projections, typed failure ids, carried-P3 ledger,
  dead-until-wired binding statement).

These tests are deliberately path-existence + content-shape assertions
rather than executable-behavior assertions: the deliverable is
documentation per doc-13a:283-285, so a documentation-shape test is
the right granularity. Per the auto-memory ``feedback_verify_changes``
+ ``feedback_no_silent_degradation`` rules, every assertion is real;
none are skipped.

The README sub-entry is also asserted to point to the artifact (the
APPEND-ONLY index discipline doc-13a:283-285 requires).

Author: Slice 13A 7th sub-slice (implementer).
"""

from __future__ import annotations

import pathlib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACT_PATH = (
    REPO_ROOT / "docs" / "execution-control-plane" / "13a-acceptance.md"
)
README_PATH = (
    REPO_ROOT / "docs" / "execution-control-plane" / "README.md"
)
AUTHORITY_DOC_PATH = (
    REPO_ROOT
    / "docs"
    / "execution-control-plane"
    / "13a-lossless-context-and-evidence-completeness.md"
)
JOURNAL_PATH = (
    REPO_ROOT / "docs" / "execution-control-plane" / "implementation-journal.md"
)


# ---------------------------------------------------------------------------
# (a) File existence + non-empty content
# ---------------------------------------------------------------------------


def test_acceptance_artifact_file_exists() -> None:
    """The 13A acceptance artifact must exist at the documented path."""
    assert ARTIFACT_PATH.is_file(), (
        f"Slice 13A acceptance artifact missing at {ARTIFACT_PATH}; "
        f"required by doc-13a:283-285 (step 8)."
    )


def test_acceptance_artifact_is_non_empty() -> None:
    """The artifact must contain real content (>2KB) not an empty stub."""
    size = ARTIFACT_PATH.stat().st_size
    assert size > 2048, (
        f"Slice 13A acceptance artifact at {ARTIFACT_PATH} is too small "
        f"({size} bytes); expected >2KB of pinned documentation."
    )


def test_readme_index_file_exists() -> None:
    """The execution-control-plane README index must exist."""
    assert README_PATH.is_file(), (
        f"README index missing at {README_PATH}; required by "
        f"doc-13a:283-285 (step 8)."
    )


def test_authority_doc_exists() -> None:
    """The doc-13a authority doc must remain present (sanity)."""
    assert AUTHORITY_DOC_PATH.is_file(), (
        f"Doc-13a authority missing at {AUTHORITY_DOC_PATH}; the "
        f"acceptance artifact references it."
    )


def test_journal_tail_present() -> None:
    """The implementation journal must be present (artifact references it)."""
    assert JOURNAL_PATH.is_file(), (
        f"Implementation journal missing at {JOURNAL_PATH}; the "
        f"acceptance artifact references its tail."
    )


# ---------------------------------------------------------------------------
# (b) README index entry linking to the artifact
# ---------------------------------------------------------------------------


def test_readme_links_to_acceptance_artifact() -> None:
    """The README must link to the 13A acceptance artifact (append-only).

    Per doc-13a:283-285 the README index entry MUST link to the
    acceptance artifact; the link is asserted explicitly to catch a
    silent-drop regression.
    """
    text = README_PATH.read_text(encoding="utf-8")
    assert "13a-acceptance.md" in text, (
        "README does not link to 13a-acceptance.md; required by "
        "doc-13a:283-285 (step 8)."
    )


def test_readme_links_under_13a_authority_entry() -> None:
    """The README sub-entry must appear adjacent to the 13A authority entry.

    This is a structural assertion: the new acceptance-artifact pointer
    must live as a sub-entry under (or immediately after) the existing
    13A authority-doc entry so readers find both pieces together.
    """
    text = README_PATH.read_text(encoding="utf-8")
    auth_idx = text.find("13a-lossless-context-and-evidence-completeness.md")
    art_idx = text.find("13a-acceptance.md")
    assert auth_idx != -1, "README must reference the 13A authority doc."
    assert art_idx != -1, "README must reference the 13A acceptance artifact."
    assert art_idx > auth_idx, (
        "README acceptance-artifact link must appear AFTER the 13A "
        "authority-doc entry (append-only sub-entry discipline)."
    )
    # The two must live close together (within ~2KB of text) -- i.e.
    # the artifact pointer is a sub-bullet of the authority entry, not
    # an orphan reference elsewhere in the README.
    distance = art_idx - auth_idx
    assert distance < 2048, (
        f"README acceptance-artifact pointer is {distance} bytes from "
        f"the 13A authority entry; expected <2048 (sub-entry layout)."
    )


# ---------------------------------------------------------------------------
# (c) Artifact section headers / canonical content shape
# ---------------------------------------------------------------------------


# Required headers per doc-13a:283-285 + the user-prompt test contract.
_REQUIRED_HEADERS: tuple[str, ...] = (
    "# 13A. Lossless Context And Evidence Completeness",
    "## Doc-13a § Refactoring Steps",
    "## Invariants pinned by Slice 13A",
    "## Per-sub-slice module `__all__` projections",
    "## Typed failure ids registered by Slice 13A",
    "## Carried-P3 ledger",
    "## Dead-until-wired binding statement",
)


@pytest.mark.parametrize("header", _REQUIRED_HEADERS)
def test_acceptance_artifact_contains_required_header(header: str) -> None:
    """The artifact must contain every doc-13a-required header.

    Per doc-13a:283-285 the acceptance artifact pins (a) the §
    Refactoring Steps coverage, (b) invariants, (c) per-sub-slice
    module __all__ projections, (d) typed failure ids, (e) carried-P3
    ledger, (f) dead-until-wired binding statement. Each header is
    asserted explicitly.
    """
    text = ARTIFACT_PATH.read_text(encoding="utf-8")
    assert header in text, (
        f"Slice 13A acceptance artifact missing required section header: "
        f"{header!r}. Per doc-13a:283-285 the artifact must pin this "
        f"section explicitly."
    )


# ---------------------------------------------------------------------------
# (d) Refactoring steps 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 SATISFIED claim
# ---------------------------------------------------------------------------


_SATISFIED_STEPS: tuple[int, ...] = (2, 3, 4, 5, 6, 7, 8, 9)


@pytest.mark.parametrize("step", _SATISFIED_STEPS)
def test_acceptance_artifact_pins_step_satisfied(step: int) -> None:
    """Each of steps 2-9 must be pinned SATISFIED in the artifact.

    Step 1 is the foundational doc-13a itself; step 9 was previously
    DEFERRED to the LAST sub-slice 13An and is now SATISFIED by the
    13An SPLIT (13An-1 + 13An-2 + 13An-3 slice-end finalizer per the
    SPLIT). The artifact must explicitly state each of steps 2-9
    SATISFIED with its implementing sub-slice.
    """
    text = ARTIFACT_PATH.read_text(encoding="utf-8")
    # Look for the table row "| <step> | doc-13a:NN-MM ... | **SATISFIED** | ..."
    needle = f"| {step} |"
    assert needle in text, (
        f"Acceptance artifact missing table row for doc-13a step {step}."
    )
    # The same row must claim SATISFIED -- enforce the satisfaction marker.
    # We split into table rows and assert the row that starts with this step
    # carries the SATISFIED marker.
    rows = [
        line for line in text.splitlines()
        if line.startswith(f"| {step} |")
    ]
    assert rows, (
        f"Could not find table row starting with '| {step} |' in artifact."
    )
    assert any("SATISFIED" in row for row in rows), (
        f"Step {step} row does not claim SATISFIED. Per doc-13a:283-285 "
        f"every step 2-8 must be pinned SATISFIED with its implementing "
        f"sub-slice."
    )


def test_step_9_satisfied_by_13an_split() -> None:
    """Step 9 must be pinned SATISFIED by the 13An SPLIT (13An-1
    appended uniform Slice 13A Shared Completeness Model Dependency
    sub-section to 9 plan docs; 13An-2 finalizer landed the P3-13A-6-3
    binding closure via production-callsite swap at dashboard.py:1568;
    13An-3 slice-end finalizer updates the per-step status table per
    the SPLIT)."""
    text = ARTIFACT_PATH.read_text(encoding="utf-8")
    # Find the table row for step 9.
    rows = [
        line for line in text.splitlines()
        if line.startswith("| 9 |")
    ]
    assert rows, "Acceptance artifact missing table row for doc-13a step 9."
    row_text = " ".join(rows)
    assert "SATISFIED" in row_text, (
        "Step 9 row must claim SATISFIED (delivered by the 13An SPLIT: "
        "13An-1 step 9 reconciliation + 13An-2 P3-13A-6-3 binding "
        "closure wiring + 13An-3 slice-end SIX-VECTOR review per "
        "doc-13a:285-287)."
    )
    assert "13An" in row_text, (
        "Step 9 row must reference the LAST sub-slice 13An SPLIT."
    )


# ---------------------------------------------------------------------------
# (e) Typed failure ids registered by Slice 13A
# ---------------------------------------------------------------------------


_REGISTERED_FAILURE_IDS: tuple[str, ...] = (
    "runtime_context/context_incomplete",
    "verifier_context/companion_record_unavailable",
    "verifier_context/proof_row_required",
    "evidence_corruption/list_field_incomplete",
    "evidence_corruption/classifier_rule_blocked",
)


@pytest.mark.parametrize("failure_id", _REGISTERED_FAILURE_IDS)
def test_acceptance_artifact_pins_failure_id(failure_id: str) -> None:
    """Every Slice 13A typed failure id must be pinned in the artifact."""
    text = ARTIFACT_PATH.read_text(encoding="utf-8")
    assert failure_id in text, (
        f"Slice 13A acceptance artifact missing typed failure id pin: "
        f"{failure_id!r}. All 5 ids registered in Slice 13A must be "
        f"enumerated in the artifact."
    )


# Per the 7th-sub-slice finalizer P1-13A-7-1 remediation: the
# ``runtime_context`` and ``verifier_context`` failure_classes
# pre-existed in Slices 00-12 (per the journal cross-reference at
# lines 43546 + 43622 + 44115; per
# ``src/iriai_build_v2/workflows/develop/execution/failure_router.py:31``
# + ``:35`` in the ``FailureClass`` typed alias). Only the failure_ids
# (sub-types) are new. The artifact MUST NOT mis-label the
# failure_classes as "(new failure_class)". This test PINS the
# corrected wording so the inaccuracy cannot regress.
_FAILURE_CLASS_LABEL_CONTRACT: tuple[tuple[str, str], ...] = (
    # (failure-class-name, required-row-suffix-substring)
    # `runtime_context` + `verifier_context` pre-existed -- they must
    # carry "(existing failure_class; new failure_id)".
    ("runtime_context", "(existing failure_class; new failure_id)"),
    ("verifier_context", "(existing failure_class; new failure_id)"),
    # `evidence_corruption` also pre-existed (per the 6th-sub-slice
    # P3-13A-6-1 force-fit decision); rows must carry "(existing
    # failure_class)" wording.
    ("evidence_corruption", "(existing failure_class)"),
)


@pytest.mark.parametrize(
    "failure_class,required_suffix", _FAILURE_CLASS_LABEL_CONTRACT
)
def test_acceptance_artifact_does_not_mislabel_failure_class(
    failure_class: str, required_suffix: str
) -> None:
    """Pre-existing failure_classes must NOT be labelled '(new failure_class)'.

    Per the 7th-sub-slice finalizer P1-13A-7-1 remediation, all
    Slice 13A typed failure ids register under failure_classes that
    pre-existed in Slices 00-12 (none of the failure_classes
    themselves are new). The acceptance artifact MUST reflect this
    accurately:

    - ``runtime_context`` + ``verifier_context`` rows must carry
      "(existing failure_class; new failure_id)" (only the
      failure_id sub-type is new).
    - ``evidence_corruption`` rows must carry "(existing
      failure_class)" per the 6th-sub-slice P3-13A-6-1 force-fit.

    This test pins the corrected wording so the "(new failure_class)"
    inaccuracy cannot regress.
    """
    text = ARTIFACT_PATH.read_text(encoding="utf-8")
    # The "(new failure_class)" wording must NOT appear adjacent to
    # any of the three pre-existing failure_classes.
    forbidden = f"`{failure_class}` (new failure_class)"
    assert forbidden not in text, (
        f"Slice 13A acceptance artifact mis-labels {failure_class!r} as "
        f"'(new failure_class)'. The failure_class pre-existed in Slices "
        f"00-12 (per failure_router.py FailureClass typed alias); only "
        f"the failure_id sub-type is new. Use {required_suffix!r} instead."
    )
    # And the corrected wording MUST appear for the failure_class.
    expected = f"`{failure_class}` {required_suffix}"
    assert expected in text, (
        f"Slice 13A acceptance artifact missing the corrected wording "
        f"for {failure_class!r}; expected at least one row to label it "
        f"as {expected!r}."
    )


def test_acceptance_artifact_has_no_new_failure_class_mislabels() -> None:
    """Belt-and-braces: zero occurrences of '(new failure_class)' anywhere.

    All 5 Slice 13A typed failure ids register under failure_classes
    (`runtime_context`, `verifier_context`, `evidence_corruption`)
    that pre-existed in Slices 00-12. The phrase '(new failure_class)'
    must NOT appear anywhere in the artifact -- the corrected wording
    is "(existing failure_class; new failure_id)" or "(existing
    failure_class)".
    """
    text = ARTIFACT_PATH.read_text(encoding="utf-8")
    assert "(new failure_class)" not in text, (
        "Slice 13A acceptance artifact contains the forbidden '(new "
        "failure_class)' phrase. ALL Slice 13A failure_classes "
        "pre-existed; only the failure_ids (sub-types) are new. Use "
        "'(existing failure_class; new failure_id)' or '(existing "
        "failure_class)' instead."
    )


# ---------------------------------------------------------------------------
# (f) Per-sub-slice module __all__ projections
# ---------------------------------------------------------------------------


_MODULE_PROJECTIONS: tuple[tuple[str, int], ...] = (
    # (module-path-fragment, __all__ count)
    ("execution_control/completeness.py", 7),
    ("execution_control/prompt_context_adapter.py", 3),
    ("execution_control/dispatcher_prompt_context.py", 6),
    ("execution_control/gate_companion.py", 9),
    ("execution_control/snapshot_companion.py", 9),
)


@pytest.mark.parametrize("module_fragment,count", _MODULE_PROJECTIONS)
def test_acceptance_artifact_pins_module_all_count(
    module_fragment: str, count: int
) -> None:
    """Each per-sub-slice module + its __all__ count must be pinned."""
    text = ARTIFACT_PATH.read_text(encoding="utf-8")
    assert module_fragment in text, (
        f"Slice 13A acceptance artifact does not reference module path "
        f"fragment {module_fragment!r}; expected the per-sub-slice "
        f"__all__ projections table to list it."
    )
    assert f"**{count}**" in text or f" {count} " in text, (
        f"Slice 13A acceptance artifact does not pin __all__ count "
        f"{count} for module {module_fragment!r}."
    )


# ---------------------------------------------------------------------------
# (g) Carried-P3 ledger pins
# ---------------------------------------------------------------------------


_CARRIED_P3_IDS: tuple[str, ...] = (
    "P3-13A-1",
    "P3-13A-5-1",
    "P3-13A-5-2",
    "P3-13A-5-4",
    "P3-13A-6-1",
    "P3-13A-6-2",
    "P3-13A-6-3",
)


@pytest.mark.parametrize("p3_id", _CARRIED_P3_IDS)
def test_acceptance_artifact_pins_carried_p3(p3_id: str) -> None:
    """Each carried-P3 id from Slice 13A must be pinned in the artifact."""
    text = ARTIFACT_PATH.read_text(encoding="utf-8")
    assert p3_id in text, (
        f"Slice 13A acceptance artifact missing carried-P3 ledger entry "
        f"{p3_id!r}. The artifact must enumerate the full Slice 13A "
        f"carried-P3 ledger."
    )


def test_acceptance_artifact_pins_p3_13A_5_4_downgrade() -> None:
    """The P3-13A-5-4 DOWNGRADED status must be explicitly pinned.

    Per the sixth-sub-slice finalizer, P3-13A-5-4 was downgraded from
    CLOSED to DOWNGRADED (the implementer's closure claim was
    OVERSTATED). The acceptance artifact must record this explicitly to
    avoid a repeat of the documentation drift.
    """
    text = ARTIFACT_PATH.read_text(encoding="utf-8")
    assert "DOWNGRADED" in text, (
        "Acceptance artifact must explicitly pin the P3-13A-5-4 "
        "DOWNGRADED status (restated as P3-13A-6-3)."
    )


# ---------------------------------------------------------------------------
# (h) Dead-until-wired binding statement (P3-13A-6-3)
# ---------------------------------------------------------------------------


def test_acceptance_artifact_pins_dead_until_wired_binding_statement() -> None:
    """The P3-13A-6-3 dead-until-wired binding statement must be explicit.

    Per the sixth-sub-slice finalizer P2-V-1 reframing, the composite
    `LegacyGateConsumerSnapshotAdapter` chain remains dead-until-wired
    because NEITHER underlying adapter has external production callers.
    The acceptance artifact must record this binding statement so a
    future reader cannot miss the production-wiring prerequisite.
    """
    text = ARTIFACT_PATH.read_text(encoding="utf-8")
    assert "dead-until-wired" in text, (
        "Acceptance artifact must pin the 'dead-until-wired' binding "
        "statement for the composite LegacyGateConsumerSnapshotAdapter "
        "chain (P3-13A-6-3)."
    )
    assert "LegacyGateConsumerSnapshotAdapter" in text, (
        "Acceptance artifact must name the composite adapter chain by "
        "type (LegacyGateConsumerSnapshotAdapter)."
    )
    # The wiring target options must be named so the LAST sub-slice
    # implementer knows where the wiring lands.
    assert (
        "supervisor/classifier.py" in text
        and "public_dashboard.py" in text
    ), (
        "Acceptance artifact must name the wiring target options "
        "(supervisor/classifier.py + public_dashboard.py)."
    )


# ---------------------------------------------------------------------------
# (i) Citations
# ---------------------------------------------------------------------------


_REQUIRED_DOC_CITATIONS: tuple[str, ...] = (
    "doc-13a:18-23",
    "doc-13a:111-115",
    "doc-13a:280-282",
    "doc-13a:283-285",
    "doc-13a:285-287",
)


@pytest.mark.parametrize("citation", _REQUIRED_DOC_CITATIONS)
def test_acceptance_artifact_cites_doc13a(citation: str) -> None:
    """Per the auto-memory ``feedback_cite_everything`` rule every claim
    in the acceptance artifact must be backed by a doc-13a citation.

    These specific citations are required by the user-prompt §
    Non-negotiables:

    - doc-13a:18-23 (invariant)
    - doc-13a:111-115 (blocking deviations)
    - doc-13a:280-282 (snapshot classifier fail-closed)
    - doc-13a:283-285 (step 8 -- acceptance artifact + README index)
    - doc-13a:285-287 (step 9 -- deferred dependency reconciliation)
    """
    text = ARTIFACT_PATH.read_text(encoding="utf-8")
    assert citation in text, (
        f"Slice 13A acceptance artifact missing required doc-13a "
        f"citation: {citation}. Per the user-prompt non-negotiables "
        f"every claim must be backed by a doc-13a citation."
    )
