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


class DelayedReadyBridge(FakeBridge):
    """Simulates a page where textarea and send button become ready after checks."""

    def __init__(self) -> None:
        super().__init__()
        self.textarea_checks = 0
        self.send_button_checks = 0

    async def run_javascript(self, account_name: str, script: str) -> Any:
        if "textarea.semi-input-textarea" in script and "return Boolean(textarea)" in script:
            self.textarea_checks += 1
            if self.textarea_checks < 2:
                return json.dumps({"__doubaoBridge": True, "ok": True, "value": False})
        if "#flow-end-msg-send" in script and "return Boolean(button" in script:
            self.send_button_checks += 1
            if self.send_button_checks < 2:
                return json.dumps({"__doubaoBridge": True, "ok": True, "value": False})
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
