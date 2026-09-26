# 飞书 S5b 已读与正式反馈设计

- Date: 2026-09-25
- Status: Approved by user (2026-09-25)
- Parent roadmap: [`2026-09-12-feishu-capability-completion-design.md`](2026-09-12-feishu-capability-completion-design.md)
- Scope: S5b only

## Approved amendment: bounded feedback worker (2026-09-25)

The user explicitly approved bounded background processing after implementation review showed that a file-lock timeout cannot
bound JSONL scanning or fsync. This amendment supersedes synchronous-persistence and immediate-success-toast language below.
The callback validates the exact payload shape, extracts only token/actor/chat, and attempts a non-blocking bounded queue admission.
An admitted request receives a private `已收到` receipt, which does not promise a durable write. A full or closed queue receives a
private retry message. The worker rechecks allowlist, original requester, original chat and current expiry against durable authority,
then performs the idempotent feedback write before consuming authority. Failed writes leave authority available for another click.
No callback or worker starts a turn, invokes a model, changes session content, mutates shared cards, or sends follow-up messages.
Queued requests are not durable: shutdown/crash may drop them; clicking again is safe. Blocked-disk tests must prove callback return
does not wait for either authority lookup or feedback persistence. Raw callback objects are never retained or logged.

## 1. Purpose

S5b 给飞书对话补齐两个互相独立但共享 turn 生命周期的交互：

1. OpenSRE 真正开始处理一条合法入站消息后，在用户原消息上添加 `EYE`（👀）；turn 进入任一终态后删除。
2. 正常回答完整交付到最终 CardKit 卡片后，在卡片末尾追加一次 `✅ 采纳` 按钮；只有原请求者在原 chat
   中可以把该回答按 `good` 写入 transport-neutral feedback JSONL。

👀 表示“正在处理”，不是永久已读标记。采纳表示一次幂等反馈记录，不批准工具、不启动新 turn、不重试回答。
两者都属于辅助交互：reaction 或组件 API 失败不得阻塞、取消或改写主 turn 的业务结果。

## 2. Goals

1. 在消息通过入站安全检查、principal/session 解析且没有预先取消后，best-effort 添加 👀。
2. 对成功、失败、用户取消、超时和 gateway shutdown 建立统一、可恢复的 👀 删除语义。
3. 对同一 inbound `message_id` 的并发或重放只创建一个本地 ack 生命周期、只放行一个 turn，并尽量避免重复 EYE。
4. 跨进程重启恢复本机器人遗留的 EYE；无法证明 reaction 归本机器人所有时 fail closed，不删除。
5. 只在正常成功且回答已完整落到一张可识别的最终 CardKit 卡片时追加 `✅ 采纳`。
6. 用随机不透明 token、原请求者 `open_id`、原 `chat_id` 和最终 `message_id` 在服务端重新授权 callback。
7. 按 `(final_message_id, actor_open_id)` 跨线程、跨进程、跨重启幂等写入一条 `verdict="good"`。
8. 复用现有 `card.action.trigger` 长连接与 transport-neutral feedback persistence，不改变 Slack/Discord 语义。
9. 回调只做有界内存入队，在飞书响应窗口内返回；授权与持久化由独立有界后台处理，不等待 turn。
10. 用确定性测试覆盖身份隔离、并发、重放、重启、shutdown、降级和不确定交付。

## 3. Non-goals

S5b 不实现：

- `THUMBSUP` / `CROSS` reaction 事件订阅、映射或删除处理；这些属于 S5c。
- `🔄 重试` 按钮、重新执行、计费 turn、旧卡重跑或会话轮换；这些属于 S5c。
- `bad` / 不采纳反馈；S5b 只有明确的正向 `good`。
- 通过 reaction 反馈、通过文本词语反馈，或把 👀 保留为永久阅读回执。
- 为纯文本 fallback、错误卡、取消卡、超时卡或不完整输出添加反馈按钮。
- 在 callback 中修改共享卡片、调用 CardKit/IM REST、调用模型或执行工具。
- 为其他 transport 改造反馈 UX，或改变 Slack/Discord 允许重复写入的现有行为。
- 新建公网 callback server；继续使用已经为 S5a 验证过的 WebSocket CARD-frame 兼容层。
- 把问题、回答、卡片正文、附件、原始 callback 或明文 token 写入任何反馈文件。

## 4. Confirmed decisions

### 4.1 Ack meaning and terminal matrix

`EYE` 的唯一含义是“OpenSRE 正在处理此消息”。它不表达成功、质量、审批或用户反馈。

