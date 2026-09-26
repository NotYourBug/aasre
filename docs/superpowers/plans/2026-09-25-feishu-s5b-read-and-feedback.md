# Feishu S5b Read Ack and Formal Feedback Implementation Plan

> **Execution mode:** Single agent only. Follow the tasks in order and stop at every explicit approval boundary.

**Status:** Approved by user; bounded-feedback-worker amendment approved 2026-09-25

**Implementation record (2026-09-26):** Tasks 1–6 were implemented in commits
`880b397`, `1ce0cea`, `99080f8`, `5b965a0`, `e5c2b5c`, and `6008278`.
Tasks 7–8 are implemented in `dbb4514` and `a8d0eb4`; Task 9's local checks
passed (421 scoped tests, full Ruff and mypy, strict import checks). Task 10 (authorized live acceptance, PR review, merge,
and post-merge monitoring) remains open. The checkboxes below are the original
execution checklist and do not represent live acceptance evidence.

**Approved adjustment:** Task 7 uses non-blocking bounded background admission for minimal token/actor/chat metadata.
The private receipt means received, not persisted. Authority, allowlist and expiry are rechecked in the worker before the
idempotent write. Full/closed queues return a private retry message. No follow-up message, card mutation or new turn is permitted.
This supersedes later synchronous callback persistence, no-executor and immediate-success-toast instructions. Test blocked
authority/feedback I/O, saturation and shutdown explicitly. Queued requests can be lost on crash; safe repeat-clicks recover.

**Goal:** Add a recoverable 👀 processing-reaction lifecycle to accepted Feishu turns and append one secure, durable, positive-only
`✅ 采纳` action to completely delivered final cards without changing turn, billing, or other transport behavior.

**Architecture:** Vendor request construction remains under `integrations/feishu`; Feishu lifecycle and callback authority remain
under `gateway/transports/feishu`; generic idempotent JSONL persistence remains under `gateway/core/storage/feedback`. The existing
terminal arbiter decides when ack cleanup and feedback issuance occur. Final-card targeting is supplied by the existing stream owner,
and S5a/S5b share one thin exact-key callback router.

**Tech stack:** Python 3.11+, `threading`, `concurrent.futures`, `filelock`, JSONL, `lark-oapi 1.7.3`, Card JSON 2.0/CardKit,
pytest, Ruff, mypy, GitHub CLI.

**Spec:** [`.superpowers/specs/2026-09-25-feishu-s5b-read-and-feedback-design.md`](../../../.superpowers/specs/2026-09-25-feishu-s5b-read-and-feedback-design.md)

## Global constraints

- S5b only. Do not add retry, reaction-created/deleted subscriptions, THUMBSUP/CROSS mapping, a new turn, S7/S8 behavior, or a
  public callback service.
- Do not use subagents.
- Preserve R33–R35 and the approved design. If implementation evidence requires changing them or an adjacent project's API, stop
  and request approval rather than widening this plan.
- Treat reaction and component APIs as best-effort side effects. Their failures never change an already determined turn result.
- EYE is removed on success, failure, cancellation, timeout and shutdown. Never replace it with another reaction.
- Delete only an exact reaction ID proven to belong to this app. Unknown ownership fails closed.
- Feedback button value is exactly `{"feedback_id": "<opaque-token>"}`. No user/chat/message/card/content/verdict fields go to the
  client.
- Only normal success with a completely delivered, certain final CardKit target can receive a button. Pure-text fallback and every
  non-success terminal path remain button-free.
- Feishu feedback writes at most one row per `(final message_id, actor)`. Keep existing Slack/Discord repeated-click behavior.
- Callback processing does bounded local persistence only. It performs no CardKit/IM call, wait, retry, executor dispatch, model,
  tool, approval resolution, session mutation or turn creation.
- Keep every `__init__.py` a lightweight import/`__all__` facade. Do not re-export SDK-bearing clients through
  `integrations.feishu`; preserve the lazy boot path.
- New or changed Protocol methods use docstring-only bodies.
- Never log raw tokens, content, complete IDs, credentials, request/response objects, SDK messages, `str(exc)` or `repr(exc)` on an
  externally influenced path.
- Never commit `.env`, real targets, real open IDs, credentials, CardKit IDs, message IDs, feedback data or live payloads.
- Use `apply_patch` for edits. After each RED test, confirm the failure is caused by the missing contract before implementing.
- Suggested commits are logical boundaries, not permission to push. Do not push or open a PR before local gates and live acceptance.

---

### Task 1: Add opt-in idempotent feedback persistence

**Files:**

- Modify: `gateway/core/storage/feedback/jsonl.py`
- Modify: `gateway/core/storage/feedback/__init__.py`
- Modify: `gateway/tests/test_feedback_store.py`
- Regression only: `gateway/tests/slack/test_feedback.py`
- Regression only: `gateway/tests/discord/test_feedback.py`

**Interface:**

