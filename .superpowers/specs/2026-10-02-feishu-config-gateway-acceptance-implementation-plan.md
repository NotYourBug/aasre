# Feishu Configuration Resolution and Ordinary Gateway Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Feishu 纯身份策略记录遮蔽环境配置的问题，保持既有授权规则，并通过正常网关入口验证消息搜索、单条读取和卡片回答。

**Architecture:** Feishu 专属的纯策略记录识别放在 vendor 模块；catalog 的合并和来源元数据使用同一份经过筛选的配置记录。真实 store 与身份策略不迁移、不删除、不补写环境凭据；网关继续使用原启动、会话、授权、模型和工具调用链路。

**Tech Stack:** Python、现有 Pydantic、pytest/Click CliRunner、Ruff、mypy、uv、PowerShell、现有 Feishu SDK；无新依赖。

**Spec:** 用户于 2026-10-02 批准的会话内有限修复设计，冻结于本文 §1；诊断与路线背景见[能力路线](2026-09-12-feishu-capability-completion-design.md)及[S8b-2 已批准设计](2026-10-01-feishu-s8b2-message-search-design.md)。本项不另建架构设计。

**Status:** **2026-10-02 配置实现与验证已完成，两次获批窗口已结束，普通运行未验成；建议配置单独交付、运行验收延期，待用户决定。** 两次窗口授权均已撤销，不自动重试或扩预算；本项新限制尚未接受，合并尚未批准。执行步骤按实际证据更新。
**Baseline:** `d8598c2bfda86e1b858e283ce58ae604b1482b51`（PR #45 merge）；执行前重新核对 main。
**Worktree / branch:** `C:/Users/23033/Desktop/opensre2/.worktrees/feishu-s5b-feedback` / `codex/feishu-config-gateway-acceptance`。
**Execution method:** Native：主代理实施；完成后进行一次独立全分支评审，不新建工作树或用户会话。

## Global Constraints

- S8b-3 当前群成员列表、S8b-4 Agent 主动 reaction 工具已取消；保留 S5b／S5c 用户反馈、重试及 reaction 捷径。
- 只修复 Feishu 纯身份策略记录遮蔽环境配置；不全局改为 env 优先，不扩展到其他聊天平台。
- 符合例外的 store 记录须为 active、单个默认实例，凭据只有可验证的 `identity_policy`。含应用配置、目标授权、多个实例、非默认实例或停用状态的记录继续遵守原优先级。
- 身份策略继续从原 store 加载；不删除记录，不把 `.env` 凭据复制到 store，不改变配对、allow/revoke 或授权算法。
- 显式清空 `receive_id`、`allowed_outbound_targets` 的行为保持；不得恢复陈旧环境目标。
- 正常 bootstrap 已正确解析当前 `.env`；不改 env parser，不新增 env 变量。工作树通过现有 `OPENSRE_PROJECT_ENV_PATH` 指定原文件。
- 所有 Python 命令使用 `uv run python`；编辑使用 apply_patch；遵守适用 AGENTS、CI.md 与 PR 模板。
- vendor 逻辑归 `integrations/feishu/`；共享 catalog 只负责调用；facade 仅 imports/`__all__`。不扩大 import/API-border allowlist。
- 不提交凭据、真实消息 ID、正文、完整 SDK 响应、带令牌的 WebSocket URL 或敏感异常。报告只保留提交 SHA、阶段、匹配布尔值、计数和安全错误类别。
- 不改权限或发布应用，不部署远端服务，不删除测试消息，不启动其他 transport 或定时任务。
- 真实验收须完成预检、提供具体请求/模型/出站预算并取得当轮授权；旧只读窗口已结束，不能复用。合并另行明确批准。
- 上轮“只写计划”暂停已由本轮用户指令解除；常规实施及本地验证已授权，真实验收和合并门禁继续有效。不得提前勾选步骤。

## Review Focus

1. `messaging allow/pair` 写入纯策略后，普通 catalog 仍能解析 env 应用，且身份策略原样保留 — Tasks 1/2。
2. store 中空应用字段或空目标字段仍是显式配置，不能被当成“只有策略”而恢复 env 权限 — Task 1。
3. 重复记录、多个/非默认实例、停用及坏策略不能被宽松筛选掉；其他平台合并不变 — Task 1。
4. unbound 与 org-bound 各读自己的 store；显式 env 路径不覆盖已有进程 env，也不被隔离目录掩盖 — Task 2。
5. 普通网关有真实模型和出站副作用；全群读权限、其他 transport、自动重连或无法计数的 CLI 模型调用可能突破窗口 — Task 4，必须先证明限制可执行。

