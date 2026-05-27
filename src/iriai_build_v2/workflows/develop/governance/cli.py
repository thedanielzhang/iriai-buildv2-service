"""Governance CLI (Slice 19 8th sub-slice) -- READ-ONLY typed
projection consumer.

Per ``docs/execution-control-plane/19-governance-agent-and-reporting.md``
§ Refactoring Steps step 1 (line 150) + § Proposed Interfaces lines
59-66 + § Tests line 198 this module delivers the governance CLI
deferred from the 2026-05-25 slice-end SIX-VECTOR remediation
(V1 P1-A). The CLI surface lives at:

    python -m iriai_build_v2.workflows.develop.governance analyze --feature-id <id>
    python -m iriai_build_v2.workflows.develop.governance report --feature-id <id>
    python -m iriai_build_v2.workflows.develop.governance explain-line --repo-id <repo> --path <path> --line <n>
    python -m iriai_build_v2.workflows.develop.governance compare --baseline <corpus> --candidate <corpus>

per doc-19:62-65 verbatim.

**Activation-authority boundary (doc-19:348-349 + doc-19:296-303).**

Per doc-19:348-349 AC *"Supervisor/dashboard read-only contract
preserved (no governance writer extends the Slice 10c-1
``CONTROL_PLANE_WRITER_METHODS`` set)."* + doc-19:296-303 (the new
AC bullet enumerated for the 8th sub-slice CLI):

- The CLI does NOT extend the Slice 10c-1
  :data:`~iriai_build_v2.supervisor.read_only.CONTROL_PLANE_WRITER_METHODS`
  set.
- The CLI does NOT emit ``dag-*`` artifact-key string literals (it
  is purely a consumer of typed Slice 13-18 + Slice 19 2nd-6th
  governance projections + the Slice 19 6th sub-slice typed
  ``review:governance-report:{corpus_id}`` artifact-key shape).
- The CLI does NOT introduce mutation methods on any BaseModel.
- The CLI reads typed projections ONLY; it does not own the
  bounded-read transactions (the upstream typed APIs own those).

**Fail-closed discipline (per auto-memory
``feedback_no_silent_degradation``).**

Per doc-19:198 *"CLI emits stable JSON and nonzero exit for blocked
evidence."*: every subcommand catches every exception from the typed
upstream APIs and emits a typed gap projection JSON with a nonzero
exit code; the CLI MUST NEVER let an uncaught exception propagate
to stderr in a way that breaks the JSON-first contract. The exit
codes are:

- **0** -- happy path; the typed upstream surface emitted a clean
  result and no gaps fired and no blocked-evidence markers fired.
- **1** -- usage error (argparse rejected the args; argparse exits
  with 2 by default but we wrap it via the runner so callers see a
  consistent JSON gap shape on the stdout stream).
- **2** -- blocked-evidence gap (the typed upstream surface emitted
  a typed gap finding OR the typed shape carried a non-empty
  ``blocked_by`` OR a ``preview_only`` / ``unavailable`` completeness
  state). Per doc-19:198 the exit code is nonzero so callers can
  detect blocked evidence programmatically.
- **3** -- upstream-projection exception (the upstream API itself
  raised an exception that was caught by the wrapper). Distinct
  from 2 so callers can distinguish blocked-evidence gaps from
  upstream crashes.

**JSON-first + prose-second (per doc-19:150 step 1).**

Per doc-19:150 *"Add governance CLI with JSON output first and prose
rendering second."*: the default ``--format=json`` emits the typed
upstream payload as canonical JSON; the opt-in ``--format=prose``
emits a human-readable prose rendering with the same exit-code
discipline.

**Dependency-injection seams.**

The CLI exposes a typed :class:`CLIProviderFactories` shape that
tests can override to inject fakes for the upstream typed APIs +
the per-subcommand corpus loaders. This keeps the SUT (the CLI
runner) untouched during testing -- only the upstream typed-API
factories are stubbed.

**Per the auto-memory ``feedback_no_overengineer_use_library`` rule**
the CLI uses stdlib :mod:`argparse` (NOT a third-party CLI library
like ``click`` or ``typer``). The stdlib ``argparse`` is sufficient
for the 4 subcommands' shape; it is already used by every Python
install; it has zero new dependencies.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from pydantic import BaseModel

# Slice 14 -- line-provenance reader + query shapes (consumed by
# `explain-line`).
from iriai_build_v2.execution_control.commit_provenance import (
    LineProvenanceQuery,
    LineProvenanceResult,
)
from iriai_build_v2.execution_control.commit_provenance_reader import (
    LineProvenanceReader,
    LineProvenanceReadResult,
)

# Slice 18 -- counterfactual metrics comparator (consumed by `compare`).
from iriai_build_v2.execution_control.counterfactual_metrics_comparator import (
    CounterfactualMetricsComparator,
    MetricsComparatorInputs,
    MetricsComparatorResult,
)
from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
)
from iriai_build_v2.execution_control.finding_engine import (
    GovernanceFinding,
)

# Slice 19 1st sub-slice -- typed snapshot + agent context shapes
# (consumed by `analyze`).
from iriai_build_v2.execution_control.governance_agent import (
    GovernanceSnapshot,
)

# Slice 19 6th sub-slice -- typed report-artifact emitter (consumed
# by `report`).
from iriai_build_v2.execution_control.governance_report_artifact import (
    GovernanceReportArtifact,
    GovernanceReportArtifactEmitter,
    ReportArtifactInputs,
    ReportArtifactResult,
)

# Slice 19 2nd sub-slice -- typed snapshot API (consumed by `analyze`
# + `report` upstream).
from iriai_build_v2.execution_control.governance_snapshot_api import (
    GovernanceSnapshotAPI,
    SnapshotAPICorpus,
    SnapshotAPIInputs,
    SnapshotAPIResult,
)

# Slice 15 -- metric value shape (typed input for the comparator).
from iriai_build_v2.execution_control.governance_metrics import (
    GovernanceMetricValue,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidencePageRef,
    GovernanceEvidenceRef,
)


__all__ = [
    # The 4 typed subcommand names per doc-19:62-65.
    "SUBCOMMAND_NAMES",
    # Typed exit codes (doc-19:198).
    "EXIT_OK",
    "EXIT_USAGE_ERROR",
    "EXIT_BLOCKED_EVIDENCE",
    "EXIT_UPSTREAM_EXCEPTION",
    # Typed format names (doc-19:150).
    "FORMAT_NAMES",
    # Typed CLI provider-factories shape (DI seam for tests).
    "CLIProviderFactories",
    # The typed CLI parser builder (re-usable across `__main__.py` +
    # tests).
    "build_parser",
    # The typed CLI runner (programmatic E2E surface).
    "main",
    # The 4 typed subcommand handlers (programmatic E2E surface).
    "cmd_analyze",
    "cmd_report",
    "cmd_explain_line",
    "cmd_compare",
    # Typed default factories the runner constructs when the caller
    # does not inject overrides.
    "default_provider_factories",
]


# --- Typed subcommand names (doc-19:62-65) ---------------------------------


SUBCOMMAND_NAMES: tuple[str, ...] = (
    "analyze",
    "report",
    "explain-line",
    "compare",
)
"""Doc-19:62-65 verbatim -- the 4 subcommand names the CLI exposes.

