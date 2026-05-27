"""Small read-only CLI for Slice 21 context-layer inspection."""

from __future__ import annotations

import argparse
import asyncio

from .models import CodeSpanRef, ContextEvidenceSnapshot, ContextLayerRequest, digest_payload
from .providers import NativeGitProvider
from .service import ContextLayerService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m iriai_build_v2.workflows.develop.context_layer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    providers = subparsers.add_parser("providers")
    providers.add_argument("--repo-id", default="repo")
    providers.add_argument("--repo-path", default=".")

    explain = subparsers.add_parser("explain")
    explain.add_argument("--feature-id", required=True)
    explain.add_argument("--repo-id", required=True)
    explain.add_argument("--repo-path", default=".")
    explain.add_argument("--path", required=True)
    explain.add_argument("--line", type=int, required=True)
    explain.add_argument("--dag-artifact-id", type=int, default=1)
    explain.add_argument("--dag-sha256", default=None)

    package = subparsers.add_parser("package")
    package.add_argument("--feature-id", required=True)
    package.add_argument("--task-id", default=None)
    package.add_argument("--repo-id", required=True)
    package.add_argument("--repo-path", default=".")
    package.add_argument("--path", required=True)
    package.add_argument("--line-start", type=int, default=1)
    package.add_argument("--line-end", type=int, default=1)
    package.add_argument("--dag-artifact-id", type=int, default=1)
    package.add_argument("--dag-sha256", default=None)

    args = parser.parse_args(argv)
    if args.command == "providers":
        provider = NativeGitProvider({args.repo_id: args.repo_path})
        availability = asyncio.run(provider.available())
        print(availability.model_dump_json())
        return 0
    if args.command == "explain":
        package_obj = asyncio.run(
            _build_package(
                feature_id=args.feature_id,
                task_id=None,
                repo_id=args.repo_id,
                repo_path=args.repo_path,
                path=args.path,
                line_start=args.line,
                line_end=args.line,
                dag_artifact_id=args.dag_artifact_id,
                dag_sha256=args.dag_sha256,
            )
        )
        print(package_obj.model_dump_json())
        return 0
    if args.command == "package":
        package_obj = asyncio.run(
            _build_package(
                feature_id=args.feature_id,
                task_id=args.task_id,
                repo_id=args.repo_id,
                repo_path=args.repo_path,
                path=args.path,
                line_start=args.line_start,
                line_end=args.line_end,
                dag_artifact_id=args.dag_artifact_id,
                dag_sha256=args.dag_sha256,
            )
        )
        print(package_obj.model_dump_json())
        return 0
    return 2


async def _build_package(
    *,
    feature_id: str,
    task_id: str | None,
    repo_id: str,
    repo_path: str,
    path: str,
    line_start: int,
    line_end: int,
    dag_artifact_id: int,
    dag_sha256: str | None,
):
    effective_dag_sha = dag_sha256 or digest_payload({"feature_id": feature_id})
    snapshot = ContextEvidenceSnapshot(
        source_dag_artifact_id=dag_artifact_id,
        dag_sha256=effective_dag_sha,
        typed_journal_high_watermark=0,
        typed_evidence_digest=digest_payload(
            {
                "feature_id": feature_id,
                "task_id": task_id,
                "repo_id": repo_id,
                "path": path,
                "line_start": line_start,
                "line_end": line_end,
                "dag_artifact_id": dag_artifact_id,
                "dag_sha256": effective_dag_sha,
            }
        ),
    )
    request = ContextLayerRequest(
        feature_id=feature_id,
        source_dag_artifact_id=dag_artifact_id,
        dag_sha256=effective_dag_sha,
        evidence_snapshot=snapshot,
        task_id=task_id,
        repo_ids=[repo_id],
        spans=[
            CodeSpanRef(
                repo_id=repo_id,
                path=path,
                start_line=line_start,
                end_line=line_end,
            )
        ],
    )
    provider = NativeGitProvider({repo_id: repo_path})
    service = ContextLayerService([provider], repos=[])
    return await service.build_context_package(request)


if __name__ == "__main__":
    raise SystemExit(main())
