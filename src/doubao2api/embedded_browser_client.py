from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from .browser_client import (
    BrowserUnavailableError,
    LoginRequiredError,
    ReferenceExpansionError,
)
from .cookie_utils import parse_cookie_records
from .research_platforms import category_for_url, platform_for_url, to_js_platform_data
from .selectors import (
    CAPTCHA_DOM_SELECTORS,
    CAPTCHA_IFRAME_PATTERNS,
    REFERENCE_SUMMARY_PATTERN,
    SELECTORS,
    js_regex_alternation,
    js_regex_pattern,
    js_selector_list,
    js_string,
)
from .text_utils import _collect_text, _merge_text_fragments, _text_from_content

CHAT_URL = "https://www.doubao.com/chat/"
SESSION_COOKIE_NAMES = {"sessionid", "sessionid_ss"}

RESPONSE_POLL_INTERVAL_SECONDS = 0.5
REFERENCE_POLL_INTERVAL_SECONDS = 0.3
SEND_BUTTON_READY_TIMEOUT_SECONDS = 8.0
REFERENCE_APPEAR_TIMEOUT_SECONDS = 10.0
NEW_CONVERSATION_READY_TIMEOUT_SECONDS = 8.0
PAGE_HEALTH_PING_TIMEOUT_SECONDS = 5.0
MAX_SCRIPT_TIMEOUT_STREAK = 2
CAPTCHA_STALL_SECONDS = 30.0
CAPTCHA_MAX_WAIT_SECONDS = 600.0


CAPTURE_SCRIPT = r"""
(() => {
  window.__doubaoEmbeddedCapture = {
    events: [],
    done: false,
    error: null
  };
  if (window.__doubaoEmbeddedCaptureInstalled) return true;
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const request = args[0];
    const url = typeof request === 'string' ? request : (request && request.url) || '';
    const response = await originalFetch(...args);
    const capture = window.__doubaoEmbeddedCapture;
    if (capture && url.includes('/chat/completion') && response.body) {
      const cloned = response.clone();
      (async () => {
        try {
          const reader = cloned.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            for (const line of lines) {
              const trimmed = line.trim();
              if (!trimmed.startsWith('data:')) continue;
              const raw = trimmed.slice(5).trim();
              if (!raw || raw === '[DONE]') continue;
              try { capture.events.push(JSON.parse(raw)); }
              catch (_) { capture.events.push({ text: raw }); }
            }
          }
          capture.done = true;
        } catch (error) {
          capture.error = String(error);
          capture.done = true;
        }
      })();
    }
    return response;
  };
  window.__doubaoEmbeddedCaptureInstalled = true;
  return true;
})()
"""

REFERENCE_ROWS_SCRIPT_TEMPLATE = r"""
(() => {
  const tidy = value => (value || '').replace(/\n{2,}/g, ' ').trim();
  const absolute = value => {
    try { return new URL(value, location.href).href; }
    catch (_) { return ''; }
  };
  const platformData = __PLATFORM_DATA__;
  return [...document.querySelectorAll(__REFERENCE_ROWS__)].map(anchor => {
    const label = anchor.querySelector(__REFERENCE_TITLE__) || anchor;
    let title = tidy(label.textContent || label.innerText || '')
      .replace(/^[0-9]+[.、]\s*/, '');
    const sourceNode = anchor.querySelector(__REFERENCE_SOURCE__);
    let platform = tidy(
      sourceNode ? (sourceNode.textContent || sourceNode.innerText || '') : ''
    );
    let platformType = '';
    if (!platform) {
      const sourceMatch = title.match(
        /\s[-–—·｜|]\s*([^-–—·｜|\n]{2,40})\s*$/
      );
      platform = sourceMatch ? tidy(sourceMatch[1]) : '';
      if (sourceMatch && typeof sourceMatch.index === 'number') {
        title = tidy(title.slice(0, sourceMatch.index));
      }
    }
    try {
      const hostname = new URL(anchor.href, location.href).hostname
        .replace(/^www\./, '');
      const owner = Object.entries(platformData).find(([domain]) =>
        hostname === domain || hostname.endsWith(`.${domain}`)
      );
      if (owner) {
        platform = owner[1][0];
        platformType = owner[1][1];
      } else {
        platform = platform || hostname;
      }
    } catch (_) {
      platform = platform || '';
    }
    return {
      title,
      platform,
      platformType,
      link: absolute(anchor.getAttribute('href') || '')
    };
  }).filter(item => item.title && item.link);
})()
"""


def build_reference_rows_script() -> str:
    return (
        REFERENCE_ROWS_SCRIPT_TEMPLATE.replace(
            "__REFERENCE_ROWS__", js_string(", ".join(SELECTORS["reference_rows"]))
        )
        .replace("__REFERENCE_TITLE__", js_string(SELECTORS["reference_title"]))
        .replace("__REFERENCE_SOURCE__", js_string(SELECTORS["reference_source"]))
        .replace("__PLATFORM_DATA__", to_js_platform_data())
    )


