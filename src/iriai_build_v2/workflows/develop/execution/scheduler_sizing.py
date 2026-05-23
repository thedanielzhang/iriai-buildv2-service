"""The conservative cap computation + the typed ``SchedulerFeedback`` (Slice 09d-2).

Slice 09 generalizes the one-off ``G45-G73`` derived-DAG regroup into a
reusable typed overlay and feeds typed execution metrics back into future wave
sizing. 09a delivered the typed models (``regroup_overlay``), 09b the store,
09b-2 the validator, 09c activation / rollback / the fail-closed resolver, and
09d-1 the deterministic :func:`~iriai_build_v2.workflows.develop.execution.scheduler_metrics.build_scheduler_group_metrics`
typed-evidence joiner.

This module (**09d-2**) is the LAST Slice 09 implementation sub-slice (09e is
the slice-end six-vector review). It consumes 09d-1's
:class:`~iriai_build_v2.workflows.develop.execution.regroup_overlay.SchedulerGroupMetric`
list and delivers two things doc ``09-regroup-overlay-and-scheduler-feedback.md``
§§ "Scheduler Metrics And Cap Rules" / "Scheduler Feedback Schema" / "Adaptive
Sizing Data Flow" steps 4-8 specify:

(A) **The conservative cap computation** — every formula / threshold is
    transcribed VERBATIM from doc 09 § "Scheduler Metrics And Cap Rules":

    1. ``policy_cap`` from task risk — *unknown writes or high-risk barriers*
       cap at 4; *backend or multi-repo* work caps at 6; *isolated UI/document*
       work caps at 10; *test-only and perf* lanes cap at 14.
    2. Require **at least two** completed samples with evidence ids for the
       lane/barrier before widening above the current cap.
    3. Reduce to the current cap or 4 when workflow repair, product repair,
       commit failure, or merge conflict rates exceed the global completed
       baseline by **more than 10 percent**.
    4. ``evidence_cap = floor(12h_checkpoint_budget / hours_per_task_p75)``,
       clamped to ``[4, policy_cap]``.
    5. Apply the dependency / hard-barrier / write-set / mapping validators
       **after** cap selection — they may *shrink* or *reject* a candidate
       wave; metrics may NOT override them.

(B) **The typed ``SchedulerFeedback`` + the review projection** — adaptive
    sizing emits a typed :class:`~...regroup_overlay.SchedulerFeedback` (the 09a
    model), persisted via
    :meth:`~iriai_build_v2.execution_control.regroup_overlay_store.RegroupOverlayStore.insert_scheduler_feedback`,
    plus a ``review:dag-sizing:{feature_id}:{window}`` compatibility projection.

**CRITICAL SAFETY PROPERTY — 09d-2 NEVER writes an active marker.** It can
*recommend* caps and candidate waves, but a recommendation must be converted
into a NEW :class:`~...regroup_overlay.RegroupOverlay` and pass the full
13-step :func:`~iriai_build_v2.workflows.develop.execution.regroup_overlay_validation.validate_overlay`
before activation (doc 09 § "Validation Algorithm": "Validation never widens a
wave because of scheduler metrics. Metrics can only produce a recommendation
artifact. The recommendation must be converted into a new overlay and pass the
same validation path before activation"). The ``review:dag-sizing:*`` key is
NEVER consumed by
:class:`~iriai_build_v2.workflows.develop.execution.regroup_overlay_resolver.RegroupOverlayResolver`.
This module's persistence surface is *exactly two writes* — one
``execution_scheduler_feedback`` row (through the 09b store) and one
``review:dag-sizing:*`` ``artifacts`` row — and NOTHING else: no
``execution_regroup_overlays`` row, no ``dag-regroup-active:*`` /
``dag-regroup:*`` / ``dag-regroup-rollback:*`` artifact, no ``events`` row, no
typed-attempt / evidence / merge-queue row. The cap recommendation cannot reach
dispatch without going through ``validate_overlay`` + ``activate_overlay``.

**``data_quality`` degradation.** doc 09 § "Scheduler Feedback Schema": a
metric "is usable for sizing only when it has typed evidence links for task
attempts, gate/verification, merge or checkpoint, and compatibility projection
lineage while legacy readers remain. Missing links do not block status
reporting, but they set ``data_quality_flags`` and force
``SchedulerFeedback.data_quality`` away from ``sufficient``." 09d-1 sets those
``data_quality_flags`` per missing category;
:func:`compute_scheduler_feedback` reads them: ANY contributing metric carrying
a ``data_quality_flags`` entry forces ``data_quality`` off ``sufficient``
(``missing_projection_lineage`` → ``stale``; any other flag → ``insufficient``
when *every* contributing metric is flagged, else ``mixed``). doc 09
§ "Adaptive Sizing Data Flow" step 3: "stale projection lineage sets
``data_quality="stale"``."

**Determinism.** Every digest is over sorted-keys JSON (``stable_digest``);
percentiles use a fixed nearest-rank rule; there is no clock / random in any
id. ``generated_at`` is an explicit caller-supplied parameter (so a test can
pin it) — it is NOT part of ``feedback_id``.

**Why a sibling leaf module (not ``scheduler_metrics.py`` / not
``regroup_overlay.py``).** ``scheduler_metrics.py`` (09d-1) is review-CLEAN and
the 09a/09b/09c/09d-1 no-refactor discipline (STATUS.md "Loop discipline")
keeps it untouched; ``regroup_overlay.py`` is the 09a model-only module the
same discipline protects. This module imports the 09a models, 09d-1's
:func:`build_scheduler_group_metrics`, and the 09b
:class:`RegroupOverlayStore`. ``dag_regroup.py`` is left UNTOUCHED — the legacy
``recommend_adaptive_sizing`` stays a compatibility path until 09e's facade
conversion; doc 09 § "Refactoring Steps" item 6 makes the typed
``SchedulerFeedback`` + ``review:dag-sizing:*`` projection the new output.

Interfaces verified at file:line while writing this module:

- ``SchedulerGroupMetric`` / ``SchedulerFeedback`` (the 09a feedback models,
  every field) — ``regroup_overlay.py`` (09a).
- ``RegroupOverlay`` (``group_idx_offset`` / ``derived_execution_order`` /
  ``write_sets`` / ``barriers`` (``OverlayBarrier.hard`` / ``barrier_id`` /
  ``task_ids``) / ``speed_index`` (``OverlayTaskSpeedMetadata``) /
  ``remaining_dependency_edges``) — ``regroup_overlay.py``.
- ``build_scheduler_group_metrics`` /
  ``effective_execution_order_for_overlay`` / ``SchedulerMetricsError`` —
  ``scheduler_metrics.py`` (09d-1).
- ``RegroupOverlayStore.insert_scheduler_feedback`` (idempotent insert keyed on
  ``scheduler_feedback_idempotency_key``) + ``RegroupOverlayStoreError`` —
  ``execution_control/regroup_overlay_store.py`` (09b).
- ``stable_digest`` / ``stable_json`` (sha256 over sorted-keys compact JSON) —
  ``execution_control/models.py:1141-1146``.
- The ``artifacts`` ``INSERT`` shape (``feature_id`` / ``key`` / ``value``) —
  ``regroup_overlay_activation.py:594`` ``_insert_artifact``.
- ``ImplementationDAG`` / ``ImplementationTask`` (``id`` / ``dependencies`` /
  ``files``) — ``models/outputs.py:942`` / ``:984``.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

import asyncpg

from ....execution_control.models import stable_digest
from ....execution_control.regroup_overlay_store import (
    RegroupOverlayStore,
    SchedulerFeedbackRecord,
)
from ....models.outputs import ImplementationDAG
from .regroup_overlay import (
    RegroupOverlay,
    SchedulerFeedback,
    SchedulerGroupMetric,
)
from .scheduler_metrics import (
    build_scheduler_group_metrics,
    effective_execution_order_for_overlay,
)

__all__ = [
    "SchedulerSizingError",
    "CapDecision",
    "CandidateWave",
    "AdaptiveSizingResult",
    "POLICY_CAP_UNKNOWN_OR_HIGH_RISK",
    "POLICY_CAP_BACKEND_OR_MULTI_REPO",
    "POLICY_CAP_ISOLATED_UI_OR_DOCUMENT",
    "POLICY_CAP_TEST_OR_PERF",
    "MIN_CAP",
    "CHECKPOINT_BUDGET_HOURS",
    "RATE_REGRESSION_THRESHOLD",
    "MIN_SAMPLES_TO_WIDEN",
    "compute_policy_cap",
    "compute_evidence_cap",
    "compute_cap_decision",
    "build_candidate_waves",
    "compute_scheduler_feedback",
    "persist_scheduler_feedback",
    "project_sizing_review",
    "run_adaptive_sizing",
]


# ── error ───────────────────────────────────────────────────────────────────


class SchedulerSizingError(RuntimeError):
    """Raised when the adaptive-sizing computation is given inconsistent inputs.

    Fail-fast (a clear error, never a silent degraded result) — doc 09 / the
    feedback-no-silent-degradation rule.
    """


# ── doc-09 § "Scheduler Metrics And Cap Rules" constants (VERBATIM) ──────────
#
# Every number here is transcribed verbatim from doc 09 § "Scheduler Metrics
# And Cap Rules" (== § "Adaptive Sizing Data Flow" steps 5-6). Nothing is
# invented or rounded.

# Step 1: policy_cap from task risk.
#   "unknown writes or high-risk barriers cap at 4"
POLICY_CAP_UNKNOWN_OR_HIGH_RISK = 4
#   "backend or multi-repo work caps at 6"
POLICY_CAP_BACKEND_OR_MULTI_REPO = 6
#   "isolated UI/document work caps at 10"
POLICY_CAP_ISOLATED_UI_OR_DOCUMENT = 10
#   "test-only and perf lanes cap at 14"
POLICY_CAP_TEST_OR_PERF = 14

# The conservative floor every cap clamps to (doc 09 step 4: clamped to
# `[4, policy_cap]`; step 3: "Reduce to current cap or 4").
MIN_CAP = 4

# Step 4: `floor(12h_checkpoint_budget / hours_per_task_p75)`.
CHECKPOINT_BUDGET_HOURS = 12.0

# Step 3: rates that exceed the global completed baseline "by more than 10
# percent" force the reduction. Expressed as a multiplicative factor: a rate is
# a regression when `rate > baseline * 1.10`.
RATE_REGRESSION_THRESHOLD = 0.10

# Step 2: "Require at least two completed samples with evidence ids for the
# lane/barrier before widening above the current cap."
MIN_SAMPLES_TO_WIDEN = 2


# ── high-risk barrier / lane classification ─────────────────────────────────
#
# doc 09 § "Scheduler Metrics And Cap Rules" step 1 names four risk tiers but
# does not enumerate which barrier ids / lane names belong to each. The
# overlay's typed `OverlayTaskSpeedMetadata` carries a free-text `barrier` and
# `semantic_lane`; doc 09 also flags per-task `unknown_write`. We classify by
# matching documented substrings, conservatively: an unrecognized lane/barrier
# falls to the SAFEST tier (the 4-cap) so a mis-tagged lane never widens. The
# four tiers, in doc-09 order, are checked most-conservative first.

# "high-risk barriers" — the barrier id / lane carries one of these markers.
_HIGH_RISK_BARRIER_MARKERS: tuple[str, ...] = (
    "high-risk",
    "high_risk",
    "highrisk",
    "hard-barrier",
    "hard_barrier",
    "schema",
    "migration",
    "release",
)
# "test-only and perf lanes" (cap 14).
_TEST_OR_PERF_LANE_MARKERS: tuple[str, ...] = (
    "test",
    "perf",
    "performance",
    "benchmark",
)
# "isolated UI/document work" (cap 10).
_ISOLATED_UI_OR_DOCUMENT_LANE_MARKERS: tuple[str, ...] = (
    "ui",
    "frontend",
    "front-end",
    "doc",
    "docs",
    "document",
    "documentation",
    "copy",
    "content",
)
# "backend or multi-repo work" (cap 6).
_BACKEND_LANE_MARKERS: tuple[str, ...] = (
    "backend",
    "back-end",
    "server",
    "api",
    "service",
    "db",
    "database",
    "infra",
    "infrastructure",
)


def _markers_hit(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in markers)


# ── (A) the conservative cap computation ─────────────────────────────────────


class CapDecision:
    """The outcome of :func:`compute_cap_decision`.

    Plain attribute carrier (not a Pydantic model — it is internal scheduler
    state, never agent-facing structured output). Carries every intermediate
    value so :func:`compute_scheduler_feedback` can record precise reasons.
    """

    __slots__ = (
        "recommended_cap",
        "current_cap",
        "policy_cap",
        "evidence_cap",
        "sample_count",
        "reduced",
        "widened",
        "reasons",
    )

    def __init__(
        self,
        *,
        recommended_cap: int,
        current_cap: int,
        policy_cap: int,
        evidence_cap: int | None,
        sample_count: int,
        reduced: bool,
        widened: bool,
        reasons: list[str],
    ) -> None:
        self.recommended_cap = recommended_cap
        self.current_cap = current_cap
        self.policy_cap = policy_cap
        self.evidence_cap = evidence_cap
        self.sample_count = sample_count
        self.reduced = reduced
        self.widened = widened
        self.reasons = reasons


def compute_policy_cap(
    *,
    unknown_write: bool,
    high_risk_barrier: bool,
    barrier: str,
    lane: str,
    multi_repo: bool,
) -> tuple[int, str]:
    """``policy_cap`` from task risk — doc 09 § "Scheduler Metrics" step 1.

    The four tiers, transcribed VERBATIM:

    - "unknown writes or high-risk barriers cap at 4"
    - "backend or multi-repo work caps at 6"
    - "isolated UI/document work caps at 10"
    - "test-only and perf lanes cap at 14"

    Evaluated most-conservative-first: a window that is *both* unknown-write
    and test-lane caps at 4 (the riskier tier wins). ``barrier`` / ``lane``
    are the typed ``OverlayTaskSpeedMetadata`` strings for the window; an
    unrecognized lane/barrier conservatively falls to the 4-cap so a mis-tagged
    lane can never widen above the floor.

    Returns ``(policy_cap, reason)`` — the reason names the matched tier.
    """

    barrier_text = barrier or ""
    lane_text = lane or ""

    # Tier 1 (cap 4) — unknown writes OR high-risk barriers.
    if unknown_write:
        return POLICY_CAP_UNKNOWN_OR_HIGH_RISK, "policy_cap=4 (unknown write set)"
    if high_risk_barrier or _markers_hit(barrier_text, _HIGH_RISK_BARRIER_MARKERS):
        return (
            POLICY_CAP_UNKNOWN_OR_HIGH_RISK,
            f"policy_cap=4 (high-risk barrier {barrier_text!r})",
        )

    # Tier 2 (cap 6) — backend OR multi-repo work.
    if multi_repo:
        return POLICY_CAP_BACKEND_OR_MULTI_REPO, "policy_cap=6 (multi-repo work)"
    if _markers_hit(lane_text, _BACKEND_LANE_MARKERS):
        return (
            POLICY_CAP_BACKEND_OR_MULTI_REPO,
            f"policy_cap=6 (backend lane {lane_text!r})",
        )

    # Tier 4 (cap 14) — test-only and perf lanes. Checked before tier 3 because
    # a test/perf lane is unambiguous and never also isolated-UI.
    if _markers_hit(lane_text, _TEST_OR_PERF_LANE_MARKERS):
        return POLICY_CAP_TEST_OR_PERF, f"policy_cap=14 (test/perf lane {lane_text!r})"

    # Tier 3 (cap 10) — isolated UI/document work.
    if _markers_hit(lane_text, _ISOLATED_UI_OR_DOCUMENT_LANE_MARKERS):
        return (
            POLICY_CAP_ISOLATED_UI_OR_DOCUMENT,
            f"policy_cap=10 (isolated UI/document lane {lane_text!r})",
        )

    # Unrecognized lane/barrier — conservative floor (doc 09: caps are
    # conservative; an unknown lane must not widen above the safe minimum).
    return (
        POLICY_CAP_UNKNOWN_OR_HIGH_RISK,
        f"policy_cap=4 (unclassified lane {lane_text!r}/barrier {barrier_text!r})",
    )


def compute_evidence_cap(
    *,
    hours_per_task_p75: float | None,
    policy_cap: int,
) -> tuple[int | None, str]:
    """``evidence_cap`` from p75 hours/task — doc 09 § "Scheduler Metrics" step 4.

    Transcribed VERBATIM: ``evidence_cap = floor(12h_checkpoint_budget /
    hours_per_task_p75)``, clamped to ``[4, policy_cap]``.

    Returns ``(evidence_cap, reason)``. ``evidence_cap`` is ``None`` when
    ``hours_per_task_p75`` is unavailable (no completed samples) or non-positive
    — there is no p75 throughput to bound the cap, so the caller falls back to
    the policy cap / current cap.
    """

    if hours_per_task_p75 is None or hours_per_task_p75 <= 0:
        return None, "evidence_cap=None (no p75 hours/task — no completed samples)"
    raw = math.floor(CHECKPOINT_BUDGET_HOURS / hours_per_task_p75)
    # clamp to [4, policy_cap].
    clamped = max(MIN_CAP, min(raw, policy_cap))
    return (
        clamped,
        (
            f"evidence_cap=floor({CHECKPOINT_BUDGET_HOURS:g}h / "
            f"{hours_per_task_p75:g}h)={raw} clamped to [{MIN_CAP},{policy_cap}]"
            f"={clamped}"
        ),
    )


def _percentile_nearest_rank(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile of a non-empty sorted-able list, or ``None``.

    Deterministic: the nearest-rank method (no interpolation) is order- and
    float-stable. ``pct`` is a fraction in ``[0, 1]``. The rank is
    ``ceil(pct * n)`` clamped to ``[1, n]``. doc 09 names ``p50`` / ``p75`` but
    does not pin a percentile estimator; nearest-rank is the simplest fully
    deterministic choice and matches the conservative-by-construction intent
    (it never invents an intermediate value).
    """

    present = sorted(v for v in values if v is not None)
    if not present:
        return None
    n = len(present)
    rank = max(1, min(n, math.ceil(pct * n)))
    return present[rank - 1]


