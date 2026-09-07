# 本轮设计与输入溯源

本轮由用户要求“输出必须带结构化逻辑链条（多尺度）”启动，随后两份补充设计依次修订实现。最新补充优先，既有日志、独立验证、Humanizer、数据和 Claim Gate 继续保留。

| 输入 | 决策与落实 |
|---|---|
| 多尺度父子链与自适应下钻要求 | 新增确定性源单元 ID、综合 coherence registry、风险路由、反向审查、覆盖率 Gate |
| `8632cf2e-e41b-4fb2-9fc5-ff80d281ad83/pasted-text.txt`：科研论文多尺度逻辑审查模块设计说明 | 明确结构树/同尺度图/跨尺度图；版本化 ontology；证据 span；Proposal–Verification；硬门槛与软诊断；受影响关系失效 |
| `d6c51f0b-ae96-4d22-ac30-0aff9298c9f0/pasted-text.txt`：Sentence Attachment 与局部整体结构恢复 | 章节组/段落组/风险段落整体恢复局部 DAG；primary/additional parents；独立 coverage challenge；远距离调度器只补充稀疏候选，不在局部穷举 pair |

输入原件在用户本机 Codex attachments 中；本文件记录可公开理解的需求与决策，不复制本机对话原文或私有路径到操作 Skill。实现、测试和说明由同一次 Git 提交关联；运行时另有每次真实论文任务的原始输入、结构化输出、事件链和独立调用记录。

输入 SHA-256：

- `8632cf2e-e41b-4fb2-9fc5-ff80d281ad83`：`cf6ef04f4692c5be70047639ab72433108d8ac4dd03ac6b7b5adafd9ae5a9f57`
- `d6c51f0b-ae96-4d22-ac30-0aff9298c9f0`：`86278e3380d59331f5006fe92897befc8a2e775768fb3e775b42437a0d24f206`

### 实施范围

- 复用现有角色：hierarchy_reviewer、language_coherence_reviewer、argument_reviewer、challenger、verifier、final_integrity_auditor。没有增加重复 Reviewer。
- `scripts/coherence.py`：源清单、契约/覆盖验证、风险下钻、Bottom-up、G24–G27、链条查看。
- `scripts/local_recovery.py`：完整局部上下文恢复、主/额外挂接、DAG 校验、独立漏检挑战。
- `scripts/logic_graph.py`：稀疏补边、ontology、原文 span、独立关系复核、争议、关系级缓存/失效、G28 和诊断汇总。
- Schema、操作 Skill、README、Agent 输出协议及测试同步更新。

### 验证边界

自动测试使用明确标注的模拟 Agent 输出，验证工作流、覆盖约束、独立输入、证据一致性和失败门槛；不把模拟 PASS 当成真实论文审查结果。本轮没有消耗真实论文的 Codex 审查调用，也没有宣称通过科学正确性评测。实例 JSON 为结构演示，不是已验收论文。

标准 TeX 输入和规则风险召回已实现。默认 embedding 检索、复杂宏/PDF/DOCX 结构适配与全面的 Agent 增量调度仍是后续扩展，不纳入此次“已实现”能力。
