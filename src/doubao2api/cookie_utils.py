from __future__ import annotations

from typing import Any


def _is_target_domain(domain: str, allowed_domains: set[str]) -> bool:
    """Return True if the cookie domain belongs to one of the allowed domains."""

    normalized = domain.strip().lower()
    if not normalized:
        return False
    if normalized in allowed_domains:
        return True
    root = normalized[1:] if normalized.startswith(".") else normalized
    for allowed in allowed_domains:
        allowed_root = allowed[1:] if allowed.startswith(".") else allowed
        if root == allowed_root or root.endswith(f".{allowed_root}"):
            return True
    return False


def _parse_cookie_attributes(parts: list[str], allowed_domains: set[str]) -> dict[str, Any]:
    """Parse a single cookie's attributes from a semicolon-split list."""

    default_domain = next(iter(sorted(allowed_domains)), ".doubao.com")
    record: dict[str, Any] = {
        "domain": default_domain,
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
                if _is_target_domain(value, allowed_domains):
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


def parse_cookie_records(
    cookie_text: str,
    allowed_domains: set[str] | str | None = None,
) -> list[dict[str, Any]]:
    """Parse a cookie string into records suitable for browser cookie jars.

    Supports two common formats:

    1. Simple `name=value; name2=value2` paste (default domain `.doubao.com`).
    2. Set-Cookie style with attributes (`name=value; Domain=...; Path=...`).

    Any cookie whose explicit domain is not under one of the allowed domains is skipped.
    """

    if allowed_domains is None:
        allowed_domains = {".doubao.com"}
    elif isinstance(allowed_domains, str):
        allowed_domains = {allowed_domains}
    else:
        allowed_domains = set(allowed_domains)

    # Normalize allowed domains: ensure leading dot for suffix matching.
    normalized_allowed = set()
    for domain in allowed_domains:
        domain = domain.strip().lower()
        if domain and not domain.startswith("."):
            domain = f".{domain}"
        normalized_allowed.add(domain)

    # Choose a primary default domain for simple `name=value` pastes.
    default_domain = next(iter(sorted(normalized_allowed)), ".doubao.com")

    text = cookie_text.strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()

    records: list[dict[str, Any]] = []

    # Heuristic: if the text contains cookie attributes, treat each line as a
    # single Set-Cookie value; otherwise treat each semicolon part as a cookie.
    has_attributes = any(
        attr in text.lower() for attr in ("domain=", "path=", "samesite=", "httponly", "secure")
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
            attrs = _parse_cookie_attributes(parts[1:], normalized_allowed)
            if not attrs["domain"]:
                continue
            # Recheck the parsed domain against all allowed domains.
            if not _is_target_domain(attrs["domain"], normalized_allowed):
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
                    "domain": default_domain,
                    "path": "/",
                    "secure": True,
                }
            )

    return records