| Turn outcome | EYE | Replacement reaction | Feedback button |
| --- | --- | --- | --- |
| success | remove | none | append only after complete final-card delivery |
| failure | remove | none | none |
| user cancel | remove | none | none |
| timeout | remove | none | none |
| gateway shutdown | remove now or reconcile on next startup | none | none |
| pre-cancel before processing | never add | none | none |
| security/pairing/help/no-turn path | never add | none | none |

Reaction add/delete failures are contained and logged with safe categories. They never become a turn failure, and no user-facing
error is sent merely because ack failed.

### 4.2 Feedback eligibility

The feedback button is issued only when all conditions hold:

- the handler returned normally and the normal-success branch won `TerminalOutcomeArbiter`;
- `FeishuTurnOutput` finalized a non-empty answer;
- every part of that answer was confirmed delivered;
- one final interactive CardKit message is known, with non-empty `card_id` and `message_id`;
- the final card has a known next monotonic sequence;
- no stream close, pagination, card delivery, or text fallback failure made the final target uncertain.

The target is the original streaming card for an in-budget answer. For a completely delivered overflow/degraded-card answer it is
the last successfully sent complete card. Pure-text fallback has no CardKit target and therefore never gets a feedback button.

### 4.3 Opaque callback payload

The appended button is a Card JSON 2.0 callback button whose value is exactly:

```json
{"feedback_id": "<random-opaque-token>"}
```

The token has at least 256 bits of CSPRNG entropy. The client never receives `open_id`, `chat_id`, `message_id`, verdict, answer,
question, card ID or authorization conclusion. The authority store keeps only a SHA-256 digest of the token; high token entropy,
not secrecy of the hash algorithm, prevents offline guessing.

### 4.4 Positive-only and no new turn

A valid first click writes `verdict="good"`. A replay for the same final message and actor returns an already-recorded private
toast and writes nothing. Neither path starts a turn, invokes the model, retries the answer, resolves an approval, or mutates the
shared card.

### 4.5 Retention

Feedback authority expires 14 days after registration. This is an OpenSRE retention decision aligned with CardKit's documented
14-day card-entity management window; it is not a claim that all client-side card interactions stop at exactly that time.

Expired authority cannot write feedback. Durable feedback rows are not deleted by authority expiry. Ack lifecycle records and
settled authority tombstones are compacted after the same 14-day bound so local state cannot grow without limit.

## 5. Verified platform and SDK contracts

The locked `lark-oapi 1.7.3` SDK contains the required request models and clients.

### 5.1 Message reaction

- Create: `POST /open-apis/im/v1/messages/:message_id/reactions`, body `reaction_type: Emoji`.
- Successful create returns `reaction_id`, `operator`, `action_time` and `reaction_type`.
- Delete: `DELETE /open-apis/im/v1/messages/:message_id/reactions/:reaction_id`.
- Delete is limited by the platform to reactions created by the calling identity.
- List: `GET /open-apis/im/v1/messages/:message_id/reactions`, filterable by reaction type, with pagination.
- Least privilege for create/delete is `im:message.reactions:write_only`; startup reconciliation additionally requires
  `im:message.reactions:read`.
- Create has no caller-supplied idempotency UUID. Crash recovery must therefore reconcile remote state rather than claim
  exactly-once creation.

References:

- <https://open.feishu.cn/document/server-docs/im-v1/message-reaction/create>
- <https://open.feishu.cn/document/server-docs/im-v1/message-reaction/delete>
- <https://open.feishu.cn/document/server-docs/im-v1/message-reaction/list>

### 5.2 CardKit element append

- Insert element: `POST /open-apis/cardkit/v1/cards/:card_id/elements`.
- Append to the card body uses `type="append"` without a target element ID.
- The request carries Card JSON 2.0 `elements`, a UUID idempotency key and a strictly increasing `sequence`.
- Permission: `cardkit:card:write`.
- The same app identity that created the card must mutate it; no exclusive-mode handoff is introduced.

Reference: <https://open.feishu.cn/document/cardkit-v1/card-element/create>

The official page currently describes a smaller generic card-size limit than the parent roadmap's 2026-09-16 measured CardKit
limits. S5b appends one small button component and does not change R32 or any pagination budget.

## 6. Existing system and gaps

### 6.1 Reusable behavior

- `gateway/transports/feishu/inbound_handler.py` already centralizes security, principal/session resolution, cancellation,
  timeout, metering and terminal arbitration.
