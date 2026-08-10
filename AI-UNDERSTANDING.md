# AI 快速上下文：豆包关键词资料采集器

> 启动时先读本文档，可在 2 分钟内建立项目全局认知。如需实现细节，再读 `UNDERSTANDING.md`。

---

## 1. 项目定位

**豆包关键词资料采集器（开源版）** `v0.5.2`

本地优先的非官方关键词调研工具：管理多个豆包账号，批量提问，自动展开回答中的“参考资料”区域，提取资料链接、平台名、平台类型，存入本地 SQLite，支持筛选、导出 Excel、信源对比、长尾信源分析。

---

## 2. 两个运行模式

| 模式 | 命令 | 入口文件 | 用途 |
|------|------|----------|------|
| 桌面版 | `doubao-keyword-collector` / `dkc` | `src/doubao2api/windows_entry.py` | 原生 Qt/PySide6 窗口，推荐 |
| 服务端 | `doubao-account-manager` | `src/doubao2api/__main__.py` | FastAPI + `/admin` 网页管理端 |

两者共用账号池、调度器、数据库、平台规则库。

---

## 3. 技术栈

- Python ≥ 3.10
- 桌面 GUI：PySide6 / Qt WebEngine
- 无界面浏览器：Playwright（服务端模式）
- Web：FastAPI + Uvicorn + `static/index.html`
- 存储：SQLite + `settings.json`
- Excel：openpyxl
- 构建：hatchling / PyInstaller

---

## 4. 关键目录与文件

```
D:\ai-source-capturer\doubao-keyword-collector
├── src/doubao2api/
│   ├── windows_entry.py          # 桌面 EXE 入口
│   ├── __main__.py               # 服务端入口
│   ├── desktop.py                # Qt 主窗口、账号标签、浏览器桥接
│   ├── native_dashboard.py       # 原生管理面板（7 个页签）
│   ├── account_manager.py        # 账号池
│   ├── browser_client.py         # Playwright 客户端
│   ├── embedded_browser_client.py# Qt WebEngine 客户端
│   ├── research_scheduler.py     # 任务调度器
│   ├── research_store.py         # SQLite 数据层
│   ├── research_platforms.py     # 平台规则库（域名 → 平台名 → 类型）
│   ├── platform_editor.py        # 运行时编辑/导入平台规则
│   ├── research_export.py        # Excel 导出
│   ├── research_import.py        # 关键词导入
│   ├── server.py                 # FastAPI 接口
│   └── static/index.html         # Web 管理端
├── tests/                        # pytest 测试
├── packaging/                    # PyInstaller 打包
├── README.md                     # 用户文档
├── UNDERSTANDING.md              # 技术人员/AI 详细参考
├── ROADMAP.md                    # 已完成与待完成计划
└── AI-UNDERSTANDING.md           # 本文档
```

---

## 5. 核心模块速查

| 文件 | 一句话职责 | 改动它会影响哪里 |
|------|-----------|------------------|
| `native_dashboard.py` | 桌面端 8 页签 UI | 所有界面交互 |
| `desktop.py` | Qt 主窗口与账号标签管理 | 账号页签生命周期 |
| `account_manager.py` | 账号池创建/启动/快照/重命名/删除 | 账号环境页 |
| `research_scheduler.py` | 调度关键词任务、处理风控/重试 | 新建采集、任务执行 |
| `research_store.py` | SQLite 读写、schema | 所有数据持久化 |
| `research_platforms.py` | URL → 平台名/类型映射库 | 导出、结果页、同步按钮 |
| `platform_editor.py` | 运行时导入规则并持久化 | 平台信息页 |
| `research_export.py` | 生成 Excel | 导出按钮 |
| `research_import.py` | 解析关键词 Excel/CSV | 新建采集导入 |
| `browser_client.py` / `embedded_browser_client.py` | 浏览器自动化与链接提取 | 采集稳定性 |
| `selectors.py` | DOM 选择器与验证码文案 | 豆包页面结构变化时需改 |
| `server.py` | FastAPI 与 OpenAI 兼容接口 | Web 端、外部 API |
| `models.py` | Pydantic 请求模型 | API 参数校验 |

---

## 6. 桌面端 7 个页签

| 页签 | 主要功能 | 关键方法/文件 |
|------|----------|---------------|
| 新建采集 | 输入任务名、关键词、间隔、尝试次数、选择账号，创建任务 | `NativeDashboard.create_job()` |
| 账号环境 | 创建/启动/关闭/重命名/删除账号，处理验证码恢复 | `refresh_accounts()` / `account_manager.py` |
| 历史任务 | 查看已完成/失败任务，导出 Excel、重命名、删除、同步平台信息 | `refresh_history()` / `export_job_results()` / `rename_job()` / `sync_platform_info()` |
| 采集结果 | 筛选结果、查看信源分布、导出 Excel | `refresh_results()` / `result_dashboard()` |
| 长尾信源 | 按频次/广度/密度识别垂直长尾宝藏平台，气泡四象限图可视化，支持悬停、导出 Excel | `analyze_long_tail()` / `LongTailChart` |
| 信源对比 | A/B 两个日期区间对比平台变化 | `refresh_source_comparison()` |
| 平台信息 | 查看当前平台规则库，导入 Excel 扩展 | `refresh_platforms()` / `platform_editor.add_entries()` |
| 定时任务 | 任务模板 + 触发计划两层模型，支持按间隔/一次性/每日定时自动生成采集任务 | `refresh_schedules_page()` / `save_job_template()` / `create_schedule()` |