Per doc-19:62-65:

.. code-block:: bash

    python -m iriai_build_v2.workflows.develop.governance analyze --feature-id <id>
    python -m iriai_build_v2.workflows.develop.governance report --feature-id <id>
    python -m iriai_build_v2.workflows.develop.governance explain-line --repo-id <repo> --path <path> --line <n>
    python -m iriai_build_v2.workflows.develop.governance compare --baseline <corpus> --candidate <corpus>

The tuple is :class:`tuple` (immutable + hashable + iteration-order
stable) so tests can assert membership + order verbatim.
"""


# --- Typed exit codes (doc-19:198) -----------------------------------------


EXIT_OK: int = 0
"""Doc-19:198 -- happy-path exit code.

The typed upstream surface emitted a clean result and no gaps fired
and no blocked-evidence markers fired. Callers MAY treat exit 0 as
the only success signal.
"""

EXIT_USAGE_ERROR: int = 1
"""Doc-19:198 -- usage-error exit code.

Argparse rejected the args; the runner translates argparse's default
exit code (2) to this value so callers can distinguish usage errors
from blocked-evidence gaps (which use exit code 2 per the next
constant).
"""

EXIT_BLOCKED_EVIDENCE: int = 2
"""Doc-19:198 -- blocked-evidence exit code.

The typed upstream surface emitted a typed gap finding OR the typed
shape carried a non-empty ``blocked_by`` OR a ``preview_only`` /
``unavailable`` completeness state. Per doc-19:198
*"CLI emits stable JSON and nonzero exit for blocked evidence."* the
exit code is nonzero so callers can detect blocked evidence
programmatically.
"""

EXIT_UPSTREAM_EXCEPTION: int = 3
"""Doc-19:198 -- upstream-projection-exception exit code.

The upstream API itself raised an exception that was caught by the
fail-closed wrapper. Distinct from
:data:`EXIT_BLOCKED_EVIDENCE` so callers can distinguish blocked-
evidence gaps from upstream crashes.

Per auto-memory ``feedback_no_silent_degradation`` the wrapper
catches every exception (including :class:`KeyboardInterrupt` is
NOT caught -- the CLI propagates user-initiated cancellation
verbatim).
"""


# --- Typed format names (doc-19:150) ---------------------------------------


FORMAT_NAMES: tuple[str, ...] = ("json", "prose")
"""Doc-19:150 -- the 2 typed format names per the JSON-first + prose-
second discipline.