---

## 1. 已批准设计与诊断基线

批准的有限设计：保留 Feishu 身份策略，仅对 active、单个默认实例且只含有效 `identity_policy` 的记录保留环境配置参与解析；有应用配置、目标授权、多个实例或停用状态的记录保持原优先级。回归验证驱动真实身份策略持久化流程，再检查目录、凭据和授权边界。修复和本地检查完成后，准备普通网关验收，先启动/命令，再模型驱动搜索、读取和卡片回答。

2026-10-02 的零网络只读诊断事实：

- 主仓库 `.env` 存在；正常 bootstrap 后六个已检查字段与 dotenv 解析一致，未证明 parser 缺陷。
- unbound store 为 v2，含一条 active Feishu 记录、一个实例；唯一凭据字段是 `identity_policy`，策略有效。
- 默认有效 catalog 无 Feishu；显式空 store 的对照有 Feishu；真实凭据 leaf 回退后与 env 的应用、secret、chat 匹配。
- org-bound store 当时不存在，普通 catalog 能解析同一应用。以后该作用域也可能持久化策略，故回归要覆盖两个作用域。
- 干净工作树没有 `.env`，正常入口须显式指定已有文件。
- 未启动普通网关、模型或发送消息。实际 LLM provider/认证路径和现有网关进程是否存活尚待正确预检，不根据旧脚本的枚举或单一 pidfile 存在性作结论。

### 文件地图

| 文件 | 预定责任 |
| --- | --- |
| 新建 `integrations/feishu/catalog_policy.py` | 纯函数识别可作为策略记录的 Feishu 条目，产出用于凭据解析的 store 记录列表 |
| 修改 `integrations/feishu/__init__.py` | 仅导入并导出上述 helper；保持 import-light |
| 修改 `integrations/_catalog_impl.py` | 本地合并入口与 effective resolver 调用 helper；实际来源为 env 时标记 `local env` |
| 修改 `tests/integrations/test_feishu_catalog.py` | 合并边界、source、显式配置及输入不变回归 |
| 修改 `tests/cli/test_messaging.py` | 真实 allow 命令持久化后的 env catalog 回归 |
| 修改 `gateway/tests/feishu/test_settings.py` | 真实 store → catalog → credentials → settings 链路与作用域隔离回归 |
| 修改 `docs/messaging/feishu.mdx` | store 配置优先的操作提示；工作树读取既有 env 文件的命令 |
| 已改路线及本计划 | 取消决定、设计批准、实施/验收状态；不虚构完成项 |

不预定修改 `credentials.py`、`settings.py`、`identity_policy.py`、env parser、gateway worker 或模型 provider。若实现确需改变这些模块的行为，先说明根因与范围变化，更新设计再继续。

## Task 1: 修复纯身份策略记录的凭据合并

**Files:** 新建 `integrations/feishu/catalog_policy.py`；修改 Feishu facade、`integrations/_catalog_impl.py`、`tests/integrations/test_feishu_catalog.py`、`tests/cli/test_messaging.py`。

**Interfaces:**

- Produces: `feishu_credential_records(store_records: list[dict[str, Any]], env_records: list[dict[str, Any]]) -> list[dict[str, Any]]`，经 `integrations.feishu` 导出。
- Consumes: `MessagingIdentityPolicy.model_validate`、现有 Feishu classifier、现有 `merge_integrations_by_service` / `_service_metadata`。
- 公共 `merge_local_integrations`、`resolve_effective_integrations` 的签名不变。

- [x] **Step 1: 添加真实 CLI 持久化回归。** 在 `tests/cli/test_messaging.py` 添加 `test_feishu_allow_preserves_env_catalog_and_policy`，使用已有 `_isolated_store`，设置合成 env `cli_env` / `s_env` / `oc_env`，调用真实 `messaging allow -p feishu -u ou_allowed`，不用 mock catalog、credential leaf 或持久化函数。断言：

```python
assert result.exit_code == 0
before = _isolated_store.read_bytes()
effective = catalog.resolve_effective_integrations()
assert effective["feishu"]["config"]["app_id"] == "cli_env"
assert effective["feishu"]["config"]["app_secret"] == "s_env"
assert effective["feishu"]["source"] == "local env"
assert _isolated_store.read_bytes() == before
stored = get_integration("feishu")
assert stored["credentials"]["identity_policy"]["allowed_user_ids"] == ["ou_allowed"]
assert "app_id" not in stored["credentials"]
assert "app_secret" not in stored["credentials"]
```

