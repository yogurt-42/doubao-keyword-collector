from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AIPlatform:
    """Static configuration for an AI chat platform.

    This object contains everything that differs between Doubao, DeepSeek, etc.
    so the browser clients can remain generic.
    """

    key: str
    name: str
    chat_url: str
    session_cookie_names: frozenset[str]
    selectors: dict[str, Any]
    reference_summary_pattern: str = ""
    more_references_text: str = ""
    ignored_hosts: frozenset[str] = field(default_factory=frozenset)
    cookie_domains: frozenset[str] = field(default_factory=frozenset)
    chat_models: list[str] = field(default_factory=list)
    response_capture_url_patterns: list[str] = field(default_factory=list)
    captcha_patterns: list[str] = field(default_factory=list)
    captcha_iframe_patterns: list[str] = field(default_factory=list)
    captcha_dom_selectors: list[str] = field(default_factory=list)
    extract_references_script: str = ""

    def cookie_match_domains(self) -> frozenset[str]:
        """Return domains used for manual cookie import filtering."""
        return self.cookie_domains or self.ignored_hosts


# Type alias for a callable that builds a browser client for a platform.
PlatformClientFactory = Callable[[Any, "AIPlatform"], Any]
