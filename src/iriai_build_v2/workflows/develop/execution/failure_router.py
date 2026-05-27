from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any, Literal, Mapping, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

FailureSeverity: TypeAlias = Literal["info", "warning", "error", "fatal"]

FailureClass: TypeAlias = Literal[
    "product_defect",
    "contract_compile",
    "contract_violation",
    "stale_projection",
    "worktree_alias",
    "acl_workability",
    "sandbox_allocation",
    "sandbox_binding",
    "sandbox_isolation",
    "sandbox_capture",
    "sandbox_cleanup",
    "commit_hygiene",
    "merge_conflict",
    "runtime_provider",
    "runtime_timeout",
    "runtime_cancelled",
    "runtime_context",
    "runtime_structured_output",
    "dispatcher_internal",
    "verifier_provider",
    "verifier_context",
    "checkpoint_contradiction",
    "regroup_invalid",
    "evidence_corruption",
    "resource_exhausted",
    "operator_required",
    "unknown",
]

FailureType: TypeAlias = Literal[
    "semantic_verifier_rejected",
    "required_path_missing",
    "contract_invalid_path",
    "contract_scope_conflict",
    "contract_missing_dependency",
    "contract_same_wave_dependency",
    "outside_allowed_paths",
    "forbidden_path_touched",
    "read_only_path_touched",
    "contract_id_mismatch",
    "alias_points_to_noncanonical_root",
    "alias_only_canonical_missing",
    "alias_canonical_divergent",
    "unwritable_runtime_path",
    "sandbox_clone_failed",
    "sandbox_disk_quota",
    "sandbox_base_snapshot_unavailable",
    "runtime_workspace_binding_failed",
    "canonical_path_exposed_to_writer",
    "path_escape_detected",
    "patch_capture_failed",
    "sandbox_index_corrupt",
    "cleanup_failed",
    "commit_hook_failed",
    "dirty_after_commit",
    "stale_base_commit",
    "rebase_conflict",
    "patch_apply_conflict",
    "provider_internal_error",
    "provider_rate_limited",
    "provider_transport_error",
    "process_failed",
    "watchdog_timeout",
    "runtime_cancelled",
    "prompt_too_large",
    "context_materialization_failed",
    "context_permission_denied",
    "context_incomplete",
    "malformed_structured_output",
    "idempotency_conflict",
    "verifier_context_stale",
    "workspace_snapshot_stale",
    "verifier_provider_timeout",
    "verifier_provider_crash",
    "verifier_parse_failed",
    "checkpoint_after_failed_gate",
    "regroup_dependency_cycle",
    "regroup_write_conflict",
    "artifact_hash_mismatch",
    "payload_digest_mismatch",
    "projection_body_conflict",
    "db_resource_exhausted",
    "disk_resource_exhausted",
    "process_resource_exhausted",
    "provider_quota_exhausted",
    "operator_clearance_required",
    # Slice 13A fifth sub-slice -- doc-13a:273-275 + doc-13a:276-278.
    # Per doc-13a:273-275 "A gate may not approve from preview_only
    # evidence after 13A is enabled"; per doc-13a:276-278 "A summary
    # can satisfy a required gate only if the proof row states the
    # exact source digest, page refs, proof algorithm, and verification
    # time" -- the two NEW typed failure ids under verifier_context
    # carry the fail-closed signal for gate-companion-record and
    # proof-row validation failures (per auto-memory
    # feedback_no_silent_degradation).
    "companion_record_unavailable",
    "proof_row_required",
    # Slice 13A sixth sub-slice -- doc-13a:280-282 + auto-memory
    # feedback_no_silent_degradation. Per doc-13a:280-282 "Partial
    # snapshots are allowed for display but classifier rules fail
    # closed unless their required fields are complete" -- the two
    # NEW typed failure ids carry the fail-closed signal for
    # snapshot-companion-record validation failures. They are
    # registered under the EXISTING `evidence_corruption` failure
    # class (NOT a new failure_class) so the supervisor classifier
    # mapping coverage rule does not require a new mapping row in
    # `supervisor/classifier_mapping.py` (which is READ-ONLY per
    # doc-13a:42-46 + 124-126 change-control rule + the implementer
    # prompt's MUST-NOT-EDIT-SUPERVISOR-MODULES rule). The
    # `evidence_corruption` failure_class is semantically the closest
    # match: both signal that the snapshot's evidence is
    # structurally incomplete / corrupted; both route to `quiesce`
    # per the fail-closed contract.
    "list_field_incomplete",
    "classifier_rule_blocked",
    # Slice 14 second sub-slice -- doc-14:192-201 + doc-14:242-243.
    # Per doc-14:194-196 "Git note write fails after commit: governance
    # records a `line_provenance_gap` or `governance_evidence_conflict`
    # finding and retries the projection idempotently. It does not block
    # checkpointing or resume." -- the two NEW typed failure ids under
    # the EXISTING `evidence_corruption` failure_class carry the
    # NON-BLOCKING governance-projection signal raised by
    # :class:`iriai_build_v2.execution_control.commit_provenance_writer.GitProvenanceWriter`
    # when the Git notes/refs write fails post-commit. They register
    # under EVIDENCE_CORRUPTION because Git provenance failures signal
    # disagreement between the Postgres typed `dag-commit-proof:*`
    # evidence and the Git ref/notes evidence (structurally analogous to
    # `artifact_hash_mismatch` / `payload_digest_mismatch` /
    # `projection_body_conflict` already under the same class) BUT they
    # route to the NEW `retry_governance_projection` non-blocking action
    # (NOT `quiesce`) per doc-14:242-243 ("Governance provenance
    # projection failures never block `dag-group:*` checkpointing, merge
    # queue integration, or resume"). This is INTENTIONALLY DIFFERENT
    # from the prior Slice 13A typed ids `list_field_incomplete` +
    # `classifier_rule_blocked` (also under `evidence_corruption`) which
    # route to `quiesce` -- the Slice 13A pattern is a fail-closed safety
    # stop for required gate evidence; the Slice 14 pattern is a
    # non-blocking governance projection observer.
    "line_provenance_gap",
    "governance_evidence_conflict",
    # Slice 15 second sub-slice -- doc-15:117-136 step 2 + doc-15:140-145.
    # Per doc-15:117-136 step 2 the governance metric extractor consumes
    # Slice 13 evidence sets and projects metric definitions onto typed
    # GovernanceMetricValue records; per doc-15:140-145 governance metrics
    # are derived rows that do NOT change execution state. The NEW typed
    # failure id under the EXISTING `evidence_corruption` failure_class
    # carries the NON-BLOCKING governance-projection signal raised by
    # :class:`iriai_build_v2.execution_control.governance_metric_extractor.MetricExtractor`
    # when structural extraction failures occur. It registers under
    # EVIDENCE_CORRUPTION because metric-extraction failures signal
    # disagreement between the Slice 13 typed evidence-set and the
    # extractor's typed projection (structurally analogous to the prior
    # Slice 14 `line_provenance_gap` + `governance_evidence_conflict`
    # under the same class) AND it routes to the EXISTING NEW
    # `retry_governance_projection` non-blocking action (NOT `quiesce`)
    # REUSED from Slice 14 2nd sub-slice -- the doc-14:242-243 contract
    # (governance projection failures never block checkpointing, merge
    # queue, or resume) applies verbatim because the metric extractor is
    # also a post-checkpoint governance projection observer. This is
    # INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption`) which route to `quiesce` -- the Slice 13A
    # pattern is a fail-closed safety stop for required gate evidence;
    # the Slice 15 pattern matches the Slice 14 non-blocking observer.
    "governance_metric_extraction_failed",
    # Slice 15 fourth sub-slice -- doc-15:133-134 step 6 + doc-15:140-145.
    # Per doc-15:133-134 step 6 the governance scorecard writer composes
    # the typed GovernanceScorecard governance row + the bounded review
    # projection at review:governance-metrics:{corpus_id}; per doc-15:140-145
    # governance metrics are derived rows that do NOT change execution state.
    # The NEW typed failure id under the EXISTING `evidence_corruption`
    # failure_class carries the NON-BLOCKING governance-projection signal
    # raised by
    # :class:`iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter`
    # when structural persistence failures occur. It registers under
    # EVIDENCE_CORRUPTION because scorecard-persistence failures signal
    # disagreement between the Slice 15 1st-sub-slice typed scorecard shape
    # and the writer's typed projection (structurally analogous to the prior
    # Slice 14 `line_provenance_gap` + `governance_evidence_conflict` +
    # Slice 15 2nd-sub-slice `governance_metric_extraction_failed` under the
    # same class) AND it routes to the EXISTING `retry_governance_projection`
    # non-blocking action (NOT `quiesce`) REUSED from Slice 14 2nd sub-slice --
    # the doc-14:242-243 contract (governance projection failures never block
    # checkpointing, merge queue, or resume) applies verbatim because the
    # scorecard writer is also a post-checkpoint governance projection
    # observer. This is INTENTIONALLY DIFFERENT from the prior Slice 13A
    # typed ids `list_field_incomplete` + `classifier_rule_blocked` (also
    # under `evidence_corruption`) which route to `quiesce` -- the Slice 13A
    # pattern is a fail-closed safety stop for required gate evidence; the
    # Slice 15 pattern matches the Slice 14 + Slice 15 2nd-sub-slice
    # non-blocking observer.
    "governance_scorecard_persistence_failed",
    # Slice 16 second sub-slice -- doc-16:155-169 + doc-16:158 + doc-14:242-243.
    # Per doc-16:155-169 § Refactoring Steps 2 + 3 + 4 + 7 the governance
    # finding rule engine consumes Slice 13 evidence sets + Slice 15 metric
    # scorecards + Slice 16 1st sub-slice typed rules and emits typed
    # GovernanceFinding records; per doc-14:242-243 (inherited by every
    # post-checkpoint governance projection observer) the rule engine NEVER
    # blocks `dag-group:*` checkpointing, merge queue integration, or
    # resume. The NEW typed failure id under the EXISTING
    # `evidence_corruption` failure_class carries the NON-BLOCKING
    # governance-projection signal raised by
    # :class:`iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine`
    # when at-least-one-primary invariant violations / product-workflow
    # separation violations / suppression / expiry / below-threshold
    # confidence / structural construction failures occur. It registers
    # under EVIDENCE_CORRUPTION because rule-emission failures signal
    # disagreement between the Slice 13 typed evidence-set and the
    # engine's typed projection (structurally analogous to the prior
    # Slice 14 + Slice 15 governance projection observer failure ids
    # under the same class) AND it routes to the EXISTING
    # `retry_governance_projection` non-blocking action (NOT `quiesce`)
    # REUSED from Slice 14 2nd sub-slice -- the doc-14:242-243 contract
    # (governance projection failures never block checkpointing, merge
    # queue, or resume) applies verbatim because the rule engine is also
    # a post-checkpoint governance projection observer. This is
    # INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption`) which route to `quiesce` -- the Slice 13A
    # pattern is a fail-closed safety stop for required gate evidence;
    # the Slice 16 pattern matches the Slice 14 + Slice 15 non-blocking
    # observer.
    "finding_rule_emission_failed",
    # Slice 16 third-A sub-slice -- doc-16:164-165 + doc-16:191-192 +
    # doc-14:242-243. Per doc-16:164-165 § Refactoring Steps step 5 the
    # governance implementation-plan deviation engine consumes parsed
    # Slice 13c ImplementationArtifactAnchor rows (the typed output of
    # `parse_implementation_journal` per `journal_parser.py:320`) and
    # emits typed GovernanceFinding records for the
    # `accepted_plan_deviation` (doc-16:135) +
    # `implementation_journal_gap` (doc-16:134 + doc-16:191-192) finding
    # classes; per doc-14:242-243 (inherited by every post-checkpoint
    # governance projection observer) the plan-deviation engine NEVER
    # blocks `dag-group:*` checkpointing, merge queue integration, or
    # resume. The NEW typed failure id under the EXISTING
    # `evidence_corruption` failure_class carries the NON-BLOCKING
    # governance-projection signal raised by
    # :func:`iriai_build_v2.execution_control.finding_plan_deviation_engine.parse_plan_deviation_anchors`
    # when an anchor-parse failure occurs (missing journal file;
    # unparseable markdown body; Pydantic validation error on a
    # candidate ImplementationArtifactAnchor). It registers under
    # EVIDENCE_CORRUPTION because anchor-parse failures signal
    # disagreement between the Slice 13c parser's typed surface and the
    # raw markdown corpus (structurally analogous to the prior Slice 14
    # + Slice 15 + Slice 16 2nd sub-slice governance projection observer
    # failure ids under the same class) AND it routes to the EXISTING
    # `retry_governance_projection` non-blocking action (NOT `quiesce`)
    # REUSED from Slice 14 2nd sub-slice -- the doc-14:242-243 contract
    # (governance projection failures never block checkpointing, merge
    # queue, or resume) applies verbatim because the plan-deviation
    # engine is also a post-checkpoint governance projection observer.
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption`) which route to `quiesce` -- the Slice 13A
    # pattern is a fail-closed safety stop for required gate evidence;
    # the Slice 16 3rd-A pattern matches the Slice 14 + Slice 15 + Slice
    # 16 2nd sub-slice non-blocking observer.
    "finding_plan_deviation_parse_failed",
    # Slice 16 third-B sub-slice -- doc-16:164-165 + doc-16:137 +
    # doc-16:183-184 + doc-14:242-243. Per doc-16:164-165 § Refactoring
    # Steps step 5 remaining categories (reviewer-findings + late test
    # failures) the governance reviewer-finding + late-test-failure
    # engine consumes parsed ImplementationArtifactAnchor rows from BOTH
    # the Slice 13c journal markdown parser (per-line event="finding"
    # anchors at `journal_parser.py:514-535` + per-line event="test_result"
    # anchors at `journal_parser.py:584-595`) AND the Slice 13d JSONL
    # decision-log parser (per `decision_log_parser.py:455`); emits typed
    # GovernanceFinding records for the `governance_evidence_conflict`
    # class (doc-16:137 + doc-16:183-184 verbatim "Conflicting evidence:
    # lower confidence and emit a `governance_evidence_conflict` finding
    # if conflict affects a policy decision"). Per doc-14:242-243
    # (inherited by every post-checkpoint governance projection observer)
    # the reviewer + late-test-failure engine NEVER blocks `dag-group:*`
    # checkpointing, merge queue integration, or resume. The NEW typed
    # failure id under the EXISTING `evidence_corruption` failure_class
    # carries the NON-BLOCKING governance-projection signal raised by
    # :func:`iriai_build_v2.execution_control.finding_reviewer_test_failure_engine.parse_reviewer_test_failure_anchors`
    # when an anchor-parse failure occurs in EITHER parser (missing
    # journal / decision-log file; unparseable markdown / JSONL body;
    # Pydantic validation error on a candidate ImplementationArtifactAnchor).
    # It registers under EVIDENCE_CORRUPTION because anchor-parse
    # failures signal disagreement between either parser's typed surface
    # and the raw corpus (structurally analogous to the prior Slice 14 +
    # Slice 15 + Slice 16 2nd + 3rd-A sub-slice governance projection
    # observer failure ids under the same class) AND it routes to the
    # EXISTING `retry_governance_projection` non-blocking action (NOT
    # `quiesce`) REUSED from Slice 14 2nd sub-slice -- the doc-14:242-243
    # contract (governance projection failures never block checkpointing,
    # merge queue, or resume) applies verbatim because the reviewer +
    # late-test-failure engine is also a post-checkpoint governance
    # projection observer. This is INTENTIONALLY DIFFERENT from the prior
    # Slice 13A typed ids `list_field_incomplete` +
    # `classifier_rule_blocked` (also under `evidence_corruption`) which
    # route to `quiesce` -- the Slice 13A pattern is a fail-closed safety
    # stop for required gate evidence; the Slice 16 3rd-B pattern matches
    # the Slice 14 + Slice 15 + Slice 16 2nd + 3rd-A sub-slice non-
    # blocking observer.
    "finding_reviewer_test_failure_parse_failed",
    # Slice 16 fourth sub-slice -- doc-16:166-167 + doc-16:174-176 +
    # doc-14:242-243. Per doc-16:166-167 § Refactoring Steps step 6
    # (verbatim *"Store findings as typed governance rows and project
    # bounded review artifacts such as
    # `review:governance-findings:{corpus_id}`."*) the governance
    # finding writer composes typed GovernanceFinding records (per
    # doc-16:82-104) from the Slice 16 2nd + 3rd-A + 3rd-B sub-slice
    # engines' emissions and projects them onto BOTH typed
    # governance_finding:* rows AND the bounded review projection at
    # the `review:governance-findings:` key prefix. Per doc-14:242-243
    # (inherited by every post-checkpoint governance projection observer)
    # the finding writer NEVER blocks `dag-group:*` checkpointing, merge
    # queue integration, or resume. The NEW typed failure id under the
    # EXISTING `evidence_corruption` failure_class carries the
    # NON-BLOCKING governance-projection signal raised by
    # :class:`iriai_build_v2.execution_control.governance_finding_writer.GovernanceFindingWriter`
    # when a persistence step fails structurally (e.g.
    # findings_construction_failed; findings_digest_failed;
    # projection_body_conflict). It registers under EVIDENCE_CORRUPTION
    # because finding-persistence failures signal disagreement between
    # the writer's typed surface and the upstream rule-engine output
    # (structurally analogous to the prior Slice 14 + Slice 15 + Slice
    # 16 2nd + 3rd-A + 3rd-B sub-slice governance projection observer
    # failure ids under the same class) AND it routes to the EXISTING
    # `retry_governance_projection` non-blocking action (NOT `quiesce`)
    # REUSED from Slice 14 2nd sub-slice -- the doc-14:242-243 contract
    # (governance projection failures never block checkpointing, merge
    # queue, or resume) applies verbatim because the finding writer is
    # also a post-checkpoint governance projection observer. This is
    # INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption`) which route to `quiesce` -- the Slice 13A
    # pattern is a fail-closed safety stop for required gate evidence;
    # the Slice 16 4th pattern matches the Slice 14 + Slice 15 + Slice
    # 16 2nd + 3rd-A + 3rd-B sub-slice non-blocking observer.
    "governance_finding_persistence_failed",
    # Slice 17 second sub-slice -- doc-17:168-169 + doc-17:204 + doc-14:242-243.
    # Per doc-17:168-169 § Refactoring Steps step 2 the governance
    # recommendation builder consumes Slice 16 1st sub-slice typed
    # GovernanceFinding BaseModels + the Slice 17 1st sub-slice typed
    # GovernancePolicyRecommendation + per-consumer *PolicyArtifact
    # BaseModels and emits typed GovernancePolicyRecommendation records;
    # per doc-14:242-243 (inherited by every post-checkpoint governance
    # projection observer) the recommendation builder NEVER blocks
    # `dag-group:*` checkpointing, merge queue integration, or resume.
    # The NEW typed failure id under the EXISTING `evidence_corruption`
    # failure_class carries the NON-BLOCKING governance-projection
    # signal raised by
    # :class:`iriai_build_v2.execution_control.recommendation_builder.RecommendationBuilder`
    # when a per-finding emission step fails structurally (e.g.
    # policy_artifact_construction_failed; recommendation_construction_failed;
    # unmapped_finding_kind). It registers under EVIDENCE_CORRUPTION
    # because recommendation-emission failures signal disagreement
    # between the Slice 16 typed finding-surface and the builder's
    # typed projection (structurally analogous to the prior Slice 14 +
    # Slice 15 + Slice 16 2nd + 3rd-A + 3rd-B + 4th sub-slice
    # governance projection observer failure ids under the same class)
    # AND it routes to the EXISTING `retry_governance_projection`
    # non-blocking action (NOT `quiesce`) REUSED from Slice 14 2nd
    # sub-slice -- the doc-14:242-243 contract (governance projection
    # failures never block checkpointing, merge queue, or resume)
    # applies verbatim because the recommendation builder is also a
    # post-checkpoint governance projection observer. This is
    # INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption`) which route to `quiesce` -- the Slice 13A
    # pattern is a fail-closed safety stop for required gate evidence;
    # the Slice 17 pattern matches the Slice 14 + Slice 15 + Slice 16
    # non-blocking observer.
    "recommendation_builder_emission_failed",
    # Slice 17 third sub-slice -- doc-17:170-171 + doc-17:208-210 + doc-14:242-243.
    # Per doc-17:170-171 § Refactoring Steps step 3 the per-consumer
    # policy validation interface consumes the Slice 17 2nd sub-slice
    # typed GovernancePolicyRecommendation + *PolicyArtifact union
    # members and validates per-consumer policy-shape rules per
    # doc-17:208-210 (scheduler dependency/write-set/barrier/safety;
    # failure_router untested route changes; supervisor/dashboard
    # read_only; planning advisory_only; merge_queue
    # required_queue_tests). Per doc-14:242-243 (inherited by every
    # post-checkpoint governance projection observer) the validator
    # NEVER blocks `dag-group:*` checkpointing, merge queue
    # integration, or resume. The NEW typed failure id under the
    # EXISTING `evidence_corruption` failure_class carries the
    # NON-BLOCKING governance-projection signal raised by
    # :class:`iriai_build_v2.execution_control.policy_validation_interface.PolicyValidationInterface`
    # when an internal validation step fails structurally (e.g.
    # policy_validation_internal_unmapped_consumer;
    # policy_validation_internal_exception). It registers under
    # EVIDENCE_CORRUPTION because policy-validation failures signal
    # disagreement between the Slice 17 1st sub-slice typed
    # GovernancePolicyRecommendation surface and the validator's typed
    # projection (structurally analogous to the prior Slice 14 +
    # Slice 15 + Slice 16 2nd + 3rd-A + 3rd-B + 4th + Slice 17 2nd
    # sub-slice governance projection observer failure ids under the
    # same class) AND it routes to the EXISTING
    # `retry_governance_projection` non-blocking action (NOT
    # `quiesce`) REUSED from Slice 14 2nd sub-slice -- the
    # doc-14:242-243 contract (governance projection failures never
    # block checkpointing, merge queue, or resume) applies verbatim
    # because the validator is also a post-checkpoint governance
    # projection observer. This is INTENTIONALLY DIFFERENT from the
    # prior Slice 13A typed ids `list_field_incomplete` +
    # `classifier_rule_blocked` (also under `evidence_corruption`)
    # which route to `quiesce` -- the Slice 13A pattern is a fail-
    # closed safety stop for required gate evidence; the Slice 17
    # pattern matches the Slice 14 + Slice 15 + Slice 16 + Slice 17
    # 2nd sub-slice non-blocking observer.
    "policy_validation_failed",
    # Slice 17 fourth sub-slice -- doc-17:172 + doc-17:182-188 + doc-14:242-243.
    # Per doc-17:172 § Refactoring Steps step 4 the decision-record
    # writer persists typed PolicyRecommendationDecision rows at
    # `review:governance-recommendations:{corpus_id}` per doc-17:182-188;
    # per doc-14:242-243 (inherited by every post-checkpoint governance
    # projection observer) the writer NEVER blocks `dag-group:*`
    # checkpointing, merge queue integration, or resume. The NEW typed
    # failure id under the EXISTING `evidence_corruption` failure_class
    # carries the NON-BLOCKING governance-projection signal raised by
    # :class:`iriai_build_v2.execution_control.decision_record_writer.DecisionRecordWriter`
    # when a decision-record persistence step fails structurally (e.g.
    # decisions_construction_failed; decisions_digest_failed;
    # review_projection_id_failed). It registers under
    # EVIDENCE_CORRUPTION because decision-record persistence failures
    # signal disagreement between the Slice 17 1st sub-slice typed
    # PolicyRecommendationDecision surface and the writer's typed
    # projection (structurally analogous to the prior Slice 14 +
    # Slice 15 + Slice 16 2nd + 3rd-A + 3rd-B + 4th + Slice 17 2nd +
    # 3rd sub-slice governance projection observer failure ids under
    # the same class) AND it routes to the EXISTING
    # `retry_governance_projection` non-blocking action (NOT `quiesce`)
    # REUSED from Slice 14 2nd sub-slice -- the doc-14:242-243 contract
    # (governance projection failures never block checkpointing, merge
    # queue, or resume) applies verbatim because the decision-record
    # writer is also a post-checkpoint governance projection observer.
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed
    # ids `list_field_incomplete` + `classifier_rule_blocked` (also
    # under `evidence_corruption`) which route to `quiesce` -- the
    # Slice 13A pattern is a fail-closed safety stop for required gate
    # evidence; the Slice 17 4th pattern matches the Slice 14 + Slice
    # 15 + Slice 16 + Slice 17 2nd + 3rd sub-slice non-blocking
    # observer.
    "decision_record_persistence_failed",
    # Slice 17 fifth sub-slice -- doc-17:173-174 + doc-17:159-163 + doc-14:242-243.
    # Per doc-17:173-174 § Refactoring Steps step 5 the replay-requirement
    # validator consumes the Slice 17 1st sub-slice typed
    # GovernancePolicyRecommendation and checks that behavior-changing
    # recommendations (safe_runtime_action=True) carry the typed
    # cross-slice reference to Slice 18 counterfactual replay results
    # (non-empty counterfactual_result_refs list). Per doc-17:159-163 +
    # doc-17:225-226 the validator does NOT introduce a second source of
    # replay truth -- Slice 18 owns the replay result records; this
    # validator owns ONLY the typed cross-reference check. Per
    # doc-14:242-243 (inherited by every post-checkpoint governance
    # projection observer) the validator NEVER blocks `dag-group:*`
    # checkpointing, merge queue integration, or resume. The NEW typed
    # failure id under the EXISTING `evidence_corruption` failure_class
    # carries the NON-BLOCKING governance-projection signal raised by
    # :class:`iriai_build_v2.execution_control.replay_requirement_hook.ReplayRequirementValidator`
    # when an internal validation step fails structurally (e.g.
    # replay_requirement_internal_exception:<ExceptionName>). It
    # registers under EVIDENCE_CORRUPTION because replay-requirement
    # validation failures signal disagreement between the Slice 17 1st
    # sub-slice typed GovernancePolicyRecommendation surface and the
    # validator's typed projection (structurally analogous to the prior
    # Slice 14 + Slice 15 + Slice 16 2nd + 3rd-A + 3rd-B + 4th +
    # Slice 17 2nd + 3rd + 4th sub-slice governance projection observer
    # failure ids under the same class) AND it routes to the EXISTING
    # `retry_governance_projection` non-blocking action (NOT `quiesce`)
    # REUSED from Slice 14 2nd sub-slice -- the doc-14:242-243 contract
    # (governance projection failures never block checkpointing, merge
    # queue, or resume) applies verbatim because the replay-requirement
    # validator is also a post-checkpoint governance projection observer.
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed
    # ids `list_field_incomplete` + `classifier_rule_blocked` (also
    # under `evidence_corruption`) which route to `quiesce` -- the
    # Slice 13A pattern is a fail-closed safety stop for required gate
    # evidence; the Slice 17 5th pattern matches the Slice 14 + Slice
    # 15 + Slice 16 + Slice 17 2nd + 3rd + 4th sub-slice non-blocking
    # observer.
    "replay_requirement_validation_failed",
    # Slice 17 sixth sub-slice -- doc-17:175-177 + doc-17:159-163 + doc-14:242-243.
    # Per doc-17:175-177 § Refactoring Steps step 6 the consumer
    # read-API exposes the typed accepted-but-not-activated
    # GovernancePolicyRecommendation records SEPARATELY from
    # consumer-owned activated policy records (per
    # doc-17:175-177 VERBATIM: *"Add consumer read APIs that return
    # accepted-but-not-activated policy artifacts separately from
    # consumer-owned activated policy. Runtime consumers must ignore
    # non-activated governance recommendations."*). Per
    # doc-17:159-163 + doc-17:217 the read-API does NOT introduce a
    # second source of activation truth -- activation belongs to the
    # consumer-owned policy record per doc-17:159-163; this read-API
    # GRANTS NO consumer-side activation authority. Per
    # doc-14:242-243 (inherited by every post-checkpoint governance
    # projection observer) the read-API NEVER blocks `dag-group:*`
    # checkpointing, merge queue integration, or resume. The NEW typed
    # failure id under the EXISTING `evidence_corruption` failure_class
    # carries the NON-BLOCKING governance-projection signal raised by
    # :class:`iriai_build_v2.execution_control.consumer_read_api.GovernanceReadAPI`
    # when an internal query step fails structurally (e.g.
    # consumer_read_api_internal_exception:<ExceptionName>). It
    # registers under EVIDENCE_CORRUPTION because consumer read-API
    # failures signal disagreement between the Slice 17 1st sub-slice
    # typed GovernancePolicyRecommendation surface and the read-API's
    # typed projection (structurally analogous to the prior Slice 14 +
    # Slice 15 + Slice 16 2nd + 3rd-A + 3rd-B + 4th + Slice 17 2nd +
    # 3rd + 4th + 5th sub-slice governance projection observer failure
    # ids under the same class) AND it routes to the EXISTING
    # `retry_governance_projection` non-blocking action (NOT
    # `quiesce`) REUSED from Slice 14 2nd sub-slice -- the
    # doc-14:242-243 contract (governance projection failures never
    # block checkpointing, merge queue, or resume) applies verbatim
    # because the consumer read-API is also a post-checkpoint
    # governance projection observer. This is INTENTIONALLY DIFFERENT
    # from the prior Slice 13A typed ids `list_field_incomplete` +
    # `classifier_rule_blocked` (also under `evidence_corruption`)
    # which route to `quiesce` -- the Slice 13A pattern is a fail-
    # closed safety stop for required gate evidence; the Slice 17 6th
    # pattern matches the Slice 14 + Slice 15 + Slice 16 + Slice 17
    # 2nd + 3rd + 4th + 5th sub-slice non-blocking observer.
    "consumer_read_api_failed",
    # Slice 18 second sub-slice -- doc-18:111-112 + doc-14:242-243.
    # Per doc-18:111 § Refactoring Steps step 1 the replay corpus loader
    # consumes typed Slice 13a GovernanceEvidenceRef inputs + Slice 00
    # fixture paths and emits typed Slice 18 1st sub-slice ReplayCorpus
    # records; per doc-18:112 § Refactoring Steps step 2 the scenario
    # definition builder consumes the typed corpus + the typed scenario
    # inputs and emits typed Slice 18 1st sub-slice CounterfactualScenario
    # records with required_evidence_kinds + validity_limits verification
    # per doc-18:134-135. Per doc-14:242-243 (inherited by every post-
    # checkpoint governance projection observer) the loader + builder
    # NEVER block `dag-group:*` checkpointing, merge queue integration,
    # or resume. The NEW typed failure id under the EXISTING
    # `evidence_corruption` failure_class carries the NON-BLOCKING
    # governance-projection signal raised by
    # :class:`iriai_build_v2.execution_control.counterfactual_replay_loader.ReplayCorpusLoader`
    # AND
    # :class:`iriai_build_v2.execution_control.counterfactual_replay_loader.ScenarioDefinitionBuilder`
    # when a construction step fails structurally (e.g.
    # corpus_construction_failed; scenario_construction_failed;
    # evidence_set_refs_exceeded_bound;
    # implementation_anchor_refs_exceeded_bound; corpus_id_empty;
    # feature_ids_empty; scenario_id_empty; empty_affected_consumers).
    # A SINGLE failure id covers BOTH the loader + builder per the
    # Slice 17 6th sub-slice `consumer_read_api_failed` precedent (one
    # typed failure id covering multiple typed surface methods on a
    # single typed class). It registers under EVIDENCE_CORRUPTION
    # because replay-load failures signal disagreement between the
    # typed Slice 13a evidence-ref surface + the Slice 18 1st sub-slice
    # typed corpus/scenario surfaces and the loader/builder's typed
    # projection (structurally analogous to the prior Slice 14 +
    # Slice 15 + Slice 16 2nd + 3rd-A + 3rd-B + 4th + Slice 17 2nd +
    # 3rd + 4th + 5th + 6th sub-slice governance projection observer
    # failure ids under the same class) AND it routes to the EXISTING
    # `retry_governance_projection` non-blocking action (NOT `quiesce`)
    # REUSED from Slice 14 2nd sub-slice -- the doc-14:242-243 contract
    # (governance projection failures never block checkpointing, merge
    # queue, or resume) applies verbatim because the replay corpus
    # loader + scenario definition builder are also post-checkpoint
    # governance projection observers + per doc-18:123 replay results
    # are review/governance artifacts only -- never runtime policy
    # authority. This is INTENTIONALLY DIFFERENT from the prior Slice
    # 13A typed ids `list_field_incomplete` + `classifier_rule_blocked`
    # (also under `evidence_corruption`) which route to `quiesce` --
    # the Slice 13A pattern is a fail-closed safety stop for required
    # gate evidence; the Slice 18 pattern matches the Slice 14 +
    # Slice 15 + Slice 16 + Slice 17 non-blocking observer.
    "replay_corpus_or_scenario_load_failed",
    # Slice 18 third sub-slice -- doc-18:113 + doc-14:242-243.
    # Per doc-18:113 § Refactoring Steps step 3 the
    # CounterfactualSummaryReplayEngine consumes typed Slice 18 1st
    # sub-slice ReplayCorpus + CounterfactualScenario + Slice 15 typed
    # GovernanceMetricValue baseline records + optional Slice 15 typed
    # GovernanceScorecard baseline and emits typed Slice 18 1st sub-
    # slice CounterfactualResult records with all 16 fields populated
    # (per doc-18:79-96) -- WITHOUT requiring typed-event replay (the
    # latter lands in the Slice 18 4th sub-slice per doc-18:114). Per
    # doc-14:242-243 (inherited by every post-checkpoint governance
    # projection observer) the summary-replay engine NEVER blocks
    # `dag-group:*` checkpointing, merge queue integration, or resume.
    # The NEW typed failure id under the EXISTING `evidence_corruption`
    # failure_class carries the NON-BLOCKING governance-projection
    # signal raised by
    # :class:`iriai_build_v2.execution_control.counterfactual_summary_replay.CounterfactualSummaryReplayEngine`
    # when a projection step fails structurally (e.g.
    # result_construction_failed; baseline_metrics_exceeded_bound;
    # invalid_replay_mode_for_engine; result_id_empty;
    # baseline_metrics_empty). It registers under EVIDENCE_CORRUPTION
    # because summary-replay failures signal disagreement between the
    # typed Slice 15 metric baseline surface + the typed Slice 18 1st
    # sub-slice CounterfactualResult surface and the engine's typed
    # projection (structurally analogous to the prior Slice 14 +
    # Slice 15 + Slice 16 2nd + 3rd-A + 3rd-B + 4th + Slice 17 2nd +
    # 3rd + 4th + 5th + 6th + Slice 18 2nd sub-slice governance
    # projection observer failure ids under the same class) AND it
    # routes to the EXISTING `retry_governance_projection` non-
    # blocking action (NOT `quiesce`) REUSED from Slice 14 2nd sub-
    # slice -- the doc-14:242-243 contract (governance projection
    # failures never block checkpointing, merge queue, or resume)
    # applies verbatim because the summary-replay engine is also a
    # post-checkpoint governance projection observer + per doc-18:123
    # replay results are review/governance artifacts only -- never
    # runtime policy authority. This is INTENTIONALLY DIFFERENT from
    # the prior Slice 13A typed ids `list_field_incomplete` +
    # `classifier_rule_blocked` (also under `evidence_corruption`)
    # which route to `quiesce` -- the Slice 13A pattern is a fail-
    # closed safety stop for required gate evidence; the Slice 18 3rd
    # pattern matches the Slice 14 + Slice 15 + Slice 16 + Slice 17 +
    # Slice 18 2nd non-blocking observer.
    "summary_replay_failed",
    # Slice 18 fourth sub-slice -- doc-18:114 + doc-14:242-243.
    # Per doc-18:114 § Refactoring Steps step 4 the
    # CounterfactualEventReplayEngine consumes typed Slice 18 1st
    # sub-slice ReplayCorpus + CounterfactualScenario + typed Slice
    # 10a event-transition shapes (ExecutionAttemptSummary +
    # GateStatusSummary + TypedFailureSummary + MergeQueueSummary +
    # EvidenceRef checkpoints) + optional Slice 15 typed
    # GovernanceMetricValue baseline records and emits typed Slice 18
    # 1st sub-slice CounterfactualResult records with all 16 fields
    # populated (per doc-18:79-96) at HIGHER fidelity than the 3rd
    # sub-slice summary-replay engine (which carries
    # SUMMARY_REPLAY_CONFIDENCE_CEILING = 0.65; the event-replay
    # engine carries EVENT_REPLAY_CONFIDENCE_CEILING = 0.90 per
    # doc-18:114 vs doc-18:133 contrast). Per doc-14:242-243
    # (inherited by every post-checkpoint governance projection
    # observer) the event-replay engine NEVER blocks `dag-group:*`
    # checkpointing, merge queue integration, or resume. The NEW
    # typed failure id under the EXISTING `evidence_corruption`
    # failure_class carries the NON-BLOCKING governance-projection
    # signal raised by
    # :class:`iriai_build_v2.execution_control.counterfactual_event_replay.CounterfactualEventReplayEngine`
    # when a projection step fails structurally (e.g.
    # result_construction_failed; event_transitions_exceeded_bound;
    # invalid_replay_mode_for_engine; result_id_empty;
    # all_event_transitions_empty). It registers under
    # EVIDENCE_CORRUPTION because event-replay failures signal
    # disagreement between the typed Slice 10a event-transition
    # surface + the typed Slice 18 1st sub-slice CounterfactualResult
    # surface and the engine's typed projection (structurally
    # analogous to the prior Slice 14 + Slice 15 + Slice 16 2nd +
    # 3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th +
    # Slice 18 2nd + 3rd sub-slice governance projection observer
    # failure ids under the same class) AND it routes to the
    # EXISTING `retry_governance_projection` non-blocking action
    # (NOT `quiesce`) REUSED from Slice 14 2nd sub-slice -- the
    # doc-14:242-243 contract applies verbatim because the event-
    # replay engine is also a post-checkpoint governance projection
    # observer + per doc-18:123 replay results are review/governance
    # artifacts only -- never runtime policy authority. This is
    # INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption`) which route to `quiesce` -- the Slice
    # 13A pattern is a fail-closed safety stop for required gate
    # evidence; the Slice 18 4th pattern matches the Slice 14 +
    # Slice 15 + Slice 16 + Slice 17 + Slice 18 2nd + 3rd non-
    # blocking observer.
    "event_replay_failed",
    # Slice 18 fifth sub-slice -- doc-18:115 + doc-14:242-243.
    # `metrics_comparator_failed` is the typed governance failure id
    # under the EXISTING `evidence_corruption` failure_class. The
    # failure_class carries the NON-BLOCKING governance-projection
    # signal raised by
    # :class:`iriai_build_v2.execution_control.counterfactual_metrics_comparator.CounterfactualMetricsComparator`
    # when a comparator step fails structurally (e.g.
    # result_construction_failed; baseline_metrics_exceeded_bound;
    # result_id_empty; baseline_metrics_empty; scenario_result_invalid).
    # It registers under EVIDENCE_CORRUPTION because metrics-comparator
    # failures signal disagreement between the typed Slice 15
    # GovernanceMetricValue baseline surface + the typed Slice 18 1st
    # sub-slice CounterfactualResult scenario surface and the
    # comparator's typed projection (structurally analogous to the
    # prior Slice 14 + Slice 15 + Slice 16 2nd + 3rd-A + 3rd-B + 4th +
    # Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18 2nd + 3rd + 4th
    # sub-slice governance projection observer failure ids under the
    # same class) AND it routes to the EXISTING
    # `retry_governance_projection` non-blocking action (NOT `quiesce`)
    # REUSED from Slice 14 2nd sub-slice -- the doc-14:242-243 contract
    # applies verbatim because the metrics-comparator is also a post-
    # checkpoint governance projection observer + per doc-18:123 replay
    # results are review/governance artifacts only -- never runtime
    # policy authority. This is INTENTIONALLY DIFFERENT from the prior
    # Slice 13A typed ids `list_field_incomplete` +
    # `classifier_rule_blocked` (also under `evidence_corruption`)
    # which route to `quiesce` -- the Slice 13A pattern is a fail-
    # closed safety stop for required gate evidence; the Slice 18 5th
    # pattern matches the Slice 14 + Slice 15 + Slice 16 + Slice 17 +
    # Slice 18 2nd + 3rd + 4th non-blocking observer.
    "metrics_comparator_failed",
    # Slice 18 sixth sub-slice -- doc-18:116-117 + doc-14:242-243.
    # `counterfactual_result_persistence_failed` is the typed governance
    # failure id under the EXISTING `evidence_corruption` failure_class.
    # The failure_class carries the NON-BLOCKING governance-projection
    # signal raised by
    # :class:`iriai_build_v2.execution_control.counterfactual_result_writer.CounterfactualResultWriter`
    # when a counterfactual-result persistence step fails structurally
    # (e.g. results_construction_failed; results_digest_failed;
    # review_projection_digest_mismatch; results_count_exceeds_cap).
    # It registers under EVIDENCE_CORRUPTION because counterfactual-
    # result-writer failures signal disagreement between the typed
    # Slice 18 1st sub-slice CounterfactualResult result-row surface +
    # the typed Slice 18 5th sub-slice MetricsComparatorResult
    # comparator surface and the writer's typed projection
    # (structurally analogous to the prior Slice 14 + Slice 15 +
    # Slice 16 2nd + 3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th +
    # 5th + 6th + Slice 18 2nd + 3rd + 4th + 5th sub-slice governance
    # projection observer failure ids under the same class) AND it
    # routes to the EXISTING `retry_governance_projection` non-blocking
    # action (NOT `quiesce`) REUSED from Slice 14 2nd sub-slice -- the
    # doc-14:242-243 contract applies verbatim because the
    # counterfactual-result writer is also a post-checkpoint governance
    # projection observer + per doc-18:123-125 replay results are
    # review/governance artifacts only -- never runtime policy
    # authority. This is INTENTIONALLY DIFFERENT from the prior Slice
    # 13A typed ids `list_field_incomplete` + `classifier_rule_blocked`
    # (also under `evidence_corruption`) which route to `quiesce` --
    # the Slice 13A pattern is a fail-closed safety stop for required
    # gate evidence; the Slice 18 6th pattern matches the Slice 14 +
    # Slice 15 + Slice 16 + Slice 17 + Slice 18 2nd + 3rd + 4th + 5th
    # non-blocking observer.
    "counterfactual_result_persistence_failed",
    # Slice 18 seventh sub-slice -- doc-18:117-119 + doc-18:165-166 +
    # doc-14:242-243. `recommendation_citation_validation_failed` is
    # the typed governance failure id under the EXISTING
    # `evidence_corruption` failure_class. The failure_class carries
    # the NON-BLOCKING governance-projection signal raised by
    # :class:`iriai_build_v2.execution_control.recommendation_citation_hook.RecommendationCitationValidator`
    # when an internal validation step fails structurally (e.g.
    # CitationSufficiencyResult construction validation race against a
    # concurrent upstream Slice 17 recommendation-builder emission
    # updating the typed GovernancePolicyRecommendation
    # safe_runtime_action / counterfactual_result_refs / status
    # fields; transient typed
    # :class:`iriai_build_v2.execution_control.recommendation_citation_hook.CitationGap`
    # construction validation race against a concurrent upstream
    # Slice 18 1st sub-slice CounterfactualResult emission updating
    # the typed result_id field). Per doc-18:117-119 step 7 the
    # citation validator extends the Slice 17 5th sub-slice
    # `replay_requirement_validation_failed` contract one step further:
    # checks the ref-strings the recommendation cites resolve to actual
    # typed CounterfactualResult.result_id values in the provided list
    # OR the recommendation explicitly carries status="needs_more_evidence"
    # (per doc-18:165-166 AC4 verbatim: *"Recommendations that affect
    # runtime behavior cite replay results or explicitly say more
    # evidence is needed."*). It registers under EVIDENCE_CORRUPTION
    # because citation-validation failures signal disagreement between
    # the typed Slice 17 1st sub-slice GovernancePolicyRecommendation
    # surface + the typed Slice 18 1st sub-slice CounterfactualResult
    # cross-reference surface and the validator's typed projection
    # (structurally analogous to the prior Slice 14 + Slice 15 +
    # Slice 16 2nd + 3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th +
    # 5th + 6th + Slice 18 2nd + 3rd + 4th + 5th + 6th sub-slice
    # governance projection observer failure ids under the same class)
    # AND it routes to the EXISTING `retry_governance_projection`
    # non-blocking action (NOT `quiesce`) REUSED from Slice 14 2nd
    # sub-slice -- the doc-14:242-243 contract applies verbatim because
    # the citation validator is also a post-checkpoint governance
    # projection observer + per doc-18:123-125 replay results are
    # review/governance artifacts only -- never runtime policy
    # authority. This is INTENTIONALLY DIFFERENT from the prior Slice
    # 13A typed ids `list_field_incomplete` + `classifier_rule_blocked`
    # (also under `evidence_corruption`) which route to `quiesce` --
    # the Slice 13A pattern is a fail-closed safety stop for required
    # gate evidence; the Slice 18 7th pattern matches the Slice 14 +
    # Slice 15 + Slice 16 + Slice 17 + Slice 18 2nd + 3rd + 4th + 5th
    # + 6th non-blocking observer.
    "recommendation_citation_validation_failed",
    # Slice 19 second sub-slice -- doc-19:151 + doc-19:184-194 +
    # doc-14:242-243. `governance_snapshot_api_failed` is the typed
    # governance failure id under the EXISTING `evidence_corruption`
    # failure_class. The failure_class carries the NON-BLOCKING
    # governance-projection signal raised by
    # :class:`iriai_build_v2.execution_control.governance_snapshot_api.GovernanceSnapshotAPI`
    # when a projection step fails structurally (e.g.
    # corpus_id_empty; snapshot_construction_failed;
    # digest_computation_failed) OR an informational gap fires
    # (e.g. governance_snapshot_stale per doc-19:186-187;
    # active_workflow_pressure per doc-19:193-194). A SINGLE failure
    # id covers ALL doc-19:184-194 edge-case rows per the Slice 17
    # 6th sub-slice `consumer_read_api_failed` + Slice 18 2nd
    # sub-slice `replay_corpus_or_scenario_load_failed` precedent
    # (one typed failure id per typed-API class; the typed gap
    # carries the surface `reason` for downstream classification).
    # It registers under EVIDENCE_CORRUPTION because snapshot-API
    # failures signal disagreement between the typed Slice 13a/16/17/18
    # corpus surface and the API's typed Slice 19 1st sub-slice
    # GovernanceSnapshot projection (structurally analogous to the
    # prior Slice 14 + Slice 15 + Slice 16 2nd + 3rd-A + 3rd-B + 4th +
    # Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18 2nd + 3rd + 4th
    # + 5th + 6th + 7th sub-slice governance projection observer
    # failure ids under the same class) AND it routes to the EXISTING
    # `retry_governance_projection` non-blocking action (NOT
    # `quiesce`) REUSED from Slice 14 2nd sub-slice -- the
    # doc-14:242-243 contract applies verbatim because the snapshot
    # API is also a post-checkpoint governance projection observer +
    # per doc-19:170-171 dashboards read snapshots with bounded fields
    # + per doc-19:166-167 reports are projections of governance rows
    # -- never runtime policy authority. This is INTENTIONALLY
    # DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption`) which route to `quiesce` -- the Slice 13A
    # pattern is a fail-closed safety stop for required gate evidence;
    # the Slice 19 2nd pattern matches the Slice 14 + Slice 15 +
    # Slice 16 + Slice 17 + Slice 18 non-blocking observer.
    "governance_snapshot_api_failed",
    # Slice 19 third sub-slice -- doc-19:152 + doc-19:170-171 +
    # doc-19:184-194 + doc-14:242-243.
    # `governance_dashboard_view_failed` is the typed governance
    # failure id under the EXISTING `evidence_corruption`
    # failure_class. The failure_class carries the NON-BLOCKING
    # governance-projection signal raised by
    # :class:`iriai_build_v2.execution_control.governance_dashboard_view.GovernanceDashboardView`
    # when a structural projection step fails (e.g.
    # upstream_snapshot_missing; payload_construction_failed;
    # summary_projection_failed; etag_computation_failed) OR an
    # informational gap fires (e.g. governance_snapshot_stale per
    # doc-19:186-187; active_workflow_pressure per doc-19:193-194).
    # A SINGLE failure id covers ALL doc-19:184-194 edge-case rows
    # per the Slice 17 6th sub-slice `consumer_read_api_failed` +
    # Slice 18 2nd sub-slice `replay_corpus_or_scenario_load_failed`
    # + Slice 19 2nd sub-slice `governance_snapshot_api_failed`
    # precedent (one typed failure id per typed-API class; the typed
    # gap carries the surface `reason` for downstream classification).
    # It registers under EVIDENCE_CORRUPTION because dashboard view
    # failures signal disagreement between the typed Slice 19 1st
    # sub-slice GovernanceSnapshot + Slice 19 2nd sub-slice
    # SnapshotAPIResult and the typed Slice 19 3rd sub-slice
    # DashboardViewPayload projection (structurally analogous to the
    # prior Slice 14 + Slice 15 + Slice 16 2nd + 3rd-A + 3rd-B + 4th +
    # Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18 2nd + 3rd + 4th
    # + 5th + 6th + 7th + Slice 19 2nd sub-slice governance projection
    # observer failure ids under the same class) AND it routes to the
    # EXISTING `retry_governance_projection` non-blocking action (NOT
    # `quiesce`) REUSED from Slice 14 2nd sub-slice -- the
    # doc-14:242-243 contract applies verbatim because the dashboard
    # view is also a post-checkpoint governance projection observer +
    # per doc-19:170-171 dashboards read snapshots with bounded fields
    # + per doc-19:166-167 reports are projections of governance rows
    # -- never runtime policy authority. This is INTENTIONALLY
    # DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption`) which route to `quiesce` -- the Slice 13A
    # pattern is a fail-closed safety stop for required gate evidence;
    # the Slice 19 3rd pattern matches the Slice 14 + Slice 15 +
    # Slice 16 + Slice 17 + Slice 18 + Slice 19 2nd non-blocking
    # observer.
    "governance_dashboard_view_failed",
    # Slice 19 fourth sub-slice -- doc-19:155 + doc-19:140-142 +
    # doc-19:122-123 + doc-19:184-194 + doc-19:191-192 +
    # doc-14:242-243.
    # `governance_slack_renderer_failed` is the typed governance
    # failure id under the EXISTING `evidence_corruption`
    # failure_class. The failure_class carries the NON-BLOCKING
    # governance-projection signal raised by
    # :class:`iriai_build_v2.execution_control.governance_slack_renderer.GovernanceSlackRenderer`
    # when a structural projection step fails (e.g.
    # upstream_snapshot_missing; payload_construction_failed;
    # summary_projection_failed; dedupe_key_computation_failed;
    # budget_exceeded) OR an informational gap fires (e.g.
    # governance_snapshot_stale per doc-19:186-187;
    # slack_delivery_failure per doc-19:191-192; active_workflow_pressure
    # per doc-19:193-194). A SINGLE failure id covers ALL doc-19:184-194
    # edge-case rows + the doc-19:191-192 Slack delivery failure row
    # per the Slice 17 6th sub-slice `consumer_read_api_failed` +
    # Slice 18 2nd sub-slice `replay_corpus_or_scenario_load_failed`
    # + Slice 19 2nd sub-slice `governance_snapshot_api_failed` +
    # Slice 19 3rd sub-slice `governance_dashboard_view_failed`
    # precedent (one typed failure id per typed-API class; the typed
    # gap carries the surface `reason` for downstream classification).
    # It registers under EVIDENCE_CORRUPTION because Slack renderer
    # failures signal disagreement between the typed Slice 19 1st
    # sub-slice GovernanceSnapshot + Slice 19 2nd sub-slice
    # SnapshotAPIResult and the typed Slice 19 4th sub-slice
    # SlackRenderPayload projection or the 40 KB Block Kit budget
    # (structurally analogous to the prior Slice 14 + Slice 15 +
    # Slice 16 2nd + 3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th +
    # 5th + 6th + Slice 18 2nd + 3rd + 4th + 5th + 6th + 7th + Slice
    # 19 2nd + 3rd sub-slice governance projection observer failure
    # ids under the same class) AND it routes to the EXISTING
    # `retry_governance_projection` non-blocking action (NOT
    # `quiesce`) REUSED from Slice 14 2nd sub-slice -- the
    # doc-14:242-243 contract applies verbatim because the Slack
    # renderer is also a post-checkpoint governance projection
    # observer + per doc-19:166-167 reports are projections of
    # governance rows + per doc-19:191-192 Slack delivery failure
    # retries via existing outbox policy -- never runtime policy
    # authority. This is INTENTIONALLY DIFFERENT from the prior
    # Slice 13A typed ids `list_field_incomplete` +
    # `classifier_rule_blocked` (also under `evidence_corruption`)
    # which route to `quiesce` -- the Slice 13A pattern is a fail-
    # closed safety stop for required gate evidence; the Slice 19
    # 4th pattern matches the Slice 14 + Slice 15 + Slice 16 +
    # Slice 17 + Slice 18 + Slice 19 2nd + 3rd non-blocking
    # observer.
    "governance_slack_renderer_failed",
    # Slice 19 fifth sub-slice -- doc-19 step 5 + doc-19:124-127 +
    # doc-19:144-146 + doc-19:184-194 + doc-14:242-243.
    # `governance_agent_context_builder_failed` is the typed governance
    # failure id under the EXISTING `evidence_corruption`
    # failure_class. The failure_class carries the NON-BLOCKING
    # governance-projection signal raised by
    # :class:`iriai_build_v2.execution_control.governance_agent_context_builder.GovernanceAgentContextBuilder`
    # when a structural projection step fails (e.g.
    # upstream_snapshot_missing; context_construction_failed;
    # parallel_list_length_mismatch; prompt_budget_exceeded) OR an
    # informational gap fires (e.g. governance_snapshot_stale per
    # doc-19:186-187; missing_line_provenance per doc-19:188-189;
    # active_workflow_pressure per doc-19:193-194). A SINGLE failure
    # id covers ALL doc-19:184-194 edge-case rows per the Slice 17 6th
    # sub-slice `consumer_read_api_failed` + Slice 18 2nd sub-slice
    # `replay_corpus_or_scenario_load_failed` + Slice 19 2nd sub-slice
    # `governance_snapshot_api_failed` + Slice 19 3rd sub-slice
    # `governance_dashboard_view_failed` + Slice 19 4th sub-slice
    # `governance_slack_renderer_failed` precedent (one typed failure
    # id per typed-API class; the typed gap carries the surface
    # `reason` for downstream classification).
    # It registers under EVIDENCE_CORRUPTION because agent-context-
    # builder failures signal disagreement between the typed Slice 19
    # 1st sub-slice GovernanceAgentContext + Slice 19 2nd sub-slice
    # SnapshotAPIResult and the typed Slice 19 5th sub-slice agent-
    # context projection or the 20 000 char prompt budget (structurally
    # analogous to the prior Slice 14 + Slice 15 + Slice 16 2nd + 3rd-A
    # + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18
    # 2nd + 3rd + 4th + 5th + 6th + 7th + Slice 19 2nd + 3rd + 4th sub-
    # slice governance projection observer failure ids under the same
    # class) AND it routes to the EXISTING
    # `retry_governance_projection` non-blocking action (NOT
    # `quiesce`) REUSED from Slice 14 2nd sub-slice -- the
    # doc-14:242-243 contract applies verbatim because the agent-
    # context builder is also a post-checkpoint governance projection
    # observer + per doc-19:166-167 reports are projections of
    # governance rows + per doc-19:174-176 agent `policy_guidance` is
    # prompt context only -- never runtime policy authority. This is
    # INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption`) which route to `quiesce` -- the Slice 13A
    # pattern is a fail-closed safety stop for required gate
    # evidence; the Slice 19 5th pattern matches the Slice 14 + Slice
    # 15 + Slice 16 + Slice 17 + Slice 18 + Slice 19 2nd + 3rd + 4th
    # non-blocking observer.
    "governance_agent_context_builder_failed",
    # Slice 19 sixth sub-slice -- doc-19:161-162 + doc-19:166-167 +
    # doc-19:184-194 + doc-14:242-243.
    # `governance_report_artifact_emission_failed` is the typed
    # governance failure id under the EXISTING `evidence_corruption`
    # failure_class. The failure_class carries the NON-BLOCKING
    # governance-projection signal raised by
    # :class:`iriai_build_v2.execution_control.governance_report_artifact.GovernanceReportArtifactEmitter`
    # when a structural projection step fails (e.g.
    # upstream_snapshot_missing; corpus_id_empty;
    # artifact_construction_failed) OR an informational gap fires
    # (e.g. governance_snapshot_stale per doc-19:186-187; active
    # workflow pressure per doc-19:193-194). A SINGLE failure id
    # covers ALL doc-19:184-194 edge-case rows per the Slice 17 6th
    # sub-slice `consumer_read_api_failed` + Slice 18 2nd sub-slice
    # `replay_corpus_or_scenario_load_failed` + Slice 19 2nd sub-slice
    # `governance_snapshot_api_failed` + Slice 19 3rd sub-slice
    # `governance_dashboard_view_failed` + Slice 19 4th sub-slice
    # `governance_slack_renderer_failed` + Slice 19 5th sub-slice
    # `governance_agent_context_builder_failed` precedent (one typed
    # failure id per typed-API class; the typed gap carries the
    # surface `reason` for downstream classification).
    # It registers under EVIDENCE_CORRUPTION because report-artifact
    # emission failures signal disagreement between the typed Slice
    # 19 2nd sub-slice SnapshotAPIResult and the typed Slice 19 6th
    # sub-slice GovernanceReportArtifact bounded-summary projection or
    # the typed `review:governance-report:{corpus_id}` artifact-key
    # substitution (structurally analogous to the prior Slice 14 +
    # Slice 15 + Slice 16 2nd + 3rd-A + 3rd-B + 4th + Slice 17 2nd +
    # 3rd + 4th + 5th + 6th + Slice 18 2nd + 3rd + 4th + 5th + 6th +
    # 7th + Slice 19 2nd + 3rd + 4th + 5th sub-slice governance
    # projection observer failure ids under the same class) AND it
    # routes to the EXISTING `retry_governance_projection`
    # non-blocking action (NOT `quiesce`) REUSED from Slice 14 2nd
    # sub-slice -- the doc-14:242-243 contract applies verbatim
    # because the report-artifact emitter is also a post-checkpoint
    # governance projection observer + per doc-19:166-167 reports are
    # projections of governance rows + per doc-19:161-162 the
    # `review:governance-report:{corpus_id}` artifact key is a
    # bounded summary only -- never runtime policy authority. This is
    # INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption`) which route to `quiesce` -- the Slice
    # 13A pattern is a fail-closed safety stop for required gate
    # evidence; the Slice 19 6th pattern matches the Slice 14 + Slice
    # 15 + Slice 16 + Slice 17 + Slice 18 + Slice 19 2nd + 3rd + 4th
    # + 5th non-blocking observer.
    "governance_report_artifact_emission_failed",
    "unclassified",
]