- `gateway/transports/feishu/turn_output.py` already owns each turn's CardKit stream and pure-text fallback.
- `gateway/transports/feishu/card_stream.py` already owns card ID, message ID and sequence progression.
- `integrations/feishu/card_client.py` already centralizes authenticated CardKit calls.
- S5a already receives `card.action.trigger`, verifies allowlist/requester/chat, and returns private toast responses.
- `gateway/core/storage/feedback/jsonl.py` already provides a thread-safe append API used by Slack and Discord.

### 6.2 Missing contracts

- Feishu has no ack client, durable ack lifecycle or startup reconciliation.
- `CardStreamSession` discards IDs for overflow cards, hides final sequence, and swallows close/delivery failures.
- `FeishuTurnOutput` does not distinguish success from error/cancel/timeout and exposes no complete-delivery receipt.
- `FeishuCardClient` cannot append CardKit elements.
- S5a's callback entrypoint only routes approval payloads.
- Shared feedback append is process-local only and deliberately writes repeats; it cannot satisfy S5b restart idempotency.
- There is no durable feedback authority whose lifetime outlives one gateway process.

## 7. Ownership and package boundaries

### 7.1 `integrations/feishu/reactions.py`

Vendor API wrapper for create/list/delete message reactions. It builds SDK requests, validates non-empty IDs, maps responses to
small typed results and raises fixed internal exception categories. It does not know turns, sessions, allowlists, persistence or
shutdown.

### 7.2 `integrations/feishu/feedback_cards.py`

Pure Card JSON 2.0 builder for the `✅ 采纳` button element. It receives only the opaque token and returns the component list
accepted by the append API. It imports no gateway module and performs no I/O or authorization.

### 7.3 `integrations/feishu/card_client.py`

Add one `append_elements(card_id, elements, sequence, uuid)` operation using the locked SDK's CardKit element-create model.
`uuid` is required from the caller so a logical request has a stable idempotency identity. The method performs one API attempt;
it never retries internally.

### 7.4 `gateway/transports/feishu/reaction_lifecycle.py`

Sole owner of ack orchestration:

- in-process message-ID dedupe and state transitions;
- returning an admission result so an already active/settled inbound delivery cannot start a second turn;
- submitting add/delete work to a dedicated bounded side-effect executor;
- persisting lifecycle events and replaying them at startup;
- reconciling unfinished records before accepting new turns;
- draining/removing active ack reactions on shutdown;
- safe structured logging without raw message/chat IDs.

The worker creates one lifecycle manager for the app instance and injects it into inbound turn handling.

### 7.5 `gateway/transports/feishu/feedback.py`

Sole owner of Feishu feedback orchestration:

- final-card authority registration and invalidation;
- strict callback parsing and routing result;
- allowlist, requester, chat and expiry checks;
- calling the idempotent shared feedback writer;
- returning private success/already-recorded/unavailable/error toasts.

It does not build SDK requests directly and does not update the shared card in callback handling.

### 7.6 `gateway/transports/feishu/feedback_authority.py`

Dedicated durable authority registry, implemented as an append-only JSONL event ledger plus compacted snapshot rewrite under a
cross-process `FileLock`. It stores token digests and minimum authorization metadata, never raw tokens or content.

### 7.7 `gateway/core/storage/feedback/`

Keep `append_feedback_entry()` unchanged for Slack and Discord. Add a separate API, tentatively
`append_feedback_entry_once(entry, key, path) -> FeedbackWriteResult`, whose result is `WRITTEN`, `DUPLICATE` or `FAILED`.
The new API owns cross-process locking, reload-under-lock, malformed-tail handling, idempotency lookup and one-line append.

### 7.8 `config/constants/feishu.py`

Static filenames, retention values, lock timeout and reaction type live in the existing Feishu constants leaf and are re-exported
through `config.constants`. No new user configuration or environment variable is added.

### 7.9 Facades and imports

All touched `__init__.py` files remain import-and-`__all__` facades. Gateway code imports Feishu integration behavior through the
public `integrations.feishu` API where required by the existing border allowlist; vendor modules never import `gateway`.

## 8. Ack data model and state machine

### 8.1 Persisted fields

Each ack record contains only:

- inbound `message_id`;
- `reaction_id` once known;
- remote operator type/ID returned by create, when present;
- state;
- created/updated timestamps;
- terminal outcome category when known.

It does not contain chat ID, sender ID, session ID, message text, attachments, credentials or raw SDK responses.

### 8.2 States