Per doc-19:150 *"Add governance CLI with JSON output first and prose
rendering second."* the default ``--format=json`` emits the typed
upstream payload as canonical JSON; the opt-in ``--format=prose``
emits a human-readable prose rendering with the same exit-code
discipline.
"""


_DEFAULT_FIXTURE_ROOT: Path = (
    Path(__file__).resolve().parents[5]
    / "tests"
    / "fixtures"
    / "execution_control_plane"
)
"""Checked-in bounded summary fixture root used by the default report path."""

_DEFAULT_FIXTURE_MAX_FILE_BYTES: int = 64 * 1024
"""Per-file byte cap for the default fixture-backed corpus loader."""

_DEFAULT_FIXTURE_MAX_JSONL_ROWS: int = 256
"""Per-JSONL row cap for the default fixture-backed corpus loader."""


# --- Typed CLI provider-factories shape (DI seam for tests) ----------------


@dataclasses.dataclass(frozen=True)
class CLIProviderFactories:
    """Typed dependency-injection seam for the CLI runner.

    Holds the 6 provider hooks (callables that
    return typed upstream API instances + the per-subcommand corpus
    loaders) the runner consumes. Tests OVERRIDE individual factories
    to inject fakes without mocking the CLI itself.

    Per auto-memory ``feedback_no_overengineer_use_library`` the
    factories are plain stdlib ``Callable`` types -- no protocol +
    no ABC + no third-party DI framework. The runner introspects the
    factory's return value via its public methods only.

    Per auto-memory ``feedback_flat_structured_output`` the typed
    shape is FLAT (just 6 callable fields); no nested DI metadata.

    Per ``feedback_no_silent_degradation`` the dataclass is ``frozen=True``
    so a mis-construction raises a typed :class:`dataclasses.FrozenInstanceError`
    rather than being silently absorbed.
    """

    snapshot_api_factory: Callable[[], GovernanceSnapshotAPI]
    """Returns the typed Slice 19 2nd sub-slice
    :class:`GovernanceSnapshotAPI` instance.

    The default factory returns ``GovernanceSnapshotAPI()`` (the
    stateless typed projection helper). Tests OVERRIDE to inject a
    fake that returns canned :class:`SnapshotAPIResult` rows.
    """

    snapshot_corpus_loader: Callable[[str], SnapshotAPICorpus]
    """Loads the typed Slice 19 2nd sub-slice
    :class:`SnapshotAPICorpus` for a given ``feature_id``.

    The default loader is a bounded, checked-in fixture loader for
    summary-only governance corpora such as ``8ac124d6``. Real callers
    MAY still inject a custom loader that returns a typed
    :class:`SnapshotAPICorpus` for live or non-fixture feature ids.

    Per the fail-closed discipline the runner CATCHES the
    fixture-loader exceptions + emits a typed gap projection with
    :data:`EXIT_UPSTREAM_EXCEPTION` rather than letting the exception
    propagate to stderr.
    """

    report_artifact_emitter_factory: Callable[[], GovernanceReportArtifactEmitter]
    """Returns the typed Slice 19 6th sub-slice
    :class:`GovernanceReportArtifactEmitter` instance.

    The default factory returns ``GovernanceReportArtifactEmitter()``
    (the stateless typed projection helper). Tests OVERRIDE to inject
    a fake that returns canned :class:`ReportArtifactResult` rows.
    """

    line_provenance_reader_factory: Callable[[str], LineProvenanceReader]
    """Returns the typed Slice 14 :class:`LineProvenanceReader`
    instance bound to the given ``repo_id`` argument.

    The default factory RAISES :class:`NotImplementedError` because
    the production reader requires a Git working tree + the 4 typed
    source ports (commit_proof_provider / payload_store /
    trailer_source / lineage_walker) + a stdlib-subprocess runner
    that the READ-ONLY CLI does NOT own. Real callers MUST inject a
    custom factory.

    Per the fail-closed discipline the runner CATCHES the
    :class:`NotImplementedError` + emits a typed gap projection with
    :data:`EXIT_UPSTREAM_EXCEPTION`.
    """

    metrics_comparator_factory: Callable[[], CounterfactualMetricsComparator]
    """Returns the typed Slice 18
    :class:`CounterfactualMetricsComparator` instance.

    The default factory returns
    ``CounterfactualMetricsComparator()`` (the stateless typed
    projection helper). Tests OVERRIDE to inject a fake that
    returns canned :class:`MetricsComparatorResult` rows.
    """

    compare_corpus_loader: Callable[
        [str, str],
        tuple[list[GovernanceMetricValue], CounterfactualResult],
    ]
    """Loads the (baseline metrics, candidate counterfactual result)
    pair for the typed ``baseline_corpus_id`` + ``candidate_corpus_id``
    arguments.

    The default loader RAISES :class:`NotImplementedError` because
    the production loader requires the Slice 15 metric store + the
    Slice 18 counterfactual result writer that the READ-ONLY CLI does
    NOT own. Real callers MUST inject a custom loader.

    Per the fail-closed discipline the runner CATCHES the
    :class:`NotImplementedError` + emits a typed gap projection with
    :data:`EXIT_UPSTREAM_EXCEPTION`.
    """


def _default_snapshot_corpus_loader(feature_id: str) -> SnapshotAPICorpus:
    """Default snapshot corpus loader backed by bounded summary fixtures.

    Slice 19A remediation 19A-2 closes the historical fail-loud
    placeholder on the accepted default report path. The loader reads
    only checked-in bounded fixture summaries from
    ``tests/fixtures/execution_control_plane/feature_<feature_id>``.
    It enforces a small byte cap before parsing each file and a row
    cap before accepting each JSONL file, then projects those rows into
    typed refs/summaries consumed by :class:`GovernanceSnapshotAPI`.

    Unknown feature ids still fail loud so callers do not receive an
    empty, falsely-successful corpus.
    """

    return _load_fixture_snapshot_corpus(feature_id)


def _load_fixture_snapshot_corpus(feature_id: str) -> SnapshotAPICorpus:
    safe_feature_id = _validate_fixture_feature_id(feature_id)
    fixture_dir = _DEFAULT_FIXTURE_ROOT / f"feature_{safe_feature_id}"
    if not fixture_dir.is_dir():
        raise FileNotFoundError(
            "No bounded governance fixture corpus is available for "
            f"feature_id={feature_id!r}. Inject a typed loader via "
            "CLIProviderFactories.snapshot_corpus_loader for non-fixture "
            "features."
        )

    manifest = _read_fixture_json(fixture_dir / "manifest.json")
    if manifest.get("feature_id") != safe_feature_id:
        raise ValueError(
            "Fixture manifest feature_id mismatch for "
            f"feature_id={feature_id!r}"
        )
    collection_policy = manifest.get("collection_policy")
    if not isinstance(collection_policy, dict) or not (
        collection_policy.get("bounded_reads") is True
        and collection_policy.get("summary_only") is True
        and collection_policy.get("live_mutation") is False
    ):
        raise ValueError(
            "Fixture manifest does not prove bounded summary-only reads "
            f"for feature_id={feature_id!r}"
        )

    artifact_rows = _read_fixture_jsonl(
        fixture_dir / "artifact_summaries.jsonl"
    )
    event_rows = _read_fixture_jsonl(fixture_dir / "event_summaries.jsonl")
    slice_rows = _read_fixture_jsonl(
        fixture_dir / "selected_artifact_slices.jsonl"
    )
    audit_rows = _read_fixture_jsonl(fixture_dir / "collector_audit.jsonl")
    drag_findings = _read_fixture_json(fixture_dir / "drag_findings.json")

    _validate_collector_audit(audit_rows, safe_feature_id)

    artifact_digests = _digest_by_id(artifact_rows, id_key="id")
    event_digests = _digest_by_id(event_rows, id_key="id")
    slice_digests = {
        f"slice:{row.get('purpose')}": str(row.get("sha256", ""))
        for row in slice_rows
        if isinstance(row, dict) and row.get("purpose")
    }

    page_refs = [
        _page_ref_from_fixture_slice(safe_feature_id, row)
        for row in slice_rows
        if isinstance(row, dict)
    ]
    findings = _findings_from_fixture(
        safe_feature_id,
        drag_findings,
        artifact_digests=artifact_digests,
        event_digests=event_digests,
        slice_digests=slice_digests,
    )

    return SnapshotAPICorpus(
        findings=findings,
        recommendations=[],
        replay_results=[],
        page_refs=page_refs,
        corpus_evidence_quality="canonical",
        blocked_by=[],
    )


def _validate_fixture_feature_id(feature_id: str) -> str:
    if not feature_id or not feature_id.strip():
        raise ValueError("feature_id must be non-empty")
    stripped = feature_id.strip()
    if not all(ch.isalnum() or ch in {"_", "-"} for ch in stripped):
        raise ValueError(
            "feature_id contains characters that are not valid for the "
            "bounded fixture loader"
        )
    return stripped


def _read_fixture_text(path: Path) -> str:
    resolved = path.resolve()
    fixture_root = _DEFAULT_FIXTURE_ROOT.resolve()
    if fixture_root not in resolved.parents:
        raise ValueError(f"Refusing to read outside fixture root: {path}")
    size = resolved.stat().st_size
    if size > _DEFAULT_FIXTURE_MAX_FILE_BYTES:
        raise ValueError(
            f"Fixture file {resolved.name} exceeds bounded read cap "
            f"({_DEFAULT_FIXTURE_MAX_FILE_BYTES} bytes)"
        )
    return resolved.read_text(encoding="utf-8")


def _read_fixture_json(path: Path) -> dict[str, Any]:
    payload = json.loads(_read_fixture_text(path))
    if not isinstance(payload, dict):
        raise ValueError(f"Fixture JSON {path.name} must be an object")
    return payload


def _read_fixture_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lineno, raw in enumerate(_read_fixture_text(path).splitlines(), start=1):
        if lineno > _DEFAULT_FIXTURE_MAX_JSONL_ROWS:
            raise ValueError(
                f"Fixture JSONL {path.name} exceeds row cap "
                f"({_DEFAULT_FIXTURE_MAX_JSONL_ROWS})"
            )
        if not raw.strip():
            continue
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise ValueError(
                f"Fixture JSONL {path.name}:{lineno} must be an object"
            )
        rows.append(row)
    return rows


def _validate_collector_audit(
    rows: list[dict[str, Any]], feature_id: str
) -> None:
    if not rows:
        raise ValueError("collector_audit.jsonl must contain audit rows")
    for row in rows:
        if row.get("feature_id") != feature_id:
            raise ValueError("collector audit feature_id mismatch")
        if row.get("bounded") is not True:
            raise ValueError("collector audit row is not bounded")
        if row.get("forbidden_broad_hydration_count") != 0:
            raise ValueError("collector audit records broad hydration")
        if row.get("forbidden_live_mutation_count") != 0:
            raise ValueError("collector audit records live mutation")


def _digest_by_id(
    rows: list[dict[str, Any]], *, id_key: str
) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        row_id = row.get(id_key)
        if not isinstance(row_id, str) or not row_id:
            continue
        digest = row.get("sha256") or row.get("value_preview_sha256")
        if isinstance(digest, str) and digest:
            result[row_id] = digest
    return result


def _page_ref_from_fixture_slice(
    feature_id: str, row: dict[str, Any]
) -> GovernanceEvidencePageRef:
    artifact_id = _required_str(row, "artifact_id")
    purpose = _required_str(row, "purpose")
    digest = _required_str(row, "sha256")
    byte_start = _required_int(row, "start")
    byte_end = _required_int(row, "end")
    return GovernanceEvidencePageRef(
        page_ref_id=f"fixture:{feature_id}:slice:{artifact_id}:{byte_start}:{byte_end}",
        authority="legacy_artifact_summary",
        source_ref_id=artifact_id,
        byte_start=byte_start,
        byte_end=byte_end,
        digest=digest,
        completeness="paged",
        exact=True,
        stale_check={
            "ref_digest": digest,
            "source_row_count": 1,
            "authority": "legacy_artifact_summary",
            "freshness": f"fixture:{purpose}",
        },
    )


def _findings_from_fixture(
    feature_id: str,
    payload: dict[str, Any],
    *,
    artifact_digests: dict[str, str],
    event_digests: dict[str, str],
    slice_digests: dict[str, str],
) -> list[GovernanceFinding]:
    if payload.get("feature_id") != feature_id:
        raise ValueError("drag_findings feature_id mismatch")
    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        raise ValueError("drag_findings.findings must be a list")

    findings: list[GovernanceFinding] = []
    for index, raw in enumerate(raw_findings, start=1):
        if not isinstance(raw, dict):
            raise ValueError("drag_findings row must be an object")
        failure_class = _required_str(raw, "failure_class")
        observed = raw.get("observed") is True
        evidence_tokens = raw.get("evidence_refs")
        if not isinstance(evidence_tokens, list):
            raise ValueError("drag_findings evidence_refs must be a list")
        refs = [
            _evidence_ref_from_fixture_token(
                feature_id,
                str(token),
                artifact_digests=artifact_digests,
                event_digests=event_digests,
                slice_digests=slice_digests,
            )
            for token in evidence_tokens
        ]
        finding_key = (
            f"fixture:{feature_id}:finding:{index:02d}:{failure_class}"
        )
        findings.append(
            GovernanceFinding(
                idempotency_key=finding_key,
                kind=_finding_kind_for_failure_class(failure_class),
                class_name=failure_class,
                severity="medium" if observed else "low",
                confidence=0.9 if observed else 0.55,
                feature_id=feature_id,
                affected_scope={
                    "feature_id": feature_id,
                    "failure_class": failure_class,
                },
                primary_evidence_refs=refs,
                supporting_evidence_refs=[],
                implementation_log_anchors=[
                    "fixture:feature_8ac124d6:README.md"
                ],
                metric_refs=[f"fixture_failure_class:{failure_class}"],
                estimated_lost_hours=None,
                estimated_retry_impact=None,
                recommended_action_display=(
                    "Review bounded fixture evidence for "
                    f"{failure_class}; governance output is advisory."
                ),
                recommendation_draft_ref=None,
                safe_runtime_action=False,
                requires_policy_artifact=True,
                product_defect_related=(
                    failure_class == "product_contract_drift"
                ),
                workflow_related=True,
                causal_role="primary",
                primary_cause_finding_id=None,
                linked_finding_ids=[],
            )
        )
    return findings


def _evidence_ref_from_fixture_token(
    feature_id: str,
    token: str,
    *,
    artifact_digests: dict[str, str],
    event_digests: dict[str, str],
    slice_digests: dict[str, str],
) -> GovernanceEvidenceRef:
    if token.startswith("artifact:"):
        ref_id = token.removeprefix("artifact:")
        authority = "legacy_artifact_summary"
        digest = artifact_digests.get(ref_id)
    elif token.startswith("event:"):
        ref_id = token.removeprefix("event:")
        authority = "legacy_event"
        digest = event_digests.get(ref_id)
    elif token.startswith("slice:"):
        ref_id = token
        authority = "legacy_artifact_summary"
        digest = slice_digests.get(ref_id)
    else:
        ref_id = token
        authority = "legacy_artifact_summary"
        digest = None
    if not digest:
        raise ValueError(
            "Fixture evidence token has no digest in bounded indexes: "
            f"{token!r}"
        )
    return GovernanceEvidenceRef(
        authority=authority,
        ref_id=ref_id,
        feature_id=feature_id,
        digest=digest,
        quality="canonical",
        completeness="complete",
    )


def _finding_kind_for_failure_class(failure_class: str) -> str:
    mapping = {
        "broad_read_legacy_consumer": "provenance_gap",
        "checkpoint_contradiction": "governance_evidence_conflict",
        "commit_only_routing": "unsafe_route",
        "product_contract_drift": "product_defect_cluster",
        "queue_recovery": "merge_queue_drag",
        "runtime_provider": "runtime_instability",
        "stale_projection": "stale_projection",
    }
    return mapping.get(failure_class, "workflow_inefficiency")


def _required_str(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Fixture row missing non-empty string {key!r}")
    return value


def _required_int(row: dict[str, Any], key: str) -> int:
    value = row.get(key)
    if not isinstance(value, int):
        raise ValueError(f"Fixture row missing integer {key!r}")
    return value


def _default_line_provenance_reader_factory(repo_id: str) -> LineProvenanceReader:
    """Default line-provenance reader factory -- RAISES
    NotImplementedError.

    Per the CLI's READ-ONLY contract the production reader requires
    a Git working tree + 4 typed source ports + a subprocess runner
    that the CLI does NOT own. Real callers MUST inject a custom
    factory via :class:`CLIProviderFactories`.

    Per ``feedback_no_silent_degradation`` the factory FAILS LOUD
    rather than silently returning a degraded reader.
    """

    raise NotImplementedError(
        "No LineProvenanceReader factory is wired for "
        f"repo_id={repo_id!r}. Inject a typed factory via "
        "CLIProviderFactories.line_provenance_reader_factory (the "
        "CLI is a READ-ONLY projection consumer; the production "
        "reader requires Git + typed source ports per doc-14:171-184)."
    )


def _default_compare_corpus_loader(
    baseline_corpus_id: str, candidate_corpus_id: str
) -> tuple[list[GovernanceMetricValue], CounterfactualResult]:
    """Default compare corpus loader -- RAISES NotImplementedError.

    Per the CLI's READ-ONLY contract the production loader requires
    the Slice 15 metric store + Slice 18 counterfactual result
    writer that the CLI does NOT own. Real callers MUST inject a
    custom loader via :class:`CLIProviderFactories`.

    Per ``feedback_no_silent_degradation`` the loader FAILS LOUD.
    """

    raise NotImplementedError(
        "No compare corpus loader is wired for "
        f"baseline={baseline_corpus_id!r} candidate={candidate_corpus_id!r}. "
        "Inject a typed loader via "
        "CLIProviderFactories.compare_corpus_loader (the CLI is a "
        "READ-ONLY projection consumer; the production loader "
        "requires Slice 15 + Slice 18 typed primitives the CLI does "
        "not own per doc-18:115 step 5)."
    )


def default_provider_factories() -> CLIProviderFactories:
    """Construct the typed :class:`CLIProviderFactories` with the
    stateless default factories, the bounded fixture snapshot loader,
    and the fail-loud loaders for non-report surfaces that still lack
    owned default data sources.

    The CLI runner consumes this factory bundle when no override is
    passed. Unsupported feature ids and unsupported compare/provenance
    defaults still FAIL LOUD so callers never receive silent empty
    projections.
    """

    return CLIProviderFactories(
        snapshot_api_factory=GovernanceSnapshotAPI,
        snapshot_corpus_loader=_default_snapshot_corpus_loader,
        report_artifact_emitter_factory=GovernanceReportArtifactEmitter,
        line_provenance_reader_factory=_default_line_provenance_reader_factory,
        metrics_comparator_factory=CounterfactualMetricsComparator,
        compare_corpus_loader=_default_compare_corpus_loader,
    )


# --- Helpers: canonical JSON + prose stringification -----------------------


def _canonical_json(payload: Any) -> str:
    """Serialise the typed payload as canonical JSON.

    Per the Slice 13/14/15/16/17/18/19 canonical-form contract the
    JSON is sorted + compact + UTF-8. The CLI uses this serialiser
    for the ``--format=json`` (default) output so tests can assert
    byte-identical output across runs.

    Pydantic BaseModels are serialised via ``.model_dump(mode="json")``
    so :class:`datetime` etc. are JSON-safe.
    """

    if isinstance(payload, BaseModel):
        payload_dict = payload.model_dump(mode="json")
    elif isinstance(payload, list) and payload and isinstance(payload[0], BaseModel):
        payload_dict = [item.model_dump(mode="json") for item in payload]
    elif isinstance(payload, dict):
        payload_dict = _coerce_dict(payload)
    else:
        payload_dict = payload
    return json.dumps(payload_dict, sort_keys=True, indent=2, default=_json_default)


def _coerce_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """Coerce a typed dict that may contain BaseModel values to a
    JSON-safe dict.
    """

    result: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, BaseModel):
            result[key] = value.model_dump(mode="json")
        elif isinstance(value, list):
            coerced_list: list[Any] = []
            for item in value:
                if isinstance(item, BaseModel):
                    coerced_list.append(item.model_dump(mode="json"))
                elif isinstance(item, dict):
                    coerced_list.append(_coerce_dict(item))
                else:
                    coerced_list.append(item)
            result[key] = coerced_list
        elif isinstance(value, dict):
            result[key] = _coerce_dict(value)
        else:
            result[key] = value
    return result


def _json_default(obj: Any) -> Any:
    """JSON-serialiser fallback for non-trivial Python types.

    Covers :class:`datetime` + :class:`tuple` + frozenset. Raises
    :class:`TypeError` (per stdlib convention) for unsupported
    types so the CLI fails closed if a new untyped value sneaks
    into the payload.
    """

    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, tuple):
        return list(obj)
    raise TypeError(
        f"Object of type {type(obj).__name__} is not JSON serializable"
    )


def _render_prose(label: str, payload: Any) -> str:
    """Render the typed payload as a human-readable prose summary.

    Per doc-19:150 the prose rendering is OPT-IN (default is JSON).
    The prose layer is intentionally simple -- it just labels the
    payload + emits the canonical JSON underneath. Tests assert the
    label appears verbatim.

    The prose rendering preserves the fail-closed exit-code
    discipline -- the prose-vs-JSON choice does NOT change the exit
    code.
    """

    return f"=== {label} ===\n{_canonical_json(payload)}\n"


def _emit(payload: Any, *, fmt: str, stream: Any, label: str) -> None:
    """Write the typed payload to the typed stream using the typed
    format (``json`` or ``prose``).

    Per doc-19:150 the default is ``json``; ``prose`` is opt-in. The
    runner always writes to ``sys.stdout`` (the typed payload is the
    primary CLI output); the typed gap projections go through this
    same helper so callers see a consistent format.
    """

    if fmt == "prose":
        stream.write(_render_prose(label, payload))
    else:
        stream.write(_canonical_json(payload))
        stream.write("\n")


def _utcnow() -> datetime:
    """Return the current UTC timestamp (timezone-aware).

    Mirrors the Slice 19 2nd-6th sub-slice ``_utcnow`` helper
    verbatim. Stdlib-only.
    """

    return datetime.now(timezone.utc)


def _typed_gap_projection(
    *,
    subcommand: str,
    reason: str,
    exception_type: str | None = None,
    exception_message: str | None = None,
    upstream_gap_count: int = 0,
) -> dict[str, Any]:
    """Construct a typed gap-projection dict the CLI emits on
    blocked-evidence / upstream-exception paths.

    Per doc-19:198 the CLI emits a stable JSON payload (NOT a raw
    Python traceback) so callers can parse the gap programmatically.
    The shape is FLAT + uses primitive types only (per auto-memory
    ``feedback_flat_structured_output``).

    The typed gap dict carries:

    * ``subcommand`` -- the typed subcommand name that emitted the
      gap.
    * ``cli_failure_class`` -- the typed surface name; always
      ``"governance_cli_blocked_or_unavailable"`` for the CLI gap
      shape. This is INTENTIONALLY not registered in the typed
      :class:`~iriai_build_v2.workflows.develop.execution.failure_router.FailureType`
      Literal because the CLI is a pure projection consumer -- the
      typed failure ids come from upstream surfaces
      (``governance_snapshot_api_failed`` /
      ``governance_report_artifact_emission_failed`` /
      ``line_provenance_gap`` / ``metrics_comparator_failed``).
    * ``reason`` -- free-form string describing the gap class.
    * ``exception_type`` / ``exception_message`` -- when set, the
      caught upstream exception's type + first 500 chars of the
      message. Both ``None`` for non-exception blocked-evidence
      gaps.
    * ``upstream_gap_count`` -- when non-zero, the count of typed
      upstream gap findings propagated alongside this CLI-level gap.
    * ``observed_at`` -- ISO-8601 UTC timestamp the gap was observed.
    """

    return {
        "subcommand": subcommand,
        "cli_failure_class": "governance_cli_blocked_or_unavailable",
        "reason": reason,
        "exception_type": exception_type,
        "exception_message": exception_message,
        "upstream_gap_count": upstream_gap_count,
        "observed_at": _utcnow().isoformat(),
    }


# --- Argparse parser builder ----------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the typed argparse parser for the 4 subcommands per
    doc-19:62-65.

    The parser is the typed CLI shape -- re-usable across the
    :mod:`__main__` invocation path + the programmatic test E2E
    path.

    Per doc-19:150 the default ``--format`` is ``json``; the opt-in
    ``--format=prose`` enables the prose rendering layer. The
    ``--format`` flag is declared on the TOP-LEVEL parser (NOT
    per-subcommand) so callers can switch formats without remembering
    a per-subcommand syntax.
    """

    parser = argparse.ArgumentParser(
        prog="python -m iriai_build_v2.workflows.develop.governance",
        description=(
            "READ-ONLY governance CLI (Slice 19 8th sub-slice) -- "
            "typed projection consumer of the Slice 13-18 + Slice 19 "
            "2nd-6th sub-slice governance surfaces. Per doc-19:62-65 "
            "the CLI emits typed JSON to stdout + returns a nonzero "
            "exit code for blocked evidence per doc-19:198."
        ),
        epilog=(
            "Activation-authority boundary preserved per doc-19:348-349 "
            "+ doc-19:296-303 -- the CLI does NOT extend the Slice "
            "10c-1 CONTROL_PLANE_WRITER_METHODS set; the CLI does NOT "
            "emit dag-* artifact-key string literals; the CLI is a "
            "pure projection consumer."
        ),
    )
    parser.add_argument(
        "--format",
        choices=list(FORMAT_NAMES),
        default="json",
        help=(
            "Output format. Defaults to json (per doc-19:150 step 1: "
            "JSON-first); prose is opt-in for human-readable output."
        ),
    )

    subparsers = parser.add_subparsers(
        dest="subcommand",
        required=True,
        metavar="{" + ",".join(SUBCOMMAND_NAMES) + "}",
    )

    # --- analyze --feature-id <id> -----------------------------------
    analyze_parser = subparsers.add_parser(
        "analyze",
        help=(
            "Project a typed GovernanceSnapshot for the given "
            "feature-id (per doc-19:62 + doc-19:151 step 2)."
        ),
    )
    analyze_parser.add_argument(
        "--feature-id",
        required=True,
        type=str,
        help="The feature-id (corpus_id) to project the snapshot for.",
    )

    # --- report --feature-id <id> ------------------------------------
    report_parser = subparsers.add_parser(
        "report",
        help=(
            "Project a typed review:governance-report:{corpus_id} "
            "artifact for the given feature-id (per doc-19:63 + "
            "doc-19:161-162 step 6)."
        ),
    )
    report_parser.add_argument(
        "--feature-id",
        required=True,
        type=str,
        help="The feature-id (corpus_id) to project the report for.",
    )

    # --- explain-line --repo-id --path --line ------------------------
    explain_parser = subparsers.add_parser(
        "explain-line",
        help=(
            "Project a typed LineProvenanceReadResult for a "
            "(repo_id, path, line) tuple (per doc-19:64 + "
            "doc-14:171-184 step 5)."
        ),
    )
    explain_parser.add_argument(
        "--repo-id",
        required=True,
        type=str,
        help="The Git repo id to query.",
    )
    explain_parser.add_argument(
        "--path",
        required=True,
        type=str,
        help="The repo-relative file path to query.",
    )
    explain_parser.add_argument(
        "--line",
        required=True,
        type=int,
        help="The 1-indexed line number to query.",
    )
    explain_parser.add_argument(
        "--ref",
        default="HEAD",
        type=str,
        help="The Git ref to evaluate the query at (defaults to HEAD).",
    )

    # --- compare --baseline --candidate ------------------------------
    compare_parser = subparsers.add_parser(
        "compare",
        help=(
            "Project a typed counterfactual MetricsComparatorResult "
            "for the given (baseline, candidate) corpora pair (per "
            "doc-19:65 + doc-18:115 step 5)."
        ),
    )
    compare_parser.add_argument(
        "--baseline",
        required=True,
        type=str,
        help="The baseline corpus_id to project metrics from.",
    )
    compare_parser.add_argument(
        "--candidate",
        required=True,
        type=str,
        help="The candidate corpus_id to evaluate against the baseline.",
    )

    return parser


