from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard
from dashboard import BridgeManager, _assemble_bugflow_response, _parse_timeline_entry
from iriai_build_v2.runtime_policy import PRIMARY_IMPL_SECONDARY_REVIEW_POLICY


def test_bridge_manager_injects_dashboard_base_url_into_bridge_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("IRIAI_DASHBOARD_BASE_URL", raising=False)
    manager = BridgeManager({
        "channel": "C123",
        "dashboard_base_url": "https://dash.trycloudflare.com",
    })

    env = manager._build_env()

    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["IRIAI_DASHBOARD_BASE_URL"] == "https://dash.trycloudflare.com"


def test_bridge_manager_forwards_runtime_policy_to_slack_bridge():
    manager = BridgeManager({
        "channel": "C123",
        "workspace": "/tmp/workspace",
        "agent_runtime": "claude_pool",
        "runtime_policy": PRIMARY_IMPL_SECONDARY_REVIEW_POLICY,
    })

    cmd = manager._build_cmd()

    assert "--agent-runtime" in cmd
    assert cmd[cmd.index("--agent-runtime") + 1] == "claude_pool"
    assert "--runtime-policy" in cmd
    assert cmd[cmd.index("--runtime-policy") + 1] == PRIMARY_IMPL_SECONDARY_REVIEW_POLICY


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


def test_parse_timeline_entry_understands_dag_repair_keys():
    now = datetime.now(timezone.utc)

    sanitize = _parse_timeline_entry(
        "dag-repair-result-sanitize:g26:retry-1",
        json.dumps({
            "ignored_path_count": 2,
            "rewritten_path_count": 1,
            "invalid_product_path_count": 0,
        }),
        now,
    )
    dispatch = _parse_timeline_entry(
        "dag-repair-dispatch:g26:retry-1",
        json.dumps({
            "contradiction_group_count": 1,
            "schedule": [{"round": 0, "group_ids": ["A", "B"]}],
        }),
        now,
    )

    assert sanitize == {
        "key": "dag-repair-result-sanitize:g26:retry-1",
        "type": "sanitize",
        "passed": None,
        "summary": "Sanitized paths: ignored=2, rewritten=1, invalid=0",
        "created_at": now.isoformat(),
    }
    assert dispatch == {
        "key": "dag-repair-dispatch:g26:retry-1",
        "type": "dispatch",
        "passed": None,
        "summary": "2 fix group(s), 1 contradiction(s)",
        "created_at": now.isoformat(),
    }


def test_assemble_dag_repair_metrics_derives_cycles_and_sanitizer_counts():
    now = datetime.now(timezone.utc)
    artifacts = {
        f"dag-group:{idx}": ("{}", (now - timedelta(hours=2)).isoformat())
        for idx in range(26)
    }
    timeline_rows = [
        {
            "key": "dag-repair-preflight:g26:retry-initial",
            "value": json.dumps({"approved": True}),
            "created_at": now - timedelta(minutes=30),
        },
        {
            "key": "dag-verify:g26:initial",
            "value": json.dumps({"approved": False, "summary": "normal verify failed"}),
            "created_at": now - timedelta(minutes=29),
        },
        {
            "key": "dag-repair-lens:g26:build-dependency:retry-1",
            "value": json.dumps({"approved": False, "summary": "build lens"}),
            "created_at": now - timedelta(minutes=25),
        },
        {
            "key": "dag-repair-expanded-verify:g26:retry-1",
            "value": json.dumps({"approved": False, "summary": "expanded"}),
            "created_at": now - timedelta(minutes=20),
        },
        {
            "key": "dag-repair-triage:g26:retry-1",
            "value": json.dumps({"groups": [{"group_id": "A"}, {"group_id": "B"}]}),
            "created_at": now - timedelta(minutes=18),
        },
        {
            "key": "dag-repair-rca:g26:A:retry-1",
            "value": json.dumps({"hypothesis": "A"}),
            "created_at": now - timedelta(minutes=16),
        },
        {
            "key": "dag-repair-dispatch:g26:retry-1",
            "value": json.dumps({
                "fixable_group_count": 2,
                "contradiction_group_count": 1,
                "rejected_contradiction_count": 1,
                "schedule": [{"round": 0, "group_ids": ["A", "B"]}],
            }),
            "created_at": now - timedelta(minutes=15),
        },
        {
            "key": "dag-repair-result-sanitize:g26:retry-1",
            "value": json.dumps({
                "ignored_path_count": 3,
                "rewritten_path_count": 1,
                "invalid_product_path_count": 2,
            }),
            "created_at": now - timedelta(minutes=10),
        },
        {
            "key": "dag-fix:g26:retry-1",
            "value": json.dumps({"summary": "Parallel DAG repair applied 2 root-cause-group fix(es)."}),
            "created_at": now - timedelta(minutes=9),
        },
        {
            "key": "dag-repair-preflight:g26:retry-1",
            "value": json.dumps({"approved": False, "concerns": [{"description": "bad path"}]}),
            "created_at": now - timedelta(minutes=8),
        },
        {
            "key": "dag-verify:g26:retry-1",
            "value": json.dumps({
                "approved": False,
                "summary": "Programmatic DAG preflight failed before model verification.",
            }),
            "created_at": now - timedelta(minutes=8),
        },
    ]

    metrics = dashboard._assemble_dag_repair_metrics(
        timeline_rows,
        artifacts,
        {"total_groups": 27, "execution_order": [[] for _ in range(27)]},
    )

    assert metrics["active_group_index"] == 26
    assert metrics["latest_checkpoint_group"] == 25
    assert metrics["summary"]["retry_count_for_active_group"] == 1
    assert metrics["summary"]["expanded_verify_runs"] == 1
    assert metrics["summary"]["fix_groups_scheduled"] == 2
    assert metrics["summary"]["fix_groups_applied"] == 2
    assert metrics["summary"]["final_preflight_failures"] == 1
    assert metrics["summary"]["sanitizer_ignored_paths"] == 3
    assert metrics["summary"]["sanitizer_rewritten_paths"] == 1
    assert metrics["summary"]["sanitizer_invalid_paths"] == 2
    assert metrics["current_cycle"]["group_idx"] == 26
    assert metrics["current_cycle"]["retry"] == "1"
    assert metrics["current_cycle"]["stage_durations"]["expanded_verify"] == 300


