# 飞书通讯能力补全设计（Feishu Capability Completion）

- Date: 2026-09-12（2026-09-18 修订后续路线）
- Status: Revision under review；S1–S4 已交付，S5a–S8b 待实施
- 上游 spec: [`2026-08-30-feishu-comms-replacement-design.md`](2026-08-30-feishu-comms-replacement-design.md)
- Ledger: `.superpowers/sdd/2026-08-30-feishu-comms-replacement/progress.md` — a local-only working
  artifact (gitignored, so not rendered as a link), holding the full R-decision history this spec
  continues from.
- 性质：**工程面设计产物，不是用户文档。** `.superpowers/` 不在 `docs/docs.json` 的站点导航里，
  Mintlify 不会渲染它 —— 因此本文刻意保留 vendor 端点、SDK 方法名、内部模块路径与调研结论，这些
  正是剩余飞书子项目实现时要照着做的东西。同目录的
  [`2026-08-30-feishu-comms-replacement-design.md`](2026-08-30-feishu-comms-replacement-design.md)
  是同一体例的既有先例（已在 main 上）。用户可见的说明随各子项目落地另写。

## 1. 背景与目标

上游 spec §2 的 Phase 3 是「删除 telegram / slack / discord / buzz 四条旧聊天传输」。Phase 3 的
盘点已完成（见 ledger「Phase 3 盘点完成 → 搁置」一节），但**删除执行被用户搁置**。

搁置原因（用户裁决）：经对比发现飞书仍存在大量能力不足；若将其它通讯 IM 模块全部删去仅保留
飞书，会失去大量功能。期望是 **feishu 模块能够独揽大任**。

**本 spec 的目标因此反转**：不是删除旧通讯，而是把飞书补全到能独立承担四条传输职责的水平。
只有当飞书覆盖了实际工作流所需的全部能力后，才重新评估 Phase 3。

### 验收标准（用户裁决：按实际工作流，不按能力并集）

用户声明实际使用以下四类工作流，**只补会被这些工作流触及的缺口**：

1. 对话式调查 —— 发消息让 agent 调查并回复结果
2. 工具动作审批 —— 写操作 approve/deny
3. 发图 / 发文件 —— 把日志、截图、配置文件发给 agent
4. 告警与投递 —— watchdog 告警、定时 digest、调查报告落到飞书

因此本 spec **不追求**与四条传输的字节级 parity。明确排除项见 §5。

## 2. 关键调研结论（spike，2026-09-12）

对已安装的 `lark-oapi` 1.7.3 做了实测探针，结论改变了既有裁决的前提。

### 2.1 交互卡片、反应、卡片流式更新 —— 全部可用

| 能力 | SDK 证据 |
| --- | --- |
| 卡片按钮回调 | `EventDispatcherHandler.builder(...).register_p2_card_action_trigger` |
| 发送卡片 | `msg_type="interactive"` |
| 卡片构建 / 更新 | `lark_oapi.api.cardkit.v1.model`：`Card` / `CardBuilder`、`CreateCardRequest`、`BatchUpdateCardRequest`、`ContentCardElementRequest` |
| 卡片服务 | `lark_oapi.api.cardkit.service.CardkitService` / `V1` |
| 消息反应（双向） | `CreateMessageReactionRequest`（`reaction_type`）、`register_p2_im_message_reaction_created_v1` / `_deleted_v1` |

事件注册方法挂在 **builder** 上（`EventDispatcherHandler.builder(...)`，共 200 个 `register_*`），
不在 `build()` 后的 handler 上 —— 排查时勿被 `dir(handler)` 为空误导。

### 2.2 卡片回调可走长连接（无需公网端点）

飞书官方支持「使用长连接接收回调」：`card.action.trigger` 经既有 WebSocket 通道投递，
**不需要公网 HTTPS 端点**，且长连接方式**无需解密验签**（仅建连时鉴权）。

三条运行约束：

1. 须在开发者后台「事件与回调 → 回调配置」开启，并在**「已订阅的回调」**（**不是**「已订阅的
   事件」）中订阅 `card.action.trigger`；配置变更后须**重新发布应用版本**，否则点击报 `200340`。
2. 回调须 **3 秒内**响应，否则客户端提示交互错误。
3. 回调响应体可直接更新卡片本身（点击 approve → 卡片原地变为结果态）。

### 2.3 对既有裁决的影响