def compute_cap_decision(
    *,
    completed_metrics: list[SchedulerGroupMetric],
    current_cap: int,
    policy_cap: int,
    hours_per_task_p75: float | None,
    baseline: "_GlobalBaseline",
    stale: bool,
) -> CapDecision:
    """The full conservative cap computation — doc 09 § "Scheduler Metrics".

    Composes, in doc-09 order:

    1. ``policy_cap`` (already computed by :func:`compute_policy_cap`, passed
       in) — the hard ceiling.
    2. The **widen guard** (step 2): a cap above ``current_cap`` requires
       ``>= MIN_SAMPLES_TO_WIDEN`` completed samples *with evidence ids*.
    3. The **rate-regression reduction** (step 3): when the window's workflow
       repair / product repair / commit failure / merge conflict per-task rate
       exceeds the global completed baseline by ``> 10%``, reduce to
       ``min(current_cap, MIN_CAP)``.
    4. ``evidence_cap`` (step 4): ``floor(12h / hours_per_task_p75)`` clamped to
       ``[4, policy_cap]``.
    5. The post-cap dependency / hard-barrier / write-set / mapping validators
       are applied SEPARATELY by :func:`build_candidate_waves` — step 5 says
       they run "after cap selection" and "may shrink or reject a candidate
       wave; metrics may not override them."

    The conservative compositions:

    - ``recommended_cap`` starts at ``min(policy_cap, evidence_cap)`` (or
      ``policy_cap`` when ``evidence_cap`` is ``None``).
    - It is then clamped DOWN to ``current_cap`` whenever a widen is not
      justified — step 2 (insufficient samples) AND step 6 ("If sample count is
      below two, data is stale, ... keep the current cap or reduce to 4"):
      stale data or ``sample_count < 2`` clamps to ``min(current_cap,
      candidate)``.
    - A rate regression overrides everything downward to ``min(current_cap,
      MIN_CAP)``.
    - The result is never below ``MIN_CAP``.
    """

    reasons: list[str] = []
    # Only completed groups with evidence ids count as samples for widening
    # (step 2: "completed samples WITH evidence ids").
    samples = [m for m in completed_metrics if m.completed and m.evidence_ids]
    sample_count = len(samples)

    # (4) evidence_cap.
    evidence_cap, evidence_reason = compute_evidence_cap(
        hours_per_task_p75=hours_per_task_p75, policy_cap=policy_cap
    )
    reasons.append(evidence_reason)

    # The metrics-supported candidate cap: bounded by BOTH the policy cap and
    # the evidence cap (the smaller wins — conservative).
    if evidence_cap is None:
        candidate = policy_cap
        reasons.append(
            f"candidate_cap={policy_cap} (policy cap only — no evidence cap)"
        )
    else:
        candidate = min(policy_cap, evidence_cap)
        reasons.append(
            f"candidate_cap=min(policy_cap={policy_cap}, "
            f"evidence_cap={evidence_cap})={candidate}"
        )

    recommended = candidate
    reduced = False
    widened = False

    # (2)+(6) the widen guard. A cap STRICTLY above current_cap requires >= 2
    # completed samples with evidence ids AND non-stale data.
    if candidate > current_cap:
        if stale:
            recommended = min(current_cap, candidate)
            reasons.append(
                f"widen blocked: data is stale — held at current_cap "
                f"({current_cap})"
            )
        elif sample_count < MIN_SAMPLES_TO_WIDEN:
            recommended = min(current_cap, candidate)
            reasons.append(
                f"widen blocked: only {sample_count} completed sample(s) with "
                f"evidence ids (need >= {MIN_SAMPLES_TO_WIDEN}) — held at "
                f"current_cap ({current_cap})"
            )
        else:
            widened = True
            reasons.append(
                f"widen allowed: {sample_count} completed samples (>= "
                f"{MIN_SAMPLES_TO_WIDEN}), data not stale — recommended_cap="
                f"{recommended}"
            )
    elif candidate < current_cap:
        reasons.append(
            f"recommended_cap={candidate} is below current_cap ({current_cap}) "
            f"— a narrowing is always allowed"
        )
    else:
        reasons.append(f"recommended_cap={candidate} == current_cap")

    # (3) the rate-regression reduction. doc 09 step 3: "Reduce to current cap
    # or 4 when workflow repair, product repair, commit failure, or merge
    # conflict rates exceed the global completed baseline by more than 10
    # percent." We compare the window's per-task rate to the global baseline.
    regressions = baseline.rate_regressions(samples)
    if regressions:
        floor_to = min(current_cap, MIN_CAP)
        if floor_to < recommended:
            recommended = floor_to
            reduced = True
        else:
            # Even when the floor is not below the current recommendation, the
            # regression still BLOCKS any widen — record it.
            recommended = min(recommended, current_cap)
        widened = widened and not reduced and recommended > current_cap
        for label in regressions:
            reasons.append(
                f"rate regression: {label} exceeds the global completed "
                f"baseline by > {int(RATE_REGRESSION_THRESHOLD * 100)}% — "
                f"reduced to {recommended} (min(current_cap={current_cap}, "
                f"{MIN_CAP}))"
            )

    # The result is never below the conservative floor.
    recommended = max(MIN_CAP, recommended)

    return CapDecision(
        recommended_cap=recommended,
        current_cap=current_cap,
        policy_cap=policy_cap,
        evidence_cap=evidence_cap,
        sample_count=sample_count,
        reduced=reduced,
        widened=widened and recommended > current_cap,
        reasons=reasons,
    )


