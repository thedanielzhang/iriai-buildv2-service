"""Generic project-constraints prompt injection (readiness item 7 / R2 interim a).

Flag `IRIAI_PROJECT_CONSTRAINTS_PROMPT` default OFF => the block is always ""
(today's prompts byte-for-byte). Flag ON => the `project-constraints` store
artifact (when present) is rendered as one binding section; absent artifact or
read error degrades to "" (the artifact is optional planning output).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from iriai_compose import Feature

from iriai_build_v2.workflows.develop.phases import implementation as impl

FLAG = impl.PROJECT_CONSTRAINTS_PROMPT_ENV

_CONSTRAINTS_MD = (
    "### Migrations\n- Migrations are AUTHORED ONLY, never executed. [D-3]\n"
    "### Enums\n- approval_status members are UPPERCASE. [D-358]\n"
)


class _Artifacts:
    def __init__(self, value=None, raise_exc: Exception | None = None) -> None:
        self.value = value
        self.raise_exc = raise_exc
        self.get_calls: list[str] = []

    async def get(self, key, feature=None):
        self.get_calls.append(key)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.value


class _Runner:
    def __init__(self, artifacts) -> None:
        self.artifacts = artifacts


def _feature() -> Feature:
    # Unique id per test so the per-feature memo cache never crosses tests.
    return Feature(
        id=f"feat-{uuid.uuid4().hex[:8]}", name="f", slug="f",
        workflow_name="full-develop", workspace_id="main",
    )


def _block(runner, feature) -> str:
    return asyncio.run(impl._project_constraints_prompt_block(runner, feature))


@pytest.fixture(autouse=True)
def _clean_cache():
    impl._PROJECT_CONSTRAINTS_BLOCK_CACHE.clear()
    yield
    impl._PROJECT_CONSTRAINTS_BLOCK_CACHE.clear()


# ---------------------------------------------------------------- flag OFF
def test_flag_off_returns_empty_even_with_artifact(monkeypatch) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    artifacts = _Artifacts(value=_CONSTRAINTS_MD)
    assert _block(_Runner(artifacts), _feature()) == ""
    # today's behavior: the store is never even consulted
    assert artifacts.get_calls == []


def test_flag_explicit_off_values(monkeypatch) -> None:
    for off in ("0", "false", "no", "off"):
        monkeypatch.setenv(FLAG, off)
        assert _block(_Runner(_Artifacts(value=_CONSTRAINTS_MD)), _feature()) == ""


# ----------------------------------------------------------------- flag ON
def test_flag_on_plain_markdown(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    block = _block(_Runner(_Artifacts(value=_CONSTRAINTS_MD)), _feature())
    assert impl._PROJECT_CONSTRAINTS_HEADING in block
    assert "AUTHORED ONLY" in block
    assert "UPPERCASE" in block


def test_flag_on_json_dict_content(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    block = _block(
        _Runner(_Artifacts(value={"content": _CONSTRAINTS_MD})), _feature()
    )
    assert impl._PROJECT_CONSTRAINTS_HEADING in block
    assert "[D-3]" in block


def test_flag_on_json_encoded_string(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    import json

    block = _block(
        _Runner(_Artifacts(value=json.dumps({"content": _CONSTRAINTS_MD}))),
        _feature(),
    )
    assert "[D-358]" in block


def test_flag_on_absent_artifact(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    assert _block(_Runner(_Artifacts(value=None)), _feature()) == ""


def test_flag_on_read_error_degrades_to_empty(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    artifacts = _Artifacts(raise_exc=RuntimeError("store down"))
    feature = _feature()
    assert _block(_Runner(artifacts), feature) == ""
    # errors are NOT cached — a later healthy read self-heals
    artifacts.raise_exc = None
    artifacts.value = _CONSTRAINTS_MD
    assert "[D-3]" in _block(_Runner(artifacts), feature)


def test_flag_on_runner_without_artifacts(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "1")

    class _Bare:
        pass

    assert _block(_Bare(), _feature()) == ""


def test_non_empty_block_is_cached_per_feature(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    artifacts = _Artifacts(value=_CONSTRAINTS_MD)
    feature = _feature()
    runner = _Runner(artifacts)
    first = _block(runner, feature)
    second = _block(runner, feature)
    assert first == second
    assert len(artifacts.get_calls) == 1  # memoized
    # a different feature id is its own cache entry
    _block(runner, _feature())
    assert len(artifacts.get_calls) == 2


# ------------------------------------------------- context-package wiring
def test_context_package_gets_constraints_section(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    captured: dict = {}

    async def _fake_build_context_package(runner, feature, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(
        impl, "build_context_package", _fake_build_context_package
    )
    runner = _Runner(_Artifacts(value=_CONSTRAINTS_MD))
    asyncio.run(
        impl._build_prompt_context_package(
            runner, _feature(), title="t", file_stem="stem",
            intro_lines=[], sections=[("other", "Other", "body")],
        )
    )
    keys = [item.key for item in captured["items"]]
    assert keys == ["project-constraints", "other"]
    assert "[D-358]" in captured["items"][0].content


def test_context_package_unchanged_when_flag_off(monkeypatch) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    captured: dict = {}

    async def _fake_build_context_package(runner, feature, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(
        impl, "build_context_package", _fake_build_context_package
    )
    runner = _Runner(_Artifacts(value=_CONSTRAINTS_MD))
    asyncio.run(
        impl._build_prompt_context_package(
            runner, _feature(), title="t", file_stem="stem",
            intro_lines=[], sections=[("other", "Other", "body")],
        )
    )
    assert [item.key for item in captured["items"]] == ["other"]


# ------------------------------------------------------- raw normalization
def test_text_from_raw_shapes() -> None:
    f = impl._project_constraints_text_from_raw
    assert f(None) == ""
    assert f("plain md") == "plain md"
    assert f({"content": "x"}) == "x"
    assert f({"markdown": "y"}) == "y"
    assert f({"text": "z"}) == "z"
    assert f({"unrelated": 1}) == ""
    assert f(42) == ""
    assert f('"just a json string"') == "just a json string"
    # malformed JSON-looking text falls back to the raw string
    assert f("{not json").startswith("{not json")
