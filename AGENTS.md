# AGENTS.md

本文件用于指导后续 Agent/Codex/开发者在本项目中协作开发。内容参考《知识图谱+向量库协同驱动的智能问数建设方案-V4.0》，并结合当前代码实现整理。若建设方案与现阶段代码存在差异，以本文件中“当前实现”为准。

最后核对日期：2026-09-16。依据当前工作区代码（包含尚未提交的修改）更新；运行情况必须在验证时重新检查，不能把历史服务状态或验证结果当作当前保证。后续代码与本文件不一致时，应核查代码并同步文档。

## 项目定位

本仓库是 Knowledge-graph NL2SQL agent（自然语言问数助手）。目标是让业务人员通过自然语言提出数据问题，系统自动完成问题解析、语义检索、选表、表结构汇总、SQL 生成、SQL 校验、执行查询和结果展示。对外名称使用「自然语言问数助手」；GitHub / README 使用 `nl2sql-agent`。内部代码标识（如 `TYWS_TABLE`、ADK 工作流名）暂不改。

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

- Angular 21 / TypeScript 5.9（版本范围见 `web/package.json`）
- Angular Material
- Tailwind CSS
- ngx-markdown / PrismJS
- Neo4j NVL 图谱可视化组件
- Vitest / jsdom（由 Angular 单元测试构建器运行）

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
  -> prepare_planner_input
     ├─ 规划次数耗尽：end_plan
     └─ 否则：planner
  -> parse_plan
  -> route_after_plan
     ├─ stop：end_plan
     ├─ 闲聊：chitchat（终端，不再规划）
     ├─ 出图：prepare_chart_planner_input
     ├─ 报告：prepare_report_planner_input
     └─ 问数：search
  -> prepare_table_selector_input
  -> table_selector
  -> parse_table_selection
  -> route_after_table_selection
     ├─ 无可用表：format_no_table_response -> observe_no_tables -> prepare_planner_input
     └─ 有可用表：load_sql_schema_context
  -> prepare_sql_generator_input
  -> sql_generator
  -> validate_sql
     ├─ 校验失败但未达上限：回到 sql_generator 修复
     ├─ 校验失败且达上限：format_validation_failure_response -> observe_validation_failure -> prepare_planner_input
     └─ 校验通过：execute_sql -> observe_query_result -> prepare_planner_input
  出图分支：
     ├─ 无快照：observe_no_snapshot -> prepare_planner_input
     └─ 有快照：chart_planner -> parse_chart_spec -> observe_chart_result -> prepare_planner_input
  报告分支：
     ├─ 无快照：observe_no_snapshot -> prepare_planner_input
     └─ 有快照：report_planner -> parse_report_spec -> observe_report_result -> prepare_planner_input
