# 飞书 S6 告警与投递卡片化设计

- Date: 2026-09-19
- Status: Revision under review; initial sections approved and review findings incorporated on 2026-09-19
- Parent roadmap: [`2026-09-12-feishu-capability-completion-design.md`](2026-09-12-feishu-capability-completion-design.md)
- Scope: S6 only

## 1. Purpose

S3 已提供 CardKit schema 2.0 Markdown 卡片、64 KiB 工程预算和按字节/表格分页；S4 已把对话 turn 接到这套能力。主动投递仍有三条旧路径没有迁移：watchdog 告警、调查报告和定时投递仍通过 `msg_type="text"` 发送，并在 4096 字符处截断。长报告可能丢失后半段，表格、代码块、标题和链接也无法正确渲染。

S6 将这些已经完整生成的主动投递正文统一接入非流式 CardKit 文档投递。成功标准不是“能够发送一张卡片”，而是三条路径都能保留规范 Markdown；超出单卡预算时自动分页；卡片创建或发送失败时以完整纯文本分片兜底，不再截断内容。

## 2. Goals

1. watchdog 告警、调查报告和定时投递统一复用 S3 的 `paginate()`、`render_card_spec()` 和 CardKit 客户端。
2. 主动投递使用非流式卡片；不引入 turn、回复线程或流式生命周期。
3. 定义渠道中立的规范 Markdown 内容契约，避免把 Slack mrkdwn 或 Telegram HTML 原样发给飞书。
4. 卡片发送失败时从失败页的原文起点开始使用 4096 Unicode code-point 纯文本分片继续投递；优先保证内容完整，接受网络结果不确定时当前页可能重复。
5. 保持每条调用链现有的凭据、目标、冷却、重试和返回值语义。
6. 保持 `integrations`、`infrastructure`、`tools` 和 `gateway` 的既有导入边界以及轻量包 facade。
7. 通过聚焦测试、完整本地质量门、真实飞书测试 chat 验收和 PR/CI/Greptile/post-merge 闭环交付。

## 3. Non-goals

S6 不实现：

- 👀 已读、✅采纳反馈、🔄重试或 reaction；这些属于 S5b/S5c。
- Agent 主动发送/回复、读取、搜索、成员或 reaction 工具；这些属于 S8a/S8b。
- 运行时渠道感知 prompt；这属于 S7。
- 对话 turn 的流式卡片或纯文本兜底；S4 已拥有该路径。
- 其他 transport 的长消息能力重构。只做为规范 Markdown 生产契约所需的格式适配回归。
- 内部重试队列、持久化 outbox、跨进程幂等或送达回执。
- 为卡片增加按钮、反馈组件、reaction 或装饰性分页正文。
- 改变后台 RCA 完成通知的“有界摘要”产品语义；它会改用 Markdown 卡片，但上游摘要预算仍保留。

## 4. Confirmed decisions

### 4.1 Content completeness over exact-once display

卡片投递的网络结果可能不确定。若第 N 页发送抛出异常，服务端无法可靠判断飞书是否已经接收该页。S6 选择从第 N 页开始做纯文本兜底：极端情况下第 N 页会重复，但后续内容不会丢失。不得为了避免重复而跳过结果不确定的页面。

### 4.2 Canonical Markdown at the producer boundary

scheduler 和调查报告新增规范 Markdown 表达；各 transport 在自己的适配器内转换最终格式。拒绝在飞书适配器中做通用 HTML→Markdown 猜测，也拒绝同时维护两套独立业务正文。

### 4.3 One shared Feishu document-delivery owner

分页、CardKit 发送游标、纯文本兜底和安全结果归一化只在一个 integration-local 模块中实现。watchdog、报告和 scheduler 适配器只负责各自的策略与参数解析，不复制分页循环。

### 4.4 Overall status is independent of message IDs

一次投递具有唯一整体状态：`SUCCESS`、`DEGRADED_SUCCESS`、`FAILED` 或 `SKIPPED`。已经确认的 message ID 只是诊断和持久化附属信息；即使前页已有 ID，只要后续正文没有全部确认，整体仍是 `FAILED`。任何 adapter 都不得用“ID 非空”推导成功。

