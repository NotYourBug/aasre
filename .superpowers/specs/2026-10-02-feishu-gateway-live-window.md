# 普通 Feishu 网关当轮现场窗口

**状态：等待单独现场授权；尚未启动。** 临时监督器的零网络实现已获批准。此文件供审阅实际目标、副作用、预算及停止条件，不包含合并授权。配置产品提交为 `759e056e6467c8dacda30ab076581d5ebffefd48`；文档更新后的准确 PR HEAD 和配置指纹须在授权前重新核对。

## 固定目标与已知限制

- 工作树 `C:/Users/23033/Desktop/opensre2/.worktrees/feishu-s5b-feedback`，分支 `codex/feishu-config-gateway-acceptance`，PR [#46](https://github.com/NotYourBug/aasre/pull/46)。
- 使用原 `C:/Users/23033/Desktop/opensre2/.env` 的 `FEISHU_APP_ID`、`FEISHU_CHAT_RECEIVE_ID` 和 `ORGANIZATION_ID`，以及真实策略/环境核对通过的唯一允许用户。应用、群、用户、组织固定为该次预检解析值；不改凭据、store 或授权。具体值留在本机，不提交标识和密钥。HEAD、这些文件及临时脚本/依赖的摘要绑定在本机 readiness 指纹中；任何变化即重新预检。
- 实际 provider 为 DeepSeek native SDK，reasoning/classification/toolcall 三档均为 `deepseek-flash`，目标 `https://api.deepseek.com/chat/completions`。不切换 `.env` 模型。key 本地存在，远端认证及该模型名称的可用性尚未验证；错误即停。
- 正常 registry 保留 Telegram、Slack、Discord、Buzz、Feishu；本机仅 Feishu 配置有效。scheduler、Sentry、analytics 用已有 knob 在本轮子进程禁用。hydration、credits、Clerk mint、vault、JWT remote integration、embedding 与模型 CLI 子进程预算为 0，启动前配置发生变化即停止。
- 原 pidfile 为 `C:/Users/23033/.opensre/gateway/gateway.pid`，当前记录 PID 34844，最近本机快照该进程不存在、8000 无监听，未发现其他本地 gateway/Feishu、Docker/WSL 消费者。启动前刷新快照；任何存活或不确定消费者、占用端口均停止，不清理他人进程。快照不能证明远端没有消费者。
- worker 的 mention-only 注释与应用已有群历史权限不一致。额外消息可能已被 SDK 接收；监督器仅能在派发/模型/出站前拒绝并停止，不能撤销接收。此行为未现场验证，不修改 worker。
- 普通 Web 服务仍按原配置监听 `0.0.0.0:8000`，监督器拒绝该 app 的全部 HTTP 入站并停止。本窗口 health 预算 **0**，health 项继续记录为未验；未改绑定地址或 transport registry。

## 可执行预算

所有上限包含 SDK 重试及底层实际尝试，不承诺成功次数。未知请求和超额请求在发送前拒绝；已经发出的请求无法撤回。无需第二名参与者。

| 类别 | 固定目标与上限 |
| --- | --- |
| 时间 | 授权记录建立至关闭最多 900 秒，含启动与两阶段操作；全局锁闭后清理最多 30 秒 |
| 入站与 turn | 仅该群/该用户一次 `/help`，成功回复后一次完整标记 prompt；模型 turn 最多 1；重复 ID、额外消息、附件、线程、按钮、reaction 首次出现即停 |
| 模型 | DeepSeek 同一目标、同一模型，总 HTTP 尝试最多 6；包括正常 agent 内部模型调用与 SDK 尝试；错误、超时或第二个 turn 即停 |
| Feishu 初始化 | `open.feishu.cn` tenant-token POST 最多 5，bot identity GET 最多 1，WS endpoint POST 最多 1 |
| WS 连接 | 仅 endpoint 实际返回的 `.feishu.cn` WSS URL；TCP 连接最多 1、握手最多 1；redirect、重连和第二次连接拒绝 |
| 搜索与读取 | 正常 offered `feishu_search_messages` 最多 1 次、`feishu_get_message` 最多 1 次；固定群过去不超过 300 秒历史 GET 最多 6，搜索最新匹配且等于本轮 prompt 的真实 ID 单条 GET 最多 1 |
| 正常出站 | 该群固定 help 文本及正常模型回答、状态和流式卡片；消息/CardKit 写请求总尝试最多 60；回复仅限本轮入站 ID，更新仅限本轮成功创建的 card/message ID |
| Lark 应用帧 | 发 ping 最多 10；接收 pong、control ping 各最多 10；接纳 data event 最多 2、关联 ACK 最多 2；额外已接收帧如实计数后拒绝，不发送拒绝说明 |
| WS 底层帧 | 协议 PING、PONG 各最多 50；清理 CLOSE 最多 1；binary 仅限上述实际 SDK 帧，不允许其他数据/分片 |
| DNS / TCP | 为上述 HTTP 和一次 WSS 连接服务的 DNS 调用最多 85、TCP connect 尝试总数最多 100（含 WS 的 1 次）；固定主机/解析 IP/当前请求授权必须匹配；stdlib 精确 loopback socketpair IPC 单独记录 |
| 其他 | health、credits POST、Clerk mint、vault、hydration、其他工具/模型/平台网络、子进程、权限修改、部署、删除消息均为 0 |

HTTP 连接/连接池等待上限 5 秒，读/写等待上限 10 秒；urllib3 连接 5 秒、读取 10 秒。监督器的 deadline 独立于请求超时；关闭后不新增 HTTP/模型/应用帧，仅允许一次协议 CLOSE 做清理。原生 OS 对已发数据的重传不能撤销，不把网络包数当成 API 请求数。

## 两阶段操作与出站内容

启动前核对准确 HEAD、readiness 指纹、原 env/store 摘要、模型和本机进程/端口。临时 `runtime_authorization.json` 当前不存在；只有取得这一次现场批准后，主代理才为准确 HEAD/指纹建立一次性记录，设置不超过 900 秒的到期时间和当轮唯一 marker/prompt。它不随 PR 提交。

正常入口保持 `uv run opensre gateway start --foreground`。子进程仅设置原 env 路径、三个已有禁用 knob、专用 `PYTHONPATH` 与 `UV_OFFLINE=1`；后者仅阻止 uv 安装联网，不改变 Python 应用网络预算。进入临时 sitecustomize 的授权及零网络核对后，原 CLI composition、原授权器、真实会话、模型、offered schemas 和输出链继续执行。无授权、错误 argv、HEAD/指纹/身份变化时在 CLI 之前退出。

```powershell
# 仅获本文件对应的当轮授权、刷新预检并建立一次性记录后执行。
# 在独立 PowerShell 子进程设置这些值，退出时结束该子进程环境。
Set-Location -LiteralPath 'C:/Users/23033/Desktop/opensre2/.worktrees/feishu-s5b-feedback'
$env:OPENSRE_PROJECT_ENV_PATH = 'C:/Users/23033/Desktop/opensre2/.env'
$env:OPENSRE_GATEWAY_HOST_SCHEDULER = '0'
$env:OPENSRE_SENTRY_DISABLED = '1'
$env:OPENSRE_ANALYTICS_DISABLED = '1'
$env:PYTHONPATH = 'C:/Users/23033/Desktop/opensre2/.worktrees/feishu-s5b-feedback/.superpowers/sdd/2026-10-02-feishu-config-gateway-acceptance-implementation-plan/window-runtime'
$env:UV_OFFLINE = '1'
uv run opensre gateway start --foreground
```

1. **阶段 A**：主代理确认真实 Feishu ready 和监督器启用，再给用户当轮 `/help` 操作通知。用户在固定群 @bot 发送一次 `/help`。只接受正常命令输出：`OpenSRE Feishu gateway.\nSend a message to chat with the agent.\nCommands: /new (new session), /help`。该阶段模型与历史读取次数须为 0；收到实际回复后才进入 B。
2. **阶段 B**：主代理给出以下模板的当轮唯一完整文本（当前不要发送）。用户只在固定群 @bot 发送这一条：`本轮标记为 FEISHU_CONFIG_VERIFY_<UTC秒>_<nonce>。请在当前聊天过去5分钟内搜索这个完整标记，读取最新匹配消息ID，然后仅用一句简短中文报告已找到并读取。不运行其他工具，不主动额外发送或回复消息。` 这条消息本身就是待搜索内容。真实模型须从正常 offered 工具调用 search，再 get 同一实际 ID，然后正常卡片完成；未调用、调错或出现 fallback 只报告未验成，不补发 turn。

Agent 主动 send/reply、其他工具、reaction、删除、额外消息与未知 API 拒绝并停止。这里的拒绝来自临时验收监督器，不能描述为产品新权限。模型一句结果及正常状态/卡片内容不能预造；只允许原输出路径在固定群生成它们。

## 停止、写入和证据

任何失败响应（包括成功 HTTP 中的业务/流错误）、未知请求/帧、超限、超时、身份或配置不一致、额外入站、重连、第二消费者都锁闭全局闸，不自行重试、扩预算或加权限。链路正常完成后，最后一次正常写请求安静 2 秒即提前停止，不用满 15 分钟。只操作本轮自建控制器；启动句柄尚未交回时等待同一 30 秒清理预算，异常/超时只退出本轮进程并报告清理失败。

允许原流程在本机 OpenSRE home、任务证据目录及系统 TEMP 写入必要的 gateway status/log、入站安全审计、会话/绑定、turn/反馈 manifest、锁和临时状态。原 env、unbound/org integration store（含 identity policy）及其原子替换路径受保护；不改真实授权。foreground 保留原 stale pidfile，不以它替代进程存活证据。结束时撤销本轮临时授权、关闭本轮连接/线程/进程并刷新消费者快照，保留计数和必要状态，不删除群消息，不清理用户文件。

报告仅含时间、准确 HEAD、阶段、底层尝试/写入计数、正常 offered/调用次序、同一 ID 与 card 完成的布尔证据、通用错误类别和 env/store 不变证明。不会输出正文、ID、凭据或带令牌 WS URL。

区分配置回归通过、Feishu 普通入口/真实模型搜索读取/卡片现场结果和清理结果。health、跨操作者/聊天、分页、post/卡片搜索、S8a 多页投递不在本窗口现场覆盖；不将旧接受记录沿用到本项，不把零网络 synthetic 测试当真实成功。现场汇报后仍等待独立合并决定。