RouteAction: TypeAlias = Literal[
    "retry_dispatch",
    "run_product_repair",
    "run_contract_repair",
    "run_canonicalization_repair",
    "run_workspace_repair",
    "run_commit_hygiene_repair",
    "retry_verifier",
    "retry_merge",
    "retry_sandbox_capture",
    "run_sandbox_cleanup",
    # Slice 14 second sub-slice -- doc-14:192-201 + doc-14:242-243.
    # Per doc-14:242-243 governance provenance projection failures NEVER
    # block `dag-group:*` checkpointing, merge queue integration, or
    # resume. The NEW `retry_governance_projection` action is the
    # NON-BLOCKING retry route for the 2 typed failure ids
    # `line_provenance_gap` + `governance_evidence_conflict` so the
    # router does NOT fall back to `quiesce` (which would be the wrong
    # signal -- `quiesce` is a fail-closed safety stop that pauses the
    # whole executor; governance projection failures are post-checkpoint
    # observer-only). The action is INTENTIONALLY a sibling of the other
    # `retry_*` actions: it benefits from `action.startswith("retry_")`
    # downstream so legacy callers that bucket retries together see it
    # as a retry route; it also gets included in `_RETRY_ACTIONS` below
    # so the budget reservation machinery applies the configured retry
    # budget per class.
    "retry_governance_projection",
    "quiesce",
    "operator_required",
]

