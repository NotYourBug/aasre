# 飞书 S6 告警与投递卡片化设计

- Date: 2026-09-19
- Status: Draft for user review; conversational design sections approved on 2026-09-19
- Parent roadmap: [`2026-09-12-feishu-capability-completion-design.md`](2026-09-12-feishu-capability-completion-design.md)
- Scope: S6 only

## 1. Purpose

S3 已提供 CardKit schema 2.0 Markdown 卡片、64 KiB 工程预算和按字节/表格分页；S4 已把对话 turn 接到这套能力。主动投递仍有三条旧路径没有迁移：watchdog 告警、调查报告和定时投递仍通过 `msg_type="text"` 发送，并在 4096 字符处截断。长报告可能丢失后半段，表格、代码块、标题和链接也无法正确渲染。

S6 将这些已经完整生成的主动投递正文统一接入非流式 CardKit 文档投递。成功标准不是“能够发送一张卡片”，而是三条路径都能保留规范 Markdown；超出单卡预算时自动分页；卡片创建或发送失败时以完整纯文本分片兜底，不再截断内容。

## 2. Goals

1. watchdog 告警、调查报告和定时投递统一复用 S3 的 `paginate()`、`render_card_spec()` 和 CardKit 客户端。
2. 主动投递使用非流式卡片；不引入 turn、回复线程或流式生命周期。
3. 定义渠道中立的规范 Markdown 内容契约，避免把 Slack mrkdwn 或 Telegram HTML 原样发给飞书。
4. 卡片发送失败时从失败页开始使用 4096 字符纯文本分片继续投递；优先保证内容完整，接受网络结果不确定时当前页可能重复。
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
- 维护仅在确认非空 `message_id` 后推进的页游标。
- 从失败页开始调用纯文本发送器分片兜底。
- 返回不可变 `FeishuDocumentDeliveryResult`。
- 记录不含正文、secret 或完整目标 ID 的降级/失败日志。

该模块不得导入 `gateway`、`tools` 或 scheduler 类型。它不是 streaming session，也不拥有业务重试。

### 6.2 `integrations/feishu/delivery.py`

保留底层 `post_feishu_message()` 作为纯文本发送原语。删除“报告截断”职责；旧 `send_feishu_report()` 调用者全部迁移后移除该兼容入口，不保留两个文档发送 API。

### 6.3 Thin adapters

- `integrations/feishu/alarms.py`：继续拥有 cooldown reserve/suppress 语义；正文交给文档投递。
- `integrations/feishu/reporting_adapter.py`：继续使用 chat app 凭据和 configured target；选择规范 Markdown 报告字段。
- `integrations/feishu/background_adapter.py`：继续使用 alert-push app 凭据；用专门的 Markdown 摘要构建器生成现有有界摘要，再交给文档投递。
- `integrations/feishu/scheduled_delivery.py`：继续解析 task override 与 configured fallback 的目标配对；正文直接作为规范 Markdown 投递。

### 6.4 Formatting ownership

- 调查报告 formatter 新增渠道中立 Markdown 输出，不从 Slack 文本反向转换。
- scheduler 固定文案改成规范 Markdown。
- Telegram scheduled adapter 继续做 Markdown→Telegram HTML。
- Slack scheduled adapter 显式做 Markdown→Slack mrkdwn。
- Rocket.Chat 与 Feishu 直接消费 Markdown；Discord 保持自身 embed 适配。
- watchdog 的飞书分支使用现有 Markdown 字段格式器，不再生成纯文本版本。

### 6.5 Import boundaries

`integrations.feishu.__init__` 不 re-export `document_delivery` 或其他会导入 `lark_oapi` 的符号。需要投递的适配器在实际调用点延迟导入，保持基础启动路径轻量。`infrastructure` 不导入 `integrations`；scheduler 继续通过既有 adapter bundle 反转依赖。

## 7. Data contracts

### 7.1 Document-delivery input

统一入口接收显式参数，而不是 gateway 或 scheduler 对象：

- `app_id`
- `app_secret`
- `receive_id`
- `receive_id_type`
- `markdown`

凭据或目标缺失、正文仅含空白时，在任何 SDK 调用前返回失败。

### 7.2 Delivery result

`FeishuDocumentDeliveryResult` 至少包含：

- `success: bool`
- `message_ids: tuple[str, ...]`
- `mode: cards | text_fallback`
- `error: str`

`error` 仅在最终投递失败时非空，并且必须脱敏。卡片失败但纯文本兜底全部成功时，`success=True`、`mode=text_fallback`、`error=""`；降级原因只进入安全服务端日志。

