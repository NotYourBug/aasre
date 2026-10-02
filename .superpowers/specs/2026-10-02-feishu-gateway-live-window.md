# 普通 Feishu 网关当轮现场窗口

**状态：两次获批窗口均已结束，普通网关未验成；建议配置修复单独交付，运行验收延期，待用户决定。** 临时监督器的零网络实现已获批准。本文件保留已结束窗口的实际目标、预算和停止条件，不是第三次启动提案，不包含合并授权。配置产品提交为 `759e056e6467c8dacda30ab076581d5ebffefd48`；两次授权均已撤销，不能复用。

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

允许原流程在本机 OpenSRE home、任务证据目录及系统 TEMP 写入必要的 gateway PID/status/log、入站安全审计、会话/绑定、turn/反馈 manifest、锁和临时状态。原 env、unbound/org integration store（含 identity policy）及其原子替换路径受保护；不改真实授权。正常控制器会更新原 pidfile 为本轮 PID；停止后保留正常流程产生的 pid/status，不以它替代进程存活证据。结束时撤销本轮临时授权、关闭本轮连接/线程/进程并刷新消费者快照，保留计数和必要状态，不删除群消息，不清理用户文件。

报告仅含时间、准确 HEAD、阶段、底层尝试/写入计数、正常 offered/调用次序、同一 ID 与 card 完成的布尔证据、通用错误类别和 env/store 不变证明。不会输出正文、ID、凭据或带令牌 WS URL。

区分配置回归通过、Feishu 普通入口/真实模型搜索读取/卡片现场结果和清理结果。health、跨操作者/聊天、分页、post/卡片搜索、S8a 多页投递不在本窗口现场覆盖；不将旧接受记录沿用到本项，不把零网络 synthetic 测试当真实成功。现场汇报后仍等待独立合并决定。

## 首次授权窗口结果

用户审阅后明确批准下一步。2026-10-02 09:15:27 UTC 在准确 HEAD `fd96db7066ea4ed1c877fceaa2db214b0897e9ed` 建立一次性记录，原到期时间为 09:30:27 UTC。普通 `uv run opensre gateway start --foreground` 约 1.9 秒后退出 78，日志只有 `WINDOW_GUARD_REFUSED`；未通知用户发送消息。授权记录在 09:17:09 UTC 前撤销，本机消费者、Docker/WSL 和 8000 监听数均为 0，原 stale PID 34844 不存活。

拒绝发生在监督器读取授权 JSON 时，早于 `prepare`、网关 composition 和网络闸开启。Windows console-script 的 UTF-8 mode 为 0、locale 为 cp936；UTF-8 写入的中文 prompt 被默认 GBK 解码而抛出 `UnicodeDecodeError`。当次运行没有建立请求计数器，不能把缺少计数报告当成完整网络验收；该启动顺序和静态拒绝日志只支持 CLI 前退出，Feishu ready、命令、模型、搜索/读取和卡片仍全部未验。

授权撤销后，原脚本/HEAD 的离线 `prepare` 指纹与启动前相同，确认原 env/store 未变。临时监督器现显式用 UTF-8 读取授权；新增 Windows locale + 中文记录回归先失败再通过，完整 39 项离线回归通过，退出时外部请求尝试 0。另在真实 Windows `opensre.exe` 的 sitecustomize 时机执行完整离线 `prepare` 并立即退出，网络闸一直关闭；cp936 模式下通过。没有重启现场窗口、修改产品、凭据、授权或预算。

首次失败后形成预算不变的新窗口提案，随后用户重新授权，并要求再次判断验证必要性；实际判断及第二次结果如下。

## 第二次窗口与必要性复核

配置修复本身已有真实身份策略持久化、默认 catalog/leaf/settings、作用域和授权回归，以及完整 PR CI 证据；无需真实网络重复证明纯配置选择。普通 CLI → 真实授权/会话 → 实际 offered 模型工具 → 同一 ID 搜索/读取 → 卡片的运行结论仍缺证据，PR #45 的显式 env-record 受控验收没有覆盖该链。因此在用户新授权下仅再尝试一次最小窗口，不扩大预算。

2026-10-02 22:12:20（Asia/Shanghai）在准确 HEAD `44793637811b99490d0ce6a0011e56a397fca862` 建立新的 900 秒记录；此前 CI 为 29 success / 7 skipped、Greptile 5/5、零未解决线程，main 仍为 `d8598c2`，原指纹一致、本机消费者和端口为 0。监督器成功激活，普通 CLI 的 GATEWAY_PROFILE 能力探针进入 `run_python_sandbox`；创建本地沙箱 Python 子进程时被预算 0 在创建前拒绝，退出 1，原因 `unscoped_network_or_subprocess`。

计数：model、turn、token、bot identity、endpoint、WS TCP、DNS、TCP、history/get、消息/CardKit 写入及入站均为 0；精确 stdlib socketpair IPC 1。观察到一次子进程创建拒绝，实际子进程未创建。Feishu 未 ready，offered/search/get/卡片均未验证；没有通知用户发送测试消息。结束摘要确认原 env/store 未变。22:14:13（Asia/Shanghai）核对授权已撤销、本机消费者/Docker/WSL/8000 监听均为 0，原 PID 34844 不存活；无自行重试或第三次窗口。

预检覆盖不足：拒绝沙箱临时文件写入使能力探针提前返回“不可用”，异常被原探针的非致命路径捕获，零网络 boot 因而继续成功，没有走到后续 subprocess。现场允许正常临时状态写入后才走到子进程创建。这证明当前正常启动路径与“子进程 0”的窗口预算冲突；不能把原零网络 boot 或 39 项边界测试升级为可执行普通网关成功证明，也不能据此判定 Feishu 产品故障。

## 建议的交付安排（待用户决定）

- PR #46 作为有限配置修复单独审阅：保留已完成的实现、回归、真实零网络配置证据和文档；不声称普通网关可用，不自动接受本项新限制或合并。
- 普通网关验收保留为待验，停止当前现场方案。若用户要求继续，先独立设计正常启动能力探针的明确子进程预算及完整启动预检，验证进程目标/脚本/环境、额外子进程拒绝和清理，再提出新窗口；本轮不增加豁免、不修共享启动或继续扩张监督器。
- health、跨操作者/聊天、分页、post/卡片搜索和 S8a 多页仍未在本项现场验证；旧限制接受记录不代替本项决定。S8b-3/4 保持取消，不新增 S8b-5。接受拆分或限制之后仍须单独明确批准合并，并跟踪 merge SHA 的 main CI、完整 CodeQL 和 release。