CAPTCHA_DETECT_SCRIPT_TEMPLATE = r"""
(() => {
  const tidy = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = node => {
    if (!node) return false;
    const style = getComputedStyle(node);
    const box = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden'
      && box.width > 0 && box.height > 0;
  };
  const bodyText = document.body ? (document.body.innerText || '') : '';
  const textPattern = new RegExp(__CAPTCHA_TEXT_PATTERN__);
  const textMatch = textPattern.test(bodyText);
  const iframePatterns = __CAPTCHA_IFRAME_PATTERNS__;
  const iframeMatch = [...document.querySelectorAll('iframe')].some(iframe => {
    const src = (iframe.src || iframe.getAttribute('src') || '').toLowerCase();
    return iframePatterns.some(pattern => src.includes(pattern));
  });
  const captchaSelectors = __CAPTCHA_DOM_SELECTORS__;
  const overlayNodes = captchaSelectors
    .flatMap(selector => [...document.querySelectorAll(selector)])
    .filter(visible);
  let imageGridMatch = false;
  let dragHandleMatch = false;
  for (const node of overlayNodes) {
    const imgs = [...node.querySelectorAll('img')].filter(visible);
    if (imgs.length >= 6) {
      imageGridMatch = true;
    }
    const text = tidy(node.innerText || '');
    if (/拖动|拖拽|滑动|滑块/.test(text)) {
      dragHandleMatch = true;
    }
  }
  if (!imageGridMatch) {
    const visibleImgs = [...document.querySelectorAll('img')].filter(visible);
    const parentCounts = new Map();
    for (const img of visibleImgs) {
      let parent = img.parentElement;
      while (parent && parent !== document.body) {
        parentCounts.set(parent, (parentCounts.get(parent) || 0) + 1);
        parent = parent.parentElement;
      }
    }
    for (const [parent, count] of parentCounts) {
      if (count >= 6 && visible(parent)) {
        imageGridMatch = true;
        break;
      }
    }
  }
  return {
    textMatch,
    iframeMatch,
    imageGridMatch,
    dragHandleMatch,
    overlayVisible: overlayNodes.length > 0
  };
})()
"""


def build_captcha_detect_script() -> str:
    return (
        CAPTCHA_DETECT_SCRIPT_TEMPLATE.replace(
            "__CAPTCHA_TEXT_PATTERN__", js_regex_alternation(SELECTORS["captcha_patterns"])
        )
        .replace("__CAPTCHA_IFRAME_PATTERNS__", js_selector_list(CAPTCHA_IFRAME_PATTERNS))
        .replace("__CAPTCHA_DOM_SELECTORS__", js_selector_list(CAPTCHA_DOM_SELECTORS))
    )


REFERENCE_GENERIC_SCRIPT_TEMPLATE = r"""
(() => {
  const tidy = value => (value || '').replace(/\n{2,}/g, ' ').trim();
  const absolute = value => {
    try { return new URL(value, location.href).href; }
    catch (_) { return ''; }
  };
  const ignoredHosts = new Set([
    'www.doubao.com', 'doubao.com', 'lf-flow-web-cdn.doubao.com'
  ]);
  const summaryPattern = new RegExp(__REFERENCE_SUMMARY_PATTERN__);
  const bodyText = document.body ? (document.body.innerText || '') : '';
  if (!summaryPattern.test(bodyText)) return [];
  const candidates = [...document.querySelectorAll('*')].filter(node => {
    const box = node.getBoundingClientRect();
    return box.width > 0 && box.height > 0
      && summaryPattern.test((node.innerText || '').trim());
  });
  if (!candidates.length) return [];
  candidates.sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width);
  let container = candidates[0];
  let anchors = [];
  for (let i = 0; i < 6; i++) {
    anchors = [...container.querySelectorAll('a[href^="http"]')].filter(a => {
      try {
        const host = new URL(a.href, location.href).hostname.replace(/^www\./, '');
        return !ignoredHosts.has(host);
      } catch (_) { return false; }
    });
    if (
      anchors.length
      || !container.parentElement
      || container.parentElement === document.body
    ) break;
    container = container.parentElement;
  }
  const seen = new Set();
  return anchors.map(a => {
    const link = absolute(a.getAttribute('href') || '');
    let title = tidy(a.textContent || a.innerText || '');
    if (!title) {
      const img = a.querySelector('img');
      title = img ? tidy(img.alt || img.title || '') : '';
    }
    if (!title || !link || seen.has(link)) return null;
    seen.add(link);
    return { title, link };
  }).filter(Boolean);
})()
"""

REFERENCE_GENERIC_SCRIPT = REFERENCE_GENERIC_SCRIPT_TEMPLATE.replace(
    "__REFERENCE_SUMMARY_PATTERN__", js_regex_pattern(REFERENCE_SUMMARY_PATTERN)
)

LOGIN_STATE_SCRIPT_TEMPLATE = r"""
(() => {
  const visible = node => {
    const style = getComputedStyle(node);
    const box = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden'
      && box.width > 0 && box.height > 0;
  };
  const compact = value => (value || '').replace(/\s+/g, ' ').trim();
  const roles = __LOGIN_ROLES__;
  const controls = [...document.querySelectorAll(roles.join(','))].filter(visible);
  const textPatterns = __LOGIN_TEXT_PATTERNS__;
  const ariaPatterns = __LOGIN_ARIA_PATTERNS__;
  const hasLoginControl = controls.some(node => {
    const text = compact(node.innerText || node.textContent);
    const aria = compact(
      (node.getAttribute('aria-label') || '') + ' '
      + (node.getAttribute('title') || '')
    );
    return textPatterns.some(pattern => new RegExp(pattern).test(text))
      || ariaPatterns.some(pattern => new RegExp(pattern, 'i').test(aria));
  });
  const newChatText = __NEW_CHAT_TEXT__;
  const hasNewChat = controls.some(node =>
    compact(node.innerText || node.textContent) === newChatText
  );
  const body = document.body ? (document.body.innerText || '') : '';
  const captchaPattern = __CAPTCHA_PATTERN__;
  const hasCaptcha = new RegExp(captchaPattern).test(body);
  const historyText = __HISTORY_TEXT__;
  const historyLinkSelector = __HISTORY_LINK_SELECTOR__;
  const historyMinLinks = __HISTORY_MIN_LINKS__;
  const hasHistory = body.includes(historyText)
    && document.querySelectorAll(historyLinkSelector).length >= historyMinLinks;
  const composerSelectors = __COMPOSER_SELECTORS__;
  const hasComposer = composerSelectors.some(selector =>
    [...document.querySelectorAll(selector)].some(visible)
  );
  return {
    ready: document.readyState !== 'loading',
    hasLoginControl,
    hasNewChat,
    hasHistory,
    hasComposer,
    hasCaptcha,
    loggedIn: !hasLoginControl && hasComposer && (hasNewChat || hasHistory)
  };
})()
"""

