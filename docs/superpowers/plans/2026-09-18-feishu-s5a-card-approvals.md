# Feishu S5a Card Approvals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Feishu text-reply write-tool approvals with secure Card JSON 2.0 callbacks, including concurrency safety, SDK CARD-frame compatibility, live validation, and delivery through merge.

**Architecture:** `ApprovalBroker` remains the transport-neutral decision gate. Feishu adds a server-side two-token authority registry and callback orchestrator; pure card construction stays in `integrations/feishu`, and the existing worker locally adapts the verified `lark-oapi 1.7.3` CARD-frame defect. Keep the text route only until real callback acceptance passes, then delete it before opening the PR.

**Tech Stack:** Python 3.11+, `threading`, `lark-oapi>=1.7.3` (lockfile: 1.7.3), Card JSON 2.0/CardKit, pytest, ruff, mypy, GitHub CLI.

**Spec:** [`.superpowers/specs/2026-09-18-feishu-s5a-card-approvals-design.md`](../../../.superpowers/specs/2026-09-18-feishu-s5a-card-approvals-design.md)

## Global Constraints

- Scope is S5a only: no reactions, read receipts, retry UX, report cards, public callback server, polling, or cross-process persistence.
- The broker ID stays server-side. Approve and Deny use distinct random opaque tokens; each wire value is exactly `{"approval_id": "<opaque-action-token>"}`.
- Derive the decision only from server-side token state. Validate current allowlist, requester `open_id`, original `chat_id`, `action.tag == "button"`, and the exact value key set before claim.
- Callback handling performs no REST call, retry, sleep, executor dispatch, turn creation, or wait, and returns within three seconds.
- The first authorized click atomically consumes both sibling tokens. Duplicate, concurrent, late, unauthorized, wrong-actor, and wrong-chat callbacks never resolve twice or mutate the shared card.
- Create/send failures fail closed, call `ApprovalBroker.abandon()`, and leave no pending state. There is no text fallback.
- Never log action tokens, raw callback payloads, card/message bodies, full arguments, credentials, or authorization headers. Result cards show only outcome and tool name.
- Use monotonic deadlines and `MAX_APPROVAL_WAIT_SECONDS` tombstone retention. Keep `gateway.core` transport-neutral and card builders free of SDK/network/gateway imports.
- SDK compatibility is instance-local: no site-packages edit, process-global monkeypatch, or copied full handler. Preserve EVENT, CONTROL, fragmentation, reconnect, and stop behavior.
- The final PR contains no text approval route. Changed Protocol methods use docstring-only bodies.
- After every PR push, monitor CI and reviews; merge requires green checks and Greptile 5/5 with zero unresolved comments. After merge, monitor main CI, CodeQL, and release.

---

### Task 1: Linearize the Transport-Neutral Broker

**Files:**
- Modify: `gateway/core/middleware/approvals.py`
- Create: `gateway/tests/runtime/test_approval_broker_lifecycle.py`
- Modify: `gateway/tests/runtime/test_approval_broker_close.py`
- Modify: `gateway/tests/storage/test_security_audit.py`

**Interfaces:**
- Consumes: existing `create()`, `resolve()`, `wait()`, `close()` contracts.
- Produces: `ApprovalBroker.abandon(approval_id: str) -> bool` and one lock-linearized resolve/expire terminal state.

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_abandon_releases_waiter_without_expire_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    actions: list[str] = []
    monkeypatch.setattr(approvals_module, "audit_security_action", lambda **kw: actions.append(kw["action"]))
    broker = ApprovalBroker()
    approval_id = broker.create(platform="feishu", chat_id="oc_1")
    result: list[tuple[bool, str]] = []
    started = threading.Event()

    def _wait() -> None:
        started.set()
        result.append(broker.wait(approval_id, timeout=5.0))

    thread = threading.Thread(target=_wait)
    thread.start()
    assert started.wait(1.0)
    assert broker.abandon(approval_id) is True
    thread.join(1.0)
    assert result == [(False, "")]
    assert actions == ["approval.create", "approval.abandon"]
