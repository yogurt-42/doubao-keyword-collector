# AI 可读：豆包关键词资料采集器技术/用途文档

> 本文档面向 AI 助手/开发者，用于快速理解项目结构、核心数据流、关键类和扩展方式。阅读后应能回答“这个文件是干什么的”“改动会影响哪里”“在哪里加新功能”。

---

## 1. 项目定位

**豆包关键词资料采集器（开源版）** 是一个本地-first 的非官方关键词调研工具。

- 用户在桌面软件中管理多个豆包账号（每个账号独立浏览器数据目录）。
- 批量向豆包发送关键词，自动点击“新对话 → 输入 → 发送”。
- 等待回答完成后，自动展开“搜索 X 个关键词 / 参考 X 篇资料”区域，采集参考资料链接。
- 将链接、平台、类型、日期等存入本地 SQLite，支持筛选、Excel 导出、信源对比。
- 不保存 AI 回答正文，只采集思考过程引用区的链接。

---

## 2. 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python >= 3.10 |
| 桌面 GUI | PySide6（可选依赖 `[desktop]`） |
| 内嵌浏览器 | PySide6.QtWebEngine（桌面模式） |
| 可选浏览器 | Playwright（`browser_client.py`，用于无界面/服务端模式） |
| Web 后台 | FastAPI + Uvicorn |
| 数据存储 | SQLite（本地文件） |
| 配置存储 | JSON 文件 `settings.json` |
| Excel 导出/导入 | openpyxl |
| 构建 | hatchling / PyInstaller |

---

## 3. 目录结构

```
D:\ai-source-capturer\doubao-keyword-collector
├── packaging/                 # PyInstaller 打包相关
│   ├── AI信源采集工具.spec
│   └── app-icon.ico
├── src/doubao2api/            # 主要源码
│   ├── __main__.py            # 服务端入口：doubao-account-manager
│   ├── windows_entry.py       # 桌面 EXE 入口：doubao-keyword-collector
│   ├── account_manager.py     # 账号池：创建、启动、停止、快照
│   ├── browser_client.py      # Playwright 浏览器客户端
│   ├── cookie_utils.py        # Cookie 解析与域名校验
│   ├── desktop.py             # PySide 主窗口、标签页、浏览器桥
│   ├── embedded_browser_client.py  # Qt WebEngine 浏览器客户端（桌面核心）
│   ├── models.py              # Pydantic 请求模型
│   ├── native_dashboard.py    # 桌面管理界面（PySide6 实现）
│   ├── platform_editor.py     # 运行时编辑/导入平台规则库
│   ├── research_export.py     # Excel 导出
│   ├── research_import.py     # 关键词文件导入（xlsx/csv/tsv）
│   ├── research_links.py      # 链接提取、平台名称归一化
│   ├── research_platforms.py  # URL → 中文平台名 → 类型映射库
│   ├── research_scheduler.py  # 任务调度器
│   ├── research_store.py      # SQLite 数据访问层
│   ├── selectors.py           # DOM 选择器、正则、JS 辅助函数
│   ├── server.py              # FastAPI 接口
│   ├── static/index.html      # Web 管理界面（单页应用）
│   ├── text_utils.py          # 文本片段合并工具
│   └── config.py              # Settings / RuntimeConfig
├── tests/                     # pytest 测试
├── AI-UNDERSTANDING.md        # AI 启动时快速上下文
├── UNDERSTANDING.md           # 本文档
├── README.md                  # 用户文档
└── ROADMAP.md                 # 开发路线图
```

---

## 4. 运行模式

### 4.1 桌面模式（推荐）

- 启动命令：`doubao-keyword-collector`
- 入口：`src/doubao2api/windows_entry.py`
- 流程：`run_desktop()` → `DesktopWindow` + `QtBrowserBridge` + `DesktopBackend` + `NativeDashboard`。
- 管理界面直接嵌入在主窗口中（`PersistentTabWidget` 的 index 0）。
- 每个账号是一个 Qt WebEngine 标签页，独立 profile/storage。

### 4.2 服务端/兼容模式

- 启动命令：`doubao-account-manager`
- 入口：`src/doubao2api/__main__.py`
- 启动 FastAPI 服务，默认 `http://127.0.0.1:9090/admin`。
- 可通过浏览器访问 Web 管理界面 `static/index.html`。
- 仍然依赖本地账号浏览器；服务端本身不渲染 GUI，但可驱动 Playwright。

---

## 5. 核心模块详解

### 5.1 配置层：`config.py`

