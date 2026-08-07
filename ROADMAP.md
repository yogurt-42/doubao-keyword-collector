# 开发路线图

> 记录已完成事项与下一阶段实施计划。
> 最近一次更新：2026-08-07

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

---

## 待完成

### Phase 1：Web UI 与 Native 对齐

**目标**：让 Web 管理端的结果页具备与 Native 端一致的数据可视化能力。

**文件**：
- `src/doubao2api/static/index.html`
- `src/doubao2api/server.py`（如有需要）

**内容**：
- 结果面板整体可滚动
- 信源分布与占比（Top 20 / “其他” / 查看全部弹窗）
- 长尾信源分析面板
- 结果表格增加“平台类型”列
- （可选）平台信息管理入口

---

### Phase 2：账号置顶

**目标**：让 Web 管理端的结果页具备与 Native 端一致的数据可视化能力。

**文件**：
- `src/doubao2api/static/index.html`
- `src/doubao2api/server.py`（如有需要）

**内容**：
- 结果面板整体可滚动
- 信源分布与占比（Top 20 / “其他” / 查看全部弹窗）
- 长尾信源分析面板
- 结果表格增加“平台类型”列
- （可选）平台信息管理入口

---

### Phase 3：账号置顶

**目标**：在“账号环境”页为每个账号增加置顶开关，置顶账号标签固定在“采集管理中心”右侧。

**涉及文件**：
- `src/doubao2api/config.py`：`Settings.account_pinned`
- `src/doubao2api/account_manager.py`：`set_pinned()`、snapshot 返回 `pinned`
- `src/doubao2api/desktop.py`：置顶标签插入到 index 1
- `src/doubao2api/native_dashboard.py`：账号卡片开关
- `src/doubao2api/models.py` + `server.py` + `static/index.html`：API 与 Web 端

**详细任务**：

1. **持久化配置**（`config.py`）
   - `Settings` 新增 `account_pinned: dict[str, bool] | None = None`。
   - `__post_init__` 中初始化为空字典。

2. **账号池支持**（`account_manager.py`）
   - 新增 `set_pinned(account_id: str, pinned: bool)`，更新 `settings.account_pinned` 并保存。
   - `snapshot()` 与 `_snapshot_error_state()` 返回 `"pinned": bool`。
   - `rename_account()` 迁移 `account_pinned` key；`delete_account()` 删除对应 key。

3. **主窗口标签管理**（`desktop.py`）
   - 在 `QtBrowserBridge._open_account()` 打开账号时：
     - 若账号在 `account_pinned` 中，使用 `self.window.tabs.insertTab(1, view, ...)`；
     - 否则仍用 `addTab`。
   - 置顶状态变化时，Native UI 触发标签重排。

4. **Native Dashboard 账号卡片开关**（`native_dashboard.py`）
   - 在 `_account_card()` 状态徽章右侧增加 `QCheckBox`（“置顶标签”）。
   - 初始状态读取 `row["pinned"]`；切换时调用 `set_pinned()` 并触发刷新/重排。

5. **Web Dashboard 与后端 API**
   - 新增 `AccountPinRequest(BaseModel)`，字段 `pinned: bool`。
   - 新增 `POST /admin/api/accounts/{account_id}/pin`，调用 `account_pool.set_pinned()`。
   - Web 端账号卡片增加置顶开关并调用新接口。
   - Web 端没有主窗口 tab，开关仅持久化并在 UI 中显示；实际置顶排序仅在 Desktop 模式生效。

---

### Phase 4：定时任务

**目标**：新增“定时任务”页签，支持按间隔、一次性、每日定时自动触发关键词采集。

**涉及文件**：
- `src/doubao2api/research_store.py`：新增 `research_schedules` 表及 CRUD 方法
- `src/doubao2api/research_scheduler.py`：`_check_schedules()` 集成到调度循环
- `src/doubao2api/native_dashboard.py` + `static/index.html`：定时任务页面
- `src/doubao2api/models.py` + `server.py`：后端 API

**详细任务**：

