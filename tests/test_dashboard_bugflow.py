from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard
from dashboard import BridgeManager, _assemble_bugflow_response, _parse_timeline_entry
from iriai_build_v2.runtime_policy import PRIMARY_IMPL_SECONDARY_REVIEW_POLICY
from iriai_build_v2.workflows.develop.execution.snapshots import SnapshotBudget


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
        "concurrency_max": 2,
    })

    cmd = manager._build_cmd()

    assert "--agent-runtime" in cmd
    assert cmd[cmd.index("--agent-runtime") + 1] == "claude_pool"
    assert "--runtime-policy" in cmd
    assert cmd[cmd.index("--runtime-policy") + 1] == PRIMARY_IMPL_SECONDARY_REVIEW_POLICY
    assert "--concurrency-max" in cmd
    assert cmd[cmd.index("--concurrency-max") + 1] == "2"


def test_bridge_manager_caps_huge_log_lines(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dashboard, "BRIDGE_LOG_LINE_CHARS", 20)
    manager = BridgeManager({"channel": "C123"})

    manager._append_line("x" * 100)

    assert manager.line_count == 1
    assert manager.truncated_line_count == 1
    assert len(manager.lines[0]) < 100
    assert "truncated bridge log line" in manager.lines[0]


@pytest.mark.asyncio
async def test_bridge_logs_response_is_bounded(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dashboard, "BRIDGE_LOG_RESPONSE_LINES", 2)
    original_bridge = dashboard.bridge
    manager = BridgeManager({"channel": "C123"})
    for idx in range(5):
        manager._append_line(f"line-{idx}")
    dashboard.bridge = manager
    try:
        payload = await dashboard.bridge_logs(after=0)
    finally:
        dashboard.bridge = original_bridge

    assert payload["cursor"] == 5
    assert payload["earliest_cursor"] == 0
    assert payload["dropped_line_count"] == 3
    assert payload["lines"] == ["line-3", "line-4"]
    assert payload["response_line_cap"] == 2


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


def test_dashboard_artifact_preview_reads_bounded_spill_slice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    body = "large implementation report\n" * 100
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    spill_path = tmp_path / "feature-spill" / f"{digest}.txt"
    spill_path.parent.mkdir(parents=True)
    spill_path.write_text(body, encoding="utf-8")
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_DIR", str(tmp_path))
    envelope = {
        "__iriai_spill_v1__": True,
        "feature_id": "feature-spill",
        "path": f"feature-spill/{digest}.txt",
        "sha256": digest,
        "bytes": len(body.encode("utf-8")),
        "chars": len(body),
        "content_type": "text/plain",
        "created_at": "2026-05-21T02:00:00Z",
        "policy": "lossless_spill",
    }
    now = datetime.now(timezone.utc)

    row = {
        "key": "implementation",
        "value": json.dumps(envelope)[:200],
        "content_ref": json.dumps(envelope),
        "total_chars": len(json.dumps(envelope)),
        "stored_bytes": len(json.dumps(envelope).encode("utf-8")),
        "value_truncated": True,
        "created_at": now,
    }

    preview = dashboard._dashboard_artifact_preview_row(row, 80)

    assert preview["key"] == "implementation"
    assert preview["value"] == body[:80]
    assert "__iriai_spill_v1__" not in preview["value"]
    assert preview["total_chars"] == len(body)
    assert preview["stored_bytes"] == len(body.encode("utf-8"))
    assert preview["value_truncated"] is True
    assert preview["created_at"] == now


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


