# 📊 智数析言：智能数据分析 Agent

用自然语言提问，自动完成 SQL 查询、Python 分析与图表生成，并返回分析结论。

支持多数据源切换（内置示例库 + 上传 CSV/Excel）、多轮追问、图表可视化、历史回看，是一条**从问题到结论**的完整数据分析流水线。

## 📸 项目概览

### Web 交互界面

![Web 界面](docs/img_web.png)


## ✨ 功能特性

- 🔗 **自然语言全链路**：从问题到结论，无需写 SQL 或 Python
- 🧭 **自动规划**：根据问题复杂度判断是否需要 Python 分析或图表生成
- 🔧 **自我纠错**：SQL 执行失败自动反馈给 LLM 重写，最多重试 3 次
- 🗂️ **多数据源管理**：内置示例库 + 上传 CSV/Excel 自动建表，前端一键切换
- 💬 **多轮追问**：结合历史上下文自动改写问题（"那华东呢？" → "华东地区销售额是多少？"）
- 🖥️ **Web + 命令行双入口**：浏览器可视化交互，或终端脚本调用
- 📚 **历史回看**：历史对话本地持久化，每条结果可点击回看（含图表、数据表、SQL）
- 🔍 **LangSmith 追踪**：全链路 LLM 调用可视化
- 🧩 **可扩展数据源**：基于 SQLAlchemy，一行配置接入 MySQL / PostgreSQL / DuckDB

## 🏗️ 系统架构

```
用户使用自然语言提问
    ↓
【改写】结合历史 → 补全为独立问题
    ↓
【规划】LLM 判断 need_sql / need_python / need_chart
    ↓
【SQL】Text-to-SQL + 执行 + 自我纠错
    ↓
【Python】(可选) pandas 计算派生指标
    ↓
【图表】(可选) 自动选图类型 + matplotlib 渲染
    ↓
【结论】合成自然语言分析结论
    ↓
输出：结论 + 图表 + 数据表 + SQL + 执行步骤
```

Agent 使用 **LangGraph** 构建状态机。六个节点（rewrite → planner → sql → python → chart → conclusion）通过 2 条条件边决定分支：SQL 失败时直接路由到 conclusion，Python 失败或不需要图表时跳过 chart。任一阶段失败都会用兜底提示词生成友好错误提示，而不是把异常抛给用户。

## 🛠️ 技术栈

| 模块 | 方案 | 说明 |
|---|---|---|
| 开发语言 | Python 3.11+ | |
| Agent 编排 | LangGraph 1.x | 状态机 + 条件边 |
| 大模型 | DeepSeek Chat | 兼容 OpenAI 接口，可替换 |
| 数据库抽象 | SQLAlchemy 2.x | 支持 SQLite / MySQL / PostgreSQL / DuckDB |
| 默认数据库 | SQLite | 零配置，文件级 |
| 数据处理 | pandas + numpy | Python 分析工具核心 |
| 可视化 | matplotlib | 自动选图类型 + 中文字体 |
| 后端服务 | FastAPI + Uvicorn | REST API |
| 前端 | Vue 3（本地 vendor） | 无构建步骤，开箱即用 |
| 配置管理 | python-dotenv + dataclass | API 层用 pydantic 做请求校验 |
| 链路追踪 | LangSmith（可选） | APAC 区域 |

## 🚀 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/slw-cuddlebear/Smart_Data_Analyst_Agent.git
cd Smart_Data_Analyst_Agent
```

### 2. 创建虚拟环境

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置环境变量

```bash
# Windows
Copy-Item .env.example .env

# macOS / Linux
cp .env.example .env
```

编辑 .env：

```env
DEEPSEEK_API_KEY=sk-你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

> DeepSeek API Key 申请地址：https://platform.deepseek.com

### 5. 生成示例数据

```bash
python data/generate_sample.py
python -m app.db.load_csv
```

- 第一步生成 500 行示例销售数据 data/sample.csv
- 第二步将其导入 SQLite 数据库 data/sample.db

### 6. 启动应用

```bash
uvicorn app.api.server:app --reload --port 8000
```

浏览器访问 http://localhost:8000

## 📖 使用说明

### 提问示例

| 问题 | 触发的工具链 |
|---|---|
| 各地区总销售额是多少？ | SQL |
| 2024 年上半年的订单金额总和是多少？ | SQL |
| 按月统计销售额，并计算环比增长率。 | SQL + Python |
| 各类别销售额占比，用饼图展示。 | SQL + Python + 图表 |
| 单价和销量用散点图展示关系。 | SQL + 图表 |

### 多轮追问

