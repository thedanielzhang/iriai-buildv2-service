"""Slice 19 slice-end SIX-VECTOR REMEDIATION (post V1 P2 finding) --
structural sentinel for the Slice 21-conditional
``ContextLayerPackageSummary`` deferral.

Per the **2026-05-25 Slice 19 slice-end SIX-VECTOR review V1 P2
finding**:

    *AC4 (post-Slice-21 ``ContextLayerPackageSummary``) is correctly
    documented as deferred but ``governance_agent_context_builder.py``
    doesn't ship a deferred-AC sentinel test that fails when Slice 21
    lands without ``ContextLayerPackageSummary`` wiring; the deferral
    is a comment, not a structural reminder.*

This module ships the structural reminder. It fails IF/WHEN
``ContextLayerPackageSummary`` is wired into any Slice 19 source
module BEFORE the proper Slice 21 acceptance + the corresponding
slice-21 AC bullet at doc-19:228-229 is enforced via a
``ContextLayerPackageSummary``-typed end-to-end test in
``tests/test_execution_control_governance_agent_context_builder.py``.

The sentinel is intentionally NEGATIVE (asserts absence). Slice 21
WILL eventually land ``ContextLayerPackageSummary`` wiring; at that
point this sentinel SHOULD start failing, forcing the Slice 21
implementer to:

1. CLOSE this sentinel by either:
   (a) deleting this test file once Slice 21 acceptance is recorded
       in STATUS.md, OR
   (b) UPDATING this sentinel to assert the PRESENCE of the typed
       :class:`ContextLayerPackageSummary` import + the wired
       end-to-end test, instead of its absence.
2. Append a new typed end-to-end acceptance test asserting the
   ``ContextLayerPackageSummary``-backed wiring honours doc-19:179-182
   (citeable package id + digest + source DAG sha + typed evidence
   digest + provider state digest).
3. Update :file:`tests/test_execution_control_governance_agent_context_builder.py`
   Section 15 sentinels to flip from "DEFERRED" to "WIRED" assertions.

Per the auto-memory ``feedback_no_silent_degradation`` rule: a
silent post-Slice-21 reuse of pre-Slice-21 stubs WITHOUT the proper
acceptance-test wiring is fail-closed. This sentinel is the
structural enforcement.

Per the auto-memory ``feedback_cite_everything`` rule: the sentinel
cites doc-19:89-101 (``ContextLayerPackageSummary`` shape) +
doc-19:125-127 (post-Slice-21 binding) + doc-19:228-229 (AC4 PIN cite)
+ doc-19:205-210 (test expectations after Slice 21).

Author: Slice 19 slice-end SIX-VECTOR REMEDIATION (post V1 P2).
"""

from __future__ import annotations

import importlib
import pathlib

import pytest


SLICE_19_SOURCE_MODULES: tuple[str, ...] = (
    "iriai_build_v2.execution_control.governance_agent",
    "iriai_build_v2.execution_control.governance_snapshot_api",
    "iriai_build_v2.execution_control.governance_dashboard_view",
    "iriai_build_v2.execution_control.governance_slack_renderer",
    "iriai_build_v2.execution_control.governance_agent_context_builder",
    "iriai_build_v2.execution_control.governance_report_artifact",
)


# ---------------------------------------------------------------------------
# (a) Slice 21 not landed yet -- assert no Slice 19 module exposes
#     ContextLayerPackageSummary as a typed symbol.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", SLICE_19_SOURCE_MODULES)
def test_no_slice_19_source_module_imports_context_layer_package_summary(
    module_name: str,
) -> None:
    """Per doc-19:89-101 + doc-19:125-127 the typed
    :class:`ContextLayerPackageSummary` shape is a post-Slice-21
    deliverable; until Slice 21 ACCEPTANCE no Slice 19 source module
    may IMPORT or RE-EXPORT the symbol.

    If this assertion fails, Slice 21 wiring is in flight -- the
    Slice 21 implementer MUST update Section 15 sentinels in
    :file:`tests/test_execution_control_governance_agent_context_builder.py`
    and close or flip this sentinel per the module docstring.
    """
    mod = importlib.import_module(module_name)
    assert (
        getattr(mod, "ContextLayerPackageSummary", None) is None
    ), (
        f"Slice 19 source module {module_name!r} exposes a typed "
        f"`ContextLayerPackageSummary` symbol BEFORE Slice 21 has been "
        f"ACCEPTED. Per doc-19:89-101 + doc-19:125-127 + doc-19:228-229 "
        f"AC4 + doc-19:205-210 test contract this is the structural "
        f"sentinel for Slice 21-conditional wiring. The Slice 21 "
        f"implementer MUST either close this sentinel (delete this test "
        f"file once Slice 21 ACCEPTANCE is recorded in STATUS.md) OR "
        f"flip it to assert PRESENCE + add the typed end-to-end wiring "
        f"acceptance test. See this module's docstring for the full "
        f"close-out checklist."
    )


# ---------------------------------------------------------------------------
# (b) The typed GovernanceAgentContext shape does NOT yet carry a
#     context_package field (per doc-19:89-101 + doc-19:125-127).
# ---------------------------------------------------------------------------


