import json
from types import SimpleNamespace

import pytest
from iriai_compose import Ask

from iriai_build_v2.models.outputs import (
    ArtifactRepairResult,
    ArtifactRepairUpdate,
    BugGroup,
    BugTriage,
    Gap,
    ImplementationResult,
    ImplementationTask,
    Issue,
    RootCauseAnalysis,
    Verdict,
)
from iriai_build_v2.workflows.develop.phases import implementation as implementation_module


def test_dag_expanded_verify_env_defaults_on(monkeypatch):
    monkeypatch.delenv(implementation_module.DAG_EXPANDED_VERIFY_ENV, raising=False)

    assert implementation_module._dag_expanded_verify_enabled() is True


def test_dag_expanded_verify_env_kill_switch(monkeypatch):
    monkeypatch.setenv(implementation_module.DAG_EXPANDED_VERIFY_ENV, "0")

    assert implementation_module._dag_expanded_verify_enabled() is False


def test_dag_repair_runtime_roles_are_fixed():
    assert implementation_module._dag_repair_runtime_for("dag-normal-verify") == "secondary"
    assert implementation_module._dag_repair_runtime_for("dag-final-verify") == "secondary"
    assert implementation_module._dag_repair_runtime_for("dag-rca") == "primary"
    assert implementation_module._dag_repair_runtime_for("dag-fix") == "primary"
    assert implementation_module._dag_repair_runtime_for("dag-contradiction-resolve") == "secondary"
    assert implementation_module._dag_repair_runtime_for("lens:acceptance-coverage") == "secondary"
    assert implementation_module._dag_repair_runtime_for("lens:contract-protocol") == "secondary"
    assert implementation_module._dag_repair_runtime_for("lens:build-dependency") == "primary"
    assert implementation_module._dag_repair_runtime_for("unknown", "fallback") == "fallback"


def test_dag_parallel_repair_kill_switch(monkeypatch):
    monkeypatch.setenv(implementation_module.DAG_PARALLEL_REPAIR_ENV, "0")

    assert implementation_module._dag_parallel_repair_enabled() is False


def test_dag_auto_resolve_contradictions_kill_switch(monkeypatch):
    monkeypatch.setenv(implementation_module.DAG_AUTO_RESOLVE_CONTRADICTIONS_ENV, "0")

    assert implementation_module._dag_auto_resolve_contradictions_enabled() is False


def test_dag_expanded_verify_merges_and_dedupes_lens_findings():
    base = Verdict(
        approved=False,
        summary="normal failed",
        concerns=[
            Issue(
                severity="major",
                description="import fails",
                file="pkg/app.py",
            ),
        ],
    )
    lens_specs = implementation_module._dag_verify_lens_specs()
    lens_verdict = Verdict(
        approved=False,
        summary="lens found more",
        concerns=[
            Issue(
                severity="major",
                description="import fails",
                file="pkg/app.py",
            ),
            Issue(
                severity="blocker",
                description="runtime registration is missing",
                file="pkg/runtime.py",
            ),
        ],
        gaps=[
            Gap(
                category="coverage",
                severity="major",
                description="owned AC is not exercised",
            ),
        ],
    )

    merged = implementation_module._merge_dag_expanded_verify_verdicts(
        base,
        [(lens_specs[1], lens_verdict)],
    )

    assert merged.approved is False
    assert [concern.description for concern in merged.concerns] == [
        "import fails",
        "[Runtime Composition Lens] runtime registration is missing",
    ]
    assert merged.gaps[0].description == (
        "[Runtime Composition Lens] owned AC is not exercised"
    )


@pytest.mark.asyncio
async def test_run_expanded_dag_verify_lenses_records_successes_and_failures(tmp_path):
    feature = SimpleNamespace(id="feat-expanded-verify", slug="expanded-verify")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {}
            self.prompts: list[str] = []
            self.actor_runtimes: dict[str, str | None] = {}

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            assert isinstance(task, Ask)
            self.prompts.append(task.prompt)
            self.actor_runtimes[task.actor.name] = task.actor.role.metadata.get("runtime")
            if "contract-protocol" in task.actor.name:
                raise RuntimeError("tooling timed out")
            if "acceptance-coverage" in task.actor.name:
                return Verdict(
                    approved=False,
                    summary="missing gate",
                    concerns=[
                        Issue(
                            severity="major",
                            description="AC-1 is not covered",
                            file="pkg/test_app.py",
                        ),
                    ],
                )
            return Verdict(approved=True, summary="clean")

    runner = _Runner()
    base = Verdict(
        approved=False,
        summary="normal failed",
        concerns=[Issue(severity="major", description="normal concern")],
    )

    merged = await implementation_module._run_expanded_dag_verify_lenses(
        runner,
        feature,
        3,
        0,
        base,
        [
            ImplementationResult(
                task_id="TASK-1",
                summary="implemented",
                files_modified=["pkg/app.py"],
            ),
        ],
        ["pkg/app.py"],
        [
            ImplementationTask(
                id="TASK-1",
                name="Task",
                description="Do the thing",
                requirement_ids=["REQ-1"],
                verification_gates=["AC-1"],
            ),
        ],
        feature_root=tmp_path,
    )

    lens_keys = [
        key
        for key in runner.artifacts.store
        if key.startswith("dag-repair-lens:g3:")
    ]
    assert len(lens_keys) == 6
    assert "dag-repair-lens:g3:contract-protocol:retry-0" in runner.artifacts.store
    contract_payload = json.loads(
        runner.artifacts.store["dag-repair-lens:g3:contract-protocol:retry-0"]
    )
    assert contract_payload["status"] == "failed"
    assert contract_payload["runtime"] == "secondary"
    assert "tooling timed out" in contract_payload["error"]
    acceptance_payload = json.loads(
        runner.artifacts.store["dag-repair-lens:g3:acceptance-coverage:retry-0"]
    )
    assert acceptance_payload["status"] == "completed"
    assert acceptance_payload["runtime"] == "secondary"
    assert acceptance_payload["verdict"]["approved"] is False
    assert "dag-repair-expanded-verify:g3:retry-0" in runner.artifacts.store
    expanded_payload = json.loads(
        runner.artifacts.store["dag-repair-expanded-verify:g3:retry-0"]
    )
    successful_runtimes = {
        item["lens"]: item["runtime"]
        for item in expanded_payload["successful_lenses"]
    }
    assert successful_runtimes["build-dependency"] == "primary"
    assert successful_runtimes["runtime-composition"] == "primary"
    assert successful_runtimes["acceptance-coverage"] == "secondary"
    assert successful_runtimes["security-boundary"] == "primary"
    assert successful_runtimes["regression-downstream"] == "primary"
    assert merged.approved is False
    assert any(
        concern.description == "[Acceptance Coverage Lens] AC-1 is not covered"
        for concern in merged.concerns
    )
    assert len(runner.prompts) == 6
    assert all("read-only verifier lens" in prompt for prompt in runner.prompts)
    assert runner.actor_runtimes[
        "verifier-dag-lens-g3-r0-acceptance-coverage"
    ] == "secondary"
    assert runner.actor_runtimes[
        "verifier-dag-lens-g3-r0-build-dependency"
    ] == "primary"


@pytest.mark.asyncio
async def test_dag_group_preflight_reports_structural_blockers(tmp_path):
    feature = SimpleNamespace(id="feat-preflight", slug="preflight")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    task_a = ImplementationTask(
        id="TASK-1",
        name="A",
        description="A",
        dependencies=["TASK-2", "TASK-MISSING"],
        verification_gates=["AC-1", "AC-1", "BAD-GATE"],
    )
    task_b = ImplementationTask(id="TASK-2", name="B", description="B")
    result = ImplementationResult(
        task_id="TASK-1",
        summary="done",
        files_modified=["missing.py"],
    )

    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        4,
        "initial",
        [task_a, task_b],
        [result],
        feature_root=tmp_path,
        known_task_ids={"TASK-1", "TASK-2"},
    )

    assert isinstance(verdict, Verdict)
    assert verdict.approved is False
    assert any("same execution wave" in issue.description for issue in verdict.concerns)
    assert any("unknown dependency" in issue.description for issue in verdict.concerns)
    assert any("BAD-GATE" in issue.description for issue in verdict.concerns)
    assert any("missing.py" in issue.description for issue in verdict.concerns)
    payload = json.loads(runner.artifacts.store["dag-repair-preflight:g4:retry-initial"])
    assert payload["approved"] is False
    assert payload["repairs"][0]["field"] == "verification_gates"
    assert task_a.verification_gates == ["AC-1", "BAD-GATE"]


@pytest.mark.asyncio
async def test_dag_repair_sanitizer_ignores_context_and_rewrites_legacy_paths(tmp_path):
    feature = SimpleNamespace(id="feat-sanitize", slug="sanitize")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "ok.py").write_text("ok", encoding="utf-8")
    backend_path = tmp_path / "iriai-studio-backend" / "iriai_studio_backend"
    backend_path.mkdir(parents=True)
    (backend_path / "paths.py").write_text("paths", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    result = ImplementationResult(
        task_id="FIX-1",
        summary="done",
        files_modified=[
            "pkg/ok.py",
            ".iriai-context/context.md",
            ".iriai/artifacts/features/feat/compile-sources-dag.md",
            "src/iriai_studio_backend/paths.py",
            "missing.py",
        ],
    )

    sanitized = await implementation_module._sanitize_dag_repair_results(
        runner,
        feature,
        26,
        1,
        [result],
        tmp_path,
        context_label="test",
    )

    assert sanitized[0].files_modified == [
        "pkg/ok.py",
        "iriai-studio-backend/iriai_studio_backend/paths.py",
        "missing.py",
    ]
    report = json.loads(runner.artifacts.store["dag-repair-result-sanitize:g26:retry-1"])
    assert report["ignored_path_count"] == 2
    assert report["rewritten_path_count"] == 1
    assert report["invalid_product_path_count"] == 1
    assert report["has_invalid_product_paths"] is True


def test_dag_task_prompt_uses_canonical_backend_paths_for_retired_prefixes():
    task = ImplementationTask(
        id="TASK-S20-01",
        name="Security hooks",
        description="Implement hook-disable controls",
        repo_path="iriai-studio-backend",
        file_scope=[
            {
                "path": "iriai-studio-backend/src-py/iriai_studio_backend/paths.py",
                "action": "modify",
            },
            {
                "path": "iriai-studio-backend/src/iriai_studio_backend/security/hooks_disable.py",
                "action": "create",
            },
        ],
        files=["src/iriai_studio_backend/security/__init__.py"],
    )

    canonical_tasks, rewrites = implementation_module.canonicalize_implementation_tasks([task])
    prompt = implementation_module._build_task_prompt(
        canonical_tasks[0],
        repo_prefix="iriai-studio-backend/",
    )

    assert len(rewrites) == 3
    assert "`iriai_studio_backend/paths.py`" in prompt
    assert "`iriai_studio_backend/security/hooks_disable.py`" in prompt
    assert "src/iriai_studio_backend" not in prompt
    assert "src-py/iriai_studio_backend" not in prompt
    assert task.file_scope[0].path == (
        "iriai-studio-backend/src-py/iriai_studio_backend/paths.py"
    )


@pytest.mark.asyncio
async def test_dag_repair_sanitizer_rewrites_retired_existing_source_before_product_acceptance(tmp_path):
    feature = SimpleNamespace(id="feat-stale-source-sanitize", slug="stale-source-sanitize")
    stale_path = (
        tmp_path
        / "iriai-studio-backend"
        / "src"
        / "iriai_studio_backend"
        / "security"
    )
    stale_path.mkdir(parents=True)
    (stale_path / "hooks_disable.py").write_text("dead copy", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    result = ImplementationResult(
        task_id="FIX-1",
        summary="done",
        files_modified=[
            "iriai-studio-backend/src/iriai_studio_backend/security/hooks_disable.py",
        ],
    )

    sanitized = await implementation_module._sanitize_dag_repair_results(
        runner,
        feature,
        26,
        0,
        [result],
        tmp_path,
        context_label="test",
    )

    assert sanitized[0].files_modified == [
        "iriai-studio-backend/iriai_studio_backend/security/hooks_disable.py",
    ]
    report = json.loads(runner.artifacts.store["dag-repair-result-sanitize:g26:retry-0"])
    assert report["rewritten_path_count"] == 1
    assert report["paths"][0]["category"] == "rewritten_product"

    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        26,
        "0",
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        sanitized,
        feature_root=tmp_path,
        known_task_ids={"TASK-1"},
    )

    assert isinstance(verdict, Verdict)
    assert any(
        "iriai-studio-backend/iriai_studio_backend/security/hooks_disable.py"
        in issue.description
        for issue in verdict.concerns
    )


@pytest.mark.asyncio
async def test_dag_repair_sanitizer_prevents_context_paths_from_blocking_preflight(tmp_path):
    feature = SimpleNamespace(id="feat-context-sanitize", slug="context-sanitize")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "ok.py").write_text("ok", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    result = ImplementationResult(
        task_id="FIX-1",
        summary="done",
        files_modified=["pkg/ok.py", ".iriai-context/context.md"],
    )
    sanitized = await implementation_module._sanitize_dag_repair_results(
        runner,
        feature,
        6,
        0,
        [result],
        tmp_path,
        context_label="test",
    )

    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        6,
        "0",
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        sanitized,
        feature_root=tmp_path,
        known_task_ids={"TASK-1"},
    )

    assert verdict is None
    assert sanitized[0].files_modified == ["pkg/ok.py"]


