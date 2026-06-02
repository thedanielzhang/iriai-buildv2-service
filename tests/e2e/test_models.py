"""Smoke tests for e2e subsystem models."""

from __future__ import annotations

from iriai_build_v2.workflows.develop.e2e.models import (
    E2EGreenPointer,
    E2ESpecRecord,
    E2EStatus,
    E2ETrackCursor,
    E2EVerdictRecord,
    ProjectProfile,
)


def test_project_profile_defaults_flat():
    p = ProjectProfile(project_kind="electron", adapter_id="browser")
    assert p.project_kind == "electron"
    assert p.env_keys == []
    assert p.extra_repo_paths == []
    # round-trips through JSON (flat structured output)
    assert ProjectProfile.model_validate_json(p.model_dump_json()) == p


def test_spec_record_carries_assertion_digests():
    s = E2ESpecRecord(
        spec_id="badge-1",
        critical=True,
        linked_ac_ids=["AC-badge-1"],
        author_assertion_digests={"AC-badge-1": "deadbeef"},
        author_commit="0d480cd",
    )
    assert s.author_assertion_digests["AC-badge-1"] == "deadbeef"
    assert s.critical is True


def test_verdict_failure_classes_have_no_drift():
    from iriai_build_v2.workflows.develop.e2e.models import FAILURE_CLASSES

    assert "drift" not in FAILURE_CLASSES
    assert set(FAILURE_CLASSES) == {"regression", "intended_change", "flaky", "infra"}
    v = E2EVerdictRecord(spec_id="x", status="fail", failure_class="regression")
    assert v.failure_class == "regression"


def test_cursor_and_status_and_green_pointer():
    c = E2ETrackCursor(last_processed_commit="abc", group_idx=79)
    assert c.group_idx == 79 and c.updated_at
    st = E2EStatus(latest_checkpoint="group 79", boot_smoke="pass", passed=3)
    assert st.passed == 3 and st.updated_at
    g = E2EGreenPointer(group_idx=79, result_commits={"iriai-studio": "0d480cd"})
    assert g.result_commits["iriai-studio"] == "0d480cd"