# ── the global post-change baseline (doc 09 step 4) ──────────────────────────


class _GlobalBaseline:
    """The global completed-group baseline rates (doc 09 § "Adaptive Sizing"
    step 4: "Keep global post-change baseline metrics for comparison").

    Built from ALL completed metrics in the window (not just the lane/barrier
    bucket), so a single lane is compared to the feature-wide norm. Each
    baseline rate is the mean per-task rate across completed groups. The
    rate-regression check (doc 09 step 3) flags a lane whose rate exceeds the
    matching baseline by more than 10%.
    """

    __slots__ = (
        "workflow_repair_per_task",
        "product_repair_per_task",
        "commit_failures_per_task",
        "merge_conflicts_per_task",
        "completed_sample_count",
    )

    def __init__(
        self,
        *,
        workflow_repair_per_task: float | None,
        product_repair_per_task: float | None,
        commit_failures_per_task: float | None,
        merge_conflicts_per_task: float | None,
        completed_sample_count: int,
    ) -> None:
        self.workflow_repair_per_task = workflow_repair_per_task
        self.product_repair_per_task = product_repair_per_task
        self.commit_failures_per_task = commit_failures_per_task
        self.merge_conflicts_per_task = merge_conflicts_per_task
        self.completed_sample_count = completed_sample_count

    @classmethod
    def from_completed(
        cls, completed_metrics: list[SchedulerGroupMetric]
    ) -> "_GlobalBaseline":
        """Build the baseline from every completed metric.

        A completed group always has a non-zero ``task_count``; the per-task
        rates are taken straight off the typed metric (09d-1 computed them via
        ``_ratio``). A group whose ratio is ``None`` is skipped for that rate.
        """

        done = [m for m in completed_metrics if m.completed]
        return cls(
            workflow_repair_per_task=_mean_opt(
                [m.workflow_repair_cycles_per_task for m in done]
            ),
            product_repair_per_task=_mean_opt(
                [m.product_repair_cycles_per_task for m in done]
            ),
            commit_failures_per_task=_mean_opt(
                [m.commit_failures_per_task for m in done]
            ),
            merge_conflicts_per_task=_mean_opt(
                [m.merge_conflicts_per_task for m in done]
            ),
            completed_sample_count=len(done),
        )

    def rate_regressions(
        self, window_samples: list[SchedulerGroupMetric]
    ) -> list[str]:
        """Names of every per-task rate that regresses vs. the baseline.

        doc 09 step 3: a rate "exceeds the global completed baseline by more
        than 10 percent" → a regression. The window's rate is the mean per-task
        rate across ``window_samples`` (the lane/barrier bucket's completed
        groups). An empty window or a missing baseline rate yields no
        regression for that metric (there is nothing to compare).
        """

        if not window_samples:
            return []
        out: list[str] = []
        checks: tuple[tuple[str, float | None, float | None], ...] = (
            (
                "workflow repair cycles/task",
                _mean_opt(
                    [m.workflow_repair_cycles_per_task for m in window_samples]
                ),
                self.workflow_repair_per_task,
            ),
            (
                "product repair cycles/task",
                _mean_opt(
                    [m.product_repair_cycles_per_task for m in window_samples]
                ),
                self.product_repair_per_task,
            ),
            (
                "commit failures/task",
                _mean_opt([m.commit_failures_per_task for m in window_samples]),
                self.commit_failures_per_task,
            ),
            (
                "merge conflicts/task",
                _mean_opt([m.merge_conflicts_per_task for m in window_samples]),
                self.merge_conflicts_per_task,
            ),
        )
        for label, window_rate, baseline_rate in checks:
            if window_rate is None or baseline_rate is None:
                continue
            # "exceeds the global completed baseline by more than 10 percent".
            if window_rate > baseline_rate * (1.0 + RATE_REGRESSION_THRESHOLD):
                out.append(label)
        return out


