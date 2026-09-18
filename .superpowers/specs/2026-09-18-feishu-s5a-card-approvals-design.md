# 飞书 S5a 卡片审批设计

- Date: 2026-09-18
- Status: Draft — architecture approved in brainstorming; awaiting explicit spec approval
- Parent roadmap: [`2026-09-12-feishu-capability-completion-design.md`](2026-09-12-feishu-capability-completion-design.md)
- Scope: S5a only

## 1. Purpose

OpenSRE 的飞书 gateway 当前通过文本提示和回复中的 `approve` / `deny` 词语完成写工具审批。S5a 用 CardKit
Approve/Deny 按钮完整取代该路径，并把现有的身份授权、等待、超时和 shutdown 语义迁移到
`card.action.trigger` 长连接回调。

本设计的成功标准不是“按钮能够触发回调”，而是卡片回调成为安全的授权入口：只有发起该工具请求的
成员，仍在允许名单中并处于原聊天时，才能决定该请求。旁观者、错误聊天、重复、过期、乱序和关闭期间
到达的回调都必须 fail closed，且不能改变共享卡片或 broker 状态。

S5a 完成后，文本 `approve` / `deny` 只是普通对话内容。合并态不保留文本与卡片双轨。

## 2. Goals

1. 用 schema 2.0 CardKit 卡片展示写工具审批请求及 Approve/Deny 按钮。
2. 通过既有 WebSocket worker 接收 `card.action.trigger`，无需新增公网 HTTP 端点。
3. 复用 transport-neutral `ApprovalBroker` 作为工具线程的唯一审批状态机。
4. 对 callback operator 做 allowlist、requester `open_id` 和原 `chat_id` 三重校验。
5. 让首个合法决定在 3 秒内返回 toast 和原地结果卡，同时异步释放等待中的工具线程。
6. 对至少一次投递、并发点击、超时和 shutdown 建立单终态语义。
7. 保证卡片发送失败或空 ID 时没有 broker/pending 泄漏，并默认拒绝工具执行。
8. 通过单元、并发、边界、回归和真实飞书测试 chat 验收后，删除文本审批路径。

## 3. Non-goals

S5a 不实现以下能力：

- 消息 reaction、已读 👀、采纳反馈或重试；这些属于 S5b/S5c。
- 新工具、新 prompt persona 或运行时渠道感知；这些属于 S8a/S7。
- 告警、报告和定时投递卡片化；这些属于 S6。
- 新公网 callback server、轮询机制或 CardKit REST 回调确认。
- 跨进程持久化审批。pending 状态与 gateway 进程生命周期一致，关闭即拒绝。
- 其他 transport 的审批 UX 重构。
- 为审批卡提供文本 fallback。卡片不可发送时必须 fail closed。

## 4. Confirmed decisions

### 4.1 Button payload

采用 brainstorming 方案 A：

- 两个按钮共享 broker 生成的随机、不透明 `approval_id`。
- `action.value` 严格只包含 `{"approval_id": "<opaque-id>"}`。
- 固定 `action.name` 区分 Approve 与 Deny。
- 工具名、参数、原因、正文、token、凭据和授权结论都不进入 `value`。
- callback 中除 `approval_id` 和固定 action 名外的业务字段一律不可信。

Approve/Deny 名称是静态协议标识，不携带请求数据。服务端仍按 `approval_id` 查询 pending，并重新判断
操作者、聊天、生命周期及 broker 状态。

### 4.2 Callback response ownership

合法点击的 callback handler 直接返回 `P2CardActionTriggerResponse`：

- `toast` 仅给点击者确认 Approved 或 Denied。
- `card` 携带完整结果卡并替换共享审批卡。

handler 不调用 CardKit REST API、不提交 executor、不等待工具或 turn。非法、过期或重复点击只返回私有
toast，不含 `card`。

### 4.3 Expired card

超时不主动修改共享审批卡。其按钮仍可被点击，但只会得到私有 unavailable toast。这一选择严格保持
“过期事件不改变共享卡片”，也避免为 S5a 增加定时 CardKit 更新任务。

### 4.4 Migration sequence

实现期间可暂时保留文本审批以便真实 callback 验收；真实测试 chat 通过后，必须在同一 PR 中删除文本
识别、reply-to-prompt 分流和对应测试。最终 PR 不得包含双轨。

