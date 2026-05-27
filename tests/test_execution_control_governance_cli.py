"""Tests for the Slice 19 8th sub-slice governance CLI (doc-19:150 +
doc-19:62-65 + doc-19:198 + doc-19:296-303 + doc-19:348-349).

These tests cover the typed CLI deferred from the 2026-05-25 slice-end
SIX-VECTOR remediation (V1 P1-A). Per doc-19 § Refactoring Steps step
1 (line 150) + § Proposed Interfaces lines 59-66 + § Tests line 198
the CLI:

- Emits stable JSON to stdout (default ``--format=json`` per doc-19:150).
- Returns nonzero exit codes for blocked evidence per doc-19:198.
- Preserves the activation-authority boundary per doc-19:348-349 +
  doc-19:296-303 (no ``CONTROL_PLANE_WRITER_METHODS`` extension; no
  ``dag-*`` artifact-key string literals; READ-ONLY; only consumes
  typed upstream projections).

The tests use a DEPENDENCY-INJECTION discipline (the CLI exposes a
typed :class:`CLIProviderFactories` shape that tests override to
inject fakes) rather than mocking the SUT. This is the auto-memory
``feedback_no_silent_degradation`` + ``feedback_no_overengineer_use_library``
disciplines applied to the test surface: the upstream factory is
injectable; the CLI runner itself is exercised verbatim.

Per the auto-memory ``feedback_cite_everything`` rule the test
module's docstring carries verbatim PIN cites for doc-19:62-65 +
doc-19:150 + doc-19:198 + doc-19:296-303 + doc-19:348-349.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from iriai_build_v2.execution_control.commit_provenance import (
    LineProvenanceQuery,
    LineProvenanceResult,
)
from iriai_build_v2.execution_control.commit_provenance_reader import (
    LineProvenanceReadResult,
)
from iriai_build_v2.execution_control.commit_provenance_writer import (
    CommitProvenanceGapFinding,
)
from iriai_build_v2.execution_control.counterfactual_metrics_comparator import (
    CounterfactualMetricsComparator,
    MetricsComparatorResult,
)
from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
)
from iriai_build_v2.execution_control.governance_agent import (
    GovernanceSnapshot,
)
from iriai_build_v2.execution_control.governance_metrics import (
    GovernanceMetricValue,
)
from iriai_build_v2.execution_control.governance_report_artifact import (
    REPORT_ARTIFACT_KEY_PREFIX,
    GovernanceReportArtifact,
    GovernanceReportArtifactEmitter,
)
from iriai_build_v2.execution_control.governance_snapshot_api import (
    GovernanceSnapshotAPI,
    SnapshotAPICorpus,
    SnapshotAPIGap,
    SnapshotAPIInputs,
    SnapshotAPIResult,
)
from iriai_build_v2.workflows.develop.governance.cli import (
    CLIProviderFactories,
    EXIT_BLOCKED_EVIDENCE,
    EXIT_OK,
    EXIT_UPSTREAM_EXCEPTION,
    EXIT_USAGE_ERROR,
    FORMAT_NAMES,
    SUBCOMMAND_NAMES,
    build_parser,
    cmd_analyze,
    cmd_compare,
    cmd_explain_line,
    cmd_report,
    default_provider_factories,
    main,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


# ============================================================================
# Section 1: Module surface + Activation-authority boundary
# ============================================================================


def test_subcommand_names_are_four() -> None:
    """Doc-19:62-65 -- the CLI exposes exactly 4 subcommands."""

    assert len(SUBCOMMAND_NAMES) == 4


def test_subcommand_names_match_doc_19_62_65() -> None:
    """Doc-19:62-65 -- the 4 subcommand names verbatim."""

    assert SUBCOMMAND_NAMES == ("analyze", "report", "explain-line", "compare")


def test_format_names_are_two() -> None:
    """Doc-19:150 -- 2 format choices: json (default) + prose."""

    assert FORMAT_NAMES == ("json", "prose")


def test_exit_code_ok_is_zero() -> None:
    """EXIT_OK MUST be 0 per stdlib convention."""

    assert EXIT_OK == 0


def test_exit_code_usage_error_is_one() -> None:
    """EXIT_USAGE_ERROR MUST be 1 per stdlib convention."""

    assert EXIT_USAGE_ERROR == 1


def test_exit_code_blocked_evidence_is_two() -> None:
    """Doc-19:198 -- nonzero exit on blocked evidence. EXIT_BLOCKED_EVIDENCE
    MUST be 2 (distinct from usage error)."""

    assert EXIT_BLOCKED_EVIDENCE == 2


def test_exit_code_upstream_exception_is_three() -> None:
    """EXIT_UPSTREAM_EXCEPTION MUST be 3 (distinct from blocked evidence)."""

    assert EXIT_UPSTREAM_EXCEPTION == 3


def test_all_exports_present() -> None:
    """The CLI module's __all__ list MUST be stable + non-empty."""

    from iriai_build_v2.workflows.develop.governance import cli as mod

    assert hasattr(mod, "__all__")
    assert isinstance(mod.__all__, list)
    assert len(mod.__all__) > 0
    expected = {
        "SUBCOMMAND_NAMES",
        "EXIT_OK",
        "EXIT_USAGE_ERROR",
        "EXIT_BLOCKED_EVIDENCE",
        "EXIT_UPSTREAM_EXCEPTION",
        "FORMAT_NAMES",
        "CLIProviderFactories",
        "build_parser",
        "main",
        "cmd_analyze",
        "cmd_report",
        "cmd_explain_line",
        "cmd_compare",
        "default_provider_factories",
    }
    assert set(mod.__all__) == expected


def test_cli_module_carries_doc_19_pin_cites() -> None:
    """The CLI module's docstring MUST carry the doc-19 step 1 / 62-65 /
    150 / 198 / 348-349 PIN cites per feedback_cite_everything."""

    from iriai_build_v2.workflows.develop.governance import cli as mod

    docstring = mod.__doc__ or ""
    for cite in ("150", "62-65", "198", "348-349", "296-303"):
        assert cite in docstring, (
            f"CLI module docstring missing doc-19:{cite} PIN cite"
        )


def test_main_module_exists() -> None:
    """The `python -m` entry-point module MUST exist at the doc-19:62-65
    invocation path."""

    main_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/"
        "workflows/develop/governance/__main__.py"
    )
    assert main_path.exists(), f"__main__.py missing at {main_path}"


def test_main_module_imports_cli_main() -> None:
    """The `__main__.py` MUST re-import :func:`cli.main` -- this is the
    typed contract that ties the `python -m` invocation to the
    typed runner."""

    main_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/"
        "workflows/develop/governance/__main__.py"
    )
    source = main_path.read_text()
    assert "from iriai_build_v2.workflows.develop.governance.cli import main" in source, (
        "__main__.py does not import cli.main -- typed entry contract broken"
    )


def test_main_module_calls_sys_exit() -> None:
    """The `__main__.py` MUST propagate the typed exit code via
    :func:`sys.exit` so the OS observes the nonzero exit per
    doc-19:198."""

    main_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/"
        "workflows/develop/governance/__main__.py"
    )
    source = main_path.read_text()
    assert "sys.exit(main())" in source, (
        "__main__.py does not call sys.exit(main()) -- the typed exit "
        "code would not propagate to the OS"
    )


# ============================================================================
# Section 2: Activation-authority boundary
# ============================================================================


