# nl2sql-agent

Knowledge-graph NL2SQL agent（自然语言问数助手）

用自然语言问业务数据：系统完成问题解析、图谱与向量检索、选表、生成并校验只读 SQL、执行查询，并支持基于结果出图和生成分析报告。后端为 FastAPI + Google ADK，检索层为 Neo4j 知识图谱与 Milvus 向量库，前端为 Angular。

## 功能概览

- 元数据维护：表、字段、枚举、表关系维护。
- 业务知识维护：业务概念、规则和关联表字段维护。
- 历史 SQL 管理：沉淀可复用 SQL 案例，并建立 SQL 与表字段关系。
- Excel 数据导入：从 `resources/data` 导入表结构、枚举值、业务知识、历史 SQL 和表关系。
- 智能问数：用户输入自然语言问题，系统规划下一步、检索上下文、选择候选表、生成只读 SQL、校验并执行查询。
- 分析图表：在已有查询结果快照上规划图表，按维度聚合展示，不另写 GROUP BY SQL。
- 分析报告：按「总体情况 / 分类分析（含图表）/ 分析结论」生成 Word 报告，可在对话中下载。
- 模型套餐切换：主套餐限流或额度用尽后，自动改走火山方舟 Agent Plan 再试一次。
- 执行轨迹展示：前端展示规划、检索、选表、SQL、图表、报告等步骤及耗时。

## 技术栈

后端：

- Python 3.12
- FastAPI
- Google ADK
- LiteLLM
- SQLAlchemy asyncio
- sqlglot
- pandas / openpyxl
- python-docx / matplotlib

数据与检索：

- Neo4j：图谱数据，存储表、字段、枚举、知识、SQL 及关系。
- Milvus：向量数据，存储表描述、字段描述、枚举值、业务知识等 embedding。
- SQLite / MySQL：业务查询数据库。

前端：

- Angular 21
- Angular Material
- Tailwind CSS
- RxJS
- ngx-markdown / PrismJS
- Neo4j NVL 图谱可视化组件

## 目录结构

```text
项目根目录
├─ adk_agents/              # Google ADK Agent 工作流
│  ├─ extractor/            # 用户问题检索意图抽取 Agent
│  └─ copilot/              # 智能问数主 Workflow（规划、问数、出图、报告）
├─ data/                    # 本地运行数据、示例库、用户工作区配置
│  ├─ auth/                 # 用户与 workspace 配置
│  ├─ reports/              # 分析报告 Word 产出，已加入 .gitignore
│  └─ dev.sqlite            # 本地 SQLite 示例业务库
├─ resources/
│  ├─ data/                 # Excel 初始化数据
│  └─ cache/                # 开发缓存，缓存 LLM/embedding 结果
├─ scripts/
│  └─ init.py               # 全量初始化 Neo4j 和 Milvus 的入口
├─ server/                  # FastAPI 应用
│  ├─ app.py                # 后端入口
│  ├─ routers/              # API 路由
│  ├─ chat/                 # SSE 协议和 ADK Runtime 封装
│  ├─ auth/                 # 本地认证与 workspace 解析
│  └─ schemas/              # API schema
├─ tools/                   # 后端核心工具层
│  ├─ graph/                # Neo4j 图谱模型和 CRUD
│  ├─ db.py                 # 业务数据库连接、SQL 执行
│  ├─ embedding.py          # embedding 调用
│  ├─ ingest.py             # Excel 导入逻辑
│  ├─ llm.py                # 普通 LLM 调用与主/备用套餐切换
│  ├─ retrieval.py          # 语义检索与上下文组装
│  ├─ sql.py                # SQL 解析、血缘提取、只读校验
│  └─ vector.py             # Milvus collection 和向量写入/检索
└─ web/                     # Angular 前端
```

## 核心问数流程

智能问数主入口在 `adk_agents/copilot/agent.py`。每轮用户消息先解析问题，再由 planner 决定下一步：问数、出图、写报告、闲聊或结束。问数内部仍是固定管道；出图和报告都基于本轮已有的查询结果快照，不另写聚合 SQL。