### 4.5 Errors are constructed from safe fields

对外错误、`DeliveryResult.error` 和普通日志只使用固定枚举与固定文案。原始异常、SDK `msg`、响应体和请求对象不得先进入结果或日志再依赖正则清洗；`redact_token()` 仅作为第二道防线。

### 4.6 One source-text cursor

分页结果同时描述渲染页和它拥有的原始 Markdown 范围。确认发送一页后，游标推进到该页的 `source_end`；失败时从该页的 `source_start`（等价于当前确认游标）切换纯文本。分页器补出的围栏、表头或其他 wrapper 只用于卡片渲染，永远不作为剩余原文。

### 4.7 Cooldown throttles attempts, not only successes

watchdog 继续沿用仓库现有的 attempt-based cooldown：通过预检并开始一次外部投递尝试后，无论最终成功、降级成功或失败，都保留 cooldown reservation，防止网络故障时每个采样周期形成重试风暴。缺配置、缺目标或空正文在 reservation 前返回 `SKIPPED`，不进入 cooldown。

## 5. Existing system

可复用资产：

- `integrations/feishu/card_document.py`：纯函数 CardKit schema 2.0 构建、64 KiB 字节预算、表格上限、围栏代码和表格安全分页。
- `integrations/feishu/card_client.py`：同步 CardKit 建卡和 `interactive` 消息发送，强制非空 `card_id` / `message_id`。
- `integrations/feishu/delivery.py`：现有 `msg_type="text"` 发送器，已包含 SDK 构建/请求异常边界和 secret 脱敏。
- `gateway/transports/feishu/card_stream.py`：S4 流式状态机。S6 只复用其下层纯 CardKit 原语，不复用这个 turn-scoped session。

当前缺陷：

- `FeishuAlarmDispatcher` 在发送前按 `MAX_MESSAGE_SIZE` 截断。
- `send_feishu_report()` 在发送前截断；调查报告和后台 RCA 通知都经过它。
- `FeishuScheduledDelivery` 先删除 HTML，再截断为单条文本。
- 报告仅暴露 `slack_text` 与 `telegram_html`；`slack_text` 含 `<url|label>` 等 Slack 专属语法。
- scheduler 固定文案混用 `<b>/<i>` 和 Markdown，生产者契约不明确。

## 6. Architecture and ownership

### 6.1 `integrations/feishu/document_delivery.py`

新增飞书一次性文档投递的唯一 owner，负责：

- 接收 app 凭据、`receive_id`、`receive_id_type` 和完整 Markdown。
- 调用 `paginate()` 生成有序非流式卡片页。
- 使用单个 `FeishuCardClient` 逐页 create/send。
- 维护仅在平台明确 success 且返回非空 `message_id` 后推进的原文游标。
- 从失败页的 `source_start` 开始对原始 Markdown 分片兜底。
- 把 vendor/transport 异常映射为固定错误类别，不保存原始异常文本。
- 返回不可变 `FeishuDocumentDeliveryResult`。
- 记录不含正文、secret 或完整目标 ID 的降级/失败日志。

该模块不得导入 `gateway`、`tools` 或 scheduler 类型。它不是 streaming session，也不拥有业务重试。

### 6.2 Safe SDK boundaries

`card_client.py` 和纯文本原语不得把 vendor 文本作为异常契约。非流式 CardKit 拒绝改用只携带稳定 `code` 与调用 stage 的类型化异常；异常文案固定，不含 SDK `msg`。现有 `FeishuStreamRejected` 继续保留流式错误码语义，但同样不再把 SDK `msg` 放进异常文本。调用方只能依据异常类型、stage 和 allowlist code 分类。

纯文本发送原语返回结构化的 `accepted`、`message_id` 和固定 `error_category`，不返回原始错误字符串，也不自行逐条记录请求失败。文档投递 owner 负责唯一总结日志及降级/最终失败 warning。这样 CardKit 与纯文本两条路径共享相同的安全错误边界，且测试不需要从异常字符串反解析 code。

### 6.3 `integrations/feishu/delivery.py`

