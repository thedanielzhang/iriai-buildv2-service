"""Tests for Slack helpers: markdown_to_mrkdwn, block builders."""

from iriai_build_v2.interfaces.slack.helpers import (
    build_decision_blocks,
    build_resolved_blocks,
    markdown_to_mrkdwn,
)


# ── markdown_to_mrkdwn ───────────────────────────────────────────────────


class TestMarkdownToMrkdwn:
    def test_bold(self):
        assert markdown_to_mrkdwn("**bold**") == "*bold*"

    def test_header(self):
        assert markdown_to_mrkdwn("# Header") == "*Header*"

    def test_h2_header(self):
        assert markdown_to_mrkdwn("## Sub Header") == "*Sub Header*"

    def test_link(self):
        assert markdown_to_mrkdwn("[text](https://example.com)") == "<https://example.com|text>"

    def test_image(self):
        assert markdown_to_mrkdwn("![alt](path/to/img.png)") == "[img:path/to/img.png]"

    def test_preserves_inline_code(self):
        result = markdown_to_mrkdwn("use `**not bold**` here")
        assert "`**not bold**`" in result

    def test_preserves_code_block(self):
        md = "before\n```\n**keep**\n```\nafter"
        result = markdown_to_mrkdwn(md)
        assert "```\n**keep**\n```" in result

    def test_empty_string(self):
        assert markdown_to_mrkdwn("") == ""

    def test_none_input(self):
        assert markdown_to_mrkdwn(None) is None

    def test_mixed_markdown(self):
        md = "# Title\n\n**bold** and [link](http://x.com)\n\n![img](pic.png)"
        result = markdown_to_mrkdwn(md)
        assert "*Title*" in result
        assert "*bold*" in result
        assert "<http://x.com|link>" in result
        assert "[img:pic.png]" in result


# ── build_decision_blocks ─────────────────────────────────────────────────


class TestBuildDecisionBlocks:
    def test_produces_two_blocks(self):
        blocks = build_decision_blocks(
            "d1",
            "Approve design?",
            "Some context",
            [{"id": "yes", "label": "Approve"}],
        )
        assert len(blocks) == 2
        assert blocks[0]["type"] == "section"
        assert blocks[1]["type"] == "actions"

    def test_action_id_format(self):
        blocks = build_decision_blocks(
            "abc",
            "Title",
            "",
            [{"id": "opt1", "label": "Option 1"}],
        )
        btn = blocks[1]["elements"][0]
        assert btn["action_id"] == "decision_abc_opt1"

    def test_button_style_passed_through(self):
        blocks = build_decision_blocks(
            "d2",
            "Title",
            "",
            [
                {"id": "ok", "label": "OK", "style": "primary"},
                {"id": "no", "label": "No", "style": "danger"},
            ],
        )
        elements = blocks[1]["elements"]
        assert elements[0]["style"] == "primary"
        assert elements[1]["style"] == "danger"

    def test_button_no_style(self):
        blocks = build_decision_blocks(
            "d3",
            "Title",
            "",
            [{"id": "plain", "label": "Plain"}],
        )
        btn = blocks[1]["elements"][0]
        assert "style" not in btn

    def test_empty_context(self):
        blocks = build_decision_blocks(
            "d4",
            "Title",
            "",
            [{"id": "a", "label": "A"}],
        )
        section_text = blocks[0]["text"]["text"]
        assert section_text == "*Title*"


# ── build_resolved_blocks ─────────────────────────────────────────────────


class TestBuildResolvedBlocks:
    def test_resolved_with_user(self):
        blocks = build_resolved_blocks("Design review", "Approved", "U123")
        text = blocks[0]["text"]["text"]
        assert "<@U123>" in text
        assert "*Approved*" in text
        assert "~Resolved~" in text

    def test_feedback_with_newline_quoting(self):
        blocks = build_resolved_blocks(
            "Title", "OK", "U1", feedback="line1\nline2"
        )
        text = blocks[0]["text"]["text"]
        assert "> line1" in text
        assert "> line2" in text

    def test_no_feedback(self):
        blocks = build_resolved_blocks("Title", "OK", "U1")
        text = blocks[0]["text"]["text"]
        assert "\n>" not in text

    def test_no_resolved_by(self):
        blocks = build_resolved_blocks("Title", "OK", "")
        text = blocks[0]["text"]["text"]
        assert "<@" not in text
