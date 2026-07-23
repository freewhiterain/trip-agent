# Trip 项目新对话交接说明

> 这份文件用于把当前对话的关键记忆交接给新的 AI。开始工作前请先完整阅读本文件，再阅读项目中的进度账本、设计文档和实施计划。

## 一、当前项目

- 项目目录：`D:\Desktop\project\Trip`
- 当前分支：`main`
- 用户明确要求：直接在 `main` 分支修改。
- 用户明确要求：不要创建新分支，不要创建 worktree，不要提交 commit。
- 必须保留工作区中已经存在的用户修改；不能使用 `git reset --hard`、`git checkout --` 或其他会覆盖已有修改的操作。
- 当前工作区本来就有大量修改、删除和未跟踪文件。它们不一定都是本次任务产生的，开始时必须先查看 `git status --short --branch`，不能把整个工作区当成干净仓库。

## 二、用户之前表达过的想法

用户最初希望把项目改成其提供的图片样式，之后发现项目中很多内容和交互逻辑都不正确。用户特别指出，之前围绕“槽位”的理解不对，并确认真正需要的是一个流程图式的 Agent 工作流：

```text
用户消息
  -> Main Agent 判断本轮意图
      -> 明确要规划旅行
          -> 展示旅行规划表单工具
      -> 目的地没有确定，但用户需要推荐
          -> 目的地推荐 RAG
          -> 用户选择目的地后恢复原来的表单
      -> 开放式旅行问题
          -> 只使用 RAG 回答
      -> 普通聊天
          -> 直接回答
```

用户已经确认按这个方向继续，并同意由 AI 按技术建议推进。用户还明确了：

- 目的地相关 Worker 的责任是“目的地的景点”，不是笼统的目的地研究。因此后续责任名称应使用 `attractions`。
- Worker 的具体数据源暂时先放下，之后再单独设计，不要在当前重构中擅自引入新的数据源策略。
- 用户选择了直接实施方案，不需要再花时间比较分支、worktree 或提交方式。
- 用户说“这个问题先放下”时，表示该问题暂不扩展范围；当前主线仍是 Agent 路由、表单工具、Supervisor 和前端恢复。

关于用户提供的参考图片：图片用于表达目标界面和体验，但图片本身的全部视觉细节没有可靠地写入当前文本上下文。不要凭空描述图片中没有确认过的内容；如果需要做前端视觉实现，应先查看 `1_zhixing.html` 和实际图片文件，再遵循项目现有风格和 `ui-ux-pro-max` 指导。

## 三、已经确认的新业务逻辑

系统要从旧的 slot-driven chat 改成以下架构：

```text
Main Agent
├── confirmed/direct planning
│   └── three-step form tool
│       └── Tool Result
│           └── Supervisor
│               └── attractions / weather / transport / hotel / food Workers
│                   └── Main Agent 组织最终答复
├── destination undecided
│   └── destination recommendation RAG
│       └── resume the same form invocation
├── open travel question
│   └── RAG only
└── ordinary conversation
    └── direct response
```

### 1. 新建会话

每次新建会话后，系统必须在同一个数据库事务中持久化一条 Assistant 主动邀请：

```text
需要我帮你规划一下旅行吗？
```

这条消息的 `extra_info` 必须包含：

```json
{"kind": "conversation_offer"}
```

创建会话的 POST 响应应包含 `initial_message`。读取会话不能再次创建或复制这条消息。

### 2. 用户确认后打开表单

只有当最近的相关 Assistant 消息正好是上面的主动邀请时，用户回复“好的”“可以”“是”等肯定回答才打开表单。历史消息中有目的地、旧行程或旧槽位值，不能单独强迫当前消息进入规划流程。

### 3. 明确规划请求

用户也可以直接说：

```text
帮我规划一次成都旅行
帮我规划东京五天旅行
我想去京都旅游
```

这类明确规划请求直接打开表单。能够确定提取的目的地、日期或天数可以作为安全的初始值；不能确定的字段必须留空，让用户填写。日期和天数不能靠系统默认值补全。

### 4. 三步表单

表单是 Main Agent 发出的前端工具，不是普通聊天消息，也不是后端根据缺失 slot 自动弹出的固定卡片。工具包含三个必填字段：

| 字段 | 含义 | 校验 |
|---|---|---|
| `destination` | 目的地 | 非空字符串，长度受后端 schema 限制 |
| `departure_date` | 出发日期 | 有效日期，使用结构化日期格式 |
| `days` | 旅行天数 | 必须是整数，范围 `1-30` |

用户填写完成后，前端提交结构化 Tool Result。只有三个字段都有效并且用户明确提交，才允许进入 Supervisor。

### 5. 目的地不确定

当用户表达想旅行但没有决定目的地，并且需要推荐时：