@pytest.mark.asyncio
async def test_get_feature_control_plane_uses_live_connection_and_shows_post_dag_gates():
    # Slice-10b remediation (P2-10b-1): doc 10 step 6 makes the typed
    # `ControlPlaneSnapshot` the single control-plane contract — the
    # `/api/feature/{id}` response embeds the COMPACT TYPED `control_plane`
    # object. This test still proves the embed uses the LIVE connection and
    # that post-DAG gates render; the control-plane assertions are updated to
    # the typed compact shape (every still-applicable assertion is kept and
    # typed-shape assertions added — not weakened).
    now = datetime(2026, 5, 20, tzinfo=timezone.utc)

    class _Conn:
        def __init__(self) -> None:
            self.live = False
            self.control_plane_fetch_count = 0

        async def fetchrow(self, query: str, *args: object):
            assert self.live
            if "SELECT f.updated_at" in query:
                return {
                    "updated_at": now,
                    "max_art": 10,
                    "max_evt": 0,
                    "last_activity_at": now,
                }
            if "FROM features WHERE id = $1" in query:
                return {
                    "id": "feat-dashboard-control",
                    "name": "Dashboard Control",
                    "phase": "implementation",
                    "workflow_name": "full-develop",
                    "updated_at": now,
                    "metadata": {},
                }
            raise AssertionError(query)

        async def fetch(self, query: str, *args: object):
            assert self.live
            # Snapshot-version cursors — cheap MAX() aggregates.
            if "MAX(" in query and " AS max_id" in query:
                self.control_plane_fetch_count += 1
                return [{"max_id": 100, "max_updated": now}]
            if "FROM execution_journal_rows" in query:
                self.control_plane_fetch_count += 1
                return [{
                    "id": 41,
                    "feature_id": "feat-dashboard-control",
                    "dag_sha256": "d" * 64,
                    "entry_type": "dispatch_attempt",
                    "status": "started",
                    "dispatcher_state": "runtime_invoking",
                    "actor": "implementer",
                    "runtime": "codex",
                    "group_idx": 0,
                    "task_id": "TASK-1",
                    "request_digest": "req",
                    "retry": "0",
                    "attempt_no": "1",
                    "workspace_snapshot_id": None,
                    "workspace_snapshot_ids": [],
                    "created_at": now,
                    "updated_at": now,
                }]
            if "SELECT DISTINCT ON (key) key, substring" in query:
                rows = [
                    {"key": "dag", "value": json.dumps({"tasks": [], "execution_order": [[]]}), "created_at": now},
                    {"key": "dag-group:0", "value": "{}", "created_at": now},
                    {"key": "dag-gate:source-push", "value": "ok", "created_at": now},
                    {"key": "dag-gate:implementation-report", "value": "ok", "created_at": now},
                    {"key": "dag-gate:notify", "value": "ok", "created_at": now},
                ]
                return rows
            return []

    class _Acquire:
        def __init__(self, conn: _Conn) -> None:
            self.conn = conn

        async def __aenter__(self):
            self.conn.live = True
            return self.conn

        async def __aexit__(self, exc_type, exc, tb):
            self.conn.live = False
            return False

    conn = _Conn()
    original_pool = dashboard.pool
    dashboard._response_cache.clear()
    dashboard.pool = SimpleNamespace(acquire=lambda: _Acquire(conn))
    try:
        response = await dashboard.get_feature("feat-dashboard-control", SimpleNamespace(headers={}))
    finally:
        dashboard.pool = original_pool
        dashboard._response_cache.clear()

    payload = json.loads(response.body.decode("utf-8"))
    # The typed control-plane embed used the live connection (version cursors
    # + the typed attempt read both ran against it).
    assert conn.control_plane_fetch_count >= 2
    # The single typed compact `control_plane` object.
    control_plane = payload["control_plane"]
    assert control_plane["schema"] == "typed"
    assert isinstance(control_plane["snapshot_version"], str)
    assert len(control_plane["snapshot_version"]) == 64
    assert control_plane["source"] == "typed"
    # The typed attempt row renders from the typed snapshot.
    assert control_plane["counts"]["active_attempts"] == 1
    assert control_plane["active_attempts"][0]["attempt_id"] == 41
    assert control_plane["active_attempts"][0]["task_id"] == "TASK-1"
    # The compact typed object carries no artifact-body field.
    assert '"value"' not in json.dumps(control_plane)
    # Post-DAG gates still render.
    assert payload["gates"]["source-push"] is True
    assert payload["gates"]["implementation-report"] is True
    assert payload["gates"]["notify"] is True


@pytest.mark.asyncio
async def test_get_feature_legacy_schema_falls_back_when_typed_tables_are_absent():
    now = datetime(2026, 5, 20, tzinfo=timezone.utc)

    class _Conn:
        async def fetchrow(self, query: str, *args: object):
            if "SELECT f.updated_at" in query:
                assert "execution_journal_rows" not in query
                assert "evidence_nodes" not in query
                assert "sandbox_leases" not in query
                return {
                    "updated_at": now,
                    "max_art": 0,
                    "max_evt": 0,
                    "last_activity_at": None,
                }
            if "FROM features WHERE id = $1" in query:
                return {
                    "id": "feat-legacy",
                    "name": "Legacy Feature",
                    "phase": "implementation",
                    "workflow_name": "full-develop",
                    "updated_at": now,
                    "metadata": {},
                }
            raise AssertionError(query)

        async def fetch(self, query: str, *args: object):
            if any(
                table in query
                for table in (
                    "execution_journal_rows",
                    "evidence_nodes",
                    "workspace_snapshots",
                    "sandbox_leases",
                    "execution_artifact_projections",
                )
            ):
                raise RuntimeError("relation does not exist")
            return []

    class _Acquire:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    conn = _Conn()
    original_pool = dashboard.pool
    dashboard._response_cache.clear()
    dashboard.pool = SimpleNamespace(acquire=lambda: _Acquire())
    try:
        response = await dashboard.get_feature("feat-legacy", SimpleNamespace(headers={}))
    finally:
        dashboard.pool = original_pool
        dashboard._response_cache.clear()

    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["id"] == "feat-legacy"
    # Slice-10b remediation: the embedded `control_plane` is the typed compact
    # object. When the typed tables are absent the Slice-10a builder degrades
    # every section and reports `source="legacy_fallback"` / `degraded=True` —
    # the dashboard surfaces that degraded state without raising.
    assert payload["control_plane"]["source"] == "legacy_fallback"
    assert payload["control_plane"]["degraded"] is True


