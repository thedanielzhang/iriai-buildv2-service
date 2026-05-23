"""Compatibility wrapper for the typed execution-control journal.

The canonical implementation lives in :mod:`iriai_build_v2.execution_control`.
This module preserves the architecture-documented workflow import path without
creating a second persistence authority.
"""

from iriai_build_v2.execution_control import *  # noqa: F403
from iriai_build_v2.execution_control import __all__
