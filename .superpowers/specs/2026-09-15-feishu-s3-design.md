# 飞书 S3（卡片基建层）设计

> 上游：`2026-09-12-feishu-capability-completion-design.md`（R26/R29/R30/R32、§4 S3 行、五级降级表、§7）
> 本文档是 S3 的 brainstorm 产出，**决策已获用户确认**（2026-09-15，见 §8）
> 状态：**已确认** → 转 `writing-plans` 出实现计划

---

## 1. 范围与交付边界

**S3 交付 = 一个卡片库，不含接线。** 退出标准（spec §4）：

> 任意长文本可流式渲染成卡片；超 30KB **不丢内容**（覆盖级 0–3，级 4 由 S4 分片补齐）。

据此，S3 **不**做（属 S4/S5/S6）：

| 不做 | 归属 | 理由 |
| --- | --- | --- |
| 接进 turn 输出（`_FeishuChannel`） | S4 | S4 范围本就是"把 turn 输出接到 S3" |
| 纯文本长消息分片（4096 级） | S4 | 硬约束 9：**不要把分片偷偷塞进 S3** |
| 按钮组件、回调校验 | S5 | §7：组件追加仅在流式结束后，S3 只提供钩子位 |
| 告警/报告改走卡片 | S6 | S6 消费 S3 的渲染层 |

S3 的验收方式因此是**测试 + 一个一次性真机探针**（见 §7），不是端到端 turn。

---

## 2. 事实核验（推翻 spec 正文的两处陈述）

评审与 spec 的建议都要先跑一遍再采纳（S2 教训 3）。以下两条经**实测**，spec 正文有误。

### 2.1 spec §2.5 关于"当前实现走 `post`/`md`"是错的

全仓库 `msg_type(` 只有两个调用点，**都是 `"text"`**：

- `gateway/transports/feishu/turn_output.py:37`
- `integrations/feishu/delivery.py:47`

**不存在 `post`/`md` 路径。** 所以当前的真实缺陷不是"`post`/`md` 静默丢表格"，而是：

> **纯 `text`，完全不渲染任何标记** —— 无表格、无代码块、无加粗、无链接；
> 叠加 `truncate(..., 4096)` 截断。

**影响**：§7 风险行「`post`/`md` 静默丢表格、截断代码块 → S6 必须修」的**归因错了**。
S6 要做的不是"修 `post`/`md`"，而是"把 `msg_type` 从 `text` 换成卡片"。
结论（内容丢失的定性）不变，路径错了。**建议一并更正 spec**（见 §8 D7）。

### 2.2 `im:resource` 权限名错误（用户已裁决更正）

下载消息资源要求 `im:message` / `im:message:readonly`；`im:resource` 属**上传**接口。

### 2.3 全仓库零卡片代码

`cardkit` / `card_id` / `msg_type("interactive")` 在产品代码中**零命中**。
`gateway/transports/feishu/approvals.py:3` 的注释反而断言"Feishu has no interactive buttons" —— S3 落地后该注释与 `turn_output.py:71-81` 的 "No placeholder in v1 / No in-place edit in v1" 一并失效，需在 S4 清理。

---

## 3. SDK 表面（实测 `lark-oapi` 1.7.3，`uv.lock` 已钉）

### 3.1 同步 cardkit 资源存在，与仓库现有风格一致

| 调用 | 方法 |
| --- | --- |
| 建卡片实体 | `client.cardkit.v1.card.create(CreateCardRequest)` |
| 关流式 / 改设置 | `client.cardkit.v1.card.settings(SettingsCardRequest)` |
| 更新元素内容（流式打字机） | `client.cardkit.v1.card_element.content(ContentCardElementRequest)` |
| 追加组件（S5 按钮） | `client.cardkit.v1.card_element.create(CreateCardElementRequest)` |

全部 `coroutine=False`（`inspect.iscoroutinefunction` 为 `False`）。

**关键载荷事实**（决定了 JSON 结构怎么写）：

