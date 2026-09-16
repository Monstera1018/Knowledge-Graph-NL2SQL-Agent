# 自然语言问数助手 API 调用说明

本文说明如何**不经过前端页面**，直接调用后端智能体（Knowledge-graph NL2SQL agent）：用自然语言问题做知识/元数据检索，再生成、校验并执行只读 SQL。

当前**没有**单独的「只生成 SQL」HTTP 接口。问数能力全部挂在聊天 SSE 接口上，前端 `/chat` 页也走同一条链路。

最后核对日期：2026-09-16。以仓库当前代码为准。

---

## 1. 这条链路实际做什么

一次成功的问数请求会按顺序执行：

```text
自然语言问题
  → extractor        改写检索 query、提取 keywords、判断 intent
  → search           Milvus 向量召回 + Neo4j 图谱补全（检索上下文）
  → table_selector   仅从候选表白名单选物理表名
  → load_sql_schema_context  加载已选表的完整字段/枚举/知识/JOIN/历史 SQL
  → sql_generator    生成中文说明 + SQL 代码块
  → validate_sql     只读/安全/表字段/方言校验（失败最多重试 3 次）
  → execute_sql      在业务库执行已校验 SQL，返回 Markdown 表格
```

若 extractor 判定为闲聊，会走 `chitchat`，**不会**检索、也不会生成 SQL。  
若选表为空，会走 `format_no_table_response`，**不会**生成 SQL。

智能体定义在 `adk_agents/copilot/agent.py`，由 `server/chat/service.py` 按请求启动，**不需要单独起一个 agent 进程**。

---

## 2. 前置条件

1. Neo4j、Milvus 已启动，且已完成元数据初始化（`python scripts/init.py` 或导入任务）。
2. `.env` 中大模型、embedding、图谱、向量库、业务库配置正确。
3. 后端已启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

4. 探活：

```http
GET http://127.0.0.1:8000/health
```

期望：`{"status":"ok"}`。

5. 协议探活（无需登录）：

```http
GET http://127.0.0.1:8000/api/chat/protocol
```

期望：`{"version":1}`。

FastAPI 交互文档：`http://127.0.0.1:8000/docs`。

---

## 3. 调用总览

| 项 | 值 |
|----|----|
| Base URL | `http://127.0.0.1:8000` |
| 鉴权 | `Authorization: Bearer <token>` |
| Workspace | 可选头 `X-Workspace-Id`；不传则用用户默认 workspace（本地一般为 `default`） |
| Agent | `copilot`（目前唯一可用） |
| 问数入口 | `POST /api/chat/sessions/{session_id}/messages` |
| 响应 | `text/event-stream`（SSE），不是一次性 JSON |
| 会话 ID | 调用方自定非空字符串；后端没有该会话则当场创建 |

建议：测单轮时用新 UUID 作为 `session_id`；测「杭州呢」这类追问时复用同一个 `session_id`。

---

## 4. 鉴权

Token 存在后端**进程内存**里，有效期 **7 天**。**重启 uvicorn 后旧 token 全部失效**，需要重新登录。

### 4.1 登录

```http
POST /api/auth/login
Content-Type: application/json
```

请求体：

```json
{
  "username": "admin",
  "password": "admin"
}
```

账号以 `data/auth/users.json` 为准，不要把真实密码写进仓库。上面是本地开发常用示例。

成功响应 `200`：

```json
{
  "token": "……",
  "user": {
    "id": "local-admin",
    "username": "admin",
    "display_name": "本地管理员",
    "workspace_ids": ["default"],
    "default_workspace_id": "default"
  }
}
```

失败 `401`：`{"detail":"用户名或密码错误"}`。

### 4.2 后续请求头

```http
Authorization: Bearer <token>
X-Workspace-Id: default
```

`X-Workspace-Id` 可省略。若传入的 workspace 不在 `workspace_ids` 中，返回 `403`。

### 4.3 校验当前用户（可选）

```http
GET /api/auth/me
Authorization: Bearer <token>
```

### 4.4 登出（可选）

```http
POST /api/auth/logout
Authorization: Bearer <token>
```

成功为 `204` 无响应体。

---

## 5. 可用 Agent

```http
GET /api/chat/agents
Authorization: Bearer <token>
```

响应示例：

```json
{
  "agents": [
    {
      "id": "copilot",
      "name": "自然语言问数助手",
      "description": "自然语言问数助手",
      "capabilities": ["retrieval", "sql", "streaming", "sessions"]
    }
  ]
}
```