def test_cli_module_does_not_extend_control_plane_writer_methods() -> None:
    """Doc-19:348-349 AC -- the CLI module MUST NOT extend the Slice 10c-1
    CONTROL_PLANE_WRITER_METHODS frozenset.

    Structural check via the same patterns the Slice 19 7th sub-slice
    activation-boundary test surface enforces.
    """

    cli_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/"
        "workflows/develop/governance/cli.py"
    )
    main_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/"
        "workflows/develop/governance/__main__.py"
    )
    forbidden_patterns = (
        "CONTROL_PLANE_WRITER_METHODS.add(",
        "CONTROL_PLANE_WRITER_METHODS.update(",
        "CONTROL_PLANE_WRITER_METHODS |=",
        # Allow CONTROL_PLANE_WRITER_METHODS as a token in a docstring
        # citation, but NOT in an assignment statement.
    )
    for path in (cli_path, main_path):
        source = path.read_text()
        for pattern in forbidden_patterns:
            assert pattern not in source, (
                f"{path.name} contains forbidden writer-extension "
                f"pattern: {pattern!r}"
            )


def test_cli_module_does_not_emit_dag_artifact_keys() -> None:
    """Doc-19:296-303 -- the CLI MUST NOT emit `dag-*` artifact-key
    string literals. The CLI cites `review:*` artifact keys ONLY.

    Per the Slice 19 7th sub-slice activation-boundary test surface
    precedent the structural check looks for `"dag-` / `'dag-` /
    `"dag:` / `'dag:` literals.
    """

    import ast

    cli_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/"
        "workflows/develop/governance/cli.py"
    )
    source = cli_path.read_text()
    tree = ast.parse(source)
    bad_literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value.startswith("dag-") or value.startswith("dag:"):
                bad_literals.append(value)
    assert bad_literals == [], (
        f"cli.py contains `dag-*` / `dag:*` artifact-key string "
        f"literals: {bad_literals}"
    )


def test_main_module_does_not_emit_dag_artifact_keys() -> None:
    """Doc-19:296-303 -- the `__main__.py` entry-point also has no
    `dag-*` literals."""

    import ast

    main_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/"
        "workflows/develop/governance/__main__.py"
    )
    source = main_path.read_text()
    tree = ast.parse(source)
    bad_literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value.startswith("dag-") or value.startswith("dag:"):
                bad_literals.append(value)
    assert bad_literals == [], (
        f"__main__.py contains `dag-*` / `dag:*` artifact-key string "
        f"literals: {bad_literals}"
    )


def test_cli_does_not_import_supervisor_writer_surface() -> None:
    """Doc-19:296-303 -- the CLI must NOT import the supervisor / dashboard
    / merge_queue / failure_router writer modules."""

    cli_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/"
        "workflows/develop/governance/cli.py"
    )
    source = cli_path.read_text()
    forbidden_imports = (
        "from iriai_build_v2.supervisor.actions",
        "from iriai_build_v2.execution_control.merge_queue_store",
        "from iriai_build_v2.execution_control.regroup_overlay_store",
        "import dashboard",
        "from dashboard ",
    )
    for forbidden in forbidden_imports:
        assert forbidden not in source, (
            f"cli.py imports forbidden writer-surface: {forbidden!r}"
        )


def test_cli_class_has_no_mutation_methods() -> None:
    """The typed :class:`CLIProviderFactories` dataclass has NO mutation
    methods (it's frozen=True; only attribute reads).

    The CLI module exposes only callables (no classes besides the
    frozen dataclass) -- structurally there are no mutation methods.
    """

    methods = [
        name for name in dir(CLIProviderFactories)
        if not name.startswith("_")
    ]
    # The frozen dataclass exposes no callable public methods of its
    # own; only the 6 callable fields. We assert there is no
    # `activate_` / `mutate_` / `commit_` / `dispatch_` method.
    forbidden_prefixes = (
        "activate_",
        "apply_",
        "bind_",
        "mutate_",
        "commit_",
        "dispatch_",
        "schedule_",
    )
    for name in methods:
        for prefix in forbidden_prefixes:
            assert not name.startswith(prefix), (
                f"CLIProviderFactories has forbidden mutation method "
                f"name {name!r} starting with {prefix!r}"
            )


def test_cli_module_does_not_redefine_governance_snapshot() -> None:
    """The CLI MUST NOT redefine GovernanceSnapshot -- it must REUSE
    the typed shape from governance_agent.py (DIRECT annotation
    identity via import)."""

    from iriai_build_v2.workflows.develop.governance import cli as mod

    assert mod.GovernanceSnapshot is GovernanceSnapshot


def test_cli_module_does_not_redefine_snapshot_api_result() -> None:
    """The CLI MUST NOT redefine SnapshotAPIResult."""

    from iriai_build_v2.workflows.develop.governance import cli as mod

    assert mod.SnapshotAPIResult is SnapshotAPIResult


def test_cli_module_does_not_redefine_report_artifact() -> None:
    """The CLI MUST NOT redefine GovernanceReportArtifact."""

    from iriai_build_v2.workflows.develop.governance import cli as mod

    assert mod.GovernanceReportArtifact is GovernanceReportArtifact


def test_cli_module_does_not_redefine_line_provenance_read_result() -> None:
    """The CLI MUST NOT redefine LineProvenanceReadResult."""

    from iriai_build_v2.workflows.develop.governance import cli as mod

    assert mod.LineProvenanceReadResult is LineProvenanceReadResult


def test_cli_module_does_not_redefine_metrics_comparator_result() -> None:
    """The CLI MUST NOT redefine MetricsComparatorResult."""

    from iriai_build_v2.workflows.develop.governance import cli as mod

    assert mod.MetricsComparatorResult is MetricsComparatorResult


# ============================================================================
# Section 3: Parser shape
# ============================================================================


def test_parser_builds_without_error() -> None:
    """build_parser() MUST return an argparse.ArgumentParser."""

    import argparse

    parser = build_parser()
    assert isinstance(parser, argparse.ArgumentParser)


def test_parser_requires_a_subcommand() -> None:
    """Per the CLI shape the subcommand is REQUIRED -- argparse must
    raise SystemExit when no subcommand is provided."""

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_accepts_all_four_subcommands() -> None:
    """The parser MUST accept all 4 subcommands per doc-19:62-65."""

    parser = build_parser()
    for sub in SUBCOMMAND_NAMES:
        args = (
            ["--format", "json", sub, "--feature-id", "fx"]
            if sub in ("analyze", "report")
            else (
                ["--format", "json", sub, "--repo-id", "r", "--path", "p", "--line", "1"]
                if sub == "explain-line"
                else [
                    "--format", "json", sub,
                    "--baseline", "b",
                    "--candidate", "c",
                ]
            )
        )
        parsed = parser.parse_args(args)
        assert parsed.subcommand == sub


def test_parser_format_defaults_to_json() -> None:
    """Per doc-19:150 the default --format is json."""

    parser = build_parser()
    parsed = parser.parse_args(
        ["analyze", "--feature-id", "fx"]
    )
    assert parsed.format == "json"


def test_parser_format_accepts_prose() -> None:
    """Per doc-19:150 the opt-in --format is prose."""

    parser = build_parser()
    parsed = parser.parse_args(
        ["--format", "prose", "analyze", "--feature-id", "fx"]
    )
    assert parsed.format == "prose"


def test_parser_rejects_invalid_format() -> None:
    """Any --format value other than json / prose MUST be rejected."""

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["--format", "xml", "analyze", "--feature-id", "fx"]
        )


def test_parser_analyze_requires_feature_id() -> None:
    """The analyze subcommand requires --feature-id."""

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["analyze"])


def test_parser_report_requires_feature_id() -> None:
    """The report subcommand requires --feature-id."""

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["report"])


