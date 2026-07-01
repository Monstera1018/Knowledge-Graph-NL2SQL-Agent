# AGENTS.md

本文件用于指导后续 Agent/Codex/开发者在 TYWS 项目中协作开发。内容参考《知识图谱+向量库协同驱动的智能问数建设方案-V4.0》，并结合当前代码实现整理。若建设方案与现阶段代码存在差异，以本文件中“当前实现”为准。

## 项目定位

TYWS 是一个面向自然语言问数的产品化原型。目标是让业务人员通过自然语言提出数据问题，系统自动完成问题解析、语义检索、选表、表结构汇总、SQL 生成、SQL 校验、执行查询和结果展示。

建设方案中的核心思想是“知识图谱 + 向量库协同驱动”：

- 向量库负责从自然语言问题中召回相似的数据表、字段、枚举值、业务知识、历史 SQL。
- 知识图谱负责补全被召回对象的结构化关系，例如表-字段、字段-枚举、知识-表字段、SQL-表字段、表-表关联。
- 智能体工作流负责把检索结果转化为可执行 SQL，并通过校验与执行闭环提升可靠性。

当前项目已经实现了核心问数链路和元数据维护页面，但尚未完整实现建设方案中提到的全部能力，例如双盲语义校验、自动报告、可视化图表推荐、MCP 对外开放、权限细粒度管控、用户反馈闭环等。

## 当前技术栈

后端：

- Python 3.12
- FastAPI
- Google ADK Workflow / Agent
- LiteLLM
- SQLAlchemy asyncio
- sqlglot
- pandas / openpyxl

图谱与检索：

- Neo4j：存储表、字段、枚举、业务知识、历史 SQL、表关系及其关系边。
- Milvus：存储向量索引。
- 当前向量库已拆分为 4 个 collection：
  - `TYWS_TABLE`：表和字段
  - `TYWS_ENUM`：枚举值
  - `TYWS_KNOWLEDGE`：业务知识
  - `TYWS_SQL`：历史 SQL

前端：

- Angular
- Angular Material
- Tailwind CSS
- ngx-markdown / PrismJS
- Neo4j NVL 图谱可视化组件

## 关键目录

```text
TYWS
├─ adk_agents/
│  ├─ extractor/            # 问题解析 Agent
│  └─ copilot/              # 智能问数主 Workflow
├─ server/
│  ├─ app.py                # FastAPI 入口；存在前端 dist 时会挂载 SPA
│  ├─ chat/                 # SSE 协议、ADK Runtime 封装、前端展示适配
│  ├─ routers/              # auth/chat/graph/health/ingest/metadata API
│  └─ auth/                 # 本地认证与 workspace 解析
├─ tools/
│  ├─ graph/                # Neo4j 模型和 CRUD
│  ├─ ingest.py             # Excel 初始化/导入
│  ├─ retrieval.py          # Milvus 检索 + Neo4j 补全 + 上下文组装
│  ├─ vector.py             # Milvus collection、写入、检索
│  ├─ db.py                 # 业务库连接、SQL 执行、枚举值查询
│  ├─ sql.py                # SQL 提取、血缘解析、只读校验
│  └─ llm.py / embedding.py # 大模型与 embedding 调用
├─ resources/data/          # Excel 初始化数据
├─ scripts/init.py          # 全量重建 Neo4j 和 Milvus 初始化入口
├─ web/                     # Angular 前端
├─ data/dev.sqlite          # 本地 SQLite 示例业务库
└─ README.md                # 项目启动和使用说明
```

## 当前智能体链路

主工作流在 `adk_agents/copilot/agent.py`。

```text
用户问题
  -> reset_sql_turn_state
  -> prepare_extractor_input
  -> extractor
  -> parse_extractor_output
  -> route_after_extractor
     ├─ 非问数：chitchat
     └─ 问数：search
  -> prepare_table_selector_input
  -> table_selector
  -> parse_table_selection
  -> route_after_table_selection
     ├─ 无可用表：format_no_table_response
     └─ 有可用表：load_sql_schema_context
  -> prepare_sql_generator_input
  -> sql_generator
  -> validate_sql
     ├─ 校验失败但未达上限：回到 sql_generator 修复
     ├─ 校验失败且达上限：format_validation_failure_response
     └─ 校验通过：execute_sql
```

### extractor

职责：

- 将用户自然语言问题改写为适合检索的 query。
- 提取 keywords。
- 判断 intent。
- 对多轮对话会参考历史消息，例如“杭州呢”这类同主题追问需要结合上文理解。

注意：

- `reset_sql_turn_state` 会清理 SQL 相关状态，避免无关旧上下文泄漏。
- 但 extractor 仍可使用会话历史做问题改写，这是多轮问数所需能力。

### search

职责：

