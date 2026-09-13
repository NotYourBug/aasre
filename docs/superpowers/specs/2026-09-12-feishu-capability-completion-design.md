# 飞书通讯能力补全设计（Feishu Capability Completion）

- Date: 2026-09-12
- Status: Draft（待用户评审）
- 上游 spec: [`2026-08-30-feishu-comms-replacement-design.md`](2026-08-30-feishu-comms-replacement-design.md)
- Ledger: [`.superpowers/sdd/2026-08-30-feishu-comms-replacement/progress.md`](../../../.superpowers/sdd/2026-08-30-feishu-comms-replacement/progress.md)

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
> 因此 B/C 两层（卡片审批、反馈按钮、卡片流式更新）从「大工程」降级为「接线 + 卡片模板」。

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

- **单卡片体积 30KB** —— 超出即失败。这是流式长度的实际上限。
- **`sequence` 必须严格递增**（否则错误码 `300317`）；`uuid` 提供幂等重试。
- `content` 传**全量文本**而非增量 delta。新文本以旧文本为前缀才有打字机效果；前缀不同则全量直接上屏。
- 流式与独享卡片模式互斥（`update_multi` 不能为 `false`）。
- 频控：流式内容接口 **50 次/秒、1000 次/分钟**；**卡片组件追加/更新仅 10 QPS**。
- **客户端版本要求：飞书客户端 ≥ 7.20。** 低于该版本的客户端无法正确渲染 CardKit 流式卡片 ——
  这是环境前置，不是代码可绕过的（见 §7 与降级级 4）。

### 2.5 富文本渲染：`schema 2.0` + `tag: "markdown"`（2026-09-13 调研）

Slack Block Kit 的 markdown 原生渲染，在飞书的对应物是 **CardKit 2.0 的 `tag: "markdown"` 组件**。

**必须显式声明 `"schema": "2.0"`** —— 默认为 1.0，而 **1.0 不支持 GFM 表格**。

这解释了当前飞书链路的真实缺陷：现有实现走 `post`/`md` 消息类型（内容来自
`strip_html(slack_text)`，见 `integrations/feishu/scheduled_delivery.py`），而 `post`/`md` 会
**静默丢弃 GFM 表格**、**截断超过约 6 行的围栏代码块**。含表格的调查报告因此发生**内容丢失**，
不只是渲染难看。

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
- **R26（用户，2026-09-12）：推进顺序 S1→S2→S3→S4→S5→S6。** 地基先行 + 痛感排序。
  每个子项目各自走 spec → plan → 实现 → PR，不合并成一个大 PR。
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
  当前 `post`/`md` 路径会静默丢表格、截断代码块，属内容丢失，必须修。
- **R31（用户，2026-09-13）：飞书是项目唯一的通讯模块。** prompt 片段不是「照抄 slack 措辞」，
  而是以飞书为唯一渠道重新设计（R28 的定位同样源于此）。
- **R32（用户，2026-09-13）：流式超限的降级策略** —— CardKit 30KB 上限触发时结束流式，
  再另发完整消息，保证不丢内容（见 §4 S3 的退出标准）。

## 4. 子项目分解

| # | 子项目 | 范围 | 前置 | 退出标准 |
| --- | --- | --- | --- | --- |
| **S1** | 飞书集成一等公民化（地基） | catalog 接入 + 凭据三层 | 无 | `setup/verify/list feishu` 可用；`.env`-only 部署行为不变 |
| **S2** | 感知层：附件与图片 | 下载+内联、图片 vision、停止丢弃非文本 | 无（需飞书 `im:resource` 权限） | 发截图/日志文件，agent 能看到内容 |
| **S3** | **卡片基建层（新地基）** | CardKit 2.0 卡片构建 + `schema 2.0` 的 `tag:"markdown"` 渲染 + CardKit v1 流式 + 五级降级 | 无 | 任意长文本可流式渲染成卡片；超 30KB **不丢内容** |
| **S4** | 对话输出接线 | 把 turn 输出接到 S3：流式卡片 + 长消息分片 + outbound 回复线程 | S3 | 长调查可见进度；超长回答不截断 |
| **S5** | 卡片交互（审批 + 反馈） | 审批按钮**取代**文本审批；流式结束后追加 ✅采纳/🔄重试 按钮；👀 已读；reaction 事件作降级捷径 | S3、S4；**需控制台订阅 `card.action.trigger` 与 `im.message.reaction.*`** | 按钮审批可用；群聊旁观者无法误触；reaction 可作捷径 |
| **S6** | 告警与投递卡片化 | watchdog 告警、报告/定时投递改走 S3 的渲染层 | S3 | 告警/报告呈现为卡片且**表格不丢** |
| **S7** | prompt 片段 | 以「飞书是唯一通讯模块」为前提重写 persona / action / gather / assistant 提示 + `message_context` 渠道前缀 | 无 | agent 在飞书里具备渠道自觉 |
| **S8** | agent 工具面 | `integrations/feishu/tools/`：`feishu_send_message` 可照抄；读消息/搜消息/列成员/加反应需逐一平台调研 | S1 | agent 能主动发飞书消息 |

**依赖**：S4 / S5 / S6 都依赖 S3 的卡片基建（新增的地基）；S8 依赖 S1 的凭据层；
S2、S7 完全独立。