> **R25 推翻 R15 的前提。** R15（2026-09-01）裁决「Feishu v1 无卡片基建，审批 UX 采用文本回复
> （镜像 Buzz），交互卡片属"大一个量级"的范围」——该判断**从未经验证**，是当时的假设。
> spike 证明卡片基建完整可用，且回调不需要公网端点。
>
> 因此卡片基础设施本身可行；但审批授权、反馈记录和重新执行仍是不同状态机，按 R33 拆分交付，
> 不再把整个交互层视为单纯的「接线 + 卡片模板」。

### 2.4 CardKit v1 流式（2026-09-13 调研）

**禁止**用 `PATCH /im/v1/messages/{message_id}` 高频更新整条消息做伪流式 —— 那条路有隐性次数
上限（约 20–30 次后静默失败），且长回答会直接失败。必须走 CardKit v1「流式更新文本」。

| 步骤 | 端点 |
| --- | --- |
| 建卡片实体（`streaming_mode: true`） | `POST /open-apis/cardkit/v1/cards` |
| 发卡片消息（`msg_type=interactive`，content 指向 `card_id`） | `im.message.create` |
| 流式更新文本 | `PUT /open-apis/cardkit/v1/cards/:card_id/elements/:element_id/content` |
| 追加组件（流式结束后放按钮） | `POST /open-apis/cardkit/v1/cards/:card_id/elements` |
| 关闭流式 / 改设置 | `settings` 调用 |

SDK 请求体已实测：

- `ContentCardElementRequestBody`：`uuid` / `content` / `sequence`
- `SettingsCardRequestBody`：`settings` / `uuid` / `sequence`
- `CreateCardRequestBody`：`type` / `data`
- `CreateCardElementRequest`：追加组件

**硬限制**：

- **单卡片体积** —— 平台对**两条路径各设一个上限**，单位还不一样（2026-09-16 实测）：
  建卡实体超过约 **150 KiB** 报 `200860`（"card over max size"）；流式元素更新超过
  **100,000 个字符**报 `99992402`。后者是**字符数**不是字节数 —— 10 万个 CJK 字符
  （30 万字节）通过，100,001 个 ASCII 字符失败。

  > 本节原先写的是"单卡片体积 30KB —— 超出即失败，这是流式长度的实际上限"。
  > 该数字在整条设计链里被反复引用，但**从无测量或引用支撑**，实测后被推翻
  > （低估约 5 倍）。S3 的实现预算据此定为 64 KiB，见 S3 设计 §7。
- **`sequence` 必须严格递增**（否则错误码 `300317`）；`uuid` 提供幂等重试。
- `content` 传**全量文本**而非增量 delta。新文本以旧文本为前缀才有打字机效果；前缀不同则全量直接上屏。
- 流式与独享卡片模式互斥（`update_multi` 不能为 `false`）。
- 频控：流式内容接口 **50 次/秒、1000 次/分钟**；**卡片组件追加/更新仅 10 QPS**。
- **客户端版本要求：飞书客户端 ≥ 7.20。** 低于该版本的客户端无法正确渲染 CardKit 流式卡片 ——
  这是环境前置，不是代码可绕过的。**服务端探测不到客户端版本**（事件里没有该字段），所以它不是
  一条可编程的降级分支，见 §4「不丢内容的边界」与 §7。

### 2.5 富文本渲染：`schema 2.0` + `tag: "markdown"`（2026-09-13 调研）

Slack Block Kit 的 markdown 原生渲染，在飞书的对应物是 **CardKit 2.0 的 `tag: "markdown"` 组件**。

**必须显式声明 `"schema": "2.0"`** —— 默认为 1.0，而 **1.0 不支持 GFM 表格**。

这解释了 S3/S4 前飞书链路的真实缺陷：当时实现只发 `msg_type="text"`，纯文本**不渲染任何标记**，
表格、围栏代码块、加粗、链接全部以原文显示，再叠加 `truncate(..., MAX_MESSAGE_SIZE=4096)` 的
截断，含表格的调查报告因此发生**内容丢失**，不只是渲染难看。S4 已把对话 turn 接到卡片与文本
分片；watchdog、调查报告和定时投递仍保留该缺陷，由 S6 收口。

> **2026-09-15 更正**：本节原写「实现走 `post`/`md` 消息类型」，与当时代码不符 ——
> 两个发送点均为 `"text"`。**结论（内容丢失的定性）不变，路径更正。**

2.0 `markdown` 组件支持：GFM 表格、带语言高亮的围栏代码块（python/go/sql/json…）、
`<br>`/`<hr>`/`<at>`/`<link>` 等。

组件约束：单个富文本组件最多 4 个表格；单卡片表格总数上限约 5（超出返回
`11310 card table number over limit`）；表格除表头外最多展示 5 行数据（超出分页）；组件需要
`element_id`（字母开头，仅字母/数字/下划线，≤20 字符）。