```

### extractor

职责：

- 将用户自然语言问题改写为适合检索的 query。
- 提取 keywords。
- 判断 intent。
- 对多轮对话会参考历史消息，例如“杭州呢”这类同主题追问需要结合上文理解。
- `task` 只是给规划节点的提示，不再直接三路分流。

注意：

- `reset_sql_turn_state` 会清理 SQL 相关状态，避免无关旧上下文泄漏。
- 但 extractor 仍可使用会话历史做问题改写，这是多轮问数所需能力。

### planner

职责：

- 每次只输出下一步：`query`、`chart`、`report`、`chitchat`、`stop`。
- 问数内部（选表、生成 SQL、`validate_sql` 重试）仍是固定管道，不每步再规划。
- 管道出口写入观察（有无快照、查询是否成功、出图是否可行、报告是否生成等），再回到 `prepare_planner_input`。
- 用户直接要求出图或分析报告但当前没有查询快照时，允许先选 `chart`/`report`；没有快照时观察 `no_snapshot` 后重新规划，通常下一步改为 `query`。
- 清单/统计题默认下一步只有 `query`，不会因为“可以画图/可以写报告”就选 `chart` 或 `report`。
- 后端只做空转防护：没有图表/报告意图且无快照时去掉 `chart`/`report`；问数/选表彻底失败则结束；用户需求未完成时不允许直接 `stop`。出图或报告失败后由 planner 根据观察选择再试或重新问数。
- 分析报告约定三个部分（总体情况、分类分析含嵌入图表、分析结论），正文由模型生成；`parse_report_spec` 基于查询快照聚合图表并写出 Word 文件，落到 `data/reports/{file_id}.docx`，前端通过 `GET /api/chat/reports/{file_id}` 下载。
- 每轮用户消息最多规划 `MAX_PLANNER_TURNS`（当前为 5）次。
- SQL 仍必须经过 `validate_sql`；图表仍只基于查询快照聚合，不另写 GROUP BY SQL。
- 闲聊是终端节点，不再交回规划。

### search

职责：

- 调用 `search_and_build_llm_context_with_hits()`。
- 分别从 Milvus 的表/字段、枚举、业务知识、SQL collection 召回。
- 再通过 Neo4j 补齐图谱关系，形成“检索上下文”。
- 会记录未绑定表字段的通用业务知识 UUID，以及向量命中的历史 SQL UUID，供后续 `load_sql_schema_context` 保留。
- 历史 SQL 上下文必须包含样例名称、逻辑说明和 SQL 正文；向量命中的 SQL 会直接进入上下文，不只依赖「表反查 SQL」。

重要边界：

- 检索上下文不是纯 Milvus 结果，而是“Milvus 召回 + Neo4j 补全”后的结果。
- 业务知识中未绑定表字段的通用知识不应因为选表结果而被过滤掉。
- 向量命中的历史 SQL 样例同样应在确定表结构后继续保留，并展示逻辑说明与 SQL 正文，供 `sql_generator` 参考 JOIN 路径和字段写法；`FROM`/`JOIN` 表名仍只能来自本轮候选表白名单。样例中的额外筛选条件不得在用户问题未要求时抄入本轮 SQL。
- 仅 `enabled=true`（缺省视为启用）的历史 SQL 会进入向量召回和图谱补全；停用条目保留在 SQL 页，但不参与生成。
- `search` 使用 `retrieval_query`、各个 `keywords` 和 `intent` 组成多条查询；召回结果经 `dedupe_hits()` 去重后再补全图谱。
- `tools/vector.py` 的 `_search_specs()` 在未指定 `node_types` 时，分别读取 `VECTOR_TOPK_TABLE`、`VECTOR_TOPK_ENUM`、`VECTOR_TOPK_KNOWLEDGE`、`VECTOR_TOPK_SQL`。表和字段共享一个检索配额；显式指定 `node_types` 时使用调用方的 `limit`。
- TopK 是每条查询在相应集合中的召回上限，不是最终页面条数。多查询、图谱补全和展示拆分都会改变最终数量。

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
- 检索阶段向量命中的历史 SQL 应继续保留；上下文中同时给出逻辑说明和 SQL 正文，条数受 `MAX_SQL_REFERENCE_SAMPLES` 限制。

### sql_generator

职责：

- 根据 schema context 生成只读 SQL。
- 可调用 `lookup_column_values(table_name, column_name)` 查询数据库实际枚举值。
- 输出面向用户的中文说明和一个 SQL markdown 代码块。
- 筛选条件以用户问题为准：业务知识只做术语到字段/存储值的映射；历史 SQL 只学 JOIN 与写法，不把样例里用户未提到的剔除规则、账期或额外过滤抄进本轮查询。
- 校验通过且执行结果有数据时，将用户问题、逻辑说明、规范化 SQL 沉淀为历史 SQL：`source=llm`，默认 `enabled=false`。Excel/页面手工创建为 `source=import`，默认启用。

风险点：

- 模型仍可能在 `part.thought` 中输出英文过程，例如工具 lookup 为空后的解释；该字段不得作为用户可见正文发送。
- 模型可能把业务知识中的枚举列表当成开放集合，并结合字段枚举自行扩展。
- 重复文本可能来自模型、ADK 增量/聚合事件或合并逻辑，需要沿事件链定位，不能仅凭页面重复就归因模型。

当前实现：

- `adk_agents/copilot/prompts.py` 已要求中文说明、禁止英文和工具调用过程，并限制为 1～3 句说明加一个 SQL 代码块；这些是提示词约束，仍可能被模型违反。
- `server/chat/service.py` 的 `_TEXT_STREAM_STAGES` 包含 `sql_generator`，但该阶段受 `_BUFFERED_TEXT_STAGES` 控制：生成期间不发送正文，仅在收到最终答案事件后通过 `field_set` 清理旧正文并发布最终内容。
- `_merge_stage_chunk()` 对 `partial=True` 的增量事件逐字追加，避免重复数字、字符和换行被误判为重叠内容；非增量事件仍兼容累计文本去重。
- `_iter_event_text_chunks()` 会过滤 `part.thought=True` 的思考文本，并将同一事件中的非思考正文按原顺序拼接；相同内容同时出现在 `content` 和 `output` 时只保留一份。

优化优先级：

1. 保留后端“仅展示 `sql_generator` 最终非思考答案”的协议边界，并用包含工具调用、思考分片和最终正文的事件回放做回归验证。
2. 保留现有中文提示词约束，并验证工具调用之后的最终输出是否遵守；仅追加提示词不能保证修复。
3. 对业务知识中的枚举列表增加“闭集约束”提示：若知识明确给出取值集合，不得自行补充额外枚举值。

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
- 查询结果有数据时调用 `upsert_llm_generated_sql` 沉淀样例；沉淀失败只记日志，不影响本次问数返回。

### SQL 知识沉淀

问数过程中校验通过、实际查出数据的 SQL，会写入现有历史 SQL 图谱节点（`/sql` 页），并解析 `USES` 表/字段血缘。

约定：

- `source=import`：Excel 初始化或 SQL 页手工新建，默认 `enabled=true`，写入 `TYWS_SQL` 向量。
- `source=llm`：智能体沉淀，默认 `enabled=false`，先不写入向量。名称优先用 extractor 改写后的 `retrieval_query`。
- UUID 仍按 workspace + 名称 + SQL 正文计算；同 UUID 的人工导入条目不会被 LLM 沉淀覆盖。
- 沉淀前会与现存 SQL 做相似度校验（规范化 SQL、表集合、字面量、名称）。达到 `SQL_MERGE_SIMILARITY_THRESHOLD`（默认 0.84）则合并到已有样例：人工导入只跳过新建；LLM 样例保留原 UUID/名称/启用状态，更新逻辑说明与最新 SQL。不同地区等字面量差异不会合并。
- 空结果、无法解析血缘的语句不沉淀。
- SQL 节点记录 `created_at` / `updated_at`。SQL 页可按来源、启用状态筛选，并按最近更新、最早更新或名称排序。
- SQL 页「启用 / 停用」写入 `enabled`。停用时删除对应向量记录，召回 Cypher 使用 `coalesce(s.enabled, true)=true`，旧节点缺字段视为启用。
- 启用后的样例才进入 search / schema context，供后续 SQL 生成参考。

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

- `/chat`：智能对话（登录后默认进入）
- `/catalog`：数据目录
- `/enums`：枚举值
- `/relations`：表关系
- `/knowledge`：业务知识
- `/sql`：历史 SQL（区分人工导入 / LLM 生成，可用「启用 / 停用」控制是否参与问数生成；左侧支持来源/状态筛选与时间排序）
- `/ingest`：导入任务

智能对话页：

- 通过 `fetch + ReadableStream` 消费 SSE。
- SSE 解析在 `web/src/app/features/chat/chat-stream.ts`。
- SSE 补丁应用也在 `chat-stream.ts`，需要同时支持 `text_append` 与 `field_set`。
- 消息部件排序、合并和 Markdown 重排在 `web/src/app/features/chat/chat-message-parts.ts`。
- 工作流展示组件在 `web/src/app/features/chat/components/`。

### 当前工作流展示

前端显示节点：解析问题 → 规划步骤 → 检索上下文 → 确定数据表与表结构 → 生成 SQL → 校验 SQL → 执行 SQL；若本轮出图，再显示分析图表；若本轮出报告，再显示分析报告。规划节点可能出现多次（每条管道结束后再规划），前端会把多次 `planner`/`parse_plan` 合并为一个「规划步骤」，并取最后一次决策。步骤行右侧显示该步耗时（执行中实时计时）；规划、选表、出图、报告等合并节点显示各子步骤耗时之和。没有计时数据的历史消息仍显示「已完成」。

- `mergeDisplayToolParts()` 将 `table_selector`、`route_after_table_selection`、`format_no_table_response`、`load_sql_schema_context` 合并为 `table_context`；将报告相关节点合并为 `report_analysis`；后端实际节点仍独立执行。
- “检索上下文”独立显示召回与图谱补全结果，不能与选表后的结构汇总混为一谈。
- 数据表模块和“库表结构（供生成 SQL）”按表分组，展示表中文名、英文名、摘要及字段内容。
- “库表结构（供生成 SQL）”、表关系、业务知识、SQL 是同级分类，不要在库表结构下再加“数据表”这一层。
- 业务知识分为专用知识和通用知识。当前前端 `knowledgeContextGroups()` 根据文本特征分组；后端通用知识保留依赖图谱绑定关系与召回 UUID，两者不能相互替代。
- SQL 分类展示样例名称、逻辑说明和 SQL 正文；条数按样例条数计，不要把语句行拆成多条。
- SQL 页条目需展示来源（人工导入 / LLM 生成）和「启用 / 停用」按钮；LLM 沉淀样例默认停用，启用后才写入向量并进入召回。
- 选表结果集中展示中文名、英文名和一份选表理由；合并节点过滤重复的“用户问题、选表说明、候选表”分类，独立 `schema_plan` 部件不再重复展示。
- 原始输入/输出仍可在默认收起的“查看技术详情”中查看，业务摘要优先使用中文。

### 检索分类折叠

实现位于 `chat-tool-part.component.ts` 和对应 HTML，当前工作区已经完成：

- 外层工具节点按 `phase === 'call'` 默认展开，结果阶段默认收起。
- `search` 内的数据表、表关系、业务知识、SQL 分类默认全部收起，标题保留名称和数量。
- `expandedSearchContextSections` 使用 signal 保存分类标题集合，通过 `[open]` 和 `(toggle)` 独立控制各分类，允许同时展开多个。
- 正文由 `@if (isContextSectionExpanded(section))` 控制，仅展开后创建表分组、知识分组等内容。
- 关闭再打开外层节点时，同一组件实例内的分类状态保留；状态未持久化，刷新、销毁重建组件或新一轮创建新组件时重新收起。不要承诺跨路由或刷新保留。
- 该默认收起策略仅针对 `search`；其他节点的分类默认展开，不应用检索节点的状态集合。
- 分类数量当前来自 `section.items.length`，包含表标题、说明、摘要及字段等展示项，不能把“124 条”解释成“124 张表”或 Milvus 原始命中数。

### 数据目录、枚举值和表关系

- 数据目录采用表列表、字段列表、详情区三列布局；详情区已去除外围留白、边框和阴影，与工作区衔接。
- 表详情以中文名为主标题、英文名为副标题，提供编辑、删除、关系图谱图标操作；统计信息采用紧凑布局。
- 详情页签为概览、关联表、业务知识、引用 SQL；字段仍在中间列表展示，右侧不再重复提供字段页签。
- 全局侧栏展开宽度为 `12rem`、收起宽度为 `3.25rem`；主区使用 `minmax(0, 1fr)`。目录通过列宽及 `min-width: 0` 控制横向空间，长内容由内部区域滚动；仍需对窄视口验证是否裁切。
- 枚举值页展示关联表、字段及其说明，表关系页展示关联双方及条件。
- 枚举值、表关系页底部分页顺序为：共多少条 → 每页条数下拉框 → 上一页 → 页码 → 下一页；支持 10/20/30/50/100 条，默认 50 条，变更页大小会重新请求数据。

### 聊天滚动与 Markdown

- `chat.page.ts` 已监听 `visibilitychange`、窗口 `focus` 和正文 `ResizeObserver`，在恢复可见或内容高度变化后校准底部状态；底部定位优先使用 `threadBottom` 锚点。
- 上述可见性处理针对浏览器后台/焦点恢复，不等同于实现了跨 Angular 路由保存会话或流式请求。
- 助手消息使用头像加正文的 Grid 布局；正文、`app-chat-part`、`app-chat-text-part` 和文本块已有 `min-width: 0`、全宽约束。
- `ChatMarkdownComponent` 在流式输出时跳过代码块、表格和结果区的重型 DOM 增强，结束后再处理。
- Markdown 组件仍为 `ViewEncapsulation.None`，CSS 已通过实际宿主选择器 `app-chat-markdown` 设置块级全宽，避免 `:host` 在无封装模式下失效。

## 启动顺序

前置条件：

- 本地 Neo4j 已启动。
- 本地 Milvus 已启动。
- `.env` 已配置大模型、embedding、Neo4j、Milvus、业务数据库。

首次准备（已经完成的步骤无需反复执行）：

```powershell
# 1. 安装 Python 依赖
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. 安装前端依赖
cd web
npm.cmd install
cd ..

