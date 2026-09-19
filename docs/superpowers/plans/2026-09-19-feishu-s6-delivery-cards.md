# Feishu S6 Delivery Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver watchdog alarms, investigation reports, background-RCA summaries, and scheduled messages through one lossless non-streaming Feishu CardKit path with safe plaintext fallback and unambiguous whole-delivery status.

**Architecture:** Add source-aware pagination and safe SDK result types beneath a single `integrations/feishu/document_delivery.py` state machine. Producers emit canonical Markdown and keep policy in thin adapters; only the integration-local owner paginates, sends cards, advances the original-source cursor, falls back to plaintext, classifies errors, and writes delivery summaries.

**Tech Stack:** Python 3.11+, frozen dataclasses/`StrEnum`, `lark-oapi>=1.7.3`, Feishu CardKit schema 2.0, pytest, ruff, mypy, GitHub CLI.

**Spec:** [`.superpowers/specs/2026-09-19-feishu-s6-delivery-cards-design.md`](../../../.superpowers/specs/2026-09-19-feishu-s6-delivery-cards-design.md)

## Global Constraints

- Scope is S6 only: no read receipts, reactions, retries, outbox, delivery receipts, buttons, or agent messaging tools.
- A delivery has exactly one overall status: `SUCCESS`, `DEGRADED_SUCCESS`, `FAILED`, or `SKIPPED`; message IDs never imply whole-delivery success.
- Advance the source cursor only after an explicit platform success with a nonempty message ID. A card entity ID is not a delivered message.
- `CardPage.source_start/source_end` and plaintext chunk ranges are half-open indexes into the original Python string; rendered wrappers never become fallback body text.
- Plaintext fallback starts at the failed page's original `source_start`, uses ordered chunks of at most 4096 Unicode code points, preserves whitespace, and never calls `strip` or truncate.
- A timeout, response parse failure, or unknown exception during a visible send is `DELIVERY_UNCERTAIN`; do not advance, and accept possible duplication of the current source range.
- Results, adapter errors, ordinary logs, and external chat output use fixed enums and fixed text. Never copy `str(exc)`, `repr(exc)`, `exc.args`, SDK `msg`, a response body, request object, or traceback into them.
- `redact_token()` is defense in depth only. Tests must prove secrets, body text, full targets, card/message IDs, and Authorization headers never escape even when a malicious exception contains all of them.
- One delivery writes one summary log, plus at most one degradation warning and one terminal-failure warning. Indexes are 1-based; confirmed count includes only successful message sends.
- The document-delivery layer does no retry, sleep, persistent cursor, or cooldown work. Existing outer retry policies remain outside it.
- Watchdog cooldown remains attempt-based: `SUCCESS`, `DEGRADED_SUCCESS`, and attempted `FAILED` retain the reservation; preflight `SKIPPED` does not reserve.
- Explicit and configured destination/type values are atomic pairs. Never combine a target from one source with a type from another.
- Canonical producer content is Markdown. Telegram converts to HTML, Slack converts to mrkdwn, Rocket.Chat and Feishu consume Markdown, and Discord keeps its embed adapter.
- Keep `integrations.feishu.__init__` SDK-free; do not re-export `document_delivery`, `card_client`, or raw SDK result types from the facade.
- Shared env/static names and verified Feishu error-code sets live in `config/constants/feishu.py` and are re-exported through `config/constants/__init__.py` only when another package needs them.
- Preserve package borders: `infrastructure` does not import `integrations`; `tools` does not gain a new direct vendor dependency; adapter SDK imports stay lazy on boot paths.
- Protocol methods changed by this work keep docstring-only bodies. New tests use named fakes rather than inline `type(...)` lambdas.
- Live sends require fresh user authorization in the execution turn. Do not simulate live success, deliberately cause network failure, or send unbounded repeats.
- After every PR push, monitor CI and reviews; merge requires green checks and Greptile 5/5 with zero unresolved comments. After merge, inspect every workflow triggered for the merge commit, including main CI, CodeQL, release, synthetic, and interactive-shell workflows, and record any workflow that was not triggered.

## Review Focus

- CRLF, Unicode line separators, CJK, and non-BMP emoji at a page boundary must keep contiguous source ranges and reconstruct the original string byte-for-byte after Python decoding; Task 1 adds this test.
- A platform response that says success but omits `card_id` or `message_id` must remain unconfirmed and must never advance the cursor; Tasks 2 and 3 add these tests.
- An exception raised after `send_card`/`message.create` begins, containing body and credential material, must become `DELIVERY_UNCERTAIN` without leaking any supplied value; Tasks 2 and 3 add this test.
- A scheduled task with `chat_id`, conflicting task params, and a configured `open_id` fallback must use exactly `(task.chat_id, "chat_id")`; Task 8 adds the full precedence test.
- Untrusted report text containing `[fake](https://attacker)`, Slack mention syntax, and HTML must not become a producer-controlled link/mention while canonical links generated from evidence remain valid Markdown; Task 4 adds this test.

---

### Task 1: Make Card Pagination Source-Aware

**Files:**
- Modify: `integrations/feishu/card_document.py`
- Modify: `tests/integrations/test_feishu_card_document.py`

**Interfaces:**
- Consumes: existing `paginate(text, *, budget, max_tables) -> list[CardPage]`, `render_card_spec()`, fence/table splitting, and byte/table budgets.
- Produces: `CardPage(text: str, index: int, source_start: int, source_end: int)` whose ranges partition the original nonblank input without gaps or overlap.

- [ ] **Step 1: Update simple-page expectations and add failing range invariants**

```python
def test_short_text_has_the_whole_source_range() -> None:
    assert paginate("hello") == [
        CardPage(text="hello", index=1, source_start=0, source_end=5)
    ]

@pytest.mark.parametrize(
    "text",
    [
        "first\r\n\r\nsecond\r\nthird",
        "标题\u2028段落\n\n尾部🙂",
        _OVERSIZE_FENCE,
        _OVERSIZE_TABLE,
    ],
)
def test_source_ranges_partition_the_original_text(text: str) -> None:
    pages = paginate(text, budget=_budget(1_000))
    assert pages[0].source_start == 0
    assert pages[-1].source_end == len(text)
    assert all(left.source_end == right.source_start for left, right in pairwise(pages))
    assert "".join(text[p.source_start:p.source_end] for p in pages) == text
```

For oversize fences and tables, retain the existing assertions that every `page.text` is independently renderable and that synthetic fence/header lines appear where needed.