- 载荷字段**都是 JSON 字符串**：`CreateCardRequestBody.data` 是 `str`，`ContentCardElementRequestBody.content` 是 `str`，`SettingsCardRequestBody.settings` 是 `str`。要自己 `json.dumps`。
- 建卡 `type` 取值是 **`"card_json"`**（取自 SDK 自身 `FeishuChannel.create_card_instance`），**不是 `"card"`**。
- 发卡片消息：`msg_type="interactive"`，`content = json.dumps({"type":"card","data":{"card_id": <card_id>}})`。
- 关闭流式：`settings = json.dumps({"config": {"streaming_mode": false}})`，同样带 `sequence`。
- 路径参数走 builder：`ContentCardElementRequest.builder().card_id(...).element_id(...)`。

### 3.2 SDK 自带 `MarkdownStreamController`（异步）——**不复用，但抄它的卡片 spec**

`lark_oapi.channel.FeishuChannel` 提供了完整的卡片流式方法（`create_card_instance` /
`send_card_by_reference` / `update_card_element_content` / `finish_streaming_card` / `stream`），
**但全部是 `async def`**，且要构造整个 channel 框架（inbound pipeline / policy / safety /
dedup store / token store / identity）。**不采纳**（理由见 §4 D1）。

它仍有两条直接价值：

**(a) level 0 的已知良好卡片 spec**（`MarkdownStreamController._ensure_started`）：

```json
{
  "schema": "2.0",
  "config": { "streaming_mode": true, "summary": { "content": "" } },
  "body": { "elements": [
    { "tag": "markdown", "element_id": "stream_md", "content": "..." }
  ] }
}
```

这正是 §2.5 要求的 `schema 2.0` + `tag:"markdown"`，可直接照抄。

**(b) `merge_streaming_text(prev, chunk)` 是纯函数、无框架依赖、sync** —— 处理
**delta / accumulated / mixed** 三种生产者语义（当 chunk 以 prev 为前缀时取 chunk；
prev 以 chunk 为前缀时保留 prev；否则求最长重叠后缀再拼接）。**直接复用**，不重写。

### 3.3 SDK **没有**卡片层的 30KB/超限处理

`OversizeContext` 属于**文本**发送路径（`OutboundConfig.on_oversize`、`text_chunk_limit`），
**与卡片无关**。`MarkdownStreamController` 对体积没有任何判断。

> **→ 五级降级完全是我们自己的活。** level 0 由 SDK 示范，level 1/2/3 无先例可抄。

### 3.4 无法复用的部分

- **`Throttle` 不能复用**：内部用 `asyncio.get_running_loop()` + `loop.call_later`，是 async-bound。
  需自写 sync 版（~40 行），**策略照抄**：到 `min_ms` 或累积 `min_chars` 任一满足即 flush。
- **`sequence` / `uuid` 无 SDK helper**，由调用方维护。

---

## 4. 决策

### D1 · 用同步 cardkit 资源，**不用** `lark_oapi.channel`（推荐，需确认）

| | 同步 hand-rolled | `FeishuChannel`（async） |
| --- | --- | --- |
| 与仓库一致性 | ✅ 现有全部走 `lark.Client.builder()` 阻塞调用，gateway 是**线程**模型 | ❌ 需给 worker 线程引事件循环 |
| 依赖面 | 4 个请求对象 | ~50 个类型（inbound/policy/safety/dedup/token store） |
| 测试风格 | ✅ 现有 `test_turn_output.py` 就是 stub 一棵假 `lark.Client` 树 | ❌ 需 async 测试设施 |
| level 1/2/3 | 都要自己写 | **同样都要自己写**（§3.3） |
| `merge_streaming_text` | ✅ 仍可复用（纯函数） | ✅ |

**→ 同步。** 换 async 只买到 level 0，却要付整个框架 + 事件循环的代价。

### D2 · 级 2 用**卡片分页**，不用文本分片（推荐，需确认 —— 这是对 spec 的重新解释）

spec 级 2 原文是「另发消息承载剩余（**按尺寸分片**）」。但：

- 若"分片"指 **4096 文本分片**，则 S3 必须实现文本分片 → **违反硬约束 9**（那是 S4 的活）；
- 且分片后走 `msg_type="text"`，**溢出的部分丢掉全部 markdown/表格** —— 正是 §7 定性为
  **内容丢失**的那类缺陷。一张含 8 个表格的报告，前 30KB 有表格、溢出部分变纯文本，是荒谬的。

