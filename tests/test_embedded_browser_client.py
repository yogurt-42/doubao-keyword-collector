from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from doubao2api.embedded_browser_client import EmbeddedBrowserClient


class FakeBridge:
    def __init__(self) -> None:
        self.scripts: list[str] = []
        self.opened = False
        self.focus_count = 0
        self.activation_count = 0

    async def open_account(self, account_name: str, account_dir: Path, url: str) -> None:
        self.opened = True

    async def close_account(self, account_name: str) -> None:
        self.opened = False

    async def focus_account(self, account_name: str) -> None:
        self.focus_count += 1
        return None

    async def activate_account(self, account_name: str) -> None:
        self.activation_count += 1
        return None

    async def cookies(self, account_name: str) -> list[dict[str, Any]]:
        return [{"name": "sessionid", "value": "test-session"}]

    async def state(self, account_name: str) -> dict[str, Any]:
        return {
            "opened": self.opened,
            "page_url": "https://www.doubao.com/chat/",
        }

    async def navigate(self, account_name: str, url: str) -> None:
        return None

    async def screenshot(self, account_name: str) -> bytes:
        return b""

    async def set_cookies(self, account_name: str, cookies: list[dict[str, Any]]) -> None:
        return None

    async def run_javascript(self, account_name: str, script: str) -> Any:
        self.scripts.append(script)
        if "window.__doubaoEmbeddedCapture || {}" in script:
            return {"done": True, "events": [{"text": "回答完成"}]}
        if "referenceReady" in script:
            return {"loading": False, "referenceReady": True}
        if "const pattern" in script:
            return 0
        if (
            'a[data-tool-call-item-id*="-result-"]' in script
            or 'a[data-tool-call-item-id*=\\"-result-\\"]' in script
        ):
            return []
        # Helpers introduced to make sending more robust.
        if ").found" in script or script.strip().endswith(".found"):
            return True
        if "found: Boolean(textarea)" in script:
            return {"found": True, "value": ""}
        if "textarea.value === ''" in script:
            return True
        return True


class DomCompletionBridge(FakeBridge):
    def __init__(self) -> None:
        super().__init__()
        self.loading_checks = 0

    async def run_javascript(self, account_name: str, script: str) -> Any:
        self.scripts.append(script)
        if "window.__doubaoEmbeddedCapture || {}" in script:
            return {"done": False, "events": []}
        if "referenceReady" in script:
            self.loading_checks += 1
            return {
                "loading": self.loading_checks == 1,
                "referenceReady": False,
            }
        if ").found" in script or script.strip().endswith(".found"):
            return True
        if "found: Boolean(textarea)" in script:
            return {"found": True, "value": ""}
        if "textarea.value === ''" in script:
            return True
        return True


class CaptchaBridge(FakeBridge):
    async def run_javascript(self, account_name: str, script: str) -> Any:
        self.scripts.append(script)
        if "window.__doubaoEmbeddedCapture || {}" in script:
            return {"done": False, "events": []}
        if "referenceReady" in script:
            return {
                "loading": False,
                "captcha": True,
                "referenceReady": False,
            }
        if ").found" in script or script.strip().endswith(".found"):
            return True
        if "found: Boolean(textarea)" in script:
            return {"found": True, "value": ""}
        if "textarea.value === ''" in script:
            return True
        return True


class QtJsonBridge(FakeBridge):
    async def cookies(self, account_name: str) -> list[dict[str, Any]]:
        return [{"name": "ttwid", "value": "test"}]

    async def run_javascript(self, account_name: str, script: str) -> Any:
        self.scripts.append(script)
        value: Any = {
            "ready": True,
            "hasLoginControl": False,
            "hasNewChat": True,
            "hasHistory": True,
            "hasComposer": True,
            "loggedIn": True,
        }
        return json.dumps(
            {
                "__doubaoBridge": True,
                "ok": True,
                "value": value,
            }
        )


