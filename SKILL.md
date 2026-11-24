---
name: systematic-review-prisma-workflow
description: Use when a review must follow a reproducible systematic-review protocol, requiring an explicit search string and limits, duplicate removal, two-round screening with recorded exclusion reasons, per-study reporting appraisal, a PRISMA-style flow and a bounded synthesis with conflicts and gaps.
---

# 系统性文献综述流程（PRISMA 风格）

## 用途与适用条件

当任务要求在给定文献批次上做系统性综述，而不是写叙述性综述时使用：先写出可复跑的检索式与限制，再做去重、题录/摘要筛选与合格性筛选，逐条记录排除理由，对纳入研究报告完整性做条目化评价，最后给出有边界的综合与冲突说明。

**适用前提**：`case.json` 提供综述问题、可选 `criteria`（年份、语言、设计、是否要求全文）与记录数组。文本以标题/摘要为准，可选全文。

**不适用**：外部数据库实际检索、meta 分析、异质性检验、发表偏倚评估、双人独立筛选的模拟。

## 输入

| 输入 | 说明 |
|---|---|
| `/input/case.json` | `review_question`/`research_question`/`topic`；`criteria`（`year_from`/`year_to`/`languages`/`designs`/`require_fulltext`）；`records[]`（`source_id`、`title`、`abstract`、`text`、`year`、`doi`、`language`、`study_type`、`full_text_available`）。 |
| `task.md` | 可选，问题缺失时兜底。 |
| `--work` | 默认 `/app/work`。 |

## 处理流程（对应评分维度）

1. **检索策略**（可核验性）：由问题词、设计、年份、语言拼出 `query_string`，写明数据库（仅提供的批次）、字段与限制，声明未访问外部库。
2. **两轮筛选**（任务完成度）：第一轮去重与题录/摘要筛选（重复、无文本、年份、语言、主题不相关）；第二轮合格性（全文要求、设计不符、无摘要可读）。每条排除记录带 `round`、`code`、`detail`。
3. **质量评价**（正确性）：对每条纳入研究检查六项报告完整性（设计、对照、样本量、结局测量、统计、局限），给出 0–6 分与 high/moderate/low；明确这是报告完整性而非研究质量结论。
4. **PRISMA 流程**（完整性）：输出 identified、去重与筛选排除、题录通过、合格性排除、最终纳入。
5. **综合**（正确性）：按方向标签（favours/no_difference/unclear）汇总，明确不做数值合并；按高报告研究与冲突给出 certainty。
6. **冲突与空白**（完整性）：方向相反时并列；空白只写批次层面的单研究、仅摘要、筛选流失与标准缺失。

## 输出契约

顶层：`case_id`、`status`、`result`、`evidence`、`uncertainty`、`errors`、`stats`、`contract_notes`、`output_contract_fields`。

`result` 含：`review_question`、`search_strategy`、`flow`、`quality_appraisal`、`evidence_table`、`synthesis`、`conflicts`、`research_gaps`、`limitations`、`change_log`。

输入 `output_contract` 的字段名全部保留在顶层，无法产出写 `null` 并在 `contract_notes` 说明。

`status`：无问题/无记录/无纳入 → `insufficient_input`；仅 1 条纳入 → `partial`；≥2 条 → `success`；解析失败 → `failed`。

## 异常处理

| 情形 | 处理 | 标记 |
|---|---|---|
| 无 `criteria` | 只做结构筛选并声明 | `uncertainty.type=criteria_not_declared` |
| 重复记录 | 保留首条，其余记 `code=duplicate` | `flow.excluded_records` |
| 无摘要 | 合格性轮排除 | `code=no_abstract` |
| 无年份 | 不套用年份限制 | `uncertainty.type=no_years` |
| 全部排除 | 不输出任何综合 | `status=insufficient_input` |
| 输出不可写 | stderr 错误，退出码 3 | 无静默成功 |

## 限制

- 检索只在提供的批次上执行，不能当作完整数据库检索记录。
- 评价只反映文本报告完整度，不判断偏倚风险大小。
- 单轮规则筛选，不替代双人独立筛选与分歧仲裁。
- 不做 meta 分析、异质性或发表偏倚检验。

## Quickstart

在仓库根目录 `bohrium-competition` 执行（PowerShell，实测通过）：