发消息时 `agent_id` 必须是 `copilot`，否则 `404`。

---

## 6. 发送问数消息（核心接口）

```http
POST /api/chat/sessions/{session_id}/messages
Authorization: Bearer <token>
Content-Type: application/json
Accept: text/event-stream
```

路径参数：

- `session_id`：非空。推荐 `[guid]::NewGuid().ToString("N")` 或 UUID。

请求体：

```json
{
  "agent_id": "copilot",
  "content": "金华地区电费未结清用户清单"
}
```

| 字段 | 类型 | 约束 |
|------|------|------|
| `agent_id` | string | 必填，目前只能是 `copilot` |
| `content` | string | 必填，用户自然语言问题 |

响应：

- `Content-Type: text/event-stream`
- 每条事件一行 `data: {json}`，事件之间空一行
- 需要**边收边解析**；整轮常见耗时数十秒（多次 LLM + Milvus + Neo4j）
- 不要用会把响应全部缓冲完才返回的客户端（PowerShell 的 `Invoke-RestMethod` 不适合读 SSE）

---

## 7. SSE 事件协议（v1）

每条 `data:` 后是一个 JSON 对象，公共字段：

```json
{
  "v": 1,
  "type": "turn_start | part_start | part_delta | part_end | turn_end | error",
  "session_id": "……",
  "turn_id": "本轮 UUID",
  "ts": "ISO-8601",
  "agent_id": "copilot"
}
```

### 7.1 事件类型

| `type` | 含义 | 关键字段 |
|--------|------|----------|
| `turn_start` | 本轮开始 | `user_message` |
| `part_start` | 新增一个展示部件 | `part` |
| `part_delta` | 更新已有部件 | `part_id` + `patch` |
| `part_end` | 部件结束（内容已在前面 delta 里） | `part_id` |
| `turn_end` | 本轮结束 | `status`: `ok` / `error` / `cancelled` |
| `error` | 出错 | `message`、`code`、`recoverable` |

收到 `turn_end` 即可认为流结束。

### 7.2 部件 `kind`

| `kind` | 用途 |
|--------|------|
| `text` | 面向用户的正文（生成 SQL 的说明 + 代码块、闲聊、未选到表、校验失败说明） |
| `workflow` | 工作流进度摘要 |
| `tool` | 某个后端阶段：`name` 为阶段名，`phase` 为 `call` 或 `result` |
| `data` | 结构化结果，如选表计划、SQL 诊断、执行结果表 |

### 7.3 `patch.op`

| `op` | 作用 |
|------|------|
| `text_append` | 把 `patch.text` 追加到 text 部件 |
| `field_set` | 按 `path` 覆盖字段。`path=text` 会**整段替换**正文（生成 SQL 阶段会先清空再写入最终答案） |
| `payload_merge` | 合并到 data 部件的 `payload` |

**不要**把同一 `part_id` 上所有 `text_append` 简单拼起来当作最终 SQL。生成 SQL 阶段会用 `field_set` 清掉草稿，再写入最终非思考文本。应按 `part_id` 顺序应用补丁。

### 7.4 工具阶段 `part.name`

| `name` | 前端标题 | 说明 |
|--------|----------|------|
| `extractor` | 解析问题 | 改写 query / keywords / intent |
| `search` | 检索上下文 | Milvus 召回 + 图谱补全后的 Markdown |
| `table_selector` | 选择数据表 | JSON：`tables` + `reason` |
| `load_sql_schema_context` | 汇总表结构 | 已选表完整字段树 |
| `sql_generator` | 生成 SQL | 最终用户可见正文（缓冲后一次性放出） |
| `validate_sql` | 校验 SQL | `result.ok`；失败含 `errors`、`checked_sql` |
| `execute_sql` | 执行 SQL | 查询结果；另有 `kind=data` 的表格 payload |
| `chitchat` | 闲聊回复 | 非问数，无 SQL |
| `format_no_table_response` | 未匹配到数据表 | 无 SQL |
| `format_validation_failure_response` | SQL 修复失败 | 3 次校验仍失败 |

`sql_generator` 生成期间**不向 SSE 推正文**，只在拿到最终非思考答案后，用 `field_set` 写入 text 部件。

---

## 8. 怎样从流里取出 SQL

推荐按优先级取「真正会执行」的 SQL：

1. **校验通过并已执行**  
   看 `execute_sql` 对应的用户正文，或 `data` 部件里 `payload.kind = "sql_result"`。  
   执行节点输出的 Markdown 含：

   ```markdown
   **执行的 SQL**

   ```sql
   SELECT ...
   ```
   ```

   这是校验通过后的规范化 SQL，优先用这一份。