---

## 7. 数据库表速查

| 表 | 存储内容 | 关键字段 |
|----|---------|----------|
| `research_jobs` | 任务批次 | `name`, `prompt_template`, `status`, `interval_seconds`, `max_attempts`, `account_ids_json` |
| `research_tasks` | 每个关键词一次执行 | `job_id`, `keyword`, `status`, `scheduled_at`, `account_id`, `attempt_count`, `result_count` |
| `research_results` | 采集到的链接 | `job_id`, `task_id`, `keyword`, `link`, `platform`, `platform_type`, `account_id`, `collected_at/date`, `title` |
| `account_runtime` | 账号使用/暂停状态 | `last_used_at`, `paused_until`, `pause_reason` |
| `research_job_templates` | 任务模板（关键词、提问模板、间隔、尝试次数等） | `name`, `keywords_json`, `prompt_template`, `interval_seconds`, `account_cooldown_seconds`, `max_attempts` |
| `research_schedules` | 触发计划（引用模板，按间隔/一次性/每日定时触发） | `name`, `template_id`, `enabled`, `schedule_type`, `schedule_value`, `next_run_at`, `run_count`, `last_job_id` |

---

## 8. 核心数据流（一句话）

用户在 `native_dashboard.py` 创建任务 → `research_store.py` 拆分为 `research_tasks` → `research_scheduler.py` 每 2 秒轮询并选可用账号 → 浏览器客户端打开豆包提问 → 展开参考资料 → 逐条回调保存到 `research_results` → UI 结果页/历史任务页读取并展示，导出时调用 `research_export.py` 生成 Excel。

---

## 9. 最近已完成的关键改动

- 结果页新增信源分布图表（Top 20 + 其他）。
- 新增“信源对比”页，支持 A/B 区间平台变化分析。
- 新增“平台信息”页，支持 Excel 导入扩展平台规则。
- `platform_type` 列改造：导出、结果页、同步按钮均可按最新规则回填。
- 历史任务行内新增“导出”按钮，可单独导出该任务结果 Excel。
- 历史任务支持“重命名”。
- 历史任务标题栏新增“同步平台信息”按钮，按最新规则回填缺失的平台类型。
- 结果页切换任务卡死问题已修复（临时切换 `ResizeMode` + `blockSignals`）。
- 新增“长尾信源”独立页签：按频次/广度/密度识别垂直长尾宝藏平台，支持气泡四象限图、悬停查看、Excel 导出。
- 新增“定时任务”页签：采用任务模板 + 触发计划两层模型，支持按间隔、一次性、每日定时自动生成采集任务。

---

## 10. Roadmap 当前状态

| 状态 | 内容 |
|------|------|
| ✅ 已完成 | 平台类型改造、平台信息管理、信源分布、信源对比、账号暂停/恢复、历史任务导出/重命名/同步平台信息、长尾信源分析、定时任务（Native） |
| ⏳ Phase 1 | Web UI 与 Native 对齐（历史任务、信源对比、平台信息、长尾信源、图表、定时任务） |
| ⏳ Phase 2 | 账号置顶 |
| ⏳ Phase 3 | 性能优化专项 |

---

## 11. 常见修改点索引

| 需求 | 修改位置 |
|------|----------|
| 新增/修改平台或类型 | `src/doubao2api/research_platforms.py` 或平台信息页导入 |
| 豆包页面结构变了 | `src/doubao2api/selectors.py` |
| 新增桌面 UI 页签 | `src/doubao2api/native_dashboard.py` |
| 新增后台 API | `src/doubao2api/server.py` + `models.py` |
| 新增 Web UI 页面 | `src/doubao2api/static/index.html` |
| 调整 Excel 导出列 | `src/doubao2api/research_export.py` |
| 调整数据库 schema | `src/doubao2api/research_store.py` `_initialize()` |
| 调整调度策略 | `src/doubao2api/research_scheduler.py` |
| 调整打包 | `packaging/AI信源采集工具.spec` |

---

## 12. 重要约束

- 与字节跳动/豆包无官方关系。
- 不绕过验证码；检测到验证码/风控会暂停账号 30 分钟等待人工处理。
- 所有账号数据、Cookie、数据库保存在本地。
- 桌面模式依赖 PySide6 可选依赖；服务端模式不需要 GUI。