# --- Per-subcommand typed handlers ----------------------------------------


def cmd_analyze(
    *,
    feature_id: str,
    fmt: str,
    factories: CLIProviderFactories,
    stdout: Any,
) -> int:
    """Run the `analyze` subcommand (doc-19:62).

    Projects a typed :class:`GovernanceSnapshot` for the given
    ``feature_id`` via the typed Slice 19 2nd sub-slice
    :class:`GovernanceSnapshotAPI` upstream + the typed
    :class:`SnapshotAPICorpus` loaded by the injected corpus loader.

    Per doc-19:150 emits the typed snapshot as JSON (default) or
    prose (opt-in via ``fmt="prose"``). Per doc-19:198 returns a
    nonzero exit code (:data:`EXIT_BLOCKED_EVIDENCE`) when the typed
    snapshot is None OR when gap_findings is non-empty OR when the
    typed snapshot carries a non-empty ``blocked_by`` OR a
    ``preview_only`` / ``unavailable`` completeness state.

    Per ``feedback_no_silent_degradation`` the handler catches
    every exception from the upstream loaders + emits a typed gap
    projection JSON with :data:`EXIT_UPSTREAM_EXCEPTION`.
    """

    try:
        corpus = factories.snapshot_corpus_loader(feature_id)
        inputs = SnapshotAPIInputs(corpus_id=feature_id)
        api = factories.snapshot_api_factory()
        result: SnapshotAPIResult = api.build_snapshot(inputs, corpus)
    except Exception as exc:  # pragma: no cover - defensive; tests cover specific cases
        gap = _typed_gap_projection(
            subcommand="analyze",
            reason="upstream_snapshot_construction_exception",
            exception_type=type(exc).__name__,
            exception_message=str(exc)[:500],
        )
        _emit(gap, fmt=fmt, stream=stdout, label="analyze-gap")
        return EXIT_UPSTREAM_EXCEPTION

    # Doc-19:198 -- nonzero exit on blocked evidence.
    if result.snapshot is None:
        gap = _typed_gap_projection(
            subcommand="analyze",
            reason="upstream_snapshot_missing",
            upstream_gap_count=len(result.gap_findings),
        )
        # Emit the typed gap shape alongside the upstream gap findings
        # so callers can drilldown.
        payload: dict[str, Any] = {
            "cli_gap": gap,
            "upstream_gap_findings": [
                g.model_dump(mode="json") for g in result.gap_findings
            ],
        }
        _emit(payload, fmt=fmt, stream=stdout, label="analyze-blocked")
        return EXIT_BLOCKED_EVIDENCE

    snapshot: GovernanceSnapshot = result.snapshot
    if snapshot.blocked_by:
        # Snapshot was projected but flagged blocked; per doc-19:186-187
        # the snapshot is informational but blocked-evidence-bound.
        payload = {
            "snapshot": snapshot.model_dump(mode="json"),
            "cli_gap": _typed_gap_projection(
                subcommand="analyze",
                reason="snapshot_blocked_by_non_empty",
                upstream_gap_count=len(result.gap_findings),
            ),
        }
        _emit(payload, fmt=fmt, stream=stdout, label="analyze-blocked")
        return EXIT_BLOCKED_EVIDENCE
    if snapshot.completeness in ("preview_only", "unavailable"):
        # Per doc-19:128-131 + doc-13a:18-23 + doc-19:225-226 AC2
        # preview-only / unavailable snapshots are display-only +
        # cannot feed downstream consumers.
        payload = {
            "snapshot": snapshot.model_dump(mode="json"),
            "cli_gap": _typed_gap_projection(
                subcommand="analyze",
                reason=f"snapshot_completeness_{snapshot.completeness}",
                upstream_gap_count=len(result.gap_findings),
            ),
        }
        _emit(payload, fmt=fmt, stream=stdout, label="analyze-blocked")
        return EXIT_BLOCKED_EVIDENCE
    if result.gap_findings:
        # Informational gaps fired but snapshot is otherwise clean;
        # per doc-19:198 emit the snapshot + the gap shape +
        # nonzero exit so the caller knows downstream consumers may
        # need to refresh.
        payload = {
            "snapshot": snapshot.model_dump(mode="json"),
            "cli_gap": _typed_gap_projection(
                subcommand="analyze",
                reason="upstream_gap_findings_non_empty",
                upstream_gap_count=len(result.gap_findings),
            ),
            "upstream_gap_findings": [
                g.model_dump(mode="json") for g in result.gap_findings
            ],
        }
        _emit(payload, fmt=fmt, stream=stdout, label="analyze-blocked")
        return EXIT_BLOCKED_EVIDENCE

    _emit(
        snapshot, fmt=fmt, stream=stdout, label="analyze",
    )
    return EXIT_OK


