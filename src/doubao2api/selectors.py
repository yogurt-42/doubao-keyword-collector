from __future__ import annotations

"""Centralized, configurable DOM selectors and patterns.

The goal is to decouple the automation scripts from the exact Doubao page
structure.  When Doubao changes a class name, data-testid, or button label,
most fixes should only require updating this file.
"""

# Reference summary text pattern used across both Playwright and Qt clients.
REFERENCE_SUMMARY_PATTERN = (
    r"(?:搜索\s*\d+\s*个关键词[\s\S]*?)?参考[了]?\s*(\d+)\s*篇(?:资料|网页|来源|参考)?"
)
MORE_REFERENCES_TEXT = "展开更多"

# Playwright-specific absolute XPath fallback for the thinking box expand button.
THINKING_EXPAND_XPATH = (
    '//*[@id="root"]/div/div/div/div[2]/div/div/div[2]/div/div[1]/div/div/div[1]/div/div/div[1]'
)

SELECTORS = {
    # "New conversation" button in the left sidebar.
    "new_chat": {
        "text": "新对话",
        "roles": ["span", "button", '[role="button"]', "div"],
    },
    # Chat input textarea. Tried in order.
    # Doubao new layout uses a contenteditable div instead of a textarea.
    "composer": [
        "textarea.semi-input-textarea",
        'div[contenteditable="true"]',
        '[contenteditable]',
        '[role="textbox"]',
        'textarea',
    ],
    # Send button.  Tried in order until one is found and enabled.
    "send_button": [
        "#flow-end-msg-send",
        '[data-testid="send-button"]',
        'button[aria-label*="发送"]',
    ],
    # Reference / thinking result anchor rows.
    "reference_rows": [
        'a[data-tool-call-item-id*="-result-"]',
        'a[data-thinking-box-tool-call="true"]',
    ],
    # Title node inside a reference row.
    "reference_title": ".truncate",
    # Source/platform node inside a reference row.
    "reference_source": '[class*="platform"],[class*="source"],[class*="site"]',
    # Buttons/areas that expand the thinking box / reference summary.
    # New Doubao Tailwind layout uses a cursor-pointer summary row;
    # old layout used explicit collapse buttons.
    "reference_expand": [
        f"xpath={THINKING_EXPAND_XPATH}",
        '[data-testid="collapse_button"]',
        '[class*="collapse-collapse-button"]',
        '[data-copy-ignore][class*="cursor-pointer"]',  # precise new-layout summary row
        '[class*="cursor-pointer"]',                       # new-layout fallback
        '[aria-label*="参考"]',                             # accessible label fallback
    ],
    # Text of the "load more references" button.
    "reference_more_text": MORE_REFERENCES_TEXT,
    # Login detection: controls that indicate the user is not logged in.
    "login_controls": {
        "selectors": ["button", "a", '[role="button"]'],
        "text_patterns": [r"^登录$", r"^注册$", r"^登录/注册$"],
        "aria_patterns": [r"^登录$", r"^login$"],
    },
    # Logged-in user menu / avatar (new layout strong positive signal).
    "user_menu_trigger": [
        '[data-slot="dropdown-menu-trigger"]',
        "img.rounded-full",
        '[class*="avatar"]',
    ],
    "logout_text": "退出登录",
    "user_name_indicator": [
        '[data-slot="dropdown-menu-trigger"] span',
        "img.rounded-full + div span",
    ],
    # History detection: presence means the account has previous chats.
    "history_indicator": {
        "text": "历史对话",
        "link_selector": 'a[href*="/chat/"]',
        "min_links": 2,
    },
    # Captcha detection patterns (matched against body text).
    "captcha_patterns": [
        "验证码",
        "安全验证",
        "安全校验",
        "人机验证",
        "点击验证",
        "滑动验证",
        "拖拽到下方",
        "请选择所有符合上下文描述的图片",
        "请完成验证",
        "身份验证",
        "校验码",
        "拖动",
        "拖拽",
        "滑块",
    ],
}

# Additional captcha detection heuristics used by the desktop client.
# These are matched against iframe URLs and visible DOM structures, which
# helps catch image-grid or drag-and-drop challenges that do not expose
# descriptive text in document.body.innerText.
CAPTCHA_IFRAME_PATTERNS = [
    "captcha",
    "verify",
    "verification",
    "geetest",
    "turing",
    "sec",
    "hcaptcha",
    "recaptcha",
    "slider",
]

CAPTCHA_DOM_SELECTORS = [
    '[class*="captcha"]',
    '[class*="verify"]',
    '[class*="verification"]',
    '[class*="geetest"]',
    '[class*="turing"]',
    '[class*="risk"]',
    '[class*="slider"]',
    '[class*="drag"]',
]


def js_selector_list(selectors: list[str]) -> str:
    """Return a JSON-escaped JS array literal from a list of CSS selectors."""

    import json

    return json.dumps(selectors, ensure_ascii=False)


def js_regex_pattern(pattern: str) -> str:
    """Return a JSON-escaped regex string literal."""

    import json

    return json.dumps(pattern, ensure_ascii=False)


def js_string(value: str) -> str:
    """Return a JSON-escaped plain string literal."""

    import json

    return json.dumps(value, ensure_ascii=False)


def js_regex_alternation(patterns: list[str]) -> str:
    """Return a JSON-escaped regex string that matches any of the patterns."""

    import json

    return json.dumps("|".join(patterns), ensure_ascii=False)