2. **模型刚生成、尚未/未能执行**  
   看 `sql_generator` 结束后 text 部件里的 ` ```sql ` 代码块。  
   这是模型原文，校验可能改写或拒绝它。

3. **校验失败**  
   看 `validate_sql` 的 `output.result`：

   ```json
   {
     "ok": false,
     "errors": ["……"],
     "attempt": 1,
     "checked_sql": "SELECT ...",
     "recovery_action": "repair"
   }
   ```

   `recovery_action` 为 `repair` 时会再生成；为 `stop` 时本轮结束。成功时仅为 `{"ok": true}`，规范化 SQL 在后续执行结果里。

4. **没有 SQL 的正常结束**  
   - 闲聊：只有 `chitchat` 文本  
   - 未选到表：`format_no_table_response`  
   - `turn_end.status = error`：看 `error` 事件

---

## 9. 完整调用示例

### 9.1 PowerShell（推荐用 curl.exe 读 SSE）

```powershell
$base = "http://127.0.0.1:8000"

$login = Invoke-RestMethod -Method Post -Uri "$base/api/auth/login" `
  -ContentType "application/json" `
  -Body '{"username":"admin","password":"admin"}'

$token = $login.token
$sessionId = [guid]::NewGuid().ToString("N")
$outFile = Join-Path $env:TEMP "tyws-chat.sse"

$bodyObj = @{
  agent_id = "copilot"
  content  = "金华地区电费未结清用户清单"
} | ConvertTo-Json -Compress

curl.exe -N --max-time 300 -X POST "$base/api/chat/sessions/$sessionId/messages" `
  -H "Authorization: Bearer $token" `
  -H "X-Workspace-Id: default" `
  -H "Content-Type: application/json" `
  -H "Accept: text/event-stream" `
  --data-binary $bodyObj `
  -o $outFile

Write-Host "session_id=$sessionId"
Write-Host "sse file=$outFile"

Select-String -Path $outFile -Pattern "sql_generator|validate_sql|execute_sql|```sql|sql_result|checked_sql|turn_end"
```

`--max-time 300` 表示最多等 5 分钟。本地问数经常超过 60 秒。

### 9.2 Python（解析 SSE 并打印 SQL）

在项目根目录、已激活 venv 的前提下：

```python
from __future__ import annotations

import json
import re
import uuid

import httpx

BASE = "http://127.0.0.1:8000"
QUESTION = "金华地区电费未结清用户清单"


def iter_sse_data(response: httpx.Response):
    buf = ""
    for chunk in response.iter_text():
        buf += chunk
        while "\n\n" in buf:
            raw, buf = buf.split("\n\n", 1)
            for line in raw.splitlines():
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload:
                        yield json.loads(payload)


def apply_patch(parts: dict, event: dict) -> None:
    part_id = event.get("part_id")
    patch = event.get("patch") or {}
    if not part_id or part_id not in parts:
        return
    part = parts[part_id]
    op = patch.get("op")
    if op == "text_append" and part.get("kind") == "text":
        part["text"] = part.get("text", "") + (patch.get("text") or "")
    elif op == "field_set" and patch.get("path") == "text" and part.get("kind") == "text":
        part["text"] = str(patch.get("value") or "")
    elif op == "field_set" and patch.get("path") == "output" and part.get("kind") == "tool":
        part["output"] = patch.get("value")
    elif op == "field_set" and patch.get("path") == "phase" and part.get("kind") == "tool":
        part["phase"] = patch.get("value")
    elif op == "payload_merge" and part.get("kind") == "data":
        part["payload"] = {**(part.get("payload") or {}), **(patch.get("payload") or {})}


def extract_sql_blocks(text: str) -> list[str]:
    return re.findall(r"```sql\s*([\s\S]*?)```", text, flags=re.I)