```python
class FeedbackWriteResult(StrEnum):
    WRITTEN = "written"
    DUPLICATE = "duplicate"
    FAILED = "failed"


def append_feedback_entry_once(
    entry: Mapping[str, object],
    *,
    idempotency_fields: tuple[str, ...],
    path: Path,
    lock_timeout_seconds: float,
) -> FeedbackWriteResult:
    """Append one durable row unless its selected field values already exist."""
```

- [ ] **Step 1: Write the failing high-signal tests**

Add tests for:

- first write returns `WRITTEN`, an identical key returns `DUPLICATE`, and the file contains one line;
- different actor or message ID produces a distinct row;
- 20 concurrent threads with the same key produce one `WRITTEN`, the rest `DUPLICATE`;
- two spawned processes contending on the same temporary path still produce one row;
- constructing a fresh caller after the first write still observes the duplicate from disk;
- missing idempotency fields, serialization failure, file-lock timeout, invalid JSON and a truncated tail return `FAILED` without
  truncating/replacing the log;
- an injected write/fsync failure never returns `WRITTEN`;
- the existing `append_feedback_entry()` still records repeats.

Use process-safe top-level worker functions and bounded joins; no sleeps as synchronization.

- [ ] **Step 2: Confirm RED**

Run:

```powershell
uv run python -m pytest gateway/tests/test_feedback_store.py -q
```

Expected: FAIL because `FeedbackWriteResult` and `append_feedback_entry_once` do not exist.

- [ ] **Step 3: Implement the minimum shared owner behavior**

- Serialize before opening append mode.
- Acquire a sibling `FileLock` with the supplied timeout.
- Reload the file under the lock and build a `set[tuple[object, ...]]` for O(1) membership.
- Treat unreadable/malformed input as failure, never as an empty store.
- Append one UTF-8 JSON line, flush and `os.fsync()` before reporting `WRITTEN`.
- Tighten the file to owner-only where supported; failure to chmod is a safe debug category, not a write failure.
- Catch `filelock.Timeout`, `OSError`, JSON and serialization failures without logging the entry.
- Leave `_WRITE_LOCK` and `append_feedback_entry()` semantics untouched.

- [ ] **Step 4: Verify GREEN and transport compatibility**

Run:

```powershell
uv run python -m pytest gateway/tests/test_feedback_store.py gateway/tests/slack/test_feedback.py gateway/tests/discord/test_feedback.py -q
```

**Completion:** Cross-process/restart dedupe is deterministic; malformed storage fails closed; Slack/Discord regressions pass.

- [ ] **Step 5: Suggested commit boundary**

```powershell
git add gateway/core/storage/feedback/jsonl.py gateway/core/storage/feedback/__init__.py gateway/tests/test_feedback_store.py
git commit -m "feat: add idempotent gateway feedback writes"
```

---

### Task 2: Build and append the Feishu feedback component

**Files:**

- Create: `integrations/feishu/feedback_cards.py`
- Modify: `integrations/feishu/card_client.py`
- Modify: `integrations/feishu/delivery_types.py`
- Modify: `integrations/feishu/__init__.py`
- Create: `tests/integrations/test_feishu_feedback_cards.py`
- Modify: `tests/integrations/test_feishu_card_client.py`
- Regression only: `tests/integrations/test_feishu_boot_path.py`
- Regression only: `tests/shared/test_integrations_api_border.py`

**Interfaces:**

```python
def render_feedback_button_elements(token: str) -> list[dict[str, object]]:
    """Return one Card JSON 2.0 positive-feedback action row."""


def append_elements(
    self,
    card_id: str,
    elements: list[dict[str, object]],
    sequence: int,
    *,
    uuid: str,
) -> None:
    """Append elements to a card with one caller-owned idempotency UUID."""
```

- [ ] **Step 1: Write failing pure-builder tests**

Assert one visible `✅ 采纳` button, Card JSON 2.0 callback behavior, exact `{"feedback_id": token}` value, and absence of approval,
retry, actor, chat, message, card, question, answer and verdict fields. Blank token must be rejected before building.

- [ ] **Step 2: Write failing SDK wire-contract tests**

Extend the card-client SDK stub with `card_element.create`. Assert:

- request path model carries the supplied `card_id`;
- body uses `type="append"`, no `target_element_id`, caller UUID, exact sequence and JSON-encoded elements;
- confirmed SDK rejection raises `FeishuCardCallError` with new stage `APPEND_ELEMENTS` and no vendor detail;
- no automatic retry occurs;
- the underlying SDK client is still built once.

- [ ] **Step 3: Confirm RED**

Run:

```powershell
uv run python -m pytest tests/integrations/test_feishu_feedback_cards.py tests/integrations/test_feishu_card_client.py -q
```

Expected: FAIL because the builder, enum member and append method are absent.

- [ ] **Step 4: Implement the builder and one-attempt append call**

Use the locked SDK's `CreateCardElementRequest` and `CreateCardElementRequestBody`. Require the UUID rather than silently creating a
new one inside the method: retry policy belongs to the caller, and S5b intentionally does not retry uncertain append results.
Re-export only the pure builder from `integrations.feishu`; import the SDK-bearing client by its leaf module as existing callers do.

