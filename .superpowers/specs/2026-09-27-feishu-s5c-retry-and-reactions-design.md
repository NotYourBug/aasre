# Feishu S5c Retry and Reaction Shortcuts Design

- Date: 2026-09-27
- Status: Approved by user (2026-09-27); implemented locally, pending live acceptance and PR
- Parent roadmap: [`2026-09-12-feishu-capability-completion-design.md`](2026-09-12-feishu-capability-completion-design.md)
- Scope: S5c only

## 1. Purpose

S5c adds an explicit `🔄 重试` action to a successfully completed Feishu answer and maps two
message reactions to the same server-side business actions:

- `THUMBSUP` is a shortcut for the existing S5b `✅ 采纳` positive-feedback action;
- `CrossMark` is a shortcut for `🔄 重试` and may start one new metered turn.

The official Feishu reaction identifier is `CrossMark`, not the parent roadmap's historical
`CROSS`. The official list also confirms the S5b amendment that `EYE` is unavailable. S5c does
not add, remove, or list reactions; it listens to reaction-created and reaction-deleted events.

Retry is a deliberate, short-lived capability. It is not durable replay. The original normalized
prompt may contain user text and extracted attachment content, so S5c keeps it in memory only.
Restarting the gateway, rotating the session, accepting another user turn, or reaching the one-hour
expiry invalidates retry. S5b positive-feedback authority and JSONL durability remain unchanged.

## 2. Goals

1. Append one combined final-answer action row containing `✅ 采纳` and `🔄 重试` after a complete,
   successful CardKit delivery.
2. Route exact `retry_id` callbacks without weakening S5a approval or S5b feedback authorization.
3. Listen to `im.message.reaction.created_v1` and `im.message.reaction.deleted_v1` through the
   existing WebSocket event dispatcher.
4. Map `THUMBSUP` to S5b good feedback and `CrossMark` to the same retry claim used by the button.
5. Require the current allowlist, original requester, original chat, current session, latest-answer
   generation, unexpired lifetime, and one-shot claim before starting a retry.
6. Ensure one retry capability produces at most one metered turn across callback redelivery,
   reaction replay, concurrent button/reaction actions, and add/delete/add event sequences.
7. Defer reaction intent received while streaming; never run two turns for one conversation at once.
8. Let a reaction deletion cancel only a not-yet-executed pending intent. Deletion never compensates
   or reverses a feedback write or a dispatched turn.
9. Reuse the existing cancellation, timeout, approvals, output, metering, storage-scope, usage-context,
   and conversation-lock pipeline for the retry turn.
10. Preserve S5b's metadata-only feedback row and all other transport behavior.

## 3. Non-goals

S5c does not implement:

- durable retry across process restart;
- persistence of the question, normalized prompt, answer, attachment text, card body, or callback body;
- arbitrary replay of an old answer after a newer accepted user turn;
- a `bad` feedback verdict or a negative-feedback dataset;
- automatic retry after model/provider failure, timeout, user cancellation, or shutdown;
- reading the source message through Feishu APIs; that belongs to S8b;
- agent-initiated send/reply tools, which belong to S8a;
- channel-aware prompt changes, which belong to S7;
- reaction creation/deletion APIs, an ack reaction, or a replacement for the withdrawn S5b `EYE`;
- a new public callback server, polling loop, deployment, or control-plane mutation;
- changes to Slack or Discord feedback semantics.

## 4. Confirmed platform and SDK contracts

### 4.1 Reaction vocabulary

The official Feishu emoji table lists `THUMBSUP` and `CrossMark`. Identifier matching is exact and
case-sensitive in S5c. `CROSS`, `EYE`, aliases, Unicode glyphs, and tenant-defined emoji names are
not accepted as business actions.

Reference:
<https://open.feishu.cn/document/server-docs/im-v1/message-reaction/emojis-introduce>

### 4.2 Created and deleted events

The locked `lark-oapi 1.7.3` SDK provides:

- `P2ImMessageReactionCreatedV1` and
  `EventDispatcherHandlerBuilder.register_p2_im_message_reaction_created_v1()`;