保留底层 `post_feishu_message()` 作为纯文本发送原语。删除“报告截断”职责；旧 `send_feishu_report()` 调用者全部迁移后移除该兼容入口，不保留两个文档发送 API。

### 6.4 Thin adapters

- `integrations/feishu/alarms.py`：继续拥有 cooldown reserve/suppress 语义；正文交给文档投递。
- `integrations/feishu/reporting_adapter.py`：继续使用 chat app 凭据和 configured target；选择规范 Markdown 报告字段。
- `integrations/feishu/background_adapter.py`：继续使用 alert-push app 凭据；用专门的 Markdown 摘要构建器生成现有有界摘要，再交给文档投递。
- `integrations/feishu/scheduled_delivery.py`：继续解析 task override 与 configured fallback 的目标配对；正文直接作为规范 Markdown 投递。

### 6.5 Formatting ownership

- 调查报告 formatter 新增渠道中立 Markdown 输出，不从 Slack 文本反向转换。
- scheduler 固定文案改成规范 Markdown。
- Telegram scheduled adapter 继续做 Markdown→Telegram HTML。
- Slack scheduled adapter 显式做 Markdown→Slack mrkdwn。
- Rocket.Chat 与 Feishu 直接消费 Markdown；Discord 保持自身 embed 适配。
- watchdog 的飞书分支使用现有 Markdown 字段格式器，不再生成纯文本版本。

### 6.6 Import boundaries

`integrations.feishu.__init__` 不 re-export `document_delivery` 或其他会导入 `lark_oapi` 的符号。需要投递的适配器在实际调用点延迟导入，保持基础启动路径轻量。`infrastructure` 不导入 `integrations`；scheduler 继续通过既有 adapter bundle 反转依赖。

## 7. Data contracts

### 7.1 Document-delivery input

统一入口接收显式参数，而不是 gateway 或 scheduler 对象：

- `app_id`
- `app_secret`
- `receive_id`
- `receive_id_type`
- `markdown`

凭据或目标缺失、正文仅含空白时，在任何 SDK 调用前返回 `SKIPPED`。`SKIPPED` 不是成功，也不是已经尝试；缺配置使用 `CONFIGURATION`，空正文使用 `VALIDATION`，调用方只暴露对应固定文案。

### 7.2 Delivery result

定义 `FeishuDeliveryStatus`：

- `SUCCESS`：全部原文通过卡片确认送达。
- `DEGRADED_SUCCESS`：发生卡片失败，但全部剩余原文通过纯文本确认送达。
- `FAILED`：已经尝试投递，但存在未确认送达的原文。
- `SKIPPED`：预检未通过，未调用任何 SDK。

`FeishuDocumentDeliveryResult` 至少包含：

- `status: FeishuDeliveryStatus`
- `attempted: bool`
- `confirmed_message_ids: tuple[str, ...]`
- `delivery_mode: cards | text_fallback | none`
- `error_category: FeishuDeliveryErrorCategory | None`
- `error: str`，只允许固定安全文案

`first_message_id` 可以是从 `confirmed_message_ids` 派生的只读便利属性，但不能代表整体成功。`attempted=False` 当且仅当状态为 `SKIPPED`；其他状态均为 `True`。`FAILED` 结果可以包含前页已经确认的 ID，但调用方不得因此改变状态。

结果字段组合固定如下：

- `SUCCESS`：`delivery_mode=cards`、`error_category=None`、`error=""`。
- `DEGRADED_SUCCESS`：`delivery_mode=text_fallback`、`error=""`；`error_category` 可保留触发降级的安全类别，供结构化总结日志使用。
- `FAILED`：`delivery_mode` 表示终止时实际使用的 `cards` 或 `text_fallback`；`error_category` 是导致正文未确认的终止类别，`error` 是其固定安全文案。
- `SKIPPED`：`delivery_mode=none`、`error_category` 为 `CONFIGURATION` 或 `VALIDATION`、`error` 是对应固定预检文案，且确认 ID 为空。

adapter 映射固定如下：