```

Add a Barrier-based resolve/timeout race asserting exactly one of `approval.resolve` and `approval.expire`, plus unknown/duplicate abandon and closed-broker coverage.

- [ ] **Step 2: Confirm RED**

Run: `uv run python -m pytest gateway/tests/runtime/test_approval_broker_lifecycle.py gateway/tests/runtime/test_approval_broker_close.py gateway/tests/storage/test_security_audit.py -q`

Expected: FAIL because `abandon()` is absent and timeout finalization is outside the lock.

- [ ] **Step 3: Implement abandonment and lock-finalized wait**

```python
def abandon(self, approval_id: str) -> bool:
    """Withdraw an approval that was never successfully exposed."""
    with self._lock:
        pending = self._pending.pop(approval_id, None)
        if pending is None or pending.event.is_set():
            return False
        pending.event.set()
        platform, chat_id = pending.platform, pending.chat_id
    audit_security_action(
        action="approval.abandon", platform=platform or None, chat_id=chat_id or None,
        resource_type="approval", resource_id=approval_id, outcome="denied",
    )
    return True

def wait(self, approval_id: str, *, timeout: float) -> tuple[bool, str]:
    """Block for one linearized decision; expiry counts as deny."""
    with self._lock:
        pending = self._pending.get(approval_id)
        closed = self._closed
    if pending is None:
        return (False, "")
    pending.event.wait(0 if closed else timeout)
    with self._lock:
        if self._pending.get(approval_id) is not pending:
            return (False, "")
        self._pending.pop(approval_id)
        decided = pending.event.is_set()
        result = (pending.approved, pending.decided_by)
    if decided:
        return result
    audit_security_action(
        action="approval.expire", platform=pending.platform or None,
        chat_id=pending.chat_id or None, resource_type="approval",
        resource_id=approval_id, outcome="denied",
    )
    return (False, "")
```

Keep decision fields and `event.set()` together under `resolve()`'s lock.

- [ ] **Step 4: Verify GREEN and cross-transport compatibility**

Run: `uv run python -m pytest gateway/tests/runtime/test_approval_broker_lifecycle.py gateway/tests/runtime/test_approval_broker_close.py gateway/tests/storage/test_security_audit.py gateway/tests/slack/test_approvals.py gateway/tests/discord/test_approvals_allowlist.py gateway/tests/telegram/test_approvals.py gateway/tests/buzz/test_approvals.py gateway/tests/feishu/test_approvals.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gateway/core/middleware/approvals.py gateway/tests/runtime/test_approval_broker_lifecycle.py gateway/tests/runtime/test_approval_broker_close.py gateway/tests/storage/test_security_audit.py
git commit -m "fix: linearize gateway approval lifecycle"
```

### Task 2: Build Pure Approval Cards

**Files:**
- Create: `integrations/feishu/approval_cards.py`
- Modify: `integrations/feishu/__init__.py`
- Create: `tests/integrations/test_feishu_approval_cards.py`

**Interfaces:**
- Consumes: `FEISHU_CARD_SCHEMA` and already-redacted `arguments_preview()` text.
- Produces: `render_approval_prompt_card(*, approve_token: str, deny_token: str, tool_name: str, reason: str, arguments_preview: str) -> dict[str, object]`; `render_approval_result_card(*, tool_name: str, approved: bool) -> dict[str, object]` through `integrations.feishu`.

- [ ] **Step 1: Write failing card-contract tests**

```python
def test_prompt_has_distinct_callback_tokens_and_no_client_decision() -> None:
    spec = render_approval_prompt_card(
        approve_token="approve-token", deny_token="deny-token",
        tool_name="write_tool", reason="Publish summary",
        arguments_preview='{"channel":"ops"}',
    )
    elements = spec["body"]["elements"]  # type: ignore[index]
    columns = next(e for e in elements if e["tag"] == "column_set")["columns"]
    buttons = [column["elements"][0] for column in columns]
    assert spec["schema"] == "2.0"
    assert [button["behaviors"] for button in buttons] == [
        [{"type": "callback", "value": {"approval_id": "approve-token"}}],
        [{"type": "callback", "value": {"approval_id": "deny-token"}}],
    ]
    assert all("name" not in button and "value" not in button for button in buttons)

def test_result_has_no_buttons_or_request_details() -> None:
    serialized = json.dumps(render_approval_result_card(tool_name="write_tool", approved=False))
    assert "Denied" in serialized and "write_tool" in serialized
    assert "button" not in serialized and "approval_id" not in serialized
```

Also assert a sentinel secret not present in the supplied redacted preview never appears.

- [ ] **Step 2: Confirm RED**

Run: `uv run python -m pytest tests/integrations/test_feishu_approval_cards.py -q`

Expected: collection FAIL because the module does not exist.

- [ ] **Step 3: Implement focused Card JSON 2.0 builders**

```python
def _callback_button(label: str, token: str, *, kind: str) -> dict[str, object]:
    return {
        "tag": "button", "text": {"tag": "plain_text", "content": label},
        "type": kind, "width": "fill",
        "behaviors": [{"type": "callback", "value": {"approval_id": token}}],
    }