- `P2ImMessageReactionDeletedV1` and
  `EventDispatcherHandlerBuilder.register_p2_im_message_reaction_deleted_v1()`.

Both event bodies expose:

- `message_id`;
- `reaction_type.emoji_type`;
- `operator_type`;
- `user_id.open_id` plus optional user/union IDs;
- `app_id`;
- `action_time`.

The event header exposes `event_id`, event type, creation time, app and tenant identifiers. The event
body does not expose `chat_id`. Authorization must therefore resolve a server-owned record by answer
`message_id`, then compare the event's `open_id` with the stored requester and apply the stored chat
to the current identity policy.

References:

- <https://open.feishu.cn/document/server-docs/im-v1/message-reaction/event/created>
- <https://open.feishu.cn/document/server-docs/im-v1/message-reaction/event/deleted>

### 4.3 Event subscription prerequisites

The application must subscribe to both event types and publish the changed application version. Each
event page permits either the reaction-view permission or the direct/group-message read permission.
Because S5c authorizes by `open_id`, the application must also have the user-ID field permission shown
on the event page. Missing event or field permission fails closed: no feedback and no retry are inferred.

S5c does not require reaction write permission because it never creates or deletes a reaction.

## 5. Approved alternative and rejected alternatives

### 5.1 Approved scheme A: memory-only retry capsule

Keep one bounded, in-process retry record for the latest answer generation in each conversation. The
record contains the normalized prompt needed to run the same request, but no record is written to disk.
The record is invalidated by a new accepted turn, `/new`, one-hour expiry, terminal failure/cancel/timeout,
gateway shutdown, or process restart.

Benefits:

- no new persistence of user content;
- no dependency on future read-message tools or permissions;
- O(1) message/action lookup and deterministic concurrency;
- explicit stale-card and session-rotation behavior.

Trade-off: a retry button visible on an old card may return a private unavailable receipt after restart,
rotation, expiry, or a newer turn. The existing adoption button remains independently durable for 14 days.

### 5.2 Rejected: durable retry capsule

Persisting the normalized prompt and attachment-derived context would preserve retry across restart, but
would create a new sensitive-content store with encryption, deletion, retention, migration, corruption,
and cross-process coordination requirements. S5c does not justify that data expansion.

### 5.3 Rejected: refetch the source message

Fetching the original message on click avoids local prompt persistence, but introduces S8b message-read
API ownership, source-message deletion behavior, attachment reconstruction, pagination, permissions, and
network latency into the callback path. It violates the project boundary and cannot recreate the exact
normalized prompt in all cases.

### 5.4 Rejected: retry every reaction add/delete cycle

Allowing remove-and-readd to create unlimited billable retries makes a stale answer an unbounded execution
surface. One answer generation has one retry capability. Deleting before execution releases a pending
intent; once dispatched, the capability is permanently consumed.

## 6. Existing system and reusable assets

- `gateway/transports/feishu/worker.py` owns WebSocket registration, the bounded turn slots, cancellation
  registry, conversation locks, and dispatch onto the turn executor.
- `gateway/transports/feishu/inbound_handler.py` owns security, principal/session resolution, attachment
  normalization, metering, timeout, cancellation, approvals, output, and terminal arbitration.
- `gateway/transports/feishu/card_stream.py` knows each CardKit message ID and the certain final card target.
- `gateway/transports/feishu/turn_output.py` exposes only a complete, successful final target and disqualifies
  feedback on errors, cancellation, timeout, shutdown, or uncertain delivery.
- `gateway/transports/feishu/feedback.py` owns current-policy authorization and metadata-only S5b writes.
- `gateway/transports/feishu/feedback_authority.py` keeps durable opaque-button authority for adoption.
- `gateway/transports/feishu/card_actions.py` routes exact callback key sets for S5a and S5b.
- `integrations/feishu/feedback_cards.py` builds the current adoption button.
- `ConversationLockRegistry` serializes execution for one `(chat, requester)` conversation.

The missing seams are a per-answer interaction lifecycle, CardKit message observation during streaming,
one combined action-row publisher, reaction event routing, and a safe way to replay an already normalized
prompt through the existing metered turn pipeline while checking the original session ID.