@pytest.mark.asyncio
async def test_dag_repair_sanitizer_preserves_missing_product_paths(tmp_path):
    feature = SimpleNamespace(id="feat-invalid-sanitize", slug="invalid-sanitize")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    result = ImplementationResult(
        task_id="FIX-1",
        summary="done",
        files_modified=["missing.py"],
    )
    sanitized = await implementation_module._sanitize_dag_repair_results(
        runner,
        feature,
        7,
        0,
        [result],
        tmp_path,
        context_label="test",
    )

    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        7,
        "0",
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        sanitized,
        feature_root=tmp_path,
        known_task_ids={"TASK-1"},
    )

    assert isinstance(verdict, Verdict)
    assert any("missing.py" in issue.description for issue in verdict.concerns)


@pytest.mark.asyncio
async def test_parallel_dag_repair_runs_scheduled_fixes_with_primary_runtime(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-parallel-repair", slug="parallel-repair")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {}
            self.parallel_batches: list[list[str]] = []
            self.actor_runtimes: dict[str, str | None] = {}

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            assert isinstance(task, Ask)
            self.actor_runtimes[task.actor.name] = task.actor.role.metadata.get("runtime")
            if task.output_type is BugTriage:
                return BugTriage(
                    groups=[
                        BugGroup(
                            group_id="BG-1",
                            likely_root_cause="alpha",
                            issue_indices=[0],
                            severity="major",
                            affected_files_hint=["pkg/a.py"],
                        ),
                        BugGroup(
                            group_id="BG-2",
                            likely_root_cause="beta",
                            issue_indices=[1],
                            severity="major",
                            affected_files_hint=["pkg/b.py"],
                        ),
                    ],
                )
            if task.output_type is RootCauseAnalysis:
                suffix = "a.py" if "BG-1" in task.actor.name else "b.py"
                return RootCauseAnalysis(
                    hypothesis=f"fix {suffix}",
                    affected_files=[f"pkg/{suffix}"],
                    proposed_approach="patch it",
                    confidence="high",
                )
            if task.output_type is ImplementationResult:
                suffix = "a.py" if "BG-1" in task.actor.name else "b.py"
                return ImplementationResult(
                    task_id=f"FIX-{suffix}",
                    summary=f"fixed {suffix}",
                    files_modified=[f"pkg/{suffix}"],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            self.parallel_batches.append([task.actor.name for task in tasks])
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()
    verdict = Verdict(
        approved=False,
        summary="failed",
        concerns=[
            Issue(severity="major", description="alpha broken", file="pkg/a.py"),
            Issue(severity="major", description="beta broken", file="pkg/b.py"),
        ],
    )

    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        5,
        0,
        verdict,
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert {result.task_id for result in results} == {"FIX-a.py", "FIX-b.py"}
    assert "dag-repair-triage:g5:retry-0" in runner.artifacts.store
    assert "dag-repair-rca:g5:BG-1:retry-0" in runner.artifacts.store
    assert "dag-repair-dispatch:g5:retry-0" in runner.artifacts.store
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g5:retry-0"])
    assert dispatch["schedule"] == [{"round": 0, "group_ids": ["BG-1", "BG-2"]}]
    assert {
        "implementer-dag-g5-r0-fix-BG-1",
        "implementer-dag-g5-r0-fix-BG-2",
    }.issubset(runner.actor_runtimes)
    assert runner.actor_runtimes["bug-triager-dag-g5-r0-triage"] == "primary"
    assert runner.actor_runtimes["root-cause-analyst-dag-g5-r0-rca-BG-1"] == "primary"
    assert runner.actor_runtimes["implementer-dag-g5-r0-fix-BG-1"] == "primary"
    assert runner.actor_runtimes[
        "verifier-dag-g5-r0-focused-reverify-BG-1"
    ] == "primary"


@pytest.mark.asyncio
async def test_parallel_dag_repair_autonomous_resolves_decision_only_contradiction(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-contradiction", slug="contradiction", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                "decisions": "HUGE_DECISION_ARTIFACT_START" + ("x" * 200_000),
                "decisions:global": "global decision",
                "dag:strategy": "strategy",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True}
            self.parallel_batches: list[list[str]] = []
            self.actor_runtimes: dict[str, str | None] = {}
            self.prompts: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            assert isinstance(task, Ask)
            self.prompts.append(task.prompt)
            self.actor_runtimes[task.actor.name] = task.actor.role.metadata.get("runtime")
            if task.output_type is BugTriage:
                return BugTriage(
                    groups=[
                        BugGroup(
                            group_id="BG-FIX",
                            likely_root_cause="ordinary bug",
                            issue_indices=[0],
                            severity="major",
                            affected_files_hint=["pkg/fix.py"],
                        ),
                        BugGroup(
                            group_id="BG-CONTRA",
                            likely_root_cause="event names conflict",
                            issue_indices=[1],
                            severity="major",
                            affected_files_hint=["pkg/events.py"],
                        ),
                    ],
                )
            if task.output_type is RootCauseAnalysis:
                if "BG-CONTRA" in task.actor.name:
                    return RootCauseAnalysis(
                        hypothesis="bare name conflicts with @v1",
                        evidence=["pkg/events.py:1 uses project_updated@v1"],
                        affected_files=["pkg/events.py"],
                        proposed_approach="ratify @v1",
                        confidence="contradiction",
                        contradiction_detail="bare vs @v1",
                    )
                return RootCauseAnalysis(
                    hypothesis="fix bug",
                    evidence=["pkg/fix.py"],
                    affected_files=["pkg/fix.py"],
                    proposed_approach="patch it",
                    confidence="high",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                assert "Read the context index first" in task.prompt
                assert "HUGE_DECISION_ARTIFACT_START" not in task.prompt
                assert len(task.prompt) < 10_000
                return implementation_module.DagContradictionResolution(
                    resolution="`@v1` event names are authoritative wire tokens.",
                    authoritative_sources=["pkg/events.py:1"],
                    superseded_expectation="bare event names are prose labels",
                    implementation_direction="Do not rename events.",
                    requires_code_change=False,
                    needs_human=False,
                    confidence="high",
                    rationale="Tests and consumers use @v1.",
                )
            if task.output_type is ImplementationResult:
                return ImplementationResult(
                    task_id="FIX-BG-FIX",
                    summary="fixed ordinary bug",
                    files_modified=["pkg/fix.py"],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            self.parallel_batches.append([task.actor.name for task in tasks])
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()
    verdict = Verdict(
        approved=False,
        summary="failed",
        concerns=[
            Issue(severity="major", description="ordinary bug", file="pkg/fix.py"),
            Issue(severity="major", description="event name conflict", file="pkg/events.py"),
        ],
    )

    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        26,
        0,
        verdict,
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert {result.task_id for result in results} == {
        "CONTRADICTION-g26-r0-BG-CONTRA",
        "FIX-BG-FIX",
    }
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g26:retry-0"])
    assert dispatch["fixable_group_count"] == 1
    assert dispatch["resolved_contradiction_count"] == 1
    assert dispatch["human_needed_contradiction_count"] == 0
    assert dispatch["schedule"] == [{"round": 0, "group_ids": ["BG-FIX"]}]
    assert implementation_module.CONTRADICTION_DECISIONS_KEY in runner.artifacts.store
    assert (
        "contradiction:dag-repair:g26:retry-0:BG-CONTRA"
        in runner.artifacts.store
    )
    assert runner.actor_runtimes[
        "root-cause-analyst-dag-g26-r0-contradiction-BG-CONTRA"
    ] == "secondary"
    assert runner.actor_runtimes["implementer-dag-g26-r0-fix-BG-FIX"] == "primary"


@pytest.mark.asyncio
async def test_parallel_dag_repair_contradiction_requiring_code_change_joins_schedule(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-contradiction-code", slug="contradiction-code", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True}
            self.parallel_batches: list[list[str]] = []
            self.run_actors: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.run_actors.append(task.actor.name)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-CONTRA",
                        likely_root_cause="conflict",
                        issue_indices=[0],
                        severity="major",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="conflict",
                    evidence=["pkg/code.py"],
                    affected_files=["pkg/code.py"],
                    proposed_approach="decide",
                    confidence="contradiction",
                    contradiction_detail="A vs B",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                return implementation_module.DagContradictionResolution(
                    resolution="Source B wins.",
                    authoritative_sources=["pkg/spec.md:1"],
                    implementation_direction="Change code to Source B.",
                    requires_code_change=True,
                    needs_human=False,
                    confidence="high",
                    rationale="B is newer.",
                )
            if task.output_type is ImplementationResult:
                return ImplementationResult(
                    task_id="FIX-CONTRA",
                    summary="fixed contradiction code",
                    files_modified=["pkg/code.py"],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            self.parallel_batches.append([task.actor.name for task in tasks])
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()

    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        6,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[Issue(severity="major", description="conflict")],
            gaps=[Gap(category="coverage", severity="major", description="extra")],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == ["FIX-CONTRA"]
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g6:retry-0"])
    assert dispatch["resolved_contradiction_count"] == 1
    assert dispatch["fixable_group_count"] == 1
    assert dispatch["schedule"] == [{"round": 0, "group_ids": ["BG-CONTRA"]}]
    assert "implementer-dag-g6-r0-fix-BG-CONTRA" in runner.run_actors


@pytest.mark.asyncio
async def test_parallel_dag_repair_normalizes_legacy_contradiction_confidence(
    tmp_path,
):
    feature = SimpleNamespace(id="feat-legacy-confidence", slug="legacy-confidence", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True}

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-CONTRA",
                        likely_root_cause="stale expectation",
                        issue_indices=[0],
                        severity="major",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="old expectation conflicts with current tests",
                    evidence=["pkg/test_current.py"],
                    affected_files=["pkg/current.py"],
                    proposed_approach="current tests win",
                    confidence="contradiction",
                    contradiction_detail="old vs current",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                return implementation_module.DagContradictionResolution(
                    resolution="Current tests prove the finding is stale.",
                    resolution_kind="stale_not_reproducing",
                    authoritative_sources=["pkg/test_current.py:10"],
                    requires_code_change=False,
                    needs_human=False,
                    confidence="contradiction",
                    rationale="Resolver used legacy RCA confidence spelling.",
                )
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        9,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="stale env finding"),
                Issue(severity="major", description="same stale finding"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == [
        "CONTRADICTION-g9-r0-BG-CONTRA"
    ]
    accepted = json.loads(
        runner.artifacts.store["contradiction:dag-repair:g9:retry-0:BG-CONTRA"]
    )
    assert accepted["resolution_kind"] == "stale_not_reproducing"
    assert accepted["confidence"] == "medium"
    assert accepted["requires_code_change"] is False
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g9:retry-0"])
    assert dispatch["resolved_contradiction_count"] == 1
    assert dispatch["rejected_contradiction_count"] == 0
    assert dispatch["human_needed_contradiction_count"] == 0


@pytest.mark.asyncio
async def test_parallel_dag_repair_artifact_repair_uses_dedicated_lane(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-artifact-repair", slug="artifact-repair", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True}
            self.run_actors: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.run_actors.append(task.actor.name)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-ARTIFACT",
                        likely_root_cause="manifest path drift",
                        issue_indices=[0],
                        severity="major",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="manifest path drift",
                    evidence=[".iriai-context/changed-files.md"],
                    affected_files=[
                        "src/vs/workbench/contrib/iriaiStudio/browser/views/"
                        "components/WorkflowCard.tsx"
                    ],
                    proposed_approach="normalize manifest",
                    confidence="contradiction",
                    contradiction_detail="manifest vs repo",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                return implementation_module.DagContradictionResolution(
                    resolution="Manifest paths should be normalized to the canonical layout.",
                    resolution_kind="artifact_repair",
                    authoritative_sources=["repo/pyproject.toml:1"],
                    artifact_paths=[".iriai-context/changed-files.md", "dag:strategy"],
                    implementation_direction=(
                        "Patch .iriai-context/changed-files.md so it no longer "
                        "points at stale product paths."
                    ),
                    requires_code_change=False,
                    needs_human=False,
                    confidence="high",
                )
            if task.output_type is ArtifactRepairResult:
                return ArtifactRepairResult(
                    task_id="ARTIFACT-FIX",
                    group_id="BG-ARTIFACT",
                    summary="normalized artifact",
                    artifacts_modified=[".iriai-context/changed-files.md"],
                    artifact_updates=[
                        ArtifactRepairUpdate(
                            artifact_key="dag:strategy",
                            content='{"workstreams":[]}',
                            summary="removed stale surface path",
                        )
                    ],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        10,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="manifest path drift"),
                Issue(severity="major", description="preflight blocked"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == ["ARTIFACT-FIX"]
    assert "implementer-dag-g10-r0-artifact-repair-BG-ARTIFACT" in runner.run_actors
    assert "implementer-dag-g10-r0-fix-BG-ARTIFACT" not in runner.run_actors
    assert runner.artifacts.store["dag:strategy"] == '{"workstreams":[]}'
    repair = json.loads(
        runner.artifacts.store["dag-artifact-repair:g10:BG-ARTIFACT:retry-0"]
    )
    assert repair["target_refs"] == [
        ".iriai-context/changed-files.md",
        "dag:strategy",
    ]
    applied_update = repair["artifact_update_application"]["applied_updates"][0]
    assert applied_update["artifact_key"] == "dag:strategy"
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g10:retry-0"])
    assert dispatch["resolved_contradiction_count"] == 1
    assert dispatch["artifact_repair_group_count"] == 1
    assert dispatch["schedule"] == []


@pytest.mark.asyncio
async def test_parallel_dag_repair_metadata_only_high_confidence_routes_artifact_lane(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-meta-route", slug="meta-route", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True}
            self.run_actors: list[str] = []
            self.fix_attempted = False

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.run_actors.append(task.actor.name)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-META",
                        likely_root_cause=(
                            "manifest drift in orchestration metadata"
                        ),
                        issue_indices=[0],
                        severity="blocker",
                        affected_files_hint=[
                            ".iriai/artifacts/features/feat-meta-route/"
                            ".iriai-context/g28-changed-files.md",
                            "repo/src/live-code.ts",
                        ],
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis=(
                        "This is not a code defect; metadata-only repair is "
                        "needed for the changed-files artifact."
                    ),
                    evidence=[
                        ".iriai/artifacts/features/feat-meta-route/"
                        ".iriai-context/g28-changed-files.md:1",
                        "repo/src/live-code.ts is evidence only",
                    ],
                    affected_files=[
                        ".iriai/artifacts/features/feat-meta-route/"
                        ".iriai-context/g28-changed-files.md",
                        "repo/src/live-code.ts",
                    ],
                    proposed_approach=(
                        "Do not touch source. Replace the stale changed-files "
                        "artifact with the current metadata."
                    ),
                    confidence="high",
                )
            if task.output_type is ArtifactRepairResult:
                return ArtifactRepairResult(
                    task_id="ARTIFACT-META",
                    group_id="BG-META",
                    summary="repaired metadata",
                    artifact_updates=[
                        ArtifactRepairUpdate(
                            artifact_key="dag:strategy",
                            content='{"ok": true}',
                            summary="store update",
                        )
                    ],
                )
            if task.output_type is ImplementationResult:
                self.fix_attempted = True
                return ImplementationResult(task_id="BAD-FIX", summary="bad")
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        13,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="stale metadata"),
                Issue(severity="major", description="preflight blocked"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == ["ARTIFACT-META"]
    assert runner.fix_attempted is False
    assert "implementer-dag-g13-r0-artifact-repair-BG-META" in runner.run_actors
    assert "implementer-dag-g13-r0-fix-BG-META" not in runner.run_actors
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g13:retry-0"])
    assert dispatch["metadata_artifact_repair_group_count"] == 1
    assert dispatch["artifact_repair_group_count"] == 1
    assert dispatch["schedule"] == []


@pytest.mark.asyncio
async def test_parallel_dag_repair_blocked_metadata_fix_reroutes_artifact_lane(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-blocked-route", slug="blocked-route", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True}
            self.run_actors: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.run_actors.append(task.actor.name)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-BLOCKED-META",
                        likely_root_cause="context file stale",
                        issue_indices=[0],
                        severity="major",
                        affected_files_hint=[".iriai-context/g28-results.md"],
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="stale context file",
                    affected_files=[".iriai-context/g28-results.md"],
                    proposed_approach="Patch the context file.",
                    confidence="high",
                )
            if task.output_type is ImplementationResult:
                return ImplementationResult(
                    task_id="FIX-BG-BLOCKED-META",
                    summary="blocked by boundary",
                    status="blocked",
                    notes=(
                        ".iriai-context/g28-results.md is outside workspace "
                        "write boundary for this implementer."
                    ),
                )
            if task.output_type is ArtifactRepairResult:
                return ArtifactRepairResult(
                    task_id="ARTIFACT-BLOCKED-META",
                    group_id="BG-BLOCKED-META",
                    summary="repaired context",
                    artifact_updates=[
                        ArtifactRepairUpdate(
                            artifact_key="dag:strategy",
                            content='{"rerouted": true}',
                        )
                    ],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        14,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="stale context"),
                Issue(severity="major", description="preflight blocked"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == ["ARTIFACT-BLOCKED-META"]
    assert "implementer-dag-g14-r0-fix-BG-BLOCKED-META" in runner.run_actors
    assert (
        "implementer-dag-g14-r0-artifact-repair-BG-BLOCKED-META"
        in runner.run_actors
    )
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g14:retry-0"])
    assert dispatch["metadata_artifact_repair_group_count"] == 0
    assert dispatch["artifact_repair_group_count"] == 1


@pytest.mark.asyncio
async def test_artifact_repair_update_writes_allowed_target_ref(tmp_path):
    feature = SimpleNamespace(id="feat-target-ref", slug="target-ref", metadata={})
    feature_root = tmp_path / "repos"
    feature_root.mkdir()

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    result = ArtifactRepairResult(
        task_id="ARTIFACT-TARGET",
        group_id="BG-TARGET",
        summary="target refs",
        artifact_updates=[
            ArtifactRepairUpdate(
                target_ref="repo/.iriai-context/handover.md",
                content="fixed handover",
                summary="allowed context",
            ),
            ArtifactRepairUpdate(
                target_ref="repo/src/app.ts",
                content="bad product edit",
                summary="blocked product",
            ),
        ],
    )

    record = await implementation_module._apply_dag_artifact_repair_updates(
        runner,
        feature,
        result,
        feature_root,
    )

    assert (
        feature_root / "repo/.iriai-context/handover.md"
    ).read_text(encoding="utf-8") == "fixed handover"
    assert record["applied_target_updates"][0]["target_ref"] == (
        "repo/.iriai-context/handover.md"
    )
    assert record["skipped_updates"][0]["target_ref"] == "repo/src/app.ts"
    assert record["skipped_updates"][0]["reason"] == (
        "target_ref_not_artifact_context"
    )


@pytest.mark.asyncio
async def test_artifact_repair_update_writes_valid_dag_task_artifact(tmp_path):
    feature = SimpleNamespace(id="feat-dag-task", slug="dag-task", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    for path in [
        "src/webviews/projectSurface/src/dashboard/components/WorkflowCard.tsx",
        "src/webviews/projectSurface/src/dashboard/hooks/useLastActivityTick.ts",
        "src/webviews/projectSurface/src/styles/dashboard.css",
    ]:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    task_result = ImplementationResult(
        task_id="TASK-S18-3",
        summary="canonical D-GR-1 paths",
        files_created=[
            "src/webviews/projectSurface/src/dashboard/components/WorkflowCard.tsx",
            "src/webviews/projectSurface/src/dashboard/hooks/useLastActivityTick.ts",
        ],
        files_modified=["src/webviews/projectSurface/src/styles/dashboard.css"],
    )

    record = await implementation_module._apply_dag_artifact_repair_updates(
        runner,
        feature,
        ArtifactRepairResult(
            task_id="ARTIFACT-REPAIR",
            group_id="G-STale",
            summary="repair dag task",
            artifact_updates=[
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-S18-3",
                    content=task_result.model_dump_json(),
                    summary="replace stale task row",
                )
            ],
        ),
        feature_root,
    )

    stored = ImplementationResult.model_validate_json(
        runner.artifacts.store["dag-task:TASK-S18-3"]
    )
    assert stored.files_created == task_result.files_created
    assert stored.files_modified == task_result.files_modified
    assert record["applied_updates"][0]["artifact_kind"] == "dag_task"
    assert record["applied_updates"][0]["task_id"] == "TASK-S18-3"


@pytest.mark.asyncio
async def test_artifact_repair_update_rejects_invalid_dag_task_artifacts(tmp_path):
    feature = SimpleNamespace(id="feat-dag-task-reject", slug="dag-task-reject", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    existing = repo / "src/webviews/projectSurface/src/styles/dashboard.css"
    existing.parent.mkdir(parents=True)
    existing.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/iriaiStudio/browser/"
                        "views/components/WorkflowCard.tsx"
                    ),
                    "source": "D-GR-1",
                }
            ],
        }),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    def _result(task_id: str, *, status: str = "completed", path: str | None = None) -> str:
        return ImplementationResult(
            task_id=task_id,
            summary="candidate",
            status=status,
            files_modified=[path or "src/webviews/projectSurface/src/styles/dashboard.css"],
        ).model_dump_json()

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    record = await implementation_module._apply_dag_artifact_repair_updates(
        runner,
        feature,
        ArtifactRepairResult(
            task_id="ARTIFACT-REPAIR",
            group_id="G-STale",
            summary="bad dag task repairs",
            artifact_updates=[
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-MISMATCH",
                    content=_result("OTHER"),
                ),
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-BLOCKED",
                    content=_result("TASK-BLOCKED", status="blocked"),
                ),
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-BADJSON",
                    content="{not json",
                ),
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-EMPTY",
                    content=ImplementationResult(
                        task_id="TASK-EMPTY",
                        summary="empty",
                        files_created=[],
                        files_modified=[],
                    ).model_dump_json(),
                ),
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-MISSING",
                    content=_result("TASK-MISSING", path="src/missing.ts"),
                ),
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-CONTEXT",
                    content=_result("TASK-CONTEXT", path=".iriai-context/report.md"),
                ),
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-FORBIDDEN",
                    content=_result(
                        "TASK-FORBIDDEN",
                        path=(
                            "src/vs/workbench/contrib/iriaiStudio/browser/"
                            "views/components/WorkflowCard.tsx"
                        ),
                    ),
                ),
            ],
        ),
        feature_root,
    )

    assert runner.artifacts.store == {}
    assert [item["reason"] for item in record["skipped_updates"]] == [
        "dag_task_id_mismatch",
        "dag_task_status_not_completed_or_partial",
        "invalid_dag_task_result_json",
        "dag_task_no_reported_files",
        "dag_task_invalid_product",
        "dag_task_artifact_context",
        "dag_task_forbidden_path",
    ]