```

The prompt returns `schema: FEISHU_CARD_SCHEMA`, orange `Approval required` header, one markdown details block, then a two-column set containing `primary_filled` Approve and `danger_filled` Deny buttons. The result returns a green/red header and one markdown outcome line, with no buttons. Re-export only the two builders; do not import `lark_oapi`.

- [ ] **Step 4: Verify card and boot-path contracts**

Run: `uv run python -m pytest tests/integrations/test_feishu_approval_cards.py tests/integrations/test_feishu_boot_path.py tests/shared/test_integrations_api_border.py -q`

Expected: PASS and `integrations.feishu` still does not import the SDK.

- [ ] **Step 5: Commit**

```bash
git add integrations/feishu/approval_cards.py integrations/feishu/__init__.py tests/integrations/test_feishu_approval_cards.py
git commit -m "feat: build Feishu approval cards"
```

### Task 3: Implement the Two-Token Authority Registry

**Files:**
- Rewrite: `gateway/transports/feishu/pending_approvals.py`
- Rewrite: `gateway/tests/feishu/test_pending_approvals.py`

**Interfaces:**
- Consumes: opaque tokens, broker ID, requester/chat, tool name, monotonic deadline.
- Produces: `ClaimStatus`, `ApprovalClaim`, and `register()`, `claim()`, `settle()`, `finish_wait()`, `discard_request()`, `drain()` with O(1) indexes.

Exact signatures: `register(*, broker_approval_id: str, approve_token: str, deny_token: str, requester_open_id: str, chat_id: str, tool_name: str, expires_at: float) -> None`; `claim(action_token: str, *, open_id: str, chat_id: str) -> ApprovalClaim`; `settle(broker_approval_id: str, *, approved: bool) -> bool`; `finish_wait(broker_approval_id: str) -> None`; `discard_request(broker_approval_id: str) -> bool`; `drain() -> list[str]`.

- [ ] **Step 1: Write failing authority/concurrency/retention tests**

```python
def _register(pending: PendingApprovals, *, expires_at: float = 50.0) -> None:
    pending.register(
        broker_approval_id="broker-1", approve_token="approve-token",
        deny_token="deny-token", requester_open_id="ou_requester",
        chat_id="oc_chat", tool_name="write_tool", expires_at=expires_at,
    )

def test_wrong_actor_does_not_consume_sibling() -> None:
    pending = PendingApprovals(clock=lambda: 10.0)
    _register(pending)
    assert pending.claim("approve-token", open_id="ou_other", chat_id="oc_chat").status is ClaimStatus.WRONG_ACTOR
    accepted = pending.claim("deny-token", open_id="ou_requester", chat_id="oc_chat")
    assert accepted.status is ClaimStatus.CLAIMED and accepted.approved is False
```

Use a three-party `threading.Barrier` to race both tokens and assert one `CLAIMED`, one `IN_PROGRESS`. Cover wrong chat, unknown token, exact-deadline expiry, settled duplicate through both tokens, tombstone eviction, `finish_wait()` preserving CLAIMED/SETTLED but removing OPEN, discard, and unique drain.

- [ ] **Step 2: Confirm RED**

Run: `uv run python -m pytest gateway/tests/feishu/test_pending_approvals.py -q`

Expected: FAIL against the old message-ID registry.

- [ ] **Step 3: Implement exact public types and shared request state**

```python
class ClaimStatus(Enum):
    CLAIMED = "claimed"
    SETTLED = "settled"
    IN_PROGRESS = "in_progress"
    WRONG_ACTOR = "wrong_actor"
    WRONG_CHAT = "wrong_chat"
    UNAVAILABLE = "unavailable"

@dataclass(frozen=True, slots=True)
class ApprovalClaim:
    status: ClaimStatus
    broker_approval_id: str = ""
    approved: bool | None = None
    tool_name: str = ""