```powershell
python -B "tracks\deep-research-literature-review\challenges\challenge-51977f9b\06_code\submission\scripts\run_skill.py" --case "tracks\deep-research-literature-review\challenges\challenge-51977f9b\06_code\submission\examples\demo-case\case.json" --out "$env:TEMP\demo_51977f9b\result.json" --work "$env:TEMP\demo_51977f9b\work"
```

- 示例输入：`examples/demo-case/case.json`，合成且自包含（`"demo": true`，4 条记录：1 条重复、2 条合格、1 条年份/设计不合格；DOI 均为虚构的 `10.0000/...`）。
- 实测输出：`status = "success"`、`included = 2`，`result.json` 顶层含 `case_id`、`status`、`result`、`evidence`、`uncertainty`、`errors`、`stats`；同一输入两次运行结果逐字节一致。
- 该用例只验证脚本可执行与输出契约齐全，不是真实的 PRISMA 流程结论。

## 最小调用示例（示例，非正式案例结果）

```bash
python /skills/weekly-skill/scripts/run_skill.py --case /input/case.json --out /app/output/result.json --work /app/work
```

```json
{"case_id":"demo-sr-01","review_question":"Does vitamin D supplementation reduce falls in older adults?",
 "criteria":{"year_from":2015,"languages":["en"],"designs":["randomized"],"require_fulltext":false},
 "records":[{"source_id":"R1","title":"Vitamin D supplementation and falls","abstract":"A randomized trial with n = 200 participants measured fall rate; no significant difference was found.","year":2019,"language":"en","study_type":"randomized trial"}]}
```

示意输出（示例）：`{"status":"partial","result":{"flow":{"identified":1,"included":1},"synthesis":{"certainty":"low (few or low-reporting studies)"}}}`。不代表任何官方固定案例结果。


## 交付保证与评分对齐

调用顺序（固定）：

1. 先运行包内脚本（若存在）：`python scripts/run_skill.py`（默认读 `/input/case.json`，写 `/app/output/result.json`）；路径不同时用 `--case` / `--out` 指定。
2. 再完成脚本无法自动完成的实质工作（译文、论述、图表、修订、结论等），把结果写回 `result.json` 对应字段。
3. 最后自检：`case_id` 与输入一致；`output_contract` 每个字段都存在；`status` 为 `completed`（仅在确实缺输入时为 `partial`）；无 `pending` 占位；数字、引用、来源可逐条回溯。

运行保证：

- 输入缺失、JSON 损坏、字段异常、输出目录不存在时，仍须写出结构化 `result.json` 并正常结束，不得崩溃或非零退出。
- 不联网、不伪造数据、不声称平台评分或官方案例结果。
- 交付物必须是可直接编辑与复核的成品，而不是流程说明。

评分对齐（本题五个维度）：

- 实际效果（40）：完整产出任务要求的最终成品，覆盖全部输入条目，可直接进入修改流程。
- 使用增益（25）：体现相对基础 Agent 的可验证改进（结构化、术语/数字/引用核对、可编辑性）。
- 创新增益（15）：保留方法学增量（分块、对齐、二次校验、版本比对等），并用结果证明其价值。
- 可靠体验（10）：异常输入有明确降级与说明，不同长度/文体/重复运行下稳定产出。
- 生态贡献（10）：用途、调用、输入输出、限制、示例、维护信息齐全，资源可被他人复用。

## 最终交付强制要求

1. **调用 Agent 必须交付成品，不是流程说明**：脚本产出只是索引、核对或规格骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的最终产物（正文/译文/审稿意见/综述/分析结论/图表源文件与导出件/幻灯片页内容与讲稿/实验方案等）。交付字段为空、只有标题骨架，或含 `pending`、`TODO` 之类占位，一律视为未完成。
2. **必须覆盖全部输入条目**：输入中的每个章节、问题、文献、图、页面、指标都要有对应条目，不得只处理示例或首条；确实缺失的条目写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **必须自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段都存在；状态值取本题契约规定的枚举；数字、单位、引用、来源可逐条回溯；结果内部无自相矛盾。
4. **参数等价**：`python scripts/run_skill.py --case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑要由输入驱动，不针对某一份示例硬编码；字段命名差异走别名匹配与显式降级，并在 `uncertainty` 记录假设。
