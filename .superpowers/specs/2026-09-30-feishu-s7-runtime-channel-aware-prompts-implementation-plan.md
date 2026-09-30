# Feishu S7 Runtime Channel-Aware Prompts — Implementation Plan

- Date: 2026-09-30
- Status: Approved by user (2026-09-30); implementation and offline acceptance complete; PR/CI/review closure pending, not merged
- Approved design: [`2026-09-30-feishu-s7-runtime-channel-aware-prompts-design.md`](2026-09-30-feishu-s7-runtime-channel-aware-prompts-design.md)
- Roadmap: [`2026-09-12-feishu-capability-completion-design.md`](2026-09-12-feishu-capability-completion-design.md), R33/R34
- Checkout: `C:\Users\23033\Desktop\opensre2\.worktrees\feishu-s5b-feedback`
- Branch: `codex/feishu-s7-spec`
- Base: `407a9b2baa527353195b8d16aa86cc9ba27d67ae` (PR #40); S8a previously merged as PR #38
- Scope: S7 only; no S8b tools, new Feishu API, new environment variable, or live operation

## 1. Authority and implementation choices

The user approved the S7 design and delegated its tradeoffs on 2026-09-30, then
separately approved this plan and requested implementation in a new conversation.
Both approvals required by design §7 and roadmap §8 are complete. Scoped S7
implementation, offline checks and the branch/PR workflow are authorized; do
not ask for the same design/plan approval again. This does not authorize a live
Feishu operation or merging without the agreed delivery gates and user direction.

Use the existing worktree, recheck branch/status/diff before implementation, and
preserve all pre-existing modifications. Use `apply_patch` for edits and
`uv run python` / `uv run opensre` for Python / OpenSRE commands. No `.env`
contents or credentials enter output, fixtures, commits, prompt facts or PRs.

Recommended choices, now fixed for this plan:

1. One immutable per-turn prompt context, built from the frozen turn metadata
   and final offered tools. Do not infer the channel from configuration or text.
2. Enforce the gateway messaging gate at the final action-driver boundary,
   including custom/precomputed tool providers. Keep the existing availability
   and capability checks; the new gate only removes inactive messaging tools.
3. Maintain a dedicated neutral gateway base; leave the shell Markdown intact.
   Context-aware vendor fragments emit only recipes for actual offered names.
4. Hide the existing Slack-mandatory `morning-report` on every non-Slack gateway
   in the index, demos, body lookup and error suggestions. Keep Slack/shell
   visibility and its delivery contract; do not adapt it to Feishu in S7.
5. Retain unused historical gather/assistant registries without new consumers;
   migrate only live action/persona APIs. No compatibility forwarding module or
   zero-argument escape hatch for the new action/persona API.
6. Fix work-item default-channel lookup to use the frozen `AgentToolContext`,
   not a mutable session cache. Preserve explicit destination handling and
   existing authorization; no new cross-channel scheduling permission.
7. Deterministic acceptance is mandatory. Live acceptance is optional, requires
   new bounded authorization, and is not a prerequisite for proving prompt
   isolation or cache correctness.

## 2. Runtime wiring and failure contract

```text
gateway-injected metadata → frozen TurnPlan / TurnSnapshot
                                    │
normal available action/chat tools → final messaging gate
                                    │
                         exact offered-tool names
                                    │
                         immutable prompt context
                       ┌────────────┴──────────────┐
                 prompt envelope             tool resources
                 base + persona              skill_view visibility
                 vendor recipes              (same frozen context)
                 visible skills
                 connected integrations
```

### 2.1 Context and messaging-source ownership

Add a small stdlib-only `ActionPromptContext` leaf under
`infrastructure/harness_providers/prompt_context.py`, with immutable `surface`,
`active_platform` and `offered_tool_names: frozenset[str]`. It carries no chat
ID, credentials, configuration objects, session reference or message text.
The existing provider layer is accessible from core, integrations and tools;
putting this leaf there avoids a back-edge into prompt assembly.

Add an independent messaging-source registry under
`infrastructure/harness_providers/messaging_sources.py`. Registration maps a
tool's exact `source` to its messaging-provider role and optional inbound
platform; the registry does not implement vendor APIs. Populate it once from
`integrations/harness_adapters.py` using the existing inbound
`MessagingPlatform` and delivery `Provider` vocabularies. Exclude
`Provider.INTERACTIVE_SHELL` from messaging sources. Rocket.Chat is a messaging
source but delivery-only: do not add it to the inbound identity enum.

The registry exposes pure recognition, source eligibility and inactive-source
queries. Include sources even when they have no persona or action fragment.
A newly enumerated delivery provider is messaging and gateway-ineligible unless
explicitly registered as an inbound platform; an unknown active platform never
matches a messaging source. Add a completeness contract for both vocabularies
so provider additions cannot silently evade the gate. A new vendor messaging
tool must use its registered source, not disguise itself as a generic action.
Non-messaging tools retain their existing availability/capability policy.

Build channel context only from the snapshot's surface and frozen resolved
metadata. When the snapshot says gateway, missing, wrong-type or unknown
`_gateway_platform` means neutral gateway plus no messaging tools. Do not fall
back to shell because a gateway's platform is missing. For a direct runner with
no explicit snapshot, gateway metadata can preserve an already injected
gateway identity; never guess from integration names. Unknown non-gateway
surfaces use the existing shell profile as specified in the design.

### 2.2 Final tool gate and resource handoff

Keep `ToolProvider.action_tools`'s current interface. In `_run_action_turn`,
resolve integrations once, obtain normal candidates, and apply one pure
channel filter before building schemas or selecting the LLM. This also covers
`DefaultToolProvider`'s precomputed branch and other injected providers.
Use `RegisteredTool` / runtime tool `source` metadata; no name-prefix or
natural-language routing. For shell turns, return the original tool order and
availability unchanged.

Build the final context with exactly the filtered tool names. Pass it explicitly
to the prompt builder and place it in a fresh tool-resource mapping under a
named resource key defined in the context leaf. Do not mutate the provider's
resource dictionary or a process-global current context. Both prompt APIs
require an explicit context after migration; a caller may not fall back to all
registered fragments/tools. Migrate repository callers/tests in the same
change. Literal `/slash` and `!shell` dispatch policy remains unchanged.

`skill_view` reads this resource through its `AgentToolContext` wrapper, then
uses a shared visibility-aware catalog/body API. It does not read
`ActionToolScope.session.resolved_integrations_cache`. The runtime wrapper
requires a valid frozen resource; missing/malformed runtime resources fail
closed for platform-restricted skills, even if platform metadata is absent.
Only a truly local standalone helper may use an explicit shell default. Do not
make a missing runtime resource look local.

Work-item default target lookup reads `context.resolved_integrations`, which
the action driver already freezes for this turn. An absent context produces
no current-channel default; do not restore the old session-cache fallback.
Explicit destination parsing, deduplication and validation remain unchanged.

### 2.3 Prompt, skills and cache details

- Keep `opensre_system_prompt.md` byte-identical. Add an adjacent
  `gateway_system_prompt.md` and load it through `system_prompt.py`; missing
  packaged data must fail a packaging check, not trigger a shell fallback.
- The gateway base preserves tool-result grounding, clarification/menu
  availability, task completion, explicit authorization, compound actions,
  investigation handoff and honest limitation rules. Audit it against the
  shell base section-by-section; omit terminal identity, repository-operating
  instructions and terminal-only command advice. This does not remove a tool
  already allowed by gateway capability policy.
- Add `ACTION_GATEWAY_PERSONA` to the existing block IDs. Common bases/runtime
  facts stay `STABLE`; active persona, offered-tool-dependent vendor recipes,
  skills and visible integration facts are `CONTEXT`. User message, history,
  prior actions and task-plan interaction remain `EPHEMERAL`. Keep the current
  memory policy; S7 does not broaden shared-gateway memory access.
- Action/persona factories take `ActionPromptContext`. Existing historical
  gather/assistant callbacks keep their separate zero-arg type. Registration
  remains boot-time and reset/idempotent; factories only read the context.
- Vendor files use explicit per-recipe tool-name requirements, not runtime
  search/replace or regex stripping of the old paragraphs. A recipe needing
  multiple tools appears only if all prerequisites are offered. Slack's
  send/read/search/member/reaction guidance must be independently eligible.
  GitHub's CLI and star-history guidance likewise follows offered names.
- Feishu persona applies only on active Feishu gateway turns. Its separate
  action fragment describes ordinary gateway output versus extra approved
  sends/replies; send-only and reply-only contexts emit only applicable
  recipes. Never claim S8b tools or invent a reply-parent ID.
- Telegram/Buzz guidance is active-platform-only on gateway; Discord and other
  recognized channels can use the neutral gateway base without Slack fallback.
  Rocket.Chat fragments remain eligible in the shell but not unrelated gateway
  turns. A vendor setup command is mentioned only on the shell when its tool
  is offered; gateway limitations point to the bot operator.
- Connected-integration grounding and the action block share the registry's
  inactive-source policy. Extend gateway metadata's existing hidden set to
  include delivery-only sources; assembly also computes the policy from its
  frozen context rather than trusting an old hidden-set cache.
- Only advertise a skill index/demo instruction when `skill_view` is actually
  offered; the assembler must not tell the model to call a withheld reader.
  Shared skill visibility itself remains based on surface/platform.
- Add optional `gateway_platforms` frontmatter to `ActionSkill`, with
  `gateway_platforms: [slack]` on `morning-report`. Absence means unrestricted;
  malformed declared restrictions fail closed on gateway. Shell keeps its
  catalog. Audit all other bundled skills before enabling them unchanged.
- Cache disk discovery separately from visibility. Index/demo cache keys use
  only normalized surface/platform, not user text, chat ID, mutable dicts or
  the entire tool list. Use an immutable/read-only name map for body lookup and
  sets for membership. Clearing test caches clears every derived view. Never
  overwrite a single global rendered catalog for the last channel.

Shell regression means identical base text, local tool/skill availability and
existing workflows. The entire pre-S7 shell prompt need not be byte-identical:
offered-tool filtering deliberately removes recipes for unavailable tools,
and fragment tiers change. Within the same surface/platform/tool view, changed
user text/history must still leave the cached prefix byte-identical.

## 3. Exact product, resource and documentation file map

Paths below are repository-relative. New files are marked; all other paths
already exist. Read any nested `AGENTS.md` before editing its subtree.

| File | Responsibility |
| --- | --- |
| `infrastructure/harness_providers/prompt_context.py` (new) | Immutable context and named tool-resource key; stdlib-only leaf. |
| `infrastructure/harness_providers/messaging_sources.py` (new) | Boot registration, source/platform classification, inactive-source queries and reset. |
| `infrastructure/harness_providers/prompt_fragments.py` | Context-aware live action/persona callbacks; retain unused gather/assistant API without new consumers. |
| `infrastructure/harness_providers/lifecycle.py` (new) | Move the existing reset orchestration out of the facade, characterize it before moving, and add the new registry reset; do not import the package facade back. |
| `infrastructure/harness_providers/__init__.py` | Export intended APIs and re-export reset from its lifecycle owner; imports/`__all__` only, no selection/reset implementation in the facade. |
| `integrations/harness_adapters.py` | Register messaging-source metadata and Feishu factories; keep dispatch thin and install idempotent. |
| `core/agent_harness/prompts/kernel/channel_context.py` (new) | Pure per-turn context construction/normalization from explicit surface and resolved metadata; no prompt text or upper-tier imports. |
| `core/agent_harness/prompts/kernel/__init__.py` | Re-export the new kernel helper only. |
| `core/agent_harness/tools/action_tools.py` | Pure final channel filter based on tool source metadata; normal availability stays intact. |
| `core/agent_harness/turns/action_driver.py` | Final gate, exact offered names, explicit prompt context and copied tool-resource handoff. |
| `core/agent_harness/prompts/gateway_system_prompt.md` (new) | Maintained channel-neutral chat base with preserved shared safety/completion rules. |
| `core/agent_harness/prompts/system_prompt.py` | Load both adjacent bases without shell-text rewriting or gateway fallback. |
| `core/agent_harness/prompts/kernel/envelope.py` | Add the gateway-persona block ID; preserve render/split semantics. |
| `core/agent_harness/prompts/action/assemble.py` | Explicit context parameter, surface base/persona selection, actual-tool fragments, visible integrations and context-tier skills. |
| `integrations/feishu/gateway_persona.py` (new) | Feishu teammate/Markdown wording only. |
| `integrations/feishu/action_prompt.py` (new) | Offered send/reply recipes and ordinary-answer versus extra-side-effect guidance. |
| `integrations/slack/gateway_persona.py` | Restrict to Slack gateway; correct obsolete consumer documentation. |
| `integrations/slack/action_prompt.py` | Separate eligible Slack recipes; no inactive tools or gateway CLI setup advice. |
| `integrations/github/action_prompt.py` | Emit CLI/star-history recipes only for actually offered prerequisites. |
| `integrations/telegram/action_prompt.py` | Surface/platform/tool-aware Telegram recipe. |
| `integrations/rocketchat/action_prompt.py` | Shell-only delivery recipe unless a separately supported inbound platform exists. |
| `integrations/buzz/action_prompt.py` | Surface/platform/tool-aware Buzz recipe. |
| `gateway/core/session/gateway_chat_context.py` | Include inactive delivery-only messaging sources in the hidden-integration set. |
| `core/agent_harness/prompts/grounding/provider.py` | Use the same frozen source policy for visible-integration grounding. |
| `core/agent_harness/prompts/skills/loader.py` | Parse visibility metadata; share filtered discovery/index/demo/body rules and keyed caches. |
| `core/agent_harness/prompts/skills/morning_report/SKILL.md` | Add Slack gateway visibility metadata only; do not change workflow/body/delivery defaults. |
| `tools/interactive_shell/actions/skill_view.py` | Consume frozen resource context; filtered success/error catalog; make schema examples channel-neutral. |
| `tools/system/work_items/delivery.py` | Current-channel default from frozen agent context, not mutable session cache. |
| `pyproject.toml` | Include the new gateway base in prompt package data; no dependency changes. |
| `infrastructure/deployment/packaging/release_manifest.py` | Include both runtime prompt bases in the existing non-skill data manifest. |
| `opensre.spec` | Use the common manifest for prompt resources; avoid a second hardcoded gateway-only list. |
| `docs/messaging/feishu.mdx` | Explain ordinary replies versus additional approved sends, channel-local gateway capabilities and current morning-report limitation. |
| S7 design/plan and total roadmap under `.superpowers/specs/` | Approval, implementation, validation and delivery status. |

Existing `action/__init__.py`, `prompts/__init__.py`, `skills/__init__.py` and
`spi/grounding.py` retain public names where possible; function signatures flow
through their existing imports. Edit a facade only if an intentional new API
needs exporting, using imports/`__all__` only. Do not add forwarding modules.
No change is planned to `.env.example`, env constants, SDK payloads, S8a tools,
inbound identity policy, Release workflow repository guard, tool registry
discovery, shell base, or import-border allowlists. Amend the plan if a further
behavior file is actually necessary; do not drive by adjacent features.

## 4. Exact test map and high-signal TDD sequence

### 4.1 New tests

| File | Distinct failure category |
| --- | --- |
| `tests/infrastructure/test_messaging_source_policy.py` (new) | Registry classification, unknown/delivery-only platform fail-closed and vocabulary completeness. |
| `tests/core/agent/prompts/test_channel_prompt_context.py` (new) | Frozen metadata authority, wrong-type/absent platform and no configuration/text inference. |
| `tests/core/agent_harness/test_gateway_channel_tools.py` (new) | Final source gate on normal/precomputed providers; non-messaging and shell preservation. |
| `tests/core/agent/prompts/test_gateway_channel_prompt.py` (new) | Exact offered subset, platform persona, gateway base and filtered connected facts. |
| `tests/core/agent/prompts/test_skill_channel_visibility.py` (new) | Catalog/demo/body/error consistency and cache-key isolation. |
| `tests/core/agent_harness/test_channel_turn_isolation.py` (new) | Real action-runner wiring, sequential reuse and concurrent different-session isolation with local recording LLM. |

### 4.2 Existing tests to extend or migrate

- Provider installation/callback migration:
  `tests/infrastructure/test_harness_providers_registries.py`,
  `tests/integrations/test_harness_adapter_wiring.py`,
  `tests/integrations/test_harness_providers_install.py`.
- Prompt API/behavior:
  `tests/core/agent/test_prompt_envelope.py`,
  `tests/core/agent/orchestration/test_action_prompt.py`,
  `tests/core/agent/prompts/test_system_prompt_markdown.py`,
  `tests/core/agent/prompts/test_gateway_visible_integrations.py`,
  `tests/core/agent/prompts/test_skills_demo.py`,
  `tests/core/agent/prompts/test_action_setup_state.py`,
  `tests/core/agent/prompts/test_action_runtime_facts.py`,
  `tests/core/agent/prompts/test_turn_interaction.py`.
- Other explicit prompt call sites/API fixtures:
  `tests/core/agent_harness/task_plan/test_prompt.py`,
  `tests/core/agent_harness/turns/test_tool_output_markup.py`,
  `tests/core/agent_harness/test_harness_api.py`,
  `tests/core/agent/test_turn_scenarios.py`,
  `tests/core/state/test_state_package_boundary.py`.
- Skill runtime contract:
  `tests/core/agent/orchestration/test_skill_view_tool.py`,
  `tests/core/agent_harness/turns/test_skill_view_display.py`.
- Frozen reminder defaults:
  `tests/tools/test_work_items_tools.py`.
- Gateway hidden names and turn snapshot:
  `gateway/tests/session/test_gateway_chat_context.py`,
  `gateway/tests/test_session_resolver.py`.
- Vendor recipe subset:
  `tests/integrations/github/test_action_prompt.py`.
- Packaging:
  `tests/packaging/test_release_manifest.py`,
  `tests/packaging/test_validate_wheel.py`,
  `tests/packaging/test_frozen_entrypoint.py`.

Do not add another happy-path wrapper for each vendor. Pin one source-policy
matrix, then distinct subset/cache/authorization/wiring failures. Existing tests
are migrated because their actual API changes, not to delete failing coverage.

### 4.3 RED → GREEN order

Every command below is a planned implementation command, not a test run already
performed. Add one named test for the stated bug class, run its command RED,
record the relevant assertion failure, implement the smallest fix, then run
the identical command GREEN plus the affected neighboring file(s). When a new
module/API is needed, add only the minimal importable interface before the
behavioral RED so an import error is not mistaken for a tested bug. A collection
or unrelated environment failure is not a valid RED. Characterization tests
run GREEN before a behavior-preserving refactor, including extraction of the
existing provider-reset implementation from its facade.

| Step | One failure class / implementation work | Focused RED/GREEN command |
| --- | --- | --- |
| 0 | Characterize shell base text, tool order/availability, skill bodies and literal dispatch before refactoring; capture only deterministic block inputs, not host secrets. | `uv run python -m pytest -q tests/core/agent/prompts/test_system_prompt_markdown.py tests/core/agent/orchestration/test_action_prompt.py tests/core/agent/orchestration/test_skill_view_tool.py` |
| 1 | Unknown or delivery-only active provider must not enable messaging tools; add registry/context leaves and vocabulary-completeness test. | `uv run python -m pytest -q tests/infrastructure/test_messaging_source_policy.py -k unknown_platform` |
| 2 | Gateway without trusted platform must not become shell/Feishu; build context solely from frozen snapshot metadata. | `uv run python -m pytest -q tests/core/agent/prompts/test_channel_prompt_context.py -k missing_platform` |
| 3 | A precomputed mixed-vendor tool list must not bypass the final gate; retain shell/non-messaging order. | `uv run python -m pytest -q tests/core/agent_harness/test_gateway_channel_tools.py -k precomputed` |
| 4 | Prompt/resource context must use final offered tools, not configured vendors or an earlier list; migrate live action/persona callback APIs and driver handoff. | `uv run python -m pytest -q tests/core/agent_harness/test_channel_turn_isolation.py -k offered_tools` |
| 5 | Feishu with send-only must not claim reply/Slack/terminal capabilities; implement gateway base, Feishu persona, context-tier assembly and vendor recipe tables. | `uv run python -m pytest -q tests/core/agent/prompts/test_gateway_channel_prompt.py -k send_only` |
| 6 | Configured inactive/delivery-only sources must not reappear via connected/setup grounding; share registry policy and extend gateway hidden metadata. | `uv run python -m pytest -q tests/core/agent/prompts/test_gateway_channel_prompt.py -k connected` |
| 7 | A hidden skill must not be retrievable by direct name or error suggestions; add metadata and shared catalog/body visibility, then wire skill_view to frozen resources. | `uv run python -m pytest -q tests/core/agent/prompts/test_skill_channel_visibility.py -k direct_lookup` |
| 8 | A global cached skill view must not bleed from Slack to Feishu or back; isolate visibility caches and preserve cache-clear behavior. | `uv run python -m pytest -q tests/core/agent/prompts/test_skill_channel_visibility.py -k cache_isolation` |
| 9 | Mutating session cache after turn start must not redirect a reminder's implicit target; use the frozen AgentToolContext only. | `uv run python -m pytest -q tests/tools/test_work_items_tools.py -k frozen_gateway` |
| 10 | Reusing a runner/session binding must rebuild channel/tool facts; use the real ActionTurnRunner with recorded provider inputs. | `uv run python -m pytest -q tests/core/agent_harness/test_channel_turn_isolation.py -k sequential` |
| 11 | Concurrent different-channel sessions must not share registry selection, context or skill catalog; synchronize with events/barriers, bounded joins and cleanup, not sleeps. | `uv run python -m pytest -q tests/core/agent_harness/test_channel_turn_isolation.py -k concurrent` |
| 12 | Same view/different user history must keep cached bytes stable, while platform/tool change invalidates only the intended context blocks; preserve split/render equality. | `uv run python -m pytest -q tests/core/agent/test_prompt_envelope.py` |
| 13 | An installed/frozen artifact must not omit the gateway base; add manifest/package-data and real wheel resource checks. | `uv run python -m pytest -q tests/packaging/test_release_manifest.py tests/packaging/test_validate_wheel.py tests/packaging/test_frozen_entrypoint.py` |

After step 5, independently cover reply-only and neither-tool as different
capability branches, plus a subsequent Slack turn. Do not multiply target IDs
or prose variants. After step 7 audit the other bundled skill bodies for
unconditional transport/scheduling instructions; scope any newly discovered
restriction explicitly before editing another runtime skill document.

Runner tests must execute the real prompt/tool/resource construction and at
least a controlled skill_view call; do not only assert that a mocked builder
was called. The local LLM returns scripted responses; any accidental network
client/API creation is a test failure. Concurrent tests use separate sessions
on one boot registry; sequential tests bind the next session to reused
infrastructure and change the frozen metadata without resolving again.

## 5. Local verification and packaging

The commands below were approved during planning. Their actual implementation
results are recorded in §9; do not treat a listed command as evidence of a run.

### 5.1 Mandatory quality commands

On Windows, the following are the exact `Makefile` target equivalents, using
the repository-required `uv run python`:

```powershell
git status --short
uv run python -m ruff check bootstrap config core gateway integrations infrastructure surfaces tools tests/
uv run python -m ruff format --check bootstrap config core gateway integrations infrastructure surfaces tools tests/
uv run python -m mypy bootstrap config core gateway integrations infrastructure surfaces tools
```

If format fails, format only intended touched files, then rerun the full format
check; do not bulk-reformat unrelated user code. If the working tree changed
externally, reread the diff before any bulk mechanical command.

### 5.2 Scoped regression gates

The broad rules for touched `core/`, `integrations/`, `gateway/` and `tools/`
map to the following packages. Run them in bounded invocations, inspect real
failures, and record results; no normal local full-suite/test-cov run:

```powershell
uv run python -m pytest -q tests/core/
uv run python -m pytest -q tests/integrations/
uv run python -m pytest -q gateway/tests/
uv run python -m pytest -q tests/tools/
uv run python -m pytest -q tests/packaging/
```

Supplement with the directly affected provider/shell seams and S8a contracts:

```powershell
uv run python -m pytest -q tests/infrastructure/test_harness_providers_registries.py tests/infrastructure/test_messaging_source_policy.py tests/interactive_shell/runtime/test_prompt_builder.py
uv run python -m pytest -q tests/integrations/test_feishu_send_reply_tools.py tests/integrations/test_feishu_outbound_targets.py tests/integrations/test_feishu_message_lookup.py tests/integrations/test_feishu_document_delivery.py gateway/tests/feishu/test_approvals.py
```

The S8a regression gate preserves target=current authority, cross-platform/
cross-chat rejection, per-call approve/deny, reply preflight and partial/uncertain
delivery. No live SDK send/reply/GET is run. Also verify that investigation
tool descriptions stay based on selected investigation tools and never import
gateway persona fragments; use existing tests under `tests/agent/` and
`tests/core/` as applicable, adding one focused assertion only if the owning
investigation contract is otherwise untested. This is not an S8b read tool.

### 5.3 Import/API and resource gates

The applicable tool/integration/harness instructions and approved design require
these border and packaging checks in addition to the standard CI harness:

```powershell
uv run python .github/ci/check_imports.py
uv run python -m pytest -q tests/shared/test_tool_api_border.py tests/shared/test_integrations_api_border.py tests/tools/test_harness_api_border.py tests/interactive_shell/test_harness_api_border.py gateway/tests/test_harness_api_border.py gateway/tests/test_package_borders.py
uv build --wheel
```

Inspect the exact wheel produced by this build (do not guess a version/glob
away multiple artifacts), then validate its resources with:

```powershell
uv run python infrastructure/deployment/packaging/validate_wheel.py <exact-wheel-path>
```

The manifest/validator tests require both adjacent bases; inspect actual wheel
entries and prove the installed resource can be read without the source tree.
Use a temporary extracted-wheel/import probe, no global install and no daemon
startup. Frozen tests pin the checked-in spec's common manifest/resource
coverage; a full PyInstaller binary build is not required locally. Release
workflow enablement remains out of scope.

No new import ignore/allowlist or `TYPE_CHECKING`/lazy-import cycle workaround.
Infrastructure context leaves cannot import vendor/harness assembly modules;
core cannot import integrations/tools/gateway/surfaces. Tools consume the
existing harness SPI/tool APIs. Any added/changed Protocol method has a
docstring-only body; HTTP constants and env names stay in their owning leaves.

## 6. Commit boundaries, rollback and PR scope

Suggested reviewable commits, after plan approval:

1. **Context/policy foundations:** immutable context, source registry and boot
   registration/reset tests. No new runtime channel behavior activated alone.
2. **Atomic live channel wiring:** final driver tool gate, context handoff,
   dedicated base/personas, context-aware vendor fragments and visible facts;
   migrate direct callers/tests in the same commit.
3. **Skill/default isolation:** scoped skill catalog/body and wrapper, immutable
   reminder defaults, sequential/concurrent/cache regressions.
4. **Packaging and handoff:** package data/common manifest, wheel/frozen tests,
   user docs, final roadmap/spec/plan verification evidence.

Intermediate commits are review points, not live-deployment checkpoints. Do
not start a gateway or perform external acceptance with partially wired
channel/skill isolation. Prefer one complete branch push after all local gates;
every subsequent fix push still requires CI monitoring. Include the existing
Markdown S8a status correction, clearly identified as historical status only.

Keep one S7 PR against `main`. Do not include S8b, optional morning-report
adaptation, unrelated CodeQL-alert fixes, dependency changes, release activation
or old-registry cleanup. Fill every applicable PR-template field, including
AI usage/review disclosure and offline channel-isolation demo evidence; do not
invent an issue number, screenshot or live acceptance result.

Rollback before push is a targeted follow-up edit preserving all unrelated
worktree changes; never reset/clean/discard them. Rollback after merge is a
reviewed revert of the S7 PR as a unit, restoring prompt/tool/skill coherence
and retaining S8a; no partial revert that restores global fragments but keeps
another half of the gate. If a post-merge failure needs a revert, obtain any
required new authority and do not report completion while it remains broken.

## 7. PR and post-merge closure

1. Before each push, recheck status/diff and run `CI.md` quality plus focused
   tests for touched modules. Never stage `.env`, credentials or build output.
2. After every push with an open PR, run `gh pr checks --watch` (or inspect
   `gh pr view --json statusCheckRollup,url`). On failure, read
   `gh run view <run-id> --log-failed`, repair the product/test root cause,
   rerun focused local gates, push and watch again. Treat concurrency flakes
   as real harness bugs; no skipped tests/constant-condition disabling.
3. Address actionable human/automated review, resolve conversations and obtain
   Greptile 5/5. If review is unavailable, report it as an unmet gate, not an
   implicit pass. Attach the created S7 PR to this task.
4. Merge only with the agreed user direction and all gates satisfied. Monitor
   the merge commit's `main` CI, full Python/JS CodeQL and release workflow
   states. Release may still legitimately skip under the unchanged repository
   guard; describe the actual state rather than claiming a release succeeded.
5. Compare CodeQL alerts with the existing pre-S7 baseline: existing open alerts
   are not silently dismissed or batch-fixed here. Any S7-introduced finding
   must be triaged/fixed; any workflow failure remains unfinished delivery.
6. Record commit/PR/check IDs, focused local results and any live limitation in
   the S7 status documents. Only then mark S7 delivered. S8b starts separately
   with individual research/spec/plan/tool authority.

## 8. Optional future live acceptance budget

No earlier S8a time window/count budget is reused. Suggested new authorization:
one explicit test chat, a fresh user-approved time window, at most two inbound
user prompts (“who are you / what can you do?” and an ordinary SRE explanation),
zero approval prompts, zero agent extra-send/reply tool calls and zero metadata
GETs. Do not trigger a skill, schedule, investigation, report delivery or other
tool side effect.

Ordinary gateway output is still an external write. Propose a separate cap of
four message create/reply and four CardKit creations total, including status
and answer cards, only after a dry-run/transport inspection confirms this is
sufficient and calls can be counted. Do not conflate zero S8a calls with zero
gateway writes, and do not call a global count exact without instrumentation.
On unknown count, failure, uncertainty or possible cap overrun, stop without
automatic retry; ask for a newly scoped budget if needed.

Live observation can check Feishu wording and ordinary reply behavior, but
cannot establish concurrent Slack/Feishu isolation, offered-tool-subset safety,
hidden skill nonretrievability or stable cached bytes. Those remain mandatory
offline gates. If live acceptance is skipped, document that limitation rather
than upgrading S8a's unverified multipage test to “passed”.

## 9. Exit and current state

S7 is implemented in the approved checkout/branch. The shell base Markdown is
unchanged. The action driver filters final tools by registered source, then
passes one frozen context to prompt factories and a copied tool-resource map.
Gateway persona/recipes, connected integrations and skill visibility use that
context. Work-item defaults read the frozen tool metadata. The historical
gather/assistant APIs remain unused and unchanged.

Behavioral RED evidence (assertion failures, not collection failures):

| Contract | Observed RED | GREEN / acceptance |
| --- | --- | --- |
| Unknown/delivery-only platform | Three gateway cases allowed messaging | Registry/context policy tests pass |
| Missing/malformed platform | Gateway became shell | Four neutral-gateway cases pass |
| Precomputed tools | Inactive sources remained offered | Real runner filters by source and preserves order |
| Context/resource handoff | Probe received no prompt context | Prompt and tool receive the same immutable object; provider map unchanged |
| Feishu send-only | Old shell base lacked Feishu persona | Send-only, reply-only and neither-tool branches pass |
| Connected integrations | Slack/Rocket.Chat appeared on Feishu | Shared source policy hides inactive and delivery-only sources |
| Hidden skill direct lookup | Four contexts loaded morning-report | Body, missing-name and unknown-name suggestions enforce visibility |
| Skill cache isolation | Feishu index retained morning-report after Slack | Index/demo/body/catalog agree through Slack → Feishu → neutral → shell → Slack |
| Frozen delivery default | Session rebind changed Telegram target to Slack | Default remains original target; absent metadata has no session fallback |
| Runtime resources | Both prompt bases absent from common manifest/validator | Manifest, wheel validator and frozen-spec contracts pass |
| Malformed skill metadata | Empty YAML list became unrestricted | Gateway hides malformed metadata; local shell retains lookup |

The composed sequential, concurrent and prompt-cache acceptance tests run the
real ActionTurnRunner with an offline scripted LLM and a controlled skill_view
call. They pass after the earlier fixes; no additional behavioral RED is claimed
for these composed acceptance tests. A normal DefaultToolProvider test also pins
availability checks before the final channel gate. History changes leave cached
bytes identical; channel/tool changes alter CONTEXT blocks while gateway base
and static runtime facts remain STABLE; split/render equality is preserved.

Bundled-skill audit: morning-report is the only bundled action skill with an
unconditional Slack delivery workflow. Only its frontmatter changed to
`gateway_platforms: [slack]`; its body and delivery contract remain unchanged.
No other skill workflow was adapted or restricted.

Completed local evidence (2026-09-30):

- Original characterization: 34 passed before runtime edits; provider-reset
  characterization: 7 passed before moving reset orchestration out of the facade.
- Full-path Ruff lint/format and mypy passed (1996 source files). Import checks
  passed all three gates with Windows `PYTHONUTF8=1`; no cycles or new allowlists.
- Tool/integration/harness/gateway API-border suite: 36 passed.
- Provider/shell seams, S8a target/reply/approval/document contracts, all packaging
  tests and investigation prompt separation: 135 passed.
- Final prompt/action/skill/default-target suite, normal and precomputed tool
  providers, real sequential/concurrent runner and gateway synchronization: 128 passed.
- Encoding-related Git/Sentry shipping regressions plus channel runner, complexity
  and Slack fan-out verification: 57 passed with `PYTHONUTF8=1`.
- Broad offline package runs were performed, excluding live_llm and the explicit
  live Sentry query: core 1997 passed / 3 failed / 2 xfailed / 16 deselected;
  integrations 3854 passed / 1 failed / 47 skipped / 2 deselected;
  gateway 1114 passed / 9 failed / 2 teardown errors;
  tools 3648 passed / 4 failed / 41 skipped / 5 deselected. These initial counts
  are not whole-package green claims. The S7 complexity failure was fixed; the
  Git/Sentry encoding failures passed under UTF-8. Remaining unrelated Windows
  limitations involve POSIX lock files/SIGKILL/mode bits, daemon signal behavior,
  subprocess cleanup and path-separator assertions. Do not alter their product
  code as part of S7; Linux PR CI remains the repository-wide gate.
- The gateway fan-out failure under concurrent local load was a real harness
  synchronization defect. Replaced the early five-second worker barrier with a
  coordinator that waits for all actor handlers, then releases them with bounded
  waits/joins and cleanup. The binding/isolation assertions remain intact.
- `uv build --wheel` produced `dist/opensre-0.1-py3-none-any.whl`. The exact wheel
  passed validate_wheel.py; an isolated extracted-wheel subprocess imported the
  packaged loader and read both adjacent Markdown bases without the source tree.
  No full PyInstaller binary build or release workflow activation was performed.

PR/check/review IDs will be added after delivery gates complete. S7 is not marked
delivered or merged yet. No live Feishu acceptance was run; S8a's real multipage
delivery limitation remains unchanged. S8b remains a separate future project.