```text
用户问题
  -> extractor 抽取检索 query / keywords / intent
  -> planner 选择下一步：query / chart / report / chitchat / stop
  -> 问数：search（Milvus 召回 + Neo4j 补全）
       -> table_selector 选择候选表
       -> load_sql_schema_context 加载完整 schema
       -> sql_generator 生成 SQL
       -> validate_sql 校验只读、安全性、表字段和方言
       -> execute_sql 执行查询，写入结果快照
       -> 回到 planner
  -> 出图：chart_planner 基于快照规划图表
  -> 报告：report_planner 生成三部分正文，写出 Word
  -> FastAPI SSE 流式返回前端
```

说明：

- 清单或统计类问题默认先问数，不会因为“可以画图/可以写报告”就直接出图或出报告。
- 用户直接要求出图或分析报告、但还没有查询快照时，planner 会先问数，再继续出图或出报告。
- 同一会话里可以说「生成分析报告」「报告呢」，系统会尽量复用已有查询结果，不必重新取数。
- 分析报告约定三个部分：总体情况、分类分析（可嵌入图表）、分析结论。文件落到 `data/reports/{file_id}.docx`。
- 每轮用户消息最多规划 5 次。闲聊是终端节点，不再交回规划。

后端聊天接口：

```text
POST /api/chat/sessions/{session_id}/messages
GET  /api/chat/reports/{file_id}
```

前端通过 `fetch + ReadableStream` 消费 SSE 事件，事件处理在 `web/src/app/features/chat/chat-stream.ts`。对话页会把多次规划合并为一个「规划步骤」，并展示检索、选表、SQL、图表和报告节点。

## 环境变量

项目通过根目录 `.env` 读取配置。`.env` 包含密钥，已加入 `.gitignore`，不要提交真实密钥。

### 大模型配置

聊天、规划、选表、生成 SQL、出图和报告都走同一套入口：`adk_agents/common.py:get_llm_model()`。通用文本调用在 `tools/llm.py:chat()`，与智能体共用主/备用套餐配置。

主套餐按火山引擎 Coding Plan OpenAI 兼容接口配置：

```env
ARK_API_KEY=替换为你的 Coding Plan API Key

LLM_MODEL_NAME=openai:ark-code-latest
OPENAI_API_BASE=https://ark.cn-beijing.volces.com/api/coding/v3
OPENAI_API_KEY=${ARK_API_KEY}
```

备用套餐走 Agent Plan。主套餐遇到限流、突发保护（如 `request burst`）或额度用尽，且这轮还没产出内容时，会自动换套餐再试一次；同套餐不会连打。Agent Plan 必须使用专属 Key，不要和 Coding Plan / 普通方舟 Key 混用。未配齐备用地址和 Key 时不会切换。

```env
ARK_AGENT_PLAN_API_KEY=替换为你的 Agent Plan 专属 Key
LLM_FALLBACK_MODEL_NAME=openai:ark-code-latest
LLM_FALLBACK_API_BASE=https://ark.cn-beijing.volces.com/api/plan/v3
LLM_FALLBACK_API_KEY=${ARK_AGENT_PLAN_API_KEY}
```

`LLM_FALLBACK_MODEL_NAME` 需在 Agent Plan 套餐支持矩阵里。`ark-code-latest` 是 Auto 模型；也可以改成套餐中已开通的其它模型，例如 `openai:deepseek-v4-flash`。

修改 `.env` 后必须重启后端，备用套餐才会生效。Embedding 仍走下面的 `EMBEDDING_*`，默认不切 Agent Plan。

表结构导入时如果开启表描述生成，会调用大模型：

```env
LLM_GENERATE_TABLE_DESCRIPTION=true
```

如果想加快初始化、减少模型调用，可以临时改为：

```env
LLM_GENERATE_TABLE_DESCRIPTION=false
```