### 2.6 reactions 的两套系统（2026-09-13 调研）

必须区分两个**完全不同**的机制，不要混淆：

| | Reaction（消息表情） | 卡片内按钮组件 |
| --- | --- | --- |
| 事件通道 | `im.message.reaction.created_v1`（IM 事件） | `card.action.trigger`（卡片回调） |
| 可用选项 | 仅内置 emoji 枚举，不可自定义 | 完全自定义文案与图标 |
| 流式中可用 | ✅ 生成中途用户即可点 | ❌ 必须等流式结束后追加组件 |
| 频控 | 独立 IM 接口频控 | 卡片组件更新 10 QPS |

**关键约束**：

- 仅内置 emoji 枚举（`THUMBSUP` / `EYE` / `CROSS`），不支持租户自定义表情。
- 机器人**只能删除自己添加的**表情；用户打的表情删不掉，且事件**不返回 `reaction_id`**。
- 用户可在流式**进行中**点表情 → 事件先到，而 agent 尚未生成完 → 状态机必须处理「生成中收到反馈」。
- 权限：需 `获取单聊、群组消息` + `查看消息表情回复`，并订阅
  `im.message.reaction.created_v1` / `deleted_v1`；表情事件**同样走 WS 长连接**，无需公网回调。

## 3. 决策

延续上游 ledger 的 R 编号。

- **R24（用户，2026-09-12）：Phase 3 删除搁置。** 转向飞书能力补全。删除名单已盘点完成但未执行；
  待飞书能力补全后重新评估。
- **R25（spike，2026-09-12）：推翻 R15 的卡片可行性前提。** lark SDK 卡片、反应、卡片流式更新
  完整可用；卡片回调支持长连接、无需公网端点、无需验签。
- **R26（用户，2026-09-12，已被 R33 取代剩余路线）：推进顺序 S1→S2→S3→S4→S5→S6。**
  地基先行 + 痛感排序；每个子项目各自走 spec → plan → 实现 → PR，不合并成一个大 PR。
- **R27（用户，2026-09-12）：卡片审批完全取代文本审批。** 审批卡片化落地后移除 R15 的文本
  回复 approve/deny 路径，不保留共存分支。
- **R28（用户，2026-09-13）：reactions 纳入范围**，分两层用途：
  (a) **必做** —— 收到用户消息后立即在**用户原消息**上添加 `EYE`(👀) 表示已收到；best-effort，
  失败静默忽略、不阻塞主链路；
  (b) **辅助** —— 监听 `im.message.reaction.created_v1`，把流式卡片消息上的 `THUMBSUP`/`CROSS`
  映射到与卡片按钮相同的业务逻辑，**仅作捷径，不作唯一入口**。
  **正式反馈入口**是流式结束后用 CardKit 组件追加 API 在卡片末尾插入的按钮组件。
  不要用 reaction 承担多状态控制 —— 状态一多就全部交给卡片按钮。
- **R29（用户，2026-09-13）：流式必须用 CardKit v1，禁止消息 PATCH 伪流式。** 见 §2.4。
- **R30（用户，2026-09-13）：富文本走 `schema 2.0` + `tag: "markdown"`。** 见 §2.5。
  裁决时出站只发 `msg_type="text"`，**不渲染任何标记**（表格、代码块、链接全部以原文显示），
  再叠加 `truncate(..., 4096)` 的截断，属内容丢失。S4 已修复对话输出；主动投递留给 S6。
- **R31（用户，2026-09-13）：目标部署中飞书是唯一通讯模块。** prompt 片段不是「照抄 slack 措辞」，
  而是以飞书工作流重新设计（R28 的定位同样源于此）。这不等于把仓库的全局 prompt 硬编码成飞书；
  多 transport 代码仍存在时，具体渠道必须由每个 turn 的运行时上下文决定，见 R34。
- **R32（用户，2026-09-13；容量值于 2026-09-16 实测修正）：流式超限的降级策略** ——
  达到 **64 KiB 工程预算**或平台拒绝时结束流式，再用分页卡片承载剩余内容，保证不丢内容。
  平台实测硬限制是约 150 KiB 建卡体积与 100,000 字符流式元素更新；30KB 不是平台限制。
