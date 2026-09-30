# Feishu S8b-1 Single-Message Read Design

- Date: 2026-09-30
- Status: Core design approved by user (2026-09-30); §12 gate refinement and independent implementation plan await approval. User requested plan preparation only, with no execution.
- Parent roadmap: [`2026-09-12-feishu-capability-completion-design.md`](2026-09-12-feishu-capability-completion-design.md)
- Implementation plan: [Independent plan](2026-09-30-feishu-s8b1-read-message-implementation-plan.md), awaiting separate approval
- Scope: One read-only ACTION tool, `feishu_get_message`, for a known message ID in the current Feishu chat
- Scope decision: Current-chat-only first release, approved with this design on 2026-09-30

## 1. Intent and success criteria

The user authorized starting S8b after S7 delivery and accepted starting with a
single known message ID. The goal is to let the action agent inspect an existing
message when handling a request in that message's Feishu chat.

A successful call returns a bounded, sanitized representation of that one
message. A message from another chat, an unverifiable response, or a revoked or
unavailable message never releases content to the model. Permissions granted to
the bot by Feishu are necessary but do not replace OpenSRE's current-chat boundary.

This design fixes current-chat-only access. It adds no read allowlist, default
read target, shell access, user OAuth, or cross-chat capability. Existing default
delivery targets and `FEISHU_ALLOWED_OUTBOUND_TARGETS` authorize writes only.

## 2. Verified baseline

Research used the existing worktree
`C:\Users\23033\Desktop\opensre2\.worktrees\feishu-s5b-feedback`. Its starting
branch was `codex/feishu-s7-delivery-record`, HEAD
`5c8b5d982e4cc5a64dcb3f81d015c3a1ac1c86d8`, with a clean working tree and a tree
identical to `origin/main` at `588fafb314069651dcbacd2da14ee92a8865989d`.
The design branch starts at that `origin/main` commit and reuses this worktree.

- S7 is delivered through PR #41, its CodeQL fix #42, and delivery record #43.
  Its frozen channel context and final messaging-source gate are available.
- `integrations/feishu/message_lookup.py` already calls the pinned SDK's message
  GET for reply-parent verification, returning only `FeishuMessageMetadata`.
  Its body-exclusion and fail-closed contracts must remain unchanged.
- `outbound_targets.py` owns S8a write targets, not read authority.
- The installed `lark-oapi` version is 1.7.3. Offline introspection confirmed
  `GetMessageRequest`, `GetMessageResponseBody.items`, `Message.body.content`,
  identity/deletion fields, the request builder, and client timeout support.
- `GetMessageRequest` does not accept `chat_id`. A single GET must be followed
  by response identity checks before content is processed or released.
- `AgentToolContext.resolved_integrations` carries the turn's frozen integration
  view. Normal availability and runtime execution already consume that view.
- `integrations/feishu/action_prompt.py` currently describes send and reply only.
  No S8b read tool is registered or promised by the prompt.

This research used repository reads, offline SDK inspection and unauthenticated
public documentation requests. It did not load credentials, read real messages,
send messages, show approvals, or start/restart a gateway.

## 3. Official API findings and permission contract