```text
NEW
 └─ begin accepted ───────────────> ADDING
ADDING
 ├─ create success ──────────────> ACTIVE
 ├─ create failure ──────────────> ADD_FAILED
 └─ terminal arrives ────────────> TERMINAL_PENDING_ADD
TERMINAL_PENDING_ADD
 ├─ create success ──────────────> REMOVING
 └─ create failure ──────────────> ADD_FAILED
ACTIVE
 └─ any terminal outcome ────────> REMOVING
REMOVING
 ├─ delete success/not-found ────> REMOVED
 └─ delete failure/uncertain ────> REMOVE_FAILED
```

`ADD_FAILED`, `REMOVED` and `REMOVE_FAILED` are terminal local records. `REMOVE_FAILED` remains eligible for startup
reconciliation until retention expiry. A duplicate `begin(message_id)` observes the existing record, never calls create again,
and is not admitted to the handler. This is required for lifecycle correctness: allowing a duplicate turn would let the first
completion remove EYE while the second copy was still running, and could emit two billable answers and two feedback cards for one
platform delivery.

### 8.3 Ordering and non-blocking behavior

One dedicated bounded executor is owned by `ReactionLifecycleManager`. `begin` first performs a bounded local ledger admission and
then enqueues remote work; turn threads never wait for IM API completion. Per-message state and generation checks ensure a delete
cannot be lost when the terminal event races the add response. If local persistence, the queue or the manager is unavailable, ack
falls back to `UNTRACKED` and the turn proceeds: ack/dedupe support must never become a new availability dependency for the agent.

The executor is not the turn executor: a saturated or waiting turn pool must not prevent ack cleanup. Shutdown stops accepting new
begins, marks every active record terminal, submits/bounds cleanup, then closes the executor within the existing gateway shutdown
budget. Work that exceeds the budget remains durable for next-start reconciliation.

### 8.4 Start boundary

`begin(message_id)` occurs only after:

1. normalized user message validation;
2. inbound security/allowlist decision;
3. principal resolution;
4. session resolve/rotation;
5. active-cancel registration and the pre-cancel check.

Therefore `/stop`, pairing/help/denial paths, unsupported events and turns cancelled before execution never flash an EYE.

### 8.5 Terminal classification

Every exit calls the idempotent `finish(message_id, outcome)`:

- normal handler return and terminal claim: `SUCCESS`;
- handler or output exception: `FAILURE`;
- credits denied: `FAILURE`;
- `/stop`: `CANCELLED`;
- timeout callback: `TIMEOUT`;
- output/gateway shutdown: `SHUTDOWN`.

The classification is audit/test metadata only; all categories request the same EYE removal.

### 8.6 Crash and restart reconciliation

On worker startup, before readiness is announced:

1. replay and compact unexpired ack records under the file lock;
2. for records with a persisted `reaction_id`, attempt exact delete using `(message_id, reaction_id)`;
3. for `ADDING`/`TERMINAL_PENDING_ADD` without a reaction ID, list only EYE reactions for that message;
4. delete a listed reaction only when its operator identity is proven to be the configured app identity;
5. if ownership cannot be proven, log a safe unresolved category and leave it untouched;
6. persist reconciliation outcomes, then start accepting turns.

Reconciliation has a fixed startup time/record budget. Reaching it does not keep readiness blocked indefinitely: remaining records
stay durable for a later startup, and readiness proceeds with one fixed warning. The implementation plan must select limits that fit
the gateway's existing startup timeout and test the budget deterministically.

The live acceptance gate must verify how the current tenant-token response represents the app operator. Until that is proven, the
implementation may recover only exact persisted reaction IDs; it must not weaken ownership checks to make the test pass.

This provides at-least-once cleanup, not impossible exactly-once creation: a process can die after Feishu creates EYE but before
the returned reaction ID reaches disk. The list-and-owner check bounds that platform limitation safely.

## 9. Final-card delivery receipt

### 9.1 Receipt shape

`CardStreamSession.finish()` exposes a read-only receipt only after completion:

```text
FinalCardTarget(
  card_id,
  message_id,
  append_sequence,
)
```

No receipt means feedback is ineligible. The receipt is not a general delivery result and does not expose answer content.

### 9.2 Normal stream

For a healthy in-budget stream:

1. flush pending final text;
2. successfully close streaming mode;
3. select the original card/message as final target;
4. reserve the next sequence after the successful close.

If final flush or close raises, no receipt is produced, even if the user may see part or all of the answer.

### 9.3 Overflow and degraded cards