1. **数据库表**（`research_store.py`）

   新增表 `research_schedules`：

   ```sql
   CREATE TABLE IF NOT EXISTS research_schedules (
       id TEXT PRIMARY KEY,
       name TEXT NOT NULL,
       enabled INTEGER NOT NULL DEFAULT 1,
       schedule_type TEXT NOT NULL,          -- 'interval' | 'once' | 'daily'
       schedule_value TEXT NOT NULL,         -- 秒数 / ISO 时间 / HH:MM
       next_run_at TEXT NOT NULL,
       keywords_json TEXT NOT NULL,
       prompt_template TEXT NOT NULL,
       account_ids_json TEXT NOT NULL,
       max_attempts INTEGER NOT NULL,
       interval_seconds INTEGER NOT NULL,    -- 同 research_jobs 的关键词间隔
       account_cooldown_seconds INTEGER NOT NULL DEFAULT 0,
       created_at TEXT NOT NULL,
       updated_at TEXT NOT NULL,
       last_run_at TEXT,
       last_job_id TEXT,
       run_count INTEGER NOT NULL DEFAULT 0,
       last_error TEXT NOT NULL DEFAULT ''
   );
   CREATE INDEX IF NOT EXISTS idx_research_schedules_due
   ON research_schedules(enabled, next_run_at);
   ```

   新增方法：
   - `create_schedule(...)` / `list_schedules()` / `get_schedule(id)` / `update_schedule(...)`
   - `toggle_schedule(id, enabled)` / `delete_schedule(id)`
   - `due_schedules(limit=20)`：查询 `enabled=1 AND next_run_at <= now`。
   - `create_job_from_schedule(schedule_id)`：使用 schedule 的参数调用现有 `create_job()`。
   - `advance_schedule(schedule_id, job_id, next_run_at)`：更新 `last_run_at`、`last_job_id`、`run_count`、`next_run_at`。

2. **下次执行时间计算**
   - 新增辅助函数 `_compute_next_run(schedule_type, schedule_value, after=None)`：
     - `interval`：每次执行后加 `schedule_value` 秒。
     - `once`：执行一次后 `enabled=0`。
     - `daily`：按 `HH:MM` 取下一个本地时间（当天或次日）。
   - 非法值抛出 `ValueError`。

3. **调度器集成**（`research_scheduler.py`）
   - `_run_loop()` 在 `_dispatch_due_tasks()` 前调用 `await self._check_schedules()`。
   - `_check_schedules()`：
     1. 查询 `due_schedules`。
     2. 对每个 schedule 调用 `create_job_from_schedule()`。
     3. 调用 `advance_schedule()` 计算并保存下一次执行时间。
     4. 调用 `self.wake()` 立即分发新任务。
   - 顺序执行，避免并发冲突；禁用 schedule 不执行；过期 schedule 执行一次后按规则重新计算。

4. **Native Dashboard 定时任务页面**（`native_dashboard.py`）
   - `_build_ui()` 中“信源对比”后新增 `self.schedules_page = self._build_schedules_page()`，并 `addTab(..., "定时任务")`。
   - `_build_schedules_page()` 包含：
     - 表单区：名称、触发类型（间隔/每日/一次性）、参数、关键词输入/导入、提问模板、账号选择、最大尝试次数、关键词间隔。
     - 列表区：schedule 卡片，显示名称、下次执行、上次执行、运行次数、启用/禁用、删除、立即执行。
   - 新增 `create_schedule()`、`toggle_schedule()`、`delete_schedule()`、`run_schedule_now()`。

5. **Web Dashboard 定时任务页面**（`static/index.html`）
   - `<nav>` 中“信源对比”后新增 `<button class="tab" data-page="schedules">定时任务</button>`。
   - 新增 `<main id="schedules">` 页面，结构与 Native 端类似。
   - 新增前端函数调用 `/admin/api/research/schedules*`。

6. **后端 API**（`models.py`、`server.py`）
   - 新增模型：`ResearchScheduleCreateRequest`、`ResearchScheduleUpdateRequest`、`ResearchScheduleToggleRequest`。
   - 新增端点：
     - `POST /admin/api/research/schedules`
     - `GET /admin/api/research/schedules`
     - `GET /admin/api/research/schedules/{schedule_id}`
     - `POST /admin/api/research/schedules/{schedule_id}/toggle`
     - `DELETE /admin/api/research/schedules/{schedule_id}`
     - `POST /admin/api/research/schedules/{schedule_id}/run`（立即执行一次）
   - 校验：`prompt_template` 必须包含 `{keyword}`；keywords 非空；`max_attempts` 1-3。

---

### Phase 5：性能优化专项

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

---

## 数据模型/表变更汇总