# 3. 仅首次初始化或明确要求全量重建时执行，会清空图谱和向量数据
.\.venv\Scripts\python.exe scripts\init.py
```

日常启动：先确认依赖服务可连接，在项目根目录启动后端，再在另一个终端启动前端。

```powershell
# 终端一：后端，智能体随后端运行，无需单独启动
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

```powershell
# 终端二：前端开发服务
cd web
npm.cmd start
```

- 后端开发需要热更新时可追加 `--reload`，等价于激活环境后执行 `uvicorn server.app:app --host 127.0.0.1 --port 8000 --reload`。
- Windows 后台运行或配置变更后重启，推荐不带 `--reload`，降低重载父子进程残留的排查成本。
- 修改 `.env` 后必须重启后端；重启前确认本项目对应的监听进程，避免误停其他服务，重启后验证 `/api/chat/protocol` 及实际问数请求。
- PowerShell 拦截 `npm.ps1` 时直接使用 `npm.cmd`，不需要修改系统执行策略。

访问方式：

- 后端 API：`http://127.0.0.1:8000`
- 前端开发服务：`http://localhost:4200`
- 如果已构建 `web/dist/web/browser`，后端会挂载静态前端，访问 `http://127.0.0.1:8000` 也能看到前端页面。

是否需要单独启动前端：