```

Keep one mutable record shared by both token keys plus a broker-ID index. Under one lock, lazy-clean expired live records and old tombstones, validate actor/chat before returning settled results, and move OPEN to CLAIMED. `settle()` records one result for both tokens. `finish_wait()` removes only OPEN because the waiter may wake between broker resolve and registry settle. `drain()` clears both indexes and returns each live broker ID once.

- [ ] **Step 4: Verify repeated concurrency**

Run: `uv run python -m pytest gateway/tests/feishu/test_pending_approvals.py -q`

Then run: `1..20 | ForEach-Object { uv run python -m pytest gateway/tests/feishu/test_pending_approvals.py -q; if ($LASTEXITCODE -ne 0) { throw "registry repetition failed on run $_" } }`

Do not add a test dependency only for repetition.

Expected: every run PASS.

- [ ] **Step 5: Commit**

```bash
git add gateway/transports/feishu/pending_approvals.py gateway/tests/feishu/test_pending_approvals.py
git commit -m "feat: secure Feishu approval action tokens"
```

### Task 4: Add the Card Callback Orchestrator

**Files:**
- Modify: `gateway/transports/feishu/approvals.py`
- Rewrite callback portions of: `gateway/tests/feishu/test_approvals.py`

**Interfaces:**
- Consumes: SDK callback model, broker, registry, allowlist, result builder.
- Produces: `handle_card_action(data: P2CardActionTrigger, *, broker: ApprovalBroker, pending_approvals: PendingApprovals, env_allowed_open_ids: list[str], logger: logging.Logger) -> P2CardActionTriggerResponse`.

- [ ] **Step 1: Write failing strict-validation tests**

```python
def _callback(token: str, *, open_id: str = REQUESTER, chat_id: str = CHAT) -> P2CardActionTrigger:
    return P2CardActionTrigger({"event": {"operator": {"open_id": open_id},
        "context": {"open_chat_id": chat_id},
        "action": {"tag": "button", "value": {"approval_id": token}}}})

def test_authorized_click_resolves_and_returns_raw_card(monkeypatch: pytest.MonkeyPatch) -> None:
    broker, pending, approval_id = _live_request()
    monkeypatch.setattr(approvals_module, "is_open_id_authorized", lambda **_kw: True)
    response = handle_card_action(_callback("approve-token"), broker=broker,
        pending_approvals=pending, env_allowed_open_ids=[REQUESTER], logger=LOGGER)
    assert broker.wait(approval_id, timeout=0.0) == (True, REQUESTER)
    assert response.toast.content == "Approved"
    assert response.card.type == "raw"
    assert "button" not in json.dumps(response.card.data)
```

Add tests for settled sibling duplicate (one broker resolve), unauthorized allowlist, wrong requester/chat, unknown/expired token, missing models, non-button tag, non-mapping/extra-key/blank token values, IN_PROGRESS, and broker resolve returning `False`. Rejected paths must not leak callback data or mutate broker/card.

- [ ] **Step 2: Confirm RED**

Run: `uv run python -m pytest gateway/tests/feishu/test_approvals.py -k 'card_action or callback or duplicate or malformed or unauthorized' -q`

Expected: FAIL because the handler is absent.

- [ ] **Step 3: Implement short synchronous transitions**

```python
def _toast(content: str, *, kind: str = "info") -> P2CardActionTriggerResponse:
    return P2CardActionTriggerResponse({"toast": {"type": kind, "content": content}})

def _settled_response(*, approved: bool, tool_name: str) -> P2CardActionTriggerResponse:
    return P2CardActionTriggerResponse({
        "toast": {"type": "success", "content": "Approved" if approved else "Denied"},
        "card": {"type": "raw", "data": render_approval_result_card(tool_name=tool_name, approved=approved)},
    })
```

Order: read models; require button; treat values without `approval_id` as non-S5a empty response; enforce exact one-key mapping; check live allowlist; claim registry; resolve broker only for CLAIMED; settle registry; return card. SETTLED returns `Already approved/denied` with no card. All authority failures use `This approval is unavailable`. Let unexpected exceptions reach the worker wrapper.

- [ ] **Step 4: Verify callbacks and audits**

Run: `uv run python -m pytest gateway/tests/feishu/test_approvals.py gateway/tests/feishu/test_pending_approvals.py gateway/tests/storage/test_security_audit.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gateway/transports/feishu/approvals.py gateway/tests/feishu/test_approvals.py gateway/tests/storage/test_security_audit.py
git commit -m "feat: handle Feishu card approval callbacks"
```

### Task 5: Move the Prompter to CardKit

**Files:**
- Modify: `gateway/transports/feishu/approvals.py`
- Modify: `gateway/transports/feishu/inbound_handler.py`
- Modify: `gateway/tests/feishu/test_approvals.py`
- Modify: `gateway/tests/feishu/test_inbound_handler.py`

**Interfaces:**
- Consumes: `FeishuCardClient`, prompt builder, broker abandonment, registry.
- Produces: `ApprovalCardClient` Protocol and card-based `FeishuApprovalPrompter`.

- [ ] **Step 1: Write failing lifecycle/cleanup tests**

```python
class _FakeCardClient:
    def __init__(self, *, card_id: str = "card-1", message_id: str = "message-1") -> None:
        self.card_id, self.message_id = card_id, message_id
        self.created: list[dict[str, object]] = []
    def create_card(self, spec: dict[str, object]) -> str:
        self.created.append(spec); return self.card_id
    def send_card(self, chat_id: str, card_id: str, *, receive_id_type: str = "chat_id") -> str:
        return self.message_id
