# Feishu S8b-1 Single-Message Read Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Recommended for this plan: native execution in the current session, followed by one independent whole-branch review; execution choice and plan approval are still pending.

**Goal:** Let the Feishu action agent inspect one known message in its current chat without releasing foreign-chat or unsanitized content.

**Architecture:** A vendor-owned tool resolves immutable read scope from the frozen turn view, uses a dedicated SDK client for one guarded GET, and returns bounded sanitized JSON. Existing reply preflight stays metadata-only. A pure scrubber moves to the infrastructure layer before reuse, preserving gateway attachment and approval behavior.

**Tech Stack:** Existing Python/uv environment, lark-oapi 1.7.3, pytest, Ruff and mypy; no dependency additions.

**Spec:** [S8b-1 design](2026-09-30-feishu-s8b1-read-message-design.md): core design approved by the user on 2026-09-30; the narrow §12 gate refinement remains unapproved.

- Date: 2026-09-30
- Status: Plan prepared for review only; design §12 gate refinement and execution remain unapproved. User requested no execution on 2026-09-30.
- Worktree: `C:\Users\23033\Desktop\opensre2\.worktrees\feishu-s5b-feedback`
- Branch: `codex/feishu-s8b1-read-message-spec`
- Planning baseline: `7bd738090d98f20a8fbe39cedc0ece487d5a35ee`; clean before this plan/approval record

## Global Constraints

- Current-chat-only access: exact `_gateway_platform == "feishu"` and a non-empty string `_gateway_chat_id` from `AgentToolContext.resolved_integrations`, plus the existing frozen runtime `ActionPromptContext` identifying a Feishu gateway; never infer from text, default targets, outbound allowlists or a mutable session cache.
- Tool name `feishu_get_message`, `source="feishu"`, `requires=["feishu"]`, `surfaces=(ToolSurface.ACTION,)`, `side_effect_level=SideEffectLevel.READ_ONLY`, `requires_approval=False`, `accepts_runtime_context=True`, `parallel_safe=True`, `log_omitted_input_fields=("message_id",)`. Proposed design §12 refinement adds `tags=(GATEWAY_ONLY_TOOL_TAG,)` and a generic final tag gate; approval is pending.
- Only public input: `message_id`, stripped, non-empty, maximum 256 characters; reject additional properties.
- At most one message GET; `user_id_type="open_id"`, `card_msg_content_type="user_card_content"`, a 10-second per-HTTP-request timeout, existing chat-app tenant-token identity.
- Require one item, exact message/chat IDs and `deleted is False` before touching its body. Reject merge-forward/multi-item expansion. Cancelled reads do not release content.
- The GET is ID-only: a foreign body can enter trusted SDK memory before rejection. Do not claim the boundary prevents upstream retrieval; it prevents content disclosure.
- Body JSON object, maximum 256 KiB UTF-8 before parsing, processing depth 64 and 8,192 nodes; maximum returned representation 12,000 Unicode characters with explicit `json_prefix`/`truncated` semantics.
- Key redaction, decoded-string secret scrubbing, then configured per-call masking precede serialization/truncation. Do not return raw SDK objects, raw exception/provider details, foreign metadata or unsanitized duplicates.
- Existing S8a approvals, targets, metadata-only lookup, delivery certainty, morning-report and S7 surface isolation remain unchanged. No history/search/member/reaction tools, user OAuth, cross-chat read allowlist, new env vars, dependencies or workflow changes.
- Reuse this worktree; preserve unrelated changes. Edits use `apply_patch`; Python/opensre commands use `uv run`; on Windows set `PYTHONUTF8=1`. No live API operations or gateway start/restart without fresh explicit authorization.
- Before structural work read root/integrations/infrastructure/gateway/gateway-core AGENTS, infrastructure README, architecture/tool-placement/adding-tools guides, CI.md, the PR template, and any AGENTS in actual touched trees. Read harness/prompt AGENTS before modifying their tests/wiring.
- Imports use the `core.tool` / `core.tool_framework` APIs; no new API-border exceptions, compatibility forwarding functions, heavy package facades, Protocol stub bodies or CodeQL suppressions. Shared limits live in `config/constants/feishu.py` and are re-exported.

## Review Focus

These concrete input classes supplement the design's broader contracts and are assigned below:

1. Pydantic vs dictionary integration views, explicit `connection_verified=False`, non-string metadata, and a shell carrying valid gateway markers must fail closed at the appropriate scope/host boundary — Tasks 2, 5 and 6.
2. UTF-8 bytes vs Unicode characters, and exact depth/node boundaries, must not let CJK bodies or deeply nested JSON exceed processing limits — Task 3.
3. Escaped secrets and credentials straddling the output prefix must be sanitized before truncation; `details` must never retain the original — Tasks 3 and 5.
4. An upstream failure or wrong-chat response carrying a diagnostic/body canary must not disclose it or cause another request — Task 4.
5. Cancellation and session/context mutation during an in-flight GET must discard cancelled content and preserve the captured chat/app identity — Tasks 4 and 6.

## 1. File map and contracts

| File | Responsibility |
| --- | --- |
| `infrastructure/safety/masking/secrets.py` (new) | Canonical unchanged `scrub_secrets(text: str) -> str` and its private patterns |
| `gateway/core/attachments/inline.py` | Import the scrubber internally; remove its old definition and public export |
| `gateway/core/middleware/approvals.py` | Import the canonical scrubber; preserve preview rendering |
| `config/constants/feishu.py`, `config/constants/__init__.py` | Read limits and read-specific upstream code constants; facade exports only |
| `config/constants/tool_policy.py` (new) | Proposed generic `GATEWAY_ONLY_TOOL_TAG = "gateway_only"`, re-exported by the constants facade |
| `integrations/feishu/read_types.py` (new) | Stdlib-only immutable read records and safe error codes; no client/parser imports |
| `integrations/feishu/read_scope.py` (new) | Pure current-chat scope resolution; no outbound target logic or I/O |
| `integrations/feishu/message_content.py` (new) | Bounded JSON parsing, sanitization and output representation |
| `integrations/feishu/message_read.py` (new) | Single SDK GET, response verification, cancellation and safe outcomes |
| `integrations/feishu/tools/feishu_get_message_tool/{__init__,tool,validation,results}.py` (new) | Public tool contract, validation, orchestration and read-only result mapping |
| `integrations/feishu/action_prompt.py` | Separate offered read guidance from existing extra-write guidance |
| `core/agent_harness/tools/action_tools.py` | Proposed opt-in generic final gateway-only tag check, preserving the source check and untagged behavior |
| `docs/messaging/feishu.mdx` | User-facing permissions, known-ID usage and content/resource limitations |
| `.superpowers/specs/2026-09-12-feishu-capability-completion-design.md` and the S8b-1 design/plan | Truthful approval, implementation and delivery records |

New test files: `tests/masking/test_secrets.py`,
`tests/integrations/test_feishu_read_scope.py`,
`tests/integrations/test_feishu_message_content.py`,
`tests/integrations/test_feishu_message_read.py`,
`tests/integrations/test_feishu_get_message_tool.py`, and
`tests/integrations/feishu_read_support.py` (offline SDK fixtures/probes only).

Modify only these existing tests where needed:
`gateway/tests/discord/test_attachments_inline.py`,
`tests/core/agent/prompts/test_gateway_channel_prompt.py`,
`tests/core/agent_harness/test_gateway_channel_tools.py`, and
`tests/core/agent_harness/test_channel_turn_isolation.py`.
Existing attachment/approval, masking, Feishu and border suites supply regressions.
Do not add a generic registry root, change `INTEGRATION_TOOL_PACKAGES`, or grow the
existing write-only `integrations/feishu/tools/results.py`.

**Approval delta:** Design §12 documents why existing S7 shell behavior requires
the opt-in tag check. Availability returns integration-scope candidacy only;
the final filter has the actual frozen host surface. This is the sole proposed
adjacent contract extension, explicitly awaiting future approval with this plan.

The shared leaf `read_types.py` defines:

- Frozen `FeishuReadScope(app_id: str, chat_id: str)`.
- Frozen `FeishuMessageContent(body_content: str, body_format: Literal["json", "json_prefix"], truncated: bool, redacted: bool)`.
- Frozen `FeishuReadMessage(message_id: str, chat_id: str, msg_type: str, content: FeishuMessageContent)`.
- `FeishuReadErrorCode(StrEnum)` with exactly nine categories: `VALIDATION`, `AUTHORIZATION`, `CANCELLED`, `MESSAGE_UNAVAILABLE`, `UNSUPPORTED_CONTENT`, `CONTENT_TOO_LARGE`, `CONTENT_TOO_COMPLEX`, `RATE_LIMITED`, `UPSTREAM_ERROR`. Their values are the lowercase design codes.
- `FeishuReadError(Exception)` with `__init__(self, code: FeishuReadErrorCode) -> None`, carrying only that safe code, with no original exception/response attached.

These records avoid a parser ↔ client ↔ result import cycle. Do not import a
client/parser from `read_types.py`, including through TYPE_CHECKING or lazy imports.

