"""Slice 11l -- extraction proof for `execution/post_dag_gates.py` CREATE.

Verifies the doc-11 § "How To Use This Map" four-question contract for the
pure post-DAG-gate proof-primitive cluster extraction:

1. What behavior moved: eight pure post-DAG-gate primitives --
   ``_implementation_report_metadata(*, tree_digest, report_url,
   backlog_url, backlog, report_body_sha256, publish_status) -> dict[str,
   Any]`` (the implementation-report-metadata-v1 dict builder for the
   feature-level implementation-report gate),
   ``_notify_delivery_id(feature, tree_digest, notification) -> str`` (the
   SHA-256 hash factory for the post-DAG `notify` gate delivery-id;
   ``feature`` is shape-only via ``getattr(feature, "id", "")``),
   ``_source_push_proof_key() -> str`` (the dag-source-push-proof artifact-
   key constant factory),
   ``_source_push_proof_digest(payload) -> str`` (the SHA-256 over the
   proof payload excluding ``proof_digest``),
   ``_source_push_base_proof(prior, *, repos_root, tree_digest,
   expected_origins) -> dict[str, Any]`` (the base-proof builder for the
   dag-source-push-proof-v1 schema),
   ``_finalize_source_push_proof(payload) -> dict[str, Any]`` (the proof-
   digest finalizer that fills in ``proof_digest``),
   ``_source_push_prior_proof_matches(prior_record, *, repo, tree_digest,
   branch, local_head, remote_ref, expected_origin, actual_origin) ->
   bool`` (the prior-record matcher),
   ``_source_push_proof_records_are_self_consistent(proof, tree_digest) ->
   bool`` (the proof-records self-consistency validator) -- moved byte-for-
   byte from ``workflows/develop/phases/implementation.py`` to the NEW
   canonical module ``workflows/develop/execution/post_dag_gates.py``
   (CREATED by Slice 11l; mirrors the Slice-11a ``types.py`` CREATE
   pattern -- no pre-existing surface to preserve).

2. Which legacy import names still work: every existing
   ``from iriai_build_v2.workflows.develop.phases.implementation import X``
   for one of the eight moved names keeps resolving to the SAME object as
   the canonical definition in ``execution/post_dag_gates.py`` (the shim
   is ``is``-equivalent, not a copy). ``monkeypatch.setattr(
   implementation_module, X, ...)`` continues to mutate the SAME function
   object that any direct ``from execution.post_dag_gates import X``
   reader sees. The moved names are externally consumed by 14 sites in
   ``tests/workflows/test_workflow_quiesce.py`` (via
   ``implementation_module._source_push_proof_key()``,
   ``implementation_module._finalize_source_push_proof(...)``,
   ``implementation_module._notify_delivery_id(...)``,
   ``monkeypatch.setattr(post_test_module, "_notify_delivery_id", ...)``)
   and 9 sites in ``tests/workflows/test_dag_expanded_verify.py`` (via
   ``implementation_module._source_push_proof_key()``,
   ``implementation_module._finalize_source_push_proof(...)``); each one
   continues working through the shim.

3. Which targeted tests prove the new facade and the compatibility shim:
   THIS file is the proof; it pins every moved name's shim equivalence,
   ``__module__`` rebinding, behavioral smoke against each of the eight
   primitives (the implementation-report-metadata dict-builder; the
   notify-delivery ID hash determinism; the source-push proof-key
   constant; the proof-digest SHA-256 determinism; the base-proof builder
   with `prior=None` and with a stale-prior reset; the finalize round-
   trip; the prior-proof matcher accept + reject paths; the proof-
   records self-consistency accept + reject paths), a cluster-ownership
   pin against the 12 sibling execution modules, a shim-block
   completeness probe, and a back-import guard against
   ``post_dag_gates.py`` ever importing from ``implementation.py``.

4. Why is the PR still refactor-only: nothing else moves. The eight pure
   post-DAG-gate primitives moved byte-for-byte; no contract change, no
   behavior change. The phase-level post-DAG-gates PORT surface (the
   ``_get_feature_root``+subprocess-coupled ``_post_dag_gate_tree_digest``
   / ``_current_post_dag_gate_tree_digest`` family, the
   ``_record_typed_verification_gate_node`` /
   ``_execution_control_store_for_runner``-coupled
   ``_post_dag_gate_is_fresh`` + ``_record_post_dag_gate_proof`` artifact
   recorders, the ``_workflow_blocker_text``-coupled async blocker
   recorders, the ``runner.artifacts.put``-coupled artifact writers, the
   ``_json_object_from_text``-coupled (impl.py-local) durable-proof
   readers, the ``_normalize_git_remote_reference``-coupled (impl.py-
   local) source-push origin matchers, the ``_run_git``-coupled async
   ``_source_push_authorized_push_target``, the ``runner.services``-
   coupled async ``_source_push_expected_origins``, the full
   ``_generate_and_publish_implementation_report`` feature-level
   orchestrator, the sandbox-coupled
   ``_bind_post_dag_product_repair_sandbox``, and the ``Callable``-
   callback-coupled async ``_persist_source_push_proof``) is genuinely
   PHASE-LEVEL and CORRECTLY stays in ``implementation.py`` per the
   prompt hard rule against splitting non-pure helpers. Additionally,
   three post-DAG-gate primitives that already moved in earlier Slice
   11 sub-slices stay in their canonical homes: ``_post_dag_gate_proof_
   key`` + ``_notify_gate_proof_extra_from_delivery`` (Slice 11f --
   ``execution/gates.py``); ``_post_dag_repair_group_idx`` (Slice 11h --
   ``execution/repair.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest


# Each entry is a name moved from ``implementation.py`` to
# ``execution/post_dag_gates.py`` in Slice 11l. The order is the import-
# line order in the Slice-11l shim block in ``implementation.py`` so a grep
# over either file lists the names in the same order.
MOVED_NAMES = [
    "_finalize_source_push_proof",
    "_implementation_report_metadata",
    "_notify_delivery_id",
    "_source_push_base_proof",
    "_source_push_prior_proof_matches",
    "_source_push_proof_digest",
    "_source_push_proof_key",
    "_source_push_proof_records_are_self_consistent",
]

# All eight moved names are module-level functions; each has a
# ``__module__``.
MOVED_CALLABLES = list(MOVED_NAMES)


# -- Identity + module-rebind --------------------------------------------------


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_shim_re_export_is_same_object_as_new_canonical(name: str) -> None:
    """Every moved helper imported via the OLD path is the SAME object as
    the import via the NEW canonical path. Proves the shim is a re-export,
    not a copy. Locks the monkeypatch target equivalence --
    ``monkeypatch.setattr(implementation_module, name, ...)`` will mutate
    the SAME function object that any direct
    ``from execution.post_dag_gates import name`` reader sees.
    """

    from iriai_build_v2.workflows.develop import execution as execution_pkg
    from iriai_build_v2.workflows.develop.execution import (
        post_dag_gates as post_dag_gates_mod,
    )
    from iriai_build_v2.workflows.develop.phases import (
        implementation as impl_mod,
    )

    legacy = getattr(impl_mod, name)
    canonical = getattr(post_dag_gates_mod, name)
    assert legacy is canonical, (
        f"shim drift: implementation.{name} is not the same object as "
        f"execution.post_dag_gates.{name}"
    )
    # ``execution_pkg`` is imported only to ensure the package import chain
    # works end-to-end (no side-effect import errors).
    _ = execution_pkg


@pytest.mark.parametrize("name", MOVED_CALLABLES)
def test_canonical_module_is_post_dag_gates(name: str) -> None:
    """The moved function objects' ``__module__`` is the new canonical
    ``iriai_build_v2.workflows.develop.execution.post_dag_gates`` -- not
    the legacy ``...phases.implementation``. Proves the definition
    genuinely moved rather than being re-aliased from the old module.
    """

    from iriai_build_v2.workflows.develop.execution import (
        post_dag_gates as post_dag_gates_mod,
    )

    canonical = getattr(post_dag_gates_mod, name)
    assert canonical.__module__ == (
        "iriai_build_v2.workflows.develop.execution.post_dag_gates"
    ), (
        f"{name}.__module__ = {canonical.__module__!r}; expected the new "
        "post_dag_gates-module path"
    )


# -- Behavioral smoke ----------------------------------------------------------


class _FakeFeature:
    """Minimal shape-only ``Feature``-compatible stub for
    ``_notify_delivery_id`` which only reads ``feature.id`` via
    ``getattr(feature, "id", "")``.
    """

    def __init__(self, id_: str) -> None:
        self.id = id_


class _FakeBacklogItem:
    """Minimal ``EnhancementItem``-compatible stub for
    ``_implementation_report_metadata`` which only reads the items'
    ``model_dump(mode="json")`` (or falls back to the raw item if no
    ``model_dump`` attr).
    """

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        return dict(self._payload)


class _FakeBacklog:
    """Minimal ``EnhancementBacklog``-compatible stub holding an ``items``
    iterable -- the only attribute read by ``_implementation_report_
    metadata``.
    """

    def __init__(self, items: list) -> None:
        self.items = items


def test_implementation_report_metadata_builds_v1_schema_dict() -> None:
    """``_implementation_report_metadata`` returns a dict carrying every
    required field for the ``implementation-report-metadata-v1`` artifact:
    schema marker, tree_digest, report_url, backlog_url, the model-dumped
    backlog items, report_body_sha256, publish_status. The backlog items
    are model-dumped via ``model_dump(mode="json")`` when the helper finds
    that method on each item (and pass through raw otherwise).
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _implementation_report_metadata,
    )

    backlog = _FakeBacklog(
        items=[
            _FakeBacklogItem({"id": "i-1", "title": "First"}),
            _FakeBacklogItem({"id": "i-2", "title": "Second"}),
            # Raw dict (no ``model_dump`` attr) -- passed through verbatim.
            {"id": "i-3", "title": "Third"},
        ]
    )
    payload = _implementation_report_metadata(
        tree_digest="abc123",
        report_url="https://example/report",
        backlog_url="https://example/backlog",
        backlog=backlog,
        report_body_sha256="def456",
        publish_status="complete",
    )
    assert payload == {
        "artifact_schema": "implementation-report-metadata-v1",
        "tree_digest": "abc123",
        "report_url": "https://example/report",
        "backlog_url": "https://example/backlog",
        "backlog_items": [
            {"id": "i-1", "title": "First"},
            {"id": "i-2", "title": "Second"},
            {"id": "i-3", "title": "Third"},
        ],
        "report_body_sha256": "def456",
        "publish_status": "complete",
    }