- **R33（用户，2026-09-18）：修订 S4 后的剩余路线。** 原 S5 拆成 S5a/S5b/S5c，原 S8 拆成
  S8a/S8b；顺序调整为 **路线图校正 → S5a → S6 → S5b → S5c → S8a → S7 → S8b**。
  S5a 先完成高风险审批授权迁移，S6 随即修复主动投递的既有内容丢失，再引入反馈与重试状态机。
  每个子项目继续独立走 brainstorm → spec → plan → 实现 → PR，不创建覆盖全部剩余范围的大计划。
- **R34（用户，2026-09-18）：S7 是运行时渠道感知，不是全局飞书化。** gateway 向 turn 注入当前
  `platform` / `chat_id` 与可见能力；prompt 只描述当前 surface 可用的通讯渠道和工具。Slack、Telegram、
  Rocket.Chat 等未删除代码不得因 S7 获得错误的飞书 persona 或工具指引。
- **R35（用户，2026-09-18）：反馈与重试分离。** 「采纳」是幂等反馈记录，不启动新 turn；
  「重试」与 reaction 快捷入口属于单独状态机，必须定义身份、生命周期、并发、计费、事件重放与
  会话轮换语义后才能落地。`deleted_v1` 不撤销已经执行的业务动作。

## 4. 子项目分解

| # | 子项目 | 范围 | 前置 | 退出标准 | 状态 |
| --- | --- | --- | --- | --- | --- |
| **S1** | 飞书集成一等公民化（地基） | catalog 接入 + 凭据三层 | 无 | `setup/verify/list feishu` 可用；`.env`-only 部署行为不变 | 已完成 |
| **S2** | 感知层：附件与图片 | 下载+内联、图片 vision、停止丢弃非文本 | 无（需飞书 `im:message` / `im:message:readonly` 权限；**不是** `im:resource`，那是上传接口） | 发截图/日志文件，agent 能看到内容 | 已完成 |
| **S3** | **卡片基建层（新地基）** | CardKit 2.0 卡片构建 + `schema 2.0` 的 `tag:"markdown"` 渲染 + CardKit v1 流式 + 分页降级 | 无 | 任意长文本可渲染成卡片；达到 64 KiB 工程预算或平台拒绝时分页承载，内容不丢失 | 已完成 |
| **S4** | 对话输出接线 | 把 turn 输出接到 S3：流式卡片 + 长消息分片 + outbound 回复线程 | S3 | 长调查可见进度；超长回答不截断；回复线程正确 | 已完成 |
| **S5a** | 卡片审批 | Approve/Deny 卡片按钮**取代**文本审批；迁移既有授权与生命周期语义 | S3、S4；控制台订阅 `card.action.trigger` 并重新发布应用 | 只有原请求者可决定；重复、过期、旁观者、关闭排空均安全；真实回调验收通过后删除文本审批，合并态不存在双轨 | 下一项 |
| **S6** | 告警与投递卡片化 | watchdog 告警、调查报告、定时投递改走 S3 渲染层 | S3 | 三条路径均以卡片保留 markdown/表格；超预算分页、卡片失败时兜底也不截断内容 | 待实施 |
| **S5b** | 已读与正式反馈 | 用户原消息上的 👀 生命周期；流式结束后追加 ✅采纳按钮；复用 transport-neutral feedback JSONL 存储 | S4、S5a | ack 失败不阻塞 turn；成功、失败、取消、超时均有确定终态；仅原请求者可采纳；按最终 `message_id` + 操作者幂等记录 `good`，不保存回答正文、不启动新 turn | 待实施 |
| **S5c** | 重试与 reaction 捷径 | 🔄重试按钮；`THUMBSUP`/`CROSS` reaction 映射；处理流式中、过期、重复与删除事件 | S5b；订阅 `im.message.reaction.created_v1` / `deleted_v1` 并具备相应权限 | 只有原请求者可触发；一次用户意图最多启动一个可计费 turn；流式中/旧卡/会话轮换行为确定；删除 reaction 不撤销动作 | 待实施 |
| **S8a** | Agent 发送/回复工具 | `integrations/feishu/tools/` 中提供主动发送与线程回复；复用凭据和 S4 回复语义 | S1、S4 | agent 可向获准目标发送或回复飞书消息；目标校验、审批、失败结果和内容分页均有契约测试 | 待实施 |
| **S7** | 运行时渠道感知 prompt | 基于当前 turn 的 platform/chat context 重写 persona / action / gather / assistant 片段；只暴露当前渠道能力 | S8a | 飞书 turn 使用飞书语义与工具；其他 transport 不出现飞书 persona 或不可用工具；本地 surface 行为不回退 | 待实施 |
| **S8b** | Agent 读取类工具 | 读消息、搜消息、列成员、加 reaction 逐项调研、逐项 spec/PR，不以工具集合打包 | S1、S7；加 reaction 复用 S5b/S5c 客户端能力 | 每项工具分别定义权限、分页、目标范围与跨聊天授权；只注册已完成且可验收的能力 | 待实施 |