| 层级 | 变更 |
|------|------|
| `Settings` (`config.py`) | 新增 `account_pinned: dict[str, bool]` |
| `account_manager.py` | snapshot 返回 `pinned`；新增 `set_pinned` |
| SQLite | 新增 `research_schedules` 表；新增 `idx_research_schedules_due`、`idx_research_tasks_job_status`、`idx_research_results_task`、`idx_research_results_job_date` |

---

## API/UI 变更汇总

### 新增后端 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/admin/api/accounts/{account_id}/pin` | 设置账号置顶状态 |
| POST | `/admin/api/research/schedules` | 创建定时任务 |
| GET | `/admin/api/research/schedules` | 列表 |
| GET | `/admin/api/research/schedules/{id}` | 详情 |
| POST | `/admin/api/research/schedules/{id}/toggle` | 启用/禁用 |
| POST | `/admin/api/research/schedules/{id}/run` | 立即执行一次 |
| DELETE | `/admin/api/research/schedules/{id}` | 删除 |

### 新增 UI

- **Native**：账号卡片置顶开关；新增“定时任务”内部页签。
- **Web**：账号卡片置顶开关；新增“定时任务”页面。

---

## 测试计划

### 单元测试

- `config.py`：`Settings` 序列化/反序列化 `account_pinned`；重命名/删除后字典同步。
- `research_store.py`：
  - `interval` / `once` / `daily` 下次执行时间计算。
  - `create_job_from_schedule` 生成的 job/tasks 与手动创建一致。
- `desktop.py` / `account_manager.py`：置顶账号 tab 插入位置（dashboard 0，置顶 1）。

### 手动测试

**账号置顶**
1. 创建多个账号，分别开启/关闭置顶。
2. 关闭后重新打开账号，置顶账号出现在“采集管理中心”右侧第 1 位。
3. 取消置顶后标签移到非置顶区末尾。
4. 重命名/删除账号后置顶状态正确迁移/清除。

**定时任务**
1. 创建 interval=60 秒任务，1 分钟后自动生成并运行新 job。
2. 创建一次性任务，确认准时触发。
3. 创建 daily 任务，确认 `next_run_at` 为下一个 `HH:MM`。
4. 禁用/删除 schedule，确认不再触发。
5. 点击“立即执行”按钮，确认立即生成 job。

**性能**
1. 10 个以上账号时账号环境页刷新流畅。
2. 大结果集下结果页筛选和导出响应可接受。
3. 观察 SQLite WAL 与日志，确认没有频繁建连/断连。

---

## 风险与兼容性

| 风险 | 缓解 |
|------|------|
| 多账号置顶 tab 顺序冲突 | 按 `discover_account_ids()` 顺序依次占 1、2、3… 位；取消置顶后移到非置顶区末尾 |
| 定时任务重叠触发 | `_check_schedules()` 单线程顺序执行；先 create_job 再 advance_schedule |
| 数据库连接线程安全 | `threading.local()` 保证每线程独立连接 |
| 性能改动影响采集稳定性 | 先以 P0/P1 改动上线，保留旧值可回滚；充分手动测试 |
| 老版本 settings.json | 未知字段自动忽略；新增 `account_pinned` 不影响旧版本 |
| 老版本数据库 | `CREATE TABLE IF NOT EXISTS` 自动创建新表，无需迁移脚本 |

---

## 推荐实施顺序

```
Phase 1（Web UI 对齐） → Phase 2（账号置顶） → Phase 3（定时任务） → Phase 4（性能专项）
```

每完成一个 Phase，先跑全量测试并验收，再进入下一个。

Phase 3/4/5 的内部顺序：
1. **账号置顶**：配置层 → 账号池 → Desktop tab 管理 → Native UI → Web UI/API。
2. **定时任务**：数据库表 → `ResearchStore` 方法 → `ResearchScheduler` 集成 → API → Native/Web UI。
3. **性能优化**：账号快照并发限制 → 动态调度休眠 → 数据库连接复用/索引 → UI 刷新频率 → 浏览器轮询优化。

---

## 验收命令

```cmd
cd /d "D:\ai-source-capturer\doubao-keyword-collector"
python -m pytest tests/ -q
doubao-keyword-collector
```

---

## 备注

- `AI-UNDERSTANDING.md` 与 `UNDERSTANDING.md` 已随本次改动更新；后续随 Phase 1/2 完成后继续补充“长尾信源分析”等内容。