def test_collect_files_dedupes_preserving_first_seen_order():
    files = implementation_module._collect_files([
        ImplementationResult(
            task_id="A",
            summary="a",
            files_created=["pkg/a.py", "pkg/shared.py"],
            files_modified=["pkg/shared.py", "pkg/b.py"],
        ),
        ImplementationResult(
            task_id="B",
            summary="b",
            files_modified=["pkg/a.py", "pkg/c.py"],
        ),
    ])

    assert files == ["pkg/a.py", "pkg/shared.py", "pkg/b.py", "pkg/c.py"]


@pytest.mark.asyncio
async def test_dag_preflight_labels_missing_forbidden_reported_files(tmp_path):
    feature = SimpleNamespace(id="feat-forbidden-preflight", slug="forbidden", metadata={})
    config_path = tmp_path / "iriai-studio/scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                "src/vs/workbench/contrib/iriaiStudio/browser/views/components/"
                "WorkflowCard.tsx"
            ]
        }),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        15,
        "retry-0",
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [
            ImplementationResult(
                task_id="TASK-1",
                summary="reported stale path",
                files_modified=[
                    "iriai-studio/src/vs/workbench/contrib/iriaiStudio/"
                    "browser/views/components/WorkflowCard.tsx"
                ],
            )
        ],
        feature_root=tmp_path,
    )

    assert verdict is not None
    assert "forbidden/stale by verify-file-scope.expected-files.json" in (
        verdict.concerns[0].description
    )


@pytest.mark.asyncio
async def test_dag_preflight_reads_dict_shaped_forbidden_files(tmp_path):
    feature = SimpleNamespace(id="feat-forbidden-dict", slug="forbidden-dict", metadata={})
    config_path = tmp_path / "iriai-studio/scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {
                    "path": "src/webviews/projectSurface/src/styles/dashboard.css",
                    "source": "TASK-1 canonical D-GR-1",
                }
            ],
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/iriaiStudio/browser/"
                        "styles/workflow-card.css"
                    ),
                    "source": "D-GR-1",
                }
            ]
        }),
        encoding="utf-8",
    )
    canonical = (
        tmp_path
        / "iriai-studio/src/webviews/projectSurface/src/styles/dashboard.css"
    )
    (tmp_path / "iriai-studio/.git").mkdir(parents=True, exist_ok=True)
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        15,
        "retry-0",
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [
            ImplementationResult(
                task_id="TASK-1",
                summary="reported stale path",
                files_modified=[
                    "src/vs/workbench/contrib/iriaiStudio/browser/"
                    "styles/workflow-card.css"
                ],
            )
        ],
        feature_root=tmp_path,
    )

    assert verdict is not None
    assert "forbidden/stale by verify-file-scope.expected-files.json" in (
        verdict.concerns[0].description
    )
    report = json.loads(next(iter(runner.artifacts.store.values())))
    path_problem = report["path_problems"][0]
    assert path_problem["forbidden_source"] == "D-GR-1"
    assert path_problem["candidate_evidence"][0]["path"] == (
        "src/webviews/projectSurface/src/styles/dashboard.css"
    )
    assert path_problem["candidate_evidence"][0]["exists"] is True


