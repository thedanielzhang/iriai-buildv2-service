"""Tests for SlackStreamer format helpers and structured output detection."""

from iriai_build_v2.interfaces.slack.streamer import (
    _format_thinking,
    _format_tool_result,
    _format_tool_use,
    _is_structured_output,
)


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