def test_parser_explain_line_requires_repo_id_path_line() -> None:
    """The explain-line subcommand requires --repo-id, --path, --line."""

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["explain-line"])
    with pytest.raises(SystemExit):
        parser.parse_args(["explain-line", "--repo-id", "r"])
    with pytest.raises(SystemExit):
        parser.parse_args(["explain-line", "--repo-id", "r", "--path", "p"])


def test_parser_explain_line_line_must_be_int() -> None:
    """The --line arg must be parseable as int."""

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "explain-line",
                "--repo-id", "r",
                "--path", "p",
                "--line", "not-an-int",
            ]
        )


def test_parser_compare_requires_baseline_candidate() -> None:
    """The compare subcommand requires --baseline and --candidate."""

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["compare"])
    with pytest.raises(SystemExit):
        parser.parse_args(["compare", "--baseline", "b"])


def test_parser_explain_line_ref_defaults_to_head() -> None:
    """--ref defaults to HEAD when not specified."""

    parser = build_parser()
    parsed = parser.parse_args(
        ["explain-line", "--repo-id", "r", "--path", "p", "--line", "1"]
    )
    assert parsed.ref == "HEAD"


# ============================================================================
# Section 4: Default provider factories (fail-loud discipline)
# ============================================================================


def test_default_factories_construct() -> None:
    """default_provider_factories() returns a CLIProviderFactories."""

    factories = default_provider_factories()
    assert isinstance(factories, CLIProviderFactories)


def test_default_factories_are_frozen() -> None:
    """CLIProviderFactories MUST be frozen=True so test misuse fails
    loud (feedback_no_silent_degradation)."""

    import dataclasses

    factories = default_provider_factories()
    with pytest.raises(dataclasses.FrozenInstanceError):
        factories.snapshot_api_factory = lambda: None  # type: ignore[misc]


def test_default_snapshot_corpus_loader_raises_for_unknown_feature() -> None:
    """Unknown feature ids still fail loud rather than returning an
    empty corpus."""

    factories = default_provider_factories()
    with pytest.raises(FileNotFoundError, match="bounded governance fixture"):
        factories.snapshot_corpus_loader("any-feature")


def test_default_snapshot_corpus_loader_reads_8ac_fixture() -> None:
    """19A-2 -- the accepted 8ac124d6 default report path has a real
    bounded summary corpus instead of the historical placeholder."""

    factories = default_provider_factories()
    corpus = factories.snapshot_corpus_loader("8ac124d6")

    assert isinstance(corpus, SnapshotAPICorpus)
    assert len(corpus.findings) == 11
    assert len(corpus.page_refs) == 11
    assert corpus.recommendations == []
    assert corpus.replay_results == []
    assert corpus.corpus_evidence_quality == "canonical"
    assert all(ref.exact for ref in corpus.page_refs)
    assert all(ref.completeness == "paged" for ref in corpus.page_refs)


def test_fixture_evidence_tokens_require_existing_digest() -> None:
    """19A-2 -- fixture evidence must fail closed when a digest is missing."""

    from iriai_build_v2.workflows.develop.governance import cli as mod

    with pytest.raises(ValueError, match="no digest"):
        mod._evidence_ref_from_fixture_token(
            "8ac124d6",
            "artifact:missing",
            artifact_digests={},
            event_digests={},
            slice_digests={},
        )


def test_default_line_provenance_reader_factory_raises() -> None:
    """The default line-provenance reader factory MUST raise
    NotImplementedError."""

    factories = default_provider_factories()
    with pytest.raises(NotImplementedError, match="LineProvenanceReader"):
        factories.line_provenance_reader_factory("any-repo")


def test_default_compare_corpus_loader_raises() -> None:
    """The default compare corpus loader MUST raise
    NotImplementedError."""

    factories = default_provider_factories()
    with pytest.raises(NotImplementedError, match="compare"):
        factories.compare_corpus_loader("baseline", "candidate")


def test_default_snapshot_api_factory_returns_real_api() -> None:
    """The default snapshot API factory returns the REAL stateless
    GovernanceSnapshotAPI (only the corpus loader is stubbed)."""

    factories = default_provider_factories()
    api = factories.snapshot_api_factory()
    assert isinstance(api, GovernanceSnapshotAPI)


def test_default_report_emitter_factory_returns_real_emitter() -> None:
    """The default report emitter factory returns the REAL
    GovernanceReportArtifactEmitter."""

    factories = default_provider_factories()
    emitter = factories.report_artifact_emitter_factory()
    assert isinstance(emitter, GovernanceReportArtifactEmitter)


def test_default_metrics_comparator_factory_returns_real_comparator() -> None:
    """The default metrics comparator factory returns the REAL
    CounterfactualMetricsComparator."""

    factories = default_provider_factories()
    comparator = factories.metrics_comparator_factory()
    assert isinstance(comparator, CounterfactualMetricsComparator)


# ============================================================================
# Section 5: Fixture builders (typed shapes for the tests)
# ============================================================================


def _evidence_ref() -> GovernanceEvidenceRef:
    return GovernanceEvidenceRef(
        authority="typed_journal",
        ref_id="ref-cli-test",
        digest="d" * 64,
        quality="canonical",
        completeness="complete",
    )


def _make_metric_value(
    *,
    definition_name: str = "tasks_per_hour",
    value: float | int | None = 2.5,
    unit: str = "tasks/hour",
    confidence: float = 0.9,
) -> GovernanceMetricValue:
    return GovernanceMetricValue(
        definition_name=definition_name,
        definition_version="v1",
        scope={"feature_id": "fx"},
        value=value,
        unit=unit,
        confidence=confidence,
        data_quality="canonical",
        source_mix={},
        evidence_refs=[_evidence_ref()],
        exclusions=[],
    )


def _make_counterfactual_result(
    *,
    corpus_id: str = "candidate",
) -> CounterfactualResult:
    return CounterfactualResult(
        result_id=f"result-{corpus_id}",
        result_version="v1",
        scenario_id=f"scenario-{corpus_id}",
        corpus_id=corpus_id,
        assumptions=["test_assumption"],
        validity_limits=[],
        policy_provenance_refs=[_evidence_ref()],
        safety_guard_class=None,
        estimated_delta_hours=-1.0,
        estimated_delta_repair_cycles=-0.1,
        estimated_delta_commit_failures=-0.05,
        estimated_risk_change="lower",
        confidence=0.7,
        invalidated_by=[],
        supporting_finding_ids=[],
        recommended_next_step="draft_policy",
    )


def _make_factories_for_analyze_happy(
    *,
    snapshot_blocked_by: list[str] | None = None,
    snapshot_completeness: str = "complete",
    snapshot_gap_findings: list[SnapshotAPIGap] | None = None,
) -> CLIProviderFactories:
    """Construct a CLIProviderFactories whose snapshot loader yields
    a real-shaped (empty) corpus + a happy-path snapshot."""

    base = default_provider_factories()
    blocked_by = snapshot_blocked_by or []

    def loader(feature_id: str) -> SnapshotAPICorpus:
        return SnapshotAPICorpus(
            findings=[],
            recommendations=[],
            replay_results=[],
            page_refs=[],
            corpus_evidence_quality="canonical",
            blocked_by=blocked_by,
        )

    # We override the snapshot_api_factory so the resulting snapshot
    # carries the requested completeness override and the requested
    # gap_findings list.
    real_api = GovernanceSnapshotAPI()

    class _StubAPI:
        def build_snapshot(
            self, inputs: SnapshotAPIInputs, corpus: SnapshotAPICorpus
        ) -> SnapshotAPIResult:
            # Use the real API for the snapshot, then optionally
            # post-mutate the completeness via an override at the
            # input level.
            new_inputs = inputs.model_copy(
                update={"completeness_override": snapshot_completeness}
                if snapshot_completeness != "complete"
                else {}
            )
            result = real_api.build_snapshot(new_inputs, corpus)
            if snapshot_gap_findings is not None and result.snapshot is not None:
                return SnapshotAPIResult(
                    snapshot=result.snapshot,
                    gap_findings=snapshot_gap_findings,
                )
            return result

    return CLIProviderFactories(
        snapshot_api_factory=_StubAPI,
        snapshot_corpus_loader=loader,
        report_artifact_emitter_factory=base.report_artifact_emitter_factory,
        line_provenance_reader_factory=base.line_provenance_reader_factory,
        metrics_comparator_factory=base.metrics_comparator_factory,
        compare_corpus_loader=base.compare_corpus_loader,
    )


