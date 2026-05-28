# In-Flight Execution-Control Adoption Migration Guide

This guide is the operator playbook for adopting an already-running feature into
strict execution-control resume behavior. It exists for rare one-off migrations.
Runtime code must not silently repair old DAGs, infer repo identity, or skip
historical checkpoints without an explicit adoption marker.

## Safe Boundary Requirements

Adopt only at a checkpoint or quiesce boundary where all of the following are
true:

- The boundary `dag-group:{N}` artifact exists and has `verdict: "approved"`.
- `dag-group:{N}.task_ids` exactly names the effective group at that boundary.
- Every boundary task has exactly one result and each result status is
  `completed`.
- The root `dag` artifact has durable identity: artifact row id and SHA-256.
- Active regroup state is either absent or explicitly identified by typed row /
  active-marker artifact ids.
- The next group is known: `next_effective_group_idx == N + 1`.
- No manual root-DAG edits, checkpoint repairs, or pause artifact deletion are
  used to make the boundary look valid.

## Required Artifact

Write exactly one marker:

```text
execution-control-adoption:{feature_id}
```

The marker body is an `InFlightAdoptionRecord` with:

- `status: "adopted"`
- `feature_id`
- `candidate_commit`
- `deploy_artifact_id`
- `legacy_root_dag_artifact_id`
- `legacy_root_dag_sha256`
- `completed_checkpoint_range: [0, N]`
- `next_effective_group_idx: N + 1`
- `active_regroup_artifact_ids`
- `projection_digest`
- `adopted_at`
- `pre_adoption_baseline`

The `pre_adoption_baseline` should seal the legacy debt: boundary group id,
boundary checkpoint hash, task ids, result statuses, active regroup metadata,
and an explicit note that groups in the completed range are skipped because of
the adoption marker, not because the runtime is allowed to revalidate legacy
proofless checkpoints.

## Validation Checklist

Before writing the marker:

- Run `build_in_flight_adoption_preflight(...)` from
  `iriai_build_v2.execution_control.adoption_migration`.
- Confirm `preflight.ready is True`.
- Inspect `preflight.snapshot.pre_adoption_baseline`.
- Construct `InFlightAdoptionRecord(**preflight.adoption_record_fields)`.
- Write the marker through the artifact store only after human approval of the
  boundary and metadata.

After writing the marker:

- Resume should jump to `next_effective_group_idx`.
- Groups inside `completed_checkpoint_range` must not be contract-recompiled.
- Any group after the boundary uses strict contract compilation and must carry
  explicit repo identity.
- Missing, corrupt, or mismatched markers should block deterministically and
  point back to this guide.

## Failure Modes

- Missing marker: resume blocks before dispatch.
- Corrupt marker body: resume blocks before dispatch.
- Marker `feature_id` mismatch: resume blocks before dispatch.
- `completed_checkpoint_range` not starting at `0`: resume blocks.
- `next_effective_group_idx != completed_checkpoint_range.end + 1`: resume
  blocks.
- Boundary group missing or not approved: preflight returns typed blockers.
- Boundary result coverage incomplete or non-completed: preflight returns typed
  blockers.
- Root DAG row id / hash missing: preflight returns typed blockers.
- Ambiguous regroup active state: preflight returns typed blockers.
- Post-boundary task lacks `repo_id` or `repo_path`: strict contract compile
  fails with `contract_compile/contract_invalid_path`.

## Rollback Posture

Do not delete the adoption marker as a repair tactic. If strict resume blocks,
fix the marker inputs or the post-boundary task metadata and retry. If a feature
must be abandoned, quiesce it explicitly and record the operator decision; do
not mutate historical root DAG or checkpoint artifacts to satisfy the new
runtime.

## Worked Example: `8ac124d6` At Group 77

The final legacy boundary for `8ac124d6` is group `77`.

- Completed checkpoint range: `[0, 77]`
- First strict group: `78`
- Required marker: `execution-control-adoption:8ac124d6`
- Marker boundary fields:
  - `completed_checkpoint_range: [0, 77]`
  - `next_effective_group_idx: 78`
  - `status: "adopted"`

Group `77` evidence should be recorded in `pre_adoption_baseline`, including
the root DAG id/hash, the `dag-group:77` artifact id/hash, commit hash,
the four group task ids, their completed statuses, and active regroup metadata.
After this marker exists, strict resume
must not rescan or contract-recompile groups `0..77`; it starts at group `78`.
