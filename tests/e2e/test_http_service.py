"""Integration test: http_service adapter against a stdlib fixture service.

Validates the adapter abstraction generalizes beyond the browser adapter:
provision + per-surface boot-smoke + request replay, all green.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from iriai_build_v2.workflows.develop.e2e.adapters import get_adapter
from iriai_build_v2.workflows.develop.e2e.models import ProjectProfile

FIXTURE = Path(__file__).parent / "fixtures" / "http_fixture.py"


@pytest.mark.asyncio
async def test_http_service_adapter_provision_smoke_run(tmp_path):
    profile = ProjectProfile(
        project_kind="api",
        adapter_id="http_service",
        start_cmd=f"{sys.executable} {FIXTURE} --port {{port}}",
        ready_probe_kind="http_get",
        ready_probe_target="/healthz",
        base_url_template="http://127.0.0.1:{port}",
    )
    adapter = get_adapter("http_service")
    instance = await adapter.provision(profile, tmp_path)
    try:
        smokes = await adapter.smoke(instance, profile)
        assert len(smokes) == 1
        assert smokes[0].status == "pass", smokes[0].detail
        assert smokes[0].surface == "api"

        verdicts = await adapter.run(
            instance, [], requests=[("/healthz", 200), ("/api/items", 200),
                                    ("/missing", 404)]
        )
        by_path = {v.spec_id: v.status for v in verdicts}
        assert by_path["/healthz"] == "pass"
        assert by_path["/api/items"] == "pass"
        assert by_path["/missing"] == "pass"  # 404 expected -> matched -> pass
    finally:
        await adapter.teardown(instance)


def test_http_service_adapter_registered():
    from iriai_build_v2.workflows.develop.e2e.adapters import available_adapters

    assert "http_service" in available_adapters()
    assert "browser" in available_adapters()
