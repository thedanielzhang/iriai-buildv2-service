from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard
from dashboard import BridgeManager, _assemble_bugflow_response, _parse_timeline_entry


def test_bridge_manager_injects_dashboard_base_url_into_bridge_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("IRIAI_DASHBOARD_BASE_URL", raising=False)
    manager = BridgeManager({
        "channel": "C123",
        "dashboard_base_url": "https://dash.trycloudflare.com",
    })

    env = manager._build_env()

    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["IRIAI_DASHBOARD_BASE_URL"] == "https://dash.trycloudflare.com"


@pytest.mark.asyncio
async def test_maybe_start_dashboard_tunnel_uses_cloudflared_url(monkeypatch: pytest.MonkeyPatch):
    original_bridge_config = dict(dashboard.bridge_config)
    original_dashboard_config = dict(dashboard.dashboard_config)
    original_tunnel = dashboard.dashboard_tunnel

    class _FakeTunnel:
        async def start(self, target_url: str) -> str | None:
            assert target_url == "http://localhost:51234"
            return "https://dash.trycloudflare.com"

        async def stop(self) -> None:
            return None

    try:
        dashboard.bridge_config.clear()
        dashboard.bridge_config.update({"channel": "C123"})
        dashboard.dashboard_config.clear()
        dashboard.dashboard_config.update({"port": 51234})
        dashboard.dashboard_tunnel = None
        monkeypatch.setenv("IRIAI_DASHBOARD_BASE_URL", "https://stale.example")
        monkeypatch.setattr(
            "iriai_build_v2.services.tunnel.CloudflaredUrlTunnel",
            _FakeTunnel,
        )

        public_url = await dashboard._maybe_start_dashboard_tunnel()

        assert public_url == "https://dash.trycloudflare.com"
        assert isinstance(dashboard.dashboard_tunnel, _FakeTunnel)
    finally:
        dashboard.bridge_config.clear()
        dashboard.bridge_config.update(original_bridge_config)
        dashboard.dashboard_config.clear()
        dashboard.dashboard_config.update(original_dashboard_config)
        dashboard.dashboard_tunnel = original_tunnel


def test_parse_timeline_entry_understands_bugflow_keys():
    now = datetime.now(timezone.utc)

    queue_entry = _parse_timeline_entry(
        "bugflow-queue",
        json.dumps({"active_step": "Re-verifying C-3", "counts": {"queued": 2}}),
        now,
    )
    lane_entry = _parse_timeline_entry(
        "bugflow-lane:L-1",
        json.dumps({"status": "active_fix", "current_phase": "fixing"}),
        now,
    )
    report_entry = _parse_timeline_entry(
        "bugflow-report:BR-12",
        json.dumps({"status": "queued", "current_step": "Validating"}),
        now,
    )
    push_entry = _parse_timeline_entry(
        "bugflow-repo-status",
        json.dumps({"branch_name": "bugfix-v2/demo", "repos": [{"repo_name": "frontend"}]}),
        now,
    )
    strategy_entry = _parse_timeline_entry(
        "bugflow-strategy:C-3:2",
        json.dumps({"strategy_mode": "broaden_scope", "reasoning": "Need parity surfaces too."}),
        now,
    )
    bundle_entry = _parse_timeline_entry(
        "bugflow-failure-bundle:C-3:2",
        json.dumps({"stable_failure_family": "checkout parity mismatch", "bundle_summary": "Need a broader parity fix."}),
        now,
    )

    assert queue_entry == {
        "key": "bugflow-queue",
        "type": "queue",
        "passed": None,
        "summary": "Re-verifying C-3",
        "created_at": now.isoformat(),
    }
    assert lane_entry == {
        "key": "bugflow-lane:L-1",
        "type": "lane",
        "passed": None,
        "summary": "L-1 — fixing",
        "created_at": now.isoformat(),
    }
    assert report_entry == {
        "key": "bugflow-report:BR-12",
        "type": "report",
        "passed": None,
        "summary": "BR-12 — queued — Validating",
        "created_at": now.isoformat(),
    }
    assert push_entry == {
        "key": "bugflow-repo-status",
        "type": "push",
        "passed": None,
        "summary": "frontend",
        "created_at": now.isoformat(),
    }
    assert strategy_entry == {
        "key": "bugflow-strategy:C-3:2",
        "type": "strategy",
        "passed": None,
        "summary": "C-3 — broaden_scope — Need parity surfaces too.",
        "created_at": now.isoformat(),
    }
    assert bundle_entry == {
        "key": "bugflow-failure-bundle:C-3:2",
        "type": "failure_bundle",
        "passed": None,
        "summary": "C-3 — checkout parity mismatch",
        "created_at": now.isoformat(),
    }


