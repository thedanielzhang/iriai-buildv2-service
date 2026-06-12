"""Pinning tests — operator-attested cross-read-write pair tolerance.

Default (env empty) must stay byte-identical fail-closed; a listed
(reader, writer) pair co-waving is tolerated with a loud warning; unlisted
pairs and reversed order still fail.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from iriai_build_v2.workflows.develop.execution.task_contracts import (
    ContractCompileError,
    ContractPathRule,
    _fail_cross_read_write,
)


def _rule(path: str) -> ContractPathRule:
    return ContractPathRule(repo_id="r1", path=path, intent="modify", source="test")


def _contract(task_id: str, *, reads: list[str] = [], writes: list[str] = []):
    return SimpleNamespace(
        task_id=task_id,
        read_only_paths=[_rule(p) for p in reads],
        allowed_paths=[_rule(p) for p in writes],
        dependency_task_ids=[],
    )


PATH = "supply-chain/app/api/router.py"


def _pair():
    return (
        _contract("READER", reads=[PATH]),
        _contract("WRITER", writes=[PATH]),
    )


def test_default_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IRIAI_CONTRACT_TOLERATED_READ_WRITE_PAIRS", raising=False)
    reader, writer = _pair()
    with pytest.raises(ContractCompileError):
        _fail_cross_read_write(reader, writer, {"READER": 7, "WRITER": 7})


def test_listed_pair_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIAI_CONTRACT_TOLERATED_READ_WRITE_PAIRS", "READER:WRITER")
    reader, writer = _pair()
    _fail_cross_read_write(reader, writer, {"READER": 7, "WRITER": 7})  # no raise


def test_reversed_pair_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIAI_CONTRACT_TOLERATED_READ_WRITE_PAIRS", "WRITER:READER")
    reader, writer = _pair()
    with pytest.raises(ContractCompileError):
        _fail_cross_read_write(reader, writer, {"READER": 7, "WRITER": 7})


def test_unlisted_pair_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIAI_CONTRACT_TOLERATED_READ_WRITE_PAIRS", "OTHER:WRITER, READER:OTHER")
    reader, writer = _pair()
    with pytest.raises(ContractCompileError):
        _fail_cross_read_write(reader, writer, {"READER": 7, "WRITER": 7})
