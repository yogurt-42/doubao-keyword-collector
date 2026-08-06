import re

import pytest

from doubao2api.selectors import (
    REFERENCE_SUMMARY_PATTERN,
    SELECTORS,
    js_regex_alternation,
    js_regex_pattern,
    js_selector_list,
    js_string,
)


def test_selectors_has_required_keys() -> None:
    required = {
        "new_chat",
        "composer",
        "send_button",
        "reference_rows",
        "reference_title",
        "reference_source",
        "reference_expand",
        "reference_more_text",
        "login_controls",
        "history_indicator",
        "captcha_patterns",
    }
    assert required.issubset(SELECTORS)


def test_selector_lists_are_non_empty() -> None:
    for key in ("composer", "send_button", "reference_rows", "reference_expand"):
        assert SELECTORS[key], f"{key} should not be empty"


def test_reference_summary_pattern_matches_example() -> None:
    sample = "搜索 3 个关键词，已阅读 5 篇参考资料，参考 8 篇资料"
    match = re.search(REFERENCE_SUMMARY_PATTERN, sample)
    assert match is not None
    assert match.group(1) == "8"


def test_reference_summary_pattern_matches_new_doubao_phrases() -> None:
    samples = {
        "搜索了 2 个关键词，参考了 5 篇资料": "5",
        "参考 3 篇网页": "3",
        "参考了 1 篇来源": "1",
        "搜索 2 个关键词\n参考 4 篇参考": "4",
    }
    for sample, expected in samples.items():
        match = re.search(REFERENCE_SUMMARY_PATTERN, sample)
        assert match is not None, f"failed to match: {sample}"
        assert match.group(1) == expected, f"wrong capture for: {sample}"


def test_js_helpers_produce_valid_literals() -> None:
    assert js_selector_list(["a", 'b[data="x"]']) == '["a", "b[data=\\"x\\"]"]'
    assert js_string("hello") == '"hello"'
    assert js_regex_pattern(r"\d+") == '"\\\\d+"'
    assert js_regex_alternation(["a", "b"]) == '"a|b"'


def test_captcha_patterns_are_non_empty() -> None:
    assert SELECTORS["captcha_patterns"]
    assert all(isinstance(value, str) and value for value in SELECTORS["captcha_patterns"])


@pytest.mark.parametrize("key", ["reference_title", "reference_source", "reference_more_text"])
def test_string_selectors_are_non_empty(key: str) -> None:
    assert SELECTORS[key]