- 调用 `search_and_build_llm_context_with_hits()`。
- 分别从 Milvus 的表/字段、枚举、业务知识、SQL collection 召回。
- 再通过 Neo4j 补齐图谱关系，形成“检索上下文”。
- 会记录未绑定表字段的通用业务知识 UUID，供后续 `load_sql_schema_context` 保留。

重要边界：

- 检索上下文不是纯 Milvus 结果，而是“Milvus 召回 + Neo4j 补全”后的结果。
- 业务知识中未绑定表字段的通用知识不应因为选表结果而被过滤掉。

### table_selector

职责：

- 基于检索上下文选择候选表。
- 只能从“候选数据表白名单”中选择完整物理表名。
- `tables` 中的表名必须逐字命中白名单，不再使用 `T1`、`T2` 或 `table_ids`。
- 不允许手写、猜测、改写、缩写或补全物理表名。

当前增强：

- `parse_table_selection()` 支持从混合文本中提取 JSON。
- `server/chat/service.py` 也对 table selector 展示层做了混合文本 JSON 兜底解析。
- 后端会补齐 `table_details`，避免前端只显示英文表名和“未命名数据表”。

开发注意：

- 如果修改 table selector prompt，必须保持“只输出 JSON”的约束。
- 如果模型偶发输出说明文字，解析层也必须继续容错。
- 表名必须来自候选数据表白名单；白名单之外或被改写过的表名应被后端丢弃。

### load_sql_schema_context

职责：

- 根据选出的表，到 Neo4j 中加载完整表结构。
- 汇总：
  - 用户问题
  - 选表说明
  - 候选表
  - 数据表与字段
  - 枚举值
  - 表关系
  - 业务知识
  - 历史 SQL
- 保存 `SQL_SCHEMA_CONTEXT_STATE_KEY`，供 SQL 生成使用。

重要边界：

- 专用知识：绑定了候选表或候选字段的知识。
- 通用知识：未绑定表字段，但在检索阶段召回的知识。
- 当前要求：通用知识应在确定表结构后继续保留，不应被候选表过滤掉。

### sql_generator

职责：

- 根据 schema context 生成只读 SQL。
- 可调用 `lookup_column_values(table_name, column_name)` 查询数据库实际枚举值。
- 输出面向用户的中文说明和一个 SQL markdown 代码块。

风险点：

- 模型可能输出英文中间过程，例如工具 lookup 为空后的解释。
- 模型可能把业务知识中的枚举列表当成开放集合，并结合字段枚举自行扩展。
- 模型可能重复输出流式 chunk，导致前端出现重复文本。

优化优先级：

1. 最优：后端仅展示 `sql_generator` 的最终答案，不展示中间过程。
2. 加强 prompt：所有用户可见内容必须中文，禁止英文，禁止展示工具调用过程。
3. 对业务知识中的枚举列表增加“闭集约束”提示：若知识明确给出取值集合，不得自行补充额外枚举值。
4. 修复流式 chunk 合并重复问题。

### validate_sql

职责：

- 从模型输出中提取 SQL。
- 校验只读、安全性、表名、字段、SQL 方言。
- 通过 `sqlglot` 和业务数据库进行只读/结构校验。
- 失败时最多重试，重试信息写入 `SQL_VALIDATION_FEEDBACK_STATE_KEY`。

注意：

- SQL 校验通过后，规范化 SQL 会写入 `SQL_VALIDATED_SQL_STATE_KEY`。
- `execute_sql` 应优先执行已校验 SQL，避免执行模型原始文本。

### execute_sql

职责：

- 执行校验通过的 SQL。
- 输出 Markdown 表格。
- 受 `SQL_EXECUTION_MAX_ROWS` 控制展示最大行数。

## 数据初始化和导入

初始化入口：

```powershell
python scripts\init.py
```

当前行为：

- 清空并重建 Neo4j 图谱。
- 删除并重建 Milvus collection。
- 额外兼容删除旧集合 `TYWS`。
- 读取 `resources/data` 下的 Excel：
  - 表结构DEV.xlsx
  - 枚举值DEV.xlsx
  - 业务知识DEV.xlsx
  - 历史SQLDEV.xlsx
  - 表关系DEV.xlsx
- 初始化完成后 flush Milvus collection。

重要提醒：

- `scripts/init.py` 是全量重建入口，会清空 Neo4j 与 Milvus 初始化数据。
- 若后续需要只重建向量库或只重建图谱，应拆分脚本，避免初始化耦合。

前端导入任务：

- 页面：`/ingest`
- 后端：`server/routers/ingest.py`
- 导入逻辑：`tools/ingest.py`
- 当前导入会写入 Neo4j，并同步相关 Milvus 向量记录。

## 前端功能现状

主页面路由在 `web/src/app/app.routes.ts`。

已实现页面：