- [x] **Step 2: 添加 catalog 边界回归。** 在 `test_feishu_catalog.py` 用命名 fixture/helper 生成 v2 记录。覆盖以下不同语义，断言公共 merge 的结果、effective 配置/source 及原输入相等；不为同一分支换字面量增加用例：

| 测试名 | 输入 / 精确断言 |
| --- | --- |
| `test_policy_only_store_preserves_env_config_without_mutation` | 一条 active/default/仅有效策略 + 完整 env；Feishu 等于 env 分类配置，source=`local env`，两份输入不变 |
| `test_explicit_store_config_and_empty_targets_still_win` | store=`cli_store`/`s_store`，目标两字段为 `""`，env 有陈旧目标；source=`local store`，两目标仍空，真实 leaf 也为空 |
| `test_explicit_blank_app_field_is_not_policy_only` | 在策略凭据中加入 `app_id=""`；store 仍覆盖 env，effective 无 Feishu |
| `test_ambiguous_or_invalid_policy_record_keeps_store_precedence` | 参数覆盖 inactive、坏策略（allowed_user_ids 为字符串）、非默认实例、两个实例、重复 Feishu 记录；公共 merge 与旧 later-group-wins 结果一致，不过滤任何相关记录 |
| `test_policy_record_without_configured_env_is_preserved` | env 无 Feishu 或缺少完整应用凭据；公共 merge 保留原策略记录，effective 不凭空获得完整应用 |
| `test_other_platform_policy_records_keep_existing_merge_semantics` | Telegram 纯策略 store + env；store 胜出，Feishu 例外不泛化 |
| `test_legacy_record_does_not_gain_new_fallback_semantics` | 显式传入无 instances 的 v1 原始记录；合并保留原行为；正常 store 加载仍由既有迁移负责 |

- [x] **Step 3: 取得 RED。** 执行：
  `uv run python -m pytest tests/cli/test_messaging.py::test_feishu_allow_preserves_env_catalog_and_policy tests/integrations/test_feishu_catalog.py -q`。
  预期新回归在修复前因 effective 缺少 Feishu 失败；已有优先级测试不得先被“修好”或跳过。
- [x] **Step 4: 实现纯 helper。** 只在完整、active 的 env Feishu 配置存在且 store 只有一条 Feishu 记录时考虑例外。该 store 记录须为 canonical v2、active、唯一 name=`default` 的实例，credentials 的键集合恰为 `{"identity_policy"}`，policy 是通过既有模型验证的 dict；额外顶层配置字段也不能被忽略。任何歧义、错误、显式配置字段均返回保留该记录的新列表。只改内存中的列表选择，不改任何 record、policy 或文件；不发网络请求，不吞并异常详情到日志。
- [x] **Step 5: 接入 catalog 和来源。** 公共本地 merge 使用 helper 的结果；effective resolver 每次只计算一次同一结果，然后用于合并和 `_service_metadata`。复用 generic later-group-wins，不重复筛选，不增加 vendor 算法到共享入口；不修改 generic merge 的契约。helper 经 facade 导出，不引入 catalog/SDK 到 facade boot path。
- [x] **Step 6: 取得 GREEN。** 重跑 Step 3 和现有 `test_catalog_multi_instance.py`、`test_catalog_secret_credential_resolve.py`、`test_catalog_silent_fallback_elimination.py`、`test_feishu_credentials.py`、`test_feishu_boot_path.py`。所有预期通过，真实身份策略文件字节不变。
- [x] **Step 7: 自审后提交。** 建议提交 `fix(feishu): preserve env config beside identity-only records`，只暂存本 task 的明确文件；不推送、不混入真实配置。

## Task 2: 验证正常配置链路并补操作文档

**Files:** 修改 `gateway/tests/feishu/test_settings.py`、`docs/messaging/feishu.mdx`；读取既有 `config/local_env.py` 与 `tests/config/test_local_env.py`，不改 parser。

**Interfaces:**

- Consumes: Task 1 的公共 catalog；原 `save_identity_policy(platform, record, policy)`、`load_chat_credentials_from_env()`、`load_feishu_gateway_settings()`、`bound_storage_scope(scope)`。
- Produces: 真实链路及隔离回归、明确的 env 路径用法；不新增启动 API。