## 2. Task-by-task implementation

For each new API, first make its typed interface minimally importable without
implementing the behavior, then obtain a behavioral RED. Collection/import/
environment failures do not count as RED. Never commit a placeholder implementation.
Behavior-preserving moves start with GREEN characterization. All commands below
are future implementation checks, not validation already performed.

### Task 1: Canonical scrubber with gateway behavior preserved

**Files:** Create `infrastructure/safety/masking/secrets.py` and
`tests/masking/test_secrets.py`; modify `gateway/core/attachments/inline.py`,
`gateway/core/middleware/approvals.py` and
`gateway/tests/discord/test_attachments_inline.py`.

**Interfaces:** Produces `scrub_secrets(text: str) -> str` at the canonical
infrastructure path. Existing `budgeted_section(...)` and `arguments_preview(...)`
signatures and rendered outputs do not change.

- [ ] Add `test_scrubber_preserves_existing_secret_replacements`, initially importing the old gateway function. Assert unchanged benign Markdown and the exact existing replacements for private-key, Slack/app-token, AWS-key, Bearer and generic assignment inputs. Keep one characterization test for this move.

```python
assert scrub_secrets("status: healthy") == "status: healthy"
assert scrub_secrets("Bearer abcdefghijklmnopqrst") == "Bearer [REDACTED]"
assert scrub_secrets("token=abcdefgh") == "token=[REDACTED]"
```

- [ ] Run `uv run python -m pytest tests/masking/test_secrets.py gateway/tests/discord/test_attachments_inline.py gateway/tests/runtime/test_approval_arguments_preview.py -q`; obtain GREEN on the pre-move code.
- [ ] Move the pure function and patterns unchanged, migrate both product consumers and tests, remove the old public `scrub_secrets` export/definition and unused `re` import. Leave the masking package facade import-only; no forwarding shim is needed.
- [ ] Run the same command plus `uv run python -m pytest gateway/tests/feishu/test_attachments.py gateway/tests/slack/test_attachments.py -q`; expect GREEN with identical preview/attachment behavior.
- [ ] Search all imports with `rg -n 'scrub_secrets' gateway infrastructure tests`; verify all callers use the canonical path, then commit the listed files as `refactor(safety): share canonical secret scrubber`.

### Task 2: Frozen read scope and leaf read records

**Files:** Create `integrations/feishu/read_types.py`,
`integrations/feishu/read_scope.py`, `tests/integrations/test_feishu_read_scope.py`
and `config/constants/tool_policy.py`; re-export the tag in
`config/constants/__init__.py`.

**Interfaces:** Consumes `availability_view(resolved_integrations)` through
`core.tool`; produces
`resolve_read_scope(resolved_integrations: dict[str, Any]) -> FeishuReadScope`,
raising `FeishuReadError(AUTHORIZATION)` on invalid/unavailable scope.
`is_read_available(sources: dict[str, Any]) -> bool` uses the same resolver for
integration-scope candidacy; it cannot inspect the actual host surface.
`resolve_runtime_read_scope(resolved_integrations: dict[str, Any], resources: Mapping[str, Any]) -> FeishuReadScope`
additionally requires an `ActionPromptContext` from the existing
`ACTION_PROMPT_CONTEXT_RESOURCE`, with exact gateway/Feishu surface/platform.
Missing or malformed resources raise the same safe authorization error.
`Mapping` is imported from `collections.abc`. The proposed shared tag is
`GATEWAY_ONLY_TOOL_TAG = "gateway_only"`.

- [ ] Add `test_read_scope_requires_typed_current_chat_and_verified_chat_app`: exercise a real `FeishuConfig` and its dictionary equivalent; assert `FeishuReadScope("cli_test", "oc_current")`, then reject explicit unverified credentials, missing/blank credentials, malformed metadata and wrong/missing platform. Check that no default/outbound-only configuration grants a scope.

```python
assert resolve_read_scope(valid_view) == FeishuReadScope("cli_test", "oc_current")
assert is_read_available(unverified_view) is False
assert is_read_available(outbound_only_view) is False
```

- [ ] Add `test_runtime_scope_rejects_shell_with_gateway_markers`: retain the valid Feishu integration view while changing only the frozen prompt resource to shell, wrong platform, missing or malformed; assert authorization failure. A gateway/Feishu resource and the valid view return the same immutable scope. This tests runtime authority separately from integration candidacy.
- [ ] Run `uv run python -m pytest tests/integrations/test_feishu_read_scope.py -q`; require an assertion failure from the importable unimplemented resolver, not ImportError.
- [ ] Implement the immutable records, scope/runtime resolvers and proposed tag constant. Preserve string types, copy stripped app/chat values once and never retain the mutable integration map. Default locally classified verification follows `availability_view`; an explicit false flag is never overwritten. Availability catches the safe domain rejection and performs no credential loading/network I/O. Import the existing prompt-context leaf without changing that resource's contract.
- [ ] Run the test again; expect GREEN and no captured/logged configuration secrets.
- [ ] Commit the listed files as `feat(feishu): define current-chat read authority`.