```

Pin register-before-send, timeout capping, callback resolution, and settled tombstone retention. Separately test builder/create/register/send exceptions and empty IDs; every failure returns `(False, "")`, makes `pending.drain() == []`, and makes `broker.close() == 0`.

- [ ] **Step 2: Confirm RED**

Run: `uv run python -m pytest gateway/tests/feishu/test_approvals.py -k 'request or prompt or card' -q`

Expected: FAIL because the prompter still uses `send_text`.

- [ ] **Step 3: Implement the Protocol and ordered lifecycle**

```python
class ApprovalCardClient(Protocol):
    def create_card(self, spec: dict[str, object]) -> str:
        """Create a card entity and return its card ID."""
    def send_card(self, chat_id: str, card_id: str, *, receive_id_type: str = "chat_id") -> str:
        """Expose a card in one chat and return its message ID."""
```

In `request()`: cap timeout; create broker; generate two tokens with named `_action_token()` using `secrets.token_urlsafe(32)`; build/create card; register before send with `expires_at=time.monotonic()+timeout`; send card; on any exposure failure discard request and abandon broker; otherwise wait and call `finish_wait()` in `finally`. Never store message ID or argument preview in the registry.

- [ ] **Step 4: Wire the SDK client**

```python
FeishuApprovalPrompter(
    broker=approvals,
    card_client=FeishuCardClient(settings.app_id, settings.app_secret),
    chat_id=inbound.chat_id,
    requester_open_id=inbound.open_id,
    pending_approvals=pending_approvals,
)
```

Keep text sending for ordinary turn output; it is no longer an approval dependency.

- [ ] **Step 5: Verify and commit**

Run: `uv run python -m pytest gateway/tests/feishu/test_approvals.py gateway/tests/feishu/test_inbound_handler.py tests/shared/test_integrations_api_border.py -q`

Expected: PASS.

```bash
git add gateway/transports/feishu/approvals.py gateway/transports/feishu/inbound_handler.py gateway/tests/feishu/test_approvals.py gateway/tests/feishu/test_inbound_handler.py
git commit -m "feat: prompt Feishu approvals with CardKit"
```

### Task 6: Register Callbacks and Adapt SDK CARD Frames

**Files:**
- Modify: `gateway/transports/feishu/worker.py`
- Modify: `gateway/tests/feishu/test_worker.py`
- Modify: `gateway/tests/feishu/test_worker_stop.py`

**Interfaces:**
- Consumes: `handle_card_action()`, SDK `P2CardActionTrigger`, existing `_ReadyOnConnectClient`, and SDK frame/header types isolated to `worker.py`.
- Produces: dual dispatcher registration, safe callback wrapper, instance-local CARD compatibility, registry drain before broker close.

- [ ] **Step 1: Write a failing synthetic CARD-frame test**

Build a protobuf `Frame` with `type=card`, message/trace IDs, `sum=1`, `seq=0`, and a real card callback payload. Register a real `EventDispatcherHandler`, monkeypatch `Client._write_message` (the base socket boundary, not the instance override) with a named async recorder, then assert:

```python
asyncio.run(client._handle_data_frame(frame))
assert callback_payloads == [expected_payload]
assert len(written_frames) == 1
ack = Frame()
ack.ParseFromString(written_frames[0])
assert _header(ack, HEADER_TYPE) == MessageType.CARD.value
response = JSON.unmarshal(ack.payload.decode("utf-8"), Response)
assert response.code == HTTPStatus.OK
assert response.data
```

Add an EVENT-frame characterization proving ordinary dispatch still uses the base route once. Do not open a network connection.

- [ ] **Step 2: Confirm the locked SDK defect**

Run: `uv run python -m pytest gateway/tests/feishu/test_worker.py -k 'card_frame or event_frame' -q`

Expected: FAIL with zero card callback calls and no response frame on the unadapted SDK path.

- [ ] **Step 3: Implement the instance-local adapter**

For non-CARD frames, delegate directly to `super()._handle_data_frame(frame)`. For CARD, copy the frame, change only the copy's type to EVENT for base dispatch, and track its message ID in an instance set protected by a lock. Permanently override `_write_message()` to change the serialized response header back to CARD only when its message ID is in that set, then call `super()._write_message()`. Remove the ID in `finally`.

```python
async def _handle_data_frame(self, frame: Frame) -> None:
    type_header = next((header for header in frame.headers if header.key == HEADER_TYPE), None)
    if type_header is None or type_header.value != MessageType.CARD.value:
        await super()._handle_data_frame(frame)
        return
    message_id = _get_by_key(frame.headers, HEADER_MESSAGE_ID)
    forwarded = Frame()
    forwarded.CopyFrom(frame)
    next(header for header in forwarded.headers if header.key == HEADER_TYPE).value = MessageType.EVENT.value
    with self._card_frame_lock:
        self._adapted_card_message_ids.add(message_id)
    try:
        await super()._handle_data_frame(forwarded)
    finally:
        with self._card_frame_lock:
            self._adapted_card_message_ids.discard(message_id)