`_send_overflow` must retain the card/message IDs of each confirmed page and advance its delivery cursor only after the whole batch
lands, preserving the existing no-skip contract. When all remaining content is delivered, the last complete card becomes the final
target and its first mutation sequence is returned.

If any page create/send is rejected, uncertain or missing an ID, `_delivery_failed` is set and no receipt is produced. A stream
close failure anywhere in the session also makes the session feedback-ineligible, even if later fallback cards appear complete;
this deliberately favors a missing button over attaching feedback to an ambiguous transcript.

### 9.4 Pure-text fallback

`FeishuTurnOutput` continues sending all text chunks, but does not retain a target for feedback. No hidden card is created solely to
host a feedback button.

### 9.5 Outcome-aware finalization

`FeishuTurnOutput` records whether its terminal call represented normal success or a non-success terminal path. Only the inbound
handler's normal-success branch may request a receipt and append feedback. `render_error`, timeout, user-stop, credits-denied and
`shutdown` paths cannot accidentally reuse a visually complete status/error card as a feedback target.

## 10. Feedback authority model

### 10.1 Registration record

Before attempting the CardKit append, register:

- `token_digest`;
- requester `open_id`;
- original `chat_id`;
- final `message_id`;
- `created_at` and `expires_at` wall-clock timestamps;
- state `OPEN`.

Do not store `card_id`, raw token, question, answer, card content, session ID or callback payload. The card ID is required only for
the immediate append and is not authorization material.

### 10.2 Ledger events

The authority JSONL supports `REGISTERED`, `CONSUMED`, `INVALIDATED` and `EXPIRED` events. Replay reduces events by token digest.
Compaction writes only unexpired open authorities and bounded consumed tombstones to a temporary owner-only file, fsyncs it, and
atomically replaces the ledger while holding the same `FileLock`.

### 10.3 Registration before visibility

The authority is durable before the button can become visible:

1. generate token and CardKit UUID;
2. persist `REGISTERED`;
3. call append exactly once with the receipt's sequence;
4. on confirmed append success, finish normally;
5. on definite pre-send/rejection failure, append `INVALIDATED`;
6. on uncertain network/result failure, do not retry and leave authority open until expiry because the button may be visible.

An authority row without a visible button is inert. A visible button without durable authority would be permanently unusable, so
the reverse ordering is forbidden.

## 11. Callback dispatch and authorization

### 11.1 Thin router

The worker keeps one `card.action.trigger` registration and routes by exact value-key set:

| `action.value` keys | Route |
| --- | --- |
| exactly `{approval_id}` | existing S5a handler |
| exactly `{feedback_id}` | S5b feedback handler |
| contains a known key plus any extra/both known keys | private generic interaction error |
| unknown key set or non-OpenSRE action | empty response |

This preserves future extensibility without allowing a malformed action to downgrade into another handler.

### 11.2 Validation order

The feedback handler performs:

1. require event, operator, context and action;
2. require `action.tag == "button"`;
3. require exact `{"feedback_id"}` mapping and non-empty string token;
4. require non-empty `operator.open_id` and `context.open_chat_id`;
5. apply current `FEISHU_ALLOWED_OPEN_IDS` policy;
6. hash token and load an unexpired `OPEN` or consumed authority;
7. constant-time compare the digest;
8. match requester `open_id` and original `chat_id`;
9. construct the fixed feedback row from server-side authority only;
10. call idempotent feedback persistence;
11. on `WRITTEN` or `DUPLICATE`, append `CONSUMED` if needed and return a private toast.

Unknown, expired, unauthorized and wrong-chat tokens share one unavailable toast. They do not reveal whether a token exists or
which predicate failed. Malformed OpenSRE actions get one generic interaction-error toast. Neither case changes shared card state.

### 11.3 Feedback row

The Feishu JSONL row contains exactly:

```json
{
  "ts": "2026-09-26T00:00:00+00:00",
  "platform": "feishu",
  "user_id": "<actor open_id>",
  "chat_id": "<original chat_id>",
  "message_id": "<final answer message_id>",
  "verdict": "good"
}
```

The idempotency key is derived from `message_id` and `user_id`; it need not be duplicated as another persisted field.

### 11.4 Callback latency and failure

Callback processing only admits minimal metadata to a bounded background queue. The worker uses 0.5-second file-lock timeouts,
but disk work never blocks callback return. Neither callback nor worker performs network, turn lookup or model/tool work.