def test_assemble_bugflow_response_builds_bugflow_payload():
    now = datetime.now(timezone.utc)
    feat = {
        "id": "abcd1234",
        "name": "Bugflow Demo",
        "phase": "bugflow-queue",
        "workflow_name": "bugfix-v2",
        "updated_at": now,
        "metadata": {"source_feature_id": "beced7b1"},
    }

    rows = [
        {
            "key": "bugflow-queue",
            "value": json.dumps({
                "active_step": "Promoting L-3",
                "active_report_id": "BR-12",
                "active_lane_ids": ["L-1"],
                "verified_pending_promotion_ids": ["L-2"],
                "promoting_lane_id": "L-3",
                "promotion_status_text": "Promoting L-3",
                "counts": {
                    "active_fix": 1,
                    "queued": 0,
                    "resolved": 2,
                },
            }),
            "created_at": now,
        },
        {
            "key": "bugflow-report:BR-12",
            "value": json.dumps({
                "title": "Validation fails on save",
                "status": "active_fix",
                "category": "bug",
                "severity": "major",
                "cluster_id": "C-3",
                "current_step": "Re-verifying",
                "summary": "Main reproduction thread",
                "validation_summary": "Still reproduces on latest branch head.",
                "expected_behavior": "Save should succeed",
                "actual_behavior": "Save returns 500",
                "ui_involved": True,
                "evidence_modes": ["ui", "api"],
                "latest_proof_key": "bugflow-proof:BR-12:validate",
                "terminal_proof_key": "bugflow-proof:BR-12:terminal",
            }),
            "created_at": now - timedelta(minutes=5),
        },
        {
            "key": "bugflow-cluster:C-3",
            "value": json.dumps({
                "group_id": "BG-1",
                "status": "verified_pending_promotion",
                "current_phase": "reverify",
                "report_ids": ["BR-12"],
                "lane_id": "L-1",
                "likely_root_cause": "Validation service throws on null payload",
                "affected_files": ["services/validation.py"],
                "repo_paths": ["services/api"],
                "schedule_round": 2,
                "schedule_total_rounds": 3,
                "attempt_number": 4,
            }),
            "created_at": now - timedelta(minutes=4),
        },
        {
            "key": "bugflow-lane:L-1",
            "value": json.dumps({
                "status": "active_fix",
                "current_phase": "fixing",
                "report_ids": ["BR-12"],
                "category": "bug",
                "source_cluster_id": "C-3",
                "lock_scope": ["file:services/validation.py"],
                "repo_paths": ["services/api"],
                "latest_rca_keys": ["bug-rca:integration:C-3:attempt-1"],
                "latest_verify_keys": ["bug-reverify:integration:C-3:attempt-1"],
                "latest_regression_keys": ["bug-regression:integration:attempt-1"],
                "latest_fix_summary": "Guard null payload before validation",
                "latest_rca_summary": "Validation service throws on null payload",
            }),
            "created_at": now - timedelta(minutes=4),
        },
        {
            "key": "bugflow-lane:L-2",
            "value": json.dumps({
                "status": "verified_pending_promotion",
                "report_ids": ["BR-22"],
                "category": "bug",
                "lock_scope": ["file:services/payments.py"],
                "repo_paths": ["services/api"],
            }),
            "created_at": now - timedelta(minutes=3),
        },
        {
            "key": "bugflow-lane:L-3",
            "value": json.dumps({
                "status": "promoting",
                "report_ids": ["BR-12"],
                "category": "bug",
                "lock_scope": ["file:services/validation.py"],
                "repo_paths": ["services/api"],
            }),
            "created_at": now - timedelta(minutes=2),
        },
        {
            "key": "bugflow-decisions",
            "value": json.dumps([{
                "decision_id": "D-1",
                "report_ids": ["BR-12"],
                "title": "Clarify validation copy",
                "old_expectation": "Generic error",
                "new_decision": "Show actionable validation copy",
                "approved": True,
                "summary": "Use actionable validation copy",
            }]),
            "created_at": now - timedelta(minutes=3),
        },
        {
            "key": "bugflow-proof:BR-12:validate",
            "value": json.dumps({
                "report_id": "BR-12",
                "stage": "validate",
                "bundle_url": "https://dash.trycloudflare.com/proof/abcd1234/BR-12/validate/index.html",
                "primary_artifact_url": "https://dash.trycloudflare.com/proof/abcd1234/BR-12/validate/01-screenshot-after-save.png",
                "bundle": {
                    "summary": "Validation proof bundle",
                    "ui_involved": True,
                    "evidence_modes": ["ui", "api"],
                },
            }),
            "created_at": now - timedelta(minutes=3),
        },
        {
            "key": "bugflow-proof:BR-12:terminal",
            "value": json.dumps({
                "report_id": "BR-12",
                "stage": "terminal",
                "bundle_url": "https://dash.trycloudflare.com/proof/abcd1234/BR-12/validate/index.html",
                "primary_artifact_url": "https://dash.trycloudflare.com/proof/abcd1234/BR-12/validate/01-screenshot-after-save.png",
                "bundle": {
                    "summary": "Terminal proof bundle",
                    "ui_involved": True,
                    "evidence_modes": ["ui", "api"],
                },
            }),
            "created_at": now - timedelta(minutes=2),
        },
        {
            "key": "bugflow-repo-status",
            "value": json.dumps({
                "branch_name": "bugfix-v2/demo",
                "has_unpushed_verified_work": True,
                "repos": [{
                    "repo_path": "services/api",
                    "repo_name": "api",
                    "last_pushed_commit": "abc1234",
                    "status": "verified",
                    "touched": True,
                }],
            }),
            "created_at": now - timedelta(minutes=2),
        },
        {
            "key": "bug-fix-attempts",
            "value": "\n\n".join([
                json.dumps({
                    "bug_id": "BUG-1",
                    "group_id": "BG-1",
                    "description": "validation service save failure",
                    "fix_applied": "Guard null payload before validation",
                    "re_verify_result": "FAIL",
                }),
            ]),
            "created_at": now - timedelta(minutes=1),
        },
    ]

    timeline_rows = [
        rows[0],
        rows[1],
        rows[2],
        {
            "key": "bug-rca:integration:C-3:attempt-1",
            "value": json.dumps({"hypothesis": "Null payload reaches validator"}),
            "created_at": now - timedelta(minutes=4),
        },
        {
            "key": "bug-dispatch:integration:attempt-1",
            "value": json.dumps({
                "schedule": [{"round": 0, "group_ids": ["C-3"]}],
            }),
            "created_at": now - timedelta(minutes=4),
        },
        {
            "key": "bug-reverify:integration:C-3:attempt-1",
            "value": json.dumps({"approved": False, "summary": "Still failing on null payload"}),
            "created_at": now - timedelta(minutes=3),
        },
        {
            "key": "bug-regression:integration:attempt-1",
            "value": json.dumps({"approved": True, "summary": "No regressions"}),
            "created_at": now - timedelta(minutes=2),
        },
        {
            "key": "contradiction:integration:C-3",
            "value": "User confirmed new validation copy direction.",
            "created_at": now - timedelta(minutes=1),
        },
    ]

    events = [
        {
            "event_type": "agent_start",
            "source": "integration_tester",
            "content": "",
            "created_at": now,
        },
    ]

    result = _assemble_bugflow_response(
        feat=feat,
        rows=rows,
        timeline_rows=timeline_rows,
        events=events,
        feature_id="abcd1234",
        last_activity_at=now.isoformat(),
        request_base_url="https://dash.example",
    )

    bugflow = result["bugflow"]
    report = bugflow["reports"][0]
    promoting_lane = bugflow["promoting_lane"]
    active_lane = bugflow["active_lanes"][0]

    assert result["workflow_name"] == "bugfix-v2"
    assert result["source_feature_id"] == "beced7b1"
    assert result["dashboard_url"] == "https://dash.example/feature/abcd1234"
    assert bugflow["status_text"] == "Promoting L-3"
    assert bugflow["health"] == "running"
    assert bugflow["repo_status"]["branch_name"] == "bugfix-v2/demo"
    assert bugflow["repo_status"]["has_unpushed_verified_work"] is True

    assert report["report_id"] == "BR-12"
    assert report["thread_status"] == "ready"
    assert report["strategy_mode"] == ""
    assert report["decision"]["decision_id"] == "D-1"
    assert "/proof/abcd1234/BR-12/validate/" in report["latest_proof"]["bundle_url"]
    assert report["terminal_proof"]["bundle"]["summary"] == "Terminal proof bundle"
    assert len(report["fix_attempts"]) == 1
    assert report["lane"]["lane_id"] == "L-1"
    assert report["cluster"]["group_id"] == "BG-1"

    assert active_lane["lane_id"] == "L-1"
    assert active_lane["latest_rca_summary"] == "Null payload reaches validator"
    assert active_lane["latest_fix_summary"] == "Guard null payload before validation"
    assert active_lane["latest_verify_summary"] == "Still failing on null payload"
    assert promoting_lane["lane_id"] == "L-3"
    assert [lane["lane_id"] for lane in bugflow["verified_pending_promotion"]] == ["L-2"]
    contradiction_entry = next(decision for decision in bugflow["decisions"] if decision["source_key"] == "contradiction:integration:C-3")
    assert contradiction_entry["approved"] is False

    section_names = [section["name"] for section in bugflow["timeline_sections"]]
    assert section_names == ["Queue / Intake", "Fix Engine", "Decisions", "Pushes"]


