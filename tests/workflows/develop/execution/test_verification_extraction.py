"""Slice 11g -- extraction proof for `execution/verification.py` extension.

Verifies the doc-11 § "How To Use This Map" four-question contract for
the 19 pure verification-domain helper extraction:

1. What behavior moved: 19 pure helpers --
   `_dag_verify_stage_from_projection_key`,
   `_dag_verify_graph_artifact_key`, `_pydantic_json`,
   `_synthetic_verification_verdict_id`,
   `_dag_verify_graph_payload_covers_projection`,
   `_dag_verify_graph_payload_has_durable_projection`,
   `_dag_verify_graph_digest`,
   `_dag_verify_graph_projection_metadata`,
   `_dag_verify_graph_payload_without_durable`,
   `_dag_verify_graph_store_payload_digest`,
   `_dag_verify_graph_payload_digest_for_proof`,
   `_dag_verify_graph_int`, `_dag_verify_graph_edge_ids`,
   `_dag_verify_graph_durable_metadata_from_reload`,
   `_dag_verify_graph_payload_for_projection`,
   `_dag_verify_graph_ref`, `_dag_verify_graph_lineage_payload`,
   `_prefix_lens_issue`, `_prefix_lens_gap` -- moved from
   `workflows/develop/phases/implementation.py` to
   `workflows/develop/execution/verification.py`. The pre-11g
   verification-graph orchestration surface (the
   `VerificationGraphAttempt`, `EvidenceNode`, `EvidenceEdge`,
   `ReadBudgetReport`, `VerifierCompatibilityLinks`,
   `AggregateBuildResult`, `GraphApprovalProof` types + the
   `normalize_raw_verifier_result`,
   `normalize_lens_verifier_result`,
   `merge_verdicts_deterministically`, `build_aggregate_verdict`,
   `build_graph_approval_proof`, `map_verifier_failure`,
   `stable_digest` factories) already in `verification.py` is
   UNTOUCHED -- Slice 11g EXTENDS, never modifies.
2. Which legacy import names still work: every existing
   `from iriai_build_v2.workflows.develop.phases.implementation import X`
   for one of the 19 moved names keeps resolving to the SAME object as
   the canonical definition in `execution/verification.py` (the shim
   is `is`-equivalent, not a copy). `monkeypatch.setattr(
   implementation_module, X, ...)` continues to mutate the SAME
   binding any direct `from execution.verification import X` reader
   sees.
3. Which targeted tests prove the new facade and the compatibility
   shim: THIS file is one of them; it pins every moved name's shim
   equivalence and behaviorally smoke-tests each moved helper.
4. Why is the PR still refactor-only: nothing else moves. The 19
   pure helpers moved byte-for-byte. The phase-level verification
   PORT surface (the async runner+feature-coupled artifact/store
   helpers `_get_artifact_text`, `_load_verified_dag_verification_
   graph_projection`, `_recover_dag_verification_graph_payload_
   from_store`, `_persist_dag_verification_graph_payload`,
   `_record_dag_verification_graph_artifact`,
   `_put_dag_verify_artifact`,
   `_validate_dag_verification_graph_payload`,
   `_validate_dag_verification_graph_rejected_payload`,
   `_record_dag_verifier_runtime_failure`,
   `_run_checkpoint_required_dag_verify_lenses`,
   `_require_dag_verification_graph_approval`,
   `_verify_and_fix_group`, `_run_expanded_dag_verify_lenses`,
   `_merge_dag_expanded_verify_verdicts`, `_verify`,
   `_verify_enhancements`, `_single_rca_fix_verify`; the env-coupled
   `_dag_expanded_verify_enabled`, `_dag_verify_lens_specs`,
   `_dag_verify_required_lens_slugs`,
   `_dag_verify_read_budget_for_projection`; the
   `_dag_verification_graph_blocker_route_payload` consumer of the
   impl.py-local `_json_object_from_text`; and the
   `_dag_verification_graph_attempt_from_payload` consumer of the
   impl.py-local `VerifyEvidenceNode`/`VerifyEvidenceEdge`/
   `VerificationGraphAttempt` rename aliases) is genuinely PHASE-
   LEVEL and CORRECTLY stays in `implementation.py` per the prompt
   hard rule against splitting non-pure helpers.
"""

from __future__ import annotations