- `/catalog`：数据目录
- `/enums`：枚举值
- `/relations`：表关系
- `/knowledge`：业务知识
- `/sql`：历史 SQL
- `/ingest`：导入任务
- `/chat`：智能对话

智能对话页：

- 通过 `fetch + ReadableStream` 消费 SSE。
- SSE 解析在 `web/src/app/features/chat/chat-stream.ts`。
- 消息部件合并在 `web/src/app/features/chat/chat-message-parts.ts`。
- 工作流展示组件在 `web/src/app/features/chat/components/`。

当前展示优化方向：

- 检索上下文按类型展示：数据表、业务知识、SQL 等。
- 数据表模块应按表分组。
- 业务知识应区分专用知识和通用知识。
- “确定数据表与表结构”合并显示选表结果、库表结构、表关系、业务知识。
- 避免把后端原始 JSON 直接暴露给业务人员。

## 启动顺序

前置条件：

- 本地 Neo4j 已启动。
- 本地 Milvus 已启动。
- `.env` 已配置大模型、embedding、Neo4j、Milvus、业务数据库。

推荐顺序：

```powershell
# 1. 安装 Python 依赖
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt

# 2. 安装前端依赖
cd web
npm install
cd ..

# 3. 初始化 Neo4j 和 Milvus
python scripts\init.py

# 4. 启动后端
uvicorn server.app:app --host 127.0.0.1 --port 8000 --reload

# 5. 启动前端开发服务
cd web
npm start
```

访问方式：

- 后端 API：`http://127.0.0.1:8000`
- 前端开发服务：`http://localhost:4200`
- 如果已构建 `web/dist/web/browser`，后端会挂载静态前端，访问 `http://127.0.0.1:8000` 也能看到前端页面。

是否需要单独启动前端：

- 开发前端时需要 `npm start`，使用 `http://localhost:4200`。
- 只验证后端和已构建页面时，可以只启动后端。

## 环境变量约定

不要提交真实 `.env` 密钥。文档和示例只允许写占位值。

关键配置：

```env
LLM_MODEL_NAME=openai:...
OPENAI_API_BASE=...
OPENAI_API_KEY=...

EMBEDDING_MODEL_NAME=openai:...
EMBEDDING_API_BASE=...
EMBEDDING_API_KEY=...
EMBEDDING_MODE=litellm

VECTOR_COLLECTION_TABLE=TYWS_TABLE
VECTOR_COLLECTION_ENUM=TYWS_ENUM
VECTOR_COLLECTION_KNOWLEDGE=TYWS_KNOWLEDGE
VECTOR_COLLECTION_SQL=TYWS_SQL
VECTOR_DIMENSION=2048
VECTOR_URI=http://127.0.0.1:29531

GRAPH_URI=neo4j://localhost:17688
GRAPH_USER=neo4j
GRAPH_PASSWORD=...
GRAPH_SCHEMA_NS=TYWS.NS

DATABASE_URL=sqlite+aiosqlite:///C:/.../TYWS/data/dev.sqlite
DATABASE_BACKEND=sqlite
SQL_READ_DIALECT=sqlite
```

业务数据库说明：

- `DATABASE_URL` 优先级最高。
- 如果 `DATABASE_URL` 非空，`DATABASE_HOST`、`DATABASE_PORT`、`DATABASE_NAME` 等拆分配置会被忽略。
- SQLite 本地开发推荐使用绝对路径，避免工作目录变化导致连接错库。

## 开发原则

### 注释和说明使用中文

- 新增或修改代码注释、docstring、技术说明时，默认使用中文。
- 已有英文注释如果所在代码正被修改，应顺手改为中文；不要为了翻译注释单独做大范围无关重写。
- 协议字段、函数名、类名、第三方 API 名称、SQL 关键字、配置项等代码接口仍保持原有英文命名。

### 不要破坏图谱与向量的一致性

凡是新增、修改、删除以下对象，都要同时考虑 Neo4j 和 Milvus：

- 表
- 字段
- 枚举值
- 业务知识
- 历史 SQL
- 表关系

CRUD 层已有 `commit_graph_then_vectors`、`delete_vector_records_by_node_uuids`、`upsert_vector_records` 等机制。修改元数据写入逻辑时，必须确认向量记录同步更新。

### 表名必须受控

SQL 生成链路中，物理表名必须来自：

- `table_selector` 从候选数据表白名单中逐字选择的表名；
- `load_sql_schema_context` 输出的候选表；
- `format_sql_generator_user_message()` 附加的“本轮允许使用的物理表名”。

禁止让模型凭记忆补全或改写表名。

### 业务知识优先，但要明确边界

业务知识是口径、指标、规则的优先来源。若业务知识给出明确取值集合，后续优化应将其视为闭集，避免模型根据字段枚举自行扩展。

示例：