现有 scheduler 三元组使用 `message_ids[0]` 作为 `posted_message_id`。watchdog 只读取 `success`；报告 adapter 在凭据齐全时仍按注册表契约返回“已尝试”；后台通知仍返回 `sent` 或安全的 `failed: <reason>`。

### 7.3 Canonical report field

`ReportMessages` 增加渠道中立 `markdown_text`。报告节点把它写入明确的 `report_markdown` 状态字段；`slack_message`、现有 `report` 和 `telegram_html` 保持兼容。因为该字段跨 stage/runner 边界，必须同步更新相应 state model/slice，而不能依赖未声明的 dict key。

scheduler 调查类 builder 优先使用 `report_markdown`，旧 runner 结果没有该字段时回退到 `report`。固定 scheduler 文案直接产生 Markdown，所以 fan-out 的共同输入也是 Markdown。

## 8. Delivery algorithm

1. 校验输入。
2. `paginate(markdown)` 一次生成全部页面；每页用 `render_card_spec(page.text, streaming=False)`。
3. 创建一个绑定凭据的 `FeishuCardClient`。
4. 依序 create/send 每页；只有拿到非空 `message_id` 才把该 ID 加入结果并推进确认页游标。
5. 当前页 create/send 抛错或返回空 ID 时停止卡片循环，从当前页开始进入纯文本兜底。
6. 对当前页及后续每个 `CardPage.text` 依序做字符安全的 4096 字符分片，并用 `post_feishu_message()` 发送。重复的表头或代码围栏 wrapper 可以保留；它们保证语义完整，不得删除正文。
7. 所有兜底分片成功：返回成功的 `text_fallback` 结果，包含此前卡片 ID 和后续文本 ID。
8. 任一兜底分片失败：立即返回失败；不得把未发送正文标记为成功。

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

| Failure | Required outcome |
| --- | --- |
| missing credentials/target | no SDK call; failure |
| blank body | no SDK call; failure |
| pagination/build failure | full plaintext fallback when body exists |
| empty `card_id` or `message_id` | current page is unconfirmed; fallback from current page |
| create/send exception before any page | full document falls back to plaintext chunks |
| create/send exception after confirmed pages | confirmed cards remain; fallback begins at failed page |
| uncertain send actually reached Feishu | current page may repeat; no content is skipped |
| all plaintext chunks succeed | overall success, mode `text_fallback` |
| any plaintext chunk fails | overall failure; caller policy decides retry/cooldown |

scheduler 的外层 provider retry 可能在整次失败后重发已经确认的部分；这是既有 at-least-once 行为。S6 不新增跨尝试持久化游标，因此不得声称 exactly-once display。

## 11. Security and observability

允许记录：

- delivery mode
- page/chunk index 与总数
- `receive_id_type`
- 成功消息数量
- 安全错误类别

禁止记录：

- Markdown 或纯文本正文
- app secret、token、authorization header
- 完整 `receive_id`、card ID 或 message ID
- SDK 原始响应体

所有返回错误都经过 `redact_token()`，并优先暴露错误类型而非 vendor detail。报告、scheduler 和后台通知不得把异常详情发送到外部 chat。卡片失败降级成功时记录 warning；最终失败记录 warning，但不把正文附入日志。

## 12. Test strategy

遵循 TDD，每个独立失败类先写失败测试，再做最小实现。

### 12.1 Document delivery

- 短 Markdown 单卡成功，卡片为 schema 2.0 非流式 Markdown。
- 长文本、多个表格和围栏代码分页；各页满足字节/表格预算。
- 首卡创建失败时全文纯文本分片兜底。
- 首卡发送结果不确定时当前页可重复但正文完整。
- 中间页失败时保留前面确认卡片，从失败页开始兜底。
- 兜底分片中途失败时整体失败，游标不跨过失败分片。
- 空 card/message ID、缺凭据、缺目标和空正文均不误报成功。
- result 的模式、ID 顺序和首个 ID 契约固定。
- secret、正文和完整目标 ID 不出现在日志或错误中。

### 12.2 Adapter integration

- watchdog 成功、失败、降级成功和 cooldown 行为。
- 报告 adapter 使用 `markdown_text`，缺凭据时 skip，失败时仍返回 attempted。
- 后台 RCA 使用 alert-push 凭据和 Markdown 摘要。
- scheduled task 的显式 chat 覆盖 configured fallback，且 ID type 与目标成对。
- scheduled delivery 不再 strip/truncate，返回首个成功消息 ID。

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

真实环境不故意制造网络故障；卡片失败与纯文本兜底由确定性测试验收。若真实发送因配置或权限失败，不能用 mock 结果代替现场成功。

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