import json

import pytest


# Each entry is a name moved from `implementation.py` to
# `execution/verification.py` in Slice 11g. The order is the import-
# line order in the shim block in `implementation.py` (the Slice-11g
# block) so a grep over either file lists the names in the same
# order.
MOVED_NAMES = [
    "_dag_verify_graph_artifact_key",
    "_dag_verify_graph_digest",
    "_dag_verify_graph_durable_metadata_from_reload",
    "_dag_verify_graph_edge_ids",
    "_dag_verify_graph_int",
    "_dag_verify_graph_lineage_payload",
    "_dag_verify_graph_payload_covers_projection",
    "_dag_verify_graph_payload_digest_for_proof",
    "_dag_verify_graph_payload_for_projection",
    "_dag_verify_graph_payload_has_durable_projection",
    "_dag_verify_graph_payload_without_durable",
    "_dag_verify_graph_projection_metadata",
    "_dag_verify_graph_ref",
    "_dag_verify_graph_store_payload_digest",
    "_dag_verify_stage_from_projection_key",
    "_prefix_lens_gap",
    "_prefix_lens_issue",
    "_pydantic_json",
    "_synthetic_verification_verdict_id",
]


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_shim_re_export_is_same_object_as_new_canonical(name: str) -> None:
    """Every moved helper imported via the OLD path is the SAME object
    as the import via the NEW canonical path. Proves the shim is a re-
    export, not a copy. Locks the monkeypatch target equivalence --
    `monkeypatch.setattr(implementation_module, name, ...)` will mutate
    the SAME function object that any direct
    `from execution.verification import name` reader sees.
    """

    from iriai_build_v2.workflows.develop import execution as execution_pkg
    from iriai_build_v2.workflows.develop.execution import (
        verification as verification_mod,
    )
    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    legacy = getattr(impl_mod, name)
    canonical = getattr(verification_mod, name)
    assert legacy is canonical, (
        f"shim drift: implementation.{name} is not the same object as "
        f"execution.verification.{name}"
    )
    # `execution_pkg` is imported only to ensure the package import chain
    # works end-to-end (no side-effect import errors).
    _ = execution_pkg


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_canonical_module_is_verification(name: str) -> None:
    """The moved function objects' `__module__` is the new canonical
    `iriai_build_v2.workflows.develop.execution.verification` -- not the
    legacy `...phases.implementation`. Proves the definition genuinely
    moved rather than being re-aliased from the old module.
    """

    from iriai_build_v2.workflows.develop.execution import (
        verification as verification_mod,
    )

    canonical = getattr(verification_mod, name)
    assert canonical.__module__ == (
        "iriai_build_v2.workflows.develop.execution.verification"
    ), (
        f"{name}.__module__ = {canonical.__module__!r}; expected the "
        "new verification-module path"
    )


def test_no_back_import_from_implementation() -> None:
    """`execution/verification.py` MUST NOT import from
    `..phases.implementation` (the compatibility arrow points IN to
    `verification.py` from `implementation.py`, NEVER the reverse).
    Locks the dependency-direction invariant for the Slice 11
    extraction order: `implementation.py` may import from
    `verification.py` via the Slice-11g shim block; `verification.py`
    must depend only on stdlib + `models.outputs` + `iriai_compose` +
    sibling execution modules.
    """

    from iriai_build_v2.workflows.develop.execution import verification

    source_path = verification.__file__
    assert source_path is not None
    with open(source_path, encoding="utf-8") as fh:
        source = fh.read()
    assert "from ..phases.implementation" not in source, (
        "verification.py must not import from ..phases.implementation"
    )
    assert "from iriai_build_v2.workflows.develop.phases.implementation" not in source, (
        "verification.py must not import from "
        "iriai_build_v2.workflows.develop.phases.implementation"
    )