def _mean_opt(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 4)


# ── (B-pre) candidate-wave construction — doc 09 § "Adaptive Sizing" step 7 ──


class CandidateWave:
    """One recommended wave produced by :func:`build_candidate_waves`.

    Plain attribute carrier. ``task_ids`` is sorted; ``shrunk_from_cap`` is True
    when a post-cap validator (hard barrier / write-set overlap / unknown
    write / missing contract) shrank this wave below the cap.
    """

    __slots__ = ("group_idx", "task_ids", "shrunk_from_cap", "shrink_reasons")

    def __init__(
        self,
        *,
        group_idx: int,
        task_ids: list[str],
        shrunk_from_cap: bool,
        shrink_reasons: list[str],
    ) -> None:
        self.group_idx = group_idx
        self.task_ids = task_ids
        self.shrunk_from_cap = shrunk_from_cap
        self.shrink_reasons = shrink_reasons


def _barrier_by_task(overlay: RegroupOverlay | None) -> dict[str, str]:
    """Per-task HARD-barrier id from the overlay (doc 09 step 7).

    Only ``hard`` barriers gate same-wave membership (doc 09 § "Validation
    Algorithm" step 9: "Reject a derived group that mixes hard barriers");
    soft barriers do not. A task in no hard barrier maps to ``""``.
    """

    out: dict[str, str] = {}
    if overlay is None:
        return out
    for barrier in overlay.barriers:
        if not barrier.hard:
            continue
        for task_id in barrier.task_ids:
            out[str(task_id)] = str(barrier.barrier_id)
    return out


def _unknown_write_tasks(overlay: RegroupOverlay | None) -> set[str]:
    """Tasks the overlay flags ``unknown_write`` (doc 09 step 7).

    doc 09 step 7: "Unknown writes ... shrink the candidate wave before
    validation instead of being deferred to runtime." A task with an unknown
    write set may only ever be scheduled alone.
    """

    if overlay is None:
        return set()
    return {
        str(task_id)
        for task_id, meta in overlay.speed_index.items()
        if meta.unknown_write
    }


def build_candidate_waves(
    *,
    base_dag: ImplementationDAG,
    overlay: RegroupOverlay | None,
    cap: int,
    resume_from_group: int,
    write_sets: dict[str, list[str]],
) -> list[CandidateWave]:
    """Recommended waves by topological order — doc 09 § "Adaptive Sizing" step 7.

    Transcribed from doc 09 step 7: "Produce recommended waves by topological
    order. The scheduler may choose eligible tasks up to the cap only when hard
    barriers match, write sets do not overlap, merged original groups have full
    write-set coverage, and dependencies are already scheduled. Unknown writes,
    missing contracts, or hard-barrier ambiguity shrink the candidate wave
    before validation instead of being deferred to runtime."

    This is the POST-CAP validator set (doc 09 § "Scheduler Metrics" step 5:
    the dependency / hard-barrier / write-set / mapping validators run "after
    cap selection" and "may shrink or reject a candidate wave; metrics may not
    override them"). The ``cap`` is the upper bound ONLY — a wave is shrunk
    below it whenever a validator says so.

    SAFETY: this is a pure in-memory recommendation. It produces candidate
    waves; it does NOT mutate the DAG, write an overlay, or write a marker. The
    recommendation must still go through ``validate_overlay`` (the full 13-step
    validator) before any activation.

    Parameters
    ----------
    base_dag
        The root DAG. Its task definitions supply the dependency graph.
    overlay
        The active overlay (or ``None``). Supplies the effective execution
        order suffix, the hard barriers, and the ``unknown_write`` flags.
    cap
        The conservative cap (the upper wave size bound).
    resume_from_group
        The first group index to (re)plan from — typically the high-water
        checkpoint + 1.
    write_sets
        Per-task authoritative write-set map (Slice 3 contract scopes, or the
        overlay's own ``write_sets``). A task with NO entry is treated as a
        *missing contract* and may only be scheduled alone (doc 09 step 7).

    Returns
    -------
    list[CandidateWave]
        The recommended waves, each ``<= cap`` tasks. ``[]`` when there is no
        remaining suffix to plan.
    """

    if cap < MIN_CAP:
        raise SchedulerSizingError(
            f"cap={cap} is below the conservative minimum {MIN_CAP}"
        )

    effective_order = effective_execution_order_for_overlay(base_dag, overlay)
    if resume_from_group < 0:
        raise SchedulerSizingError(
            f"resume_from_group={resume_from_group} must be >= 0"
        )
    remaining_groups = effective_order[resume_from_group:]
    remaining_ids = {tid for wave in remaining_groups for tid in wave}
    if not remaining_ids:
        return []

    task_by_id = {task.id: task for task in base_dag.tasks}
    # Dependencies restricted to the remaining suffix (a dep to an
    # already-checkpointed task is satisfied evidence — doc 09 § "Validation
    # Algorithm" step 6).
    deps: dict[str, set[str]] = {}
    for task_id in remaining_ids:
        task = task_by_id.get(task_id)
        task_deps = set(task.dependencies) if task is not None else set()
        deps[task_id] = {d for d in task_deps if d in remaining_ids}

    barrier_by_task = _barrier_by_task(overlay)
    unknown_writes = _unknown_write_tasks(overlay)

    # The original group of each remaining task (doc 09 step 7: "merged
    # original groups have full write-set coverage"). Tasks from different
    # original groups may only share a wave when every one has a write set.
    original_group_by_task: dict[str, int] = {}
    for offset, wave in enumerate(remaining_groups):
        for task_id in wave:
            original_group_by_task[task_id] = resume_from_group + offset

    waves: list[CandidateWave] = []
    scheduled: set[str] = set()
    unscheduled = set(remaining_ids)
    group_idx = resume_from_group

    # A deterministic topological order: by (original group, task id).
    def _sort_key(task_id: str) -> tuple[int, str]:
        return (original_group_by_task.get(task_id, 0), task_id)

    # Guard against an unsatisfiable dependency graph (a cycle / a dep to a
    # task outside the suffix that is not checkpointed) — fail-fast, never an
    # infinite loop.
    max_iterations = len(remaining_ids) + 1
    iterations = 0

    while unscheduled:
        iterations += 1
        if iterations > max_iterations:
            raise SchedulerSizingError(
                "candidate-wave construction did not converge — the remaining "
                "dependency graph has a cycle or an unreachable dependency "
                f"(unscheduled={sorted(unscheduled)})"
            )
        eligible = sorted(
            (
                task_id
                for task_id in unscheduled
                if deps.get(task_id, set()).issubset(scheduled)
            ),
            key=_sort_key,
        )
        if not eligible:
            raise SchedulerSizingError(
                "candidate-wave construction stalled — no eligible task "
                f"(unscheduled={sorted(unscheduled)})"
            )

        seed = eligible[0]
        wave = [seed]
        shrink_reasons: list[str] = []
        # The seed itself can be a wave-of-one when it is an unknown-write or a
        # missing-contract task.
        seed_solo = seed in unknown_writes or seed not in write_sets

        if not seed_solo:
            for task_id in eligible[1:]:
                if len(wave) >= cap:
                    break
                can_add, reason = _can_join_wave(
                    task_id,
                    wave,
                    barrier_by_task=barrier_by_task,
                    unknown_writes=unknown_writes,
                    write_sets=write_sets,
                    original_group_by_task=original_group_by_task,
                )
                if can_add:
                    wave.append(task_id)
                elif reason:
                    shrink_reasons.append(reason)
        else:
            shrink_reasons.append(
                f"task {seed!r} "
                + (
                    "has an unknown write set"
                    if seed in unknown_writes
                    else "has no authoritative write-set contract"
                )
                + " — scheduled alone (doc 09 step 7)"
            )

        wave_sorted = sorted(wave)
        waves.append(
            CandidateWave(
                group_idx=group_idx,
                task_ids=wave_sorted,
                shrunk_from_cap=len(wave_sorted) < cap and bool(shrink_reasons),
                shrink_reasons=sorted(set(shrink_reasons)),
            )
        )
        scheduled.update(wave)
        unscheduled.difference_update(wave)
        group_idx += 1

    return waves