```
用户：各地区总销售额是多少？
Agent：华南最高约 206 万，华北次之……

用户：那华东呢？
Agent：[改写为"华东地区总销售额是多少？"] 华东约 156 万……

用户：换成饼图
Agent：[改写为"用饼图展示各地区销售额"] 图表已生成……
```

### 上传自己的数据

1. 点击顶部 **+ 上传 CSV / Excel**
2. 选择本地文件（支持 .csv / .xlsx / .xls）
3. 上传成功后下拉框自动切换到新数据源
4. 直接对新数据源提问

### 数据源切换

顶部下拉框切换数据源。**切换时会自动开启新会话**，不同数据源的对话历史互相隔离。

### 命令行模式

```bash
# 单次提问（不带记忆）
python main.py "各地区总销售额是多少？"

# 交互模式（带记忆）
python main.py
```

交互模式内置命令：exit 退出 / new 新会话 / history 查看历史 / help 帮助。

## 📊 测试与验收

### 自动化单元测试

`tests/` 下的测试全部**离线可跑**，不需要 API Key、不需要数据库。每次 push / PR 由 GitHub Actions 自动执行（`.github/workflows/tests.yml`）：

```bash
pip install -r requirements.txt
pip install pytest ruff

python -m pytest tests/ -v          # 全部为真断言，离线可跑
ruff check app tests --select F,E9  # 未使用 import / 语法级问题
```

| 测试文件 | 覆盖内容 |
|---|---|
| `tests/test_sql_tool_safety.py` | SQL 只读校验：多语句绕过拦截、写操作拦截、`created_at` 等列名不误杀、注释与字符串剥离、LIMIT 兜底、`raw_sql` 语义 |
| `tests/test_chart_tool_sandbox.py` | 图表代码沙箱：危险关键字拦截、受限 builtins、pandas/numpy IO 入口封闭、超时保护、危险代码不被执行 |
| `tests/test_chart_type_infer.py` | 图表类型推断：`kind=` 解析、matplotlib 函数识别、注释/字符串不干扰、退回关键词兜底 |
| `tests/test_text_utils.py` | 公共文本工具：字符串与注释剥离、语句切分（单一实现，两个工具共用） |
| `tests/test_registry_paths.py` | 数据源注册表：SQLite 相对路径存储与还原、历史绝对路径兼容、项目外路径保持绝对、真实注册表不含绝对路径 |

### 手工验收记录

以下为在示例数据（500 行销售订单）上**手工验收**的功能面，非自动化测试：

#### SQL 生成正确性

覆盖聚合、分组、条件过滤、日期范围、TopN、多条件叠加等场景。SQLite 的 strftime 与 MySQL 的 DATE_FORMAT 差异由 LLM 根据 schema 自适应，偶有失败通过自我纠错重写。

#### 多工具协作

- SQL 取数 → Python 计算占比、环比、中位数 → 图表渲染，链路贯通
- SQL 里禁止计算派生指标（如用 AVG 冒充中位数），统一交给 Python 处理

#### 多数据源隔离

上传 CSV 后注册为新数据源，验证：

- 上传的数据源 SQL 使用正确的表名和字段
- 切换数据源时，历史对话隔离
- 删除数据源时对应的历史与 data/uploads/{id}/ 目录一并清理

#### 会话记忆改写

| 输入 | 改写结果 |
|---|---|
| 那华东呢？ | 华东地区销售额是多少？ |
| 换成饼图 | 用饼图展示各地区销售额 |
| 销量前 3 的产品呢？ | 华东地区销量前 3 的产品是什么？ |

## 📁 项目结构

```
Smart_Data_Analyst_Agent/
├── .env                       # 本地配置（不提交）
├── .env.example               # 配置模板
├── requirements.txt
├── main.py                    # 命令行入口
│
├── app/
│   ├── config.py              # 配置加载
│   ├── llm.py                 # DeepSeek 客户端封装
│   │
│   ├── agent/                 # LangGraph 工作流
│   │   ├── state.py           # AgentState 定义
│   │   ├── prompts.py         # 集中管理所有提示词
│   │   ├── memory.py          # 会话记忆存储
│   │   └── graph.py           # 工作流编排
│   │
│   ├── tools/                 # Agent 工具集
│   │   ├── sql_tool.py        # Text-to-SQL + 自我纠错
│   │   ├── python_tool.py     # 受限沙箱执行 pandas
│   │   ├── chart_tool.py      # 图表代码生成 + matplotlib（同级沙箱）
│   │   └── text_utils.py      # 公共文本处理（字符串/注释剥离、语句切分）
│   │
│   ├── db/                    # 数据层
│   │   ├── registry.py        # 数据源注册表
│   │   ├── engine.py          # 按 ID 缓存 SQLAlchemy 引擎
│   │   └── load_csv.py        # CSV → SQLite 导入
│   │
│   ├── api/server.py          # FastAPI 服务
│   └── web/index.html         # 单页前端
│
├── docs/                      # 文件目录
│   ├── generate_sample.py     # 前端界面截图
│  
├── data/                      # 数据目录
│   ├── generate_sample.py
│   ├── sample.csv
│   ├── sample.db              # 不提交
│   ├── datasources.json       # 自动生成
│   └── uploads/               # 上传文件（不提交）
│
└── outputs/                   # 生成的图表（不提交）
```