- [ ] **Step 2: Run the pagination tests to verify RED**

Run: `uv run python -m pytest tests/integrations/test_feishu_card_document.py -q`

Expected: FAIL because `CardPage` has no source ranges and structured rewrapping currently loses ownership offsets.

- [ ] **Step 3: Carry source spans through hard and structured splits**

Add an internal rendered span rather than trying to infer ranges from `page.text`:

```python
@dataclass(frozen=True)
class _RenderedSpan:
    text: str
    source_start: int
    source_end: int


@dataclass(frozen=True)
class CardPage:
    """One rendered card and its owned half-open original-source range."""

    text: str
    index: int
    source_start: int
    source_end: int
```

Change `_hard_split` and `_split_structured` to accept the block's absolute start offset and return `_RenderedSpan` objects. For fences, the first span owns the source opening fence, the last owns the source closing fence, and intermediate pages add synthetic open/close wrappers without claiming them. For tables, the first span owns the source header/delimiter; later pages repeat those lines only in rendered text while owning contiguous data-row ranges. Plain hard splits own literal contiguous substrings.

- [ ] **Step 4: Build final pages from rendered spans and assert invariants**

Have `paginate()` accumulate source ownership while preserving current whitespace behavior. Before returning, validate first start `0`, last end `len(text)`, and adjacent equality; then enumerate:

```python
return [
    CardPage(
        text=span.text,
        index=index,
        source_start=span.source_start,
        source_end=span.source_end,
    )
    for index, span in enumerate(rendered, 1)
]
```

Do not derive either source boundary from `len(span.text)`.

- [ ] **Step 5: Verify pagination and streaming consumers**

Run: `uv run python -m pytest tests/integrations/test_feishu_card_document.py gateway/tests/feishu/test_card_stream.py -q`

Expected: PASS; existing streaming behavior ignores the new metadata and rendered cards remain inside byte/table limits.

- [ ] **Step 6: Commit**

```bash
git add integrations/feishu/card_document.py tests/integrations/test_feishu_card_document.py
git commit -m "feat: track Feishu card source ranges"
```

### Task 2: Define Safe Delivery and SDK Contracts

**Files:**
- Create: `integrations/feishu/delivery_types.py`
- Modify: `config/constants/feishu.py`
- Modify: `config/constants/__init__.py`
- Modify: `integrations/feishu/card_client.py`
- Modify: `integrations/feishu/delivery.py`
- Modify: `tests/integrations/test_feishu_constants.py`
- Modify: `tests/integrations/test_feishu_card_client.py`
- Modify: `tests/integrations/test_feishu_delivery.py`
- Modify: `gateway/tests/feishu/test_card_stream.py`

**Interfaces:**
- Consumes: `FeishuCardClient.create_card()`, `send_card()`, and raw `im.message.create` text transport.
- Produces: safe enums/results in `delivery_types.py`; `FeishuCardCallError(code: int, stage: FeishuCardCallStage)` with no vendor text; `post_feishu_message(...) -> FeishuMessageSendResult`.

- [ ] **Step 1: Write failing safe-result and fixed-error tests**

```python
def test_rejected_text_send_returns_only_fixed_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "s_secret"
    body = "# Secret report"
    target = "oc_full_target"
    _stub_lark(
        monkeypatch,
        create_impl=lambda _req: _response(
            ok=False,
            code=230020,
            msg=f"{secret} {body} {target} Authorization: Bearer token",
        ),
    )

    result = post_feishu_message("cli", secret, target, "chat_id", body)

    assert result.accepted is False
    assert result.error_category is FeishuDeliveryErrorCategory.RATE_LIMIT
    assert result.certainty is FeishuSendCertainty.DEFINITELY_NOT_SENT
    assert result.message_id == ""
    assert all(value not in repr(result) for value in (secret, body, target, "Bearer token"))


def test_send_success_without_message_id_is_uncertain(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_lark(monkeypatch, create_impl=lambda _req: _response(ok=True, message_id=""))
    result = post_feishu_message("cli", "secret", "oc", "chat_id", "body")
    assert result.error_category is FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN
    assert result.certainty is FeishuSendCertainty.MAYBE_SENT
```

Add card-client tests asserting an explicit rejection exposes `code` and `stage` but not SDK `msg`, and update stream tests so `FeishuStreamRejected` takes only a code.

- [ ] **Step 2: Run safe-boundary tests to verify RED**

Run: `uv run python -m pytest tests/integrations/test_feishu_constants.py tests/integrations/test_feishu_card_client.py tests/integrations/test_feishu_delivery.py gateway/tests/feishu/test_card_stream.py -q`

Expected: FAIL because current tuples and exceptions preserve raw vendor detail.

- [ ] **Step 3: Add fixed error-code allowlists and immutable result types**

In `config/constants/feishu.py`, add verified message/card codes only:

```python
FEISHU_RATE_LIMIT_ERROR_CODES = frozenset({230020, 99991400})
FEISHU_VALIDATION_ERROR_CODES = frozenset({11310, 200860, 230001, 230025})
FEISHU_AUTHORIZATION_ERROR_CODES = frozenset({230002, 230006, 230013, 230017, 230018, 230027})
```

In `delivery_types.py`, define:

```python
class FeishuDeliveryStatus(StrEnum):
    SUCCESS = "success"
    DEGRADED_SUCCESS = "degraded_success"
    FAILED = "failed"
    SKIPPED = "skipped"


class FeishuDeliveryMode(StrEnum):
    CARDS = "cards"
    TEXT_FALLBACK = "text_fallback"
    NONE = "none"


class FeishuDeliveryErrorCategory(StrEnum):
    CONFIGURATION = "configuration"
    AUTHORIZATION = "authorization"
    VALIDATION = "validation"
    RATE_LIMIT = "rate_limit"
    DEFINITE_REJECTION = "definite_rejection"
    DELIVERY_UNCERTAIN = "delivery_uncertain"
    TRANSPORT = "transport"
    INTERNAL = "internal"


class FeishuSendCertainty(StrEnum):
    CONFIRMED_SENT = "confirmed_sent"
    DEFINITELY_NOT_SENT = "definitely_not_sent"
    MAYBE_SENT = "maybe_sent"


@dataclass(frozen=True)
class FeishuMessageSendResult:
    accepted: bool
    message_id: str
    error_category: FeishuDeliveryErrorCategory | None
    certainty: FeishuSendCertainty
```

Add a pure `classify_feishu_rejection(code: int) -> FeishuDeliveryErrorCategory` using those sets, with unknown nonzero codes mapping to `DEFINITE_REJECTION`.

