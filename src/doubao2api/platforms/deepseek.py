from __future__ import annotations

from .base import AIPlatform

# DeepSeek selectors measured from chat.deepseek.com.
# Class names are obfuscated and may change; the order below is from most specific
# fallback to more generic fallbacks.
SELECTORS: dict[str, object] = {
    "new_chat": {
        "text": "New Chat",
        "roles": ["button", "a", '[role="button"]', "div"],
    },
    "composer": [
        "textarea._27c9245",
        'textarea[placeholder*="DeepSeek"]',
        "textarea",
        'div[contenteditable="true"]',
        "[contenteditable]",
        '[role="textbox"]',
    ],
    "send_button": [
        'div[role="button"].ds-button--primary.ds-button--filled.ds-button--circle:not(.ds-button--disabled)',
        'div[role="button"].ds-button--primary:not(.ds-button--disabled)',
        '[data-testid="send-button"]',
    ],
    # DeepSeek source cards are anchor tags under the answer sources container.
    "reference_rows": [
        "div._223dd7b a.c64652fe",
        "a.c64652fe",
        'a[rel="noreferrer"]',
    ],
    "reference_title": "._8d3001c.search-view-card__title",
    "reference_source": "._9efb180",
    # Sources summary row that expands/collapses the source cards.
    "reference_expand": [
        "div.f93f59e4",
        "span._669a677",
    ],
    "reference_more_text": "",
    "login_controls": {
        "selectors": ["button", "a", '[role="button"]'],
        "text_patterns": [r"^Sign in$", r"^Log in$", r"^Login$", r"^登录$", r"^登录/注册$"],
        "aria_patterns": [r"^Sign in$", r"^Log in$", r"^登录$"],
    },
    "user_menu_trigger": [
        'img[src*="user-avatar"]',
        '[class*="user-avatar"]',
        '[class*="avatar"]',
        "img.rounded-full",
    ],
    "logout_text": "退出登录",
    "user_name_indicator": [
        "._9d8da05",
    ],
    "history_indicator": {
        "text": "History",
        "link_selector": 'a[href*="/chat/"]',
        "min_links": 2,
    },
    "captcha_patterns": [
        "captcha",
        "verify",
        "verification",
        "human verification",
        "robot",
        "Cloudflare",
        "安全验证",
    ],
}

CAPTCHA_IFRAME_PATTERNS = [
    "captcha",
    "verify",
    "verification",
    "hcaptcha",
    "recaptcha",
    "cloudflare",
    "turnstile",
]

CAPTCHA_DOM_SELECTORS = [
    '[class*="captcha"]',
    '[class*="verify"]',
    '[class*="verification"]',
    '[class*="hcaptcha"]',
    '[class*="recaptcha"]',
    '[class*="turnstile"]',
]

# Script injected into the page to extract source cards for DeepSeek.
# It returns a list of {title, link, platform, date} objects.
DEEPSEEK_EXTRACT_SOURCES_SCRIPT = r"""
(() => {
  const tidy = value => (value || '').replace(/\s+/g, ' ').trim();
  const container = document.querySelector('div._223dd7b');
  if (!container) return [];
  const anchors = container.querySelectorAll('a.c64652fe');
  const seen = new Set();
  const results = [];
  for (const anchor of anchors) {
    const href = anchor.getAttribute('href');
    if (!href || seen.has(href)) continue;
    seen.add(href);
    const titleNode = anchor.querySelector('._8d3001c.search-view-card__title');
    const siteNode = anchor.querySelector('._9efb180');
    const dateNode = anchor.querySelector('.d79666ac');
    results.push({
      title: titleNode ? tidy(titleNode.innerText) : '',
      link: href,
      platform: siteNode ? tidy(siteNode.innerText) : '',
      date: dateNode ? tidy(dateNode.innerText) : '',
    });
  }
  return results;
})()
"""

DEEPSEEK_PLATFORM = AIPlatform(
    key="deepseek",
    name="DeepSeek",
    chat_url="https://chat.deepseek.com/",
    session_cookie_names=frozenset(),  # To be filled after cookie measurement.
    selectors=SELECTORS,
    reference_summary_pattern="",
    more_references_text="",
    ignored_hosts=frozenset(
        {
            "www.deepseek.com",
            "deepseek.com",
            "chat.deepseek.com",
            "cdn.deepseek.com",
        }
    ),
    cookie_domains=frozenset({"deepseek.com", "www.deepseek.com", ".deepseek.com"}),
    chat_models=["deepseek-chat", "deepseek-reasoner"],
    response_capture_url_patterns=[],  # To be filled after network measurement.
    captcha_patterns=SELECTORS["captcha_patterns"],
    captcha_iframe_patterns=CAPTCHA_IFRAME_PATTERNS,
    captcha_dom_selectors=CAPTCHA_DOM_SELECTORS,
    extract_references_script=DEEPSEEK_EXTRACT_SOURCES_SCRIPT,
)