### Task 3: Bounded sanitized message-body representation

**Files:** Create `integrations/feishu/message_content.py` and
`tests/integrations/test_feishu_message_content.py`; modify
`config/constants/feishu.py` and `config/constants/__init__.py`.

**Interfaces:** Consumes Task 1's scrubber and Task 2's leaf records/errors;
produces `normalize_read_content(raw_content: str) -> FeishuMessageContent`.
Uses existing `redact_sensitive`, `MaskingPolicy.from_env()` and a fresh
`MaskingRules` instance per call; no shared visibility/body/placeholder cache.

Named limits:

| Constant | Value |
| --- | --- |
| `FEISHU_MESSAGE_READ_MAX_ID_CHARS` | 256 |
| `FEISHU_MESSAGE_READ_MAX_INPUT_BYTES` | `256 * 1024` |
| `FEISHU_MESSAGE_READ_MAX_OUTPUT_CHARS` | `12_000` |
| `FEISHU_MESSAGE_READ_MAX_DEPTH` | 64 |
| `FEISHU_MESSAGE_READ_MAX_NODES` | `8_192` |
| `FEISHU_MESSAGE_READ_TIMEOUT_SECONDS` | `10.0` |

- [ ] Add `test_content_sanitizes_decoded_json_before_prefix`: use a JSON object with a secret-bearing key and a decoded Bearer/private-key string crossing the 12,000-character prefix. Assert `len(result.body_content) <= 12_000`, `truncated is True`, `body_format == "json_prefix"`, `redacted is True`, and no credential value or cut credential fragment remains.

```python
assert len(result.body_content) <= 12_000
assert result.truncated is True and result.body_format == "json_prefix"
assert result.redacted is True
assert "abcdefghijklmnopqrstuvwxyz012345" not in result.body_content
```

- [ ] Add `test_content_preserves_rich_text_and_card_nodes`: feed documented-shaped post and original 2.0 card objects; assert unfamiliar benign nodes survive, complete output parses, and `body_format == "json"` / `truncated is False`. Resource keys stay metadata; no fetch/vision callback exists in this module.
- [ ] Add `test_content_bounds_use_utf8_bytes_and_count_json_values`: pin CJK byte overflow, exact-boundary acceptance and over-limit depth/nodes. Root value has depth 1; containers and value leaves count as nodes, object keys do not. Use a bounded iterative preflight before recursive helpers. Test malformed/non-object/non-finite JSON as unsupported; parser recursion failure maps to `content_too_complex`.
- [ ] Add `test_configured_masking_uses_a_fresh_map_per_read`: enable the existing policy in the test environment, read different chats' identifier-bearing content, and assert each result contains only its own placeholders and no original identifier/mapping.
- [ ] Run `uv run python -m pytest tests/integrations/test_feishu_message_content.py -q`; obtain behavioral RED on the importable normalizer.
- [ ] Implement UTF-8 preflight, JSON-object parsing, complexity checks, ordered sanitization and one serialization. Compare sanitized data to the parsed original to set `redacted`; then discard originals. Reject invalid Unicode/non-finite numeric values safely. Convert parser exceptions to safe codes and raise domain errors outside the catch blocks so `__context__`/`__cause__` cannot retain the parser's original document. Return the bounded string prefix with explicit flags; catch neither by disabling validation nor by changing the configured limits.
- [ ] Run the same tests plus `uv run python -m pytest tests/masking/ -q`; expect GREEN.
- [ ] Commit the listed files as `feat(feishu): bound and sanitize read content`.

### Task 4: Single guarded SDK GET without content leakage

**Files:** Create `integrations/feishu/message_read.py`,
`tests/integrations/test_feishu_message_read.py`,
`tests/integrations/feishu_read_support.py`; extend read-specific upstream code
constants in `config/constants/feishu.py` and re-export them.

**Interfaces:** Consumes Tasks 2/3 and produces:

```python
read_current_message(
    *, app_id: str, app_secret: str, message_id: str,
    scope: FeishuReadScope, cancel_requested: Callable[[], bool],
) -> FeishuReadMessage
```

