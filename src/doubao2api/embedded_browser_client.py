from __future__ import annotations

import asyncio
import inspect
import json
import logging
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
from .platforms import get_platform
from .platforms.base import AIPlatform
from .research_platforms import category_for_url, platform_for_url, to_js_platform_data
from .selectors import (
    js_regex_alternation,
    js_regex_pattern,
    js_selector_list,
    js_string,
)
from .text_utils import _collect_text, _merge_text_fragments, _text_from_content

LOGGER = logging.getLogger(__name__)

RESPONSE_POLL_INTERVAL_SECONDS = 0.5
REFERENCE_POLL_INTERVAL_SECONDS = 0.3
SEND_BUTTON_READY_TIMEOUT_SECONDS = 8.0
REFERENCE_APPEAR_TIMEOUT_SECONDS = 10.0
NEW_CONVERSATION_READY_TIMEOUT_SECONDS = 8.0
PAGE_HEALTH_PING_TIMEOUT_SECONDS = 5.0
MAX_SCRIPT_TIMEOUT_STREAK = 2
# 页面脚本超时后的重试间隔与次数：吸收回答渲染/验证 SDK 活动造成的
# 页面主线程短暂繁忙（Qt runJavaScript 偶发 6 秒无回调）。
SCRIPT_TIMEOUT_RETRY_INTERVAL_SECONDS = 1.0
CAPTCHA_STALL_SECONDS = 30.0
CAPTCHA_MAX_WAIT_SECONDS = 600.0
# 弱验证码信号（仅 iframe/全屏遮罩，无文字/图片/滑块）需要延迟复查，
# 以过滤豆包等平台的静默验证（verifycenter iframe 出现数秒后自动消失）。
CAPTCHA_WEAK_CONFIRM_DELAY_SECONDS = 4.0
# 验证确认后的保持期：确认命中后该时间内直接沿用"验证存在"的结论，
# 不重复执行检测（弱信号每次复查要 4 秒）也不重复输出 WARNING 日志。
# 豆包批量静默验证的遮罩会持续约 1 分钟，期间账号页 10 秒周期刷新
# 会对每个账号反复检测复查，1 分钟刷数十条 WARNING 并拖慢状态刷新。
CAPTCHA_CONFIRMED_HOLD_SECONDS = 30.0

# 文本型验证码的扫描范围：验证码 DOM 选择器之外的常见弹窗容器。
# 文本关键词只在这些弹层节点内匹配，不再扫整页 body——
# 否则回答正文里的“验证码/身份验证”等业务词汇会误报。
# 注意：不能用 [class*="dialog"] / [class*="popup"] 这类 class 子串匹配——
# 豆包新布局的 Tailwind 原子类里嵌着 aria-haspopup="dialog" 等条件选择器
# 文本，普通按钮/容器的 class 属性值也会包含 "dialog"/"popup" 字样，
# 实测正常聊天页有 65 个普通元素被误当弹层，扫到正文业务词汇即误报。
CAPTCHA_DIALOG_SELECTORS = [
    '[role="dialog"]',
    '[role="alertdialog"]',
    '[class*="modal"]',
    '[class*="mask"]',
    '[class*="overlay"]',
    '[id*="modal"]',
    '[id*="dialog"]',
    '[id*="popup"]',
]

# 弹层文案长度上限：验证弹层的提示文字都很短（“请完成安全验证”等），
# 超过该长度的节点文本视为回答正文/深度思考内容，不参与验证码文本匹配。
CAPTCHA_TEXT_MAX_LENGTH = 200


def _captcha_text_scope_selectors(platform: AIPlatform) -> str:
    """Combined selector string for captcha text scanning (dialogs + captcha nodes)."""

    return js_string(", ".join([*platform.captcha_dom_selectors, *CAPTCHA_DIALOG_SELECTORS]))


# _default_platform is kept for backward compatibility but is no longer used.
def _default_platform() -> AIPlatform:
    return get_platform("doubao")