- 开发前端时使用 `npm.cmd start`，访问 `http://localhost:4200`；`web/proxy.conf.json` 将 `/api` 代理到 8000。
- 只验证后端和已构建页面时，可以只启动后端。
- 静态托管模式先在 `web` 执行 `npm.cmd run build`，再启动后端。前端源码修改后必须重新构建并刷新浏览器，8000 不会自动使用 4200 的开发资源。
- `server/app.py` 导入时检查 `web/dist/web/browser` 是否存在；若后端启动时该目录不存在，首次构建后需要重启后端才能挂载。
- SPA 浏览器路由对接受 HTML 的 GET/HEAD 请求回退到 `index.html`，`/api` 请求不走此回退。

## 环境变量约定

不要提交真实 `.env` 密钥。文档和示例只允许写占位值。

关键配置：

```env
LLM_MODEL_NAME=openai:...
OPENAI_API_BASE=...
OPENAI_API_KEY=...
LLM_FALLBACK_MODEL_NAME=openai:...
LLM_FALLBACK_API_BASE=https://ark.cn-beijing.volces.com/api/plan/v3
LLM_FALLBACK_API_KEY=...

EMBEDDING_MODEL_NAME=openai:...
EMBEDDING_API_BASE=...
EMBEDDING_API_KEY=...
EMBEDDING_MODE=litellm

VECTOR_COLLECTION_TABLE=TYWS_TABLE
VECTOR_COLLECTION_ENUM=TYWS_ENUM
VECTOR_COLLECTION_KNOWLEDGE=TYWS_KNOWLEDGE
VECTOR_COLLECTION_SQL=TYWS_SQL
VECTOR_TOPK_TABLE=10
VECTOR_TOPK_ENUM=8
VECTOR_TOPK_KNOWLEDGE=8
VECTOR_TOPK_SQL=5
SQL_MERGE_SIMILARITY_THRESHOLD=0.84
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

模型调用入口：

- 智能体聊天模型全部走同一套入口：`adk_agents/common.py:get_llm_model()`。当前使用点包括 extractor、planner、chitchat、table_selector、sql_generator、chart_planner、report_planner。
- 配置为 `temperature=0.0`、`enable_thinking=False`，并保留 `allowed_openai_params=["enable_thinking"]`，未传 `reasoning_effort`；构造时显式传入 `api_base`/`api_key`/`num_retries=0`。
- 通用文本调用在 `tools/llm.py:chat()`，与智能体共用 `load_llm_profiles()`。
- 主套餐读取 `LLM_MODEL_NAME`、`OPENAI_API_BASE`、`OPENAI_API_KEY`（当前常见为 Coding Plan：`https://ark.cn-beijing.volces.com/api/coding/v3`）。
- 备用套餐读取 `LLM_FALLBACK_MODEL_NAME`、`LLM_FALLBACK_API_BASE`、`LLM_FALLBACK_API_KEY`。Agent Plan 的 OpenAI 兼容地址为 `https://ark.cn-beijing.volces.com/api/plan/v3`，必须使用 Agent Plan 专属 Key，不要和 Coding Plan / 普通方舟 Key 混用。未配齐备用 base 与 key 时不会切换。
- 仅在限流、突发保护（如 `request burst`）或额度类错误、且尚未向调用方产出内容时切换备用套餐；同套餐不连打。Embedding 仍走 `EMBEDDING_*`，默认不切 Agent Plan。
- 上述数值是当前代码配置，不是 `.env` 中可直接修改的同名开关。不要将发出的 `enable_thinking=False` 等同于供应商已经保证不输出思考文本。

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