Failures raise only `FeishuReadError`. The test helper defines
`install_read_transport(monkeypatch: pytest.MonkeyPatch, responder: Callable[[GetMessageRequest], dict[str, Any]]) -> ReadTransportProbe`.
The probe exposes `requests: list[GetMessageRequest]`, `timeouts: list[float]`
and `app_ids: list[str]`. It intercepts real SDK transport/token acquisition and
returns realistic deserialized GET responses, using no real credential loader.

- [ ] Add `test_sdk_get_uses_original_card_format_and_tenant_identity`: intercept the real SDK transport and token acquisition with named test functions; deserialize an actual `GetMessageResponse` payload. Assert one GET, `user_id_type="open_id"`, `card_msg_content_type="user_card_content"`, timeout `10.0`, tenant token selection and exact requested message/chat in the returned record. Use `HTTPStatus.OK` in fake HTTP responses.

```python
assert len(probe.requests) == 1
assert probe.requests[0].card_msg_content_type == "user_card_content"
assert probe.requests[0].user_id_type == "open_id"
assert probe.timeouts == [10.0]
assert message.chat_id == "oc_current"
```

- [ ] Add `test_foreign_or_unverifiable_message_never_touches_body`: a sentinel property raises on body access. Exercise wrong chat/ID, absent or non-false deletion and multiple items; assert safe rejection, untouched body, zero normalizer calls and no second request. Explicit `merge_forward` rejects before content processing even with one item.
- [ ] Add `test_upstream_failure_does_not_leak_canary_or_retry`: upstream code/message and raised transport exceptions contain a canary; assert the safe error code and absence of the canary/foreign IDs in errors and captured diagnostics. No response/raw object is passed to telemetry.
- [ ] Add `test_cancellation_discards_completed_get` and `test_captured_scope_survives_mutation_during_get`: switch cancellation or mutate the originating view inside the intercepted GET; verify respectively no content release and continued comparison against the captured immutable scope.
- [ ] Run `uv run python -m pytest tests/integrations/test_feishu_message_read.py -q`; require behavioral RED from the importable client.
- [ ] Implement client construction, app-ID agreement, cancellation checks and one GET. Inspect identity/deletion before body/msg-type processing, then invoke Task 3. Convert caught upstream/parser exceptions to safe codes and raise domain errors outside those catch blocks so no original `__context__`/`__cause__` is attached. Missing access/membership/invisible/deleted upstream outcomes map to `message_unavailable`; reuse known rate-limit constants. Unknown/transport errors map to `upstream_error`. HTTP comparisons use `HTTPStatus`; no raw provider text is retained.
- [ ] Define `FEISHU_MESSAGE_READ_UNAVAILABLE_ERROR_CODES = frozenset({230050, 230110})` for GET-specific invisible/deleted cases and classify it alongside existing `FEISHU_AUTHORIZATION_ERROR_CODES`. Reuse `FEISHU_RATE_LIMIT_ERROR_CODES` and `HTTPStatus.TOO_MANY_REQUESTS` for rate limiting. Keep literals out of independent feature/test classification tables; do not change S8a's existing classification.
- [ ] Run the client tests plus `uv run python -m pytest tests/integrations/test_feishu_message_lookup.py -q`; expect GREEN and unchanged metadata-only reply preflight.
- [ ] Commit the listed files as `feat(feishu): verify single-message reads before release`.

### Task 5: ACTION tool validation, execution and safe results

**Files:** Create the four files under
`integrations/feishu/tools/feishu_get_message_tool/` and
`tests/integrations/test_feishu_get_message_tool.py`.

**Interfaces:** Consumes Tasks 2–4 and existing chat credential/cancel helpers;
produces `FeishuGetMessageTool` and decorated `feishu_get_message`.

- `normalize_message_id(value: object) -> str`, raising the safe validation code.
- `prepare_read_input(payload: dict[str, Any], resolved_integrations: dict[str, Any]) -> tuple[dict[str, Any], str | None]`.
- `read_success(message: FeishuReadMessage) -> ToolExecutionResult`.
- `read_failure(code: FeishuReadErrorCode) -> ToolExecutionResult`.
- `FeishuGetMessageTool.run(self, *, message_id: str, context: AgentToolContext) -> ToolExecutionResult`.

