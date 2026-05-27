"""Governance CLI `python -m` entry point (Slice 19 8th sub-slice).

Per doc-19:62-65 the governance CLI is invoked via:

.. code-block:: bash

    python -m iriai_build_v2.workflows.develop.governance analyze --feature-id <id>
    python -m iriai_build_v2.workflows.develop.governance report --feature-id <id>
    python -m iriai_build_v2.workflows.develop.governance explain-line --repo-id <repo> --path <path> --line <n>
    python -m iriai_build_v2.workflows.develop.governance compare --baseline <corpus> --candidate <corpus>

This module is the thin entry-point wrapper that calls
:func:`iriai_build_v2.workflows.develop.governance.cli.main` and
propagates its typed exit code to :func:`sys.exit`. All the typed
CLI logic lives in :mod:`iriai_build_v2.workflows.develop.governance.cli`
(per the typed-runner-vs-entry-point split convention).

**Activation-authority boundary (doc-19:348-349 + doc-19:296-303).**

The CLI is READ-ONLY:

- NO :data:`~iriai_build_v2.supervisor.read_only.CONTROL_PLANE_WRITER_METHODS`
  extension.
- NO ``dag-*`` artifact-key string literals (the CLI cites
  ``review:*`` artifact keys ONLY -- per doc-19:161-162).
- NO mutation methods on any BaseModel.

This is a 4-line entry-point module by design; it does not own any
typed logic of its own. The typed contract lives in
:mod:`iriai_build_v2.workflows.develop.governance.cli`.
"""

from __future__ import annotations

import sys

from iriai_build_v2.workflows.develop.governance.cli import main


if __name__ == "__main__":
    sys.exit(main())
