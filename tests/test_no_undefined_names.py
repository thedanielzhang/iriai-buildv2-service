"""Regression guard against F821 undefined-name bugs in the shipped package.

A missing import is invisible to a plain ``import`` of the module — Python only
raises ``NameError`` when the offending line actually executes. That let
``broad.py`` ship calling ``choose_step_mode`` without importing it, which
halted a live planning workflow at runtime. A static F821 scan catches the
entire class, so we scan the whole package here with ruff.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import iriai_build_v2

_RUFF = shutil.which("ruff")
if _RUFF is None:  # pragma: no cover - env dependent
    pytest.skip("ruff not installed", allow_module_level=True)


def test_package_has_no_undefined_names() -> None:
    pkg_dir = Path(iriai_build_v2.__file__).parent
    result = subprocess.run(
        [
            _RUFF,
            "check",
            "--select",
            "F821",
            "--no-cache",
            "--output-format",
            "concise",
            str(pkg_dir),
        ],
        capture_output=True,
        text=True,
    )
    errors = [line for line in result.stdout.splitlines() if "F821" in line]
    assert not errors, "F821 undefined names:\n" + "\n".join(errors)
