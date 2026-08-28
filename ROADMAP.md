# 开发路线图

> 记录已完成大事记与下一阶段计划。
> 最近一次更新：2026-08-27

---

## 已完成大事记

| 序号 | 功能 | 关键文件 |
|------|------|----------|
| 1 | 页面卡死/验证码检测 + 自动暂停 + 人工恢复 | `embedded_browser_client.py`, `research_scheduler.py`, `native_dashboard.py` |
| 2 | URL → 中文平台名 → 类型映射库 | `research_platforms.py` |
| 3 | 数据层 `platform_type` 改造（列、回填、聚合、导出） | `research_store.py`, `research_export.py` |
| 4 | 采集结果页整体滚动/布局修复 | `native_dashboard.py` |
| 5 | 信源分布与占比重做（Top 20 + “其他”，弹窗查看全部） | `native_dashboard.py` |
| 6 | 平台信息管理模块（导入 Excel 扩充映射库） | `platform_editor.py`, `native_dashboard.py` |
| 7 | 结果页性能优化（增量刷新、懒刷新下拉框） | `native_dashboard.py` |
| 8 | 历史任务行内导出 Excel | `native_dashboard.py`, `research_export.py` |
| 9 | 历史任务重命名 | `native_dashboard.py`, `research_store.py` |
| 10 | 同步平台信息按钮（按最新规则回填旧记录平台类型） | `native_dashboard.py`, `research_store.py` |
| 11 | 结果页切换任务卡死修复 | `native_dashboard.py` |
| 12 | 长尾信源分析页（频次/广度/密度四象限、气泡图、悬停提示、Excel 导出） | `native_dashboard.py`, `research_store.py`, `pyproject.toml` |
| 13 | 定时任务（Native）：任务模板 + 触发计划两层模型，支持按间隔/一次性/每日定时触发 | `research_store.py`, `research_scheduler.py`, `native_dashboard.py`, `models.py`, `server.py` |
| 14 | Web UI 与 Native 对齐（历史任务、结果增强、长尾信源、信源对比、定时任务、平台信息） | `server.py`, `models.py`, `static/index.html`, `research_export.py` |
| 15 | URL → 平台匹配性能优化（suffix map 查找） | `research_platforms.py`, `platform_editor.py` |
| 16 | 桌面端账号标签显示/隐藏切换 | `config.py`, `account_manager.py`, `desktop.py`, `native_dashboard.py`, `models.py`, `server.py` |
| 17 | Phase 3 性能优化专项（快照并发、调度休眠、连接复用、索引、UI 刷新、浏览器轮询） | `account_manager.py`, `research_scheduler.py`, `research_store.py`, `native_dashboard.py`, `embedded_browser_client.py`, `browser_client.py` |
| 18 | 应用内检查更新：版本检查模块、设置持久化、检查更新页签、GitHub API 速率限制退化方案 | `update_checker.py`, `config.py`, `native_dashboard.py` |
| 19 | 应用内下载更新：单文件/便携版 asset 下载、进度条、SHA256/完整性兜底校验、本地缓存 | `update_checker.py`, `native_dashboard.py` |
| 20 | 豆包新布局兼容：适配 `contenteditable` 输入框与新版参考资料展开按钮 | `selectors.py`, `embedded_browser_client.py` |
| 21 | Windows 自替换 updater：便携版目录替换、单文件 exe 替换、独立 helper、备份回滚 | `update_installer.py`, `update_installer_helper.py`, `native_dashboard.py`, 打包脚本 |
| 22 | **多平台采集架构（v1.1.0）**：新增 `platforms/` 包抽象 `AIPlatform`；接入 DeepSeek；账号/任务绑定 AI 平台；调度器按平台过滤；导出增加“AI 平台”列；桌面端与命令行端浏览器客户端全面平台化 | `platforms/`, `account_manager.py`, `research_scheduler.py`, `research_store.py`, `research_export.py`, `native_dashboard.py`, `server.py`, `models.py`, `embedded_browser_client.py`, `browser_client.py`, `cookie_utils.py`, `research_links.py`, `desktop.py`, `windows_entry.py`, `__main__.py`, `config.py` |
| 23 | 任务名自动生成优化（首个关键词-平台-日期）与信源对比体验优化 | `native_dashboard.py`, `research_store.py`, `static/index.html` |
| 24 | 长尾信源页支持平台/账号多选筛选 + 参数说明优化 | `native_dashboard.py`, `research_store.py`, `models.py`, `server.py`, `static/index.html` |
| 25 | 账号环境页按平台筛选 + `MultiSelectFilter` 单位自定义 | `native_dashboard.py`, `account_manager.py`, `config.py`, `desktop.py` |
| 26 | 更新下载支持断点续传与 30 分钟卡住检测 | `update_checker.py`, `config.py`, `native_dashboard.py` |
| 27 | 桌面端加载速度优化（SQLite 索引、聚合查询缓存、非阻塞后端初始化）+ 记忆化账号启动/隐藏状态恢复 + 调度器自动拉起未启动账号 | `research_store.py`, `account_manager.py`, `config.py`, `native_dashboard.py`, `research_scheduler.py`, `desktop.py`, `windows_entry.py` |
| 28 | 新建任务支持勾选平台自动生成多任务：桌面/Web 平台多选、按平台拆分创建、账号随平台自动筛选；定时任务模板支持多平台（`ai_platforms_json`）；API 兼容旧 `ai_platform` 字段 | `native_dashboard.py`, `research_store.py`, `research_scheduler.py`, `models.py`, `server.py`, `static/index.html` |

