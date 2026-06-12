# TYWS 智能问数项目

TYWS 是一个面向“自然语言问数”的产品化原型。项目通过 FastAPI 后端、Google ADK Agent 工作流、Neo4j 图谱、Milvus 向量库和 Angular 前端，实现从业务元数据导入、语义检索、选表、生成 SQL、SQL 校验到查询结果展示的完整链路。

## 功能概览

- 元数据维护：表、字段、枚举、表关系维护。
- 业务知识维护：业务概念、规则和关联表字段维护。
- 历史 SQL 管理：沉淀可复用 SQL 案例，并建立 SQL 与表字段关系。
- Excel 数据导入：从 `resources/data` 导入表结构、枚举值、业务知识、历史 SQL 和表关系。
- 智能问数：用户输入自然语言问题，系统检索上下文、选择候选表、生成只读 SQL、校验并执行查询。
- 执行轨迹展示：前端展示 Agent 工作流、工具调用、SQL 结果和错误诊断。

## 技术栈

后端：

- Python 3.12
- FastAPI
- Google ADK
- LiteLLM
- SQLAlchemy asyncio
- sqlglot
- pandas / openpyxl

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
TYWS
├─ adk_agents/              # Google ADK Agent 工作流
│  ├─ extractor/            # 用户问题检索意图抽取 Agent
│  └─ copilot/              # 智能问数主 Workflow
├─ data/                    # 本地运行数据、示例库、用户工作区配置
│  ├─ auth/                 # 用户与 workspace 配置
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
│  ├─ llm.py                # 普通 LLM 调用
│  ├─ retrieval.py          # 语义检索与上下文组装
│  ├─ sql.py                # SQL 解析、血缘提取、只读校验
│  └─ vector.py             # Milvus collection 和向量写入/检索
└─ web/                     # Angular 前端
```

## 核心问数流程

智能问数主入口在 `adk_agents/copilot/agent.py`，整体流程如下：

```text
用户问题
  -> extractor 抽取检索 query / keywords / intent
  -> 判断是否为问数问题
  -> search 调用 Milvus 语义检索
  -> retrieval.py 回 Neo4j 补齐表、字段、枚举、知识和历史 SQL 上下文
  -> table_selector 选择候选表
  -> load_sql_schema_context 加载候选表完整 schema
  -> sql_generator 生成 SQL
  -> validate_sql 校验 SQL 只读、安全性、表字段存在性和 EXPLAIN
  -> execute_sql 执行业务库查询
  -> FastAPI SSE 流式返回前端
```

后端聊天接口：

```text
POST /api/chat/sessions/{session_id}/messages
```

前端通过 `fetch + ReadableStream` 消费 SSE 事件，事件处理在 `web/src/app/features/chat/chat-stream.ts`。

## 环境变量

项目通过根目录 `.env` 读取配置。`.env` 包含密钥，已加入 `.gitignore`，不要提交真实密钥。

### 大模型配置

当前项目按火山引擎 Coding Plan OpenAI 兼容接口配置：

```env
ARK_API_KEY=替换为你的火山方舟 API Key

LLM_MODEL_NAME=openai:ark-code-latest
OPENAI_API_BASE=https://ark.cn-beijing.volces.com/api/coding/v3
OPENAI_API_KEY=${ARK_API_KEY}
```

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
VECTOR_COLLECTION_NAME=TYWS
VECTOR_DIMENSION=2048
VECTOR_URI=http://127.0.0.1:29531
VECTOR_TIMEOUT=5
```

### Neo4j 配置

```env
GRAPH_DATABASE=neo4j
GRAPH_URI=neo4j://localhost:17688
GRAPH_USER=neo4j
GRAPH_PASSWORD=REMOVED_DEVELOPMENT_PASSWORD
GRAPH_SCHEMA_NS=TYWS.NS
```

### 业务数据库配置

业务数据库是 Agent 生成 SQL 后实际查询的数据源，不是 Python 自带环境。

`DATABASE_URL` 优先级最高。只要它非空，代码会直接使用它，下面的 `DATABASE_HOST`、`DATABASE_PORT` 等配置会被忽略。

本地 SQLite 示例配置：

```env
DATABASE_URL=sqlite+aiosqlite:///C:/Users/your-name/Desktop/大模型相关资料/通用问数-产品化/开发代码/TYWS/data/dev.sqlite
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
collection: TYWS
row_count: 1998
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
Milvus：VECTOR_URI / VECTOR_COLLECTION_NAME
大模型接口：LLM_MODEL_NAME / OPENAI_API_BASE / OPENAI_API_KEY
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
  -> 调用检索、选表、SQL 生成、SQL 校验、SQL 执行等工具
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
4. 确认 .env 中大模型和 embedding 接口配置正确
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

默认账号：

```text
用户名：admin
密码：admin
默认 workspace：default
```

初始化脚本默认写入 workspace：

```text
default
```

因此使用 `admin/admin` 登录后可以访问初始化后的问数数据。

## 前端页面

```text
/login      登录页
/catalog    数据目录，维护表、字段、枚举、表关系
/knowledge  业务知识维护
/sql        历史 SQL 维护
/ingest     Excel 导入任务页面
/chat       智能问数聊天页面
```

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

### 5. `DATABASE_URL` 指向旧路径

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
