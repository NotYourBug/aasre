# Feishu S8b-2 Bounded Message Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在当前飞书聊天中提供有界消息发现/关键词搜索，返回可供 S8b-1 使用的消息 ID 和安全预览。

**Architecture:** 现有冻结 read_scope 和 gateway_only 门禁决定权限；独立历史客户端最多读取三页，
逐页先验证全部身份元数据再处理正文。安全正文投影、公共输入、结果编码分别归属独立模块；
工具仅协调这些模块，提示词只说明实际 offered 的能力。

**Tech Stack:** Python、stdlib dataclasses/JSON、现有 lark-oapi 1.7.3、core.tool/core.tool_framework、
pytest、Ruff、mypy；PowerShell，Python 通过 uv run。

**Spec:** [已批准设计](2026-10-01-feishu-s8b2-message-search-design.md)

**Status:** 用户于 2026-10-01 批准设计并选择 Native，授权执行本计划；实施、本地验证和独立审查完成；[PR #45](https://github.com/NotYourBug/aasre/pull/45) 已创建，准确 HEAD 的 CI/Greptile 状态以 PR 为准。
**Worktree / branch:** `C:\Users\23033\Desktop\opensre2\.worktrees\feishu-s5b-feedback` /
`codex/feishu-s8b2-search-spec`；规划开始时干净 HEAD `52419c3`，
基于 main `837124ae5ee83b51b6e70e90ba38b891dff4cf3f`。

## Global Constraints

- 只实现 `feishu_search_messages`，ACTION、READ_ONLY、gateway_only、无需写审批、parallel_safe。
- 输入仅 query/start_time/end_time/limit，拒绝额外字段；query 去首尾空白后最多 256 Unicode 字符；
  必填整数时间满足 0 <= start_time < end_time <= 捕获的 UTC 秒，窗口最多 7 天；limit 默认 10，范围 1–20。
- 每次最多 3 次历史数据 GET，每页 50、最多 150 个上游项；不自动重试，每个 HTTP 请求超时 10 秒。
- 单条正文沿用 256 KiB UTF-8、深度 64、8,192 节点和安全 JSON 的 12,000 字符边界；
  每次累计处理正文最多 2 MiB UTF-8；预览最多 600 字符，成功 JSON 总长最多 20,000 字符。
- 机器人聊天应用 tenant 身份；当前应用/聊天来自冻结上下文；不引入用户 OAuth、全局搜索、
  索引、跨聊天读取白名单、新依赖、环境变量、workflow、共享上下文接口或 registry root。
- 资源不下载、merge_forward 不展开；未知类型不释放原始类型；先脱敏后匹配/预览，不输出
  query、page_token、sender/mentions/tenant 元数据、原始 SDK 对象、敏感异常或原始正文副本。
- token 和去重 set 只在本次调用局部存在；本页所有项身份验证必须先于本页任意正文访问；
  后续页失败整次失败；取消复用原通道并丢弃结果。
- complete 仅指声明的 API-visible chat 容器范围；普通群话题回复不包含，也不保证并发更新时一致性快照。
- 不处理成员/reaction、无关瑕疵、旧 CodeQL 清理或旧传输删除；保持 S8a/S8b-1/S7 既有契约。
- 编辑用 apply_patch；Python 命令用 uv run，设置 PYTHONUTF8=1；保留无关改动，不 reset/clean/force push。
- 产品/测试改动前读适用 AGENTS、CI.md、架构、工具归属/完整性指南及 PR 模板。
  包 facade 只 imports/__all__；共享常量归 config/constants；HTTP 状态用 HTTPStatus；
  named fakes，新增/修改 Protocol 方法仅 docstring；不扩大 import/API-border allowlist。
- live 验收和合并分别需要对应授权；设计批准不启动网关、模型、OAuth、真实读取或权限变更。

## Review Focus

1. 单一末页恰好达到页数/正文预算时，不误报未完成；预算阻止后续工作才报告不完整 — Task 3。
2. Unicode casefold 长度变化（ß → ss）不会把预览截到命中之外，CJK 按字节计预算 — Tasks 2/3。
3. JSON 转义使 600 字符预览膨胀；外层 JSON 仍完整且 <=20,000 字符，details 与之相同 — Task 4。
4. 空 query 下撤回、重复、资源/未知类型及空页造成的计数必须真实，不把缺预览称为完整内容 — Task 3。
5. 第三方 provider 强行 offered、复用 session 或并发请求不能继承另一聊天/窗口或加载 shell 凭据 — Tasks 4/5。

---

## 1. 文件地图、常量与类型

| 文件 | 责任 |
| --- | --- |
| `config/constants/feishu.py`、`config/constants/__init__.py` | 新检索上限/分类与 facade 导出，保留旧常量 |
| `integrations/feishu/search_types.py`（新） | stdlib-only 冻结记录、安全错误和 stop reason |
| `integrations/feishu/search_input.py`（新） | 无 I/O 的公共参数规范化；避免底层反向导入 tool package |
| `integrations/feishu/search_content.py`（新） | 复用脱敏后 JSON，形成一次性安全投影与命中预览 |
| `integrations/feishu/message_search.py`（新） | SDK list、元数据门禁、局部分页/去重/预算/取消 |
| `integrations/feishu/tools/feishu_search_messages_tool/{__init__,validation,results,tool}.py`（新） | 包 facade、准备输入、结果编码与工具协调 |
| `integrations/feishu/action_prompt.py` | 仅为真实 offered 搜索添加范围/不完整指导 |
| `docs/messaging/feishu.mdx` | 用户用法、读取权限前置与覆盖限制 |
| 本设计、本计划、总路线 | 只按实际完成/批准情况回写 |

测试新增：
`tests/integrations/test_feishu_search_input.py`、
`tests/integrations/test_feishu_search_content.py`、
`tests/integrations/test_feishu_message_search.py`、
`tests/integrations/test_feishu_search_messages_tool.py`、
`tests/integrations/feishu_search_support.py`。

既有测试仅按本项需要扩展：
`tests/core/agent/prompts/test_gateway_channel_prompt.py`、
`tests/core/agent_harness/test_gateway_channel_tools.py`、
`tests/core/agent_harness/test_channel_turn_isolation.py`、
`tests/tools/test_telemetry.py`。其余既有测试只运行，不改旧行为或断言以隐藏失败。

常量在 Task 1 定义、导出：

| 常量 | 值 |
| --- | --- |
| FEISHU_SEARCH_MAX_QUERY_CHARS | 256 |
| FEISHU_SEARCH_MAX_WINDOW_SECONDS | 7 * 24 * 60 * 60 |
| FEISHU_SEARCH_DEFAULT_LIMIT / FEISHU_SEARCH_MAX_RESULTS | 10 / 20 |
| FEISHU_SEARCH_PAGE_SIZE / FEISHU_SEARCH_MAX_PAGES | 50 / 3 |
| FEISHU_SEARCH_MAX_BODY_BYTES | 2 * 1024 * 1024 |
| FEISHU_SEARCH_MAX_PREVIEW_CHARS / FEISHU_SEARCH_PREVIEW_LEAD_CHARS | 600 / 120 |
| FEISHU_SEARCH_MAX_OUTPUT_CHARS | 20_000 |
| FEISHU_SEARCH_MAX_PAGE_TOKEN_CHARS / FEISHU_SEARCH_MAX_MSG_TYPE_CHARS | 4_096 / 64 |
| FEISHU_SEARCH_TEXT_TYPES | frozenset({"text", "post", "interactive"}) |
| FEISHU_SEARCH_METADATA_TYPES | frozenset({"image", "file", "audio", "media", "sticker", "share_chat", "share_user", "merge_forward", "system"}) |
| FEISHU_SEARCH_RESOURCE_KEYS | frozenset({"image_key", "file_key", "audio_key", "video_key", "media_key"}) |
| FEISHU_SEARCH_METADATA_KEYS（PR review 补强） | frozenset({"tag", "user_id", "user_name", "open_id", "union_id", "tenant_key", "sender", "mentions"}) |
| FEISHU_HISTORY_UNAVAILABLE_ERROR_CODES | FEISHU_AUTHORIZATION_ERROR_CODES \| FEISHU_MESSAGE_READ_UNAVAILABLE_ERROR_CODES \| {230073, 231203, 231204} |

复用 FEISHU_MESSAGE_READ_MAX_ID_CHARS、正文/深度/节点/安全 JSON 上限、10 秒 timeout 和
FEISHU_RATE_LIMIT_ERROR_CODES，不复制这些定义。新 preview lead 是窗口内展示位置的实施细节，
不改变 600 字符上限。

`search_types.py` 只导入 stdlib，定义：

- `FeishuSearchErrorCode(StrEnum)`：validation、authorization、cancelled、history_unavailable、
  rate_limited、upstream_error；`FeishuSearchError(code: FeishuSearchErrorCode)` 仅保存 code。
- `FeishuSearchStopReason(StrEnum)`：source_exhausted、page_limit、work_limit。
- frozen `FeishuSearchInput(query: str, start_time: int, end_time: int, limit: int)`。
- frozen `FeishuSearchContent(text: str | None, redacted: bool)`；None 表示无法提供可搜索预览，
  空字符串是已成功处理但没有字符串内容。
- frozen `FeishuSearchItem(message_id: str, msg_type: str, create_time_ms: int, preview: str,
  preview_available: bool, preview_truncated: bool, redacted: bool)`。
- frozen `FeishuSearchResult(chat_id: str, start_time: int, end_time: int,
  items: tuple[FeishuSearchItem, ...], pages_fetched: int, scanned_count: int,
  matched_count: int, unsearchable_count: int, scan_complete: bool,
  stop_reason: FeishuSearchStopReason)`。

结果记录不保存凭据、query、SDK item、raw body 或 page token；Result 不反向依赖 scope/client。
Input 只在本次调用局部使用，由 normalize_search_input 构造；客户端接收已验证输入。

## 2. 五个实现任务

每个任务依次完成可检查的 RED → 实现 → GREEN → 提交。
新接口可先最小可导入以获得行为 RED，但 import/收集/环境失败不计 RED，不提交占位实现。
本计划不迁移既有行为；如实施发现确需迁移，先用既有代码取得 characterization GREEN。

### Task 1: 输入契约、叶记录与共享上限

**Files:** 创建 `integrations/feishu/search_types.py`、`integrations/feishu/search_input.py`、
`tests/integrations/test_feishu_search_input.py`；修改 `config/constants/feishu.py`、
`config/constants/__init__.py`。

**Interfaces:**
`normalize_search_input(*, start_time: object, end_time: object, query: object = "",
limit: object = 10, now: int) -> FeishuSearchInput`，无 I/O，失败只抛 validation。

- [x] 写 `test_search_input_rejects_non_integer_and_unbounded_windows` 的规范化/边界断言。
  UTF-8 与 C0/C1 控制字符检查在 trim 前，trim 后校验长度；只接收真正 int，拒绝 bool，
  无隐式转换；未来时间或窗口 >604800 秒失败。同一 bug class 用参数组。
- [x] 将接口最小可导入，运行 `uv run --no-sync python -m pytest tests/integrations/test_feishu_search_input.py -q`；
  取得行为 RED，不能把 module 不存在当验证。
- [x] 实现记录、常量及 normalizer，保证如下断言并为无效值抛 code-only 错误：

```python
inputs = normalize_search_input(
    start_time=1000, end_time=2000, query=" 故障 ", limit=10, now=2000
)
assert inputs == FeishuSearchInput("故障", 1000, 2000, 10)
# 同一 invalid-input 测试：True 时间、1.0 时间、"1000" 时间、空窗口、
# 未来 end、query 含 "\n" 或孤立 surrogate、query 257 字符、limit 0/21/True。
assert FEISHU_SEARCH_PAGE_SIZE * FEISHU_SEARCH_MAX_PAGES == 150
```

- [x] 重跑该文件与 `tests/config/test_feishu_card_constants.py`，期望 GREEN；
  facade 新名字可导入，旧常量值不变。
- [x] 仅提交上述文件：`feat(feishu): define bounded search input contracts`。

### Task 2: 安全字符串投影与命中预览

**Files:** 创建 `integrations/feishu/search_content.py`、
`tests/integrations/test_feishu_search_content.py`；复用 `integrations/feishu/message_content.py`。

**Interfaces:**
`normalize_search_content(raw_content: str) -> FeishuSearchContent`；
`make_search_preview(content: FeishuSearchContent, query: str) -> tuple[str, bool] | None`。
返回 None 表示无完整可搜索文本或非空 query 不命中；空 query 对完整文本返回头部预览。

- [x] 写 `test_search_matches_only_sanitized_values_and_not_resource_keys`、
  `test_casefold_preview_keeps_the_original_unicode_match`、
  `test_incomplete_or_invalid_json_never_becomes_searchable`，明确断言：

```python
safe = normalize_search_content('{"token":"SECRET-CANARY","text":"故障","image_key":"img_canary"}')
assert make_search_preview(safe, "故障") is not None
assert make_search_preview(safe, "SECRET-CANARY") is None
assert make_search_preview(safe, "img_canary") is None
unicode_body = json.dumps({"text": "ß" * 400 + "Straße" + "尾" * 650}, ensure_ascii=False)
found = make_search_preview(normalize_search_content(unicode_body), "STRASSE")
assert found is not None
preview, shortened = found
assert "Straße" in preview and len(preview) <= 600 and shortened
assert normalize_search_content('{"text":"' + "x" * 13000 + '"}').text is None
```

- [x] 最小可导入后运行 `uv run --no-sync python -m pytest tests/integrations/test_feishu_search_content.py -q`，
  取得上述行为 RED。
- [x] 复用 normalize_read_content。只有 body_format=json、未 truncated 才解码已安全 JSON。
  以迭代 stack 保持字符串值原出现顺序，跳过 resource keys 及其子树；用 list + join 一次形成文本。
  安全类别的正文失败转成 text=None，不保存原异常；意外失败交给客户端安全边界。
- [x] 匹配 casefold 后文本。一次建立 folded 字符到原字符下标的映射，正确处理长度变化；
  预览从命中前最多 120 原字符开始，取最多 600 原字符。只要裁掉头或尾，preview_truncated=true。
- [x] 重跑新测试与 `tests/integrations/test_feishu_message_content.py`，期望 GREEN；
  同时 pin decoded escaped secret、CJK UTF-8 边界和 per-call masking 不串用，不重复写旧解析器测试。
- [x] 提交：`feat(feishu): build sanitized search previews`。

### Task 3: 当前聊天历史客户端和局部分页

**Files:** 创建 `integrations/feishu/message_search.py`、
`tests/integrations/feishu_search_support.py`、`tests/integrations/test_feishu_message_search.py`。

**Interfaces:**
`search_current_messages(*, app_id: str, app_secret: str, scope: FeishuReadScope,
inputs: FeishuSearchInput, cancel_requested: Callable[[], bool]) -> FeishuSearchResult`。
失败只抛 FeishuSearchError。私有守卫函数可处理 SDK 细节；外层在 except 之外重建错误。

测试支持模块定义 `SearchTransportProbe`，记录 ListMessageRequest、timeout、app_id 和 token 尝试；
`install_search_transport(monkeypatch: pytest.MonkeyPatch,
responder: Callable[[ListMessageRequest], dict[str, Any]]) -> SearchTransportProbe`，
通过 TokenManager.cache/Transport.execute 拦截真实 SDK，不复制旧 read helper 或改其类型。

- [x] 写 `test_history_scan_freezes_request_and_stops_after_three_pages`：
  三个响应各 50 个已验证项、has_more=true，断言数据请求数 3、scanned_count=150、
  stop_reason=page_limit、scan_complete=false；请求均为同一 chat、start/end、降序、
  page_size=50、card_msg_content_type=user_card_content、TENANT，HTTP timeout=10.0；
  中途修改 session/config 值不改变捕获的请求。
- [x] 写 `test_whole_page_identity_gate_precedes_every_body_access`：
  本页首项和第二项 body 均为会触发 canary 的 property，第二项 foreign chat；
  断言 touched=[]、history_unavailable、无 canary/foreign metadata/异常链。
- [x] 写 `test_pagination_failure_discards_earlier_page_without_retry`：
  第二页上游失败/重复 token/has_more 缺 token 的 bug class 参数组；
  断言无成功结果、无 retry、请求数量准确，__context__/__cause__ 为 None，caplog 无 canary。
- [x] 写 `test_discovery_counts_deleted_duplicate_unsupported_and_empty_pages`：
  删除项不读 body；重复 ID 只处理一次；已知资源在空 query 下可提供无预览 ID；
  未知 type 不释放原 type，计 unsearchable；空页 has_more=true 仍在三页预算内前进。
  断言各计数和 content coverage，不能借未知类型增加检索。
- [x] 写 `test_budget_and_cancellation_do_not_release_partial_work`：
  多字节正文累计越过 2 MiB 时不再处理后续项，stop_reason=work_limit/scan_complete=false；
  预算恰好等于总工作且末页耗尽时为 source_exhausted/true。
  取消使用 Event：请求前、响应后及页间触发均丢弃结果且不继续分页。
- [x] 写 `test_history_error_classification_is_safe`：
  真实 RawResponse 的 HTTPStatus.TOO_MANY_REQUESTS 和业务限流 ->rate_limited；
  既有授权代码及 230073/231203/231204 ->history_unavailable；
  坏 envelope、超 50 项、坏 has_more/token、其他上游或 transport 异常 ->upstream_error。
  身份/时间/deleted/msg_type 类型错误 ->history_unavailable。
- [x] 最小可导入后运行 `uv run --no-sync python -m pytest tests/integrations/test_feishu_message_search.py -q`，
  取得行为 RED。
- [x] 实现单次 client 构造、固定 ListMessageRequest，限制 token_types 为 TENANT；
  取消检查、应用身份匹配与有效 UTF-8 scope 在请求前完成。局部 set 管 token/ID。
  每页先验证所有身份与闭合毫秒边界 start*1000 <= create_time <= end*1000，再逐项处理。
  未知类型计不可搜索但不返回；已知资源/merge_forward/system 不访问 body。
- [x] raw 是 str 且字符数 <= 单条字节上限才编码 UTF-8，避免超大字符串的额外完整编码；
  单条不合规计不可搜索。合规 raw 按实际 UTF-8 字节扣累计预算，normalize 之前先检查预算。
  首次可见的 live known 类型在空 query 下计 matched；非空只对安全全文命中计 matched；
  最多保留 inputs.limit 个 Item，但继续有界扫描以得到真实 matched_count。
- [x] 完全完成所有项且 has_more=false 时 source_exhausted；预算阻止剩余工作时才 work_limit，
  三页仍 has_more 时 page_limit。scanned_count 只统计逐项处理阶段已到达的项。
  每次正文处理前和最终释放前复核取消，任何后续页失败不返回早先暂存结果。
- [x] 新测试及 S8b-1 `test_feishu_message_read.py`/`test_feishu_message_lookup.py` GREEN 后提交：
  `feat(feishu): search guarded current-chat history`。

### Task 4: 注册工具、受控结果与安全遥测

**Files:** 创建 `integrations/feishu/tools/feishu_search_messages_tool/__init__.py`、
`integrations/feishu/tools/feishu_search_messages_tool/validation.py`、
`integrations/feishu/tools/feishu_search_messages_tool/results.py`、
`integrations/feishu/tools/feishu_search_messages_tool/tool.py`、
`tests/integrations/test_feishu_search_messages_tool.py`；修改 `tests/tools/test_telemetry.py`。

**Interfaces:**
`prepare_search_input(payload: dict[str, Any], resolved_integrations: dict[str, Any])
-> tuple[dict[str, Any], str | None]`；
`search_success(result: FeishuSearchResult) -> ToolExecutionResult`；
`search_failure(code: FeishuSearchErrorCode) -> ToolExecutionResult`；
`FeishuSearchMessagesTool.run(self, *, start_time: int, end_time: int,
query: str = "", limit: int = 10, context: AgentToolContext) -> ToolExecutionResult`。
入口 `feishu_search_messages = tool(FeishuSearchMessagesTool(), surfaces=(ToolSurface.ACTION,))`。

- [x] 写 `test_real_executor_rejects_injected_authority_before_credentials`：
  真实 RegisteredTool/execute_tool_calls 覆盖额外 target/chat/token/page 参数、bool/future window、
  未验证配置、其他平台、shell/缺失 context。断言 credential/data GET 调用为零；
  direct run 同样拒绝。校验先 input/runtime scope，后 cancel/credentials/client。
- [x] 写 `test_search_result_bounds_escaped_json_and_keeps_details_identical`：
  20 个包含可转义字符的安全 600 字符 Item 触发编码裁剪；
  断言 len(content)<=20000、json.loads(content)==details、所有项完整、
  returned_count==len(items)、results_truncated=true、complete=false；
  limit 截断也不隐藏 matched_count。无 page token/query/raw canary。
- [x] 写 `test_complete_flags_do_not_turn_an_empty_partial_scan_into_no_matches`：
  complete 精确由 scan_complete、unsearchable_count==0 和 !results_truncated 合取；
  0 items、扫描未耗尽或跳过正文均不得宣称完整。
- [x] 写 `test_unexpected_tool_failure_reports_only_an_unchained_exception_type`：
  credentials 抛含 canary 的 RuntimeError，输出 code-only upstream_error，
  report_run_error 收到 RuntimeError("RuntimeError")，无敏感 context/cause。
- [x] 运行 `uv run --no-sync python -m pytest tests/integrations/test_feishu_search_messages_tool.py -q`，取得行为 RED。
- [x] 实现 metadata：name/source/requires/surfaces/tags、requires_approval=false、parallel_safe=true、
  accepts_runtime_context=true、非空用例及准确 schema；只 required start_time/end_time，
  additionalProperties=false。log_omitted_input_fields=("query","start_time","end_time")。
  声明 TOOL_MODULES=("tool",)，facade 只导出 TOOL_MODULES/feishu_search_messages。
- [x] prepare 使用 normalizer 和既有 scope candidacy；run 重新捕获 UTC 秒并规范输入，
  冻结 inputs/scope 后不再读可变 session。既有 FeishuReadError 的授权失败映射 authorization；
  凭据 app_id 与 scope 一致性由 client 再核对，不注入 model 可控秘密。
- [x] 实现设计 §8 的全部成功字段。每个安全 Item JSON 编码一次；
  固定 metadata JSON 编码一次，并为动态 returned_count/results_truncated/complete 及括号逗号
  预留最长形式的空间。选可容纳的完整 item 前缀，join 编码片段形成最终 JSON；
  不反复编码同一个完整 payload，不截断外层 JSON。固定 metadata 已超预算或编码失败时安全失败。
  details 使用完全相同值；content_complete=(unsearchable_count==0)。
- [x] 六类失败使用固定文案，只有 source/status/error_type/error；意外异常在 except 后只以
  安全 type name 报告，不把原 exc 附到 Sentry。正常权限/取消/上游失败不报原始 SDK 异常。
- [x] 新增 _feishu_search_case()、_TOOL_FAILURE_CASES 项和 _MIGRATED_TOOL_NAMES 项；
  延用正常遥测断言，对新 search case 增加安全 type/无链断言，不降低既有覆盖门禁。
- [x] 运行 `uv run --no-sync python -m pytest tests/integrations/test_feishu_search_messages_tool.py tests/tools/test_telemetry.py tests/tools/test_description_contract.py -q -k "feishu or coverage or ratchets or registry_floor"`；
  另运行 `uv run --no-sync python -m pytest tests/tools/test_registry.py tests/tools/test_registry_index.py -q`，GREEN 后提交：
  `feat(feishu): expose bounded message search action`。

### Task 5: 网关可见性、真实 runner 隔离与用户说明

**Files:** 修改 `integrations/feishu/action_prompt.py`、`docs/messaging/feishu.mdx`、
`tests/core/agent/prompts/test_gateway_channel_prompt.py`、
`tests/core/agent_harness/test_gateway_channel_tools.py`、
`tests/core/agent_harness/test_channel_turn_isolation.py`。

**Interfaces:** 保持 `feishu_action_prompt_fragment(context: ActionPromptContext) -> str` 签名；
复用 ActionTurnRunner/TurnSnapshot/DefaultToolProvider，无 core/harness 产品改动。

- [x] 写 `test_search_prompt_describes_only_offered_bounded_current_chat_search`：
  search offered 才出现；说明 window、limited scan、untrusted、complete=false 不能作无匹配结论、
  thread replies/resources 不覆盖、找到 ID 后只能在 reader offered 时调用 reader。
  只有旧 write/read offered 时输出保持旧测试期望，shell/非 Feishu 不出现 search；
  不提前提及未实现 members/reaction。
- [x] 扩展真实发现与 provider runner：normal/precomputed/custom 均按冻结 surface/platform
  限制 search；仅 ACTION，不在 INVESTIGATION；强行工具调用且坏 chat scope 时无 credential/GET。
  新工具是已存在 integrations.feishu.tools 下面的包，不修改 registry root/发现算法。
- [x] 写 `test_search_scope_and_window_are_frozen_across_session_reuse` 和
  `test_concurrent_searches_do_not_share_windows_or_previews`：
  ScriptedLLM 实际发工具调用，经 runner + 新 offline SDK probe；
  同一 session 两个顺序 turn、两个并发聊天的 app/chat/window 和安全预览保持各自值。
  中途修改 session cache 不改变已捕获输入，shared resources 不被写回。
  并发使用 Barrier(timeout=10)、future.result(timeout=30)、finally abort/cancel，不用 sleep。
- [x] 运行 `uv run --no-sync python -m pytest tests/core/agent/prompts/test_gateway_channel_prompt.py tests/core/agent_harness/test_gateway_channel_tools.py tests/core/agent_harness/test_channel_turn_isolation.py -q`，取得新行为 RED。
- [x] 增加包内独立搜索 prompt 片段，按精确 offered 名判断；不修改通用渠道门禁、写审批、
  S8b-1 文案或 morning-report。有限扫描不足时可在用户请求范围内缩小窗口，
  不自动扩大窗口或改用跨聊天/全局检索。
- [x] 用户文档添加自然语言查找示例、空关键词 ID 发现、最多 7 天/150 条的范围、
  脱敏预览、不包含话题回复和权限失败前置。已知 ID 读取的原行为说明保持；
  把旧“没有搜索能力”句改成单条读取和新搜索各自职责。不写内部模块/端点/历史 bug。
- [x] 运行 `uv run --no-sync python -m pytest tests/core/agent/prompts/test_gateway_channel_prompt.py tests/core/agent_harness/test_gateway_channel_tools.py tests/core/agent_harness/test_channel_turn_isolation.py -q`，
  GREEN 后提交：`feat(feishu): guide and isolate offered message search`。

## 3. 最终本地检查、独立审查与交付

以下命令已完成本地检查；mypy 的两处字符串类型收窄问题已修复并复验通过。按 CI.md 与现有 PathRule，
integrations 运行 focused Feishu 包；改动测试同时直接运行。静态命令是 Makefile 的 Windows 等价：

```powershell
$env:PYTHONUTF8='1'
git status --short
git diff --check
uv run --no-sync python -m ruff check bootstrap config core gateway integrations infrastructure surfaces tools tests/
uv run --no-sync python -m ruff format --check bootstrap config core gateway integrations infrastructure surfaces tools tests/
uv run --no-sync python -m mypy bootstrap config core gateway integrations infrastructure surfaces tools
uv run --no-sync python -m pytest tests/integrations/ -k feishu -q
uv run --no-sync python -m pytest tests/core/agent/prompts/test_gateway_channel_prompt.py tests/core/agent/prompts/test_channel_prompt_context.py tests/core/agent/prompts/test_skill_channel_visibility.py tests/core/agent_harness/test_gateway_channel_tools.py tests/core/agent_harness/test_channel_turn_isolation.py -q
uv run --no-sync python -m pytest tests/shared/test_integrations_api_border.py tests/shared/test_tool_api_border.py tests/tools/test_harness_api_border.py tests/config/test_feishu_card_constants.py -q
uv run --no-sync python -m pytest tests/tools/test_registry.py tests/tools/test_registry_index.py tests/tools/test_telemetry.py tests/tools/test_description_contract.py -q
uv run --no-sync python .github/ci/check_imports.py
```

--no-sync 复用已存在开发环境，不修改依赖；若环境不完整先解决环境，
不把收集/权限错误当行为失败或成功。仅 format 触及文件；如有非本项既有静态失败，
比较基线并准确记录，不能修 unrelated 以扩大本项范围，也不能绕过门禁。

- [x] 五个任务完成后跑上述 local gate，记录准确 HEAD、命令和结果。
- [x] 按用户选择执行方式完成独立审查并修复。推荐 Native：当前会话逐项实现，
  末尾一次新鲜独立整分支审查；比逐任务重新传递这些紧密依赖的接口更直接。
  独立 reviewer 遵循执行技能的能力要求，审查输入类型、数据边界、预算、取消和真实 runner。
- [x] 更新 spec/plan/路线与 PR 描述，只勾选真实完成项；记录 live 结果或未执行状态。
- [x] 本地任务提交保留；显式推送当前功能分支，不向 main 或默认 origin/main upstream 推送。
- [x] 创建一个完整 S8b-2 PR，填写 .github/PULL_REQUEST_TEMPLATE.md（含 AI 使用披露和离线 demo），
  用 attach_artifact 附加到本聊天。不开独立输入/投影/客户端 PR，不捆绑 S8b-3/4。
- [ ] 每次 PR push 后 gh pr checks --watch；失败抓 gh run view --log-failed，
  修根因、focused 复验、push 后重新跟踪。处理所有有效 review thread，
  按 CONTRIBUTING.md 触发 Greptile re-review，达到准确 HEAD 的 5/5 和绿色必需检查。
- [ ] 真实验收未执行时保留待办；带限制合并需要本项明确接受限制及 merge 指令，不复用 S8b-1 豁免。
- [ ] 获准合并后监控准确 merge SHA 的 main CI、完整 Python/JS CodeQL 与 release；
  新失败修复或回退，CodeQL 对照当时 main 基线，不 dismiss/清理旧告警。
  Release guard 跳过不表述为发布，也不表述为网关部署。

回退使用本项明确的 revert/fix 提交，保持旧 read/send/reply 与通用门禁；
不 reset/force push 或丢弃别人的工作。

## 4. 真实验收授权与执行记录（权限阻塞）

沿用设计 §11：新批准的 15 分钟窗口、明确聊天应用/当前测试群，
最多 6 次历史数据 GET、最多 1 次后续 S8b-1 GET；token 按需，独立计数，无 retry。

一位用户即可发固定文本关键词消息。第一调用空 query 发现 ID，后续 reader 验证；
第二调用限定窗口/关键词验证命中预览。仅通过真实 RegisteredTool/executor 调用，
不启动普通网关或模型、不发送回复、不创建/删除消息、不改权限或发布应用。
真实权限不足则停止并记录安全失败，不能用 mock 替代现场成功。
真实分页或跨聊天没测则分别披露；SDK 记录不得提交 ID/正文/凭据。
普通部署网关是否加载本功能，仍是单独的端到端验收事实。

2026-10-01，用户明确回复“批准只读验收（推荐）”并发送固定关键词文本。
测试产品 HEAD 为 `f27d5df91033e14a681ca1f51480398e990ee05a`，该提交的 CI Gate 和所选检查已通过，
Greptile 5/5、两条评审线程均已解决。批准窗口：北京时间 20:32:48–20:47:48；20:33:36 已停止。

| 真实请求 | 次数 | 安全结果 |
| --- | --- | --- |
| 当前应用 token POST | 1 | HTTP 200 / API code 0 |
| 当前群历史 GET | 1 | HTTP 400 / API code 230027 |
| 发现 ID 后单条 GET | 0 | 未调用 |
| 第二次关键词搜索 | 0 | 未调用 |

真实 RegisteredTool/executor 返回 `status=failed, error_type=history_unavailable`，未释放消息结果。
权限拒绝后立即停止，没有重试、重定向或继续使用剩余预算；没有网关/模型启动、消息发送、权限变更或发布。
固定文本的 ID 发现、后续 reader、关键词命中和预览成功路径均保持未验。真实分页、跨聊天和跨操作者仍未验。
只记录计数、状态、时间和提交，没有凭据、消息 ID、正文或原始 SDK 响应。

零网络预检显示全局有效 catalog 未提供 Feishu 配置；实际凭据加载器与批准的 `.env` 应用/测试群一致。
受控执行器使用真实 catalog 的显式 env record 输入冻结配置，不修改全局 store 或 `.env`。
Ruling: 按用户批准的 `.env` 范围完成受控工具链验收；代价是无法据此证明普通网关解析本机配置或已部署本功能。
该配置前置和权限生效状态保留为现场待办；不扩大本 PR 为无关配置修复。

本轮授权已在首次拒绝后终止。只有权限核对并生效后的新授权窗口，或本项明确接受已披露限制及单独合并指令，
才能推进相应下一步；S8b-1 的接受限制/合并授权不适用于本项。记录更新提交继续按准确新 PR HEAD 关闭 CI/Greptile 门禁。

随后用户授权只读核对权限并自行登录。目标应用与 `.env` 一致；后台原有 `im:message:readonly` 已开通，
精确筛选 `im:message.group_msg` 原无结果。官方历史接口文档明确应用身份读取群历史还需此权限，
已补到 spec §2 和用户说明。用户自行开通后，刷新后台确认 `im:message.group_msg` 为应用身份、已开通，
并显示“当前修改均已发布”；用户同时开通了相关 include_bot 权限。Agent 没有更改权限或发布应用。
本次核对没有验收脚本的 token POST 或群消息 GET，后台开通状态不等于现场成功。
用户随后明确“新的只读窗口批准给你了”，批准第二轮：北京时间 21:21:18–21:36:18；
干净 HEAD `d70b6780c8c8883d1d764259d6790cc6cb11912f`，冻结查询区间 20:21:18–21:22:24，
同一应用/群和此前测试词。实际在 21:22:25 停止，退出 0，报告 `marker_not_uniquely_discovered`。

| 第二轮真实请求 | 次数 | 安全结果 |
| --- | --- | --- |
| 当前应用 token POST | 1 | HTTP 200 / API code 0 |
| 当前群历史 GET | 1 | HTTP 200 / API code 0；两条消息，has_more=false |
| 发现 ID 后单条 GET | 0 | 未调用 |
| 第二次关键词搜索 | 0 | 未调用 |

真实发现工具 `status=searched`：pages/scanned/matched/returned 为 1/2/2/2，unsearchable=0；
scan_complete、content_complete 和 complete 均为 true，results_truncated=false，stop_reason=source_exhausted。
覆盖为当前 chat 容器，话题回复和资源正文不包含。两条文本均命中固定词，ID 发现与安全预览现场成功。
临时脚本错误地要求固定词唯一，故停止选择步骤；这是验收脚本条件过严，不是产品或权限失败。
没有重试、继续消费剩余预算、普通网关/模型启动、发消息或更改权限。
本项同一 ID 后续读取/独立关键词命中仍未验，真实分页、跨聊天/操作者和普通部署网关仍未验。

临时脚本零网络修正为选取最新匹配文本，同创建时间保持上游顺序；人工数据检查重复词、同时间和缺失词。
下一轮提案复用第二轮的精确查询区间，无需用户再发送消息；采用 15 分钟、6 历史 GET / 1 单条 GET、
最多 3 token、失败即停和无重试，随后获用户单独批准。第二轮已终止，合并仍不在授权范围。
记录更新继续跟踪准确新 PR HEAD 的 CI/Greptile；未修改产品代码或全局配置。

第三轮用户明确“批准下一轮只读验收（推荐）”。批准窗口为北京时间 21:35:02–21:50:02，
测试干净 HEAD `d3dbfe89530036bf8177076c601b33987ca0b865`，其 CI 全绿、Greptile 5/5、零未解决线程。
查询固定 20:21:18–21:22:24，同一应用/群和先前固定文本；于 21:35:45 成功结束，退出 0，outcome=passed。

| 第三轮真实请求 | 次数 | 安全结果 |
| --- | --- | --- |
| 当前应用 token POST | 1 | HTTP 200 / API code 0 |
| 当前群历史 GET | 2 | 各 HTTP 200 / API code 0；各两条消息，has_more=false |
| 发现 ID 后单条 GET | 1 | HTTP 200 / API code 0；同一 ID / 固定文本断言通过 |

真实 RegisteredTool/executor 的空 query 发现 → 单条读取 → 独立关键词搜索三步无错误，
搜索均返回 searched、读取返回 read；最新命中选择成功，关键词搜索再次找到同一 ID 的安全预览。
两次搜索各 pages=1、scanned/matched/returned=2/2/2、unsearchable=0；
scan_complete/content_complete/complete=true，results_truncated=false，stop_reason=source_exhausted。
content/details 一致性通过；覆盖仍为当前 chat 容器，内容为 untrusted，不包含话题回复/资源正文。
没有重试、重定向、额外消息请求、普通网关/模型启动、发消息、权限变更或应用发布。
仅保留安全计数、断言、提交和时间，未提交凭据、正文、真实 ID 或 SDK 原始响应。

文本链路现场验收已通过；真实多页、跨聊天/操作者、post/卡片搜索和普通网关配置解析/部署仍未现场验证。
全局有效 catalog 缺 Feishu 配置的前置仍保留，不以显式 env record 的受控执行器证明普通部署可用。
第三轮授权已成功执行完毕；用户随后明确“接收现场限制，合并吧”，接受本项已披露的真实多页、
跨聊天/操作者、post/卡片搜索、普通网关配置解析/部署未验，并批准合并。不能将接受限制升级为现场通过，
也不修复全局 catalog 前置或授权部署、发消息/权限修改；不是复用 S8b-1 豁免。
此次仅记录更新，按 CI.md §0 检查非运行 diff 和 git status，推送后核对准确新 HEAD 的 CI/Greptile。
准确最终 HEAD 满足 CI、Greptile 5/5 与零未解决线程后合并；继续观察准确 merge SHA 的 main CI、
完整 Python/JS CodeQL 与 release，失败须修复或回滚后再报告交付结果。

## 5. 覆盖自审和执行交接

| 设计条款 | 实施责任 |
| --- | --- |
| §1/3 单一有界搜索与既有身份 | 五任务边界、Task 3/4 |
| §4 schema、时间、工作与输出预算 | Task 1/3/4 |
| §5 冻结宿主/聊天/应用、TENANT、无替代检索 | Task 3/4/5 |
| §6 页内身份优先、token/ID 去重、取消 | Task 3；Task 5 真实 runner |
| §7 脱敏后匹配、Unicode、预览及资源限制 | Task 2/3 |
| §8 计数、完整性、全次失败、安全错误和 JSON | Task 3/4 |
| §9 所属模块、轻 facade、仅 offered prompt、docs | 文件地图、Task 4/5 |
| §10 离线 SDK、并发隔离、边界与 CI/review | Tasks 1–5、最终门禁 |
| §11 live 限制 | 本计划 §4，单独授权 |
| §12 逐项推进与结束标准 | 本计划交付门禁，S8b-3/4 不在此注册或实现 |

计划自审检查 spec 覆盖、步骤粒度、签名/字段一致性、五项 Review Focus 和长度。
时间/预算恰好耗尽的标志与 Unicode 命中窗口已经明确为实施细节；
没有扩大聊天、内容、权限或共享接口。新增 search_input.py 是输入职责拆分，
避免客户端反向导入会注册 tool 的 package。

用户已选择 Native；Task 1–5 分别提交为 22bfb14、eaf6d19、1ec6c32、0b76821、168ea94。
最新 focused 检查为 663 passed、41 skipped（既有描述契约隔离项 #5498）；lint、格式及导入边界通过。
mypy 初次报告两处 Any | None 不能传入 str 的错误，显式类型收窄后复验 2014 个源文件通过。
账户额度曾阻止 GitHub 只读查询和独立审查启动；用户指示继续后额度恢复，main 仍为 837124a。
独立整分支审查覆盖 837124a..6879e27，未发现 Critical/Important 运行时问题。
两处文档事实纠正随计划内状态更新完成；PR #45 已创建并附加本聊天。
产品 HEAD f27d5df 的必需 CI 全绿、Greptile 5/5、零未解决线程。真实 API 只读验收已获准并执行三次：
首次权限拒绝，第二次因重复词停止临时脚本；第三次文本发现 ID / 读取同一 ID / 独立关键词命中链路成功，详见 §4。
未运行普通网关。用户已单独接受剩余现场限制并批准合并，准确最终 HEAD/merge SHA 检查仍须闭环。
新真实请求、权限变更和合并保留各自授权边界，不重复请求已批准的设计和实施。

审查暂未判断的边界已裁决：真实权限与部署只能现场建立；SDK 解析前的响应分配不受本项正文预算限制；
全局搜索/OAuth/索引/话题展开/成员/reaction 继续留给独立子项；既有同聊天进度提示可能显示关键词，
本项省略 query 的契约适用于工具结果、日志和错误，不扩展修改通用 observer。

PR #45 的 Greptile 对 56ae02a 给出 4/5，并发现富文本提及 ID/姓名进入匹配和预览的有效 P1。
test_rich_text_mentions_cannot_match_or_enter_search_previews 已在修复前 RED，
证明身份/提及 canary 可匹配并输出；跳过 at 节点和元数据键后 GREEN，投影与既有脱敏合计 11 项通过。
修复后的完整 lint/格式/mypy（2014 个源文件）通过；focused groups 为 263 + 38 + 21 + 341，
合计 663 passed、41 既有描述契约隔离项跳过；三个 import 检查通过。准确新 HEAD 的 CI/Greptile 继续按 PR 跟进。

对 c255bba 的 CI 已全部通过。Greptile 确认提及边界修复后，仅剩新包 facade 的模块 docstring 规范项。
Ruling: 采用根 AGENTS 的 imports/__all__ only 规则，移除 docstring，而不采用添加工具指南允许 docstring 的较宽表述；
代价仅为少一行模块说明，工具导出/真实发现路径通过现有测试验证。没有为此添加镜像规范断言。
该修正后完整 lint/format/mypy 通过；Feishu 263、registry/index 50、gateway discovery 6 项通过。
