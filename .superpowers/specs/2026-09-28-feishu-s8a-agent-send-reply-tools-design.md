# Feishu S8a Agent Send and Reply Tools Design

- Date: 2026-09-28
- Status: Approved by user (2026-09-28); implementation plan separately approved
- Parent roadmap: [`2026-09-12-feishu-capability-completion-design.md`](2026-09-12-feishu-capability-completion-design.md)
- Scope: S8a only

## 1. Purpose

S8a adds two Feishu-owned, agent-callable write tools:

- `feishu_send_message` sends complete Markdown content to an authorized Feishu destination;
- `feishu_reply_message` replies to a specific Feishu message after verifying that the message
  belongs to the authorized chat named in the tool call.

Both tools are external side effects. They use the existing integration credentials, S4/S6
CardKit rendering and lossless fallback, and the shared approval gate. They do not expose raw
credentials, accept an unbounded destination, silently change a reply into a fresh message, or
make message-reading content available to the agent.

S8a deliberately establishes the smallest safe tool surface that S7 can later describe using
runtime channel context. It does not perform the S7 prompt rewrite itself.

## 2. Goals

1. Register Feishu send and reply tools under `integrations/feishu/tools/`.
2. Limit delivery to a turn-scoped current Feishu chat, the configured default destination, or an
   exact operator-configured outbound target.
3. Require a human approval for every valid, authorized tool call before its side effect, including
   calls requested explicitly in chat.
4. Verify a reply target's actual `chat_id` before any side effect; never trust a model-supplied
   `chat_id`/`message_id` pair by itself.
5. Preserve Markdown, tables, fenced code and long content through the existing CardKit pagination
   and lossless text fallback.
6. Report confirmed, partial, definitely-not-sent and delivery-uncertain outcomes distinctly so the
   agent does not create duplicates by retrying an ambiguous call.
7. Reuse store-first credential resolution and prevent secrets from entering public schemas,
   approval cards, tool traces, logs or results.
8. Make availability accurate on local/action surfaces and hide Feishu write tools in another
   messaging gateway's turn.
9. Keep package facades lightweight and preserve import/API borders.
10. Cover authorization, approval, cross-chat isolation, pagination, partial delivery and live SDK
    payload shapes with focused tests.

## 3. Non-goals

S8a does not implement:

- the S7 Feishu persona, action/gather/assistant prompt fragments, or general prompt rewrite;
- agent-visible message reading, message search, member listing, or reaction writes; those remain S8b;
- target discovery by chat name, user name, email lookup, fuzzy matching or search;
- arbitrary delivery to any chat the bot has joined;
- files, images, audio, video, mentions, edits, recalls or scheduled delivery;
- automatic delivery without approval, approval reuse across calls, or an always-allow policy stored
  by the Feishu gateway;
- a reply fallback that posts a new message when the parent is deleted or unavailable;
- a durable outbound queue, offline retry worker or exactly-once guarantee across process crashes;
- changes to Slack, Telegram, Discord, Rocket.Chat or Buzz tools;
- deletion of legacy transports or implementation of the later Phase 3 decision;
- live Feishu mutation before a separate, current and bounded acceptance authorization.

## 4. Confirmed platform and SDK contracts

### 4.1 Locked SDK

The repository currently requires `lark-oapi>=1.7.3`; the resolved lock contains
`lark-oapi 1.7.3`. S8a continues using the existing low-level `lark.Client` path rather than adding
the newer channel abstraction or another dependency.

### 4.2 Create message

The installed SDK's `CreateMessageRequestBody` has:

- `receive_id`;
- `msg_type`;
- `content`;
- `uuid`.

`CreateMessageRequest` carries `receive_id_type`. The existing Feishu integration already uses this
API for text and CardKit-backed messages and classifies responses into stable certainty/error values.

### 4.3 Reply message

The installed SDK's `ReplyMessageRequestBody` has:

- `content`;
- `msg_type`;
- `reply_in_thread`;
- `uuid`.

`ReplyMessageRequest` targets one `message_id`. A successful reply body exposes `message_id`,
`root_id`, `parent_id`, `thread_id` and `chat_id`. `reply_in_thread=true` is therefore supported, but
S8a keeps it explicit rather than forcing every reply to create/use a topic thread.

The official SDK channel documentation describes an automatic "reply target gone -> fresh send"
fallback. S8a rejects that behavior: changing a reply into a new message changes the authorized
side effect and may post in an unintended place.

### 4.4 Message metadata preflight

The installed `GetMessageRequest` accepts `message_id`; its response contains `items: list[Message]`.
Each `Message` includes `message_id`, `chat_id`, deletion state and thread identifiers as well as its
body. S8a reads only the minimum metadata needed for authorization and threading. It must not return,
log, persist or place the fetched body into prompt context.

This internal metadata check does not create an agent-visible read capability and therefore does not
consume S8b. It uses the message read permission already required by the existing attachment path.

Primary references:

- <https://github.com/larksuite/oapi-sdk-python>
- <https://github.com/larksuite/oapi-sdk-python/blob/v2_main/doc/channel.md>
- <https://github.com/larksuite/oapi-sdk-python/blob/v2_main/doc/channel/reference.md>

## 5. Recommended design and rejected alternatives

### 5.1 Recommended: explicit destination capabilities plus reply preflight

Use three sources of outbound authority:

1. `current` -- the current chat only when the active gateway platform is Feishu;
2. `default` -- the existing configured `(receive_id_type, receive_id)` pair;
3. an exact entry from a new static outbound-target allowlist.

The tool call names its target. Before approval, a generic tool-runtime preparation seam resolves
`current`/`default` to the exact canonical `receive_id_type:receive_id`, validates explicit targets,
and rejects unauthorized targets. The approval card therefore shows the actual destination, not only
an alias. The prepared approval mapping places the canonical target first so it remains visible even
when the message body is truncated by the shared preview limit. For replies, the tool fetches message
metadata and requires the returned `chat_id` to equal the resolved authorized chat before sending.

Benefits:

- current-chat actions need no duplicated configuration;
- local/headless use has a deterministic configured target;
- cross-chat writes are possible only after explicit operator configuration;
- the approval card shows the intended destination;
- a forged `chat_id`/`message_id` pair fails before delivery;
- S8b remains free to design agent-visible reading separately.

Trade-off: a reply incurs one metadata read before the write. If that read is unavailable, the reply
fails closed even if the platform might otherwise accept it.

### 5.2 Rejected: trust `message_id` and caller-supplied `chat_id`

Feishu's reply API needs only `message_id`. A caller could pair a message from another chat with an
allowed `chat_id` and bypass destination authorization. Post-send response validation is too late
because the external side effect has already occurred.

### 5.3 Rejected: current/default destination only

This is simpler but does not satisfy deliberate cross-chat incident handoff, one of the reasons for
an agent-callable send tool. A static exact allowlist provides that capability without workspace-wide
write access.

### 5.4 Rejected: any chat the bot can access

Bot membership is not user authorization. It would let a prompt or mistaken tool call post to every
chat the application has joined and would make the approval preview the sole security boundary.

### 5.5 Rejected: use the channel SDK's automatic fallbacks

Automatic reply-to-send fallback and format fallback obscure the delivery certainty and destination.
The existing integration already owns CardKit pagination, format fallback and stable error
classification, so S8a extends that path instead of adding a parallel transport stack.

## 6. Outbound target configuration

### 6.1 New setting

Add `FEISHU_ALLOWED_OUTBOUND_TARGETS` as a comma-separated list of exact
`receive_id_type:receive_id` capabilities, for example:

```text
chat_id:oc_ops,chat_id:oc_incident,open_id:ou_owner
```