### Embedding 配置

```env
EMBEDDING_MODEL_NAME=openai:doubao-embedding-vision
EMBEDDING_API_BASE=https://ark.cn-beijing.volces.com/api/coding/v3
EMBEDDING_API_KEY=${ARK_API_KEY}
EMBEDDING_MODE=litellm
```

注意：当前火山 embedding 实际返回 2048 维向量，因此 Milvus collection 维度需要是：

```env
VECTOR_DIMENSION=2048
```

### Milvus 配置

```env
VECTOR_COLLECTION_TABLE=TYWS_TABLE
VECTOR_COLLECTION_ENUM=TYWS_ENUM
VECTOR_COLLECTION_KNOWLEDGE=TYWS_KNOWLEDGE
VECTOR_COLLECTION_SQL=TYWS_SQL
VECTOR_DIMENSION=2048
VECTOR_URI=http://127.0.0.1:29531
VECTOR_TIMEOUT=5
VECTOR_TOPK_TABLE=10
VECTOR_TOPK_ENUM=8
VECTOR_TOPK_KNOWLEDGE=8
VECTOR_TOPK_SQL=5
```

向量库按数据类型拆分为 4 个 Milvus collection：

```text
TYWS_TABLE      TABLE / COLUMN
TYWS_ENUM       ENUM
TYWS_KNOWLEDGE  KNOWLEDGE
TYWS_SQL        SQL
```

检索时会分别从 4 个 collection 召回，再合并到检索上下文中，避免表字段或枚举结果挤占业务知识、历史 SQL 的召回名额。

### Neo4j 配置

```env
GRAPH_DATABASE=neo4j
GRAPH_URI=neo4j://localhost:17688
GRAPH_USER=neo4j
GRAPH_PASSWORD=替换为你的 Neo4j 密码
GRAPH_SCHEMA_NS=TYWS.NS
```

### 业务数据库配置

业务数据库是 Agent 生成 SQL 后实际查询的数据源，不是 Python 自带环境。

`DATABASE_URL` 优先级最高。只要它非空，代码会直接使用它，下面的 `DATABASE_HOST`、`DATABASE_PORT` 等配置会被忽略。

本地 SQLite 示例配置：

```env
DATABASE_URL=sqlite+aiosqlite:///C:/path/to/TYWS/data/dev.sqlite
DATABASE_BACKEND=sqlite
SQL_READ_DIALECT=sqlite
```

MySQL 配置示例：

```env
DATABASE_URL=
DATABASE_HOST=localhost
DATABASE_PORT=13306
DATABASE_NAME=zh_YB_ALL
DATABASE_USER=dev
DATABASE_PASSWORD=你的密码
DATABASE_DRIVER=mysql+aiomysql
DATABASE_BACKEND=mysql
SQL_READ_DIALECT=mysql
```

通用超时：

```env
DATABASE_QUERY_TIMEOUT_S=120.0
DATABASE_LOOKUP_TIMEOUT_S=20.0
```

## Python 依赖安装

建议在项目根目录创建虚拟环境后安装：

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
```

已知兼容要求：

```text
pymilvus>=2.6,<2.7
setuptools<81
```

原因：

- 项目使用 `AsyncMilvusClient.has_collection()` 等 PyMilvus 2.6 API。
- 较新的 `setuptools` 版本移除了 `pkg_resources`，部分 PyMilvus 依赖链仍会访问它。

## 前端依赖安装

```powershell
cd web
npm install
```

如网络不稳定，可使用镜像源：

```powershell
npm install --registry=https://registry.npmmirror.com
```

## 初始化 Neo4j 和 Milvus

Excel 初始化数据位于：

```text
resources/data
├─ 表结构DEV.xlsx
├─ 枚举值DEV.xlsx
├─ 业务知识DEV.xlsx
├─ 历史SQLDEV.xlsx
└─ 表关系DEV.xlsx
```

全量初始化入口：

```powershell
python scripts\init.py
```

这个脚本会执行：

```text
1. 清空 Neo4j 所有节点和关系
2. 删除 Milvus collection
3. 重新导入 resources/data 下的 Excel
4. 写入 Neo4j 图谱
5. 调用 embedding 接口生成向量并写入 Milvus
```

注意：这是全量重建操作，会覆盖当前 Neo4j 和 Milvus 里的初始化数据。

当前已验证初始化结果：

```text
Neo4j:
TABLE      21
COLUMN     2341
ENUM       1189
KNOWLEDGE  59
SQL        9