- [x] **Step 1: 添加正常链路回归。** 新测试 `test_policy_only_store_and_env_resolve_through_gateway_settings` 在临时真实 store 上调用 `save_identity_policy("feishu", None, policy)`。用合成 env 设置应用、secret、chat、env 用户 `ou_env`；策略只允许 `ou_policy`。不用 stub resolver/settings/policy reader。断言目录、leaf、settings 的应用/secret 相等且 source 为 env；原策略允许 `ou_policy`，拒绝仅在 env 出现的 `ou_env` 和陌生用户；解析不写 store。再持久化该策略的 `inbound_enabled=False`，断言策略读取及原授权函数继续拒绝，应用配置仍可解析。不用空 allowlist 变体扩展修复既有授权算法。
- [x] **Step 2: 添加作用域隔离回归。** 新测试 `test_policy_only_resolution_respects_bound_org_store` 使用合成 `org_a` / `org_b` 和真实 `bound_storage_scope`。将 `config.constants.paths.OPENSRE_HOME_DIR` 定向到 tmp_path，清除 `integrations.store.STORE_PATH`、`OPENSRE_INTEGRATIONS_STORE_PATH`、`OPENSRE_CONTEXT_ROOT` override；在两个作用域写入不同用户策略，分别解析。断言各自只读各自策略、凭据均来自合成 env、unbound store 没被修改，退出绑定后 scope 恢复。禁止设置一个全局 store override 来伪造这个测试。
- [x] **Step 3: 运行 focused 回归。** 执行：
  `uv run python -m pytest gateway/tests/feishu/test_settings.py gateway/tests/feishu/test_inbound_security.py gateway/tests/runtime/test_identity_policy.py tests/cli/test_messaging.py tests/config/test_local_env.py -q`。
  预期全绿；本 task 只增加已修复链路的集成验证，产品 RED 证据来自 Task 1 的真实持久化回归。若出现必须调整产品代码的新失败，先取得对应 RED 并核对范围；与修复无关的旧授权缺陷单独披露，不扩大本 task。
- [x] **Step 4: 补用户文档。** 在 Feishu 配置段说明完整 setup/store 应用配置优先于环境配置，配对/allow 的身份策略不会阻止 env 部署；在启动段添加 PowerShell 示例：
  `$env:OPENSRE_PROJECT_ENV_PATH = 'C:/path/to/original/.env'`，随后 `uv run opensre gateway start --foreground`。说明路径指向已有文件，不要求复制密钥。内部 helper、根因历史和 vendor 端点只写 PR/工程记录。
- [x] **Step 5: 做零网络真实配置预检。** 对执行时的准确 HEAD，通过正常 bootstrap、默认 resolver、leaf、settings 核对既有 env 的应用/secret/chat 匹配及 default catalog 存在。分别核对 unbound 与实际 org 作用域；保留真实 store，不使用 `store_integrations=[]` 或显式 env records 作为成功证据。阻断外部网络；仅输出匹配布尔值、source、策略有效性、数量和安全错误类别，比较 env/store 前后摘要是否不变。默认 catalog / leaf / settings 不一致即停止，不启动网关。
- [x] **Step 6: 自审后提交。** 建议提交 `test(feishu): cover env policy resolution through gateway settings`；产品检查按 Task 3 完成后再推送。

## Task 3: PR 与 CI 闭环

**Files:** 已触及的实现、测试和文档；工程路线取消记录与本计划；使用 `.github/PULL_REQUEST_TEMPLATE.md`，不修改 workflow。

**Interfaces:** 消费 Tasks 1/2 的绿色本地结果；产出准确 HEAD 的可审查 PR、检查与审查证据。不产出合并。

- [x] **Step 1: 核对差异与本地规范。** 依 CI.md 检查意外文件、密钥及 diff；执行 `make lint`、`make format-check`、`make typecheck`。Windows 如 make 不可用，使用 Makefile 中的同一命令体：
  `uv run python -m ruff check bootstrap config core gateway integrations infrastructure surfaces tools tests/`；
  `uv run python -m ruff format --check bootstrap config core gateway integrations infrastructure surfaces tools tests/`；
  `uv run python -m mypy bootstrap config core gateway integrations infrastructure surfaces tools`。
