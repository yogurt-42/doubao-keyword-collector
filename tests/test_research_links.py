from doubao2api.research_links import (
    extract_research_links,
    normalize_thinking_references,
    platform_for_url,
)


def test_platform_mapping() -> None:
    assert platform_for_url("https://mp.weixin.qq.com/s/demo") == "微信公众号"
    assert platform_for_url("https://zhuanlan.zhihu.com/p/1") == "知乎"
    assert platform_for_url("https://example.com/article") == "example.com"
    assert platform_for_url("https://m.to8to.com/yezhu/z1.html") == "土巴兔"
    assert platform_for_url("https://m.toutiao.com/group/1") == "今日头条"
    assert platform_for_url("https://k.sina.cn/article_1.html") == "新浪"
    assert platform_for_url("https://yanjiao.jiaju.sina.cn/zixun/1.shtml") == "新浪家居"
    assert platform_for_url("https://www.cnblogs.com/demo/p/1") == "博客园"
    assert platform_for_url("https://www.iesdouyin.com/share/video/1") == "抖音"
    assert platform_for_url("https://m.ctrip.com/webapp/hotel/") == "携程"
    assert platform_for_url("https://docs.meshy.ai/en/article") == "Meshy"
    assert platform_for_url("https://post.m.smzdm.com/p/1") == "什么值得买"
    assert platform_for_url("https://pmc.ncbi.nlm.nih.gov/articles/1") == "NCBI"


def test_domain_mapping_overrides_generic_page_label() -> None:
    rows = normalize_thinking_references(
        [
            {
                "title": "装修资料",
                "platform": "企业博客",
                "link": "https://www.cnblogs.com/demo/p/1",
            },
            {
                "title": "装修资料",
                "platform": "m.to8to.com",
                "link": "https://m.to8to.com/yezhu/z1.html",
            },
        ]
    )

    assert [row["platform"] for row in rows] == ["博客园", "土巴兔"]
    assert [row["platform_type"] for row in rows] == ["博客/技术社区", "生活/房产/汽车门户"]


def test_only_normalizes_explicit_thinking_references() -> None:
    rows = normalize_thinking_references(
        [
            {
                "title": "资料一 - 示例资料平台",
                "platform": "示例资料平台",
                "link": "https://example.com/a#section",
            },
            {"title": "重复", "link": "https://example.com/a"},
            {"title": "豆包内部页", "link": "https://www.doubao.com/chat/123"},
        ]
    )
    assert rows == [
        {
            "title": "资料一 - 示例资料平台",
            "link": "https://example.com/a",
            "platform": "example.com",
            "platform_type": "",
        }
    ]


def test_unknown_domain_uses_host_instead_of_noisy_page_label() -> None:
    rows = normalize_thinking_references(
        [
            {
                "title": "Free and Zero Skills Required",
                "platform": "Free and Zero Skills Required",
                "link": "https://buildcad.ai/blog/example",
            }
        ]
    )
    assert rows[0]["platform"] == "buildcad.ai"
    assert rows[0]["platform_type"] == ""


def test_extract_research_links_includes_platform_type() -> None:
    links = extract_research_links(
        "Check https://zhihu.com/question/1 and https://taobao.com/item/1"
    )
    assert len(links) == 2
    by_platform = {row["platform"]: row["platform_type"] for row in links}
    assert by_platform["知乎"] == "博客/技术社区"
    assert by_platform["淘宝"] == "分类信息/黄页/电商"