class NewAccountBridge(QtJsonBridge):
    """Simulates a freshly logged-in account with no chat history yet."""

    async def cookies(self, account_name: str) -> list[dict[str, Any]]:
        return []

    async def run_javascript(self, account_name: str, script: str) -> Any:
        self.scripts.append(script)
        value: Any = {
            "ready": True,
            "hasLoginControl": False,
            "hasNewChat": True,
            "hasHistory": False,
            "hasComposer": True,
            "hasCaptcha": False,
            "loggedIn": True,
        }
        return json.dumps(
            {
                "__doubaoBridge": True,
                "ok": True,
                "value": value,
            }
        )


class FullscreenCaptchaBridge(FakeBridge):
    """Simulates a page covered by the Bytedance verify-center fullscreen overlay.

    The overlay lives in a cross-origin iframe (#captcha_container), so body
    text never mentions it; only the structural detection script can see it.
    """

    async def run_javascript(self, account_name: str, script: str) -> Any:
        self.scripts.append(script)
        if "fullscreenOverlayMatch" in script:
            return {
                "textMatch": False,
                "iframeMatch": True,
                "imageGridMatch": False,
                "dragHandleMatch": False,
                "fullscreenOverlayMatch": True,
                "overlayVisible": True,
            }
        return await super().run_javascript(account_name, script)


class VisualCaptchaStateBridge(QtJsonBridge):
    """Logged-in page state plus a structural captcha hit on the detect script."""

    async def run_javascript(self, account_name: str, script: str) -> Any:
        self.scripts.append(script)
        if "fullscreenOverlayMatch" in script:
            value: Any = {
                "textMatch": False,
                "iframeMatch": True,
                "imageGridMatch": False,
                "dragHandleMatch": False,
                "fullscreenOverlayMatch": True,
                "overlayVisible": True,
            }
            return json.dumps({"__doubaoBridge": True, "ok": True, "value": value})
        return await super().run_javascript(account_name, script)


class SendFailCaptchaBridge(FakeBridge):
    """Send button never becomes ready; captcha overlay appears after the failure.

    The first structural check (pre-send) reports no captcha; the second one
    (after the send failure) reports the fullscreen overlay.
    """

    def __init__(self) -> None:
        super().__init__()
        self.captcha_checks = 0

    async def run_javascript(self, account_name: str, script: str) -> Any:
        self.scripts.append(script)
        if "fullscreenOverlayMatch" in script:
            self.captcha_checks += 1
            hit = self.captcha_checks >= 2
            return {
                "textMatch": False,
                "iframeMatch": hit,
                "imageGridMatch": False,
                "dragHandleMatch": False,
                "fullscreenOverlayMatch": hit,
                "overlayVisible": hit,
            }
        if "#flow-end-msg-send" in script and "return Boolean(button" in script:
            return False
        if "KeyboardEvent" in script:
            return False
        if "answerStarted" in script:
            return False
        return await super().run_javascript(account_name, script)


class FlakySendBridge(FakeBridge):
    """第一轮发送全部未确认（React 状态丢失），重填词后的第二轮正常。"""

    def __init__(self) -> None:
        super().__init__()
        self.type_count = 0

    async def run_javascript(self, account_name: str, script: str) -> Any:
        self.scripts.append(script)
        if "beforeinput" in script:
            self.type_count += 1
            return await super().run_javascript(account_name, script)
        if self.type_count <= 1:
            if "#flow-end-msg-send" in script and "return Boolean(button" in script:
                return False
            if "KeyboardEvent" in script:
                return False
            if "answerStarted" in script:
                return False
        return await super().run_javascript(account_name, script)