- `Settings`（`src/doubao2api/config.py`）：用户持久化配置。
  - `default_account_id`、`auto_start_all_accounts`、各类别/配额设置。
  - `account_tab_hidden: dict[str, bool]`：账号标签显示/隐藏状态，默认显示。
- `RuntimeConfig`：运行时环境配置（host/port/headless/browser 等）。
- `SettingsStore`：将 `Settings` 读写为 `%LOCALAPPDATA%\DoubaoAccountManager\settings.json`。

### 5.2 账号池：`account_manager.py`

- `BrowserAccountPool`
  - 管理多个 `ManagedBrowserAccount`。
  - 每个账号有独立的 `user_data_dir`（`accounts/{account_id}`）。
  - `discover_account_ids()`：扫描 `accounts/` 目录发现账号。
  - `start_account()` / `stop_account()`：启动/停止浏览器。
  - `snapshots()`：并发获取所有账号状态，支持失败退避。
  - `set_category()` / `is_tab_hidden()` / `set_tab_hidden()` / `rename_account()` / `delete_account()`：账号元数据维护。
- 桌面模式下，客户端实例由 `desktop.py` 的 `client_factory` 创建为 `EmbeddedBrowserClient`。

### 5.3 浏览器客户端

#### Playwright 客户端：`browser_client.py`

- `BrowserClient`
  - 基于 Playwright 的持久化浏览器上下文。
  - `chat()`：发送关键词、等待回答、展开参考资料、返回链接。
  - `import_cookies()`：导入 Cookie。
  - 主要供无桌面环境或服务端模式使用。

#### Qt 内嵌客户端：`embedded_browser_client.py`

- `EmbeddedBrowserClient`
  - 通过 `QtBrowserBridge` 与 Qt WebEngine 交互。
  - 关键流程 `chat()`：
    1. `inspect_session_state()` 检查登录状态。
    2. 点击“新对话”。
    3. 输入关键词并发送。
    4. 拦截 `fetch('/chat/completion')` 流式响应。
    5. 检测验证码/页面无响应。
    6. 展开参考资料并提取链接。
  - `_expand_references()`：多种选择器兜底展开参考摘要。
  - `_ping_page()` / `_run_script_or_track_timeout()`：页面卡死检测。

### 5.4 调度器：`research_scheduler.py`

- `ResearchScheduler`
  - 运行在独立后台线程的 asyncio 循环中（`DesktopBackend`）。
  - `_run_loop()`：每 2 秒轮询 `due_tasks()`，也可被 `wake()` 立即唤醒。
  - `_dispatch_due_tasks()`：为每个到期任务选择可用账号并创建 worker。
  - `_run_task()`：
    - 替换 `{keyword}` 生成 prompt。
    - 调用 `account.client.chat(..., reference_callback=save_reference)`。
    - 异常时根据类型暂停账号或重试任务。
  - 账号选择逻辑：跳过 `busy_accounts`、跳过 `paused_until` 未到期的账号、检测登录/验证码/聊天就绪状态。
  - `_check_schedules()`：在 `_dispatch_due_tasks()` 之前检查到期的 `research_schedules`，按模板最新配置生成一次性 `research_jobs` 并推进下一次执行时间。

### 5.5 数据层：`research_store.py`

- `ResearchStore`
  - SQLite 文件：`{data_root}/research.sqlite3`。
  - WAL 模式 + 外键约束。
  - 核心表：
    - `research_jobs`：任务批次（name、prompt_template、status、scheduled_at、interval_seconds、max_attempts、account_ids_json）。
    - `research_tasks`：每个关键词一次执行（job_id、keyword、status、scheduled_at、account_id、attempt_count、result_count）。
    - `research_results`：采集到的每条链接（job_id、task_id、keyword、link、platform、platform_type、account_id、collected_at/date、title）。
    - `account_runtime`：账号使用/暂停状态。
    - `research_job_templates`：任务模板（name、keywords_json、prompt_template、interval_seconds、account_cooldown_seconds、max_attempts）。
    - `research_schedules`：触发计划（name、template_id、enabled、schedule_type、schedule_value、next_run_at、run_count、last_job_id）。
  - 核心方法：
    - `create_job()` / `list_jobs()` / `get_job()` / `set_job_status()` / `rename_job()` / `delete_job()`。
    - `due_tasks()` / `mark_task_running()` / `complete_task()` / `fail_or_retry_task()`。
    - `add_result()`：实时写入单条链接。
    - 任务模板：`create_job_template()` / `list_job_templates()` / `get_job_template()` / `update_job_template()` / `delete_job_template()`。
    - 触发计划：`create_schedule()` / `list_schedules()` / `get_schedule()` / `update_schedule()` / `toggle_schedule()` / `delete_schedule()` / `due_schedules()` / `create_job_from_schedule()` / `advance_schedule()`。
    - `result_dashboard()` / `source_comparison()` / `list_results()`：筛选与聚合。
    - `sync_platform_info()`：按最新规则回填缺失的 `platform_type`。
    - `long_tail_analysis()`：按平台聚合频次、关键词覆盖广度、密度，返回四象限分类与代表性链接/域名。
    - `rename_account_references()` / `remove_account_references()`：账号重命名/删除时同步数据库引用。