**→ 级 2 = 把剩余内容渲染成后续的「非流式卡片」，按同一字节预算分页。**
每张卡片承载约 30KB（远大于 4096），markdown 与表格**全程保留**，且只用 S3 自己的卡片原语，
**不引入任何文本分片逻辑**，与 S4 不重复。

数量上界：一张卡片 ≈ 30KB，递归分页直到内容放完。

### D3 · 级 3 = 一张完整的**非流式卡片**（不是纯文本消息）

spec 级 3 原文是「一次性完整消息」。同样地，`msg_type="text"` 只能放 4096，**装不下一份 25KB 的回答**。
若按字面实现，级 3 本身就是内容丢失。

**→ 级 3 = 放弃流式，把当前完整内容按 D2 的同一分页逻辑发成非流式卡片。**
级 2 与级 3 因此**共用同一个分页原语**（见 §5 的 `paginate`）。

### D4 · 模块划分

沿用 S2 的切分先例（纯解析进 `integrations/`，传输策略进 `gateway/`）：

| 模块 | Tier | 职责 | lark |
| --- | --- | --- | --- |
| `integrations/feishu/card_document.py` | 3 | **纯函数**：markdown → 卡片 spec；字节预算；分页；块边界切分；截断标记 | ❌ 不导入 |
| `integrations/feishu/card_client.py` | 3 | 同步 cardkit 调用（建卡 / 更新元素 / 关流式 / 发卡片消息）。**模块级导入 lark**，与 `delivery.py:15-16` 同构，故**不进门面** | 模块级 |
| `gateway/transports/feishu/card_stream.py` | 1 | 流式**会话**：节流、`sequence`、五级状态机；消费上面两者 | 经 client |

**为什么 sender 放 integrations 而不是 gateway**：S6 的告警/投递（`integrations/feishu/`）
也要发卡片，而 `integrations` **禁止 import `gateway`**（Tier 3 → Tier 1 是反向边）。
`delivery.py` 已经是"Tier 3 里建 lark client 发消息"的先例，`card_client.py` 与它同构。

**为什么会话放 gateway**：带可变状态的流式策略属传输层，与 S2 的
`gateway/transports/feishu/attachments.py`（传输策略）同构；S4 的接线也在 gateway。

> ⚠️ **CI 门禁 —— 两类名字，两种处理，别搞混**（S1/S2 都在这里丢过 CI）：
>
> - **不带 lark 的纯函数**（`card_document`：`CardPage` / `paginate` / `render_card_spec` /
>   `spec_bytes`）→ 经 `integrations/feishu/__init__.py` **门面 re-export**，消费者写
>   `from integrations.feishu import …`。走 API 模块**不产生内部依赖，allowlist 无需改动**。
> - **带 lark 的 client**（`card_client`）→ **绝不能进门面**。它和 `delivery.py:15-16` 一样在
>   **模块级**导入 lark；一旦门面 re-export，`import integrations.feishu` 就会把 vendor
>   transport 拉进 boot path，直接违反
>   `test_registering_every_adapter_pulls_no_vendor_transport`。
>   它必须**深度导入**，因此 `gateway` 要在
>   `tests/shared/test_integrations_api_border.py` 的 `_ALLOWED["gateway"]` 里**新增一条**
>   `integrations.feishu.card_client`。
>
> 该 allowlist **只收缩**，条目过时同样失败 —— 所以条目必须真实被用到。**深度导入被拦是预期行为，
> 漏配 allowlist 才是 bug。**

### D5 · 体积预算是**实测序列化字节**，不是字符数

30KB 是**字节**，且中文一字 3 字节。`json.dumps(..., ensure_ascii=False)`（SDK 的做法）
保留原始 UTF-8，所以中文字符串的字节数 ≈ 3×字符数。

**→ 预算量的是「实际要发出去的那串 JSON 的 UTF-8 字节数」**，不是文本长度：

```python
def _spec_bytes(spec: dict[str, Any]) -> int:
    return len(json.dumps(spec, ensure_ascii=False).encode("utf-8"))
```

