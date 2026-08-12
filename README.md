# 豆包关键词资料采集器（开源版）

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)](https://github.com/yogurt-42/doubao-keyword-collector)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

> 本地优先的非官方豆包关键词调研工具。
> 批量提问、自动展开参考资料、提取链接与平台信息，存入本地 SQLite，支持筛选、导出 Excel、信源对比与长尾分析。

---

## 📑 目录

- [✨ 主要功能](#-主要功能)
- [🖥️ 两种运行模式](#️-两种运行模式)
- [🚀 快速开始](#-快速开始)
- [📖 使用流程](#-使用流程)
- [📁 项目结构](#-项目结构)
- [🛡️ 数据与隐私](#️-数据与隐私)
- [🔧 采集口径](#-采集口径)
- [🧑‍💻 开发](#-开发)
- [📄 许可证](#-许可证)

---

## ✨ 主要功能

| 功能 | 说明 |
| --- | --- |
| 关键词导入 | 支持手工粘贴、`.xlsx` / `.csv` / `.tsv` 导入；自动识别常见表头 |
| 多账号调度 | 多账号轮询分配，同一账号一次只执行一个关键词，不同账号可并行 |
| 桌面端标签 | 每个账号独立 Qt WebEngine 页签；支持隐藏/显示标签，隐藏后仍保持激活 |
| 自动采集 | 自动“新对话 → 填词 → 发送 → 等待回答 → 展开参考资料 → 保存链接” |
| 实时保存 | 每识别一条参考资料立即写入 SQLite，不必等任务结束 |
| 结果增强 | 信源分布 Top 20、信源对比（A/B 任务群）、长尾信源四象限分析 |
| 平台规则库 | URL → 平台名 → 平台类型映射，支持 Excel 导入扩展 |
| 历史任务 | 查看、导出 Excel、重命名、删除、同步平台信息 |
| 风控保护 | 检测到验证码或疑似风控时暂停账号 30 分钟，不绕过验证 |

---

## 🖥️ 两种运行模式

| 模式 | 命令 | 入口 | 适用场景 |
| --- | --- | --- | --- |
| 桌面版 | `doubao-keyword-collector` / `dkc` | `src/doubao2api/windows_entry.py` | 原生 Qt 窗口，推荐日常使用 |
| 服务端 | `doubao-account-manager` | `src/doubao2api/__main__.py` | FastAPI + Web 管理端，浏览器访问 |

服务端默认打开 `http://127.0.0.1:9090/admin`。

---

## 🚀 快速开始

需要 **Python 3.10+**。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[desktop]"
doubao-keyword-collector
```

Linux / macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[desktop]"
doubao-keyword-collector
```

### 命令速查

```powershell
# 桌面版
doubao-keyword-collector
# 或简写
dkc

# 服务端
doubao-account-manager
```

---

## 📖 使用流程

1. **账号登录**：进入“账号环境”，创建账号并在顶部页签中手动登录豆包。
2. **创建任务**：在“新建采集”粘贴或导入关键词，设置采集间隔与尝试次数。
3. **查看结果**：在“采集结果”筛选、查看信源分布，或导出 Excel。
4. **深度分析**：使用“信源对比”比较两个任务群，或用“长尾信源”发现垂直平台。

### 关键词 Excel 最简格式

| 关键词 |
| --- |
| 新能源汽车销量 |
| 家庭储能市场 |

### 每个关键词的执行步骤

1. 切换到选定账号页签，点击“新对话”。
2. 写入关键词并点击发送。
3. 等待本次回答完成。
4. 展开“搜索 X 个关键词、参考 X 篇”及全部“展开更多”。
5. 保存资料链接、平台名称与平台类型，按间隔继续下一个关键词。

> 未开启定时任务时，第一个关键词创建后立即调度；关键词间隔仅用于后续关键词。

---

## 📁 项目结构

```
doubao-keyword-collector/
├── src/doubao2api/
│   ├── windows_entry.py          # 桌面 EXE 入口
│   ├── __main__.py               # 服务端入口
│   ├── desktop.py                # Qt 主窗口与账号标签管理
│   ├── native_dashboard.py       # 原生管理面板
│   ├── account_manager.py        # 账号池
│   ├── research_scheduler.py     # 任务调度器
│   ├── research_store.py         # SQLite 数据层
│   ├── research_platforms.py     # 平台规则库
│   ├── research_export.py        # Excel 导出
│   ├── server.py                 # FastAPI 接口
│   └── static/index.html         # Web 管理端
├── tests/                        # pytest 测试
├── README.md                     # 本文件
├── AI-UNDERSTANDING.md           # AI 快速上下文
├── UNDERSTANDING.md              # 详细技术参考
└── ROADMAP.md                    # 开发路线图
```

---

## 🛡️ 数据与隐私

程序默认把浏览器环境和 `research.sqlite3` 数据库保存在：

| 系统 | 默认路径 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\DoubaoAccountManager` |
| Linux / macOS | `$XDG_DATA_HOME/doubao-account-manager` 或 `~/.local/share/doubao-account-manager` |

- 可通过 `DOUBAO_DATA_ROOT` 环境变量指定其他目录。
- 桌面版不启动网页管理服务。
- 兼容接口默认仅允许本机访问。
- 程序不会把 Cookie、登录状态或机器信息发送给作者服务器。

---

## 🔧 采集口径

资料链接只来自豆包页面思考过程中的以下元素：

- `a[data-tool-call-item-id*="-result-"]`
- `a[data-thinking-box-tool-call="true"]`

程序会为每个关键词点击“新对话”，避免把上一条回答的资料混入当前关键词。如果页面声明的参考资料数量大于实际展开数量，该次会视为失败并在设定次数内重试。

豆包网页结构更新后，定位规则可能需要随之调整。

---

## 🧑‍💻 开发

```powershell
cd /d "D:\ai-source-capturer\doubao-keyword-collector"
python -m pip install -e ".[dev]"
python -m pytest tests/ -q
ruff check .
ruff format --check .
```

---

## 📄 许可证

重建源码使用 [MIT License](LICENSE)。第三方组件、网站内容和商标不因此获得重新许可。

---

> 本项目与字节跳动 / 豆包无官方关系，仅用于本地关键词调研与学习。