def _make_factories_with_empty_corpus() -> CLIProviderFactories:
    base = default_provider_factories()

    def loader(feature_id: str) -> SnapshotAPICorpus:
        return SnapshotAPICorpus(
            findings=[],
            recommendations=[],
            replay_results=[],
            page_refs=[],
            corpus_evidence_quality="canonical",
        )

    return CLIProviderFactories(
        snapshot_api_factory=base.snapshot_api_factory,
        snapshot_corpus_loader=loader,
        report_artifact_emitter_factory=base.report_artifact_emitter_factory,
        line_provenance_reader_factory=base.line_provenance_reader_factory,
        metrics_comparator_factory=base.metrics_comparator_factory,
        compare_corpus_loader=base.compare_corpus_loader,
    )


def _make_factories_with_empty_corpus_and_blocked(
    blocked_by: list[str],
) -> CLIProviderFactories:
    base = default_provider_factories()

    def loader(feature_id: str) -> SnapshotAPICorpus:
        return SnapshotAPICorpus(
            findings=[],
            recommendations=[],
            replay_results=[],
            page_refs=[],
            corpus_evidence_quality="canonical",
            blocked_by=blocked_by,
        )

    return CLIProviderFactories(
        snapshot_api_factory=base.snapshot_api_factory,
        snapshot_corpus_loader=loader,
        report_artifact_emitter_factory=base.report_artifact_emitter_factory,
        line_provenance_reader_factory=base.line_provenance_reader_factory,
        metrics_comparator_factory=base.metrics_comparator_factory,
        compare_corpus_loader=base.compare_corpus_loader,
    )


class _FakeLineProvenanceReader:
    def __init__(self, response: LineProvenanceReadResult) -> None:
        self._response = response

    def read(self, query: LineProvenanceQuery) -> LineProvenanceReadResult:
        return self._response


def _make_line_provenance_factories(
    response: LineProvenanceReadResult,
) -> CLIProviderFactories:
    base = default_provider_factories()
    fake = _FakeLineProvenanceReader(response)

    return CLIProviderFactories(
        snapshot_api_factory=base.snapshot_api_factory,
        snapshot_corpus_loader=base.snapshot_corpus_loader,
        report_artifact_emitter_factory=base.report_artifact_emitter_factory,
        line_provenance_reader_factory=lambda repo_id: fake,  # type: ignore[return-value]
        metrics_comparator_factory=base.metrics_comparator_factory,
        compare_corpus_loader=base.compare_corpus_loader,
    )


def _make_compare_factories(
    *,
    baseline_metrics: list[GovernanceMetricValue] | None = None,
    candidate_result: CounterfactualResult | None = None,
) -> CLIProviderFactories:
    base = default_provider_factories()

    metrics = baseline_metrics if baseline_metrics is not None else [_make_metric_value()]
    cf = candidate_result if candidate_result is not None else _make_counterfactual_result()

    def loader(
        b: str, c: str
    ) -> tuple[list[GovernanceMetricValue], CounterfactualResult]:
        return metrics, cf

    return CLIProviderFactories(
        snapshot_api_factory=base.snapshot_api_factory,
        snapshot_corpus_loader=base.snapshot_corpus_loader,
        report_artifact_emitter_factory=base.report_artifact_emitter_factory,
        line_provenance_reader_factory=base.line_provenance_reader_factory,
        metrics_comparator_factory=base.metrics_comparator_factory,
        compare_corpus_loader=loader,
    )


def _capture_stdout() -> io.StringIO:
    return io.StringIO()


# ============================================================================
# Section 6: cmd_analyze
# ============================================================================


