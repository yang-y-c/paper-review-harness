# Paper Review Harness

一个可复制到 LaTeX 论文仓库根目录的 Codex 原生质量控制系统。它把论文工作从“模型觉得改好了”改成一条有状态、可验证的链：

```text
Global Contract -> Section/Subsection Hierarchy -> Selected Granularity
                -> Claim Review -> Revision -> Independent Verification
                -> Final Integrity Audit -> Deterministic Gates
```

Agent 负责判断如何讨论、审查和修改；Python harness 负责状态转换、结构校验和是否允许验收。

## 系统能力

- 13 个项目级 custom agents：自然语言 `intake_coordinator`，全篇 `macro_architect`，章节层 `hierarchy_reviewer`，强制使用 `$humanizer` 的 `language_coherence_reviewer`，终审 `final_integrity_auditor`，以及 Claim mapper、4 个领域 reviewer、challenger、reviser、verifier。
- 5 种工作模式：讨论、审查、修改、验证、优化；另有完整闭环模式。
- Claim / Issue / Revision / Verification 四类机器可读账本。
- 基于 JSON Schema 的输出和账本校验。
- Reviewer 只读；只有 Revision 阶段允许修改论文；修改者不能关闭 Issue。
- 强主张动态进入 challenger；修改只触发受影响 claim 及其依赖项的验证。
- `Stop`、`SubagentStop` 和写入策略 hooks；即使 hooks 未启用，Python 状态机仍独立执行相同硬规则。
- 可选 LaTeX 编译、未解析引用/文献检查，以及干净快照工具。
- `$paper-review-operator` 控制面：接收普通自然语言，自动生成规范 Request，并负责引导、准入、启动、查看、溯源和监控。
- 15 项程序化准入检查；修改型工作未确认、粒度未选择、Humanizer 缺失、证据政策不安全、配置不完整或已有冲突任务时不会启动。
- 追加式哈希链审计日志、敏感字段脱敏、每次对话的 JSON/Markdown 溯源文档。
- 每个 Agent 调用均保存结构化输入、结构化输出、Codex JSONL、stderr 与调用清单。
- 全篇宏观契约、章节树、细粒度检查和最终审查分别固化到四个独立 JSON 账本，细节修改必须引用上层约束。

## 放入论文仓库

把本目录中的这些路径复制到目标论文仓库根目录；如已有 `AGENTS.md`，请合并其中的 Paper Review Harness 段落，不要覆盖原有规则：

```text
AGENTS.md
.codex/
.agents/
.review/
scripts/
```

然后修改 `.review/config.json`，至少设置 `main_tex`、`manuscript_roots` 和需要时的 `build_command`。默认假设主文件是 `manuscript/main.tex`。

安装运行时依赖（不需要改目标仓库已有的 `pyproject.toml`）：

```powershell
python -m pip install "jsonschema>=4.21"
```

在仓库根目录检查配置和控制面：

```powershell
python scripts/review.py status
python scripts/review.py validate
python scripts/review.py route
python scripts/control.py status
python scripts/control.py verify-log
```

## 使用方式

交互式 Codex 中直接用自然语言即可，不需要自己整理 JSON：

```text
使用 $paper-review-operator 讨论 C07 的全局最优性主张，不要改论文。
使用 $paper-review-operator 对论文做完整独立审查，先告诉我准入结果。
使用 $paper-review-operator 修复所有 BLOCKER 和 CORE Claim 上的 MAJOR，然后独立验证；修改前让我确认归一化范围。
```

控制面会先把自然语言写成 `.review/requests/REQ-*.json`。命令行等价操作如下：

```powershell
python scripts/control.py intake --text "完整审查论文，不修改正文"
python scripts/control.py admit REQ-ID
python scripts/control.py start REQ-ID --dry-run
python scripts/control.py start REQ-ID
```

`REVISE`、`OPTIMIZE`、`FULL` 必须在规范化摘要经用户确认后执行：

```powershell
python scripts/control.py confirm REQ-ID
python scripts/control.py admit REQ-ID
python scripts/control.py start REQ-ID
```

讨论模式会在 `.review/runs/<run-id>/artifacts/discussion-packet.json` 写入结构化讨论包，按 Claim 汇总已有证据、argument findings、最强挑战和作者决策点；它不会替作者选择科学结论。

底层 `review.py run` 也强制要求一个已通过准入的 Request，不能作为绕过入口：

```powershell
python scripts/review.py run --mode review --request REQ-ID --dry-run
```

完整模式会在达到 `max_rounds` 后以失败结束，不会把未收敛状态当成通过。

## 分层把控与审查粒度

对于 `REVIEW`、`OPTIMIZE` 和 `FULL`，系统不会直接钻进句子。它先冻结全篇契约，再逐层向下：