- [x] **Step 2: 完成 scoped tests。** 依据执行时 `.github/ci/test_scope_rules.py` 核对所有改动；本项至少覆盖 Tasks 1/2 列出的 Feishu、generic catalog、CLI、gateway policy/settings、local-env 用例及 `tests/shared/test_integrations_api_border.py`。`integrations/` 匹配 broad target 时按 CI.md 使用 focused 文件/过滤；完整仓库套件交给 PR CI，不运行 test-cov。边界失败修正 import，不扩大 allowlist。
- [x] **Step 3: 准备并创建 PR。** 先核对 Native 实施结果并完成自审，按 PR 模板说明触发（env 部署后持久化身份策略）、前后行为、保留授权、focused 命令和安全 demo 输出；AI 披露由 agent 如实填写，不代替人类勾选其已审查。提交信息以最终范围为准，取消路线记录随同纳入。推送当前分支，创建 PR 并 attach_artifact；不合并。
- [x] **Step 4: 每次 push 后跟踪。** 执行 `gh pr checks --watch` 或准确 statusCheckRollup，等待全部 required checks 绿色；失败取 `gh run view <id> --log-failed`，修根因、focused 本地检查、推送、重新跟踪。
- [x] **Step 5: 完成审查。** 检查人类/自动评论和 unresolved threads；逐条验证、修复或说明并关闭。仅按 CONTRIBUTING 的现有方法触发 Greptile，运行中不重复触发，直至准确 HEAD 的 5/5、零未解决 actionable 线程。未经授权不向其他人发送消息；仓库明确要求的审查触发遵守其规则。
- [x] **Step 6: 进入现场准备门禁。** 报告准确 PR HEAD、本地/CI/Greptile 结果和限制，继续 Task 4 的零网络准备；获得当轮现场窗口授权前保持网关关闭。代码变更后需重新核对 HEAD 和预检，不能沿用旧窗口证据。

## Task 4: 普通飞书网关实际运行验收（两次窗口已结束，尚未验成）

**Files:** 不预定产品改动；执行时可编写 git 外的临时观测/预算脚本及安全结果摘要。它们不是已实现能力，不随 PR 提交。

**Interfaces:** 使用 `uv run opensre gateway start --foreground` 的正常 CLI composition、真实 TurnRunner/SessionAgentPool、现有模型 provider、实际 offered 工具和 Feishu 回包。产出现场证据及清理证明。

- [ ] **Step 1: 完成零网络启动预检。** 配置、作用域、身份、模型、进程和 transport 项已核对；第二次窗口证明临时写入被拒绝时没有覆盖真实能力探针的子进程路径，因此重新标记完整启动预检未完成。恢复前须补该路径的独立方案，不能以原 boot 成功替代。确认准确 HEAD、原 env 路径、应用/群/允许用户及 org 匹配；读取真正的 `GATEWAY_PID_FILE`（host root 下的 `gateway/gateway.pid`）并核对存活进程，不停止既有用户进程。解析实际模型 provider、认证、预加载、embedding/CLI 路径及 credits/vault 是否启用。
- [ ] **Step 2: 明确副作用并证明可执行预算。** 原 39 项守卫边界测试通过且现场成功拒绝子进程，但普通启动所需沙箱能力探针与零子进程预算冲突，当前方案不能继续标记可执行。恢复前先设计明确的探针进程/脚本/环境及预算，不增加无约束豁免。继续使用现有 scheduler/Sentry/analytics 子进程 knob，保留正常 registry、授权和模型；不通过替换 TRANSPORTS、handler/model 或注入 env records 宣称普通网关验收。
- [x] **Step 3: 预检额外入站。** 当前 worker 注释依赖 mention-only，而应用已开群读权限；把这一假设不匹配当作现场风险，不声称已证明自动启动 turn。窗口必须限制测试群/操作者/预定消息和最多一个模型 turn；额外入站、重试按钮或 reaction 导致另一 turn 时应在模型调用前拒绝并停止。若必须绕过正常授权/dispatch 才能保证预算，停止并提出独立行为修复设计；不暗中扩大本项。
- [x] **Step 4: 形成具体窗口并取得两次当轮授权。** 用户审阅后批准首次窗口，随后重新授权并要求复核必要性；两次失败后授权均已撤销。下表为已结束窗口的预算记录，不是第三次启动授权或提案：

| 项目 | 提议上限与条件 |
| --- | --- |
| 普通网关进程 | 1 个前台实例；本地运行，窗口结束停止；单一连接，不自动重开窗口 |
| 用户消息 | 原测试群同一已允许用户，先一次 @bot `/help`，再一条固定测试 prompt；不要求第二名用户 |
| 模型 turn / LLM 尝试 | 至多 1 个模型 turn、6 次实际 provider 请求尝试（含重试、辅助/embedding 路径）；若 CLI 内部请求无法计数，先不批准该模型阶段 |
| Feishu token / 启动读取 | token POST 最多 5 次；bot identity 读取最多 1 次；WS endpoint 请求最多 1 次；必要连接心跳单独统计 |
| 消息读取 | 历史 GET 最多 6 次、发现 ID 后单条 GET 最多 1 次；不重试业务请求 |
| 出站 | 最多 60 次消息/CardKit 写请求，全部仅用于该群的 help/回答及其流式更新；包括失败尝试，不允许 Agent 主动 send/reply、reaction 写或删除 |
| 其他网络 | 原草案 health 3 次现收紧为 0，保留未验标记；实际窗口补全 DeepSeek/Feishu、WS 及 DNS/TCP 预算，credits/vault 等为 0；未列目标拒绝。详见[当轮现场窗口](2026-10-02-feishu-gateway-live-window.md) |

