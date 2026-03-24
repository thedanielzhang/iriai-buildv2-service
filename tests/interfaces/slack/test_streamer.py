"""Tests for SlackStreamer format helpers and structured output detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from iriai_build_v2.interfaces.slack.streamer import (
    SlackStreamer,
    _format_thinking,
    _format_tool_result,
    _format_tool_use,
    _is_structured_output,
)


# ── Mock message types for streamer tests ─────────────────────────────────
# SlackStreamer checks type(block).__name__, so we create classes with the
# exact names the streamer expects.


class TextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class ResultMessage:
    def __init__(self, structured_output: Any = None) -> None:
        self.structured_output = structured_output


class AssistantMessage:
    def __init__(self, content: list | None = None, id: str = "msg-1") -> None:
        self.content = content or []
        self.id = id


# ── _format_thinking ──────────────────────────────────────────────────────


class TestFormatThinking:
    def test_wraps_in_italics_with_emoji(self):
        result = _format_thinking("reasoning about the problem")
        assert result.startswith("\U0001f4ad _")
        assert result.endswith("_")

    def test_truncates_at_200_chars(self):
        long_text = "a" * 300
        result = _format_thinking(long_text)
        # 💭 _ + 200 chars + ... + _
        assert "..." in result
        # The inner text (between _ markers) should be 200 + len("...")
        inner = result[len("\U0001f4ad _") : -len("_")]
        assert len(inner) == 203  # 200 chars + "..."

    def test_short_text_no_truncation(self):
        result = _format_thinking("short")
        assert "..." not in result

    def test_strips_newlines(self):
        result = _format_thinking("line1\nline2\nline3")
        assert "\n" not in result


# ── _format_tool_use ──────────────────────────────────────────────────────


class TestFormatToolUse:
    def test_extracts_file_path(self):
        result = _format_tool_use("Read", {"file_path": "/src/main.py"})
        assert "/src/main.py" in result
        assert "*Read*" in result

    def test_extracts_command(self):
        result = _format_tool_use("Bash", {"command": "npm test"})
        assert "npm test" in result

    def test_extracts_pattern(self):
        result = _format_tool_use("Grep", {"pattern": "TODO"})
        assert "TODO" in result

    def test_extracts_query(self):
        result = _format_tool_use("WebSearch", {"query": "python asyncio"})
        assert "python asyncio" in result

    def test_truncates_at_80(self):
        long_path = "/very/long/" + "x" * 100
        result = _format_tool_use("Read", {"file_path": long_path})
        assert "..." in result

    def test_empty_input(self):
        result = _format_tool_use("Unknown", {})
        assert "*Unknown*" in result


# ── _format_tool_result ───────────────────────────────────────────────────


class TestFormatToolResult:
    def test_success_no_content(self):
        result = _format_tool_result(None, False)
        assert "\u2713" in result  # checkmark

    def test_error(self):
        result = _format_tool_result("something", True)
        assert "\u2717" in result  # X mark

    def test_truncates_long_content(self):
        long_content = "x" * 200
        result = _format_tool_result(long_content, False)
        assert "..." in result

    def test_short_content(self):
        result = _format_tool_result("ok", False)
        assert "ok" in result
        assert "..." not in result


# ── _is_structured_output ────────────────────────────────────────────────


class TestIsStructuredOutput:
    def test_detects_envelope_with_question(self):
        import json
        text = json.dumps({"question": "What is the goal?", "options": [], "output": None})
        assert _is_structured_output(text) is True

    def test_detects_envelope_with_output(self):
        import json
        text = json.dumps({"output": {"title": "PRD", "complete": True}})
        assert _is_structured_output(text) is True

    def test_rejects_plain_text(self):
        assert _is_structured_output("Hello world") is False

    def test_rejects_non_envelope_json(self):
        import json
        text = json.dumps({"name": "foo", "value": 42})
        assert _is_structured_output(text) is False

    def test_rejects_empty_string(self):
        assert _is_structured_output("") is False

    def test_rejects_json_array(self):
        assert _is_structured_output("[1, 2, 3]") is False

    def test_handles_invalid_json_gracefully(self):
        assert _is_structured_output("{not json") is False

    def test_handles_whitespace_around_json(self):
        import json
        text = f"  {json.dumps({'question': 'Q?'})}  "
        assert _is_structured_output(text) is True


# ── SlackStreamer structured output suppression ──────────────────────────


class TestStructuredOutputSuppression:
    """Tests for suppressing agent text when structured output is present."""

    def _make_streamer(self) -> SlackStreamer:
        adapter = MagicMock()
        return SlackStreamer(adapter, "C123", thread_ts="t1")

    def test_completion_called_with_structured_flag(self):
        """When ResultMessage has structured_output, completion is called with has_structured=True."""
        streamer = self._make_streamer()
        streamer._schedule_completion = MagicMock()

        # Feed some text
        streamer.on_message(AssistantMessage(
            content=[TextBlock("I've prepared the PRD")],
            id="msg-1",
        ))

        # ResultMessage WITH structured output
        streamer.on_message(ResultMessage(
            structured_output={"question": "", "output": {"complete": True}},
        ))

        streamer._schedule_completion.assert_called_once()
        args = streamer._schedule_completion.call_args[0]
        assert args[2] is True  # has_structured

    def test_completion_called_without_structured_flag(self):
        """When ResultMessage has no structured_output, completion is called with has_structured=False."""
        streamer = self._make_streamer()
        streamer._schedule_completion = MagicMock()

        streamer.on_message(AssistantMessage(
            content=[TextBlock("Here is my analysis")],
            id="msg-2",
        ))

        streamer.on_message(ResultMessage(structured_output=None))

        streamer._schedule_completion.assert_called_once()
        args = streamer._schedule_completion.call_args[0]
        assert "Here is my analysis" in args[0]
        assert args[2] is False  # has_structured

    def test_json_starting_textblock_suppressed(self):
        """TextBlocks starting with '{' should not produce status previews."""
        streamer = self._make_streamer()
        streamer._schedule_flush = MagicMock()

        streamer.on_message(AssistantMessage(
            content=[TextBlock('{"title": "", "overview": ""}')],
            id="msg-3",
        ))

        # Status should not be set (no schedule_flush call)
        streamer._schedule_flush.assert_not_called()

    def test_textblock_does_not_produce_status(self):
        """TextBlocks should not produce status previews (text is posted separately on completion)."""
        streamer = self._make_streamer()
        streamer._schedule_flush = MagicMock()

        streamer.on_message(AssistantMessage(
            content=[TextBlock("Analyzing the codebase for patterns")],
            id="msg-4",
        ))

        streamer._schedule_flush.assert_not_called()

    def test_reset_between_turns(self):
        """After ResultMessage, a new turn starts fresh with clean state."""
        streamer = self._make_streamer()
        streamer._schedule_completion = MagicMock()

        # Turn 1: structured output
        streamer.on_message(AssistantMessage(
            content=[TextBlock('{"question": "test"}')],
            id="msg-5",
        ))
        streamer.on_message(ResultMessage(
            structured_output={"question": "test"},
        ))

        # State should be reset
        assert streamer._final_text == ""
        assert streamer._message_ts is None
        assert streamer._current_status == ""


# ── _extract_question empty Envelope handling ────────────────────────────


class TestExtractQuestionFallback:
    """Tests for _extract_question handling empty-question Envelopes."""

    def test_populated_question_works_normally(self):
        import json
        from iriai_build_v2.interfaces.slack.interaction import _extract_question

        prompt = json.dumps({"question": "What about security?", "options": ["A", "B"]})
        question, options = _extract_question(prompt)
        assert question == "What about security?"
        assert options == ["A", "B"]

    def test_empty_question_envelope_returns_fallback(self):
        import json
        from iriai_build_v2.interfaces.slack.interaction import _extract_question

        prompt = json.dumps({"question": "", "options": [], "output": {"complete": False}})
        question, options = _extract_question(prompt)
        assert "processing" in question.lower() or "feedback" in question.lower()
        assert options == []

    def test_output_only_envelope_returns_fallback(self):
        import json
        from iriai_build_v2.interfaces.slack.interaction import _extract_question

        prompt = json.dumps({"output": {"title": "", "complete": False}})
        question, options = _extract_question(prompt)
        assert "processing" in question.lower() or "feedback" in question.lower()

    def test_raw_model_json_not_passed_through(self):
        """Raw model JSON (PRD, TechnicalPlan, etc.) should show fallback, not JSON."""
        import json
        from iriai_build_v2.interfaces.slack.interaction import _extract_question

        # Simulate a raw PRD model JSON (no "question" or "output" keys)
        prompt = json.dumps({"title": "My PRD", "overview": "...", "complete": True})
        question, options = _extract_question(prompt)
        assert "processing" in question.lower() or "feedback" in question.lower()
        assert prompt not in question  # raw JSON must NOT be shown

    def test_plain_text_passed_through(self):
        from iriai_build_v2.interfaces.slack.interaction import _extract_question

        question, options = _extract_question("Hello, what do you think?")
        assert question == "Hello, what do you think?"
        assert options == []