The primary source is the official
[Get message documentation](https://open.feishu.cn/document/server-docs/im-v1/message/get),
verified on 2026-09-30. Its public document-portal JSON was used because the
HTML page renders its body through JavaScript. This is documentation retrieval,
not an authenticated request to the message API.

- Use `GET /open-apis/im/v1/messages/:message_id` through
  `client.im.v1.message.get(GetMessageRequest...)`.
- Use the existing chat application's application identity and tenant token.
  Do not accept a user token or add OAuth or alert-push credentials.
- Base access requires `im:message` or `im:message:readonly`. Reading group
  messages additionally requires `im:message.group_msg`. The bot must have bot
  capability and access to the message's chat. Do not request the legacy
  `im:message.history:readonly` scope for a new deployment.
- Request `user_id_type="open_id"`; do not request employee IDs or sender names.
- Request `card_msg_content_type="user_card_content"` to obtain the original
  card JSON supported by the API, including its documented 1.0/2.0 forms.
  Do not represent the default card projection as the original card.
- The API can return a merge-forward parent and multiple children in `items`.
  S8b-1 deliberately rejects merge-forward expansion and multi-item responses.

Feishu permissions and membership are verified by the upstream API. Availability
checks local credentials/context only and must not claim to have inspected live
scope grants. A missing permission produces a safe structured failure. No setup
flow automatically requests broader permissions or probes real messages.

## 4. Approaches considered

| Approach | Trade-off | Decision |
| --- | --- | --- |
| Current-chat-only, known ID, one guarded GET | Small authority surface; unsuitable for shell and cross-chat lookup | Selected for S8b-1 |
| Known ID plus a separate read-chat allowlist | Enables cross-chat workflows; requires independent configuration, actor policy and authorization contracts | Defer to a separately approved extension |
| Chat history/search first | Makes message discovery easier; adds time windows, pagination and a wider data surface | Subsequent independent S8b projects |

Do not expand `lookup_reply_parent` into a public content reader. Its existing
S8a callers must continue to receive metadata only. A dedicated read client
performs a single GET and validates that same response; metadata GET followed
by a second content GET would add a race and unnecessary network work.

## 5. Public tool contract

The only input is `message_id`, a non-empty string, stripped of surrounding
whitespace and limited to 256 characters. Additional properties are rejected.
There is no `target`, `chat_id`, token, endpoint, sender selector, offset, or
pagination input. The ID must be supplied by the user or a prior grounded tool
result; the agent must not invent it or promise to search for one.

Metadata:

- `source="feishu"`, `requires=["feishu"]`, `surfaces=(ToolSurface.ACTION,)`;
- `side_effect_level=SideEffectLevel.READ_ONLY`, `requires_approval=False`;
- `accepts_runtime_context=True`, `parallel_safe=True`;
- `log_omitted_input_fields=("message_id",)`.

Normal availability requires a valid Feishu integration with chat-app
credentials, exact `_gateway_platform == "feishu"`, and a non-empty string
`_gateway_chat_id`. An explicitly unverified connection remains unavailable.
Malformed, missing, unknown, shell and other-platform contexts fail closed.
No alias, configured default target, outbound allowlist, user text or mutable
session cache can supply the missing current chat.

`prepare_public_input` validates the input and frozen view without network I/O.
`run` repeats the authority check so direct invocation and stale/custom tool
providers cannot bypass it. S7's final messaging-source gate remains in effect.
The tool is unavailable on investigation, background delivery and local shell
surfaces; no capability-policy changes or new discovery root are required.

## 6. Authority and data-release sequence

1. Validate the payload, take local copies of the current platform/chat from
   `context.resolved_integrations`, and check cancellation before any API call.
2. Resolve chat-app credentials through the existing Feishu credential loader;
   keep secrets internal and never put them into public arguments or results.
   The frozen integration's app ID must agree with the resolved credential app
   ID; a changed app identity fails closed before the GET.
3. Build a per-call SDK client with a 10-second per-HTTP-request timeout. Use
   the SDK's existing tenant-token path, with no shared response/body cache.
4. Perform at most one message GET. Do not retry, list history, query members,
   fetch a parent, expand a forward bundle, or download a resource.
5. On success require `items` to be a list containing exactly one item, the
   exact requested `message_id`, the exact frozen `chat_id`, and
   `deleted is False`. Missing/malformed identity or deletion fields reject.
   Explicit `merge_forward` also rejects even if it has no returned children.
6. Only after these checks, inspect `msg_type` and `body.content`, normalize
   content, and construct the tool result. Recheck cancellation before release;
   cancelled reads discard the response content.

**Boundary limitation:** Feishu's ID-only endpoint can return another chat's
body to the SDK before OpenSRE knows its chat. The guard prevents disclosure to
the agent, another chat, telemetry, or diagnostics; it does not claim that an
out-of-chat body never entered trusted process memory. If the requirement is
to prevent that upstream retrieval altogether, a separately designed trusted
message-ID provenance mechanism is required before implementation.

Rejected SDK objects, their `raw` response, provider `msg`, actual foreign
chat IDs, sender data, children and bodies must not be serialized or logged.
Do not include them as exception arguments or telemetry extras. The pinned
SDK transport was inspected: its debug completion log does not include the
response body. Keep full response logging disabled.

## 7. Content representation and bounds

Return one representation: the API's message-body JSON, sanitized and serialized
as a string. This avoids losing unfamiliar rich-text/card nodes through the
existing inbound `flatten_post` helper. `text`, `post` and `interactive` content
remain inspectable, and other single-message types retain their API metadata.
Resource keys are metadata only: no image, file, audio, video or linked content
is downloaded, described or executed. Unsupported/malformed bodies fail safely.

The body must be a JSON object. Before parsing, limit its UTF-8 size to 256 KiB;
reject a larger body. Bound recursive content processing to depth 64 and 8,192
nodes, with recursion/complexity failures producing a safe error. These are
processing bounds, not a claim that the SDK streams or caps HTTP response bytes.

Sanitize the parsed object before truncation:

1. Apply the existing key-based `redact_sensitive` helper to secret-bearing
   fields and runtime-only values.
2. Apply the canonical obvious-secret scrubber to decoded string values.
3. Honor the existing `MaskingPolicy` through a per-call `MaskingRules` instance
   when enabled. Do not share placeholder maps across chats or retain originals
   in the tool result.

The scrubber currently lives in `gateway/core/attachments/inline.py`, which an
integration cannot import. Promote its unchanged pure function and private
patterns to `infrastructure/safety/masking/secrets.py`. Migrate its gateway
attachment and approval consumers and its test to the canonical path; retain
no forwarding function or old public scrubber export. Characterize the existing
behavior before this move. This targeted extraction is part of the approved
design and must preserve all existing attachment/approval behavior.

Serialize the sanitized object once and return at most 12,000 Unicode
characters. Set `truncated=true` and `body_format="json_prefix"` when only a
prefix fits; otherwise `body_format="json"`. The outer tool result is always
valid JSON. A prefix is explicitly incomplete and need not itself parse as JSON.
Do not silently truncate or let the agent claim it read the whole message.
`redacted` records whether sanitization changed content; it is independent of
`truncated`. Obvious-secret scrubbing is bounded detection, not a guarantee
that arbitrary credentials or PII are recognizable.

## 8. Result and error contract

Successful `ToolExecutionResult.content` is JSON and `details` contains the
same safe object, including:

```json
{
  "source": "feishu",
  "status": "read",
  "message_id": "om_example",
  "chat_id": "oc_current",
  "msg_type": "text",
  "body_content": "{\"text\":\"示例消息\"}",
  "body_format": "json",
  "truncated": false,
  "redacted": false,
  "resource_content_included": false,
  "content_trust": "untrusted"
}
```

Do not copy sender identifiers/names or tenant keys from the response envelope
into the result, and never return credentials, raw SDK objects or a second
unsanitized representation. Mentions within the message body remain message
content, subject to the configured masking policy. Chat/message IDs in
successful results identify only the validated current-chat resource.

Failure returns `is_error=True`, `source`, `status="failed"`, a stable
`error_type` and safe human-readable `error`; it contains no `body_content` or
foreign-resource metadata. Use these distinct categories:

| Category | Behavior |
| --- | --- |
| `validation` | Invalid public payload; no API call |
| `authorization` | Missing/invalid current context or credential identity; no GET |
| `cancelled` | Cancelled before request or before release; no content returned |
| `message_unavailable` | Unverifiable identity/chat/deletion, invisible/deleted message, missing upstream access or membership; do not reveal which foreign chat exists |
| `unsupported_content` | Merge-forward expansion or unsupported/malformed body representation |
| `content_too_large` / `content_too_complex` | Body exceeds the agreed processing bound |
| `rate_limited` | Upstream rate limit; safe retry-later guidance, no automatic retry |
| `upstream_error` | Transport, service or unexpected failure; safe message, no raw exception/provider detail |

No read failure triggers a send, reply, reaction, permission escalation,
scope grant, alternative credential path, or discovery fallback. Do not reuse
S8a write-result fields such as `sent`, `maybe_sent` or delivery retry certainty.

## 9. Ownership and runtime wiring

Proposed owning modules, to be refined in the separately approved plan:

- `integrations/feishu/message_read.py`: SDK request, exact response verification
  and read outcome; `message_lookup.py` remains metadata-only.
- `integrations/feishu/read_scope.py`: pure current-chat availability and
  authorization rules; it does not consult outbound targets.
- `integrations/feishu/message_content.py`: bounded JSON parsing/sanitization
  and representation; no network or gateway imports.
- `integrations/feishu/tools/feishu_get_message_tool/`: focused `tool.py`,
  `validation.py`, `results.py` and a lightweight `__init__.py` exporting the
  decorated tool. Do not grow the existing write-only result module.
- `config/constants/feishu.py`: named body/output/timeout/complexity limits,
  re-exported through the constants facade. No new environment variables.
- `infrastructure/safety/masking/secrets.py` and existing gateway consumers:
  the targeted scrubber extraction described above.

The existing integration tool-package discovery handles the new package.
No new global registry, provider branch, transport event or package-border
exception is required. Import `BaseTool`, context and result through `core.tool`
and the decorator through `core.tool_framework`. Keep package facades light.

Extend only the active Feishu action fragment, conditioned on the actual
offered `feishu_get_message` name. Separate read guidance from additional-write
guidance so read-only availability does not imply send/reply availability.
Describe the known-ID/current-chat rule, incomplete results, metadata-only
resources and untrusted content. Preserve S8a approval and ordinary gateway
answer rules. Do not add unsupported capabilities to personas or old unused
fragment registries, or change `morning-report`.

Update the existing `docs/messaging/feishu.mdx` in the implementation PR with
user-facing setup permissions, known-ID usage, current-chat scope, body limits
and attachment limitations. No new docs navigation entry is needed.

## 10. High-signal validation and acceptance

The implementation plan must select the focused commands from `CI.md` and the
touched-path rules. Pin distinct failure modes rather than literal variations:

1. A realistic `GetMessageResponse` fixture exercises the real SDK model,
   including text, rich text and original 2.0 card JSON. Card-format request
   conformance is verified with an intercepted transport, never real credentials.
2. A wrong-chat or wrong-ID response has a sentinel body that raises if touched.
   Assert no body access, result leakage, diagnostic leakage or secondary calls.
   Cover deleted/missing-deletion and merge-forward/multi-item rejection.
3. Runtime invocation requires current frozen authority even with a custom or
   precomputed provider. Missing/malformed metadata, shell, other platforms,
   outbound-only configuration and changed app identity cannot enable a GET.
4. Reusing a session or running two chats concurrently preserves each frozen
   chat and content boundary. Use events/barriers and reliable bounded cleanup.
5. Secret-bearing JSON values, policy-enabled identifiers, content bounds,
   explicit prefix truncation and cancellation before release cannot leak the
   unsanitized/full body through `content`, `details` or exception handling.
6. Characterize the pre-move scrubber, then keep its canonical utility,
   attachment rendering and approval preview behavior green after extraction.
7. Through real discovery/action assembly, the new tool and prompt agree for
   Feishu read-only, write-only and unavailable views; inactive channels remain
   isolated. S8a metadata-only lookup, approvals and delivery regressions pass.
8. Run applicable import/API-border checks without widening allowlists. No
   vendor tests may connect to live services.

Live acceptance remains separately authorized, with an exact current budget
and a designated test chat. The future plan should propose successful same-chat
text/card reads and a controlled foreign/deleted-message rejection, including
the SDK-retrieval limitation in section 6. Do not reuse S8a/S7 authorization,
start/restart a live gateway, or treat offline fixtures as live acceptance.
Any permission changes are external configuration actions requiring current
authorization. No real reads or live operations are authorized by this design approval.

## 11. Approval and delivery boundary

The user explicitly approved this independent S8b-1 design on 2026-09-30.
The approval includes current-chat-only access and the targeted scrubber
extraction. It authorizes preparation of the separate implementation plan;
product code, tests, runtime configuration, dependencies, user docs and
workflows remain unchanged until that plan is separately approved.

After explicit design approval, write a separate implementation plan with exact
files, behavioral characterization/TDD order, focused CI commands, commit and
rollback boundaries, and any proposed live-operation budget. Obtain that plan's
approval before implementation. Routine details may be refined within the
approved boundary; changes to access scope or adjacent contracts require a
written revision and approval.

The implementation PR contains only S8b-1 and its necessary redaction extraction.
History/search, members, reactions, cross-chat read policy, S8a semantics,
morning-report adaptation, release activation and old CodeQL cleanup remain
separate work. Follow the PR template, AI disclosure, checks after every push,
Greptile 5/5, an explicit merge instruction and post-merge main CI/full CodeQL/
release closure. Preserve S8a's previously recorded live multipage limitation.

## 12. Planning refinement awaiting approval: gateway-only enforcement

The approved access scope remains current-chat-only. Plan review found that
S7's `filter_action_tools_for_channel` deliberately preserves messaging tools
on the interactive shell, even when a resolved view contains gateway markers.
Normal availability receives integration sources, not the host's surface;
those markers alone cannot prove this turn actually came from a gateway.

The independent implementation plan therefore proposes one narrow adjacent
contract extension, still awaiting approval:

- Add the shared `GATEWAY_ONLY_TOOL_TAG = "gateway_only"` in
  `config/constants/tool_policy.py`, re-export it, and opt only the new
  `feishu_get_message` tool into this existing tool-tag mechanism.
- Extend the existing final action-tool filter with a generic tag check:
  tagged tools require `ActionPromptContext.surface == "gateway"`, in addition
  to the existing messaging-source check. Keep both function signatures and
  all untagged tools' behavior unchanged; do not introduce a vendor/name branch
  or a new registry.
- At execution, require the existing frozen `ActionPromptContext` resource
  to identify a Feishu gateway, alongside the frozen integration scope. Missing,
  malformed or shell resources cannot enable a GET, including direct calls
  and custom/precomputed providers. Public preparation can validate input and
  integration scope only; the final host filter and runtime check enforce the
  actual surface.

This refinement enforces the approved no-shell contract without broadening
access. Future implementation-plan approval must explicitly include this shared
gate extension; the earlier design approval alone does not authorize it.