def test_implementation_report_metadata_defaults_optional_fields() -> None:
    """The ``report_body_sha256`` + ``publish_status`` parameters default
    to ``""`` and ``"complete"`` respectively. The defaults are part of the
    public contract; a future regression that flips them would silently
    corrupt every legacy artifact reader. Belt-and-braces lock.
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _implementation_report_metadata,
    )

    payload = _implementation_report_metadata(
        tree_digest="t",
        report_url="r",
        backlog_url="b",
        backlog=_FakeBacklog(items=[]),
    )
    assert payload["report_body_sha256"] == ""
    assert payload["publish_status"] == "complete"


def test_notify_delivery_id_is_deterministic_sha256_over_canonical_payload() -> None:
    """``_notify_delivery_id`` returns a hex SHA-256 over a deterministic
    JSON-encoded triple ``(feature_id, tree_digest, notification_sha256)``.
    Same inputs => same hash. The ``feature`` parameter is shape-only --
    only ``getattr(feature, "id", "")`` is read; this is why the helper
    is PURE despite taking a ``Feature``-typed argument.
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _notify_delivery_id,
    )

    feature = _FakeFeature("feat-1")
    hash_a = _notify_delivery_id(feature, "tree-abc", "Notification body")
    hash_b = _notify_delivery_id(feature, "tree-abc", "Notification body")
    assert hash_a == hash_b
    assert len(hash_a) == 64  # SHA-256 hex digest length
    # Different feature.id changes the hash.
    other_hash = _notify_delivery_id(
        _FakeFeature("feat-2"), "tree-abc", "Notification body"
    )
    assert other_hash != hash_a
    # Different notification body changes the hash.
    notif_hash = _notify_delivery_id(feature, "tree-abc", "Different body")
    assert notif_hash != hash_a
    # Different tree_digest changes the hash.
    digest_hash = _notify_delivery_id(feature, "tree-xyz", "Notification body")
    assert digest_hash != hash_a