class DelayedReadyBridge(FakeBridge):
    """Simulates a page where textarea and send button become ready after checks."""

    def __init__(self) -> None:
        super().__init__()
        self.textarea_checks = 0
        self.send_button_checks = 0

    async def run_javascript(self, account_name: str, script: str) -> Any:
        if "found: Boolean(textarea)" in script:
            self.textarea_checks += 1
            return {
                "found": self.textarea_checks >= 2,
                "value": "",
            }
        if ").found" in script or script.strip().endswith(".found"):
            return True
        if "textarea.value === ''" in script:
            return True
        if "textarea.semi-input-textarea" in script and "return Boolean(textarea)" in script:
            self.textarea_checks += 1
            if self.textarea_checks < 2:
                return json.dumps({"__doubaoBridge": True, "ok": True, "value": False})
        if "#flow-end-msg-send" in script and "return Boolean(button" in script:
            self.send_button_checks += 1
            if self.send_button_checks < 2:
                return json.dumps({"__doubaoBridge": True, "ok": True, "value": False})
        return await super().run_javascript(account_name, script)


class SilentVerifyBridge(FakeBridge):
    """豆包静默验证：verifycenter iframe 出现一次，数秒后自动消失。"""

    def __init__(self) -> None:
        super().__init__()
        self.captcha_checks = 0

    async def run_javascript(self, account_name: str, script: str) -> Any:
        self.scripts.append(script)
        if "fullscreenOverlayMatch" in script:
            self.captcha_checks += 1
            hit = self.captcha_checks == 1
            return {
                "textMatch": False,
                "iframeMatch": hit,
                "imageGridMatch": False,
                "dragHandleMatch": False,
                "fullscreenOverlayMatch": hit,
                "overlayVisible": hit,
            }
        return await super().run_javascript(account_name, script)


class PersistentWeakCaptchaBridge(FakeBridge):
    """弱信号持续存在（如 iframe/遮罩 4 秒后仍未消失），应确认为真实验证。"""

    async def run_javascript(self, account_name: str, script: str) -> Any:
        self.scripts.append(script)
        if "fullscreenOverlayMatch" in script:
            return {
                "textMatch": False,
                "iframeMatch": True,
                "imageGridMatch": False,
                "dragHandleMatch": False,
                "fullscreenOverlayMatch": True,
                "overlayVisible": True,
            }
        return await super().run_javascript(account_name, script)


@pytest.mark.asyncio
async def test_chat_follows_visible_doubao_controls(tmp_path: Path) -> None:
    bridge = FakeBridge()
    client = EmbeddedBrowserClient(
        bridge=bridge,
        user_data_dir=tmp_path,
        account_id="账号1",
    )

    await client.start()
    result = await client.chat(
        [{"role": "user", "content": "北京装修公司推荐"}],
        fresh_conversation=True,
        collect_thinking_references=True,
    )

    scripts = "\n".join(bridge.scripts)
    assert "新对话" in scripts
    assert "textarea.semi-input-textarea" in scripts
    assert "#flow-end-msg-send" in scripts
    assert bridge.focus_count == 0
    assert bridge.activation_count == 1
    assert result["text"] == "回答完成"
    assert result["thinking_references"] == []


@pytest.mark.asyncio
async def test_chat_can_finish_from_page_state_without_network_capture(
    tmp_path: Path,
) -> None:
    bridge = DomCompletionBridge()
    client = EmbeddedBrowserClient(
        bridge=bridge,
        user_data_dir=tmp_path,
        account_id="账号1",
    )

    await client.start()
    result = await client.chat(
        [{"role": "user", "content": "武汉装修公司推荐"}],
        fresh_conversation=True,
    )

    assert bridge.loading_checks >= 2
    assert result["text"] == "豆包回答完成"


@pytest.mark.asyncio
async def test_chat_waits_when_captcha_appears(tmp_path: Path) -> None:
    bridge = CaptchaBridge()
    client = EmbeddedBrowserClient(
        bridge=bridge,
        user_data_dir=tmp_path,
        account_id="账号1",
    )

    await client.start()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            client.chat(
                [{"role": "user", "content": "装修公司推荐"}],
                fresh_conversation=True,
                collect_thinking_references=True,
            ),
            timeout=2,
        )

    assert client._needs_captcha is True


