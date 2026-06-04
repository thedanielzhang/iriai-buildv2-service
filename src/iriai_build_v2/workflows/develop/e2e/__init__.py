"""Async e2e-testing subsystem.

A standalone, async, non-blocking track that runs e2e/UI verification against
sealed workflow checkpoints, generalized across project types (api, full_stack,
cli, electron, library) via an inferred project profile + thin discovered
adapter. Built and proven entirely standalone (driven by the CLI), read-only
against committed checkpoint state — never importing phase/orchestrator
internals, never acquiring the feature advisory lock, never mutating live repos
or the orchestrator's DB pool.

This package sits beside ``execution/`` and reads its checkpoint state through
the public ``coverage()`` API only.
"""

from __future__ import annotations