业务知识用来解释用户问题里已经出现的术语、指标和取值集合，而不是把知识全文的所有稽核规则都变成过滤条件。

- 用户说「宗教」「居民合表」时，应把这些词映射到行业分类、电价名称等存储值。
- 用户没提到的定量定比剔除、分表/分户计量、默认账期下限等，即使出现在业务知识或历史 SQL 样例中，也不应写入本轮 SQL。
- 若业务知识给出明确取值集合，应视为闭集，避免模型根据字段枚举自行扩展。

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
- 问数成功且有结果时沉淀 LLM 生成 SQL；SQL 页以来源标记和「启用 / 停用」控制是否参与生成。
- 导入任务页面和 Excel 初始化。
- 前端智能对话与工作流轨迹展示。

部分实现或待增强：

- 方案中的“数据定位 Agent”目前由 search + table_selector + load_sql_schema_context 组合实现，尚未独立成循环探测 Agent。
- 方案中的“双盲校验”当前主要是 SQL 语法/安全/表字段校验，尚未实现“SQL 反向解释再与原问题比对”的完整闭环。
- 方案中的结果分析、自动图表、报告生成、疑点工单、预警推送尚未完整实现。当前已有基于查询快照的图表规划和基础分析报告（三部分 Word）能力，仍需按样例继续调正文与维度。
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