- scheduler 只有在 `SUCCESS` / `DEGRADED_SUCCESS` 时返回 `(True, "", first_message_id)`；`FAILED` 返回 `(False, fixed_error, "")`，即使结果中已有确认 ID；`SKIPPED` 返回 `(False, fixed_preflight_error, "")`。
- watchdog 根据整体状态判断投递成功，不能读取 ID 推断。
- 报告 registry 的布尔返回只表达 attempted：`SKIPPED → False`，其余状态均为 `True`；成功与失败另由结果状态和日志表达。
- 后台通知只有 `SUCCESS` / `DEGRADED_SUCCESS` 返回 `sent`；其他状态返回固定安全文案。

### 7.3 Canonical report field

`ReportMessages` 增加渠道中立 `markdown_text`。报告节点把它写入明确的 `report_markdown` 状态字段；`slack_message`、现有 `report` 和 `telegram_html` 保持兼容。因为该字段跨 stage/runner 边界，必须同步更新相应 state model/slice，而不能依赖未声明的 dict key。

scheduler 调查类 builder 优先使用 `report_markdown`，旧 runner 结果没有该字段时回退到 `report`。固定 scheduler 文案直接产生 Markdown，所以 fan-out 的共同输入也是 Markdown。

### 7.4 Source ranges and cursor invariants

`CardPage` 增加 `source_start` / `source_end`，均为原始 Python 字符串的半开区间索引。分页结果必须满足：

- 第一页 `source_start == 0`，最后一页 `source_end == len(markdown)`。
- 相邻页 `previous.source_end == next.source_start`；范围不重叠、无空洞。
- `page.text` 可以含分页器补出的代码围栏、表头或其他渲染 wrapper；`markdown[page.source_start:page.source_end]` 才是该页拥有的原文。
- 原文游标只赋值为已经确认页的 `source_end`，不得按 `len(page.text)` 推进。
- 纯文本 chunk 同样携带原文 `source_start/source_end`；每次平台明确 success 后才推进。

围栏代码、超长表格、CJK 和非 BMP emoji 必须保持这些不变量。Python 字符串索引按 Unicode code point 工作，因此合法非 BMP 字符不会被拆成 UTF-16 surrogate halves；分片重组必须逐字符等于原始剩余文本。

## 8. Delivery algorithm

1. 在任何 cooldown reservation 或 SDK 调用前校验凭据、成对目标和非空正文；失败时返回 `SKIPPED`。
2. 对完整原始 Markdown 调用一次 `paginate(markdown)`，得到带原文范围的页面；每页用 `render_card_spec(page.text, streaming=False)`。分页或构建失败时，把整个原文从游标 `0` 送入纯文本兜底。
3. 创建一个绑定凭据的 `FeishuCardClient`，并把 `source_cursor` 初始化为 `0`。
4. 依序 create/send 每页。只有平台明确 success 且返回非空 `message_id` 时，才记录该 ID，并令 `source_cursor = page.source_end`。
5. create/send 明确拒绝、抛错、响应无法解析或返回空 ID 时，不推进游标；分类错误后立即停止卡片循环，并从 `markdown[source_cursor:]` 做纯文本兜底。对当前页而言，`source_cursor == page.source_start`。
6. 纯文本分片器把剩余原文切成最多 4096 个 Python Unicode code point 的连续、有序 `TextChunk`。它不得 `strip`、截断或使用会改变正文的旧发送路径；单个超长行按同一上限安全切分。
7. 依序发送 chunk。只有平台明确 success 且返回非空 `message_id` 时，才记录 ID 并把原文游标推进到该 chunk 的 `source_end`。
8. 任一 chunk 失败或结果不确定时立即返回 `FAILED`；保留此前确认的 message ID 和数量，但 adapter 不得据此报告成功。
9. 所有原文都已确认且从未进入兜底时返回 `SUCCESS`；进入过兜底且所有剩余原文均已确认时返回 `DEGRADED_SUCCESS`。

文档投递层不做 retry、sleep 或持久化。scheduler 现有最多三次 provider retry、watchdog cooldown 和报告的 opportunistic 调用保持原位。

## 9. Formatting behavior

### 9.1 Investigation reports

