# 开发路线图

> 记录已完成事项与下一阶段实施计划。
> 最近一次更新：2026-08-12

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

---

## 待完成

### Phase 2：账号置顶

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

---

## 数据模型/表变更汇总

| 层级 | 变更 |
|------|------|
| `Settings` (`config.py`) | 新增 `account_pinned: dict[str, bool]`（账号置顶，尚未实现） |
| SQLite | 已新增 `research_job_templates` 表、`research_schedules` 表；已新增 `idx_research_schedules_due`；计划新增 `idx_research_tasks_job_status`、`idx_research_results_task`、`idx_research_results_job_date` |

---

## API/UI 变更汇总

### 新增后端 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/admin/api/accounts/{account_id}/pin` | 设置账号置顶状态（尚未实现） |
| DELETE | `/admin/api/research/jobs/{job_id}` | 删除历史任务 |
| POST | `/admin/api/research/jobs/{job_id}/rename` | 重命名历史任务 |
| POST | `/admin/api/research/results/sync-platform-info` | 按最新平台规则回填旧记录平台类型 |
| GET | `/admin/api/research/results/keywords` | 结果关键词下拉 |
| GET | `/admin/api/research/results/jobs` | 结果任务下拉 |
| POST | `/admin/api/research/results/source-comparison` | A/B 日期区间信源对比 |
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
- **Native/Web**：账号置顶开关（尚未实现）。

---

## 测试计划

### 单元测试

- `config.py`：`Settings` 序列化/反序列化 `account_pinned`；重命名/删除后字典同步。
- `research_store.py`：
  - `interval` / `once` / `daily` 下次执行时间计算。
  - 任务模板 CRUD 与级联删除。
  - `create_job_from_schedule` 按模板最新配置生成 job/tasks。
  - 触发计划启用/禁用、到期查询、推进下一次执行时间。
- `tests/test_research_api.py`：Web 端新增 API（历史任务操作、结果筛选/导出、长尾分析、信源对比、平台信息导入、定时任务立即执行）。
- `desktop.py` / `account_manager.py`：置顶账号 tab 插入位置（dashboard 0，置顶 1）。

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

**账号置顶**
1. 创建多个账号，分别开启/关闭置顶。
2. 关闭后重新打开账号，置顶账号出现在“采集管理中心”右侧第 1 位。
3. 取消置顶后标签移到非置顶区末尾。
4. 重命名/删除账号后置顶状态正确迁移/清除。

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
Phase 1（Web UI 对齐） ✅ 已完成 → Phase 2（账号置顶） → Phase 3（性能专项）
```

每完成一个 Phase，先跑全量测试并验收，再进入下一个。

当前剩余顺序：
1. **账号置顶**：配置层 → 账号池 → Desktop tab 管理 → Native UI → Web UI/API。
2. **性能优化**：账号快照并发限制 → 动态调度休眠 → 数据库连接复用/索引 → UI 刷新频率 → 浏览器轮询优化。

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

- `AI-UNDERSTANDING.md` 与 `UNDERSTANDING.md` 已随本次改动更新；后续随 Phase 1/2 完成后继续补充。
- 定时任务采用“任务模板 + 触发计划”两层模型：模板保存采集配置（不保存账号），计划保存触发规则并引用模板；计划触发时按模板最新配置生成一次性 `research_jobs`，账号由调度器按现有 LRU 逻辑动态选择。