**S3 的五级降级**（R32）：

| 级 | 触发 | 动作 |
| --- | --- | --- |
| 0 | 正常 | 卡片 `streaming_mode=true` + markdown 元素，节流 PUT 全量文本，`sequence` 单调递增 |
| 1 | 卡片体积逼近 30KB | 关闭流式，卡面保留已输出部分并标注截断 |
| 2 | 有剩余内容 | **另发**消息承载剩余（按尺寸分片），不再更新原卡 |
| 3 | 流式错误码（`200850` 流式超时 / `300309` 流式已关 / `300317` sequence 乱序） | 退出流式 → 一次性完整消息 |
| 4 | 客户端不支持卡片 | 退回现有纯文本路径 |

**每一级都不丢内容** —— 最差退化回今天的行为（一条纯文本长消息），不会出现「编辑到一半卡住、
用户拿不到答案」。

**建议顺序**：S1 → S2 → S3 → S4 → S5 → S6。S7 / S8 无依赖，可随时插入；
**S7 成本低、价值高，建议提前**（R31 已明确飞书是唯一通讯模块）。

## 5. 明确排除（YAGNI）

用户按实际工作流验收，以下缺口**不做**（记录在案以免后续误认为遗漏）：

| 排除项 | 来源 | 理由 |
| --- | --- | --- |
| 频道欢迎语 | slack `channel_intro.py` | 无工作流触及 |
| slash 命令注册（`/investigate`） | discord `slash_command.py` | 飞书文本命令已覆盖 |
| silo guild allowlist | discord `DISCORD_SILO_GUILD_IDS` | 飞书 chat 模型无对应概念 |
| HTTP events 接收 + 签名校验 | slack `transport/events_api/` | WS 长连接已满足；设计 §3 明确无公网端点 |
| `getChat` 目标解析 / `getMe` 验证 | telegram | S1 的 verifier 覆盖验证；目标解析飞书侧用 `receive_id` |
| thread + 线程历史回填 | slack / discord | 飞书会话键为 `chat_id:open_id`，无 thread 概念；非当前工作流 |
| DM / 群区分与 open-workspace 模式 | slack / discord | 飞书 `chat_id`(群) 与 `open_id`(人) 已天然区分 |
| ~~消息反应（reactions）~~ | slack / discord | **已移出排除项** —— R28 裁决纳入范围，见 §2.6 与 S5 |

## 6. S1 详细设计

### 6.1 架构与边界

飞书当前不是 catalog 集成（`integrations/registry.py` 无 `feishu` 条目），凭据仅读环境变量。
S1 把它接入既有 catalog 契约，**不新造机制** —— 完全镜像 telegram/slack/buzz 的三件套
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
| `integrations/feishu/credentials.py` | 改 | 三层解析：secret 走 `resolve_env_credential`（env→凭据文件）再进 store/keyring；chat 目标 `override → store → env`。**保持无 lark** |
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
| **单卡片 30KB 硬上限** | 高 | S3 五级降级保证不丢内容（§4）；超限时结束流式并另发消息 |
| **卡片组件更新仅 10 QPS**（远紧于流式的 50/s） | 中 | 按钮组件只在流式**结束后**一次性追加，不边流边追加 |
| **飞书客户端需 ≥ 7.20** 才能渲染 CardKit 流式卡片 | 中 | 环境前置，代码不可绕过；需写入用户文档，并由降级级 4 覆盖旧客户端 |
| `post`/`md` 静默丢表格、截断代码块 | 高 | §2.5 已定性为**内容丢失**（非渲染问题）；S6 必须修，否则报告表格持续丢失 |
| 「生成中收到用户 reaction」的状态机 | 中 | R28 已定：reaction 仅作降级捷径；主入口是流式结束后的卡片按钮，天然规避该边缘态 |
| `sequence` 乱序（`300317`）/ 流式超时（`200850`） | 中 | 每卡片维护单调递增计数器 + `uuid` 幂等；错误码走降级级 3 |
| 卡片路径需控制台手工配置（订阅回调 + 重新发布） | 中 | **代码侧不可自动化**；S5 开工前需用户先完成配置，否则按钮点击报 `200340` |
| S5 另需订阅 `im.message.reaction.created_v1` / `deleted_v1` 并申请两个权限 | 中 | 同上；权限缺失时**收不到表情事件**（静默无反馈） |
| S2 需飞书权限 `im:resource` | 中 | 同上，需控制台授权 |

## 8. 后续

本 spec 落地后，S1 走 `writing-plans` 出实现计划。S2–S8 各自在本 spec 的框架下单独 brainstorm
与 spec（每个子项目的实现细节尚未设计，本 spec 只固定其范围、前置与退出标准）。

**S3 是新的关键路径** —— S4/S5/S6 都挂在它上面，且它承载了三项 2026-09-13 调研的硬约束
（CardKit 流式 30KB 上限、`schema 2.0` 才支持表格、组件更新 10 QPS）。建议 S3 单独走一次
brainstorm，把卡片 JSON 结构与降级阶梯定死，再往下推。

**S1 不受本次修正影响** —— 它是纯 catalog/凭据工作，与卡片、流式、渲染、prompt 零交叉，
可照现有计划先行推进。