规范 Markdown renderer 使用 GFM 标题、`**bold**`、围栏代码、表格和 `[label](url)`。Slack 的 `<url|label>`、`<!channel>` 或 Telegram HTML 不得进入 `report_markdown`。现有脱敏、evidence 引用、provenance 和 masking/unmasking 语义必须与其他报告变体一致。

### 9.2 Scheduled messages

`tasks.py` 中由 OpenSRE 生成的安静期、回放、synthetic 和 custom fallback 文案改用 Markdown。来自 agent 或 investigation runner 的 Markdown 原样保留。不得在 Feishu adapter 中运行广义 HTML stripping 或 HTML→Markdown 猜测转换。

### 9.3 Watchdog alarms

Feishu 使用与 Rocket.Chat 等价的 Markdown 字段语义：标题强调、字段名强调、值使用安全 code span。现有 hostname、PID、command、threshold、runtime 和 started 字段均保留；command 中的反引号继续被中和，防止破坏 code span。

### 9.4 Background RCA completion

用专门的 Markdown builder 组合现有 `summary_sections()` 输出。S6 不扩大摘要预算，也不把 email formatter 变成聊天格式器。卡片/纯文本兜底只保证这个已生成摘要完整送达。

## 10. Failure and retry semantics

### 10.1 Error categories

`FeishuDeliveryErrorCategory` 是内部固定枚举，不能携带 vendor detail：

| Category | Meaning | Eligible for outer retry |
| --- | --- | ---: |
| `CONFIGURATION` | 凭据或成对目标缺失 | 否 |
| `AUTHORIZATION` | allowlist 中明确的认证/授权拒绝 | 否 |
| `VALIDATION` | allowlist 中明确的请求/正文校验拒绝 | 否 |
| `RATE_LIMIT` | allowlist 中明确的限流响应 | 是 |
| `DEFINITE_REJECTION` | 平台明确返回未接受且不属于上述类别 | 否 |
| `DELIVERY_UNCERTAIN` | 请求可能已到达平台，但客户端无法确认结果 | 是 |
| `TRANSPORT` | 明确发生在交付前或未写出请求的连接故障 | 是 |
| `INTERNAL` | 分页、构建或本地不变量失败 | 否 |

表中的“可重试”只供外层策略决策；文档投递层本身从不重试。错误码只通过显式 allowlist 映射；未知发送阶段异常默认 `DELIVERY_UNCERTAIN`，未知本地构建异常默认 `INTERNAL`，不得读取 SDK `msg` 来决定或展示类别。

每次可见消息发送还维护内部确定性：

- `CONFIRMED_SENT`：平台明确 success 且返回非空 message ID；这是唯一可推进游标的状态。
- `DEFINITELY_NOT_SENT`：预检/本地构建失败、尚未调用可见消息 API、平台明确拒绝，或 SDK 明确标识请求尚未写出。
- `MAYBE_SENT`：可见消息 API 调用中的 timeout、写出后的连接中断、响应解析失败或无法证明未写出的未知异常。

`create_card` 只创建不可见实体：即使其结果不确定，只要尚未调用 `send_card`，对应正文仍属于 `DEFINITELY_NOT_SENT`，可以安全从当前范围兜底；它绝不增加确认消息数。

### 10.2 Cursor and outcome rules

| Condition | Required outcome |
| --- | --- |
| missing credentials/paired target | no SDK call; `SKIPPED`, `attempted=False`, category `CONFIGURATION` |
| blank body | no SDK call; `SKIPPED`, `attempted=False`, category `VALIDATION` |
| pagination/build failure with nonblank body | fallback from original source index `0`; category `INTERNAL` |
| explicit card-message success with nonempty ID | count one confirmed message and advance to `page.source_end` |
| `create_card` success | do not count a message and do not advance source cursor |
| empty `card_id` or explicit API rejection | current page remains unconfirmed; classify definite error and fallback from its `source_start` |
| timeout, connection loss after write, or response parse failure | `DELIVERY_UNCERTAIN`; do not advance; current source range may be sent again |
| card failure after confirmed pages | keep confirmed IDs internally; fallback from the failed page's original `source_start` |
| all plaintext chunks succeed | `DEGRADED_SUCCESS`, mode `text_fallback` |
| any plaintext chunk fails or is uncertain | `FAILED`; preserve confirmed count but leave unconfirmed source cursor unchanged |