def test_assemble_bugflow_response_prefers_exact_verify_key_over_unscoped_obs_verdict():
    now = datetime.now(timezone.utc)
    feat = {
        "id": "abcd1234",
        "name": "Bugflow Demo",
        "phase": "bugflow-queue",
        "workflow_name": "bugfix-v2",
        "updated_at": now,
        "metadata": {"source_feature_id": "beced7b1"},
    }

    exact_verify_key = "bug-reverify:lane-retry:L-1:L-1-retry-1"
    rows = [
        {
            "key": "bugflow-queue",
            "value": json.dumps({"counts": {"active_fix": 1}}),
            "created_at": now,
        },
        {
            "key": "bugflow-report:BR-12",
            "value": json.dumps({
                "status": "active_fix",
                "category": "bug",
                "severity": "major",
                "cluster_id": "C-3",
                "lane_id": "L-1",
                "summary": "Main reproduction thread",
            }),
            "created_at": now,
        },
        {
            "key": "bugflow-cluster:C-3",
            "value": json.dumps({
                "report_ids": ["BR-12"],
                "lane_id": "L-1",
                "status": "active_fix",
            }),
            "created_at": now,
        },
        {
            "key": "bugflow-lane:L-1",
            "value": json.dumps({
                "status": "active_fix",
                "report_ids": ["BR-12"],
                "category": "bug",
                "source_cluster_id": "C-3",
                "latest_verify_keys": [exact_verify_key],
            }),
            "created_at": now,
        },
        {
            "key": exact_verify_key,
            "value": json.dumps({"approved": False, "summary": "Retry verify exact summary"}),
            "created_at": now - timedelta(minutes=1),
        },
    ]
    timeline_rows = [
        rows[0],
        rows[1],
        rows[2],
        {
            "key": "obs-verdict:BR-other",
            "value": json.dumps({"approved": False, "summary": "Wrong fallback summary"}),
            "created_at": now,
        },
    ]

    result = _assemble_bugflow_response(
        feat=feat,
        rows=rows,
        timeline_rows=timeline_rows,
        events=[],
        feature_id="abcd1234",
        last_activity_at=now.isoformat(),
        request_base_url="https://dash.example",
    )

    lane = result["bugflow"]["lanes"][0]
    assert lane["latest_verify_summary"] == "Retry verify exact summary"


