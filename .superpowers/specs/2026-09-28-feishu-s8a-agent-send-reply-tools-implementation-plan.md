# Feishu S8a Agent Send/Reply Tools — Implementation Plan

- Date: 2026-09-28
- Status: Implemented and merged as PR #38 (2026-09-30); live short send, normal/threaded reply and denial verified. Live multi-page pagination was not exercised; offline pagination passed and the user approved merge with that limitation.
- Approved design: [`2026-09-28-feishu-s8a-agent-send-reply-tools-design.md`](2026-09-28-feishu-s8a-agent-send-reply-tools-design.md)
- Scope: S8a only; no S7 prompt/persona work or S8b read/search/member/reaction tools
- Checkout: `C:\Users\23033\Desktop\opensre2\.worktrees\feishu-s5b-feedback`, branch `codex/feishu-s8a-spec`

## 1. Gates and working rules

The design and this plan have received separate user approvals. This approval permits scoped S8a
implementation, local checks, commit, push, and PR work; it does not permit live Feishu calls.
Obtain a fresh, bounded live-operation authorization before acceptance.

At implementation start, recheck branch, `git status --short`, `git diff --stat`, the untracked spec
and this plan; preserve all pre-existing worktree changes. Use `uv run python`/`uv run opensre` for
Python and CLI commands. Write one high-signal failing test for one failure category, run it RED, make
it GREEN, and only then add the next category. Never run a real Feishu message API from tests.

The invariant to guard throughout is: **the exact canonical target visible to the approver is the
target used after approval**, subject to fresh authorization. For a reply, a metadata-only lookup must
also prove that its parent belongs to that chat. An ambiguous write stops; it does not fall back or
auto-retry.

## 2. Exact production and documentation file map

| File | Responsibility / intended edit |
| --- | --- |
| `config/constants/feishu.py` | Define `FEISHU_ALLOWED_OUTBOUND_TARGETS_ENV` only once. |
| `config/constants/__init__.py` | Re-export the env-name constant through the existing constants facade. |
| `integrations/config_models.py` | Add typed `FeishuConfig.allowed_outbound_targets`; keep app secret repr-hidden. |
| `integrations/_catalog_impl.py` | Carry env fallback into the Feishu catalog record. |
| `integrations/feishu/classify.py` | Preserve the setting when normalizing store/environment records. |
| `integrations/feishu/setup.py` | Collect the optional setting and give immediate single-field feedback. |
| `integrations/feishu/verifier.py` | Authoritatively reject malformed outbound targets and invalid cross-field combinations before credential probing. |
| `integrations/feishu/credentials.py` | Resolve outbound setting store-first, env-fallback; leave SDK/catalog off the import path and secrets out of repr. |
| `integrations/feishu/outbound_targets.py` (new) | Parse/deduplicate exact capabilities; resolve `current`/`default`/explicit; authorize sends and chat-ID-only replies; reject changed authority. |
| `integrations/feishu/message_lookup.py` (new) | Call pinned SDK `GetMessageRequest`, normalize only ID/chat/deleted metadata, require exactly one exact live match, discard body. |
| `integrations/feishu/delivery_types.py` | Add `FeishuDeliveryStatus.PARTIAL` and any narrow certainty carrier needed without changing complete-success semantics. |
| `integrations/feishu/delivery.py` | Share text create/reply transport, correct definite/ambiguous classification, and attach per-logical-chunk UUIDs. |
| `integrations/feishu/card_client.py` | Carry create/reply UUID and thread flag in real SDK payloads; distinguish card creation failure from ambiguous message-write outcomes. |
| `integrations/feishu/document_delivery.py` | Add optional reply parent/thread flag and cancellation probe; preserve source cursor across cards/text, stop on ambiguity, expose partial results. |
| `integrations/feishu/tools/__init__.py` (new) | Lightweight vendor-tool package facade only. |
| `integrations/feishu/tools/results.py` (new) | Map delivery/validation/preflight outcomes to the safe common `sent`/`partial`/`failed` result contract. |
| `integrations/feishu/tools/feishu_send_message_tool/{__init__.py,tool.py,validation.py}` (new) | Registry manifest; ACTION-only approved send tool; pre-approval semantic validation. |
| `integrations/feishu/tools/feishu_reply_message_tool/{__init__.py,tool.py,validation.py}` (new) | Registry manifest; ACTION-only approved reply tool; chat-only target and message-ID validation. |
| `core/tool/contracts.py` | Enforce string `minLength`; add vendor-neutral input-preparation and log-omitted-field tool metadata, propagated from `BaseTool` to `RegisteredTool`. |
| `core/tool/execution.py` | Run preparation between schema validation and approval; pass one immutable/copy-on-write canonical argument mapping to approval and invocation. |
| `core/agent_harness/tools/tool_provider.py` | Omit each tool's declared `message` input from ordinary `tool_start` log preview, without suppressing the separate bounded approval preview. |
| `tools/registry_discovery.py` | Add `integrations.feishu.tools` to integration discovery only after security/delivery layers pass. |
| `docs/messaging/feishu.mdx` | Operator-facing target setup, approval, reply read-permission, pagination and uncertain/partial retry guidance. |
| `.env.example` | Add commented `FEISHU_ALLOWED_OUTBOUND_TARGETS` syntax with fake IDs only. |