@pytest.mark.asyncio
async def test_dag_preflight_matches_forbidden_directory_descendants(tmp_path):
    feature = SimpleNamespace(id="feat-forbidden-dir", slug="forbidden-dir", metadata={})
    config_path = tmp_path / "iriai-studio/scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {
                    "path": "src/vs/workbench/contrib/iriaiStudio",
                    "source": "D-GR-1-retired-tree",
                }
            ]
        }),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        29,
        "retry-1",
        [ImplementationTask(id="TASK-S18-4", name="Task", description="Task")],
        [
            ImplementationResult(
                task_id="TASK-S18-4",
                summary="reported retired test tree",
                files_created=[
                    "iriai-studio/src/vs/workbench/contrib/iriaiStudio/"
                    "test/integration/reconnect.test.ts"
                ],
            )
        ],
        feature_root=tmp_path,
    )

    assert verdict is not None
    assert "forbidden/stale by verify-file-scope.expected-files.json" in (
        verdict.concerns[0].description
    )
    assert "dag-task:TASK-S18-4" in verdict.concerns[0].description


@pytest.mark.asyncio
async def test_dag_preflight_fails_forbidden_task_spec_path(tmp_path):
    feature = SimpleNamespace(id="feat-forbidden-spec", slug="forbidden-spec", metadata={})
    repo = tmp_path / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    canonical = repo / "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {
                    "path": "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts",
                    "source": "TASK-SH2-1 canonical",
                }
            ],
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/studioWorkflow/browser/"
                        "workflowTab/chat"
                    ),
                    "source": "D-GR-1 retired chat tree",
                }
            ],
        }),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    task_id = "chat-sidepane-shell-slice-3-TASK-chat-util-dedup"
    runner = SimpleNamespace(artifacts=_Artifacts())
    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        30,
        "retry-0",
        [
            ImplementationTask.model_validate({
                "id": task_id,
                "name": "Dedup",
                "description": "Dedup",
                "file_scope": [
                    {
                        "path": (
                            "iriai-studio/src/vs/workbench/contrib/"
                            "studioWorkflow/browser/workflowTab/chat/util/"
                            "eventDeduplicator.ts"
                        ),
                        "action": "create",
                    }
                ],
                "repo_path": "iriai-studio",
                "subfeature_id": "chat-sidepane-shell",
            })
        ],
        [
            ImplementationResult(
                task_id=task_id,
                summary="canonical result",
                files_modified=[
                    "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts"
                ],
            )
        ],
        feature_root=tmp_path,
    )

    assert verdict is not None
    assert "task spec file_scope[0].path references" in verdict.concerns[0].description
    assert "dag-fragment:chat-sidepane-shell:slice-3" in (
        verdict.concerns[0].description
    )
    report = json.loads(runner.artifacts.store["dag-repair-preflight:g30:retry-retry-0"])
    problem = report["path_problems"][0]
    assert problem["reason"] == "forbidden_task_spec"
    assert problem["repair_route"] == "artifact_only"
    assert problem["source_artifact_ref"] == "dag-fragment:chat-sidepane-shell:slice-3"


@pytest.mark.asyncio
async def test_dag_preflight_fails_manifest_forbidden_file_on_disk(tmp_path):
    feature = SimpleNamespace(id="feat-forbidden-disk", slug="forbidden-disk", metadata={})
    repo = tmp_path / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    forbidden_path = (
        "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat/"
        "util/eventDeduplicator.ts"
    )
    forbidden = repo / forbidden_path
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("old", encoding="utf-8")
    canonical = repo / "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {
                    "path": "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts",
                    "source": "TASK-chat-util-dedup canonical",
                }
            ],
            "forbidden_files": [
                {
                    "path": forbidden_path,
                    "source": "D-GR-1 retired workflowTab chat util",
                }
            ],
        }),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        30,
        "retry-0",
        [ImplementationTask(id="TASK-chat-util-dedup", name="Task", description="Task")],
        [
            ImplementationResult(
                task_id="TASK-chat-util-dedup",
                summary="canonical",
                files_modified=[
                    "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts"
                ],
            )
        ],
        feature_root=tmp_path,
    )

    assert verdict is not None
    assert "manifest-forbidden path exists" in verdict.concerns[0].description
    report = json.loads(runner.artifacts.store["dag-repair-preflight:g30:retry-retry-0"])
    problem = report["path_problems"][0]
    assert problem["reason"] == "forbidden_workspace_path"
    assert problem["repair_route"] == "product_cleanup_required"
    assert problem["exists_on_disk"] is True


@pytest.mark.asyncio
async def test_dag_task_artifact_repair_clears_preflight_stale_row(tmp_path):
    feature = SimpleNamespace(id="feat-preflight-repair", slug="preflight-repair", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    for path in [
        "src/webviews/projectSurface/src/dashboard/components/WorkflowCard.tsx",
        "src/webviews/projectSurface/src/dashboard/hooks/useLastActivityTick.ts",
        "src/webviews/projectSurface/src/styles/dashboard.css",
    ]:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/iriaiStudio/browser/"
                        "views/components/WorkflowCard.tsx"
                    ),
                    "source": "D-GR-1",
                }
            ]
        }),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    task = ImplementationTask(id="TASK-S18-3", name="Task", description="Task")
    stale_result = ImplementationResult(
        task_id="TASK-S18-3",
        summary="stale",
        files_modified=[
            "src/vs/workbench/contrib/iriaiStudio/browser/"
            "views/components/WorkflowCard.tsx"
        ],
    )
    before = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        15,
        "retry-0",
        [task],
        [stale_result],
        feature_root=feature_root,
    )
    assert before is not None

    corrected = ImplementationResult(
        task_id="TASK-S18-3",
        summary="corrected",
        files_created=[
            "src/webviews/projectSurface/src/dashboard/components/WorkflowCard.tsx",
            "src/webviews/projectSurface/src/dashboard/hooks/useLastActivityTick.ts",
        ],
        files_modified=["src/webviews/projectSurface/src/styles/dashboard.css"],
    )
    await implementation_module._apply_dag_artifact_repair_updates(
        runner,
        feature,
        ArtifactRepairResult(
            task_id="ARTIFACT-REPAIR",
            group_id="G-S18",
            summary="repair persisted task result",
            artifact_updates=[
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-S18-3",
                    content=corrected.model_dump_json(),
                )
            ],
        ),
        feature_root,
    )
    latest = ImplementationResult.model_validate_json(
        runner.artifacts.store["dag-task:TASK-S18-3"]
    )
    after = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        15,
        "retry-1",
        [task],
        [latest],
        feature_root=feature_root,
    )

    assert after is None


@pytest.mark.asyncio
async def test_verify_failure_routes_stale_dag_task_to_artifact_repair(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-verify-artifact", slug="verify-artifact", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    canonical = repo / "src/webviews/projectSurface/src/styles/dashboard.css"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("ok", encoding="utf-8")
    corrected = ImplementationResult(
        task_id="TASK-S18-3",
        summary="corrected",
        files_modified=["src/webviews/projectSurface/src/styles/dashboard.css"],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis=(
                        "stale persisted dag-task ImplementationResult row "
                        "still has old files_created/files_modified metadata"
                    ),
                    affected_files=["dag-task:TASK-S18-3"],
                    proposed_approach=(
                        "Repair the DB-backed artifact row through artifact_updates."
                    ),
                    confidence="contradiction",
                )
            if task.output_type is ArtifactRepairResult:
                return ArtifactRepairResult(
                    task_id="ARTIFACT-REPAIR-VERIFY",
                    group_id="VERIFY",
                    summary="updated dag task",
                    artifact_updates=[
                        ArtifactRepairUpdate(
                            artifact_key="dag-task:TASK-S18-3",
                            content=corrected.model_dump_json(),
                        )
                    ],
                )
            if task.output_type is ImplementationResult:
                raise AssertionError("normal implementer should not run")
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="verified")
            raise AssertionError(f"unexpected output type {task.output_type}")

    async def _fail_commit(*args, **kwargs):
        raise AssertionError("artifact-only repair should not commit repos")

    monkeypatch.setattr(implementation_module, "_commit_repos", _fail_commit)
    monkeypatch.setattr(implementation_module, "_commit_repos_in_root", _fail_commit)
    runner = _Runner()
    attempt = await implementation_module._single_rca_fix_verify(
        runner,
        feature,
        "preflight failed on stale dag-task row",
        "verify",
        implementation_module.qa_engineer,
        implementation_module.implementer,
        "",
        bug_id="VERIFY-FAIL-DB",
        attempt_number=1,
        workspace_root=feature_root,
    )

    stored = ImplementationResult.model_validate_json(
        runner.artifacts.store["dag-task:TASK-S18-3"]
    )
    assert stored.files_modified == corrected.files_modified
    assert "bug-artifact-repair:verify:VERIFY-FAIL-DB" in runner.artifacts.store
    assert attempt.re_verify_result == "PASS"
    assert ImplementationResult not in runner.output_types


@pytest.mark.asyncio
async def test_verify_failure_routes_existing_forbidden_dag_task_to_product_cleanup(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-verify-product-cleanup", slug="verify-product-cleanup", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    task_id = "chat-sidepane-shell-slice-3-TASK-chat-util-dedup"
    forbidden_path = (
        "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat/"
        "util/eventDeduplicator.ts"
    )
    canonical_path = (
        "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts"
    )
    forbidden = repo / forbidden_path
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("old", encoding="utf-8")
    canonical = repo / canonical_path
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {
                    "path": canonical_path,
                    "source": f"{task_id} canonical",
                }
            ],
            "forbidden_files": [
                {
                    "path": forbidden_path,
                    "source": "D-GR-1 retired workflowTab chat util",
                }
            ],
        }),
        encoding="utf-8",
    )
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale",
        files_modified=[forbidden_path],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                f"dag-task:{task_id}": stale.model_dump_json()
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis=(
                        "stale persisted dag-task ImplementationResult row "
                        "still points at a manifest-forbidden product file"
                    ),
                    evidence=["the forbidden workflowTab util still exists on disk"],
                    affected_files=[f"dag-task:{task_id}", forbidden_path],
                    proposed_approach=(
                        "Clean up the retired product file, port coverage, then "
                        "let the host append corrected dag-task metadata."
                    ),
                    confidence="high",
                )
            if task.output_type is ArtifactRepairResult:
                raise AssertionError("artifact repair must not run while forbidden file exists")
            if task.output_type is ImplementationResult:
                forbidden.unlink()
                return ImplementationResult(
                    task_id="VERIFY-PRODUCT-CLEANUP",
                    summary="removed retired util and kept canonical implementation",
                    files_modified=[canonical_path],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="verified")
            raise AssertionError(f"unexpected output type {task.output_type}")

    async def _noop_commit(*args, **kwargs):
        return None

    monkeypatch.setattr(implementation_module, "_commit_repos", _noop_commit)
    monkeypatch.setattr(implementation_module, "_commit_repos_in_root", _noop_commit)
    runner = _Runner()
    attempt = await implementation_module._single_rca_fix_verify(
        runner,
        feature,
        f"preflight failed on dag-task:{task_id} and {forbidden_path}",
        "verify",
        implementation_module.qa_engineer,
        implementation_module.implementer,
        "",
        bug_id="VERIFY-FAIL-PRODUCT-DRIFT",
        attempt_number=1,
        workspace_root=feature_root,
        skip_regression=True,
    )

    stored = ImplementationResult.model_validate_json(
        runner.artifacts.store[f"dag-task:{task_id}"]
    )
    assert stored.task_id == task_id
    assert stored.files_modified == [canonical_path]
    assert attempt.re_verify_result == "PASS"
    assert ArtifactRepairResult not in runner.output_types
    assert ImplementationResult in runner.output_types
    reconcile = json.loads(
        runner.artifacts.store[
            "dag-task-product-reconcile:verify:VERIFY-FAIL-PRODUCT-DRIFT"
        ]
    )
    assert reconcile["applied"][0]["action"] == "appended_dag_task_row"