只有平台明确 success 才推进原文游标。结果不确定时允许当前原文范围重复，但不得跳过它。scheduler 的外层 provider retry 可能在整次 `FAILED` 后重发已经确认的部分；这是既有 at-least-once 行为，S6 不声称 exactly-once display。

### 10.3 Target and `receive_id_type` pairing

目标和类型必须来自同一个配置来源，不能交叉拼接：

| Task/config input | Resolution |
| --- | --- |
| nonblank `task.chat_id` | use `(task.chat_id, "chat_id")`; ignore task-param and configured fallback target/type |
| task params contain `receive_id` and explicit type | use that explicit pair |
| task params contain `receive_id` but no type | use `(task receive_id, "chat_id")`; do not inherit configured type |
| task params contain type but no `receive_id` | `SKIPPED`; do not pair it with configured target |
| no task-level target/type | use the configured credential object's target/type pair; its intrinsic default type may be `"chat_id"`, but no value may be inherited from task params |
| configured target is missing | `SKIPPED`, regardless of a configured type |
| configured target is present but the resolved configured type is blank | `SKIPPED`; the adapter never repairs it from task params |

空字符串先按缺失归一化。`task.chat_id` 在现有模型中没有独立 type 字段，因此其固定语义就是 `chat_id`；未来若模型同时增加显式 target/type，必须作为原子 pair 覆盖。配置模型可以在自身边界把缺省 type 规范化为 `chat_id`；该默认值属于 configured source 自身，不是 adapter 从另一来源交叉补齐。若配置边界没有产出完整 pair，adapter 必须 `SKIPPED`。

### 10.4 Watchdog cooldown outcome

cooldown 表达“本阈值近期已发起一次外部尝试”，不是“投递成功”：

| Delivery result | Keep/enter cooldown |
| --- | ---: |
| `SUCCESS` | 是 |
| `DEGRADED_SUCCESS` | 是 |
| `FAILED`, `attempted=True` | 是 |
| `SKIPPED`, `attempted=False` | 否 |

这与仓库现有 Telegram、Rocket.Chat、Buzz 和 Feishu 的故障抑制语义一致。若在 `FAILED` 后立即释放 reservation，持续网络故障会让每个 watchdog 采样周期都重新发送；因此失败仍保留 cooldown，但业务返回值保持失败，绝不把 cooldown 误当成成功。

## 11. Security and observability

安全边界采用字段 allowlist，而不是“先记录原始异常，再做清洗”：

- `DeliveryResult.error`、adapter 返回值和外部 chat 错误只来自固定类别到固定文案的映射。
- 禁止把 `str(exc)`、`repr(exc)`、`exc.args`、SDK `msg`、响应体、请求对象或 traceback 写入 result 或普通日志。
- 已知投递异常不得直接 `exc_info=True`。未知异常也只记录安全类别和 allowlist 元数据；只有能证明异常链不含正文、请求对象和 SDK 响应时，才可进入受控的服务端异常采集。
- `redact_token()` 只作为固定文案和 allowlist 之后的第二道防线，不能成为安全正确性的前提。

允许的结构化日志字段固定为：

- `status`
- `delivery_mode`
- `receive_id_type`
- `card_page_index` / `card_page_total`
- `fallback_chunk_index` / `fallback_chunk_total`
- `confirmed_message_count`
- `error_category`

index 从 1 开始。`confirmed_message_count` 只统计平台明确 success 的 `send_card` / `send_message`；`create_card` 成功不计数。禁止记录 Markdown/纯文本正文、secret/token/Authorization header、完整 `receive_id`、card ID、message ID、SDK 原始响应或 vendor detail。

每次文档投递只写一条总结日志。进入纯文本降级时最多额外写一条 warning，最终失败时最多额外写一条 warning；成功页/成功 chunk 不逐条写日志，避免噪声和扩大敏感信息暴露面。

## 12. Test strategy

遵循 TDD，每个独立失败类先写失败测试，再做最小实现。

### 12.1 Document delivery