def cmd_report(
    *,
    feature_id: str,
    fmt: str,
    factories: CLIProviderFactories,
    stdout: Any,
) -> int:
    """Run the `report` subcommand (doc-19:63 + doc-19:161-162 step 6).

    Projects a typed `review:governance-report:{corpus_id}` artifact
    via the typed Slice 19 6th sub-slice
    :class:`GovernanceReportArtifactEmitter` upstream. The upstream
    snapshot is built via the same path as
    :func:`cmd_analyze`.

    Per doc-19:150 emits the typed artifact as JSON (default) or
    prose (opt-in). Per doc-19:198 returns a nonzero exit code when
    the typed artifact is None OR when gap_findings is non-empty
    OR when the artifact carries a non-empty ``blocked_by`` OR a
    ``preview_only`` / ``unavailable`` completeness state.
    """

    try:
        corpus = factories.snapshot_corpus_loader(feature_id)
        inputs = SnapshotAPIInputs(corpus_id=feature_id)
        api = factories.snapshot_api_factory()
        snapshot_result: SnapshotAPIResult = api.build_snapshot(
            inputs, corpus
        )
        emitter = factories.report_artifact_emitter_factory()
        report_inputs = ReportArtifactInputs(source=snapshot_result)
        result: ReportArtifactResult = emitter.emit_report_artifact(
            report_inputs
        )
    except Exception as exc:  # pragma: no cover - defensive; tests cover specific cases
        gap = _typed_gap_projection(
            subcommand="report",
            reason="upstream_report_projection_exception",
            exception_type=type(exc).__name__,
            exception_message=str(exc)[:500],
        )
        _emit(gap, fmt=fmt, stream=stdout, label="report-gap")
        return EXIT_UPSTREAM_EXCEPTION

    if result.artifact is None:
        gap = _typed_gap_projection(
            subcommand="report",
            reason="upstream_artifact_missing",
            upstream_gap_count=len(result.gap_findings),
        )
        payload: dict[str, Any] = {
            "cli_gap": gap,
            "upstream_gap_findings": [
                g.model_dump(mode="json") for g in result.gap_findings
            ],
        }
        _emit(payload, fmt=fmt, stream=stdout, label="report-blocked")
        return EXIT_BLOCKED_EVIDENCE

    artifact: GovernanceReportArtifact = result.artifact
    if artifact.blocked_by:
        payload = {
            "artifact": artifact.model_dump(mode="json"),
            "cli_gap": _typed_gap_projection(
                subcommand="report",
                reason="artifact_blocked_by_non_empty",
                upstream_gap_count=len(result.gap_findings),
            ),
        }
        _emit(payload, fmt=fmt, stream=stdout, label="report-blocked")
        return EXIT_BLOCKED_EVIDENCE
    if artifact.completeness in ("preview_only", "unavailable"):
        payload = {
            "artifact": artifact.model_dump(mode="json"),
            "cli_gap": _typed_gap_projection(
                subcommand="report",
                reason=f"artifact_completeness_{artifact.completeness}",
                upstream_gap_count=len(result.gap_findings),
            ),
        }
        _emit(payload, fmt=fmt, stream=stdout, label="report-blocked")
        return EXIT_BLOCKED_EVIDENCE
    if result.gap_findings:
        payload = {
            "artifact": artifact.model_dump(mode="json"),
            "cli_gap": _typed_gap_projection(
                subcommand="report",
                reason="upstream_gap_findings_non_empty",
                upstream_gap_count=len(result.gap_findings),
            ),
            "upstream_gap_findings": [
                g.model_dump(mode="json") for g in result.gap_findings
            ],
        }
        _emit(payload, fmt=fmt, stream=stdout, label="report-blocked")
        return EXIT_BLOCKED_EVIDENCE

    _emit(artifact, fmt=fmt, stream=stdout, label="report")
    return EXIT_OK


