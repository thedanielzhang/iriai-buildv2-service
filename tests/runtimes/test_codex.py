from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from iriai_compose.actors import Role
from iriai_compose.storage import AgentSession

from iriai_build_v2.runtimes import normalize_agent_runtime, secondary_agent_runtime_name
from iriai_build_v2.runtimes.codex import CodexAgentRuntime, _prepare_schema
from iriai_build_v2.models.outputs import Envelope, ReviewOutcome


class TestNormalizeAgentRuntime:
    def test_defaults_to_claude(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("IRIAI_AGENT_RUNTIME", raising=False)
        assert normalize_agent_runtime() == "claude"

    def test_accepts_aliases(self):
        assert normalize_agent_runtime("openai") == "codex"
        assert normalize_agent_runtime("anthropic") == "claude"

    def test_rejects_unknown_value(self):
        with pytest.raises(ValueError, match="Unsupported agent runtime"):
            normalize_agent_runtime("something-else")

    def test_codex_secondary_stays_codex(self):
        assert secondary_agent_runtime_name("codex") == "codex"

    def test_claude_secondary_is_codex(self):
        assert secondary_agent_runtime_name("claude") == "codex"

    def test_claude_secondary_stays_claude_in_single_runtime_mode(self):
        assert secondary_agent_runtime_name("claude", single_runtime=True) == "claude"


class TestCodexAgentRuntime:
    def _runtime(self, monkeypatch: pytest.MonkeyPatch) -> CodexAgentRuntime:
        monkeypatch.setattr(
            "iriai_build_v2.runtimes.codex.shutil.which",
            lambda _command: "/usr/local/bin/codex",
        )
        return CodexAgentRuntime()

    def test_build_command_is_fresh_session_and_ignores_claude_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        runtime = self._runtime(monkeypatch)
        role = Role(
            name="pm",
            prompt="Plan the work",
            tools=["Read", "Write"],
            model="claude-sonnet-4-6",
        )

        command = runtime._build_command(
            role=role,
            workspace=SimpleNamespace(path="/tmp/project"),
            output_schema_path="/tmp/schema.json",
            output_path="/tmp/final.txt",
            resume_thread_id=None,
            ephemeral=False,
        )

        assert command[:2] == ["codex", "exec"]
        assert "--output-schema" in command
        assert "/tmp/schema.json" in command
        assert "-m" not in command
        assert command[-1] == "-"

    def test_compose_prompt_includes_notes_and_prior_turns(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        runtime = self._runtime(monkeypatch)
        role = Role(
            name="pm",
            prompt="Lead the planning conversation.",
            tools=["Read"],
            metadata={"keep_recent_messages": 2},
        )
        session = AgentSession(
            session_key="pm:feat-1",
            session_id=None,
            metadata={
                "turns": [
                    {"role": "user", "text": "We need SSO support."},
                    {"role": "assistant", "text": "I will scope the auth flow."},
                ]
            },
        )
        runtime.queue_user_note("feat-1", "Also include a mobile-first constraint.")

        prompt = runtime._compose_prompt(
            role,
            "Draft the next response.",
            feature_id="feat-1",
            session=session,
            output_type=None,
        )

        assert "Also include a mobile-first constraint." in prompt
        assert "## Prior Conversation" in prompt
        assert "User: We need SSO support." in prompt
        assert "Assistant: I will scope the auth flow." in prompt

    def test_prior_turns_are_used_even_when_session_has_old_thread_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        runtime = self._runtime(monkeypatch)
        role = Role(name="architect", prompt="Design it.", metadata={})
        session = AgentSession(
            session_key="architect:feat-1",
            session_id="old-thread-id",
            metadata={"turns": [{"role": "assistant", "text": "Previous design context."}]},
        )

        prompt = runtime._compose_prompt(
            role,
            "Continue the architecture review.",
            feature_id="feat-1",
            session=session,
            output_type=None,
        )

        assert "## Prior Conversation" in prompt
        assert "Previous design context." in prompt

    def test_prepare_schema_sets_additional_properties_false(self):
        prepared = _prepare_schema(Envelope[ReviewOutcome].model_json_schema())
        assert prepared["additionalProperties"] is False
        assert prepared["required"] == ["question", "options", "output", "complete", "artifact_path"]
        assert prepared["properties"]["output"]["anyOf"][0]["additionalProperties"] is False

    def test_mcp_config_flags_generates_correct_flags(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        runtime = self._runtime(monkeypatch)
        role = Role(
            name="verifier",
            prompt="Verify the implementation",
            tools=["Read", "Bash"],
            metadata={
                "mcp_servers": {
                    "playwright": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "@anthropic/mcp-playwright"],
                    },
                    "github": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-github"],
                        "env": {"GITHUB_TOKEN": "test-token"},
                    },
                },
            },
        )

        flags = runtime._mcp_config_flags(role)

        assert "-c" in flags
        assert 'mcp_servers.playwright.command="npx"' in flags
        assert 'mcp_servers.playwright.args=["-y", "@anthropic/mcp-playwright"]' in flags
        assert 'mcp_servers.github.command="npx"' in flags
        assert 'mcp_servers.github.env.GITHUB_TOKEN="test-token"' in flags
        # type field should NOT appear
        assert not any("type" in f and "stdio" in f for f in flags)

    def test_mcp_config_flags_empty_when_no_servers(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        runtime = self._runtime(monkeypatch)
        role = Role(name="pm", prompt="Plan", tools=["Read"])
        assert runtime._mcp_config_flags(role) == []

    def test_build_command_excludes_mcp_flags(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """MCP servers are now configured via CODEX_HOME, not -c flags."""
        runtime = self._runtime(monkeypatch)
        role = Role(
            name="verifier",
            prompt="Verify",
            tools=["Read"],
            metadata={
                "mcp_servers": {
                    "playwright": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "@anthropic/mcp-playwright"],
                    },
                },
            },
        )

        command = runtime._build_command(
            role=role,
            workspace=SimpleNamespace(path="/tmp/project"),
            output_schema_path=None,
            output_path="/tmp/out.txt",
            resume_thread_id=None,
            ephemeral=True,
        )

        assert not any("mcp_servers" in arg for arg in command)
        assert command[-1] == "-"

    def test_build_command_includes_add_dir_npm(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        runtime = self._runtime(monkeypatch)
        role = Role(name="impl", prompt="Implement", tools=["Read", "Write"])

        command = runtime._build_command(
            role=role,
            workspace=SimpleNamespace(path="/tmp/project"),
            output_schema_path=None,
            output_path="/tmp/out.txt",
            resume_thread_id=None,
            ephemeral=True,
        )

        assert "--add-dir" in command
        idx = command.index("--add-dir")
        assert ".npm" in command[idx + 1]

    def test_build_command_uses_dangerous_bypass_for_gate_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        runtime = self._runtime(monkeypatch)
        role = Role(name="lead-architect", prompt="Review the system design")

        command = runtime._build_command(
            role=role,
            workspace=SimpleNamespace(path="/tmp/project"),
            output_schema_path=None,
            output_path="/tmp/out.txt",
            resume_thread_id=None,
            ephemeral=True,
            session_key="lead-architect-gate-reviewer:feat-1",
        )

        assert "--dangerously-bypass-approvals-and-sandbox" in command
        assert "--full-auto" not in command
        assert "shell_environment_policy.inherit=all" in command

    def test_build_command_keeps_full_auto_for_non_e2e_role(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        runtime = self._runtime(monkeypatch)
        role = Role(name="implementer", prompt="Implement the feature")

        command = runtime._build_command(
            role=role,
            workspace=SimpleNamespace(path="/tmp/project"),
            output_schema_path=None,
            output_path="/tmp/out.txt",
            resume_thread_id=None,
            ephemeral=True,
            session_key="implementer:feat-1",
        )

        assert "--full-auto" in command
        assert "--dangerously-bypass-approvals-and-sandbox" not in command

    def test_compose_prompt_includes_mcp_tools_section(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        runtime = self._runtime(monkeypatch)
        role = Role(
            name="verifier",
            prompt="Verify the implementation.",
            tools=["Read"],
            metadata={
                "mcp_servers": {
                    "playwright": {"command": "npx", "args": []},
                    "qa-feedback": {"command": "node", "args": []},
                },
            },
        )

        prompt = runtime._compose_prompt(
            role,
            "Run verification.",
            feature_id=None,
            session=None,
            output_type=None,
        )

        assert "## MCP Tools Available" in prompt
        assert "playwright" in prompt
        assert "qa-feedback" in prompt

    def test_compose_prompt_announces_runtime_capabilities_for_gate_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ):
        runtime = self._runtime(monkeypatch)
        monkeypatch.setattr(
            runtime,
            "_discover_database_url",
            lambda **_kwargs: "postgresql://danielzhang@localhost:5431/compose_dev",
        )
        role = Role(
            name="lead-architect",
            prompt="Review the latest design and verify it.",
            metadata={
                "mcp_servers": {
                    "context7": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "@upstash/context7-mcp@latest"],
                    },
                },
            },
        )

        prompt = runtime._compose_prompt(
            role,
            "Continue the gate review.",
            feature_id="beced7b1",
            session_key="lead-architect-gate-reviewer:beced7b1",
            workspace=SimpleNamespace(path=str(tmp_path)),
        )

        assert "## Runtime Capabilities" in prompt
        assert "Playwright" in prompt
        assert "postgres" in prompt
        assert "preview" in prompt

    @pytest.mark.asyncio
    async def test_read_stdout_handles_large_jsonl_events(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        runtime = self._runtime(monkeypatch)
        emitted: list[object] = []
        runtime.on_message = emitted.append

        large_text = "A" * 200_000
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "msg-1",
                    "type": "agent_message",
                    "text": large_text,
                },
            }
        ) + "\n"

        stdout = asyncio.StreamReader()
        stdout.feed_data(line[:70_000].encode("utf-8"))
        stdout.feed_data(line[70_000:].encode("utf-8"))
        stdout.feed_eof()

        state = {"thread_id": None, "last_agent_message": "", "last_error": ""}
        await runtime._read_stdout(stdout, state=state, output_type=None)

        assert state["last_agent_message"] == large_text
        assert emitted
        assert emitted[-1].content[0].text == large_text

    @pytest.mark.asyncio
    async def test_run_process_aborts_if_stdout_reader_crashes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        runtime = self._runtime(monkeypatch)

        class _FakeStdin:
            def write(self, _data: bytes) -> None:
                return None

            async def drain(self) -> None:
                return None

            def close(self) -> None:
                return None

        class _FakeProc:
            def __init__(self) -> None:
                self.stdin = _FakeStdin()
                self.stdout = asyncio.StreamReader()
                self.stderr = asyncio.StreamReader()
                self.returncode: int | None = None
                self._waiter: asyncio.Future[int] = asyncio.get_running_loop().create_future()

            async def wait(self) -> int:
                return await self._waiter

            def kill(self) -> None:
                self.returncode = -9
                if not self._waiter.done():
                    self._waiter.set_result(-9)
                self.stderr.feed_eof()

        fake_proc = _FakeProc()

        async def _fake_create_subprocess_exec(*_args, **_kwargs):
            return fake_proc

        async def _broken_stdout(*_args, **_kwargs):
            raise ValueError("boom")

        async def _empty_stderr(_stderr):
            return ""

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
        monkeypatch.setattr(runtime, "_read_stdout", _broken_stdout)
        monkeypatch.setattr(runtime, "_read_stderr", _empty_stderr)

        with pytest.raises(RuntimeError, match="stdout reader failed"):
            await runtime._run_process(["codex", "exec"], "prompt", None)

        assert fake_proc.returncode == -9

    @pytest.mark.asyncio
    async def test_invoke_persists_full_assistant_turn_text(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        class _Store:
            def __init__(self) -> None:
                self.sessions: dict[str, AgentSession] = {}

            async def load(self, session_key: str):
                return self.sessions.get(session_key)

            async def save(self, session: AgentSession) -> None:
                self.sessions[session.session_key] = session

            async def delete(self, session_key: str) -> None:
                self.sessions.pop(session_key, None)

        monkeypatch.setattr(
            "iriai_build_v2.runtimes.codex.shutil.which",
            lambda _command: "/usr/local/bin/codex",
        )
        runtime = CodexAgentRuntime(session_store=_Store())
        monkeypatch.setattr(runtime, "_log_runtime_differences", lambda _role: None)

        long_text = "A" * 8000

        async def _fake_run_codex(*_args, **_kwargs):
            return long_text, None

        monkeypatch.setattr(runtime, "_run_codex", _fake_run_codex)

        role = Role(
            name="planner",
            prompt="Plan it.",
            metadata={"max_session_chars": 10_000},
        )

        result = await runtime.invoke(
            role,
            "Do the work.",
            session_key="planner:feat-1",
        )

        assert result == long_text
        session = await runtime.session_store.load("planner:feat-1")
        assert session is not None
        assert session.metadata["turns"][0]["text"] == long_text

    @pytest.mark.asyncio
    async def test_run_codex_uses_workspace_local_temp_files(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ):
        runtime = self._runtime(monkeypatch)
        workspace_root = tmp_path / "workspace"
        repo_root = workspace_root / ".iriai" / "features" / "feat" / "repos" / "app"
        repo_root.mkdir(parents=True)

        class _SimpleOutput(BaseModel):
            value: str

        observed: dict[str, str] = {}

        async def _fake_run_process(command, _prompt, _output_type, *, env=None):
            output_path = command[command.index("-o") + 1]
            schema_path = command[command.index("--output-schema") + 1]
            observed["output_path"] = output_path
            observed["schema_path"] = schema_path
            observed["env"] = env
            assert Path(output_path).parent == workspace_root / ".iriai" / "runtime" / "codex"
            assert Path(schema_path).parent == workspace_root / ".iriai" / "runtime" / "codex"
            Path(output_path).write_text('{"value":"ok"}', encoding="utf-8")
            return "", None, ""

        monkeypatch.setattr(runtime, "_run_process", _fake_run_process)

        final_text, _thread_id = await runtime._run_codex(
            Role(name="implementer", prompt="Do the work"),
            "Return a value.",
            workspace=SimpleNamespace(path=repo_root),
            output_type=_SimpleOutput,
            resume_thread_id=None,
            ephemeral=True,
        )

        assert final_text == '{"value":"ok"}'
        assert observed
        # CODEX_HOME should have been passed in env
        assert observed["env"] is not None
        assert "CODEX_HOME" in observed["env"]


class TestCodexHomeIsolation:
    """Tests for per-invocation CODEX_HOME to prevent MCP server bloat."""

    def _runtime(self, monkeypatch: pytest.MonkeyPatch) -> CodexAgentRuntime:
        monkeypatch.setattr(
            "iriai_build_v2.runtimes.codex.shutil.which",
            lambda _command: "/usr/local/bin/codex",
        )
        return CodexAgentRuntime()

    def test_prepare_codex_home_creates_minimal_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ):
        runtime = self._runtime(monkeypatch)
        # Simulate global config with model setting but no MCP servers
        runtime._global_codex_config = {"model": "gpt-5.4"}

        role = Role(
            name="implementer",
            prompt="Do the work",
            metadata={
                "mcp_servers": {
                    "context7": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "@upstash/context7-mcp@latest"],
                    },
                },
            },
        )

        codex_home = runtime._prepare_codex_home(
            role, SimpleNamespace(path=str(tmp_path)),
        )

        config_path = Path(codex_home) / "config.toml"
        assert config_path.exists()
        content = config_path.read_text()
        # Should include global model setting
        assert 'model = "gpt-5.4"' in content
        # Should include only context7, not playwright/github/etc.
        assert "context7" in content
        assert "playwright" not in content
        assert "github" not in content
        assert "sequential-thinking" not in content

        # Cleanup
        shutil.rmtree(codex_home)

    def test_prepare_codex_home_no_mcp_servers_in_role(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ):
        runtime = self._runtime(monkeypatch)
        runtime._global_codex_config = {"model": "gpt-5.4"}

        role = Role(name="pm", prompt="Plan", tools=["Read"])

        codex_home = runtime._prepare_codex_home(
            role, SimpleNamespace(path=str(tmp_path)),
        )

        content = (Path(codex_home) / "config.toml").read_text()
        assert "mcp_servers" not in content
        assert 'model = "gpt-5.4"' in content

        shutil.rmtree(codex_home)

    def test_prepare_codex_home_augments_gate_role_with_e2e_servers(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ):
        runtime = self._runtime(monkeypatch)
        runtime._global_codex_config = {"model": "gpt-5.4"}
        monkeypatch.setattr(
            runtime,
            "_discover_database_url",
            lambda **_kwargs: "postgresql://danielzhang@localhost:5431/compose_dev",
        )
        monkeypatch.setenv("RAILWAY_TOKEN", "railway-token")

        role = Role(
            name="lead-architect",
            prompt="Review the architecture",
            metadata={
                "mcp_servers": {
                    "context7": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "@upstash/context7-mcp@latest"],
                    },
                },
            },
        )

        codex_home = runtime._prepare_codex_home(
            role,
            SimpleNamespace(path=str(tmp_path)),
            feature_id="beced7b1",
            session_key="lead-architect-gate-reviewer:beced7b1",
        )

        content = (Path(codex_home) / "config.toml").read_text()
        assert "context7" in content
        assert "playwright" in content
        assert "qa-feedback" in content
        assert "preview" in content
        assert "postgres" in content
        assert "postgresql://danielzhang@localhost:5431/compose_dev" in content
        assert 'RAILWAY_TOKEN = "railway-token"' in content

        shutil.rmtree(codex_home)

    def test_prepare_codex_home_omits_postgres_without_database_url(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ):
        runtime = self._runtime(monkeypatch)
        runtime._global_codex_config = {"model": "gpt-5.4"}
        monkeypatch.setattr(runtime, "_discover_database_url", lambda **_kwargs: None)

        role = Role(
            name="integration-tester",
            prompt="Run integration tests",
            metadata={
                "mcp_servers": {
                    "playwright": {"type": "stdio", "command": "npx", "args": ["-y", "@playwright/mcp"]},
                    "postgres": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres"]},
                },
            },
        )

        codex_home = runtime._prepare_codex_home(
            role,
            SimpleNamespace(path=str(tmp_path)),
            feature_id="beced7b1",
            session_key="integration-tester:beced7b1",
        )

        content = (Path(codex_home) / "config.toml").read_text()
        assert "playwright" in content
        assert "postgres" not in content

        shutil.rmtree(codex_home)

    def test_prepare_codex_home_symlinks_auth(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ):
        runtime = self._runtime(monkeypatch)
        runtime._global_codex_config = {}

        # Create a fake auth.json in a fake CODEX_HOME
        fake_global_home = tmp_path / "global_codex"
        fake_global_home.mkdir()
        auth = fake_global_home / "auth.json"
        auth.write_text('{"auth_mode": "test"}')
        monkeypatch.setenv("CODEX_HOME", str(fake_global_home))

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        role = Role(name="impl", prompt="Do it")

        codex_home = runtime._prepare_codex_home(
            role, SimpleNamespace(path=str(workspace)),
        )

        auth_link = Path(codex_home) / "auth.json"
        assert auth_link.exists()
        assert auth_link.read_text() == '{"auth_mode": "test"}'

        shutil.rmtree(codex_home)

    def test_parallel_codex_homes_are_isolated(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ):
        runtime = self._runtime(monkeypatch)
        runtime._global_codex_config = {}
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nonexistent"))

        role_a = Role(
            name="fixer-a", prompt="Fix A",
            metadata={"mcp_servers": {"context7": {"command": "npx", "args": []}}},
        )
        role_b = Role(
            name="fixer-b", prompt="Fix B",
            metadata={"mcp_servers": {"playwright": {"command": "npx", "args": []}}},
        )

        workspace = SimpleNamespace(path=str(tmp_path))
        home_a = runtime._prepare_codex_home(role_a, workspace)
        home_b = runtime._prepare_codex_home(role_b, workspace)

        assert home_a != home_b
        assert "context7" in (Path(home_a) / "config.toml").read_text()
        assert "playwright" in (Path(home_b) / "config.toml").read_text()
        assert "playwright" not in (Path(home_a) / "config.toml").read_text()
        assert "context7" not in (Path(home_b) / "config.toml").read_text()

        shutil.rmtree(home_a)
        shutil.rmtree(home_b)

    def test_discover_database_url_prefers_feature_specific_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ):
        runtime = self._runtime(monkeypatch)
        monkeypatch.delenv("IRIAI_E2E_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        feature_env = (
            tmp_path
            / ".iriai"
            / "features"
            / "compose-beced7b1"
            / "repos"
            / "tools"
            / "compose"
            / "backend"
            / ".env"
        )
        feature_env.parent.mkdir(parents=True)
        feature_env.write_text(
            "DATABASE_URL=postgresql://danielzhang@localhost:5431/compose_dev\n",
            encoding="utf-8",
        )

        database_url = runtime._discover_database_url(
            workspace=SimpleNamespace(path=str(tmp_path)),
            feature_id="beced7b1",
        )

        assert database_url == "postgresql://danielzhang@localhost:5431/compose_dev"