@pytest.mark.asyncio
async def test_parallel_dag_repair_infers_dag_task_artifact_from_preflight_issue(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-g29-route", slug="g29-route", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    for path in [
        "src/vs/workbench/contrib/studioBridge/test/browser/reconnect.integrationTest.ts",
        "src/vs/workbench/contrib/studioBridge/test/browser/fixtures/reconnectFixtures.ts",
    ]:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")

    task_id = "project-and-launcher-slice-18-TASK-SF4-S18-4"
    corrected = ImplementationResult(
        task_id=task_id,
        summary="canonical studioBridge reconnect test metadata",
        files_created=[
            "src/vs/workbench/contrib/studioBridge/test/browser/"
            "reconnect.integrationTest.ts",
            "src/vs/workbench/contrib/studioBridge/test/browser/fixtures/"
            "reconnectFixtures.ts",
        ],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True}
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="stale-iriaiStudio-test-paths",
                        likely_root_cause=(
                            "stale ImplementationResult files_created metadata"
                        ),
                        issue_indices=[0],
                        severity="blocker",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis=(
                        "The implementation is already in studioBridge, but the "
                        "stale ImplementationResult still reports retired "
                        "iriaiStudio files_created paths."
                    ),
                    evidence=[
                        "Canonical reconnect files exist under studioBridge/test/browser.",
                        "The old iriaiStudio tree is retired by D-GR-1.",
                    ],
                    affected_files=[
                        "src/vs/workbench/contrib/iriaiStudio/test/integration/"
                        "reconnect.test.ts"
                    ],
                    proposed_approach=(
                        "Append a corrected ImplementationResult with canonical "
                        "studioBridge paths; do not create the retired files."
                    ),
                    confidence="high",
                )
            if task.output_type is ArtifactRepairResult:
                return ArtifactRepairResult(
                    task_id="ARTIFACT-REPAIR-G29",
                    group_id="stale-iriaiStudio-test-paths",
                    summary="updated stale task metadata",
                    artifact_updates=[
                        ArtifactRepairUpdate(
                            artifact_key=f"dag-task:{task_id}",
                            content=corrected.model_dump_json(),
                        )
                    ],
                )
            if task.output_type is ImplementationResult:
                raise AssertionError("normal implementer should not run")
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    async def _fail_commit(*args, **kwargs):
        raise AssertionError("artifact-only repair should not commit repos")

    monkeypatch.setattr(implementation_module, "_commit_repos", _fail_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        29,
        1,
        Verdict(
            approved=False,
            summary="Programmatic DAG preflight failed",
            concerns=[
                Issue(
                    severity="major",
                    description=(
                        f"{task_id} reports changed file that is forbidden/stale "
                        "by verify-file-scope.expected-files.json; repair stale "
                        "task metadata instead of creating this path: "
                        "src/vs/workbench/contrib/iriaiStudio/test/integration/"
                        "reconnect.test.ts"
                    ),
                ),
                Issue(severity="major", description="second issue keeps DAG repair path"),
            ],
        ),
        [ImplementationTask(id=task_id, name="Task", description="Task")],
        feature_root=feature_root,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="preflight failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == ["ARTIFACT-REPAIR-G29"]
    stored = ImplementationResult.model_validate_json(
        runner.artifacts.store[f"dag-task:{task_id}"]
    )
    assert stored.files_created == corrected.files_created
    assert ImplementationResult not in runner.output_types
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g29:retry-1"])
    assert dispatch["dag_task_artifact_repair_group_count"] == 1
    assert dispatch["artifact_repair_group_count"] == 1
    assert dispatch["schedule"] == []


@pytest.mark.asyncio
async def test_parallel_dag_repair_routes_existing_forbidden_dag_task_to_product_cleanup(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-g30-product-cleanup", slug="g30-product-cleanup", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    task_id = "chat-sidepane-shell-slice-3-TASK-chat-util-dedup"
    forbidden_path = (
        "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat/"
        "util/eventDeduplicator.test.ts"
    )
    canonical_path = (
        "src/webviews/projectSurface/src/chat/stores/__tests__/"
        "EventDeduplicator.test.ts"
    )
    forbidden = repo / forbidden_path
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("old test", encoding="utf-8")
    canonical = repo / canonical_path
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("canonical test", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {
                    "path": canonical_path,
                    "source": f"{task_id} AC-31 AC-32 canonical",
                }
            ],
            "forbidden_files": [
                {
                    "path": forbidden_path,
                    "source": "D-GR-1 retired workflowTab chat util tests",
                }
            ],
        }),
        encoding="utf-8",
    )
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale workflowTab test metadata",
        files_created=[forbidden_path],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                f"dag-task:{task_id}": stale.model_dump_json()
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True}
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="stale-workflowtab-test-on-disk",
                        likely_root_cause=(
                            "stale ImplementationResult metadata plus retired "
                            "workflowTab test file still in the product tree"
                        ),
                        issue_indices=[0],
                        severity="blocker",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis=(
                        "The latest dag-task row points at a manifest-forbidden "
                        "workflowTab test file that still exists on disk."
                    ),
                    evidence=[
                        "The canonical projectSurface EventDeduplicator test exists.",
                        "The retired workflowTab test must be cleaned up first.",
                    ],
                    affected_files=[f"dag-task:{task_id}", forbidden_path],
                    proposed_approach=(
                        "Remove the retired test after preserving AC-31/AC-32 "
                        "coverage in the canonical projectSurface test, then "
                        "reconcile dag-task metadata."
                    ),
                    confidence="high",
                )
            if task.output_type is ArtifactRepairResult:
                raise AssertionError("artifact repair must not run before product cleanup")
            if task.output_type is ImplementationResult:
                forbidden.unlink()
                return ImplementationResult(
                    task_id="PRODUCT-CLEANUP-G30",
                    summary="ported acceptance coverage and removed retired test",
                    files_modified=[canonical_path],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    async def _noop_commit(*args, **kwargs):
        return None

    monkeypatch.setattr(implementation_module, "_commit_repos", _noop_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        30,
        1,
        Verdict(
            approved=False,
            summary="Programmatic DAG preflight failed",
            concerns=[
                Issue(
                    severity="major",
                    description=(
                        f"{task_id} reports changed file that is forbidden/stale "
                        "by verify-file-scope.expected-files.json; source artifact: "
                        f"dag-task:{task_id}; repair stale task metadata instead "
                        f"of creating this path: {forbidden_path}"
                    ),
                ),
                Issue(severity="major", description="second issue keeps DAG repair path"),
            ],
        ),
        [ImplementationTask(id=task_id, name="Task", description="Task")],
        feature_root=feature_root,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="preflight failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == ["PRODUCT-CLEANUP-G30"]
    stored = ImplementationResult.model_validate_json(
        runner.artifacts.store[f"dag-task:{task_id}"]
    )
    assert stored.files_modified == [canonical_path]
    assert ArtifactRepairResult not in runner.output_types
    assert ImplementationResult in runner.output_types
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g30:retry-1"])
    assert dispatch["dag_task_artifact_repair_group_count"] == 0
    assert dispatch["dag_task_product_cleanup_group_count"] == 1
    assert dispatch["schedule"] == [{"round": 0, "group_ids": ["stale-workflowtab-test-on-disk"]}]


@pytest.mark.asyncio
async def test_parallel_dag_repair_runs_source_artifact_followup_after_product_cleanup(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(
        id="feat-g30-source-followup",
        slug="g30-source-followup",
        metadata={},
    )
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    artifact_root = (
        tmp_path / ".iriai" / "artifacts" / "features" / feature.id
    )
    fragment = (
        artifact_root
        / "subfeatures/chat-sidepane-shell/dag-fragments/slice-3.json"
    )
    fragment.parent.mkdir(parents=True, exist_ok=True)
    fragment.write_text('{"tasks": "stale"}', encoding="utf-8")

    task_id = "chat-sidepane-shell-slice-3-TASK-chat-util-dedup"
    forbidden_path = (
        "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat/"
        "util/eventDeduplicator.ts"
    )
    canonical_path = (
        "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts"
    )
    forbidden = repo / forbidden_path
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("old", encoding="utf-8")
    canonical = repo / canonical_path
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {
                    "path": canonical_path,
                    "source": "TASK-SH2-1 canonical EventDeduplicator",
                }
            ],
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/studioWorkflow/browser/"
                        "workflowTab/chat"
                    ),
                    "source": "D-GR-1 retired workflowTab chat tree",
                }
            ],
        }),
        encoding="utf-8",
    )
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale workflowTab metadata",
        files_created=[forbidden_path],
    )
    corrected = ImplementationResult(
        task_id=task_id,
        summary="source artifact retired duplicate task to canonical EventDeduplicator",
        files_modified=[canonical_path],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                f"dag-task:{task_id}": stale.model_dump_json()
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Mirror:
        def feature_dir(self, feature_id: str):
            assert feature_id == feature.id
            return artifact_root

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "autonomous_remainder": True,
                "artifact_mirror": _Mirror(),
            }
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="stale-source-fragment",
                        likely_root_cause=(
                            "stale dag-fragment task spec and stale dag-task row "
                            "resurrect retired workflowTab chat files"
                        ),
                        issue_indices=[0],
                        severity="blocker",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis=(
                        "The source dag-fragment file_scope still points at the "
                        "forbidden workflowTab chat util path, while the product "
                        "tree also contains the stale file."
                    ),
                    evidence=[f"{fragment}:9 still contains the forbidden path"],
                    affected_files=[
                        str(fragment),
                        f"dag-task:{task_id}",
                        forbidden_path,
                    ],
                    proposed_approach=(
                        "Delete the retired product file first, then repair the "
                        "DAG source artifact and append corrected dag-task metadata."
                    ),
                    confidence="high",
                )
            if task.output_type is ImplementationResult:
                forbidden.unlink()
                for parent in [
                    forbidden.parent,
                    forbidden.parent.parent,
                    forbidden.parent.parent.parent,
                ]:
                    try:
                        parent.rmdir()
                    except OSError:
                        pass
                return ImplementationResult(
                    task_id="PRODUCT-CLEANUP-G30",
                    summary=(
                        "removed retired workflowTab file; canonical "
                        "EventDeduplicator already exists"
                    ),
                    files_modified=[],
                )
            if task.output_type is ArtifactRepairResult:
                assert not forbidden.exists()
                return ArtifactRepairResult(
                    task_id="ARTIFACT-SOURCE-FOLLOWUP",
                    group_id="stale-source-fragment",
                    summary="repaired source fragment and task row",
                    artifact_updates=[
                        ArtifactRepairUpdate(
                            target_ref=str(fragment),
                            content='{"tasks": "fixed"}',
                            summary="retired stale slice-3 task spec",
                        ),
                        ArtifactRepairUpdate(
                            artifact_key=f"dag-task:{task_id}",
                            content=corrected.model_dump_json(),
                            summary="append corrected task metadata",
                        ),
                    ],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    async def _noop_commit(*args, **kwargs):
        return None

    monkeypatch.setattr(implementation_module, "_commit_repos", _noop_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        30,
        1,
        Verdict(
            approved=False,
            summary="Programmatic DAG preflight failed",
            concerns=[
                Issue(
                    severity="major",
                    description=(
                        f"{task_id} reports changed file that is forbidden/stale "
                        "by verify-file-scope.expected-files.json; source artifact: "
                        f"dag-task:{task_id}; repair stale task metadata instead "
                        f"of creating this path: {forbidden_path}"
                    ),
                ),
                Issue(severity="major", description="second issue keeps DAG repair path"),
            ],
        ),
        [ImplementationTask(id=task_id, name="Task", description="Task")],
        feature_root=feature_root,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="preflight failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == [
        "ARTIFACT-SOURCE-FOLLOWUP",
        "PRODUCT-CLEANUP-G30",
    ]
    assert fragment.read_text(encoding="utf-8") == '{"tasks": "fixed"}'
    stored = ImplementationResult.model_validate_json(
        runner.artifacts.store[f"dag-task:{task_id}"]
    )
    assert stored.files_modified == [canonical_path]
    assert ArtifactRepairResult in runner.output_types
    product_reconcile = json.loads(
        runner.artifacts.store[
            "dag-task-product-reconcile:dag-repair:g30:retry-1:"
            "stale-source-fragment"
        ]
    )
    assert product_reconcile["skipped"][0]["reason"] == (
        "no_canonical_product_files_reported_by_product_repair"
    )
    repair = json.loads(
        runner.artifacts.store[
            "dag-artifact-repair:g30:stale-source-fragment:retry-1"
        ]
    )
    assert repair["artifact_update_application"]["applied_target_updates"][0][
        "path"
    ] == str(fragment)
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g30:retry-1"])
    assert dispatch["dag_task_product_cleanup_group_count"] == 1
    assert dispatch["dag_task_product_cleanup_artifact_followup_count"] == 1


def test_dag_artifact_closure_scans_real_g30_artifact_shapes(tmp_path):
    feature = SimpleNamespace(id="feat-g30-closure", slug="g30-closure", metadata={})
    artifact_root = tmp_path / ".iriai" / "artifacts" / "features" / feature.id
    stale_path = (
        "iriai-studio/src/vs/workbench/contrib/studioWorkflow/browser/"
        "workflowTab/chat/index.ts"
    )
    stale_text = f"stale file_scope path {stale_path}\n"
    for rel in [
        "subfeatures/chat-sidepane-shell/dag.md",
        "subfeatures/chat-sidepane-shell/dag-fragments/slice-1.json",
        "subfeatures/chat-sidepane-shell/dag-fragments/slice-4.json",
        "subfeatures/chat-sidepane-shell/plan.md",
        "dag.md",
        "dag-ws-WS-E-chat-sidepane-shell-slice-7.md",
        "dag/dag-ws-WS-E-chat-sidepane-shell-slice-14-tasks.md",
        "outputs/dag-ws-WS-E-chat-sidepane-shell-slice-2-target-only.md",
        "compile-sources-dag-chunk-11.md",
        ".iriai-context/g30-expanded-verify-r1-task-specs.md",
        "subfeatures/chat-sidepane-shell/system-design-source.md",
    ]:
        target = artifact_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(stale_text, encoding="utf-8")

    class _Mirror:
        def feature_dir(self, feature_id: str):
            assert feature_id == feature.id
            return artifact_root

    runner = SimpleNamespace(services={"artifact_mirror": _Mirror()})
    task_id = "chat-sidepane-shell-slice-1-T-sf11-s1-003"
    scan = implementation_module._dag_artifact_closure_scan(
        runner,
        feature,
        30,
        [
            ImplementationTask(
                id=task_id,
                name="Task",
                description="Task",
                subfeature_id="chat-sidepane-shell",
            )
        ],
        [
            {
                "task_id": task_id,
                "artifact_key": f"dag-task:{task_id}",
                "path": stale_path,
                "reason": "forbidden_task_spec",
                "forbidden_rule": (
                    "src/vs/workbench/contrib/studioWorkflow/browser/"
                    "workflowTab/chat"
                ),
                "source_artifact_ref": "dag-fragment:chat-sidepane-shell:slice-1",
            }
        ],
    )

    blocking = {item["relative_path"] for item in scan.blocking_targets}
    assert "subfeatures/chat-sidepane-shell/dag.md" in blocking
    assert "subfeatures/chat-sidepane-shell/dag-fragments/slice-1.json" in blocking
    assert "subfeatures/chat-sidepane-shell/dag-fragments/slice-4.json" in blocking
    assert "subfeatures/chat-sidepane-shell/plan.md" in blocking
    assert "dag.md" in blocking
    assert "dag-ws-WS-E-chat-sidepane-shell-slice-7.md" in blocking
    assert "dag/dag-ws-WS-E-chat-sidepane-shell-slice-14-tasks.md" in blocking
    assert "outputs/dag-ws-WS-E-chat-sidepane-shell-slice-2-target-only.md" in blocking
    assert "compile-sources-dag-chunk-11.md" in blocking
    assert ".iriai-context/g30-expanded-verify-r1-task-specs.md" in blocking
    assert scan.target_refs()
    advisory = {item["relative_path"] for item in scan.advisory_residuals}
    assert "subfeatures/chat-sidepane-shell/system-design-source.md" in advisory


def test_dag_artifact_closure_ignores_canonical_candidate_paths(tmp_path):
    feature = SimpleNamespace(id="feat-g30-canonical-closure", slug="g30-canonical", metadata={})
    artifact_root = tmp_path / ".iriai" / "artifacts" / "features" / feature.id
    stale_path = (
        "iriai-studio/src/vs/workbench/contrib/studioWorkflow/browser/"
        "workflowTab/chat/index.ts"
    )
    retired_prefix = (
        "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat"
    )
    canonical_paths = [
        "src/webviews/projectSurface/src/chat/index.ts",
        "src/webviews/projectSurface/src/chat/stores/index.ts",
        "iriai_studio_backend/lifecycle/events.py",
    ]
    stale_fragment = artifact_root / "subfeatures/chat-sidepane-shell/dag.md"
    stale_fragment.parent.mkdir(parents=True, exist_ok=True)
    stale_fragment.write_text(
        f"retired {stale_path}\ncanonical {canonical_paths[0]}",
        encoding="utf-8",
    )
    canonical_fragment = (
        artifact_root / "subfeatures/chat-sidepane-shell/dag-fragments/slice-1.json"
    )
    canonical_fragment.parent.mkdir(parents=True, exist_ok=True)
    canonical_fragment.write_text("\n".join(canonical_paths), encoding="utf-8")
    context_manifest = (
        artifact_root / ".iriai-context/g30-expanded-verify-r0-context-manifest.md"
    )
    context_manifest.parent.mkdir(parents=True, exist_ok=True)
    context_manifest.write_text(
        ".iriai-context/g30-expanded-verify-r0-task-specs.md\n"
        + "\n".join(canonical_paths),
        encoding="utf-8",
    )

    class _Mirror:
        def feature_dir(self, feature_id: str):
            assert feature_id == feature.id
            return artifact_root

    runner = SimpleNamespace(services={"artifact_mirror": _Mirror()})
    task_id = "chat-sidepane-shell-slice-1-T-sf11-s1-003"
    scan = implementation_module._dag_artifact_closure_scan(
        runner,
        feature,
        30,
        [
            ImplementationTask(
                id=task_id,
                name="Task",
                description="Task",
                subfeature_id="chat-sidepane-shell",
            )
        ],
        [
            {
                "task_id": task_id,
                "artifact_key": f"dag-task:{task_id}",
                "path": stale_path,
                "reason": "forbidden_task_spec",
                "forbidden_rule": retired_prefix,
                "forbidden_path": retired_prefix,
                "source_artifact_ref": "dag-fragment:chat-sidepane-shell:slice-1",
                "candidate_evidence": [
                    {"path": path, "exists": True, "source": "canonical"}
                    for path in canonical_paths[:2]
                ],
            },
            {
                "path": canonical_paths[2],
                "reason": "missing",
                "candidate_evidence": [],
            },
        ],
    )

    assert [item["relative_path"] for item in scan.blocking_targets] == [
        "subfeatures/chat-sidepane-shell/dag.md"
    ]
    blocking_signatures = {
        signature
        for item in scan.blocking_targets
        for signature in item["stale_signatures"]
    }
    assert all(
        "projectSurface" not in signature for signature in blocking_signatures
    )
    assert all(
        "iriai_studio_backend" not in signature
        for signature in blocking_signatures
    )
    ignored_paths = {item["relative_path"] for item in scan.ignored_matches}
    assert "subfeatures/chat-sidepane-shell/dag-fragments/slice-1.json" in ignored_paths
    assert ".iriai-context/g30-expanded-verify-r0-context-manifest.md" in ignored_paths
    assert any(
        record["kind"] == "candidate_evidence" and record["blocking"] is False
        for record in scan.signature_records
    )


def test_dag_closure_path_problems_are_scoped_to_planned_group():
    stale_problem = {
        "task_id": "chat-sidepane-shell-slice-1-T-sf11-s1-003",
        "artifact_key": "dag-task:chat-sidepane-shell-slice-1-T-sf11-s1-003",
        "path": (
            "iriai-studio/src/vs/workbench/contrib/studioWorkflow/browser/"
            "workflowTab/chat/index.ts"
        ),
        "reason": "forbidden_task_spec",
        "forbidden_rule": (
            "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat"
        ),
        "source_artifact_ref": "dag-fragment:chat-sidepane-shell:slice-1",
    }
    backend_problem = {
        "path": "iriai_studio_backend/lifecycle/events.py",
        "reason": "missing",
        "repair_route": "artifact_only",
    }
    group_tasks = [
        ImplementationTask(
            id="chat-sidepane-shell-slice-1-T-sf11-s1-003",
            name="Task",
            description="Task",
            subfeature_id="chat-sidepane-shell",
        )
    ]
    changed_files = implementation_module.PlannedBugGroup(
        group=BugGroup(
            group_id="changed-files-backend-path-prefix",
            likely_root_cause="changed-files evidence used a backend prefix",
            issue_indices=[0],
            severity="major",
        ),
        rca=RootCauseAnalysis(
            hypothesis="The changed-files context mentions the backend file.",
            affected_files=["iriai_studio_backend/lifecycle/events.py"],
            proposed_approach="Repair the changed-files context only.",
            confidence="contradiction",
        ),
        issue_text="iriai_studio_backend/lifecycle/events.py is in changed-files.",
        rca_key="rca:changed-files",
    )
    stale_group = implementation_module.PlannedBugGroup(
        group=BugGroup(
            group_id="dag-stale-forbidden-paths",
            likely_root_cause="stale forbidden DAG task paths",
            issue_indices=[1],
            severity="blocker",
        ),
        rca=RootCauseAnalysis(
            hypothesis="Stale DAG artifacts report retired chat paths.",
            affected_files=[],
            proposed_approach="Repair all stale DAG artifacts.",
            confidence="contradiction",
        ),
        issue_text="Retired chat task specs are stale.",
        rca_key="rca:stale",
    )

    scoped = implementation_module._dag_closure_path_problems_for_planned(
        changed_files,
        [stale_problem, backend_problem],
        group_tasks,
    )
    assert scoped == [backend_problem]
    assert implementation_module._dag_closure_blocking_signatures(
        implementation_module._dag_closure_signature_records_from_path_problems(scoped)
    ) == []

    umbrella = implementation_module._dag_closure_path_problems_for_planned(
        stale_group,
        [stale_problem, backend_problem],
        group_tasks,
    )
    assert umbrella == [stale_problem, backend_problem]


@pytest.mark.asyncio
async def test_dag_artifact_repair_closure_blocks_partial_repair(tmp_path):
    feature = SimpleNamespace(id="feat-g30-partial-closure", slug="g30-partial", metadata={})
    artifact_root = tmp_path / ".iriai" / "artifacts" / "features" / feature.id
    fragment = artifact_root / "subfeatures/chat-sidepane-shell/dag-fragments/slice-1.json"
    downstream = artifact_root / "dag/dag-ws-WS-E-chat-sidepane-shell-slice-14-tasks.md"
    stale_path = (
        "iriai-studio/src/vs/workbench/contrib/studioWorkflow/browser/"
        "workflowTab/chat/index.ts"
    )
    for path in [fragment, downstream]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"stale {stale_path}", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Mirror:
        def feature_dir(self, feature_id: str):
            assert feature_id == feature.id
            return artifact_root

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": _Mirror()}
            self.prompts: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.prompts.append(task.prompt)
            return ArtifactRepairResult(
                task_id="ARTIFACT-PARTIAL",
                group_id="dag-fragment-stale-paths",
                summary="only repaired first fragment",
                artifact_updates=[
                    ArtifactRepairUpdate(
                        target_ref=str(fragment),
                        content='{"tasks": "fixed"}',
                    )
                ],
            )

    runner = _Runner()
    task_id = "chat-sidepane-shell-slice-1-T-sf11-s1-003"
    planned = implementation_module.PlannedBugGroup(
        group=BugGroup(
            group_id="dag-fragment-stale-paths",
            likely_root_cause="stale DAG artifact paths",
            issue_indices=[0],
            severity="major",
        ),
        rca=RootCauseAnalysis(
            hypothesis="source DAG artifacts still contain retired chat paths",
            affected_files=[str(fragment)],
            proposed_approach="repair stale DAG source artifacts",
            confidence="contradiction",
        ),
        issue_text="stale DAG fragment",
        rca_key="rca:g30",
    )
    result, synthetic, record = await implementation_module._run_dag_artifact_repair_lane(
        runner,
        feature,
        30,
        1,
        planned,
        implementation_module.DagContradictionResolution(
            resolution="repair stale artifact",
            resolution_kind="artifact_repair",
            authoritative_sources=["preflight"],
            artifact_paths=[str(fragment)],
            confidence="high",
        ),
        {"artifact_key": "contradiction:g30"},
        group_tasks=[
            ImplementationTask(
                id=task_id,
                name="Task",
                description="Task",
                subfeature_id="chat-sidepane-shell",
            )
        ],
        feature_root=tmp_path / "repos",
        runtime="primary",
        feedback="preflight failed",
        fix_context="",
        closure_path_problems=[
            {
                "task_id": task_id,
                "artifact_key": f"dag-task:{task_id}",
                "path": stale_path,
                "reason": "forbidden_task_spec",
                "forbidden_rule": (
                    "src/vs/workbench/contrib/studioWorkflow/browser/"
                    "workflowTab/chat"
                ),
                "source_artifact_ref": "dag-fragment:chat-sidepane-shell:slice-1",
            }
        ],
    )

    assert result.status == "blocked"
    assert synthetic.status == "blocked"
    assert fragment.read_text(encoding="utf-8") == '{"tasks": "fixed"}'
    closure = json.loads(
        runner.artifacts.store[
            "dag-artifact-closure:g30:retry-1:dag-fragment-stale-paths"
        ]
    )
    assert closure["status"] == "blocked"
    assert closure["blocking_residuals"][0]["relative_path"] == (
        "dag/dag-ws-WS-E-chat-sidepane-shell-slice-14-tasks.md"
    )
    assert str(downstream) in runner.prompts[0]
    assert record["closure_target_refs"]


@pytest.mark.asyncio
async def test_artifact_repair_deletes_generated_context_snapshot_only(tmp_path):
    feature = SimpleNamespace(id="feat-delete-context", slug="delete-context", metadata={})
    feature_root = tmp_path / "repos"
    artifact_root = tmp_path / ".iriai" / "artifacts" / "features" / feature.id
    generated = artifact_root / ".iriai-context/g30-expanded-verify-r1-task-specs.md"
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_text("stale snapshot", encoding="utf-8")
    source_dag = artifact_root / "dag.md"
    source_dag.write_text("source dag", encoding="utf-8")
    product = feature_root / "iriai-studio/src/product.ts"
    product.parent.mkdir(parents=True, exist_ok=True)
    product.write_text("product", encoding="utf-8")

    class _Artifacts:
        async def put(self, key: str, value: str, *, feature):
            del key, value, feature

    class _Mirror:
        def feature_dir(self, feature_id: str):
            assert feature_id == feature.id
            return artifact_root

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": _Mirror()},
    )
    record = await implementation_module._apply_dag_artifact_repair_updates(
        runner,
        feature,
        ArtifactRepairResult(
            task_id="ARTIFACT-DELETE",
            group_id="G30",
            summary="delete generated snapshot",
            artifacts_deleted=[
                ".iriai-context/g30-expanded-verify-r1-task-specs.md",
                "dag.md",
                "iriai-studio/src/product.ts",
            ],
        ),
        feature_root,
    )

    assert not generated.exists()
    assert source_dag.exists()
    assert product.exists()
    assert record["deleted_artifacts"][0]["normalized_ref"] == (
        ".iriai-context/g30-expanded-verify-r1-task-specs.md"
    )
    assert [item["reason"] for item in record["skipped_deletes"]] == [
        "target_ref_delete_not_generated_or_staging_artifact",
        "target_ref_not_artifact_context"
    ]