def cmd_explain_line(
    *,
    repo_id: str,
    path: str,
    line: int,
    ref: str,
    fmt: str,
    factories: CLIProviderFactories,
    stdout: Any,
) -> int:
    """Run the `explain-line` subcommand (doc-19:64 + doc-14:171-184
    step 5).

    Projects a typed :class:`LineProvenanceReadResult` for the given
    (repo_id, path, line) tuple via the typed Slice 14
    :class:`LineProvenanceReader` upstream.

    Per doc-19:150 emits the typed result as JSON (default) or
    prose (opt-in). Per doc-19:198 + doc-14:202-205 returns a
    nonzero exit code when the typed result is ineligible for
    downstream consumers (the
    :attr:`LineProvenanceReadResult.is_eligible_for_downstream_consumers`
    property is False -- i.e., completeness is ``preview_only`` or
    ``unavailable``) OR when the result carries a non-empty
    ``gap_finding``.
    """

    try:
        reader = factories.line_provenance_reader_factory(repo_id)
        query = LineProvenanceQuery(
            repo_id=repo_id,
            ref=ref,
            path=path,
            line_start=line,
            line_end=line,
        )
        result: LineProvenanceReadResult = reader.read(query)
    except Exception as exc:  # pragma: no cover - defensive
        gap = _typed_gap_projection(
            subcommand="explain-line",
            reason="upstream_line_provenance_exception",
            exception_type=type(exc).__name__,
            exception_message=str(exc)[:500],
        )
        _emit(gap, fmt=fmt, stream=stdout, label="explain-line-gap")
        return EXIT_UPSTREAM_EXCEPTION

    # Compose the typed CLI projection payload.
    line_result: LineProvenanceResult = result.result
    if (
        not result.is_eligible_for_downstream_consumers
        or result.gap_finding is not None
    ):
        gap_payload: dict[str, Any] | None = None
        if result.gap_finding is not None:
            gap_payload = result.gap_finding.model_dump(mode="json")
        payload: dict[str, Any] = {
            "result": line_result.model_dump(mode="json"),
            "cli_gap": _typed_gap_projection(
                subcommand="explain-line",
                reason=(
                    f"line_provenance_completeness_{line_result.completeness}"
                    if not result.is_eligible_for_downstream_consumers
                    else "upstream_gap_finding_present"
                ),
                upstream_gap_count=1 if result.gap_finding is not None else 0,
            ),
            "upstream_gap_finding": gap_payload,
        }
        _emit(
            payload, fmt=fmt, stream=stdout, label="explain-line-blocked"
        )
        return EXIT_BLOCKED_EVIDENCE

    _emit(line_result, fmt=fmt, stream=stdout, label="explain-line")
    return EXIT_OK