```text
global_contract.json
  -> structure.json
      -> granular_review.json
          -> Claim/Issue/Revision/Verification ledgers
              -> final_audit.json
```

`global_contract.json` 固化题目、摘要、全部章节命名规则、结论、核心逻辑链、主题锚点、规范术语、符号表和防漂移规则。`structure.json` 为每个章节/小节记录目的、前序输入、后序输出、父节点、主题锚点和 Claim。`granular_review.json` 保存选定粒度下每个单元的目的、衔接、术语、符号和语言问题。`final_audit.json` 独立复核题目、摘要、章节名、结论、逻辑链、术语、符号、层级、语言与 Claim 范围。

如果用户没有明确审查深度，Skill 必须先询问：宏观、章节、小节、逐段、逐句或自适应。自适应模式逐段审查全文，只对标题、摘要、结论、Core Claims、关键衔接、定义/符号边界和高风险单元逐句检查。粒度未明确时，准入门 `A14` 拒绝执行。

语言与段落逻辑由 `language_coherence_reviewer` 强制加载 `$humanizer`。它只在上层契约内处理表达：不得改变事实、数据、引用、公式、符号含义、限定条件或 Claim 范围。缺少该 Skill 时，`A15` 拒绝分层任务。

## 结构化输入输出与准入

自然语言先由 `intake_coordinator` 按 `request-draft.schema.json` 生成草案，控制器再补齐来源哈希、会话/轮次、Request ID、时间与确认状态，并按 `request.schema.json` 校验。用户不需要理解这些字段。

`admit` 每次重新计算准入结果，写入 `.review/admission/<request-id>.json`。检查覆盖：Request Schema、可执行意图、主论文存在、目标与成功标准、缺失输入、修改确认、修改许可、禁止伪造证据、约束冲突、Agent/Schema 完整、并发任务冲突、轮数边界、明确的审查粒度、Humanizer 可用性，以及完整模式所需的编译配置。任一 `FAIL` 都会阻止执行。

每次 Agent 调用的产物结构为：

```text
.review/runs/<run-id>/
  run.json
  agents/<invocation-id>/
    input.json
    output.json
    events.jsonl
    stderr.log
    invocation.json
```

`input.json` 包含 assignment、角色配置/Schema 的路径与 SHA-256，以及脱敏后的执行参数；`output.json` 必须通过对应 JSON Schema。

## 日志、溯源与监控

`.review/logs/events.jsonl` 是追加式审计流。每条记录包含顺序号、前一事件哈希、本事件哈希、原始载荷哈希、时间、Actor、阶段、Request/Run/Session/Turn 标识和产物引用。密钥、Token、Authorization、Cookie、密码等字段在落盘前脱敏；`verify-log` 可检测内容、顺序和链关系是否被改写。

```powershell
python scripts/control.py status
python scripts/control.py monitor --once
python scripts/control.py monitor --interval 5 --timeout 300
python scripts/control.py verify-log
python scripts/control.py trace --session SESSION-ID
```

`trace` 会生成 `.review/provenance/conversations/<session-id>.json` 与同名 `.md`。JSON 供程序读取，Markdown 供作者审阅；两者包含时间线、Request/Run 关联、事件链头和当前产物哈希。Codex 原始 transcript 路径只作为参考，不被解析成稳定事实来源。

## Hooks 与信任

项目 hooks 定义在 `.codex/hooks.json`。首次使用或脚本变化后，Codex 会要求审核并信任 hook；在 CLI 中使用 `/hooks` 完成检查。Hooks 是 session 级守门员，不是唯一执行边界：reviewer 的 `read-only` sandbox 和 Python harness 仍是主要保证。

`Stop` hook 只在 `.review/state.json` 的 `active` 与 `stop_gate_enabled` 都为 `true` 时生效，并有有限 continuation 次数，避免把普通 Codex 会话锁死。完整自动循环由 `review.py` 的 `max_rounds` 管理。

## 验收含义

`python scripts/validators.py --final` 返回 0 表示机器 Gate 通过；正式验收使用 `python scripts/review.py validate --final`，它会在通过后把状态推进到 `ACCEPT`。机器 Gate 证明的是已编码的不变量，例如账本合法、严重 Issue 已独立验证、强主张完成对抗审查、动态 reviewer 覆盖完整、LaTeX 编译无未解析引用；它不等于数学真理或实验真实性已经被形式证明。缺失的科学证据必须标记为 `NEEDS_AUTHOR`，不能由 Agent 编造。

## 扩展边界

控制面和科学工作流分离：`paper-review-operator` 管理用户意图与运行治理，`paper-review` 管理 Claim → Issue → Revision → Verification。未来可新增 defender / judge、presentation / PDF / fresh reviewer，但不应绕过 Request、Admission、Audit 或独立验证状态转换。