def test_dag_verify_stage_from_projection_key_canonical_routes() -> None:
    """`_dag_verify_stage_from_projection_key("dag-verify:<feature>:<stage>")`
    returns `<stage>`; mismatched prefixes or wrong arity return
    `"unknown"`.
    """

    from iriai_build_v2.workflows.develop.execution.verification import (
        _dag_verify_stage_from_projection_key,
    )

    assert _dag_verify_stage_from_projection_key("dag-verify:feat-1:initial") == "initial"
    assert _dag_verify_stage_from_projection_key("dag-verify:feat-1:retry-2") == "retry-2"
    # Extra colons in the stage portion are absorbed by maxsplit=2.
    assert _dag_verify_stage_from_projection_key("dag-verify:feat-1:retry:2") == "retry:2"
    # Wrong prefix.
    assert _dag_verify_stage_from_projection_key("foo:feat-1:initial") == "unknown"
    # Missing stage component.
    assert _dag_verify_stage_from_projection_key("dag-verify:feat-1") == "unknown"
    # Empty input.
    assert _dag_verify_stage_from_projection_key("") == "unknown"


def test_dag_verify_graph_artifact_key_format() -> None:
    """`_dag_verify_graph_artifact_key(group_idx, stage)` returns
    `f"dag-verify-graph:g{group_idx}:{stage}"` deterministically.
    """

    from iriai_build_v2.workflows.develop.execution.verification import (
        _dag_verify_graph_artifact_key,
    )

    assert _dag_verify_graph_artifact_key(0, "initial") == "dag-verify-graph:g0:initial"
    assert _dag_verify_graph_artifact_key(7, "retry-2") == "dag-verify-graph:g7:retry-2"
    assert _dag_verify_graph_artifact_key(42, "x") == "dag-verify-graph:g42:x"


def test_pydantic_json_dispatches_to_model_dump() -> None:
    """`_pydantic_json(value)` returns `value.model_dump(mode="json")`
    when the value has a callable `model_dump`; otherwise returns
    `to_str(value)`.
    """

    from iriai_build_v2.models.outputs import Issue
    from iriai_build_v2.workflows.develop.execution.verification import (
        _pydantic_json,
    )

    issue = Issue(
        file="a.py",
        line=1,
        severity="minor",
        description="example",
    )
    dumped = _pydantic_json(issue)
    assert isinstance(dumped, dict)
    assert dumped["file"] == "a.py"
    assert dumped["severity"] == "minor"

    # Non-pydantic value falls back to to_str.
    plain = _pydantic_json("hello")
    assert isinstance(plain, str)
    assert "hello" in plain


def test_synthetic_verification_verdict_id_is_deterministic() -> None:
    """`_synthetic_verification_verdict_id(projection_key, verdict,
    suffix=...)` returns a deterministic positive int from the digest
    of `{projection_key, suffix, verdict}`.
    """

    from iriai_build_v2.models.outputs import Verdict
    from iriai_build_v2.workflows.develop.execution.verification import (
        _synthetic_verification_verdict_id,
    )

    verdict = Verdict(approved=False, summary="boom")
    a = _synthetic_verification_verdict_id("dag-verify:f:initial", verdict)
    b = _synthetic_verification_verdict_id("dag-verify:f:initial", verdict)
    assert a == b
    # A different suffix produces a different id.
    c = _synthetic_verification_verdict_id("dag-verify:f:initial", verdict, suffix="x")
    assert c != a
    # Ints are positive (< 2^48).
    assert a > 0
    assert c > 0


def test_dag_verify_graph_int_roundtrips_safely() -> None:
    """`_dag_verify_graph_int` returns `int(value)` on valid input and
    `None` on type/value errors. Pure parser.
    """

    from iriai_build_v2.workflows.develop.execution.verification import (
        _dag_verify_graph_int,
    )

    assert _dag_verify_graph_int(7) == 7
    assert _dag_verify_graph_int("42") == 42
    assert _dag_verify_graph_int(3.7) == 3
    assert _dag_verify_graph_int(None) is None
    assert _dag_verify_graph_int("nope") is None
    assert _dag_verify_graph_int([1, 2]) is None


def test_dag_verify_graph_edge_ids_normalizes_lists() -> None:
    """`_dag_verify_graph_edge_ids(value)` returns a list of canonical
    str ids; non-list input returns `[]`; non-int items pass through
    as `str(item)`.
    """

    from iriai_build_v2.workflows.develop.execution.verification import (
        _dag_verify_graph_edge_ids,
    )

    assert _dag_verify_graph_edge_ids([1, 2, 3]) == ["1", "2", "3"]
    assert _dag_verify_graph_edge_ids(["1", "2"]) == ["1", "2"]
    assert _dag_verify_graph_edge_ids([1, "x", 3]) == ["1", "x", "3"]
    assert _dag_verify_graph_edge_ids(None) == []
    assert _dag_verify_graph_edge_ids("not-a-list") == []
    assert _dag_verify_graph_edge_ids([]) == []