## 5. Existing system

当前链路如下：

1. `approval_tool_hooks()` 在 write tool 前调用 transport prompter。
2. `FeishuApprovalPrompter` 发送纯文本提示。
3. `PendingApprovals` 以提示消息的 `message_id` 为键，保存 broker ID、requester 和 chat。
4. 用户回复提示消息；worker 从文本首词解析 approve/deny。
5. worker 校验 allowlist、requester 和 chat，然后 `ApprovalBroker.resolve()`。
6. 工具 executor 线程在 `ApprovalBroker.wait()` 中解除阻塞。

可复用资产：

- `ApprovalBroker`、`approval_tool_hooks()`、`arguments_preview()`。
- `FeishuCardClient.create_card()` / `send_card()`。
- `EventDispatcherHandler.builder().register_p2_card_action_trigger()`。
- SDK `P2CardActionTrigger` 的 operator、context、action 模型，以及 response 的 toast/card 模型。
- `is_open_id_authorized()` 的现有飞书身份策略。

## 6. Ownership and package boundaries

### 6.1 `integrations/feishu/approval_cards.py`

新增 vendor 专用纯构建模块，负责：

- 构造 schema 2.0 审批提示卡。
- 构造 Approved/Denied 结果卡。
- 定义飞书卡片按钮的静态 action 名。

该模块不导入 `gateway`，不读 pending，不做授权或网络调用。通用 markdown 分页继续留在
`card_document.py`；审批卡不是通用文档渲染的分支。

### 6.2 `gateway/transports/feishu/approvals.py`

负责：

- 卡片 prompter 的 create/register/send/wait/cleanup 生命周期。
- 解析 `P2CardActionTrigger`。
- allowlist、requester/chat 校验。
- pending claim、broker resolve 和 callback response 构造。

该模块是飞书审批编排的唯一 owner。worker 只注册和转发事件。

### 6.3 `gateway/transports/feishu/pending_approvals.py`

继续作为线程安全的 server-side authority registry，但改为以 `approval_id` 为键。它保存授权和重放所需
的最小状态，不保存完整工具参数、消息正文或 callback payload。

### 6.4 `gateway/core/middleware/approvals.py`

保持 transport-neutral。只增加所有 transport 均可解释的生命周期原语：

- 显式 `abandon(approval_id)`，用于提示从未成功展示时撤销 entry。
- timeout 与 resolve 的单终态线性化。

此模块不得导入飞书 SDK、卡片类型或 transport 模块。

### 6.5 `gateway/transports/feishu/worker.py`

在同一个 dispatcher builder 上注册消息事件和卡片回调。worker 的薄 wrapper 捕获未预期异常、记录
不含 payload 的服务端日志，并返回不含 card 的通用私有错误 toast，避免 SDK 把异常变成无说明的回调
失败。删除文本决策词表、文本解析及 reply-to-prompt 审批分流；普通回复继续走常规 inbound turn。

## 7. Data model and state machine

### 7.1 Pending record

每条 pending record 至少包含：

- `approval_id`
- `requester_open_id`
- `chat_id`
- `tool_name`，仅用于构造安全的结果卡
- `expires_at`，由单调时钟计算
- 当前状态
- settled decision（仅 approved/denied）与 settled 时间

不保存：

- 完整 `arguments`
- 消息正文
- 凭据、token 或 authorization header
- 原始 callback payload

脱敏后的参数预览只用于建卡调用期间。发送完成后 registry 不需要保留它。

### 7.2 States

```text
OPEN
 ├─ first authorized click ─→ CLAIMED ─→ SETTLED_APPROVED
 │                                  └──→ SETTLED_DENIED
 ├─ deadline ───────────────→ EXPIRED
 ├─ create/send failure ────→ ABANDONED
 └─ gateway shutdown ───────→ CLOSED
```

只有 `OPEN → CLAIMED` 是可竞争的授权 claim。错误用户、错误 chat、未通过 allowlist、未知 action 和畸形
payload 不引起状态迁移。

### 7.3 Settled tombstones

成功决定后 registry 保留最小 tombstone，使同一 callback 的至少一次重投可以返回“already
approved/denied”私有提示，但不能再次 resolve 或更新卡片。

tombstone：