- [ ] **Step 4: Replace raw CardKit exceptions with typed safe exceptions**

```python
class FeishuCardCallStage(StrEnum):
    CREATE_CARD = "create_card"
    SEND_CARD = "send_card"
    UPDATE_ELEMENT = "update_element"
    CLOSE_STREAM = "close_stream"


class FeishuCardCallError(RuntimeError):
    def __init__(self, *, code: int, stage: FeishuCardCallStage) -> None:
        super().__init__(f"Feishu card call rejected during {stage.value}")
        self.code = code
        self.stage = stage
```

Pass the stage into `_check`, preserve the `FeishuStreamRejected` subclass for the existing streaming codes, and remove SDK `msg` plus per-call rejection logging. Missing card/message IDs raise the same safe typed exception with code `0` and their exact stage.

- [ ] **Step 5: Make the text primitive stage-aware and silent**

Split client construction from the visible message call. Construction failures return `TRANSPORT/DEFINITELY_NOT_SENT`; an exception from `message.create` returns `DELIVERY_UNCERTAIN/MAYBE_SENT`; explicit response failures classify only by numeric code; explicit success needs a nonempty ID. Do not log or call `redact_token()` here.

Keep `send_feishu_report()` temporarily as a compatibility wrapper until Tasks 5–8 migrate every caller; have it consume the safe result and return fixed text only.

- [ ] **Step 6: Verify safe boundaries and streaming compatibility**

Run: `uv run python -m pytest tests/integrations/test_feishu_constants.py tests/integrations/test_feishu_card_client.py tests/integrations/test_feishu_delivery.py gateway/tests/feishu/test_card_stream.py gateway/tests/feishu/test_turn_output.py -q`

Expected: PASS; no assertion relies on raw SDK text.

- [ ] **Step 7: Commit**

```bash
git add config/constants/feishu.py config/constants/__init__.py integrations/feishu/delivery_types.py integrations/feishu/card_client.py integrations/feishu/delivery.py tests/integrations/test_feishu_constants.py tests/integrations/test_feishu_card_client.py tests/integrations/test_feishu_delivery.py gateway/tests/feishu/test_card_stream.py
git commit -m "refactor: secure Feishu delivery boundaries"
```

### Task 3: Implement the Unified Document-Delivery State Machine

**Files:**
- Create: `integrations/feishu/document_delivery.py`
- Create: `tests/integrations/test_feishu_document_delivery.py`

**Interfaces:**
- Consumes: source-aware `paginate()`, `render_card_spec()`, safe `FeishuCardClient`, `post_feishu_message()`, and types from Task 2.
- Produces: `deliver_feishu_document(*, app_id: str, app_secret: str, receive_id: str, receive_id_type: str, markdown: str) -> FeishuDocumentDeliveryResult`.

- [ ] **Step 1: Write failing state/result contract tests**

```python
def test_first_card_success_then_fallback_failure_is_whole_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_pages(monkeypatch, _three_source_pages())
    _install_card_client(monkeypatch, sent_ids=["om_card_1"], fail_send_at=2)
    _install_text_sender(
        monkeypatch,
        results=[_sent("om_text_1"), _sent("om_text_2"), _uncertain()],
    )

    result = deliver_feishu_document(**_input("x" * 12_500))

    assert result.status is FeishuDeliveryStatus.FAILED
    assert result.attempted is True
    assert result.confirmed_message_ids == ("om_card_1", "om_text_1", "om_text_2")
    assert result.first_message_id == "om_card_1"
    assert result.error_category is FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN


def test_blank_body_is_skipped_without_constructing_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _spy_factories(monkeypatch)
    result = deliver_feishu_document(**_input(" \n "))
    assert result.status is FeishuDeliveryStatus.SKIPPED
    assert result.attempted is False
    assert result.delivery_mode is FeishuDeliveryMode.NONE
    assert built == []
```

Also add `SUCCESS`, `DEGRADED_SUCCESS`, pagination failure, create-card failure, empty IDs, uncertain card send, and full plaintext failure cases.

- [ ] **Step 2: Add failing original-source fallback/chunk tests**

Use oversize fence/table input containing CJK and emoji. Fail the second rendered card and capture plaintext calls:

```python
assert "".join(captured_text_chunks) == markdown[pages[1].source_start:]
assert all(len(chunk) <= MAX_MESSAGE_SIZE for chunk in captured_text_chunks)
assert captured_text_chunks[0] == markdown[pages[1].source_start:][:MAX_MESSAGE_SIZE]
assert rendered_wrapper_header not in captured_text_chunks[1]
```

Add exact 4096/4097-code-point cases, one 9000-character line, leading/trailing whitespace, and a non-BMP emoji at index 4095. Assert concatenation exactly equals the original remainder.

- [ ] **Step 3: Add failing malicious-exception/log-cardinality tests**

Raise an exception whose text contains secret, Markdown, target, card ID, message ID, and Authorization header. With `caplog`, assert none occurs in `repr(result)` or log text. Assert one summary record, one degradation record, at most one terminal-failure record, 1-based page/chunk fields, and a confirmed count that ignores successful `create_card` calls.

- [ ] **Step 4: Run the new suite to verify RED**

Run: `uv run python -m pytest tests/integrations/test_feishu_document_delivery.py -q`

Expected: collection FAIL because the owner module/result do not exist.

- [ ] **Step 5: Implement immutable result and plaintext chunks**

```python
@dataclass(frozen=True)
class FeishuDocumentDeliveryResult:
    status: FeishuDeliveryStatus
    attempted: bool
    confirmed_message_ids: tuple[str, ...]
    delivery_mode: FeishuDeliveryMode
    error_category: FeishuDeliveryErrorCategory | None
    error: str

    @property
    def first_message_id(self) -> str:
        return self.confirmed_message_ids[0] if self.confirmed_message_ids else ""

    @property
    def successful(self) -> bool:
        return self.status in {
            FeishuDeliveryStatus.SUCCESS,
            FeishuDeliveryStatus.DEGRADED_SUCCESS,
        }


@dataclass(frozen=True)
class _TextChunk:
    text: str
    source_start: int
    source_end: int
    index: int
```

Map categories to fixed safe strings in one private dictionary. `_text_chunks(markdown, source_start)` slices by Python indexes without normalization.

- [ ] **Step 6: Implement card-first delivery and source-cursor fallback**