**依赖与节奏**：S5a 只迁移审批；S6 不等待反馈状态机；S5b 不包含重试；S5c 才允许产生新 turn。
S8a 先提供 prompt 可引用的最小工具面，S7 再按运行时 surface 暴露它，S8b 的研究型能力逐项推进。
任何子项目都不得把后续项目作为“顺手补齐”并入同一个 PR。

**S5a/S5b/S5c 的回调身份边界（2026-09-13 评审新增，2026-09-18 拆分）**：
`card.action.trigger` 对审批或重试是**授权入口**，对采纳反馈是身份归属入口，不是单纯展示层。
R27 让卡片按钮取代文本审批，那就必须把文本审批的授权校验一并搬过来 —— 现有实现在 claim 时要求
「请求者的 `open_id` **且** 提示所在 `chat_id` 双匹配」（`gateway/transports/feishu/pending_approvals.py`
的 `claim`），
卡片路径此前在本文里**没有任何等价要求**，"群聊旁观者无法误触"就不成立：群里任何人都能替别人
批准一次写工具。S5a 必须带上：

- 回调带操作者 `open_id` 与 `chat_id`，与 pending 请求比对，**任一不符即拒绝**该次点击；
  拒绝时只回一条仅点击者可见的提示，**不改卡片状态**（否则旁观者能靠卡片变化探测审批是否存在）。
- 操作者仍须通过既有入站白名单 `FEISHU_ALLOWED_OPEN_IDS`。
- 回调体是**外部输入**：按钮 `value` 只放不透明的 `approval_id`，不放工具名、参数或审批内容；
  授权结论一律服务端按 `approval_id` 重新判定，绝不采信客户端回传的字段。
- 回调处理必须在飞书的 3 秒窗口内应答；耗时工作只能在授权 claim 成功后异步继续，不能让 callback
  handler 同步等待工具执行或新 turn 完成。
- S5a 的现场验收至少覆盖本人 approve、本人 deny、群聊旁观者、重复点击、过期点击和 shutdown drain；
  旁观者/过期/重复事件均不得改变卡片或 broker 状态。
- S5b 的采纳按钮同样匹配原请求者 `open_id` 与原 `chat_id`；记录只含时间、平台、操作者、chat、
  最终 `message_id` 和 `verdict="good"`，不得保存问题、回答或卡片正文。

同一条规则适用于 S5c 的重试按钮和 reaction 捷径（R28(b)）：事件的 `open_id` 同样要过这道
校验，不因为“只是个捷径”就放宽。所有 callback/reaction 事件按至少一次投递处理：重复事件返回
已有结果，不得再次批准工具或启动 turn。

**S3 的五级降级**（R32）：

| 级 | 触发 | 动作 |
| --- | --- | --- |
| 0 | 正常 | 卡片 `streaming_mode=true` + markdown 元素，节流 PUT 全量文本，`sequence` 单调递增 |
| 1 | 卡片体积达到 64 KiB 工程预算 | 关闭流式，卡面保留已输出部分并标注截断 |
| 2 | 有剩余内容 | **另发非流式卡片**承载剩余（按字节预算分页），不再更新原卡 |
| 3 | 流式错误码（`200850` 流式超时 / `300309` 流式已关 / `300317` sequence 乱序） | 退出流式 → 一次性完整**卡片**（超预算则按级 2 分页） |
| 4 | 客户端 < 7.20（**服务端不可探测**，见下） | 卡片渲染失败。不是一条可编程分支：事件不带客户端版本，服务端无从判断。兜底 = S4 的纯文本**分片**路径 |

**不丢内容的边界（2026-09-13 评审修正，2026-09-18 容量勘误）**：级 0–3 的“不丢内容”成立 ——
卡片承载全部文本；达到 64 KiB 工程预算时关闭流式、按字节预算**另发卡片**承载剩余；流式错误码
则退回一次性完整**卡片**。