class _RecordingArtifacts:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, object]]] = {}
        self.next_id = 1

    async def get(self, key: str, *, feature):
        del feature
        rows = self.rows.get(key, [])
        return rows[-1]["value"] if rows else ""

    async def get_record(self, key: str, *, feature):
        del feature
        rows = self.rows.get(key, [])
        if not rows:
            return None
        row = rows[-1]
        value = str(row["value"])
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "value": value,
            "sha256": __import__("hashlib").sha256(
                value.encode("utf-8")
            ).hexdigest(),
        }

    async def put(self, key: str, value: str, *, feature):
        del feature
        self.rows.setdefault(key, []).append({
            "id": self.next_id,
            "created_at": f"t{self.next_id}",
            "value": value,
        })
        self.next_id += 1


@pytest.mark.asyncio
async def test_dag_task_reconciler_appends_full_id_row_from_expected_files(tmp_path):
    feature = SimpleNamespace(id="feat-reconcile-g29", slug="reconcile-g29", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    expected_paths = [
        "src/vs/workbench/contrib/studioBridge/test/browser/reconnect.integrationTest.ts",
        "src/vs/workbench/contrib/studioBridge/test/browser/fixtures/reconnectFixtures.ts",
    ]
    for path in expected_paths:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {"path": path, "source": "TASK-SF4-S18-4 canonical"}
                for path in expected_paths
            ],
            "forbidden_files": [
                {"path": "src/vs/workbench/contrib/iriaiStudio", "source": "retired"}
            ],
        }),
        encoding="utf-8",
    )

    task_id = "project-and-launcher-slice-18-TASK-SF4-S18-4"
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale",
        files_created=[
            "src/vs/workbench/contrib/iriaiStudio/test/integration/reconnect.test.ts",
            "src/vs/workbench/contrib/iriaiStudio/test/integration/fixtures/"
            "reconnectFixtures.ts",
        ],
    )
    artifacts = _RecordingArtifacts()
    await artifacts.put(f"dag-task:{task_id}", stale.model_dump_json(), feature=feature)
    runner = SimpleNamespace(artifacts=artifacts, services={})
    task = ImplementationTask(id=task_id, name="Task", description="Task")

    outcome = await implementation_module._reconcile_dag_task_results(
        runner,
        feature,
        29,
        "retry-1",
        [task],
        results=[stale],
        verify_results_context=[stale],
        all_results=[stale],
        repair_results=[],
        feature_root=feature_root,
    )

    stored = ImplementationResult.model_validate_json(
        await artifacts.get(f"dag-task:{task_id}", feature=feature)
    )
    assert stored.task_id == task_id
    assert stored.files_created == expected_paths
    assert len(artifacts.rows[f"dag-task:{task_id}"]) == 2
    assert outcome.results == [stored]
    assert outcome.verify_results_context == [stored]
    assert outcome.all_results == [stored]
    assert outcome.report["applied"][0]["action"] == "appended_dag_task_row"

    preflight = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        29,
        "retry-2",
        [task],
        outcome.verify_results_context,
        feature_root=feature_root,
    )
    assert preflight is None


