from __future__ import annotations

from .base import AIPlatform

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
        "[contenteditable]",
        '[role="textbox"]',
        "textarea",
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
        '[data-copy-ignore][class*="cursor-pointer"]',
        '[class*="cursor-pointer"]',
        '[aria-label*="参考"]',
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

DOUBAO_PLATFORM = AIPlatform(
    key="doubao",
    name="豆包",
    chat_url="https://www.doubao.com/chat/",
    session_cookie_names=frozenset({"sessionid", "sessionid_ss"}),
    selectors=SELECTORS,
    reference_summary_pattern=REFERENCE_SUMMARY_PATTERN,
    more_references_text=MORE_REFERENCES_TEXT,
    ignored_hosts=frozenset(
        {
            "www.doubao.com",
            "doubao.com",
            "lf-flow-web-cdn.doubao.com",
        }
    ),
    cookie_domains=frozenset({"doubao.com", "www.doubao.com", ".doubao.com"}),
    chat_models=["doubao", "doubao-pro", "doubao-think", "doubao-expert"],
    response_capture_url_patterns=["/chat/completion"],
    captcha_patterns=SELECTORS["captcha_patterns"],
    captcha_iframe_patterns=CAPTCHA_IFRAME_PATTERNS,
    captcha_dom_selectors=CAPTCHA_DOM_SELECTORS,
)
