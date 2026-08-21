from __future__ import annotations

"""Centralized, configurable DOM selectors and patterns.

The goal is to decouple the automation scripts from the exact Doubao page
structure.  When Doubao changes a class name, data-testid, or button label,
most fixes should only require updating the corresponding platform config in
``platforms/``.  This module re-exports the legacy Doubao constants for
backward compatibility during the migration.
"""


# Re-export selector serialization helpers used by inline JS scripts.


def js_selector_list(selectors: list[str]) -> str:
    """Return a JSON-escaped JS array literal from a list of CSS selectors."""

    import json

    return json.dumps(selectors, ensure_ascii=False)


def js_regex_pattern(pattern: str) -> str:
    """Return a JSON-escaped regex string literal."""

    import json

    return json.dumps(pattern, ensure_ascii=False)


def js_string(value: str) -> str:
    """Return a JSON-escaped plain string literal."""

    import json

    return json.dumps(value, ensure_ascii=False)


def js_regex_alternation(patterns: list[str]) -> str:
    """Return a JSON-escaped regex string that matches any of the patterns."""

    import json

    return json.dumps("|".join(patterns), ensure_ascii=False)