Preflight before all SDK construction. For each page, create/render/send; append a message ID and assign `source_cursor = page.source_end` only after confirmed send. On any card failure, classify by typed exception/stage, keep `source_cursor` unchanged, and send `_text_chunks(markdown, source_cursor)`. A terminal fallback error determines the `FAILED` category; a complete fallback returns `DEGRADED_SUCCESS` and may retain the original safe card category for summary logging.

Catch unknown exceptions by stage: before a visible send use `INTERNAL` or `TRANSPORT`; while `send_card` is active use `DELIVERY_UNCERTAIN`. Never interpolate the exception into any output.

- [ ] **Step 7: Implement one safe summary plus bounded warnings**

Use constant log messages with structured `extra` fields from the spec. Do not log IDs or target values. Emit no per-page success log. The final summary includes status/mode/type/totals/confirmed count/category; degradation and final failure warnings contain the same allowlisted vocabulary only.

- [ ] **Step 8: Verify the state machine and lower layers**

Run: `uv run python -m pytest tests/integrations/test_feishu_document_delivery.py tests/integrations/test_feishu_card_document.py tests/integrations/test_feishu_card_client.py tests/integrations/test_feishu_delivery.py -q`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add integrations/feishu/document_delivery.py tests/integrations/test_feishu_document_delivery.py
git commit -m "feat: deliver Feishu documents with safe fallback"
```

### Task 4: Add Canonical Markdown Investigation Reports

**Files:**
- Modify: `tools/investigation/reporting/formatters/base.py`
- Modify: `tools/investigation/reporting/formatters/evidence.py`
- Modify: `tools/investigation/reporting/formatters/infrastructure.py`
- Modify: `tools/investigation/reporting/formatters/report.py`
- Modify: `tools/investigation/reporting/formatters/messages.py`
- Modify: `tools/investigation/reporting/node.py`
- Modify: `tools/investigation/reporting/delivery/dispatch.py`
- Modify: `core/state/models.py`
- Modify: `core/state/runtime_slices.py`
- Modify: `tests/delivery/test_report_provenance.py`
- Modify: `tests/delivery/test_node.py`
- Modify: `tests/delivery/test_masking_unmask.py`
- Modify: `tests/core/state/test_agent_state_sync.py`

**Interfaces:**
- Consumes: `ReportContext`, existing Slack/Telegram renderers, masking/unmasking, and flat investigation state.
- Produces: `format_markdown_message(ctx) -> str`; `ReportMessages.markdown_text`; state key `report_markdown`; adapter payload key `markdown_text`.

- [ ] **Step 1: Add and run characterization tests before refactoring**

Pin a representative Slack report containing headings, evidence links, code, incident command, provenance, and CloudWatch. Pin Telegram HTML tags/links for the same context.

Run: `uv run python -m pytest tests/delivery/test_report_provenance.py tests/delivery/test_correlation_report_section.py tests/delivery/test_evidence_slack_escaping.py -q`

Expected: PASS on pre-refactor code. Keep these assertions green throughout the task.

- [ ] **Step 2: Write failing canonical Markdown tests**

```python
def test_markdown_report_uses_gfm_and_not_vendor_markup() -> None:
    message = format_markdown_message(_rich_context())
    assert "## Findings" in message
    assert "**Provenance:**" in message
    assert "[E1](https://example.test/evidence/1)" in message
    assert "```" in message or "`top error`" in message
    assert "<https://" not in message
    assert "<b>" not in message


def test_untrusted_markup_cannot_inject_link_or_slack_mention() -> None:
    ctx = _rich_context(root_cause="[fake](https://attacker) <!channel> <b>boom</b>")
    message = format_markdown_message(ctx)
    assert "[fake](https://attacker)" not in message
    assert "<!channel>" not in message
    assert "[E1](https://example.test/evidence/1)" in message
```

Add node tests asserting masked identifiers are unmasked in `markdown_text`, `_messages_payload()` carries it, and `generate_report()` returns `report_markdown`.

- [ ] **Step 3: Run canonical/state tests to verify RED**

Run: `uv run python -m pytest tests/delivery/test_report_provenance.py tests/delivery/test_node.py tests/delivery/test_masking_unmask.py tests/core/state/test_agent_state_sync.py -q`

Expected: FAIL because the Markdown variant and state field are absent.

- [ ] **Step 4: Generalize text helpers without reverse-converting Slack output**

Add `format_markdown_link(label, url)` plus Markdown-safe escaping in `base.py`. Parameterize evidence, trace, and CloudWatch helpers with link/sanitize functions so the same semantic data can render directly into canonical Markdown or Slack mrkdwn. Do not parse `slack_text` to obtain Markdown.

Implement `format_markdown_message(ctx)` as the canonical section assembly. Keep `format_slack_message(ctx)` producing the characterized Slack output, either through the same semantic section builder with Slack syntax functions or by a direct Markdown-to-Slack projection proven by the characterization tests. Keep the specialized Telegram HTML renderer and Slack Block Kit builder unchanged.

- [ ] **Step 5: Thread Markdown through messages, masking, dispatch, and state**

```python
@dataclass(frozen=True)
class ReportMessages:
    markdown_text: str
    slack_text: str
    telegram_html: str
    slack_blocks: list[dict]