## 🔌 扩展数据源

数据源基于 SQLAlchemy，任何 SQLAlchemy 支持的数据库都可以接入。编辑 data/datasources.json：

```json
{
  "id": "company_mysql",
  "name": "公司业务库",
  "type": "mysql",
  "url": "mysql+pymysql://readonly_user:password@192.168.1.10:3306/biz?charset=utf8mb4",
  "builtin": false,
  "created_at": "2026-09-18 10:00:00"
}
```

重启服务后，前端下拉框中即可选择。

| 数据库 | 连接串格式 | 驱动包 |
|---|---|---|
| SQLite | sqlite:///path/to/file.db | 内置 |
| MySQL | mysql+pymysql://user:pass@host:3306/db | pymysql |
| PostgreSQL | postgresql+psycopg2://user:pass@host:5432/db | psycopg2-binary |
| DuckDB | duckdb:///path/to/file.duckdb | duckdb-engine |

### 路径约定：项目内的 SQLite 存相对路径

`data/datasources.json` 里，**位于项目目录内**的 SQLite 数据源存的是相对路径：

```json
"url": "sqlite:///data/sample.db"
```

读取时由 `app/db/registry.py` 统一还原成绝对路径，调用方拿到的连接串始终可直接使用。

这样设计是为了让整个目录**可以整体搬迁**：复制到别的路径、别的机器，或者被别人 clone 下来，
都不会因为注册表里写死的绝对路径而启动失败（否则会报 `unable to open database file`）。
首次运行生成的默认注册表同样使用相对路径。

两点边界：

- **项目外**的数据库（例如自带的外部 db 文件）保持绝对路径不变，不做相对化
- 历史文件里已经写死的绝对路径**仍可正常读取**，并会在下一次写盘时自动规范化成相对路径
- MySQL / PostgreSQL / DuckDB 等非 SQLite 连接串不做任何处理

## 🎯 已实现 / 待优化

### 已实现

- [x] 自然语言 → SQL → Python → 图表 → 结论 全链路
- [x] LangGraph 状态机编排 + 条件路由
- [x] SQL 执行自我纠错（失败自动重写，最多 3 次）
- [x] 受限沙箱执行 pandas 与图表代码（黑名单 + 受限 builtins + 超时，见「已知局限」）
- [x] 自动选择图表类型 + 中文字体
- [x] 多数据源管理（内置 SQLite + 上传 CSV/Excel）
- [x] 会话记忆与多轮追问改写
- [x] Web 前端（数据源切换、历史回看、图表展示）
- [x] 命令行交互入口
- [x] LangSmith 全链路追踪（可选）
- [x] FastAPI REST 服务

### 待优化（v2 规划）

- [ ] 流式输出（SSE），结论逐字显示、步骤实时推送
- [ ] 导出分析报告（Markdown / PDF / Excel）
- [ ] Docker 化，docker-compose up 一键启动
- [ ] 会话历史后端持久化（Redis / SQLite）
- [ ] Schema RAG 检索，支持大库（>20 张表）
- [ ] 预置 PostgreSQL / DuckDB 数据源模板
- [ ] SQL 生成准确率评估集
- [ ] 前端从历史中间节点续接追问
- [ ] 用户认证与多租户隔离

## ⚠️ 已知局限

这些是我清楚知道不够硬的地方，写在明面上而不是等人问：

### 沙箱不是真隔离

`python_tool.py` 和 `chart_tool.py` 的沙箱是**进程内**的：正则黑名单 + 白名单 builtins + 受限命名空间 + 超时，**没有**用子进程 / 容器做真正的隔离。因此：

- 黑名单能被绕过（例如把危险方法名拼接后再调用），只挡得住直白写法
- 超时靠子线程 `join(timeout)`，只能让调用方停止等待，**杀不掉已经在跑的线程**；真隔离需要换成子进程 + 资源限制