常量 `FEISHU_CARD_MAX_BYTES = 150 * 1024`，可用预算留安全边际
（`FEISHU_CARD_BUDGET_BYTES = 64 * 1024`）。

> **已由 §7 探针定值（2026-09-16）。**原 D5 问题问的是"30KB 量的是内层 JSON 还是
> 外层信封" —— 实测发现**该问题的前提不成立**：平台并非只有一个 30KB 上限，而是
> 两条路径各一个，且单位不同。
>
> | 路径 | 实测上限 | 错误码 |
> | --- | --- | --- |
> | `create_card` 建卡实体 | 151,721 字节通过 / 155,817 失败 → **约 150 KiB**（卡片 JSON 字节数） | `200860` card over max size |
> | `update_element` 流式元素 | **恰好 100,000 个字符**（内容字符数，非字节） | `99992402` field validation failed |
>
> 元素路的字符上限意味着**字节预算不等于安全**：纯 ASCII 文档里 1 字符 = 1 字节，
> 预算取 100KB 就会正好顶到 10 万字符。64 KiB 对两条路都留足余量
> （建卡余 57%，ASCII 字符数余 35%，CJK 字符数余 78%）。

### D6 · 切分必须落在 markdown 块边界

在字节预算处**硬切**会切进 ``` 围栏代码块或表格中间 → 渲染破损。

**→ 在预算附近回退到最近的安全块边界**（围栏外的空行），并保证：
- 围栏代码块**不跨卡片**；
- 表格**不跨卡片**；
- 切点前若有未闭合围栏，回退到该围栏起点之前。

> **单块超过一张卡片时无边界可退**（一个 40KB 的代码块、一张 400 行的表）。
> 此时**按块内行边界切分并重新包裹**：代码块每片自行闭合 ``` 并在下一片用原
> info string 重开；表格每片重放表头与分隔行 —— 少了分隔行的续片根本不是表格。
> 行内容一字不丢、顺序不变，重复的只是围栏与表头这类**结构标记**。
> 若连一行都放不下（单行超预算），退回逐字节切分并如实记录。
>
> 同一原因，`safe_prefix` 对**装不下的结构化首块**返回**空前缀**：任何字面切点
> 都会让剩余部分留下未被配对的围栏。空前缀把调用方引向分页器（级 3），那里会
> 重新包裹。**这不是内容丢失**，是换一条能正确渲染的路径。

### D7 · 表格数量是**第二个**预算维度

§2.5 的硬约束：单富文本组件**最多 4 个表格**；单卡片表格总数上限约 **5**
（超出返回 `11310 card table number over limit`）。

> **字节没超、表格超了，一样硬失败。** 分页判定必须是
> `超字节 OR 超表数`，不能只看字节。

（"表格除表头外最多 5 行、超出分页"是飞书**自动**行为，不需我们处理。）

### D8 · `sequence` 与 `uuid`

- 每张卡片**独立**维护从 1 起的单调递增 `sequence`；**绝不复用**（复用即 `300317`）。
- 每次变更请求带 `uuid = uuid4()` 供幂等重试（**同一逻辑请求重试沿用同一 uuid**）。
- 级 3 的错误码（`300850` / `300309` / `300317`）触发后**不再对同一张卡片发任何请求**。

> **实测更正（实现期发现）**：`uuid` 只能覆盖**变更类**请求。SDK 的
> `ContentCardElementRequestBody` 与 `SettingsCardRequestBody` 有 `uuid` builder，
> 但 `CreateCardRequestBody` **只有 `type` / `data` 两个字段，没有 uuid** ——
> 建卡本身无法幂等。因此 `create_card` 重复执行会产生多张卡片，这一条由 S4 的
> 调用方按"建卡失败即放弃该次回复"处理，不在本层兜底。

### D9 · 节流

流式内容接口 50/s、1000/min；**组件追加/更新仅 10 QPS**。

- 流式期间**只**调 `card_element.content`，**绝不**调 `card_element.create`/`update`
  → 绕开 10 QPS（§7 的既定缓解：按钮只在流式**结束后**追加，属 S5）。