The setting lives in `config/constants/feishu.py`, is represented by
`FeishuConfig.allowed_outbound_targets`, and is collected by Feishu guided setup. It follows the
existing store-first, environment-fallback resolution order.

`FEISHU_ALLOWED_OPEN_IDS` remains the inbound actor allowlist. It must not be reused as an outbound
destination list: actor identity and delivery authority are different security domains.

### 6.2 Canonical parsing

- split entries on commas, trim whitespace and ignore empty entries;
- split each entry once on `:`;
- require a type in `FEISHU_RECEIVE_ID_TYPES` and a non-empty identifier;
- compare normalized pairs exactly and case-sensitively;
- deduplicate with a set on the hot path while retaining a stable sorted form for diagnostics;
- reject malformed configured entries during setup/verification rather than silently widening access;
- never resolve names, aliases or prefixes into a different identifier.

The configured default `(receive_id_type, receive_id)` is automatically authorized when both fields
are present. Operators need not duplicate it in the new setting.

### 6.3 Turn-scoped current target

The gateway already injects `_gateway_platform` and `_gateway_chat_id` into the session's resolved
integration view. `current` resolves only when:

- `_gateway_platform == "feishu"`;
- `_gateway_chat_id` is non-empty.

It resolves to `chat_id:<current chat>`. Another gateway's metadata never authorizes a Feishu target.
The capability expires with the turn/session context and is not persisted as configuration.

## 7. Tool contracts

### 7.1 `feishu_send_message`

Public input:

```json
{
  "target": "current | default | receive_id_type:receive_id",
  "message": "complete Markdown body"
}
```

Rules:

- both fields are required; missing, wrong-type and empty-string values are rejected before an approval
  is requested;
- the public schema gives `target` and `message` `minLength: 1`; semantic validation then rejects
  whitespace-only values in the pre-approval preparation step;
- `current` and `default` resolve as defined above;
- an explicit capability must exactly match the configured outbound set or configured default;
- approval displays the resolved canonical target; `default` is never approved as an opaque alias;
- empty/whitespace-only content is rejected before dispatch;
- the content is delivered through non-streaming CardKit pagination with lossless text fallback;
- the tool does not truncate content and does not accept credentials or raw webhook/API parameters.

### 7.2 `feishu_reply_message`

Public input:

```json
{
  "target": "current | default | chat_id:oc_...",
  "message_id": "om_...",
  "message": "complete Markdown body",
  "reply_in_thread": false
}
```

Rules:

- `target`, `message_id`, `message` and `reply_in_thread` are required;
- the public schema gives every string field `minLength: 1`; semantic validation then rejects
  whitespace-only values in the pre-approval preparation step;
- the resolved target must be a `chat_id`; an `open_id`/email/user/union target cannot prove which
  chat owns an existing message and is rejected for replies;
- approval displays the exact `chat_id` that the metadata preflight must later match;
- metadata lookup must return exactly one non-deleted message with the requested ID;
- the returned `chat_id` must exactly equal the resolved target;
- `reply_in_thread` is passed explicitly to every card/text page;
- target revocation/deletion fails as a reply and never becomes a fresh send.

The current shared `RegisteredTool.validate_public_input()` checks required fields, types, enums and
additional properties but does not enforce JSON Schema `minLength`. S8a must add generic string
`minLength` enforcement in `core/tool/contracts.py` with focused contract tests; merely publishing
`minLength` to the model schema does not satisfy the pre-approval validation claim. This is a shared
schema correctness fix, not Feishu-specific intent routing. Whitespace-only rejection remains in the
tool's pre-approval semantic validator because JSON Schema `minLength` deliberately counts whitespace.

`reply_in_thread=false` is the recommended default in prompts and examples because it mirrors normal
message reply behavior. A caller requests `true` only when a topic/thread reply is intended.

### 7.3 Common metadata

Both tools declare:

- `source="feishu"`;
- `requires=["feishu"]`;
- `surfaces=(ToolSurface.ACTION,)`;
- `side_effect_level=SideEffectLevel.EXTERNAL`;
- `requires_approval=True`;
- a contract-focused approval reason naming the outbound Feishu message;
- `parallel_safe=False`, preserving message order and avoiding concurrent approval/send races.

They are not investigation evidence tools and have no evidence mapper.

### 7.4 Common result

The stable result contains only safe fields:

```json
{
  "source": "feishu",
  "status": "sent | partial | failed",
  "sent": true,
  "attempted": true,
  "target": "chat_id:oc_...",
  "reply_to_message_id": "",
  "confirmed_message_ids": ["om_..."],
  "delivery_mode": "cards | text_fallback | none",
  "certainty": "confirmed_sent | definitely_not_sent | maybe_sent | partial",
  "retry_safe": false,
  "error_type": "",
  "error": ""
}
```

- `sent=true` only when the complete source content is confirmed delivered;
- `partial` means at least one page/chunk is confirmed but the complete content is not;
- `retry_safe=true` only when no side effect was attempted or the platform definitely rejected it;
- delivery uncertainty and partial delivery are never retry-safe;
- errors use stable generic text and categories, never raw exceptions or vendor response bodies.

## 8. Credential and parameter injection

Tool public schemas contain no app ID, secret or integration-store fields. Availability checks use the
registry's resolved Feishu view, but execution must not inject credentials or the outbound allowlist into
traceable tool kwargs. After approval, `run()` re-loads the store-first Feishu credentials/config through
`integrations.feishu.credentials`; only turn-scoped gateway metadata comes from `AgentToolContext` with
`accepts_runtime_context=True`. The app secret must be excluded from repr, output, approval arguments and
structured logs. Model input cannot supply or override the gateway platform/chat or any credential/config
field.

Availability requires usable app credentials plus at least one target source relevant to the tool.
If gateway metadata names a non-Feishu platform, both Feishu tools are unavailable even when Feishu is
configured globally. On a local/action surface with no gateway platform, static/default targets remain
available.

## 9. Approval and authorization sequence

Every call follows this order:

1. JSON Schema validation checks required public fields, types and `minLength`.
2. A generic pre-approval preparation seam performs Feishu semantic validation, resolves the target
   alias, authorizes it against the current resolved view, and replaces the approval/execution target
   with the exact canonical pair. It injects no credential or secret and orders the canonical target
   first in the approval mapping.
3. The existing `requires_approval` hook renders that canonical target and redacted/truncated arguments.
4. The current surface obtains one approval. In the Feishu gateway, only the original requester in the
   original chat may approve, using the S5a card authority.
5. The tool re-loads credentials/config and re-authorizes the exact target shown for approval. If
   configuration changed, the call fails closed; it never re-resolves `default` to a different target.
6. A reply performs message metadata preflight and exact chat comparison.
7. Only then does delivery begin.

Denial, expiry, shutdown drain or failed approval-card exposure produces no Feishu tool side effect.
Approval authorizes one call only; it is not cached for a later tool call.

## 10. Delivery and certainty semantics

### 10.1 Shared document delivery

Extend `deliver_feishu_document` with an optional reply target and thread flag. The same pagination,
CardKit schema 2.0, table budget and source-range accounting apply to sends and replies.
S8a tool calls explicitly enable stop-on-ambiguous-write behavior; the existing background-delivery
fallback policy remains the default for its callers.

All pages for one reply target the same parent message. If CardKit rendering, card creation, or the
message API definitely fails before the current source range can be visible, text fallback resumes at
the exact unconfirmed source cursor and continues as replies to that same parent. It never starts again
from byte/character zero.

An ambiguous create/reply transport outcome is different: the card may already be visible. Delivery
must stop immediately and must not text-fallback that source range, because doing so can duplicate
content. With no prior confirmed page the result is `failed` / `maybe_sent`; with a confirmed prefix it
is `partial` / `maybe_sent`. Reply fallback always calls the reply API; target-revoked/deleted replies
never become fresh sends.