@pytest.mark.asyncio
async def test_serve_bugflow_proof_reads_from_feature_proof_store(tmp_path: Path):
    feature_root = tmp_path / ".iriai" / "features" / "bugflow-abcd1234"
    proof_file = feature_root / "proof" / "BR-12" / "validate" / "index.html"
    proof_file.parent.mkdir(parents=True, exist_ok=True)
    proof_file.write_text("<html>proof</html>", encoding="utf-8")

    class _Conn:
        async def fetchrow(self, _query: str, _feature_id: str):
            return {"slug": "bugflow-abcd1234", "metadata": {"workspace_path": str(tmp_path)}}

    class _Acquire:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    original_pool = dashboard.pool
    dashboard.pool = SimpleNamespace(acquire=lambda: _Acquire())
    try:
        response = await dashboard.serve_bugflow_proof("abcd1234", "BR-12", "validate", "index.html")
    finally:
        dashboard.pool = original_pool

    assert Path(response.path) == proof_file


def test_derive_bugflow_health_prefers_degraded_when_recovery_and_blocked_mix():
    health = dashboard._derive_bugflow_health(
        {
            "recovering_lane_ids": ["L-9"],
            "stalled_lane_ids": [],
            "strategy_pending_cluster_ids": [],
        },
        reports=[],
        active_lanes=[],
        promoting_lane=None,
        active_report=None,
        counts={"blocked": 2},
    )

    assert health == "degraded"