```

`_write_message()` parses outgoing bytes, obtains an optional message ID without raising on ping/control frames, restores CARD for tracked IDs, and forwards bytes. This preserves the SDK's fragmentation, timing, response encoding, and ACK logic without a global monkeypatch or full handler copy.

- [ ] **Step 4: Register and safely wrap the callback**

Create `on_card_action(data)` beside `on_message(data)`. It synchronously calls `handle_card_action()`; on unexpected exception it logs `exc_info=True` without payload and returns only:

```python
P2CardActionTriggerResponse({
    "toast": {"type": "error", "content": "This interaction could not be completed"}
})
```

Register both callbacks on the same builder:

```python
dispatcher_handler = (
    EventDispatcherHandler.builder(encrypt_key="", verification_token="")
    .register_p2_im_message_receive_v1(on_message)
    .register_p2_card_action_trigger(on_card_action)
    .build()
)
```

Do not use the turn executor. In `finally`, drain transport state before `approvals.close()`.

- [ ] **Step 5: Verify worker behavior**

Run: `uv run python -m pytest gateway/tests/feishu/test_worker.py gateway/tests/feishu/test_worker_stop.py gateway/tests/feishu/test_approvals.py -q`

Expected: PASS for CARD ACK, EVENT non-regression, safe error response, direct callback handling, and shutdown release.

- [ ] **Step 6: Commit**

```bash
git add gateway/transports/feishu/worker.py gateway/tests/feishu/test_worker.py gateway/tests/feishu/test_worker_stop.py
git commit -m "feat: dispatch Feishu card callbacks"
```

### Task 7: Document Control-Plane Prerequisites

**Files:**
- Create: `docs/messaging/feishu.mdx`
- Modify: `docs/docs.json`

**Interfaces:**
- Consumes: public configuration and Feishu console behavior.
- Produces: navigable Messaging documentation with no SDK internals, endpoints, bug history, or private state names.

- [ ] **Step 1: Write the operator guide**

Start with this action-focused content, then add existing start/verify commands and required message permissions using the concise style of `docs/messaging/telegram.mdx`:

```mdx
---
title: Feishu
description: Connect an OpenSRE chat gateway to a Feishu custom app.
---

## Configure the app

Set `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, and a non-empty comma-separated
`FEISHU_ALLOWED_OPEN_IDS`. Only listed members can start turns or decide their
own write-tool approvals.

## Receive messages and card actions

Use long-connection delivery. Add `card.action.trigger` under **Subscribed
callbacks** (not **Subscribed events**) and publish a new app version after
changing permissions or subscriptions.

## Approve write tools

Only the member whose turn requested a tool can use its card buttons, and the
decision must come from the original chat. Typing `approve` or `deny` is an
ordinary chat message and does not authorize a tool.
```

- [ ] **Step 2: Add navigation and validate**

Insert `"messaging/feishu"` in the Messaging pages list in `docs/docs.json`.

Run: `uv run python -m json.tool docs/docs.json > NUL`