## 7. Package ownership

### 7.1 `gateway/transports/feishu/reply_actions.py`

Own the per-answer interaction registry and orchestration. Responsibilities:

- begin an answer generation after authorization/session resolution and prompt normalization;
- invalidate the prior generation for the same conversation;
- register every interactive answer `message_id` emitted by the CardKit session;
- accept created/deleted reaction observations;
- mark success with the certain final card/message target;
- expose a single final-action publication request;
- atomically claim or cancel pending good/retry intent;
- enforce latest generation, session ID, expiry, one-shot retry, shutdown, and bounded indexes;
- return a prepared retry request without running the turn itself.

It performs no SDK network calls, JSONL writes, principal lookup, model invocation, or billing.

### 7.2 `gateway/transports/feishu/retry.py`

Own retry-specific callback parsing, current identity/session reauthorization, bounded background admission,
and handoff to the worker's turn dispatcher. It never persists prompt content. The callback only extracts the
opaque token, actor and chat, admits bounded work, and returns a private receipt within the callback window.

The retry service uses `reply_actions` for atomic claim. It does not reuse S5a `PendingApprovals` or S5b
durable feedback authority because their lifecycle and business effects differ.

### 7.3 `gateway/transports/feishu/feedback.py`

Retain ownership of positive feedback. Add an internal entrypoint that accepts an already-authorized,
server-owned reply-action record for a `THUMBSUP` shortcut and calls the same
`append_feedback_entry_once()` path as the button. Button-token authority remains durable and independent.

A reaction write and later adoption-button click converge on the existing `(final_message_id, actor)`
idempotency key and therefore still produce one JSONL row.

### 7.4 `gateway/transports/feishu/final_actions.py`

Coordinate one post-stream CardKit append:

1. ask S5b feedback authority for an opaque adoption token;
2. obtain the in-memory retry token from the completed reply-action record;
3. build one action row containing both buttons;
4. append once using the final target's next sequence and one UUID;
5. preserve S5b's definite-failure invalidation and uncertain-result behavior;
6. leave reaction shortcuts usable even if the component append fails.

This module is orchestration only. It delegates card JSON to the integration package and authorization/state
to the two specialist services.

### 7.5 `integrations/feishu/reply_action_cards.py`

Replace `feedback_cards.py` with the canonical Feishu provider UI module. It builds the Card JSON 2.0 action
row from an opaque `feedback_id` and opaque `retry_id`. Each button value contains exactly one key; no prompt,
answer, actor, chat, message, session, verdict, or business conclusion appears in the card payload.

All imports/tests migrate in the same change and the old module is removed; no compatibility-only forwarding
module remains.

### 7.6 `gateway/transports/feishu/inbound_handler.py`

Extract the already-authorized, already-resolved execution body into a focused helper used by both normal
inbound turns and retries. The retry path must:

- re-evaluate the current identity policy;
- acquire the normal conversation lock;
- resolve the current session and require its ID to equal the capsule's session ID;
- use the capsule's already normalized prompt without re-downloading attachments;
- enter the same usage, metering, timeout, cancellation, approvals and output contexts as a normal turn.

This is a behavior-preserving extraction for ordinary inbound messages and needs characterization tests before
the refactor.

### 7.7 `gateway/transports/feishu/worker.py`

Remain the thin event/dispatch owner:

- register created/deleted reaction handlers on the existing dispatcher builder;
- normalize SDK event fields and pass them to `reply_actions`;
- release the current turn slot before dispatching a deferred streaming-time retry;
- schedule retries through the same bounded turn executor and semaphore;
- log fixed safe categories only;
- close retry admission and clear capsules during shutdown.

### 7.8 `config/constants/feishu.py`

Own the exact reaction identifiers, one-hour retry expiry, queue bound, and any new state filename-free static
values. S5c adds no environment variable and no retry file path.

## 8. Data model

### 8.1 Reply action record

One in-memory record contains:

- random generation ID;
- random opaque retry token and its SHA-256 lookup digest;
- conversation key;
- requester `open_id`;
- original `chat_id`;
- original inbound message/thread reply metadata needed for output threading;
- normalized prompt text, including already extracted attachment context;
- session ID used by the original turn;
- monotonic creation and expiry times;
- a set of observed CardKit answer message IDs;
- final `message_id`, `card_id`, and append sequence after certain success;
- terminal state;
- latest reaction timestamp per `(message_id, actor, emoji_type)`;
- pending-good and pending-retry flags;
- retry claim state.

The registry does not contain credentials, answer text, card JSON, raw event/callback bodies, SDK responses,
approval data, feedback rows, or exception details.

### 8.2 Indexes and bounds

Use O(1) maps:

- conversation key to latest generation;
- each observed answer message ID to generation;
- retry token digest to generation.

Each conversation retains only its latest live or terminal generation until expiry. Replacement removes all
indexes for the previous generation. A global upper bound matching the gateway's bounded conversation state
prevents unbounded growth if many unique conversations arrive; expired/invalidated entries are pruned on every
mutation and shutdown clears all state.

## 9. State machine

```text
PREPARING
  ├─ first CardKit message observed ───────────────> STREAMING
  ├─ setup/output failure ─────────────────────────> INVALIDATED
  └─ newer accepted turn or /new ──────────────────> INVALIDATED

STREAMING
  ├─ THUMBSUP created ─────────────────────────────> pending_good = true
  ├─ THUMBSUP deleted before execution ────────────> pending_good = false
  ├─ CrossMark created ────────────────────────────> pending_retry = true
  ├─ CrossMark deleted before execution ───────────> pending_retry = false
  ├─ certain successful final target ──────────────> READY
  └─ failure/cancel/timeout/shutdown/uncertain ────> INVALIDATED

READY
  ├─ authorized good claim ────────────────────────> good write once; remain READY
  ├─ authorized retry claim ───────────────────────> RETRY_CLAIMED
  ├─ reaction delete after good/claim ─────────────> no compensation
  └─ newer turn, /new, expiry, shutdown ───────────> INVALIDATED

RETRY_CLAIMED
  ├─ accepted by turn dispatcher ──────────────────> RETRY_DISPATCHED
  ├─ dispatcher unavailable/full or session drift ─> INVALIDATED
  └─ reaction delete ──────────────────────────────> no compensation

RETRY_DISPATCHED
  └─ any later event ──────────────────────────────> no second retry
```

`THUMBSUP` and `CrossMark` are independent business actions. One answer may be adopted and retried. The retry
side alone is consume-once. S5b feedback idempotency remains `(final_message_id, actor)`.

## 10. Reaction ordering and replay

### 10.1 Actor and type validation

Ignore events unless:

- `operator_type == "user"`;
- `message_id`, `user_id.open_id`, `action_time`, and emoji type are present and bounded;
- emoji type is exactly `THUMBSUP` or `CrossMark`;
- the message ID maps to a current server-owned reply-action record.

Unknown messages, app-originated reactions, wrong emoji types and malformed events do not create state.

### 10.2 Created/deleted ordering

For each `(message_id, actor, emoji_type)`, apply only an event whose numeric `action_time` is newer than the
last applied timestamp. Equal or older events are redelivery/out-of-order no-ops. Header `event_id` may be used
for diagnostic correlation but is not the business idempotency key.

Created sets the pending flag while streaming. Deleted clears it only while the corresponding action has not
been executed. After a good write or retry claim, deletion records no compensating action.

### 10.3 Multiple cards for one answer

CardKit overflow can emit several message IDs. Every interactive answer card created for the generation maps
to the same record. A valid reaction on any of those messages acts on the one final answer generation. Positive
feedback is still keyed to the certain final answer `message_id`, not the page on which the reaction appeared.

If delivery degrades to pure text or the final target is uncertain, all message indexes for the generation are
invalidated and pending reactions are discarded.

## 11. Authorization

### 11.1 Button callback

The retry button value is exactly:

```json
{"retry_id": "<random-opaque-token>"}
```