def main() -> None:
    session_id = uuid.uuid4().hex
    with httpx.Client(timeout=httpx.Timeout(300.0)) as client:
        login = client.post(
            f"{BASE}/api/auth/login",
            json={"username": "admin", "password": "admin"},
        )
        login.raise_for_status()
        token = login.json()["token"]
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Workspace-Id": "default",
            "Accept": "text/event-stream",
        }

        parts: dict[str, dict] = {}
        with client.stream(
            "POST",
            f"{BASE}/api/chat/sessions/{session_id}/messages",
            headers=headers,
            json={"agent_id": "copilot", "content": QUESTION},
        ) as resp:
            resp.raise_for_status()
            for event in iter_sse_data(resp):
                et = event.get("type")
                if et == "part_start" and event.get("part"):
                    part = event["part"]
                    parts[part["id"]] = part
                    if part.get("kind") == "tool":
                        print(f"[stage] {part.get('name')} {part.get('phase')}")
                elif et == "part_delta":
                    apply_patch(parts, event)
                elif et == "error":
                    print("ERROR:", event.get("message"))
                elif et == "turn_end":
                    print("TURN:", event.get("status"))

    print("session_id =", session_id)

    for part in parts.values():
        if part.get("kind") == "tool" and part.get("name") == "validate_sql":
            print("validate_sql =", json.dumps(part.get("output"), ensure_ascii=False)[:2000])

    texts = [p.get("text", "") for p in parts.values() if p.get("kind") == "text"]
    for text in texts:
        blocks = extract_sql_blocks(text)
        for sql in blocks:
            print("----- SQL -----")
            print(sql.strip())

    for part in parts.values():
        payload = part.get("payload") or {}
        if part.get("kind") == "data" and payload.get("kind") == "sql_result":
            print("sql_result rows:", payload)


if __name__ == "__main__":
    main()
```

依赖：`httpx`（项目 `requirements.txt` 若未包含，可临时 `pip install httpx`）。

---

## 10. 推荐测试问题

与现有问数样本一致：

- `金华地区电费未结清用户清单`
- `全省高压用户非商业用电，为专变的情况`
- `帮我查询一下本次抄见有功总电量大于1000的低压用户抄表明细数据`

观察重点：

1. `search` 结果是否含数据表、表关系、业务知识、历史 SQL。
2. `table_selector` 的 `tables` 是否为白名单中的完整物理表名。
3. `sql_generator` 最终正文是否为中文说明 + 一个 SQL 代码块。
4. `validate_sql` 是否 `ok: true`。
5. `execute_sql` 是否返回表格。本地 SQLite 示例库若未灌业务行，**SQL 仍会生成，结果可能为空表**。

---

## 11. 多轮对话

- **同主题追问**（「杭州呢」「换成金华」）：复用同一个 `session_id`。extractor 会读会话历史做问题改写。
- **新问题**：仍可复用 session，但每轮开始会清理上一轮 SQL 状态（选表、schema、已校验 SQL），避免串题。若希望完全隔离，换新的 `session_id`。

ADK 会话默认落在 `data/chat/adk_sessions.sqlite3`。

---

## 12. HTTP 错误

| 状态码 | 典型原因 |
|--------|----------|
| 400 | `session_id` 为空，或缺少 workspace |
| 401 | 未带 token、token 过期、后端重启导致内存会话丢失 |
| 403 | `X-Workspace-Id` 无权访问 |
| 404 | `agent_id` 不是 `copilot` |
| 422 | 请求体缺 `agent_id` / `content` |

流已经开始后的业务失败，多数走 SSE `error` 或 `turn_end.status=error`，HTTP 仍可能是 `200`。

---

## 13. 当前限制（调用前需要知道）

- **不能**只调用 `sql_generator`，跳过检索和选表。
- **不能**请求「只生成 SQL、不执行」。校验通过后会执行只读查询。
- SQL 必须只读；表名必须来自本轮选表白名单，模型改写的表名会被校验拒绝。
- 生成阶段的英文思考文本不会作为用户正文下发；最终答案以 `sql_generator` 的 `field_set` 文本为准。
- 整轮耗时受大模型和检索影响，客户端超时建议 ≥ 180～300 秒。
- CORS 由环境变量 `CORS_ORIGINS` 控制；用 curl/Python 直连本机不受影响。

---

## 14. 和图谱补全的关系（便于对照结果）

`search` 阶段的「检索上下文」不是纯向量命中，而是：

1. Milvus 按表/字段、枚举、知识、历史 SQL 四个 collection 召回；
2. Neo4j 按 UUID 做定向补全（字段/枚举向上找父表，知识 `DESCRIBES` 拉绑定表，再查一跳 `JOINS` / 知识 / 历史 SQL）；
3. 检索阶段**不会**把父表的全部字段展开；
4. 选表之后 `load_sql_schema_context` 才加载已选表的完整字段和枚举，供生成 SQL。

因此：你在 SSE 的 `search` 工具输出里看到的是「局部字段树」；在「确定数据表与表结构」里看到的才是生成 SQL 用的完整库表结构。