def test_notify_delivery_id_handles_missing_feature_id_attribute() -> None:
    """A ``feature`` whose ``id`` attribute is missing falls back to ``""``
    (via ``getattr(feature, "id", "")``) -- the helper must never raise on
    the missing-id case. Belt-and-braces probe.
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _notify_delivery_id,
    )

    class _Bare:
        pass

    # No ``.id`` attribute on ``_Bare``; fallback is the empty string.
    hash_value = _notify_delivery_id(_Bare(), "tree", "notify")
    assert len(hash_value) == 64


def test_source_push_proof_key_returns_constant_artifact_key() -> None:
    """``_source_push_proof_key`` returns the artifact-key constant
    ``"dag-source-push-proof"`` -- the artifact-store key under which the
    post-DAG source-push gate's proof payload lives. The constant is used
    by both the proof writer and the durable-proof reader; any drift in
    this value would silently disconnect the producer from the consumer.
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _source_push_proof_key,
    )

    assert _source_push_proof_key() == "dag-source-push-proof"


def test_source_push_proof_digest_is_deterministic_and_excludes_proof_digest_field() -> None:
    """``_source_push_proof_digest`` returns a hex SHA-256 over the proof
    payload with the ``proof_digest`` field stripped. Same payload =>
    same digest, even if ``proof_digest`` was tacked on. This is what
    enables the self-referential proof scheme (the finalize step computes
    the digest and stores it INSIDE the same payload, then the verify
    step recomputes the digest over the payload-minus-proof_digest and
    compares).
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _source_push_proof_digest,
    )

    payload_a = {"a": 1, "b": "two", "tree_digest": "abc"}
    payload_b = {"a": 1, "b": "two", "tree_digest": "abc", "proof_digest": "stale"}
    # Including ``proof_digest`` does NOT change the digest, by design.
    assert _source_push_proof_digest(payload_a) == _source_push_proof_digest(
        payload_b
    )
    # Different payload content does change the digest.
    payload_c = {"a": 2, "b": "two", "tree_digest": "abc"}
    assert _source_push_proof_digest(payload_a) != _source_push_proof_digest(
        payload_c
    )
    # Output is hex SHA-256.
    assert len(_source_push_proof_digest(payload_a)) == 64


def test_source_push_base_proof_with_no_prior_returns_fresh_skeleton(
    tmp_path: Path,
) -> None:
    """``_source_push_base_proof(prior=None, ...)`` returns a fresh skeleton
    dict carrying the v1 schema marker, the requested tree_digest, the
    resolved repos_root, sorted expected_origins (deterministic order),
    and an empty repos dict.
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _source_push_base_proof,
    )

    proof = _source_push_base_proof(
        None,
        repos_root=tmp_path,
        tree_digest="t-abc",
        expected_origins={"z": "url-z", "a": "url-a"},
    )
    assert proof["artifact_schema"] == "dag-source-push-proof-v1"
    assert proof["tree_digest"] == "t-abc"
    # ``expected_origins`` is sorted by key for determinism.
    assert list(proof["expected_origins"].keys()) == ["a", "z"]
    assert proof["repos"] == {}
    assert proof["repos_root"] == str(tmp_path.resolve(strict=False))