- 只保存 `approval_id`、approved/denied 和 monotonic settled time。
- 保留期复用 `MAX_APPROVAL_WAIT_SECONDS`，不增加用户配置项。
- 在 register/find/claim 等 registry 操作中惰性清理。
- shutdown 时一次性清空。

这让内存占用受“最近一个审批窗口内的请求数”约束，而不是随进程运行时间增长。

## 8. End-to-end flow

### 8.1 Prompt creation

1. prompter 调用 `broker.create(platform="feishu", chat_id=...)` 得到 `approval_id`。
2. 使用工具名、原因和 `arguments_preview(arguments)` 构造审批卡。
3. CardKit `create_card()` 返回非空 `card_id`。
4. 在卡片对用户可见前，以 `approval_id` 注册 pending。
5. `send_card()` 返回非空 `message_id`。
6. 工具线程调用 `broker.wait()`，timeout 取 tool expiry 与 `MAX_APPROVAL_WAIT_SECONDS` 的较小值。
7. `finally` 清理仍为 OPEN/CLAIMED 的 transport record；已 settled 的最小 tombstone按保留期存活。

注册发生在 send 前，消除“卡片已经可点击但 registry 尚未就绪”的窗口。create、register 或 send 任一步
失败时，prompter 清理 pending 并调用 `broker.abandon()`，返回 `(False, "")`。

### 8.2 Callback validation

固定顺序：

1. 读取 event、operator、context、action；缺失则返回错误 toast 或空响应。
2. `action.name` 必须等于固定 Approve/Deny 名称。
3. `action.value` 必须是 mapping，键集合必须严格等于 `{approval_id}`，值为非空字符串。
4. 用 callback 的 `operator.open_id` 和 `context.open_chat_id` 执行现有飞书 allowlist 校验。
5. 按 `approval_id` 查询 server-side record。
6. 比对 requester `open_id` 和原 `chat_id`。
7. 在 registry 锁内原子 claim。
8. 用固定 action 名得到 approved/denied，并调用 `broker.resolve()`。
9. resolve 成功后把 record settle，并返回 toast + 无按钮结果卡。

不根据 callback 的工具名、理由、参数或卡片正文做任何授权决定。

### 8.3 Invalid callbacks

下列情况不修改 broker 或共享卡片：

- allowlist 拒绝
- 非原请求者
- 非原 chat
- 未知/过期 approval ID
- duplicate 或 settled callback
- 不完整或额外字段的 approval value
- unknown approval action
- broker 已 timeout/close

除已 settled duplicate 可私下说明已有 Approved/Denied 结果外，其余授权失败使用同一 unavailable 文案，
不向旁观者区分“存在但无权”和“不存在/过期”。

### 8.4 Callback timing

callback handler 只允许：

- 数据结构读取和固定字符串比较
- allowlist/identity policy 查询
- O(1) dict lookup
- 短临界区状态迁移
- `threading.Event.set()`
- 构造内存 response object

禁止：

- 等待工具或 turn 完成
- CardKit/IM REST 调用
- executor dispatch 或启动新 turn
- retry、sleep 或网络探针

## 9. Concurrency semantics

### 9.1 Concurrent clicks

多个合法点击并发时，registry 锁保证只有一个从 OPEN 进入 CLAIMED。赢家可以 resolve；输家得到私有
duplicate/in-progress 提示，不修改 card 或 broker。

### 9.2 Timeout versus resolve

`ApprovalBroker.wait()` 当前在 `Event.wait()` timeout 后、删除 entry 前存在竞态：resolve 可能在这段窗口
成功，但 waiter 仍记录 expiry。S5a 必须让最终检查与 pop 在线性化锁内完成：

- resolve 先持锁并 set：wait 读取决定。
- timeout 清理先持锁并 pop：后续 resolve 返回 `False`。

每个 approval 只产生一个 resolve/expire 终态审计事件。

### 9.3 Claim versus timeout/close

registry claim 成功后若 `broker.resolve()` 返回 `False`，说明 broker 的 timeout/close 已先线性化。handler：

- 不返回结果卡。
- 把 transport record 归类为 expired/closed，而非 settled。
- 返回私有 unavailable toast。

### 9.4 Shutdown

worker `finally`：

1. drain transport pending/tombstones。
2. `broker.close()` 拒绝并释放所有等待者。

close 后创建的请求继续立即 fail closed；不得等待完整 timeout。

