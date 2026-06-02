"""Round-trip tests for E2ERegistry against the scratch DB (skips if absent)."""

from __future__ import annotations

import os

import pytest

from iriai_build_v2.workflows.develop.e2e.models import (
    E2EGreenPointer,
    E2ESpecRecord,
    E2EStatus,
    E2ETrackCursor,
    E2EVerdictRecord,
    ProjectProfile,
)
from iriai_build_v2.workflows.develop.e2e.registry import (
    open_scratch_registry,
    scratch_feature,
)

SCRATCH_DSN = os.environ.get(
    "IRIAI_E2E_SCRATCH_DSN",
    "postgresql://danielzhang@localhost:5431/iriai_build_v2_e2e_scratch",
)


@pytest.mark.asyncio
async def test_registry_round_trips_all_models():
    feature = scratch_feature("8ac124d6-regtest")
    try:
        pool, reg = await open_scratch_registry(SCRATCH_DSN, feature)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"scratch DB unavailable: {exc}")
    try:
        await reg.put_profile(
            ProjectProfile(project_kind="electron", adapter_id="browser",
                           native_test_cmd="npx playwright test")
        )
        got = await reg.get_profile()
        assert got is not None and got.project_kind == "electron"
        assert got.native_test_cmd == "npx playwright test"

        spec = E2ESpecRecord(spec_id="badge-1", critical=True,
                             author_assertion_digests={"AC-badge-1": "abc"},
                             author_commit="0d480cd")
        await reg.put_spec(spec)
        assert (await reg.get_spec("badge-1")).author_assertion_digests == {
            "AC-badge-1": "abc"
        }

        v = E2EVerdictRecord(spec_id="badge-1", source_commit="cafe",
                             status="fail", failure_class="regression")
        await reg.put_verdict(v)
        assert (await reg.get_verdict("badge-1", "cafe")).failure_class == "regression"

        await reg.put_cursor(E2ETrackCursor(last_processed_commit="cafe", group_idx=79))
        assert (await reg.get_cursor()).group_idx == 79

        await reg.put_status(E2EStatus(latest_checkpoint="group 79", passed=2))
        assert (await reg.get_status()).passed == 2

        await reg.put_green_pointer(
            E2EGreenPointer(group_idx=79, result_commits={"iriai-studio": "0d480cd"})
        )
        assert (await reg.get_green_pointer()).group_idx == 79

        # latest-wins on re-put
        await reg.put_cursor(E2ETrackCursor(last_processed_commit="beef", group_idx=80))
        assert (await reg.get_cursor()).group_idx == 80
    finally:
        await pool.close()