def test_dag_verify_graph_payload_has_durable_projection_predicate() -> None:
    """`_dag_verify_graph_payload_has_durable_projection(payload)` is
    True iff `durable_projection.persisted is True` AND either
    `typed_row_id` or `projection_row_id` is truthy AND if `edge_ids`
    is present it is a list.
    """

    from iriai_build_v2.workflows.develop.execution.verification import (
        _dag_verify_graph_payload_has_durable_projection,
    )

    # Happy path.
    assert _dag_verify_graph_payload_has_durable_projection(
        {
            "durable_projection": {
                "persisted": True,
                "typed_row_id": 1,
                "evidence_edge_ids": ["1"],
            }
        }
    )
    # projection_row_id is also accepted.
    assert _dag_verify_graph_payload_has_durable_projection(
        {
            "durable_projection": {
                "persisted": True,
                "projection_row_id": 5,
            }
        }
    )
    # Not persisted.
    assert not _dag_verify_graph_payload_has_durable_projection(
        {"durable_projection": {"persisted": False, "typed_row_id": 1}}
    )
    # Missing both row ids.
    assert not _dag_verify_graph_payload_has_durable_projection(
        {"durable_projection": {"persisted": True}}
    )
    # Non-list edge_ids -> false.
    assert not _dag_verify_graph_payload_has_durable_projection(
        {
            "durable_projection": {
                "persisted": True,
                "typed_row_id": 1,
                "evidence_edge_ids": "nope",
            }
        }
    )
    # Missing durable_projection -> false.
    assert not _dag_verify_graph_payload_has_durable_projection({})


def test_dag_verify_graph_payload_without_durable_drops_field() -> None:
    """`_dag_verify_graph_payload_without_durable(payload)` returns a
    canonical (sort_keys=True) copy of the payload with the
    `durable_projection` key removed.
    """

    from iriai_build_v2.workflows.develop.execution.verification import (
        _dag_verify_graph_payload_without_durable,
    )

    payload = {
        "projection_key": "dag-verify:f:initial",
        "durable_projection": {"persisted": True},
        "nodes": [],
    }
    canonical = _dag_verify_graph_payload_without_durable(payload)
    assert "durable_projection" not in canonical
    assert canonical["projection_key"] == "dag-verify:f:initial"
    assert canonical["nodes"] == []


def test_dag_verify_graph_digest_uses_stable_digest() -> None:
    """`_dag_verify_graph_digest(value)` delegates to
    `verify_graph_stable_digest` (the canonical `stable_digest` in
    `execution/verification.py`) and is therefore deterministic across
    equivalent JSON-shaped inputs.
    """

    from iriai_build_v2.workflows.develop.execution.verification import (
        _dag_verify_graph_digest,
        stable_digest,
    )

    payload = {"projection_key": "dag-verify:f:initial", "nodes": [1, 2, 3]}
    direct = stable_digest(payload)
    indirect = _dag_verify_graph_digest(payload)
    assert direct == indirect
    # Different shapes produce different digests.
    assert _dag_verify_graph_digest({"a": 1}) != _dag_verify_graph_digest({"a": 2})


def test_dag_verify_graph_store_payload_digest_strips_durable() -> None:
    """`_dag_verify_graph_store_payload_digest(payload)` is the
    `_dag_verify_graph_digest` of the payload WITHOUT
    `durable_projection`. Adding/removing the durable field MUST NOT
    change the store digest.
    """

    from iriai_build_v2.workflows.develop.execution.verification import (
        _dag_verify_graph_store_payload_digest,
    )

    base = {"projection_key": "dag-verify:f:initial", "nodes": []}
    with_durable = {**base, "durable_projection": {"persisted": True}}
    assert (
        _dag_verify_graph_store_payload_digest(base)
        == _dag_verify_graph_store_payload_digest(with_durable)
    )