@pytest.mark.asyncio
async def test_get_feature_etag_changes_for_material_sandbox_lease_updates():
    # Slice-10b remediation: the `/api/feature/{id}` ETag folds the TYPED
    # snapshot version (doc 10 step 6 — the single control-plane contract). A
    # material sandbox-lease update advances the typed `sandbox_leases`
    # version cursor (`MAX(id)`/`MAX(updated_at)`), so a control-plane-only
    # change still invalidates the ETag without an artifact/event write.
    now = datetime(2026, 5, 20, tzinfo=timezone.utc)
    later = datetime(2026, 5, 20, 1, tzinfo=timezone.utc)

    class _Conn:
        def __init__(self) -> None:
            self.lease_status = "running"
            self.lease_version = 0
            # A material lease update moves the row's `updated_at` forward —
            # the typed version cursor over `sandbox_leases` reflects it.
            self.lease_updated_at = now

        async def fetchrow(self, query: str, *args: object):
            if "SELECT f.updated_at" in query:
                return {
                    "updated_at": now,
                    "max_art": 0,
                    "max_evt": 0,
                    "last_activity_at": None,
                }
            if "FROM features WHERE id = $1" in query:
                return {
                    "id": "feat-sandbox-etag",
                    "name": "Sandbox ETag",
                    "phase": "implementation",
                    "workflow_name": "full-develop",
                    "updated_at": now,
                    "metadata": {},
                }
            raise AssertionError(query)

        async def fetch(self, query: str, *args: object):
            # Snapshot-version cursors — the `sandbox_leases` cursor reflects
            # the lease's mutable `updated_at`; other cursors are static.
            if "MAX(" in query and " AS max_id" in query:
                if "FROM sandbox_leases" in query:
                    return [{"max_id": 261, "max_updated": self.lease_updated_at}]
                return [{"max_id": 0, "max_updated": None}]
            if "FROM sandbox_leases" in query:
                return [{
                    "id": 261,
                    "feature_id": "feat-sandbox-etag",
                    "dag_sha256": "d" * 64,
                    "group_idx": 6,
                    "mode": "task",
                    "status": self.lease_status,
                    "lease_owner": "implementer",
                    "leased_until": now,
                    "lease_version": self.lease_version,
                    "sandbox_root": "/sandbox",
                    "patch_summary_ids": [],
                    "created_at": now,
                    "updated_at": self.lease_updated_at,
                }]
            return []

    class _Acquire:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    conn = _Conn()
    original_pool = dashboard.pool
    dashboard._response_cache.clear()
    dashboard.pool = SimpleNamespace(acquire=lambda: _Acquire())
    try:
        first = await dashboard.get_feature("feat-sandbox-etag", SimpleNamespace(headers={}))
        first_etag = first.headers["etag"]
        conn.lease_status = "retained"
        conn.lease_version = 1
        conn.lease_updated_at = later
        second = await dashboard.get_feature(
            "feat-sandbox-etag",
            SimpleNamespace(headers={"if-none-match": first_etag}),
        )
    finally:
        dashboard.pool = original_pool
        dashboard._response_cache.clear()

    assert second.status_code == 200
    assert second.headers["etag"] != first_etag
    payload = json.loads(second.body.decode("utf-8"))
    assert payload["control_plane"]["snapshot_version"]


@pytest.mark.asyncio
async def test_control_plane_endpoint_returns_bounded_typed_snapshot_without_artifact_bodies():
    # Slice-10b remediation (P2-10b-1): doc 10 § "Refactoring Steps" step 6 —
    # the typed `ControlPlaneSnapshot` IS the `/api/feature/{id}/control-plane`
    # endpoint. This test exercises that endpoint (`get_feature_control_plane`)
    # and proves it serves the bounded typed snapshot WITHOUT any artifact-body
    # read; the ETag is the typed snapshot version digest. (The dict-based
    # pre-Slice-10 snapshot it superseded is gone — its rich assertions had no
    # shipped consumer; the typed contract carries its own bounded/no-body
    # guarantees, asserted here and in the typed-route tests below.)
    conn = _TypedControlPlaneConn()
    original_pool = dashboard.pool
    dashboard.pool = _typed_cp_pool(conn)
    try:
        response = await dashboard.get_feature_control_plane(
            "feat-typed-cp", SimpleNamespace(headers={})
        )
    finally:
        dashboard.pool = original_pool

    payload = json.loads(response.body.decode("utf-8"))
    # The typed `ControlPlaneSnapshot` contract — typed rows only.
    assert payload["feature_id"] == "feat-typed-cp"
    assert isinstance(payload["snapshot_version"], str)
    assert len(payload["snapshot_version"]) == 64
    assert payload["source"] == "typed"
    # The ETag is the typed snapshot version digest.
    assert response.headers["etag"] == f'"control-plane:{payload["snapshot_version"]}"'
    # Typed panels render from typed rows (not dict-based / artifact inference).
    assert payload["active_attempts"][0]["attempt_id"] == 41
    assert payload["latest_failures"][0]["failure_class"] == "verifier_context"
    assert payload["latest_failures"][0]["route"] == "retry_verifier"
    assert payload["merge_queue"][0]["item_id"] == 77
    assert payload["gates"][0]["gate_name"] == "source-push"
    assert payload["checkpoints"][0]["table"] == "evidence_nodes"
    # Detail panes fetch bounded slices by EvidenceRef id — never a body.
    failure_refs = payload["latest_failures"][0]["evidence_refs"]
    assert all("body" not in ref and "value" not in ref for ref in failure_refs)
    # The typed snapshot endpoint reads NO artifact body.
    assert conn.artifact_value_reads == []
    blob = json.dumps(payload)
    assert '"value"' not in blob
    assert '"content"' not in blob