### 5.6 长尾信源分析

- `research_store.long_tail_analysis()`：
  - 输入：与 `list_results()` 相同的筛选条件（任务、平台、账号、日期）+ 分类阈值。
  - 指标：每个平台的 `freq`（总频次）、`breadth`（覆盖不同关键词数）、`density = freq / breadth`（平均引用密度）。
  - 分类：
    - 垂直长尾宝藏（高广度 + 低频次 + 低密度）
    - 虚假长尾(噪声)（高广度 + 低频次 + 极高密度）
    - 头部主流媒体（高广度 + 高频次）
    - 特定品类垂直站（低广度 + 高频次）
    - 普通垂直信源 / 一次性/僵尸信源
  - 输出：代表性链接/域名、覆盖关键词示例、四象限统计。
- `native_dashboard.LongTailChart`：
  - matplotlib + QtAgg 后端，气泡大小映射密度，颜色映射象限。
  - 支持 X/Y 轴独立对数刻度、悬停提示、阈值分割线。
  - 对拥挤的低频次/低广度区域做小幅确定性抖动并缩小僵尸信源点。
- `native_dashboard.analyze_long_tail()` / `export_long_tail_excel()` / `copy_long_tail_keywords()`：
  - 独立“长尾信源”页签触发分析并导出优质长尾名单。

### 5.7 平台映射：`research_platforms.py`

- 平台规则库的唯一事实来源。
- `PLATFORM_CATEGORIES`：15 个中文类型，如“综合新闻门户”“企业官网/品牌站”“短视频/社交媒体”等。
- `PLATFORM_ENTRIES`：按 specificity 排序的 `{domain, name, category}` 列表（约 5000+ 条）。
- `_DOMAIN_SUFFIX_MAP`：为加速 URL 匹配而预建的 domain → entry 字典；`entry_for_url()` 按 host 后缀从长到短查找，避免线性扫描。
- `entry_for_url()` / `platform_for_url()` / `category_for_url()` / `platform_category()`：匹配规则。
- `to_js_platform_data()`：把规则注入浏览器端 JS，供页面内提取时直接识别平台。
- 新增/扩充时按域名 specificity 插入（更具体的域名放在通用域名之前），并同步重建 suffix map。

### 5.7 平台规则编辑器：`platform_editor.py`

- 运行时编辑 `research_platforms.py` 中的规则库。
- `add_entry(domain, name, category)`：单条添加。
- `add_entries(rows)`：批量导入 Excel/CSV 行，更新内存规则并持久化到源文件。
- `find_insert_position()`：保证更具体域名排在通用域名之前。
- `all_entries()`：返回全部规则。

### 5.8 链接提取：`research_links.py`

- `extract_research_links(answer_text, events)`：从回答文本/事件中提取外链。
- `normalize_thinking_references(references)`：归一化参考资料，去重，优先 URL 推导平台。
- 返回的 item 包含 `link`、`platform`、`platform_type`、`title`。

### 5.9 导出/导入

#### `research_export.py`

- `build_results_workbook(rows)`：使用 `openpyxl` 生成 `.xlsx`。
- 列：任务、日期、提问关键词、资料名称、检索资料链接、检索资料平台、平台类型。
- 首行加深蓝底色表头，冻结首行，自动筛选，链接设为超链接。
- `_platform_type_for_result()`：导出时对空 `platform_type` 按当前规则兜底。

#### `research_import.py`

- 关键词文件导入：识别 `.xlsx/.csv/.tsv` 的“关键词、关键字、提问关键词、keyword、query”等表头。

### 5.10 DOM 选择器：`selectors.py`