Validation requires event/operator/context/action, `action.tag == "button"`, an exact one-key mapping, bounded
non-empty token, bounded actor `open_id`, and bounded callback `open_chat_id`. The background worker then:

1. applies the current Feishu allowlist/identity policy;
2. looks up the retry token digest;
3. matches original requester and original chat;
4. checks latest generation, expiry and `READY` state;
5. checks the current session binding still equals the stored session ID;
6. atomically claims retry.

Unknown, expired, wrong-actor, wrong-chat, wrong-session and consumed tokens share a private unavailable result.
They do not reveal which predicate failed and never mutate the shared card.

### 11.2 Reaction event

Because the event has no chat ID, the handler first finds the record by server-owned message ID. It then:

1. compares `user_id.open_id` with the stored requester;
2. applies current authorization using the stored original chat;
3. checks generation, expiry and session ID;
4. applies the ordered reaction transition.

No event field supplies or overrides chat, session, prompt, verdict, or retry state.

## 12. Turn dispatch and billing

### 12.1 Callback timing

Card callbacks must not wait for policy files, session storage, disk persistence, network, model, tools, or a
turn. The callback parses bounded metadata and attempts non-blocking admission to a bounded retry queue:

- admitted: private `已收到` receipt, which is not a promise that a turn will start;
- queue full/closed: private `暂时繁忙，请稍后重试` receipt;
- malformed OpenSRE action: private generic unavailable receipt.

Reaction event handlers likewise do no turn work inline. They admit bounded metadata or perform only the
registry's bounded in-memory transition.

### 12.2 Deferred streaming-time retry

A `CrossMark` created during streaming sets pending intent only. At successful completion, it becomes eligible,
but dispatch waits for the original turn's done callback to:

1. release its turn semaphore slot;
2. unregister its cancellation event;
3. submit the prepared retry through normal bounded dispatch.

This ordering works when `max_concurrent_turns == 1` and prevents self-deadlock or dropped retry.

### 12.3 Metering

Only a successfully claimed and admitted retry enters `bound_turn_metering(reason="feishu_turn")`. Replayed
events, duplicate callbacks, queue rejection, stale session, expiry, or dispatch failure do not invoke the model
and do not consume a turn. Once dispatch is accepted, reaction deletion does not undo billing or execution.

### 12.4 Retry output

The retry runs the exact normalized prompt captured for the original turn, including attachment-derived text,
without re-downloading attachments. It runs in the same session ID and replies using the original inbound
thread metadata. Its answer is a new generation with its own adoption/retry actions; the source generation's
retry stays consumed.

## 13. Final card actions

### 13.1 Visibility

Buttons appear only when the normal-success terminal outcome wins and `CardStreamSession.finish()` returns a
certain final target. Streaming updates never contain the buttons. Error, cancel, timeout, shutdown, partial
overflow, uncertain close, failed delivery, and pure-text fallback expose no new buttons.

### 13.2 One append

The final component append contains one action row with:

- `✅ 采纳`, value exactly `{"feedback_id": "..."}`;
- `🔄 重试`, value exactly `{"retry_id": "..."}`.

The append uses one sequence and UUID. It is attempted once. A definite failure invalidates the new adoption
token but leaves reaction shortcuts bound to the successfully delivered answer. An uncertain result is never
retried because the buttons may already be visible; feedback authority follows S5b's existing uncertain-result
rule and the in-memory retry token remains safe and one-shot.

### 13.3 Callback router

The exact key-set router becomes:

| `action.value` keys | Route |
| --- | --- |
| exactly `{approval_id}` | S5a approval |
| exactly `{feedback_id}` | S5b adoption |
| exactly `{retry_id}` | S5c retry |
| any known key plus extra/another known key | private generic rejection |
| unknown key set | empty response |

No handler may reinterpret another action's token or downgrade malformed known actions to an unknown action.

## 14. Session rotation, newer turns, expiry, and restart

- An authorized `/new` invalidates the prior generation before the new binding becomes usable.
- Any newly accepted ordinary turn invalidates the previous answer's retry before becoming the latest generation.
- Denied, pairing, help, unsupported-message and no-turn paths do not replace the latest valid generation.
- Retry authority expires one hour after the original generation begins. This is a fixed code constant, not a
  user configuration value.