- 节流阈值复用已接好的 `settings.status_update_interval_seconds`（默认 1.5s），
  即 `min_ms = 1500`；叠加 `min_chars` 阈值，二者任一满足即 flush。**远低于 50/s 上限。**

---

## 5. 五级降级阶梯（可实现的判定与分支）

`paginate(text) -> list[CardPage]` 是级 2 与级 3 共用的**唯一**分页原语（D2/D3）。

| 级 | 触发条件（代码可判） | 动作 | 分支 |
| --- | --- | --- | --- |
| **0** | 正常 | 建卡（`streaming_mode=true` + `markdown` 元素）→ 发 `interactive` 消息 → 节流 PUT 全量文本，`sequence` 单调递增 | `_stream_loop()` |
| **1** | `_spec_bytes(spec) >= FEISHU_CARD_BUDGET_BYTES` | 在 D6 的块边界回退切点 → 最后一次 `content` 更新为 `text[:cut]` + 截断标记 → `settings{streaming_mode:false}` 关流式 | `_cut_at_budget()` |
| **2** | 级 1 后 `text[cut:]` 非空 | 对剩余内容 `paginate()` → 逐张建**非流式**卡片 → 各发一条 `interactive` 消息 | `_send_overflow()` |
| **3** | `content`/`settings` 返回 `200850` / `300309` / `300317` | **立即**退出流式 → 关闭当前卡片 → 把**完整文本**走 `paginate()` 发成非流式卡片（D3） | `_on_stream_error()` |
| **4** | 客户端 < 7.20 | **不是代码分支**。事件不带客户端版本，服务端无从判断 | 环境前置，写进用户文档 |

**"不丢内容"的边界（与 spec §4 一致）**：
- 级 0–3 覆盖：级 1 关流式保住已渲染部分，级 2 把剩余分到后续卡片，级 3 退回完整卡片。
  **三条路径都以 `paginate()` 收尾，文本一字不丢。**
- 级 4 **不在保证内**：旧客户端拿到的就是渲染失败的卡片，兜底是 S4 的纯文本分片；S4 落地前上限即今天的行为（截断）。

**级 4 特别说明**（沿用 spec §4）：服务端探测不到客户端版本，**不存在"检测到旧客户端 → 走级 4"这条分支**。
不得在代码里为它写任何探测。

---

## 6. 常量

新增于 `config/constants/feishu.py`（叶子，任何层可导入），并经
`config/constants/__init__.py` 门面 re-export（S2 教训：常量绕过门面被 Greptile 判 P2）。

| 常量 | 值 | 说明 |
| --- | --- | --- |
| `FEISHU_CARD_SCHEMA` | `"2.0"` | §2.5：1.0 不支持 GFM 表格 |
| `FEISHU_CARD_MAX_BYTES` | `150 * 1024` | 建卡实测硬上限（§7） |
| `FEISHU_CARD_BUDGET_BYTES` | `64 * 1024` | 实际预算，对两条实测上限都留足余量（§7） |
| `FEISHU_CARD_MAX_TABLES` | `5` | D7 |
| `FEISHU_STREAM_ELEMENT_ID` | `"stream_md"` | 与 SDK 一致，字母开头、≤20 字符 |
| `FEISHU_STREAM_ERROR_CODES` | `frozenset({200850, 300309, 300317})` | 级 3 判定 |
| `FEISHU_CARD_TRUNCATED_MARKER` | 见下 | 级 1 标注 |

级 1 的截断标注要**同时**满足：

- **老实**：不假装内容到此为止；
- **可操作**：告诉用户后面还有、在哪看。

用 `"…（内容较长，后续 N 部分见下方卡片）"`，不再使用 `truncate` 的裸 `"…"`。

---

## 7. 验证策略

**单测**（无需网络，stub 假 `lark.Client` 树，沿用 `test_turn_output.py` 的 `_stub_lark` 手法）：

