# 飞书通讯替换设计（Feishu Comms Replacement）

- Date: 2026-08-30
- Status: Draft（待用户评审）
- 范围：本期只做飞书；钉钉挂起，后续决定

## 1. 背景与目标

当前 IM 通讯分两层，各有一组 provider：

| 层 | 契约位置 | 现有 provider |
|---|---|---|
| 聊天传输（交互式） | `gateway/transports/`（`TransportName` + `TRANSPORTS` + `start_*_worker`） | telegram, slack, discord, buzz |
| 告警投递（单向） | `infrastructure/delivery/notifications/delivery_transport.py` + `integrations/<vendor>/delivery.py` | telegram, slack, discord, rocketchat, whatsapp, twilio |

目标：用飞书替换上述全部，最终只保留飞书（钉钉后续决定）；watchdog 告警投递能力保留并迁到飞书。

硬约束（用户）：**任何集成删除前，必须与用户确认删除名单，不得自行删除。**

## 2. 顺序（已确认）

1. Phase 0 盘点：集成保/删清单 + 通讯契约细节确认
2. Phase 1 飞书竖切（聊天传输 + 告警投递各一条最小链路；旧通讯全部保留、并行运行）
3. Phase 2 剪纯叶子集成（无人依赖、零风险）
4. Phase 3 删旧通讯（飞书验证稳定后）
5. Phase 4 剪有耦合集成（hermes/github 类，每刀小 commit、CI 绿）
6. 钉钉：挂起

## 3. 飞书聊天传输设计

- 模式：**自建应用 + WebSocket 长连接**（官方 SDK `lark-oapi` 的 `ws.Client`）。
  理由：网关是守护进程、无公网 HTTPS 端点，长连接模式匹配现有 polling/connected 守护进程形态，无需暴露公网回调地址。
- 新增包 `gateway/transports/feishu/`：
  - `__init__.py` — 轻量包门面（import + `__all__`）
  - `settings.py` — 解析 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`；缺任一抛 `GatewayConfigurationError`
  - `startup.py` — `start_feishu_worker(logger, handler)` 返回 `(worker, settings)`
  - `turn_output.py` — 把 turn 输出渲染成飞书消息（文本为主，卡片可选）
- 改动（复用现有契约）：
  - `gateway/transports/names.py`：加 `FEISHU = "feishu"`
  - `gateway/transports/startup.py`：`TRANSPORTS` 加一行 `TransportRegistration(TransportName.FEISHU, start_feishu_worker, "…")`
- 事件：注册 `im.message.receive_v1`，解析文本 → 构建 turn input → `TurnCallback` 进入 agent 循环。
- **生命周期（不阻塞终端）**：遵循 Discord 的 `background.py` 模式——`start_feishu_worker` 返回一个后台 worker（`wait_until_ready()` + `stop()`），`ws.Client.start()` 由后台线程承载，跑在网关守护进程内，**不占用 CLI/终端前台**。`stop()` 负责断连并在超时预算内 join（与 `stop_transports` 的 `ShutdownBudget` 对齐）。
- **凭据**：WS 长连接模式**仅需 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`**。`verification_token`（webhook 回调校验）与 `encrypt_key`（可选事件加密）均**非必需**，默认不启用。

## 4. 飞书告警投递设计

- 模式：**独立的告警推送自建应用（机器人）**，走 `im.message.create`（`tenant_access_token`），与聊天应用分开、不复用同一机器人。
  理由：未来拓展性好（卡片/@人/多目标），且告警推送与交互聊天职责分离、凭据独立。
- 配置：
  - `ALERTPUSH_APP_ID` + `ALERTPUSH_APP_SECRET`（告警推送应用凭据）
  - `FEISHU_ALARM_RECEIVE_ID` + `FEISHU_ALARM_RECEIVE_ID_TYPE`（`chat_id` 群 / `open_id` 单人）
- 前提：应用需 `im:message` 发送权限；目标 chat 需机器人入群（或目标用户可接收机器人消息）。
- 目标 ID 获取：机器人先入目标群 → 群里 @ 触发一条事件取 `chat_id`，或调 `im.chat.list` 查机器人所在群，得 `oc_xxx` 填入 `FEISHU_ALARM_RECEIVE_ID`。
- 新增 `integrations/feishu/delivery.py`，实现与现有 `delivery.py` 一致的接口，并在 `outbound_registry.py` 注册。
- 成功判定：响应 `code == 0`。

## 5. 安全（用户已知悉，收尾时处理）

`.env.example` 现含两组明文凭据（tracked 文件）：

- 聊天应用：`app_id` / `app_secret`（需改名 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`，符合命名规范）
- 告警推送应用：`ALERTPUSH_APP_ID` / `ALERTPUSH_APP_SECRET`

约定（用户确认）：开发测试阶段照用；项目完善后由用户统一迁移到 `.env`（gitignore）并轮换。代码一律从环境变量读取，不硬编码。

启动校验：凭据缺失 → 该 transport 跳过（not configured），与现有 transport 行为一致。

## 6. 错误处理

- 聊天：凭据缺失 → `GatewayConfigurationError`；WS 断线 → 自动重连（SDK 内置）。
- 投递：`DeliveryResponse` 归一化；`code != 0` 记失败日志，不向上抛。

## 7. 测试

- `settings.py` 解析（含缺失凭据）
- `turn_output.py` 渲染
- `delivery.py` payload 构建 + 成功/失败判定（fake 依赖，遵循 repo 命名规范）
- `TransportName` / `TRANSPORTS` 对齐契约测试（新增 feishu）
- 真实飞书沙箱收发（凭据到位后，可选）

## 8. 钉钉（后续）

挂起。飞书跑顺后，复用同一契约再加 `gateway/transports/dingtalk/` + `integrations/dingtalk/delivery.py`。

## 9. 集成剪枝（独立 spec，待 Phase 0 后）

本 spec 不覆盖；Phase 0 产出保/删清单后，再写独立设计并逐项与你确认。