FailureSource: TypeAlias = Literal[
    "dispatcher",
    "workspace_authority",
    "contract",
    "sandbox",
    "verification_graph",
    "merge_queue",
    "regroup",
    "journal",
    "artifact_store",
]

FAILURE_SEVERITIES: tuple[str, ...] = ("info", "warning", "error", "fatal")

FAILURE_CLASSES: tuple[str, ...] = (
    "product_defect",
    "contract_compile",
    "contract_violation",
    "stale_projection",
    "worktree_alias",
    "acl_workability",
    "sandbox_allocation",
    "sandbox_binding",
    "sandbox_isolation",
    "sandbox_capture",
    "sandbox_cleanup",
    "commit_hygiene",
    "merge_conflict",
    "runtime_provider",
    "runtime_timeout",
    "runtime_cancelled",
    "runtime_context",
    "runtime_structured_output",
    "dispatcher_internal",
    "verifier_provider",
    "verifier_context",
    "checkpoint_contradiction",
    "regroup_invalid",
    "evidence_corruption",
    "resource_exhausted",
    "operator_required",
    "unknown",
)

FAILURE_TYPES: tuple[str, ...] = (
    "semantic_verifier_rejected",
    "required_path_missing",
    "contract_invalid_path",
    "contract_scope_conflict",
    "contract_missing_dependency",
    "contract_same_wave_dependency",
    "outside_allowed_paths",
    "forbidden_path_touched",
    "read_only_path_touched",
    "contract_id_mismatch",
    "alias_points_to_noncanonical_root",
    "alias_only_canonical_missing",
    "alias_canonical_divergent",
    "unwritable_runtime_path",
    "sandbox_clone_failed",
    "sandbox_disk_quota",
    "sandbox_base_snapshot_unavailable",
    "runtime_workspace_binding_failed",
    "canonical_path_exposed_to_writer",
    "path_escape_detected",
    "patch_capture_failed",
    "sandbox_index_corrupt",
    "cleanup_failed",
    "commit_hook_failed",
    "dirty_after_commit",
    "stale_base_commit",
    "rebase_conflict",
    "patch_apply_conflict",
    "provider_internal_error",
    "provider_rate_limited",
    "provider_transport_error",
    "process_failed",
    "watchdog_timeout",
    "runtime_cancelled",
    "prompt_too_large",
    "context_materialization_failed",
    "context_permission_denied",
    "context_incomplete",
    "malformed_structured_output",
    "idempotency_conflict",
    "verifier_context_stale",
    "workspace_snapshot_stale",
    "verifier_provider_timeout",
    "verifier_provider_crash",
    "verifier_parse_failed",
    "checkpoint_after_failed_gate",
    "regroup_dependency_cycle",
    "regroup_write_conflict",
    "artifact_hash_mismatch",
    "payload_digest_mismatch",
    "projection_body_conflict",
    "db_resource_exhausted",
    "disk_resource_exhausted",
    "process_resource_exhausted",
    "provider_quota_exhausted",
    "operator_clearance_required",
    # Slice 13A fifth sub-slice -- doc-13a:273-275 + doc-13a:276-278.
    # See FailureType Literal above for the per-id citation.
    "companion_record_unavailable",
    "proof_row_required",
    # Slice 13A sixth sub-slice -- doc-13a:280-282.
    # See FailureType Literal above for the per-id citation +
    # the evidence_corruption-class decision rationale.
    "list_field_incomplete",
    "classifier_rule_blocked",
    # Slice 14 second sub-slice -- doc-14:192-201 + doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the
    # NON-BLOCKING `retry_governance_projection` routing rationale.
    "line_provenance_gap",
    "governance_evidence_conflict",
    # Slice 15 second sub-slice -- doc-15:117-136 + doc-15:140-145.
    # See FailureType Literal above for the per-id citation + the
    # REUSED NON-BLOCKING `retry_governance_projection` routing
    # rationale (REUSES the Slice 14 2nd sub-slice action; NOT a new
    # action).
    "governance_metric_extraction_failed",
    # Slice 15 fourth sub-slice -- doc-15:133-134 step 6 + doc-15:140-145.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action; matches
    # the Slice 15 2nd sub-slice extractor failure-id pattern).
    "governance_scorecard_persistence_failed",
    # Slice 16 second sub-slice -- doc-16:155-169 + doc-16:158 + doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action; matches
    # the Slice 15 2nd + 4th sub-slice extractor/writer failure-id patterns).
    "finding_rule_emission_failed",
    # Slice 16 third-A sub-slice -- doc-16:164-165 + doc-16:191-192 + doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action; matches
    # the Slice 15 2nd + 4th + Slice 16 2nd sub-slice extractor/writer/
    # engine failure-id patterns).
    "finding_plan_deviation_parse_failed",
    # Slice 16 third-B sub-slice -- doc-16:164-165 + doc-16:137 + doc-16:183-184 + doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action; matches
    # the Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A sub-slice
    # extractor/writer/engine failure-id patterns).
    "finding_reviewer_test_failure_parse_failed",
    # Slice 16 fourth sub-slice -- doc-16:166-167 + doc-16:174-176 + doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action; matches
    # the Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B sub-slice
    # extractor/writer/engine failure-id patterns).
    "governance_finding_persistence_failed",
    # Slice 17 second sub-slice -- doc-17:168-169 + doc-17:204 + doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action; matches
    # the Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B + 4th
    # sub-slice extractor/writer/engine failure-id patterns).
    "recommendation_builder_emission_failed",
    # Slice 17 third sub-slice -- doc-17:170-171 + doc-17:208-210 + doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action;
    # matches the Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B +
    # 4th + Slice 17 2nd sub-slice extractor/writer/engine/builder
    # failure-id patterns).
    "policy_validation_failed",
    # Slice 17 fourth sub-slice -- doc-17:172 + doc-17:182-188 + doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action;
    # matches the Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B +
    # 4th + Slice 17 2nd + 3rd sub-slice extractor/writer/engine/
    # builder/validator failure-id patterns).
    "decision_record_persistence_failed",
    # Slice 17 fifth sub-slice -- doc-17:173-174 + doc-17:159-163 + doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action;
    # matches the Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B +
    # 4th + Slice 17 2nd + 3rd + 4th sub-slice extractor/writer/engine/
    # builder/validator/writer failure-id patterns).
    "replay_requirement_validation_failed",
    # Slice 17 sixth sub-slice -- doc-17:175-177 + doc-17:159-163 + doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action;
    # matches the Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B +
    # 4th + Slice 17 2nd + 3rd + 4th + 5th sub-slice extractor/writer/
    # engine/builder/validator/writer/validator failure-id patterns).
    "consumer_read_api_failed",
    # Slice 18 second sub-slice -- doc-18:111-112 + doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action;
    # matches the Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B +
    # 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th sub-slice failure-id
    # patterns; the SINGLE failure id covers BOTH the corpus loader
    # AND the scenario definition builder per the Slice 17 6th sub-
    # slice `consumer_read_api_failed` precedent).
    "replay_corpus_or_scenario_load_failed",
    # Slice 18 third sub-slice -- doc-18:113 + doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action;
    # matches the Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B +
    # 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18 2nd sub-
    # slice failure-id patterns).
    "summary_replay_failed",
    # Slice 18 fourth sub-slice -- doc-18:114 + doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action;
    # matches the Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B +
    # 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18 2nd + 3rd
    # sub-slice failure-id patterns).
    "event_replay_failed",
    # Slice 18 fifth sub-slice -- doc-18:115 + doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action;
    # matches the Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B +
    # 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18 2nd + 3rd +
    # 4th sub-slice failure-id patterns).
    "metrics_comparator_failed",
    # Slice 18 sixth sub-slice -- doc-18:116-117 + doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action;
    # matches the Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B +
    # 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18 2nd + 3rd +
    # 4th + 5th sub-slice failure-id patterns).
    "counterfactual_result_persistence_failed",
    # Slice 18 seventh sub-slice -- doc-18:117-119 + doc-18:165-166 +
    # doc-14:242-243.
    # See FailureType Literal above for the per-id citation + the REUSED
    # NON-BLOCKING `retry_governance_projection` routing rationale
    # (REUSES the Slice 14 2nd sub-slice action; NOT a new action;
    # matches the Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B +
    # 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18 2nd + 3rd +
    # 4th + 5th + 6th sub-slice failure-id patterns).
    "recommendation_citation_validation_failed",
    # Slice 19 second sub-slice -- doc-19:151 + doc-19:184-194 +
    # doc-14:242-243. See FailureType Literal above for the per-id
    # citation + the REUSED NON-BLOCKING `retry_governance_projection`
    # routing rationale (REUSES the Slice 14 2nd sub-slice action; NOT
    # a new action; matches Slice 15 2nd + 4th + Slice 16 2nd + 3rd-A +
    # 3rd-B + 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18 2nd
    # + 3rd + 4th + 5th + 6th + 7th sub-slice failure-id patterns).
    "governance_snapshot_api_failed",
    # Slice 19 third sub-slice -- doc-19:152 + doc-19:170-171 +
    # doc-19:184-194 + doc-14:242-243. See FailureType Literal above
    # for the per-id citation + the REUSED NON-BLOCKING
    # `retry_governance_projection` routing rationale (REUSES the
    # Slice 14 2nd sub-slice action; NOT a new action; matches Slice
    # 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B + 4th + Slice 17 2nd
    # + 3rd + 4th + 5th + 6th + Slice 18 2nd + 3rd + 4th + 5th + 6th +
    # 7th + Slice 19 2nd sub-slice failure-id patterns).
    "governance_dashboard_view_failed",
    # Slice 19 fourth sub-slice -- doc-19:155 + doc-19:140-142 +
    # doc-19:122-123 + doc-19:184-194 + doc-19:191-192 +
    # doc-14:242-243. See FailureType Literal above for the per-id
    # citation + the REUSED NON-BLOCKING `retry_governance_projection`
    # routing rationale (REUSES the Slice 14 2nd sub-slice action;
    # NOT a new action; matches Slice 15 2nd + 4th + Slice 16 2nd +
    # 3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th +
    # Slice 18 2nd + 3rd + 4th + 5th + 6th + 7th + Slice 19 2nd + 3rd
    # sub-slice failure-id patterns).
    "governance_slack_renderer_failed",
    # Slice 19 fifth sub-slice -- doc-19 step 5 + doc-19:124-127 +
    # doc-19:144-146 + doc-19:184-194 + doc-14:242-243. See FailureType
    # Literal above for the per-id citation + the REUSED NON-BLOCKING
    # `retry_governance_projection` routing rationale (REUSES the
    # Slice 14 2nd sub-slice action; NOT a new action; matches Slice
    # 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B + 4th + Slice 17 2nd
    # + 3rd + 4th + 5th + 6th + Slice 18 2nd + 3rd + 4th + 5th + 6th +
    # 7th + Slice 19 2nd + 3rd + 4th sub-slice failure-id patterns).
    "governance_agent_context_builder_failed",
    # Slice 19 sixth sub-slice -- doc-19:161-162 + doc-19:166-167 +
    # doc-19:184-194 + doc-14:242-243. See FailureType Literal above
    # for the per-id citation + the REUSED NON-BLOCKING
    # `retry_governance_projection` routing rationale (REUSES the
    # Slice 14 2nd sub-slice action; NOT a new action; matches Slice
    # 15 2nd + 4th + Slice 16 2nd + 3rd-A + 3rd-B + 4th + Slice 17 2nd
    # + 3rd + 4th + 5th + 6th + Slice 18 2nd + 3rd + 4th + 5th + 6th +
    # 7th + Slice 19 2nd + 3rd + 4th + 5th sub-slice failure-id
    # patterns).
    "governance_report_artifact_emission_failed",
    "unclassified",
)

ROUTE_ACTIONS: tuple[str, ...] = (
    "retry_dispatch",
    "run_product_repair",
    "run_contract_repair",
    "run_canonicalization_repair",
    "run_workspace_repair",
    "run_commit_hygiene_repair",
    "retry_verifier",
    "retry_merge",
    "retry_sandbox_capture",
    "run_sandbox_cleanup",
    # Slice 14 second sub-slice -- doc-14:192-201 + doc-14:242-243.
    # See RouteAction Literal above for the NON-BLOCKING rationale.
    "retry_governance_projection",
    "quiesce",
    "operator_required",
)

FAILURE_SOURCES: tuple[str, ...] = (
    "dispatcher",
    "workspace_authority",
    "contract",
    "sandbox",
    "verification_graph",
    "merge_queue",
    "regroup",
    "journal",
    "artifact_store",
)

_VOLATILE_FIELD_NAMES = frozenset(
    {
        "attempt",
        "attempt_no",
        "attempt_number",
        "attempt_ordinal",
        "captured_at",
        "completed_at",
        "created",
        "created_at",
        "duration_ms",
        "elapsed_ms",
        "finished_at",
        "idempotency_key",
        "line",
        "line_no",
        "line_number",
        "pid",
        "process_id",
        "raw_stderr",
        "raw_stdout",
        "raw_text",
        "retry",
        "retry_count",
        "retry_ordinal",
        "source_verdict_key",
        "started_at",
        "status",
        "stderr",
        "stderr_body",
        "stdout",
        "stdout_body",
        "timestamp",
        "updated_at",
        "wall_time_ms",
    }
)

_DIRECT_ROUTE_SOURCE_RE = re.compile(
    r"^dag-verify:g(?P<group_idx>\d+):(?P<suffix>initial|retry-\d+|checkpoint-commit)$"
)
_DIRECT_PRODUCT_REPAIR_ROUTES = frozenset({"normal_verify_repair"})
_DIRECT_CONTRACT_PRODUCT_REPAIR_ROUTES = frozenset({"manifest_forbidden_product_cleanup"})

_PATH_FIELD_NAMES = frozenset(
    {
        "allowed_paths",
        "blocked_paths",
        "canonical_path",
        "canonical_root",
        "context_file_paths",
        "forbidden_paths",
        "offending_path",
        "offending_paths",
        "path",
        "paths",
        "readonly_roots",
        "repo_path",
        "repo_paths",
        "repo_root",
        "repo_roots",
        "root",
        "roots",
        "target_path",
        "target_paths",
        "touched_paths",
        "workspace_path",
        "workspace_root",
        "writable_roots",
    }
)

_UNORDERED_FIELD_NAMES = frozenset(
    {
        "allowed_paths",
        "base_commits",
        "contract_ids",
        "evidence_ids",
        "gate_ids",
        "offending_paths",
        "paths",
        "repo_ids",
        "required_evidence_ids",
        "snapshot_ids",
        "source_evidence_ids",
        "target_contract_ids",
        "target_paths",
        "touched_paths",
        "workspace_snapshot_ids",
    }
)

_SCOPED_CONTRACT_PRODUCT_TYPES = frozenset(
    {"outside_allowed_paths", "forbidden_path_touched", "read_only_path_touched"}
)