def test_public_exhibit_prefers_safe_generated_summary_and_artifact_gallery():
    now = datetime.now(timezone.utc).isoformat()
    feat = {
        "id": "feat1234",
        "name": "Fallback Name",
        "phase": "implementation",
        "workflow_name": "develop",
        "updated_at": now,
        "metadata": {},
    }
    artifacts = {
        "project": (
            json.dumps({"feature_name": "Generated Public Feature", "worktree_root": "/Users/danielzhang/src/private"}),
            now,
        ),
        "prd:broad": ("## Problem Statement\n\nBuild a public workflow exhibit.", now),
        "dag": (json.dumps({"tasks": [{"id": "T1"}], "execution_order": [["T1"]]}), now),
        "dag:strategy": (json.dumps({"workstreams": []}), now),
        "public-summary": (
            json.dumps({
                "title": "Public Narrative Title",
                "tagline": "A polished multi-agent delivery story.",
                "description": "A safe public description.",
                "current_focus": "Showing the current agent round.",
                "next_checkpoint": "Verifier approval.",
                "health": "running",
                "provenance": {"source_artifact_keys": ["prd:broad"]},
            }),
            now,
        ),
        "public-artifact-gallery": (
            json.dumps({
                "cards": [{
                    "key": "prd:broad",
                    "title": "Product Brief",
                    "family": "product",
                    "summary": "Curated explanation of the product artifact.",
                    "status": "published",
                }],
            }),
            now,
        ),
    }
    groups = [{
        "index": 0,
        "status": "active",
        "task_count": 1,
        "completed_count": 0,
        "tasks": [{"id": "T1", "name": "Build exhibit", "status": "in_progress"}],
        "verify_steps": [],
    }]

    exhibit = dashboard._assemble_public_exhibit(
        feat=feat,
        artifacts=artifacts,
        timeline=[],
        events=[],
        agent_activity={"active_agents": [], "recent_agents": []},
        dag_info={"total_groups": 1, "total_tasks": 1, "execution_order": [["T1"]]},
        groups=groups,
        workstreams=[],
        dag_repair=None,
        active_gate=None,
        gates={},
        last_activity_at=now,
    )

    assert exhibit["public_summary"]["title"] == "Public Narrative Title"
    assert exhibit["public_summary"]["source"] == "public-summary"
    assert exhibit["public_summary"]["provenance"]["source_artifact_keys"] == ["prd:broad"]
    assert exhibit["current_work"]["active_group"]["index"] == 0
    assert exhibit["current_work"]["active_tasks"][0]["id"] == "T1"
    assert exhibit["current_work"]["next_checkpoint"] == "Checkpoint batch 0 after final verifier approval"
    card = next(card for card in exhibit["artifact_exhibit"]["cards"] if card["key"] == "prd:broad")
    assert card["title"] == "Product Brief"
    assert card["source"] == "public-artifact-gallery"


