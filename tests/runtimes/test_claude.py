from __future__ import annotations

import sys
from types import SimpleNamespace

from iriai_compose.actors import Role

from iriai_build_v2.config import BUDGET_TIERS
from iriai_build_v2.runtimes.claude import ClaudeAgentRuntime


class _FakeClaudeAgentOptions:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_budget_tiers_use_opus_4_7_native_1m_context():
    assert BUDGET_TIERS["opus"] == "claude-opus-4-7"
    assert BUDGET_TIERS["opus_1m"] == "claude-opus-4-7"


def test_build_options_default_to_opus_4_7(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )

    runtime = object.__new__(ClaudeAgentRuntime)
    role = Role(name="pm", prompt="Plan the work", tools=["Read"])

    options = runtime._build_options(role, workspace=None)

    assert options.model == "claude-opus-4-7"