LOGIN_STATE_SCRIPT = (
    LOGIN_STATE_SCRIPT_TEMPLATE.replace(
        "__LOGIN_ROLES__", js_selector_list(SELECTORS["new_chat"]["roles"])
    )
    .replace(
        "__LOGIN_TEXT_PATTERNS__",
        js_selector_list(SELECTORS["login_controls"]["text_patterns"]),
    )
    .replace(
        "__LOGIN_ARIA_PATTERNS__",
        js_selector_list(SELECTORS["login_controls"]["aria_patterns"]),
    )
    .replace("__NEW_CHAT_TEXT__", js_string(SELECTORS["new_chat"]["text"]))
    .replace("__CAPTCHA_PATTERN__", js_regex_alternation(SELECTORS["captcha_patterns"]))
    .replace("__HISTORY_TEXT__", js_string(SELECTORS["history_indicator"]["text"]))
    .replace(
        "__HISTORY_LINK_SELECTOR__",
        js_string(SELECTORS["history_indicator"]["link_selector"]),
    )
    .replace("__HISTORY_MIN_LINKS__", str(SELECTORS["history_indicator"]["min_links"]))
    .replace("__COMPOSER_SELECTORS__", js_selector_list(SELECTORS["composer"]))
)


class EmbeddedBrowserClient:
    """Browser client backed by an in-application Qt WebEngine tab."""

    def __init__(self, bridge: Any, user_data_dir: Path, account_id: str) -> None:
        self.bridge = bridge
        self.user_data_dir = user_data_dir.resolve()
        self.account_id = account_id
        self._started = False
        self._chat_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._started_at = 0.0
        self._needs_captcha = False
        self._last_error_code = 0
        self._consecutive_failures = 0
        self._script_timeout_streak = 0

    @property
    def started(self) -> bool:
        return self._started

    @property
    def startup_age_seconds(self) -> float:
        if not self._started:
            return 0.0
        return max(0.0, time.monotonic() - self._started_at)

    async def start(self) -> None:
        if self._started:
            await self.bring_to_front()
            return
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        await self.bridge.open_account(
            self.account_id,
            self.user_data_dir,
            CHAT_URL,
        )
        self._started = True
        self._started_at = time.monotonic()

    async def stop(self) -> None:
        if self._started:
            await self.bridge.close_account(self.account_id)
        self._started = False

    async def bring_to_front(self) -> None:
        if not self._started:
            raise BrowserUnavailableError("内置浏览器标签页尚未打开")
        await self.bridge.focus_account(self.account_id)

    async def set_tab_visible(self, visible: bool) -> None:
        if not self._started:
            raise BrowserUnavailableError("内置浏览器标签页尚未打开")
        await self.bridge.set_tab_visible(self.account_id, visible)

    async def _activate_for_automation(self) -> None:
        if not self._started:
            raise BrowserUnavailableError("内置浏览器标签页尚未打开")
        activate = getattr(self.bridge, "activate_account", self.bridge.focus_account)
        await activate(self.account_id)

    async def cookies(self) -> list[dict[str, Any]]:
        if not self._started:
            return []
        return await self.bridge.cookies(self.account_id)

    async def _run_script(self, script: str) -> Any:
        """Run page JavaScript through a JSON envelope.

        Qt WebEngine 6.11 can turn JavaScript objects and arrays into an empty
        string in its callback. Returning JSON text keeps the value stable
        across Qt versions and is also convenient for test bridges.
        """

        wrapped = (
            "(() => {\n"
            "  try {\n"
            "    const value = (\n" + script + "\n    );\n"
            "    return JSON.stringify({"
            '"__doubaoBridge":true,"ok":true,"value":value'
            "});\n"
            "  } catch (error) {\n"
            "    return JSON.stringify({"
            '"__doubaoBridge":true,"ok":false,'
            '"error":String(error && (error.stack || error.message) || error)'
            "});\n"
            "  }\n"
            "})()"
        )
        raw = await self.bridge.run_javascript(self.account_id, wrapped)
        if not isinstance(raw, str):
            return raw
        try:
            decoded = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return raw
        if not isinstance(decoded, dict) or not decoded.get("__doubaoBridge"):
            return decoded
        if not decoded.get("ok"):
            raise RuntimeError(f"豆包页面操作失败：{decoded.get('error', '未知错误')}")
        return decoded.get("value")

    async def _ping_page(self, timeout: float = PAGE_HEALTH_PING_TIMEOUT_SECONDS) -> bool:
        """Quickly check whether the page JavaScript loop is still responsive."""

        try:
            result = await asyncio.wait_for(self._run_script("true"), timeout=timeout)
            return bool(result)
        except Exception:
            return False

    async def _ensure_new_conversation(self) -> None:
        """Open a fresh empty conversation before typing the next keyword.

        The most reliable way is to navigate to the base chat URL: this preserves
        the login session and loads a brand-new empty conversation. After the page
        loads we also try to click the sidebar "新对话" button, in case the
        navigated page restored an existing conversation. If neither produces a
        blank composer, we raise so the caller never types into a stale dialog.
        """

        new_conversation_script = r"""
            (() => {
              const visible = node => {
                const style = getComputedStyle(node);
                const box = node.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && box.width > 0 && box.height > 0;
              };
              const roles = __NEW_CHAT_ROLES__;
              const label = [...document.querySelectorAll(roles.join(','))]
                .find(node => visible(node)
                  && (node.textContent || '').trim() === __NEW_CHAT_TEXT__);
              if (!label) return false;
              const target = label.closest('[class*="sidebar_nav_item"]')
                || label.closest('[class*="nav-link-"]')
                || label.closest('button,[role="button"],a')
                || label.parentElement
                || label;
              target.scrollIntoView({ block: 'center' });
              const rect = target.getBoundingClientRect();
              const x = rect.left + rect.width / 2;
              const y = rect.top + rect.height / 2;
              const opts = {
                bubbles: true, cancelable: true, view: window,
                clientX: x, clientY: y,
              };
              target.dispatchEvent(new PointerEvent('pointerdown', opts));
              target.dispatchEvent(new MouseEvent('mousedown', opts));
              target.dispatchEvent(new PointerEvent('pointerup', opts));
              target.dispatchEvent(new MouseEvent('mouseup', opts));
              target.dispatchEvent(new MouseEvent('click', opts));
              target.click();
              return true;
            })()
            """.replace(
            "__NEW_CHAT_ROLES__",
            js_selector_list(SELECTORS["new_chat"]["roles"]),
        ).replace(
            "__NEW_CHAT_TEXT__",
            js_string(SELECTORS["new_chat"]["text"]),
        )
        textarea_state_script = r"""
            (() => {
              const visible = node => {
                const box = node.getBoundingClientRect();
                return box.width > 0 && box.height > 0;
              };
              const selectors = __COMPOSER_SELECTORS__;
              const textarea = selectors.flatMap(selector =>
                [...document.querySelectorAll(selector)]
              ).find(node => visible(node));
              return {
                found: Boolean(textarea),
                value: textarea ? (textarea.value || '') : ''
              };
            })()
            """.replace("__COMPOSER_SELECTORS__", js_selector_list(SELECTORS["composer"]))

        async def has_fresh_composer() -> bool:
            state = await self._run_script(textarea_state_script)
            return isinstance(state, dict) and state.get("value") == ""

        # Primary method: reload the base chat URL. This always gives a clean page.
        await self.bridge.navigate(self.account_id, CHAT_URL)
        ready = await self._wait_for_condition(
            f"({textarea_state_script}).found",
            timeout=NEW_CONVERSATION_READY_TIMEOUT_SECONDS,
            interval=0.1,
        )
        if ready and await has_fresh_composer():
            return

        # Secondary method: if the navigated page restored an existing conversation,
        # explicitly click the "新对话" button.
        clicked = await self._run_script(new_conversation_script)
        if clicked:
            ready = await self._wait_for_condition(
                f"({textarea_state_script}).found",
                timeout=NEW_CONVERSATION_READY_TIMEOUT_SECONDS,
                interval=0.1,
            )
            if ready and await has_fresh_composer():
                return

        raise RuntimeError("未能成功切换到新的空白对话")

    async def _type_prompt(self, prompt: str) -> None:
        """Fill the composer textarea and trigger React input events."""

        type_script = r"""
            (() => {
              const visible = node => {
                const box = node.getBoundingClientRect();
                return box.width > 0 && box.height > 0;
              };
              const selectors = __COMPOSER_SELECTORS__;
              const textarea = selectors.flatMap(selector =>
                [...document.querySelectorAll(selector)]
              ).find(node => visible(node));
              if (!textarea) throw new Error('No chat textarea found');
              const setter = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype, 'value'
              ).set;
              const text = __PROMPT__;
              setter.call(textarea, text);
              textarea.focus();
              textarea.selectionStart = textarea.selectionEnd = text.length;
              [
                new FocusEvent('focus', { bubbles: true }),
                new KeyboardEvent('keydown', { key: text, bubbles: true, cancelable: true }),
                new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }),
                new Event('change', { bubbles: true }),
                new KeyboardEvent('keyup', { key: text, bubbles: true, cancelable: true }),
              ].forEach(event => textarea.dispatchEvent(event));
              return true;
            })()
            """.replace("__COMPOSER_SELECTORS__", js_selector_list(SELECTORS["composer"])).replace(
            "__PROMPT__", json.dumps(prompt, ensure_ascii=False)
        )
        await self._run_script(type_script)

    async def _submit_prompt(self, prompt: str) -> None:
        """Click the send button if ready; otherwise fall back to pressing Enter."""

        send_button_selectors = js_selector_list(SELECTORS["send_button"])
        send_ready_script = r"""
            (() => {
              const selectors = __SEND_BUTTON_SELECTORS__;
              const button = selectors
                .map(selector => document.querySelector(selector))
                .find(node => node && !node.disabled
                  && node.getAttribute('aria-disabled') !== 'true');
              return Boolean(button);
            })()
            """.replace("__SEND_BUTTON_SELECTORS__", send_button_selectors)
        click_send_script = r"""
            (() => {
              const selectors = __SEND_BUTTON_SELECTORS__;
              const button = selectors
                .map(selector => document.querySelector(selector))
                .find(node => node);
              if (!button) return false;
              button.click();
              return true;
            })()
            """.replace("__SEND_BUTTON_SELECTORS__", send_button_selectors)
        composer_selectors = js_selector_list(SELECTORS["composer"])
        textarea_empty_script = r"""
            (() => {
              const visible = node => {
                const box = node.getBoundingClientRect();
                return box.width > 0 && box.height > 0;
              };
              const selectors = __COMPOSER_SELECTORS__;
              const textarea = selectors.flatMap(selector =>
                [...document.querySelectorAll(selector)]
              ).find(node => visible(node));
              return textarea ? textarea.value === '' : false;
            })()
            """.replace("__COMPOSER_SELECTORS__", composer_selectors)

        async def try_click_send() -> bool:
            ready = await self._wait_for_condition(
                send_ready_script,
                timeout=SEND_BUTTON_READY_TIMEOUT_SECONDS,
                interval=0.1,
            )
            if not ready:
                return False
            sent = await self._run_script(click_send_script)
            if not sent:
                return False
            return await self._wait_for_condition(
                textarea_empty_script,
                timeout=2.0,
                interval=0.1,
            )

        async def try_press_enter() -> bool:
            enter_script = r"""
                (() => {
                  const visible = node => {
                    const box = node.getBoundingClientRect();
                    return box.width > 0 && box.height > 0;
                  };
                  const selectors = __COMPOSER_SELECTORS__;
                  const textarea = selectors.flatMap(selector =>
                    [...document.querySelectorAll(selector)]
                  ).find(node => visible(node));
                  if (!textarea) return false;
                  textarea.focus();
                  [
                    new KeyboardEvent('keydown', {
                      key: 'Enter', code: 'Enter', bubbles: true, cancelable: true,
                    }),
                    new KeyboardEvent('keypress', {
                      key: 'Enter', code: 'Enter', bubbles: true, cancelable: true,
                    }),
                    new KeyboardEvent('keyup', {
                      key: 'Enter', code: 'Enter', bubbles: true, cancelable: true,
                    }),
                  ].forEach(event => textarea.dispatchEvent(event));
                  return true;
                })()
                """.replace("__COMPOSER_SELECTORS__", composer_selectors)
            sent = await self._run_script(enter_script)
            if not sent:
                return False
            return await self._wait_for_condition(
                textarea_empty_script,
                timeout=3.0,
                interval=0.1,
            )

        if await try_click_send():
            return
        if await try_press_enter():
            return
        raise RuntimeError("豆包发送按钮尚未就绪，关键词没有发送")

    def _mark_needs_captcha(self, reason: str = "") -> None:
        """Mark the account as needing manual captcha resolution and reset counters."""

        self._needs_captcha = True
        self._script_timeout_streak = 0
        if reason:
            self._last_error_code = 1

    async def _run_script_or_track_timeout(self, script: str) -> Any:
        """Run a script and treat repeated timeouts as a captcha/unresponsive page."""

        try:
            result = await self._run_script(script)
        except Exception as exc:
            error_text = str(exc).casefold()
            if "超时" in error_text or "timeout" in error_text:
                self._script_timeout_streak += 1
                if self._script_timeout_streak >= MAX_SCRIPT_TIMEOUT_STREAK:
                    self._mark_needs_captcha("页面连续无响应")
                    raise RuntimeError("页面连续无响应，疑似需要验证码，请人工处理") from exc
            raise
        self._script_timeout_streak = 0
        return result

    async def _wait_for_condition(
        self,
        predicate_script: str,
        timeout: float,
        interval: float = 0.1,
    ) -> bool:
        """Poll a JavaScript predicate until it returns a truthy value."""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = await self._run_script(predicate_script)
            if result:
                return True
            await asyncio.sleep(interval)
        return False

    async def _detect_captcha(self) -> dict[str, Any]:
        """Run visual/structural captcha detection in addition to body-text scan."""

        try:
            result = await self._run_script(build_captcha_detect_script())
        except Exception:
            return {}
        if isinstance(result, dict):
            return result
        return {}

    async def _has_visual_captcha(self) -> bool:
        detected = await self._detect_captcha()
        return any(
            detected.get(key)
            for key in ("textMatch", "iframeMatch", "imageGridMatch", "dragHandleMatch")
        )

    async def _debug_snapshot(self) -> dict[str, Any]:
        """Capture a lightweight snapshot of the current page for debugging."""

        reference_selector = js_string(", ".join(SELECTORS["reference_rows"]))
        expand_selector = js_string(
            ", ".join(s for s in SELECTORS["reference_expand"] if not s.startswith("xpath="))
        )
        more_text = js_string(SELECTORS["reference_more_text"])
        send_selector = js_string(", ".join(SELECTORS["send_button"]))
        composer_selector = js_string(", ".join(SELECTORS["composer"]))
        summary_pattern = js_regex_pattern(REFERENCE_SUMMARY_PATTERN)
        script = (
            r"""
            (() => {
              const bodyText = document.body ? (document.body.innerText || '') : '';
              const summaryRe = new RegExp(__SUMMARY_PATTERN__);
              const hasSummary = summaryRe.test(bodyText);
              const visible = node => {
                const box = node.getBoundingClientRect();
                return box.width > 0 && box.height > 0;
              };
              const snapshot = {
                url: location.href,
                bodyLength: bodyText.length,
                bodyPreview: bodyText.slice(0, 1000),
                bodyTail: bodyText.slice(-1500),
                hasSummary,
                summaryMatches: [...document.querySelectorAll('*')].filter(node => {
                  return visible(node) && summaryRe.test((node.innerText || '').trim());
                }).slice(0, 5).map(node => ({
                  tag: node.tagName,
                  className: node.getAttribute('class') || '',
                  text: (node.innerText || '').trim().slice(0, 200)
                })),
                referenceRows: document.querySelectorAll(__REFERENCE_SELECTOR__).length,
                expandButtons: document.querySelectorAll(__EXPAND_SELECTOR__).length,
                moreButtons: [...document.querySelectorAll('button,div,span')].filter(node => {
                  const text = (node.innerText || '').trim();
                  return text === __MORE_TEXT__;
                }).length,
                sendButtons: document.querySelectorAll(__SEND_SELECTOR__).length,
                composerTextareas: document.querySelectorAll(__COMPOSER_SELECTOR__).length,
                candidateAnchors: [...document.querySelectorAll('a[href^="http"]')].filter(node => {
                  return visible(node);
                }).slice(0, 10).map(node => ({
                  href: node.href,
                  text: (node.innerText || '').trim().slice(0, 120),
                  className: node.getAttribute('class') || ''
                })),
                sampleEvent: (() => {
                  const capture = window.__doubaoEmbeddedCapture || {};
                  const events = capture.events || [];
                  const event = events[0] || events[events.length - 1] || null;
                  if (!event) return null;
                  return JSON.stringify(event).slice(0, 2000);
                })()
              };
              return snapshot;
            })()
            """.replace("__SUMMARY_PATTERN__", summary_pattern)
            .replace("__REFERENCE_SELECTOR__", reference_selector)
            .replace("__EXPAND_SELECTOR__", expand_selector)
            .replace("__MORE_TEXT__", more_text)
            .replace("__SEND_SELECTOR__", send_selector)
            .replace("__COMPOSER_SELECTOR__", composer_selector)
        )
        try:
            return await self._run_script(script)
        except Exception as exc:
            return {"error": str(exc)}

    async def inspect_session_state(self) -> dict[str, Any]:
        async with self._state_lock:
            state = await self.bridge.state(self.account_id) if self._started else {"page_url": ""}
            if self._started and not state.get("load_finished", True):
                return {
                    "account_id": self.account_id,
                    "started": True,
                    "logged_in": False,
                    "browser": "loading",
                    "has_ms_token": False,
                    "chat_ready": False,
                    "needs_captcha": self._needs_captcha,
                    "last_error_code": self._last_error_code,
                    "consecutive_failures": self._consecutive_failures,
                    "page_url": state.get("page_url", ""),
                    "login_source": "",
                    "uptime_seconds": int(self.startup_age_seconds),
                }
            cookies = await self.cookies()
            cookie_names = {str(item.get("name", "")) for item in cookies}
            if self._started and not await self._ping_page():
                self._mark_needs_captcha("页面 JavaScript 无响应")
                return {
                    "account_id": self.account_id,
                    "started": True,
                    "logged_in": False,
                    "browser": "unresponsive",
                    "has_ms_token": "msToken" in cookie_names,
                    "chat_ready": False,
                    "needs_captcha": True,
                    "last_error_code": self._last_error_code,
                    "consecutive_failures": self._consecutive_failures,
                    "page_url": state.get("page_url", ""),
                    "login_source": "",
                    "uptime_seconds": int(self.startup_age_seconds),
                }
            page_login = await self._run_script(LOGIN_STATE_SCRIPT) if self._started else {}
            dom_logged_in = (
                bool(page_login.get("loggedIn")) if isinstance(page_login, dict) else False
            )
            if isinstance(page_login, dict):
                self._needs_captcha = bool(page_login.get("hasCaptcha"))
            page_ready = (
                bool(page_login.get("ready") and page_login.get("hasComposer"))
                if isinstance(page_login, dict)
                else False
            )
            logged_in = bool(cookie_names & SESSION_COOKIE_NAMES) or dom_logged_in
            return {
                "account_id": self.account_id,
                "started": self._started,
                "logged_in": logged_in,
                "browser": "ready" if self._started else "not_started",
                "has_ms_token": "msToken" in cookie_names,
                "chat_ready": (
                    self._started and logged_in and page_ready and not self._needs_captcha
                ),
                "needs_captcha": self._needs_captcha,
                "last_error_code": self._last_error_code,
                "consecutive_failures": self._consecutive_failures,
                "page_url": state.get("page_url", ""),
                "login_source": (
                    "cookie"
                    if cookie_names & SESSION_COOKIE_NAMES
                    else ("page" if dom_logged_in else "")
                ),
                "uptime_seconds": (
                    int(time.monotonic() - self._started_at) if self._started else 0
                ),
            }

    async def reset_captcha(self) -> None:
        self._needs_captcha = False
        self._last_error_code = 0
        self._consecutive_failures = 0
        self._script_timeout_streak = 0

    async def screenshot(self) -> bytes:
        if not self._started:
            raise BrowserUnavailableError("内置浏览器标签页尚未打开")
        return await self.bridge.screenshot(self.account_id)

    async def import_cookies(self, cookie_text: str) -> int:
        if not self._started:
            await self.start()
        records = parse_cookie_records(cookie_text)
        if records:
            await self.bridge.set_cookies(self.account_id, records)
            await self.bridge.navigate(self.account_id, CHAT_URL)
        return len(records)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        timeout: float = 180,
        collect_thinking_references: bool = False,
        fresh_conversation: bool = False,
        reference_callback: Callable[[dict[str, str]], Any] | None = None,
    ) -> dict[str, Any]:
        if not self._started:
            await self.start()
        state = await self.inspect_session_state()
        if not state["logged_in"]:
            raise LoginRequiredError("账号尚未登录，请先在软件内的账号标签页完成登录")
        prompt = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                prompt = _text_from_content(message.get("content"))
                break
        if not prompt:
            raise ValueError("提问内容不能为空")

        async with self._chat_lock:
            try:
                # Keep the account page active without taking the management
                # dashboard away from the user.
                await self._activate_for_automation()
                if fresh_conversation:
                    await self._ensure_new_conversation()
                await self._run_script(CAPTURE_SCRIPT)
                await self._type_prompt(prompt)
                await self._submit_prompt(prompt)
                self._script_timeout_streak = 0
                if not await self._ping_page():
                    self._mark_needs_captcha("页面 JavaScript 无响应")
                    raise RuntimeError("页面无响应，疑似需要验证码，请人工处理")
                deadline = time.monotonic() + timeout
                captcha_hard_deadline: float | None = None
                last_event_len = 0
                last_progress_at = time.monotonic()
                capture: dict[str, Any] = {}
                response_completed = False
                saw_loading = False
                answer_finished_at: float | None = None
                send_button_selectors = js_selector_list(SELECTORS["send_button"])
                reference_summary_pattern = js_regex_pattern(REFERENCE_SUMMARY_PATTERN)
                captcha_pattern = js_regex_alternation(SELECTORS["captcha_patterns"])
                while time.monotonic() < deadline or (
                    self._needs_captcha
                    and captcha_hard_deadline is not None
                    and time.monotonic() < captcha_hard_deadline
                ):
                    capture = (
                        await self._run_script_or_track_timeout(
                            "window.__doubaoEmbeddedCapture || {}",
                        )
                        or {}
                    )
                    current_event_len = len(capture.get("events", []))
                    if current_event_len > last_event_len:
                        last_event_len = current_event_len
                        last_progress_at = time.monotonic()
                    if capture.get("done"):
                        if not collect_thinking_references:
                            response_completed = True
                            break
                        answer_finished_at = answer_finished_at or time.monotonic()
                        last_progress_at = time.monotonic()
                    page_state = (
                        await self._run_script_or_track_timeout(
                            (
                                r"""
                                (() => {
                                  const sendButtonSelectors = __SEND_BUTTON_SELECTORS__;
                                  const button = sendButtonSelectors
                                    .map(selector => document.querySelector(selector))
                                    .find(node => node);
                                  const loading = Boolean(button) && (
                                    button.getAttribute('data-loading') === 'true'
                                    || button.getAttribute('aria-busy') === 'true'
                                    || (button.getAttribute('aria-label') || '').includes('停止')
                                  );
                                  const referencePattern = __REFERENCE_SUMMARY_PATTERN__;
                                  const body = document.body.innerText || '';
                                  const captchaPattern = __CAPTCHA_PATTERN__;
                                  const captcha = new RegExp(captchaPattern).test(body);
                                  return {
                                    loading,
                                    captcha,
                                    referenceReady: new RegExp(referencePattern).test(body)
                                  };
                                })()
                                """.replace("__SEND_BUTTON_SELECTORS__", send_button_selectors)
                                .replace("__REFERENCE_SUMMARY_PATTERN__", reference_summary_pattern)
                                .replace("__CAPTCHA_PATTERN__", captcha_pattern)
                            ),
                        )
                        or {}
                    )
                    if not isinstance(page_state, dict):
                        page_state = {}
                    if page_state.get("referenceReady"):
                        last_progress_at = time.monotonic()
                    if self._needs_captcha:
                        # Keep polling until the user clears the captcha flag.
                        # The scheduler will pause the account in the meantime.
                        if page_state.get("captcha"):
                            last_progress_at = time.monotonic()
                        await asyncio.sleep(RESPONSE_POLL_INTERVAL_SECONDS)
                        continue
                    if page_state.get("captcha"):
                        self._needs_captcha = True
                        if captcha_hard_deadline is None:
                            captcha_hard_deadline = time.monotonic() + CAPTCHA_MAX_WAIT_SECONDS
                        await asyncio.sleep(RESPONSE_POLL_INTERVAL_SECONDS)
                        continue
                    # Response-stall watchdog: if nothing has happened for a while,
                    # run visual captcha detection to catch image-grid/iframe challenges.
                    if time.monotonic() - last_progress_at >= CAPTCHA_STALL_SECONDS:
                        with suppress(Exception):
                            if await self._has_visual_captcha():
                                self._needs_captcha = True
                                if captcha_hard_deadline is None:
                                    captcha_hard_deadline = (
                                        time.monotonic() + CAPTCHA_MAX_WAIT_SECONDS
                                    )
                                await asyncio.sleep(RESPONSE_POLL_INTERVAL_SECONDS)
                                continue
                        # Reset the stall clock so we do not spam detection.
                        last_progress_at = time.monotonic()
                    loading = bool(page_state.get("loading"))
                    saw_loading = saw_loading or loading
                    if page_state.get("referenceReady"):
                        response_completed = True
                        break
                    if saw_loading and not loading:
                        if not collect_thinking_references:
                            response_completed = True
                            break
                        answer_finished_at = answer_finished_at or time.monotonic()
                    if (
                        collect_thinking_references
                        and answer_finished_at is not None
                        and time.monotonic() - answer_finished_at >= 20
                    ):
                        response_completed = True
                        break
                    await asyncio.sleep(RESPONSE_POLL_INTERVAL_SECONDS)
                if not response_completed:
                    if self._needs_captcha:
                        raise TimeoutError("等待人工验证超时")
                    raise TimeoutError("等待豆包回答超时")
                fragments: list[str] = []
                for event in capture.get("events", []):
                    _collect_text(event, fragments)
                text = _merge_text_fragments(fragments) or "豆包回答完成"
                references: list[dict[str, str]] = []
                expected = 0
                if collect_thinking_references:
                    reference_summary_pattern = js_regex_pattern(REFERENCE_SUMMARY_PATTERN)
                    reference_rows_selector = js_string(", ".join(SELECTORS["reference_rows"]))
                    reference_appear_script = r"""
                        (() => {
                          const pattern = __REFERENCE_SUMMARY_PATTERN__;
                          if (new RegExp(pattern).test(document.body.innerText || '')) return true;
                          const selector = __REFERENCE_ROWS_SELECTOR__;
                          return document.querySelectorAll(selector).length > 0;
                        })()
                        """.replace(
                        "__REFERENCE_SUMMARY_PATTERN__", reference_summary_pattern
                    ).replace("__REFERENCE_ROWS_SELECTOR__", reference_rows_selector)
                    await self._wait_for_condition(
                        reference_appear_script,
                        timeout=REFERENCE_APPEAR_TIMEOUT_SECONDS,
                        interval=0.1,
                    )
                    references, expected = await self._expand_references(reference_callback)
                self._consecutive_failures = 0
                self._last_error_code = 0
                return {
                    "text": text,
                    "conversation_id": None,
                    "events": capture.get("events", []),
                    "thinking_references": references,
                    "expected_reference_count": expected,
                }
            except Exception:
                self._consecutive_failures += 1
                raise

    async def _reference_rows(self) -> list[dict[str, str]]:
        rows = await self._run_script(build_reference_rows_script())
        if not isinstance(rows, list):
            rows = []
        output: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in rows:
            link = str(row.get("link", "")).strip()
            if link and link not in seen:
                seen.add(link)
                platform_type = str(row.get("platformType", "")).strip()
                if not platform_type:
                    platform_type = category_for_url(link)
                output.append(
                    {
                        "title": str(row.get("title", "")).strip(),
                        "platform": str(row.get("platform", "")).strip(),
                        "platform_type": platform_type,
                        "link": link,
                    }
                )
        if output:
            return output
        generic = await self._run_script(REFERENCE_GENERIC_SCRIPT)
        if not isinstance(generic, list):
            generic = []
        for row in generic:
            link = str(row.get("link", "")).strip()
            if link and link not in seen:
                seen.add(link)
                output.append(
                    {
                        "title": str(row.get("title", "")).strip(),
                        "platform": platform_for_url(link),
                        "platform_type": category_for_url(link),
                        "link": link,
                    }
                )
        return output

    async def _expand_references(
        self,
        reference_callback: Callable[[dict[str, str]], Any] | None = None,
    ) -> tuple[list[dict[str, str]], int]:
        reference_summary_pattern = js_regex_pattern(REFERENCE_SUMMARY_PATTERN)
        reference_rows_selector = js_string(", ".join(SELECTORS["reference_rows"]))
        reference_expand_selectors = js_selector_list(SELECTORS["reference_expand"])
        more_references_text = js_string(SELECTORS["reference_more_text"])
        expected_script = (
            r"""
            (() => {
              const visible = node => {
                const style = getComputedStyle(node);
                const box = node.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && box.width > 0 && box.height > 0;
              };
              const pattern = __REFERENCE_SUMMARY_PATTERN__;
              const bodyText = document.body.innerText || '';
              const matches = [...bodyText.matchAll(new RegExp(pattern, 'g'))];
              const expected = matches.length
                ? Number(matches[matches.length - 1][1])
                : 0;
              const visibleRows = [...document.querySelectorAll(
                __REFERENCE_ROWS_SELECTOR__
              )].filter(visible);
              if (!visibleRows.length) {
                const expandSelectors = __REFERENCE_EXPAND_SELECTORS__;
                let candidates = [...document.querySelectorAll(
                  expandSelectors.filter(s => !s.startsWith('xpath=')).join(',')
                )].filter(node => {
                  if (!visible(node)) return false;
                  const text = (node.innerText || node.textContent || '')
                    .replace(/\s+/g, ' ').trim();
                  return text.length < 120 && new RegExp(pattern).test(text);
                });
                if (!candidates.length) {
                  candidates = [...document.querySelectorAll('*')].filter(node => {
                    if (!visible(node)) return false;
                    const text = (node.innerText || node.textContent || '')
                      .replace(/\s+/g, ' ').trim();
                    return text.length < 120 && new RegExp(pattern).test(text);
                  });
                }
                const label = candidates[candidates.length - 1];
                if (label) {
                  const target = label.closest(
                    'button,[role="button"],[class*="cursor-pointer"]'
                  ) || label;
                  target.scrollIntoView({ block: 'center' });
                  target.click();
                }
              }
              return expected;
            })()
            """.replace("__REFERENCE_SUMMARY_PATTERN__", reference_summary_pattern)
            .replace("__REFERENCE_ROWS_SELECTOR__", reference_rows_selector)
            .replace("__REFERENCE_EXPAND_SELECTORS__", reference_expand_selectors)
        )
        expected = (await self._run_script(expected_script)) or 0
        await self._wait_for_condition(
            (
                r"""
                (() => {
                  const selector = __REFERENCE_ROWS_SELECTOR__;
                  if (document.querySelectorAll(selector).length > 0) return true;
                  const pattern = __REFERENCE_SUMMARY_PATTERN__;
                  return new RegExp(pattern).test(document.body.innerText || '');
                })()
                """.replace("__REFERENCE_SUMMARY_PATTERN__", reference_summary_pattern).replace(
                    "__REFERENCE_ROWS_SELECTOR__", reference_rows_selector
                )
            ),
            timeout=REFERENCE_APPEAR_TIMEOUT_SECONDS,
            interval=0.1,
        )
        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        stalled = 0
        for _ in range(40):
            for row in await self._reference_rows():
                if row["link"] not in seen:
                    seen.add(row["link"])
                    rows.append(row)
                    if reference_callback is not None:
                        callback_result = reference_callback(row)
                        if inspect.isawaitable(callback_result):
                            await callback_result
            if expected == 0:
                break
            if expected and len(rows) >= expected:
                break
            before = len(rows)
            await self._run_script(
                (
                    r"""
                    (() => {
                      const visible = node => {
                        const style = getComputedStyle(node);
                        const box = node.getBoundingClientRect();
                        return style.display !== 'none' && style.visibility !== 'hidden'
                          && box.width > 0 && box.height > 0;
                      };
                      const moreText = __MORE_REFERENCES_TEXT__;
                      const more = [...document.querySelectorAll('button,div,span')]
                        .find(node => visible(node)
                          && (node.innerText || '').trim() === moreText);
                      if (more) {
                        more.scrollIntoView({ block: 'center' });
                        more.click();
                        return true;
                      }
                      for (const element of document.querySelectorAll('div')) {
                        const style = getComputedStyle(element);
                        if (['auto', 'scroll'].includes(style.overflowY)
                            && element.scrollHeight > element.clientHeight + 40) {
                          element.scrollTop = element.scrollHeight;
                        }
                      }
                      window.scrollTo(0, document.body.scrollHeight);
                      return false;
                    })()
                    """.replace("__MORE_REFERENCES_TEXT__", more_references_text)
                ),
            )
            await asyncio.sleep(REFERENCE_POLL_INTERVAL_SECONDS)
            for row in await self._reference_rows():
                if row["link"] not in seen:
                    seen.add(row["link"])
                    rows.append(row)
                    if reference_callback is not None:
                        callback_result = reference_callback(row)
                        if inspect.isawaitable(callback_result):
                            await callback_result
            stalled = stalled + 1 if len(rows) == before else 0
            if stalled >= 5:
                break
        if expected and len(rows) < expected:
            snapshot: dict[str, Any] = {}
            preview = ""
            if os.environ.get("DOUBAO_DEBUG"):
                snapshot = await self._debug_snapshot()
                snapshot_path = self.user_data_dir / ".doubao-debug-snapshot.json"
                with suppress(OSError):
                    snapshot_path.write_text(
                        json.dumps(snapshot, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                preview = snapshot.get("bodyPreview", "")
            raise ReferenceExpansionError(
                f"参考资料未完整展开：页面标明 {expected} 篇，实际识别到 {len(rows)} 篇。"
                f"页面摘要检测={snapshot.get('hasSummary', 'unknown')}，"
                f"参考行节点数={snapshot.get('referenceRows', 'unknown')}，"
                f"展开按钮数={snapshot.get('expandButtons', 'unknown')}，"
                f"页面预览：{preview[:200]}"
            )
        return rows, int(expected)