def test_public_exhibit_rejects_unsafe_generated_summary_and_falls_back():
    now = datetime.now(timezone.utc).isoformat()
    feat = {
        "id": "feat1234",
        "name": "Safe Feature Name",
        "phase": "implementation",
        "workflow_name": "develop",
        "updated_at": now,
        "metadata": {},
    }
    artifacts = {
        "project": (json.dumps({"feature_name": "Safe Feature Name"}), now),
        "prd:broad": ("## Problem Statement\n\nBuild a safe public workflow exhibit.", now),
        "public-summary": (
            json.dumps({
                "title": "Unsafe",
                "description": "Leaked path /Users/danielzhang/src/iriai/.iriai/artifacts/private",
            }),
            now,
        ),
    }

    exhibit = dashboard._assemble_public_exhibit(
        feat=feat,
        artifacts=artifacts,
        timeline=[],
        events=[],
        agent_activity={"active_agents": [], "recent_agents": []},
        dag_info=None,
        groups=[],
        workstreams=[],
        dag_repair=None,
        active_gate=None,
        gates={},
        last_activity_at=now,
    )

    assert exhibit["public_summary"]["source"] == "deterministic-fallback"
    assert exhibit["public_summary"]["title"] == "Safe Feature Name"
    assert "/Users/" not in json.dumps(exhibit)
    assert ".iriai" not in json.dumps(exhibit)


def test_agent_activity_uses_unmatched_starts_not_stale_starts():
    now = datetime.now(timezone.utc)
    events = [
        {
            "event_type": "agent_start",
            "source": "implementer-old",
            "content": "claude_pool",
            "created_at": now - timedelta(minutes=6),
        },
        {
            "event_type": "agent_done",
            "source": "implementer-old",
            "content": "claude_pool complete",
            "created_at": now - timedelta(minutes=5),
        },
        {
            "event_type": "agent_start",
            "source": "verifier-live",
            "content": "codex",
            "created_at": now - timedelta(minutes=1),
        },
    ]

    activity = dashboard._assemble_agent_activity(events)

    assert [agent["name"] for agent in activity["active_agents"]] == ["verifier-live"]
    assert activity["active_agents"][0]["runtime"] == "Codex"
    assert activity["recent_agents"][0]["name"] == "implementer-old"


def test_agent_activity_enriches_current_dag_context():
    now = datetime.now(timezone.utc)
    events = [
        {
            "event_type": "agent_start",
            "source": "implementer-dag-g26-r0-fix-active-project-contract",
            "content": "claude_pool",
            "created_at": now - timedelta(minutes=3),
        },
    ]
    groups = [{
        "index": 26,
        "status": "active",
        "tasks": [{
            "id": "TASK-26-01",
            "name": "Repair active project contract",
            "summary": "Keep active project lifecycle behavior aligned with the canonical contract.",
            "repo_path": "iriai-studio",
            "file_scope": [{"path": "iriai-studio/src/projectStore.ts", "action": "update"}],
        }],
    }]
    artifacts = {
        "dag-repair-rca:g26:active-project-contract:retry-0": (
            json.dumps({"summary": "Root cause identified in ProjectStore active-state handling."}),
            now.isoformat(),
        ),
    }

    activity = dashboard._assemble_agent_activity(events, artifacts=artifacts, groups=groups)
    agent = activity["active_agents"][0]

    assert agent["group_idx"] == 26
    assert agent["task_id"] == "TASK-26-01"
    assert "active project lifecycle" in agent["prompt_preview"]
    assert agent["related_files"] == ["iriai-studio/src/projectStore.ts"]
    assert agent["related_artifact_keys"] == [
        "dag-repair-rca:g26:active-project-contract:retry-0"
    ]


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


@pytest.mark.asyncio
async def test_serve_bugflow_proof_reads_storage_stage_suffix(tmp_path: Path):
    feature_root = tmp_path / ".iriai" / "features" / "bugflow-abcd1234"
    proof_file = feature_root / "proof" / "BR-12" / "promotion-verify-deadbeef" / "index.html"
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
        response = await dashboard.serve_bugflow_proof(
            "abcd1234",
            "BR-12",
            "promotion-verify-deadbeef",
            "index.html",
        )
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


def test_derive_bugflow_status_text_prefers_proof_capture_retry():
    text = dashboard._derive_bugflow_status_text(
        {
            "promotion_status_text": "",
            "recovering_lane_ids": [],
            "stalled_lane_ids": [],
            "proof_capture_retry_lane_ids": ["L-proof"],
            "strategy_pending_cluster_ids": [],
        },
        active_lanes=[],
        active_report=None,
        promoting_lane=None,
        counts={},
    )

    assert text == "Recapturing promotion proof for lanes: L-proof"


def test_derive_bugflow_health_treats_proof_capture_retry_as_degraded():
    health = dashboard._derive_bugflow_health(
        {
            "recovering_lane_ids": [],
            "stalled_lane_ids": [],
            "proof_capture_retry_lane_ids": ["L-proof"],
            "strategy_pending_cluster_ids": [],
        },
        reports=[],
        active_lanes=[],
        promoting_lane=None,
        active_report=None,
        counts={"blocked": 1},
    )

    assert health == "degraded"