- [ ] **Step 5: Verify GREEN and boot/API borders**

Run:

```powershell
uv run python -m pytest tests/integrations/test_feishu_feedback_cards.py tests/integrations/test_feishu_card_client.py tests/integrations/test_feishu_boot_path.py tests/shared/test_integrations_api_border.py -q
```

**Completion:** The wire shape matches SDK 1.7.3, one logical append has one UUID/sequence, and facade import remains SDK-lazy.

- [ ] **Step 6: Suggested commit boundary**

```powershell
git add integrations/feishu/feedback_cards.py integrations/feishu/card_client.py integrations/feishu/delivery_types.py integrations/feishu/__init__.py tests/integrations/test_feishu_feedback_cards.py tests/integrations/test_feishu_card_client.py
git commit -m "feat: append Feishu feedback components"
```

---

### Task 3: Expose a certain final-card target

**Files:**

- Modify: `gateway/transports/feishu/card_stream.py`
- Modify: `gateway/transports/feishu/turn_output.py`
- Modify: `gateway/tests/feishu/test_card_stream.py`
- Modify: `gateway/tests/feishu/test_turn_output.py`

**Interface:**

```python
@dataclass(frozen=True, slots=True)
class FinalCardTarget:
    card_id: str
    message_id: str
    append_sequence: int
```

`CardStreamSession.finish() -> FinalCardTarget | None`; `FeishuTurnOutput` exposes the receipt only for a normal-success finalization.

- [ ] **Step 1: Add characterization tests before refactoring**

Pin the currently observable short-stream, overflow, fallback and shutdown behavior on the pre-change code. Run these tests once and
confirm they pass before changing the session internals.

Run:

```powershell
uv run python -m pytest gateway/tests/feishu/test_card_stream.py gateway/tests/feishu/test_turn_output.py -q
```

- [ ] **Step 2: Add failing receipt tests**

Cover:

- a healthy short stream returns the original card/message and the sequence immediately after confirmed close;
- complete overflow returns the last confirmed complete card and its first mutation sequence;
- overflow retains every page's returned IDs without changing the no-skip delivery cursor;
- empty card/message IDs, page failure, final flush failure or close failure return `None`;
- a stream failure followed by fallback honors the approved conservative close-failure rule;
- pure-text fallback returns no target while preserving all chunks;
- error, timeout, cancellation and shutdown finalization cannot expose an eligible target;
- repeated finish/shutdown calls do not allocate a second sequence or receipt.

- [ ] **Step 3: Confirm RED**

Run the same focused command. Expected: new receipt assertions FAIL because IDs/sequence/failure certainty are currently discarded.

- [ ] **Step 4: Implement minimum receipt tracking**

- Track the last complete `(card_id, message_id)` in `_send_overflow` only after each send returns non-empty IDs.
- Track any close/final-delivery uncertainty independently from `_degraded`.
- Return a frozen receipt only after all pending content is confirmed and the chosen card can accept the next sequence.
- Preserve existing content cursor and five-level fallback behavior.
- Make output finalization outcome-aware without changing the generic turn-output contract: normal handler completion may expose the
  receipt; `render_error`, timeout/stop/credits helpers and shutdown clear or suppress eligibility.

- [ ] **Step 5: Verify GREEN and repeat race tests**

```powershell
uv run python -m pytest gateway/tests/feishu/test_card_stream.py gateway/tests/feishu/test_turn_output.py -q
1..20 | ForEach-Object { uv run python -m pytest gateway/tests/feishu/test_turn_output.py -k "shutdown_and_completion_race" -q; if ($LASTEXITCODE -ne 0) { throw "turn-output race failed on run $_" } }
```

**Completion:** Receipt existence is equivalent to a certain, complete CardKit answer; all prior lossless delivery tests remain green.

- [ ] **Step 6: Suggested commit boundary**

```powershell
git add gateway/transports/feishu/card_stream.py gateway/transports/feishu/turn_output.py gateway/tests/feishu/test_card_stream.py gateway/tests/feishu/test_turn_output.py
git commit -m "feat: expose completed Feishu card targets"
```

---

### Task 4: Add the Feishu reaction API client

**Files:**

- Create: `integrations/feishu/reactions.py`
- Create: `tests/integrations/test_feishu_reactions.py`
- Regression only: `tests/integrations/test_feishu_boot_path.py`
- Regression only: `tests/shared/test_integrations_api_border.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class FeishuReaction:
    reaction_id: str
    operator_id: str
    operator_type: str


class FeishuReactionClient:
    def add_eye(self, message_id: str) -> FeishuReaction: ...
    def delete(self, message_id: str, reaction_id: str) -> None: ...
    def list_eye(self, message_id: str) -> tuple[FeishuReaction, ...]: ...
```

Any Protocol introduced for gateway injection uses docstring-only method bodies.

- [ ] **Step 1: Write failing SDK request/response tests**

Pin:

- create uses message reaction create with emoji type `EYE` and requires a non-empty `reaction_id`;
- returned operator ID/type are preserved for ownership evidence;
- delete uses both exact message and reaction IDs;
- platform not-found is mapped to idempotent deletion success only for a verified numeric code;
- list filters to EYE, paginates until `has_more` is false, and preserves operator metadata;
- rejected responses expose only fixed operation and numeric code;
- network exceptions are not stringified or logged by the client;
- client construction is reused across calls.

- [ ] **Step 2: Confirm RED**

```powershell
uv run python -m pytest tests/integrations/test_feishu_reactions.py -q
```

Expected: collection FAIL because the module does not exist.

- [ ] **Step 3: Implement the narrow SDK wrapper**

Use `CreateMessageReactionRequest`, `DeleteMessageReactionRequest` and `ListMessageReactionRequest` from SDK 1.7.3. Do not add
retry, lifecycle state, allowlist logic or logging of SDK objects. Keep this SDK-bearing module out of the package facade.

- [ ] **Step 4: Verify GREEN and lazy-import borders**

```powershell
uv run python -m pytest tests/integrations/test_feishu_reactions.py tests/integrations/test_feishu_boot_path.py tests/shared/test_integrations_api_border.py -q
```

**Completion:** Create/list/delete contracts are pinned to the locked SDK and only stable allowlisted failure fields escape.

- [ ] **Step 5: Suggested commit boundary**

```powershell
git add integrations/feishu/reactions.py tests/integrations/test_feishu_reactions.py
git commit -m "feat: add Feishu reaction client"
```

---

### Task 5: Implement the durable, non-blocking EYE lifecycle

**Files:**

- Modify: `config/constants/feishu.py`
- Modify: `config/constants/__init__.py`
- Create: `gateway/transports/feishu/reaction_lifecycle.py`
- Create: `gateway/tests/feishu/test_reaction_lifecycle.py`
- Modify: `tests/integrations/test_feishu_constants.py`

**Interfaces:**

```python
class AckAdmission(StrEnum):
    ADMITTED = "admitted"
    DUPLICATE = "duplicate"
    UNTRACKED = "untracked"


class AckOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    SHUTDOWN = "shutdown"


class ReactionLifecycleManager:
    def reconcile(self, *, budget_seconds: float, record_limit: int) -> None: ...
    def begin(self, message_id: str) -> AckAdmission: ...
    def finish(self, message_id: str, outcome: AckOutcome) -> None: ...
    def shutdown(self, *, timeout_seconds: float) -> None: ...
```

- [ ] **Step 1: Write failing state/ledger tests**

Using a temporary ledger and fake reaction client, cover:

- `NEW → ADDING → ACTIVE → REMOVING → REMOVED`;
- terminal-before-add gives `TERMINAL_PENDING_ADD`, then removes a late successful reaction;
- create failure ends in `ADD_FAILED` without blocking callers;
- delete failure persists `REMOVE_FAILED` and remains reconcilable;
- a duplicate active or retained terminal `message_id` returns `DUPLICATE` and schedules no work;
- replaying a freshly constructed manager from disk preserves dedupe and cleanup state;
- ledger lock/write failure returns `UNTRACKED` and still allows the turn;
- malformed ledger fails safe without replacing it;
- records beyond 14 days compact away while active cleanup records survive;
- persisted rows contain no chat, actor, session or content.

- [ ] **Step 2: Add failing executor/race/shutdown tests**

Use Events/Barriers rather than sleeps to cover:

- `begin` returns before a blocked API call completes;
- a bounded-submit semaphore prevents unbounded queue growth;
- queue full/closed degrades to best effort without waiting;
- finish is idempotent under concurrent terminal calls;
- shutdown stops new admissions, requests active cleanup and returns within its budget;
- unfinished cleanup remains in the ledger for restart;
- startup reconciliation deletes exact persisted IDs;
- an `ADDING` crash window lists EYE but deletes only an operator proven to match this app;
- unknown ownership is never deleted;
- time/record reconciliation budgets stop deterministically and retain remaining work.

- [ ] **Step 3: Confirm RED**

```powershell
uv run python -m pytest gateway/tests/feishu/test_reaction_lifecycle.py tests/integrations/test_feishu_constants.py -q
```

Expected: collection FAIL because lifecycle types and constants do not exist.

- [ ] **Step 4: Implement the minimum ledger and manager**

- Put shared static filenames, `EYE`, 14-day retention, queue bound and lock/startup budget values in `config/constants/feishu.py`.
- Use one append-only owner-only JSONL ledger protected by `FileLock`, with fsync and atomic compaction under the same lock.
- Persist admission before submitting create so a replay cannot start a second turn.
- Use one dedicated `ThreadPoolExecutor` plus a `BoundedSemaphore`; release permits in a done callback.
- Serialize per-message transitions under a lock but never hold it during network I/O.
- Persist `message_id`, exact reaction ID/operator evidence, state, timestamps and terminal outcome only.
- Log fixed categories/digest prefixes only.

- [ ] **Step 5: Verify GREEN and repeat races**