- [ ] Add `test_public_input_cannot_inject_scope_or_credentials`: invalid/missing/whitespace-only/overlong IDs and additional target/chat/token fields are rejected before GET. Normal preparation rechecks integration scope; direct invocation additionally checks the frozen prompt resource. A shell with valid gateway markers, a missing resource and default/outbound targets all cause zero GETs.
- [ ] Add `test_execution_matches_frozen_app_and_returns_only_safe_read_result`: execute through `core.tool.execute_tool_calls` and `RegisteredTool.from_base_tool` with intercepted SDK transport and a named fake credential loader. Assert changed resolved app identity causes zero GETs; success has the design's exact fields, `json.loads(content) == details`, no sender envelope/tenant/credential data and no S8a `sent`/certainty fields.

```python
assert result.is_error is False
assert json.loads(result.content) == result.details
assert result.details["content_trust"] == "untrusted"
assert result.details["resource_content_included"] is False
assert "sent" not in result.details and "certainty" not in result.details
```

- [ ] Add `test_error_and_truncated_details_cannot_retain_original_body`: assert the result and trace-facing details contain only the sanitized prefix on success, and no body/foreign metadata on failure. Verify the read does not request approval or dispatch any send/reply/reaction fallback.
- [ ] Run `uv run python -m pytest tests/integrations/test_feishu_get_message_tool.py -q`; require behavioral RED after the minimal tool interface is importable.
- [ ] Implement schema/metadata with the proposed gateway-only tag, shared availability and preparation, `resolve_runtime_read_scope` before credential loading, credential/app-ID agreement, cancellation and guarded client dispatch. Declare schema `maxLength=256`; trim the ID before the normalizer's length check and onward dispatch, including direct calls. Test runtime resources include the real frozen gateway/Feishu `ActionPromptContext`, as the actual runner supplies. Use a safe wrapper containing only an exception type for unexpected telemetry, never original details. Map errors in the read tool's own result module.
- [ ] Export only the decorated public tool from the lightweight package `__init__.py`. Discovery already walks `integrations.feishu.tools`; do not add a second root or register unimplemented capabilities.
- [ ] Run the tool tests plus `uv run python -m pytest tests/integrations/test_feishu_send_reply_tools.py tests/integrations/test_feishu_outbound_targets.py -q`; expect GREEN with write approvals/targets unchanged.
- [ ] Commit the listed files as `feat(feishu): expose current-chat message read tool`.

### Task 6: Real action wiring, prompt agreement and user guidance

**Files:** Modify `integrations/feishu/action_prompt.py`,
`core/agent_harness/tools/action_tools.py`,
`tests/core/agent/prompts/test_gateway_channel_prompt.py`,
`tests/core/agent_harness/test_gateway_channel_tools.py`,
`tests/core/agent_harness/test_channel_turn_isolation.py` and
`docs/messaging/feishu.mdx`.

**Interfaces:** Retain
`feishu_action_prompt_fragment(context: ActionPromptContext) -> str`;
read guidance uses only the actual offered tool names. Existing `ActionTurnRunner`,
provider, context and persona interfaces do not change. The existing final
`filter_action_tools_for_channel(tools: list[Any], context: ActionPromptContext) -> list[Any]`
signature stays unchanged; the proposed design §12 extension adds a generic
gateway-only tag predicate alongside its messaging-source predicate.

- [ ] Add a single characterization test recording the current send/reply/both write-fragment views before editing them; run the existing prompt suite and this test to obtain GREEN. Store fixed pre-change expectations, not values recomputed from the function under test.
- [ ] Add `test_read_only_feishu_prompt_declares_only_offered_read`: assert the assembled prompt includes known-ID/current-chat/untrusted/incomplete guidance, includes `feishu_get_message`, and does not claim send/reply/history/search/member/reaction capability. Write-only and unavailable views must not gain read promises.

```python
prompt = prompt_for("feishu", "feishu_get_message")
assert "feishu_get_message" in prompt
assert "feishu_send_message" not in prompt and "feishu_reply_message" not in prompt
assert "feishu_get_message" not in prompt_for("feishu", "feishu_send_message")
```

