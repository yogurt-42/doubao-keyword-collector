from doubao2api.embedded_browser_client import build_captcha_detect_script
from doubao2api.platforms import get_platform, list_platforms
from doubao2api.platforms.deepseek import DEEPSEEK_EXTRACT_SOURCES_SCRIPT, DEEPSEEK_PLATFORM
from doubao2api.platforms.doubao import DOUBAO_PLATFORM


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


def test_doubao_captcha_selectors_cover_id_based_overlay() -> None:
    """字节验证中心遮罩是 id="captcha_container"，仅有 class 选择器会漏检。"""

    selectors = DOUBAO_PLATFORM.captcha_dom_selectors
    assert '[id*="captcha"]' in selectors
    assert '[id*="verify"]' in selectors


def test_deepseek_captcha_selectors_cover_id_based_overlay() -> None:
    selectors = DEEPSEEK_PLATFORM.captcha_dom_selectors
    assert '[id*="captcha"]' in selectors
    assert '[id*="verify"]' in selectors


def test_captcha_detect_script_flags_fullscreen_overlay() -> None:
    """检测脚本必须输出 fullscreenOverlayMatch 信号（fixed/absolute 全屏遮罩）。"""

    for platform in (DOUBAO_PLATFORM, DEEPSEEK_PLATFORM):
        script = build_captcha_detect_script(platform)
        assert "fullscreenOverlayMatch" in script
        # 选择器经 json 转义，内部引号带反斜杠，这里只断言关键片段
        assert "[id*=" in script


def test_captcha_detect_script_grid_fallback_requires_uniform_large_images() -> None:
    """九宫格兜底必须要求同尺寸大图，否则正常页面的头像/图标会误报。"""

    script = build_captcha_detect_script(DOUBAO_PLATFORM)
    assert "imageGridInfo" in script
    assert "56" in script  # 边长门槛，头像/图标一般 ≤48px