def _can_join_wave(
    task_id: str,
    wave: list[str],
    *,
    barrier_by_task: dict[str, str],
    unknown_writes: set[str],
    write_sets: dict[str, list[str]],
    original_group_by_task: dict[str, int],
) -> tuple[bool, str]:
    """Whether ``task_id`` may join ``wave`` — the post-cap validators (step 7).

    Returns ``(can_join, shrink_reason)``. ``shrink_reason`` is non-empty only
    when the task was REJECTED for a reason worth recording.

    The doc-09 step-7 validators, in order:

    - **unknown write / missing contract** — an ``unknown_write`` task or a
      task with no write-set entry may only be scheduled alone.
    - **hard-barrier mix** — a task in a hard barrier may not join a wave that
      already holds a task in a *different* hard barrier (doc 09 step 9).
    - **write-set overlap** — the task's write set may not overlap any path
      already claimed by the wave after path canonicalization (doc 09 step
      10).
    - **merged-group coverage** — a task from a different original group than
      the wave's tasks may only join when *every* wave task has a write set
      (doc 09 step 7: "merged original groups have full write-set coverage").
    """

    # unknown write / missing contract — the joining task must be solo.
    if task_id in unknown_writes:
        return False, f"task {task_id!r} has an unknown write set — cannot widen"
    if task_id not in write_sets:
        return (
            False,
            f"task {task_id!r} has no authoritative write-set contract — "
            f"cannot widen",
        )

    # hard-barrier mix.
    task_barrier = barrier_by_task.get(task_id, "")
    wave_barriers = {
        barrier_by_task.get(member, "") for member in wave
    } - {""}
    if task_barrier:
        other = wave_barriers - {task_barrier}
        if other:
            return (
                False,
                f"task {task_id!r} barrier {task_barrier!r} mixes with wave "
                f"barrier(s) {sorted(other)} — hard-barrier mix rejected",
            )
    elif wave_barriers:
        # The joining task is in NO hard barrier but the wave already has a
        # hard-barriered task — mixing a barriered and a non-barriered task is
        # a hard-barrier ambiguity; keep them apart conservatively.
        return (
            False,
            f"task {task_id!r} is in no hard barrier but the wave holds "
            f"barrier(s) {sorted(wave_barriers)} — hard-barrier ambiguity",
        )

    # write-set overlap (after path canonicalization).
    task_paths = {_canon_path(p) for p in write_sets.get(task_id, [])}
    wave_paths: set[str] = set()
    wave_has_uncontracted = False
    for member in wave:
        member_ws = write_sets.get(member)
        if member_ws is None:
            wave_has_uncontracted = True
            continue
        for path in member_ws:
            wave_paths.add(_canon_path(path))
    overlap = task_paths & wave_paths
    if overlap:
        return (
            False,
            f"task {task_id!r} write set overlaps the wave on "
            f"{sorted(overlap)} — same-wave write-set conflict",
        )

    # merged-group coverage. When the joining task is from a different original
    # group than ANY wave task, every wave task must have a write set.
    task_group = original_group_by_task.get(task_id)
    wave_groups = {original_group_by_task.get(m) for m in wave}
    if task_group is not None and wave_groups - {task_group, None}:
        if wave_has_uncontracted:
            return (
                False,
                f"task {task_id!r} would merge original groups but a wave task "
                f"lacks a write-set contract — incomplete merged-group coverage",
            )

    return True, ""


def _canon_path(path: Any) -> str:
    """Canonicalize a write-set path for overlap comparison.

    Strips a leading ``./``, collapses backslashes to forward slashes, lowers
    case-insensitive duplication risk, and trims trailing slashes. Deterministic
    (no filesystem access) — this is a comparison key, not a resolved path.
    """

    text = str(path).strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/")


# ── (B) the typed SchedulerFeedback ──────────────────────────────────────────


def _derive_feedback_id(
    *,
    feature_id: str,
    window_start_group: int,
    window_end_group: int,
    lane: str,
    barrier: str,
    overlay_id: str | None,
    metric_ids: list[str],
) -> str:
    """Deterministic 24-hex ``feedback_id``.

    doc 09 gives no explicit ``feedback_id`` formula (unlike ``overlay_id`` /
    ``metric_id``). We mint one in the SAME deterministic style as the 09a
    derivations: ``sha256`` over the sorted-keys identity tuple. ``generated_at``
    is deliberately EXCLUDED — the id is a pure function of the window + lane +
    barrier + the contributing metric ids, so re-running adaptive sizing over
    the identical metric set yields the identical ``feedback_id`` (and the 09b
    store's idempotency key collides → an idempotent no-op re-insert). The
    ``metric_ids`` are sorted before hashing so row-fetch order cannot perturb
    the id.
    """

    return stable_digest(
        {
            "feature_id": feature_id,
            "window_start_group": window_start_group,
            "window_end_group": window_end_group,
            "lane": lane,
            "barrier": barrier,
            "overlay_id": overlay_id or "root",
            "metric_ids": sorted(metric_ids),
        }
    )[:24]