- 短 Markdown 单卡成功，卡片为 schema 2.0 非流式 Markdown。
- 长文本、多个表格和围栏代码分页；各页满足字节/表格预算。
- `CardPage` 原文范围在跨页围栏、超长表格、CJK 和 emoji 输入上连续、无重叠、无空洞；按范围重组逐字符等于原文。
- 首卡创建失败时全文纯文本分片兜底。
- 首卡发送结果不确定时当前页可重复但正文完整。
- 中间页失败时保留前面确认卡片，从失败页的原始 `source_start` 开始兜底，不拼接渲染后的 `page.text`。
- 前页成功、后页切换兜底、第三个 fallback chunk 失败时，结果为 `FAILED`；结果可保留确认 ID，但 scheduler adapter 返回 false 和空 message ID。
- 纯文本分片按最多 4096 个 Python code point 切分，保持原始空白和顺序；单个超长行、CJK、emoji 均可无损重组，不走 `strip` / truncate 路径。
- 兜底分片中途失败或结果不确定时整体 `FAILED`，游标不跨过失败分片。
- 空 card/message ID、缺凭据、缺目标和空正文均不误报成功。
- 明确拒绝、timeout、响应解析失败、发送前 transport 故障和内部分页失败映射到规定错误类别；未知发送异常默认为 `DELIVERY_UNCERTAIN`。
- result 的整体状态、`attempted`、模式、确认 ID 顺序和附属首 ID 契约固定。
- 恶意异常同时包含 secret、正文、完整目标 ID、card ID、message ID 和 Authorization header；日志、result 和 adapter 返回均不含任一值。
- 日志只含 §11 allowlist 字段：index 从 1 开始，`create_card` 不增加确认数；每次投递恰有一条总结，降级转换和最终失败各至多一条 warning，成功页/chunk 无逐条日志。

### 12.2 Adapter integration

- watchdog cooldown 参数化契约：`SUCCESS`、`DEGRADED_SUCCESS` 和 `attempted=True` 的 `FAILED` 均保留 reservation；`SKIPPED` 不 reserve。失败仍向调用方报告失败，cooldown 不代表成功。
- 报告 adapter 使用 `markdown_text`，缺凭据时 skip，失败时仍返回 attempted。
- 后台 RCA 使用 alert-push 凭据和 Markdown 摘要。
- scheduled task 的目标/type 参数化覆盖 §10.3 完整矩阵，并断言不同来源绝不交叉拼接。
- scheduled delivery 不再 strip/truncate；只有整体 `SUCCESS` / `DEGRADED_SUCCESS` 才返回首个成功消息 ID，`FAILED` 即使已有确认 ID 也返回空 ID。
- 四种状态在 scheduler、watchdog、报告 registry 和后台 RCA adapter 的映射分别符合 §7.2，`attempted` 不承担成功语义。

### 12.3 Format regressions

- `report_markdown` 使用 GFM link/heading/code/table，不含 Slack link 或 Telegram HTML。
- Slack 报告、Slack blocks 和 Telegram HTML 的既有输出保持通过。
- scheduler 固定文案为 Markdown；Telegram/Slack/Rocket.Chat/Discord 适配测试保持通过。
- watchdog 其他 provider 的 HTML/Markdown 格式不回退。

### 12.4 Borders and quality gates

运行：

- Feishu card document/client/delivery/adapter 测试。
- watchdog、scheduler、reporting 和 state model 聚焦测试。
- integrations API border、import graph、boot-path 测试。
- `CI.md` 对触及模块要求的 Ruff lint、format-check、mypy 和 pytest。

行为保持型 formatter 重构先补 characterization test，在改动前确认通过，再保持为绿。

## 13. Live Feishu acceptance

真实测试 chat 发送必须在当轮取得用户授权。现场验收包括：

1. watchdog 告警：收到非流式 Markdown 卡片，字段完整且 cooldown 行为不变。
2. 调查报告：包含标题、链接、表格和围栏代码；没有 Slack 专属链接文本。
3. 定时投递：正文超过单卡工程预算，自动产生多张有序卡片，尾部内容存在。
4. 日志检查：不含 secret、正文和完整目标 ID。
5. 发送数量检查：只产生预先说明的测试消息，不进行无界重复发送。