预算须在底层请求发出前检查，失败、超额、超时、身份/配置不一致或意外外部请求即停止并关闭本轮；已发出的请求无法撤销，需如实记录。SDK 内置重试/重连也属于尝试次数。若现有路径无法实现这些守卫，则只报告预检阻塞，不把未经约束的运行当成验收。

- [ ] **Step 5: 阶段 A 启动和命令。** 正常 bootstrap 从原 env 读取，运行实际 CLI gateway；确认 Feishu 连接 ready、监督器已启用、scheduler 已关闭及其他 transport 未连接。本地 health 按当轮窗口的 0 次预算延期，保持未验，不发送 health 请求。请用户在测试群 @bot 发 `/help`；确认一次普通命令回复，模型/历史 GET 计数均为 0。仅 ready 日志不算通过。
- [ ] **Step 6: 阶段 B 一个模型驱动读消息 turn。** 预检通过后给用户一条包含当轮唯一标记的完整 prompt：声明标记 `FEISHU_CONFIG_VERIFY_<UTC秒>_<nonce>`，请在当前聊天过去 5 分钟内搜索该标记、读取最新匹配 ID，再给一句简短结果，不运行其他工具或额外发送。用户只发送这一条并 @bot；其文本同时是待发现的测试消息，避免额外普通群消息意外启动 turn。观测实际 offered 的 `feishu_search_messages` 和 `feishu_get_message`、真实调用次序与相同 ID 匹配、最终卡片及流式完成状态。不用临时执行器直接调用这两个工具来替代 Agent 路径；若模型没按要求调用，报告链路未验成，窗口内不追加新 turn。
- [x] **Step 7: 两次尝试清理并回写证据。** 首次在授权解码阶段退出 78；第二次在能力探针的子进程创建前被拒绝，退出 1。两次临时授权均撤销，本机消费者/端口无残留，原 env/store 未变。第二次模型/Feishu/API/WS/入站计数均 0，未建立飞书连接；不 kill 无关进程或删除聊天消息。
- [ ] **Step 8: 处理结果。** 普通入口配置 → 授权入站 → 模型 offered/call → 搜索/读取同一 ID → 卡片回答 → 清理全部成功，才标记本轮通过。分页、跨聊天/操作者、post/卡片搜索及 S8a 多页投递未在本轮完成的继续记录为未验，不自动解除旧限制。失败定位根因；产品修复按 scoped checks 和 PR/CI 闭环，权限或预算不足停在窗口边界，不自行提权或重试。

## Task 5: 交付决定与合并后验证（另行批准）

**Files:** 更新 PR 描述及工程状态记录，不新增范围。

**Interfaces:** 消费准确 HEAD 的 CI/Greptile 和 Task 4 现场结果；仅在用户明确批准合并后产出 merge SHA 与 post-merge 结果。

- [x] **Step 1: 汇报可审查结果。** 已提供配置回归/CI、两次实际窗口失败和清理证据，提出配置修复单独交付、普通运行延期的可审阅安排；普通网关未通过。限制是否接受、是否补验和是否合并仍由用户分别决定，本计划不预先接受。
- [ ] **Step 2: 获得合并批准后复核并合并。** 再确认准确 HEAD 绿色、Greptile 5/5、零未解决线程；新增变更后重做对应门禁。不把设计批准、执行批准或现场窗口批准当成合并批准。
- [ ] **Step 3: 跟踪准确 merge SHA。** 等 main CI、完整 Python/JavaScript CodeQL 和 release workflow；Release 按 guard 跳过可记 skipped，不能写已发布；新增 CodeQL 告警须修复，不清理无关旧告警。
- [ ] **Step 4: 闭环失败并回写。** 合并后失败必须修复或回滚并复验；最终文档说明配置修复、现场结果与限制、准确 merge SHA 和检查链接。取消项保持取消，不新增 S8b-5 来包装后续可靠性工作。

## 2. 计划自审与授权记录

