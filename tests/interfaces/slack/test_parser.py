"""Tests for workflow request parser."""

from iriai_build_v2.interfaces.slack.parser import ParsedRequest, parse_workflow_request


class TestParseWorkflowRequest:
    def test_feature_tag(self):
        result = parse_workflow_request("[FEATURE] Add dark mode")
        assert result == ParsedRequest("full-develop", "Add dark mode", None)

    def test_bug_tag(self):
        result = parse_workflow_request("[BUG] beced7b1")
        assert result == ParsedRequest("bugfix-v2", "beced7b1", "beced7b1")

    def test_plan_tag(self):
        result = parse_workflow_request("[PLAN] API redesign")
        assert result == ParsedRequest("planning", "API redesign", None)

    def test_case_insensitive(self):
        result = parse_workflow_request("[feature] lowercase works")
        assert result is not None
        assert result.workflow_name == "full-develop"

    def test_mixed_case(self):
        result = parse_workflow_request("[Feature] Mixed case")
        assert result is not None
        assert result.workflow_name == "full-develop"

    def test_no_tag(self):
        assert parse_workflow_request("just chatting") is None

    def test_empty_string(self):
        assert parse_workflow_request("") is None

    def test_tag_only_no_name(self):
        assert parse_workflow_request("[FEATURE]") is None

    def test_tag_with_whitespace_only_name(self):
        assert parse_workflow_request("[FEATURE]   ") is None

    def test_unknown_tag(self):
        assert parse_workflow_request("[DEPLOY] something") is None

    def test_tag_not_at_start(self):
        assert parse_workflow_request("hello [FEATURE] something") is None

    def test_strips_whitespace(self):
        result = parse_workflow_request("  [FEATURE]  Add dark mode  ")
        assert result is not None
        assert result.feature_name == "Add dark mode"

    def test_preserves_inner_brackets(self):
        result = parse_workflow_request("[FEATURE] Add [beta] mode")
        assert result is not None
        assert result.feature_name == "Add [beta] mode"

    def test_bug_tag_sets_source_feature_id(self):
        result = parse_workflow_request("[bug] 04ff5ee5 ")
        assert result is not None
        assert result.workflow_name == "bugfix-v2"
        assert result.source_feature_id == "04ff5ee5"

    def test_bug_tag_extracts_source_feature_id_from_bold_slack_formatting(self):
        result = parse_workflow_request("[BUG] *beced7b1*")
        assert result is not None
        assert result.workflow_name == "bugfix-v2"
        assert result.feature_name == "*beced7b1*"
        assert result.source_feature_id == "beced7b1"

    def test_bug_tag_extracts_source_feature_id_from_code_slack_formatting(self):
        result = parse_workflow_request("[BUG] `beced7b1`")
        assert result is not None
        assert result.workflow_name == "bugfix-v2"
        assert result.source_feature_id == "beced7b1"