后台 RCA 不再单独制造一次真实 incident：它与 watchdog 使用相同的 alert-push 凭据解析和同一个 Feishu 文档投递入口，因此 watchdog 真机测试验收共享的外部 transport；后台 RCA 特有的摘要格式和 adapter 状态映射由确定性测试覆盖。若实现中两者没有实际共享该入口，这一豁免失效，必须补做受控后台 RCA 真机投递。

真实环境不故意制造网络故障；卡片失败、结果不确定与纯文本兜底由确定性测试验收。若真实发送因配置或权限失败，不能用 mock 结果代替现场成功。

## 14. Documentation

更新现有飞书用户文档，只保留会改变操作者行为的信息：

- watchdog、报告和 scheduled delivery 现在使用卡片并自动分页。
- 卡片不可用时会退回完整纯文本分片，可能在网络结果不确定时重复当前页。
- CardKit 客户端版本前置条件沿用现有说明。

不写内部模块名、SDK endpoint、历史 bug 或测试实现。

## 15. Rejected alternatives

### 15.1 每个适配器单独分页

改动局部，但会复制发送游标、失败兜底和脱敏逻辑，三条路径很快产生不同语义，因此拒绝。

### 15.2 飞书本地 HTML→Markdown 转换

看似范围小，但无法可靠区分格式标签与日志/代码中的尖括号，并继续让 producer contract 模糊，因此拒绝。

### 15.3 双格式 scheduler payload

同时维护 HTML 与 Markdown 正文会扩大所有 scheduler adapter 接口并制造内容漂移。规范 Markdown 加 provider-local renderer 已足够，因此拒绝。

### 15.4 把 CardKit 投递下沉到 `infrastructure`

会让基础层依赖 vendor SDK 或引入反向导入，违反仓库分层；CardKit 必须留在 `integrations/feishu`。

### 15.5 复用 `CardStreamSession`

主动投递正文已经完整生成，不需要 throttle、sequence、close-stream 或 turn shutdown 状态。复用会把 gateway 生命周期带入无用户 producer，因此拒绝。

### 15.6 卡片失败即整体失败

实现简单但不满足“不截断、不丢内容”的 S6 退出标准，因此拒绝。

### 15.7 用已渲染页面反推剩余正文

分页器可能为了独立渲染补出代码围栏、表头或其他 wrapper。把 `CardPage.text` 拼成兜底正文会重复合成内容并改变语义，因此兜底只能切原始 Markdown 的 source range。

### 15.8 先记录原始异常再调用 `redact_token()`

原始异常可能携带正文、目标、卡片/消息 ID、Authorization header 或整个请求对象；正则不可能可靠识别所有业务字段。因此错误必须从固定枚举和 allowlist 元数据安全构造，token 脱敏仅作第二道防线。

### 15.9 用首个 message ID 代表整体成功

多页投递可能在已有确认 ID 后失败。ID 只说明至少一条消息已确认，不说明全部正文已确认；整体成功必须只由 `FeishuDeliveryStatus` 表达。

## 16. Delivery gates

1. 本 spec 获得用户明确批准。
2. 使用 `superpowers:writing-plans` 创建只覆盖 S6 的实施计划并再次评审。
3. 使用 TDD 实现，不并入 S5b/S5c/S7/S8。
4. 完成聚焦测试和完整本地质量门。
5. 取得当轮授权后在真实飞书测试 chat 完成三条路径验收。
6. 创建 PR，填写完整模板和 AI usage disclosure。
7. 每次 push 后监控 CI；失败时拉取日志、修根因、聚焦复测并重新 push。
8. 处理全部 actionable review，达到 Greptile 5/5 且零未解决会话。
9. 合并后监控 main CI、Synthetic、Interactive Shell、CodeQL、Release 等适用流程；条件 skip 如实记录。

## 17. Approval boundary

本文只定义 S6 设计，不是实施计划。用户评审并明确批准本 spec 后，下一步才调用 `superpowers:writing-plans` 生成独立实施计划。在此之前不得修改产品或测试代码。