@pytest.mark.asyncio
async def test_control_plane_endpoint_rejects_negative_group_idx():
    # Slice-10b remediation (P3-10b-2): a negative `group_idx` is rejected with
    # a 422 rather than silently returning an empty group-scoped snapshot.
    conn = _TypedControlPlaneConn()
    original_pool = dashboard.pool
    dashboard.pool = _typed_cp_pool(conn)
    try:
        with pytest.raises(dashboard.HTTPException) as exc_info:
            await dashboard.get_feature_control_plane(
                "feat-typed-cp", SimpleNamespace(headers={}), group_idx=-1
            )
    finally:
        dashboard.pool = original_pool

    assert exc_info.value.status_code == 422



@pytest.mark.asyncio
async def test_get_feature_bounds_event_content_in_sql():
    now = datetime(2026, 5, 11, tzinfo=timezone.utc)

    class _Conn:
        def __init__(self) -> None:
            self.fetches: list[tuple[str, tuple[object, ...]]] = []

        async def fetchrow(self, query: str, *args: object):
            if "SELECT f.updated_at" in query:
                return {
                    "updated_at": now,
                    "max_art": 0,
                    "max_evt": 1,
                    "last_activity_at": now,
                }
            if "FROM features WHERE id = $1" in query:
                return {
                    "id": "feat-dashboard-events",
                    "name": "Dashboard Events",
                    "phase": "implementation",
                    "workflow_name": "full-develop",
                    "updated_at": now,
                    "metadata": {},
                }
            raise AssertionError(query)

        async def fetch(self, query: str, *args: object):
            self.fetches.append((query, args))
            if "FROM events" in query:
                return [
                    {
                        "event_type": "agent_start",
                        "source": "implementer-g44-t1",
                        "content": "x" * dashboard.DASHBOARD_EVENT_PREVIEW_CHARS,
                        "content_bytes": 50_000,
                        "content_truncated": True,
                        "created_at": now,
                    }
                ]
            return []

    class _Acquire:
        def __init__(self, conn: _Conn) -> None:
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    conn = _Conn()
    original_pool = dashboard.pool
    dashboard._response_cache.clear()
    dashboard.pool = SimpleNamespace(acquire=lambda: _Acquire(conn))
    try:
        response = await dashboard.get_feature(
            "feat-dashboard-events",
            SimpleNamespace(headers={}),
        )
    finally:
        dashboard.pool = original_pool
        dashboard._response_cache.clear()

    payload = json.loads(response.body.decode("utf-8"))
    assert payload["events"][0]["content_bytes"] == 50_000
    assert payload["events"][0]["content_truncated"] is True
    event_sql, event_args = next(
        (sql, args) for sql, args in conn.fetches if "FROM events" in sql
    )
    assert "substring(content from 1 for $2) AS content" in event_sql
    assert "pg_column_size(content)" in event_sql
    assert "content_truncated" in event_sql
    assert event_args == ("feat-dashboard-events", dashboard.DASHBOARD_EVENT_PREVIEW_CHARS)


