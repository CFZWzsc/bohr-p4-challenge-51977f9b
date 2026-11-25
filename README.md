# Systematic Review Skill（PRISMA 风格）

## 用途

把给定的一批文献记录与材料文件，转成一条可复核的系统性综述流程：可执行检索式、E1–E5 条款化纳排、两轮逐条筛选台账、四档质量评价、PRISMA 计数、有界综合、冲突与证据性空白。所有证据都带 `locator`，优先定位到材料段落（`S3#P7`），其次退回题录字段。

适用于：给定 `case.json`（研究问题 + 题录批次）与可选材料目录（md/txt/csv/json/pdf）的系统筛选、质量评价与综述。

不适用于：联网数据库检索、meta 分析、异质性检验、发表偏倚评估、双人独立筛选仲裁。

## 调用

在容器内（输入 `/input`，输出 `/app/output`）：

```bash
python /skills/weekly-skill/scripts/run_skill.py \
  --case /input/case.json \
  --work /app/work \
  --out /app/output/result.json
```

在本包目录内自测：

```bash
python -B scripts/run_skill.py \
  --case examples/demo-case/case.json \
  --work "$TMPDIR/demo-work" \
  --out "$TMPDIR/demo-out/result.json"
```

仅依赖 Python 3 标准库。`pdftotext` 存在时优先使用；否则用内置 zlib 流解析 PDF；仍无文本时把该材料标为 `unparsed`，不中断运行。

## 输入 / 输出

输入：

- `case.json`：`review_question`（或 `research_question`/`topic`）、可选 `criteria`（`year_from`/`year_to`/`languages`/`designs`/`require_fulltext`）、`records[]`（`source_id`、`title`、`abstract`、`authors`、`year`、`doi`、`language`、`study_type`、`text`）。
- 材料文件：与 `case.json` 同目录（或递归子目录）的 `.md` `.txt` `.csv` `.json` `.pdf`，按相对路径排序编号为 `S1..Sn`。
- `task.md`：可选，问题缺失时兜底。

输出（`--out` 所在目录）：

| 文件 | 内容 |
|---|---|
| `result.json` | 契约化主结果：`case_id`、`status`、`result`、`evidence`、`uncertainty`、`errors`、`stats` 等 |
| `report.md` | 协议版本、PRISMA 计数表、纳排条款表、逐条筛选、研究清单、冲突、空白、材料清单、限制 |
| `evidence_matrix.csv` | 纳入研究的证据行：来源、题名、年份、设计、方向、质量等级、材料号、引句、locator |
| `screening_log.csv` | 每条记录一行：阶段、结论、条款 id、理由码、说明、引句、locator |

`result.result` 的主要键：`protocol`、`human_edits`、`search_strategy`、`criteria`（E1–E5）、`flow`、`flow_assertion`、`screening_log`、`quality_appraisal`、`outcome_vocabulary`、`evidence_table`、`materials`、`synthesis`、`conflicts`、`research_gaps`、`artifacts`、`limitations`、`change_log`。

`status`：无问题/无记录/无纳入 → `insufficient_input`；仅 1 条纳入 → `partial`；≥2 条 → `success`；解析失败 → `failed`。

## 限制

- 检索只在提供的批次上执行，`hits_per_query` 是批次内命中，不是数据库检索结果。
- 质量评价衡量“报告完整度”，不是偏倚风险；`yes` 需全文段落命中，`partly` 仅题录层命中。
- 单审阅人、规则化筛选；不模拟双人独立筛选与分歧仲裁。
- 不做数值合并、异质性或发表偏倚检验。
- 无匹配材料的记录只能按摘要层评价，并在 `research_gaps` 与 `uncertainty` 中显式说明。
- 同一输入两次运行 `result.json` 逐字节一致；不读系统时钟、不联网、不写绝对主机路径。

## 示例

`examples/demo-case/case.json` 为合成、自包含用例（4 条记录：1 条重复、1 条年份不合格、2 条纳入）。

实测（同一命令连跑两次）：`status = "success"`，`included = 2`，
`flow = {identified: 4, duplicates_removed: 1, screened: 3, title_abstract_excluded: 1, title_abstract_passed: 2, eligibility_excluded: 0, included: 2, pending: 0}`，
闭合校验 3/3 通过，产物为 `result.json`、`report.md`、`evidence_matrix.csv`、`screening_log.csv`。该结果仅验证接口与流程，不代表任何官方案例结论。

## 维护

- 检索式 / 条款 / 筛选规则的版本号在 `result.protocol` 与 `scripts/run_skill.py` 的 `PROTOCOL` 中同步维护。
- 质量量表条目在 `assets/policy.json`；缺失时回退到脚本内置默认值。
- 理由码在 `assets/reason_codes.json`；新增代码时同时给出 `stage` 与 `criteria_id`。
- 修改脚本后运行：`python -m py_compile scripts/run_skill.py`，再用 demo 连跑两次比较 `result.json` 字节一致。
- 包内文件上限：50 个文本文件、单文件 500KB、合计 2MB，无符号链接。