def test_source_push_base_proof_resets_stale_prior_with_different_tree_digest(
    tmp_path: Path,
) -> None:
    """When the supplied ``prior`` carries a different ``tree_digest`` from
    the new build, the helper RESETS the payload (drops the prior repos +
    expected_origins) and rebuilds from scratch. This is the producer-
    side invariant that "a stale prior proof cannot leak into a fresh
    build's proof".
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _finalize_source_push_proof,
        _source_push_base_proof,
    )

    # Seed a finalized prior with tree_digest="t-old" and a populated
    # repos dict.
    prior = _finalize_source_push_proof(
        {
            "artifact_schema": "dag-source-push-proof-v1",
            "tree_digest": "t-old",
            "repos_root": str(tmp_path),
            "expected_origins": {"x": "url"},
            "repos": {"repo-a": {"status": "pushed", "tree_digest": "t-old"}},
        }
    )
    # Build the base proof for a NEW tree_digest; the prior must be
    # discarded.
    proof = _source_push_base_proof(
        prior,
        repos_root=tmp_path,
        tree_digest="t-new",
        expected_origins={"x": "url"},
    )
    assert proof["tree_digest"] == "t-new"
    # The stale repos dict was discarded.
    assert proof["repos"] == {}


def test_finalize_source_push_proof_fills_proof_digest_field() -> None:
    """``_finalize_source_push_proof`` returns a copy of the payload with
    a freshly-computed ``proof_digest`` field. The ``proof_digest`` value
    matches ``_source_push_proof_digest`` over the payload-minus-
    proof_digest (the self-referential invariant).
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _finalize_source_push_proof,
        _source_push_proof_digest,
    )

    payload = {
        "artifact_schema": "dag-source-push-proof-v1",
        "tree_digest": "t-abc",
        "repos_root": "/tmp",
        "expected_origins": {},
        "repos": {},
    }
    finalized = _finalize_source_push_proof(payload)
    assert "proof_digest" in finalized
    # Self-referential invariant.
    assert finalized["proof_digest"] == _source_push_proof_digest(payload)
    # The helper does NOT mutate the input.
    assert "proof_digest" not in payload