@pytest.mark.asyncio
async def test_get_feature_uses_smaller_timeline_preview_cap():
    now = datetime(2026, 5, 11, tzinfo=timezone.utc)

    class _Conn:
        def __init__(self) -> None:
            self.fetches: list[tuple[str, tuple[object, ...]]] = []

        async def fetchrow(self, query: str, *args: object):
            if "SELECT f.updated_at" in query:
                return {
                    "updated_at": now,
                    "max_art": 1,
                    "max_evt": 0,
                    "last_activity_at": now,
                }
            if "FROM features WHERE id = $1" in query:
                return {
                    "id": "feat-dashboard-artifacts",
                    "name": "Dashboard Artifacts",
                    "phase": "implementation",
                    "workflow_name": "full-develop",
                    "updated_at": now,
                    "metadata": {},
                }
            raise AssertionError(query)

        async def fetch(self, query: str, *args: object):
            self.fetches.append((query, args))
            return []

    class _Acquire:
        def __init__(self, conn: _Conn) -> None:
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    conn = _Conn()
    original_pool = dashboard.pool
    dashboard._response_cache.clear()
    dashboard.pool = SimpleNamespace(acquire=lambda: _Acquire(conn))
    try:
        await dashboard.get_feature("feat-dashboard-artifacts", SimpleNamespace(headers={}))
    finally:
        dashboard.pool = original_pool
        dashboard._response_cache.clear()

    latest_sql, latest_args = next(
        (sql, args)
        for sql, args in conn.fetches
        if "SELECT DISTINCT ON (key)" in sql and "CASE WHEN" in sql
    )
    timeline_sql, timeline_args = next(
        (sql, args) for sql, args in conn.fetches if "ORDER BY created_at DESC LIMIT $3" in sql
    )
    assert "CASE WHEN" in latest_sql
    assert "key LIKE 'dag%'" in latest_sql
    assert "key LIKE 'workspace-authority-%'" in latest_sql
    assert "key LIKE 'runtime-workspace-binding:%'" in latest_sql
    assert "substring(value from 1" in latest_sql
    assert "key LIKE 'dag-task-contract:%'" in timeline_sql
    assert "key LIKE 'dag-contract-verdict:%'" in timeline_sql
    assert "key LIKE 'dag-sandbox-patch:%'" in timeline_sql
    assert "key LIKE 'workspace-authority-%'" in timeline_sql
    assert "key LIKE 'runtime-workspace-binding:%'" in timeline_sql
    assert "substring(value from 1 for $2)" in timeline_sql
    assert latest_args == (
        "feat-dashboard-artifacts",
        dashboard.DASHBOARD_STRUCTURED_ARTIFACT_PREVIEW_CHARS,
        dashboard.DASHBOARD_ARTIFACT_PREVIEW_CHARS,
    )
    assert timeline_args == (
        "feat-dashboard-artifacts",
        dashboard.DASHBOARD_TIMELINE_PREVIEW_CHARS,
        dashboard.DASHBOARD_TIMELINE_ROWS,
    )


@pytest.mark.asyncio
async def test_get_feature_does_not_parse_truncated_dag_preview_as_complete():
    now = datetime(2026, 5, 11, tzinfo=timezone.utc)

    class _Conn:
        def __init__(self) -> None:
            self.fetches: list[tuple[str, tuple[object, ...]]] = []

        async def fetchrow(self, query: str, *args: object):
            if "SELECT f.updated_at" in query:
                return {
                    "updated_at": now,
                    "max_art": 2,
                    "max_evt": 0,
                    "max_exec": 0,
                    "max_evidence": 0,
                    "last_activity_at": now,
                }
            if "FROM features WHERE id = $1" in query:
                return {
                    "id": "feat-truncated-dag",
                    "name": "Truncated DAG",
                    "phase": "implementation",
                    "workflow_name": "full-develop",
                    "updated_at": now,
                    "metadata": {},
                }
            raise AssertionError(query)

        async def fetch(self, query: str, *args: object):
            self.fetches.append((query, args))
            if "SELECT DISTINCT ON (key) key," in query and "substring(value" in query:
                return [
                    {
                        "key": "dag",
                        "value": json.dumps({
                            "tasks": [{"id": "T1", "name": "Preview task"}],
                            "execution_order": [["T1"]],
                        }),
                        "total_chars": 250_000,
                        "stored_bytes": 250_000,
                        "value_truncated": True,
                        "created_at": now,
                    },
                    {
                        "key": "dag:strategy",
                        "value": json.dumps({
                            "workstreams": [{
                                "id": "ws1",
                                "name": "Preview workstream",
                                "subfeature_slugs": [],
                            }]
                        }),
                        "total_chars": 250_000,
                        "stored_bytes": 250_000,
                        "value_truncated": True,
                        "created_at": now,
                    },
                ]
            return []

    class _Acquire:
        def __init__(self, conn: _Conn) -> None:
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    conn = _Conn()
    original_pool = dashboard.pool
    dashboard._response_cache.clear()
    dashboard.pool = SimpleNamespace(acquire=lambda: _Acquire(conn))
    try:
        response = await dashboard.get_feature("feat-truncated-dag", SimpleNamespace(headers={}))
    finally:
        dashboard.pool = original_pool
        dashboard._response_cache.clear()

    payload = json.loads(response.body.decode("utf-8"))
    assert payload["dag"] is None
    assert payload["groups"] == []
    assert payload["workstreams"] == []


# ── Slice 10b — typed control-plane snapshot wiring ─────────────────────────
#
# These tests cover the Slice-10b dashboard views: the new typed bounded
# `/api/feature/{id}/control-plane` route (doc 10 step 6 — the typed
# snapshot IS the control-plane endpoint), the embedded compact
# `control_plane` object in `/api/feature/{id}`, and the ETag
# composition extended with the typed snapshot version. They mirror the
# existing dashboard `_Conn` fake pattern. The typed snapshot path's store
# (`ExecutionControlStore`) tolerates a fake connection with only
# `fetch`/`fetchrow` (no `execute`/`transaction`) — `_set_local_statement_
# timeout` no-ops without `execute` and `_transaction` uses `nullcontext()`.


_TYPED_SNAPSHOT_NOW = datetime(2026, 5, 21, tzinfo=timezone.utc)