- Process restart loses every retry capsule and pending reaction. Old retry buttons receive unavailable; old
  reaction events are ignored.
- Durable S5b adoption buttons continue to work according to their independent 14-day authority.
- Shutdown closes queue admission, prevents deferred dispatch, invalidates capsules, and does not wait
  unboundedly for new turns.

## 15. Failure behavior

| Failure | Required behavior |
| --- | --- |
| reaction subscription/field permission missing | no shortcut; buttons remain available |
| malformed/unknown reaction event | ignore without state or user message |
| wrong actor or current allowlist denial | no good write, no retry, no existence disclosure |
| reaction during stream then delete | cancel pending intent |
| stream ends with failure/cancel/timeout/shutdown | invalidate pending intent; no retry |
| action-row append fails | answer remains success; reactions remain usable |
| retry queue full/closed | private retry-later for button; reaction no-op |
| current session differs | invalidate retry; no metered turn |
| turn semaphore/executor unavailable | consume/invalidate claim conservatively; no hidden repeated dispatch |
| duplicate/replayed callback/event | no second feedback row or turn |
| feedback JSONL write fails | preserve S5b retryable feedback authority behavior; no retry impact |
| gateway restart | retry unavailable; adoption durability unchanged |

## 16. Privacy and observability

The normalized retry prompt is sensitive and memory-only. It must never enter logs, exception messages exposed
to users, feedback JSONL, retry authority files, card values, metrics, test snapshots, PR descriptions, or live
acceptance evidence.

Allowed log fields are fixed operation/outcome categories, state name, bounded counts, elapsed bucket and short
one-way correlation digests when needed. Do not log full message/chat/open/session IDs, raw token, prompt, answer,
attachment content, callback/event payload, SDK response body, `str(exc)` or `repr(exc)` where vendor/user data
may be embedded.

No retry persistence file is created. Existing S5b files and schema remain unchanged.

## 17. Test strategy

Use red-green-refactor. Add one high-signal test for each distinct failure or concurrency class.

### 17.1 Official vocabulary and event normalization

- exact `THUMBSUP` and `CrossMark` are accepted;
- historical `CROSS`, `EYE`, Unicode glyphs and unknown emoji are ignored;
- created/deleted event models extract message, actor, operator type, emoji and action time;
- missing actor field permission fails closed;
- app-originated and malformed events do nothing.

### 17.2 Reply-action registry

- every CardKit page message maps to one generation using sets/maps, not linear scans;
- a new accepted turn invalidates the previous generation and all indexes;
- one-hour expiry, `/new`, shutdown and restart semantics;
- wrong actor/chat/session does not consume rightful authority;
- ordered create/delete/create uses action time and ignores replay/out-of-order delivery;
- concurrent button and CrossMark claims have exactly one winner;
- delete before claim cancels; delete after claim does not undo;
- registry remains bounded across many conversations.

### 17.3 Streaming lifecycle

- reaction during streaming never starts a turn immediately;
- pending CrossMark dispatches only after successful terminal and slot release;
- pending THUMBSUP writes only after a certain final message exists;
- failure, timeout, user cancel, shutdown, uncertain final target and pure-text fallback discard pending actions;
- overflow reactions on any answer card converge on the final message ID.

### 17.4 Callback and final action row

- one final append contains both buttons exactly once;
- each callback value has exactly one opaque key and no sensitive/business fields;
- mixed/extra keys fail closed without shared-card mutation;
- queue admission returns promptly while policy/session/disk/turn work is blocked;
- definite and uncertain append failures retain the specified S5b/S5c authority semantics.

### 17.5 Retry execution and billing

- replay uses the normalized prompt without another attachment download;
- current session equality is checked after acquiring the conversation lock;
- normal and retry turns share timeout, cancellation, approvals, output, usage, storage and metering contexts;
- one accepted action creates one metered turn; duplicates and stale actions create zero;
- max-concurrency one releases the source slot before retry submission;
- a retry answer becomes a new generation while the source retry remains consumed.

