# 开发路线图

> 记录已完成事项与下一阶段实施计划。
> 最近一次更新：2026-08-13

---

## 已完成（插队优先）

| 序号 | 功能 | 关键文件 |
|------|------|----------|
| 1 | 页面卡死/验证码检测 + 自动暂停 + 人工恢复 | `embedded_browser_client.py`, `research_scheduler.py` |
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

---

## 待完成

### Phase 3：性能优化专项

**目标**：降低 UI 刷新、账号快照、调度轮询和数据库访问的无效开销。

**涉及文件**：
- `src/doubao2api/account_manager.py`：快照并发限制
- `src/doubao2api/research_scheduler.py`：动态调度休眠
- `src/doubao2api/research_store.py`：SQLite 连接线程复用、补充索引
- `src/doubao2api/native_dashboard.py`：非激活页刷新频率优化
- `src/doubao2api/embedded_browser_client.py` + `browser_client.py`：浏览器轮询优化

**详细任务**：

1. **降低账号快照并发**（`account_manager.py`）
   - `snapshots()` 中使用 `asyncio.Semaphore(3)` 限制同时 `inspect_session_state()` 数量，避免大量账号同时检测导致卡顿。

2. **动态调度器轮询间隔**（`research_scheduler.py`）
   - `_run_loop()` 中，若当前无 pending 任务且无到期 schedule，则休眠 5 秒；一旦 wake 或发现任务，回到 2 秒甚至更短。

3. **优化 Native Dashboard 刷新**（`native_dashboard.py`）
   - `refresh_all()` 仅刷新当前可见内部页签所需数据。
   - “账号环境”未激活时延长快照刷新间隔或暂停。
   - “信源对比”页不加入 5 秒轮询，仅手动点击或切换时刷新。

4. **数据库连接优化**（`research_store.py`）
   - 使用 `threading.local()` 为每个线程保留一个 SQLite 连接（WAL 模式已启用）。
   - 增加 `PRAGMA synchronous = NORMAL` 与 `PRAGMA cache_size = -32768`。
   - 补充索引：
     ```sql
     CREATE INDEX IF NOT EXISTS idx_research_tasks_job_status
     ON research_tasks(job_id, status);
     CREATE INDEX IF NOT EXISTS idx_research_results_task
     ON research_results(task_id);
     CREATE INDEX IF NOT EXISTS idx_research_results_job_date
     ON research_results(job_id, collected_date);
     ```

5. **浏览器采集轮询优化**（`embedded_browser_client.py`、`browser_client.py`）
   - 在不影响采集的前提下，将无结果轮询间隔从 0.2s 提高到 0.5s。
   - `_expand_references` 中当已收集到 `expected` 条或 `expected == 0` 时提前退出循环。
   - 调试快照默认仅在启用调试标志时写入，或限制 body 大小。

### ✅ 优化：触发豆包人机验证后账号卡住无处理

**现象**：实际采集时一旦豆包页面触发验证码/安全校验/人机验证，账号页面会卡住，既不暂停账号，也不提示用户人工处理；当前关键词任务一直挂起，直到所有任务跑完才按失败处理，期间调度器仍可能继续分配任务。

**已实现**：
- 补强验证码检测：除 `body.innerText` 外，新增 iframe URL、九宫格图片容器、拖拽元素等视觉/结构检测。
- `chat()` 检测到验证码后不再抛异常，而是标记 `_needs_captcha=True` 并继续等待；验证码清除后原提问继续运行。
- 调度器 `_run_task()` 每 3 秒检查 `_needs_captcha`，一旦为真立即 `pause_account(account_id, 1800, ...)` 并触发 UI 跳转。
- 账号卡片显示“已暂停—需处理验证”并展示 `pause_reason` tooltip。
- 隐藏账号触发验证时自动恢复标签可见并 `bring_to_front()`。

**涉及文件**：
- `src/doubao2api/selectors.py`
- `src/doubao2api/embedded_browser_client.py`
- `src/doubao2api/research_scheduler.py`
- `src/doubao2api/account_manager.py`
- `src/doubao2api/native_dashboard.py`
- `tests/test_research_scheduler.py`

> 服务端 Playwright 模式（`browser_client.py`）暂未处理，后续按需补充。

---

## 数据模型/表变更汇总

| 层级 | 变更 |
|------|------|
| `Settings` (`config.py`) | 新增 `account_tab_hidden: dict[str, bool]`（账号标签显示/隐藏，已实现） |
| SQLite | 已新增 `research_job_templates` 表、`research_schedules` 表；已新增 `idx_research_schedules_due`；计划新增 `idx_research_tasks_job_status`、`idx_research_results_task`、`idx_research_results_job_date` |

---

## API/UI 变更汇总

### 新增后端 API

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

### 新增 UI

- **Native**：新增“定时任务”页签（任务模板管理 + 触发计划管理）。
- **Web**：已扩展为 8 页签（新建采集、账号环境、历史任务、采集结果、长尾信源、信源对比、定时任务、平台信息）。
- **Native**：账号环境卡片新增“隐藏标签/显示标签”按钮，支持隐藏后仍保持账号激活并在切换标签时恢复显示。

---

## 测试计划

### 单元测试

- `config.py`：`Settings` 序列化/反序列化 `account_tab_hidden`；重命名/删除后字典同步。
- `research_store.py`：
  - `interval` / `once` / `daily` 下次执行时间计算。
  - 任务模板 CRUD 与级联删除。
  - `create_job_from_schedule` 按模板最新配置生成 job/tasks。
  - 触发计划启用/禁用、到期查询、推进下一次执行时间。
- `tests/test_research_api.py`：Web 端新增 API（历史任务操作、结果筛选/导出、长尾分析、信源对比、平台信息导入、定时任务立即执行）。
- `tests/test_account_manager.py`：账号标签隐藏状态持久化、rename/delete 迁移、snapshot 返回 `tab_hidden`。
- `tests/test_api.py`：`POST /admin/api/accounts/{account_id}/tab-hidden` 接口测试。

### 手动测试

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

---

## 风险与兼容性

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

## 推荐实施顺序

```
Phase 1（Web UI 对齐） ✅ 已完成 → Phase 2（账号标签显示/隐藏切换） ✅ 已完成 → Phase 3（性能专项）
```

每完成一个 Phase，先跑全量测试并验收，再进入下一个。

当前剩余顺序：
1. **性能优化**：账号快照并发限制 → 动态调度休眠 → 数据库连接复用/索引 → UI 刷新频率 → 浏览器轮询优化。

---

## 验收命令

```cmd
cd /d "D:\ai-source-capturer\doubao-keyword-collector"
python -m pytest tests/ -q
python -m ruff check .
python -m ruff format --check src/doubao2api tests
```

---

## 备注

- `AI-UNDERSTANDING.md` 与 `UNDERSTANDING.md` 已随 Phase 1/2 完成后更新；后续随 Phase 3 完成后继续补充。
- 定时任务采用“任务模板 + 触发计划”两层模型：模板保存采集配置（不保存账号），计划保存触发规则并引用模板；计划触发时按模板最新配置生成一次性 `research_jobs`，账号由调度器按现有 LRU 逻辑动态选择。