class _TypedControlPlaneConn:
    """Fake connection serving the Slice-10a typed-snapshot builder SQL.

    `version_bump` controls the snapshot-version `MAX(id)` aggregates so a
    test can simulate a control-plane-only change advancing the version. It
    asserts NO artifact-body (`FROM artifacts ... value`) read is issued.
    """

    def __init__(self, *, version_bump: int = 0, attempts: int = 1) -> None:
        self.version_bump = version_bump
        self.attempt_count = attempts
        self.artifact_value_reads: list[str] = []
        self.fetched_tables: list[str] = []

    async def fetchrow(self, query: str, *args: object):
        if "SELECT id FROM features" in query:
            return {"id": "feat-typed-cp"}
        if "SELECT f.updated_at" in query:
            return {
                "updated_at": _TYPED_SNAPSHOT_NOW,
                "max_art": 3,
                "max_evt": 2,
                "last_activity_at": _TYPED_SNAPSHOT_NOW,
            }
        if "FROM features WHERE id = $1" in query:
            return {
                "id": "feat-typed-cp",
                "name": "Typed Control Plane",
                "phase": "implementation",
                "workflow_name": "full-develop",
                "updated_at": _TYPED_SNAPSHOT_NOW,
                "metadata": {},
            }
        raise AssertionError(query)

    async def fetch(self, query: str, *args: object):
        now = _TYPED_SNAPSHOT_NOW
        # Hard guard: the typed snapshot must never read an artifact body.
        if "FROM artifacts" in query and "value" in query.lower():
            self.artifact_value_reads.append(query)
        # Snapshot-version cursors — eight cheap MAX() aggregates.
        if "MAX(" in query and " AS max_id" in query:
            return [{"max_id": 100 + self.version_bump, "max_updated": now}]
        if "FROM execution_journal_rows" in query:
            self.fetched_tables.append("execution_journal_rows")
            rows = []
            for idx in range(self.attempt_count):
                rows.append({
                    "id": 41 + idx,
                    "feature_id": "feat-typed-cp",
                    "dag_sha256": "d" * 64,
                    "entry_type": "dispatch_attempt",
                    "status": "started",
                    "dispatcher_state": "runtime_invoking",
                    "actor": "implementer",
                    "runtime": "codex",
                    "group_idx": 6,
                    "task_id": f"TASK-{idx}",
                    "request_digest": "req",
                    "retry": "1",
                    "attempt_no": "1",
                    "workspace_snapshot_id": "251",
                    "workspace_snapshot_ids": [251],
                    "created_at": now,
                    "updated_at": now,
                })
            return rows
        if "FROM workspace_snapshots" in query:
            self.fetched_tables.append("workspace_snapshots")
            return [{
                "id": 251,
                "attempt_id": 41,
                "group_idx": 6,
                "repo_id": "app",
                "role": "primary",
                "canonical_path": "/workspace/app",
                "workspace_relative_path": "app",
                "stage": "pre-dispatch",
                "head_sha": "head-1",
                "index_digest": "idx-1",
                "worktree_status_digest": "wt-1",
                "no_dirty": True,
                "safety_status": "ok",
                "dirty_path_count": 0,
                "dirty_paths": [],
                "forbidden_path_count": 0,
                "forbidden_paths": [],
                "captured_at": now,
                "created_at": now,
            }]
        if "FROM merge_queue_items" in query:
            self.fetched_tables.append("merge_queue_items")
            return [{
                "id": 77,
                "feature_id": "feat-typed-cp",
                "dag_sha256": "d" * 64,
                "group_idx": 6,
                "repo_id": "app",
                "status": "leased",
                "priority": 0,
                "lease_owner": "merge-worker",
                "leased_until": now,
                "lease_version": 2,
                "result_commit": "",
                "failure_id": None,
                "gate_evidence_ids": [901],
                "updated_at": now,
            }]
        if "FROM sandbox_leases" in query:
            self.fetched_tables.append("sandbox_leases")
            return [{
                "id": 261,
                "feature_id": "feat-typed-cp",
                "dag_sha256": "d" * 64,
                "group_idx": 6,
                "mode": "task",
                "status": "running",
                "sandbox_root": "/sandbox",
                "patch_summary_ids": [501],
                "leased_until": now,
                "updated_at": now,
            }]
        if "FROM runtime_workspace_bindings" in query:
            self.fetched_tables.append("runtime_workspace_bindings")
            return [{
                "id": 311,
                "sandbox_lease_id": 261,
                "attempt_id": 41,
                "runtime_name": "codex",
                "status": "bound",
                "cwd": "/sandbox/app",
                "updated_at": now,
            }]
        if "FROM evidence_nodes" in query:
            kinds = set(args[1]) if len(args) > 1 and isinstance(args[1], list) else set()
            if "runtime_failure_context" in kinds:
                self.fetched_tables.append("typed_failures")
                return [{
                    "id": 88,
                    "attempt_id": 41,
                    "group_idx": 6,
                    "kind": "runtime_failure_context",
                    "status": "blocked",
                    "deterministic": True,
                    "failure_class": "verifier_context",
                    "failure_type": "context_materialization_failed",
                    "severity": "error",
                    "operator_required": "false",
                    "retryable": "true",
                    "route": "retry_verifier",
                    "signature_hash": "sig-1",
                    "summary": "retry verifier context",
                    "artifact_id": 120,
                    "artifact_key": "dag-verify-rca:g6:retry-1",
                    "event_id": 400,
                    "created_at": now,
                    "finished_at": None,
                    "updated_at": now,
                }]
            if "checkpoint_gate" in kinds and "merge_proof" in kinds:
                self.fetched_tables.append("checkpoints")
                return [{
                    "id": 905,
                    "kind": "merge_proof",
                    "group_idx": 5,
                    "status": "approved",
                    "summary": "merge proof for group 5",
                    "artifact_key": "dag-group:5",
                }]
            # gate kinds
            self.fetched_tables.append("gates")
            return [{
                "id": 901,
                "group_idx": 6,
                "kind": "merge_gate",
                "name": "source-push",
                "stage": "source-push",
                "status": "approved",
                "deterministic": True,
                "failure_id": None,
                "created_at": now,
            }]
        return []


