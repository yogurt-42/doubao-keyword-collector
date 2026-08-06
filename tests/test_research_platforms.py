from doubao2api.research_platforms import (
    PLATFORM_CATEGORIES,
    category_for_url,
    entry_for_url,
    platform_category,
    platform_for_url,
    to_js_known,
)


def test_platform_categories_defined() -> None:
    assert "综合新闻门户" in PLATFORM_CATEGORIES
    assert "百科/Wiki" in PLATFORM_CATEGORIES


def test_specific_domains_match_before_generic() -> None:
    assert platform_for_url("https://baike.baidu.com/item/1") == "百度百科"
    assert platform_for_url("https://www.baidu.com/s?wd=1") == "百度"
    assert platform_for_url("https://mp.weixin.qq.com/s/1") == "微信公众号"
    assert platform_for_url("https://weixin.qq.com/1") == "微信"


def test_category_returned() -> None:
    assert category_for_url("https://zhihu.com/question/1") == "博客/技术社区"
    assert category_for_url("https://taobao.com/item/1") == "分类信息/黄页/电商"
    assert category_for_url("https://unknown-domain.example.com/1") == ""


def test_entry_for_url() -> None:
    entry = entry_for_url("https://www.gov.cn/zhengce/1.htm")
    assert entry == {"name": "政府网站", "category": "政府/官方机构"}


def test_platform_category_by_name() -> None:
    assert platform_category("知乎") == "博客/技术社区"
    assert platform_category("不存在") == ""


def test_to_js_known_is_json_array() -> None:
    data = to_js_known()
    assert data.startswith("[")
    assert "微信公众号" in data