- 知识写明“高压用户电压在 10kV、20kV、35kV、110kV、220kV、500kV、1000kV 之中”。
- SQL 生成时不应自行加入 `66kV`，除非业务知识或用户问题明确允许。

### 前端应展示业务语言，不展示原始 JSON

智能对话工作流面向业务人员展示，应优先展示：

- 系统正在做什么；
- 已召回哪些资料；
- 选择了哪些表；
- 使用了哪些字段/枚举/知识/表关系；
- 生成的 SQL 和执行结果。

不要直接把内部 JSON 或模型原始中间过程暴露给用户。

### SQL 必须只读并经过校验

任何执行业务数据库的 SQL 都必须走 `validate_sql`。禁止绕过：

- SQL 只读校验；
- 表字段校验；
- 方言校验；
- 候选表约束校验。

### 多轮对话要区分“同主题追问”和“新问题”

- “杭州呢”“换成金华”这类同主题追问，应允许 extractor 结合上轮问题改写。
- 新问题必须清理 SQL 状态，不能复用旧的选表、schema context 或 SQL。

## 与建设方案的主要差异

已实现：

- 知识图谱存储表、字段、枚举、业务知识、历史 SQL、表关系。
- Milvus 向量检索与 Neo4j 图谱补全协同。
- ADK 智能体链路：问题解析、检索、选表、汇总表结构、生成 SQL、校验 SQL、执行 SQL。
- 元数据维护页面：数据目录、枚举值、表关系、业务知识、SQL。
- 导入任务页面和 Excel 初始化。
- 前端智能对话与工作流轨迹展示。

部分实现或待增强：

- 方案中的“数据定位 Agent”目前由 search + table_selector + load_sql_schema_context 组合实现，尚未独立成循环探测 Agent。
- 方案中的“双盲校验”当前主要是 SQL 语法/安全/表字段校验，尚未实现“SQL 反向解释再与原问题比对”的完整闭环。
- 方案中的结果分析、自动图表、报告生成、疑点工单、预警推送尚未完整实现。
- 方案中的 MCP 协议支持和 Skills 外部调用尚未作为正式接口落地。
- 方案中的权限管控目前只具备基础认证与 workspace 隔离，尚未实现表/字段/行级权限。
- 方案中的用户反馈、知识老化检测、模型调用监控仍需增强。

## 常见问题定位

### 前端显示“未命名数据表”

优先检查：

- `table_selector` 输出是否被解析成 `result`。
- `result.table_details` 是否包含 `comment` 或 `description`。
- 前端合并后的 `table_context` 是否丢失了 `schema_plan.selected_table_details`。

当前后端已有兜底：即使模型在 JSON 前后夹带说明，也会尝试提取 JSON 并补齐表详情。

### 选表为空但检索上下文有内容

优先检查：

- `table_selector` 是否输出了候选数据表白名单中的完整物理表名。
- 输出是否为可解析 JSON。
- 输出表名是否被改写、缩写或补全，导致无法命中候选表白名单。
- 模型是否把通用知识当作不能落表，导致没有选择候选表。

### SQL 中出现英文说明

源头通常是 `sql_generator`。它属于流式展示节点，模型输出的文本会被后端实时推送给前端。

优化方向：

- 后端只展示最终 SQL 答案，不展示中间过程。
- Prompt 明确禁止英文和工具调用过程说明。
- 工具返回为空时，用中文短句说明，不允许英文推理。

### SQL 使用了业务知识外的枚举值

原因通常是模型混合了业务知识和字段枚举/实际值查询结果。应在 prompt 中强调：

- 业务知识给出的枚举值集合是闭集时，不得自行扩展。
- 字段枚举只能用于把业务值映射到实际存储值，不能覆盖业务口径。

## 测试与验证

后端语法检查：

```powershell
.\.venv\Scripts\python.exe -m py_compile server\chat\service.py
```

后端接口验证：

- 启动后端后调用 `/api/auth/login` 获取 token。
- 调用 `POST /api/chat/sessions/{session_id}/messages`。
- 观察 SSE 中各节点输出。

前端验证：

```powershell
cd web
npm test
npm start
```

常用验证问题：

- `金华地区电费未结清用户清单`
- `全省高压用户非商业用电，为专变的情况`

验证重点：

- 检索上下文是否包含表、枚举、业务知识、历史 SQL。
- 选表结果是否显示表英文名和中文名。
- 确定数据表与表结构是否保留通用知识。
- SQL 是否只使用候选表。
- SQL 是否符合业务知识口径。
- 执行结果是否为中文列名。

## 临时文件与安全

不要提交：

- `.env`
- 本地日志
- 临时 docx/txt
- 大模型或 embedding 缓存中的敏感内容
- 真实 API Key

如果为了分析外部 Word/Excel 文档复制了临时文件到项目根目录，完成后应删除。