### 10.2 Text transport

The canonical integration-owned message helper supports create and reply instead of importing the
gateway's private `_send_text`. It returns `FeishuMessageSendResult` with the existing stable certainty
classification. Gateway behavior may later delegate to this helper, but S8a must not create a
compatibility-only forwarding module.

### 10.3 Idempotency

Create/reply request bodies use one UUID per logical page/chunk. An internal retry of that same network
operation reuses its UUID. A new agent tool call receives new UUIDs and therefore remains a distinct,
separately approved side effect.

S8a does not claim exactly-once delivery across process failure. Instead it reports uncertainty and
forbids automatic retries when the first call may have reached Feishu.

### 10.4 Partial delivery

If page N fails after pages 1..N-1 were confirmed:

- keep their IDs in `confirmed_message_ids`;
- represent the shared delivery result as `FeishuDeliveryStatus.PARTIAL`; its `successful` property
  remains false, while `SUCCESS` and `DEGRADED_SUCCESS` remain the only complete-success states;
- return `status="partial"`, `sent=false`, `retry_safe=false`; certainty is `partial` when the
  remainder is definitely unsent and `maybe_sent` when the terminal call is ambiguous;
- never hide the partial result behind a generic failed/false value;
- do not automatically resend the whole document.

The normalized result matrix is:

| Confirmed prefix | Terminal outcome | `status` | `certainty` | `retry_safe` |
| --- | --- | --- | --- | --- |
| none | no write attempted or explicit rejection | `failed` | `definitely_not_sent` | `true` |
| none | transport/response ambiguity | `failed` | `maybe_sent` | `false` |
| one or more pages | remaining range definitely not sent | `partial` | `partial` | `false` |
| one or more pages | remaining range may have been sent | `partial` | `maybe_sent` | `false` |
| complete document | every range confirmed | `sent` | `confirmed_sent` | `false` |

`attempted` means a Feishu message create/reply operation was invoked; metadata preflight alone does not
set it. A partial result is never collapsed to `failed`, even when the terminal range is ambiguous.

## 11. Package ownership

### 11.1 Configuration

- `config/constants/feishu.py` owns the new env-var name.
- `integrations/config_models.py` owns the typed config field.
- `integrations/feishu/classify.py` preserves the field when classifying store or environment records.
- `integrations/_catalog_impl.py` loads the environment fallback into the classified Feishu record.
- `integrations/feishu/setup.py` collects the outbound target string and may provide immediate
  field-level feedback.
- `integrations/feishu/verifier.py` owns authoritative cross-field verification so stored,
  environment-provided and guided-setup values follow the same rules.
- `integrations/feishu/credentials.py` carries the store-first resolved value.

### 11.2 Integration-local outbound primitives

- `integrations/feishu/outbound_targets.py` owns parsing, canonicalization and authorization.
- `integrations/feishu/message_lookup.py` owns metadata-only reply preflight and safe normalized results.
- `integrations/feishu/delivery_types.py` owns the explicit `PARTIAL` status and its unsuccessful
  semantics.
- `integrations/feishu/delivery.py` owns create/reply text calls and certainty classification.
- `integrations/feishu/card_client.py` owns create/reply card calls and request UUIDs.
- `integrations/feishu/document_delivery.py` owns lossless paginated send/reply orchestration.

These modules may import the vendor SDK. The lightweight `integrations/feishu/__init__.py` facade must not
re-export SDK-heavy tool or client implementations.

### 11.3 Tools

Add focused packages under:

```text
integrations/feishu/tools/
  __init__.py
  results.py
  feishu_send_message_tool/
    __init__.py
    tool.py
    validation.py
  feishu_reply_message_tool/
    __init__.py
    tool.py
    validation.py
```

