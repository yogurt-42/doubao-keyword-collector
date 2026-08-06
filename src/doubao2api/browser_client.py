from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .cookie_utils import parse_cookie_records
from .selectors import (
    REFERENCE_SUMMARY_PATTERN,
    SELECTORS,
)
from .text_utils import _collect_text, _merge_text_fragments, _text_from_content

CHAT_URL = "https://www.doubao.com/chat/"
SESSION_COOKIE_NAMES = {"sessionid", "sessionid_ss"}

RESPONSE_POLL_INTERVAL_SECONDS = 0.2
REFERENCE_POLL_INTERVAL_SECONDS = 0.3
SEND_BUTTON_READY_TIMEOUT_SECONDS = 3.0
REFERENCE_APPEAR_TIMEOUT_SECONDS = 3.0
NEW_CONVERSATION_READY_TIMEOUT_SECONDS = 5.0


class BrowserUnavailableError(RuntimeError):
    pass


class LoginRequiredError(RuntimeError):
    pass


class ReferenceExpansionError(RuntimeError):
    pass


class BrowserClient:
    """One persistent browser profile.

    The browser is imported lazily so account management works even before the
    optional Playwright browser runtime has been installed.
    """

    def __init__(
        self,
        user_data_dir: Path,
        *,
        account_id: str,
        headless: bool = False,
        browser_channel: str = "",
        browser_executable_path: str = "",
    ) -> None:
        self.user_data_dir = user_data_dir.resolve()
        self.account_id = account_id
        self.headless = headless
        self.browser_channel = browser_channel
        self.browser_executable_path = browser_executable_path
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        self._chat_lock = asyncio.Lock()
        self._started_at = 0.0
        self._needs_captcha = False
        self._last_error_code = 0
        self._consecutive_failures = 0

    @property
    def started(self) -> bool:
        return self._context is not None

    async def start(self) -> None:
        if self.started:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserUnavailableError(
                "Playwright is not installed. Run: pip install -e . && playwright install chromium"
            ) from exc

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        launch_options: dict[str, Any] = {
            "headless": self.headless,
            "locale": "zh-CN",
            "viewport": {"width": 1440, "height": 900},
            "args": [
                "--no-first-run",
                "--no-default-browser-check",
            ],
        }
        if self.browser_channel:
            launch_options["channel"] = self.browser_channel
        if self.browser_executable_path:
            launch_options["executable_path"] = self.browser_executable_path

        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(self.user_data_dir),
                **launch_options,
            )
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
            if "doubao.com" not in (self._page.url or ""):
                await self._page.goto(
                    CHAT_URL,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
            self._started_at = time.monotonic()
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        context, playwright = self._context, self._playwright
        self._page = None
        self._context = None
        self._playwright = None
        if context is not None:
            with contextlib.suppress(Exception):
                await context.close()
        if playwright is not None:
            with contextlib.suppress(Exception):
                await playwright.stop()

    async def bring_to_front(self) -> None:
        if not self._page:
            raise BrowserUnavailableError("Browser is not started")
        await self._page.bring_to_front()

    async def cookies(self) -> list[dict[str, Any]]:
        if not self._context:
            return []
        return await self._context.cookies(["https://www.doubao.com"])

    async def inspect_session_state(self) -> dict[str, Any]:
        cookies = await self.cookies()
        cookie_names = {item.get("name", "") for item in cookies}
        logged_in = bool(cookie_names & SESSION_COOKIE_NAMES)
        has_ms_token = "msToken" in cookie_names
        page_url = self._page.url if self._page else ""
        return {
            "account_id": self.account_id,
            "started": self.started,
            "logged_in": logged_in,
            "browser": "ready" if self.started else "not_started",
            "has_ms_token": has_ms_token,
            "chat_ready": self.started and logged_in and not self._needs_captcha,
            "needs_captcha": self._needs_captcha,
            "last_error_code": self._last_error_code,
            "consecutive_failures": self._consecutive_failures,
            "page_url": page_url,
            "uptime_seconds": (int(time.monotonic() - self._started_at) if self.started else 0),
        }

    async def reset_captcha(self) -> None:
        self._needs_captcha = False
        self._last_error_code = 0
        self._consecutive_failures = 0

    async def screenshot(self) -> bytes:
        if not self._page:
            raise BrowserUnavailableError("Browser is not started")
        return await self._page.screenshot(type="png", full_page=False)

    async def import_cookies(self, cookie_text: str) -> int:
        if not self._context:
            await self.start()
        records = parse_cookie_records(cookie_text)
        if records:
            await self._context.add_cookies(records)
            if self._page:
                await self._page.reload(wait_until="domcontentloaded", timeout=60_000)
        return len(records)

    async def _install_response_capture(self) -> None:
        if not self._page:
            raise BrowserUnavailableError("Browser is not started")
        await self._page.evaluate(
            """
            () => {
              if (window.__doubaoOssCaptureInstalled) return;
              const originalFetch = window.fetch.bind(window);
              window.__doubaoOssCapture = null;
              window.fetch = async (...args) => {
                const input = args[0];
                const url = typeof input === 'string' ? input : (input && input.url) || '';
                const response = await originalFetch(...args);
                const capture = window.__doubaoOssCapture;
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
                        const lines = buffer.split('\\n');
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
              window.__doubaoOssCaptureInstalled = true;
            }
            """
        )

    async def _send_via_ui(self, request_id: str, text: str) -> None:
        if not self._page:
            raise BrowserUnavailableError("Browser is not started")
        composer_selectors = SELECTORS["composer"]
        await self._page.evaluate(
            """
            ([requestId, text, composerSelectors]) => {
              window.__doubaoOssCapture = {
                requestId,
                events: [],
                done: false,
                error: null
              };
              let textarea = null;
              for (const selector of composerSelectors) {
                const nodes = [...document.querySelectorAll(selector)];
                textarea = nodes.find(node => {
                  const box = node.getBoundingClientRect();
                  return box.width > 0 && box.height > 0;
                });
                if (textarea) break;
              }
              if (!textarea) throw new Error('No chat textarea found');
              const setter = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype,
                'value'
              ).set;
              setter.call(textarea, text);
              textarea.focus();
              textarea.dispatchEvent(new InputEvent('input', {
                bubbles: true,
                inputType: 'insertText',
                data: text
              }));
              textarea.dispatchEvent(new Event('change', { bubbles: true }));
              const options = {
                bubbles: true,
                cancelable: true,
                key: 'Enter',
                code: 'Enter',
                which: 13,
                keyCode: 13
              };
              textarea.dispatchEvent(new KeyboardEvent('keydown', options));
              textarea.dispatchEvent(new KeyboardEvent('keypress', options));
              textarea.dispatchEvent(new KeyboardEvent('keyup', options));
            }
            """,
            [request_id, text, composer_selectors],
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        timeout: float = 180,
        collect_thinking_references: bool = False,
        fresh_conversation: bool = False,
        reference_callback: Callable[[dict[str, str]], Any] | None = None,
    ) -> dict[str, Any]:
        if not self.started:
            await self.start()
        state = await self.inspect_session_state()
        if not state["logged_in"]:
            raise LoginRequiredError(
                "This account is not logged in. Open the account window and sign in first."
            )
        prompt = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                prompt = _text_from_content(message.get("content"))
                break
        if not prompt:
            raise ValueError("A non-empty user message is required")

        async with self._chat_lock:
            try:
                if fresh_conversation:
                    await self._page.goto(
                        CHAT_URL,
                        wait_until="domcontentloaded",
                        timeout=60_000,
                    )
                await self._install_response_capture()
                request_id = uuid.uuid4().hex
                await self._send_via_ui(request_id, prompt)
                deadline = time.monotonic() + timeout
                capture: dict[str, Any] = {}
                try:
                    capture = await self._page.wait_for_function(
                        "() => {"
                        "  const c = window.__doubaoOssCapture;"
                        "  return c && c.done ? c : false;"
                        "}",
                        timeout=timeout * 1000,
                        polling="raf",
                    )
                except Exception:
                    while time.monotonic() < deadline:
                        capture = await self._page.evaluate(
                            "() => window.__doubaoOssCapture || {}"
                        )
                        if capture.get("done"):
                            break
                        await asyncio.sleep(RESPONSE_POLL_INTERVAL_SECONDS)
                if not capture.get("done"):
                    raise TimeoutError("Timed out waiting for Doubao response")
                if capture.get("error"):
                    raise RuntimeError(capture["error"])
                fragments: list[str] = []
                for event in capture.get("events", []):
                    _collect_text(event, fragments)
                text = _merge_text_fragments(fragments)
                if not text:
                    raise RuntimeError(
                        "The browser returned no readable text. The Doubao page may have changed."
                    )
                self._consecutive_failures = 0
                self._last_error_code = 0
                references: list[dict[str, str]] = []
                expected_reference_count = 0
                if collect_thinking_references:
                    summary_regex = json.dumps(REFERENCE_SUMMARY_PATTERN, ensure_ascii=False)
                    with contextlib.suppress(Exception):
                        await self._page.wait_for_function(
                            f"() => {{\n"
                            f"  const pattern = {summary_regex};\n"
                            f"  return new RegExp(pattern).test(document.body.innerText || '');\n"
                            f"}}",
                            timeout=REFERENCE_APPEAR_TIMEOUT_SECONDS * 1000,
                        )
                    (
                        references,
                        expected_reference_count,
                    ) = await self._expand_and_collect_thinking_references()
                    if reference_callback is not None:
                        for reference in references:
                            callback_result = reference_callback(reference)
                            if inspect.isawaitable(callback_result):
                                await callback_result
                return {
                    "text": text,
                    "conversation_id": None,
                    "events": capture.get("events", []),
                    "thinking_references": references,
                    "expected_reference_count": expected_reference_count,
                }
            except Exception:
                self._consecutive_failures += 1
                raise

    async def _reference_rows(self) -> list[dict[str, str]]:
        if not self._page:
            return []
        reference_selector = ", ".join(SELECTORS["reference_rows"])
        title_selector = SELECTORS["reference_title"]
        reference_selector_json = json.dumps(reference_selector, ensure_ascii=False)
        title_selector_json = json.dumps(title_selector, ensure_ascii=False)
        rows = await self._page.evaluate(
            f"""
            () => {{
              const tidy = value => (value || '').replace(/\\n{{2,}}/g, ' ').trim();
              const absolute = value => {{
                try {{ return new URL(value, location.href).href; }}
                catch (_) {{ return ''; }}
              }};
              return [...document.querySelectorAll(
                {reference_selector_json}
              )].map(anchor => {{
                const label = anchor.querySelector({title_selector_json}) || anchor;
                const title = tidy(label.textContent || label.innerText || '')
                  .replace(/^[0-9]+[.、]\\s*/, '');
                return {{
                  title,
                  link: absolute(anchor.getAttribute('href') || '')
                }};
              }}).filter(item => item.title && item.link);
            }}
            """
        )
        output: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in rows:
            link = str(row.get("link", "")).strip()
            if not link or link in seen:
                continue
            seen.add(link)
            output.append({"title": str(row.get("title", "")).strip(), "link": link})
        return output

    async def _expand_and_collect_thinking_references(
        self,
    ) -> tuple[list[dict[str, str]], int]:
        if not self._page:
            raise BrowserUnavailableError("Browser is not started")

        summary_pattern = re.compile(
            REFERENCE_SUMMARY_PATTERN,
            re.DOTALL,
        )

        # Detect whether the page declares a thinking/reference summary at all.
        has_summary = False
        with contextlib.suppress(Exception):
            has_summary = await self._page.get_by_text(summary_pattern).count() > 0

        clicked = False
        for selector in SELECTORS["reference_expand"]:
            with contextlib.suppress(Exception):
                locator = self._page.locator(selector)
                count = await locator.count()
                if count:
                    await locator.nth(count - 1).click(timeout=2500)
                    clicked = True
                    break
        if not clicked:
            with contextlib.suppress(Exception):
                locator = self._page.get_by_text(summary_pattern)
                if await locator.count():
                    await locator.last.click(timeout=2500)
                    clicked = True

        reference_selector = ", ".join(SELECTORS["reference_rows"])
        reference_selector_json = json.dumps(reference_selector, ensure_ascii=False)
        with contextlib.suppress(Exception):
            await self._page.wait_for_function(
                f"() => document.querySelectorAll({reference_selector_json}).length > 0",
                timeout=REFERENCE_APPEAR_TIMEOUT_SECONDS * 1000,
            )
        expected = 0
        with contextlib.suppress(Exception):
            summaries = await self._page.get_by_text(summary_pattern).all_inner_texts()
            for value in summaries:
                match = re.search(r"参考\s*(\d+)\s*篇资料?", value)
                if match:
                    expected = max(expected, int(match.group(1)))

        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        stalled = 0
        for _ in range(40):
            for row in await self._reference_rows():
                if row["link"] not in seen:
                    seen.add(row["link"])
                    rows.append(row)
            if expected and len(rows) >= expected:
                break
            before = len(rows)
            clicked_more = False
            with contextlib.suppress(Exception):
                more = self._page.get_by_text(
                    re.compile(r"^\s*" + re.escape(SELECTORS["reference_more_text"]) + r"\s*$")
                )
                for index in range(await more.count()):
                    target = more.nth(index)
                    if not await target.is_visible():
                        continue
                    await target.scroll_into_view_if_needed(timeout=2000)
                    try:
                        await target.click(force=True, timeout=2500)
                    except Exception:
                        await target.evaluate("(element) => element.click()")
                    clicked_more = True
                    break
            if not clicked_more:
                await self._page.evaluate(
                    """
                    () => {
                      const scrollers = [...document.querySelectorAll('div')].filter(element => {
                        const style = getComputedStyle(element);
                        return ['auto', 'scroll'].includes(style.overflowY)
                          && element.scrollHeight > element.clientHeight + 40;
                      });
                      for (const element of scrollers) element.scrollTop = element.scrollHeight;
                      window.scrollTo(0, document.body.scrollHeight);
                    }
                    """
                )
            try:
                await self._page.wait_for_function(
                    f"() => document.querySelectorAll({reference_selector_json}).length > {before}",
                    timeout=REFERENCE_POLL_INTERVAL_SECONDS * 1000,
                )
            except Exception:
                await asyncio.sleep(REFERENCE_POLL_INTERVAL_SECONDS)
            for row in await self._reference_rows():
                if row["link"] not in seen:
                    seen.add(row["link"])
                    rows.append(row)
            stalled = stalled + 1 if len(rows) == before else 0
            if stalled >= 5:
                break

        if expected and len(rows) < expected:
            raise ReferenceExpansionError(
                f"参考资料未完整展开：页面标明 {expected} 篇，实际识别到 {len(rows)} 篇"
            )
        if not rows and has_summary:
            raise ReferenceExpansionError(
                "参考资料未识别到任何链接：页面存在参考摘要但展开或解析失败"
            )
        return rows, expected