Run: `git diff --check -- docs/messaging/feishu.mdx docs/docs.json`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add docs/messaging/feishu.mdx docs/docs.json
git commit -m "docs: add Feishu gateway setup"
```

### Task 8: Run the Complete Local Gate

**Files:**
- Verify only; any fix belongs in its owning file from Tasks 1–7.

**Interfaces:**
- Consumes: implemented S5a while the temporary text route still exists.
- Produces: clean live-test candidate and exact validation record for the PR.

- [ ] **Step 1: Inspect branch scope and secrets**

Run: `git status --short`

Run: `git diff main...HEAD`

Expected: only approved S5a/spec/plan/docs files; no `.env`, credentials, raw callback capture, or cache file.

- [ ] **Step 2: Run mandatory static checks**

Run: `make lint`

Run: `make format-check`

Run: `make typecheck`

Expected: PASS. If formatting fails, run `make format`, inspect the diff, then rerun all three.

- [ ] **Step 3: Run mapped and border suites**

Run: `uv run python -m pytest gateway/tests/ tests/integrations/ tests/shared/test_integrations_api_border.py -q`

Expected: PASS. This is the `gateway/` + `integrations/` path-rule selection plus the explicit facade border.

- [ ] **Step 4: Run the high-signal approval set**

Run: `uv run python -m pytest gateway/tests/runtime/test_approval_broker_lifecycle.py gateway/tests/runtime/test_approval_broker_close.py gateway/tests/storage/test_security_audit.py gateway/tests/feishu/test_approvals.py gateway/tests/feishu/test_pending_approvals.py gateway/tests/feishu/test_worker.py gateway/tests/feishu/test_worker_stop.py gateway/tests/slack/test_approvals.py gateway/tests/discord/test_approvals_allowlist.py gateway/tests/telegram/test_approvals.py gateway/tests/buzz/test_approvals.py tests/integrations/test_feishu_approval_cards.py tests/integrations/test_feishu_card_client.py tests/integrations/test_feishu_card_document.py -q`

Expected: PASS. Record pass/skip counts and elapsed time.

- [ ] **Step 5: Commit only genuine gate fixes**

If files changed, stage only reviewed fixes and run:

```bash
git commit -m "test: harden Feishu card approval coverage"
```

If no file changed, create no empty commit.

### Task 9: Pass Live Feishu Acceptance and Remove Text Approval

**Files:**
- Live validation with the configured test app/chat.
- Modify: `gateway/transports/feishu/worker.py`
- Modify: `gateway/tests/feishu/test_worker.py`
- Modify docs/comments in: `gateway/transports/feishu/approvals.py`, `gateway/transports/feishu/pending_approvals.py`

**Interfaces:**
- Consumes: published long-connection test app, subscribed `card.action.trigger`, allowlisted requester, unauthorized bystander.
- Produces: redacted evidence for six scenarios and no text approval parser/route.

- [ ] **Step 1: Confirm live prerequisites and authorization**

Confirm long connection, callback subscription, latest published app version, requester allowlist, and group bystander. Obtain explicit authorization for the chosen test chat and harmless observable write-tool action before sending.

- [ ] **Step 2: Validate requester decisions**

Run one Approve and one Deny request. Approve must replace the card and execute the tool exactly once; Deny must replace the card and execute zero times. Record only timestamp, scenario, result, card transition, and execution count.

- [ ] **Step 3: Validate hostile/replayed/lifecycle cases**

Confirm: bystander receives private unavailable feedback and does not consume the request; duplicate/redelivery receives prior-result feedback without second resolve/card/tool; expired click gets unavailable and no shared mutation; shutdown immediately denies/releases a waiting tool. Any platform error, missing callback, second execution, or shared mutation fails the gate and returns work to the owning task.

- [ ] **Step 4: Delete the temporary text route**

Remove `_APPROVE_WORDS`, `_DENY_WORDS`, `_decision()`, `_resolve_approval_reply()`, and the `inbound.parent_id` approval branch. Delete their tests. Preserve `parent_id` normalization so threaded replies remain ordinary turns.

- [ ] **Step 5: Pin text as ordinary inbound content**

```python
def test_text_approve_is_dispatched_as_an_ordinary_turn() -> None:
    callbacks: dict[str, Callable[[P2ImMessageReceiveV1], None]] = {}

    class _DispatcherBuilder:
        def register_p2_im_message_receive_v1(self, callback):
            callbacks["message"] = callback
            return self
        def register_p2_card_action_trigger(self, callback):
            callbacks["card"] = callback
            return self
        def build(self) -> object:
            return object()

    builder = _DispatcherBuilder()
    client = MagicMock()
    client.start.side_effect = lambda: callbacks["message"](_message_event(text="approve", parent_id="om_old_prompt"))
    with (
        patch.object(worker.EventDispatcherHandler, "builder", return_value=builder),
        patch.object(worker, "_ReadyOnConnectClient", return_value=client),
        patch.object(worker, "_dispatch_turn") as dispatch,
        patch.object(ApprovalBroker, "resolve") as resolve,
    ):
        _run_worker_once()

    dispatch.assert_called_once()
    resolve.assert_not_called()