def test_source_push_prior_proof_matches_accepts_a_well_formed_pushed_record() -> None:
    """A ``status="pushed"`` prior record matches when all eight named
    fields agree AND ``remote_before != local_head`` AND
    ``remote_after == local_head``. This is the producer-side fast-path
    that lets the gate skip a re-push when a prior intent/pushed record
    already proves the desired state.
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _source_push_prior_proof_matches,
    )

    record = {
        "status": "pushed",
        "repo": "repo-a",
        "tree_digest": "t-abc",
        "branch": "main",
        "local_head": "abc",
        "remote_ref": "refs/heads/main",
        "expected_origin": "url-a",
        "actual_origin": "url-a",
        "remote_before": "old",
        "remote_after": "abc",
    }
    assert (
        _source_push_prior_proof_matches(
            record,
            repo="repo-a",
            tree_digest="t-abc",
            branch="main",
            local_head="abc",
            remote_ref="refs/heads/main",
            expected_origin="url-a",
            actual_origin="url-a",
        )
        is True
    )


def test_source_push_prior_proof_matches_rejects_remote_before_equals_local_head() -> None:
    """If the ``remote_before`` already equals ``local_head``, the record
    does NOT prove a real push happened (the remote was already at HEAD
    before the gate ran). The helper fail-closes in this case so the
    gate runs a real push.
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _source_push_prior_proof_matches,
    )

    record = {
        "status": "pushed",
        "repo": "repo-a",
        "tree_digest": "t-abc",
        "branch": "main",
        "local_head": "abc",
        "remote_ref": "refs/heads/main",
        "expected_origin": "url-a",
        "actual_origin": "url-a",
        "remote_before": "abc",  # same as local_head; no push
        "remote_after": "abc",
    }
    assert (
        _source_push_prior_proof_matches(
            record,
            repo="repo-a",
            tree_digest="t-abc",
            branch="main",
            local_head="abc",
            remote_ref="refs/heads/main",
            expected_origin="url-a",
            actual_origin="url-a",
        )
        is False
    )