```

Build, unmask, and dispatch all four fields. Add `"markdown_text"` to `_messages_payload()`. Return `"report_markdown": messages.markdown_text` from `generate_report()`, and add `report_markdown: str = ""` to `AgentStateModel` plus `report_markdown: str` to `DeliveryOutputSlice`.

- [ ] **Step 6: Verify report formats and state sync**

Run: `uv run python -m pytest tests/delivery/ tests/core/state/test_agent_state_sync.py tests/core/state/test_investigation_initial_state.py -q`

Expected: PASS; Slack and Telegram characterization remains unchanged while Markdown/state assertions pass.

- [ ] **Step 7: Commit**

```bash
git add tools/investigation/reporting/formatters/base.py tools/investigation/reporting/formatters/evidence.py tools/investigation/reporting/formatters/infrastructure.py tools/investigation/reporting/formatters/report.py tools/investigation/reporting/formatters/messages.py tools/investigation/reporting/node.py tools/investigation/reporting/delivery/dispatch.py core/state/models.py core/state/runtime_slices.py tests/delivery/ tests/core/state/test_agent_state_sync.py
git commit -m "feat: add canonical Markdown investigation reports"
```

### Task 5: Migrate Investigation and Background-RCA Adapters

**Files:**
- Modify: `infrastructure/delivery/notifications/rca_summary.py`
- Create: `tests/infrastructure/delivery/notifications/test_rca_summary.py`
- Modify: `integrations/feishu/reporting_adapter.py`
- Modify: `integrations/feishu/background_adapter.py`
- Modify: `integrations/feishu/delivery.py`
- Modify: `tests/integrations/test_feishu_reporting_adapter.py`
- Modify: `tests/integrations/test_feishu_background_adapter.py`
- Modify: `tests/integrations/test_feishu_delivery.py`
- Modify: `tests/bootstrap/test_notification_adapters.py`

**Interfaces:**
- Consumes: `messages["markdown_text"]`, `deliver_feishu_document()`, alert/chat credential loaders, and bounded `summary_sections()`.
- Produces: `format_background_rca_markdown(record: BackgroundInvestigationRecord) -> str`; report-adapter attempted semantics; background adapter `sent` or fixed safe failure/setup text.

- [ ] **Step 1: Write failing Markdown-summary tests**

```python
def test_background_summary_is_bounded_markdown_with_all_sections() -> None:
    body = format_background_rca_markdown(_record())
    assert body.startswith("# OpenSRE background investigation completed")
    assert "## Root cause" in body
    assert "## Top analysis" in body
    assert "## What to do next" in body
    assert "## Internal stats" in body
    assert "<b>" not in body
    assert len(body) <= MAX_MESSAGE_SIZE
```

Include backticks in the command and assert they cannot terminate any generated inline-code span. Keep `summary_sections()` bounds unchanged.

- [ ] **Step 2: Write failing adapter status/mapping tests**

For the report adapter, patch `deliver_feishu_document` to return each status. Assert missing credentials/`SKIPPED` returns `False`, while `SUCCESS`, `DEGRADED_SUCCESS`, and `FAILED` return `True` because the registry boolean means attempted. Capture the passed body and assert it is `markdown_text`, never `slack_text`.

For background RCA, assert success/degraded return `sent`; failed returns exactly `failed: Feishu delivery failed`; missing setup remains a fixed setup hint. Inject a malicious result/error string and prove it is never included.

- [ ] **Step 3: Run adapter tests to verify RED**

Run: `uv run python -m pytest tests/infrastructure/delivery/notifications/test_rca_summary.py tests/integrations/test_feishu_reporting_adapter.py tests/integrations/test_feishu_background_adapter.py -q`

Expected: FAIL because the Markdown summary and document-owner calls do not exist.

- [ ] **Step 4: Add the bounded background Markdown builder**

Build from `summary_sections(record)` inside `rca_summary.py`, not from SMTP output:

```python
def format_background_rca_markdown(record: BackgroundInvestigationRecord) -> str:
    """Render the bounded background-RCA summary as canonical Markdown."""
    command, root_cause, top_analysis, next_steps = summary_sections(record)
    lines = [
        "# OpenSRE background investigation completed",
        "",
        f"**Task ID:** `{_markdown_code(record.task_id)}`",
        f"**Command:** `{_markdown_code(command)}`",
        "",
        "## Root cause",
        root_cause or "Unavailable",
        "",
        "## Top analysis",
        *(_markdown_items(top_analysis)),
        "",
        "## What to do next",
        *(_markdown_items(next_steps)),
        "",
        "## Internal stats",
        f"- tool calls: {int(record.stats.get('tool_call_count', 0) or 0)}",
        f"- investigation loops: {int(record.stats.get('investigation_loop_count', 0) or 0)}",
        f"- validity score: {float(record.stats.get('validity_score', 0.0) or 0.0):.2f}",
    ]
    return "\n".join(lines)
```

Use a named helper that replaces literal backticks with `ˋ`; empty item groups render `- Unavailable`.

- [ ] **Step 5: Make both adapters thin document-delivery clients**

Keep SDK imports function-local. Pass explicit credential fields plus canonical Markdown to `deliver_feishu_document`. Report adapter returns `result.attempted`. Background adapter maps only overall status, never `first_message_id` or raw error. Remove target/error values from adapter warnings because the owner already writes the safe summary.

- [ ] **Step 6: Remove the compatibility report sender**

After `rg -n "send_feishu_report" .` shows only its definition/tests, delete `send_feishu_report()` and its truncation imports from `integrations/feishu/delivery.py`; replace the old report tests with raw safe-primitive tests. Do not leave a forwarding shim.

- [ ] **Step 7: Verify adapters and boot registration**

Run: `uv run python -m pytest tests/infrastructure/delivery/notifications/test_rca_summary.py tests/integrations/test_feishu_reporting_adapter.py tests/integrations/test_feishu_background_adapter.py tests/integrations/test_feishu_delivery.py tests/bootstrap/test_notification_adapters.py tests/integrations/test_feishu_boot_path.py -q`

Expected: PASS; importing the facade/background adapter does not load `lark_oapi`.

- [ ] **Step 8: Commit**

```bash
git add infrastructure/delivery/notifications/rca_summary.py tests/infrastructure/delivery/notifications/test_rca_summary.py integrations/feishu/reporting_adapter.py integrations/feishu/background_adapter.py integrations/feishu/delivery.py tests/integrations/test_feishu_reporting_adapter.py tests/integrations/test_feishu_background_adapter.py tests/integrations/test_feishu_delivery.py tests/bootstrap/test_notification_adapters.py
git commit -m "feat: send Feishu RCA reports as documents"
```

### Task 6: Migrate Watchdog Alarms Without Changing Cooldown Semantics

**Files:**
- Modify: `integrations/feishu/alarms.py`
- Modify: `tools/system/watch_dog/runner.py`
- Modify: `tests/integrations/test_feishu_alarms.py`
- Modify: `tests/watch_dog/test_runner.py`

**Interfaces:**
- Consumes: `FeishuAlarmCredentials`, `CooldownGate`, canonical watchdog Markdown, and `deliver_feishu_document()`.
- Produces: `FeishuAlarmDispatcher.dispatch(...) -> bool`, where only `SUCCESS`/`DEGRADED_SUCCESS` return true and every attempted delivery keeps cooldown.

- [ ] **Step 1: Replace old transport fakes with failing status/cooldown tests**

```python
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (FeishuDeliveryStatus.SUCCESS, True),
        (FeishuDeliveryStatus.DEGRADED_SUCCESS, True),
        (FeishuDeliveryStatus.FAILED, False),
    ],
)
def test_dispatch_maps_whole_status_and_keeps_attempt_cooldown(
    monkeypatch: pytest.MonkeyPatch,
    status: FeishuDeliveryStatus,
    expected: bool,
) -> None:
    calls = _install_fake_document_delivery(monkeypatch, _result(status))
    _patch_clock(monkeypatch, [100.0, 105.0])
    dispatcher = FeishuAlarmDispatcher(_CREDS, cooldown_seconds=300.0)
    assert dispatcher.dispatch("max_cpu", "**alarm**") is expected
    assert dispatcher.dispatch("max_cpu", "**again**") is False
    assert len(calls) == 1