## 10. Card UX

### 10.1 Prompt card

提示卡包含：

- `Approval required`
- 工具名
- 非空审批原因
- 非空的脱敏参数预览
- Approve primary button
- Deny danger button

卡片使用 schema 2.0。参数预览沿用 `arguments_preview()` 的 key-name redaction、secret pattern scrub 和
长度上限，不建立第二套脱敏逻辑。

### 10.2 Result card

Approve：

```text
✅ Approved — <tool name>
```

Deny：

```text
🚫 Denied — <tool name>
```

结果卡不含按钮、raw `open_id`、参数预览或原因。卡片替换与 broker 决定来自同一次合法 callback。

### 10.3 Private feedback

- 首次合法点击：Approved/Denied toast。
- settled duplicate：Already approved/denied toast。
- unauthorized、wrong chat、expired、closed、unknown：统一 unavailable toast。
- malformed OpenSRE approval action：通用 interaction error toast。
- 非 S5a action：空 response，留给未来明确的 dispatcher 扩展。

## 11. Failure behavior

| Failure | Required outcome |
| --- | --- |
| card construction raises | abandon broker; no pending; deny |
| create returns empty card ID | abandon broker; no pending; deny |
| send raises or returns empty message ID | discard pending; abandon broker; deny |
| callback malformed | no resolve; no card update; private error or empty response |
| callback handler internal error | log exception without payload; wrapper returns generic private error toast and no card; broker remains fail closed |
| broker already expired/closed | no card update; private unavailable toast |
| duplicate callback | no second resolve/update/tool execution; private prior-result toast |
| gateway shutdown | drain registry; close broker; release waiters immediately |

禁止用文本审批作为任何失败的 fallback。

## 12. Audit and privacy

允许记录：

- event category
- `approval_id`
- platform
- chat ID
- operator ID
- action outcome
- 拒绝原因类别，如 unauthorized、wrong_actor、wrong_chat、expired、duplicate

禁止记录：

- 原始 callback payload
- message/card body
- 完整工具参数或未脱敏 preview
- app secret、token、authorization header
- SDK exception 中可能携带的敏感响应正文到外部 surface

共享结果卡不展示 raw operator ID。安全审计继续使用 `audit_security_action()`；同一 approval 不得同时记录
resolved 与 expired 两个终态。

## 13. Documentation and control-plane prerequisite

仓库目前没有可导航的飞书 chat gateway 页面。S5a 增加 `docs/messaging/feishu.mdx` 并登记到
`docs/docs.json` 的 Messaging 组。页面只保留会改变操作者行为的信息：

1. 配置 Feishu chat app 和允许的 `FEISHU_ALLOWED_OPEN_IDS`。
2. 使用长连接回调。
3. 在“已订阅的回调”而非“已订阅的事件”中添加 `card.action.trigger`。
4. 配置变化后重新发布应用版本。
5. 审批卡只有原请求者可以决定；文本 approve/deny 不再控制工具。

不写 SDK 内部类、endpoint、历史 bug 或内部状态机。

## 14. Test strategy

遵循 TDD：每类真实失败模式先写失败测试，再写最小实现。避免为同一分支堆叠仅替换字面量的案例。

### 14.1 Pure card tests

新增 `tests/integrations/test_feishu_approval_cards.py`：

- schema 2.0 和预期元素结构。
- 两个按钮名称、样式和只含 approval ID 的 value。
- 工具名、原因和脱敏 preview 的显示。
- secret 永不出现在 serialized card。
- Approved/Denied 卡无按钮、参数和 operator ID。

### 14.2 Registry tests

扩展 `gateway/tests/feishu/test_pending_approvals.py`：

- 正确 requester/chat 可以 claim。
- wrong actor/chat 不消费 OPEN record。
- 并发 claim 恰好一胜者，使用 barrier 而不是 sleep。
- settled duplicate 返回既有结果但不能再 claim。
- tombstone 超期清理且不影响其他 record。
- drain 清空所有状态。

### 14.3 Broker tests

扩展 runtime approval tests：

- `abandon()` 删除未展示请求且后续 wait 立即拒绝。
- unknown/duplicate abandon 安全。
- resolve/timeout barrier race 只有一个终态。
- close 释放现有 waiter，close 后 request 不阻塞。
- 其他 transport 的 create/resolve/wait 契约保持通过。