@pytest.mark.asyncio
async def test_login_detection_uses_page_state_when_session_cookie_names_change(
    tmp_path: Path,
) -> None:
    bridge = QtJsonBridge()
    client = EmbeddedBrowserClient(
        bridge=bridge,
        user_data_dir=tmp_path,
        account_id="账号1",
    )

    await client.start()
    state = await client.inspect_session_state()

    assert state["logged_in"] is True
    assert state["chat_ready"] is True
    assert state["login_source"] == "page"
    assert bridge.focus_count == 0
    assert bridge.activation_count == 0


@pytest.mark.asyncio
async def test_login_detection_works_without_chat_history(tmp_path: Path) -> None:
    bridge = NewAccountBridge()
    client = EmbeddedBrowserClient(
        bridge=bridge,
        user_data_dir=tmp_path,
        account_id="新账号",
    )

    await client.start()
    state = await client.inspect_session_state()

    assert state["logged_in"] is True
    assert state["chat_ready"] is True
    assert state["login_source"] == "page"
    assert state["needs_captcha"] is False


@pytest.mark.asyncio
async def test_chat_waits_for_textarea_and_send_button(tmp_path: Path) -> None:
    bridge = DelayedReadyBridge()
    client = EmbeddedBrowserClient(
        bridge=bridge,
        user_data_dir=tmp_path,
        account_id="账号1",
    )

    await client.start()
    result = await client.chat(
        [{"role": "user", "content": "北京装修公司推荐"}],
        fresh_conversation=True,
        collect_thinking_references=True,
    )

    assert result["text"] == "回答完成"
    assert bridge.textarea_checks >= 2
    assert bridge.send_button_checks >= 2
    assert bridge.activation_count == 1


@pytest.mark.asyncio
async def test_chat_aborts_before_typing_when_captcha_overlay_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "doubao2api.embedded_browser_client.CAPTCHA_WEAK_CONFIRM_DELAY_SECONDS",
        0.1,
    )
    bridge = FullscreenCaptchaBridge()
    client = EmbeddedBrowserClient(
        bridge=bridge,
        user_data_dir=tmp_path,
        account_id="账号1",
    )

    await client.start()
    with pytest.raises(RuntimeError, match="人机验证"):
        await client.chat(
            [{"role": "user", "content": "验证码遮挡测试关键词"}],
            fresh_conversation=True,
            collect_thinking_references=True,
        )

    assert client._needs_captcha is True
    # 关键词绝不能被填进输入框：遮罩期间填写/点击只会触发更深的风控
    assert "验证码遮挡测试关键词" not in "\n".join(bridge.scripts)


@pytest.mark.asyncio
async def test_session_state_flags_visual_captcha_even_when_body_text_is_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "doubao2api.embedded_browser_client.CAPTCHA_WEAK_CONFIRM_DELAY_SECONDS",
        0.1,
    )
    bridge = VisualCaptchaStateBridge()
    client = EmbeddedBrowserClient(
        bridge=bridge,
        user_data_dir=tmp_path,
        account_id="账号1",
    )

    await client.start()
    state = await client.inspect_session_state()

    assert state["logged_in"] is True
    assert state["needs_captcha"] is True
    assert state["chat_ready"] is False
    assert state["chat_ready_reason"] == "需要处理人机验证"


@pytest.mark.asyncio
async def test_send_failure_escalates_to_captcha_error_when_overlay_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "doubao2api.embedded_browser_client.SEND_BUTTON_READY_TIMEOUT_SECONDS",
        0.3,
    )
    monkeypatch.setattr(
        "doubao2api.embedded_browser_client.CAPTCHA_WEAK_CONFIRM_DELAY_SECONDS",
        0.1,
    )
    bridge = SendFailCaptchaBridge()
    client = EmbeddedBrowserClient(
        bridge=bridge,
        user_data_dir=tmp_path,
        account_id="账号1",
    )

    await client.start()
    with pytest.raises(RuntimeError, match="发送失败：检测到人机验证"):
        await client.chat(
            [{"role": "user", "content": "发送失败测试关键词"}],
            fresh_conversation=True,
        )

    assert client._needs_captcha is True
    # 错误信息带“验证”字样，调度器才能按风控流程暂停账号而不是普通重试
    assert bridge.captcha_checks >= 2


