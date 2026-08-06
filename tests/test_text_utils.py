import pytest

from doubao2api.text_utils import _merge_text_fragments, _text_from_content


@pytest.mark.parametrize(
    ("fragments", "expected"),
    [
        (["hello", "hello world"], "hello world"),
        (["hello world", "world"], "hello world"),
        (["北京装", "装修", "装修公司"], "北京装修公司"),
        (["北京装修公司", "装修公司推荐"], "北京装修公司推荐"),
        (["a", "b", "c"], "abc"),
        (["abc", "def"], "abcdef"),
        (["prefix", "prefix"], "prefix"),
        ([], ""),
        (["", "only"], "only"),
        (["first", "", "second"], "firstsecond"),
        (["overlap", "lapis", "isblue"], "overlapisblue"),
    ],
)
def test_merge_text_fragments(fragments: list[str], expected: str) -> None:
    assert _merge_text_fragments(fragments) == expected


def test_merge_preserves_internal_whitespace() -> None:
    fragments = ["Hello ", "world"]
    assert _merge_text_fragments(fragments) == "Hello world"


def test_text_from_content_handles_various_types() -> None:
    assert _text_from_content("plain") == "plain"
    assert _text_from_content(["a", "b"]) == "a\nb"
    assert _text_from_content([{"text": "x"}, {"other": "y"}]) == "x"
    assert _text_from_content(None) == ""
    assert _text_from_content(123) == "123"