Shared Feishu target/delivery logic remains one level above the tools rather than being copied between
tool packages. The common safe result mapping lives in `integrations/feishu/tools/results.py` rather
than being duplicated. Each tool-package `__init__.py` contains only the registry `TOOL_MODULES`
manifest, imports and `__all__`, matching existing vendor-tool discovery facades.

### 11.4 Shared runtime seams

- `core/tool/contracts.py` enforces string `minLength` during public-input validation so an empty
  string cannot reach the approval hook. It also defines and propagates a generic
  `log_omitted_input_fields` tuple on `BaseTool`/`RegisteredTool`.
- `core/tool/execution.py` and the tool contract expose a generic pre-approval input-preparation seam.
  It returns either safe normalized public arguments or a validation/authorization error. The prepared
  arguments are the ones shown for approval and executed afterward; injected credentials are still
  resolved only after approval. This seam must remain vendor-neutral and must not contain Feishu names.
- `core/agent_harness/tools/tool_provider.py` replaces values named by
  `log_omitted_input_fields` before building the ordinary action-log preview. The send tool omits
  `("message", "target")`; the reply tool also omits `"message_id"`. Do not add a Feishu-tool-name
  branch to shared code. Approval preview and in-turn
  tool-call history retain their existing separately bounded/redacted policies.
- Both tools opt into `AgentToolContext` to read gateway metadata and the cooperative cancellation
  probe. They do not import a gateway module or invent another cancellation signal.

### 11.5 Discovery

Add `integrations.feishu.tools` to `INTEGRATION_TOOL_PACKAGES`. S8a does not register a Feishu action
prompt fragment. S7 will later own runtime prompt/persona exposure after these tool contracts exist.

## 12. End-to-end data flow

```text
model tool call
  -> public schema validation
  -> canonical target resolution + authorization
  -> surface approval hook
  -> live config reload + exact approved-target reauthorization
  -> [reply only] GET message metadata -> exact chat_id comparison
  -> paginate Markdown
  -> create CardKit page + create/reply message (stable UUID)
  -> on definitely-unsent card failure, resume remaining source through create/reply text chunks
  -> on ambiguous create/reply, stop without fallback
  -> normalized certainty/partial result
  -> agent summarizes the actual result
```

No branch in this flow stores the outbound body outside the existing turn/tool trace policy. No branch
passes fetched source-message content to the model.

## 13. Validation and failure behavior

| Condition | Result | Side effect |
| --- | --- | --- |
| empty message / malformed target | validation error | none |
| `current` outside a Feishu gateway turn | authorization error | none |
| missing default target | configuration error | none |
| explicit target absent from allowlist | authorization error | none |
| reply target not `chat_id` | validation error | none |
| metadata fetch missing/ambiguous/deleted | validation or authorization error | none |
| metadata chat differs from approved target | authorization error | none |
| missing/invalid credentials | configuration error | none |
| definite Feishu rejection before any confirmed page | failed, retry-safe | none confirmed |
| timeout/transport ambiguity before any confirmed page | failed, not retry-safe | maybe sent |
| later definite failure | partial, not retry-safe | earlier pages confirmed; remainder unsent |
| later ambiguous failure | partial, not retry-safe | earlier pages confirmed; current range maybe sent |
| reply parent revoked/deleted during race | failed/uncertain reply | never fresh-send fallback |

Expected external failures return structured results. Unexpected exceptions pass through the existing
tool telemetry boundary with only exception type/stable category exposed externally.

## 14. Privacy and observability

- Never log app secrets, tenant tokens, full SDK responses, outbound bodies or fetched source-message bodies.
- Do not log raw integration-store records or full tool kwargs.
- Structured logs may include tool name, normalized receive ID type, target class (`current`, `default`,
  `allowlisted`), page count, confirmed count, status, certainty and stable error category.
- Hash or omit receive/message IDs in general logs; security audit rows may use the existing scoped resource
  identifiers but must not contain content.