```

Implement `_message_event()` and `_run_worker_once()` as named test helpers that construct the existing SDK event and call `run_feishu_gateway_thread()` with fake bindings/executor/events. Do not test merely that vocabulary constants disappeared.

- [ ] **Step 6: Rerun final local gates**

Run: `make lint`

Run: `make format-check`

Run: `make typecheck`

Run: `uv run python -m pytest gateway/tests/ tests/integrations/ tests/shared/test_integrations_api_border.py -q`

Expected: all PASS and no text approval test remains.

- [ ] **Step 7: Commit the migration**

```bash
git add gateway/transports/feishu/worker.py gateway/transports/feishu/approvals.py gateway/transports/feishu/pending_approvals.py gateway/tests/feishu/test_worker.py
git commit -m "refactor: retire Feishu text approvals"
```

### Task 10: PR, CI, Review, Merge, and Post-Merge Verification

**Files:**
- Read/fill: `.github/PULL_REQUEST_TEMPLATE.md`
- Change product/tests only for verified CI or review root causes.

**Interfaces:**
- Consumes: green local branch, redacted live evidence, GitHub CLI.
- Produces: attached PR, green checks, Greptile 5/5, merged commit, green main/CodeQL/release.

- [ ] **Step 1: Review final branch**

Run: `git status --short --branch`

Run: `git diff --check main...HEAD`

Run: `git log --oneline main..HEAD`

Expected: clean tree, no whitespace errors, only S5a/spec/plan commits.

- [ ] **Step 2: Push without force**

Run: `git push -u origin codex/feishu-s5a-card-approvals`

Expected: success.

- [ ] **Step 3: Open and attach a complete PR**

Use title `feat: add secure Feishu card approvals`. In the template: leave `Fixes #` without a number because none was supplied; explain two-token server authority, broker linearization, callback response, SDK compatibility, docs, and text-route removal; include redacted live proof; list exact local commands/counts; truthfully check all AI review items.

Run: `gh pr create --title "feat: add secure Feishu card approvals" --body-file <reviewed-body-file>`

Attach the created PR URL to this task with the Codex app artifact tool.

- [ ] **Step 4: Close the CI loop after every push**

Run: `gh pr checks --watch`

On failure, run `gh run view <run-id> --log-failed`, fix the root cause, rerun Task 9's local gates, commit, push, and watch again. Do not skip tests, add constant-condition toggles, or ignore concurrency flakes.

- [ ] **Step 5: Resolve reviews and reach Greptile 5/5**

Inspect all reviews and unresolved conversations after each CI cycle. Fix actionable issues; explain and resolve non-actionable findings. Post `@greptile review` only when no review is running. Repeat until 5/5 and zero unresolved comments.

- [ ] **Step 6: Merge only after every gate**

Confirm required checks green, live acceptance recorded, no text route, current PR body, Greptile 5/5, and no unresolved comments. Merge using repository convention and record the merge SHA.

- [ ] **Step 7: Monitor post-merge workflows**

Run `gh run list --branch main --commit <merge-sha>` and `gh run watch <run-id>` for main CI, full CodeQL, and release. Fix or revert any failure through the same loop. Report completion only when applicable workflows are green or legitimately skipped.

## Plan Self-Review Record

- Spec coverage: Tasks 1–10 cover broker lifecycle, cards, token authority, callback validation, cleanup, CARD-frame compatibility, docs, local gates, six live cases, text removal, CI/review/merge, and post-merge monitoring.
- Placeholder scan: no deferred implementation markers remain; angle-bracket command values are runtime GitHub identifiers or the reviewed temporary PR-body path.
- Type consistency: `ClaimStatus`, `ApprovalClaim`, registry methods, card builders, `handle_card_action()`, and `ApprovalCardClient` retain one spelling and signature throughout.