> 各阶段技术细节参见 `CLAUDE.md` 与 `AI_REFERENCE.md`。

---

## 下一阶段：v1.2.0 规划

### 目标

持续扩展更多 AI 平台（如 Kimi、通义千问等），完善多平台体验；优化模板/定时任务与平台的绑定；提升采集稳定性与可观测性。

### 候选任务

| 任务 | 目标 | 主要文件 |
|------|------|----------|
| 更多 AI 平台 | 按 `platforms/` 模式接入 Kimi、通义千问等 | `platforms/` |
| 平台响应捕获完善 | 实测并补充 DeepSeek 网络响应捕获模式 | `platforms/deepseek.py` |
| 采集可观测性 | 在日志中记录每个任务的平台、账号、结果数、耗时 | `research_scheduler.py` |

---

### 风险与缓解

| 风险 | 缓解 |
|------|------|
| 没有代码签名，自动下载 exe 被杀软拦截 | 只做提示+用户确认，不静默更新；允许用户只下载不安装 |
| GitHub API 在国内不稳定或被限流 | 优先 API；触发速率限制时退化为读取 `/releases/latest` 的 302 跳转地址，仍可提示更新并跳转 Release 页面；后续可按需加镜像 fallback |
| 单文件 exe 替换失败导致程序损坏 | 替换前保留 `.bak`；updater 检查文件存在性 |
| 便携版路径含中文/空格 | updater 路径用双引号包裹 |
| Release body 过长或含特殊 Markdown | 先做纯文本渲染，必要时用简单 Markdown 解析 |

### 验收标准

1. `pytest tests/test_update_checker.py tests/test_update_installer.py -q` 通过。
2. 手动测试：
   - 启动后状态栏/页签提示新版本（或手动检查成功）。
   - 能正确显示 Release notes。
   - 下载完成后点击安装，旧程序退出、新版本启动。
   - 失败时旧文件/文件夹 `.bak` 保留。
3. `ruff check .` / `ruff format --check src/doubao2api tests` 通过。

---

## 历史归档

以下内容为已完成阶段留下的数据模型、API/UI 变更、测试计划与风险记录，供后续查阅。

### 数据模型/表变更汇总

| 层级 | 变更 |
|------|------|
| `Settings` (`config.py`) | 新增 `account_tab_hidden: dict[str, bool]`（账号标签显示/隐藏，已实现）；新增 `account_startup_states`（记忆化账号启动/隐藏状态，重启后自动恢复） |
| SQLite | 已新增 `research_job_templates` 表、`research_schedules` 表；已新增 `idx_research_schedules_due`；已新增 `idx_research_tasks_job_status`、`idx_research_results_task`、`idx_research_results_job_date`；已新增 `research_job_templates.ai_platforms_json`（任务模板多平台）；已新增结果/任务/账号运行时多个查询索引 |

### API/UI 变更汇总

#### 新增后端 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/admin/api/accounts/{account_id}/tab-hidden` | 设置账号标签显示/隐藏状态 |
| DELETE | `/admin/api/research/jobs/{job_id}` | 删除历史任务 |
| POST | `/admin/api/research/jobs/{job_id}/rename` | 重命名历史任务 |
| POST | `/admin/api/research/results/sync-platform-info` | 按最新平台规则回填旧记录平台类型 |
| GET | `/admin/api/research/results/keywords` | 结果关键词下拉 |
| GET | `/admin/api/research/results/jobs` | 结果任务下拉 |
| POST | `/admin/api/research/results/source-comparison` | A/B 任务群信源对比 |
| POST | `/admin/api/research/results/long-tail-analysis` | 长尾信源分析 |
| POST | `/admin/api/research/results/long-tail/export.xlsx` | 导出优质长尾 Excel |
| GET | `/admin/api/research/platforms` | 平台规则库列表 |
| POST | `/admin/api/research/platforms/import` | 导入 Excel 扩展平台规则 |
| POST | `/admin/api/research/templates` | 创建任务模板 |
| GET | `/admin/api/research/templates` | 任务模板列表 |
| GET | `/admin/api/research/templates/{id}` | 任务模板详情 |
| POST | `/admin/api/research/templates/{id}` | 更新任务模板 |
| DELETE | `/admin/api/research/templates/{id}` | 删除任务模板（级联删除关联计划） |
| POST | `/admin/api/research/schedules` | 创建触发计划 |
| GET | `/admin/api/research/schedules` | 触发计划列表 |
| GET | `/admin/api/research/schedules/{id}` | 触发计划详情 |
| POST | `/admin/api/research/schedules/{id}` | 更新触发计划 |
| POST | `/admin/api/research/schedules/{id}/toggle` | 启用/禁用计划 |
| POST | `/admin/api/research/schedules/{id}/run` | 立即执行一次 |
| DELETE | `/admin/api/research/schedules/{id}` | 删除计划 |