Milvus:
collections:
  TYWS_TABLE
  TYWS_ENUM
  TYWS_KNOWLEDGE
  TYWS_SQL
```

### 初始化数据清洗说明

当前 `枚举值DEV.xlsx` 中曾存在 77 条无法匹配 `表结构DEV.xlsx` 的异常枚举记录，已经删除。

原始文件备份：

```text
resources/data/枚举值DEV.before_delete_invalid_rows.xlsx
```

另外，`tools/sql.py` 已处理 `TIMESTAMPDIFF(day/hour, ...)` 的 SQL 血缘解析问题，避免将 `day`、`hour` 误识别为物理字段。

## 启动顺序

以下顺序不包含 Milvus、Neo4j 等镜像安装步骤，默认你已经在本地启动好了 Neo4j、Milvus，并且 `.env` 中的连接信息正确。

### 1. 确认基础服务可用

启动后端前，需要先确认这些外部依赖已经可连接：

```text
Neo4j：GRAPH_URI / GRAPH_USER / GRAPH_PASSWORD
Milvus：VECTOR_URI / VECTOR_COLLECTION_TABLE / VECTOR_COLLECTION_ENUM / VECTOR_COLLECTION_KNOWLEDGE / VECTOR_COLLECTION_SQL
大模型接口：LLM_MODEL_NAME / OPENAI_API_BASE / OPENAI_API_KEY
备用套餐（可选）：LLM_FALLBACK_MODEL_NAME / LLM_FALLBACK_API_BASE / LLM_FALLBACK_API_KEY
Embedding 接口：EMBEDDING_MODEL_NAME / EMBEDDING_API_BASE / EMBEDDING_API_KEY
业务数据库：DATABASE_URL 或 DATABASE_DRIVER + DATABASE_HOST 等配置
```

如果已经执行过初始化脚本，Neo4j 中应有表、字段、枚举、知识、历史 SQL 等图谱数据，Milvus 中应有向量化后的检索数据。

### 2. 启动后端服务

后端是整个系统的主入口，FastAPI 会在运行时加载 `.env`、API 路由、数据库连接、Milvus/Neo4j 工具，以及 Google ADK 智能体工作流。

项目根目录执行：

```powershell
uvicorn server.app:app --host 127.0.0.1 --port 8000 --reload
```

如果只是为了本地验证、配置变更后重启，或者在 Windows 后台进程中启动，推荐使用不带 `--reload` 的命令，避免 `watchfiles` reloader 残留旧进程：

```powershell
uvicorn server.app:app --host 127.0.0.1 --port 8000
```

修改 `.env` 后必须重启后端服务，新的大模型、Embedding、数据库、Milvus、Neo4j 等连接配置才会重新加载。

后端地址：

```text
http://127.0.0.1:8000
```

健康检查：

```text
GET http://127.0.0.1:8000/health
```

### 3. 智能体启动方式

智能体不需要单独启动一个服务。

项目中的智能体定义在：

```text
adk_agents/copilot/agent.py
```

它由后端聊天服务按请求调用，调用链路大致是：

```text
前端发送问题
  -> FastAPI /api/chat/sessions/{session_id}/messages
  -> server/chat/service.py 创建 ADK Runner
  -> 加载 adk_agents/copilot/agent.py 中的 workflow
  -> 按规划调用检索、选表、SQL 生成/校验/执行，以及出图或分析报告
  -> 通过 SSE 流式返回前端