CLASS_RETRY_BUDGETS: dict[str, int] = {
    "product_defect": 2,
    "contract_compile": 1,
    "contract_violation": 1,
    "stale_projection": 1,
    "worktree_alias": 1,
    "acl_workability": 1,
    "sandbox_allocation": 2,
    "sandbox_binding": 0,
    "sandbox_isolation": 0,
    "sandbox_capture": 1,
    "sandbox_cleanup": 3,
    "commit_hygiene": 1,
    "merge_conflict": 1,
    "runtime_provider": 2,
    "runtime_timeout": 1,
    "runtime_cancelled": 0,
    "runtime_context": 1,
    "runtime_structured_output": 1,
    "dispatcher_internal": 0,
    "verifier_provider": 2,
    "verifier_context": 1,
    "checkpoint_contradiction": 0,
    "regroup_invalid": 0,
    "evidence_corruption": 1,
    "resource_exhausted": 1,
    "operator_required": 0,
    "unknown": 0,
}

_DETERMINISTIC_FAILURE_TYPES = frozenset(
    {
        "contract_invalid_path",
        "contract_scope_conflict",
        "contract_missing_dependency",
        "contract_same_wave_dependency",
        "outside_allowed_paths",
        "forbidden_path_touched",
        "read_only_path_touched",
        "contract_id_mismatch",
        "alias_points_to_noncanonical_root",
        "alias_only_canonical_missing",
        "alias_canonical_divergent",
        "unwritable_runtime_path",
        "workspace_snapshot_stale",
        "runtime_workspace_binding_failed",
        "canonical_path_exposed_to_writer",
        "path_escape_detected",
        "sandbox_index_corrupt",
        "commit_hook_failed",
        "dirty_after_commit",
        "prompt_too_large",
        "context_materialization_failed",
        "context_permission_denied",
        "context_incomplete",
        "malformed_structured_output",
        "idempotency_conflict",
        "verifier_context_stale",
        "checkpoint_after_failed_gate",
        "regroup_dependency_cycle",
        "regroup_write_conflict",
        "artifact_hash_mismatch",
        "payload_digest_mismatch",
        "projection_body_conflict",
        "operator_clearance_required",
        # Slice 13A fifth sub-slice -- doc-13a:273-275 + 276-278.
        # Both are deterministic per the fail-closed rule (the gate
        # cannot approve from preview_only evidence; the proof row
        # without the 4 mandatory fields is structurally invalid).
        "companion_record_unavailable",
        "proof_row_required",
        # Slice 13A sixth sub-slice -- doc-13a:280-282.
        # Both are deterministic per the fail-closed rule (the
        # classifier MUST NOT proceed when a required list field
        # is structurally incomplete -- per doc-13a:280-282
        # "classifier rules fail closed unless their required fields
        # are complete").
        "list_field_incomplete",
        "classifier_rule_blocked",
    }
)

_RETRYABLE_FAILURE_TYPES = frozenset(
    {
        "semantic_verifier_rejected",
        "required_path_missing",
        "alias_points_to_noncanonical_root",
        "alias_only_canonical_missing",
        "alias_canonical_divergent",
        "unwritable_runtime_path",
        "workspace_snapshot_stale",
        "sandbox_clone_failed",
        "sandbox_disk_quota",
        "sandbox_base_snapshot_unavailable",
        "patch_capture_failed",
        "cleanup_failed",
        "commit_hook_failed",
        "dirty_after_commit",
        "stale_base_commit",
        "rebase_conflict",
        "patch_apply_conflict",
        "provider_internal_error",
        "provider_rate_limited",
        "provider_transport_error",
        "process_failed",
        "watchdog_timeout",
        "prompt_too_large",
        "context_materialization_failed",
        "malformed_structured_output",
        "verifier_context_stale",
        "verifier_provider_timeout",
        "verifier_provider_crash",
        "verifier_parse_failed",
        "db_resource_exhausted",
        "disk_resource_exhausted",
        "process_resource_exhausted",
        "provider_quota_exhausted",
        # Slice 14 second sub-slice -- doc-14:194-196 retries the projection
        # idempotently. Both are retryable (transient Git failures e.g.
        # disk full / lock contention resolve on retry; the idempotent
        # writer per doc-14:144-150 ensures a retry with identical inputs
        # is a no-op when the prior write succeeded). They are NOT in
        # `_DETERMINISTIC_FAILURE_TYPES` because the symptom is observer-
        # transient (Git invocation can succeed on a subsequent attempt
        # even with identical inputs).
        "line_provenance_gap",
        "governance_evidence_conflict",
        # Slice 15 second sub-slice -- doc-15:117-136 + doc-15:140-145.
        # Retryable per the Slice 14 precedent (REUSES the same
        # NON-BLOCKING `retry_governance_projection` action). Transient
        # extraction failures (e.g. evidence-set ref temporarily missing
        # before the ingest catches up; completeness state stale before
        # the Slice 13A snapshot adapter refreshes) resolve on retry;
        # the extractor's idempotent typed projection ensures a retry
        # with identical inputs produces the same typed GovernanceMetricValue
        # list. NOT in `_DETERMINISTIC_FAILURE_TYPES` because the symptom
        # is observer-transient (the extractor can succeed on a subsequent
        # attempt even with identical input refs).
        "governance_metric_extraction_failed",
        # Slice 15 fourth sub-slice -- doc-15:133-134 step 6 + doc-15:140-145.
        # Retryable per the Slice 14 + Slice 15 2nd sub-slice precedent
        # (REUSES the same NON-BLOCKING `retry_governance_projection`
        # action). Transient persistence failures (e.g. evidence-set
        # ref enrichment temporarily missing before the ingest catches up;
        # scorecard digest re-computation needed when the metric list
        # changes between projection attempts) resolve on retry; the
        # writer's pure projection ensures a retry with identical inputs
        # produces the same typed GovernanceScorecard + review projection.
        # NOT in `_DETERMINISTIC_FAILURE_TYPES` because the symptom is
        # observer-transient.
        "governance_scorecard_persistence_failed",
        # Slice 16 second sub-slice -- doc-16:155-169 + doc-16:158 + doc-14:242-243.
        # Retryable per the Slice 14 + Slice 15 2nd + 4th sub-slice precedent
        # (REUSES the same NON-BLOCKING `retry_governance_projection`
        # action). Transient rule-emission failures (e.g. primary-evidence
        # ref enrichment temporarily missing before the ingest catches up;
        # rule version supersede policy applied between attempts) resolve
        # on retry; the engine's deterministic idempotency_key via
        # compute_finding_idempotency_key ensures a retry with identical
        # inputs produces an identical key per doc-16:178. NOT in
        # `_DETERMINISTIC_FAILURE_TYPES` because the symptom is
        # observer-transient.
        "finding_rule_emission_failed",
        # Slice 16 third-A sub-slice -- doc-16:164-165 + doc-16:191-192 + doc-14:242-243.
        # Retryable per the Slice 14 + Slice 15 + Slice 16 2nd sub-slice
        # precedent (REUSES the same NON-BLOCKING
        # `retry_governance_projection` action). Transient anchor-parse
        # failures (e.g. journal file being rotated; markdown body
        # transiently truncated mid-write; concurrent process appending
        # while parser is reading) resolve on retry; the
        # `parse_plan_deviation_anchors` surface is a pure function over
        # the journal path + body so a retry with the post-write content
        # produces a typed bundle with anchors populated. NOT in
        # `_DETERMINISTIC_FAILURE_TYPES` because the symptom is
        # observer-transient.
        "finding_plan_deviation_parse_failed",
        # Slice 16 third-B sub-slice -- doc-16:164-165 + doc-16:137 + doc-16:183-184 + doc-14:242-243.
        # Retryable per the Slice 14 + Slice 15 + Slice 16 2nd + 3rd-A
        # sub-slice precedent (REUSES the same NON-BLOCKING
        # `retry_governance_projection` action). Transient anchor-parse
        # failures from EITHER the Slice 13c journal markdown parser OR
        # the Slice 13d JSONL decision-log parser (e.g. journal /
        # decision-log file being rotated; body transiently truncated
        # mid-write; concurrent process appending while parser is
        # reading) resolve on retry; the
        # `parse_reviewer_test_failure_anchors` surface is a pure
        # function over the source paths + bodies so a retry with the
        # post-write content produces a typed bundle with anchors
        # populated. NOT in `_DETERMINISTIC_FAILURE_TYPES` because the
        # symptom is observer-transient.
        "finding_reviewer_test_failure_parse_failed",
        # Slice 16 fourth sub-slice -- doc-16:166-167 + doc-16:174-176 +
        # doc-14:242-243. Retryable per the Slice 14 + Slice 15 + Slice
        # 16 2nd + 3rd-A + 3rd-B sub-slice precedent (REUSES the same
        # NON-BLOCKING `retry_governance_projection` action). Transient
        # persistence failures (e.g. governance review-projection store
        # transient unavailability; transient digest-computation race
        # against an upstream rule-engine concurrent emission; transient
        # bundle-construction validation race) resolve on retry; the
        # `GovernanceFindingWriter.write_findings` +
        # `GovernanceFindingWriter.write_review_projection` surfaces are
        # pure functions over the typed inputs so a retry with the
        # post-emission content produces a typed bundle without raising.
        # NOT in `_DETERMINISTIC_FAILURE_TYPES` because the symptom is
        # observer-transient.
        "governance_finding_persistence_failed",
        # Slice 17 second sub-slice -- doc-17:168-169 + doc-17:204 +
        # doc-14:242-243. Retryable per the Slice 14 + Slice 15 + Slice
        # 16 sub-slice precedent (REUSES the same NON-BLOCKING
        # `retry_governance_projection` action). Transient emission
        # failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
        # construction validation race against a concurrent upstream
        # rule-engine emission updating the finding's idempotency_key /
        # affected_scope / metric_refs; transient typed per-consumer
        # artifact construction failure for the 6 doc-17:107-145
        # consumer-specific BaseModels) resolve on retry; the
        # `RecommendationBuilder.build_recommendations` surface is a
        # pure function over the typed
        # :class:`~iriai_build_v2.execution_control.recommendation_builder.RecommendationBuilderInputs`
        # so a retry with the post-emission content produces a typed
        # :class:`~iriai_build_v2.execution_control.recommendation_builder.RecommendationBuilderResult`
        # without raising. NOT in `_DETERMINISTIC_FAILURE_TYPES`
        # because the symptom is observer-transient.
        "recommendation_builder_emission_failed",
        # Slice 17 third sub-slice -- doc-17:170-171 + doc-17:208-210 +
        # doc-14:242-243. Retryable per the Slice 14 + Slice 15 + Slice
        # 16 + Slice 17 2nd sub-slice precedent (REUSES the same NON-
        # BLOCKING `retry_governance_projection` action). Transient
        # validation failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.policy_validation_interface.ValidationResult`
        # construction validation race against a concurrent upstream
        # recommendation-builder emission updating the recommendation's
        # idempotency_key / proposed_policy_artifact; transient typed
        # per-consumer validation race) resolve on retry; the
        # :class:`~iriai_build_v2.execution_control.policy_validation_interface.PolicyValidationInterface`
        # surface is stateless + a pure function over the typed
        # :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
        # so a retry with the post-emission recommendation produces a
        # typed
        # :class:`~iriai_build_v2.execution_control.policy_validation_interface.ValidationResult`
        # without raising. NOT in `_DETERMINISTIC_FAILURE_TYPES`
        # because the symptom is observer-transient.
        "policy_validation_failed",
        # Slice 17 fourth sub-slice -- doc-17:172 + doc-17:182-188 +
        # doc-14:242-243. Retryable per the Slice 14 + Slice 15 + Slice
        # 16 + Slice 17 2nd + 3rd sub-slice precedent (REUSES the same
        # NON-BLOCKING `retry_governance_projection` action). Transient
        # decision-record persistence failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.decision_record_writer.DecisionWriterResult`
        # construction validation race against a concurrent upstream
        # reviewer-emission updating the decision's recommendation_id /
        # decision Literal / evidence_refs; transient typed digest
        # computation race; transient typed bundle construction race)
        # resolve on retry; the
        # :class:`~iriai_build_v2.execution_control.decision_record_writer.DecisionRecordWriter`
        # surface is stateless + a pure function over the typed
        # :class:`~iriai_build_v2.execution_control.decision_record_writer.DecisionWriterInputs`
        # so a retry with the post-emission decision-set produces a
        # typed
        # :class:`~iriai_build_v2.execution_control.decision_record_writer.DecisionWriterResult`
        # without raising. NOT in `_DETERMINISTIC_FAILURE_TYPES`
        # because the symptom is observer-transient.
        "decision_record_persistence_failed",
        # Slice 17 fifth sub-slice -- doc-17:173-174 + doc-17:159-163 +
        # doc-14:242-243. Retryable per the Slice 14 + Slice 15 + Slice
        # 16 + Slice 17 2nd + 3rd + 4th sub-slice precedent (REUSES the
        # same NON-BLOCKING `retry_governance_projection` action).
        # Transient replay-requirement validation failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.replay_requirement_hook.ReplayRequirementValidationResult`
        # construction validation race against a concurrent upstream
        # recommendation-builder emission updating the recommendation's
        # safe_runtime_action / counterfactual_result_refs fields;
        # transient typed cross-reference shape construction race)
        # resolve on retry; the
        # :class:`~iriai_build_v2.execution_control.replay_requirement_hook.ReplayRequirementValidator`
        # surface is stateless + a pure function over the typed
        # :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
        # so a retry with the post-emission recommendation produces a
        # typed
        # :class:`~iriai_build_v2.execution_control.replay_requirement_hook.ReplayRequirementValidationResult`
        # without raising. NOT in `_DETERMINISTIC_FAILURE_TYPES`
        # because the symptom is observer-transient.
        "replay_requirement_validation_failed",
        # Slice 17 sixth sub-slice -- doc-17:175-177 + doc-17:159-163 +
        # doc-14:242-243. Retryable per the Slice 14 + Slice 15 + Slice
        # 16 + Slice 17 2nd + 3rd + 4th + 5th sub-slice precedent
        # (REUSES the same NON-BLOCKING `retry_governance_projection`
        # action). Transient consumer read-API failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.consumer_read_api.ConsumerReadAPIResult`
        # construction validation race against a concurrent upstream
        # recommendation-builder emission updating the recommendation
        # list's typed shape; transient typed inputs construction race
        # for the typed
        # :class:`~iriai_build_v2.execution_control.consumer_read_api.ConsumerReadAPIInputs`
        # consumer / corpus_id / limit / status_filter fields) resolve
        # on retry; the
        # :class:`~iriai_build_v2.execution_control.consumer_read_api.GovernanceReadAPI`
        # surface is stateless + a pure function over the typed
        # :class:`~iriai_build_v2.execution_control.consumer_read_api.ConsumerReadAPIInputs`
        # so a retry with the post-emission recommendation list produces
        # a typed
        # :class:`~iriai_build_v2.execution_control.consumer_read_api.ConsumerReadAPIResult`
        # without raising. NOT in `_DETERMINISTIC_FAILURE_TYPES`
        # because the symptom is observer-transient.
        "consumer_read_api_failed",
        # Slice 18 second sub-slice -- doc-18:111-112 + doc-14:242-243.
        # Retryable per the Slice 14 + Slice 15 + Slice 16 + Slice 17
        # sub-slice precedent (REUSES the same NON-BLOCKING
        # `retry_governance_projection` action). Transient construction
        # failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_replay_loader.ReplayCorpusLoaderResult`
        # construction validation race against a concurrent upstream
        # evidence-set ingest updating the typed
        # :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
        # ref_id / digest fields; transient typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_replay_loader.ScenarioDefinitionResult`
        # construction validation race against a concurrent upstream
        # corpus-loader emission updating the typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
        # evidence_set_ids / implementation_anchor_ids fields) resolve
        # on retry; the
        # :class:`~iriai_build_v2.execution_control.counterfactual_replay_loader.ReplayCorpusLoader`
        # + :class:`~iriai_build_v2.execution_control.counterfactual_replay_loader.ScenarioDefinitionBuilder`
        # surfaces are stateless + pure functions over the typed
        # inputs so a retry with the post-emission content produces
        # typed results without raising. NOT in
        # `_DETERMINISTIC_FAILURE_TYPES` because the symptom is
        # observer-transient.
        "replay_corpus_or_scenario_load_failed",
        # Slice 18 third sub-slice -- doc-18:113 + doc-14:242-243.
        # Retryable per the Slice 14 + Slice 15 + Slice 16 + Slice 17
        # + Slice 18 2nd sub-slice precedent (REUSES the same NON-
        # BLOCKING `retry_governance_projection` action). Transient
        # summary-replay projection failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_summary_replay.SummaryReplayResult`
        # construction validation race against a concurrent upstream
        # Slice 15 governance metric extractor updating the typed
        # :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
        # definition_name / value / confidence fields; transient typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
        # construction validation race against a concurrent upstream
        # Slice 18 2nd sub-slice corpus-loader / scenario-builder
        # emission updating the typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
        # +
        # :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualScenario`
        # fields) resolve on retry; the
        # :class:`~iriai_build_v2.execution_control.counterfactual_summary_replay.CounterfactualSummaryReplayEngine`
        # surface is stateless + a pure function over the typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_summary_replay.SummaryReplayInputs`
        # so a retry with the post-emission content produces a typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_summary_replay.SummaryReplayResult`
        # without raising. NOT in `_DETERMINISTIC_FAILURE_TYPES`
        # because the symptom is observer-transient.
        "summary_replay_failed",
        # Slice 18 fourth sub-slice -- doc-18:114 + doc-14:242-243.
        # Retryable per the Slice 14 + Slice 15 + Slice 16 + Slice 17
        # + Slice 18 2nd + 3rd sub-slice precedent (REUSES the same
        # NON-BLOCKING `retry_governance_projection` action). Transient
        # construction failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_event_replay.EventReplayResult`
        # construction validation race against a concurrent upstream
        # Slice 10a snapshot emission updating the typed
        # :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ExecutionAttemptSummary`
        # / :class:`GateStatusSummary` / :class:`TypedFailureSummary` /
        # :class:`MergeQueueSummary` / :class:`EvidenceRef` checkpoint
        # fields; transient typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
        # construction validation race against a concurrent upstream
        # Slice 18 2nd sub-slice corpus-loader / scenario-builder
        # emission updating the typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
        # + :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualScenario`
        # fields) resolve on retry; the
        # :class:`~iriai_build_v2.execution_control.counterfactual_event_replay.CounterfactualEventReplayEngine`
        # surface is stateless + a pure function over the typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_event_replay.EventReplayInputs`
        # so a retry with the post-emission content produces a typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_event_replay.EventReplayResult`
        # without raising. NOT in `_DETERMINISTIC_FAILURE_TYPES`
        # because the symptom is observer-transient.
        "event_replay_failed",
        # Slice 18 fifth sub-slice -- doc-18:115 + doc-14:242-243.
        # Retryable per the Slice 14 + Slice 15 + Slice 16 + Slice 17
        # + Slice 18 2nd + 3rd + 4th sub-slice precedent (REUSES the
        # same NON-BLOCKING `retry_governance_projection` action).
        # Transient comparator-projection failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_metrics_comparator.MetricsComparatorResult`
        # construction validation race against a concurrent upstream
        # Slice 15 governance metric extractor updating the typed
        # :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
        # definition_name / value / confidence / evidence_refs
        # baseline fields; transient typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_metrics_comparator.MetricsAxisDelta`
        # construction validation race against a concurrent upstream
        # Slice 18 3rd or 4th sub-slice replay engine emission
        # updating the typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
        # estimated_delta_hours / estimated_delta_repair_cycles /
        # estimated_delta_commit_failures / estimated_risk_change /
        # confidence / policy_provenance_refs fields) resolve on
        # retry; the
        # :class:`~iriai_build_v2.execution_control.counterfactual_metrics_comparator.CounterfactualMetricsComparator`
        # surface is stateless + a pure function over the typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_metrics_comparator.MetricsComparatorInputs`
        # so a retry with the post-emission content produces a typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_metrics_comparator.MetricsComparatorResult`
        # without raising. NOT in `_DETERMINISTIC_FAILURE_TYPES`
        # because the symptom is observer-transient.
        "metrics_comparator_failed",
        # Slice 18 sixth sub-slice -- doc-18:116-117 + doc-14:242-243.
        # Retryable per the Slice 14 + Slice 15 + Slice 16 + Slice 17
        # + Slice 18 2nd + 3rd + 4th + 5th sub-slice precedent (REUSES
        # the same NON-BLOCKING `retry_governance_projection` action).
        # Transient writer-projection failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_result_writer.CounterfactualResultWriterResult`
        # construction validation race against a concurrent upstream
        # Slice 18 3rd or 4th sub-slice replay engine emission
        # updating the typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
        # result_id / result_version / scenario_id / corpus_id /
        # assumptions / validity_limits / policy_provenance_refs /
        # safety_guard_class / estimated_delta_* /
        # estimated_risk_change / confidence / invalidated_by /
        # supporting_finding_ids / recommended_next_step fields;
        # transient digest computation race against a concurrent
        # upstream Slice 18 5th sub-slice metrics-comparator emission
        # updating the typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_metrics_comparator.MetricsComparatorResult`
        # axis_deltas / idempotency_key / result_id /
        # scenario_result_id / emitted_at / invalidated_axes /
        # overall_confidence fields) resolve on retry; the
        # :class:`~iriai_build_v2.execution_control.counterfactual_result_writer.CounterfactualResultWriter`
        # surface is stateless + a pure function over the typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_result_writer.CounterfactualResultWriterInputs`
        # so a retry with the post-emission content produces a typed
        # :class:`~iriai_build_v2.execution_control.counterfactual_result_writer.CounterfactualResultWriterResult`
        # without raising. NOT in `_DETERMINISTIC_FAILURE_TYPES`
        # because the symptom is observer-transient.
        "counterfactual_result_persistence_failed",
        # Slice 18 seventh sub-slice -- doc-18:117-119 + doc-18:165-166
        # + doc-14:242-243. Retryable per the Slice 14 + Slice 15 +
        # Slice 16 + Slice 17 + Slice 18 2nd + 3rd + 4th + 5th + 6th
        # sub-slice precedent (REUSES the same NON-BLOCKING
        # `retry_governance_projection` action). Transient
        # citation-validation failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.recommendation_citation_hook.CitationSufficiencyResult`
        # construction validation race against a concurrent upstream
        # Slice 17 recommendation-builder emission updating the typed
        # :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
        # safe_runtime_action / counterfactual_result_refs / status
        # fields; transient typed
        # :class:`~iriai_build_v2.execution_control.recommendation_citation_hook.CitationGap`
        # construction validation race against a concurrent upstream
        # Slice 18 1st sub-slice
        # :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
        # emission updating the typed result_id field) resolve on
        # retry; the
        # :class:`~iriai_build_v2.execution_control.recommendation_citation_hook.RecommendationCitationValidator`
        # surface is stateless + a pure function over the typed
        # :class:`~iriai_build_v2.execution_control.recommendation_citation_hook.RecommendationCitationHookInputs`
        # so a retry with the post-emission content produces a typed
        # :class:`~iriai_build_v2.execution_control.recommendation_citation_hook.CitationSufficiencyResult`
        # without raising. NOT in `_DETERMINISTIC_FAILURE_TYPES`
        # because the symptom is observer-transient.
        "recommendation_citation_validation_failed",
        # Slice 19 second sub-slice -- doc-19:151 + doc-19:184-194 +
        # doc-14:242-243. Retryable per the Slice 14 + Slice 15 +
        # Slice 16 + Slice 17 + Slice 18 sub-slice precedent (REUSES
        # the same NON-BLOCKING `retry_governance_projection` action).
        # Transient snapshot-API failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.governance_agent.GovernanceSnapshot`
        # construction validation race against a concurrent upstream
        # Slice 16/17/18 row update; transient
        # :class:`~iriai_build_v2.execution_control.governance_snapshot_api.SnapshotAPIGap`
        # informational gap firing when corpus is stale) resolve on
        # retry; the
        # :class:`~iriai_build_v2.execution_control.governance_snapshot_api.GovernanceSnapshotAPI`
        # surface is stateless + a pure function over the typed
        # :class:`~iriai_build_v2.execution_control.governance_snapshot_api.SnapshotAPIInputs`
        # + :class:`~iriai_build_v2.execution_control.governance_snapshot_api.SnapshotAPICorpus`
        # so a retry with the post-update content produces a typed
        # :class:`~iriai_build_v2.execution_control.governance_snapshot_api.SnapshotAPIResult`
        # without raising. NOT in `_DETERMINISTIC_FAILURE_TYPES`
        # because the symptom is observer-transient.
        "governance_snapshot_api_failed",
        # Slice 19 third sub-slice -- doc-19:152 + doc-19:170-171 +
        # doc-19:184-194 + doc-14:242-243. Retryable per the Slice 14
        # + Slice 15 + Slice 16 + Slice 17 + Slice 18 + Slice 19 2nd
        # sub-slice precedent (REUSES the same NON-BLOCKING
        # `retry_governance_projection` action). Transient dashboard-
        # view failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.governance_dashboard_view.DashboardViewPayload`
        # construction validation race against a concurrent upstream
        # Slice 19 2nd sub-slice
        # :class:`~iriai_build_v2.execution_control.governance_snapshot_api.SnapshotAPIResult`
        # update; transient
        # :class:`~iriai_build_v2.execution_control.governance_dashboard_view.DashboardViewGap`
        # informational gap firing when upstream snapshot is missing)
        # resolve on retry; the
        # :class:`~iriai_build_v2.execution_control.governance_dashboard_view.GovernanceDashboardView`
        # surface is stateless + a pure function over the typed
        # :class:`~iriai_build_v2.execution_control.governance_dashboard_view.DashboardViewInputs`
        # so a retry with the post-update content produces a typed
        # :class:`~iriai_build_v2.execution_control.governance_dashboard_view.DashboardViewResult`
        # without raising. NOT in `_DETERMINISTIC_FAILURE_TYPES`
        # because the symptom is observer-transient.
        "governance_dashboard_view_failed",
        # Slice 19 fourth sub-slice -- doc-19:155 + doc-19:140-142 +
        # doc-19:122-123 + doc-19:184-194 + doc-19:191-192 +
        # doc-14:242-243. Retryable per the Slice 14 + Slice 15 +
        # Slice 16 + Slice 17 + Slice 18 + Slice 19 2nd + 3rd sub-
        # slice precedent (REUSES the same NON-BLOCKING
        # `retry_governance_projection` action). Transient Slack-
        # renderer failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.governance_slack_renderer.SlackRenderPayload`
        # construction validation race against a concurrent upstream
        # Slice 19 2nd sub-slice
        # :class:`~iriai_build_v2.execution_control.governance_snapshot_api.SnapshotAPIResult`
        # update; transient
        # :class:`~iriai_build_v2.execution_control.governance_slack_renderer.SlackRenderGap`
        # informational gap firing when upstream snapshot is missing
        # or 40 KB Block Kit budget is exceeded or Slack delivery
        # failure per doc-19:191-192) resolve on retry; the
        # :class:`~iriai_build_v2.execution_control.governance_slack_renderer.GovernanceSlackRenderer`
        # surface is stateless + a pure function over the typed
        # :class:`~iriai_build_v2.execution_control.governance_slack_renderer.SlackRenderInputs`
        # so a retry with the post-update content produces a typed
        # :class:`~iriai_build_v2.execution_control.governance_slack_renderer.SlackRenderResult`
        # without raising. NOT in `_DETERMINISTIC_FAILURE_TYPES`
        # because the symptom is observer-transient.
        "governance_slack_renderer_failed",
        # Slice 19 fifth sub-slice -- doc-19 step 5 + doc-19:124-127 +
        # doc-19:144-146 + doc-19:184-194 + doc-14:242-243. Retryable
        # per the Slice 14 + Slice 15 + Slice 16 + Slice 17 + Slice 18
        # + Slice 19 2nd + 3rd + 4th sub-slice precedent (REUSES the
        # same NON-BLOCKING `retry_governance_projection` action).
        # Transient agent-context-builder failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.governance_agent_context_builder.GovernanceAgentContextBuilder`
        # construction validation race against a concurrent upstream
        # Slice 19 2nd sub-slice
        # :class:`~iriai_build_v2.execution_control.governance_snapshot_api.SnapshotAPIResult`
        # update; transient
        # :class:`~iriai_build_v2.execution_control.governance_agent_context_builder.AgentContextBuilderGap`
        # informational gap firing when upstream snapshot is missing
        # or 20 000 char prompt budget cannot be honoured or required
        # line provenance is missing per doc-19:188-189) resolve on
        # retry; the
        # :class:`~iriai_build_v2.execution_control.governance_agent_context_builder.GovernanceAgentContextBuilder`
        # surface is stateless + a pure function over the typed
        # :class:`~iriai_build_v2.execution_control.governance_agent_context_builder.AgentContextBuilderInputs`
        # so a retry with the post-update content produces a typed
        # :class:`~iriai_build_v2.execution_control.governance_agent_context_builder.AgentContextBuilderResult`
        # without raising. NOT in `_DETERMINISTIC_FAILURE_TYPES`
        # because the symptom is observer-transient.
        "governance_agent_context_builder_failed",
        # Slice 19 sixth sub-slice -- doc-19:161-162 + doc-19:166-167 +
        # doc-19:184-194 + doc-14:242-243. Retryable per the Slice 14 +
        # Slice 15 + Slice 16 + Slice 17 + Slice 18 + Slice 19 2nd +
        # 3rd + 4th + 5th sub-slice precedent (REUSES the same
        # NON-BLOCKING `retry_governance_projection` action). Transient
        # report-artifact emission failures (e.g. typed
        # :class:`~iriai_build_v2.execution_control.governance_report_artifact.GovernanceReportArtifact`
        # construction validation race against a concurrent upstream
        # Slice 19 2nd sub-slice
        # :class:`~iriai_build_v2.execution_control.governance_snapshot_api.SnapshotAPIResult`
        # update; transient
        # :class:`~iriai_build_v2.execution_control.governance_report_artifact.ReportArtifactGap`
        # informational gap firing when upstream snapshot is missing
        # or corpus_id is empty/whitespace-only or governance snapshot
        # is stale per doc-19:186-187) resolve on retry; the
        # :class:`~iriai_build_v2.execution_control.governance_report_artifact.GovernanceReportArtifactEmitter`
        # surface is stateless + a pure function over the typed
        # :class:`~iriai_build_v2.execution_control.governance_report_artifact.ReportArtifactInputs`
        # so a retry with the post-update content produces a typed
        # :class:`~iriai_build_v2.execution_control.governance_report_artifact.ReportArtifactResult`
        # without raising. NOT in `_DETERMINISTIC_FAILURE_TYPES`
        # because the symptom is observer-transient.
        "governance_report_artifact_emission_failed",
    }
)

