# 普通 Feishu 网关现场窗口守卫提案

状态：用户已明确批准临时监督器的零网络实现与验证，已实施并完成离线回归。真实窗口和合并仍未授权。不新增 S8b 能力，不修改真实凭据/授权、产品 worker 或模型选择。实际预算与命令见[当轮现场窗口](2026-10-02-feishu-gateway-live-window.md)，须另行批准。

## 已取得的证据

配置修复实现提交为 `759e056e6467c8dacda30ab076581d5ebffefd48`。零网络默认 bootstrap/resolver/leaf/settings 核对通过；原 env 与 unbound/org store 摘要不变。应用、群、唯一允许用户和组织均从原 `C:/Users/23033/Desktop/opensre2/.env` 及真实策略解析，输出仅包含匹配布尔值和数量。原 pidfile 是 `C:/Users/23033/.opensre/gateway/gateway.pid`，记录 PID 34844；2026-10-02 本机进程快照中该 PID 已退出，未检出 Python/OpenSRE/uv 网关、相关 Feishu 命令、Docker 或 WSL 消费者。这不能证明远端没有消费者；启动前须刷新本机快照，任何不确定消费者均停止准备。

普通 registry 会尝试 Telegram、Slack、Discord、Buzz、Feishu；本机真实 settings 仅 Feishu 配置有效。模型为 DeepSeek native SDK，三个档位均为 `deepseek-flash`，目标 `https://api.deepseek.com`，本地 API key 可用；尚未验证远端认证。不是 CLI 模型路径，代码检索没有发现该普通 turn 的 embedding API 路径。启动凭据 hydration、credits、Clerk mint、webapp vault、JWT remote-integration 路径均未配置；代理环境未设置。

完整监督器位于忽略的任务证据目录，39 项离线回归通过。用真实 OpenAI/httpx、requests/urllib3/Lark SDK 与明确的内存 transport 验证发送前计数、重试/redirect 停止、正常授权和 runner 连续调用、原 schemas/registry 不变、真实 shape 的 search/get 同一 ID、CardKit 来源、WS/ACK/心跳、启动清理竞态、deadline 和保护文件。synthetic 数据不作为现场成功证据。

一次较早回归退出时出现 Sentry 待发送事件提示；该次结果不作为零网络证据，未确认提示对应的事件是否发出。最终重跑在启动前关闭遥测，并保留覆盖进程退出阶段的外部 socket/DNS/子进程否决，结果为 39 passed、external attempts 0。完整真实配置预检安装所有 wrappers 后正常 GATEWAY_PROFILE boot（未启动服务）通过，拒绝 27 次写入尝试，stdlib 精确 socketpair IPC 1 次，env/store 摘要不变、live_enabled=false、当轮授权文件不存在。无模型、Feishu API 或外部消息调用。

## 原有阻塞与获批处理

仅控制 HTTP 次数不能控制模型 turn：两个不同的合法群消息各发一次模型请求，也都落在 6 次预算以内。

`gateway/transports/feishu/worker.py` 的 `_handle_event` 在构建 inbound 后直接 `_dispatch_turn`，注释依赖 mention-only，实际没有 mention gate。用户此前已开群历史读取权限，因此必须准备额外群事件可能进入派发的情况；未现场验证事件是否实际送达。`inbound_handler._run_turn` 正常授权后进入真实会话，`TurnRunner.run` 的 admission hook 只处理计量，不接受窗口身份/消息/次数参数。反馈重试和 reaction 还可经 prepared retry 进入同一 runner。

模型 OpenAI 客户端未取消 SDK 默认重试，外层也有 rate-limit 重试。Lark WS 默认自动重连且 reconnect count 可为无限；Feishu 同步 REST/endpoint 使用 requests，异步 REST 使用 httpx，WS frames 另走 websockets。只在高层工具调用或 httpx 计数，不能覆盖这些真实路径。

因此先按 Task 4 Step 3/4 提交了这份独立设计。用户随后明确批准“临时监督器的零网络实现与验证”。下述拒绝/停止行为已经在临时脚本落实；现场始终未启动。只以完整离线证明和具体窗口申请当轮现场授权，不沿用旧只读窗口。