def _typed_cp_pool(conn: _TypedControlPlaneConn):
    class _Acquire:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    return SimpleNamespace(acquire=lambda: _Acquire())


@pytest.mark.asyncio
async def test_typed_control_plane_route_returns_bounded_typed_snapshot():
    conn = _TypedControlPlaneConn()
    original_pool = dashboard.pool
    dashboard.pool = _typed_cp_pool(conn)
    try:
        response = await dashboard.get_feature_control_plane(
            "feat-typed-cp", SimpleNamespace(headers={})
        )
    finally:
        dashboard.pool = original_pool

    payload = json.loads(response.body.decode("utf-8"))
    # The typed `ControlPlaneSnapshot` contract — typed rows, no artifact body.
    assert payload["feature_id"] == "feat-typed-cp"
    assert isinstance(payload["snapshot_version"], str)
    assert len(payload["snapshot_version"]) == 64
    assert payload["source"] == "typed"
    assert response.headers["etag"] == (
        f'"control-plane:{payload["snapshot_version"]}"'
    )
    # Every typed panel renders from typed rows.
    assert payload["active_attempts"][0]["attempt_id"] == 41
    assert payload["active_attempts"][0]["attempt_kind"] == "task"
    assert payload["workspace_snapshots"][0]["snapshot_id"] == 251
    assert payload["latest_failures"][0]["failure_class"] == "verifier_context"
    assert payload["latest_failures"][0]["route"] == "retry_verifier"
    assert payload["merge_queue"][0]["item_id"] == 77
    assert payload["sandbox_leases"][0]["lease_id"] == 261
    assert payload["runtime_bindings"][0]["binding_id"] == 311
    assert payload["gates"][0]["gate_name"] == "source-push"
    assert payload["checkpoints"][0]["table"] == "evidence_nodes"
    assert payload["checkpoints"][0]["id"] == 905
    # Detail panes fetch bounded slices by EvidenceRef id — the snapshot
    # carries the cited id, never the artifact body.
    failure_refs = payload["latest_failures"][0]["evidence_refs"]
    assert {ref["table"] for ref in failure_refs} == {"artifacts", "events"}
    assert all("body" not in ref and "value" not in ref for ref in failure_refs)
    # The typed snapshot must NOT read an artifact body.
    assert conn.artifact_value_reads == []
    # No "value"/"content" body field anywhere in the serialized snapshot.
    blob = json.dumps(payload)
    assert '"value"' not in blob
    assert '"content"' not in blob


@pytest.mark.asyncio
async def test_typed_control_plane_route_accepts_bounded_query_params():
    conn = _TypedControlPlaneConn()
    original_pool = dashboard.pool
    dashboard.pool = _typed_cp_pool(conn)
    try:
        response = await dashboard.get_feature_control_plane(
            "feat-typed-cp",
            SimpleNamespace(headers={}),
            group_idx=6,
            scope="supervisor",
            include_terminal_groups=True,
            after_snapshot_version="prior-version",
        )
    finally:
        dashboard.pool = original_pool

    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["feature_id"] == "feat-typed-cp"
    # The bounded query is honoured — group-scoped reads still bounded + typed.
    assert payload["active_attempts"][0]["group_idx"] == 6


def test_typed_snapshot_query_clamps_budget_and_normalises_scope():
    # An unknown scope falls back to "dashboard" rather than 500ing.
    query = dashboard._typed_snapshot_query("feat-typed-cp", scope="bogus")
    assert query.scope == "dashboard"
    # The query budget is the SnapshotBudget ceiling — a dashboard request
    # can never widen a store read.
    ceiling = SnapshotBudget()
    assert query.budget.max_attempts == ceiling.max_attempts
    assert query.budget.max_failures == ceiling.max_failures


