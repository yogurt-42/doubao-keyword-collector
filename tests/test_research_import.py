from doubao2api.research_import import parse_keyword_file, preview_keyword_file


def test_csv_keyword_preview() -> None:
    data = "关键词,分组\n牙膏推荐,口腔\n装修公司,家居\n".encode()

    assert parse_keyword_file("关键词.csv", data) == ["牙膏推荐", "装修公司"]
    assert preview_keyword_file("关键词.csv", data) == [
        ["关键词", "分组"],
        ["牙膏推荐", "口腔"],
        ["装修公司", "家居"],
    ]