def test_source_push_prior_proof_matches_intent_path_rejects_missing_remote_before() -> None:
    """A ``status="intent"`` record passes only when ``remote_before`` is
    truthy AND ``remote_after`` is falsy (a real "we observed the remote
    state and are about to push" record). Anything else fail-closes.
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _source_push_prior_proof_matches,
    )

    # Missing remote_before => false.
    record_no_before = {
        "status": "intent",
        "repo": "repo-a",
        "tree_digest": "t-abc",
        "branch": "main",
        "local_head": "abc",
        "remote_ref": "refs/heads/main",
        "expected_origin": "url-a",
        "actual_origin": "url-a",
        "remote_before": "",
        "remote_after": "",
    }
    assert (
        _source_push_prior_proof_matches(
            record_no_before,
            repo="repo-a",
            tree_digest="t-abc",
            branch="main",
            local_head="abc",
            remote_ref="refs/heads/main",
            expected_origin="url-a",
            actual_origin="url-a",
        )
        is False
    )

    # remote_after already populated => intent record has actually pushed;
    # callers should treat this as a "pushed" record check, not an intent
    # check. The intent-path therefore fail-closes.
    record_after_set = dict(record_no_before)
    record_after_set["remote_before"] = "old"
    record_after_set["remote_after"] = "abc"
    assert (
        _source_push_prior_proof_matches(
            record_after_set,
            repo="repo-a",
            tree_digest="t-abc",
            branch="main",
            local_head="abc",
            remote_ref="refs/heads/main",
            expected_origin="url-a",
            actual_origin="url-a",
        )
        is False
    )


def test_source_push_prior_proof_matches_rejects_unknown_status() -> None:
    """A record whose ``status`` is not in ``{"intent", "pushed",
    "recovered"}`` fail-closes. The helper must never let an unknown
    status string slip through.
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _source_push_prior_proof_matches,
    )

    record = {
        "status": "blocked",  # not allowed
        "repo": "repo-a",
        "tree_digest": "t-abc",
        "branch": "main",
        "local_head": "abc",
        "remote_ref": "refs/heads/main",
        "expected_origin": "url-a",
        "actual_origin": "url-a",
        "remote_before": "old",
        "remote_after": "abc",
    }
    assert (
        _source_push_prior_proof_matches(
            record,
            repo="repo-a",
            tree_digest="t-abc",
            branch="main",
            local_head="abc",
            remote_ref="refs/heads/main",
            expected_origin="url-a",
            actual_origin="url-a",
        )
        is False
    )


def test_source_push_proof_records_are_self_consistent_accepts_all_three_statuses() -> None:
    """The proof-records validator accepts ``status="pushed"``,
    ``"recovered"``, and ``"unchanged"`` records when each record has a
    populated branch + remote_ref + local_head == remote_after AND
    (``unchanged`` records additionally have ``mutation_required is
    False``).
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _source_push_proof_records_are_self_consistent,
    )

    proof = {
        "tree_digest": "t-abc",
        "repos": {
            "repo-a": {
                "repo": "repo-a",
                "tree_digest": "t-abc",
                "status": "pushed",
                "branch": "main",
                "remote_ref": "refs/heads/main",
                "local_head": "abc",
                "remote_after": "abc",
            },
            "repo-b": {
                "repo": "repo-b",
                "tree_digest": "t-abc",
                "status": "recovered",
                "branch": "main",
                "remote_ref": "refs/heads/main",
                "local_head": "def",
                "remote_after": "def",
            },
            "repo-c": {
                "repo": "repo-c",
                "tree_digest": "t-abc",
                "status": "unchanged",
                "branch": "main",
                "remote_ref": "refs/heads/main",
                "local_head": "ghi",
                "remote_after": "ghi",
                "mutation_required": False,
            },
        },
    }
    assert (
        _source_push_proof_records_are_self_consistent(proof, "t-abc") is True
    )


def test_source_push_proof_records_are_self_consistent_rejects_unchanged_with_mutation_required() -> None:
    """An ``unchanged`` record with ``mutation_required != False`` (e.g.
    ``True``, ``None``, or missing) fail-closes. ``unchanged`` is only
    valid when the repo was explicitly opted out of mutation -- otherwise
    we want a real push.
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _source_push_proof_records_are_self_consistent,
    )

    proof = {
        "tree_digest": "t-abc",
        "repos": {
            "repo-a": {
                "repo": "repo-a",
                "tree_digest": "t-abc",
                "status": "unchanged",
                "branch": "main",
                "remote_ref": "refs/heads/main",
                "local_head": "abc",
                "remote_after": "abc",
                # NOTE: missing ``mutation_required`` -- treated as not False.
            }
        },
    }
    assert (
        _source_push_proof_records_are_self_consistent(proof, "t-abc")
        is False
    )


