# Feishu S7 运行时渠道感知 Prompt 设计

- Date: 2026-09-30
- Status: Design and independent implementation plan approved by user (2026-09-30); S7 delivered through PR #41 (d03ddad) and its CodeQL repair PR #42 (1cee42f); post-merge main CI and full Python/JS CodeQL passed, with no new alerts; Release skipped by unchanged guard; no live acceptance
- Parent roadmap: [`2026-09-12-feishu-capability-completion-design.md`](2026-09-12-feishu-capability-completion-design.md), R33/R34
- Prerequisite: S8a merged in PR #38; the first complete CodeQL workflow in `NotYourBug/aasre` was enabled separately in PR #40
- Scope: S7 only; no S8b message read/search/member/reaction tools

## 1. Goal and decision boundary

One gateway message must produce an action prompt, persona and offered tool set
that agree about **this turn's** channel. A Feishu turn should understand that
the ordinary answer returns through the Feishu turn output, while an additional
message or explicit reply uses S8a's individually approved tools. Slack,
Telegram, Discord and Buzz turns must not inherit Feishu wording or tools.
The interactive shell remains a channel-neutral local surface and retains its
existing multi-integration behavior.

This is not a global switch to Feishu. Configuration merely makes a vendor
potentially available; the active gateway platform and the actual tools offered
to the model decide what the model may be told it can do. A prompt is guidance,
not an authorization mechanism, so the tool-availability boundary must agree
with it.

## 2. Verified repository baseline

1. `gateway/core/session/gateway_chat_context.py` already injects
   `_gateway_platform`, `_gateway_chat_id` and inactive inbound transports into
   a per-session resolved-integration cache. `TurnPlan` copies the resolved view
   into the per-turn `TurnSnapshot`; it must remain the source for S7.
2. `core/agent_harness/turns/action_driver.py` resolves the exact action tools
   for a turn before building its action prompt. The prompt builder currently
   does not receive that list.
3. `core/agent_harness/prompts/action/assemble.py` unconditionally joins all
   registered action fragments. `integrations/slack/action_prompt.py` names
   Slack read/search/member/reaction/send tools even in a Feishu turn.
4. `core/agent_harness/prompts/opensre_system_prompt.md` starts with an
   interactive-shell identity and contains CLI/terminal instructions. It is
   currently used as the action base for gateway turns too.
5. A Slack gateway persona is registered in
   `integrations/harness_adapters.py`, but no runtime prompt builder consumes
   `gateway_persona_fragments()`. Changing only that registration would have
   no user-visible effect.
6. Historical gather/assistant vendor-fragment registries have no runtime
   consumers in the current single-agent chat turn. The live answer is the
   action agent's accepted conclusion. Investigation has its own prompt and
   derives its tool orientation from the exact investigation tool set.
7. `DefaultPromptContextProvider` hides inactive transport *names* for some
   grounding, but the action prompt's `CONNECTED INTEGRATIONS` block reads the
   unfiltered snapshot names. Tool availability also needs its own channel
   check; hiding names alone does not remove a callable tool.
8. The bundled `morning-report` skill explicitly instructs an unconditional
   Slack send, and `skill_view` currently returns its body regardless of the
   gateway platform. It cannot be advertised unmodified to Feishu turns.

The roadmap's older “persona/action/gather/assistant fragments” wording is a
behavioral goal, not a reason to revive obsolete separate chat phases. S7
implements it on the live single-agent path and verifies that the separate
investigation path is not polluted by chat-channel instructions.

## 3. Runtime authority and visibility

### 3.1 Frozen channel context

The active platform comes only from gateway-injected metadata in the turn's
resolved view; never infer it from a configured integration, user prose, a
message prefix, a prior conversation, or a process-global variable. The chat
ID remains tool-side metadata for `target=current`; the prompt need not print
the raw ID. A gateway turn with missing or unrecognized platform receives a
neutral persona and **no vendor-specific messaging tools or claims**.

The tool list and prompt must derive from the same frozen turn view. Do not
re-resolve integrations inside individual prompt fragments or mutate a global
fragment registry per request. Concurrent sessions on different transports and
successive turns after session rotation must not inherit each other's channel.

### 3.2 Actual tool gate

Gateway action-tool selection must exclude messaging tools owned by inactive
chat/delivery providers, including configured but inactive Slack/Telegram/
Discord/Buzz/Feishu/Rocket.Chat providers. Non-messaging tools remain subject
to their normal availability and gateway capability policy. The shell retains
its existing tool selection. A new or unknown messaging provider must not be
implicitly available on an unrelated gateway channel.

The model-visible fragment is generated from the **exact names actually
offered** to the model after availability/capability filtering. If only
`feishu_send_message` is offered, the fragment must not instruct a call to
`feishu_reply_message`. If neither is offered, it must not claim Feishu
outbound capability. Source/tool metadata, not a regex over prompt text or an
LLM intent guess, drives this selection. Execution-time S8a target
authorization, metadata-only reply preflight and per-call approval remain
unchanged.

Work-item/scheduling tools that accept destination parameters retain their
existing authorization rules; S7 must audit their prompt examples so a Feishu
gateway turn does not advertise an inactive channel as its default. S7 does not
create a new cross-channel scheduling authority.

## 4. Prompt assembly

### 4.1 Surface base and persona

Keep the existing shell base intact for shell turns. Give gateway turns a
dedicated, maintained base that preserves shared agent safety, clarification,
tool-result and task-completion rules without claiming to be a terminal or
advertising terminal-only commands. Do not perform brittle string replacement
on the shell Markdown at runtime. The gateway base is channel-neutral; a
platform-owned persona supplies channel-specific language and rendering advice.