```

Add missing-credential and blank-message tests proving `SKIPPED` occurs before reservation: repair credentials/body and make a second call at the same clock value; it must attempt rather than be suppressed.

- [ ] **Step 2: Change the watchdog-format test to require Markdown for Feishu**

Rename `test_alarm_message_uses_plain_text_for_feishu` and assert Feishu receives the same `**bold**`/backtick-safe field structure as Rocket.Chat, with no Telegram HTML. Keep the literal-backtick neutralization assertion.

- [ ] **Step 3: Run watchdog tests to verify RED**

Run: `uv run python -m pytest tests/integrations/test_feishu_alarms.py tests/watch_dog/test_runner.py -q`

Expected: FAIL because Feishu still truncates/plaintexts and calls the raw sender.

- [ ] **Step 4: Perform preflight, reserve, then call the document owner**

In `dispatch()`, build the Markdown first and reject empty credentials/target/body before `try_reserve()`. After reservation, lazily import and call `deliver_feishu_document`. Return `result.successful`; do not release reservation for `FAILED`. Defense-in-depth `except Exception` returns false with a constant warning and no exception interpolation or `exc_info=True`.

Delete `MAX_MESSAGE_SIZE`, `truncate`, and raw delivery imports. Do not duplicate pagination or fallback loops.

- [ ] **Step 5: Route Feishu to the existing Markdown formatter**

```python
if provider in {Provider.ROCKETCHAT, Provider.FEISHU}:
    return _format_alarm_message_markdown(sample, breach)
return _format_alarm_message_html(sample, breach)
```

Run `rg -n "_format_alarm_message_plain" tools tests`; when the only product match is its definition, remove the function and its direct tests. If another product caller is listed, migrate that caller to `_format_alarm_message_markdown` in this task before removing the function.

- [ ] **Step 6: Verify watchdog behavior**

Run: `uv run python -m pytest tests/integrations/test_feishu_alarms.py tests/watch_dog/ -q`

Expected: PASS, including failed-attempt cooldown retention and preflight skip behavior.

- [ ] **Step 7: Commit**

```bash
git add integrations/feishu/alarms.py tools/system/watch_dog/runner.py tests/integrations/test_feishu_alarms.py tests/watch_dog/test_runner.py
git commit -m "feat: deliver Feishu watchdog alarms as cards"
```

### Task 7: Make Scheduled Producers and Non-Feishu Adapters Markdown-Native

**Files:**
- Modify: `infrastructure/scheduling/scheduler/tasks.py`
- Modify: `integrations/slack/scheduled_delivery.py`
- Modify: `integrations/rocketchat/scheduled_delivery.py`
- Modify: `integrations/discord/scheduled_delivery.py`
- Modify: `tests/scheduler/test_tasks.py`
- Modify: `tests/scheduler/test_executor.py`
- Modify: `tests/integrations/test_slack_formatting.py`
- Create: `tests/integrations/test_slack_scheduled_delivery.py`
- Create: `tests/integrations/test_rocketchat_scheduled_delivery.py`
- Create: `tests/integrations/test_discord_scheduled_delivery.py`

**Interfaces:**
- Consumes: investigation result key `report_markdown` with legacy `report` fallback; canonical Markdown task bodies.
- Produces: Markdown from every fixed scheduler builder; Slack mrkdwn conversion at its adapter; unchanged Telegram HTML conversion; direct Markdown into Rocket.Chat and Discord adapters.

- [ ] **Step 1: Add characterization tests for current non-Feishu delivery contracts**

Before editing, add one test module per modified adapter. Pin Slack's webhook-vs-token branch and returned timestamp, Rocket.Chat's resolved room and returned message ID, and Discord's webhook plus embed-helper result so formatting changes cannot silently alter readiness, targets, or result tuples.

Run: `uv run python -m pytest tests/integrations/test_slack_scheduled_delivery.py tests/integrations/test_rocketchat_scheduled_delivery.py tests/integrations/test_discord_scheduled_delivery.py tests/scheduler/test_executor.py -q`

Expected: PASS on pre-change code.

- [ ] **Step 2: Write failing producer Markdown tests**

```python
def test_daily_summary_prefers_report_markdown() -> None:
    result = {"report_markdown": "# Canonical", "report": "*Slack legacy*"}
    msg = _build_with_investigation_result(TaskKind.DAILY_SUMMARY, result)
    assert msg == "# Canonical"