已对照批准的设计检查：窄范围匹配、完整 store 优先、显式空授权保留、身份策略不变、作用域、来源元数据、import-light、正常入口及现场/合并授权均有对应步骤；无新依赖/env 或共享运行时接口、无成员或 Agent reaction 实现。

上一轮仅新增本计划并记录设计批准，未执行产品实现、测试、提交、push、PR 或网关。2026-10-02 用户明确恢复执行，选择 Native，并授权完成后一次独立整分支评审；真实窗口及合并仍单独批准。恢复时核对工作树、分支和三份既有文档改动相符；刷新远端后 `origin/main` 与 HEAD 均为 `d8598c2bfda86e1b858e283ce58ae604b1482b51`，从 Task 1 开始，无既有完成步骤。

## 3. 本轮实施与验证证据

- Task 1 产品提交：`e747a44e081a69d222794b60ec30862a91b78d8a`。真实 CLI allow 和公开 merge 回归先取得 2 failed / 22 passed；最小修复后与 generic catalog、credentials、import-light 回归共 128 passed。
- Task 2 提交：`d7ec0e86a4b7ff61fe3fb82b48b2ff270cad959f`。真实身份策略持久化、gateway settings、org_a/org_b/unbound 隔离与既有 local-env 回归 45 passed；文档补 store 应用优先及 PowerShell 原 env 路径。
- Task 3 本地规范：完整 Ruff lint / format-check 成功；mypy 成功（2015 源文件，仅原有 unused-section 提示）。按实际 PathRule 的 broad integrations/gateway target 选择 Feishu、generic catalog、CLI/local-env、policy 和 import-border 文件，695 passed（39.77s），无 skipped；完整仓库测试仍交给 PR CI。
- `e747a44` 零网络真实配置检查：正常 CLI env bootstrap 的六个 Feishu 字段与 dotenv 一致；unbound 真实 v2 store 有一个 active/default 纯有效策略记录，实际 org store 不存在。两个作用域默认 catalog/leaf/settings 的应用、secret、chat 匹配原 env，source=`local env`，一个选定用户获准、陌生用户拒绝，env/store 摘要未变。未注入 env records 或空 store。
- 实际模型解析为 DeepSeek，三档均 `deepseek-flash`，API key 可用，preload 模块导入通过；未构造请求或调用模型。普通启动预检和预算守卫继续准备，不能据此宣称普通网关已验收。
- 探针禁止外部 socket/DNS、子进程和文件写入：外部网络及子进程尝试均 0，20 个目录/写入尝试被拒绝。Windows SDK 导入所需 stdlib socketpair 的精确 loopback listener tuple 仅作为进程内事件循环 IPC 放行一次；未连接飞书。
- 执行裁定：CI.md 与用户指令优先于技能默认全套本地测试，采用 scoped tests 并以 PR 全仓 CI 闭环；Windows bash bookkeeping helper 不兼容本机路径，改用本机 ledger 与逐任务 BASE/输出记录，已停止自己的 helper，未动用户进程。若判断错误，分别由 PR CI 和计划/提交对照捕获。

独立整分支评审已完成（base `d8598c2` 至 `adc0106`，只读）。一个 Important：带空白的 service 名称不符合身份策略 reader 的精确名称契约；一个 Minor：筛选 helper 调用分类器时可能触发上报副作用。两项均有独立回归 RED（2 failed），已收紧 canonical service 并提取纯校验；25 项 catalog focused GREEN，最终 scoped suite 697 passed（25.92s），完整 lint/format/typecheck 重新通过。无延期 Minor；授权算法和 worker 保持不变。评审未判断的现场预算与既有 leaf/空 allowlist/重复身份策略行为仍不据此宣称修复。