```powershell
uv run python -m pytest gateway/tests/feishu/test_reaction_lifecycle.py tests/integrations/test_feishu_constants.py -q
1..20 | ForEach-Object { uv run python -m pytest gateway/tests/feishu/test_reaction_lifecycle.py -k "race or concurrent or shutdown" -q; if ($LASTEXITCODE -ne 0) { throw "reaction lifecycle repetition failed on run $_" } }
```

**Completion:** Admission and cleanup converge under all orderings; remote I/O cannot block a turn; restart recovery never broadens
ownership.

- [ ] **Step 6: Suggested commit boundary**

```powershell
git add config/constants/feishu.py config/constants/__init__.py gateway/transports/feishu/reaction_lifecycle.py gateway/tests/feishu/test_reaction_lifecycle.py tests/integrations/test_feishu_constants.py
git commit -m "feat: manage Feishu processing reactions"
```

---

### Task 6: Persist feedback callback authority

**Files:**

- Create: `gateway/transports/feishu/feedback_authority.py`
- Create: `gateway/tests/feishu/test_feedback_authority.py`
- Modify if constants were not already complete: `config/constants/feishu.py`
- Modify if constants were not already complete: `config/constants/__init__.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class FeedbackAuthority:
    token_digest: str
    requester_open_id: str
    chat_id: str
    message_id: str
    created_at: float
    expires_at: float


class FeedbackAuthorityStore:
    def register(self, *, token: str, requester_open_id: str, chat_id: str,
                 message_id: str) -> FeedbackAuthority: ...
    def find(self, token: str) -> FeedbackAuthority | None: ...
    def consume(self, token: str) -> bool: ...
    def invalidate(self, token: str) -> bool: ...
```

- [ ] **Step 1: Write failing durability/security tests**

Cover:

- register then fresh-instance find across restart;
- disk contains SHA-256 digest but not raw token, question, answer, card ID or callback;
- constant-time digest comparison is used for candidate validation;
- `REGISTERED`, `CONSUMED`, `INVALIDATED` and `EXPIRED` replay correctly;
- consume/invalidate are idempotent under thread and process contention;
- exact 14-day expiry denies lookup and retains a bounded tombstone until compaction;
- corrupt/truncated ledger and lock/fsync failures fail closed without data loss;
- owner-only file mode and atomic compaction;
- callback-facing operations respect the 0.5-second lock timeout.

- [ ] **Step 2: Confirm RED**

```powershell
uv run python -m pytest gateway/tests/feishu/test_feedback_authority.py -q
```

Expected: collection FAIL because the durable authority store is absent.

- [ ] **Step 3: Implement append-only authority events**

Use `secrets.token_urlsafe(32)` only at the service layer; the store accepts a token and persists only its digest. Reload under a
cross-process lock for every security decision. Compact by writing a temporary owner-only file, fsyncing and atomically replacing
while the lock remains held. Do not use S5a `PendingApprovals` or any in-memory-only source of truth.

- [ ] **Step 4: Verify GREEN**

```powershell
uv run python -m pytest gateway/tests/feishu/test_feedback_authority.py -q
```

**Completion:** An authority survives restart, expires deterministically, and cannot be reconstructed from client-visible metadata.

- [ ] **Step 5: Suggested commit boundary**

```powershell
git add gateway/transports/feishu/feedback_authority.py gateway/tests/feishu/test_feedback_authority.py config/constants/feishu.py config/constants/__init__.py
git commit -m "feat: persist Feishu feedback authority"
```

---

### Task 7: Handle feedback issuance and callbacks through an exact router

**Files:**

- Create: `gateway/transports/feishu/feedback.py`
- Create: `gateway/transports/feishu/card_actions.py`
- Create: `gateway/tests/feishu/test_feedback.py`
- Create: `gateway/tests/feishu/test_card_actions.py`
- Modify: `gateway/tests/feishu/test_approvals.py`

**Interfaces:**

- `FeishuFeedbackService.append_to(target, *, requester_open_id, chat_id) -> bool`
- `handle_feedback_action(data, *, service, env_allowed_open_ids, logger) -> P2CardActionTriggerResponse`
- `dispatch_card_action(data, *, approval dependencies, feedback service, allowlist, logger) -> response`

- [ ] **Step 1: Write failing issuance-order tests**

Pin:

- token generation and durable authority registration happen before `append_elements`;
- append receives one UUID and the target's exact sequence;
- confirmed append success produces one button attempt;
- definite rejection invalidates authority;
- uncertain exception is not retried and leaves authority open;
- authority registration failure makes no CardKit call;
- response/card content and exceptions are never logged.

- [ ] **Step 2: Write failing callback authorization/idempotency tests**

Cover the complete matrix:

- valid original requester + original chat + current allowlist writes the exact six-field `good` row and returns private success;
- same callback sequentially, concurrently and after service reconstruction writes one row and returns already-recorded;
- allowlist denial, wrong actor, wrong chat, unknown/expired/invalidated token write nothing;
- missing model/action/operator/context, non-button tag, non-mapping value, blank token and extra fields fail closed;
- persistence `FAILED` returns a private retry error and leaves authority open;
- callback never calls CardKit/IM, executor, handler, model, tool, approval broker or session APIs;
- all external failures return fixed text with no internal detail or shared card.

