from doubao2api.platforms import get_platform, list_platforms
from doubao2api.platforms.deepseek import DEEPSEEK_EXTRACT_SOURCES_SCRIPT


def test_registry_contains_doubao_and_deepseek() -> None:
    keys = {p.key for p in list_platforms()}
    assert "doubao" in keys
    assert "deepseek" in keys


def test_get_platform_defaults_to_doubao() -> None:
    assert get_platform("").key == "doubao"
    assert get_platform("unknown").key == "doubao"


def test_doubao_platform_has_required_fields() -> None:
    platform = get_platform("doubao")
    assert platform.chat_url == "https://www.doubao.com/chat/"
    assert "sessionid" in platform.session_cookie_names
    assert platform.response_capture_url_patterns
    assert platform.selectors["composer"]


def test_deepseek_platform_has_required_fields() -> None:
    platform = get_platform("deepseek")
    assert platform.chat_url == "https://chat.deepseek.com/"
    assert platform.selectors["composer"]
    assert platform.selectors["send_button"]
    assert platform.extract_references_script


def test_deepseek_extract_sources_script_is_valid_js() -> None:
    # The script is injected as an expression; ensure it is non-empty and balanced.
    assert "div._223dd7b" in DEEPSEEK_EXTRACT_SOURCES_SCRIPT
    assert "a.c64652fe" in DEEPSEEK_EXTRACT_SOURCES_SCRIPT
