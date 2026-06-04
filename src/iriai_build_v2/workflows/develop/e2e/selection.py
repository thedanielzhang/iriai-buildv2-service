"""Agnostic selection: which scenarios are testable at a sealed checkpoint.

At a sealed checkpoint we test only the acceptance criteria that (a) are gated
by a task that has actually landed (``done``), (b) are e2e/visual/integration
(the runtime-verifiable methods), and (c) are real obligations of the subfeature
per its planning contract (``canonical`` ∪ ``global_obligation`` − ``waived``).
Then we pick the ``TestScenario``s whose linked acceptance is fully covered by
that testable set. This keeps the e2e track from authoring specs against
not-yet-built surfaces, waived ACs, or manual/unit-only criteria.

The contract is duck-typed (``.canonical_ac_ids`` / ``.global_obligation_ac_ids``
/ ``.waived_ac_ids``) so this module stays decoupled from the planning phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from iriai_build_v2.models.outputs import (
    TestAcceptanceCriterion,
    TestPlan,
    TestScenario,
)

# Runtime-verifiable methods the e2e track is responsible for.
TESTABLE_METHODS = frozenset({"e2e", "visual", "integration"})


@dataclass
class SelectionResult:
    acceptance_criteria: list[TestAcceptanceCriterion] = field(default_factory=list)
    scenarios: list[TestScenario] = field(default_factory=list)
    testable_ac_ids: set[str] = field(default_factory=set)


def compute_testable_acs_for_tasks(
    test_plan: TestPlan,
    contract: Any | None,
    done_task_ids: Iterable[str],
    tasks: Iterable[Any],
) -> list[TestAcceptanceCriterion]:
    """The testable acceptance criteria at this checkpoint (ordered as in plan).

    * Invert ``ImplementationTask.verification_gates`` over the DONE tasks to get
      the AC-ids whose covering work has landed.
    * Keep only ``verification_method ∈ {e2e, visual, integration}``.
    * Honor the contract obligations when a contract is supplied:
      keep AC iff in (``canonical_ac_ids`` ∪ ``global_obligation_ac_ids``) and
      not in ``waived_ac_ids``.
    """
    done = set(done_task_ids)
    done_gated: set[str] = set()
    for t in tasks:
        if getattr(t, "id", None) in done:
            done_gated.update(getattr(t, "verification_gates", None) or [])

    obligations: set[str] | None = None
    waived: set[str] = set()
    if contract is not None:
        canonical = set(getattr(contract, "canonical_ac_ids", None) or [])
        global_obl = set(getattr(contract, "global_obligation_ac_ids", None) or [])
        waived = set(getattr(contract, "waived_ac_ids", None) or [])
        obligations = canonical | global_obl

    out: list[TestAcceptanceCriterion] = []
    for ac in test_plan.acceptance_criteria:
        if ac.verification_method not in TESTABLE_METHODS:
            continue
        if ac.id not in done_gated:
            continue
        if ac.id in waived:
            continue
        if obligations is not None and ac.id not in obligations:
            continue
        out.append(ac)
    return out


def select_testable_scenarios(
    test_plan: TestPlan, testable_ac_ids: Iterable[str]
) -> list[TestScenario]:
    """Scenarios whose (non-empty) linked_acceptance ⊆ the testable AC set."""
    ids = set(testable_ac_ids)
    out: list[TestScenario] = []
    for sc in test_plan.test_scenarios:
        linked = set(sc.linked_acceptance or [])
        if linked and linked <= ids:
            out.append(sc)
    return out


def select(
    test_plan: TestPlan,
    contract: Any | None,
    done_task_ids: Iterable[str],
    tasks: Iterable[Any],
) -> SelectionResult:
    acs = compute_testable_acs_for_tasks(test_plan, contract, done_task_ids, tasks)
    ids = {ac.id for ac in acs}
    scs = select_testable_scenarios(test_plan, ids)
    return SelectionResult(acceptance_criteria=acs, scenarios=scs, testable_ac_ids=ids)