```

因此，只要第 2 步后端启动成功，智能体能力就已经随后端可用了。

### 4. 启动前端服务

打开新终端，进入前端目录：

```powershell
cd web
npm start
```

如果 PowerShell 提示 `npm.ps1` 被执行策略禁止，可以改用：

```powershell
cd web
npm.cmd start
```

前端访问：

```text
http://localhost:4200
```

开发代理配置在 `web/proxy.conf.json`，会将 `/api` 代理到后端 `http://127.0.0.1:8000`。

### 5. 登录并开始问数

访问前端页面后，使用本地账号登录，然后进入聊天页面发起自然语言问数。

推荐完整启动顺序：

```text
1. 确认 Neo4j 已启动
2. 确认 Milvus 已启动
3. 确认业务数据库可连接
4. 确认 .env 中大模型和 embedding 接口配置正确；如需限流切换，同时配好 Agent Plan 备用套餐
5. 如数据有变化，先执行 python scripts/init.py 初始化 Neo4j 和 Milvus
6. 启动后端：uvicorn server.app:app --host 127.0.0.1 --port 8000
   开发热更新可改用：uvicorn server.app:app --host 127.0.0.1 --port 8000 --reload
7. 启动前端：cd web && npm start
   如 PowerShell 拦截 npm.ps1，使用：cd web && npm.cmd start
8. 打开 http://localhost:4200 登录使用
```

### 6. 生产构建方式

开发阶段一般使用前后端分开启动。若需要由 FastAPI 直接托管前端页面，可以先构建前端：

```powershell
cd web
npm run build
```

构建产物位于：

```text
web/dist/web/browser
```

后端启动后会挂载该目录，用于直接服务 Angular SPA。

## 登录账号

本地账号配置在：

```text
data/auth/users.json
data/auth/user_workspaces.json
```

首次克隆后，先从模板创建本地账号文件：

```powershell
Copy-Item data\auth\users.example.json data\auth\users.json
Copy-Item data\auth\user_workspaces.example.json data\auth\user_workspaces.json
```

然后修改 `data/auth/users.json` 中的本地用户名和密码。账号文件包含明文密码，已通过 `.gitignore` 排除，禁止提交。

初始化脚本默认写入 workspace：

```text
default
```

账号的 `default_workspace_id` 保持为 `default` 后，可以访问初始化后的问数数据。

## 前端页面

```text
/login      登录页
/chat       智能问数聊天页面，支持查询结果、分析图表和分析报告下载；登录后默认进入空白新对话
/catalog    数据目录，维护表、字段、枚举、表关系
/enums      枚举值列表，展示枚举值及其所属表、字段信息
/relations  表关系列表，展示来源表、目标表和关联条件
/knowledge  业务知识维护
/sql        历史 SQL 维护
/ingest     Excel 导入任务页面
```

智能对话页工作流展示顺序：

```text
解析问题 → 规划步骤 → 检索上下文 → 确定数据表与表结构 → 生成 SQL → 校验 SQL → 执行 SQL
若本轮出图，再显示分析图表；若本轮出报告，再显示分析报告及 Word 下载
```

规划节点在问数、出图、报告结束后可能再次出现，前端会合并为一个「规划步骤」，并取最后一次决策。步骤行显示耗时；检索上下文里的分类默认收起，点开后再看明细。

### 元数据列表分页

`/enums` 和 `/relations` 页面用于业务人员查看初始化到知识图谱中的枚举值和表关系。

这两个页面的列表区采用独立滚动容器，页面底部提供统一分页控制：

```text
共 X 条  [50条/页 ▼]  [上一页]  第 N / M 页  [下一页]
```

每页条数支持：

```text
10条/页、20条/页、30条/页、50条/页、100条/页
```

切换每页条数后会自动回到第 1 页，并按新的 `page_size` 重新请求后端接口。

相关后端接口：

```text
GET /api/enums?page=1&page_size=50
GET /api/join-relations?page=1&page_size=50
```