No change is planned to `integrations/feishu/__init__.py`, gateway reply behavior, global prompts,
`docs/docs.json`, dependency manifests, or import-border allowlists. If a test proves a further file
is genuinely required, amend this plan before editing it; do not add compatibility shims or a new
Feishu-vendor branch in shared orchestration.

## 3. Exact test and contract file map

| File | Contract pinned |
| --- | --- |
| `tests/integrations/test_feishu_outbound_targets.py` (new) | Exact parsing, malformed entries, default/current authority, cross-chat and cross-platform rejection, changed-target reauthorization. |
| `tests/integrations/test_feishu_catalog.py`, `test_feishu_classify.py`, `test_feishu_setup.py`, `test_feishu_verify.py`, `test_feishu_credentials.py`, `test_feishu_constants.py` | Env/store/setup/verifier parity, exported constant and secret-safe resolution. |
| `tests/core/tool/test_schema.py`, `test_registered_tool.py`, `test_execution.py` | `minLength`, metadata propagation, preparation-before-approval, same approved/executed args, denial/expiry and no side effect. |
| `tests/integrations/test_feishu_message_lookup.py` (new) | SDK GET payload, single exact non-deleted item, mismatched/multiple/missing response, zero body exposure. |
| `tests/integrations/test_feishu_delivery.py`, `test_feishu_card_client.py`, `test_feishu_document_delivery.py` | Real SDK request-body shape, UUID/thread flags, cursor-preserving card/text path, ambiguous stop, cancellation and `PARTIAL`. |
| `tests/integrations/test_feishu_send_reply_tools.py` (new) | Public schemas, ACTION-only discovery/availability, approval/reauthorization, reply isolation, result matrix. |
| `tests/core/agent_harness/test_tool_provider.py` (new) | Log omission for declared fields; no message-body leak. |
| `gateway/tests/runtime/test_approval_arguments_preview.py`, `gateway/tests/feishu/test_approvals.py` | Canonical target visible despite long message; original requester/chat authority and approval lifecycle remain intact. |
| `tests/tools/test_registry.py`, `tests/tools/test_registry_index.py`, `tests/packaging/test_release_manifest.py` | Registry discoverability, descriptor/hidden-import inclusion, no S7 prompt module. |
| `tests/integrations/test_feishu_boot_path.py`, `tests/shared/test_integrations_api_border.py`, `tests/shared/test_tool_api_border.py` | SDK-free facade, legal import edges and public tool APIs. |

Existing S5a/S5b/S5c/S6, Slack/Telegram/Rocket.Chat/Buzz and gateway regression files run in the
final scoped suite rather than receiving redundant assertions. No test fixture contains real tokens,
real chat IDs or live network requests.

## 4. TDD sequence and RED/GREEN checkpoints

Each command below is run **twice** for its named slice: first after adding only its focused test
(expect RED for the intended missing contract), then after the smallest implementation (expect
GREEN). After GREEN, expand the same file by one distinct failure class and repeat. Do not respond to
a broad failure by changing several subsystems at once. All `pytest` commands are local mocks/fakes.