## 建议的最小行为与实现位置

已按批准实施**本机、临时、仅验收用的透明监督器**的零网络实现与验证；保持 PR 产品实现不变。监督器放在被忽略的任务证据目录，不随产品发布。它只允许原流程继续或停止，不提供假 handler/model，不注入 resolver 记录，不改变 offered schemas，不过滤 TRANSPORTS，不改变配对/allow/revoke 算法。

1. 正常入口保持 `uv run opensre gateway start --foreground`。通过该子进程专用的 `sitecustomize` 安装临时 wrappers，记录启用的边界及原函数身份。原 env 路径不变；只设现有 `OPENSRE_GATEWAY_HOST_SCHEDULER=0`、`OPENSRE_SENTRY_DISABLED=1`、`OPENSRE_ANALYTICS_DISABLED=1`。没有新的产品 env 开关。未携带当轮明确授权记录时，监督器不得启动 CLI gateway。
2. 入站检查必须调用正常授权器并保留原决定；在继续会话、计量、模型和出站前额外核对固定群、固定操作者、预定消息文本和阶段。只接纳一次 `/help` 和一次带当轮唯一标记的模型消息。其他消息、附件、线程消息、按钮或 reaction 立即锁闭窗口并正常停止；不发送拒绝解释，也不启动另一个 turn。额外事件已由 SDK 收到的事实如实计数，不声称能撤销接收。
3. 在真实 `TurnRunner.run` 入口原子预留唯一模型 turn；正常授权、会话、计量和 runner 原样继续。prepared retry 等所有入口也必须经过这一总闸。若无法保持真实授权链或存在未覆盖入口，零网络验证失败，不进入现场。
4. 底层 HTTP 尝试在 httpx sync/async `_send_single_request` 和 urllib3 `_make_request` 实际发送前预留。覆盖 SDK 重试、redirect 和 token 请求，不把高层一次调用当成一次底层请求。任何失败、超限、超时或未知目标锁闭全局闸；用不会被 SDK `Exception` 重试吞掉的停止信号，并由监督线程调用该控制器正常 `stop()`。锁闭后所有后续请求拒绝。
5. 出站必须核对实体来源：创建仅来自正常 gateway help/回答输出；回复仅限本轮接纳消息的 ID；CardKit 更新仅限本轮实际成功创建的 card ID；目标只限固定群。所有实体 ID 从真实结果获得，在内存关联，不预造。Agent 主动 send/reply、reaction、删除、其他工具调用和未知 API 在执行/发送前拒绝并停止。保留正常 offered 工具集合；拒绝属于验收守卫，不能描述为产品权限行为。
6. WS 只允许一次 endpoint 请求返回的 HTTPS 来源关联的 `wss` 目标，随后只允许一次握手；第二次连接/endpoint 视为重连，立即停止。心跳、ACK 单独计数。不能只靠 `socket.connect` 计数 HTTP，因为连接池会复用 socket。未知 socket/DNS/子进程请求额外拒绝；精确允许 stdlib loopback socketpair IPC，保持 Windows 事件循环可用。
7. 监督器以 monotonic deadline 在 900 秒前锁闭请求。正常 stop 最多 30 秒；超时只处理本轮自建进程并记录未正常退出，不能清理其他进程。先锁闭网络再退出，避免 cleanup/retry 扩大预算。起止核对 env、已有策略文件摘要；允许的正常运行状态写入见下表，不删除聊天消息。

此方案增加的是已获批准的临时验收拒绝/停止行为，不作为产品能力交付。实施未修改产品 worker/dispatch 或增加持久化产品配置。真实窗口和合并仍须另行明确授权。

## 待完整守卫验证后提交的窗口草案

这张表是可审阅预算，**不是已获准的可执行窗口**。完整 wrappers 和生命周期测试通过、准确 PR HEAD 门禁通过后，必须重新提交实际启动命令、当轮消息和 guard 证据，再取得单独现场授权。

