from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from iriai_compose.actors import Role
from iriai_compose.storage import AgentSession

from iriai_build_v2.runtimes import normalize_agent_runtime
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

        async def _fake_run_process(command, _prompt, _output_type):
            output_path = command[command.index("-o") + 1]
            schema_path = command[command.index("--output-schema") + 1]
            observed["output_path"] = output_path
            observed["schema_path"] = schema_path
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
