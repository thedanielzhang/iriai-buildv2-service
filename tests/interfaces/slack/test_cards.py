"""Tests for card classes: RespondCard, ApproveCard, ChooseCard."""

from iriai_build_v2.interfaces.slack.cards import (
    ApproveCard,
    ChooseCard,
    RespondCard,
    build_modal_view,
)


# ── RespondCard ──────────────────────────────────────────────────────────


class TestRespondCard:
    def test_builds_question_section(self):
        card = RespondCard(pending_id="p1", phase_name="pm", question="What is the goal?")
        blocks = card.build_blocks()
        section = blocks[0]
        assert section["type"] == "section"
        assert "*pm*" in section["text"]["text"]
        assert "What is the goal?" in section["text"]["text"]

    def test_no_options_has_two_blocks(self):
        card = RespondCard(pending_id="p1", phase_name="pm", question="Q?")
        blocks = card.build_blocks()
        # Section + reply button (no option blocks)
        assert len(blocks) == 2

    def test_options_as_vertical_sections(self):
        card = RespondCard(
            pending_id="p1", phase_name="pm", question="Q?",
            options=["Option A", "Option B", "Option C"],
        )
        blocks = card.build_blocks()
        # 1 question + 3 option sections + 1 reply = 5 blocks
        assert len(blocks) == 5
        # Each option is a section with a button accessory
        for i in range(1, 4):
            assert blocks[i]["type"] == "section"
            assert blocks[i]["accessory"]["type"] == "button"
            assert blocks[i]["accessory"]["text"]["text"] == "Select"

    def test_option_text_not_truncated(self):
        long_option = "This is a very long option text that describes a complex choice in full detail without any truncation whatsoever"
        card = RespondCard(
            pending_id="p1", phase_name="pm", question="Q?",
            options=[long_option],
        )
        blocks = card.build_blocks()
        option_section = blocks[1]
        assert long_option in option_section["text"]["text"]

    def test_option_sections_numbered(self):
        card = RespondCard(
            pending_id="p1", phase_name="pm", question="Q?",
            options=["First", "Second"],
        )
        blocks = card.build_blocks()
        assert blocks[1]["text"]["text"].startswith("1. First")
        assert blocks[2]["text"]["text"].startswith("2. Second")

    def test_many_options_fallback_to_dropdown(self):
        options = [f"Option {i}" for i in range(11)]
        card = RespondCard(
            pending_id="p1", phase_name="pm", question="Q?",
            options=options,
        )
        blocks = card.build_blocks()
        # 1 question + 1 dropdown actions block + 1 reply = 3 blocks
        assert len(blocks) == 3
        dropdown_block = blocks[1]
        assert dropdown_block["type"] == "actions"
        select = dropdown_block["elements"][0]
        assert select["type"] == "static_select"
        assert len(select["options"]) == 11

    def test_reply_button_present(self):
        card = RespondCard(pending_id="p1", phase_name="pm", question="Q?")
        blocks = card.build_blocks()
        reply_block = blocks[-1]
        assert reply_block["type"] == "actions"
        assert len(reply_block["elements"]) == 1  # Only Reply, no Expand
        reply_btn = reply_block["elements"][0]
        assert reply_btn["action_id"] == "respond_p1_reply"
        assert reply_btn["style"] == "primary"

    def test_action_id_format_option_sections(self):
        card = RespondCard(
            pending_id="abc", phase_name="pm", question="Q?",
            options=["X", "Y"],
        )
        blocks = card.build_blocks()
        assert blocks[1]["accessory"]["action_id"] == "respond_abc_opt_0"
        assert blocks[2]["accessory"]["action_id"] == "respond_abc_opt_1"

    def test_action_id_format_dropdown(self):
        card = RespondCard(
            pending_id="abc", phase_name="pm", question="Q?",
            options=[f"opt{i}" for i in range(11)],
        )
        blocks = card.build_blocks()
        select = blocks[1]["elements"][0]
        assert select["action_id"] == "respond_abc_select"


# ── ApproveCard ──────────────────────────────────────────────────────────


class TestApproveCard:
    def test_builds_two_blocks(self):
        card = ApproveCard(pending_id="p2", title="Approval Required", context="Check the PRD")
        blocks = card.build_blocks()
        assert len(blocks) == 2  # Header + buttons

    def test_approve_reject_feedback_buttons(self):
        card = ApproveCard(pending_id="p2", title="T", context="C")
        blocks = card.build_blocks()
        btn_block = blocks[1]
        elements = btn_block["elements"]
        assert len(elements) == 3
        assert elements[0]["action_id"] == "gate_p2_approve"
        assert elements[0]["style"] == "primary"
        assert elements[1]["action_id"] == "gate_p2_reject"
        assert elements[1]["style"] == "danger"
        assert elements[2]["action_id"] == "gate_p2_feedback"

    def test_review_url_shown(self):
        card = ApproveCard(
            pending_id="p2", title="T", context="C",
            review_urls=["https://example.com/review"],
        )
        blocks = card.build_blocks()
        text = blocks[0]["text"]["text"]
        assert "https://example.com/review" in text

    def test_no_url_omitted(self):
        card = ApproveCard(pending_id="p2", title="T", context="C")
        blocks = card.build_blocks()
        text = blocks[0]["text"]["text"]
        assert "Review in browser" not in text


# ── ChooseCard ───────────────────────────────────────────────────────────


class TestChooseCard:
    def test_option_buttons(self):
        card = ChooseCard(
            pending_id="p3", title="Select", question="Pick one",
            options=["A", "B", "C"],
        )
        blocks = card.build_blocks()
        assert len(blocks) == 2  # Header + buttons
        elements = blocks[1]["elements"]
        assert len(elements) == 3
        assert all(e["type"] == "button" for e in elements)

    def test_first_option_primary(self):
        card = ChooseCard(
            pending_id="p3", title="T", question="Q",
            options=["X", "Y"],
        )
        blocks = card.build_blocks()
        elements = blocks[1]["elements"]
        assert elements[0].get("style") == "primary"
        assert "style" not in elements[1]

    def test_action_id_format(self):
        card = ChooseCard(
            pending_id="xyz", title="T", question="Q",
            options=["First", "Second"],
        )
        blocks = card.build_blocks()
        elements = blocks[1]["elements"]
        assert elements[0]["action_id"] == "choose_xyz_opt_0"
        assert elements[1]["action_id"] == "choose_xyz_opt_1"


# ── Modal ────────────────────────────────────────────────────────────────


class TestBuildModalView:
    def test_structure(self):
        view = build_modal_view("p1", "Reply")
        assert view["type"] == "modal"
        assert view["private_metadata"] == "p1"
        assert view["title"]["text"] == "Reply"

    def test_title_truncated(self):
        view = build_modal_view("p1", "A very long title that exceeds limit")
        assert len(view["title"]["text"]) <= 24

    def test_multiline_input(self):
        view = build_modal_view("p1", "T")
        block = view["blocks"][0]
        assert block["element"]["multiline"] is True
        assert block["element"]["action_id"] == "reply_input"