正确做法是子进程执行 + 只读挂载 + CPU/内存上限，这块还没做。

### SQL 只读是应用层校验

`is_safe` 做的是关键字黑名单 + 单语句限制 + 兜底 LIMIT，能挡住 `SELECT 1; ATTACH DATABASE ...` 这类多语句绕过和常见写操作，但**数据库连接本身不是只读的**。更硬的做法是：

- SQLite：`file:...?mode=ro` 只读 URI + `PRAGMA query_only=1`
- MySQL / PostgreSQL：使用只读权限的专用账号

### 其他

- 会话记忆是进程内的（`memory.py` 模块级字典），重启即丢，多 worker 下不一致
- 无鉴权、无限流，上传接口没有大小上限
- `requirements.txt` 已按 `.venv` 实际安装版本精确 pin（`==`），但未提供 `requirements-dev.txt`，测试依赖（pytest / ruff）需单独安装
- `get_llm()` 用 `lru_cache(maxsize=1)` 缓存，`temperature` 参数不参与缓存键，传非 0 值时不会生效（当前调用全部使用默认 0.0，暂无实际影响）
- 前端展示的是 LLM 原始 SQL（`raw_sql`）；实际执行时会补 LIMIT 兜底，两者不一致时界面会折叠显示实际执行的 SQL

## 🐛 踩坑与解决

### 1. LangChain 1.x 与旧教程 API 不兼容

**现象**：按网上教程写 create_pandas_dataframe_agent，直接报 ImportError。

**原因**：pip 装到的是 LangChain 1.4.1 / LangGraph 1.2.11，与 0.x 时代的 API 差异较大。

**解决**：放弃老示例，改用 LangGraph 的 StateGraph 从头构建工作流，反而更清晰。工具层只依赖 langchain-core 的消息抽象，减少版本耦合。

### 2. langchain-sandbox 与 LangChain 1.x 冲突

**现象**：pip install -r requirements.txt 时 langchain-sandbox 从 0.0.6 被强制降到 0.0.3，且安装后无法正常使用。

**解决**：不再依赖该包，在 python_tool.py 里手写轻量沙箱——白名单 builtins + 关键字黑名单 + 命名空间隔离。虽不是绝对安全，但本地学习场景足够，且完全可控。

### 3. LangSmith 上传 trace 报 403 Forbidden

**现象**：LANGSMITH_TRACING=true 后每次运行都报 403 Client Error: Forbidden，网页端也看不到任何 project。

**原因**：注册时选了 **APAC 区域**，SDK 默认把数据发往 US 端点 https://api.smith.langchain.com，区域不匹配。

**解决**：在 .env 中显式指定端点：

```env
LANGSMITH_ENDPOINT=https://apac.api.smith.langchain.com
LANGCHAIN_ENDPOINT=https://apac.api.smith.langchain.com
```

同时在 LangSmith 网页端用 **APAC 域名** apac.smith.langchain.com 登录查看。

### 4. ValueError: The truth value of a DataFrame is ambiguous

**现象**：Python 工具返回 DataFrame 时，Agent 直接崩溃。

**原因**：代码里有 state.get("python_result") or "无" 的写法。当返回值是 DataFrame 时，or 会先对它做布尔判断，而 pandas 明确规定 DataFrame 的真假值不唯一（可能部分行真、部分行假），直接抛错。

**解决**：所有可能承载 DataFrame 的地方，改用 is not None 判断，封装 _stringify() 辅助函数：

```python
def _stringify(value) -> str:
    if value is None:
        return "无"
    return str(value)
```

### 5. SQL 里 LLM 用 AVG 冒充中位数

**现象**：问"各类别销售额的总额、均值、中位数是多少"，返回的 SQL 里有两列都是 AVG(amount)，第二列还起了 median_amount 的别名。

**原因**：SQLite 没有原生中位数函数，LLM 偷懒用 AVG 顶替。

**解决**：在 SQL 提示词里明确约束——**不要在 SQL 里计算占比、环比、中位数等派生指标，只负责取数**，派生计算统一交给 pandas 步骤。同时修改了 Python 工具的提示词，让它知道要算中位数。

### 6. 结论里出现"前 10 个月"的幻觉

**现象**：按月统计销售额（12 行数据），结论却说"前 10 个月"，让用户以为数据缺失。

**原因**：conclusion_node 只把前 10 行喂给 LLM，LLM 不知道数据其实有 12 行。

**解决**：改为**动态预览策略**——数据量小时全部给，大时才截断，并在提示词里明确标注"共 N 行，已全部展示"或"共 N 行，以下仅展示前 M 行"。同时加了硬约束："如标注'已全部展示'，不得再说'只展示了部分'"。