def test_source_push_proof_records_are_self_consistent_rejects_pushed_with_mismatched_remote() -> None:
    """A ``pushed`` record where ``local_head != remote_after`` fail-
    closes. The point of the proof is to attest that the local HEAD
    actually landed on the remote.
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _source_push_proof_records_are_self_consistent,
    )

    proof = {
        "tree_digest": "t-abc",
        "repos": {
            "repo-a": {
                "repo": "repo-a",
                "tree_digest": "t-abc",
                "status": "pushed",
                "branch": "main",
                "remote_ref": "refs/heads/main",
                "local_head": "abc",
                "remote_after": "MISMATCH",
            }
        },
    }
    assert (
        _source_push_proof_records_are_self_consistent(proof, "t-abc")
        is False
    )


def test_source_push_proof_records_are_self_consistent_rejects_mismatched_top_level_tree_digest() -> None:
    """If the ``tree_digest`` at the proof's top level disagrees with the
    requested ``tree_digest`` argument, the validator fail-closes
    immediately without even looking at the repos.
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _source_push_proof_records_are_self_consistent,
    )

    proof = {
        "tree_digest": "t-WRONG",  # mismatched
        "repos": {
            "repo-a": {
                "repo": "repo-a",
                "tree_digest": "t-abc",
                "status": "pushed",
                "branch": "main",
                "remote_ref": "refs/heads/main",
                "local_head": "abc",
                "remote_after": "abc",
            }
        },
    }
    assert (
        _source_push_proof_records_are_self_consistent(proof, "t-abc")
        is False
    )


