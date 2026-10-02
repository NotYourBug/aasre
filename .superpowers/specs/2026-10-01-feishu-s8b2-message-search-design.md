# 飞书 S8b-2 当前聊天有界消息搜索设计

- Date: 2026-10-01
- Status: 书面 spec 与实施计划已批准，用户选择 Native（2026-10-01）；实施、本地验证和独立整分支审查完成；已创建 [PR #45](https://github.com/NotYourBug/aasre/pull/45)，准确 HEAD 的 CI/Greptile 状态以 PR 为准。
- Parent roadmap: [飞书能力补强路线](2026-09-12-feishu-capability-completion-design.md)
- Baseline: `origin/main` / GitHub main `837124ae5ee83b51b6e70e90ba38b891dff4cf3f`；S8b-1 已通过 PR #44 交付。
- Worktree: `C:\Users\23033\Desktop\opensre2\.worktrees\feishu-s5b-feedback`
- Branch: `codex/feishu-s8b2-search-spec`，从上述 main 新建；保留 S8b-1 分支。
- Scope: 一个只读 ACTION 工具 `feishu_search_messages`，用于当前聊天的有界历史发现和关键词匹配；书面契约已批准。
- Implementation plan: [独立实施计划](2026-10-01-feishu-s8b2-message-search-implementation-plan.md)，按用户选择的 Native 执行。
- Authorization: 用户已批准设计、Native 实施及三次限定预算的真实只读验收；第三轮文本链路通过。用户随后单独接受已披露现场限制并批准合并；三轮均已结束，新的真实请求和权限变更尚未授权。

## 1. 目标和成功标准

让飞书 action agent 在不知道消息 ID 时，先找到当前聊天中有关联的消息，再通过已完成的
`feishu_get_message` 读取选中的单条消息。用户无需在飞书客户端手工取得 ID。

本项落实原路线中的消息发现/搜索能力。搜索基于机器人可以读取的当前聊天历史；必须向调用者
说明实际扫描窗口、数量、匹配规则和遗漏情况。结果不能暗示覆盖全部历史、普通群话题内所有回复、
未扫描页或无法安全处理的正文。

用户本次明确的工作顺序是先完成 S8b，再调整项目瑕疵。为本功能必须修复的问题随本项处理；
无关重构、已存在 CodeQL 告警清理、旧传输删除和部署整顿不并入本 PR。

本稿采用 architectural 路径：新工具有新的检索接口、分页和数据释放契约，需要独立 written spec、
实施计划、实现、验证和 PR。用户于 2026-10-01 批准本文书面契约并选择 Native 执行独立实施计划。

## 2. 只读调研和可信来源

2026-10-01 核对：既有工作树干净；本地 `origin/main` 和 GitHub main 均为上述基线。
没有使用旧聊天的暂存 ID，没有加载 `.env`，没有真实消息检索、成员查询、reaction 操作或网关连接。

直接读取飞书公开文档的 schema，并与已安装的 `lark-oapi` 1.7.3 请求模型对照：

| 来源 | 与设计有关的结论 |
| --- | --- |
| [原生消息搜索](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/search) | 要求 `user_access_token` 和 `search:message`。SDK builder 列出 TENANT/USER 不构成应用身份获准使用的证据。 |
| [会话历史](https://open.feishu.cn/document/server-docs/im-v1/message/list) | 支持 tenant 身份，能绑定聊天、秒级时间范围和分页；每页最大 50。普通群的 chat 容器只返回话题根消息。 |
| [成员列表](https://open.feishu.cn/document/server-docs/group/chat-member/get) | 支持 tenant 身份，返回用户成员，不包含机器人；同批加入成员可能造成结果多于 page_size。留给 S8b-3 单独设计。 |
| [添加 reaction](https://open.feishu.cn/document/server-docs/im-v1/message-reaction/create) | 支持 tenant 身份；操作者须在消息所在会话，撤回消息和系统消息不支持。留给 S8b-4 单独设计。 |

历史读取所列基础权限为 `im:message`、`im:message:readonly` 或历史版
`im:message.history:readonly`；以应用身份读取群聊历史还必须开启 `im:message.group_msg`。
仅接收 @机器人的群消息权限不满足这个额外前置。此前 S8b-1 验收中的一次 history 请求返回 `230027`；
该历史记录只能证明那次请求被拒绝，不能据此认定具体缺少哪个权限，也不能证明本次可用。
单条消息 GET 成功不替代本接口权限验收。

公开文档原始证据仅保存在本机 scratch，不加入产品包或测试夹具；测试使用人工构造的真实 SDK
模型与离线 transport。文档没有确认的响应时序、完整性或权限状态不得当作已验证事实。

## 3. 三种方案及推荐

| 方案 | 收益 | 代价 / 限制 |
| --- | --- | --- |
| **推荐：当前聊天有界历史 + 本地关键词匹配** | 延续现有机器人身份和当前聊天权限；同时解决 ID 发现 | 只有扫描范围内的字面匹配，不能声称原生全文或全历史搜索 |
| 原生搜索 + 用户 OAuth | 使用上游搜索和筛选能力 | 需新增用户登录、请求者绑定、用户凭据保存与刷新、撤销和失效处理；不能拿应用 token 替代 |
| 为网关接收的消息维护本地搜索索引 | 可搜索长期积累的已接收消息 | 新增持久正文、保留期、删除同步、回填和隔离边界，无法直接覆盖未接收历史 |

用户于 2026-10-01 回复“按照你推荐的来做”，选定第一种；同时询问搜索是否值得做。
本项的收益定位于当前群证据和消息 ID 发现，以有界投入完成原路线搜索范围。
该回复选择已展示的方式；并未审阅本稿尚未展示的分页、内容和错误等书面契约。
不会在当前机器人方案失败时自动退到 OAuth、全局搜索或本地索引。

## 4. 公共接口和工作预算

工具名 `feishu_search_messages`；`source="feishu"`；只提供给 ACTION；
`gateway_only` 标签、READ_ONLY、无需写操作审批、parallel_safe、accepts_runtime_context。
公开 schema 拒绝额外字段；日志省略 query 和时间窗口的原始输入。

| 输入 | 契约 |
| --- | --- |
| `query` | 可选字符串，去首尾空白，最多 256 Unicode 字符；空串发现最近消息；非空执行字面大小写折叠子串匹配；拒绝无效 UTF-8 及控制字符 |
| `start_time` | 必填整数，Unix 秒；拒绝 bool、浮点、字符串和隐式转换 |
| `end_time` | 必填整数，Unix 秒；满足 0 <= start_time < end_time <= 调用时捕获的当前 UTC 秒 |
| `limit` | 可选整数，默认 10，范围 1–20；控制返回项数，不改变扫描上限 |

时间窗口最大 7 天。工具不接受 chat_id、container、thread_id、target、sender、token、endpoint、
page_token、page_size 或 max_pages。明确窗口由 action agent 根据用户请求选择，并在结果中展示；
不在工具内猜测用户意图或自动扩大范围。

预算固定：

- 每次最多 3 次历史数据 GET，每页 50 条，最多处理 150 个上游项。
- 不自动重试；分页是同一冻结窗口的前进，不是重试。每个 HTTP 请求超时 10 秒。
- 单条原始正文沿用 S8b-1 的 256 KiB、深度 64、节点 8,192 上限。
- 每次累计被处理正文最多 2 MiB UTF-8；达到上限停止本次扫描并报告不完整。
- 每条预览最多 600 Unicode 字符，最多 20 条；成功结果 content 的序列化总长度最多 20,000
  Unicode 字符，按完整条目缩减，不截断外层 JSON。
- 上述正文预算在 SDK 解析响应后落实，不声称限制 HTTP 响应体或 SDK 初始分配的内存。
- 共享常量放 `config/constants/feishu.py` 并由常量 facade 导出，不新增环境变量或依赖。

一次调用内捕获 now、规范输入、聊天和应用身份；各页重用相同 start/end、排序和 scope。
排序固定为按创建时间降序，不以重复排序重建结果。

## 5. 冻结授权和请求边界

复用 S8b-1 的 `resolve_runtime_read_scope`：

- 冻结 `ActionPromptContext` 必须是 Feishu gateway。
- 冻结集成视图必须提供当前聊天、已验证 chat-app 配置和应用身份。
- prepare 路径验证输入和集成 scope；直接 run 路径独立复核宿主与 scope。
- 凭据解析沿用现有聊天应用入口，解析后的 app_id 必须匹配捕获的 app_id。
- S8a 默认目标、出站白名单、成员列表、用户输入和可变 session 缓存均不授予读取权限。
- 实际发现和最终 offered-tools 门禁共同约束可见性；shell 及其他 transport 不提供该工具。
- 工具不接收或保存用户 OAuth token，也不使用告警推送应用身份。

通过现有 SDK 构建 `ListMessageRequest`，固定 `container_id_type="chat"`，
`container_id=scope.chat_id`、时间窗口、降序和页大小。正文使用当前 SDK/文档支持的
`card_msg_content_type="user_card_content"`，不另发单条 GET 以补充搜索正文。

实际 HTTP/token 请求数由离线 transport 和真实验收计数单独记录；3 次数据 GET 的预算
不等于只有 3 次总 HTTP 请求。SDK 日志和失败遥测不得输出请求 query、原始响应或 token。

## 6. 分页、响应校验和取消

每页响应成功后先校验 data、items 列表、items 数量及 has_more 的确切类型。
has_more 为 true 时必须存在非空、有效 UTF-8、最多 4,096 字符的下一页 token；
token 仅限本次调用局部使用，不出现在模型结果、日志、持久层或公开输入。

所有页沿用冻结请求参数。维护局部 set 检测已使用 token 和已见 message_id：
token 重复或 has_more/token 矛盾失败；重复消息只处理第一次，避免重放计数和重复结果。
不会建立全局 cursor 缓存、跨调用续页或跨会话正文缓存。

在访问本页任何正文前，先检查本页所有项的身份元数据：

- chat_id 必须精确匹配当前 scope；message_id 必须非空、长度不超过 256，
  且仅含 ASCII 字母、数字、下划线或连字符，不返回控制字符或任意描述性文本。
- create_time 必须可严格解析为非负毫秒整数，且在本次秒级窗口边界内。
- deleted 必须为 bool；deleted=true 的消息跳过，不读正文。
- msg_type 必须为非空、有效 UTF-8、最多 64 字符且无控制字符的字符串。
  仅释放已知类型；未知类型计入不可搜索候选，不释放其原始类型值或正文。
- 任一聊天/身份校验失败，整次调用失败；丢弃此前页的暂存结果，不释放被拒绝项的信息。

请求前、每页返回后、处理正文前和释放成功结果前检查已有取消通道。
取消后不继续分页，不发送、回复、加 reaction 或触发新的替代检索。
同步 HTTP 已发出时不能撤销网络请求；取消保证丢弃结果。

## 7. 匹配和脱敏预览

支持 text、post、interactive 的关键词匹配；其他类型只在空 query 的发现模式中返回
验证后的消息 ID、时间和类型，预览为空，不读取或下载资源。

text/post/interactive 正文先走既有 `normalize_read_content` 的有界解析、敏感键脱敏、
decoded-string 秘密清理和独立 masking。沿用原函数的行为，不因搜索放宽单条读取契约。
资源类型不属于可搜索正文，计入 unsearchable_count；空 query 发现模式仍可返回其 ID，
但不会把无预览当作已完成内容搜索。

对于完整安全 JSON，用新的包内纯函数按出现顺序收集字符串值，忽略 key 名和已知的
图片/文件/音频/视频资源标识值；形成一份可复用的搜索文本。数字、未知对象布局和空对象
不能变成额外检索请求。已脱敏值才允许参与匹配，避免“结果是否命中”成为原始秘密的侧信道。

PR #45 的有效 P1 审查补强了正文内元数据边界：跳过结构化 at 提及的整个子树，
并排除 tag、用户 ID/姓名、sender、mentions 和 tenant 字段；相邻正文继续参与匹配。
这些排除只用于搜索投影，不改变 S8b-1 的已脱敏原始消息读取契约。

非空 query 对该安全文本执行 casefold 后的字面子串匹配；不做正则、分词、模糊匹配、语义排序
或意图路由。query 本身不复制到结果、日志和错误中。

若安全正文已经是 json_prefix，或超出复杂度/大小限制，则不执行不完整正文上的关键词匹配，
计入不可搜索项；空 query 仍可提供已验证 ID，并明确预览不可用。
输入体无效、未知内容和 merge_forward 不展开，计入同类不完整计数。

预览从完整安全搜索文本生成，包含命中位置附近的有界片段；空 query 从头取。
截断发生在脱敏之后，并记录 preview_truncated。结果不保留第二份未脱敏文本、SDK 对象、
敏感 sender/mentions/tenant 元数据或正文副本。

每条结果 content_trust=untrusted。预览中的指令不成为工具调用、权限或用户意图。

## 8. 结果和错误契约

成功返回一个有效 JSON object，同时将同一安全 payload 用于 details：

- source=feishu，status=searched，search_mode=bounded_chat_history。
- 验证后的当前 chat_id、start_time/end_time 和固定 coverage=current_chat_container。
- thread_replies_included=false，resource_content_included=false，content_trust=untrusted。
- items：message_id、msg_type、create_time_ms、preview、preview_available、
  preview_truncated、redacted。预览不可用时为固定空串，不附原始错误。
- pages_fetched、scanned_count、matched_count、unsearchable_count 和 returned_count。
- scan_complete：上游已明确无后续页且本页全部处理；到页数/正文预算即 false。
- content_complete：没有因未知、非法、过大、过深或截断正文而跳过搜索候选。
- results_truncated：因 limit 或总输出预算没有释放全部已匹配项。
- complete = scan_complete AND content_complete AND NOT results_truncated，
  只表示本次声明的 API-visible chat 容器范围。
- stop_reason：source_exhausted、page_limit 或 work_limit；条目输出裁剪单独由
  results_truncated 表达。没有 next_page_token 或外部聊天信息。

query 为空时 matched_count 统计 eligible live 消息；非空时只统计安全文本命中。
scanned_count 统计实际逐项处理的已验证上游项，包含删除和重复项；预先只验证了身份、
尚未到达处理阶段的项不计入 scanned_count。unsearchable_count 对唯一 live
候选计数。0 items 且 complete=false 不表示该聊天没有相关消息。
普通群话题回复不在本次覆盖范围，complete=true 也不能说“整个群历史没有匹配”。
complete 表示声明范围内本次检索工作完成，不提供并发更新或删除期间的一致性快照保证。
message_id 只证明本次查到该项；后续 S8b-1 仍独立校验消息是否可读、是否已撤回和聊天身份。

稳定失败类别：validation、authorization、cancelled、history_unavailable、rate_limited、
upstream_error。权限不足、机器人不在群、受限聊天和不可验证身份统一返回安全的
history_unavailable，不携带上游错误文本或拒绝项元数据。上游 429 用 HTTPStatus 判定；
业务错误集合集中放配置常量。正文单项问题计入不完整状态，不释放原始异常。

低层错误在 except 之外重建 code-only 异常，避免保留敏感 __context__/__cause__。
意外失败只报告安全异常类型，不将 SDK 原始异常附到外部输出或遥测。后续页失败时整次失败，
不把已缓存结果伪装成完整搜索。

## 9. 模块归属和既有契约

推荐模块布局（正式实施计划再列任务/函数/命令）：

- `integrations/feishu/search_types.py`：stdlib-only 冻结输入、结果及安全错误。
- `integrations/feishu/message_search.py`：SDK 历史读取、固定分页、元数据优先校验和预算。
- `integrations/feishu/search_content.py`：安全 JSON 的字符串投影、字面匹配、预览。
- `integrations/feishu/tools/feishu_search_messages_tool/`：薄 facade、tool、validation、results。
- `integrations/feishu/action_prompt.py`：仅在真实 offered-tools 中说明搜索范围和缺失项。
- `config/constants/feishu.py`：共享上限和历史错误分类。
- `docs/messaging/feishu.mdx`：用户可用的输入示例、权限前置和覆盖限制。
- `tests/integrations/` 及现有 offered-tools/runner 测试：失败模式与真实执行路径。

复用现有 gateway_only 标签、read_scope、消息脱敏和取消通道；不建立新注册表或上下文接口。
S8b-1 公共输入、输出和单次 GET 预算保持；S8a 的审批、父消息 metadata-only 预检和
投递行为保持。成员和 reaction 不在本项注册，也不提前出现在 prompt。

## 10. 离线验证与 PR 门禁

高信号测试至少覆盖：

1. 真实 RegisteredTool/core.tool 入口：额外参数、畸形时间/bool、未验证配置、
   shell 携带合法 gateway 字段和跨 transport 都在网络前被拒绝。
2. SDK 真实请求/响应模型及离线 token/transport 拦截：固定 chat/time/order、
   三页封顶、无数据 GET 重试、无单条 GET、无 USER token。
3. 本页任一 foreign-chat 项在所有正文访问前失败；后续页失败丢弃先前结果。
4. has_more/token 不一致、重复 token、重复 ID、空页分页及原窗口冻结。
5. 秘密先脱敏再匹配；截断/非法/过复杂正文不造成假 complete；多字节预算和总输出有效 JSON。
6. 取消、session 复用和并发聊天：应用、窗口、结果和 masking 不串用。
   使用事件/屏障及 finally 清理，不以 sleep 断言并发行为。
7. 真正工具发现、最终 offered-tools 与 prompt 一致；旧 write-only prompt 和
   S8b-1/S8a 的相关行为回归。

仅文档阶段按 CI.md §0 验证改动范围与 git status。代码阶段执行 CI.md 的完整静态检查、
受影响模块的 focused 测试与必要 API/import border；每次 push 跟踪准确 PR HEAD 的 CI、
评审和 Greptile 5/5，不以创建 PR 代替完成。合并须另有用户指令；
合并后检查准确 merge SHA 的 main CI、完整 CodeQL 和 release，记录跳过状态。

## 11. 真实验收授权与结果（第三轮工具链通过）

使用明确指定的聊天应用和当前测试群，在新批准的 15 分钟窗口内最多 6 次历史数据 GET，
最多 1 次后续单条消息 GET；不重试、不自动增加预算。运行前确认真实历史读取权限，
失败则安全记录失败类别并停止，不改控制台权限或发布应用。

一位用户即可提供固定文本关键词消息并参与两项验收：

- 空 query 发现 ID，随后用 S8b-1 读取该 ID。
- 窄时间窗口的 query 命中固定文本，验证脱敏预览和声明的覆盖状态。

权限失败或真实分页未测，不用离线 mock 宣称现场通过。分页、并发、跨聊天、正文异常的
离线证据和现场证据分别记录；受控跨聊天现场拒绝需要指定的第二测试群及新授权，
不能沿用 S8b-1 的豁免。没有多用户条件时跨操作者验证保持离线并披露。

用户于 2026-10-01 明确批准一次上述只读验收，并发送固定关键词文本。实际窗口为
北京时间 20:32:48–20:47:48，执行在 20:33:36 停止；测试产品提交为
`f27d5df91033e14a681ca1f51480398e990ee05a`。经真实 RegisteredTool/executor 和 SDK transport：

- token POST 一次，HTTP 200 / code 0；仅证明本次应用 token 获取成功。
- 历史 GET 一次，HTTP 400 / code 230027；工具安全返回 `history_unavailable`，无消息结果。
- 单条 GET 零次，关键词搜索未调用；没有重试或继续消费余下预算。发现 ID、后续读取和关键词命中均未通过现场验收。

原始响应、凭据、消息 ID 和正文未写入交付记录。不能只根据此错误码断言缺少某一个具体 scope；
需要在应用权限管理中核对历史读取权限及生效状态后，另行批准新的只读验收窗口。
真实分页、跨聊天、跨操作者和普通部署网关仍未现场验证；本次安全停止不替代这些成功路径。

用户随后批准只读核对后台，并自行完成登录。核对应用身份与批准的 `.env` 一致：
`im:message:readonly` 已开通，最初精确筛选 `im:message.group_msg` 无结果，且显示当前修改均已发布。
直接读取上述官方文档确认群聊额外要求，因而找到了一个真实必要权限缺口；不能因此断言所有其他前置已满足。
用户随后自行开通相关权限。刷新后台已确认 `im:message.group_msg` 为应用身份、已开通，
并显示“当前修改均已发布”；相关 `im:message.group_msg.include_bot:read` 也由用户自行开通。
Agent 没有开通、关闭权限或发布应用，本次核对没有验收脚本的 token POST 或群消息 GET。
后台权限状态不替代真实搜索成功；用户说明中已补齐群历史权限前置。

用户随后明确“新的只读窗口批准给你了”。第二轮窗口为北京时间 21:21:18–21:36:18，
实际在 21:22:25 停止；测试 HEAD 为 `d70b6780c8c8883d1d764259d6790cc6cb11912f`。
冻结查询区间为 20:21:18–21:22:24，复用此前固定测试词和同一批准应用/群，经真实 RegisteredTool/executor：

- token POST 一次，HTTP 200 / code 0；历史 GET 一次，HTTP 200 / code 0，返回两条消息、无后续页。
- 发现工具返回 `status=searched`，扫描、匹配和返回均为 2；不可搜索计数为 0，三项完整性标志为 true，未裁剪结果。
  覆盖仅为声明的当前 chat 容器，不包含话题回复和资源正文；这些标志不证明并发快照或全部群历史。
- 两条文本均命中固定测试词，验证了已校验 ID 和安全预览可被发现；临时验收脚本要求唯一命中，
  因而以 `marker_not_uniquely_discovered` 停止。这是验收选择条件过严，不能当成产品或权限失败。
- 单条 GET 零次，独立关键词搜索未调用；无重试，没有继续消费剩余预算。发现 ID 现场成功，
  同一 ID 后续读取和独立关键词命中链路仍未通过本项现场验收，真实多页也未触发。

临时脚本随后零网络修正为按创建时间选择最新匹配文本，同时间保持上游顺序；
以人工数据验证重复词、同时间和缺失词选择，未修改产品代码。随后提出复用上述固定查询区间，
仍限制 15 分钟、6 次历史 GET / 1 次单条 GET、最多 3 次 token、失败即停和无重试，由用户单独批准。
不要求用户再发送消息，不保留或提交真实 ID、正文、凭据或 SDK 原始响应。

第三轮获用户明确“批准下一轮只读验收（推荐）”，窗口为北京时间 21:35:02–21:50:02，
在干净 HEAD `d3dbfe89530036bf8177076c601b33987ca0b865` 上执行；该提交已通过 CI、Greptile 5/5、零未解决线程。
查询仍固定为 20:21:18–21:22:24，使用同一批准应用/测试群和此前文本；实际于 21:35:45 成功结束，进程退出 0。
经真实 RegisteredTool/executor 与 SDK transport：

- token POST 1 次、历史 GET 2 次、单条 GET 1 次，四个请求均为 HTTP 200 / code 0；无重试或重定向。
- 空 query 发现返回两条文本，选取最新的固定词命中；返回了已校验的消息 ID 和安全预览。
- 单条读取 `status=read`、无错误；同一 ID 和固定文本内容两项断言均通过。
- 独立关键词搜索 `status=searched`、无错误；再次找到同一 ID，预览命中固定词。
- 两次搜索各读取一页，均为 scanned/matched/returned=2/2/2、unsearchable=0、has_more=false；
  scan_complete/content_complete/complete 均为 true，results_truncated=false，stop_reason=source_exhausted。
  content/details 一致性通过，内容标记为 untrusted，覆盖仍仅为当前 chat 容器，不包含话题回复或资源正文。

本项文本发现 → 读取 → 关键词命中现场链路现已通过；真实多页、跨聊天/操作者、post/卡片搜索及
普通网关配置解析/部署仍未现场验证，不能以这一页两条文本或离线用例升级其状态。
没有启动普通网关/模型、发送消息、更改权限或发布应用；未提交 ID、正文、凭据或原始响应。

零网络预检发现全局有效 catalog 未提供 Feishu 配置，但批准的 `.env` 应用/群与真实凭据加载器一致。
受控执行器通过真实 catalog 的显式 env record 输入建立本轮冻结配置，未修改全局 store、`.env` 或权限。
这不证明普通网关可以解析当前本机配置；该部署前置保持待验，按用户优先级不在本 PR 修复无关配置瑕疵。

前两轮分别在权限拒绝和唯一命中条件不满足后终止，第三轮验收已成功结束；新的真实请求和权限变更仍待单独授权。
用户随后明确“接收现场限制，合并吧”，在已披露真实多页、跨聊天/操作者、post/卡片搜索、
普通网关配置解析/部署未验后，接受本项这些现场限制并批准合并；这不是复用 S8b-1 豁免。
接受限制不将离线证据升级为现场通过，也不修复全局 catalog 前置或授权部署、发消息/权限修改。
批准记录推送后先核对准确新 HEAD 的 CI、Greptile 5/5 和零未解决线程，再合并并跟踪
准确 merge SHA 的 main CI、完整 Python/JS CodeQL 与 release；失败须修复或回滚，不能提前宣告交付完成。
既有 .env 授权背景不扩大为本项无限历史扫描、发送/删除测试消息、
reaction、权限变更、普通网关部署/重启或 OAuth 登录授权。

## 12. S8b 后续顺序与结束标准

2026-10-02 用户取消 S8b-3／4 后，当前选定范围与状态为：

| 子项 | 当前状态 | 本项退出后下一步 |
| --- | --- | --- |
| S8b-1 已知 ID 读取 | PR #44 已合并、合并后验证完成；保留已接受现场限制 | 不重做已完成实现 |
| S8b-2 消息发现/有界搜索 | PR #45 已合并为 `d8598c2`，main CI 与完整 CodeQL 成功，Release 按 guard 跳过；文本现场链路通过，保留已接受限制 | 配置解析问题与普通网关实际运行验收 |
| S8b-3 当前群用户成员列表 | 用户明确取消，排除于当前范围；未实现 | 不再启动设计/实施 |
| S8b-4 Agent 添加 reaction | 用户明确取消，排除于当前范围；未实现 | 不再启动设计/实施；保留既有用户反馈/reaction 捷径 |

S8b-3/4 的编号原为本稿建议；2026-10-02 的取消决定取代原后续安排，不将取消项勾选为实现完成。

后续[配置解析与普通网关验收计划](2026-10-02-feishu-config-gateway-acceptance-implementation-plan.md)
于 2026-10-02 获 Native 恢复实施授权；有限配置修复及本地回归通过，普通网关现场尚待新窗口。
这不重做本项搜索交付，也不改变 §11 已接受限制或授权新的消息/模型请求。
S8 结束需要所有选定能力实际交付、准确提交检查完成、真实验收结果或逐项接受的限制被记录；
不得只因工具有注册项就勾选完成。S8a/S7 与 S8b-1 的既有验证待办保留原有状态。

## 13. 设计自审和批准记录

已按当前仓库/API 契约自审：范围为一个工具；有明确窗口、分页/正文/输出上限，
冻结授权、响应释放顺序、错误与取消契约；不捆绑 OAuth、索引、成员或 reaction。
搜索数据的关键词过滤位于用户明确调用的工具内部，不是绕过 action agent 的意图路由。

已选定：推荐的当前聊天机器人有界搜索方式，用户于 2026-10-01 明确回复。
用户随后于同日明确回复“批准”，批准本书面 spec；又选择 Native，授权执行独立实施计划。
五个实施任务已提交，最新 663 项 focused 测试通过、41 项既有描述契约隔离项（#5498）跳过；静态检查与导入边界通过。
独立整分支审查完成，无 Critical/Important 运行时问题；两处文档事实纠正随计划内状态更新完成。
Greptile 对 56ae02a 发现的正文内提及元数据 P1 已经 RED→GREEN 修复并完成上述全套本地复验。
[PR #45](https://github.com/NotYourBug/aasre/pull/45) 已创建；必需检查、Greptile 和未解决 review threads 按最新准确 HEAD 跟进。
真实只读验收已单独获准并执行三次；第三次文本发现/读取/关键词链路成功，见 §11。
用户随后明确接受其余已披露现场限制并批准合并；Agent 未修改运行配置或权限，准确最终 PR/merge SHA 检查仍须闭环。
设计主体保持不变，执行和验证记录见实施计划。
