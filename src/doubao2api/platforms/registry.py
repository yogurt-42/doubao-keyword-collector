from __future__ import annotations

from .base import AIPlatform
from .deepseek import DEEPSEEK_PLATFORM
from .doubao import DOUBAO_PLATFORM

PLATFORM_REGISTRY: dict[str, AIPlatform] = {
    DOUBAO_PLATFORM.key: DOUBAO_PLATFORM,
    DEEPSEEK_PLATFORM.key: DEEPSEEK_PLATFORM,
}

DEFAULT_PLATFORM_KEY = DOUBAO_PLATFORM.key


def get_platform(key: str | None) -> AIPlatform:
    """Return the platform configuration for the given key.

    Falls back to the default platform if the key is missing or unknown.
    """
    if not key:
        return PLATFORM_REGISTRY[DEFAULT_PLATFORM_KEY]
    return PLATFORM_REGISTRY.get(key, PLATFORM_REGISTRY[DEFAULT_PLATFORM_KEY])


def list_platforms() -> list[AIPlatform]:
    """Return all registered platforms."""
    return list(PLATFORM_REGISTRY.values())
