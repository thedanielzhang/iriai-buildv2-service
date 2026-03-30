from __future__ import annotations

from iriai_build_v2.interfaces.slack.orchestrator import SlackWorkflowOrchestrator


class _QueuedRuntime:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str]] = []

    def queue_user_note(self, feature_id: str, text: str) -> None:
        self.notes.append((feature_id, text))


def test_queue_user_note_forwards_to_active_runtime():
    runtime = _QueuedRuntime()
    orchestrator = SlackWorkflowOrchestrator.__new__(SlackWorkflowOrchestrator)
    orchestrator._user_notes = {}
    orchestrator._active_runtimes = {"feat-1": runtime}

    orchestrator._queue_user_note("feat-1", "Please include rollback notes.")

    assert orchestrator._user_notes == {"feat-1": ["Please include rollback notes."]}
    assert runtime.notes == [("feat-1", "Please include rollback notes.")]