PR [#46](https://github.com/NotYourBug/aasre/pull/46) 已创建、推送并 attach_artifact。产品 HEAD `759e056e6467c8dacda30ab076581d5ebffefd48` 的检查为 30 success、7 skipped；Greptile 重审 5/5、零未解决线程。唯一 P2 乱码反馈经 UTF-8 源文件及 Git 内容核对为渲染问题，已回复证据并关闭；没有改写取消决定。文档更新每次 push 后仍须跟踪新准确 HEAD，不以此旧结果替代。

用户随后明确批准[临时监督器设计](2026-10-02-feishu-gateway-window-guard-proposal.md)的零网络实现与验证。完整守卫 39 项离线回归通过（贯穿进程退出的网络否决记录 external attempts 0），临时脚本 Ruff lint/format 通过。一次较早回归曾出现 Sentry 待发送提示，未确认是否发出；该次不用作零网络证据，最终套件在启动前关闭遥测并设置进程级外部 socket/DNS/子进程拒绝后重跑。无真实 Feishu API、模型或外部消息调用。

真实配置监督器预检安装完整包装后，正常 CLI/GATEWAY_PROFILE boot（不启动服务）通过，live_enabled=false、当轮授权文件不存在，env/store 摘要不变；27 次写入尝试被拒绝、stdlib socketpair IPC 1 次、外部请求尝试 0。normal `uv run opensre --version` 的无授权拒绝演练得到退出码 78，CLI 未继续运行。预检同时固定 HEAD、env/store、脚本和依赖摘要；Windows PID 只读检查使用现有 psutil，避免 POSIX liveness helper 的不适用接口，不改产品进程管理。

[具体当轮窗口](2026-10-02-feishu-gateway-live-window.md)已经形成：最多 900 秒、1 turn/6 model HTTP、history 6/get 1、消息/CardKit 写 60、独立 WS/DNS/TCP 上限、计量/vault/health 为 0、关闭后清理最多 30 秒。普通 Web 仍绑定 0.0.0.0，额外 HTTP 入站拒绝，health 继续未验。在首次批准前，Task 4 Step 4 授权及 Steps 5–8、Task 5 均未完成；这段为窗口提交时记录，后续实际结果见下文，不能声称现场通过。

首次窗口随后获用户明确批准。准确 HEAD `fd96db7066ea4ed1c877fceaa2db214b0897e9ed` 的 CI 为 29 success / 7 skipped，Greptile 5/5、零未解决线程；刷新原 env/store 和依赖指纹一致，本机消费者和端口为 0。2026-10-02 09:15:27 UTC 建立 900 秒一次性授权，正常 CLI 约 1.9 秒后在授权 JSON 解码时拒绝，退出 78，尚未进入 `prepare`、gateway composition 或请求计数阶段。没有让用户发送测试消息；09:17:09 UTC 核对授权撤销、消费者/8000 监听为 0，原 stale PID 不存活。授权撤销后的原指纹相同，env/store 未变。

根因是 Windows `opensre.exe` 的 cp936 默认编码与 UTF-8 中文授权记录不一致，临时监督器显式 UTF-8 读取后修复；产品未改。真实 Windows console-script 时机的完整离线 `prepare` 通过（网络闸保持关闭并在 CLI 前退出）；编码回归取得 RED→GREEN，39 项离线守卫测试通过（18.03s），进程退出 external attempts 0，临时脚本 lint/format 通过。Task 4 Step 4 的首次授权和 Step 7 的首次清理完成，Steps 5/6 仍未通过，Step 8 继续等待新窗口的真实结果，Task 5 未完成。再次运行须刷新新 HEAD/指纹并单独授权，预算和行为不扩展；首次现场及合并授权均不可复用。

用户随后重新授权并要求复核必要性。配置选择修复已有充分回归/CI 证据；剩余普通入口、真实会话/授权、实际模型 offered search/get 和卡片组合仍缺真实证明，因此只再尝试一次，约定若无法进入该链则停止当前现场方案并提出拆分交付。

第二次在准确 HEAD `4479363`、CI 29 success / 7 skipped、Greptile 5/5/零未解决线程及原配置指纹一致后，于 2026-10-02 22:12:20（Asia/Shanghai）建立授权。普通 CLI 进入 GATEWAY_PROFILE 能力探针，沙箱 Python 子进程创建被预算 0 在发起前拒绝，退出 1，原因为 `unscoped_network_or_subprocess`。模型/turn、所有 Feishu/API/出站、DNS/TCP/WS 和入站计数均 0，stdlib IPC 1；env/store 不变。22:14:13 核对授权撤销、消费者/Docker/WSL/8000 监听为 0，旧 PID 不存活，没有通知用户发消息或自行重试。

原零网络预检拒绝临时文件写入，能力探针因此提前返回不可用；原非致命捕获让 boot 继续，未触及实际 subprocess。现场允许必要临时写入后触及该路径，暴露预检覆盖不足与普通启动/零子进程预算冲突。现停止当前方案，不增加豁免或修改共享启动。建议 PR #46 先作为有限配置修复独立交付候选，Task 4 普通运行保留未验；若继续须独立设计能力探针子进程预算和完整预检，再单独窗口授权。Task 4 Steps 5/6/8 和 Task 5 的限制/合并决定仍未完成，详见[可审阅交付安排](2026-10-02-feishu-gateway-live-window.md)。这只是建议，用户尚未接受本项新限制或批准合并。