def test_fixed_fallbacks_are_markdown_not_html(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _quiet_daily_summary(monkeypatch)
    assert "**Daily Reliability Summary**" in msg
    assert "_Generated by OpenSRE scheduled delivery_" in msg
    assert "<b>" not in msg and "<i>" not in msg
```

Cover daily, weekly, replay, synthetic, and custom-investigation fallback bodies. Assert a legacy runner with only `report` still works.

- [ ] **Step 3: Write failing provider-rendering tests**

Assert Telegram calls `markdown_to_telegram_html`, Slack calls `markdown_to_slack_mrkdwn`, Rocket.Chat receives Markdown without `strip_html`, and Discord receives Markdown for its embed helper. Keep each provider's existing length/embedding policy; S6 does not expand other transports' long-message limits.

- [ ] **Step 4: Run scheduler/adapter tests to verify RED**

Run: `uv run python -m pytest tests/scheduler/test_tasks.py tests/scheduler/test_executor.py tests/integrations/test_slack_formatting.py -q`

Expected: FAIL on HTML fallback strings and missing `report_markdown` preference.

- [ ] **Step 5: Centralize investigation result selection and rewrite fixed copy**

```python
def _investigation_report(result: dict[str, object] | None) -> str:
    if not result:
        return ""
    markdown = str(result.get("report_markdown") or "")
    return markdown or str(result.get("report") or "")
```

Use it in every investigation-backed builder. Replace `<b>/<i>` fixed copy with `**...**`/`_..._`; do not modify agent-runner output.

- [ ] **Step 6: Move final formatting to provider adapters**

- Telegram: retain `markdown_to_telegram_html()` and its safe HTML truncation.
- Slack: replace `strip_html(message)` with `markdown_to_slack_mrkdwn(message)`.
- Rocket.Chat: pass Markdown directly to its existing 4096-character truncation.
- Discord: pass Markdown directly to `send_discord_report()`, which owns embed limits.
- Interactive shell: keep its existing local persistence unchanged; `tests/scheduler/test_executor.py` must assert the canonical Markdown body reaches its adapter unchanged.

- [ ] **Step 7: Verify scheduler and provider regressions**

Run: `uv run python -m pytest tests/scheduler/test_tasks.py tests/scheduler/test_executor.py tests/integrations/test_slack_formatting.py tests/integrations/telegram/test_formatting.py tests/integrations/test_slack_scheduled_delivery.py tests/integrations/test_rocketchat_scheduled_delivery.py tests/integrations/test_discord_scheduled_delivery.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add infrastructure/scheduling/scheduler/tasks.py integrations/slack/scheduled_delivery.py integrations/rocketchat/scheduled_delivery.py integrations/discord/scheduled_delivery.py tests/scheduler/test_tasks.py tests/scheduler/test_executor.py tests/integrations/test_slack_formatting.py tests/integrations/test_slack_scheduled_delivery.py tests/integrations/test_rocketchat_scheduled_delivery.py tests/integrations/test_discord_scheduled_delivery.py
git commit -m "refactor: make scheduled messages canonical Markdown"
```

### Task 8: Migrate Feishu Scheduled Delivery and Lock Target Pairing

**Files:**
- Modify: `infrastructure/scheduling/scheduler/credentials.py`
- Modify: `integrations/feishu/scheduled_delivery.py`
- Modify: `tests/scheduler/test_credentials.py`
- Modify: `tests/integrations/test_feishu_scheduled_delivery.py`
- Modify: `tests/scheduler/test_executor.py`

**Interfaces:**
- Consumes: task `chat_id`, task params, configured `FeishuChatCredentials`, canonical Markdown, and `FeishuDocumentDeliveryResult`.
- Produces: atomic target/type resolution and the existing scheduler tuple `(success: bool, error: str, message_id: str)` with whole-status semantics.

- [ ] **Step 1: Add the complete failing target/type matrix**

Parameterize these rows and capture the arguments sent to `deliver_feishu_document`:

```python
cases = [
    # task.chat_id wins over conflicting params and configured open_id fallback
    ("oc_task", {"receive_id": "ou_param", "receive_id_type": "open_id"},
     _chat_creds(receive_id="ou_config", receive_id_type="open_id"),
     ("oc_task", "chat_id")),
    # explicit task-param pair
    ("", {"receive_id": "ou_param", "receive_id_type": "open_id"},
     _chat_creds(), ("ou_param", "open_id")),
    # explicit target without type gets its own fixed default
    ("", {"receive_id": "oc_param"},
     _chat_creds(receive_id="ou_config", receive_id_type="open_id"),
     ("oc_param", "chat_id")),
    # no task pair uses the configured pair
    ("", {}, _chat_creds(receive_id="ou_config", receive_id_type="open_id"),
     ("ou_config", "open_id")),
]
```

Add separate skip tests for task type without task target, missing configured target, blank resolved configured type, and whitespace-only values. In every skip case assert no document-delivery call.

- [ ] **Step 2: Add failing whole-status scheduler mapping tests**

```python
@pytest.mark.parametrize("status", [FeishuDeliveryStatus.SUCCESS, FeishuDeliveryStatus.DEGRADED_SUCCESS])
def test_success_status_returns_first_confirmed_id(status: FeishuDeliveryStatus) -> None:
    _install_document_result(_result(status, ids=("om_first", "om_second")))
    assert FeishuScheduledDelivery().deliver(_task(chat_id="oc"), "# report") == (
        True, "", "om_first"
    )


def test_partial_ids_do_not_turn_failed_delivery_into_success() -> None:
    _install_document_result(_result(FeishuDeliveryStatus.FAILED, ids=("om_partial",)))
    assert FeishuScheduledDelivery().deliver(_task(chat_id="oc"), "# report") == (
        False, "Feishu delivery failed", ""
    )
```

Add `SKIPPED` and blank-body rows; assert Markdown is passed unchanged with no strip/truncate.

- [ ] **Step 3: Run credential/adapter tests to verify RED**

Run: `uv run python -m pytest tests/scheduler/test_credentials.py tests/integrations/test_feishu_scheduled_delivery.py tests/scheduler/test_executor.py -q`

Expected: FAIL because the current resolver cross-pairs sources and the adapter calls the raw sender.

- [ ] **Step 4: Resolve task-param destination pairs atomically**

In `resolve_feishu_credentials()`, continue resolving `app_id`/`app_secret` by their existing precedence. Resolve destination as one branch:

```python
param_id = task_params.get("receive_id", "").strip()
param_type = task_params.get("receive_id_type", "").strip()
if param_id:
    resolved["receive_id"] = param_id
    resolved["receive_id_type"] = param_type or "chat_id"
elif param_type:
    resolved["receive_id_type"] = param_type  # incomplete on purpose; adapter skips
else:
    if base.receive_id.strip():
        resolved["receive_id"] = base.receive_id.strip()
        resolved["receive_id_type"] = base.receive_id_type.strip()
```

Never use `base.receive_id` when a task supplied only `receive_id_type`.

- [ ] **Step 5: Replace raw send/truncation with document delivery**

Resolve `task.chat_id` first as `(value, "chat_id")`, ignoring all destination params/fallbacks. Otherwise consume only the complete pair returned by the resolver. Lazily import `deliver_feishu_document`, pass the untouched Markdown, and map status exactly as §7.2 of the spec. Do not infer success from `first_message_id`.

- [ ] **Step 6: Verify scheduled Feishu behavior and executor persistence**

Run: `uv run python -m pytest tests/scheduler/test_credentials.py tests/integrations/test_feishu_scheduled_delivery.py tests/scheduler/test_executor.py tests/scheduler/test_runner.py -q`

Expected: PASS; failed partial delivery records task failure and no posted message ID.

- [ ] **Step 7: Commit**

```bash
git add infrastructure/scheduling/scheduler/credentials.py integrations/feishu/scheduled_delivery.py tests/scheduler/test_credentials.py tests/integrations/test_feishu_scheduled_delivery.py tests/scheduler/test_executor.py
git commit -m "feat: schedule Feishu document deliveries safely"
```

### Task 9: Document, Verify, Live-Test, and Deliver S6

**Files:**
- Modify: `docs/messaging/feishu.mdx`
- Modify: `docs/cron.mdx`
- Modify: `docs/background-investigations.mdx`

**Interfaces:**
- Consumes: all prior tasks and the approved S6 spec.
- Produces: user-facing operating guidance, complete local/real acceptance evidence, a reviewed PR, merge, and post-merge validation.

- [ ] **Step 1: Update user-visible docs**

In `docs/messaging/feishu.mdx`, state that watchdog alarms, investigation reports, background RCA summaries, and scheduled deliveries use non-streaming Markdown cards, automatically paginate, and fall back to ordered plaintext chunks without truncating content. Mention possible current-page duplication only when a network result is uncertain.

In `docs/cron.mdx`, include Feishu in the description/provider guidance and state that the task's `--chat-id` is a `chat_id`; configured non-chat destinations must carry their own `receive_id_type` pair.

In `docs/background-investigations.mdx`, replace “Feishu plain text capped at 4,096” with card pagination plus complete plaintext fallback. Keep the alert-push environment names. No `docs/docs.json` change is needed because all three pages already exist in navigation.

- [ ] **Step 2: Run focused functional suites**

Run:

```bash
uv run python -m pytest tests/integrations/test_feishu_card_document.py tests/integrations/test_feishu_card_client.py tests/integrations/test_feishu_delivery.py tests/integrations/test_feishu_document_delivery.py tests/integrations/test_feishu_alarms.py tests/integrations/test_feishu_reporting_adapter.py tests/integrations/test_feishu_background_adapter.py tests/integrations/test_feishu_scheduled_delivery.py -q
uv run python -m pytest tests/delivery/ tests/core/state/test_agent_state_sync.py tests/scheduler/test_credentials.py tests/scheduler/test_tasks.py tests/scheduler/test_executor.py tests/watch_dog/ tests/bootstrap/test_notification_adapters.py -q
uv run python -m pytest tests/shared/test_integrations_api_border.py tests/integrations/test_feishu_boot_path.py gateway/tests/feishu/test_card_stream.py gateway/tests/feishu/test_turn_output.py -q
```

Expected: PASS. If a suite fails, fix the root cause and rerun its focused command before continuing.

- [ ] **Step 3: Run mandatory code-quality gates**

```bash
git status --short
make lint
make format-check
make typecheck
```

Expected: clean except intended changes; all gates PASS. If formatting fails, run `make format`, inspect the diff, then rerun `make format-check` and affected tests.

- [ ] **Step 4: Perform a fresh whole-branch review before live sends**

Use the repository's code-review workflow against the approved spec. Check especially cursor ownership, result/status mapping, raw-error exposure, target/type pairing, boot imports, and cooldown behavior. Resolve every actionable finding with a focused regression test and rerun Steps 2–3.

- [ ] **Step 5: Obtain explicit live-send authorization with an exact message count**

Before any external send, locally compute the scheduled body's `len(paginate(body))`. Tell the user the destinations (chat app configured target and alert-push target) and exact maximum: one watchdog message, one report message, and that many scheduled cards. Wait for an explicit approval in that execution turn.

- [ ] **Step 6: Run controlled watchdog, report, and scheduled adapter acceptance**

After approval, use environment/store credential loaders—never print secrets or targets—and execute three bounded one-off Python harnesses:

1. `FeishuAlarmDispatcher(load_credentials_from_env(), cooldown_seconds=300).dispatch("s6_live_watchdog", "**🚨 S6 watchdog acceptance**\n\n**status** `card delivery`")`.
2. `feishu_delivery_adapter.deliver({}, messages={"markdown_text": "# S6 investigation report acceptance\n\n## Findings\n- [OpenSRE](https://github.com/Tracer-Cloud/opensre)\n\n| check | result |\n| --- | --- |\n| card | pass |\n\n```text\nfenced code survives\n```", "slack_text": "unused", "telegram_html": "unused", "slack_blocks": []}, blocks=[])`.
3. `FeishuScheduledDelivery().deliver(ScheduledTask(id="s6-live-scheduled", kind=TaskKind.DAILY_SUMMARY, cron="0 0 * * *", provider=Provider.FEISHU, chat_id="", params={}), body)` where `body` is a deterministic Markdown document larger than one 64 KiB card and ends with `S6-LIVE-END`.

Print only booleans/status names, never IDs or errors beyond fixed safe text. Confirm visually that the watchdog/report cards render Markdown and the scheduled cards are ordered with `S6-LIVE-END` present. Background RCA needs no separate incident because it shares the watchdog alert-push credentials and the same document owner; its dedicated builder/adapter was deterministically tested in Task 5.

- [ ] **Step 7: Inspect live logs and stop on any real failure**

Confirm the delivery summary has only allowlisted fields, 1-based indexes, and no secret/body/full target/card/message ID. If live configuration, authorization, or membership fails, report the real failure and repair it; do not substitute a mock result. Do not deliberately break the network to test fallback.

- [ ] **Step 8: Commit docs and acceptance evidence**

Record only non-sensitive acceptance facts in the spec/PR body, not credentials, targets, IDs, or message bodies.

```bash
git add docs/messaging/feishu.mdx docs/cron.mdx docs/background-investigations.mdx
git commit -m "docs: explain Feishu document delivery"
```

- [ ] **Step 9: Open the PR with the complete template**

Push `codex/feishu-s6-delivery-cards`, open a PR against `main`, fill every template section including AI usage disclosure, list the exact focused/quality/live checks, and attach the PR to the Codex task.

- [ ] **Step 10: Close the PR review and CI loop**

After every push:

```bash
gh pr checks --watch
gh pr view --json statusCheckRollup,url
```

On failure, resolve and inspect the latest failing run without a handwritten ID:

```bash
failed_run_id="$(gh run list --branch codex/feishu-s6-delivery-cards --status failure --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run view "$failed_run_id" --log-failed
```

Fix the root cause, rerun the focused local command, push, and watch again. Inspect unresolved human/automated conversations after every update. Trigger Greptile only when no review is already running; repeat until 5/5 with zero unresolved comments.

- [ ] **Step 11: Merge and monitor post-merge workflows**

After all merge requirements pass, merge using the repository's accepted strategy. Monitor the merge commit's main CI, synthetic, interactive-shell, CodeQL, and release workflows. Treat any applicable failure as unfinished delivery; fix or revert before reporting S6 complete. Record conditionally skipped workflows accurately.