1. 暂停当前表单工具调用。
2. 保存已经填写的部分值和当前步骤。
3. 调用目的地推荐 RAG。
4. 展示推荐结果。
5. 用户选择或输入目的地后，恢复同一个工具调用。
6. 继续填写日期和天数。

目的地没有确认前，不能执行 Supervisor。

### 6. 开放式旅行问题

例如：

```text
东京有哪些景点？
最近成都有什么好玩的？
京都适合几月份去？
```

这类问题直接进入开放式旅行问答 RAG：

- 不弹出旅行规划表单。
- 不调用 Supervisor。
- 不因为历史消息里出现目的地，就把问题转成完整行程规划。
- 回答结束后不自动追问“要不要规划旅行”。

## 四、工具和事件契约

旅行规划工具的名称是：

```text
collect_trip_requirements
```

Tool Call 至少包含：

```json
{
  "tool": "collect_trip_requirements",
  "call_id": "unique-call-id",
  "arguments": {
    "initial_values": {
      "destination": "成都"
    }
  }
}
```

完整 Tool Result 应类似：

```json
{
  "tool": "collect_trip_requirements",
  "call_id": "unique-call-id",
  "status": "completed",
  "result": {
    "destination": "成都",
    "departure_date": "2026-08-10",
    "days": 4
  }
}
```

SSE 至少需要支持：

- `tool_call`
- `tool_result`

事件中必须保留 `tool`、`call_id`、`arguments`、`status` 和结果等信息，以便独立 HTML 前端渲染并恢复工具状态。

## 五、Supervisor 和 Workers

Supervisor 只接收已经完整确认的结构化旅行需求，不直接决定是否弹表单，也不直接负责普通聊天。

Supervisor 负责创建和调度以下五类 Worker：

1. `attractions`：目的地景点
2. `weather`：天气
3. `transport`：交通
4. `hotel`：住宿
5. `food`：美食

当前只确定职责边界，不改变具体数据源策略。之后 Supervisor 汇总 Worker 结果，Main Agent 再组织成面向用户的最终答复。

## 六、当前已完成内容

### Task 1：契约

涉及文件：

- `app/schemas/tools.py`
- `app/schemas/events.py`
- `tests/test_main_agent_contracts.py`

已完成内容：

- 添加 Main Agent decision 和工具相关 schema。
- 添加 `tool_call` / `tool_result` SSE 事件类型。
- `TripFormResult` 的三个字段全部必填。
- 日期使用 ISO 字符串兼容 SSE/JSON。
- 已有 JSON 序列化回归测试。
- 证据：11 个 focused tests 已通过。

### Task 2：工具调用持久化

涉及文件：

- `app/models/tool_invocation.py`
- `app/governance/tool_invocations.py`
- `app/models/__init__.py`
- `tests/test_tool_invocations.py`
- `tests/test_tool_invocations_postgres.py`

已完成内容：

- 持久化工具调用、状态、部分填写值和最终结果。
- `CompletionOutcome.completed_now` 用于识别本次是否是唯一获胜的提交。
- 重复或冲突提交不会覆盖第一次完成结果。
- 生产创建流程校验 conversation 所有权。
- PostgreSQL 完成操作使用条件原子更新。
- 证据：9 个 focused tests 通过；1 个 PostgreSQL 集成测试因没有可用 Docker/PostgreSQL 而跳过。

### Task 3：Main Agent 路由

涉及文件：

- `app/services/main_agent.py`
- `app/services/planning.py`
- `app/schemas/planning.py`
- `tests/test_main_agent_routing.py`

已完成内容：

- 明确规划意图优先于开放式问题标记。
- 肯定回答只有在主动邀请之后才触发表单。
- 目的地推荐是独立 action。
- 表单安全预填使用确定性提取，不使用 LLM 猜测。
- LLM 路由需要显式 enable，并且需要 API key。
- 历史目的地不会强制当前消息进入规划。
- 已删除自动日期/天数默认值和 `to_requirement_with_defaults()`。
- 证据：27 个 focused tests 通过。

## 七、Task 4 的准确状态

Task 4 的实现已经存在于：

- `app/api/v1/conversations.py`
- `tests/test_conversation_greeting.py`
- `.superpowers/sdd/task-4-report.md`
- `.superpowers/sdd/task-4-review-package.md`

当前实现会在创建会话时添加主动问候，并返回 `initial_message`。已有 fake session、HTTP 序列化、读操作不重复创建、commit 失败回滚路径测试。

但是交接时必须注意：Task 4 报告明确写过，真实 PostgreSQL transaction 测试尚未执行。开始后续任务前，先检查是否已经由 implementer/reviewer 补上以下 opt-in 测试：

- 成功事务确实持久化 conversation 和 greeting。
- 事务回滚后 conversation 和 greeting 都不存在。
- 通过 `RUN_POSTGRES_TESTS=1` 控制。
- 无 PostgreSQL 时只跳过，不伪称已验证。