### 7. 没生成图表却说"图表已保存"

**现象**：规划器判断不需要画图，但结论最后加了"图表已保存"。

**原因**：提示词里那句"如果生成了图表，就提一句图表已保存"——LLM 把"如果"当成了固定套路，无条件加上。

**解决**：把 chart_info 从"文件路径或'无'"改为**显式的语义描述**："已生成，文件路径：xxx" 或 "本次未生成图表"。同时提示词改成"只有明确给出了文件路径时，才能说图表已保存"。

### 8. Vue / Tailwind CDN 在国内加载失败

**现象**：前端显示 {{ }} 原始模板，样式全无。

**原因**：unpkg.com 和 cdn.tailwindcss.com 在国内经常被墙或超时。

**解决**：把 Vue 下载到 app/web/vendor/vue.global.prod.js 本地加载；放弃 Tailwind，全部改用内联 CSS 手写。打开即用，无需构建。

### 9. 数据源切换时 SQL 用错了库

**现象**：上传 CSV 后注册为新数据源，查询时 SQL 仍然使用旧数据源的表名和字段。

**原因**：改 engine.py 支持多数据源时，只改了连接层，忘记把 datasource_id 透传到 sql_tool.py 的 build_schema_text() 里。

**解决**：从 AgentState → run() → sql_node → text_to_sql → build_schema_text 一路透传 datasource_id。这是典型的"改了底层忘改上层"。

### 10. Python 结果的 DataFrame 显示错位

**现象**：Python 分析返回 DataFrame 时，前端用等宽文本渲染，中文字符宽度是英文的两倍，导致列名和数据错位，表头"category"跟数据"电子产品"对不上。

**原因**：pandas 的 `str(DataFrame)` 按**字符数**算列宽，不按**显示宽度**算。中英文混排时视觉上必然歪。而且这个 str 是后端生成的死文本，前端无法重新排版。

**解决**：后端把 `python_result` 从字符串改为**结构化对象**，按三种类型返回：

- `{"type": "table", "columns": [...], "rows": [...]}`：DataFrame / Series
- `{"type": "json", "value": ...}`：dict / list
- `{"type": "text", "value": "..."}`：标量

前端按 type 分支渲染，`table` 走 HTML 表格（自动按单元格对齐），彻底解决错位。同时前端兼容了旧格式（localStorage 里存的字符串），老历史不会崩。

## 📝 开发日志

- **Phase 1 - 基础环境**：Python 虚拟环境、依赖安装、DeepSeek API 连通性验证
- **Phase 2 - 数据层**：SQLAlchemy 连接、示例数据生成、CSV 导入 SQLite
- **Phase 3 - SQL 工具**：Text-to-SQL + 安全校验 + 执行 + 自我纠错
- **Phase 4 - Python 工具**：受限沙箱执行 pandas 代码
- **Phase 5 - 图表工具**：LLM 生成绘图代码 + matplotlib 渲染
- **Phase 6 - Agent 编排**：LangGraph 状态机、条件路由、结论合成
- **Phase 7 - 会话记忆**：追问改写、session_id 透传
- **Phase 8 - 多数据源**：数据源注册表、上传接口、前端切换
- **Phase 9 - Web 前端**：FastAPI 服务、单页 Vue 应用、历史持久化
- **Phase 10 - 工程收尾**：LangSmith 追踪、README、目录整理
- **Phase 11 - 显示优化**：Python 结果结构化渲染（表格 / JSON / 文本），修复中文对齐问题
- **Phase 12 - 安全加固与工程化**：
  - 图表代码执行补齐与 Python 工具同级的防护（静态校验 + 受限 builtins + 封闭 pandas/numpy IO 入口 + 执行超时），并把字符串/注释剥离逻辑抽成 `text_utils` 单一实现
  - SQL 只读校验增加单语句约束（原先 `SELECT 1; ATTACH DATABASE ...` 可绕过），修正 `created_at` 等列名被误判为写操作的问题，并加 LIMIT 兜底
  - 数据源注册表改存相对路径，支持目录整体搬迁（原先写死绝对路径，换目录即报 `unable to open database file`）
  - 补齐离线单元测试与 GitHub Actions（原先无测试、无 CI）
  - 接出 `chart_type` 与 `raw_sql`，图表卡片显示图类型，SQL 卡片展示 LLM 原始 SQL
  - 依赖按实际版本锁定，清理未使用依赖

## 📄 License

MIT

---

如果这个项目对你有帮助，欢迎 Star ⭐