def _build_capture_script(url_patterns: list[str]) -> str:
    """Build the fetch-interception script for the given platform endpoint patterns."""
    patterns_json = js_selector_list(url_patterns)
    return rf"""
(() => {{
  window.__doubaoEmbeddedCapture = {{
    events: [],
    done: false,
    error: null
  }};
  if (window.__doubaoEmbeddedCaptureInstalled) return true;
  const urlPatterns = {patterns_json};
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {{
    const request = args[0];
    const url = typeof request === 'string' ? request : (request && request.url) || '';
    const response = await originalFetch(...args);
    const capture = window.__doubaoEmbeddedCapture;
    if (capture && urlPatterns.some(pattern => url.includes(pattern)) && response.body) {{
      const cloned = response.clone();
      (async () => {{
        try {{
          const reader = cloned.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          while (true) {{
            const {{ value, done }} = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, {{ stream: true }});
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            for (const line of lines) {{
              const trimmed = line.trim();
              if (!trimmed.startsWith('data:')) continue;
              const raw = trimmed.slice(5).trim();
              if (!raw || raw === '[DONE]') continue;
              try {{ capture.events.push(JSON.parse(raw)); }}
              catch (_) {{ capture.events.push({{ text: raw }}); }}
            }}
          }}
          capture.done = true;
        }} catch (error) {{
          capture.error = String(error);
          capture.done = true;
        }}
      }})();
    }}
    return response;
  }};
  window.__doubaoEmbeddedCaptureInstalled = true;
  return true;
}})()
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


def build_reference_rows_script(platform: AIPlatform) -> str:
    selectors = platform.selectors
    return (
        REFERENCE_ROWS_SCRIPT_TEMPLATE.replace(
            "__REFERENCE_ROWS__", js_string(", ".join(selectors["reference_rows"]))
        )
        .replace("__REFERENCE_TITLE__", js_string(selectors["reference_title"]))
        .replace("__REFERENCE_SOURCE__", js_string(selectors["reference_source"]))
        .replace("__PLATFORM_DATA__", to_js_platform_data())
    )


CAPTCHA_DETECT_SCRIPT_TEMPLATE = r"""
(() => {
  const tidy = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = node => {
    if (!node) return false;
    const style = getComputedStyle(node);
    const box = node.getBoundingClientRect();
    // opacity:0 的节点在肉眼层面不可见，但 getBoundingClientRect 仍可能有尺寸。
    // 字节验证中心会预加载一个全屏透明容器/iframe，必须排除这种常驻隐藏元素。
    const opacity = parseFloat(style.opacity);
    return style.display !== 'none' && style.visibility !== 'hidden'
      && box.width > 0 && box.height > 0
      && !Number.isNaN(opacity) && opacity >= 0.05;
  };
  const textPattern = new RegExp(__CAPTCHA_TEXT_PATTERN__);
  const iframePatterns = __CAPTCHA_IFRAME_PATTERNS__;
  // 验证 SDK 可能常驻隐藏的验证 iframe（display:none 预加载），
  // 只有可见且有实际尺寸的 iframe 才算命中。
  let iframeMatch = false;
  const matchedIframeSrcs = [];
  for (const iframe of document.querySelectorAll('iframe')) {
    const src = (iframe.src || iframe.getAttribute('src') || '').toLowerCase();
    if (!iframePatterns.some(pattern => src.includes(pattern))) continue;
    if (!visible(iframe)) continue;
    const iframeBox = iframe.getBoundingClientRect();
    if (iframeBox.width < 100 || iframeBox.height < 60) continue;
    iframeMatch = true;
    if (matchedIframeSrcs.length < 5) matchedIframeSrcs.push(src.slice(0, 200));
  }
  const captchaSelectors = __CAPTCHA_DOM_SELECTORS__;
  const overlayNodes = captchaSelectors
    .flatMap(selector => [...document.querySelectorAll(selector)])
    .filter(visible);
  // 文本检测只扫弹层/遮罩类节点：整页 body 扫会把回答正文里的
  // “验证码/身份验证”等业务词汇误判为验证码。
  let textMatch = false;
  let textMatchSource = '';
  const textScopeNodes = [...overlayNodes];
  for (const node of document.querySelectorAll(__CAPTCHA_TEXT_SCOPE__)) {
    if (visible(node)) textScopeNodes.push(node);
  }
  for (const node of textScopeNodes) {
    const nodeText = tidy(node.innerText || '');
    // 超过弹层文案长度上限的节点视为回答正文/深度思考内容，跳过——
    // 否则正文里的“短信验证码”等业务词汇会误报。
    if (!nodeText || nodeText.length > __CAPTCHA_TEXT_MAX_LENGTH__) continue;
    if (textPattern.test(nodeText)) {
      textMatch = true;
      textMatchSource = nodeText.slice(0, 120);
      break;
    }
  }
  let imageGridMatch = false;
  let imageGridMaxCount = 0;
  let imageGridInfo = '';
  let dragHandleMatch = false;
  let fullscreenOverlayMatch = false;
  const overlayInfo = [];
  for (const node of overlayNodes) {
    if (overlayInfo.length < 5) {
      const nodeId = node.id ? '#' + node.id : '';
      const nodeClass = String(node.className || '').replace(/\s+/g, '.').slice(0, 80);
      overlayInfo.push(node.tagName.toLowerCase() + nodeId + (nodeClass ? '.' + nodeClass : ''));
    }
    const nodeStyle = getComputedStyle(node);
    const nodeBox = node.getBoundingClientRect();
    // 字节验证中心等风控遮罩：fixed/absolute 全屏覆盖（如 #captcha_container），
    // 遮住整个聊天页，使发送按钮被禁用或点击不可达。
    // 额外要求带遮罩背景或内含可见 iframe，排除常驻的透明全屏容器。
    const nodeBg = nodeStyle.backgroundColor || '';
    const hasDimBackground = nodeBg !== '' && nodeBg !== 'transparent'
      && !/^rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0\s*\)$/.test(nodeBg);
    const hasVisibleIframe = [...node.querySelectorAll('iframe')].some(visible);
    if ((nodeStyle.position === 'fixed' || nodeStyle.position === 'absolute')
      && nodeBox.width >= window.innerWidth * 0.8
      && nodeBox.height >= window.innerHeight * 0.8
      && (hasDimBackground || hasVisibleIframe)) {
      fullscreenOverlayMatch = true;
    }
    const imgs = [...node.querySelectorAll('img')].filter(visible);
    if (imgs.length > imageGridMaxCount) imageGridMaxCount = imgs.length;
    if (imgs.length >= 6) {
      imageGridMatch = true;
    }
    const text = tidy(node.innerText || '');
    if (/拖动|拖拽|滑动|滑块/.test(text)) {
      dragHandleMatch = true;
    }
  }
  if (!imageGridMatch) {
    // 全局兜底：九宫格验证码的特征是同一容器里 ≥6 张同尺寸大图。
    // 只统计祖先链上有 fixed/absolute 定位容器的图片——内联验证码
    // （如 geetest）一定在弹层里；豆包回答区的视频封面也是一排同尺寸
    // 大图（实测 256x192 x 6），但它们在文档流（relative/static）中，
    // 不参与统计，否则正常回答会被误判为九宫格验证码。
    // 注意跨域 iframe（字节验证中心）里的图片顶层 DOM 看不到，
    // 这条只对内联验证码有意义。
    const inFloatingLayer = node => {
      let current = node.parentElement;
      while (current && current !== document.body) {
        const position = getComputedStyle(current).position;
        if (position === 'fixed' || position === 'absolute') return true;
        current = current.parentElement;
      }
      return false;
    };
    const sizeGroups = new Map();
    for (const img of document.querySelectorAll('img')) {
      if (!visible(img)) continue;
      const imgBox = img.getBoundingClientRect();
      if (imgBox.width < 56 || imgBox.height < 56) continue;
      if (!inFloatingLayer(img)) continue;
      const sizeKey = Math.round(imgBox.width) + 'x' + Math.round(imgBox.height);
      sizeGroups.set(sizeKey, (sizeGroups.get(sizeKey) || 0) + 1);
    }
    for (const [sizeKey, count] of sizeGroups) {
      if (count > imageGridMaxCount) imageGridMaxCount = count;
      if (count >= 6) {
        imageGridMatch = true;
        imageGridInfo = sizeKey + ' x ' + count;
        break;
      }
    }
  }
  return {
    textMatch,
    iframeMatch,
    imageGridMatch,
    dragHandleMatch,
    fullscreenOverlayMatch,
    overlayVisible: overlayNodes.length > 0,
    matchedIframeSrcs,
    overlayInfo,
    imageGridMaxCount,
    imageGridInfo,
    textMatchSource
  };
})()
"""


def build_captcha_detect_script(platform: AIPlatform) -> str:
    return (
        CAPTCHA_DETECT_SCRIPT_TEMPLATE.replace(
            "__CAPTCHA_TEXT_PATTERN__", js_regex_alternation(platform.captcha_patterns)
        )
        .replace("__CAPTCHA_IFRAME_PATTERNS__", js_selector_list(platform.captcha_iframe_patterns))
        .replace("__CAPTCHA_DOM_SELECTORS__", js_selector_list(platform.captcha_dom_selectors))
        .replace("__CAPTCHA_TEXT_SCOPE__", _captcha_text_scope_selectors(platform))
        .replace("__CAPTCHA_TEXT_MAX_LENGTH__", str(CAPTCHA_TEXT_MAX_LENGTH))
    )


REFERENCE_GENERIC_SCRIPT_TEMPLATE = r"""
(() => {
  const tidy = value => (value || '').replace(/\n{2,}/g, ' ').trim();
  const absolute = value => {
    try { return new URL(value, location.href).href; }
    catch (_) { return ''; }
  };
  const ignoredHosts = new Set(__IGNORED_HOSTS__);
  const summaryPattern = new RegExp(__self.platform.reference_summary_pattern__);
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


def build_reference_generic_script(platform: AIPlatform) -> str:
    ignored = json.dumps(list(platform.ignored_hosts), ensure_ascii=False)
    summary_pattern = js_regex_pattern(platform.reference_summary_pattern)
    return REFERENCE_GENERIC_SCRIPT_TEMPLATE.replace(
        "__self.platform.reference_summary_pattern__", summary_pattern
    ).replace("__IGNORED_HOSTS__", ignored)


ROBUST_LOGIN_STATE_SCRIPT_TEMPLATE = r"""
(() => {
  const visible = node => {
    if (!node) return false;
    const style = getComputedStyle(node);
    const box = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden'
      && box.width > 0 && box.height > 0;
  };
  const compact = value => (value || '').replace(/\s+/g, ' ').trim();
  const textOf = node => compact(node.innerText || node.textContent);

  // 1. Composer is the strongest chat-ready signal and works on both layouts.
  const composerSelectors = __COMPOSER_SELECTORS__;
  const hasComposer = composerSelectors.some(selector =>
    [...document.querySelectorAll(selector)].some(visible)
  );

  // 2. Positive logged-in signals (new layout): user menu trigger / avatar.
  const userMenuSelectors = __USER_MENU_SELECTORS__;
  const hasUserMenu = userMenuSelectors.some(selector =>
    [...document.querySelectorAll(selector)].some(visible)
  );

  // 3. Positive logged-in signal: visible "退出登录" / logout control.
  const logoutText = __LOGOUT_TEXT__;
  let hasLogoutControl = false;
  if (logoutText) {
    const logoutSelectors = ['button', 'a', '[role="menuitem"]', '[role="button"]'];
    for (const sel of logoutSelectors) {
      for (const node of document.querySelectorAll(sel)) {
        if (visible(node) && textOf(node) === logoutText) {
          hasLogoutControl = true;
          break;
        }
      }
      if (hasLogoutControl) break;
    }
  }

  // 4. Negative signal: visible login/register controls.
  const loginSelectors = __LOGIN_SELECTORS__;
  const loginTextPatterns = __LOGIN_TEXT_PATTERNS__;
  const ariaPatterns = __LOGIN_ARIA_PATTERNS__;
  let hasLoginControl = false;
  for (const sel of loginSelectors) {
    for (const node of document.querySelectorAll(sel)) {
      if (!visible(node)) continue;
      const text = textOf(node);
      const aria = compact(
        (node.getAttribute('aria-label') || '') + ' '
        + (node.getAttribute('title') || '')
      );
      if (
        loginTextPatterns.some(pattern => new RegExp(pattern).test(text))
        || ariaPatterns.some(pattern => new RegExp(pattern, 'i').test(aria))
      ) {
        hasLoginControl = true;
        break;
      }
    }
    if (hasLoginControl) break;
  }

  // 5. "New conversation" button (sidebar). Fast path first, then fallback.
  const newChatText = __NEW_CHAT_TEXT__;
  const newChatSelectors = ['button', 'a', '[role="button"]'];
  let hasNewChat = false;
  for (const sel of newChatSelectors) {
    for (const node of document.querySelectorAll(sel)) {
      if (visible(node) && textOf(node) === newChatText) {
        hasNewChat = true;
        break;
      }
    }
    if (hasNewChat) break;
  }
  // Fallback for new-layout spans inside sidebar/navigation containers.
  if (!hasNewChat) {
    const containers = document.querySelectorAll(
      '[class*="sidebar"], [class*="nav"], aside, [data-slot="sidebar"]'
    );
    for (const container of containers) {
      if (!visible(container)) continue;
      const spans = container.querySelectorAll('span.font-medium, span');
      for (const span of spans) {
        if (visible(span) && textOf(span) === newChatText) {
          hasNewChat = true;
          break;
        }
      }
      if (hasNewChat) break;
    }
  }

  // 6. History list (old layout mainly).
  const historyText = __HISTORY_TEXT__;
  const historyLinkSelector = __HISTORY_LINK_SELECTOR__;
  const historyMinLinks = __HISTORY_MIN_LINKS__;
  const body = document.body ? (document.body.innerText || '') : '';
  const hasHistory = body.includes(historyText)
    && document.querySelectorAll(historyLinkSelector).length >= historyMinLinks;

  // 7. Captcha / risk detection. 只扫弹层候选节点的短文本，
  // 避免回答正文里的“验证码/身份验证”等业务词汇误报。
  const captchaPattern = new RegExp(__CAPTCHA_PATTERN__);
  let hasCaptcha = false;
  for (const node of document.querySelectorAll(__CAPTCHA_TEXT_SCOPE__)) {
    if (!visible(node)) continue;
    const nodeText = (node.innerText || '').replace(/\s+/g, ' ').trim();
    if (!nodeText || nodeText.length > __CAPTCHA_TEXT_MAX_LENGTH__) continue;
    if (captchaPattern.test(nodeText)) {
      hasCaptcha = true;
      break;
    }
  }

  // Final decision: positive signals win over negative signals when ambiguous.
  const loggedIn = hasUserMenu || hasLogoutControl
    || (!hasLoginControl && hasComposer && (hasNewChat || hasHistory));

  return {
    ready: document.readyState !== 'loading',
    hasLoginControl,
    hasUserMenu,
    hasLogoutControl,
    hasNewChat,
    hasHistory,
    hasComposer,
    hasCaptcha,
    loggedIn
  };
})()
"""


def _build_login_state_script(platform: AIPlatform) -> str:
    """Build the login-state detection script for a specific platform."""
    selectors = platform.selectors
    return (
        ROBUST_LOGIN_STATE_SCRIPT_TEMPLATE.replace(
            "__USER_MENU_SELECTORS__", js_selector_list(selectors["user_menu_trigger"])
        )
        .replace("__LOGOUT_TEXT__", js_string(selectors.get("logout_text", "")))
        .replace("__LOGIN_SELECTORS__", js_selector_list(selectors["login_controls"]["selectors"]))
        .replace(
            "__LOGIN_TEXT_PATTERNS__",
            js_selector_list(selectors["login_controls"]["text_patterns"]),
        )
        .replace(
            "__LOGIN_ARIA_PATTERNS__",
            js_selector_list(selectors["login_controls"]["aria_patterns"]),
        )
        .replace("__NEW_CHAT_TEXT__", js_string(selectors["new_chat"]["text"]))
        .replace("__CAPTCHA_PATTERN__", js_regex_alternation(platform.captcha_patterns))
        .replace("__CAPTCHA_TEXT_SCOPE__", _captcha_text_scope_selectors(platform))
        .replace("__CAPTCHA_TEXT_MAX_LENGTH__", str(CAPTCHA_TEXT_MAX_LENGTH))
        .replace("__HISTORY_TEXT__", js_string(selectors["history_indicator"]["text"]))
        .replace(
            "__HISTORY_LINK_SELECTOR__",
            js_string(selectors["history_indicator"]["link_selector"]),
        )
        .replace("__HISTORY_MIN_LINKS__", str(selectors["history_indicator"]["min_links"]))
        .replace("__COMPOSER_SELECTORS__", js_selector_list(selectors["composer"]))
    )


class EmbeddedBrowserClient:
    """Browser client backed by an in-application Qt WebEngine tab."""

    def __init__(
        self,
        bridge: Any,
        user_data_dir: Path,
        account_id: str,
        platform: str | AIPlatform = "doubao",
    ) -> None:
        self.bridge = bridge
        self.user_data_dir = user_data_dir.resolve()
        self.account_id = account_id
        self.platform = platform if isinstance(platform, AIPlatform) else get_platform(platform)
        self._started = False
        self._chat_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._started_at = 0.0
        self._needs_captcha = False
        self._last_error_code = 0
        self._consecutive_failures = 0
        self._script_timeout_streak = 0
        # 弱信号（仅 iframe/全屏遮罩）缓存，避免对豆包静默验证反复等待 4 秒。
        self._captcha_weak_last_check = 0.0
        self._captcha_weak_last_result = False
        # 验证确认保持期：确认命中后短时间内沿用结论，不重复检测/刷日志。
        self._captcha_confirmed_until = 0.0

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
            self.platform.chat_url,
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

    async def _run_script(self, script: str, *, timeout_retries: int = 2) -> Any:
        """Run page JavaScript through a JSON envelope.

        Qt WebEngine 6.11 can turn JavaScript objects and arrays into an empty
        string in its callback. Returning JSON text keeps the value stable
        across Qt versions and is also convenient for test bridges.

        页面主线程短暂繁忙（回答渲染、验证 SDK 活动、GC）时 runJavaScript
        会偶发 6 秒无回调。默认对超时自动重试 timeout_retries 次以吸收抖动；
        有副作用的脚本（点击发送/回车/展开参考资料）必须传 timeout_retries=0，
        避免超时后实际已生效又重复执行（如把停止按钮当发送再点一次）。
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
        raw: Any = None
        for attempt in range(timeout_retries + 1):
            try:
                raw = await self.bridge.run_javascript(self.account_id, wrapped)
            except Exception as exc:
                error_text = str(exc).casefold()
                is_timeout = "超时" in error_text or "timeout" in error_text
                if is_timeout and attempt < timeout_retries:
                    LOGGER.info(
                        "账号 %s 页面脚本执行超时，%.1f 秒后重试（第 %d/%d 次）",
                        self.account_id,
                        SCRIPT_TIMEOUT_RETRY_INTERVAL_SECONDS,
                        attempt + 1,
                        timeout_retries,
                    )
                    await asyncio.sleep(SCRIPT_TIMEOUT_RETRY_INTERVAL_SECONDS)
                    continue
                raise
            break
        if not isinstance(raw, str):
            return raw
        try:
            decoded = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return raw
        if not isinstance(decoded, dict) or not decoded.get("__doubaoBridge"):
            return decoded
        if not decoded.get("ok"):
            raise RuntimeError(
                f"{self.platform.name}页面操作失败：{decoded.get('error', '未知错误')}"
            )
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
                if (!node) return false;
                const style = getComputedStyle(node);
                const box = node.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && box.width > 0 && box.height > 0;
              };
              const compact = value => (value || '').replace(/\s+/g, ' ').trim();
              const newChatText = __NEW_CHAT_TEXT__;
              let label = null;
              // Fast path: clickable elements only.
              for (const sel of ['button', 'a', '[role="button"]']) {
                label = [...document.querySelectorAll(sel)].find(node =>
                  visible(node) && compact(node.innerText || node.textContent) === newChatText
                );
                if (label) break;
              }
              // Fallback: spans inside sidebar/navigation containers.
              if (!label) {
                const containers = document.querySelectorAll(
                  '[class*="sidebar"], [class*="nav"], aside, [data-slot="sidebar"]'
                );
                for (const container of containers) {
                  if (!visible(container)) continue;
                  label = [...container.querySelectorAll('span.font-medium, span')].find(node =>
                    visible(node) && compact(node.innerText || node.textContent) === newChatText
                  );
                  if (label) break;
                }
              }
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
            "__NEW_CHAT_TEXT__",
            js_string(self.platform.selectors["new_chat"]["text"]),
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
            """.replace(
            "__COMPOSER_SELECTORS__", js_selector_list(self.platform.selectors["composer"])
        )

        async def has_fresh_composer() -> bool:
            state = await self._run_script(textarea_state_script)
            return isinstance(state, dict) and state.get("value") == ""

        # Primary method: reload the base chat URL. This always gives a clean page.
        await self.bridge.navigate(self.account_id, self.platform.chat_url)
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
        """Fill the composer textarea/contenteditable and trigger React input events."""

        type_script = r"""
            (() => {
              const visible = node => {
                if (!node) return false;
                const style = getComputedStyle(node);
                const box = node.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && box.width > 0 && box.height > 0;
              };
              const selectors = __COMPOSER_SELECTORS__;
              const textarea = selectors.flatMap(selector =>
                [...document.querySelectorAll(selector)]
              ).find(node => visible(node));
              if (!textarea) throw new Error('No chat textarea found');
              const text = __PROMPT__;
              const isEditableDiv = textarea.isContentEditable
                || textarea.contentEditable === 'true';

              // Focus first so subsequent events are considered user-initiated.
              textarea.focus();
              textarea.scrollTop = textarea.scrollHeight;

              if (isEditableDiv) {
                textarea.innerHTML = '';
                textarea.innerText = text;
                const range = document.createRange();
                range.selectNodeContents(textarea);
                range.collapse(false);
                const selection = window.getSelection();
                selection.removeAllRanges();
                selection.addRange(range);
              } else {
                // Clear then set value via native setter to bypass React value caching.
                const proto = window.HTMLTextAreaElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                setter.call(textarea, '');
                setter.call(textarea, text);
                textarea.selectionStart = textarea.selectionEnd = text.length;
              }

              // Fire a realistic sequence of input events. React listens to
              // native input/change events, so these must bubble properly.
              const events = [
                new FocusEvent('focus', { bubbles: true }),
                new KeyboardEvent('keydown', {
                  key: 'a', code: 'KeyA', bubbles: true, cancelable: true,
                }),
                new InputEvent('beforeinput', {
                  bubbles: true, cancelable: true,
                  inputType: 'insertText', data: text,
                }),
                new InputEvent('input', {
                  bubbles: true, inputType: 'insertText', data: text,
                }),
                new KeyboardEvent('keyup', {
                  key: 'a', code: 'KeyA', bubbles: true, cancelable: true,
                }),
                new Event('change', { bubbles: true }),
              ];
              events.forEach(event => textarea.dispatchEvent(event));

              // Some frameworks (e.g. DeepSeek's composer) only re-enable the
              // send button after a short tick; dispatch an extra input at the
              // next animation frame boundary to ensure state sync.
              requestAnimationFrame(() => {
                textarea.dispatchEvent(new InputEvent('input', {
                  bubbles: true, inputType: 'insertText', data: text,
                }));
              });

              return { value: isEditableDiv ? textarea.innerText : textarea.value };
            })()
            """.replace(
            "__COMPOSER_SELECTORS__", js_selector_list(self.platform.selectors["composer"])
        ).replace("__PROMPT__", json.dumps(prompt, ensure_ascii=False))
        result = await self._run_script(type_script)
        if isinstance(result, dict) and result.get("value") != prompt:
            raise RuntimeError(
                f"{self.platform.name}输入框未正确填入内容（期望：{prompt!r}，实际：{result.get('value')!r}）"
            )

    async def _submit_prompt(self, prompt: str) -> None:
        """Click the send button if ready; otherwise fall back to pressing Enter."""

        send_button_selectors = js_selector_list(self.platform.selectors["send_button"])
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
              button.scrollIntoView({ block: 'center', inline: 'center' });
              const rect = button.getBoundingClientRect();
              const x = rect.left + rect.width / 2;
              const y = rect.top + rect.height / 2;
              const opts = {
                bubbles: true, cancelable: true, view: window,
                clientX: x, clientY: y,
              };
              button.dispatchEvent(new PointerEvent('pointerdown', opts));
              button.dispatchEvent(new MouseEvent('mousedown', opts));
              button.dispatchEvent(new PointerEvent('pointerup', opts));
              button.dispatchEvent(new MouseEvent('mouseup', opts));
              button.dispatchEvent(new MouseEvent('click', opts));
              button.click();
              return true;
            })()
            """.replace("__SEND_BUTTON_SELECTORS__", send_button_selectors)
        composer_selectors = js_selector_list(self.platform.selectors["composer"])
        # 发送确认：输入框清空，或回答开始生成（发送按钮变成停止/加载态）。
        # 慢网络下豆包要等服务器确认才清空输入框，只看清空会把
        # "实际已发出"误判为发送失败，因此回答生成也算发送成功。
        message_accepted_script = r"""
            (() => {
              const visible = node => {
                if (!node) return false;
                const style = getComputedStyle(node);
                const box = node.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && box.width > 0 && box.height > 0;
              };
              const sendSelectors = __SEND_BUTTON_SELECTORS__;
              const sendButton = sendSelectors
                .map(selector => document.querySelector(selector))
                .find(node => node);
              const answerStarted = Boolean(sendButton) && (
                sendButton.getAttribute('data-loading') === 'true'
                || sendButton.getAttribute('aria-busy') === 'true'
                || (sendButton.getAttribute('aria-label') || '').includes('停止')
              );
              if (answerStarted) return true;
              const selectors = __COMPOSER_SELECTORS__;
              const textarea = selectors.flatMap(selector =>
                [...document.querySelectorAll(selector)]
              ).find(node => visible(node));
              if (!textarea) return false;
              if (textarea.isContentEditable || textarea.contentEditable === 'true') {
                return (textarea.innerText || '').trim() === '';
              }
              return textarea.value === '';
            })()
            """.replace("__SEND_BUTTON_SELECTORS__", send_button_selectors).replace(
            "__COMPOSER_SELECTORS__", composer_selectors
        )

        async def try_click_send() -> bool:
            ready = await self._wait_for_condition(
                send_ready_script,
                timeout=SEND_BUTTON_READY_TIMEOUT_SECONDS,
                interval=0.1,
            )
            if not ready:
                return False
            # Allow DeepSeek's React state to settle before clicking.
            await asyncio.sleep(0.2)
            sent = await self._run_script(click_send_script, timeout_retries=0)
            if not sent:
                return False
            return await self._wait_for_condition(
                message_accepted_script,
                timeout=5.0,
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
            sent = await self._run_script(enter_script, timeout_retries=0)
            if not sent:
                return False
            return await self._wait_for_condition(
                message_accepted_script,
                timeout=5.0,
                interval=0.1,
            )

        if await try_click_send():
            return
        if await try_press_enter():
            return
        # 慢网络兜底：再给最后一次确认机会，消息可能已被服务器接收，
        # 只是输入框清空/状态刷新来得晚。
        if await self._wait_for_condition(message_accepted_script, timeout=3.0, interval=0.2):
            return
        # 发送失败时区分场景：若页面正被人机验证遮罩覆盖（输入区被禁用），
        # 按验证码流程处理，让调度器暂停账号并提醒人工，而不是按普通失败重试。
        if await self._has_visual_captcha():
            self._mark_needs_captcha("发送失败时检测到人机验证")
            raise RuntimeError(f"{self.platform.name}发送失败：检测到人机验证，请处理验证后重试")
        raise RuntimeError(f"{self.platform.name}发送按钮尚未就绪，关键词没有发送")

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
            result = await self._run_script(build_captcha_detect_script(self.platform))
        except Exception:
            return {}
        if isinstance(result, dict):
            return result
        return {}

    def _clear_captcha_confirmation(self) -> None:
        """Clear the confirmed-captcha hold and log the recovery once."""

        if self._captcha_confirmed_until:
            LOGGER.info("账号 %s 人机验证遮罩已消失，恢复采集", self.account_id)
            self._captcha_confirmed_until = 0.0

    async def _has_visual_captcha(self) -> bool:
        # 保持期内直接沿用上次确认结论，不重新检测、不重复打日志。
        if time.monotonic() < self._captcha_confirmed_until:
            return True
        detected = await self._detect_captcha()
        hit = any(
            detected.get(key)
            for key in (
                "textMatch",
                "iframeMatch",
                "imageGridMatch",
                "dragHandleMatch",
                "fullscreenOverlayMatch",
            )
        )
        if not hit:
            self._clear_captcha_confirmation()
            return False

        # 强信号：文字/图片网格/滑块，基本可确认是真实人机验证。
        strong_signals = any(
            detected.get(key) for key in ("textMatch", "imageGridMatch", "dragHandleMatch")
        )
        if not strong_signals:
            # 弱信号：只有 iframe 或全屏遮罩（如豆包 verifycenter）。
            # 豆包会在某些请求后弹出 verifycenter iframe 数秒并自动通过，
            # 如果立即判定为人工验证会造成严重误报并暂停账号。
            # 这里等待 4 秒后复查，持续命中才确认。
            now = time.monotonic()
            if not self._captcha_weak_last_result and now - self._captcha_weak_last_check < 10.0:
                LOGGER.debug("账号 %s 弱验证码信号 10 秒内已排除，跳过复查", self.account_id)
                return False
            LOGGER.warning(
                "账号 %s 检测到弱人机验证信号（疑似静默验证），%.1f 秒后复查："
                "iframeMatch=%s fullscreenOverlayMatch=%s overlayInfo=%s "
                "matchedIframeSrcs=%s",
                self.account_id,
                CAPTCHA_WEAK_CONFIRM_DELAY_SECONDS,
                detected.get("iframeMatch"),
                detected.get("fullscreenOverlayMatch"),
                detected.get("overlayInfo"),
                detected.get("matchedIframeSrcs"),
            )
            await asyncio.sleep(CAPTCHA_WEAK_CONFIRM_DELAY_SECONDS)
            detected = await self._detect_captcha()
            hit = any(
                detected.get(key)
                for key in (
                    "textMatch",
                    "iframeMatch",
                    "imageGridMatch",
                    "dragHandleMatch",
                    "fullscreenOverlayMatch",
                )
            )
            self._captcha_weak_last_check = time.monotonic()
            self._captcha_weak_last_result = bool(hit)
            if not hit:
                self._clear_captcha_confirmation()
                LOGGER.info(
                    "账号 %s 弱人机验证信号已自动消失，判定为静默验证，不暂停账号",
                    self.account_id,
                )
                return False

        # 命中时输出命中的信号与证据（iframe src、遮罩节点、图片数），
        # 便于从日志直接判断是真实验证还是误报。
        if hit:
            self._captcha_confirmed_until = time.monotonic() + CAPTCHA_CONFIRMED_HOLD_SECONDS
        LOGGER.warning(
            "账号 %s 检测到疑似人机验证：textMatch=%s iframeMatch=%s "
            "imageGridMatch=%s dragHandleMatch=%s fullscreenOverlayMatch=%s "
            "matchedIframeSrcs=%s overlayInfo=%s imageGridMaxCount=%s imageGridInfo=%s "
            "textMatchSource=%r",
            self.account_id,
            detected.get("textMatch"),
            detected.get("iframeMatch"),
            detected.get("imageGridMatch"),
            detected.get("dragHandleMatch"),
            detected.get("fullscreenOverlayMatch"),
            detected.get("matchedIframeSrcs"),
            detected.get("overlayInfo"),
            detected.get("imageGridMaxCount"),
            detected.get("imageGridInfo"),
            detected.get("textMatchSource"),
        )
        return hit

    async def _debug_snapshot(self) -> dict[str, Any]:
        """Capture a lightweight snapshot of the current page for debugging."""

        reference_selector = js_string(", ".join(self.platform.selectors["reference_rows"]))
        expand_selector = js_string(
            ", ".join(
                s for s in self.platform.selectors["reference_expand"] if not s.startswith("xpath=")
            )
        )
        more_text = js_string(self.platform.selectors["reference_more_text"])
        send_selector = js_string(", ".join(self.platform.selectors["send_button"]))
        composer_selector = js_string(", ".join(self.platform.selectors["composer"]))
        summary_pattern = js_regex_pattern(self.platform.reference_summary_pattern)
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
            if self._started:
                page_login = await self._run_script(_build_login_state_script(self.platform))
            else:
                page_login = {}
            dom_logged_in = (
                bool(page_login.get("loggedIn")) if isinstance(page_login, dict) else False
            )
            if isinstance(page_login, dict):
                # 文本检测（body.innerText）之外，再跑结构化检测（iframe src /
                # 全屏遮罩 DOM）。字节验证中心的验证内容在跨域 iframe 里，
                # 主文档文本完全不可见，纯文本检测永远漏掉它。
                visual_captcha = await self._has_visual_captcha()
                self._needs_captcha = bool(page_login.get("hasCaptcha")) or visual_captcha
            page_ready = (
                bool(page_login.get("ready") and page_login.get("hasComposer"))
                if isinstance(page_login, dict)
                else False
            )
            logged_in = bool(cookie_names & self.platform.session_cookie_names) or dom_logged_in
            chat_ready = self._started and logged_in and page_ready and not self._needs_captcha

            # Build a human-readable reason when the account is not chat-ready.
            chat_ready_reason = ""
            if not chat_ready:
                if not self._started:
                    chat_ready_reason = "账号未启动"
                elif self._needs_captcha:
                    chat_ready_reason = "需要处理人机验证"
                elif not logged_in:
                    chat_ready_reason = "账号未登录"
                elif not page_ready:
                    if isinstance(page_login, dict):
                        if not page_login.get("ready"):
                            chat_ready_reason = "页面仍在加载中"
                        elif not page_login.get("hasComposer"):
                            chat_ready_reason = "未检测到聊天输入框"
                        else:
                            chat_ready_reason = "页面未就绪"
                    else:
                        chat_ready_reason = "页面状态读取失败"
                else:
                    chat_ready_reason = "未知原因"

            return {
                "account_id": self.account_id,
                "started": self._started,
                "logged_in": logged_in,
                "browser": "ready" if self._started else "not_started",
                "has_ms_token": "msToken" in cookie_names,
                "chat_ready": chat_ready,
                "chat_ready_reason": chat_ready_reason,
                "needs_captcha": self._needs_captcha,
                "last_error_code": self._last_error_code,
                "consecutive_failures": self._consecutive_failures,
                "page_url": state.get("page_url", ""),
                "login_source": (
                    "cookie"
                    if cookie_names & self.platform.session_cookie_names
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
        self._captcha_weak_last_check = 0.0
        self._captcha_weak_last_result = False
        self._captcha_confirmed_until = 0.0

    async def screenshot(self) -> bytes:
        if not self._started:
            raise BrowserUnavailableError("内置浏览器标签页尚未打开")
        return await self.bridge.screenshot(self.account_id)

    async def import_cookies(self, cookie_text: str) -> int:
        if not self._started:
            await self.start()
        records = parse_cookie_records(cookie_text, self.platform.cookie_match_domains())
        if records:
            await self.bridge.set_cookies(self.account_id, records)
            await self.bridge.navigate(self.account_id, self.platform.chat_url)
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
                # 发送前先检测人机验证遮罩：验证弹出时豆包会禁用输入区，
                # 继续填词/点发送只会撞墙超时，还会让任务被误判为普通失败。
                if await self._has_visual_captcha():
                    self._mark_needs_captcha("发送前检测到人机验证")
                    raise RuntimeError(
                        f"{self.platform.name}检测到人机验证，关键词未发送，请处理验证后重试"
                    )
                if self.platform.response_capture_url_patterns:
                    capture_script = _build_capture_script(
                        self.platform.response_capture_url_patterns
                    )
                    await self._run_script(capture_script)
                await self._type_prompt(prompt)
                try:
                    await self._submit_prompt(prompt)
                except RuntimeError:
                    if self._needs_captcha:
                        raise
                    # 慢网络或新对话未水合时 React 状态可能没跟上（文字已填入
                    # 但按钮一直禁用），重新填词再试一轮，而不是直接判失败。
                    LOGGER.info("账号 %s 发送未被确认，重新填词后重试一次", self.account_id)
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
                send_button_selectors = js_selector_list(self.platform.selectors["send_button"])
                reference_summary_pattern = js_regex_pattern(
                    self.platform.reference_summary_pattern
                )
                reference_rows_selector = js_string(
                    ", ".join(self.platform.selectors["reference_rows"])
                )
                captcha_pattern = js_regex_alternation(self.platform.selectors["captcha_patterns"])
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
                                  let referenceReady = false;
                                  if (referencePattern) {
                                    referenceReady = new RegExp(referencePattern).test(body);
                                  } else {
                                    const selector = __REFERENCE_ROWS_SELECTOR__;
                                    referenceReady = document.querySelectorAll(selector).length > 0;
                                  }
                                  const captchaPattern = new RegExp(__CAPTCHA_PATTERN__);
                                  // 只扫弹层候选节点的短文本，避免回答正文里的
                                  // “验证码/身份验证”等业务词汇误报。
                                  let captcha = false;
                                  const scopeSel = __CAPTCHA_TEXT_SCOPE__;
                                  for (const node of document.querySelectorAll(scopeSel)) {
                                    const nodeBox = node.getBoundingClientRect();
                                    if (nodeBox.width <= 0 || nodeBox.height <= 0) continue;
                                    const rawText = node.innerText || '';
                                    const nodeText = rawText.replace(/\s+/g, ' ').trim();
                                    const tooLong = nodeText.length > __CAPTCHA_TEXT_MAX_LENGTH__;
                                    if (!nodeText || tooLong) continue;
                                    if (captchaPattern.test(nodeText)) {
                                      captcha = true;
                                      break;
                                    }
                                  }
                                  return {
                                    loading,
                                    captcha,
                                    referenceReady
                                  };
                                })()
                                """.replace("__SEND_BUTTON_SELECTORS__", send_button_selectors)
                                .replace("__REFERENCE_SUMMARY_PATTERN__", reference_summary_pattern)
                                .replace("__REFERENCE_ROWS_SELECTOR__", reference_rows_selector)
                                .replace("__CAPTCHA_PATTERN__", captcha_pattern)
                                .replace(
                                    "__CAPTCHA_TEXT_SCOPE__",
                                    _captcha_text_scope_selectors(self.platform),
                                )
                                .replace(
                                    "__CAPTCHA_TEXT_MAX_LENGTH__",
                                    str(CAPTCHA_TEXT_MAX_LENGTH),
                                )
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
                    raise TimeoutError(f"等待{self.platform.name}回答超时")
                fragments: list[str] = []
                for event in capture.get("events", []):
                    _collect_text(event, fragments)
                text = _merge_text_fragments(fragments) or f"{self.platform.name}回答完成"
                references: list[dict[str, str]] = []
                expected = 0
                if collect_thinking_references:
                    reference_summary_pattern = js_regex_pattern(
                        self.platform.reference_summary_pattern
                    )
                    reference_rows_selector = js_string(
                        ", ".join(self.platform.selectors["reference_rows"])
                    )
                    reference_appear_script = r"""
                        (() => {
                          const pattern = __REFERENCE_SUMMARY_PATTERN__;
                          if (pattern && new RegExp(pattern).test(document.body.innerText || '')) {
                            return true;
                          }
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
        rows = await self._run_script(build_reference_rows_script(self.platform))
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
        generic = await self._run_script(build_reference_generic_script(self.platform))
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

    async def _extract_references_from_script(
        self,
        reference_callback: Callable[[dict[str, str]], Any] | None = None,
    ) -> tuple[list[dict[str, str]], int]:
        """Extract references using a platform-specific DOM script.

        Used by DeepSeek and similar platforms where source cards are rendered
        directly in the answer area.
        """
        expand_script = r"""
            (() => {
              const visible = node => {
                if (!node) return false;
                const style = getComputedStyle(node);
                const box = node.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && box.width > 0 && box.height > 0;
              };
              // DeepSeek hides source cards behind a summary row such as
              // "搜索到 20 个网页" or just "20 个网页". Find and click it.
              const candidates = [...document.querySelectorAll('*')].filter(node => {
                if (!visible(node)) return false;
                const text = (node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
                return /\d+\s*个网页/.test(text) && text.length < 80;
              });
              if (!candidates.length) return false;
              // Prefer the deepest (most specific) candidate.
              const label = candidates[candidates.length - 1];
              const clickable = label.closest(
                'button, [role="button"], div[class*="cursor-pointer"], div'
              ) || label;
              clickable.scrollIntoView({ block: 'center' });
              clickable.click();
              return true;
            })()
            """
        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for attempt in range(20):
            if attempt < 3:
                await self._run_script(expand_script)
            raw = await self._run_script(self.platform.extract_references_script)
            if isinstance(raw, list):
                for item in raw:
                    link = str(item.get("link") or item.get("href") or "").strip()
                    title = str(item.get("title", "")).strip()
                    if not link or link in seen:
                        continue
                    seen.add(link)
                    row = {
                        "link": link,
                        "title": title,
                        "platform": platform_for_url(link),
                        "platform_type": category_for_url(link),
                    }
                    rows.append(row)
                    if reference_callback is not None:
                        callback_result = reference_callback(row)
                        if inspect.isawaitable(callback_result):
                            await callback_result
            if rows:
                break
            await asyncio.sleep(REFERENCE_POLL_INTERVAL_SECONDS)
        return rows, len(rows)

    async def _expand_references(
        self,
        reference_callback: Callable[[dict[str, str]], Any] | None = None,
    ) -> tuple[list[dict[str, str]], int]:
        # DeepSeek-style platforms expose source cards directly in the DOM.
        if self.platform.extract_references_script:
            return await self._extract_references_from_script(reference_callback)

        reference_summary_pattern = js_regex_pattern(self.platform.reference_summary_pattern)
        reference_rows_selector = js_string(", ".join(self.platform.selectors["reference_rows"]))
        reference_expand_selectors = js_selector_list(self.platform.selectors["reference_expand"])
        more_references_text = js_string(self.platform.selectors["reference_more_text"])
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
        expected = (await self._run_script(expected_script, timeout_retries=0)) or 0
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
                timeout_retries=0,
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
            # 豆包 2026-09 起参考资料最多只渲染前 15 条（有时滚动后能多加载
            # 几条），剩余条目页面上不存在，无法采集。此时不再判任务失败，
            # 有多少算多少；只有一条都没识别到才认为展开真的失败。
            if rows:
                LOGGER.info(
                    "账号 %s 页面标明 %d 篇参考资料，豆包最多展示前若干条，"
                    "实际采集 %d 篇，剩余忽略",
                    self.account_id,
                    int(expected),
                    len(rows),
                )
            else:
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
                    f"参考资料展开失败：页面标明 {expected} 篇，实际识别到 0 篇。"
                    f"页面摘要检测={snapshot.get('hasSummary', 'unknown')}，"
                    f"参考行节点数={snapshot.get('referenceRows', 'unknown')}，"
                    f"展开按钮数={snapshot.get('expandButtons', 'unknown')}，"
                    f"页面预览：{preview[:200]}"
                )
        return rows, int(expected)