注意：这些接口需要登录态。未登录时直接访问接口会返回 `401 未登录或会话已过期`，应先通过前端登录后再访问页面。

## 常用开发命令

后端启动：

```powershell
uvicorn server.app:app --host 127.0.0.1 --port 8000 --reload
```

配置变更后重启、后台运行或 Windows 环境推荐：

```powershell
uvicorn server.app:app --host 127.0.0.1 --port 8000
```

初始化 Neo4j / Milvus：

```powershell
python scripts\init.py
```

前端启动：

```powershell
cd web
npm start
```

PowerShell 执行策略拦截 `npm.ps1` 时：

```powershell
cd web
npm.cmd start
```

前端构建：

```powershell
cd web
npm run build
```

前端测试：

```powershell
cd web
npm test
```

## 常见问题

### 1. `No module named pkg_resources`

原因：`setuptools` 版本过高或环境不完整。

处理：

```powershell
python -m pip install "setuptools<81"
```

### 2. `AsyncMilvusClient has no attribute has_collection`

原因：PyMilvus 版本过旧。

处理：

```powershell
python -m pip install --upgrade "pymilvus>=2.6,<2.7"
```

### 3. Milvus 报向量维度不一致

示例：

```text
expected=1024 actual=2048
```

原因：`VECTOR_DIMENSION` 与 embedding 模型实际输出维度不一致。

当前火山 embedding 返回 2048 维，配置应为：

```env
VECTOR_DIMENSION=2048
```

修改后重新执行：

```powershell
python scripts\init.py
```

### 4. `Cannot add edge HAS` 或 `Cannot add edge USES`

原因：

- 枚举值引用了表结构中不存在的字段。
- 历史 SQL 解析出的表字段没有对应的表结构节点。

处理：

- 校验 `枚举值DEV.xlsx` 的表名、列名是否在 `表结构DEV.xlsx` 中存在。
- 校验 `历史SQLDEV.xlsx` 中 SQL 引用的物理表字段是否在表结构里存在。
- 必要时删除异常行或补齐表结构。

### 5. 模型接口提示限流或无法生成报告

原因：火山 Coding Plan 触发了突发保护或额度限制。若已配置 Agent Plan 备用套餐，后端会自动换套餐再试；两套都失败时，对话里会提示稍后再发「生成分析报告」，不必重新取数。

处理：

- 确认 `.env` 中 `LLM_FALLBACK_API_BASE` 为 `https://ark.cn-beijing.volces.com/api/plan/v3`。
- 确认 `ARK_AGENT_PLAN_API_KEY` 是 Agent Plan 专属 Key，不是 Coding Plan Key。
- 修改 `.env` 后重启后端。
- 同一会话直接发送「生成分析报告」，不要把整句取数问题再发一遍。

### 6. `DATABASE_URL` 指向旧路径

如果 `.env` 中存在类似 macOS 路径：

```env
DATABASE_URL=sqlite+aiosqlite:////Users/xxx/Projects/TYWS/data/dev.sqlite
```

在 Windows 本机通常不可用。改为当前项目路径，或清空 `DATABASE_URL` 使用 MySQL 配置。

## 数据模型

Neo4j 节点类型：

```text
TABLE
COLUMN
ENUM
KNOWLEDGE
SQL
```

Neo4j 边类型：

```text
TABLE -HAS-> COLUMN
COLUMN -HAS-> ENUM
KNOWLEDGE -DESCRIBES-> TABLE/COLUMN
SQL -USES-> TABLE/COLUMN
TABLE -JOINS-> TABLE
```

Milvus collection 与节点类型：

```text
TYWS_TABLE      TABLE / COLUMN
TYWS_ENUM       ENUM
TYWS_KNOWLEDGE  KNOWLEDGE
TYWS_SQL        SQL
```

Milvus 向量记录字段：

```text
id
workspace_id
text
vector
node_type
node_uuid
```

检索时按 `workspace_id` 过滤，当前初始化数据写入 `default` workspace。
