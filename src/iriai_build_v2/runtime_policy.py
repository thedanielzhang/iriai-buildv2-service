from __future__ import annotations

from typing import Literal, cast

RuntimePolicy = Literal["alternating", "primary-impl-secondary-review"]

DEFAULT_RUNTIME_POLICY: RuntimePolicy = "alternating"
PRIMARY_IMPL_SECONDARY_REVIEW_POLICY: RuntimePolicy = "primary-impl-secondary-review"
SUPPORTED_RUNTIME_POLICIES: tuple[RuntimePolicy, ...] = (
    DEFAULT_RUNTIME_POLICY,
    PRIMARY_IMPL_SECONDARY_REVIEW_POLICY,
)

_RUNTIME_POLICY_ALIASES = {
    "alternating": DEFAULT_RUNTIME_POLICY,
    "default": DEFAULT_RUNTIME_POLICY,
    "primary-impl-secondary-review": PRIMARY_IMPL_SECONDARY_REVIEW_POLICY,
    "primary_impl_secondary_review": PRIMARY_IMPL_SECONDARY_REVIEW_POLICY,
    "codex-review": PRIMARY_IMPL_SECONDARY_REVIEW_POLICY,
    "codex_review": PRIMARY_IMPL_SECONDARY_REVIEW_POLICY,
}


def normalize_runtime_policy(name: str | None = None) -> RuntimePolicy:
    raw = (name or DEFAULT_RUNTIME_POLICY).strip().lower()
    resolved = _RUNTIME_POLICY_ALIASES.get(raw)
    if resolved is None:
        supported = ", ".join(SUPPORTED_RUNTIME_POLICIES)
        raise ValueError(
            f"Unsupported runtime policy '{raw}'. Supported values: {supported}"
        )
    return cast(RuntimePolicy, resolved)