def test_dag_verify_graph_payload_digest_for_proof_strips_proof_self_ref() -> None:
    """`_dag_verify_graph_payload_digest_for_proof(payload)` strips
    `proof.proof_digest` and `proof.graph_payload_digest` before
    digesting, so the digest is stable across re-digesting.
    """

    from iriai_build_v2.workflows.develop.execution.verification import (
        _dag_verify_graph_payload_digest_for_proof,
    )

    payload_a = {
        "projection_key": "dag-verify:f:initial",
        "proof": {"proof_digest": "abc", "graph_payload_digest": "def", "extra": 1},
    }
    payload_b = {
        "projection_key": "dag-verify:f:initial",
        "proof": {"proof_digest": "xyz", "graph_payload_digest": "uvw", "extra": 1},
    }
    assert (
        _dag_verify_graph_payload_digest_for_proof(payload_a)
        == _dag_verify_graph_payload_digest_for_proof(payload_b)
    )


def test_dag_verify_graph_payload_for_projection_validates_shape() -> None:
    """`_dag_verify_graph_payload_for_projection(text, key)` returns
    the parsed payload iff the text parses as JSON AND
    `projection_key` matches AND `nodes` is a list AND `aggregate` is
    a dict. Otherwise returns `None`.
    """

    from iriai_build_v2.workflows.develop.execution.verification import (
        _dag_verify_graph_payload_for_projection,
    )

    valid = json.dumps(
        {
            "projection_key": "dag-verify:f:initial",
            "nodes": [],
            "aggregate": {},
        }
    )
    payload = _dag_verify_graph_payload_for_projection(
        valid, "dag-verify:f:initial"
    )
    assert payload is not None
    assert payload["projection_key"] == "dag-verify:f:initial"

    # Malformed JSON.
    assert _dag_verify_graph_payload_for_projection("{not json", "k") is None
    # Projection key mismatch.
    assert (
        _dag_verify_graph_payload_for_projection(valid, "different-key")
        is None
    )
    # Missing nodes list.
    bad = json.dumps({"projection_key": "k", "aggregate": {}})
    assert _dag_verify_graph_payload_for_projection(bad, "k") is None
    # Missing aggregate dict.
    bad = json.dumps({"projection_key": "k", "nodes": []})
    assert _dag_verify_graph_payload_for_projection(bad, "k") is None


def test_dag_verify_graph_payload_covers_projection_predicate() -> None:
    """`_dag_verify_graph_payload_covers_projection(text, key)` is
    True iff the payload parses, has a `proof.proof_digest` truthy
    value, AND `proof.projection_keys` lists `key`.
    """

    from iriai_build_v2.workflows.develop.execution.verification import (
        _dag_verify_graph_payload_covers_projection,
    )

    covering = json.dumps(
        {
            "projection_key": "dag-verify:f:initial",
            "nodes": [],
            "aggregate": {},
            "proof": {
                "proof_digest": "abc",
                "projection_keys": ["dag-verify:f:initial"],
            },
        }
    )
    assert _dag_verify_graph_payload_covers_projection(
        covering, "dag-verify:f:initial"
    )

    # Missing proof.
    missing_proof = json.dumps(
        {
            "projection_key": "dag-verify:f:initial",
            "nodes": [],
            "aggregate": {},
        }
    )
    assert not _dag_verify_graph_payload_covers_projection(
        missing_proof, "dag-verify:f:initial"
    )
    # Empty proof_digest.
    empty_digest = json.dumps(
        {
            "projection_key": "dag-verify:f:initial",
            "nodes": [],
            "aggregate": {},
            "proof": {
                "proof_digest": "",
                "projection_keys": ["dag-verify:f:initial"],
            },
        }
    )
    assert not _dag_verify_graph_payload_covers_projection(
        empty_digest, "dag-verify:f:initial"
    )
    # Projection key not in list.
    wrong_keys = json.dumps(
        {
            "projection_key": "dag-verify:f:initial",
            "nodes": [],
            "aggregate": {},
            "proof": {
                "proof_digest": "abc",
                "projection_keys": ["dag-verify:f:retry-2"],
            },
        }
    )
    assert not _dag_verify_graph_payload_covers_projection(
        wrong_keys, "dag-verify:f:initial"
    )