def test_source_push_proof_records_are_self_consistent_rejects_empty_repos() -> None:
    """An empty ``repos`` dict fail-closes. A source-push proof with NO
    repos has nothing to prove -- the gate must run a real push (which
    would populate at least one repo record).
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _source_push_proof_records_are_self_consistent,
    )

    proof = {"tree_digest": "t-abc", "repos": {}}
    assert (
        _source_push_proof_records_are_self_consistent(proof, "t-abc")
        is False
    )


# -- Structural ----------------------------------------------------------------


def test_cluster_ownership_pin_post_dag_gates_module() -> None:
    """All eight moved names land in the canonical
    ``execution/post_dag_gates.py`` module (not in any other
    ``execution/`` sibling like ``types.py``, ``git_service.py``,
    ``task_contracts.py``, ``sandbox.py``, ``dispatcher.py``,
    ``gates.py``, ``verification.py``, ``repair.py``,
    ``failure_router.py``, ``merge_queue.py``, or
    ``regroup_overlay.py``). Belt-and-braces guard against a future
    refactor accidentally relocating one of the helpers to the wrong
    canonical module while leaving the shim intact.
    """

    from iriai_build_v2.workflows.develop.execution import (
        post_dag_gates as post_dag_gates_mod,
    )

    expected = "iriai_build_v2.workflows.develop.execution.post_dag_gates"
    for name in MOVED_CALLABLES:
        obj = getattr(post_dag_gates_mod, name)
        assert obj.__module__ == expected, (
            f"{name}.__module__ = {obj.__module__!r}; expected {expected!r}"
        )

    # Cross-check that the names are NOT served by any of the sibling
    # execution modules (a deliberate "did anyone else accidentally define
    # a copy?" probe).
    from iriai_build_v2.workflows.develop.execution import (
        dispatcher as dispatcher_mod,
        failure_router as failure_router_mod,
        gates as gates_mod,
        git_service as git_service_mod,
        merge_queue as merge_queue_mod,
        regroup_overlay as regroup_overlay_mod,
        repair as repair_mod,
        sandbox as sandbox_mod,
        task_contracts as task_contracts_mod,
        types as types_mod,
        verification as verification_mod,
    )
    for name in MOVED_NAMES:
        for sibling, sibling_name in (
            (dispatcher_mod, "dispatcher"),
            (failure_router_mod, "failure_router"),
            (gates_mod, "gates"),
            (git_service_mod, "git_service"),
            (merge_queue_mod, "merge_queue"),
            (regroup_overlay_mod, "regroup_overlay"),
            (repair_mod, "repair"),
            (sandbox_mod, "sandbox"),
            (task_contracts_mod, "task_contracts"),
            (types_mod, "types"),
            (verification_mod, "verification"),
        ):
            assert not hasattr(sibling, name), (
                f"sibling drift: {sibling_name}.{name} unexpectedly exists; "
                "cluster ownership pin failed"
            )


def test_shim_block_exports_all_eight_names() -> None:
    """The Slice-11l shim block in ``implementation.py`` re-exports
    exactly the eight moved names from ``..execution.post_dag_gates``.
    This test asserts the shim block actually carries all eight (a
    deliberate "did the shim block lose a name?" probe).
    """

    from iriai_build_v2.workflows.develop.execution.post_dag_gates import (
        _finalize_source_push_proof,
        _implementation_report_metadata,
        _notify_delivery_id,
        _source_push_base_proof,
        _source_push_prior_proof_matches,
        _source_push_proof_digest,
        _source_push_proof_key,
        _source_push_proof_records_are_self_consistent,
    )
    from iriai_build_v2.workflows.develop.phases import (
        implementation as impl_mod,
    )

    # All eight moved names accessible via the impl module.
    for name in MOVED_NAMES:
        assert hasattr(impl_mod, name), (
            f"implementation.{name} missing -- the Slice-11l shim block "
            "dropped a re-export"
        )

    # All eight shim entries point to the SAME canonical objects.
    assert (
        impl_mod._finalize_source_push_proof is _finalize_source_push_proof
    )
    assert (
        impl_mod._implementation_report_metadata
        is _implementation_report_metadata
    )
    assert impl_mod._notify_delivery_id is _notify_delivery_id
    assert impl_mod._source_push_base_proof is _source_push_base_proof
    assert (
        impl_mod._source_push_prior_proof_matches
        is _source_push_prior_proof_matches
    )
    assert impl_mod._source_push_proof_digest is _source_push_proof_digest
    assert impl_mod._source_push_proof_key is _source_push_proof_key
    assert (
        impl_mod._source_push_proof_records_are_self_consistent
        is _source_push_proof_records_are_self_consistent
    )


def test_post_dag_gates_module_does_not_import_implementation() -> None:
    """The compatibility-arrow direction (per doc 11 § "How To Use This
    Map" Q4) is: ``execution/post_dag_gates.py`` MUST NOT import from
    ``workflows.develop.phases.implementation``. This test reads the
    on-disk source of ``post_dag_gates.py`` and asserts the import
    line is absent. Belt-and-braces guard against a future refactor
    accidentally introducing a back-import.
    """

    import iriai_build_v2.workflows.develop.execution.post_dag_gates as post_dag_gates_mod

    source_path = Path(post_dag_gates_mod.__file__)
    text = source_path.read_text(encoding="utf-8")
    assert (
        "from iriai_build_v2.workflows.develop.phases.implementation"
        not in text
    ), (
        "execution/post_dag_gates.py imports from phases/implementation -- "
        "violates the doc-11 compatibility-arrow direction"
    )
    assert "from ..phases.implementation" not in text, (
        "execution/post_dag_gates.py uses a relative back-import to "
        "phases/implementation -- violates the doc-11 compatibility-arrow "
        "direction"
    )


def test_all_export_includes_eight_moved_names() -> None:
    """``post_dag_gates.py.__all__`` includes all eight moved names.
    Belt-and-braces probe against a refactor that forgets to add the new
    public symbols to the module's public surface (which would cause
    ``from execution.post_dag_gates import *`` to silently lose them).
    """

    from iriai_build_v2.workflows.develop.execution import (
        post_dag_gates as post_dag_gates_mod,
    )

    for name in MOVED_NAMES:
        assert name in post_dag_gates_mod.__all__, (
            f"{name} missing from execution/post_dag_gates.py __all__"
        )