> **2026-09-15 更正（级 2/3 的承载物）**：原写「另发**消息**（按尺寸分片）」与「一次性完整**消息**」。
> 按字面实现会**自相矛盾**：`msg_type="text"` 上限 4096，装不下一份 25KB 的回答，级 2/3 本身
> 即内容丢失；且溢出部分走纯文本会**丢掉全部 markdown/表格** —— 正是本文档定性为内容丢失的缺陷。
> 真正的文本分片属 **S4** 范围（不得塞进 S3）。故级 2/3 一律改为**卡片**承载，由 S3 提供分页原语。
> **分页规则（级 2/3 共用）**：按**字节预算**与**表格数**双维度切分 —— 工程预算 64 KiB，
> 且建卡实体必须低于约 150 KiB 平台硬限制、流式元素必须少于 100,000 字符；
> 单卡片表格总数 ≤5，超出即 `11310 card table number over limit` 硬失败，故只按字节判断是不够的。
> 切点必须落在 markdown 块边界，**围栏代码块与表格不得跨卡**。剩余内容渲染为后续**非流式卡片**，
> 逐张发消息承载；该分页原语由 S3 提供，S4 的长消息分片是独立的一条路径，不与之合并。

**级 4 原本写错了，不能算进这条保证。** 两点：

1. **它不是可探测的降级。** 飞书事件不携带客户端版本或能力字段（现有归一化事件里没有任何版本
   字段可读），服务端无法判断"对面是不是 7.20 以下"，因此不存在"检测到旧客户端 → 走级 4"这条
   分支。级 4 是**已记录的环境前置**：旧客户端拿到的就是一张渲染失败的卡片，只能靠升级客户端解决。
2. **S3 时的“退回纯文本路径”不等于不丢内容。** 当时路径是截断而非分片；因此级 4 的真实兜底
   被明确交给 **S4 的长消息分片**。S4 现已落地，对话 turn 的纯文本兜底可保持完整；主动投递的
   同类截断问题不属于级 4，由 S6 修复。

**→ S3 的退出标准“达到工程预算或平台拒绝时不丢内容”覆盖级 0–3；级 4 已由 S4 的纯文本
分片补齐。**

**当前顺序（R33）**：S1–S4 已完成；后续为 **S5a → S6 → S5b → S5c → S8a → S7 → S8b**。
不得再以“S7/S8 无依赖、可随时插入”为由提前全局改 prompt 或一次性铺开未调研工具。

## 5. 明确排除（YAGNI）

用户按实际工作流验收，以下缺口**不做**（记录在案以免后续误认为遗漏）：

| 排除项 | 来源 | 理由 |
| --- | --- | --- |
| 频道欢迎语 | slack `channel_intro.py` | 无工作流触及 |
| slash 命令注册（`/investigate`） | discord `slash_command.py` | 飞书文本命令已覆盖 |
| silo guild allowlist | discord `DISCORD_SILO_GUILD_IDS` | 飞书 chat 模型无对应概念 |
| HTTP events 接收 + 签名校验 | slack `transport/events_api/` | WS 长连接已满足；设计 §3 明确无公网端点 |
| `getChat` 目标解析 / `getMe` 验证 | telegram | S1 的 verifier 覆盖验证；目标解析飞书侧用 `receive_id` |
| thread 历史回填 | slack / discord | S4 已实现 outbound 回复线程；把既有 thread 历史回填进 session 仍非当前工作流 |
| DM / 群区分与 open-workspace 模式 | slack / discord | 飞书 `chat_id`(群) 与 `open_id`(人) 已天然区分 |
| ~~消息反应（reactions）~~ | slack / discord | **已移出排除项** —— R28 裁决纳入范围，见 §2.6 与 S5b/S5c |

## 6. S1 历史详细设计（已完成）

### 6.1 架构与边界

本节保留 S1 实施前的设计基线，供追溯，不描述当前代码状态。S1 将飞书接入既有 catalog 契约，
**不新造机制** —— 完全镜像 telegram/slack/buzz 的三件套
（`setup.py` + `verifier.py` + `classify()`），凭据走 `store → env → keyring` 三层。

**不可违反的硬约束**：`integrations/feishu/__init__.py` 只能 re-export **无 lark** 的轻量符号。
`test_registering_every_adapter_pulls_no_vendor_transport` 钉死「注册通知适配器不得把 vendor
transport 拉进 boot path」。R1 已为此把凭据抽成 `credentials.py` leaf；S1 必须延续该结构。

### 6.2 组件