源头通常是 `sql_generator`。模型可能在 `part.thought` 中输出英文过程，但后端应过滤思考字段，并只发布最终非思考答案。

优化方向：

- 后端只展示最终 SQL 答案，不展示中间过程。
- Prompt 明确禁止英文和工具调用过程说明。
- 工具返回为空时，用中文短句说明，不允许英文推理。

### 正文一字一行或异常换行

目前状态：2026-09-01 的历史问数验证中，已完成的开发页与静态页样本未重现截图中的窄列；当时正文实测宽度为 800px。这只说明这些样本正常，不能据此认定偶发问题已修复，也不能认定宿主样式是已证实根因。

- Markdown 宿主已使用实际选择器 `app-chat-markdown` 设置 `display: block` 与全宽；若再次出现窄列，需记录异常样本的实时计算样式与父容器宽度，不能仅凭历史样本归因。
- 区分真实换行与视觉换行：检查上游文本及 SSE 是否含换行，前端是否生成异常 `<br>` 或多个短段落，以及异常文本和各级父容器的宽度、`display`、`white-space`、`word-break`、`overflow-wrap`。
- 重放 SSE 必须按 `part_id` 顺序处理 `part_start`、`text_append`、`field_set` 等事件。仅将全部 append 拼接会保留已被覆盖的草稿，不能作为最终答案或模型重复输出的证据；历史单字符行统计仅属这类初步检查。
- 同时验证流式中、结束后、侧栏切换、浏览器后台恢复及站内路由返回。`goto()` 完整导航与 Angular 站内路由切换不同，不能相互替代。
- 不要只凭“单字符行数为 0”排除所有模型格式异常，还需检查连续短行、Markdown 列表/表格/代码块，以及同一异常样本的原文和 DOM。
- 2026-09-07 的复现确认：同一最终 ADK 事件中同时包含 `thought=True` 的英文逐词换行和正常中文答案；后端过滤思考字段后，同一事件回放只保留一段中文说明和一个 SQL 代码块。若后续出现其他换行形态，仍需按原始事件、SSE 和 DOM 三层重新取证。

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
npm.cmd run build
npm.cmd test -- --watch=false
```

按变更范围选择构建、单元测试和浏览器交互验证，文档修改不需要调用模型或重建初始化数据。分类折叠调整曾通过构建，但构建成功不等于交互回归通过；包体积预算和 CommonJS 警告需与编译错误区分。

常用验证问题：

- `金华地区电费未结清用户清单`
- `全省高压用户非商业用电，为专变的情况`
- `帮我查询一下本次抄见有功总电量大于1000的低压用户抄表明细数据`

验证重点：

- 检索上下文是否包含表、枚举、业务知识、历史 SQL。
- 选表结果是否显示表英文名和中文名。
- 确定数据表与表结构是否保留通用知识。
- SQL 是否只使用候选表。
- SQL 是否符合业务知识口径。
- 执行结果是否为中文列名。
- 打开“检索上下文”后分类是否全部收起；展开一项是否只创建该项正文；关闭再打开外层节点是否在同一组件实例内保留状态；新问题是否重新收起。
- “确定数据表与表结构”是否仍按既定方式展示，选表说明是否只出现一次。
- 枚举值和表关系页是否能翻页、调整每页条数并滚动查看全部当前页内容。
- 前端 4200 与静态 8000 是否加载预期版本；正文宽度、正常段落换行、列表和 SQL 代码块是否在流式前后保持可读。
- 浏览器切到后台再恢复、用户主动向上滚动及内容继续增长时，是否保持正确的滚动位置和“回到底部”状态。

## 临时文件与安全

不要提交：

- `.env`
- 本地日志
- 临时 docx/txt
- 大模型或 embedding 缓存中的敏感内容
- 真实 API Key
- `__pycache__`、`.pyc`、`web/.angular/cache` 等运行/构建缓存；已有受版本管理的缓存不要混入功能修改。
- 原始 SSE 与复现日志可能包含业务元数据、查询内容和认证信息，应保存在本地临时目录并脱敏后再分享，禁止将登录 token 写入文档。

如果为了分析外部 Word/Excel 文档复制了临时文件到项目根目录，完成后应删除。