- `paginate()`：超字节分页；超表数分页（D7）；围栏/表格不跨页（D6）；空文本；**恰好等于预算**的边界。
- 分页**不丢内容**：`"".join(pages) == original` —— 这是退出标准的直接编码，必须是 property 式断言。
- 级 1 → 2 衔接：切断处文本完整、标记存在、剩余非空时确有后续卡片。
- 级 3：三个错误码各自触发退出流式；退出后**不再**对原卡片发请求（D8）。
- `sequence` 单调：单独断言每张卡片各自递增。
- 字节预算在**中文**下正确（3 字节/字），不是按 `len(str)`。

**真机边界探针**（一次性脚本，不入库 —— **已跑完**，2026-09-16）：
对 `create_card` 与 `update_element` 各爬一遍尺寸阶梯，定出两条路径的真实上限，
据此定 `FEISHU_CARD_BUDGET_BYTES`。结论见 D5。

探针只印尺寸与错误码，凭据从环境读；跑完即删，`git log -- scripts/` 为空。

> **尚未验证的一项**：探针只能证明**服务端不拒绝**，证明不了飞书**客户端**渲染
> 64 KiB 卡片时不卡顿、不截断。合并前需在真实客户端里肉眼看一次长回复。
> 若渲染有问题，`FEISHU_CARD_BUDGET_BYTES` 是回到 28KB 的唯一开关。

> ⚠️ 探针脚本**不得共用工作树**（S2 教训 1：两个探针互相还原文件，产出了假"通过"）。
> 串行执行，并在开始前断言工作树完整。

---

## 8. 决策记录（2026-09-15 用户裁决）

| # | 决策 | 裁决 | 影响面 |
| --- | --- | --- | --- |
| **D1** | 同步 hand-rolled vs `FeishuChannel` async | ✅ **同步 hand-rolled** | 整层依赖面与测试风格 |
| **D2** | 级 2 = 卡片分页（**重新解释 spec 字面**） | ✅ **卡片分页** | 退出标准是否真的"不丢内容" |
| **D3** | 级 3 = 非流式卡片而非纯文本（**同上**） | ✅ **非流式卡片** | 同上 |
| **D10** | 一并更正 spec §2.5 / §7 的 `post`/`md` 归因与 `im:resource` 权限名 | ✅ **一并更正** | spec 正文准确性 |

**D2/D3 是对 spec 字面的重新解释，已获用户裁决，须在 spec 中追记**（否则后续切片按字面读会
重新引入内容丢失）。追记内容：

- spec §4 五级降级表级 2 的「另发消息承载剩余（按尺寸分片）」→ 改为「另发**非流式卡片**承载剩余，
  按字节预算分页」；
- 级 3 的「一次性完整消息」→ 改为「一次性完整**卡片**（超预算则分页）」；
- 理由：`msg_type="text"` 上限 4096，按字面实现则级 2/3 本身即内容丢失，且溢出部分丢失全部
  markdown/表格；真做文本分片又违反硬约束 9（那是 S4 的分片，不与 S3 重复）。

**D10 已执行**：`im:resource` → `im:message` / `im:message:readonly`（§4 S2 行、§7）；
`post`/`md` 归因 → `msg_type="text"`（§2.5、§7）。见同日 commit。

---

## 9. 下一步

1. ~~用户确认 §8~~ → **已完成（2026-09-15）**
2. **`writing-plans` 出 S3 实现计划**（写入 `.superpowers/plans/`，该目录 gitignored）
3. SDD 派发实现；每个 Task 独立 subagent，Task 之间派 reviewer

**已完成的随附改动**：spec 更正（`im:resource` 权限名 + `post`/`md` 归因 + 级 2/3 承载物追记），
commit `d45ffc9`，分支 `docs/feishu-spec-corrections`，PR 待开。

**实现计划必须覆盖的门禁**（S1/S2 都在此丢过 CI）：

- `gateway` → `integrations.feishu.{card_document,card_client}` 的导入**必须经门面 re-export**
  并加进 `tests/shared/api_border.py` 的 allowlist；AST 扫描**连函数内导入也拦**。
- 常量经 `config/constants/__init__.py` 门面 re-export（S2 的 Greptile P2）。
- 动 `integrations/feishu/` 导入后跑两条边界检查，**都要 `PYTHONUTF8=1`**
  （少了它 `check_imports.py` 会因 GBK 解码框线字符而假阳性）。