@pytest.mark.asyncio
async def test_typed_control_plane_route_404_for_unknown_feature():
    class _Conn:
        async def fetchrow(self, query: str, *args: object):
            return None

    original_pool = dashboard.pool
    dashboard.pool = _typed_cp_pool(_Conn())
    try:
        with pytest.raises(dashboard.HTTPException) as exc_info:
            await dashboard.get_feature_control_plane(
                "missing-feature", SimpleNamespace(headers={})
            )
    finally:
        dashboard.pool = original_pool

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_typed_control_plane_route_304_when_etag_matches():
    conn = _TypedControlPlaneConn()
    original_pool = dashboard.pool
    dashboard.pool = _typed_cp_pool(conn)
    try:
        first = await dashboard.get_feature_control_plane(
            "feat-typed-cp", SimpleNamespace(headers={})
        )
        etag = first.headers["etag"]
        second = await dashboard.get_feature_control_plane(
            "feat-typed-cp", SimpleNamespace(headers={"if-none-match": etag})
        )
    finally:
        dashboard.pool = original_pool

    assert second.status_code == 304
    assert second.headers["etag"] == etag


@pytest.mark.asyncio
async def test_get_feature_embeds_compact_typed_control_plane_object():
    conn = _TypedControlPlaneConn()
    original_pool = dashboard.pool
    dashboard._response_cache.clear()
    dashboard.pool = _typed_cp_pool(conn)
    try:
        response = await dashboard.get_feature(
            "feat-typed-cp", SimpleNamespace(headers={})
        )
    finally:
        dashboard.pool = original_pool
        dashboard._response_cache.clear()

    payload = json.loads(response.body.decode("utf-8"))
    # Slice-10b remediation (P2-10b-1): doc 10 step 6 — `/api/feature/{id}`
    # embeds the SINGLE typed `control_plane` object (the dict-based embed is
    # superseded; there is one typed control-plane contract).
    typed = payload["control_plane"]
    assert typed["schema"] == "typed"
    assert typed["feature_id"] == "feat-typed-cp"
    assert len(typed["snapshot_version"]) == 64
    assert typed["source"] == "typed"
    # Compact: typed counts + bounded typed rows from the typed snapshot.
    assert typed["counts"]["active_attempts"] == 1
    assert typed["counts"]["merge_queue"] == 1
    assert typed["counts"]["gates"] == 1
    assert typed["active_attempts"][0]["attempt_id"] == 41
    assert typed["latest_failures"][0]["failure_class"] == "verifier_context"
    assert typed["checkpoints"][0]["id"] == 905
    # The embedded typed object is built from the bounded typed snapshot only:
    # it carries no `value`/`content` artifact-body field. (The `get_feature`
    # path still previews artifacts for its OWN DAG/timeline panels — that is
    # pre-existing and out of Slice-10b scope; the typed control-plane object
    # itself never hydrates an artifact body.)
    typed_blob = json.dumps(typed)
    assert '"value"' not in typed_blob
    assert '"content"' not in typed_blob


@pytest.mark.asyncio
async def test_get_feature_etag_includes_typed_control_plane_version():
    original_pool = dashboard.pool
    dashboard._response_cache.clear()
    try:
        conn_a = _TypedControlPlaneConn(version_bump=0)
        dashboard.pool = _typed_cp_pool(conn_a)
        first = await dashboard.get_feature(
            "feat-typed-cp", SimpleNamespace(headers={})
        )
        first_etag = first.headers["etag"]

        # A control-plane-only change (the typed snapshot version advances)
        # while the legacy artifact/event versions are byte-identical must
        # still invalidate the ETag.
        dashboard._response_cache.clear()
        conn_b = _TypedControlPlaneConn(version_bump=50)
        dashboard.pool = _typed_cp_pool(conn_b)
        second = await dashboard.get_feature(
            "feat-typed-cp",
            SimpleNamespace(headers={"if-none-match": first_etag}),
        )
    finally:
        dashboard.pool = original_pool
        dashboard._response_cache.clear()

    # The typed-only change refreshes the UI — ETag changed, full 200 body.
    assert second.status_code == 200
    assert second.headers["etag"] != first_etag
    second_payload = json.loads(second.body.decode("utf-8"))
    assert second_payload["control_plane"]["snapshot_version"]
    # The typed version component is part of the composed ETag.
    assert second_payload["control_plane"]["snapshot_version"] in (
        second.headers["etag"]
    )


@pytest.mark.asyncio
async def test_get_feature_etag_304_when_typed_version_unchanged():
    conn = _TypedControlPlaneConn()
    original_pool = dashboard.pool
    dashboard._response_cache.clear()
    dashboard.pool = _typed_cp_pool(conn)
    try:
        first = await dashboard.get_feature(
            "feat-typed-cp", SimpleNamespace(headers={})
        )
        etag = first.headers["etag"]
        # Nothing changed — same typed version, same artifact/event versions.
        second = await dashboard.get_feature(
            "feat-typed-cp",
            SimpleNamespace(headers={"if-none-match": etag}),
        )
    finally:
        dashboard.pool = original_pool
        dashboard._response_cache.clear()

    assert second.status_code == 304
    assert second.headers["etag"] == etag