def cmd_compare(
    *,
    baseline_corpus_id: str,
    candidate_corpus_id: str,
    fmt: str,
    factories: CLIProviderFactories,
    stdout: Any,
) -> int:
    """Run the `compare` subcommand (doc-19:65 + doc-18:115 step 5).

    Projects a typed :class:`MetricsComparatorResult` for the given
    (baseline_corpus_id, candidate_corpus_id) tuple via the typed
    Slice 18 :class:`CounterfactualMetricsComparator` upstream + the
    typed (baseline_metrics, candidate_counterfactual_result) pair
    loaded by the injected corpus loader.

    Per doc-19:150 emits the typed result as JSON (default) or
    prose (opt-in). Per doc-19:198 returns a nonzero exit code when
    the typed result carries non-empty gap_findings OR an empty
    per_axis_deltas list.
    """

    try:
        baseline_metrics, candidate_result = factories.compare_corpus_loader(
            baseline_corpus_id, candidate_corpus_id
        )
        comparator = factories.metrics_comparator_factory()
        result_id = (
            f"cli-compare:{baseline_corpus_id}:{candidate_corpus_id}"
        )
        inputs = MetricsComparatorInputs(
            baseline_metrics=baseline_metrics,
            scenario_result=candidate_result,
            result_id=result_id,
        )
        result: MetricsComparatorResult = comparator.compare(inputs)
    except Exception as exc:  # pragma: no cover - defensive
        gap = _typed_gap_projection(
            subcommand="compare",
            reason="upstream_metrics_comparator_exception",
            exception_type=type(exc).__name__,
            exception_message=str(exc)[:500],
        )
        _emit(gap, fmt=fmt, stream=stdout, label="compare-gap")
        return EXIT_UPSTREAM_EXCEPTION

    if result.gap_findings:
        payload: dict[str, Any] = {
            "result": result.model_dump(mode="json"),
            "cli_gap": _typed_gap_projection(
                subcommand="compare",
                reason="upstream_gap_findings_non_empty",
                upstream_gap_count=len(result.gap_findings),
            ),
        }
        _emit(payload, fmt=fmt, stream=stdout, label="compare-blocked")
        return EXIT_BLOCKED_EVIDENCE

    _emit(result, fmt=fmt, stream=stdout, label="compare")
    return EXIT_OK