### 14.4 Prompter and callback tests

重写 `gateway/tests/feishu/test_approvals.py`：

- prompt 卡创建、注册、发送、等待和合法 approve/deny。
- create/send exception、空 card ID、空 message ID 无 broker/pending 泄漏。
- value 多字段、错误类型、未知 action、缺 operator/context 被拒绝。
- allowlist、requester 和 chat 任一失败均不消费 pending。
- 合法 click 返回 card + toast；非法 click 只有 toast。
- duplicate 返回 prior-result toast，无 card。
- broker race 失败不伪装成功。
- 日志和 response 不含 secret、arguments 或 message body。

### 14.5 Worker and integration tests

更新 Feishu worker tests：

- dispatcher 同时注册 message receive 与 card action callback。
- callback 直接处理，不提交 turn executor。
- 文本 `approve` / `deny` 不再被审批分流，作为普通 inbound turn。
- shutdown drain + broker close 释放 approval waiter。

保留并运行：

- 全部 `gateway/tests/feishu`
- approval broker runtime tests
- Feishu card client/document tests
- Slack、Discord、Telegram approval tests
- import/API border tests
- `CI.md` 对涉及模块规定的 lint、format、typecheck 和 test 命令

## 15. Live Feishu acceptance

真实 callback 验收是删除文本路径和合并的硬门禁。开始前必须确认：

- 测试 app 已启用长连接 callback。
- `card.action.trigger` 已在已订阅回调中启用。
- 最新应用版本已发布。
- requester 在 allowlist；群聊中有一个不可审批的旁观者账号。
- 对测试 chat 的外部发送已获得当轮授权。

现场场景：

1. requester Approve：卡片变 Approved，write tool 恰好执行一次。
2. requester Deny：卡片变 Denied，write tool 不执行。
3. group bystander：只收到私有拒绝提示，卡片不变；requester 之后仍可决定。
4. duplicate/redelivery：私有 prior-result 提示；无第二次 resolve、card update 或 tool execution。
5. expired click：私有 unavailable 提示；卡片和 broker 不变。
6. shutdown drain：等待中的工具立即拒绝并释放，无 executor 挂起。

验收证据只保存脱敏的时间、场景和结果，不保存 callback payload、正文、参数或凭据。

若控制台配置、测试身份或凭据使现场验收不可执行，则 PR 不得删除文本路径、不得合并，也不得用 mock
结果代替真实回调。

## 16. Delivery gates

1. 设计 spec 获得用户明确批准。
2. 使用 `superpowers:writing-plans` 生成仅覆盖 S5a 的实施计划。
3. 使用 TDD 实现，并在真实验收前暂时保留文本路径。
4. 完成聚焦和完整本地门禁。
5. 在测试 chat 完成 §15 的真实回调验收。
6. 删除文本审批路径和旧测试，重新运行全部相关门禁。
7. 创建 PR，填写完整模板和 AI disclosure。
8. 每次 push 后监控 CI；失败则取日志、修根因、聚焦复测、再 push。
9. 处理所有 actionable review，达到 Greptile 5/5。
10. 合并后监控 main CI、CodeQL 和 release；任何 post-merge 失败都属于未完成交付。

## 17. Rejected alternatives

### 17.1 Decision inside `action.value`

实现简单，但不符合“value 仅含 opaque approval ID”的严格安全约束，因此拒绝。

### 17.2 Two independent opaque IDs

可让 server-side registry 为每个按钮映射 decision，但会增加 token、映射和生命周期复杂度。固定 action 名
已经能在不泄露请求数据的前提下表达用户选择，因此不采用。

### 17.3 Async callback then REST card update

会增加队列、失败重试和结果卡竞态，而且 callback 无法在同一响应中确定展示结果。SDK 已原生支持 response
card，因此不采用。

### 17.4 Text fallback or permanent dual path

文本 fallback 会让身份、生命周期和用户认知重新出现双轨，并违反 R27。发送失败只能 fail closed；真实
回调通过后删除文本路径。

## 18. Approval boundary

本文件只定义设计，不是实施计划。用户明确批准本 spec 后，下一步才调用
`superpowers:writing-plans`，生成独立的 S5a 实施计划。未获得该批准前不得修改产品或测试代码。