def test_dag_verify_graph_ref_collects_canonical_attrs() -> None:
    """`_dag_verify_graph_ref(value)` extracts a stable set of id/digest
    attributes from an object (via `getattr`) or its `.payload` dict.
    Empty/None/empty-list values are excluded; payload values do not
    override direct attributes (first-seen-wins).
    """

    from iriai_build_v2.workflows.develop.execution.verification import (
        _dag_verify_graph_ref,
    )

    class _Obj:
        id = "abc"
        task_id = "t1"
        contract_id = None  # excluded
        snapshot_id = ""    # excluded
        patch_summary_id = []  # excluded
        diff_sha256 = "deadbeef"
        payload = {
            "task_id": "OVERRIDE",  # already present as direct attr; should not override
            "contract_id": "c1",     # not present as direct attr; gets added
            "base_snapshot_id": "s1",
        }

    ref = _dag_verify_graph_ref(_Obj())
    assert ref == {
        "id": "abc",
        "task_id": "t1",
        "diff_sha256": "deadbeef",
        "contract_id": "c1",
        "base_snapshot_id": "s1",
    }


def test_dag_verify_graph_lineage_payload_canonical_shape() -> None:
    """`_dag_verify_graph_lineage_payload(...)` returns the expected
    canonical lineage shape with a deterministic `lineage_digest`.
    """

    from iriai_build_v2.models.outputs import ImplementationResult, ImplementationTask
    from iriai_build_v2.workflows.develop.execution.verification import (
        _dag_verify_graph_lineage_payload,
    )

    task = ImplementationTask(
        id="t1",
        name="Task one",
        description="do thing",
    )
    result = ImplementationResult(
        task_id="t1",
        summary="done",
        status="completed",
        files_created=["b.py", "a.py"],
        files_modified=["c.py"],
        notes="ok",
    )
    payload = _dag_verify_graph_lineage_payload(
        feature_id="f",
        projection_key="dag-verify:f:initial",
        dag_sha256="deadbeef",
        group_idx=0,
        stage="initial",
        tasks=[task],
        results=[result],
        contracts_by_task_id={},
        workspace_snapshots=[],
    )
    assert payload["feature_id"] == "f"
    assert payload["projection_key"] == "dag-verify:f:initial"
    assert payload["dag_sha256"] == "deadbeef"
    assert payload["group_idx"] == 0
    assert payload["stage"] == "initial"
    assert payload["task_ids"] == ["t1"]
    assert payload["result_refs"][0]["task_id"] == "t1"
    assert payload["result_refs"][0]["files_created"] == ["a.py", "b.py"]
    assert isinstance(payload["lineage_digest"], str)
    assert len(payload["lineage_digest"]) == 64


def test_prefix_lens_issue_prepends_lens_label() -> None:
    """`_prefix_lens_issue(spec, issue)` returns a copy of `issue` with
    `description` prefixed by `[{spec.label} Lens]`. The original
    issue is not mutated.
    """

    from iriai_build_v2.models.outputs import Issue
    from iriai_build_v2.workflows.develop.execution.types import DagVerifyLensSpec
    from iriai_build_v2.workflows.develop.execution.verification import (
        _prefix_lens_issue,
    )

    class _DummyActor:
        pass

    spec = DagVerifyLensSpec(
        slug="security-boundary",
        label="Security & Boundary",
        actor=_DummyActor(),
        focus="x",
    )
    issue = Issue(
        file="a.py", line=1, severity="minor", description="original"
    )
    prefixed = _prefix_lens_issue(spec, issue)
    assert prefixed.description == "[Security & Boundary Lens] original"
    # Original untouched.
    assert issue.description == "original"
    # Other fields preserved.
    assert prefixed.file == issue.file
    assert prefixed.severity == issue.severity


def test_prefix_lens_gap_prepends_lens_label() -> None:
    """`_prefix_lens_gap(spec, gap)` returns a copy of `gap` with
    `description` prefixed by `[{spec.label} Lens]`. The original gap
    is not mutated.
    """

    from iriai_build_v2.models.outputs import Gap
    from iriai_build_v2.workflows.develop.execution.types import DagVerifyLensSpec
    from iriai_build_v2.workflows.develop.execution.verification import (
        _prefix_lens_gap,
    )

    class _DummyActor:
        pass

    spec = DagVerifyLensSpec(
        slug="contract-protocol",
        label="Contract & Protocol",
        actor=_DummyActor(),
        focus="x",
    )
    gap = Gap(
        category="acceptance",
        description="missing test",
        severity="minor",
        plan_reference="AC-1",
    )
    prefixed = _prefix_lens_gap(spec, gap)
    assert prefixed.description == "[Contract & Protocol Lens] missing test"
    # Original untouched.
    assert gap.description == "missing test"
    # Other fields preserved.
    assert prefixed.category == gap.category
    assert prefixed.severity == gap.severity