def _select_lane_and_barrier(
    completed_metrics: list[SchedulerGroupMetric],
) -> tuple[str, str, str]:
    """The dominant lane + barrier of the completed-group window.

    doc 09 § "Adaptive Sizing Data Flow" step 4: "Aggregate completed groups by
    ``barrier:{barrier}`` when at least two completed samples exist; otherwise
    aggregate by ``lane:{lane}``." We pick the modal barrier / lane across the
    completed metrics (deterministic — ties broken by sorted name). Returns
    ``(lane, barrier, aggregation_basis)`` where ``aggregation_basis`` is
    ``"barrier:{barrier}"`` when >= 2 completed samples share the modal barrier,
    else ``"lane:{lane}"``, else ``"policy"`` when there is nothing to
    aggregate.
    """

    done = [m for m in completed_metrics if m.completed]
    lane_tally: dict[str, int] = {}
    barrier_tally: dict[str, int] = {}
    for metric in done:
        for lane, count in metric.lane_counts.items():
            lane_tally[lane] = lane_tally.get(lane, 0) + int(count)
        for barrier, count in metric.barrier_counts.items():
            barrier_tally[barrier] = barrier_tally.get(barrier, 0) + int(count)

    def _modal(tally: dict[str, int]) -> str:
        if not tally:
            return "unknown"
        # Highest count wins; ties broken by sorted name (deterministic).
        return sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    lane = _modal(lane_tally)
    barrier = _modal(barrier_tally)

    # The aggregation basis: barrier when >= 2 completed samples, else lane,
    # else policy.
    if len(done) >= MIN_SAMPLES_TO_WIDEN and barrier_tally:
        basis = f"barrier:{barrier}"
    elif done and lane_tally:
        basis = f"lane:{lane}"
    else:
        basis = "policy"
    return lane, barrier, basis


def _resolve_data_quality(
    contributing_metrics: list[SchedulerGroupMetric],
) -> tuple[str, list[str]]:
    """The ``SchedulerFeedback.data_quality`` from the contributing metrics.

    doc 09 § "Scheduler Feedback Schema": missing evidence links "set
    ``data_quality_flags`` and force ``SchedulerFeedback.data_quality`` away
    from ``sufficient``." doc 09 § "Adaptive Sizing Data Flow" step 3:
    "Feedback without all required evidence categories is
    ``data_quality="insufficient"`` or ``mixed``; stale projection lineage sets
    ``data_quality="stale"``."

    The rule (deterministic, conservative):

    - No contributing metric → ``insufficient`` (there is no evidence at all).
    - ANY metric carries ``missing_projection_lineage`` → ``stale`` (doc 09
      step 3: "stale projection lineage sets ``data_quality="stale"``"). Stale
      is the strongest signal — it wins over insufficient/mixed.
    - Otherwise, ANY metric carries any ``data_quality_flags`` entry:
      - EVERY contributing metric is flagged → ``insufficient`` (the whole
        window is unusable).
      - some but not all are flagged → ``mixed``.
    - No metric carries any flag → ``sufficient``.

    Returns ``(data_quality, all_flags_sorted)``.
    """

    if not contributing_metrics:
        return "insufficient", []

    all_flags: set[str] = set()
    flagged_count = 0
    stale = False
    for metric in contributing_metrics:
        flags = list(metric.data_quality_flags)
        if flags:
            flagged_count += 1
            all_flags.update(flags)
        if "missing_projection_lineage" in flags:
            stale = True

    flags_sorted = sorted(all_flags)
    if stale:
        return "stale", flags_sorted
    if flagged_count == 0:
        return "sufficient", flags_sorted
    if flagged_count == len(contributing_metrics):
        return "insufficient", flags_sorted
    return "mixed", flags_sorted


def _confidence_for(
    *, data_quality: str, sample_count: int, cap_decision: CapDecision
) -> str:
    """The ``SchedulerFeedback.confidence`` band (low | medium | high).

    doc 09 carries ``confidence`` on the model but gives no formula. The
    conservative derivation: confidence tracks how much trustworthy completed
    evidence backs the recommendation.

    - ``low`` — ``data_quality`` is not ``sufficient`` (evidence is
      incomplete / stale / mixed), OR fewer than ``MIN_SAMPLES_TO_WIDEN``
      completed samples back it.
    - ``high`` — ``data_quality`` is ``sufficient`` AND there are at least
      ``2 * MIN_SAMPLES_TO_WIDEN`` completed samples.
    - ``medium`` — everything else (sufficient data, >= 2 but < 4 samples).
    """

    if data_quality != "sufficient" or sample_count < MIN_SAMPLES_TO_WIDEN:
        return "low"
    if sample_count >= 2 * MIN_SAMPLES_TO_WIDEN:
        return "high"
    return "medium"


