import pytest

from doubao2api.cookie_utils import _is_target_domain, parse_cookie_records


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("doubao.com", True),
        (".doubao.com", True),
        ("www.doubao.com", True),
        ("sub.doubao.com", True),
        ("example.com", False),
        (".example.com", False),
        ("doubao.com.cn", False),
        ("", False),
    ],
)
def test_is_target_domain(domain: str, expected: bool) -> None:
    assert _is_target_domain(domain, {".doubao.com"}) is expected


def test_parse_simple_cookie_string() -> None:
    records = parse_cookie_records("sessionid=abc; sessionid_ss=def")
    assert len(records) == 2
    assert records[0] == {
        "name": "sessionid",
        "value": "abc",
        "domain": ".doubao.com",
        "path": "/",
        "secure": True,
    }
    assert records[1]["name"] == "sessionid_ss"
    assert records[1]["value"] == "def"


def test_parse_cookie_header_prefix() -> None:
    records = parse_cookie_records("Cookie: sessionid=abc")
    assert len(records) == 1
    assert records[0]["name"] == "sessionid"


def test_parse_set_cookie_with_attributes() -> None:
    text = "sessionid=abc; Domain=.doubao.com; Path=/; Secure; HttpOnly; SameSite=None"
    records = parse_cookie_records(text)
    assert len(records) == 1
    record = records[0]
    assert record["name"] == "sessionid"
    assert record["value"] == "abc"
    assert record["domain"] == ".doubao.com"
    assert record["path"] == "/"
    assert record["secure"] is True
    assert record["httpOnly"] is True
    assert record["sameSite"] == "None"


def test_parse_set_cookie_without_leading_dot_domain() -> None:
    records = parse_cookie_records("sessionid=abc; Domain=doubao.com; Path=/")
    assert records[0]["domain"] == ".doubao.com"


def test_parse_skips_foreign_domain() -> None:
    text = "sessionid=abc; Domain=example.com; Path=/"
    records = parse_cookie_records(text)
    assert records == []


def test_parse_mixed_lines_ignores_foreign() -> None:
    text = (
        "sessionid=abc; Domain=.doubao.com; Path=/\n"
        "bad=id; Domain=evil.com; Path=/\n"
        "sessionid_ss=def; Domain=.doubao.com; Path=/"
    )
    records = parse_cookie_records(text)
    assert len(records) == 2
    assert {r["name"] for r in records} == {"sessionid", "sessionid_ss"}


def test_parse_ignores_malformed_parts() -> None:
    records = parse_cookie_records("sessionid=abc; ; nameonly")
    assert len(records) == 1
    assert records[0]["name"] == "sessionid"


def test_parse_with_allowed_domain_set() -> None:
    records = parse_cookie_records(
        "token=abc; Domain=.deepseek.com; Path=/",
        allowed_domains={".deepseek.com"},
    )
    assert len(records) == 1
    assert records[0]["domain"] == ".deepseek.com"


def test_parse_with_string_domain() -> None:
    records = parse_cookie_records(
        "token=abc; Domain=.deepseek.com; Path=/",
        allowed_domains=".deepseek.com",
    )
    assert len(records) == 1
    assert records[0]["domain"] == ".deepseek.com"