- Feishu: production-engineering teammate in Feishu; concise Markdown that
  CardKit can render, without Slack mention tokens or Slack-only formatting.
  The ordinary turn reply uses the gateway output, not `feishu_send_message`.
  An extra send/reply is a separate approved side effect. Explicit reply needs
  a known `message_id` and the S8a parent-chat check; do not promise message
  reading, searching, member listing or agent reaction tools from S8b.
- Slack: preserve its existing teammate style and Slack-specific guidance only
  for Slack turns; do not expose Feishu wording or tools.
- Telegram/Discord/Buzz: use a neutral chat teammate base plus only that active
  platform's applicable guidance. No fallback to a Slack persona.
- Interactive shell/unknown non-gateway surface: keep the existing shell base
  and no current-chat persona. A configured Feishu integration alone does not
  make the shell a Feishu chat.

### 4.2 Vendor fragments and connected integrations

The prompt-fragment provider API should accept a small immutable context:
surface, active gateway platform (if any), and offered tool names. Vendor-owned
fragments can then emit only applicable recipes; non-chat vendors such as
GitHub remain eligible only when the relevant tools are offered. No `core/`
module imports an integration package. Registration happens once at boot;
selection happens independently for each turn.

The action prompt's connected-integrations block must use the same hidden
channel set as the gateway tool view, not `session.configured_integrations`
alone. It may describe configured non-messaging integrations, but cannot imply
that a withheld transport can be called. Do not print credentials, allowlists,
raw SDK responses, or reply-source message bodies in prompt facts.

The selected platform and offered-tool facts belong in a per-session/per-turn
prompt block, not in a process-global stable fragment. Preserve the existing
`PromptEnvelope` split invariant: stable shell/common text remains byte-stable;
changed channel/tool facts legitimately change the cached prefix, while turn
text/history remains ephemeral. A host/channel switch must recompute the
prompt rather than reuse a previous session's rendered text.

### 4.3 Skills that contain channel instructions

The S7 default is conservative: the current `morning-report` skill is **not
advertised or retrievable on non-Slack gateway turns**, because its body
mandates Slack delivery even without an explicit delivery request. The index,
demo list, `skill_view` success and its error/available lists must apply the
same visibility rule, so direct calls cannot retrieve a hidden body. Slack
gateway and interactive-shell behavior remain unchanged. Other bundled skills
must be audited for unconditional inactive-channel instructions and similarly
scoped if found.

Adapting `morning-report` to deliver via Feishu is a distinct behavior decision:
S8a requires separate approval for each extra send, and ordinary Feishu turn
output already replies to the user. Do not silently replace its Slack send
with an automatic Feishu send in S7. If the user wants a Feishu morning-report
workflow, specify its delivery/scheduling contract separately before enabling
that skill on Feishu.

## 5. Boundaries and failure behavior

- S7 adds no Feishu API call, credential, permission, env var or S8b tool.
- It does not make approval optional, cache an approval, widen S8a target
  authority, or turn an uncertain write into an automatic retry.
- A missing gateway platform is not “probably Feishu”; fail closed for
  messaging tools and show a neutral chat persona.
- A disconnected or unavailable Feishu tool is not named as callable. The
  model may explain the limitation, but must not invent a CLI/slash delivery
  command or substitute another transport.
- Prompt/tool filtering is not a substitute for execution authorization;
  existing tool guards remain the final check.
- The historical gather/assistant fragment registries are not treated as
  delivery points. Their removal or retention is an implementation-plan detail
  only if it does not leave a second unfiltered prompt path.

## 6. High-signal verification

1. With Feishu and Slack configured simultaneously, a Feishu gateway turn
   offers only Feishu messaging tools, shows Feishu guidance, and contains no
   Slack persona, Slack tool recipe, Slack-only skill body or S8b claims.
2. In the same process, a subsequent Slack gateway turn keeps Slack tools and
   persona and contains no Feishu persona/tool recipe. Add a concurrent
   different-session test to catch registry or cached-prompt bleed.
3. Feishu with send only, reply only when eligible, neither tool, malformed or
   missing platform, and changed chat/session metadata each have a focused
   prompt/tool agreement assertion. Do not multiply literal-only variants.
4. A shell turn retains its current action behavior, skill catalog and
   multi-integration visibility; compare a characterization prompt captured
   before the refactor with the new shell output.
5. `skill_view` cannot reveal a non-Slack gateway-hidden skill by direct name
   or through error suggestions. Slack and shell retain the skill.
6. The investigation prompt lists only actually selected investigation tools
   and does not inherit gateway chat fragments; no obsolete gather/assistant
   registry is mistaken for runtime coverage.
7. Prompt-envelope cached/ephemeral split, import/API borders, package data
   packaging, registry reset/idempotent install, and gateway/S8a approval
   regressions remain green.

No live Feishu send/reply or other external operation is authorized by this
design. A future real-channel acceptance needs a fresh bounded authorization;
deterministic prompt and registry tests are the primary S7 gate.

## 7. Delivery sequence

1. Obtain explicit approval of this design, including the conservative
   `morning-report` visibility decision.
2. Write a separate S7 implementation plan with exact files, small-step TDD,
   CI commands, rollback/commit boundaries and any proposed live-operation
   budget; obtain a second approval before product/test/config edits.
3. Implement only S7, update user-facing docs if behavior changes, and use
   `CI.md` for local checks, PR checks, Greptile 5/5 and post-merge monitoring.
4. Start S8b only as separately researched tools after S7 is complete.

The user has separately approved both this design and its implementation plan.
Scoped S7 implementation, offline checks and the branch/PR workflow may now
start in a new conversation. No implementation has started yet. Neither
approval authorizes live Feishu activity, S8b work or merging without the agreed
delivery gates and user direction.