| Result | Private response | Shared card | Authority |
| --- | --- | --- | --- |
| callback admitted to queue | received, not yet persisted | unchanged | checked by worker |
| queue full or closed | retry later | unchanged | unchanged |
| first durable write | no follow-up response | unchanged | consumed |
| duplicate row | no follow-up response | unchanged | consumed |
| lock/write failure | no follow-up response; click again | unchanged | remains open |
| unauthorized/wrong chat/unknown/expired | no follow-up response | unchanged | unchanged/expired |
| malformed OpenSRE action | interaction error | unchanged | unchanged |

## 12. Idempotent feedback persistence

### 12.1 Compatibility

`append_feedback_entry` and its tests remain behaviorally unchanged. Slack and Discord may continue recording repeated clicks.
Feishu alone calls the new `append_feedback_entry_once` API.

### 12.2 Cross-process algorithm

For one feedback path:

1. create its parent directory with owner-only permissions where supported;
2. acquire a sibling `FileLock` with bounded timeout;
3. read the complete JSONL under the lock;
4. tolerate one malformed/truncated tail only as a failed operation, never as an empty store;
5. compare canonical idempotency keys from existing valid rows using a set;
6. if present, return `DUPLICATE` without writing;
7. serialize the new row before opening append mode;
8. append exactly one line, flush and fsync;
9. return `WRITTEN` only after durable completion.

On parse, lock, serialization or write error, return `FAILED`; never truncate or replace the feedback log. The callback therefore
cannot claim success when persistence is uncertain.

### 12.3 Hot-path bounds

Feedback callbacks are low-frequency, but the file can grow. The first implementation may scan under lock because correctness and
single-file compatibility dominate; it must use a set for dedupe, never repeated list membership. If profiling later requires an
index, the index must be rebuildable from JSONL and cannot become the source of truth.

## 13. Filesystem and privacy

Default host-level paths are beneath `OPENSRE_HOME_DIR / "gateway"` because CARD callbacks arrive before a turn storage scope is
bound and must find the same authority after restart:

- `feishu_feedback.jsonl` — durable feedback rows;
- `feishu_feedback_authority.jsonl` — token-digest authorization ledger;
- `feishu_ack_lifecycle.jsonl` — ack lifecycle ledger;
- sibling `.lock` files for cross-process coordination.

Files are owner-only (`0600`) and directories are owner-only where the platform supports POSIX modes. Logs may contain fixed
status/category names, counts and truncated hashes for correlation. They must not contain raw callback tokens, full message/chat/
open IDs, content, credentials, SDK response bodies or exception strings that can embed them.

## 14. Failure semantics

| Failure | Required behavior |
| --- | --- |
| ack executor unavailable/full | no reaction call; turn proceeds |
| reaction create rejected/raises | record safe add failure; turn proceeds |
| terminal races create | successful late create immediately proceeds to delete |
| delete not-found | treat as removed |
| delete rejected/uncertain | persist cleanup-needed state; no turn impact |
| startup lacks reaction-read permission | exact known IDs still clean up; unresolved add window remains untouched and readiness logs a fixed warning |
| final flush/close fails | no feedback target/button |
| overflow page partially delivered | no feedback target/button |
| pure-text fallback succeeds | answer remains success; no feedback button |
| authority register fails | do not append button; answer remains success |
| element append definitely fails | invalidate authority; answer remains success |
| element append outcome uncertain | do not retry; keep authority until expiry; answer remains success |
| callback feedback write fails | authority remains open; next click may retry; no card mutation |
| duplicate callback after restart | one JSONL row; callback receives queue receipt only |

## 15. Concurrency and replay invariants

1. One inbound `message_id` has at most one admitted turn and one live local ack record; active or retained terminal duplicates are
   acknowledged internally and dropped without another handler/model invocation.
2. Terminal-before-add and add-before-terminal both converge on delete or durable cleanup-needed state.
3. Delete uses an exact owned `reaction_id`; ownership uncertainty never broadens deletion.
4. One final card gets at most one S5b append attempt and one registered feedback token.
5. CardKit sequence allocation occurs only after the prior operation has a confirmed result; uncertain mutation is never followed
   by another mutation on that card.
6. Any number of valid concurrent callback deliveries for one `(message_id, actor)` produces at most one feedback JSONL line.
7. A crash between feedback write and authority consumption is recovered by JSONL dedupe: the next callback returns duplicate and
   settles the authority.
8. A callback from another actor/chat never writes, consumes or invalidates the rightful user's authority.
9. Approval and feedback payloads cannot be reinterpreted as each other.
10. Shutdown never waits unboundedly for reaction I/O.

## 16. Observability

Allowed structured fields are limited to:

- operation (`ack_add`, `ack_remove`, `ack_reconcile`, `feedback_append`, `feedback_callback`);
- fixed outcome/category;
- terminal outcome enum;
- attempt/count/page count;
- elapsed bucket;
- short one-way digest prefix when correlation is required.

Do not log complete IDs, body content, raw token, stored row, request object, response object, SDK `msg`, `str(exc)`, `repr(exc)` or
traceback on paths where those can expose external data. Unexpected exceptions may be captured server-side only when the exception
chain is proven not to contain request/response/content objects; otherwise log the safe exception type/category.

## 17. Test strategy

Use TDD: add one failing test per distinct failure class before the smallest product change.

### 17.1 Reaction client and lifecycle

- create EYE request and non-empty reaction ID parsing;
- exact delete request; not-found is idempotent success;
- paginated EYE list and operator ownership parsing;
- no ack for denied/help/pairing/unsupported/pre-cancel paths;
- valid turn begins without waiting for create;
- add failure does not delay or fail handler;
- success/failure/cancel/timeout/shutdown all request removal;
- terminal-before-create-result removes a late successful reaction;
- duplicate/replayed inbound message creates one lifecycle and invokes the handler once;
- restart deletes known reaction IDs;
- unknown-owner listed EYE is never deleted;
- executor saturation and shutdown budget remain bounded.

### 17.2 Final-card receipt

- healthy short stream returns original card/message and next sequence;
- final flush or close failure returns no target;
- complete overflow selects the last confirmed card;
- partial overflow or empty IDs return no target;
- stream rejection followed by complete fallback obeys the conservative close-failure rule;
- pure-text fallback returns no target and preserves complete output;
- error/cancel/timeout/shutdown never expose a success target.

### 17.3 Feedback append and authority

- element-create wire contract: append, component JSON, UUID and sequence;
- authority is persisted before append;
- definite append failure invalidates; uncertain result remains open without retry;
- token ledger stores digest, never plaintext token/content/card ID;
- 14-day expiry and compaction preserve open/tombstone semantics;
- first valid click writes exactly the allowlisted Feishu row;
- duplicate sequential, concurrent-thread, concurrent-process and post-restart clicks write one row;
- wrong actor, wrong chat, allowlist denial, unknown/expired token and extra fields write nothing;
- persistence failure leaves authority retryable and returns no success claim;
- callback performs no CardKit/IM, model, tool or turn call.

### 17.4 Regression and borders

- S5a approve/deny payloads and CARD-frame ACK behavior remain unchanged;
- mixed approval/feedback keys fail closed; unknown actions remain empty responses;
- Slack/Discord repeated-feedback tests remain unchanged and green;
- existing Feishu stream, fallback, cancellation, timeout and worker shutdown suites remain green;
- integrations API border, gateway surface border, import graph and lightweight `__init__.py` checks pass.

### 17.5 Quality gates

Run the focused commands selected from `CI.md`, then full required lint, format-check, typecheck and test gates before push. Every
push with an open PR is followed by `gh pr checks --watch`; failures require logs, root-cause repair, focused local rerun and another
check cycle.

## 18. Live Feishu acceptance

No live action is authorized by approval of this spec. Immediately before live validation, request fresh authorization naming the
test chat, actions and maximum counts.

Proposed bounded acceptance, subject to that later authorization:

1. one successful user turn: at most one EYE add, one EYE delete, one final answer card and one feedback-component append;
2. one `✅ 采纳` click by the requester: one private success toast and one feedback row;
3. one repeated click: private already-recorded toast and no second row;
4. one click by a permitted second test user in the same chat, if explicitly authorized: private unavailable toast and no write;
5. one cancelled or deliberately bounded timeout turn, if explicitly authorized: EYE removed and no feedback button;
6. inspect safe logs/state for absence of content, raw tokens, credentials and full IDs.

Do not deliberately break the network or kill the production gateway to test crash windows. Those cases use deterministic tests.
If a live API fails because permissions or console publication are incomplete, record the real failure and fix the prerequisite;
mock success cannot replace live acceptance.

Required console/app prerequisites:

- existing `card.action.trigger` subscription and released app version from S5a;
- `im:message.reactions:write_only` for add/delete;
- `im:message.reactions:read` for crash-window reconciliation;
- `cardkit:card:write` for component append.

## 19. Documentation

Update user-facing Feishu setup documentation only with facts that change operator action:

- required reaction and CardKit permissions;
- 👀 means processing and disappears at completion;
- successful card answers expose a positive feedback button;
- feedback stores metadata only and never starts a retry;
- pure-text/error/cancel/timeout outputs do not expose the button.