@pytest.mark.asyncio
async def test_dag_task_reconciler_replaces_stale_and_current_same_task_results(tmp_path):
    feature = SimpleNamespace(id="feat-reconcile-memory", slug="reconcile-memory", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    canonical = repo / "src/webviews/projectSurface/src/styles/dashboard.css"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {
                    "path": "src/vs/workbench/contrib/iriaiStudio",
                    "source": "retired",
                }
            ]
        }),
        encoding="utf-8",
    )

    task_id = "TASK-S18-3"
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale",
        files_modified=[
            "src/vs/workbench/contrib/iriaiStudio/browser/styles/workflow-card.css"
        ],
    )
    corrected = ImplementationResult(
        task_id=task_id,
        summary="corrected",
        files_modified=["src/webviews/projectSurface/src/styles/dashboard.css"],
    )
    artifacts = _RecordingArtifacts()
    await artifacts.put(f"dag-task:{task_id}", stale.model_dump_json(), feature=feature)
    runner = SimpleNamespace(artifacts=artifacts, services={})
    task = ImplementationTask(id=task_id, name="Task", description="Task")

    outcome = await implementation_module._reconcile_dag_task_results(
        runner,
        feature,
        28,
        "retry-1",
        [task],
        results=[stale],
        verify_results_context=[stale, corrected],
        all_results=[stale, corrected],
        repair_results=[corrected],
        feature_root=feature_root,
    )

    assert outcome.verify_results_context == [corrected]
    assert outcome.results == [corrected]
    assert outcome.all_results == [corrected]


@pytest.mark.asyncio
async def test_artifact_repair_update_normalizes_short_task_alias(tmp_path):
    feature = SimpleNamespace(id="feat-short-alias", slug="short-alias", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    canonical = repo / "src/vs/workbench/contrib/studioBridge/test/browser/reconnect.integrationTest.ts"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")

    artifacts = _RecordingArtifacts()
    runner = SimpleNamespace(artifacts=artifacts, services={})
    full_task_id = "project-and-launcher-slice-18-TASK-SF4-S18-4"
    short_result = ImplementationResult(
        task_id="TASK-SF4-S18-4",
        summary="short alias",
        files_created=[
            "src/vs/workbench/contrib/studioBridge/test/browser/"
            "reconnect.integrationTest.ts"
        ],
    )

    record = await implementation_module._apply_dag_artifact_repair_updates(
        runner,
        feature,
        ArtifactRepairResult(
            task_id="ARTIFACT-REPAIR",
            group_id="G29",
            summary="repair short alias",
            artifact_updates=[
                ArtifactRepairUpdate(
                    artifact_key=f"dag-task:{full_task_id}",
                    content=short_result.model_dump_json(),
                )
            ],
        ),
        feature_root,
    )

    stored = ImplementationResult.model_validate_json(
        await artifacts.get(f"dag-task:{full_task_id}", feature=feature)
    )
    assert stored.task_id == full_task_id
    assert record["applied_updates"][0]["task_id"] == full_task_id


@pytest.mark.asyncio
async def test_dag_task_reconciler_rejects_forbidden_existing_candidate(tmp_path):
    feature = SimpleNamespace(id="feat-reconcile-forbidden", slug="reconcile-forbidden", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    forbidden = repo / "src/vs/workbench/contrib/iriaiStudio/test/integration/reconnect.test.ts"
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("bad", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {"path": "src/vs/workbench/contrib/iriaiStudio", "source": "retired"}
            ]
        }),
        encoding="utf-8",
    )

    task_id = "TASK-S18-4"
    candidate = ImplementationResult(
        task_id=task_id,
        summary="forbidden",
        files_created=[
            "src/vs/workbench/contrib/iriaiStudio/test/integration/reconnect.test.ts"
        ],
    )
    artifacts = _RecordingArtifacts()
    runner = SimpleNamespace(artifacts=artifacts, services={})
    task = ImplementationTask(id=task_id, name="Task", description="Task")

    outcome = await implementation_module._reconcile_dag_task_results(
        runner,
        feature,
        29,
        "retry-1",
        [task],
        results=[candidate],
        verify_results_context=[candidate],
        all_results=[candidate],
        repair_results=[candidate],
        feature_root=feature_root,
    )

    assert f"dag-task:{task_id}" not in artifacts.rows
    assert outcome.report["skipped"]


@pytest.mark.asyncio
async def test_dag_task_reconciler_idempotent_when_latest_row_is_valid(tmp_path):
    feature = SimpleNamespace(id="feat-reconcile-idempotent", slug="reconcile-idempotent", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    canonical = repo / "src/webviews/projectSurface/src/styles/dashboard.css"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")
    task_id = "TASK-S18-3"
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale",
        files_modified=["src/missing.css"],
    )
    current = ImplementationResult(
        task_id=task_id,
        summary="current",
        files_modified=["src/webviews/projectSurface/src/styles/dashboard.css"],
    )
    artifacts = _RecordingArtifacts()
    await artifacts.put(f"dag-task:{task_id}", current.model_dump_json(), feature=feature)
    runner = SimpleNamespace(artifacts=artifacts, services={})
    task = ImplementationTask(id=task_id, name="Task", description="Task")

    outcome = await implementation_module._reconcile_dag_task_results(
        runner,
        feature,
        28,
        "initial",
        [task],
        results=[stale],
        verify_results_context=[stale],
        all_results=[stale],
        repair_results=[],
        feature_root=feature_root,
    )

    assert len(artifacts.rows[f"dag-task:{task_id}"]) == 1
    assert outcome.results == [current]
    assert outcome.report["applied"][0]["action"] == "already_current"


@pytest.mark.asyncio
async def test_dag_task_reconciler_latest_valid_db_wins_over_competing_candidates(
    tmp_path,
):
    feature = SimpleNamespace(
        id="feat-reconcile-latest-wins",
        slug="reconcile-latest-wins",
        metadata={},
    )
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    canonical_paths = [
        "src/webviews/projectSurface/src/chat/stores/types.ts",
        "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts",
        "src/webviews/projectSurface/src/chat/stores/useChatStreamStore.ts",
        "src/webviews/projectSurface/src/chat/stores/index.ts",
        "src/webviews/projectSurface/src/chat/stores/__tests__/"
        "EventDeduplicator.test.ts",
        "src/webviews/projectSurface/src/chat/stores/__tests__/"
        "useChatStreamStore.test.ts",
    ]
    for path in canonical_paths:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {"path": path, "source": "TASK-SH2-1 canonical"}
                for path in canonical_paths
            ],
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/studioWorkflow/browser/"
                        "workflowTab/chat/stores"
                    ),
                    "source": "retired chat stores",
                }
            ],
        }),
        encoding="utf-8",
    )

    task_id = "chat-sidepane-shell-slice-2-TASK-SH2-1"
    stale_paths = [
        path.replace(
            "src/webviews/projectSurface/src/chat/stores",
            "src/vs/workbench/contrib/studioWorkflow/browser/"
            "workflowTab/chat/stores",
        )
        for path in canonical_paths
    ]
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale",
        files_created=stale_paths,
    )
    latest_db = ImplementationResult(
        task_id=task_id,
        summary="latest canonical DB row",
        files_created=canonical_paths,
    )
    competing_repair = ImplementationResult(
        task_id=task_id,
        summary="repair with equivalent repo-prefixed paths",
        files_created=[f"iriai-studio/{path}" for path in canonical_paths],
    )
    artifacts = _RecordingArtifacts()
    await artifacts.put(
        f"dag-task:{task_id}",
        latest_db.model_dump_json(),
        feature=feature,
    )
    runner = SimpleNamespace(artifacts=artifacts, services={})
    task = ImplementationTask(id=task_id, name="Task", description="Task")

    outcome = await implementation_module._reconcile_dag_task_results(
        runner,
        feature,
        29,
        "retry-1",
        [task],
        results=[stale],
        verify_results_context=[stale, competing_repair],
        all_results=[stale, competing_repair],
        repair_results=[competing_repair],
        feature_root=feature_root,
    )

    assert len(artifacts.rows[f"dag-task:{task_id}"]) == 1
    assert outcome.results == [latest_db]
    assert outcome.verify_results_context == [latest_db]
    assert outcome.all_results == [latest_db]
    assert outcome.report["blockers"] == []
    assert outcome.report["applied"][0]["source"] == "latest_db"
    assert outcome.report["applied"][0]["action"] == "already_current"

    preflight = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        29,
        "retry-2",
        [task],
        outcome.verify_results_context,
        feature_root=feature_root,
    )
    assert preflight is None


