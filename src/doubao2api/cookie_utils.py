from __future__ import annotations

from typing import Any

_DOUBAO_DOMAINS = {"doubao.com", "www.doubao.com", ".doubao.com"}


def _is_doubao_domain(domain: str) -> bool:
    """Return True if the cookie domain belongs to Doubao."""

    normalized = domain.strip().lower()
    if not normalized:
        return False
    if normalized in _DOUBAO_DOMAINS:
        return True
    root = normalized[1:] if normalized.startswith(".") else normalized
    return root == "doubao.com" or root.endswith(".doubao.com")


def _parse_cookie_attributes(parts: list[str]) -> dict[str, Any]:
    """Parse a single cookie's attributes from a semicolon-split list."""

    record: dict[str, Any] = {
        "domain": ".doubao.com",
        "path": "/",
        "secure": True,
    }
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            name, value = part.split("=", 1)
            name = name.strip()
            value = value.strip()
            lower = name.lower()
            if lower == "domain":
                if _is_doubao_domain(value):
                    record["domain"] = value if value.startswith(".") else f".{value}"
                else:
                    record["domain"] = ""
            elif lower == "path":
                record["path"] = value
            elif lower == "samesite":
                record["sameSite"] = value
        else:
            lower = part.lower()
            if lower == "secure":
                record["secure"] = True
            elif lower == "httponly":
                record["httpOnly"] = True
    return record


def parse_cookie_records(cookie_text: str) -> list[dict[str, Any]]:
    """Parse a cookie string into records suitable for browser cookie jars.

    Supports two common formats:

    1. Simple `name=value; name2=value2` paste (default domain `.doubao.com`).
    2. Set-Cookie style with attributes (`name=value; Domain=...; Path=...`).

    Any cookie whose explicit domain is not under `doubao.com` is skipped.
    """

    text = cookie_text.strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()

    records: list[dict[str, Any]] = []

    # Heuristic: if the text contains cookie attributes, treat each line as a
    # single Set-Cookie value; otherwise treat each semicolon part as a cookie.
    has_attributes = any(
        attr in text.lower()
        for attr in ("domain=", "path=", "samesite=", "httponly", "secure")
    )

    if has_attributes:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [part.strip() for part in line.split(";")]
            if not parts or "=" not in parts[0]:
                continue
            name, value = parts[0].split("=", 1)
            name = name.strip()
            value = value.strip()
            if not name:
                continue
            attrs = _parse_cookie_attributes(parts[1:])
            if not attrs["domain"]:
                continue
            records.append(
                {
                    "name": name,
                    "value": value,
                    **attrs,
                }
            )
    else:
        for part in text.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, value = part.split("=", 1)
            name = name.strip()
            if not name:
                continue
            records.append(
                {
                    "name": name,
                    "value": value.strip(),
                    "domain": ".doubao.com",
                    "path": "/",
                    "secure": True,
                }
            )

    return records
