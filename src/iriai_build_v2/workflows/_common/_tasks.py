"""Extended task types for the build workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from iriai_compose import Interview, to_str

if TYPE_CHECKING:
    from iriai_compose import Feature, WorkflowRunner

    from ...services.hosting import DocHostingService


class HostedInterview(Interview):
    """Interview that pushes the resulting artifact to DocHostingService on completion."""

    artifact_key: str = ""
    artifact_label: str = ""

    async def on_done(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        *,
        result: Any = None,
        error: BaseException | None = None,
    ) -> None:
        if error or result is None or not self.artifact_key:
            return

        hosting: DocHostingService | None = runner.services.get("hosting")
        if not hosting:
            raise RuntimeError("DocHostingService not available — was workflow on_start called?")

        # Extract artifact text from Envelope
        if hasattr(result, "output") and result.output is not None:
            text = to_str(result.output)
        else:
            text = to_str(result)

        if not text:
            raise RuntimeError(f"HostedInterview produced empty artifact for {self.artifact_key}")

        url = await hosting.push(
            feature.id,
            self.artifact_key,
            text,
            f"{self.artifact_label} — {feature.name}",
        )
        print(f"\n📄 {self.artifact_label} hosted at: {url}\n", flush=True)