- [ ] Add `test_discovery_and_provider_paths_enforce_read_scope`: use real `get_registered_tools(ToolSurface.ACTION)` discovery, proving ACTION-only registration and valid-current-scope availability through the normal runner. Through normal and custom/precomputed providers, prove the final tag gate excludes the real registered reader from shell views even when gateway markers remain, while existing untagged send/reply tools remain offered there. Other channels' final source gate excludes it, and an invalid current context cannot cause a GET even when a provider offers it. Assert both the actual offered names and prompt promises.
- [ ] Add `test_read_scope_is_frozen_across_session_reuse` and `test_concurrent_feishu_reads_do_not_share_bodies` to the real runner isolation suite. Use a test-local `ReadMessageLLM(message_id: str)` that emits one explicit read call and then stops, separate frozen `TurnPlan` views, intercepted SDK responses and captured tool results. Assert each chat receives only its own content despite shared registries/providers and session-cache mutations. Synchronize with events/barriers, finite timeouts and `finally` cleanup; do not use sleeps or merely prove task creation is nonblocking.
- [ ] Run `uv run python -m pytest tests/core/agent/prompts/test_gateway_channel_prompt.py tests/core/agent_harness/test_gateway_channel_tools.py tests/core/agent_harness/test_channel_turn_isolation.py -q`; require behavioral RED for new read prompt/runtime contracts, keeping the write characterization GREEN.
- [ ] Implement the proposed generic tag check in the existing final filter, retaining the existing source predicate and behavior of untagged tools. Use the shared tag constant; no Feishu/tool-name branch, registry or mutable host marker. Implement a separate read fragment conditional on gateway/Feishu context and the offered name. Preserve rendered write-only content byte-for-byte; use private module text constants for long list entries to avoid implicit string concatenation alerts. Keep resource-copy behavior intact.
- [ ] Update the existing Feishu page with the required base/group permissions and publication/membership prerequisites, known-ID examples, current-chat-only scope, sanitized/truncated results and resource metadata-only limits. Explain that reads do not require a write approval; preserve all existing write-approval instructions. Omit internal SDK names/endpoints from user docs.
- [ ] Run the task's suite plus `uv run python -m pytest tests/core/agent/prompts/test_channel_prompt_context.py tests/core/agent/prompts/test_skill_channel_visibility.py -q`; expect GREEN. Commit the listed files as `feat(feishu): describe and isolate available message reads`.

## 3. Local validation and delivery checks

The path rules currently map `integrations/` to `tests/integrations/`, `gateway/`
to `gateway/tests/`, masking to `tests/masking/`, and changed tests to themselves.
This spans multiple areas; per CI.md, run their focused packages/contracts, not
`make test-cov`. Config limits additionally require their consumer and facade
contracts. Run this gate after all tasks; repeat only affected commands after fixes.

```powershell
$env:PYTHONUTF8='1'
git status --short
git diff --check
uv run python -m ruff check bootstrap config core gateway integrations infrastructure surfaces tools tests/
uv run python -m ruff format --check bootstrap config core gateway integrations infrastructure surfaces tools tests/
uv run python -m mypy bootstrap config core gateway integrations infrastructure surfaces tools
uv run python -m pytest tests/integrations/ -k feishu -q
uv run python -m pytest tests/masking/ -q
uv run python -m pytest gateway/tests/ -k 'attachment or approval or package_border or harness_behaviour_border or harness_api_border' -q
uv run python -m pytest tests/core/agent/prompts/test_gateway_channel_prompt.py tests/core/agent/prompts/test_channel_prompt_context.py tests/core/agent/prompts/test_skill_channel_visibility.py tests/core/agent_harness/test_gateway_channel_tools.py tests/core/agent_harness/test_channel_turn_isolation.py -q
uv run python -m pytest tests/shared/test_integrations_api_border.py tests/shared/test_tool_api_border.py tests/tools/test_harness_api_border.py tests/config/test_feishu_card_constants.py -q
uv run python .github/ci/check_imports.py
```

The Ruff/mypy commands are Windows equivalents of Makefile's required lint,
format-check and typecheck targets. Format only touched files on failure. The
scope commands include new tests, the canonical scrubber's existing masking
suite, S8a delivery/approval/config regressions and common gateway consumers.
Do not silence API-border failures by expanding an allowlist or adding a lazy
import. Fix the edge structurally.

No new non-Python runtime resource, packaging configuration or dependency is
planned; do not rerun S7's wheel/frozen-resource work as a routine extra gate.
If implementation evidence requires a new resource/packaging contract, revise
the scope and plan before making that change.

Current planning-stage validation is documentation-only under CI.md §0:
status/diff checks, relative-link checks, JSON-example checks and spec/plan
coverage review. No product/test implementation checks have run yet.
The saved documents passed the whitespace check, nine relative-link checks and
one JSON-example parse; all six implementation tasks remain unchecked.

## 4. Commit, review and rollback boundaries

- Keep the approved design, reviewed plan and roadmap records on this branch.
  Immediately before product edits, verify the plan's approval and execution
  choice, the actual HEAD/diff, and applicable directory instructions.
- The six task commits are local boundaries; push a completed coherent feature,
  not scaffolding or an intermediate tool that lacks its release checks.
- Recommended execution is native in this session because scope, client,
  content and result contracts depend closely on one another. After local gates,
  a fresh independent reviewer reviews the whole branch with the most capable
  available model, followed by remediation. If the user chooses subagent-driven
  execution, use its required skill and task/reviewer gates instead.