### 17.6 Feedback and regression

- THUMBSUP and adoption button converge on one good JSONL row;
- Slack/Discord feedback behavior remains unchanged;
- S5a approve/deny, S5b adoption, Feishu attachments, output streaming, overflow, session rotation, cancellation,
  timeout and shutdown suites remain green;
- integration API borders, import graph and lightweight facades remain compliant.

## 18. Documentation and control-plane prerequisites

Update the user-facing Feishu setup page only with operator-relevant facts:

- subscribe to reaction-created and reaction-deleted events and publish the application version;
- ensure one event-page message/reaction read permission and the user-ID field permission are present;
- `THUMBSUP` adopts; `CrossMark` and `🔄 重试` may start one metered turn;
- retry applies only to the latest answer in the same session and expires after one hour or restart;
- deleting a reaction after execution does not undo the action.

Do not document SDK classes, internal filenames, queue/state internals, endpoint paths, or historical bugs.

## 19. Live Feishu acceptance

Approval of this design authorizes no live Feishu mutation. Immediately before acceptance, request a new,
bounded authorization listing the configured test chat, new messages, cards, expected turns, user clicks,
reaction actions, visible messages and local writes. Do not reuse S5b authorization.

Proposed acceptance, subject to that later authorization and available test users:

1. one answer ends with exactly one adoption button and one retry button;
2. one requester `THUMBSUP` produces at most one good row; remove/readd after execution does not add another;
3. one requester `CrossMark` or retry-button click starts exactly one additional turn;
4. duplicate callback/event and button/reaction race do not start a second retry;
5. a deliberately slow streaming answer accepts then cancels a pending reaction before completion without a
   new turn, if the timing can be achieved without destabilizing the gateway;
6. `/new` or a newer accepted message makes the prior retry unavailable;
7. safe logs and files contain no prompt, answer, raw token, credentials or complete callback/event payload.

The previously reported lack of a second test user means bystander rejection remains deterministic-test-only
unless the user later supplies and authorizes another account. Do not block S5c on that unavailable live case.

## 20. Rollout and rollback

### 20.1 Rollout

1. Land deterministic tests and code before any live event subscription test.
2. Confirm the event subscriptions, one eligible read permission and user-ID field permission are published.
3. Obtain fresh bounded live authorization and execute the acceptance matrix in the configured test chat.
4. Missing reaction events degrade to the explicit buttons; missing retry state degrades to private unavailable.
5. Do not add a feature flag or dual text command solely for rollout.

### 20.2 Rollback

- Stop the gateway cleanly so no new retry is admitted.
- Reverting S5c removes retry buttons and reaction listeners; existing adoption buttons/data remain S5b-owned.
- Visible retry buttons from the reverted build become unknown actions and cannot start turns.
- No retry store requires deletion or migration because retry capsules were never persisted.
- Do not remove reaction event subscriptions or application permissions automatically; those are control-plane
  changes requiring separate operator action.

## 21. Delivery gates

1. This formal design receives explicit user approval.
2. Write a separate implementation plan covering S5c only and request approval before product/test changes.
3. Implement through TDD and keep S8a, S7 and S8b out of the PR.
4. Run focused lint, format, typecheck, Feishu, feedback, transport-regression and border tests from `CI.md`.
5. Run the required local pre-push gate, then create the PR using the template and AI disclosure.
6. After every push, monitor PR checks, fetch failing logs, fix root causes and re-run focused tests.
7. Address all actionable review and reach Greptile 5/5 with no unresolved conversations.
8. Obtain fresh bounded authorization and complete real Feishu acceptance before merge.
9. After merge, monitor main CI, full CodeQL and release workflows; fix or revert any post-merge failure.

## 22. Approval boundary

This document fixes the S5c design, not its implementation plan. Before explicit approval, do not modify
product code, tests or user-facing documentation. After approval, write the independent S5c implementation
plan with exact files, failing tests, focused commands, completion criteria and commit boundaries, then request
approval again. Live Feishu actions always require a separate, current, bounded authorization.