def test_dag_verify_graph_projection_metadata_extracts_typed_row_state() -> None:
    """`_dag_verify_graph_projection_metadata(result)` walks `result.row`,
    `result.projection_links`, and `result.graph` (or
    `result.evidence_graph`) to assemble the canonical metadata dict
    with `persisted=True` + the typed/projection ids + the canonical
    payload keys.
    """

    from iriai_build_v2.workflows.develop.execution.verification import (
        _dag_verify_graph_projection_metadata,
    )

    class _Row:
        id = 7
        payload = {
            "aggregate_evidence_node_id": 11,
            "graph_payload_digest": "abc",
        }

    class _Link:
        id = 13
        artifact_id = 17
        payload = {"proof_digest": "pd"}

    class _Graph:
        id = 19
        aggregate_evidence_node_id = 23
        proof_digest = "graph-pd"
        graph_payload_digest = "graph-gpd"

    class _Result:
        row = _Row()
        projection_links = [_Link()]
        graph = _Graph()
        evidence_edge_ids = [1, 2, 3]

    metadata = _dag_verify_graph_projection_metadata(_Result())
    assert metadata["persisted"] is True
    assert metadata["typed_row_id"] == 7
    assert metadata["projection_link_ids"] == [13]
    assert metadata["compatibility_artifact_ids"] == [17]
    # row payload fields.
    assert metadata["aggregate_evidence_node_id"] == 23  # graph wins
    assert metadata["graph_payload_digest"] == "graph-gpd"  # graph wins
    # link payload fields.
    assert metadata["proof_digest"] == "graph-pd"  # graph wins over link
    # evidence_edge_ids list.
    assert metadata["evidence_edge_ids"] == [1, 2, 3]


def test_dag_verify_graph_durable_metadata_from_reload_persisted() -> None:
    """`_dag_verify_graph_durable_metadata_from_reload(result, verified)`
    sets `persisted=True` only when the typed_row_id + evidence_graph_id
    + projection_link_ids + edge-lineage criteria are all satisfied.
    """

    from iriai_build_v2.workflows.develop.execution.verification import (
        _dag_verify_graph_durable_metadata_from_reload,
    )

    verified = {
        "graph": {
            "id": 19,
            "execution_journal_row_id": 7,
            "aggregate_evidence_node_id": 23,
            "proof_digest": "pd",
            "graph_payload_digest": "gpd",
        },
        "required_edges": [
            {"graph_edge_id": 100, "id": 1},
            {"graph_edge_id": 101, "id": 2},
        ],
        "projection_links": [{"id": 13, "artifact_id": 17}],
    }
    metadata = _dag_verify_graph_durable_metadata_from_reload(None, verified)
    assert metadata["persisted"] is True
    assert metadata["typed_row_id"] == 7
    assert metadata["evidence_graph_id"] == 19
    assert metadata["projection_link_ids"] == [13]
    assert metadata["evidence_edge_ids"] == ["100", "101"]


def test_slice_11g_shim_block_completeness_probe() -> None:
    """The Slice-11g shim block at the head of `implementation.py`
    re-exports EVERY name in MOVED_NAMES. This locks the shim-block
    completeness: a future move that adds a new name without
    updating the shim block would let the cluster split across two
    canonical modules (one in `verification.py`, one still in
    `implementation.py`), breaking the doc-11 boundary.
    """

    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    for name in MOVED_NAMES:
        assert hasattr(impl_mod, name), (
            f"Slice-11g shim drift: implementation.py is missing the "
            f"re-export of {name!r}"
        )


def test_verification_module_lives_in_execution_package() -> None:
    """Slice 11g pins the cluster ownership: `verification.py` lives in
    `iriai_build_v2.workflows.develop.execution`, not in any other
    package. A future re-organization that moves it elsewhere would
    break the doc-11 cross-module dependency direction.
    """

    from iriai_build_v2.workflows.develop.execution import (
        verification as verification_mod,
    )

    assert (
        verification_mod.__name__
        == "iriai_build_v2.workflows.develop.execution.verification"
    )