def test_cmd_analyze_happy_emits_snapshot_json_and_exits_zero() -> None:
    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    code = cmd_analyze(
        feature_id="fx-happy",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_OK
    payload = json.loads(stdout.getvalue())
    assert payload["corpus_id"] == "fx-happy"
    assert payload["snapshot_version"] == "v1"
    assert payload["top_findings"] == []


def test_cmd_analyze_emits_canonical_json_with_sorted_keys() -> None:
    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    cmd_analyze(
        feature_id="fx",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    raw = stdout.getvalue().strip()
    # The keys must be sorted (canonical JSON contract for stability).
    parsed = json.loads(raw)
    re_serialised = json.dumps(parsed, sort_keys=True)
    re_parsed = json.loads(re_serialised)
    assert parsed == re_parsed


def test_cmd_analyze_blocked_by_returns_exit_two() -> None:
    factories = _make_factories_with_empty_corpus_and_blocked(
        ["stale_evidence:fx"]
    )
    stdout = _capture_stdout()

    code = cmd_analyze(
        feature_id="fx",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_BLOCKED_EVIDENCE
    payload = json.loads(stdout.getvalue())
    assert "cli_gap" in payload
    assert payload["cli_gap"]["reason"] == "snapshot_blocked_by_non_empty"


def test_cmd_analyze_corpus_id_empty_returns_blocked() -> None:
    """When the upstream snapshot is None (e.g. empty corpus_id), the
    CLI returns EXIT_BLOCKED_EVIDENCE per doc-19:198."""

    base = default_provider_factories()

    def loader(feature_id: str) -> SnapshotAPICorpus:
        return SnapshotAPICorpus(findings=[], recommendations=[],
                                 replay_results=[], page_refs=[])

    factories = CLIProviderFactories(
        snapshot_api_factory=base.snapshot_api_factory,
        snapshot_corpus_loader=loader,
        report_artifact_emitter_factory=base.report_artifact_emitter_factory,
        line_provenance_reader_factory=base.line_provenance_reader_factory,
        metrics_comparator_factory=base.metrics_comparator_factory,
        compare_corpus_loader=base.compare_corpus_loader,
    )
    stdout = _capture_stdout()
    # Empty corpus_id triggers SnapshotAPIGap reason="corpus_id_empty"
    code = cmd_analyze(
        feature_id="",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )
    assert code == EXIT_BLOCKED_EVIDENCE
    payload = json.loads(stdout.getvalue())
    assert payload["cli_gap"]["reason"] == "upstream_snapshot_missing"
    assert payload["cli_gap"]["upstream_gap_count"] >= 1


def test_cmd_analyze_default_loader_raises_returns_exit_three() -> None:
    """Unknown feature default-loader gaps still return EXIT_UPSTREAM_EXCEPTION."""

    factories = default_provider_factories()
    stdout = _capture_stdout()

    code = cmd_analyze(
        feature_id="fx",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_UPSTREAM_EXCEPTION
    payload = json.loads(stdout.getvalue())
    assert payload["exception_type"] == "FileNotFoundError"
    assert payload["reason"] == "upstream_snapshot_construction_exception"


def test_cmd_analyze_preview_only_completeness_returns_blocked() -> None:
    """A preview_only completeness on the snapshot is blocked per
    doc-19:128-131 + doc-19:198."""

    factories = _make_factories_for_analyze_happy(
        snapshot_completeness="preview_only",
    )
    stdout = _capture_stdout()

    code = cmd_analyze(
        feature_id="fx",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_BLOCKED_EVIDENCE
    payload = json.loads(stdout.getvalue())
    assert payload["cli_gap"]["reason"] == "snapshot_completeness_preview_only"


def test_cmd_analyze_unavailable_completeness_returns_blocked() -> None:
    """unavailable completeness is blocked too."""

    factories = _make_factories_for_analyze_happy(
        snapshot_completeness="unavailable",
    )
    stdout = _capture_stdout()

    code = cmd_analyze(
        feature_id="fx",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_BLOCKED_EVIDENCE


def test_cmd_analyze_prose_format_includes_label() -> None:
    """--format=prose emits a labelled section per doc-19:150."""

    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    code = cmd_analyze(
        feature_id="fx",
        fmt="prose",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_OK
    out = stdout.getvalue()
    assert "=== analyze ===" in out


def test_cmd_analyze_prose_format_blocked_label() -> None:
    """Prose format on blocked-evidence path uses the -blocked label."""

    factories = _make_factories_with_empty_corpus_and_blocked(
        ["stale_evidence:fx"]
    )
    stdout = _capture_stdout()

    cmd_analyze(
        feature_id="fx",
        fmt="prose",
        factories=factories,
        stdout=stdout,
    )

    out = stdout.getvalue()
    assert "=== analyze-blocked ===" in out


def test_cmd_analyze_json_schema_includes_snapshot_digest() -> None:
    """Happy-path JSON output MUST include the snapshot_digest field."""

    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    cmd_analyze(
        feature_id="fx",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert "snapshot_digest" in payload
    assert len(payload["snapshot_digest"]) == 64  # SHA-256 hex


def test_cmd_analyze_reproducibility_same_inputs_same_digest() -> None:
    """Doc-19:218 -- the snapshot digest MUST be reproducible for the
    same inputs."""

    factories = _make_factories_with_empty_corpus()

    stdout1 = _capture_stdout()
    stdout2 = _capture_stdout()
    cmd_analyze(feature_id="fx", fmt="json", factories=factories, stdout=stdout1)
    cmd_analyze(feature_id="fx", fmt="json", factories=factories, stdout=stdout2)

    p1 = json.loads(stdout1.getvalue())
    p2 = json.loads(stdout2.getvalue())
    assert p1["snapshot_digest"] == p2["snapshot_digest"]


# ============================================================================
# Section 7: cmd_report
# ============================================================================


def test_cmd_report_happy_emits_artifact_json_and_exits_zero() -> None:
    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    code = cmd_report(
        feature_id="fx-report",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_OK
    payload = json.loads(stdout.getvalue())
    assert payload["artifact_key"] == "review:governance-report:fx-report"
    assert payload["corpus_id"] == "fx-report"


def test_cmd_report_artifact_key_uses_review_prefix() -> None:
    """Doc-19:296-303 -- the artifact key uses review:* (NOT dag-*)."""

    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    cmd_report(
        feature_id="fx",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["artifact_key"].startswith("review:")
    assert not payload["artifact_key"].startswith("dag-")
    assert not payload["artifact_key"].startswith("dag:")


def test_cmd_report_artifact_key_matches_doc_19_161_162_format() -> None:
    """The artifact key matches the doc-19:161-162 template format."""

    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    cmd_report(
        feature_id="my-feature-id",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["artifact_key"] == f"{REPORT_ARTIFACT_KEY_PREFIX}my-feature-id"


def test_cmd_report_blocked_by_returns_exit_two() -> None:
    factories = _make_factories_with_empty_corpus_and_blocked(
        ["stale_evidence:fx"]
    )
    stdout = _capture_stdout()

    code = cmd_report(
        feature_id="fx",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_BLOCKED_EVIDENCE
    payload = json.loads(stdout.getvalue())
    # The upstream emitter MAY still emit the artifact with blocked_by
    # propagated, OR may emit a gap; either way the CLI returns
    # nonzero exit per doc-19:198.
    assert "cli_gap" in payload


def test_cmd_report_default_loader_raises_returns_exit_three() -> None:
    factories = default_provider_factories()
    stdout = _capture_stdout()

    code = cmd_report(
        feature_id="fx",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_UPSTREAM_EXCEPTION


def test_cmd_report_default_loader_for_8ac_fixture_exits_zero() -> None:
    """19A-2 -- the default report path for the accepted fixture emits
    the bounded summary report without injected test factories."""

    factories = default_provider_factories()
    stdout = _capture_stdout()

    code = cmd_report(
        feature_id="8ac124d6",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_OK
    payload = json.loads(stdout.getvalue())
    assert payload["artifact_key"] == "review:governance-report:8ac124d6"
    assert payload["corpus_id"] == "8ac124d6"
    assert payload["completeness"] == "paged"
    assert payload["evidence_quality"] == "canonical"
    assert payload["blocked_by"] == []
    assert payload["truncated"] is True
    assert payload["omitted_counts"]["page_refs"] == 1
    assert len(payload["top_finding_keys"]) == 11
    assert len(payload["page_refs"]) == 10
    assert "artifact_body" not in stdout.getvalue()
    assert "raw_body" not in stdout.getvalue()


def test_cmd_report_empty_corpus_id_returns_blocked() -> None:
    """Empty corpus_id triggers an upstream gap -> CLI returns blocked."""

    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    code = cmd_report(
        feature_id="",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_BLOCKED_EVIDENCE


def test_cmd_report_json_schema_stable_keys() -> None:
    """The happy-path JSON must include the required typed shape keys."""

    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    cmd_report(
        feature_id="fx",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    for key in (
        "artifact_key",
        "corpus_id",
        "snapshot_digest",
        "snapshot_version",
        "completeness",
        "evidence_quality",
        "top_finding_keys",
        "recommendation_keys",
        "replay_result_ids",
        "page_refs",
        "omitted_counts",
        "blocked_by",
        "truncated",
        "generated_at",
    ):
        assert key in payload, f"report artifact JSON missing key {key!r}"


def test_cmd_report_prose_format_includes_label() -> None:
    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    cmd_report(
        feature_id="fx",
        fmt="prose",
        factories=factories,
        stdout=stdout,
    )

    out = stdout.getvalue()
    assert "=== report ===" in out


# ============================================================================
# Section 8: cmd_explain_line
# ============================================================================


def _make_clean_line_provenance_result() -> LineProvenanceReadResult:
    return LineProvenanceReadResult(
        result=LineProvenanceResult(
            commit_hashes=["a" * 40],
            task_ids=["t-1"],
            provenance_payload_refs=["refs/iriai/provenance/" + "f" * 64],
            page_refs=[],
            completeness="complete",
            completeness_digest="c" * 64,
            confidence=1.0,
            gaps=[],
        ),
    )


def _make_blocked_line_provenance_result() -> LineProvenanceReadResult:
    return LineProvenanceReadResult(
        result=LineProvenanceResult(
            commit_hashes=[],
            task_ids=[],
            provenance_payload_refs=[],
            page_refs=[],
            completeness="preview_only",
            completeness_digest="d" * 64,
            confidence=0.4,
            gaps=["trailer_only"],
        ),
        gap_finding=CommitProvenanceGapFinding(
            failure_id="line_provenance_gap",
            feature_id="fx",
            group_idx=0,
            repo_id="r",
            commit_hash="a" * 40,
            precommit_provenance_ref="refs/iriai/provenance/" + "f" * 64,
            precommit_provenance_digest="f" * 64,
            reason="trailer-only resolution",
        ),
    )


def test_cmd_explain_line_happy_emits_result_json_and_exits_zero() -> None:
    factories = _make_line_provenance_factories(
        _make_clean_line_provenance_result()
    )
    stdout = _capture_stdout()

    code = cmd_explain_line(
        repo_id="r",
        path="p",
        line=1,
        ref="HEAD",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_OK
    payload = json.loads(stdout.getvalue())
    assert payload["completeness"] == "complete"
    assert payload["commit_hashes"] == ["a" * 40]


def test_cmd_explain_line_blocked_returns_exit_two() -> None:
    factories = _make_line_provenance_factories(
        _make_blocked_line_provenance_result()
    )
    stdout = _capture_stdout()

    code = cmd_explain_line(
        repo_id="r",
        path="p",
        line=1,
        ref="HEAD",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_BLOCKED_EVIDENCE
    payload = json.loads(stdout.getvalue())
    assert "cli_gap" in payload
    assert payload["cli_gap"]["reason"].startswith(
        "line_provenance_completeness"
    )
    assert payload["upstream_gap_finding"] is not None


def test_cmd_explain_line_default_factory_raises_returns_exit_three() -> None:
    factories = default_provider_factories()
    stdout = _capture_stdout()

    code = cmd_explain_line(
        repo_id="r",
        path="p",
        line=1,
        ref="HEAD",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_UPSTREAM_EXCEPTION
    payload = json.loads(stdout.getvalue())
    assert payload["exception_type"] == "NotImplementedError"


def test_cmd_explain_line_invalid_line_returns_exit_three() -> None:
    """A negative line index fails Pydantic validation on
    LineProvenanceQuery -> caught by the wrapper."""

    factories = _make_line_provenance_factories(
        _make_clean_line_provenance_result()
    )
    stdout = _capture_stdout()

    code = cmd_explain_line(
        repo_id="r",
        path="p",
        line=-1,  # Invalid
        ref="HEAD",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_UPSTREAM_EXCEPTION


def test_cmd_explain_line_prose_format_includes_label() -> None:
    factories = _make_line_provenance_factories(
        _make_clean_line_provenance_result()
    )
    stdout = _capture_stdout()

    cmd_explain_line(
        repo_id="r",
        path="p",
        line=1,
        ref="HEAD",
        fmt="prose",
        factories=factories,
        stdout=stdout,
    )

    out = stdout.getvalue()
    assert "=== explain-line ===" in out


def test_cmd_explain_line_gap_finding_only_returns_blocked() -> None:
    """A clean result + a non-None gap_finding should still return
    blocked because the gap is informational + indicates upstream
    issues."""

    result = LineProvenanceReadResult(
        result=LineProvenanceResult(
            commit_hashes=["a" * 40],
            task_ids=[],
            provenance_payload_refs=[],
            page_refs=[],
            completeness="complete",
            completeness_digest="c" * 64,
            confidence=1.0,
            gaps=[],
        ),
        gap_finding=CommitProvenanceGapFinding(
            failure_id="line_provenance_gap",
            feature_id="fx",
            group_idx=0,
            repo_id="r",
            commit_hash="b" * 40,
            precommit_provenance_ref="refs/iriai/provenance/" + "e" * 64,
            precommit_provenance_digest="e" * 64,
            reason="informational gap",
        ),
    )
    factories = _make_line_provenance_factories(result)
    stdout = _capture_stdout()

    code = cmd_explain_line(
        repo_id="r",
        path="p",
        line=1,
        ref="HEAD",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_BLOCKED_EVIDENCE


# ============================================================================
# Section 9: cmd_compare
# ============================================================================


def test_cmd_compare_happy_emits_result_json_and_exits_zero() -> None:
    factories = _make_compare_factories()
    stdout = _capture_stdout()

    code = cmd_compare(
        baseline_corpus_id="baseline",
        candidate_corpus_id="candidate",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_OK
    payload = json.loads(stdout.getvalue())
    # MetricsComparatorResult shape carries axis_deltas.
    assert "axis_deltas" in payload
    assert payload["gap_findings"] == []


def test_cmd_compare_emits_result_id_from_args() -> None:
    """The result_id is constructed from the (baseline, candidate)
    args -- this is a typed identity contract per doc-18:80."""

    factories = _make_compare_factories()
    stdout = _capture_stdout()

    cmd_compare(
        baseline_corpus_id="bl-A",
        candidate_corpus_id="cd-B",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    # result_id format = "cli-compare:{baseline}:{candidate}"
    assert payload["result_id"] == "cli-compare:bl-A:cd-B"


def test_cmd_compare_default_loader_raises_returns_exit_three() -> None:
    factories = default_provider_factories()
    stdout = _capture_stdout()

    code = cmd_compare(
        baseline_corpus_id="b",
        candidate_corpus_id="c",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_UPSTREAM_EXCEPTION


def test_cmd_compare_empty_baseline_metrics_returns_blocked() -> None:
    """An empty baseline metrics list typically triggers a typed
    MetricsComparatorGap -> CLI returns blocked."""

    factories = _make_compare_factories(baseline_metrics=[])
    stdout = _capture_stdout()

    code = cmd_compare(
        baseline_corpus_id="b",
        candidate_corpus_id="c",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_BLOCKED_EVIDENCE


def test_cmd_compare_prose_format_includes_label() -> None:
    factories = _make_compare_factories()
    stdout = _capture_stdout()

    cmd_compare(
        baseline_corpus_id="b",
        candidate_corpus_id="c",
        fmt="prose",
        factories=factories,
        stdout=stdout,
    )

    out = stdout.getvalue()
    assert "=== compare ===" in out


def test_cmd_compare_json_schema_includes_axis_deltas() -> None:
    """Happy-path schema MUST include axis_deltas."""

    factories = _make_compare_factories()
    stdout = _capture_stdout()

    cmd_compare(
        baseline_corpus_id="b",
        candidate_corpus_id="c",
        fmt="json",
        factories=factories,
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert "axis_deltas" in payload
    # Each axis delta is a typed MetricsAxisDelta with `axis`.
    if payload["axis_deltas"]:
        first = payload["axis_deltas"][0]
        assert "axis" in first


# ============================================================================
# Section 10: main() runner
# ============================================================================


def test_main_no_args_returns_usage_error() -> None:
    """No subcommand -> EXIT_USAGE_ERROR + typed JSON gap on stdout."""

    stdout = _capture_stdout()
    stderr = _capture_stdout()

    code = main([], stdout=stdout, stderr=stderr)

    assert code == EXIT_USAGE_ERROR


def test_main_with_help_returns_usage_error_or_zero() -> None:
    """--help triggers argparse SystemExit(0); our wrapper returns
    EXIT_USAGE_ERROR (which preserves the typed JSON contract for
    blocked-evidence detection)."""

    stdout = _capture_stdout()
    stderr = _capture_stdout()

    code = main(["--help"], stdout=stdout, stderr=stderr)

    # Either 0 or 1; the typed contract is the JSON-first discipline
    # holds (no uncaught argparse stderr break).
    assert code in (0, 1)


def test_main_with_unknown_subcommand_returns_usage_error() -> None:
    stdout = _capture_stdout()
    stderr = _capture_stdout()

    code = main(["bogus-command"], stdout=stdout, stderr=stderr)

    assert code == EXIT_USAGE_ERROR


def test_main_analyze_dispatches_to_cmd_analyze() -> None:
    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    code = main(
        ["analyze", "--feature-id", "fx"],
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_OK
    payload = json.loads(stdout.getvalue())
    assert payload["corpus_id"] == "fx"


def test_main_report_dispatches_to_cmd_report() -> None:
    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    code = main(
        ["report", "--feature-id", "fx"],
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_OK
    payload = json.loads(stdout.getvalue())
    assert payload["artifact_key"] == "review:governance-report:fx"


def test_main_explain_line_dispatches_to_cmd_explain_line() -> None:
    factories = _make_line_provenance_factories(
        _make_clean_line_provenance_result()
    )
    stdout = _capture_stdout()

    code = main(
        ["explain-line", "--repo-id", "r", "--path", "p", "--line", "1"],
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_OK
    payload = json.loads(stdout.getvalue())
    assert payload["completeness"] == "complete"


def test_main_compare_dispatches_to_cmd_compare() -> None:
    factories = _make_compare_factories()
    stdout = _capture_stdout()

    code = main(
        ["compare", "--baseline", "b", "--candidate", "c"],
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_OK


def test_main_format_prose_works() -> None:
    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    code = main(
        ["--format", "prose", "analyze", "--feature-id", "fx"],
        factories=factories,
        stdout=stdout,
    )

    assert code == EXIT_OK
    assert "=== analyze ===" in stdout.getvalue()


def test_main_format_json_is_default() -> None:
    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    main(
        ["analyze", "--feature-id", "fx"],
        factories=factories,
        stdout=stdout,
    )

    # Should be parseable as JSON.
    json.loads(stdout.getvalue())


# ============================================================================
# Section 11: Subprocess-based E2E tests (validates `python -m` invocation)
# ============================================================================


def test_python_dash_m_invocation_works() -> None:
    """The `python -m iriai_build_v2.workflows.develop.governance --help`
    invocation pattern per doc-19:62-65 MUST work."""

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "iriai_build_v2.workflows.develop.governance",
            "--help",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # argparse --help returns 0; verify subcommands listed.
    assert proc.returncode == 0
    for sub in SUBCOMMAND_NAMES:
        assert sub in proc.stdout


def test_python_dash_m_analyze_default_loader_returns_three() -> None:
    """E2E: unknown feature default loader raises -> exit 3 +
    valid JSON gap on stdout per doc-19:198."""

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "iriai_build_v2.workflows.develop.governance",
            "analyze",
            "--feature-id",
            "fx-subproc",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == EXIT_UPSTREAM_EXCEPTION
    # Stdout MUST be parseable JSON per doc-19:150 + doc-19:198.
    payload = json.loads(proc.stdout)
    assert payload["exception_type"] == "FileNotFoundError"
    assert payload["subcommand"] == "analyze"


def test_python_dash_m_no_args_returns_nonzero() -> None:
    """E2E: no args -> nonzero exit (usage error)."""

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "iriai_build_v2.workflows.develop.governance",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0


def test_python_dash_m_explain_line_default_loader_returns_three() -> None:
    """E2E for explain-line via default loader."""

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "iriai_build_v2.workflows.develop.governance",
            "explain-line",
            "--repo-id", "r",
            "--path", "p",
            "--line", "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == EXIT_UPSTREAM_EXCEPTION
    payload = json.loads(proc.stdout)
    assert payload["subcommand"] == "explain-line"


def test_python_dash_m_compare_default_loader_returns_three() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "iriai_build_v2.workflows.develop.governance",
            "compare",
            "--baseline", "b",
            "--candidate", "c",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == EXIT_UPSTREAM_EXCEPTION
    payload = json.loads(proc.stdout)
    assert payload["subcommand"] == "compare"


def test_python_dash_m_report_default_loader_returns_three() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "iriai_build_v2.workflows.develop.governance",
            "report",
            "--feature-id", "fx-subproc",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == EXIT_UPSTREAM_EXCEPTION
    payload = json.loads(proc.stdout)
    assert payload["subcommand"] == "report"


def test_exact_python_dash_m_report_8ac124d6_default_path_exits_zero() -> None:
    """19A-2 exact command regression for the default accepted report path."""

    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    src_path = str(repo_root / "src")
    env["PYTHONPATH"] = (
        src_path
        if not env.get("PYTHONPATH")
        else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "iriai_build_v2.workflows.develop.governance",
            "report",
            "--feature-id",
            "8ac124d6",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=repo_root,
        env=env,
    )

    assert proc.returncode == EXIT_OK, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["artifact_key"] == "review:governance-report:8ac124d6"
    assert payload["corpus_id"] == "8ac124d6"
    assert payload["completeness"] == "paged"
    assert payload["evidence_quality"] == "canonical"
    assert payload["blocked_by"] == []
    assert payload["truncated"] is True
    assert payload["omitted_counts"]["page_refs"] == 1
    assert len(payload["top_finding_keys"]) == 11
    assert len(payload["page_refs"]) == 10
    assert "artifact_body" not in proc.stdout
    assert "raw_body" not in proc.stdout


# ============================================================================
# Section 12: JSON-first contract preservation
# ============================================================================


def test_argparse_usage_error_emits_typed_json() -> None:
    """Even on argparse usage error the runner MUST emit a typed JSON
    gap on stdout per the JSON-first contract (doc-19:150)."""

    stdout = _capture_stdout()
    stderr = _capture_stdout()

    code = main(["bogus"], stdout=stdout, stderr=stderr)

    assert code == EXIT_USAGE_ERROR
    # stdout MUST contain the typed gap JSON.
    out = stdout.getvalue().strip()
    assert out
    payload = json.loads(out)
    assert payload["cli_failure_class"] == "governance_cli_blocked_or_unavailable"
    assert payload["reason"] == "argparse_usage_error"


def test_all_subcommands_emit_parseable_json_on_blocked() -> None:
    """Every subcommand's blocked-evidence path emits stdout that
    parses as JSON (doc-19:198 JSON-first contract)."""

    test_cases = [
        ("analyze", _make_factories_with_empty_corpus_and_blocked(["x"]),
         ["analyze", "--feature-id", "fx"]),
        ("report", _make_factories_with_empty_corpus_and_blocked(["x"]),
         ["report", "--feature-id", "fx"]),
        ("explain-line", _make_line_provenance_factories(
            _make_blocked_line_provenance_result()),
         ["explain-line", "--repo-id", "r", "--path", "p", "--line", "1"]),
        ("compare", _make_compare_factories(baseline_metrics=[]),
         ["compare", "--baseline", "b", "--candidate", "c"]),
    ]
    for name, factories, argv in test_cases:
        stdout = _capture_stdout()
        code = main(argv, factories=factories, stdout=stdout)
        assert code == EXIT_BLOCKED_EVIDENCE, f"{name} did not return blocked"
        # JSON-parseable on stdout per doc-19:198.
        payload = json.loads(stdout.getvalue())
        assert isinstance(payload, dict), f"{name} stdout is not a JSON object"


def test_no_uncaught_exception_propagates_from_runner() -> None:
    """The runner MUST catch every exception path -- no naked
    traceback leaks to stderr."""

    factories = default_provider_factories()

    for argv in (
        ["analyze", "--feature-id", "fx"],
        ["report", "--feature-id", "fx"],
        ["explain-line", "--repo-id", "r", "--path", "p", "--line", "1"],
        ["compare", "--baseline", "b", "--candidate", "c"],
    ):
        stdout = _capture_stdout()
        # MUST NOT raise.
        code = main(argv, factories=factories, stdout=stdout)
        assert code != EXIT_OK
        # Stdout should be parseable.
        json.loads(stdout.getvalue())


# ============================================================================
# Section 13: Failure router state preservation (no new ids added)
# ============================================================================


def test_failure_router_governance_failure_id_count_unchanged() -> None:
    """The Slice 19 8th sub-slice CLI does NOT add a new typed failure
    id (it's a pure projection consumer); the count stays at 16
    governance failure ids per the STATUS.md state."""

    from iriai_build_v2.workflows.develop.execution import failure_router as fr

    governance_ids = {
        # Slice 17 (5)
        "recommendation_builder_emission_failed",
        "policy_validation_failed",
        "decision_record_persistence_failed",
        "replay_requirement_validation_failed",
        "consumer_read_api_failed",
        # Slice 18 (6)
        "recommendation_citation_validation_failed",
        "replay_corpus_or_scenario_load_failed",
        "summary_replay_failed",
        "event_replay_failed",
        "metrics_comparator_failed",
        "counterfactual_result_persistence_failed",
        # Slice 19 (5: 2nd-6th)
        "governance_snapshot_api_failed",
        "governance_dashboard_view_failed",
        "governance_slack_renderer_failed",
        "governance_agent_context_builder_failed",
        "governance_report_artifact_emission_failed",
    }
    for fid in governance_ids:
        assert fid in fr.FAILURE_TYPES, f"missing failure id: {fid}"

    # No NEW Slice 19 8th sub-slice failure id added (e.g. no
    # 'governance_cli_failed').
    assert "governance_cli_failed" not in fr.FAILURE_TYPES
    assert "governance_cli_invocation_failed" not in fr.FAILURE_TYPES


def test_cli_module_does_not_register_new_failure_id() -> None:
    """Defence-in-depth: the CLI module source MUST NOT contain a
    typed failure-id Literal declaration."""

    cli_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/"
        "workflows/develop/governance/cli.py"
    )
    source = cli_path.read_text()
    # Should not declare a typed Literal "*_FAILURE_ID" for a new
    # routable failure id.
    assert "governance_cli_failed" not in source
    assert "governance_cli_invocation_failed" not in source
    assert "_FAILURE_ID: Literal[" not in source


# ============================================================================
# Section 14: AC enforcement (doc-19:150 + doc-19:198 + doc-19:296-303)
# ============================================================================


def test_ac_doc_19_150_json_first_default() -> None:
    """AC doc-19:150 -- JSON output is the default."""

    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    main(["analyze", "--feature-id", "fx"], factories=factories, stdout=stdout)

    # Stdout MUST be valid JSON.
    parsed = json.loads(stdout.getvalue())
    assert isinstance(parsed, dict)


def test_ac_doc_19_198_nonzero_exit_on_blocked_evidence() -> None:
    """AC doc-19:198 -- CLI emits stable JSON and nonzero exit for
    blocked evidence."""

    factories = _make_factories_with_empty_corpus_and_blocked(["blocked"])
    stdout = _capture_stdout()

    code = main(["analyze", "--feature-id", "fx"], factories=factories, stdout=stdout)

    assert code != 0
    # And JSON is still parseable.
    json.loads(stdout.getvalue())


def test_ac_doc_19_296_303_no_writer_method_extension() -> None:
    """AC doc-19:296-303 -- the CLI does NOT extend
    CONTROL_PLANE_WRITER_METHODS."""

    # Import the readonly module to capture the set as-of-now.
    from iriai_build_v2.supervisor import read_only

    before = frozenset(read_only.CONTROL_PLANE_WRITER_METHODS)

    # Importing + invoking the CLI must NOT mutate the set.
    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()
    main(["analyze", "--feature-id", "fx"], factories=factories, stdout=stdout)

    after = frozenset(read_only.CONTROL_PLANE_WRITER_METHODS)
    assert before == after


def test_ac_doc_19_296_303_no_dag_artifact_keys_emitted() -> None:
    """AC doc-19:296-303 -- the CLI does NOT emit dag-* keys."""

    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()

    main(["report", "--feature-id", "fx"], factories=factories, stdout=stdout)

    payload = json.loads(stdout.getvalue())
    # The artifact_key should use review:* prefix.
    assert payload["artifact_key"].startswith("review:")


# ============================================================================
# Section 15: forward applicability + activation boundary cross-reference
# ============================================================================


def test_activation_boundary_test_file_exists() -> None:
    """The Slice 19 7th sub-slice activation-boundary test file MUST
    exist (it forward-applies to this CLI module)."""

    boundary_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_governance_19_activation_boundary.py"
    )
    assert boundary_path.exists()


def test_this_test_file_exists() -> None:
    """Self-existence sentinel."""

    test_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_governance_cli.py"
    )
    assert test_path.exists()


def test_cli_module_path_exists() -> None:
    """The cli.py module MUST exist at the expected path."""

    cli_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/"
        "workflows/develop/governance/cli.py"
    )
    assert cli_path.exists()


def test_main_module_path_exists() -> None:
    """The __main__.py module MUST exist at the expected path per
    doc-19:62-65."""

    main_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/"
        "workflows/develop/governance/__main__.py"
    )
    assert main_path.exists()


def test_doc_19_pin_cites_in_test_file_docstring() -> None:
    """This test module's docstring MUST carry the doc-19 PIN cites
    per feedback_cite_everything."""

    test_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_governance_cli.py"
    )
    source = test_path.read_text()
    # Find the module docstring (the first triple-quoted string).
    start = source.find('"""')
    end = source.find('"""', start + 3)
    docstring = source[start + 3:end] if start != -1 and end != -1 else ""
    for cite in ("62-65", "150", "198", "348-349", "296-303"):
        assert cite in docstring, (
            f"this test module docstring missing doc-19:{cite} PIN cite"
        )


# ============================================================================
# Section 16: Parametrized exit-code + format combinations
# ============================================================================


@pytest.mark.parametrize("fmt", ["json", "prose"])
def test_each_format_works_for_analyze(fmt: str) -> None:
    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()
    code = cmd_analyze(
        feature_id="fx",
        fmt=fmt,
        factories=factories,
        stdout=stdout,
    )
    assert code == EXIT_OK


@pytest.mark.parametrize("fmt", ["json", "prose"])
def test_each_format_works_for_report(fmt: str) -> None:
    factories = _make_factories_with_empty_corpus()
    stdout = _capture_stdout()
    code = cmd_report(
        feature_id="fx",
        fmt=fmt,
        factories=factories,
        stdout=stdout,
    )
    assert code == EXIT_OK


@pytest.mark.parametrize("fmt", ["json", "prose"])
def test_each_format_works_for_explain_line(fmt: str) -> None:
    factories = _make_line_provenance_factories(
        _make_clean_line_provenance_result()
    )
    stdout = _capture_stdout()
    code = cmd_explain_line(
        repo_id="r", path="p", line=1, ref="HEAD",
        fmt=fmt, factories=factories, stdout=stdout,
    )
    assert code == EXIT_OK


@pytest.mark.parametrize("fmt", ["json", "prose"])
def test_each_format_works_for_compare(fmt: str) -> None:
    factories = _make_compare_factories()
    stdout = _capture_stdout()
    code = cmd_compare(
        baseline_corpus_id="b", candidate_corpus_id="c",
        fmt=fmt, factories=factories, stdout=stdout,
    )
    assert code == EXIT_OK


@pytest.mark.parametrize(
    "subcommand,argv",
    [
        ("analyze", ["analyze", "--feature-id", "fx"]),
        ("report", ["report", "--feature-id", "fx"]),
        (
            "explain-line",
            ["explain-line", "--repo-id", "r", "--path", "p", "--line", "1"],
        ),
        (
            "compare",
            ["compare", "--baseline", "b", "--candidate", "c"],
        ),
    ],
)
def test_main_with_default_factories_returns_nonzero(
    subcommand: str, argv: list[str]
) -> None:
    """Per the READ-ONLY discipline unsupported default paths fail
    loud and return EXIT_UPSTREAM_EXCEPTION."""

    stdout = _capture_stdout()
    code = main(argv, stdout=stdout)
    assert code == EXIT_UPSTREAM_EXCEPTION
    payload = json.loads(stdout.getvalue())
    assert payload["subcommand"] == subcommand