| 文件 | 动作 | 内容 |
| --- | --- | --- |
| `integrations/config_models.py` | 增 | `FeishuConfig(StrictConfigModel)`：`app_id`/`app_secret`/`receive_id`/`receive_id_type`/`allowed_open_ids` |
| `integrations/feishu/classify.py` | 新增 | `classify(credentials, record_id)` → `FeishuConfig` 视图（镜像 `slack/classify.py`） |
| `integrations/feishu/verifier.py` | 新增 | `@register_verifier("feishu")`，lark token 获取做探针；`ObtainAccessTokenException` → `failed`，app_secret 经 `redact_token` 脱敏（telegram 同款） |
| `integrations/feishu/setup.py` | 新增 | `FEISHU_SETUP: IntegrationSetupSpec`：`app_id`(必需)、`app_secret`(必需, secret)、`receive_id`/`receive_id_type`/`allowed_open_ids`(可选) |
| `integrations/feishu/credentials.py` | 改 | 三层解析，**store 永远先于 env**（与 §6.1/§6.3 及 telegram 等同序）：`app_id`/`receive_id`/`receive_id_type`/`allowed_open_ids` 走 `store → env`；`app_secret` 走 `store → resolve_env_credential`（env → 本地凭据文件 → keyring）。即 guided setup 写进 store 的值不会被陈旧的环境变量盖住；调用方另有 `override → store → env` 的目标覆盖。**保持无 lark** |
| `integrations/registry.py` | 增 | `IntegrationSpec(service="feishu", has_verifier=True, direct_effective=True)` + `setup_order`/`verify_order` 取当前表末之后的下一个可用编号（实现时读取既有条目确定，勿猜） |
| `integrations/catalog.py` | 增 | env gate `add("feishu", _env_is_set("FEISHU_APP_ID"))` |
| `integrations/_catalog_impl.py` | 增 | `_classify_feishu` 注册 + env record loader |
| `integrations/effective_models.py` | 增 | `feishu: EffectiveIntegrationEntry \| None = None` |
| `integrations/cli.py` | 增 | `_setup_feishu` + 映射表条目 |
| `surfaces/cli/wizard/_integration_configurators.py`、`configurators/chat_notifications.py` | 增 | `_configure_feishu` |
| `integrations/feishu/{delivery,alarms,background_adapter,scheduled_delivery}.py` | 改 | 凭据改从新三层入口取，不再直接读 `os.environ` |
| `gateway/transports/feishu/settings.py` | 改 | `load_feishu_gateway_settings` 目前只读 `FeishuGatewayEnv`（纯 env）。必须改为经共享凭据层解析 `app_id`/`app_secret`/`allowed_open_ids`，env 作兜底 |
| `tests/shared/test_integrations_api_border.py` | 改 | `gateway → integrations` 白名单加 `integrations.feishu.credentials`（`gateway/transports/telegram/turn_output.py` 已有同类先例） |

**YAGNI 裁剪**：不引入 `FEISHU_DEFAULT_CHAT_ID`（`FEISHU_CHAT_RECEIVE_ID` 已是该语义，R23 已定）；
不做多 bot、多租户。

### 6.3 数据流

```
配置期：
  opensre integrations setup feishu（或 wizard）
    → 写 integration store（secret 另写 keyring）
    → classify() 产出 FeishuConfig 视图
    → effective_models.feishu 填充
    → catalog 展示 / verify feishu 探针

运行期（gateway / watchdog / cron / RCA）：
  load_chat_credentials_from_env()
    → store → env → keyring（取第一个命中）
```

现有纯 `.env` 部署**零感知**：env 层仍命中，只是多了一条优先级更高的 store 层。

### 6.4 错误处理

- 凭据缺失 → 该 transport 跳过（`GatewayConfigurationError`），与现状一致
- `verify feishu` 失败 → 返回 `failed` + 脱敏原因；**app_secret 绝不进输出**（telegram 已踩过的坑：
  `requests` 会把含 token 的完整 URL 塞进异常串）
- keyring 不可用（headless VM，用户的 Rocky Linux 就属此类）→ **静默降级到 env，不抛**

### 6.5 测试（TDD，先 RED）

- 新增：`tests/integrations/test_feishu_classify.py`、`test_feishu_verifier.py`、`test_feishu_setup.py`
- 扩展：`tests/integrations/test_feishu_credentials.py` 三层解析用例（含 keyring 缺失降级）
- 更新 characterization：`test_catalog_silent_fallback_elimination.py`、`test_verify.py`、
  `test_configured_integration_services.py`、`tests/cli/test_smoke.py`
- 更新 border：`tests/shared/test_integrations_api_border.py`、`.importlinter.strict`
- **boot path 接口存在性测试**：导入 `integrations.feishu` 后断言 `lark_oapi` 不在 `sys.modules`

> 更新既有 characterization 断言是**预期内**的 —— 新增一个集成必然改变服务列表与验证顺序。
> 按「更新断言」处理，不得用 skip / 常量条件绕过（AGENTS.md footgun）。

### 6.6 退出标准