def compute_scheduler_feedback(
    *,
    feature_id: str,
    metrics: list[SchedulerGroupMetric],
    base_dag: ImplementationDAG,
    overlay: RegroupOverlay | None,
    current_cap: int,
    generated_at: datetime,
    write_sets: dict[str, list[str]] | None = None,
) -> "AdaptiveSizingResult":
    """Compute the typed :class:`SchedulerFeedback` from 09d-1's metric list.

    This is the PURE computation (no DB, no clock, no write). It composes the
    conservative cap computation (A) and assembles the typed
    :class:`SchedulerFeedback` (B). doc 09 §§ "Scheduler Metrics And Cap
    Rules" / "Scheduler Feedback Schema" / "Adaptive Sizing Data Flow" steps
    4-8.

    SAFETY: this function writes NOTHING. It returns an
    :class:`AdaptiveSizingResult` carrying the typed ``SchedulerFeedback`` + the
    candidate waves + the cap decision. :func:`persist_scheduler_feedback`
    persists the row and :func:`project_sizing_review` writes the review
    artifact; NEITHER writes an active marker — see this module's docstring.

    Parameters
    ----------
    feature_id
        The feature whose metrics are summarized.
    metrics
        09d-1's :func:`build_scheduler_group_metrics` output — one
        :class:`SchedulerGroupMetric` per group. Active / incomplete groups
        (``completed=False``) appear for the window bounds + ``data_quality``
        but are EXCLUDED from the completed-throughput / p75 averages (doc 09
        § "Adaptive Sizing Data Flow" step 2 — "Active and incomplete groups
        are included for status but excluded from completed-throughput
        averages").
    base_dag
        The root DAG (for the candidate-wave dependency graph).
    overlay
        The active overlay, or ``None``.
    current_cap
        The cap currently in force (the baseline the recommendation widens
        from / narrows to). doc 09 step 6: an unjustified widen "keep[s] the
        current cap."
    generated_at
        The ``SchedulerFeedback.generated_at`` timestamp. Caller-supplied (a
        test pins it); it is NOT part of ``feedback_id`` so a re-run over the
        same metrics is idempotent.
    write_sets
        Optional per-task authoritative write-set map (Slice 3). When ``None``
        and an overlay is present, ``overlay.write_sets`` is used.

    Raises
    ------
    SchedulerSizingError
        When ``feature_id`` is empty or ``current_cap`` is below the
        conservative minimum.
    """

    if not feature_id:
        raise SchedulerSizingError("compute_scheduler_feedback requires a feature_id")
    if current_cap < MIN_CAP:
        raise SchedulerSizingError(
            f"current_cap={current_cap} is below the conservative minimum "
            f"{MIN_CAP}"
        )

    effective_write_sets: dict[str, list[str]] = dict(
        write_sets
        if write_sets is not None
        else (overlay.write_sets if overlay is not None else {})
    )

    # The window bounds: every group in the metric set (status groups
    # included) — doc 09 § "Scheduler Feedback Schema": window_start/end_group.
    if metrics:
        window_start = min(m.group_idx for m in metrics)
        window_end = max(m.group_idx for m in metrics)
    else:
        # No metrics at all (a freshly-staged overlay with zero evidence). The
        # window is the overlay offset (or 0). A zero-metric window is
        # data_quality=insufficient and falls to the conservative policy cap.
        window_start = overlay.group_idx_offset if overlay is not None else 0
        window_end = window_start

    completed = [m for m in metrics if m.completed]

    # doc 09 § "Adaptive Sizing Data Flow" step 4: aggregate completed groups
    # by barrier (>= 2 samples) else lane.
    lane, barrier, aggregation_basis = _select_lane_and_barrier(completed)

    # The completed-throughput aggregates (completed groups ONLY — doc 09 step
    # 2). hours_per_task p50/p75 feed the evidence cap.
    hours_values = [
        m.hours_per_task for m in completed if m.hours_per_task is not None
    ]
    tph_values = [
        m.tasks_per_hour for m in completed if m.tasks_per_hour is not None
    ]
    hours_per_task_p50 = _percentile_nearest_rank(hours_values, 0.50)
    hours_per_task_p75 = _percentile_nearest_rank(hours_values, 0.75)
    tasks_per_hour = _mean_opt([float(v) for v in tph_values]) if tph_values else None
    queue_wait_values = [
        m.merge_queue_wait_h
        for m in completed
        if m.merge_queue_wait_h is not None
    ]
    queue_wait_p75_h = _percentile_nearest_rank(queue_wait_values, 0.75)

    product_repair_rate = _mean_opt(
        [m.product_repair_cycles_per_task for m in completed]
    )
    workflow_repair_rate = _mean_opt(
        [m.workflow_repair_cycles_per_task for m in completed]
    )
    commit_failures_rate = _mean_opt(
        [m.commit_failures_per_task for m in completed]
    )
    merge_conflicts_rate = _mean_opt(
        [m.merge_conflicts_per_task for m in completed]
    )
    verify_cost_rate = _mean_opt([m.verify_cost_per_task for m in completed])

    # data_quality — from the CONTRIBUTING metrics. doc 09 step 2: a metric is
    # usable for sizing only with all 4 evidence categories. The contributing
    # set is the completed metrics (the sizing inputs) when any exist, else the
    # whole status window (so a zero-completed window still reports a quality).
    contributing = completed if completed else metrics
    data_quality, data_quality_flags = _resolve_data_quality(contributing)

    # (A) the conservative cap computation.
    # The policy-cap risk inputs come from the completed window's typed
    # metrics + the overlay speed index.
    high_risk_barrier = _markers_hit(barrier, _HIGH_RISK_BARRIER_MARKERS)
    unknown_write = any(m.unknown_write_count > 0 for m in contributing)
    multi_repo = any(m.repo_count > 1 for m in contributing)
    policy_cap, policy_reason = compute_policy_cap(
        unknown_write=unknown_write,
        high_risk_barrier=high_risk_barrier,
        barrier=barrier,
        lane=lane,
        multi_repo=multi_repo,
    )

    baseline = _GlobalBaseline.from_completed(completed)

    # doc 09 § "Adaptive Sizing Data Flow" step 6: "Never widen from a window
    # whose completed groups omit typed merge/checkpoint proof or whose active
    # group is still running." A completed metric, BY 09d-1's construction,
    # always has the full merge/checkpoint proof (09d-1 only sets
    # `completed=True` for a fully-proven checkpoint). The remaining doc-09
    # staleness trigger is the projection-lineage signal: when data_quality is
    # `stale` the window is treated as stale for the cap computation.
    stale = data_quality == "stale"

    cap_decision = compute_cap_decision(
        completed_metrics=completed,
        current_cap=current_cap,
        policy_cap=policy_cap,
        hours_per_task_p75=hours_per_task_p75,
        baseline=baseline,
        stale=stale,
    )

    # (B-pre) the candidate waves (doc 09 step 7) — the post-cap validators.
    resume_from_group = window_end + 1 if completed else window_start
    try:
        candidate_waves = build_candidate_waves(
            base_dag=base_dag,
            overlay=overlay,
            cap=cap_decision.recommended_cap,
            resume_from_group=resume_from_group,
            write_sets=effective_write_sets,
        )
    except SchedulerSizingError:
        # An unsatisfiable remaining graph (cycle / unreachable dependency) is
        # a hard data inconsistency. The recommendation itself is still
        # well-formed (the cap decision holds); we surface NO candidate waves
        # and record the reason rather than crash the whole feedback emission.
        candidate_waves = []
        cap_decision.reasons.append(
            "candidate-wave construction skipped — the remaining dependency "
            "graph is unsatisfiable; the recommendation carries the cap only"
        )

    # Reasons — the cap reasons + the aggregation basis + the policy reason +
    # the data-quality reasons. doc 09 § "Scheduler Feedback Schema": reasons.
    reasons: list[str] = []
    reasons.append(f"aggregation basis: {aggregation_basis}")
    reasons.append(policy_reason)
    reasons.extend(cap_decision.reasons)
    if data_quality != "sufficient":
        reasons.append(
            f"data_quality={data_quality} — "
            + (
                "stale projection lineage on a contributing metric"
                if data_quality == "stale"
                else f"contributing metrics carry data_quality_flags "
                f"{data_quality_flags}"
            )
            + "; the recommendation is advisory and a new overlay must still "
            "pass the full 13-step validate_overlay before activation"
        )
    if not contributing or not completed:
        reasons.append(
            "no completed groups in the window — fell back to the "
            "conservative policy cap; widening requires >= "
            f"{MIN_SAMPLES_TO_WIDEN} completed samples with evidence ids"
        )
    for wave in candidate_waves:
        if wave.shrunk_from_cap:
            for shrink_reason in wave.shrink_reasons:
                reasons.append(
                    f"candidate wave g{wave.group_idx}: {shrink_reason}"
                )

    # The completed group indexes + the metric ids + the evidence ids.
    completed_groups = sorted(m.group_idx for m in completed)
    contributing_metric_ids = sorted(m.metric_id for m in contributing)
    evidence_ids = sorted(
        {eid for m in contributing for eid in m.evidence_ids}
    )

    overlay_id = overlay.overlay_id if overlay is not None else None
    feedback_id = _derive_feedback_id(
        feature_id=feature_id,
        window_start_group=window_start,
        window_end_group=window_end,
        lane=lane,
        barrier=barrier,
        overlay_id=overlay_id,
        metric_ids=contributing_metric_ids,
    )

    confidence = _confidence_for(
        data_quality=data_quality,
        sample_count=cap_decision.sample_count,
        cap_decision=cap_decision,
    )

    feedback = SchedulerFeedback(
        feedback_id=feedback_id,
        feature_id=feature_id,
        generated_at=generated_at,
        window_start_group=window_start,
        window_end_group=window_end,
        overlay_id=overlay_id,
        lane=lane,
        barrier=barrier,
        completed_groups=completed_groups,
        sample_count=cap_decision.sample_count,
        tasks_per_hour=tasks_per_hour,
        hours_per_task_p50=hours_per_task_p50,
        hours_per_task_p75=hours_per_task_p75,
        product_repair_cycles_per_task=product_repair_rate,
        workflow_repair_cycles_per_task=workflow_repair_rate,
        commit_failures_per_task=commit_failures_rate,
        merge_conflicts_per_task=merge_conflicts_rate,
        verify_cost_per_task=verify_cost_rate,
        queue_wait_p75_h=queue_wait_p75_h,
        data_quality=data_quality,  # type: ignore[arg-type]
        recommended_cap=cap_decision.recommended_cap,
        current_cap=current_cap,
        confidence=confidence,  # type: ignore[arg-type]
        reasons=reasons,
        metric_ids=contributing_metric_ids,
        evidence_ids=evidence_ids,
    )

    return AdaptiveSizingResult(
        feedback=feedback,
        cap_decision=cap_decision,
        candidate_waves=candidate_waves,
        aggregation_basis=aggregation_basis,
    )


class AdaptiveSizingResult:
    """The full output of :func:`compute_scheduler_feedback`.

    Plain attribute carrier. ``feedback`` is the typed
    :class:`SchedulerFeedback` (the persistable row); ``cap_decision`` and
    ``candidate_waves`` are the intermediate scheduler state a caller / a
    review surface can inspect. NONE of these is an executable plan — a
    candidate wave must be converted into a :class:`RegroupOverlay` and pass
    ``validate_overlay`` before it can affect dispatch.
    """

    __slots__ = ("feedback", "cap_decision", "candidate_waves", "aggregation_basis")

    def __init__(
        self,
        *,
        feedback: SchedulerFeedback,
        cap_decision: CapDecision,
        candidate_waves: list[CandidateWave],
        aggregation_basis: str,
    ) -> None:
        self.feedback = feedback
        self.cap_decision = cap_decision
        self.candidate_waves = candidate_waves
        self.aggregation_basis = aggregation_basis


# ── (B) persistence — the ONLY two writes 09d-2 ever performs ────────────────


def _sizing_review_key(feature_id: str, feedback: SchedulerFeedback) -> str:
    """The ``review:dag-sizing:{feature_id}:{window}`` projection key.

    doc 09 § "Regroup Projection Model" / § "Persistence And Artifact
    Compatibility": ``project_sizing_review(feedback)`` writes
    ``review:dag-sizing:{feature_id}:{window}``. The ``{window}`` segment is
    the ``g{start}-g{end}`` window range so a feature carries one review
    artifact per sizing window. doc 09 § "Tests": "Review projection key is
    exactly ``review:dag-sizing:{feature_id}:{window}``."
    """

    window = f"g{feedback.window_start_group}-g{feedback.window_end_group}"
    return f"review:dag-sizing:{feature_id}:{window}"