- 集中管理 XPath/CSS 选择器、正则表达式。
- `SELECTORS["composer"]`：输入框。
- `SELECTORS["send_button"]`：发送按钮。
- `SELECTORS["reference_rows"]`：参考资料行。
- `SELECTORS["reference_expand"]`：展开摘要的按钮。
- `REFERENCE_SUMMARY_PATTERN`：匹配“参考了 X 篇资料”。
- `SELECTORS["captcha_patterns"]`：验证码文案关键词。

### 5.11 文本工具：`text_utils.py`

- 抽取 `_text_from_content`、`_collect_text`、`_merge_text_fragments` 等文本处理函数。
- `browser_client.py` 与 `embedded_browser_client.py` 统一从此模块导入，避免两套浏览器客户端重复实现。
- `_merge_text_fragments` 使用最长公共前后缀合并流式返回的文本片段，减少重复或跳跃。

### 5.12 Cookie 工具：`cookie_utils.py`

- 实现 `parse_cookie_records`，支持简单 `name=value` 与 Set-Cookie 属性两种格式。
- 严格校验原始 `domain` / `path`，非 `doubao.com` 及其子域的 Cookie 会被跳过。
- `browser_client.py` 与 `embedded_browser_client.py` 的 `import_cookies` 统一调用此模块。

### 5.13 管理界面：`native_dashboard.py`

- `NativeDashboard`
  - 内部 `QTabWidget` 包含 8 个页签：新建采集、账号环境、历史任务、采集结果、长尾信源、信源对比、平台信息、定时任务。
  - `DesktopBackend` 提供账号池、调度器、数据存储。
  - 3 秒定时刷新 + 5 秒结果/对比刷新。
  - 关键方法：
    - `_build_tasks_page()` / `_build_accounts_page()` / `_build_history_page()` / `_build_results_page()` / `_build_long_tail_page()` / `_build_comparison_page()` / `_build_platforms_page()` / `_build_schedules_page()`。
    - `refresh_accounts()` / `refresh_jobs()` / `refresh_history()` / `refresh_results()` / `refresh_long_tail_options()` / `refresh_source_comparison()` / `refresh_platforms()` / `refresh_schedules_page()`。
    - `create_job()`：读取表单 → `research_store.create_job()` → `scheduler.wake()`。
    - `save_job_template()` / `edit_job_template()` / `delete_job_template()`：任务模板 CRUD。
    - `create_schedule()` / `toggle_schedule()` / `delete_schedule()` / `run_schedule_now()`：触发计划管理。
    - `analyze_long_tail()`：按筛选范围和阈值计算四象限并渲染 matplotlib 气泡图。
    - `export_job_results()`：历史任务行内导出 Excel。
    - `rename_job()`：历史任务重命名。
    - `sync_platform_info()`：按最新规则回填历史记录平台类型。
- `SourceDistributionChart`：自定义 QPainter 甜甜圈图 + 平台列表。
- `LongTailChart`：基于 matplotlib 的气泡四象限图，支持悬停提示、X/Y 对数刻度。
- `MultiSelectFilter`：带搜索、全选/清空的关键词下拉多选。

### 5.14 Web 界面：`static/index.html`

- 单文件 HTML/CSS/JS。
- 已扩展为 8 个页签：新建采集、账号环境、历史任务、采集结果、长尾信源、信源对比、定时任务、平台信息。
- 通过 `fetch('/admin/api/...')` 与后端通信，能力已与 Native 端对齐。

### 5.15 API 层：`server.py`

- FastAPI 应用。
- 主要路由分组：
  - `/v1/...`：OpenAI 兼容接口（聊天、图片、视频等，部分返回 501）。
  - `/admin/api/...`：管理后台接口（账号、任务、结果、设置、Cookie）。
  - `/admin`：返回 `static/index.html`。
- 关键接口：
  - `POST /admin/api/research/jobs`：创建采集任务。
  - `GET /admin/api/research/jobs`：任务列表 + 调度器快照。
  - `POST /admin/api/research/jobs/{id}/pause|resume|cancel`。
  - `DELETE /admin/api/research/jobs/{id}`：删除历史任务。
  - `POST /admin/api/research/jobs/{id}/rename`：重命名历史任务。
  - `GET /admin/api/research/results`：结果列表 + dashboard。
  - `GET /admin/api/research/results/keywords|jobs`：结果筛选下拉。
  - `GET /admin/api/research/results/export.xlsx`：Excel 导出。
  - `POST /admin/api/research/results/sync-platform-info`：按最新规则回填旧记录平台类型。
  - `POST /admin/api/research/results/source-comparison`：A/B 日期区间信源对比。
  - `POST /admin/api/research/results/long-tail-analysis`：长尾信源分析。
  - `POST /admin/api/research/results/long-tail/export.xlsx`：导出优质长尾。
  - `GET /admin/api/research/platforms` / `POST /admin/api/research/platforms/import`：平台规则库。
  - `POST /admin/api/accounts`：创建账号。
  - `POST /admin/api/accounts/{id}/stop|rename|category|...`。
  - 任务模板 / 触发计划 CRUD 与立即执行接口。