| Step | First isolated failure class and smallest fix | RED then GREEN command |
| --- | --- | --- |
| 1 | Malformed outbound entry must be rejected, not silently dropped; then add exact parsing/dedup, default/current and cross-chat tests. | `uv run python -m pytest -q tests/integrations/test_feishu_outbound_targets.py -k malformed_entry` |
| 2 | Env/store/classify field must survive to runtime; then setup feedback and verifier cross-field rejection without any remote probe on invalid config. | `uv run python -m pytest -q tests/integrations/test_feishu_catalog.py -k outbound_targets` |
| 3 | Runtime schema must reject empty string before approval; implement generic `minLength` in shared validator, then check wrong type/whitespace seam separately. | `uv run python -m pytest -q tests/core/tool/test_schema.py -k min_length` |
| 4 | Approval sees canonical target and invocation uses the same target; implement vendor-neutral preparation seam and test denial/expired preparation, target-first preview and config-change fail-closed. | `uv run python -m pytest -q tests/core/tool/test_execution.py -k prepared_arguments` |
| 5 | GET response with a wrong `chat_id` must prevent reply; implement metadata-only lookup, then test missing/multiple/deleted/exact-ID mismatches and no body in outputs/logs. | `uv run python -m pytest -q tests/integrations/test_feishu_message_lookup.py -k wrong_chat` |
| 6 | Reply text request must use `ReplyMessageRequest` and UUID, never create; implement shared transport and test SDK body, definite rejection vs timeout/ambiguous response. | `uv run python -m pytest -q tests/integrations/test_feishu_delivery.py -k reply_payload` |
| 7 | Card reply request must carry parent, thread flag and UUID; preserve existing send/stream APIs and classify success-without-ID as ambiguous. | `uv run python -m pytest -q tests/integrations/test_feishu_card_client.py -k send_card_replies` |
| 8 | A later ambiguous page must stop without fallback and yield `PARTIAL`/`maybe_sent`; implement cursor-aware send/reply orchestration, then test definite-unsent fallback, no truncation, cancellation boundaries and successful semantics. | `uv run python -m pytest -q tests/integrations/test_feishu_document_delivery.py -k ambiguous_partial` |
| 9 | Unauthorized explicit target must be refused before approval; implement tools using runtime context, post-approval reload/reauthorization and reply preflight, then test local/current/other-gateway availability and result matrix. | `uv run python -m pytest -q tests/integrations/test_feishu_send_reply_tools.py -k unauthorized_target` |
| 10 | Ordinary action log must omit outbound body while preserving name/target visibility; add declared-field omission and a captured-log test. | `uv run python -m pytest -q tests/core/agent_harness/test_tool_provider.py -k omitted_message` |
| 11 | Tool discovery/packaging must include both tools only after the preceding gates; add registry entry, then run registry/index/manifest and gateway approval tests. | `uv run python -m pytest -q tests/tools/test_registry.py -k feishu` |

For steps 2, 4, 8 and 11, once the named slice is GREEN, run the whole directly affected file(s)
before proceeding. Where existing tests use a different test name, create the indicated narrowly named
test; do not weaken assertions to obtain GREEN. Keep a short RED/GREEN record in the PR description.

### Mandatory security and behavior assertions within those steps

- The schema has no secret/allowlist fields, requires target/body (and reply ID/thread flag), and
  rejects `""` before the approval hook; semantic preparation rejects whitespace-only values.
- `FEISHU_ALLOWED_OPEN_IDS` never authorizes outbound sends; `current` works only with Feishu
  `_gateway_platform` and current chat; `default` and explicit pairs compare case-sensitively.
- The approval card's first visible argument is the canonical target. Its prepared argument object is
  not overwritten by a later `default` resolution, and a changed/revoked config blocks dispatch.
- Every tool invocation requires a fresh approval; deny, expiry, failed card exposure or cancellation
  before write yields zero create/reply calls. Only the original Feishu requester/chat can approve.
- Reply metadata GET returns no body to the tool result, logs, trace or model; only one exact,
  undeleted message in the approved chat permits write. Deleted/revoked parent never turns into create.
- Card and text create/reply calls have SDK 1.7.3 payload-shape assertions. The same UUID is reused
  only for a retry of the same page/chunk; a new call gets new UUIDs. No exactly-once claim.
- Card pagination and text fallback cover Markdown/table/fenced-code source ranges contiguously,
  with no character truncation. Ambiguous write stops. Definite-unsent fallback resumes at the
  unconfirmed cursor, and reply fallback remains a reply.
- `PARTIAL.successful` is false; only `SUCCESS`/`DEGRADED_SUCCESS` are complete. Assert all five
  status/certainty/retry-safe rows from the design, including cancellation before/between/in-flight.
- Availability hides both tools in another gateway's turn. Neither tool emits an S7 prompt fragment
  or becomes an investigation/GATHER tool.

## 5. Verification after the small steps

Run the `CI.md` local baseline before any push: `git status --short`, `make lint`,
`make format-check`, `make typecheck`. `make format` may repair formatting, followed by
`make format-check`. Use the touched-path mapping in `.github/ci/test_scope_rules.py`: `core/`
is high-blast-radius, `integrations/` maps to integration tests, and `gateway/` maps to gateway
tests if any gateway file is ultimately touched. Run focused affected contract files rather than
`make test-cov` locally:

```bash
uv run python -m pytest -q tests/core/tool/ tests/core/agent_harness/test_tool_provider.py
uv run python -m pytest -q tests/integrations/test_feishu_outbound_targets.py tests/integrations/test_feishu_catalog.py tests/integrations/test_feishu_classify.py tests/integrations/test_feishu_setup.py tests/integrations/test_feishu_verify.py tests/integrations/test_feishu_credentials.py tests/integrations/test_feishu_constants.py tests/integrations/test_feishu_message_lookup.py tests/integrations/test_feishu_delivery.py tests/integrations/test_feishu_card_client.py tests/integrations/test_feishu_document_delivery.py tests/integrations/test_feishu_send_reply_tools.py
uv run python -m pytest -q gateway/tests/runtime/test_approval_arguments_preview.py gateway/tests/feishu/test_approvals.py
uv run python -m pytest -q tests/tools/test_registry.py tests/tools/test_registry_index.py tests/packaging/test_release_manifest.py
uv run python -m pytest -q tests/shared/test_integrations_api_border.py tests/shared/test_tool_api_border.py tests/integrations/test_feishu_boot_path.py
uv run python .github/ci/check_imports.py --strict
uv run python -m pytest -q tests/integrations/test_verification_registry.py tests/integrations/test_registry.py
```

Add focused S5a/S5b/S5c/S6 and Slack/Telegram/Rocket.Chat/Buzz tests selected by the actual diff
and failures. The final explicit file list is the planned starting set, not permission to skip
additional scoped targets that `.github/ci/test_scope_rules.py` selects for the actual diff.
Review `git diff --check`, `git diff --stat`, tool schema/discovery output and the PR template.
Inspect `tests/packaging/test_release_manifest.py` for hidden-import coverage. Do not change
`.importlinter.strict` or border allowlists merely to silence a failure; use approved package APIs.

`make verify-integrations` probes configured remote credentials and is **not** part of the
unauthorized local phase. If it is needed for final acceptance, run it only under fresh explicit
authorization with the destination and call budget stated beforehand. CI's full suite remains the
repository-wide test authority.

## 6. Commit, rollback and PR boundaries

Make only logical, recoverable commits **after** the second approval and relevant GREEN checks:

1. Target config, parser, setup/verifier and their tests. Rollback point: no agent write tool
   registered; the new setting is inert without discovery.
2. Shared schema/preapproval/log-privacy seam, metadata lookup and their tests. Rollback point:
   no Feishu write tool registered; existing runtime behavior remains guarded by regressions.
3. Text/CardKit create-reply transport, document status/cursor logic and tests. Rollback point:
   tool discovery still absent; legacy S6 delivery must remain green.
4. Tool packages, discovery, docs, `.env.example` and acceptance-focused tests. If rollback is
   required, revert S8a commits/PR; existing gateway/S6 functionality and credentials remain.

One S8a PR only, based on this branch, with the repository PR template and AI-usage disclosure.
No S7/S8b, no unrelated refactor, no compatibility forwarding module, no data migration, and no
automatic recall of already delivered messages. Keep the pre-existing design-document changes
and this plan accounted for in that PR; never reset or discard them.

After every push to an open PR, run `gh pr checks --watch` or inspect
`gh pr view --json statusCheckRollup,url`. For any failed job, obtain `gh run view <id>
--log-failed`, fix the root cause, rerun focused `CI.md` commands, push and watch again. Review
unresolved human/automated threads after each update; respond and resolve correctly, re-request
Greptile only after the update is complete and no review is in progress, and reach 5/5 with no
unresolved actionable comment. Do not merge until required checks, review and separately
authorized live acceptance are complete. After merge, monitor the merge commit's `main` CI,
full CodeQL and release workflows; fix or revert a failure before reporting completion.

## 7. Proposed live Feishu acceptance budget — not yet authorized

Request a **new** user approval naming one test chat and an exact time window, plus permission for
approval cards, metadata reads and message writes. The proposed upper bound is five tool attempts:
two approved sends (one short, one pre-paginated Markdown/table), up to two approved replies to one
known parent (`reply_in_thread=false`, then `true` only if the client UX supports it), and one denied
send that must produce no tool write. Before asking, calculate the long fixture's actual page count
offline; set the final message-create/reply cap to the known count, never an open-ended “long send”.
Suggested provisional cap: **at most eight message create/reply requests, eight CardKit card-create
requests, two metadata GETs, and five approval-card prompts**, all in that test chat. Any fallback
that would exceed a cap stops the exercise; no automatic retry on partial/maybe-sent outcomes.
No second chat or user is probed without separately naming and authorizing it. Record observed
message IDs, statuses and counts, not bodies or secrets. A failed or incomplete live exercise is
reported honestly; it does not authorize extra attempts.

## 8. Approval boundary

The user separately approved this plan before implementation began. That approval covers the
S8a code, tests, docs, commits and PR workflow, but not live Feishu operations. Live acceptance
still requires a new, bounded authorization.