- Before push, self-review the full branch, verify only this approved scope
  changed, record actual command results and update statuses truthfully.
- Open one S8b-1 implementation PR using `.github/PULL_REQUEST_TEMPLATE.md`,
  including AI disclosure and offline demo evidence. Attach its URL to this
  Codex task. Do not publish a separate implementation PR for the pure scrubber
  move unless the scope is explicitly revised.
- After every push with an open PR, run `gh pr checks --watch`; on failure fetch
  `gh run view <run-id> --log-failed`, fix root causes and rerun the focused
  checks before pushing. Check latest reviews/unresolved conversations, follow
  CONTRIBUTING.md's Greptile re-review procedure, and reach 5/5 with no unresolved
  actionable comments. Do not equate a green check with completed review.
- Obtain an explicit merge instruction. Surface any unperformed live acceptance
  and obtain explicit user acceptance of that limitation before merging with it;
  no earlier S8a waiver applies to S8b-1.
- After merge monitor the exact merge commit's main CI, full Python/JS CodeQL
  and release workflows. Compare alerts with the then-current main baseline;
  resolve new S8b-1 alerts without dismissals or unrelated baseline cleanup.
  Fix or revert post-merge failures. Report an unchanged Release guard skip or
  absence of a triggering run accurately; never claim publication from a skip.
- Before publication, rollback consists of removing/reverting this branch's
  feature commits without resetting/discarding unrelated work. The scrubber move
  can stand independently if all canonical consumers/tests remain; reverting it
  requires migrating those consumers back together. After merge, use a reviewed
  fix/revert commit, not history rewriting or partial tool removal.

## 5. Proposed live acceptance budget — not authorization

After implementation/offline gates, ask for a fresh time-bounded authorization
identifying the test chat, allowed message IDs, current app and whether controlled
foreign-chat retrieval is acceptable. Perform acceptance via the real tool
executor in an isolated local harness so reads do not create ordinary gateway
answers or approval messages. Do not restart/connect a new gateway; this checks
the tool/API, not whether an existing live process has loaded the new code.

Proposed maximum is **four message GETs**, one per invocation:

1. One existing same-chat text/post message: grounded content and IDs.
2. One existing same-chat original card: documented card JSON, bounded result.
3. One explicitly authorized message in a second designated chat: retrieval may
   reach SDK memory, but no body or foreign metadata reaches the tool result.
4. One already unavailable/deleted test message: safe failure with no fallback.

Allow SDK tenant-token acquisition only as needed for these authorized reads.
No automatic retry or replacement call on failure; no sends/replies/reactions,
message creation/deletion, downloads, permission grants or OAuth. The user must
separately authorize any external console permission change; publishing an app
version is not implicit in this budget. Stop at the first unapproved requirement
and record actual completed checks and limitations. If no suitable foreign or
deleted ID is authorized, leave that live case unverified rather than creating
data or substituting another operation.

## 6. Spec coverage and present stop point

| Approved design requirement | Plan owner |
| --- | --- |
| §1/§5 current-chat-only authority and tool contract | Tasks 2, 5, 6 |
| §3 tenant identity, GET/card parameters and permissions | Tasks 4, 6; live proposal |
| §4 separate read path; unchanged reply preflight | Task 4 and S8a regression gate |
| §6 one response, identity/deletion-first release, cancellation and no diagnostic leakage | Tasks 4, 5, 6 |
| §7 scrubber extraction, JSON preservation, limits, masking and explicit truncation | Tasks 1, 3, 5 |
| §8 safe result fields and distinct failures | Tasks 2, 4, 5 |
| §9 owning modules, unchanged discovery root, offered prompt and user docs | File map; Tasks 1–6 |
| §10 realistic SDK fixture, real runner isolation, borders and live boundary | Tasks 4–6; local gate; live proposal |
| §11 independent approval, one PR and post-merge closure | This section and §4 |
| Proposed §12 generic gateway-only gate and runtime surface proof | Tasks 2, 5, 6; approval delta above |

Design approval is already recorded and must not be requested again. This plan
and its narrowly scoped design §12 gate refinement require future explicit
approval and an execution-method choice before product, test or runtime edits.
The user requested plan preparation only and no execution on 2026-09-30; stop
after saving and validating these planning documents. Do not request immediate
execution approval or start a worker/reviewer during this planning turn.
Recommended future method: native execution plus independent
whole-branch review. Plan approval does not authorize live operations, deployment
or merging. No implementation, product tests, push, PR or live operation has been
performed during this planning stage.