async def persist_scheduler_feedback(
    store: RegroupOverlayStore,
    feedback: SchedulerFeedback,
) -> SchedulerFeedbackRecord:
    """Persist the typed :class:`SchedulerFeedback` — doc 09 § "Adaptive..." step 8.

    Writes exactly ONE ``execution_scheduler_feedback`` row through the 09b
    :meth:`RegroupOverlayStore.insert_scheduler_feedback` (which is idempotent
    on ``scheduler_feedback_idempotency_key`` — a re-emission over the same
    metric set is an idempotent no-op).

    SAFETY: this writes ONLY the ``execution_scheduler_feedback`` row. It writes
    NO ``execution_regroup_overlays`` row, NO ``dag-regroup-active:*`` marker,
    NO ``dag-regroup:*`` canonical artifact, NO ``events`` row. doc 09
    § "Adaptive Sizing Data Flow" step 8: "No active overlay marker is written
    by this flow." doc 09 § "Scheduler Metrics And Cap Rules" / § "Validation
    Algorithm": the recommendation must be converted into a new overlay and
    pass the full 13-step ``validate_overlay`` before any activation.
    """

    return await store.insert_scheduler_feedback(feedback)


async def project_sizing_review(
    conn: asyncpg.Connection,
    feedback: SchedulerFeedback,
    *,
    candidate_waves: list[CandidateWave] | None = None,
    aggregation_basis: str = "",
) -> int:
    """Project the ``review:dag-sizing:{feature_id}:{window}`` artifact — step 8.

    doc 09 § "Regroup Projection Model": ``project_sizing_review(feedback)``
    writes ``review:dag-sizing:{feature_id}:{window}`` from
    :class:`SchedulerFeedback`. "It is review evidence only and is never read
    as an active marker or executable plan."

    The artifact body is the typed ``SchedulerFeedback`` payload plus the
    candidate-wave recommendation (review evidence) — every field is bounded.
    The body carries an explicit ``"executable": false`` and
    ``"is_active_marker": false`` so a legacy reader cannot mistake it for an
    activatable plan.

    Idempotent on identical body — an existing ``review:dag-sizing:*``
    ``artifacts`` row with the same ``(feature_id, key, value)`` is reused
    (mirrors the 09b store's ``_write_validation_artifact`` convention; the
    body is a deterministic function of the feedback so a re-run reuses it).

    SAFETY: this writes ONE ``artifacts`` row keyed ``review:dag-sizing:*``.
    The ``review:dag-sizing:*`` key is NEVER consumed by
    :class:`~iriai_build_v2.workflows.develop.execution.regroup_overlay_resolver.RegroupOverlayResolver`
    — that resolver reads only ``dag-regroup-active:*`` / ``dag-regroup:*``.
    This function writes NO ``dag-regroup-active:*`` marker. doc 09 § "Tests":
    the review key "is never consumed by resolver activation."

    Returns the ``artifacts`` row id.
    """

    if not feedback.feature_id:
        raise SchedulerSizingError("project_sizing_review requires a feature_id")
    key = _sizing_review_key(feedback.feature_id, feedback)
    body: dict[str, Any] = {
        "kind": "dag_sizing_review",
        # An explicit, unambiguous non-executable / non-marker stamp so no
        # legacy reader treats this review artifact as an activatable plan.
        "executable": False,
        "is_active_marker": False,
        "schema_version": feedback.schema_version,
        "feedback_id": feedback.feedback_id,
        "aggregation_basis": aggregation_basis,
        "feedback": feedback.model_dump(mode="json"),
        "candidate_waves": [
            {
                "group_idx": wave.group_idx,
                "task_ids": list(wave.task_ids),
                "shrunk_from_cap": wave.shrunk_from_cap,
                "shrink_reasons": list(wave.shrink_reasons),
            }
            for wave in (candidate_waves or [])
        ],
        "note": (
            "review evidence only — a candidate wave must be converted into a "
            "typed RegroupOverlay and pass the full 13-step validate_overlay "
            "before any activation; this key is never consumed by "
            "RegroupOverlayResolver"
        ),
    }
    value = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)

    existing = await conn.fetchval(
        "SELECT id FROM artifacts "
        "WHERE feature_id = $1 AND key = $2 AND value = $3 "
        "ORDER BY id DESC LIMIT 1",
        feedback.feature_id,
        key,
        value,
    )
    if existing is not None:
        return int(existing)
    artifact_id = await conn.fetchval(
        "INSERT INTO artifacts (feature_id, key, value) "
        "VALUES ($1, $2, $3) RETURNING id",
        feedback.feature_id,
        key,
        value,
    )
    return int(artifact_id)


# ── public entry point ───────────────────────────────────────────────────────


async def run_adaptive_sizing(
    conn: asyncpg.Connection,
    *,
    feature_id: str,
    base_dag: ImplementationDAG,
    overlay: RegroupOverlay | None = None,
    current_cap: int,
    generated_at: datetime,
    write_sets: dict[str, list[str]] | None = None,
    persist: bool = True,
) -> AdaptiveSizingResult:
    """The end-to-end Slice 09d-2 adaptive-sizing flow — doc 09 § "Adaptive...".

    Composes 09d-1's :func:`build_scheduler_group_metrics` (the typed-evidence
    join, steps 1-3) with this module's cap computation + feedback emission
    (steps 4-8):

    1-3. Build the typed :class:`SchedulerGroupMetric` list (09d-1).
    4-7. Compute the conservative cap + the candidate waves
         (:func:`compute_scheduler_feedback`).
    8.   Persist the typed :class:`SchedulerFeedback`
         (:func:`persist_scheduler_feedback`) and project
         ``review:dag-sizing:{feature_id}:{window}``
         (:func:`project_sizing_review`).

    SAFETY — the LAST line of this docstring is load-bearing. ``run_adaptive_
    sizing`` writes EXACTLY two rows when ``persist=True``: one
    ``execution_scheduler_feedback`` row and one ``review:dag-sizing:*``
    ``artifacts`` row. It writes NOTHING else — no ``execution_regroup_
    overlays`` row, no ``dag-regroup-active:*`` marker, no ``dag-regroup:*`` /
    ``dag-regroup-rollback:*`` artifact, no ``events`` row. The recommendation
    in the returned :class:`AdaptiveSizingResult` cannot affect dispatch: it
    must be converted into a new :class:`RegroupOverlay` and pass the full
    13-step :func:`validate_overlay` + :func:`activate_overlay` first (doc 09
    § "Validation Algorithm": "Metrics can only produce a recommendation
    artifact. The recommendation must be converted into a new overlay and pass
    the same validation path before activation").

    Parameters
    ----------
    conn
        An asyncpg connection. 09d-1's metric build is a pure read path; this
        flow then performs the two writes above when ``persist=True``.
    feature_id
        The feature.
    base_dag
        The root DAG.
    overlay
        The active overlay, or ``None``.
    current_cap
        The cap currently in force.
    generated_at
        The ``SchedulerFeedback.generated_at`` — caller-supplied for
        determinism.
    write_sets
        Optional Slice 3 write-set map.
    persist
        When ``False`` the flow computes the result but performs NO write
        (useful for a dry-run / a preview surface).

    Raises
    ------
    SchedulerSizingError
        When ``feature_id`` is empty or ``current_cap`` is below the minimum.
    """

    if not feature_id:
        raise SchedulerSizingError("run_adaptive_sizing requires a feature_id")

    # Steps 1-3 — the typed metric join (09d-1, a pure read path).
    metrics = await build_scheduler_group_metrics(
        conn,
        feature_id=feature_id,
        base_dag=base_dag,
        overlay=overlay,
        write_sets=write_sets,
    )

    # Steps 4-7 — the cap computation + the candidate waves (pure).
    result = compute_scheduler_feedback(
        feature_id=feature_id,
        metrics=metrics,
        base_dag=base_dag,
        overlay=overlay,
        current_cap=current_cap,
        generated_at=generated_at,
        write_sets=write_sets,
    )

    # Step 8 — persist the typed feedback + project the review artifact. The
    # ONLY two writes; NEVER an active marker.
    if persist:
        store = RegroupOverlayStore(conn)
        await persist_scheduler_feedback(store, result.feedback)
        await project_sizing_review(
            conn,
            result.feedback,
            candidate_waves=result.candidate_waves,
            aggregation_basis=result.aggregation_basis,
        )

    return result
