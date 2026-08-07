from pathlib import Path

from doubao2api.research_store import ResearchStore


def _seed_store(store: ResearchStore) -> tuple[str, dict[str, str]]:
    job = store.create_job(
        name="长尾测试",
        keywords=["冷库", "水泵", "弯管", "电动滚筒", "涂装"],
        account_ids=["acc-1"],
        prompt_template="{keyword}",
        scheduled_at=None,
        interval_seconds=0,
        account_cooldown_seconds=0,
        max_attempts=1,
    )
    job_id = job["id"]
    tasks = list(store.due_tasks())
    for task in tasks:
        store.mark_task_running(task["id"], "acc-1")
    return job_id, {task["keyword"]: task["id"] for task in tasks}


def _add_result(store: ResearchStore, task_id: str, platform: str, link: str) -> None:
    store.add_result(
        task_id,
        item={"link": link, "platform": platform, "title": f"{platform} 来源"},
        account_id="acc-1",
    )


def test_long_tail_analysis_defaults(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "research.sqlite3")
    job_id, tasks = _seed_store(store)

    # 博客园：覆盖 4 个关键词，每个 1 条 -> 目标长尾
    for keyword in ["冷库", "水泵", "弯管", "电动滚筒"]:
        _add_result(
            store,
            tasks[keyword],
            "博客园",
            f"https://www.cnblogs.com/x/{keyword}",
        )

    # 仪器信息网：覆盖 3 个关键词，每个 1 条 -> 目标长尾
    for keyword in ["弯管", "电动滚筒", "涂装"]:
        _add_result(
            store,
            tasks[keyword],
            "仪器信息网",
            f"https://www.instrument.com.cn/x/{keyword}",
        )

    # 抖音：覆盖 3 个关键词，每个 10 条 -> 高频头部
    for index, keyword in enumerate(["冷库", "水泵", "弯管"]):
        for sub in range(10):
            _add_result(
                store,
                tasks[keyword],
                "抖音",
                f"https://www.douyin.com/video/{index}-{sub}",
            )

    # 搜狐：仅 1 个关键词 2 条 -> 僵尸信源
    for sub in range(2):
        _add_result(
            store,
            tasks["涂装"],
            "搜狐",
            f"https://www.sohu.com/a/{sub}",
        )

    analysis = store.long_tail_analysis(job_id=job_id)

    assert analysis["params"]["split_mode"] == "threshold"
    assert analysis["summary"]["total_records"] == 39
    assert analysis["summary"]["platform_count"] == 4
    assert analysis["summary"]["target_count"] == 2

    target_names = {p["platform"] for p in analysis["target_long_tail"]}
    assert target_names == {"博客园", "仪器信息网"}

    by_platform = {p["platform"]: p for p in analysis["platforms"]}
    assert by_platform["博客园"]["freq"] == 4
    assert by_platform["博客园"]["breadth"] == 4
    assert by_platform["博客园"]["density"] == 1.0
    assert by_platform["博客园"]["quadrant"] == "垂直长尾宝藏"

    assert by_platform["抖音"]["quadrant"] == "头部主流媒体"
    assert by_platform["搜狐"]["quadrant"] == "一次性/僵尸信源"

    counts = analysis["summary"]["quadrant_counts"]
    assert counts["垂直长尾宝藏"] == 2
    assert counts["头部主流媒体"] == 1
    assert counts["一次性/僵尸信源"] == 1


def test_long_tail_analysis_fake_noise(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "research.sqlite3")
    job_id, tasks = _seed_store(store)

    # 1688：覆盖 3 个关键词，每个 20 条 -> density=20
    # 把频次阈值调到 100 后，变为虚假长尾噪声
    for keyword in ["冷库", "水泵", "弯管"]:
        for sub in range(20):
            _add_result(
                store,
                tasks[keyword],
                "1688",
                f"https://www.1688.com/offer/{keyword}-{sub}",
            )

    analysis = store.long_tail_analysis(
        job_id=job_id,
        freq_threshold=100,
        noise_density_threshold=20,
    )

    platform = next(p for p in analysis["platforms"] if p["platform"] == "1688")
    assert platform["freq"] == 60
    assert platform["breadth"] == 3
    assert platform["density"] == 20.0
    assert platform["quadrant"] == "虚假长尾(噪声)"
    assert analysis["summary"]["noise_count"] == 1


def test_long_tail_analysis_median_mode(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "research.sqlite3")
    job_id, tasks = _seed_store(store)

    # 平台 A：高频高广度
    for keyword in ["冷库", "水泵", "弯管", "电动滚筒", "涂装"]:
        for sub in range(5):
            _add_result(
                store,
                tasks[keyword],
                "平台A",
                f"https://a.com/{keyword}-{sub}",
            )

    # 平台 B：低频低广度
    _add_result(store, tasks["冷库"], "平台B", "https://b.com/1")

    analysis = store.long_tail_analysis(job_id=job_id, split_mode="median")

    by_platform = {p["platform"]: p for p in analysis["platforms"]}
    # 两个平台时，中位数等于较高值，因此平台A也满足高广度/高频
    assert by_platform["平台A"]["quadrant"] == "头部主流媒体"
    assert by_platform["平台B"]["quadrant"] == "一次性/僵尸信源"
    assert analysis["params"]["split_mode"] == "median"


def test_long_tail_analysis_representative_link_and_domain(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "research.sqlite3")
    job_id, tasks = _seed_store(store)

    # 同平台同链接出现多次，代表性链接应选出现次数最多的那条
    _add_result(store, tasks["冷库"], "博客园", "https://www.cnblogs.com/popular")
    _add_result(store, tasks["水泵"], "博客园", "https://www.cnblogs.com/popular")
    _add_result(store, tasks["弯管"], "博客园", "https://www.cnblogs.com/rare")

    analysis = store.long_tail_analysis(job_id=job_id)
    platform = next(p for p in analysis["platforms"] if p["platform"] == "博客园")
    assert platform["representative_link"] == "https://www.cnblogs.com/popular"
    assert platform["domain"] == "www.cnblogs.com"
    assert set(platform["keywords_sample"]) == {"冷库", "水泵", "弯管"}