- [ ] **Step 3: Write failing router tests**

Assert exact `{approval_id}` delegates once to S5a, exact `{feedback_id}` delegates once to S5b, either known key plus extras or
both known keys returns the generic private interaction error, and unknown key sets return an empty response. Re-run all existing
S5a approval cases to prove no authorization/card regression.

- [ ] **Step 4: Confirm RED**

```powershell
uv run python -m pytest gateway/tests/feishu/test_feedback.py gateway/tests/feishu/test_card_actions.py gateway/tests/feishu/test_approvals.py -q
```

Expected: collection FAIL for the new modules and routing contract.

- [ ] **Step 5: Implement the minimum feedback service and router**

- Build the feedback row only from server-side authority and callback actor.
- Call `append_feedback_entry_once(..., idempotency_fields=("message_id", "user_id"), ...)`.
- Write feedback before consume. If a crash occurs between them, the next callback sees `DUPLICATE` and consumes.
- Keep failure authority open so the rightful requester can retry.
- Use identical unavailable text for unknown, expired, wrong actor/chat and allowlist denial.
- Return only private toasts; never return a replacement card.
- Keep `approvals.py` focused on S5a and `card_actions.py` as a thin dispatcher.

- [ ] **Step 6: Verify GREEN and callback timing guards**

```powershell
uv run python -m pytest gateway/tests/feishu/test_feedback.py gateway/tests/feishu/test_card_actions.py gateway/tests/feishu/test_approvals.py gateway/tests/test_feedback_store.py -q
```

Use injected blocked locks to prove each operation times out at 0.5 seconds; do not assert wall time more tightly than needed for a
stable upper bound.

**Completion:** The callback is locally bounded, authorization-equivalent to S5a, restart-idempotent and incapable of starting work.

- [ ] **Step 7: Suggested commit boundary**

```powershell
git add gateway/transports/feishu/feedback.py gateway/transports/feishu/card_actions.py gateway/tests/feishu/test_feedback.py gateway/tests/feishu/test_card_actions.py gateway/tests/feishu/test_approvals.py
git commit -m "feat: handle Feishu feedback callbacks"
```

---

### Task 8: Wire ack and feedback into the Feishu turn lifecycle

**Files:**

- Modify: `gateway/transports/feishu/inbound_handler.py`
- Modify: `gateway/transports/feishu/worker.py`
- Modify as lifecycle ownership requires: `gateway/transports/feishu/background.py`
- Modify: `gateway/tests/feishu/test_inbound_handler.py`
- Modify: `gateway/tests/feishu/test_worker.py`
- Modify: `gateway/tests/feishu/test_worker_stop.py`
- Modify: `gateway/tests/feishu/test_background.py`
- Modify: `gateway/tests/feishu/test_startup.py`

**Lifecycle order:**

1. Worker constructs one reaction lifecycle and one feedback service using host-level gateway paths.
2. Startup performs bounded ack reconciliation before readiness.
3. Security, principal/session resolution, cancel registration and pre-cancel check run before ack admission.
4. `ADMITTED` runs the handler; `DUPLICATE` returns without another turn; `UNTRACKED` runs without ack.
5. Success/failure/cancel/timeout/shutdown call one idempotent ack finish with the matching outcome.
6. Only a normal-success terminal winner may issue feedback against the final-card receipt.
7. Worker shutdown drains reaction cleanup before closing its side-effect executor; approval drain/close ordering remains intact.

- [ ] **Step 1: Add failing ingress/admission tests**

Assert denied/help/pairing/unsupported and pre-cancel paths never call `begin`; a valid accepted turn calls once after session
resolution. Deliver the same inbound event concurrently and after reconstruction: only one handler/model invocation is admitted.
When admission returns `UNTRACKED`, the handler still runs.

- [ ] **Step 2: Add failing terminal and feedback-target tests**

Cover normal success, handler exception, credits denial, user stop, timeout and output/gateway shutdown. Assert each exact ack
outcome and no blocking on fake reaction I/O. Assert only normal success with `FinalCardTarget` calls `append_to` once; no-target,
text fallback, error, cancellation, timeout, shutdown and append failure do not alter the delivered answer.

- [ ] **Step 3: Add failing worker/router/startup/shutdown tests**

Pin:

- the existing CARD-frame compatibility layer passes both approval and feedback payloads to `dispatch_card_action` and writes one
  CARD ACK response;
- callbacks stay synchronous and never submit a turn;
- unexpected callback failure returns the existing generic error without raw detail;
- startup reconciliation completes or exhausts its budget before readiness is signaled;
- shutdown order cleans active acks, drains approvals, closes the broker and stays within the background stop budget;
- missing reaction-read permission emits one safe warning but does not prevent normal gateway readiness;
- the worker never registers reaction-created/deleted events.