---

## 6. 数据模型

### 6.1 SQLite 表

```sql
research_jobs (
    id, name, prompt_template, status, scheduled_at,
    interval_seconds, account_cooldown_seconds, max_attempts,
    account_ids_json, created_at, started_at, finished_at, last_error
)

research_tasks (
    id, job_id, position, keyword, status, scheduled_at,
    account_id, attempt_count, answer, error, result_count,
    created_at, started_at, finished_at
)

research_results (
    id, job_id, task_id, collected_at, collected_date,
    keyword, link, platform, platform_type, account_id, title
)

account_runtime (
    account_id, last_used_at, paused_until, pause_reason
)

research_job_templates (
    id, name, keywords_json, prompt_template,
    interval_seconds, account_cooldown_seconds, max_attempts,
    created_at, updated_at
)

research_schedules (
    id, name, template_id, enabled, schedule_type, schedule_value,
    next_run_at, run_count, last_run_at, last_job_id, last_error,
    created_at, updated_at
)
```

未来可能新增索引（见 `ROADMAP.md` Phase 3）。

### 6.2 关键状态机

- `research_jobs.status`：`running` | `paused` | `cancelled` | `completed` | `failed`
- `research_tasks.status`：`pending` | `running` | `completed` | `failed` | `cancelled`
- `research_scheduler._busy_accounts`：内存中正在执行任务的账号集合。

---

## 7. 关键流程

### 7.1 创建任务到执行

1. UI：`create_job()` 收集关键词、prompt、账号、间隔、重试次数。
2. `ResearchStore.create_job()`：
   - 插入 `research_jobs`（status=running）。
   - 每个关键词生成一条 `research_tasks`（status=pending，scheduled_at 按间隔递增）。
3. `scheduler.wake()` 触发调度循环。
4. `ResearchScheduler._dispatch_due_tasks()`：
   - 查询 `due_tasks()`。
   - 为每个 task 选账号 → `mark_task_running()` → 创建 asyncio worker。
5. `ResearchScheduler._run_task()`：
   - 替换 prompt 中的 `{keyword}`。
   - 调用 `client.chat(..., reference_callback=save_reference)`。
   - `save_reference()` 将链接写入 `research_results` 并更新进度。
6. 完成后 `complete_task()`，失败则 `fail_or_retry_task()`。

### 7.2 参考资料采集

1. 发送关键词后，前端 `fetch` 拦截器捕获 `/chat/completion` 流式响应。
2. 检测到回答完成后，开始展开参考资料：
   - 点击摘要（“参考了 X 篇资料”）。
   - 点击“展开更多”。
   - 滚动容器到底部。
3. 提取 `a[href^="http"]` 或 `SELECTORS["reference_rows"]` 中的链接。
4. 调用 `reference_callback` 逐条保存。

### 7.3 验证码/风控处理

- `embedded_browser_client.py` 检测页面是否出现验证码文案或连续 JS 无响应。
- 抛出含“验证码”/“页面无响应”的异常。
- `research_scheduler.py` 捕获后调用 `pause_account(account_id, 1800, reason)`。
- UI 账号卡片显示“验证已完成”按钮，点击后 `reset_captcha()` + `resume_account()` + `scheduler.wake()`。

### 7.4 平台类型回填

- 采集时根据当前规则库写入 `platform_type`。
- 若规则库后续更新，历史记录可能为空或不匹配。
- `research_store.sync_platform_info()` 扫描 `platform_type` 为空或 `platform` 为“未知平台”的记录，按最新规则重新匹配并批量更新。
- `native_dashboard.py` 历史任务页提供“同步平台信息”按钮触发该操作。
- `research_export.py` 导出时也有兜底逻辑：若记录本身无平台类型，按当前规则再查一次。

---

## 8. UI 架构

### 8.1 Native 桌面