# --- Top-level CLI runner (programmatic E2E surface + __main__ entry) -----


def main(
    argv: Sequence[str] | None = None,
    *,
    factories: CLIProviderFactories | None = None,
    stdout: Any | None = None,
    stderr: Any | None = None,
) -> int:
    """Top-level CLI runner.

    Programmatic E2E entry point + the runner backing the
    :mod:`__main__` ``python -m`` invocation pattern. Returns the
    typed exit code (0 / 1 / 2 / 3).

    Per the fail-closed discipline the runner catches argparse's
    :class:`SystemExit` (raised by argparse on usage errors) and
    emits a typed gap-projection JSON with
    :data:`EXIT_USAGE_ERROR` rather than letting argparse's stderr
    output break the JSON-first contract.

    :param argv: optional argv list (defaults to ``sys.argv[1:]``).
    :param factories: optional :class:`CLIProviderFactories` (defaults
        to :func:`default_provider_factories`). Tests override to
        inject fakes.
    :param stdout: optional output stream (defaults to ``sys.stdout``).
    :param stderr: optional error stream (defaults to ``sys.stderr``).
    """

    if argv is None:
        argv = sys.argv[1:]
    if factories is None:
        factories = default_provider_factories()
    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr

    parser = build_parser()
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        # argparse raises SystemExit on usage error (exit code 2) AND
        # on --help / --version (exit code 0). We propagate 0 verbatim
        # (so `--help` returns 0 per the stdlib convention) and
        # translate non-zero argparse exits to our typed EXIT_USAGE_ERROR
        # + emit a stable JSON gap shape on stdout so the JSON-first
        # contract is preserved per doc-19:150.
        if exc.code == 0:
            return EXIT_OK
        gap = _typed_gap_projection(
            subcommand="<usage>",
            reason="argparse_usage_error",
            exception_type="SystemExit",
            exception_message=f"argparse exit code {exc.code}",
        )
        # argparse already wrote its usage message to stderr; we
        # also write the typed gap to stdout so the JSON contract
        # holds.
        try:
            stdout.write(_canonical_json(gap))
            stdout.write("\n")
        except Exception:  # pragma: no cover - stream closed
            pass
        return EXIT_USAGE_ERROR

    fmt: str = args.format
    subcommand: str = args.subcommand

    if subcommand == "analyze":
        return cmd_analyze(
            feature_id=args.feature_id,
            fmt=fmt,
            factories=factories,
            stdout=stdout,
        )
    if subcommand == "report":
        return cmd_report(
            feature_id=args.feature_id,
            fmt=fmt,
            factories=factories,
            stdout=stdout,
        )
    if subcommand == "explain-line":
        return cmd_explain_line(
            repo_id=args.repo_id,
            path=args.path,
            line=args.line,
            ref=args.ref,
            fmt=fmt,
            factories=factories,
            stdout=stdout,
        )
    if subcommand == "compare":
        return cmd_compare(
            baseline_corpus_id=args.baseline,
            candidate_corpus_id=args.candidate,
            fmt=fmt,
            factories=factories,
            stdout=stdout,
        )

    # Should be unreachable because argparse `required=True` enforces
    # the subcommand choice; keep a defensive branch + typed gap.
    gap = _typed_gap_projection(
        subcommand=subcommand or "<none>",
        reason="unknown_subcommand",
    )
    stdout.write(_canonical_json(gap))
    stdout.write("\n")
    return EXIT_USAGE_ERROR