- [ ] **Step 4: Confirm RED**

```powershell
uv run python -m pytest gateway/tests/feishu/test_inbound_handler.py gateway/tests/feishu/test_worker.py gateway/tests/feishu/test_worker_stop.py gateway/tests/feishu/test_background.py gateway/tests/feishu/test_startup.py -q
```

Expected: new assertions FAIL because lifecycle/service dependencies are not wired.

- [ ] **Step 5: Implement narrow dependency injection and terminal hooks**

Keep `worker.py` as composition/dispatch: construct services, route events and own shutdown. Put no JSONL parsing or SDK request
construction there. In `inbound_handler.py`, use an idempotent local finish helper in every terminal branch and a final safety path
so an unexpected exception cannot strand EYE. Preserve the existing `TerminalOutcomeArbiter` as the sole winner selector.

Issue feedback after the handler returns and the success branch claims terminal ownership. Register authority before one append;
contain all issuance failures with safe categories. Do not delay terminal answer visibility waiting for a callback or any future work.

- [ ] **Step 6: Verify GREEN and existing Feishu regressions**

```powershell
uv run python -m pytest gateway/tests/feishu/ -q
```

Then repeat the race-focused tests:

```powershell
1..20 | ForEach-Object { uv run python -m pytest gateway/tests/feishu/test_reaction_lifecycle.py gateway/tests/feishu/test_inbound_handler.py gateway/tests/feishu/test_worker_stop.py -k "race or concurrent or duplicate or shutdown" -q; if ($LASTEXITCODE -ne 0) { throw "Feishu lifecycle repetition failed on run $_" } }
```

**Completion:** Accepted messages show one bounded ack lifecycle; every terminal removes it; only certain success gets one button;
S5a and all pre-existing Feishu flows remain green.

- [ ] **Step 7: Suggested commit boundary**

```powershell
git add gateway/transports/feishu/inbound_handler.py gateway/transports/feishu/worker.py gateway/transports/feishu/background.py gateway/tests/feishu/test_inbound_handler.py gateway/tests/feishu/test_worker.py gateway/tests/feishu/test_worker_stop.py gateway/tests/feishu/test_background.py gateway/tests/feishu/test_startup.py
git commit -m "feat: wire Feishu read and feedback lifecycle"
```

---

### Task 9: Update operator docs and complete local gates

**Files:**

- Modify: `docs/messaging/feishu.mdx`
- Modify: `.superpowers/specs/2026-09-12-feishu-capability-completion-design.md`
- Modify: `.superpowers/specs/2026-09-25-feishu-s5b-read-and-feedback-design.md`
- Add: `docs/superpowers/plans/2026-09-25-feishu-s5b-read-and-feedback.md`

- [ ] **Step 1: Update only actionable user documentation**

In `docs/messaging/feishu.mdx`:

- list reaction write/read and CardKit write permissions in operator language;
- explain that 👀 means processing and is removed when processing ends;
- explain that complete successful card answers expose `✅ 采纳`, metadata-only feedback, and no retry/new turn;
- state that fallback/error/cancel/timeout outputs have no feedback button;
- retain `card.action.trigger` publication guidance.

Do not include endpoints, SDK classes, internal filenames or bug history. No `docs/docs.json` change is needed because the page is
already navigable.

- [ ] **Step 2: Mark implementation facts only after they are true**

Keep S5b as “in progress” during implementation. After deterministic tests and live acceptance pass, update the parent roadmap and
S5b spec status to delivered without changing R33–R35; set the next roadmap item to S5c. Do not mark delivered before live evidence.

- [ ] **Step 3: Run all focused functional suites**

```powershell
uv run python -m pytest gateway/tests/test_feedback_store.py gateway/tests/slack/test_feedback.py gateway/tests/discord/test_feedback.py -q
uv run python -m pytest tests/integrations/test_feishu_feedback_cards.py tests/integrations/test_feishu_card_client.py tests/integrations/test_feishu_reactions.py tests/integrations/test_feishu_boot_path.py -q
uv run python -m pytest gateway/tests/feishu/ -q
uv run python -m pytest tests/shared/test_integrations_api_border.py gateway/tests/test_package_borders.py gateway/tests/test_surface_borders.py gateway/tests/test_storage_surface_borders.py -q
```

- [ ] **Step 4: Run the mandatory CI.md local harness**

```powershell
git status --short
make lint
make format-check
make typecheck
make check-imports-strict
make test-scope
```

Do not substitute `make test-cov`; CI.md makes pull-request CI authoritative for the full suite. If formatting changes are needed,
run `make format`, inspect the diff, then rerun affected tests and all failed gates.

- [ ] **Step 5: Perform a fresh single-agent security/code review**

Review the whole branch against the approved spec. Check especially reaction ownership, admission persistence, sequence certainty,
authority-before-visibility ordering, cross-file crash recovery, callback timing, file permissions, error exposure, package borders,
shutdown ordering, and absence of all S5c behavior. Add one focused regression for every actionable finding and rerun Steps 3–4.