- `DesktopWindow`：主窗口，包含 `PersistentTabWidget`。
- `PersistentTabWidget`：使用 `QStackedLayout.StackAll`，后台浏览器页面保持活跃不被隐藏。
- `NativeDashboard`：index 0 的“采集管理中心”标签，内部再用 `QTabWidget` 切分 8 个页面。
- `QtBrowserBridge`：通过 Signal/Slot 在主线程操作 Qt WebEngine 标签页，避免跨线程问题。
- `DesktopBackend`：在独立线程运行 asyncio 事件循环，UI 通过 `submit()` / `call()` 提交协程/同步函数，通过 `_watch()` 轮询 Future 结果。

### 8.2 Web 管理

- `static/index.html` 通过 tab 按钮切换 `#tasks`、`#accounts`、`#results` 等 `<main>`。
- 调用 `/admin/api/...` 获取数据并渲染。

---

## 9. 配置与环境

- 数据根目录：
  - Windows：`%LOCALAPPDATA%\DoubaoAccountManager`
  - 可通过环境变量 `DOUBAO_DATA_ROOT` 覆盖。
- 文件：
  - `settings.json`：用户设置。
  - `research.sqlite3`：采集数据库。
  - `accounts/{account_id}/`：账号浏览器数据目录。
- 环境变量：
  - `DOUBAO_HOST` / `DOUBAO_PORT`
  - `DOUBAO_HEADLESS`
  - `DOUBAO_BROWSER_CHANNEL` / `DOUBAO_BROWSER_EXECUTABLE_PATH`
  - `DOUBAO_OPEN_ADMIN_BROWSER`
  - `DOUBAO_API_KEY`

---

## 10. 测试

- 框架：pytest + pytest-asyncio。
- 测试位置：`tests/`。
- 当前覆盖：
  - `test_research_api.py`：Web 端新增 API（历史任务操作、结果筛选/导出、长尾分析、信源对比、平台信息导入、定时任务立即执行）。
  - `test_long_tail_analysis.py`：长尾信源聚合、分类、代表性链接提取。
  - `test_research_links.py`：平台映射、链接归一化。
  - `test_research_platforms.py`：平台库。
  - `test_research_store.py`：数据库操作、任务生命周期、索引、平台回填。
  - `test_research_scheduler.py`：调度器暂停/取消/多账号派发。
  - `test_embedded_browser_client.py`：Qt 浏览器客户端逻辑（使用 FakeBridge）。
  - `test_account_manager.py`：账号池快照退避、重命名/删除。
  - `test_selectors.py`：选择器正则。
  - `test_text_utils.py` / `test_cookie_utils.py`：文本合并、Cookie 解析。
- 运行：
  ```cmd
  cd /d "D:\ai-source-capturer\doubao-keyword-collector"
  python -m pytest tests/ -q
  ruff check .
  ```

---

## 11. 打包

- 使用 PyInstaller。
- Spec 文件：`packaging/AI信源采集工具.spec`。
- 打包命令示例：
  ```cmd
  pyinstaller packaging\AI信源采集工具.spec --distpath dist-dir --workpath build-dir --noconfirm --onedir
  ```
- 入口脚本：`doubao-keyword-collector`（桌面版）。

---

## 12. 常见扩展点

| 需求 | 修改位置 |
|------|----------|
| 新增平台/类型 | `src/doubao2api/research_platforms.py` 或平台信息页导入 |
| 调整 DOM 选择器 | `src/doubao2api/selectors.py` |
| 新增管理后台 API | `src/doubao2api/server.py` + `models.py` |
| 新增桌面 UI 页签 | `src/doubao2api/native_dashboard.py` |
| 新增 Web UI 页面 | `src/doubao2api/static/index.html` |
| 调整调度策略 | `src/doubao2api/research_scheduler.py` |
| 调整数据库 schema | `src/doubao2api/research_store.py` `_initialize()` |
| 调整 Excel 导出列 | `src/doubao2api/research_export.py` |
| 调整关键词导入规则 | `src/doubao2api/research_import.py` |
| 调整打包 | `packaging/AI信源采集工具.spec` |

---

## 13. 注意事项

- 本项目与字节跳动/豆包无官方关系。
- 不绕过验证码；检测到验证码会暂停账号等待人工处理。
- 所有账号数据、Cookie、数据库均保存在本地。
- 桌面模式依赖 PySide6 可选依赖；服务端模式不需要 GUI。
- 页面结构变化后需要更新 `selectors.py` 中的选择器。
- P0/P1 阶段修复、平台类型改造、长尾信源分析、定时任务、Web UI 与 Native 对齐、URL 匹配性能优化已全部完成；后续重点为账号置顶与性能优化专项（详见 `ROADMAP.md`）。