def test_governance_agent_context_does_not_carry_context_package_field_yet() -> None:
    """Per doc-19:89-101 + doc-19:125-127 the post-Slice-21
    ``context_package: ContextLayerPackageSummary | None = None``
    field is DEFERRED. Until Slice 21 lands, the typed
    :class:`GovernanceAgentContext` shape MUST NOT carry this field.

    If this assertion fails, Slice 21 wiring is in flight -- the
    Slice 21 implementer MUST update Section 15 sentinels in
    :file:`tests/test_execution_control_governance_agent_context_builder.py`
    and close or flip this sentinel per the module docstring.
    """
    from iriai_build_v2.execution_control.governance_agent import (
        GovernanceAgentContext,
    )

    field_names = set(GovernanceAgentContext.model_fields.keys())
    assert "context_package" not in field_names, (
        "The typed `GovernanceAgentContext` shape carries a "
        "`context_package` field BEFORE Slice 21 has been ACCEPTED. "
        "Per doc-19:89-101 + doc-19:125-127 + doc-19:228-229 AC4 + "
        "doc-19:205-210 test contract this is the structural sentinel "
        "for Slice 21-conditional wiring. The Slice 21 implementer MUST "
        "either close this sentinel (delete this test file once Slice "
        "21 ACCEPTANCE is recorded in STATUS.md) OR flip it to assert "
        "PRESENCE + add the typed end-to-end wiring acceptance test. "
        "See this module's docstring for the full close-out checklist."
    )


# ---------------------------------------------------------------------------
# (c) The module-level deferral comment exists in
#     governance_agent_context_builder.py (per the P2 finding -- "the
#     deferral is a comment, not a structural reminder").
#     This test makes the comment + this structural reminder mutually
#     reinforcing: if the comment is removed without flipping this
#     sentinel, the sentinel detects the loss of structural intent.
# ---------------------------------------------------------------------------


def test_governance_agent_context_builder_still_documents_slice_21_deferral() -> None:
    """The :mod:`governance_agent_context_builder` module's docstring
    MUST continue to document the Slice 21-conditional
    ``ContextLayerPackageSummary`` deferral until Slice 21 lands. This
    pairs with sentinels (a) + (b) -- the comment + the structural
    assertions move together.
    """
    from iriai_build_v2.execution_control import (
        governance_agent_context_builder as mod,
    )

    assert mod.__doc__ is not None
    text = mod.__doc__
    assert "Slice 21" in text, (
        "The `governance_agent_context_builder` module docstring lost "
        "its Slice 21 deferral marker. Per doc-19:89-101 + "
        "doc-19:125-127 + doc-19:228-229 AC4 the deferral must remain "
        "documented (in addition to this structural sentinel) until "
        "Slice 21 ACCEPTANCE."
    )
    assert "DEFERRED" in text, (
        "The `governance_agent_context_builder` module docstring lost "
        "its DEFERRED marker. The Slice 21 implementer MUST close this "
        "sentinel only after Slice 21 ACCEPTANCE is recorded in "
        "STATUS.md."
    )
    assert "ContextLayerPackageSummary" in text, (
        "The `governance_agent_context_builder` module docstring lost "
        "its `ContextLayerPackageSummary` reference. The Slice 21 "
        "implementer MUST close this sentinel only after Slice 21 "
        "ACCEPTANCE is recorded in STATUS.md."
    )


# ---------------------------------------------------------------------------
# (d) STATUS.md does NOT yet record Slice 21 ACCEPTANCE -- when it
#     does, this sentinel module file should be reviewed for
#     close-out per the module docstring.
# ---------------------------------------------------------------------------


def test_status_md_does_not_yet_record_slice_21_acceptance() -> None:
    """When Slice 21 ACCEPTANCE is recorded in STATUS.md (i.e. a
    line like 'Slice 21 ... ACCEPTED' appears), the Slice 21
    implementer MUST close or flip this sentinel per the module
    docstring.

    If this assertion fails, Slice 21 has landed -- close this
    sentinel module per the module docstring close-out checklist.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    status_path = (
        repo_root / "docs" / "execution-control-plane" / "STATUS.md"
    )
    text = status_path.read_text(encoding="utf-8")

    # Look for the specific marker that would only appear once Slice 21
    # is ACCEPTED (the same pattern used by Slices 13/13A/14/15/16/17/18).
    slice_21_accepted_markers = (
        "Slice 21 — IriAI Context Layer: **ACCEPTED**",
        "Slice 21 (IriAI Context Layer): **ACCEPTED**",
        "Slice 21 -- IriAI Context Layer: **ACCEPTED**",
        "### Slice 21 — IriAI Context Layer: **ACCEPTED**",
        "### Slice 21 (IriAI Context Layer): **ACCEPTED**",
        "### Slice 21 -- IriAI Context Layer: **ACCEPTED**",
    )
    matched = [m for m in slice_21_accepted_markers if m in text]
    assert not matched, (
        f"STATUS.md records Slice 21 ACCEPTED (matched marker(s): "
        f"{matched}). The Slice 21 implementer MUST close or flip "
        f"this sentinel module per the module docstring close-out "
        f"checklist. Specifically: (1) delete this test file once the "
        f"typed `ContextLayerPackageSummary` wiring lands, OR "
        f"(2) flip the sentinels to assert PRESENCE of the typed "
        f"wiring + add the typed end-to-end acceptance test per "
        f"doc-19:205-210."
    )