| 项目 | 拟定固定目标 / 上限 |
| --- | --- |
| 配置 | 原 env 的 `FEISHU_APP_ID`、`FEISHU_CHAT_RECEIVE_ID`、`ORGANIZATION_ID`；真实策略核对通过的唯一操作者；不输出 secret，不切换应用/群 |
| 时长 | 两阶段合计最多 15 分钟；停止清理最多 30 秒，超时报告失败 |
| 入站 | 一次 @bot `/help`，一次 @bot 完整唯一标记 prompt；额外用户事件首次出现即停；不点击反馈/重试/approval、不加 reaction |
| 模型 | 1 个 turn；DeepSeek / `deepseek-flash`；`POST https://api.deepseek.com/chat/completions` 总尝试最多 6；任何失败即停，不主动补发 turn |
| Feishu 初始请求 | `open.feishu.cn` tenant-token POST 最多 5，bot identity GET 最多 1，WS endpoint POST 最多 1，endpoint 返回关联 WS 握手最多 1；不重连 |
| Feishu 消息读取 | 该群过去 5 分钟历史 GET 最多 6，搜索发现的最新匹配 ID 单条 GET 最多 1；不重试业务读取，不读其他群/附件 |
| 正常出站 | 仅该群固定 help 文本和模型一句结果/其正常状态及流式卡片；消息/CardKit 写请求总尝试最多 60；不含 Agent 主动额外 send/reply、reaction 或删除 |
| WS frames | Lark ping 10，接收 pong/control ping 各 10，接纳 data event/ACK 各 2；协议 PING/PONG 各 50，CLOSE 1；额外已接收事件计数后拒绝。DNS 85、TCP connect 100，其中 WS TCP 1；redirect/reconnect 拒绝 |
| 计量 / vault / 其他 | 当前未启用，credits POST、Clerk mint、vault GET/写入、hydration、embedding、CLI 子进程、其他工具网络预算均为 0；若启动前配置改变，停止并刷新方案 |
| 本地 health | 0；普通服务仍绑定 0.0.0.0:8000，监督器拒绝全部 HTTP 入站，health 继续未验 |
| 正常状态写入 | 本轮 gateway PID/status/log、入站安全审计、会话和绑定、turn/反馈 manifest 等原流程状态；保留必要证据。原 env、已有 identity policy 和 integration store 不改 |
| 停止条件 | 任何配置/身份/HEAD 不一致、额外入站、错误响应/流错误、超预算、超时、未知请求/写入、第二消费者、重连即锁闭并停；已发生请求如实报告 |

模型 prompt 草案：`本轮标记为 FEISHU_CONFIG_VERIFY_<UTC秒>_<nonce>。请在当前聊天过去5分钟内搜索这个完整标记，读取最新匹配消息ID，然后仅用一句简短中文报告已找到并读取。不运行其他工具，不主动额外发送或回复消息。` 当轮授权后生成唯一实际文本；目前不要发送。

## 零网络验证的完成条件

- 真实 sync/async HTTP、requests/urllib3 重试、redirect、第 N+1 次、并发和 deadline 均在发送前拒绝；全局锁闭不可被 SDK 重试重新打开。
- 真实正常授权器和 runner 的正向路径未替换；另一条合法群消息、重试按钮、reaction、其他群/用户、重复相同消息、附件均在模型/计量/出站前停；不把 synthetic 回答当现场结果。
- 原 schemas/registry/composition 与无守卫时一致；允许消息继续调用原函数一次且传参不改。
- help/message/CardKit 实际 request shape、card/message ID 来源、WS endpoint/握手/heartbeat/ACK/pong、跨线程取消和 stop 全部覆盖；任何未覆盖路径继续阻塞现场。
- 本清单的离线守卫已实施并通过 39 项验证；真实普通 gateway 入口、授权/会话、实际 offered 模型调用和卡片回包须现场验证。Task 4 Step 4 的当轮批准及 Steps 5–8 仍未完成。

现场通过仍须真实普通入口 → 授权/会话 → 正常 offered search/get → 同一实际 ID → 正常卡片输出 → 清理的完整证据。仅一名参与者；跨操作者、分页、跨聊天和既有未验项目继续如实标注。现场通过或限制接受之后，再等待用户独立决定合并。
