from __future__ import annotations

from iriai_build_v2.runtime_policy import (
    DEFAULT_RUNTIME_POLICY,
    PRIMARY_IMPL_SECONDARY_REVIEW_POLICY,
)
from iriai_build_v2.workflows.develop.phases.implementation import (
    _dag_group_runtime_pair,
    _diagnostic_runtime_for_policy,
    _post_dag_runtime_pair,
)


def test_alternating_policy_preserves_existing_group_parity():
    assert _dag_group_runtime_pair(0, DEFAULT_RUNTIME_POLICY) == (
        "primary",
        "secondary",
    )
    assert _dag_group_runtime_pair(1, DEFAULT_RUNTIME_POLICY) == (
        "secondary",
        "primary",
    )


def test_alternating_policy_preserves_existing_post_dag_parity():
    assert _post_dag_runtime_pair(0, DEFAULT_RUNTIME_POLICY) == (
        "secondary",
        "primary",
    )
    assert _post_dag_runtime_pair(1, DEFAULT_RUNTIME_POLICY) == (
        "primary",
        "secondary",
    )


def test_primary_impl_secondary_review_policy_pins_runtime_roles():
    assert _dag_group_runtime_pair(0, PRIMARY_IMPL_SECONDARY_REVIEW_POLICY) == (
        "primary",
        "secondary",
    )
    assert _dag_group_runtime_pair(1, PRIMARY_IMPL_SECONDARY_REVIEW_POLICY) == (
        "primary",
        "secondary",
    )
    assert _post_dag_runtime_pair(0, PRIMARY_IMPL_SECONDARY_REVIEW_POLICY) == (
        "secondary",
        "primary",
    )
    assert _post_dag_runtime_pair(1, PRIMARY_IMPL_SECONDARY_REVIEW_POLICY) == (
        "secondary",
        "primary",
    )


def test_primary_impl_secondary_review_policy_pins_diagnostics_to_secondary():
    assert _diagnostic_runtime_for_policy(DEFAULT_RUNTIME_POLICY) is None
    assert (
        _diagnostic_runtime_for_policy(PRIMARY_IMPL_SECONDARY_REVIEW_POLICY)
        == "secondary"
    )