_OPERATOR_REQUIRED_FAILURE_TYPES = frozenset(
    {"context_permission_denied", "operator_clearance_required"}
)

_RETRY_ACTIONS = frozenset(
    {
        "retry_dispatch",
        "retry_verifier",
        "retry_merge",
        "retry_sandbox_capture",
        # Slice 14 second sub-slice -- doc-14:192-201 + doc-14:242-243.
        # `retry_governance_projection` is a sibling retry action used by
        # the 2 new typed failure ids `line_provenance_gap` +
        # `governance_evidence_conflict` under `evidence_corruption`.
        # Per doc-14:242-243 the action is NON-BLOCKING: it does NOT
        # quiesce the executor when the budget is exhausted (the typed
        # ids carry their own budget per `evidence_corruption` class
        # which is 1; budget-exhausted re-route is handled in the
        # writer's own caller surface via the typed
        # `CommitProvenanceGapFinding` -- the governance projection job
        # logs the finding and continues without blocking checkpointing
        # / merge queue / resume).
        "retry_governance_projection",
    }
)


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def stable_digest(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _normalize_path(value: str) -> str:
    raw = value.replace("\\", "/").strip()
    if not raw:
        return raw
    raw = re.sub(r"^[A-Za-z]:", "", raw)
    parts: list[str] = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            else:
                parts.append(part)
            continue
        parts.append(part)
    normalized = str(PurePosixPath(*parts)) if parts else "."
    return f"/{normalized}" if raw.startswith("/") and normalized != "." else normalized


def _looks_like_path_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _PATH_FIELD_NAMES or lowered.endswith("_path") or lowered.endswith("_paths")


def _normalize_scalar_for_signature(key: str, value: Any) -> Any:
    if isinstance(value, str) and _looks_like_path_key(key):
        return _normalize_path(value)
    return value


def _canonicalize_for_signature(key: str, value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_child_key, child_value in value.items():
            child_key = str(raw_child_key)
            if child_key in _VOLATILE_FIELD_NAMES:
                continue
            result[child_key] = _canonicalize_for_signature(child_key, child_value)
        return {child_key: result[child_key] for child_key in sorted(result)}

    if isinstance(value, (list, tuple, set, frozenset)):
        normalized_items = [
            _canonicalize_for_signature(key, item)
            for item in value
        ]
        if key in _UNORDERED_FIELD_NAMES or _looks_like_path_key(key):
            return sorted(normalized_items, key=_stable_json)
        return normalized_items

    return _normalize_scalar_for_signature(key, value)


def _payload_value(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _str_list(value: Any, *, path: bool = False) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    result = []
    for item in values:
        if item is None:
            continue
        text = str(item)
        result.append(_normalize_path(text) if path else text)
    return sorted(set(result))


def _int_list(value: Any) -> list[int]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    result: list[int] = []
    for item in values:
        if item is None:
            continue
        result.append(int(item))
    return sorted(set(result))


class FailureRouterError(RuntimeError):
    pass


class IdempotencyConflict(FailureRouterError):
    def __init__(
        self,
        idempotency_key: str,
        existing_digest: str,
        incoming_digest: str,
    ) -> None:
        super().__init__(
            "idempotency conflict for "
            f"{idempotency_key}: {existing_digest} != {incoming_digest}"
        )
        self.idempotency_key = idempotency_key
        self.existing_digest = existing_digest
        self.incoming_digest = incoming_digest


class UnknownFailurePolicyError(FailureRouterError):
    pass


class _RouterModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class FailureObservation(_RouterModel):
    feature_id: str
    dag_sha256: str
    group_idx: int | None = None
    task_id: str | None = None
    attempt_id: int | None = None
    source: FailureSource
    failure_class: FailureClass
    failure_type: FailureType
    severity: FailureSeverity = "error"
    deterministic: bool
    retryable: bool
    operator_required: bool = False
    evidence_ids: list[int]
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def _sort_evidence_ids(cls, value: Any) -> list[int]:
        if value is None:
            return []
        return sorted({int(item) for item in value})


class FailureTypePolicy(_RouterModel):
    failure_class: FailureClass
    failure_type: FailureType
    severity: FailureSeverity = "error"
    deterministic: bool
    retryable: bool
    operator_required: bool = False


class FailureRoutePolicy(_RouterModel):
    failure_class: FailureClass
    failure_type: FailureType
    action: RouteAction
    budget: int
    reason: str
    repair_kind: str | None = None
    allow_product_repair: bool = False
    requires_scoped_product_repair: bool = False


class RouteDecision(_RouterModel):
    failure_id: int
    route_decision_id: int | None
    action: RouteAction
    budget_remaining: int
    budget_exhausted: bool = False
    reason: str
    required_evidence_ids: list[int]
    signature_hash: str
    idempotency_key: str
    repair_scope: dict[str, Any] = Field(default_factory=dict)
    budget_key: str = ""
    reservation_ordinal: int = 0
    started: bool = False

    @field_validator("required_evidence_ids", mode="before")
    @classmethod
    def _sort_required_evidence_ids(cls, value: Any) -> list[int]:
        if value is None:
            return []
        return sorted({int(item) for item in value})


class RetryBudgetState(_RouterModel):
    budget_key: str
    feature_id: str
    failure_class: FailureClass
    failure_type: FailureType
    signature_hash: str
    max_attempts: int
    reserved_attempts: int = 0
    completed_attempts: int = 0
    last_failure_id: int | None = None
    last_route_decision_id: int | None = None

    @property
    def budget_remaining(self) -> int:
        return max(self.max_attempts - self.reserved_attempts, 0)

    @property
    def exhausted(self) -> bool:
        return self.budget_remaining <= 0


class FailureRecord(_RouterModel):
    failure_id: int | None = None
    observation: FailureObservation
    policy: FailureTypePolicy
    signature_hash: str
    idempotency_key: str
    input_digest: str
    occurrence_count: int = 1


class RouteRecord(_RouterModel):
    route_decision_id: int | None = None
    decision: RouteDecision
    input_digest: str
    status: Literal["started", "succeeded", "failed"] = "started"
    produced_failure_id: int | None = None


class FailureRouterPort(Protocol):
    def record_failure(self, record: FailureRecord) -> FailureRecord: ...
    def get_failure(self, failure_id: int) -> FailureRecord | None: ...
    def get_failure_by_key(self, idempotency_key: str) -> FailureRecord | None: ...
    def get_budget(self, budget_key: str) -> RetryBudgetState | None: ...
    def reserve_budget(
        self,
        *,
        budget_key: str,
        feature_id: str,
        failure_class: FailureClass,
        failure_type: FailureType,
        signature_hash: str,
        max_attempts: int,
        failure_id: int,
    ) -> RetryBudgetState: ...
    def get_route_by_key(self, idempotency_key: str) -> RouteRecord | None: ...
    def record_route_started(
        self,
        decision: RouteDecision,
        input_digest: str,
        *,
        budget_reservation: dict[str, Any] | None = None,
    ) -> RouteRecord: ...
    def mark_route_finished(
        self,
        decision: RouteDecision,
        *,
        succeeded: bool,
        produced_failure_id: int | None = None,
    ) -> None: ...


class InMemoryFailureRouterPort:
    def __init__(self) -> None:
        self._next_failure_id = 1
        self._next_route_decision_id = 1
        self.failures: dict[int, FailureRecord] = {}
        self.failures_by_key: dict[str, int] = {}
        self.budgets: dict[str, RetryBudgetState] = {}
        self.routes: dict[int, RouteRecord] = {}
        self.routes_by_key: dict[str, int] = {}

    def record_failure(self, record: FailureRecord) -> FailureRecord:
        existing_id = self.failures_by_key.get(record.idempotency_key)
        if existing_id is not None:
            existing = self.failures[existing_id]
            if existing.input_digest != record.input_digest:
                raise IdempotencyConflict(
                    record.idempotency_key,
                    existing.input_digest,
                    record.input_digest,
                )
            updated = existing.model_copy(
                update={"occurrence_count": existing.occurrence_count + 1}
            )
            self.failures[existing_id] = updated
            return updated

        failure_id = self._next_failure_id
        self._next_failure_id += 1
        stored = record.model_copy(update={"failure_id": failure_id})
        self.failures[failure_id] = stored
        self.failures_by_key[stored.idempotency_key] = failure_id
        return stored

    def get_failure(self, failure_id: int) -> FailureRecord | None:
        return self.failures.get(failure_id)

    def get_failure_by_key(self, idempotency_key: str) -> FailureRecord | None:
        failure_id = self.failures_by_key.get(idempotency_key)
        return None if failure_id is None else self.failures[failure_id]

    def get_budget(self, budget_key: str) -> RetryBudgetState | None:
        return self.budgets.get(budget_key)

    def reserve_budget(
        self,
        *,
        budget_key: str,
        feature_id: str,
        failure_class: FailureClass,
        failure_type: FailureType,
        signature_hash: str,
        max_attempts: int,
        failure_id: int,
    ) -> RetryBudgetState:
        state = self.budgets.get(budget_key)
        if state is None:
            state = RetryBudgetState(
                budget_key=budget_key,
                feature_id=feature_id,
                failure_class=failure_class,
                failure_type=failure_type,
                signature_hash=signature_hash,
                max_attempts=max_attempts,
            )
        if state.reserved_attempts >= state.max_attempts:
            updated = state.model_copy(update={"last_failure_id": failure_id})
            self.budgets[budget_key] = updated
            return updated

        updated = state.model_copy(
            update={
                "reserved_attempts": state.reserved_attempts + 1,
                "last_failure_id": failure_id,
            }
        )
        self.budgets[budget_key] = updated
        return updated

    def get_route_by_key(self, idempotency_key: str) -> RouteRecord | None:
        route_id = self.routes_by_key.get(idempotency_key)
        return None if route_id is None else self.routes[route_id]

    def record_route_started(
        self,
        decision: RouteDecision,
        input_digest: str,
        *,
        budget_reservation: dict[str, Any] | None = None,
    ) -> RouteRecord:
        existing_id = self.routes_by_key.get(decision.idempotency_key)
        if existing_id is not None:
            existing = self.routes[existing_id]
            if existing.input_digest != input_digest and not _route_replay_compatible(
                existing.decision,
                decision,
            ):
                raise IdempotencyConflict(
                    decision.idempotency_key,
                    existing.input_digest,
                    input_digest,
                )
            return existing

        stored_decision = decision
        stored_input_digest = input_digest
        if budget_reservation is not None:
            budget_key = str(budget_reservation["budget_key"])
            max_attempts = int(budget_reservation["max_attempts"])
            failure_id = int(budget_reservation["failure_id"])
            state = self.budgets.get(budget_key)
            if state is None:
                state = RetryBudgetState(
                    budget_key=budget_key,
                    feature_id=str(budget_reservation["feature_id"]),
                    failure_class=budget_reservation["failure_class"],
                    failure_type=budget_reservation["failure_type"],
                    signature_hash=str(budget_reservation["signature_hash"]),
                    max_attempts=max_attempts,
                )
            if state.reserved_attempts >= state.max_attempts:
                ordinal = state.reserved_attempts
                # Slice 14 second sub-slice -- doc-14:242-243 NON-BLOCKING
                # exception: `retry_governance_projection` MUST NOT fall
                # back to `quiesce` when budget is exhausted -- per
                # doc-14:242-243 "Governance provenance projection
                # failures never block `dag-group:*` checkpointing, merge
                # queue integration, or resume". The action stays as
                # `retry_governance_projection` with `budget_exhausted=True`;
                # the post-checkpoint governance job caller observes the
                # budget flag and gives up gracefully (logging the typed
                # `CommitProvenanceGapFinding`) without quiescing the
                # executor.
                rewritten_action: RouteAction
                if decision.action == "retry_governance_projection":
                    rewritten_action = "retry_governance_projection"
                    exhausted_reason = (
                        "retry budget exhausted for "
                        f"{budget_reservation['failure_class']}/"
                        f"{budget_reservation['failure_type']} "
                        "(non-blocking per doc-14:242-243; governance projection observer)"
                    )
                else:
                    rewritten_action = "quiesce"
                    exhausted_reason = (
                        "retry budget exhausted for "
                        f"{budget_reservation['failure_class']}/"
                        f"{budget_reservation['failure_type']}"
                    )
                idempotency_key = _route_idempotency_key_from_parts(
                    feature_id=str(budget_reservation["feature_id"]),
                    failure_id=failure_id,
                    signature_hash=str(budget_reservation["signature_hash"]),
                    action=rewritten_action,
                    reservation_ordinal=ordinal,
                )
                stored_decision = decision.model_copy(
                    update={
                        "action": rewritten_action,
                        "budget_remaining": 0,
                        "budget_exhausted": True,
                        "idempotency_key": idempotency_key,
                        "reservation_ordinal": ordinal,
                        "reason": exhausted_reason,
                    }
                )
                self.budgets[budget_key] = state.model_copy(
                    update={"last_failure_id": failure_id}
                )
            else:
                ordinal = state.reserved_attempts + 1
                state = state.model_copy(
                    update={
                        "reserved_attempts": ordinal,
                        "last_failure_id": failure_id,
                    }
                )
                self.budgets[budget_key] = state
                idempotency_key = _route_idempotency_key_from_parts(
                    feature_id=str(budget_reservation["feature_id"]),
                    failure_id=failure_id,
                    signature_hash=str(budget_reservation["signature_hash"]),
                    action=decision.action,
                    reservation_ordinal=ordinal,
                )
                stored_decision = decision.model_copy(
                    update={
                        "budget_remaining": state.budget_remaining,
                        "idempotency_key": idempotency_key,
                        "reservation_ordinal": ordinal,
                    }
                )
            stored_input_digest = _route_input_digest(stored_decision)
            existing_id = self.routes_by_key.get(stored_decision.idempotency_key)
            if existing_id is not None:
                existing = self.routes[existing_id]
                if existing.input_digest != stored_input_digest:
                    raise IdempotencyConflict(
                        stored_decision.idempotency_key,
                        existing.input_digest,
                        stored_input_digest,
                    )
                return existing

        route_decision_id = self._next_route_decision_id
        self._next_route_decision_id += 1
        stored_decision = stored_decision.model_copy(
            update={
                "route_decision_id": route_decision_id,
                "started": True,
            }
        )
        record = RouteRecord(
            route_decision_id=route_decision_id,
            decision=stored_decision,
            input_digest=stored_input_digest,
        )
        self.routes[route_decision_id] = record
        self.routes_by_key[stored_decision.idempotency_key] = route_decision_id
        if decision.idempotency_key != stored_decision.idempotency_key:
            self.routes_by_key[decision.idempotency_key] = route_decision_id
        return record

    def mark_route_finished(
        self,
        decision: RouteDecision,
        *,
        succeeded: bool,
        produced_failure_id: int | None = None,
    ) -> None:
        if decision.route_decision_id is None:
            return
        record = self.routes.get(decision.route_decision_id)
        if record is None:
            return
        status: Literal["started", "succeeded", "failed"] = (
            "succeeded" if succeeded else "failed"
        )
        self.routes[decision.route_decision_id] = record.model_copy(
            update={"status": status, "produced_failure_id": produced_failure_id}
        )
        budget = self.budgets.get(decision.budget_key)
        if budget is not None and record.status == "started":
            self.budgets[decision.budget_key] = budget.model_copy(
                update={
                    "completed_attempts": budget.completed_attempts + 1,
                    "last_route_decision_id": decision.route_decision_id,
                }
            )


def _route(
    failure_class: FailureClass,
    failure_type: FailureType,
    action: RouteAction,
    reason: str,
    *,
    budget: int | None = None,
    severity: FailureSeverity = "error",
    deterministic: bool | None = None,
    retryable: bool | None = None,
    operator_required: bool | None = None,
    repair_kind: str | None = None,
) -> tuple[FailureTypePolicy, FailureRoutePolicy]:
    resolved_budget = CLASS_RETRY_BUDGETS[failure_class] if budget is None else budget
    resolved_operator_required = (
        action == "operator_required" or failure_type in _OPERATOR_REQUIRED_FAILURE_TYPES
        if operator_required is None
        else operator_required
    )
    resolved_deterministic = (
        failure_type in _DETERMINISTIC_FAILURE_TYPES
        if deterministic is None
        else deterministic
    )
    resolved_retryable = (
        failure_type in _RETRYABLE_FAILURE_TYPES
        if retryable is None
        else retryable
    )
    allow_product = action == "run_product_repair" and (
        failure_class == "product_defect"
        or (
            failure_class == "contract_violation"
            and failure_type in _SCOPED_CONTRACT_PRODUCT_TYPES
        )
    )
    policy = FailureTypePolicy(
        failure_class=failure_class,
        failure_type=failure_type,
        severity=severity,
        deterministic=resolved_deterministic,
        retryable=resolved_retryable,
        operator_required=resolved_operator_required,
    )
    route_policy = FailureRoutePolicy(
        failure_class=failure_class,
        failure_type=failure_type,
        action=action,
        budget=resolved_budget,
        reason=reason,
        repair_kind=repair_kind,
        allow_product_repair=allow_product,
        requires_scoped_product_repair=(
            action == "run_product_repair" and failure_class == "contract_violation"
        ),
    )
    return policy, route_policy


_ROUTE_ROWS = (
    _route(
        "product_defect",
        "semantic_verifier_rejected",
        "run_product_repair",
        "semantic verifier rejected product behavior",
        repair_kind="product",
    ),
    _route(
        "product_defect",
        "required_path_missing",
        "run_product_repair",
        "required product path missing",
        repair_kind="product",
    ),
    _route(
        "contract_compile",
        "contract_invalid_path",
        "run_contract_repair",
        "contract contains an invalid path",
        repair_kind="contract",
    ),
    _route(
        "contract_compile",
        "contract_scope_conflict",
        "quiesce",
        "contract scope conflict must be resolved before dispatch",
    ),
    _route(
        "contract_compile",
        "contract_missing_dependency",
        "quiesce",
        "contract dependency is missing",
    ),
    _route(
        "contract_compile",
        "contract_same_wave_dependency",
        "quiesce",
        "contract depends on a same-wave task",
    ),
    _route(
        "contract_violation",
        "outside_allowed_paths",
        "run_product_repair",
        "product patch touched paths outside the contract",
        repair_kind="product",
    ),
    _route(
        "contract_violation",
        "forbidden_path_touched",
        "run_product_repair",
        "product patch touched forbidden paths",
        repair_kind="product",
    ),
    _route(
        "contract_violation",
        "read_only_path_touched",
        "run_product_repair",
        "product patch touched read-only paths",
        repair_kind="product",
    ),
    _route(
        "contract_violation",
        "contract_id_mismatch",
        "quiesce",
        "contract id mismatch is not repairable by product edits",
    ),
    _route(
        "stale_projection",
        "verifier_context_stale",
        "retry_verifier",
        "verifier projection is stale",
    ),
    _route(
        "stale_projection",
        "workspace_snapshot_stale",
        "run_workspace_repair",
        "workspace snapshot projection is stale",
        repair_kind="workspace",
    ),
    _route(
        "worktree_alias",
        "alias_points_to_noncanonical_root",
        "run_canonicalization_repair",
        "worktree alias points to a noncanonical root",
        repair_kind="canonicalization",
    ),
    _route(
        "worktree_alias",
        "alias_only_canonical_missing",
        "run_canonicalization_repair",
        "canonical root is missing for an alias",
        repair_kind="canonicalization",
    ),
    _route(
        "worktree_alias",
        "alias_canonical_divergent",
        "run_canonicalization_repair",
        "canonical and alias worktrees diverged",
        repair_kind="canonicalization",
    ),
    _route(
        "acl_workability",
        "unwritable_runtime_path",
        "run_workspace_repair",
        "runtime path is not writable",
        repair_kind="workspace",
    ),
    _route(
        "sandbox_allocation",
        "sandbox_clone_failed",
        "retry_dispatch",
        "sandbox clone failed",
    ),
    _route(
        "sandbox_allocation",
        "sandbox_disk_quota",
        "quiesce",
        "sandbox disk quota is exhausted",
    ),
    _route(
        "sandbox_allocation",
        "sandbox_base_snapshot_unavailable",
        "retry_dispatch",
        "sandbox base snapshot is unavailable",
    ),
    _route(
        "sandbox_binding",
        "runtime_workspace_binding_failed",
        "quiesce",
        "runtime workspace binding failed",
    ),
    _route(
        "sandbox_isolation",
        "canonical_path_exposed_to_writer",
        "quiesce",
        "canonical path was exposed to writer runtime",
    ),
    _route(
        "sandbox_isolation",
        "path_escape_detected",
        "quiesce",
        "sandbox path escape detected",
    ),
    _route(
        "sandbox_capture",
        "patch_capture_failed",
        "retry_sandbox_capture",
        "patch capture failed",
    ),
    _route(
        "sandbox_capture",
        "sandbox_index_corrupt",
        "quiesce",
        "sandbox index is corrupt",
    ),
    _route(
        "sandbox_cleanup",
        "cleanup_failed",
        "run_sandbox_cleanup",
        "sandbox cleanup failed",
        repair_kind="sandbox_cleanup",
    ),
    _route(
        "commit_hygiene",
        "commit_hook_failed",
        "run_commit_hygiene_repair",
        "commit hook failed",
        repair_kind="commit_hygiene",
    ),
    _route(
        "commit_hygiene",
        "dirty_after_commit",
        "run_commit_hygiene_repair",
        "commit left dirty worktree state",
        repair_kind="commit_hygiene",
    ),
    _route(
        "merge_conflict",
        "stale_base_commit",
        "retry_merge",
        "merge base commit is stale",
    ),
    _route(
        "merge_conflict",
        "rebase_conflict",
        "retry_merge",
        "rebase conflict requires merge retry",
    ),
    _route(
        "merge_conflict",
        "patch_apply_conflict",
        "retry_merge",
        "patch apply conflict requires merge retry",
    ),
    _route(
        "runtime_provider",
        "provider_internal_error",
        "retry_dispatch",
        "runtime provider internal error",
    ),
    _route(
        "runtime_provider",
        "provider_rate_limited",
        "retry_dispatch",
        "runtime provider rate limited",
    ),
    _route(
        "runtime_provider",
        "provider_transport_error",
        "retry_dispatch",
        "runtime provider transport error",
    ),
    _route(
        "runtime_provider",
        "process_failed",
        "retry_dispatch",
        "runtime process failed before durable completion",
    ),
    _route(
        "runtime_timeout",
        "watchdog_timeout",
        "retry_dispatch",
        "runtime watchdog timed out",
    ),
    _route(
        "runtime_cancelled",
        "runtime_cancelled",
        "quiesce",
        "runtime cancellation should quiesce the route",
    ),
    _route(
        "runtime_context",
        "prompt_too_large",
        "retry_dispatch",
        "runtime prompt exceeded the context budget",
    ),
    _route(
        "runtime_context",
        "context_materialization_failed",
        "quiesce",
        "runtime context materialization failed",
    ),
    _route(
        "runtime_context",
        "context_permission_denied",
        "operator_required",
        "runtime context requires operator clearance",
    ),
    # Slice 13A fourth sub-slice -- doc-13a:269-272 + doc-13a:307-310.
    # Per doc-13a:269-272 "If `required_complete_for` cannot be satisfied,
    # dispatch records `runtime_context/context_incomplete` and does not
    # invoke a runtime"; per doc-13a:307-310 "Required evidence cannot be
    # paged exactly: return `state='unavailable'` and route
    # `runtime_context/context_incomplete` or
    # `verifier_context/context_incomplete`" -- the fail-closed route is
    # `quiesce` (per auto-memory `feedback_no_silent_degradation`; the
    # adapter raises typed `MissingPromptContextFieldError` from
    # `execution_control/prompt_context_adapter.py` and the dispatcher
    # projects it onto this typed failure id).
    _route(
        "runtime_context",
        "context_incomplete",
        "quiesce",
        "runtime context is incomplete (Slice 13A: required exact evidence unavailable)",
    ),
    _route(
        "runtime_structured_output",
        "malformed_structured_output",
        "retry_dispatch",
        "runtime produced malformed structured output",
    ),
    _route(
        "dispatcher_internal",
        "idempotency_conflict",
        "quiesce",
        "dispatcher idempotency conflict requires investigation",
    ),
    _route(
        "verifier_provider",
        "verifier_provider_timeout",
        "retry_verifier",
        "verifier provider timed out",
    ),
    _route(
        "verifier_provider",
        "verifier_provider_crash",
        "retry_verifier",
        "verifier provider crashed",
    ),
    _route(
        "verifier_provider",
        "verifier_parse_failed",
        "retry_verifier",
        "verifier output parse failed",
    ),
    _route(
        "verifier_context",
        "context_materialization_failed",
        "retry_verifier",
        "verifier context materialization failed",
    ),
    _route(
        "verifier_context",
        "verifier_context_stale",
        "retry_verifier",
        "verifier context is stale",
    ),
    # Slice 13A fifth sub-slice -- doc-13a:273-275 + doc-13a:276-278.
    # Per doc-13a:273-275 "A gate may not approve from preview_only
    # evidence after 13A is enabled" -- the fail-closed route for the
    # gate companion record when state="preview_only" or
    # required_complete_for cannot be satisfied is `quiesce` (per
    # auto-memory feedback_no_silent_degradation; the typed exception
    # MissingGateCompanionFieldError raised from
    # execution_control/gate_companion.py projects to this typed
    # failure id).
    _route(
        "verifier_context",
        "companion_record_unavailable",
        "quiesce",
        "gate companion record is unavailable (Slice 13A: gate may not approve from preview_only evidence)",
    ),
    # Per doc-13a:276-278 "A summary can satisfy a required gate only
    # if the proof row states the exact source digest, page refs,
    # proof algorithm, and verification time" -- the fail-closed route
    # for the typed proof row when any of the 4 mandatory fields is
    # missing is `quiesce` (per auto-memory
    # feedback_no_silent_degradation; the typed exception
    # MissingProofRowFieldError raised from
    # execution_control/gate_companion.py projects to this typed
    # failure id).
    _route(
        "verifier_context",
        "proof_row_required",
        "quiesce",
        "typed proof row is required (Slice 13A: deterministic summary cannot satisfy required gate without proof row)",
    ),
    # Slice 13A sixth sub-slice -- doc-13a:280-282.
    # Per doc-13a:280-282 "Partial snapshots are allowed for display
    # but classifier rules fail closed unless their required fields
    # are complete" -- the fail-closed route for snapshot companion
    # record when a required list field is structurally incomplete
    # is `quiesce` (per auto-memory feedback_no_silent_degradation;
    # the typed exception MissingSnapshotCompanionFieldError raised
    # from execution_control/snapshot_companion.py projects to this
    # typed failure id).
    #
    # Registered under the EXISTING `evidence_corruption` failure
    # class (NOT a new `snapshot` failure_class) so the supervisor
    # classifier mapping coverage rule does not require a new
    # mapping row in `supervisor/classifier_mapping.py` (READ-ONLY
    # per doc-13a:42-46 + 124-126 change-control rule + the
    # implementer prompt's MUST-NOT-EDIT-SUPERVISOR-MODULES rule).
    # `evidence_corruption` is the closest semantic fit: both signal
    # the snapshot's evidence is structurally incomplete / corrupted;
    # both route to `quiesce`.
    _route(
        "evidence_corruption",
        "list_field_incomplete",
        "quiesce",
        "snapshot list field is incomplete (Slice 13A: classifier rules require complete required list fields)",
    ),
    # Per doc-13a:280-282 + auto-memory feedback_no_silent_degradation
    # the classifier rule MUST NOT proceed when the snapshot companion
    # record signals a blocked rule (e.g. a paged list field that the
    # classifier requires complete coverage of). The typed exception
    # MissingSnapshotCompanionFieldError raised from
    # execution_control/snapshot_companion.py projects to this typed
    # failure id.
    _route(
        "evidence_corruption",
        "classifier_rule_blocked",
        "quiesce",
        "snapshot classifier rule is blocked (Slice 13A: required list fields incomplete for rule scope)",
    ),
    _route(
        "checkpoint_contradiction",
        "checkpoint_after_failed_gate",
        "quiesce",
        "checkpoint contradicts a failed gate",
    ),
    _route(
        "regroup_invalid",
        "regroup_dependency_cycle",
        "quiesce",
        "regroup dependency cycle is deterministic",
    ),
    _route(
        "regroup_invalid",
        "regroup_write_conflict",
        "quiesce",
        "regroup write conflict is deterministic",
    ),
    _route(
        "evidence_corruption",
        "artifact_hash_mismatch",
        "quiesce",
        "artifact hash mismatch must stop replay",
    ),
    _route(
        "evidence_corruption",
        "payload_digest_mismatch",
        "quiesce",
        "payload digest mismatch must stop replay",
    ),
    _route(
        "evidence_corruption",
        "projection_body_conflict",
        "quiesce",
        "projection body conflict must stop replay",
    ),
    # Slice 14 second sub-slice -- doc-14:192-201 + doc-14:242-243.
    # Per doc-14:194-196 "Git note write fails after commit: governance
    # records a `line_provenance_gap` or `governance_evidence_conflict`
    # finding and retries the projection idempotently. It does not block
    # checkpointing or resume." -- the fail-OPEN route for the Git
    # provenance writer (per
    # `src/iriai_build_v2/execution_control/commit_provenance_writer.py`
    # :class:`GitProvenanceWriter`) is the NEW `retry_governance_projection`
    # NON-BLOCKING retry action (NOT `quiesce`) per doc-14:242-243
    # ("Governance provenance projection failures never block
    # `dag-group:*` checkpointing, merge queue integration, or resume").
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A 6th
    # sub-slice typed ids `list_field_incomplete` +
    # `classifier_rule_blocked` (also under `evidence_corruption` above)
    # which route to `quiesce` -- the Slice 13A pattern is a fail-CLOSED
    # safety stop for required gate evidence; the Slice 14 pattern is a
    # fail-OPEN non-blocking governance projection observer. The
    # `evidence_corruption` failure_class is the semantic fit (Git
    # provenance failures signal disagreement between Postgres typed
    # `dag-commit-proof:*` evidence and Git ref/notes evidence; structurally
    # analogous to `artifact_hash_mismatch` / `payload_digest_mismatch`
    # / `projection_body_conflict` above) BUT the routing is non-blocking
    # because the doc-14:242-243 contract is explicit on this point.
    _route(
        "evidence_corruption",
        "line_provenance_gap",
        "retry_governance_projection",
        "git provenance write failed (Slice 14: post-checkpoint observer; non-blocking per doc-14:242-243)",
    ),
    _route(
        "evidence_corruption",
        "governance_evidence_conflict",
        "retry_governance_projection",
        "git provenance ref conflicts with computed payload digest (Slice 14: post-checkpoint observer; non-blocking per doc-14:242-243)",
    ),
    # Slice 15 second sub-slice -- doc-15:117-136 + doc-15:140-145.
    # Per doc-15:117-136 step 2 the governance metric extractor consumes
    # Slice 13 evidence sets and projects metric definitions onto typed
    # GovernanceMetricValue records; per doc-15:140-145 governance metrics
    # are derived rows that do NOT change execution state. The fail-OPEN
    # route for the metric extractor (per
    # `src/iriai_build_v2/execution_control/governance_metric_extractor.py`
    # :class:`MetricExtractor`) REUSES the Slice 14 2nd sub-slice
    # `retry_governance_projection` NON-BLOCKING retry action verbatim
    # (NOT a new action; the doc-14:242-243 non-blocking contract applies
    # to all post-checkpoint governance projection observers, including
    # the metric extractor).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption` above) which route to `quiesce` -- the Slice
    # 13A pattern is a fail-CLOSED safety stop for required gate
    # evidence; the Slice 15 pattern matches the Slice 14 fail-OPEN
    # non-blocking governance projection observer.
    _route(
        "evidence_corruption",
        "governance_metric_extraction_failed",
        "retry_governance_projection",
        "governance metric extraction failed (Slice 15: post-checkpoint observer; non-blocking per doc-15:140-145 + doc-14:242-243)",
    ),
    # Slice 15 fourth sub-slice -- doc-15:133-134 step 6 + doc-15:140-145.
    # Per doc-15:133-134 step 6 the governance scorecard writer composes the
    # typed GovernanceScorecard governance row + the bounded review projection
    # at review:governance-metrics:{corpus_id}; per doc-15:140-145 governance
    # metrics are derived rows that do NOT change execution state. The
    # fail-OPEN route for the scorecard writer (per
    # `src/iriai_build_v2/execution_control/governance_scorecard_writer.py`
    # :class:`ScorecardWriter`) REUSES the Slice 14 2nd sub-slice
    # `retry_governance_projection` NON-BLOCKING retry action verbatim
    # (NOT a new action; the doc-14:242-243 non-blocking contract applies
    # to all post-checkpoint governance projection observers, including the
    # scorecard writer).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption` above) which route to `quiesce` -- the Slice 13A
    # pattern is a fail-CLOSED safety stop for required gate evidence; the
    # Slice 15 pattern matches the Slice 14 + Slice 15 2nd-sub-slice fail-OPEN
    # non-blocking governance projection observer.
    _route(
        "evidence_corruption",
        "governance_scorecard_persistence_failed",
        "retry_governance_projection",
        "governance scorecard persistence failed (Slice 15: post-checkpoint observer; non-blocking per doc-15:140-145 + doc-14:242-243)",
    ),
    # Slice 16 second sub-slice -- doc-16:155-169 + doc-16:158 + doc-14:242-243.
    # Per doc-16:155-169 § Refactoring Steps 2 + 3 + 4 + 7 the governance
    # finding rule engine consumes Slice 13 evidence sets + Slice 15 metric
    # scorecards + Slice 16 1st sub-slice typed rules and emits typed
    # GovernanceFinding records via deterministic rule application; per
    # doc-14:242-243 (inherited by every post-checkpoint governance
    # projection observer) the rule engine NEVER blocks `dag-group:*`
    # checkpointing, merge queue integration, or resume. The fail-OPEN
    # route for the rule engine (per
    # `src/iriai_build_v2/execution_control/finding_rule_engine.py`
    # :class:`FindingRuleEngine`) REUSES the Slice 14 2nd sub-slice
    # `retry_governance_projection` NON-BLOCKING retry action verbatim
    # (NOT a new action; the doc-14:242-243 non-blocking contract applies
    # to all post-checkpoint governance projection observers, including the
    # rule engine).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption` above) which route to `quiesce` -- the Slice 13A
    # pattern is a fail-CLOSED safety stop for required gate evidence; the
    # Slice 16 pattern matches the Slice 14 + Slice 15 2nd + 4th sub-slice
    # fail-OPEN non-blocking governance projection observer.
    _route(
        "evidence_corruption",
        "finding_rule_emission_failed",
        "retry_governance_projection",
        "governance finding rule emission failed (Slice 16: post-checkpoint observer; non-blocking per doc-16:155-169 + doc-14:242-243)",
    ),
    # Slice 16 third-A sub-slice -- doc-16:164-165 + doc-16:191-192 + doc-14:242-243.
    # Per doc-16:164-165 § Refactoring Steps step 5 (THIS SUB-SLICE OWNS
    # accepted_plan_deviation + implementation_journal_gap classes; the
    # 3rd-B sub-slice owns reviewer-findings + late-test-failure classes)
    # the implementation-plan deviation engine consumes parsed
    # Slice 13c ImplementationArtifactAnchor rows. Per doc-14:242-243
    # (inherited by every post-checkpoint governance projection observer)
    # the plan-deviation engine NEVER blocks `dag-group:*` checkpointing,
    # merge queue integration, or resume. The fail-OPEN route for the
    # plan-deviation engine (per
    # `src/iriai_build_v2/execution_control/finding_plan_deviation_engine.py`
    # :func:`parse_plan_deviation_anchors`) REUSES the Slice 14 2nd
    # sub-slice `retry_governance_projection` NON-BLOCKING retry action
    # verbatim (NOT a new action; the doc-14:242-243 non-blocking
    # contract applies to all post-checkpoint governance projection
    # observers, including the plan-deviation engine).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption` above) which route to `quiesce` -- the
    # Slice 13A pattern is a fail-CLOSED safety stop for required gate
    # evidence; the Slice 16 pattern matches the Slice 14 + Slice 15 +
    # Slice 16 2nd sub-slice fail-OPEN non-blocking governance projection
    # observer.
    _route(
        "evidence_corruption",
        "finding_plan_deviation_parse_failed",
        "retry_governance_projection",
        "governance finding plan-deviation anchor parse failed (Slice 16 3rd-A: post-checkpoint observer; non-blocking per doc-16:164-165 + doc-16:191-192 + doc-14:242-243)",
    ),
    # Slice 16 third-B sub-slice -- doc-16:164-165 + doc-16:137 + doc-16:183-184 + doc-14:242-243.
    # Per doc-16:164-165 § Refactoring Steps step 5 remaining categories
    # (THIS SUB-SLICE OWNS reviewer-finding + late-test-failure classes;
    # the 3rd-A sub-slice owns accepted_plan_deviation +
    # implementation_journal_gap classes) the reviewer-finding + late-
    # test-failure engine consumes parsed ImplementationArtifactAnchor
    # rows from BOTH the Slice 13c markdown journal parser AND the Slice
    # 13d JSONL decision-log parser. Per doc-14:242-243 (inherited by
    # every post-checkpoint governance projection observer) the engine
    # NEVER blocks `dag-group:*` checkpointing, merge queue integration,
    # or resume. The fail-OPEN route for the reviewer + late-test-failure
    # engine (per
    # `src/iriai_build_v2/execution_control/finding_reviewer_test_failure_engine.py`
    # :func:`parse_reviewer_test_failure_anchors`) REUSES the Slice 14
    # 2nd sub-slice `retry_governance_projection` NON-BLOCKING retry
    # action verbatim (NOT a new action; the doc-14:242-243 non-blocking
    # contract applies to all post-checkpoint governance projection
    # observers, including the reviewer + late-test-failure engine).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption` above) which route to `quiesce` -- the
    # Slice 13A pattern is a fail-CLOSED safety stop for required gate
    # evidence; the Slice 16 pattern matches the Slice 14 + Slice 15 +
    # Slice 16 2nd + 3rd-A sub-slice fail-OPEN non-blocking governance
    # projection observer.
    _route(
        "evidence_corruption",
        "finding_reviewer_test_failure_parse_failed",
        "retry_governance_projection",
        "governance finding reviewer/late-test-failure anchor parse failed (Slice 16 3rd-B: post-checkpoint observer; non-blocking per doc-16:164-165 + doc-16:137 + doc-16:183-184 + doc-14:242-243)",
    ),
    # Slice 16 fourth sub-slice -- doc-16:166-167 + doc-16:174-176 +
    # doc-14:242-243. Per doc-16:166-167 § Refactoring Steps step 6
    # (verbatim *"Store findings as typed governance rows and project
    # bounded review artifacts such as
    # `review:governance-findings:{corpus_id}`."*) the governance finding
    # writer composes typed GovernanceFinding records (per doc-16:82-104)
    # from the Slice 16 2nd + 3rd-A + 3rd-B sub-slice engines' emissions
    # and projects them onto BOTH typed governance_finding:* rows AND the
    # bounded review projection at the `review:governance-findings:` key
    # prefix. Per doc-14:242-243 (inherited by every post-checkpoint
    # governance projection observer) the finding writer NEVER blocks
    # `dag-group:*` checkpointing, merge queue integration, or resume.
    # The fail-OPEN route for the finding writer (per
    # `src/iriai_build_v2/execution_control/governance_finding_writer.py`
    # :class:`GovernanceFindingWriter`) REUSES the Slice 14 2nd sub-slice
    # `retry_governance_projection` NON-BLOCKING retry action verbatim
    # (NOT a new action; the doc-14:242-243 non-blocking contract applies
    # to all post-checkpoint governance projection observers, including
    # the finding writer).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption` above) which route to `quiesce` -- the
    # Slice 13A pattern is a fail-CLOSED safety stop for required gate
    # evidence; the Slice 16 pattern matches the Slice 14 + Slice 15 +
    # Slice 16 2nd + 3rd-A + 3rd-B sub-slice fail-OPEN non-blocking
    # governance projection observer.
    _route(
        "evidence_corruption",
        "governance_finding_persistence_failed",
        "retry_governance_projection",
        "governance finding writer persistence failed (Slice 16 4th: post-checkpoint observer; non-blocking per doc-16:166-167 + doc-16:174-176 + doc-14:242-243)",
    ),
    # Slice 17 second sub-slice -- doc-17:168-169 + doc-17:204 + doc-14:242-243.
    # Per doc-17:168-169 § Refactoring Steps step 2 the governance
    # recommendation builder consumes Slice 16 1st sub-slice typed
    # GovernanceFinding BaseModels + the Slice 17 1st sub-slice typed
    # GovernancePolicyRecommendation + per-consumer *PolicyArtifact
    # BaseModels and emits typed GovernancePolicyRecommendation records
    # with status="draft" per doc-17:166-167. Per doc-14:242-243
    # (inherited by every post-checkpoint governance projection
    # observer) the recommendation builder NEVER blocks `dag-group:*`
    # checkpointing, merge queue integration, or resume. The fail-OPEN
    # route for the recommendation builder (per
    # `src/iriai_build_v2/execution_control/recommendation_builder.py`
    # :class:`RecommendationBuilder`) REUSES the Slice 14 2nd sub-slice
    # `retry_governance_projection` NON-BLOCKING retry action verbatim
    # (NOT a new action; the doc-14:242-243 non-blocking contract
    # applies to all post-checkpoint governance projection observers,
    # including the recommendation builder).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption` above) which route to `quiesce` -- the
    # Slice 13A pattern is a fail-CLOSED safety stop for required gate
    # evidence; the Slice 17 pattern matches the Slice 14 + Slice 15 +
    # Slice 16 sub-slice fail-OPEN non-blocking governance projection
    # observer.
    _route(
        "evidence_corruption",
        "recommendation_builder_emission_failed",
        "retry_governance_projection",
        "governance recommendation builder emission failed (Slice 17 2nd: post-checkpoint observer; non-blocking per doc-17:168-169 + doc-17:204 + doc-14:242-243)",
    ),
    # Slice 17 third sub-slice -- doc-17:170-171 + doc-17:208-210 + doc-14:242-243.
    # Per doc-17:170-171 § Refactoring Steps step 3 the per-consumer
    # policy validation interface consumes the Slice 17 2nd sub-slice
    # typed GovernancePolicyRecommendation + *PolicyArtifact union
    # members and validates per-consumer policy-shape rules per
    # doc-17:208-210; per doc-14:242-243 (inherited by every post-
    # checkpoint governance projection observer) the validator NEVER
    # blocks `dag-group:*` checkpointing, merge queue integration, or
    # resume. The fail-OPEN route for the validator (per
    # `src/iriai_build_v2/execution_control/policy_validation_interface.py`
    # :class:`PolicyValidationInterface`) REUSES the Slice 14 2nd
    # sub-slice `retry_governance_projection` NON-BLOCKING retry
    # action verbatim (NOT a new action; the doc-14:242-243 non-
    # blocking contract applies to all post-checkpoint governance
    # projection observers, including the validator).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption` above) which route to `quiesce` -- the
    # Slice 13A pattern is a fail-CLOSED safety stop for required gate
    # evidence; the Slice 17 pattern matches the Slice 14 + Slice 15 +
    # Slice 16 + Slice 17 2nd sub-slice fail-OPEN non-blocking
    # governance projection observer.
    _route(
        "evidence_corruption",
        "policy_validation_failed",
        "retry_governance_projection",
        "governance policy validation failed (Slice 17 3rd: post-checkpoint observer; non-blocking per doc-17:170-171 + doc-17:208-210 + doc-14:242-243)",
    ),
    # Slice 17 fourth sub-slice -- doc-17:172 + doc-17:182-188 + doc-14:242-243.
    # Per doc-17:172 § Refactoring Steps step 4 the decision-record
    # writer persists typed PolicyRecommendationDecision rows at
    # `review:governance-recommendations:{corpus_id}` per doc-17:182-188;
    # per doc-14:242-243 (inherited by every post-checkpoint governance
    # projection observer) the writer NEVER blocks `dag-group:*`
    # checkpointing, merge queue integration, or resume. The fail-OPEN
    # route for the writer (per
    # `src/iriai_build_v2/execution_control/decision_record_writer.py`
    # :class:`DecisionRecordWriter`) REUSES the Slice 14 2nd sub-slice
    # `retry_governance_projection` NON-BLOCKING retry action verbatim
    # (NOT a new action; the doc-14:242-243 non-blocking contract
    # applies to all post-checkpoint governance projection observers,
    # including the decision-record writer).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption` above) which route to `quiesce` -- the
    # Slice 13A pattern is a fail-CLOSED safety stop for required gate
    # evidence; the Slice 17 4th pattern matches the Slice 14 + Slice
    # 15 + Slice 16 + Slice 17 2nd + 3rd sub-slice fail-OPEN non-
    # blocking governance projection observer.
    _route(
        "evidence_corruption",
        "decision_record_persistence_failed",
        "retry_governance_projection",
        "governance decision-record persistence failed (Slice 17 4th: post-checkpoint observer; non-blocking per doc-17:172 + doc-17:182-188 + doc-14:242-243)",
    ),
    # Slice 17 fifth sub-slice -- doc-17:173-174 + doc-17:159-163 + doc-14:242-243.
    # Per doc-17:173-174 § Refactoring Steps step 5 the replay-requirement
    # validator consumes the Slice 17 1st sub-slice typed
    # GovernancePolicyRecommendation + checks behavior-changing
    # recommendations (safe_runtime_action=True) carry the typed
    # cross-slice reference to Slice 18 counterfactual replay results
    # (non-empty counterfactual_result_refs list). Per doc-17:159-163 +
    # doc-17:225-226 the validator does NOT introduce a second source of
    # replay truth -- Slice 18 owns the replay result records; this
    # validator owns ONLY the typed cross-reference check. Per
    # doc-14:242-243 (inherited by every post-checkpoint governance
    # projection observer) the validator NEVER blocks `dag-group:*`
    # checkpointing, merge queue integration, or resume. The fail-OPEN
    # route for the validator (per
    # `src/iriai_build_v2/execution_control/replay_requirement_hook.py`
    # :class:`ReplayRequirementValidator`) REUSES the Slice 14 2nd
    # sub-slice `retry_governance_projection` NON-BLOCKING retry
    # action verbatim (NOT a new action; the doc-14:242-243 non-
    # blocking contract applies to all post-checkpoint governance
    # projection observers, including the replay-requirement validator).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption` above) which route to `quiesce` -- the
    # Slice 13A pattern is a fail-CLOSED safety stop for required gate
    # evidence; the Slice 17 5th pattern matches the Slice 14 + Slice
    # 15 + Slice 16 + Slice 17 2nd + 3rd + 4th sub-slice fail-OPEN
    # non-blocking governance projection observer.
    _route(
        "evidence_corruption",
        "replay_requirement_validation_failed",
        "retry_governance_projection",
        "governance replay requirement validation failed (Slice 17 5th: post-checkpoint observer; non-blocking per doc-17:173-174 + doc-17:159-163 + doc-14:242-243)",
    ),
    # Slice 17 sixth sub-slice -- doc-17:175-177 + doc-17:159-163 + doc-14:242-243.
    # Per doc-17:175-177 § Refactoring Steps step 6 the consumer
    # read-API exposes the typed accepted-but-not-activated
    # GovernancePolicyRecommendation records SEPARATELY from
    # consumer-owned activated policy records; per doc-17:159-163 +
    # doc-17:217 the read-API does NOT introduce a second source of
    # activation truth -- activation belongs to the consumer-owned
    # policy record per doc-17:159-163; this read-API GRANTS NO
    # consumer-side activation authority. Per doc-14:242-243
    # (inherited by every post-checkpoint governance projection
    # observer) the read-API NEVER blocks `dag-group:*` checkpointing,
    # merge queue integration, or resume. The fail-OPEN route for the
    # read-API (per
    # `src/iriai_build_v2/execution_control/consumer_read_api.py`
    # :class:`GovernanceReadAPI`) REUSES the Slice 14 2nd sub-slice
    # `retry_governance_projection` NON-BLOCKING retry action
    # verbatim (NOT a new action; the doc-14:242-243 non-blocking
    # contract applies to all post-checkpoint governance projection
    # observers, including the consumer read-API).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption` above) which route to `quiesce` -- the
    # Slice 13A pattern is a fail-CLOSED safety stop for required gate
    # evidence; the Slice 17 6th pattern matches the Slice 14 + Slice
    # 15 + Slice 16 + Slice 17 2nd + 3rd + 4th + 5th sub-slice fail-OPEN
    # non-blocking governance projection observer.
    _route(
        "evidence_corruption",
        "consumer_read_api_failed",
        "retry_governance_projection",
        "governance consumer read-API failed (Slice 17 6th: post-checkpoint observer; non-blocking per doc-17:175-177 + doc-17:159-163 + doc-14:242-243)",
    ),
    # Slice 18 second sub-slice -- doc-18:111-112 + doc-14:242-243.
    # Per doc-18:111 § Refactoring Steps step 1 the replay corpus loader
    # consumes typed Slice 13a GovernanceEvidenceRef inputs + Slice 00
    # fixture paths and emits typed Slice 18 1st sub-slice ReplayCorpus
    # records; per doc-18:112 § Refactoring Steps step 2 the scenario
    # definition builder emits typed Slice 18 1st sub-slice
    # CounterfactualScenario records with required_evidence_kinds +
    # validity_limits verification per doc-18:134-135. Per doc-14:242-243
    # (inherited by every post-checkpoint governance projection observer)
    # the loader + builder NEVER block `dag-group:*` checkpointing,
    # merge queue integration, or resume. The fail-OPEN route for the
    # loader + builder (per
    # `src/iriai_build_v2/execution_control/counterfactual_replay_loader.py`
    # :class:`ReplayCorpusLoader` + :class:`ScenarioDefinitionBuilder`)
    # REUSES the Slice 14 2nd sub-slice `retry_governance_projection`
    # NON-BLOCKING retry action verbatim (NOT a new action; the
    # doc-14:242-243 non-blocking contract applies to all post-
    # checkpoint governance projection observers, including the
    # replay corpus loader + scenario definition builder).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
    # `list_field_incomplete` + `classifier_rule_blocked` (also under
    # `evidence_corruption` above) which route to `quiesce` -- the
    # Slice 13A pattern is a fail-CLOSED safety stop for required gate
    # evidence; the Slice 18 pattern matches the Slice 14 + Slice 15 +
    # Slice 16 + Slice 17 sub-slice fail-OPEN non-blocking governance
    # projection observer.
    _route(
        "evidence_corruption",
        "replay_corpus_or_scenario_load_failed",
        "retry_governance_projection",
        "governance replay corpus loader / scenario definition builder failed (Slice 18 2nd: post-checkpoint observer; non-blocking per doc-18:111-112 + doc-14:242-243)",
    ),
    # Slice 18 third sub-slice -- doc-18:113 + doc-14:242-243.
    # Per doc-18:113 § Refactoring Steps step 3 the
    # CounterfactualSummaryReplayEngine consumes typed Slice 18 1st
    # sub-slice ReplayCorpus + CounterfactualScenario + Slice 15 typed
    # GovernanceMetricValue baseline records + optional Slice 15 typed
    # GovernanceScorecard baseline and emits typed Slice 18 1st sub-
    # slice CounterfactualResult records with all 16 fields populated
    # (per doc-18:79-96) -- WITHOUT requiring typed-event replay (the
    # latter lands in the Slice 18 4th sub-slice per doc-18:114). Per
    # doc-14:242-243 (inherited by every post-checkpoint governance
    # projection observer) the summary-replay engine NEVER blocks
    # `dag-group:*` checkpointing, merge queue integration, or resume.
    # The fail-OPEN route for the engine (per
    # `src/iriai_build_v2/execution_control/counterfactual_summary_replay.py`
    # :class:`CounterfactualSummaryReplayEngine`) REUSES the Slice 14
    # 2nd sub-slice `retry_governance_projection` NON-BLOCKING retry
    # action verbatim (NOT a new action; the doc-14:242-243 non-
    # blocking contract applies to all post-checkpoint governance
    # projection observers, including the summary-replay engine).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed
    # ids `list_field_incomplete` + `classifier_rule_blocked` (also
    # under `evidence_corruption` above) which route to `quiesce` --
    # the Slice 13A pattern is a fail-CLOSED safety stop for required
    # gate evidence; the Slice 18 3rd pattern matches the Slice 14 +
    # Slice 15 + Slice 16 + Slice 17 + Slice 18 2nd sub-slice fail-
    # OPEN non-blocking governance projection observer.
    _route(
        "evidence_corruption",
        "summary_replay_failed",
        "retry_governance_projection",
        "governance counterfactual summary replay engine failed (Slice 18 3rd: post-checkpoint observer; non-blocking per doc-18:113 + doc-14:242-243)",
    ),
    # Slice 18 fourth sub-slice -- doc-18:114 + doc-14:242-243.
    # Per doc-18:114 § Refactoring Steps step 4 the
    # CounterfactualEventReplayEngine consumes typed Slice 18 1st
    # sub-slice ReplayCorpus + CounterfactualScenario + typed Slice
    # 10a event-transition shapes (ExecutionAttemptSummary +
    # GateStatusSummary + TypedFailureSummary + MergeQueueSummary +
    # EvidenceRef checkpoints) + optional Slice 15 typed
    # GovernanceMetricValue baseline records and emits typed Slice 18
    # 1st sub-slice CounterfactualResult records with all 16 fields
    # populated (per doc-18:79-96) at HIGHER fidelity than the 3rd
    # sub-slice summary-replay engine (which carries
    # SUMMARY_REPLAY_CONFIDENCE_CEILING = 0.65; this 4th sub-slice
    # event-replay engine carries EVENT_REPLAY_CONFIDENCE_CEILING =
    # 0.90 per doc-18:114 vs doc-18:133 contrast). Per doc-14:242-243
    # (inherited by every post-checkpoint governance projection
    # observer) the event-replay engine NEVER blocks `dag-group:*`
    # checkpointing, merge queue integration, or resume. The fail-OPEN
    # route for the engine (per
    # `src/iriai_build_v2/execution_control/counterfactual_event_replay.py`
    # :class:`CounterfactualEventReplayEngine`) REUSES the Slice 14
    # 2nd sub-slice `retry_governance_projection` NON-BLOCKING retry
    # action verbatim (NOT a new action; the doc-14:242-243 non-
    # blocking contract applies to all post-checkpoint governance
    # projection observers, including the event-replay engine).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed
    # ids `list_field_incomplete` + `classifier_rule_blocked` (also
    # under `evidence_corruption` above) which route to `quiesce` --
    # the Slice 13A pattern is a fail-CLOSED safety stop for required
    # gate evidence; the Slice 18 4th pattern matches the Slice 14 +
    # Slice 15 + Slice 16 + Slice 17 + Slice 18 2nd + 3rd sub-slice
    # fail-OPEN non-blocking governance projection observer.
    _route(
        "evidence_corruption",
        "event_replay_failed",
        "retry_governance_projection",
        "governance counterfactual event replay engine failed (Slice 18 4th: post-checkpoint observer; non-blocking per doc-18:114 + doc-14:242-243)",
    ),
    # Slice 18 fifth sub-slice -- doc-18:115 + doc-14:242-243.
    # Per doc-18:115 § Refactoring Steps step 5 the
    # CounterfactualMetricsComparator consumes typed Slice 15
    # GovernanceMetricValue baseline records + typed Slice 18 1st
    # sub-slice CounterfactualResult scenario records (emitted by the
    # Slice 18 3rd sub-slice CounterfactualSummaryReplayEngine OR the
    # Slice 18 4th sub-slice CounterfactualEventReplayEngine) and
    # emits typed MetricsComparatorResult records with one
    # MetricsAxisDelta per axis (hours / repair_cycles /
    # commit_failures / risk_change per doc-18:88-92). Per doc-14:242-243
    # (inherited by every post-checkpoint governance projection
    # observer) the metrics-comparator NEVER blocks `dag-group:*`
    # checkpointing, merge queue integration, or resume. The fail-OPEN
    # route for the comparator (per
    # `src/iriai_build_v2/execution_control/counterfactual_metrics_comparator.py`
    # :class:`CounterfactualMetricsComparator`) REUSES the Slice 14
    # 2nd sub-slice `retry_governance_projection` NON-BLOCKING retry
    # action verbatim (NOT a new action; the doc-14:242-243 non-
    # blocking contract applies to all post-checkpoint governance
    # projection observers, including the metrics comparator).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed
    # ids `list_field_incomplete` + `classifier_rule_blocked` (also
    # under `evidence_corruption` above) which route to `quiesce` --
    # the Slice 13A pattern is a fail-CLOSED safety stop for required
    # gate evidence; the Slice 18 5th pattern matches the Slice 14 +
    # Slice 15 + Slice 16 + Slice 17 + Slice 18 2nd + 3rd + 4th sub-
    # slice fail-OPEN non-blocking governance projection observer.
    _route(
        "evidence_corruption",
        "metrics_comparator_failed",
        "retry_governance_projection",
        "governance counterfactual metrics comparator failed (Slice 18 5th: post-checkpoint observer; non-blocking per doc-18:115 + doc-14:242-243)",
    ),
    # Slice 18 sixth sub-slice -- doc-18:116-117 + doc-14:242-243.
    # Per doc-18:116-117 § Refactoring Steps step 6 the
    # CounterfactualResultWriter consumes typed Slice 18 1st sub-slice
    # CounterfactualResult records (emitted by the Slice 18 3rd
    # sub-slice CounterfactualSummaryReplayEngine OR the Slice 18 4th
    # sub-slice CounterfactualEventReplayEngine) + optional typed
    # Slice 18 5th sub-slice MetricsComparatorResult records (emitted
    # by the Slice 18 5th sub-slice CounterfactualMetricsComparator)
    # + optional Slice 13a typed GovernanceEvidenceRef baseline
    # references and emits typed CounterfactualResultWriterResult
    # rows + bounded review projection at
    # `review:governance-counterfactuals:{corpus_id}` per
    # doc-18:116-117. Per doc-14:242-243 (inherited by every
    # post-checkpoint governance projection observer) the writer
    # NEVER blocks `dag-group:*` checkpointing, merge queue
    # integration, or resume. The fail-OPEN route for the writer
    # (per `src/iriai_build_v2/execution_control/counterfactual_result_writer.py`
    # :class:`CounterfactualResultWriter`) REUSES the Slice 14
    # 2nd sub-slice `retry_governance_projection` NON-BLOCKING retry
    # action verbatim (NOT a new action; the doc-14:242-243 non-
    # blocking contract applies to all post-checkpoint governance
    # projection observers, including the counterfactual-result
    # writer + per doc-18:123-125 replay results are review/
    # governance artifacts only -- never runtime policy authority).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed
    # ids `list_field_incomplete` + `classifier_rule_blocked` (also
    # under `evidence_corruption` above) which route to `quiesce` --
    # the Slice 13A pattern is a fail-CLOSED safety stop for required
    # gate evidence; the Slice 18 6th pattern matches the Slice 14 +
    # Slice 15 + Slice 16 + Slice 17 + Slice 18 2nd + 3rd + 4th + 5th
    # sub-slice fail-OPEN non-blocking governance projection observer.
    _route(
        "evidence_corruption",
        "counterfactual_result_persistence_failed",
        "retry_governance_projection",
        "governance counterfactual result writer failed (Slice 18 6th: post-checkpoint observer; non-blocking per doc-18:116-117 + doc-14:242-243)",
    ),
    # Slice 18 seventh sub-slice -- doc-18:117-119 + doc-18:165-166 +
    # doc-14:242-243. Per doc-18:117-119 § Refactoring Steps step 7
    # the RecommendationCitationValidator consumes typed Slice 17 1st
    # sub-slice GovernancePolicyRecommendation records + typed Slice
    # 18 1st sub-slice CounterfactualResult records and emits a typed
    # CitationSufficiencyResult declaring whether the recommendation
    # satisfies the doc-18:165-166 AC4 binding (cite replay results
    # OR explicitly say more evidence is needed). Per doc-14:242-243
    # (inherited by every post-checkpoint governance projection
    # observer) the citation validator NEVER blocks `dag-group:*`
    # checkpointing, merge queue integration, or resume. The fail-OPEN
    # route for the validator (per
    # `src/iriai_build_v2/execution_control/recommendation_citation_hook.py`
    # :class:`RecommendationCitationValidator`) REUSES the Slice 14
    # 2nd sub-slice `retry_governance_projection` NON-BLOCKING retry
    # action verbatim (NOT a new action; the doc-14:242-243 non-
    # blocking contract applies to all post-checkpoint governance
    # projection observers, including the citation validator + per
    # doc-18:123-125 replay results are review/governance artifacts
    # only -- never runtime policy authority).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed
    # ids `list_field_incomplete` + `classifier_rule_blocked` (also
    # under `evidence_corruption` above) which route to `quiesce` --
    # the Slice 13A pattern is a fail-CLOSED safety stop for required
    # gate evidence; the Slice 18 7th pattern matches the Slice 14 +
    # Slice 15 + Slice 16 + Slice 17 + Slice 18 2nd + 3rd + 4th + 5th
    # + 6th sub-slice fail-OPEN non-blocking governance projection
    # observer.
    _route(
        "evidence_corruption",
        "recommendation_citation_validation_failed",
        "retry_governance_projection",
        "governance recommendation citation validator failed (Slice 18 7th: post-checkpoint observer; non-blocking per doc-18:117-119 + doc-18:165-166 + doc-14:242-243)",
    ),
    # Slice 19 second sub-slice -- doc-19:151 + doc-19:184-194 +
    # doc-14:242-243. Per doc-19:151 § Refactoring Steps step 2 the
    # GovernanceSnapshotAPI consumes typed Slice 16/17/18 corpus rows
    # + typed Slice 19 1st sub-slice GovernanceSnapshot foundation and
    # emits a typed SnapshotAPIResult with a populated GovernanceSnapshot
    # whose snapshot_digest is computed from bounded row ids per
    # doc-19:152-153. Per doc-19:184-194 the API surfaces the edge-case
    # reasons (governance snapshot stale; missing line provenance; too
    # many findings; Slack delivery failure; active workflow pressure)
    # as typed SnapshotAPIGap rows with surface `reason` strings. Per
    # doc-14:242-243 (inherited by every post-checkpoint governance
    # projection observer) the snapshot API NEVER blocks `dag-group:*`
    # checkpointing, merge queue integration, or resume. The fail-OPEN
    # route REUSES the Slice 14 2nd sub-slice
    # `retry_governance_projection` NON-BLOCKING retry action verbatim
    # (NOT a new action; the doc-14:242-243 non-blocking contract
    # applies to all post-checkpoint governance projection observers,
    # including the snapshot API + per doc-19:166-171 governance
    # reports are projections only -- never runtime policy authority).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed
    # ids `list_field_incomplete` + `classifier_rule_blocked` (also
    # under `evidence_corruption` above) which route to `quiesce` --
    # the Slice 13A pattern is a fail-CLOSED safety stop for required
    # gate evidence; the Slice 19 2nd pattern matches the Slice 14 +
    # Slice 15 + Slice 16 + Slice 17 + Slice 18 fail-OPEN non-
    # blocking governance projection observer.
    _route(
        "evidence_corruption",
        "governance_snapshot_api_failed",
        "retry_governance_projection",
        "governance snapshot API failed (Slice 19 2nd: post-checkpoint observer; non-blocking per doc-19:151 + doc-19:184-194 + doc-14:242-243)",
    ),
    # Slice 19 third sub-slice -- doc-19:152 + doc-19:170-171 +
    # doc-19:184-194 + doc-14:242-243. Per doc-19:152 § Refactoring
    # Steps step 3 the GovernanceDashboardView consumes the typed
    # Slice 19 2nd sub-slice SnapshotAPIResult + emits a typed
    # DashboardViewPayload with `etag = snapshot_digest` per
    # doc-19:170-171. Per doc-19:184-194 the view surfaces the edge-
    # case reasons (governance snapshot stale; active workflow
    # pressure; upstream snapshot missing; payload construction
    # failed; summary projection failed; etag computation failed)
    # as typed DashboardViewGap rows with surface `reason` strings.
    # Per doc-14:242-243 (inherited by every post-checkpoint
    # governance projection observer) the dashboard view NEVER blocks
    # `dag-group:*` checkpointing, merge queue integration, or
    # resume. The fail-OPEN route REUSES the Slice 14 2nd sub-slice
    # `retry_governance_projection` NON-BLOCKING retry action
    # verbatim (NOT a new action; the doc-14:242-243 non-blocking
    # contract applies to all post-checkpoint governance projection
    # observers, including the dashboard view + per doc-19:170-171
    # dashboards read snapshots with bounded fields + per
    # doc-19:166-167 reports are projections only -- never runtime
    # policy authority).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed
    # ids `list_field_incomplete` + `classifier_rule_blocked` (also
    # under `evidence_corruption` above) which route to `quiesce` --
    # the Slice 13A pattern is a fail-CLOSED safety stop for required
    # gate evidence; the Slice 19 3rd pattern matches the Slice 14 +
    # Slice 15 + Slice 16 + Slice 17 + Slice 18 + Slice 19 2nd
    # fail-OPEN non-blocking governance projection observer.
    _route(
        "evidence_corruption",
        "governance_dashboard_view_failed",
        "retry_governance_projection",
        "governance dashboard view failed (Slice 19 3rd: post-checkpoint observer; non-blocking per doc-19:152 + doc-19:170-171 + doc-19:184-194 + doc-14:242-243)",
    ),
    # Slice 19 fourth sub-slice -- doc-19:155 + doc-19:140-142 +
    # doc-19:122-123 + doc-19:184-194 + doc-19:191-192 +
    # doc-14:242-243. Per doc-19:155 § Refactoring Steps step 4 the
    # GovernanceSlackRenderer consumes the typed Slice 19 2nd sub-
    # slice SnapshotAPIResult + emits a typed SlackRenderPayload
    # with `dedupe_key = snapshot_digest` per doc-19:140-142 +
    # bounded by the 40 KB Block Kit budget per doc-19:122-123. Per
    # doc-19:184-194 + doc-19:191-192 the renderer surfaces the
    # edge-case reasons (governance snapshot stale; active workflow
    # pressure; upstream snapshot missing; payload construction
    # failed; summary projection failed; dedupe_key computation
    # failed; budget exceeded; Slack delivery failure) as typed
    # SlackRenderGap rows with surface `reason` strings. Per
    # doc-14:242-243 (inherited by every post-checkpoint governance
    # projection observer) the Slack renderer NEVER blocks
    # `dag-group:*` checkpointing, merge queue integration, or
    # resume. The fail-OPEN route REUSES the Slice 14 2nd sub-slice
    # `retry_governance_projection` NON-BLOCKING retry action
    # verbatim (NOT a new action; the doc-14:242-243 non-blocking
    # contract applies to all post-checkpoint governance projection
    # observers, including the Slack renderer + per doc-19:140-142
    # Slack digests dedupe by snapshot_digest + per doc-19:166-167
    # reports are projections only + per doc-19:191-192 Slack
    # delivery failure keeps report artifact and retries via outbox
    # -- never runtime policy authority).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed
    # ids `list_field_incomplete` + `classifier_rule_blocked` (also
    # under `evidence_corruption` above) which route to `quiesce` --
    # the Slice 13A pattern is a fail-CLOSED safety stop for required
    # gate evidence; the Slice 19 4th pattern matches the Slice 14 +
    # Slice 15 + Slice 16 + Slice 17 + Slice 18 + Slice 19 2nd + 3rd
    # fail-OPEN non-blocking governance projection observer.
    _route(
        "evidence_corruption",
        "governance_slack_renderer_failed",
        "retry_governance_projection",
        "governance Slack renderer failed (Slice 19 4th: post-checkpoint observer; non-blocking per doc-19:155 + doc-19:140-142 + doc-19:122-123 + doc-19:184-194 + doc-19:191-192 + doc-14:242-243)",
    ),
    # Slice 19 fifth sub-slice -- doc-19 step 5 + doc-19:124-127 +
    # doc-19:144-146 + doc-19:184-194 + doc-14:242-243. Per doc-19 step 5
    # § Refactoring Steps step 5 the GovernanceAgentContextBuilder
    # consumes the typed Slice 19 2nd sub-slice SnapshotAPIResult +
    # emits a typed GovernanceAgentContext (the Slice 19 1st sub-slice
    # typed shape) with `max_prompt_chars` hard-capped at the
    # doc-19:124-127 20 000 char cap. Per doc-19:144-146 the builder
    # selects findings + recommendations + line-provenance relevant to
    # the caller's typed AgentContextScope (task / repo / path / line
    # range). Per doc-19:184-194 + doc-19:188-189 the builder surfaces
    # the edge-case reasons (upstream snapshot missing; context
    # construction failed; parallel-list length mismatch; prompt budget
    # exceeded; governance snapshot stale; missing line provenance;
    # active workflow pressure) as typed AgentContextBuilderGap rows
    # with surface `reason` strings. Per doc-14:242-243 (inherited by
    # every post-checkpoint governance projection observer) the agent-
    # context builder NEVER blocks `dag-group:*` checkpointing, merge
    # queue integration, or resume. The fail-OPEN route REUSES the
    # Slice 14 2nd sub-slice `retry_governance_projection` NON-BLOCKING
    # retry action verbatim (NOT a new action; the doc-14:242-243 non-
    # blocking contract applies to all post-checkpoint governance
    # projection observers, including the agent-context builder + per
    # doc-19:174-176 agent `policy_guidance` is prompt context only +
    # per doc-19:166-167 reports are projections only -- never runtime
    # policy authority).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed
    # ids `list_field_incomplete` + `classifier_rule_blocked` (also
    # under `evidence_corruption` above) which route to `quiesce` --
    # the Slice 13A pattern is a fail-CLOSED safety stop for required
    # gate evidence; the Slice 19 5th pattern matches the Slice 14 +
    # Slice 15 + Slice 16 + Slice 17 + Slice 18 + Slice 19 2nd + 3rd +
    # 4th fail-OPEN non-blocking governance projection observer.
    _route(
        "evidence_corruption",
        "governance_agent_context_builder_failed",
        "retry_governance_projection",
        "governance agent-context builder failed (Slice 19 5th: post-checkpoint observer; non-blocking per doc-19 step 5 + doc-19:124-127 + doc-19:144-146 + doc-19:184-194 + doc-14:242-243)",
    ),
    # Slice 19 sixth sub-slice -- doc-19:161-162 + doc-19:166-167 +
    # doc-19:184-194 + doc-14:242-243. Per doc-19:161-162 § Refactoring
    # Steps step 6 the GovernanceReportArtifactEmitter consumes the
    # typed Slice 19 2nd sub-slice SnapshotAPIResult + emits a typed
    # GovernanceReportArtifact with bounded summary only (the
    # `review:governance-report:{corpus_id}` artifact key + by-name
    # reference shapes; refs-only per doc-19:111 + doc-19:114). Per
    # doc-19:166-167 reports are projections of governance rows. Per
    # doc-19:184-194 the emitter surfaces the edge-case reasons
    # (upstream snapshot missing; corpus_id empty; artifact
    # construction failed; governance snapshot stale; active workflow
    # pressure) as typed ReportArtifactGap rows with surface `reason`
    # strings. Per doc-14:242-243 (inherited by every post-checkpoint
    # governance projection observer) the report-artifact emitter
    # NEVER blocks `dag-group:*` checkpointing, merge queue
    # integration, or resume. The fail-OPEN route REUSES the Slice 14
    # 2nd sub-slice `retry_governance_projection` NON-BLOCKING retry
    # action verbatim (NOT a new action; the doc-14:242-243
    # non-blocking contract applies to all post-checkpoint governance
    # projection observers, including the report-artifact emitter +
    # per doc-19:161-162 the `review:governance-report:{corpus_id}`
    # artifact key is a bounded summary only + per doc-19:166-167
    # reports are projections only -- never runtime policy authority).
    #
    # This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed
    # ids `list_field_incomplete` + `classifier_rule_blocked` (also
    # under `evidence_corruption` above) which route to `quiesce` --
    # the Slice 13A pattern is a fail-CLOSED safety stop for required
    # gate evidence; the Slice 19 6th pattern matches the Slice 14 +
    # Slice 15 + Slice 16 + Slice 17 + Slice 18 + Slice 19 2nd + 3rd +
    # 4th + 5th fail-OPEN non-blocking governance projection observer.
    _route(
        "evidence_corruption",
        "governance_report_artifact_emission_failed",
        "retry_governance_projection",
        "governance report-artifact emission failed (Slice 19 6th: post-checkpoint observer; non-blocking per doc-19:161-162 + doc-19:166-167 + doc-19:184-194 + doc-14:242-243)",
    ),
    _route(
        "resource_exhausted",
        "db_resource_exhausted",
        "quiesce",
        "database resource exhaustion must quiesce",
    ),
    _route(
        "resource_exhausted",
        "disk_resource_exhausted",
        "quiesce",
        "disk resource exhaustion must quiesce",
    ),
    _route(
        "resource_exhausted",
        "process_resource_exhausted",
        "retry_dispatch",
        "process resource exhaustion can retry dispatch",
    ),
    _route(
        "resource_exhausted",
        "provider_quota_exhausted",
        "retry_dispatch",
        "provider quota exhaustion can retry dispatch",
    ),
    _route(
        "resource_exhausted",
        "unclassified",
        "quiesce",
        "unclassified resource exhaustion must quiesce",
    ),
    _route(
        "operator_required",
        "operator_clearance_required",
        "operator_required",
        "operator clearance is required",
    ),
    _route(
        "unknown",
        "unclassified",
        "quiesce",
        "unknown failure class must quiesce",
    ),
)

FAILURE_TYPE_POLICIES: dict[tuple[str, str], FailureTypePolicy] = {
    (policy.failure_class, policy.failure_type): policy for policy, _ in _ROUTE_ROWS
}

ROUTE_TABLE: dict[tuple[str, str], FailureRoutePolicy] = {
    (route.failure_class, route.failure_type): route for _, route in _ROUTE_ROWS
}


def _validate_route_table() -> None:
    for key, route in ROUTE_TABLE.items():
        if route.action != "run_product_repair":
            continue
        failure_class, failure_type = key
        allowed = failure_class == "product_defect" or (
            failure_class == "contract_violation"
            and failure_type in _SCOPED_CONTRACT_PRODUCT_TYPES
        )
        if not allowed or not route.allow_product_repair:
            raise RuntimeError(f"unsafe product repair route: {key!r}")


_validate_route_table()


def build_failure_signature(observation: FailureObservation) -> dict[str, Any]:
    payload = _canonicalize_for_signature("payload", observation.payload)
    return {
        "dag_sha256": observation.dag_sha256,
        "deterministic": observation.deterministic,
        "evidence_ids": sorted(observation.evidence_ids),
        "failure_class": observation.failure_class,
        "failure_type": observation.failure_type,
        "feature_id": observation.feature_id,
        "group_idx": observation.group_idx,
        "payload": payload,
        "severity": observation.severity,
        "source": observation.source,
        "task_id": observation.task_id,
    }


def stable_signature_hash(observation: FailureObservation) -> str:
    return stable_digest(build_failure_signature(observation))


def failure_idempotency_key(
    observation: FailureObservation,
    signature_hash: str,
) -> str:
    payload_key = observation.payload.get("idempotency_key")
    if isinstance(payload_key, str) and payload_key.startswith("failure:"):
        return payload_key
    attempt = observation.attempt_id if observation.attempt_id is not None else "-"
    return (
        f"failure:{observation.feature_id}:{attempt}:"
        f"{observation.failure_class}:{signature_hash}"
    )


def route_budget_key(record: FailureRecord) -> str:
    observation = record.observation
    return (
        f"budget:{observation.feature_id}:{observation.failure_class}:"
        f"{observation.failure_type}:{record.signature_hash}"
    )


def route_idempotency_key(
    record: FailureRecord,
    action: RouteAction,
    reservation_ordinal: int,
) -> str:
    observation = record.observation
    return _route_idempotency_key_from_parts(
        feature_id=observation.feature_id,
        failure_id=record.failure_id or 0,
        signature_hash=record.signature_hash,
        action=action,
        reservation_ordinal=reservation_ordinal,
    )


def _route_idempotency_key_from_parts(
    *,
    feature_id: str,
    failure_id: int,
    signature_hash: str,
    action: RouteAction,
    reservation_ordinal: int,
) -> str:
    return (
        f"route:{feature_id}:{failure_id}:"
        f"{signature_hash}:{action}:n{reservation_ordinal}"
    )


def _route_input_digest(decision: RouteDecision) -> str:
    return stable_digest(
        {
            "action": decision.action,
            "budget_key": decision.budget_key,
            "failure_id": decision.failure_id,
            "idempotency_key": decision.idempotency_key,
            "repair_scope": decision.repair_scope,
            "required_evidence_ids": sorted(decision.required_evidence_ids),
            "signature_hash": decision.signature_hash,
        }
    )


def _route_replay_compatible(stored: RouteDecision, incoming: RouteDecision) -> bool:
    return (
        stored.failure_id == incoming.failure_id
        and stored.signature_hash == incoming.signature_hash
        and stored.budget_key == incoming.budget_key
    )


class FailureRouter:
    def __init__(
        self,
        *,
        port: FailureRouterPort | None = None,
        route_table: Mapping[tuple[str, str], FailureRoutePolicy] | None = None,
        type_policies: Mapping[tuple[str, str], FailureTypePolicy] | None = None,
    ) -> None:
        self.port: FailureRouterPort = port or InMemoryFailureRouterPort()
        self.route_table = dict(route_table or ROUTE_TABLE)
        self.type_policies = dict(type_policies or FAILURE_TYPE_POLICIES)

    def record(self, observation: FailureObservation) -> int:
        route = self._route_for(observation.failure_class, observation.failure_type)
        policy = self._policy_for(observation.failure_class, observation.failure_type)
        normalized = self._normalize_observation(observation, policy)
        signature_hash = stable_signature_hash(normalized)
        input_digest = stable_digest(build_failure_signature(normalized))
        record = FailureRecord(
            observation=normalized,
            policy=policy,
            signature_hash=signature_hash,
            idempotency_key=failure_idempotency_key(normalized, signature_hash),
            input_digest=input_digest,
        )
        if route.action == "run_product_repair" and not route.allow_product_repair:
            raise UnknownFailurePolicyError(
                f"unsafe product repair route for {route.failure_class}/{route.failure_type}"
            )
        return self.port.record_failure(record).failure_id or 0

    def decide(self, failure_id: int) -> RouteDecision:
        record = self.get_failure(failure_id)
        route = self._route_for(
            record.observation.failure_class,
            record.observation.failure_type,
        )
        budget_key = route_budget_key(record)
        state = self.port.get_budget(budget_key)
        reserved = state.reserved_attempts if state is not None else 0
        budget_remaining = max(route.budget - reserved, 0)
        repair_scope = self._repair_scope(record, route)
        action = route.action
        budget_exhausted = False
        reason = route.reason

        if action == "run_product_repair" and not self._allows_product_repair(
            record,
            route,
            repair_scope,
        ):
            action = "quiesce"
            budget_remaining = 0
            reason = (
                "product repair requires product defect class or scoped contract "
                "violation evidence"
            )
        elif (
            action not in ("quiesce", "operator_required", "retry_governance_projection")
            and budget_remaining <= 0
        ):
            # Ordinary exhausted retry/repair routes quiesce. Governance
            # projection retries are excluded here and handled below because
            # they are post-checkpoint observers.
            action = "quiesce"
            budget_exhausted = True
            reason = f"retry budget exhausted for {route.failure_class}/{route.failure_type}"
        elif action == "retry_governance_projection" and budget_remaining <= 0:
            # Slice 14 second sub-slice -- doc-14:242-243.
            # Budget-exhausted but the action stays NON-BLOCKING; the
            # caller observes `budget_exhausted=True` + the typed
            # `retry_governance_projection` action and gracefully
            # records the finding without quiescing the executor.
            budget_exhausted = True
            reason = (
                f"retry budget exhausted for {route.failure_class}/{route.failure_type} "
                "(non-blocking per doc-14:242-243; governance projection observer)"
            )

        ordinal = reserved + 1 if not budget_exhausted else reserved
        return RouteDecision(
            failure_id=failure_id,
            route_decision_id=None,
            action=action,
            budget_remaining=budget_remaining,
            budget_exhausted=budget_exhausted,
            reason=reason,
            required_evidence_ids=record.observation.evidence_ids,
            signature_hash=record.signature_hash,
            idempotency_key=route_idempotency_key(record, action, ordinal),
            repair_scope=repair_scope,
            budget_key=budget_key,
            reservation_ordinal=ordinal,
        )

    def mark_route_started(self, decision: RouteDecision) -> RouteDecision:
        existing = self.port.get_route_by_key(decision.idempotency_key)
        input_digest = _route_input_digest(decision)
        if existing is not None:
            if existing.input_digest != input_digest and not _route_replay_compatible(
                existing.decision,
                decision,
            ):
                raise IdempotencyConflict(
                    decision.idempotency_key,
                    existing.input_digest,
                    input_digest,
                )
            return existing.decision

        record = self.get_failure(decision.failure_id)
        route = self._route_for(
            record.observation.failure_class,
            record.observation.failure_type,
        )
        if decision.action in ("quiesce", "operator_required") or route.budget <= 0:
            stored = self.port.record_route_started(decision, input_digest)
            return stored.decision

        stored = self.port.record_route_started(
            decision,
            input_digest,
            budget_reservation={
                "budget_key": decision.budget_key,
                "feature_id": record.observation.feature_id,
                "failure_class": record.observation.failure_class,
                "failure_type": record.observation.failure_type,
                "signature_hash": record.signature_hash,
                "max_attempts": route.budget,
                "failure_id": decision.failure_id,
            },
        )
        return stored.decision

    def mark_route_finished(
        self,
        decision: RouteDecision,
        *,
        succeeded: bool,
        produced_failure_id: int | None = None,
    ) -> None:
        self.port.mark_route_finished(
            decision,
            succeeded=succeeded,
            produced_failure_id=produced_failure_id,
        )

    def get_failure(self, failure_id: int) -> FailureRecord:
        record = self.port.get_failure(failure_id)
        if record is None:
            raise KeyError(f"unknown failure id: {failure_id}")
        return record

    def _normalize_observation(
        self,
        observation: FailureObservation,
        policy: FailureTypePolicy,
    ) -> FailureObservation:
        payload = deepcopy(observation.payload)
        return observation.model_copy(
            update={
                "deterministic": policy.deterministic,
                "retryable": policy.retryable,
                "operator_required": policy.operator_required,
                "severity": policy.severity,
                "payload": payload,
            }
        )

    def _policy_for(
        self,
        failure_class: str,
        failure_type: str,
    ) -> FailureTypePolicy:
        policy = self.type_policies.get((failure_class, failure_type))
        if policy is None:
            raise UnknownFailurePolicyError(
                f"no failure type policy for {failure_class}/{failure_type}"
            )
        return policy

    def _route_for(
        self,
        failure_class: str,
        failure_type: str,
    ) -> FailureRoutePolicy:
        route = self.route_table.get((failure_class, failure_type))
        if route is None:
            raise UnknownFailurePolicyError(
                f"no route policy for {failure_class}/{failure_type}"
            )
        return route

    def _repair_scope(
        self,
        record: FailureRecord,
        route: FailureRoutePolicy,
    ) -> dict[str, Any]:
        payload = record.observation.payload
        scope = {
            "feature_id": record.observation.feature_id,
            "dag_sha256": record.observation.dag_sha256,
            "group_idx": record.observation.group_idx,
            "task_id": record.observation.task_id,
            "attempt_id": record.observation.attempt_id,
            "source": record.observation.source,
            "failure_class": record.observation.failure_class,
            "failure_type": record.observation.failure_type,
            "repair_kind": route.repair_kind,
            "repo_ids": _str_list(_payload_value(payload, "repo_ids", "repo_id")),
            "target_paths": _str_list(
                _payload_value(
                    payload,
                    "target_paths",
                    "offending_paths",
                    "paths",
                    "path",
                ),
                path=True,
            ),
            "target_contract_ids": _int_list(
                _payload_value(payload, "target_contract_ids", "contract_ids", "contract_id")
            ),
            "contract_ids": _int_list(
                _payload_value(payload, "contract_ids", "target_contract_ids", "contract_id")
            ),
            "required_gate_ids": _str_list(_payload_value(payload, "gate_ids", "gate_id")),
            "gate_ids": _str_list(_payload_value(payload, "gate_ids", "required_gate_ids", "gate_id")),
            "sandbox_id": _payload_value(payload, "sandbox_id"),
            "queue_id": _payload_value(payload, "queue_id", "merge_queue_id"),
            "evidence_ids": record.observation.evidence_ids,
            "hook_evidence_ids": _int_list(_payload_value(payload, "hook_evidence_ids")),
            "status_evidence_ids": _int_list(_payload_value(payload, "status_evidence_ids")),
            "no_dirty_proof_evidence_ids": _int_list(
                _payload_value(payload, "no_dirty_proof_evidence_ids")
            ),
            "failed_merge_queue_item_id": _payload_value(
                payload,
                "failed_merge_queue_item_id",
                "merge_queue_item_id",
                "queue_item_id",
            ),
            "failed_source_queue_item_evidence_id": _payload_value(
                payload,
                "failed_source_queue_item_evidence_id",
                "source_queue_item_evidence_id",
            ),
            "source_queue_item_status": _payload_value(
                payload,
                "source_queue_item_status",
                "queue_item_status",
            ),
            "source_feature_id": _payload_value(
                payload,
                "source_feature_id",
                "failed_source_feature_id",
                "source_queue_item_feature_id",
            ),
            "replacement_feature_id": _payload_value(
                payload,
                "replacement_feature_id",
                "replacement_queue_item_feature_id",
            ),
            "source_dag_sha256": _payload_value(
                payload,
                "source_dag_sha256",
                "failed_source_dag_sha256",
                "source_queue_item_dag_sha256",
            ),
            "replacement_dag_sha256": _payload_value(
                payload,
                "replacement_dag_sha256",
                "replacement_queue_item_dag_sha256",
            ),
            "source_group_idx": _payload_value(
                payload,
                "source_group_idx",
                "failed_source_group_idx",
                "source_queue_item_group_idx",
            ),
            "replacement_group_idx": _payload_value(
                payload,
                "replacement_group_idx",
                "replacement_queue_item_group_idx",
            ),
            "source_task_ids": _str_list(
                _payload_value(
                    payload,
                    "source_task_ids",
                    "failed_source_task_ids",
                    "source_task_id",
                )
            ),
            "replacement_task_ids": _str_list(
                _payload_value(
                    payload,
                    "replacement_task_ids",
                    "replacement_queue_item_task_ids",
                    "replacement_task_id",
                )
            ),
            "source_contract_ids": _int_list(
                _payload_value(
                    payload,
                    "source_contract_ids",
                    "failed_source_contract_ids",
                    "source_queue_item_contract_ids",
                )
            ),
            "replacement_contract_ids": _int_list(
                _payload_value(
                    payload,
                    "replacement_contract_ids",
                    "replacement_queue_item_contract_ids",
                )
            ),
            "source_gate_ids": _str_list(
                _payload_value(
                    payload,
                    "source_gate_ids",
                    "failed_source_gate_ids",
                    "source_queue_item_gate_ids",
                )
            ),
            "replacement_gate_ids": _str_list(
                _payload_value(
                    payload,
                    "replacement_gate_ids",
                    "replacement_queue_item_gate_ids",
                )
            ),
            "source_queue_lane": _payload_value(
                payload,
                "source_queue_lane",
                "failed_source_queue_lane",
                "queue_lane",
            ),
            "replacement_queue_lane": _payload_value(payload, "replacement_queue_lane"),
            "source_route_decision_evidence_ids": _int_list(
                _payload_value(
                    payload,
                    "source_route_decision_evidence_ids",
                    "failed_source_route_decision_evidence_ids",
                )
            ),
            "replacement_route_decision_evidence_ids": _int_list(
                _payload_value(payload, "replacement_route_decision_evidence_ids")
            ),
            "sandbox_lease_id": _payload_value(
                payload,
                "sandbox_lease_id",
                "retained_sandbox_lease_id",
            ),
            "source_verdict_key": _payload_value(payload, "source_verdict_key"),
            "legacy_route": _payload_value(payload, "legacy_route"),
        }
        return {key: value for key, value in scope.items() if value not in (None, [], {})}

    def _allows_product_repair(
        self,
        record: FailureRecord,
        route: FailureRoutePolicy,
        repair_scope: Mapping[str, Any],
    ) -> bool:
        if route.action != "run_product_repair":
            return True
        if record.observation.failure_class == "product_defect":
            return bool(
                route.allow_product_repair
                and (
                    record.observation.evidence_ids
                    or self._authorized_direct_source_verdict(
                        record,
                        repair_scope,
                        allowed_legacy_routes=_DIRECT_PRODUCT_REPAIR_ROUTES,
                        allowed_sources={"verification_graph"},
                    )
                )
                and repair_scope.get("target_paths")
            )
        if (
            record.observation.failure_class != "contract_violation"
            or record.observation.failure_type not in _SCOPED_CONTRACT_PRODUCT_TYPES
        ):
            return False
        return bool(
            repair_scope.get("target_paths")
            and (
                repair_scope.get("target_contract_ids")
                or self._authorized_direct_source_verdict(
                    record,
                    repair_scope,
                    allowed_legacy_routes=_DIRECT_CONTRACT_PRODUCT_REPAIR_ROUTES,
                    allowed_sources={"contract"},
                )
            )
        )

    def _authorized_direct_source_verdict(
        self,
        record: FailureRecord,
        repair_scope: Mapping[str, Any],
        *,
        allowed_legacy_routes: frozenset[str],
        allowed_sources: set[str],
    ) -> bool:
        key = repair_scope.get("source_verdict_key")
        legacy_route = repair_scope.get("legacy_route")
        if (
            not isinstance(key, str)
            or not isinstance(legacy_route, str)
            or legacy_route not in allowed_legacy_routes
            or record.observation.source not in allowed_sources
        ):
            return False
        match = _DIRECT_ROUTE_SOURCE_RE.match(key)
        if match is None:
            return False
        group_idx = record.observation.group_idx
        if group_idx is None or int(match.group("group_idx")) != int(group_idx):
            return False
        return True


# --- Slice 11i -- pure decision-payload adapter helpers --------------------
# Moved byte-for-byte from `workflows/develop/phases/implementation.py` in
# Slice 11i. Per `docs/execution-control-plane/11-refactor-map.md` § "Boundary-
# level API contracts" row for `execution/failure_router.py`
# ("FailureRouter.decide(failure_id) -> RouteDecision. Typed failure taxonomy,
# retry budgets, deterministic route selection, quiesce/escalation decisions."),
# the typed→legacy `RouteDecision`-to-dict adapter functions belong on the
# failure-router surface: they read ONLY a `RouteDecision`-shaped object via
# `getattr` (duck-typed) and return a flat dict payload that legacy callers
# (`implementation.py` direct callers + the persisted `route_decision` payload
# on every `runtime_failure_context` evidence row -- the REAL typed budget
# source the Slice 10c-2 `_typed_retry_budgets` reads) consume.
#
# The phase-level failure-router PORT surface (`_failure_router_for_runner`,
# `_typed_direct_route_payload`, `_route_merge_queue_drain_failure`, and the
# `runtime_provider` retry-route adapter at `implementation.py:13190`) STAYS
# in `implementation.py` per the prompt hard rule against splitting non-pure
# helpers -- those are runner+feature/services-coupled (each takes a
# `WorkflowRunner` + `Feature` + reads `runner.services` /
# `runner._failure_router_port` and builds a typed `FailureObservation`
# observation around it).
def _route_decision_retry_budget_payload(
    decision: Any,
    *,
    action: str,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    remaining = max(0, int(getattr(decision, "budget_remaining", 0) or 0))
    ordinal = max(0, int(getattr(decision, "reservation_ordinal", 0) or 0))
    if max_attempts is None:
        max_attempts = remaining + ordinal if ordinal else remaining
    return {
        "route": action,
        "budget_key": str(getattr(decision, "budget_key", "") or ""),
        "max_attempts": max_attempts,
        "max_retries": max_attempts,
        "remaining_attempts": remaining,
        "idempotency_key": str(getattr(decision, "idempotency_key", "") or ""),
        "reservation_ordinal": ordinal,
        "budget_exhausted": bool(getattr(decision, "budget_exhausted", False)),
    }


def _route_decision_compat_payload(
    decision: Any,
    *,
    failure_class: str,
    failure_type: str,
    max_attempts: int | None = None,
    legacy_route: str = "",
    legacy_failure_type: str = "",
) -> dict[str, Any]:
    action = str(getattr(decision, "action", "") or "")
    budget = _route_decision_retry_budget_payload(
        decision,
        action=action,
        max_attempts=max_attempts,
    )
    payload = {
        "failure_id": getattr(decision, "failure_id", None),
        "typed_failure_id": getattr(decision, "failure_id", None),
        "route_decision_id": getattr(decision, "route_decision_id", None),
        "route": action,
        "action": action,
        "failure_class": failure_class,
        "failure_type": failure_type,
        "operator_required": action == "operator_required",
        "retryable": action.startswith("retry_"),
        "budget_remaining": budget["remaining_attempts"],
        "budget_exhausted": bool(getattr(decision, "budget_exhausted", False)),
        "reason": str(getattr(decision, "reason", "") or ""),
        "required_evidence_ids": list(getattr(decision, "required_evidence_ids", []) or []),
        "stable_signature_hash": str(getattr(decision, "signature_hash", "") or ""),
        "signature_hash": str(getattr(decision, "signature_hash", "") or ""),
        "idempotency_key": str(getattr(decision, "idempotency_key", "") or ""),
        "budget_key": budget["budget_key"],
        "reservation_ordinal": budget["reservation_ordinal"],
        "retry_budget": budget,
        "repair_scope": dict(getattr(decision, "repair_scope", {}) or {}),
    }
    if legacy_route:
        payload["legacy_route"] = legacy_route
    if legacy_failure_type:
        payload["legacy_failure_type"] = legacy_failure_type
    return payload


__all__ = [
    "CLASS_RETRY_BUDGETS",
    "FAILURE_CLASSES",
    "FAILURE_SEVERITIES",
    "FAILURE_SOURCES",
    "FAILURE_TYPES",
    "FAILURE_TYPE_POLICIES",
    "ROUTE_ACTIONS",
    "ROUTE_TABLE",
    "FailureClass",
    "FailureObservation",
    "FailureRecord",
    "FailureRoutePolicy",
    "FailureRouter",
    "FailureRouterError",
    "FailureRouterPort",
    "FailureSeverity",
    "FailureSource",
    "FailureType",
    "FailureTypePolicy",
    "IdempotencyConflict",
    "InMemoryFailureRouterPort",
    "RetryBudgetState",
    "RouteAction",
    "RouteDecision",
    "RouteRecord",
    "UnknownFailurePolicyError",
    "_route_decision_compat_payload",
    "_route_decision_retry_budget_payload",
    "build_failure_signature",
    "failure_idempotency_key",
    "route_budget_key",
    "route_idempotency_key",
    "stable_digest",
    "stable_signature_hash",
]
