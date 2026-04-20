from iriai_compose.tasks import Ask

from iriai_build_v2.interfaces.cli.interaction import _pending_from_task
from iriai_build_v2.roles import user


def test_pending_from_task_respects_choose_hints():
    pending = _pending_from_task(
        Ask(actor=user, prompt="Pick one"),
        feature_id="feat-1",
        phase_name="broad",
        kind_hint="choose",
        options_hint=["Interactive", "Finish in background"],
    )

    assert pending.kind == "choose"
    assert pending.options == ["Interactive", "Finish in background"]


def test_pending_from_task_respects_approve_hints():
    pending = _pending_from_task(
        Ask(actor=user, prompt="Approve?"),
        feature_id="feat-1",
        phase_name="broad",
        kind_hint="approve",
    )

    assert pending.kind == "approve"
    assert pending.options == ["Approve", "Reject", "Give feedback"]