@pytest.mark.asyncio
async def test_dag_task_reconciler_preserves_valid_original_files_with_replacement(tmp_path):
    feature = SimpleNamespace(id="feat-reconcile-merge", slug="reconcile-merge", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    original_path = "src/webviews/projectSurface/src/dashboard/components/WorkflowCard.tsx"
    replacement_path = "src/webviews/projectSurface/src/styles/dashboard.css"
    for path in (original_path, replacement_path):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")

    task_id = "TASK-MERGE"
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale with one valid path",
        files_modified=[
            original_path,
            "src/vs/workbench/contrib/iriaiStudio/browser/styles/workflow-card.css",
        ],
    )
    replacement = ImplementationResult(
        task_id=task_id,
        summary="replacement evidence",
        files_modified=[replacement_path],
    )
    artifacts = _RecordingArtifacts()
    await artifacts.put(f"dag-task:{task_id}", stale.model_dump_json(), feature=feature)
    runner = SimpleNamespace(artifacts=artifacts, services={})
    task = ImplementationTask(id=task_id, name="Task", description="Task")

    outcome = await implementation_module._reconcile_dag_task_results(
        runner,
        feature,
        30,
        "retry-1",
        [task],
        results=[stale],
        verify_results_context=[stale, replacement],
        all_results=[stale, replacement],
        repair_results=[replacement],
        feature_root=feature_root,
    )

    stored = ImplementationResult.model_validate_json(
        await artifacts.get(f"dag-task:{task_id}", feature=feature)
    )
    assert stored.files_modified == [original_path, replacement_path]
    assert outcome.results == [stored]
    assert outcome.report["applied"][0]["action"] == "appended_dag_task_row"


@pytest.mark.asyncio
async def test_parallel_dag_repair_rejects_unsafe_artifact_repair_and_persists_reason(
    tmp_path,
):
    feature = SimpleNamespace(id="feat-rejected-artifact", slug="rejected-artifact", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True}

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-BAD-ARTIFACT",
                        likely_root_cause="unsafe artifact repair",
                        issue_indices=[0],
                        severity="major",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="claims artifact repair but names source",
                    affected_files=["pkg/source.py"],
                    proposed_approach="patch source as artifact repair",
                    confidence="contradiction",
                    contradiction_detail="bad layer",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                return implementation_module.DagContradictionResolution(
                    resolution="Patch source through artifact repair.",
                    resolution_kind="artifact_repair",
                    authoritative_sources=["pkg/source.py:1"],
                    implementation_direction="Patch pkg/source.py.",
                    confidence="high",
                    needs_human=False,
                )
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    runner = _Runner()
    result = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        11,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="unsafe artifact repair"),
                Issue(severity="major", description="same root cause"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert result is None
    rejected = json.loads(
        runner.artifacts.store[
            "contradiction-rejected:dag-repair:g11:retry-0:BG-BAD-ARTIFACT"
        ]
    )
    assert "artifact_repair_has_non_artifact_paths" in rejected["rejection_reasons"]
    assert rejected["raw_resolution"]["resolution_kind"] == "artifact_repair"
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g11:retry-0"])
    assert dispatch["rejected_contradiction_count"] == 1
    assert dispatch["fallback_reason"] == "unresolved_contradiction"


@pytest.mark.asyncio
async def test_parallel_dag_repair_human_needed_contradiction_fails_closed(tmp_path):
    feature = SimpleNamespace(id="feat-human-needed", slug="human-needed", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True}
            self.fix_attempted = False

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-CONTRA",
                        likely_root_cause="conflict",
                        issue_indices=[0],
                        severity="major",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="conflict",
                    affected_files=["pkg/code.py"],
                    proposed_approach="decide",
                    confidence="contradiction",
                    contradiction_detail="A vs B",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                return implementation_module.DagContradictionResolution(
                    resolution="",
                    authoritative_sources=[],
                    requires_code_change=False,
                    needs_human=True,
                    confidence="low",
                    rationale="Ambiguous.",
                )
            if task.output_type is ImplementationResult:
                self.fix_attempted = True
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    runner = _Runner()
    result = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        7,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="conflict"),
                Issue(severity="major", description="second issue"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert result is None
    assert runner.fix_attempted is False
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g7:retry-0"])
    assert dispatch["fallback_reason"] == "unresolved_contradiction"
    assert dispatch["human_needed_contradiction_count"] == 1


@pytest.mark.asyncio
async def test_parallel_dag_repair_human_needed_does_not_block_unrelated_fix(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-human-plus-fix", slug="human-plus-fix", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True}
            self.fix_attempted = False

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-HUMAN",
                        likely_root_cause="ambiguous product decision",
                        issue_indices=[0],
                        severity="major",
                    ),
                    BugGroup(
                        group_id="BG-FIX",
                        likely_root_cause="ordinary bug",
                        issue_indices=[1],
                        severity="major",
                    ),
                ])
            if task.output_type is RootCauseAnalysis:
                if "BG-HUMAN" in task.actor.name:
                    return RootCauseAnalysis(
                        hypothesis="ambiguous product behavior",
                        affected_files=["pkg/decision.py"],
                        proposed_approach="ask human",
                        confidence="contradiction",
                        contradiction_detail="A vs B",
                    )
                return RootCauseAnalysis(
                    hypothesis="ordinary bug",
                    affected_files=["pkg/fix.py"],
                    proposed_approach="patch it",
                    confidence="high",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                return implementation_module.DagContradictionResolution(
                    resolution="A human must choose behavior.",
                    resolution_kind="needs_human",
                    authoritative_sources=["pkg/spec_a.md", "pkg/spec_b.md"],
                    needs_human=True,
                    confidence="medium",
                )
            if task.output_type is ImplementationResult:
                self.fix_attempted = True
                return ImplementationResult(
                    task_id="FIX-BG-FIX",
                    summary="fixed unrelated bug",
                    files_modified=["pkg/fix.py"],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        12,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="ambiguous behavior"),
                Issue(severity="major", description="ordinary bug"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert runner.fix_attempted is True
    assert results is not None
    assert [result.task_id for result in results] == ["FIX-BG-FIX"]
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g12:retry-0"])
    assert dispatch["fallback_reason"] == ""
    assert dispatch["human_needed_contradiction_count"] == 1
    assert dispatch["blocked_fix_group_ids"] == []
    assert dispatch["schedule"] == [{"round": 0, "group_ids": ["BG-FIX"]}]


@pytest.mark.asyncio
async def test_parallel_dag_repair_human_needed_blocks_overlapping_fix(tmp_path):
    feature = SimpleNamespace(id="feat-human-overlap", slug="human-overlap", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True}
            self.fix_attempted = False

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-HUMAN",
                        likely_root_cause="ambiguous shared file",
                        issue_indices=[0],
                        severity="major",
                    ),
                    BugGroup(
                        group_id="BG-FIX",
                        likely_root_cause="ordinary bug same file",
                        issue_indices=[1],
                        severity="major",
                    ),
                ])
            if task.output_type is RootCauseAnalysis:
                if "BG-HUMAN" in task.actor.name:
                    return RootCauseAnalysis(
                        hypothesis="ambiguous product behavior",
                        affected_files=["pkg/shared.py"],
                        proposed_approach="ask human",
                        confidence="contradiction",
                        contradiction_detail="A vs B",
                    )
                return RootCauseAnalysis(
                    hypothesis="ordinary bug same file",
                    affected_files=["pkg/shared.py"],
                    proposed_approach="patch it",
                    confidence="high",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                return implementation_module.DagContradictionResolution(
                    resolution="A human must choose behavior.",
                    resolution_kind="needs_human",
                    authoritative_sources=["pkg/spec_a.md", "pkg/spec_b.md"],
                    needs_human=True,
                    confidence="medium",
                )
            if task.output_type is ImplementationResult:
                self.fix_attempted = True
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        13,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="ambiguous behavior"),
                Issue(severity="major", description="ordinary bug same file"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is None
    assert runner.fix_attempted is False
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g13:retry-0"])
    assert dispatch["fallback_reason"] == "unresolved_contradiction"
    assert dispatch["blocked_fix_group_ids"] == ["BG-FIX"]


@pytest.mark.asyncio
async def test_parallel_dag_repair_records_failed_fix_agent_without_crashing(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-fix-agent-error", slug="fix-agent-error", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True}
            self.parallel_batches: list[list[str]] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-OK",
                        likely_root_cause="ordinary bug",
                        issue_indices=[0],
                        severity="major",
                    ),
                    BugGroup(
                        group_id="BG-FAIL",
                        likely_root_cause="quota failure while fixing",
                        issue_indices=[1],
                        severity="major",
                    ),
                ])
            if task.output_type is RootCauseAnalysis:
                if "BG-FAIL" in task.actor.name:
                    return RootCauseAnalysis(
                        hypothesis="quota prone fix",
                        affected_files=["pkg/fail.py"],
                        proposed_approach="patch fail",
                        confidence="high",
                    )
                return RootCauseAnalysis(
                    hypothesis="ordinary bug",
                    affected_files=["pkg/ok.py"],
                    proposed_approach="patch ok",
                    confidence="high",
                )
            if task.output_type is ImplementationResult:
                if "BG-FAIL" in task.actor.name:
                    raise RuntimeError("Claude pool quota exhausted")
                return ImplementationResult(
                    task_id="FIX-BG-OK",
                    summary="fixed ok bug",
                    files_modified=["pkg/ok.py"],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            self.parallel_batches.append([task.actor.name for task in tasks])
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        14,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="ordinary bug"),
                Issue(severity="major", description="quota-prone bug"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    by_id = {result.task_id: result for result in results}
    assert by_id["FIX-BG-OK"].status == "completed"
    failed = by_id["DAG-REPAIR-FAILED-g14-r0-BG-FAIL"]
    assert failed.status == "blocked"
    assert "Claude pool quota exhausted" in failed.summary
    error_key = "dag-repair-fix-error:g14:BG-FAIL:retry-0:round-0"
    assert error_key in runner.artifacts.store
    error_record = json.loads(runner.artifacts.store[error_key])
    assert error_record["status"] == "blocked"


@pytest.mark.asyncio
async def test_parallel_dag_repair_non_autonomous_preserves_manual_fallback(tmp_path):
    feature = SimpleNamespace(id="feat-manual", slug="manual", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {}
            self.fix_attempted = False

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-CONTRA",
                        likely_root_cause="conflict",
                        issue_indices=[0],
                        severity="major",
                    ),
                    BugGroup(
                        group_id="BG-FIX",
                        likely_root_cause="fix",
                        issue_indices=[1],
                        severity="major",
                    ),
                ])
            if task.output_type is RootCauseAnalysis:
                if "BG-CONTRA" in task.actor.name:
                    return RootCauseAnalysis(
                        hypothesis="conflict",
                        affected_files=["pkg/code.py"],
                        proposed_approach="decide",
                        confidence="contradiction",
                        contradiction_detail="A vs B",
                    )
                return RootCauseAnalysis(
                    hypothesis="fix",
                    affected_files=["pkg/fix.py"],
                    proposed_approach="patch",
                    confidence="high",
                )
            if task.output_type is ImplementationResult:
                self.fix_attempted = True
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    runner = _Runner()
    result = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        8,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="conflict"),
                Issue(severity="major", description="fixable"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert result is None
    assert runner.fix_attempted is False
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g8:retry-0"])
    assert dispatch["fallback_reason"] == "manual_contradiction_resolution_required"
    assert dispatch["fixable_group_count"] == 1


@pytest.mark.asyncio
async def test_contradiction_decisions_load_legacy_groups_above_nine():
    feature = SimpleNamespace(id="feat-legacy", slug="legacy", metadata={})
    dag = implementation_module.ImplementationDAG(
        execution_order=[[f"TASK-{idx}"] for idx in range(27)]
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            if key == "dag":
                return dag.model_dump_json()
            if key == "contradiction:verify:dag-g26-r0":
                return json.dumps({
                    "revision_plan": {
                        "requests": [
                            {"description": "Ratify @v1 event names for group 26."}
                        ],
                        "new_decisions": ["D-GR-X: @v1 names are authoritative."],
                    }
                })
            return ""

    runner = SimpleNamespace(artifacts=_Artifacts())

    context = await implementation_module._format_contradiction_decisions_context(
        runner, feature,
    )

    assert "contradiction:verify:dag-g26-r0" in context
    assert "Ratify @v1 event names for group 26." in context
    assert "D-GR-X: @v1 names are authoritative." in context