- Approval previews continue through the shared redaction and length cap.
- The existing shared action logger currently previews public tool arguments, so the generic field-omission
  seam in §11.4 is a delivery requirement, not an optional hardening item. The message may remain in the
  bounded approval preview and existing in-turn tool-call history, but it must not be copied into ordinary
  application logs.
- Tool results may return confirmed message IDs because they are required for follow-up operations, but never
  return the outbound content, source-message body or credentials.

## 15. Concurrency, cancellation and shutdown

- Both tools are sequential (`parallel_safe=False`).
- Target authority is rechecked after approval, immediately before metadata preflight/delivery.
- A cancellation observed before the first send returns without attempting delivery.
- The tool polls the existing runtime cancellation probe before metadata lookup, before each page/chunk,
  and after every SDK call. Cancellation after a confirmed page stops further pages and returns `partial`;
  it cannot recall confirmed messages. A cancellation that races an in-flight call is classified from that
  call's actual response or ambiguity rather than assumed not sent.
- Gateway shutdown denies pending approvals through the existing broker and does not start new sends.
- The tool does not hold or mutate Feishu conversation locks; those serialize turns, while tool execution is
  already part of the owning turn.

## 16. Test strategy

### 16.1 Target configuration and authorization

- parse/deduplicate all supported exact target types;
- reject malformed entries without silently dropping them through both direct verification and
  guided setup;
- configured default is authorized without duplication;
- `current` resolves only for a Feishu gateway context;
- another gateway's chat metadata never authorizes Feishu delivery;
- explicit cross-chat target must be allowlisted.
- approval receives the exact canonical target for `current`, `default` and explicit forms;
- changing/removing the default or allowlist while approval is pending fails closed and never redirects
  the call to a newly configured target.

### 16.2 Tool contracts and discovery

- public schemas contain no credentials and require a visible target;
- public schemas reject empty string fields before the approval hook;
- shared runtime validation demonstrably enforces `minLength` rather than only advertising it to the model;
- both tools declare external side effect, approval and sequential execution;
- tools are discovered only when configuration/context can execute them;
- non-Feishu gateway turns do not receive the tools;
- no S8a prompt/persona fragment is registered.

### 16.3 Reply isolation

- realistic `GetMessageResponse` fixture with matching `chat_id` proceeds;
- mismatched, missing, multiple, deleted and malformed items fail before the reply API;
- direct-message/open-id configuration cannot stand in for an unverified chat ID;
- fetched body is absent from result, logs and approval metadata;
- `reply_in_thread` reaches both CardKit and text fallback request bodies.

### 16.4 Delivery correctness

- Markdown table/code pagination preserves contiguous source ranges exactly once;
- send and reply paths return confirmed IDs;
- a definitely-unsent card failure resumes text fallback at the unconfirmed cursor;
- an ambiguous card create/reply outcome stops without text fallback or duplicate content;
- fallback reply never calls create-message;
- revoked reply target never falls back to create-message;
- request UUID is reused only for retry of the same logical page;
- multi-page partial and ambiguous transport outcomes are not retry-safe.
- cancellation before the first write, between confirmed pages, and racing an in-flight call preserves the
  result matrix in §10.4.

### 16.5 Approval and regression

- denied/expired/unauthorized approval produces zero delivery calls;
- malformed or unauthorized targets are rejected before an approval prompt;
- the canonical target remains visible when an oversized message makes the approval preview truncate;
- only the original Feishu requester can approve in the original chat;
- S5a approval, S5b feedback, S5c retry/reactions and S6 proactive delivery stay green;
- Slack/Telegram/Rocket.Chat/Buzz tool discovery and behavior remain unchanged;
- import graph, integration API border and lightweight facade tests stay green.
- ordinary action logs omit the `message` field while approval preview and in-turn tool history keep their
  documented bounded behavior.

## 17. User-facing documentation and setup

The implementation PR updates `docs/messaging/feishu.mdx` and existing setup material with only
operator-relevant facts:

- configure a default and/or exact allowed outbound targets;
- send and reply calls always require approval;
- replies require message-read permission for target verification;
- long Markdown is delivered as paginated cards with lossless fallback;
- partial/uncertain results must be checked before retrying.

Do not document SDK classes, internal modules, endpoint paths, response codes or historical bugs. Add the new
public env var to `.env.example`; never add `.env` or real IDs/secrets.

## 18. Live Feishu acceptance

Approval of this design authorizes no live mutation. Before acceptance, obtain a fresh bounded authorization
that names the test chat(s), message count, approvals and local writes.

Minimum proposed acceptance:

1. `feishu_send_message(target="current", ...)` displays an approval card and sends only after the original
   requester approves;
2. denial sends nothing and the agent reports the denial;
3. one long Markdown/table payload arrives completely, in order, with no truncation;
4. `feishu_reply_message` replies to an existing message in the authorized test chat, once with
   `reply_in_thread=false` and once with `true` if the client visibly supports the topic/thread UX;
5. a deliberately wrong/disallowed target is covered locally unless a second authorized test chat is
   available; do not probe another real chat without explicit authorization;
6. logs/results contain no credential, outbound body, fetched source-message body or raw SDK response.

The absence of a second test user/chat does not block S8a. Cross-chat rejection remains deterministic-test-only
until the user supplies and authorizes a second destination.

## 19. Rollout and rollback

### 19.1 Rollout

1. Land config, target authorization and metadata preflight tests before tool registration.
2. Land shared create/reply document delivery and certainty tests.
3. Register the tools only after the safe target and delivery layers are complete.
4. Update setup/docs and perform fresh bounded live acceptance.
5. Keep S7 and S8b out of the implementation PR.

No feature flag or unsafe parallel legacy path is added. Tool absence is the rollout gate until discovery is
registered.

### 19.2 Rollback

- Reverting S8a removes tool discovery and the new outbound setting; existing gateway replies, S6 delivery and
  configured Feishu credentials remain functional.
- Existing target configuration may remain inert in the store/environment after rollback.
- No queue, migration or content store requires cleanup.
- Already delivered messages are not recalled automatically.

## 20. Implementation-plan outline

After this design is approved, write a separate implementation plan with exact edits and RED/GREEN checkpoints:

1. target config/parser and setup validation;
2. metadata-only message lookup and cross-chat authorization tests;
3. shared create/reply CardKit/text delivery with UUID and certainty semantics;
4. send/reply tool contracts, discovery and approval integration;
5. docs, setup examples and focused regression suite;
6. bounded live acceptance, PR, CI/review and post-merge monitoring.

The implementation plan must identify one-PR-sized commit boundaries, focused commands from `CI.md`, rollback
points and the exact live-operation budget. It may refine filenames but cannot weaken this design's authority,
preflight, approval, content-completeness or certainty contracts without updating this spec and obtaining new
approval.

## 21. Delivery gates

1. This design receives explicit user approval.
2. Write and obtain approval for the independent implementation plan before product/test/config changes.
3. Implement S8a through TDD; keep S7 and every S8b agent-visible read/reaction tool out of the PR.
4. Run the scoped lint, format, typecheck, Feishu tool/delivery/config, approval, registry, border and transport
   regression tests selected through `CI.md`.
5. Run the required pre-push gate and create the PR with the template and AI disclosure.
6. After every push, monitor checks, fetch failing logs and fix root causes.
7. Address actionable review, close resolved conversations and reach Greptile 5/5.
8. Obtain fresh bounded authorization and complete real Feishu acceptance before merge.
9. After merge, monitor main CI, full CodeQL and release workflows; fix or revert any failure.

## 22. Approval boundary

This document fixes the S8a design only. Before explicit approval, do not modify product code, tests, runtime
configuration or user-facing documentation beyond roadmap/spec bookkeeping. After approval, write the separate
implementation plan and request approval again. Live Feishu actions always require a separate, current and
bounded authorization.