#### 新增 UI

- **Native**：新增“定时任务”页签（任务模板管理 + 触发计划管理）。
- **Web**：已扩展为 8 页签（新建采集、账号环境、历史任务、采集结果、长尾信源、信源对比、定时任务、平台信息）。
- **Native**：账号环境卡片新增“隐藏标签/显示标签”按钮，支持隐藏后仍保持账号激活并在切换标签时恢复显示。

### 测试计划

#### 单元测试

- `config.py`：`Settings` 序列化/反序列化 `account_tab_hidden`；重命名/删除后字典同步。
- `research_store.py`：
  - `interval` / `once` / `daily` 下次执行时间计算。
  - 任务模板 CRUD 与级联删除。
  - `create_job_from_schedule` 按模板最新配置生成 job/tasks。
  - 触发计划启用/禁用、到期查询、推进下一次执行时间。
- `tests/test_research_api.py`：Web 端新增 API（历史任务操作、结果筛选/导出、长尾分析、信源对比、平台信息导入、定时任务立即执行）。
- `tests/test_account_manager.py`：账号标签隐藏状态持久化、rename/delete 迁移、snapshot 返回 `tab_hidden`。
- `tests/test_api.py`：`POST /admin/api/accounts/{account_id}/tab-hidden` 接口测试。

#### 手动测试

**定时任务**
1. 创建任务模板。
2. 创建 interval=60 秒计划引用该模板，1 分钟后自动生成并运行新 job。
3. 修改模板关键词，确认下次触发使用新关键词。
4. 创建一次性计划，确认准时触发并自动禁用。
5. 创建 daily 计划，确认 `next_run_at` 为下一个 `HH:MM`。
6. 禁用/删除计划，确认不再触发。
7. 点击“立即执行”按钮，确认立即生成 job。
8. 删除模板，确认关联计划被级联删除。

**账号标签显示/隐藏切换**
1. 创建并启动两个账号，确认顶部显示两个标签。
2. 点击账号 A 的“隐藏标签”，确认顶部标签消失，但账号 A 仍可在调度中使用。
3. 隐藏当前活动标签，确认视图回退到“采集管理中心”。
4. 点击账号 A 的“切换标签”，确认标签恢复显示并跳转到该账号。
5. 重启桌面端，确认隐藏状态 persisted。
6. 重命名/删除隐藏账号，确认状态正确迁移/清除。

**性能**
1. 10 个以上账号时账号环境页刷新流畅。
2. 大结果集下结果页筛选和导出响应可接受。
3. 观察 SQLite WAL 与日志，确认没有频繁建连/断连。

### 风险与兼容性

| 风险 | 缓解 |
|------|------|
| 定时任务触发后 job 创建失败但计划已推进 | `create_job_from_schedule` 成功后再 `advance_schedule`；异常时写入 `last_error` 不推进 |
| 多个计划同时到期导致瞬间大量 job | 默认 `limit=20`，顺序执行 |
| 程序重启后错过触发窗口 | `due_schedules` 查询 `next_run_at <= now`，重启后会立即补偿执行 |
| 模板删除后关联计划被误删 | 外键 `ON DELETE CASCADE`；删除前 UI 二次确认 |
| 修改模板影响已存在的计划 | 按需求设计为“按最新配置执行”；UI 明确提示 |
| `daily` 跨夏令时边界 | 使用 `local_now()` 和 `datetime` 标准库自动处理 |
| 多账号置顶 tab 顺序冲突 | 按 `discover_account_ids()` 顺序依次占 1、2、3… 位；取消置顶后移到非置顶区末尾 |
| 数据库连接线程安全 | `threading.local()` 保证每线程独立连接 |
| 性能改动影响采集稳定性 | 先以 P0/P1 改动上线，保留旧值可回滚；充分手动测试 |
| 老版本 settings.json | 未知字段自动忽略；新增 `account_pinned` 不影响旧版本 |
| 老版本数据库 | `CREATE TABLE IF NOT EXISTS` 自动创建新表，无需迁移脚本 |

---

## 验收命令

```bash
cd "/d/ai-source-capturer/doubao-keyword-collector"
python -m pytest tests/ -q
python -m ruff check .
python -m ruff format --check src/doubao2api tests
```

---

## 备注

- `CLAUDE.md` 与 `AI_REFERENCE.md` 已随各阶段完成后同步更新；最新计划以本文档“下一阶段”为准。
- 定时任务采用“任务模板 + 触发计划”两层模型：模板保存采集配置（不保存账号），计划保存触发规则并引用模板；计划触发时按模板最新配置生成一次性 `research_jobs`，账号由调度器按现有 LRU 逻辑动态选择。
