# 多尺度逻辑审查 · v0.4

本模块把论文表示为结构树、同尺度推进图和跨尺度实现/支撑图。它检查可追溯的论证结构、重要依赖和审查覆盖；科学结论的真实性仍依赖论文证据、领域审查和可插拔 scientific validator。

## 当前执行设计

1. **结构抽取**：读取主 TeX 与字面量 `input/include`，生成 PAPER、SECTION、PARAGRAPH、SENTENCE 单元及源码位置。结构归属只由程序确定。
2. **上层契约**：复用 Global Contract、Claim Ledger、Argument Graph、Hierarchy 和横向术语/符号 Registry。章节输出必须与 source_unit_id 一一对应。
3. **段落契约**：每段输出 purpose、rhetorical_role、inputs、outputs、main_claim、topic、Claim/Argument/Term/Notation IDs 和前后过渡。
4. **局部整体恢复**：章节摘要组恢复章节图；每节的段落组恢复段落图。模型一次处理完整局部上下文，输出各节点的 primary_parent_id、additional_parent_ids，以及最小有向逻辑图。允许分支、插入解释后的回接、多前提支撑。
5. **覆盖挑战**：复用 challenger，在独立调用中检查每个节点有没有漏掉父节点、错误挂接、缺失前提或遗漏多父支撑。输入不含恢复者的 rationale/confidence。
6. **跨层与远距离补检**：程序生成父子对齐、显式 TeX 引用、CORE Claim 星形邻接；可接收有数量上限的外部语义召回候选。不会生成全文两两组合。局部关系直接来自第 4/5 步，不重新让模型逐对分类。
7. **独立关系复核**：复用 verifier。输入为原文、候选关系及固定词表，不含 Mapper 的理由、置信度或所选证据片段。输出 PASS / FAIL / ALTERNATIVE / UNCERTAIN。关键关系要求反事实检查。
8. **自适应下钻**：章节缺口、段落风险、CORE Claim、推理连接词、强主张、公式、关系分歧等产生规则分值。默认阈值 40。进入风险段落后恢复整个句子图；`deep_review=true` 的关键句深审，`LOCAL_CONTEXT` 句子轻量检查。程序不把段落内每个句子与全部前文两两比较。
9. **自下而上回查**：独立终审调用从实际子单元输出重建段落、章节、全文贡献，再对照上层契约。不能用上层 PASS 代替原文证据。
10. **修改与重验**：旧关系保留在 run artifacts。受影响单元、祖先子树或 Claim 邻接的关系变为 STALE；内容、契约、术语和 ontology 指纹未变且证据仍匹配的 CONFIRMED 关系可复用。完整源快照和高层账本仍须刷新。

## 三种图及方向

| 图 | 方向 | 判断责任 |
|---|---|---|
| Structural Tree | parent `CONTAINS` child | 源码解析器 |
| Rhetorical Flow | 按该尺度 relation definition 指定方向 | 局部整体恢复 + 独立复核 |
| Realization / Support | child/evidence → parent goal/Claim | 原文支撑判断 + 独立复核 |

`primary_parent_id` 是同尺度论证挂接点；源码 `parent_id` 是结构归属。二者分别回答“这句话接着谁说”和“这句话属于哪个段落”，不得互换。`additional_parent_ids` 表示额外前提/支撑，结构归属仍唯一。

关系必须使用 `.review/logic_ontology_v1.json`。例如 SECTION 的 PREPARES、PARAGRAPH 的 RESULT_INTERPRETATION 和 SENTENCE 的 INFERENCE 各有适用尺度与定义。CROSS 可使用 REALIZES、SUPPORTS、DECOMPOSES 等。`NO_RELATION` 表示现有证据不足以建立该尺度中的语义关系；它不能满足关键支撑门槛。

## 原文证据与关系状态

每个关系保留 source_id、target_id、scale、reason、priority、Mapper/Verifier 的独立判断和调用 ID。两端证据必须给出 `unit_id/start/end/text`：offset 在单元文本内，程序会同时核对单元文本与原始 TeX 字符串。原始文件位置和单元起止 offset 见 structural_tree。

状态包括 PROPOSED、CONFIRMED、DISPUTED、UNCERTAIN、STALE、REMOVED。当前执行会复核全部稀疏候选边后再固化；普通分歧保留诊断，关键分歧需要重审。Mapper 与 Verifier 标签不一致时不会自动取多数、自动升级置信度或把关系当成既定事实。