@pytest.mark.asyncio
async def test_visual_captcha_hit_logs_evidence(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """命中时必须输出信号值与证据字段，便于从日志区分真实验证与误报。"""

    monkeypatch.setattr(
        "doubao2api.embedded_browser_client.CAPTCHA_WEAK_CONFIRM_DELAY_SECONDS",
        0.1,
    )
    bridge = FullscreenCaptchaBridge()
    client = EmbeddedBrowserClient(
        bridge=bridge,
        user_data_dir=tmp_path,
        account_id="账号1",
    )

    await client.start()
    with caplog.at_level("WARNING", logger="doubao2api.embedded_browser_client"):
        assert await client._has_visual_captcha() is True

    assert "fullscreenOverlayMatch=True" in caplog.text
    assert "matchedIframeSrcs" in caplog.text
    assert "overlayInfo" in caplog.text


@pytest.mark.asyncio
async def test_chat_retypes_and_retries_when_send_unconfirmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第一轮发送未确认（React 状态丢失）时应重填词重试，而不是直接判失败。"""

    monkeypatch.setattr(
        "doubao2api.embedded_browser_client.SEND_BUTTON_READY_TIMEOUT_SECONDS",
        0.5,
    )
    bridge = FlakySendBridge()
    client = EmbeddedBrowserClient(
        bridge=bridge,
        user_data_dir=tmp_path,
        account_id="账号1",
    )

    await client.start()
    result = await client.chat(
        [{"role": "user", "content": "重填重试测试关键词"}],
        fresh_conversation=True,
        collect_thinking_references=True,
    )

    assert result["text"] == "回答完成"
    assert bridge.type_count == 2


@pytest.mark.asyncio
async def test_weak_captcha_signal_auto_disappears_does_not_flag_captcha(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """仅 iframe/全屏遮罩的弱信号若数秒内消失，应判定为静默验证，不暂停账号。"""

    monkeypatch.setattr(
        "doubao2api.embedded_browser_client.CAPTCHA_WEAK_CONFIRM_DELAY_SECONDS",
        0.1,
    )
    bridge = SilentVerifyBridge()
    client = EmbeddedBrowserClient(
        bridge=bridge,
        user_data_dir=tmp_path,
        account_id="账号1",
    )

    await client.start()
    with caplog.at_level("INFO", logger="doubao2api.embedded_browser_client"):
        assert await client._has_visual_captcha() is False

    assert bridge.captcha_checks == 2
    assert "弱人机验证信号已自动消失" in caplog.text


@pytest.mark.asyncio
async def test_persistent_weak_captcha_signal_confirms_captcha(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """弱信号持续存在（复查仍命中），应最终确认为真实人机验证。"""

    monkeypatch.setattr(
        "doubao2api.embedded_browser_client.CAPTCHA_WEAK_CONFIRM_DELAY_SECONDS",
        0.1,
    )
    bridge = PersistentWeakCaptchaBridge()
    client = EmbeddedBrowserClient(
        bridge=bridge,
        user_data_dir=tmp_path,
        account_id="账号1",
    )

    await client.start()
    with caplog.at_level("WARNING", logger="doubao2api.embedded_browser_client"):
        assert await client._has_visual_captcha() is True

    assert "fullscreenOverlayMatch=True" in caplog.text


def test_captcha_detect_script_filters_zero_opacity_nodes() -> None:
    """验证脚本包含 opacity 门槛，避免把预加载的透明验证容器/iframe 误判。"""

    from doubao2api.embedded_browser_client import build_captcha_detect_script
    from doubao2api.platforms import get_platform

    script = build_captcha_detect_script(get_platform("doubao"))
    assert "parseFloat(style.opacity)" in script
    assert "opacity >= 0.05" in script