1. `opensre integrations setup feishu` 可交互配置并落库，**且配置后网关聊天传输无需 `.env` 即可启动**
   （即 `load_feishu_gateway_settings` 走共享凭据层；否则 setup 只是半个真话）
2. `opensre integrations verify feishu` 返回 passed/failed 且不泄漏 app_secret
3. `opensre integrations list` 出现 feishu
4. 现有 `.env`-only 部署行为不变（有回归证明）
5. 既有测试绿 + 新增测试绿

## 7. 风险

| 风险 | 等级 | 处置 |
| --- | --- | --- |
| 新增 catalog 集成触发大批 characterization 断言变更 | 中 | 预期内，逐条更新而非绕过；CI 是权威结果 |
| keyring 在 headless VM 不可用 | 低 | 静默降级到 env；已有设计 |
| boot path 被 lark SDK 污染 | 中 | 有 CI 契约 + 新增存在性测试双重钉死 |
| **卡片存在两种不同单位的硬限制**（建卡约 150 KiB；流式元素 100,000 字符） | 高 | S3 使用 64 KiB 工程预算并按字节/表格双维度分页；平台拒绝走级 3，禁止再以 30KB 作为平台事实 |
| **卡片组件更新仅 10 QPS**（远紧于流式的 50/s） | 中 | 按钮组件只在流式**结束后**一次性追加，不边流边追加 |
| **飞书客户端需 ≥ 7.20** 才能渲染 CardKit 流式卡片 | 中 | 环境前置，代码不可绕过，需写入用户文档。**服务端无法探测客户端版本**（事件无该字段），故级 4 不是可编程降级；旧客户端只是渲染失败，兜底依赖 S4 的纯文本分片（§4） |
| **S5a 卡片回调是授权入口，漏校验即越权** | **高** | 回调必须复刻文本审批的 `open_id` + `chat_id` 双匹配 + `FEISHU_ALLOWED_OPEN_IDS`（§4）；重复/过期回调不得改变状态；真实回调验收是删除文本审批的合并门禁 |
| **主动投递仍走纯文本截断路径** | 高 | S4 已解决对话输出，watchdog、报告、定时投递仍可能丢表格或截断；S6 提前到反馈功能之前并逐条验收三条路径 |
| **生成中、重复或旧卡上的重试/reaction** | 高 | S5c 单独设计状态机；一次用户意图最多产生一个 turn/计费，流式中不立即重启，旧卡与会话轮换必须有确定结果，`deleted_v1` 不做补偿动作 |
| `sequence` 乱序（`300317`）/ 流式超时（`200850`） | 中 | 每卡片维护单调递增计数器 + `uuid` 幂等；错误码走降级级 3 |
| 卡片路径需控制台手工配置（订阅回调 + 重新发布） | 中 | **代码侧不可自动化**；S5a 合并前须在测试 chat 完成按钮回调验收，否则不得删除文本审批 |
| S5c 另需订阅 `im.message.reaction.created_v1` / `deleted_v1` 并申请两个权限 | 中 | S5c 开工前单独预检；权限缺失时**收不到表情事件**（静默无反馈），不得影响 S5b 正式按钮入口 |
| S7 全局飞书化污染其他 transport | 高 | prompt 从当前 turn 的 platform/capability context 生成；非飞书 turn 不得出现飞书 persona、目标或工具名 |
| S8b 读取工具扩大跨聊天数据面 | 高 | 每项工具单独 spec/PR，明确 token 权限、分页、目标 allowlist 与跨聊天授权；未完成的工具不得注册或写入 prompt |
| S2 需飞书权限 `im:message` / `im:message:readonly`（**不是** `im:resource` —— 那是上传接口） | 中 | 同上，需控制台授权 |

## 8. 后续

S1–S4 已交付。本文只固定剩余子项目的边界、依赖、顺序与退出标准，**不作为一个跨项目实现计划**。

下一项是 **S5a 卡片审批**：先单独 brainstorm 并形成 S5a spec，经用户评审后再用 `writing-plans`
生成只覆盖 S5a 的实现计划。S5a 合并并完成 post-merge 验证后进入 S6；其余项目依 R33 顺序重复
spec → plan → 实现 → PR 闭环。任何阶段发现需要改变相邻项目接口，先回写本文并重新取得用户确认。

后续每个项目的现场验收除自身退出标准外，共用以下门禁：不提交 `.env` 或凭据；测试 chat 的外部发送
必须取得当轮授权；PR 必须满足 `CI.md §8` 的绿色检查与 Greptile 5/5；合并后继续观察 main CI、
CodeQL 与 release workflow。外部控制台配置属于验收前置，不能用 mock 成功替代。