Do not document SDK classes, endpoint paths, internal filenames, state-machine history or implementation bugs under `docs/`.

## 20. Rejected alternatives

### 20.1 Memory-only ack and feedback registry

It is small but loses button authority, duplicate protection and ack cleanup on restart. It fails the accepted lifecycle and
idempotency requirements.

### 20.2 Signed or encrypted self-contained callback payload

It can survive restart without a registry but must carry message/user/chat claims to authorize safely. That violates the approved
opaque-ID-only payload and expands client-visible metadata.

### 20.3 Reuse S5a `PendingApprovals`

Approval state is intentionally short-lived, process-local and tied to a waiting tool thread. Feedback is durable for 14 days and
has no waiter. Reuse would couple unrelated state machines and make restart behavior incorrect.

### 20.4 Permanent EYE or success replacement reaction

Permanent EYE changes its meaning from “processing” to ambiguous “seen/completed”; a success replacement creates a second feedback
vocabulary that conflicts with the formal button and future S5c reactions.

### 20.5 Retry reaction create/delete automatically

Create lacks an idempotency key, so retrying an uncertain create can duplicate EYE. Delete retry is delegated to durable startup
reconciliation rather than an unbounded in-turn loop.

### 20.6 Add a button to pure-text fallback

There is no card entity to mutate. Creating a new card solely for feedback would make the UI claim card delivery where the real
answer used a degraded channel and would add another visible message after success.

### 20.7 Change the shared feedback writer globally

Making the existing append API dedupe would silently change Slack/Discord analytics. A new explicit idempotent API keeps the S5b
contract opt-in.

### 20.8 Mutate the shared card after callback

It adds network latency inside the 3-second handler and lets unauthorized/replayed callbacks create visible side channels. Private
toasts plus durable local feedback are sufficient for S5b.

## 21. Rollout and rollback

### 21.1 Rollout

1. Land code and deterministic tests before any live call.
2. Confirm the released Feishu app has the reaction write/read and CardKit write permissions; a missing permission is a real
   acceptance failure, not a reason to bypass authorization or substitute a mock.
3. With fresh bounded authorization, run the acceptance matrix in one test chat before relying on S5b elsewhere.
4. Reaction and feedback side effects remain graceful-degradation paths: missing/failed reaction APIs leave the turn usable, while
   a failed authority or component append leaves a complete answer without a feedback button.
5. Observe safe category counts for add/delete/append/callback failures. Do not add a feature flag or dual behavior solely for
   rollout; the existing permission boundary and best-effort contracts are the degradation mechanism.

### 21.2 Rollback

- Prefer stopping the current gateway cleanly before reverting so active EYE reactions are deleted or persisted for reconciliation.
- Reverting the S5b commit stops new ack/button creation and feedback writes; older code ignores the retained S5b ledger files.
- Do not delete feedback or authority/ack ledgers during rollback. They are owner-only, bounded data and may be needed to reconcile
  an interrupted rollout after the fixed version returns.
- Buttons already visible when rollback occurs may return no S5b result because the old dispatcher does not know `feedback_id`;
  they must not be reinterpreted as approval or retry actions, and they still cannot start a turn.
- If rollback follows an uncertain reaction delete, restore a fixed S5b build to reconcile it or perform a separately authorized,
  exact-ID cleanup; never broadly delete EYE reactions.

## 22. Delivery gates

1. This design spec receives explicit user approval.
2. Write a separate implementation plan covering S5b only and request approval before product/test changes.
3. Implement with TDD; do not include S5c retry/reaction events or S7/S8 work.
4. Complete focused and full local quality gates required by `CI.md`.
5. Obtain fresh, bounded authorization and complete real Feishu acceptance.
6. Create the PR with the full template and AI-usage disclosure.
7. After every push, monitor CI to green and fix root causes rather than skipping checks.
8. Address all actionable review; reach Greptile 5/5 with no unresolved conversations.
9. Merge only after required gates pass, then monitor main CI, CodeQL, applicable synthetic/interactive workflows and release.
10. A post-merge failure remains unfinished delivery and must be fixed or reverted before reporting completion.

## 23. Approval boundary

This document fixes the S5b design, not the implementation plan. Before explicit approval, do not modify product code, tests or
user-facing docs. After approval, write a separate S5b implementation plan with exact edit/test order and another approval gate.
Live Feishu actions always require a new authorization at the time of execution, regardless of prior design/plan approval.