## Gate 与诊断

| Gate | 必须满足 |
|---|---|
| G24 | 源文件清单、哈希及上层契约与结果一致 |
| G25 | 章节、段落、选中句子的输出数量/ID 与源清单一致；父链有效 |
| G26 | 路由可重新计算；粗尺度争议与下钻依据一致 |
| G27 | Bottom-up 覆盖完整，子先父后；关键语义问题没有被跳过 |
| G28 | 局部挂接无环/无未知节点；覆盖挑战完整；固定尺度标签与原文证据有效；关键关系独立确认 |

G17–G21 继续检查术语、符号、论证支撑、数据和跨位置 Claim 一致性。前提缺失、核心支撑缺失、scope 矛盾等按实质严重度进入 Issue；模型标签本身不被视为科学真理。

普通过渡生硬、topic jump、重复、逻辑回退和长距离认知负担属于诊断。`coherence_summary.json` 给出明确的计数、疑似孤立节点和争议 ID，不输出可替代科学判断的“87/100”。其中 orphan/transition 等语义指标都是基于已抽取图的发现，不是客观证明。

## 输出与操作

```text
.review/
├── coherence_registry.json          # 权威综合记录
├── logic_ontology_v1.json            # 固定关系词表
└── logic/
    ├── structural_tree.json         # CONTAINS + 原文位置
    ├── section_contracts.json       # 全文/章节契约
    ├── paragraph_roles.json         # 段落论证任务
    ├── local_graphs.json            # 局部 DAG + 主挂接/额外父节点 + 覆盖挑战
    ├── candidate_edges.json         # 稀疏候选与来源
    ├── rhetorical_graph.json        # 同尺度语义关系
    ├── realization_graph.json       # 跨尺度实现/支撑
    ├── critical_sentence_edges.json
    ├── relations.json               # 关系缓存、状态和独立复核
    ├── logic_disputes.json
    └── coherence_summary.json
```

```powershell
python scripts/control.py logic
python scripts/control.py logic --node NODE-ID
python scripts/coherence.py --inventory
python scripts/review.py validate --final
```

`control.py logic` 输出多尺度链、各层契约、同尺度挂接、语义边、风险选择、独立复核及 Gate 结果。旧源快照会明确显示失败 Gate，不能据旧 CURRENT 标签宣称通过。`runs/<run-id>/` 保存局部恢复、覆盖挑战、Mapper/Verifier 输入输出及风险计划；共享事件日志保存固化/失效事件。

## 抽取与成本边界

当前输入适配器是保守的 TeX 源码解析，不是完整 TeX 宏展开器。段落是空行/结构边界分隔的源码单元；公式、列表、caption 和命令保留原文，不能宣称它与排版后 PDF 的自然语言段落/句子完全一致。动态 include、条件结构、递归 include、可识别的结构生成宏会阻止覆盖认证。复杂文档需先提供可展开的标准结构；目前未实现 DOCX/PDF/任意宏包的通用结构恢复。

局部组只包含必要的父契约和原文；较大章节的远距离边上下文使用有上限的代表片段，并显式报告 omitted_unit_count。证据不足时须返回 UNCERTAIN。外部语义检索只是受限候选接口，目前没有默认 embedding 服务；纯排版编号引用和隐式远距离指代仍可能漏召回，不能宣称全文依赖已穷尽。

风险权重是可解释的调度规则。当前 coverage baseline 全文逐段，语义关系可增量复用；契约映射和独立终审仍会刷新，尚不是所有 Agent 均细粒度增量执行。审查成本取决于局部组大小和被选中的关系/关键句数量，不使用全连接比较。

模型内部 attention 不进入日志、评分或 Gate；可验证对象是模型显式恢复、绑定原文并被独立复核的外部逻辑图。

## 从已有论文 Harness 升级

更新 scripts、项目级 Agent/Skill 和 `.review/schemas`，新增 ontology 与缺失的 coherence_registry 模板，并合并 config 中的 coherence_policy。保留已有 Claims、Issues、Revision、Verification、requests、logs 和 runs；不要用本仓库的空模板覆盖真实论文账本。旧 v0.3 的 `CURRENT` 标志不足以通过新增 Gate，升级后需要启动一次新的分层审查来建立源 ID 和逻辑图。旧运行证据继续留存。