当前 `.superpowers/sdd/progress.md` 只明确记录 Task 1、Task 2、Task 3 完成，因此不要把 Task 4 或后续任务误报成已完成。完成 Task 4 的测试和 review 后，再更新进度账本。

## 八、下一步任务顺序

### Task 5：替换聊天 API

重点文件：`app/api/v1/chat.py`。

要求：保存 user message，加载有界近期上下文，每轮调用 `MainAgentService`。规划意图要持久化并发出 `tool_call`；开放问题只调用 RAG；普通聊天直接返回；chat endpoint 不能直接调用 Supervisor。要处理旧的 slot/coordinator 调用和已删除方法引用。

### Task 6：Tool Result API

重点文件建议：`app/api/v1/tools.py`，以及路由注册和相关测试。

接口：

```text
POST /api/v1/chat/tools/{call_id}/result
```

必须做会话/用户所有权校验、字段校验、推荐暂停、部分值保存、唯一完成提交和 Supervisor exactly-once 调用。重复提交只能返回已保存状态，不能再次运行 Supervisor。

### Task 7：destination 改为 attractions

更新 `TaskType`、worker 文件/类、planner、supervisor、registry 和测试。不要改变数据源策略。

### Task 8：前端三步工具

主要文件：`1_zhixing.html`。

在开始 UI 工作前必须读取并遵循 `ui-ux-pro-max` skill。前端要渲染 `tool_call`，不能用 `sendMessage()` 伪装提交；要支持三步表单、推荐暂停/恢复、刷新恢复、历史恢复，并替换旧的 `renderAskCard`。完成后要在桌面和移动端做视觉检查。

### Task 9：移除旧逻辑

排查并按引用情况移除或隔离：

- `app/services/intent.py`
- 旧 chat coordinator 路径
- 可能仍被引用的 `app/agents/coordinator.py`
- `hard_missing`
- 自动日期/天数默认逻辑
- 旧 ask chip 行为
- 与新流程冲突的旧测试
- README 中过时说明

删除前必须搜索引用，不要凭文件名直接删除。

### Task 10：端到端验证

验证新建会话、主动邀请、肯定回复表单、直接规划预填、开放问题 RAG、Tool Result、Supervisor exactly-once、推荐恢复、刷新恢复、字段校验和完整测试。

## 九、当前工作区注意事项

当前工作区不是干净状态。已知存在修改、删除和未跟踪内容，包括但不限于：

- `1_zhixing.html`
- `README.md`
- `app/api/v1/chat.py`
- `app/api/v1/conversations.py`
- `app/agents/planner.py`
- `app/agents/supervisor.py`
- `app/schemas/events.py`
- `app/schemas/planning.py`
- `app/services/planning.py`
- `app/services/main_agent.py`
- `app/schemas/tools.py`
- `app/models/tool_invocation.py`
- `.superpowers/`
- `docs/`
- 多个旧的 agents、routers、subagents 和 tests 文件

这些变化必须保留。处理某个文件前，先读当前内容和 diff；不要用旧版本覆盖它。若新实现和用户已有修改冲突，优先保留已确认的新架构，并以最小修改方式合并。

## 十、测试和验证要求

Python 测试通常使用：

```powershell
.venv\Scripts\python.exe -m pytest <specific-tests> -q
```

建议顺序：先跑当前任务的 focused tests，再跑关联 API/schema tests，最后跑完整 pytest。还要运行：

```powershell
.venv\Scripts\python.exe -m compileall app tests
git diff --check
git status --short
```

如果 PostgreSQL、Docker、API key 或外部 RAG 服务不可用，要明确记录为 skipped/unavailable，不能把未执行写成通过。测试失败时先定位根因，不要修改无关功能来绕过失败。

## 十一、协作规则

- 继续使用 Superpowers 的 subagent-driven development 工作方式。
- 任务较大时拆成独立任务，并在任务之间做 review。
- 收到 review 意见时先判断意见是否符合设计和现有代码，再实现必要修复。
- 在声称“完成”“通过”之前，必须实际运行验证命令。
- 不提交代码，除非用户之后明确要求。
- 修改前用简短中文向用户说明正在改哪些文件以及原因。
- 最终汇报要包含：改了什么、测试实际结果、哪些测试因环境跳过、还有什么后续任务。

## 十二、新对话的第一步

新 AI 不要从旧的槽位逻辑重新开始。请按以下顺序执行：

1. 阅读本文件。
2. 阅读 `.superpowers/sdd/progress.md`。
3. 阅读设计文档和实施计划。
4. 查看 `git status --short --branch`。
5. 检查 Task 4 的 PostgreSQL transaction 测试和 review 状态。
6. 若 Task 4 已满足要求，更新进度并进入 Task 5；否则先完成 Task 4 的验证。

第一条回复可以简要说明当前确认的进度，然后继续实际工作，不要只重新给计划。