**Completion:** Operator docs are actionable, all focused/local gates pass, and the only deferred checks require explicit live
authorization or GitHub state.

- [ ] **Step 6: Suggested commit boundary**

Before live acceptance, commit only user docs plus already-approved spec/plan status updates that are true at that point:

```powershell
git add docs/messaging/feishu.mdx .superpowers/specs/2026-09-12-feishu-capability-completion-design.md .superpowers/specs/2026-09-25-feishu-s5b-read-and-feedback-design.md
git add -f docs/superpowers/plans/2026-09-25-feishu-s5b-read-and-feedback.md
git commit -m "docs: explain Feishu read and feedback"
```

If delivery status is not yet true, leave that status hunk uncommitted until Task 10 acceptance succeeds; never predeclare success.

---

### Task 10: Obtain live authorization, validate, and close delivery

**External boundary:** This task cannot begin live operations until the user gives fresh authorization in that execution turn.

- [ ] **Step 1: Present an exact live-action authorization request**

Without printing values, confirm required configuration exists. Ask approval with:

- target: configured S5b test chat and the new user messages created during the test;
- at most three accepted test turns: one success, one cancellation, one bounded failure/timeout path;
- at most three EYE additions and three matching deletions;
- at most three answer cards/messages, with only the successful final card eligible for one component append;
- one requester `✅ 采纳` click plus one repeated click;
- one second-user/observer click only if the user explicitly confirms a second test user is available and authorized for this test;
- local writes: at most one `good` row plus authority/ack lifecycle metadata;
- no deployment and no deliberate network breakage/process kill.

Wait for explicit approval. Earlier S6 authorization does not count.

- [ ] **Step 2: Run the bounded local WebSocket acceptance**

After approval, run the local gateway through its normal credential loaders. Never print targets, IDs, secrets, response bodies or
raw exceptions. Verify visually and from safe counts:

1. new accepted message receives EYE;
2. successful completion removes EYE and appends exactly one `✅ 采纳` button;
3. original requester click gives private success and writes one `good` row;
4. repeated click adds no row and starts no turn;
5. authorized observer click, if approved, gets a private denial, changes no card and writes nothing;
6. cancelled and bounded failure/timeout turns remove EYE and show no feedback button;
7. feedback JSONL contains only the six approved fields and no content;
8. logs contain no credentials, raw tokens, complete IDs or provider details.

If permissions, publication or chat membership fail, stop and report the real prerequisite. Do not replace live failure with mock
evidence. Do not deliberately induce a network failure; deterministic tests own uncertain-delivery cases.

- [ ] **Step 3: Record non-sensitive evidence and finalize status**

Record only dates, pass/fail categories and counts in the spec/PR. After all acceptance rows pass, update the roadmap to S5b
completed and S5c next, preserving R33–R35. Rerun `git diff --check` and the docs/process status check. Amend the docs commit or make
one small status-only commit:

```powershell
git add .superpowers/specs/2026-09-12-feishu-capability-completion-design.md .superpowers/specs/2026-09-25-feishu-s5b-read-and-feedback-design.md
git commit -m "docs: record Feishu S5b acceptance"
```

- [ ] **Step 4: Push and open the PR with the complete template**

Push `codex/feishu-s5b-feedback`, open against `main`, fill every template section and AI-usage disclosure. Include ack state
machine, feedback authorization boundary, persistent idempotency, exact local/live results, required permissions, rollout/rollback,
and explicit S5c exclusions. Attach the resulting PR to this Codex task.

- [ ] **Step 5: Close CI and review after every push**

```powershell
gh pr checks --watch
gh pr view --json statusCheckRollup,url
```

On failure, resolve the newest failed run without inventing an ID:

```powershell
$runId = gh run list --branch codex/feishu-s5b-feedback --status failure --limit 1 --json databaseId --jq '.[0].databaseId'
gh run view $runId --log-failed
```

Fix the root cause, rerun the focused command for touched modules, push and watch again. Inspect every unresolved human/automated
conversation after each update. Validate findings, fix actionable items with tests, reply and resolve; explain and resolve incorrect
findings. Trigger Greptile only when no review is running and repeat until 5/5 with zero unresolved conversations.

- [ ] **Step 6: Merge and monitor post-merge workflows**

After all requirements pass, merge using the repository's accepted strategy. Monitor the merge commit's main CI, full CodeQL,
applicable synthetic/interactive-shell checks and release workflow. A post-merge failure is unfinished delivery: fix or revert before
reporting completion. Report conditionally skipped workflows accurately.

**Completion:** PR merged, required checks/reviews green, post-merge workflows healthy, no outstanding external configuration except
explicitly recorded prerequisites, and the next roadmap item is S5c without any S5c implementation in this branch.

## Plan approval boundary

This plan does not authorize product/test edits, live Feishu actions, pushes, PR creation or merge. Begin Task 1 only after explicit
user approval of this plan. Task 10 separately requires fresh live authorization with the exact target/actions/counts even after the
implementation plan is approved.
