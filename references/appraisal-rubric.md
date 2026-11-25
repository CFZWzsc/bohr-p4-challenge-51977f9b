# Appraisal rubric

Appraisal measures whether the supplied text *reports* an item. It is not a risk-of-bias judgement
and cannot be read as "high quality" or "low quality" on its own.

Each appraisal item returns:

| field | meaning |
|---|---|
| `item` | rubric item name |
| `judgment` | `yes` (hit in a full-text paragraph), `partly` (hit only in title/abstract), `no` (no hit), `unclear` (no readable text) |
| `basis.quote` | the first matching sentence, truncated to 320 characters |
| `basis.locator` | `S3#P7` for a material paragraph, `<case path>#abstract` or `#title` for the record layer |
| `note` | why the judgment was assigned |
| `value` / `reported` | backward-compatible mirror: `reported` iff judgment is `yes` or `partly` |

`no` means no sentence matched the pattern in the supplied text — not that the study failed to do it.

## Items

| item | what a hit looks like |
|---|---|
| `design_stated` | randomised, cohort, case-control, cross-sectional, trial, survey, systematic review |
| `comparator` | control group/arm, compared with/to, versus, placebo |
| `sample_size` | `n = 120`, `180 patients`, `96 participants`, 样本 |
| `outcome_measure` | generic cues (outcome, endpoint, score, marker, rate, level, measured, assessed) plus outcome terms collected from the supplied records and materials at run time |
| `statistics` | p-value, 95% CI, confidence interval, hazard/odds/risk ratio, effect size, regression, a significance statement |
| `limitations` | limitation, caveat, bias, confounding, small sample, underpowered, generalisability |

Patterns are configurable through `assets/policy.json`; if the file is absent the built-in defaults
are used and no error is raised. The outcome vocabulary is not a fixed domain list: `corpus_terms()`
counts non-generic terms across the records and materials and keeps those appearing at least twice.

## Overall rule

`overall_rule` is written into every appraisal:

- points: `yes = 1`, `partly = 0.5`, `no = 0`, `unclear = 0`;
- ratio = points / item count;
- `high` at ratio ≥ 0.75, `moderate` at ≥ 0.5, otherwise `low`;
- `unclear` only when every item is `unclear` (no readable text at all).

`judgment_counts`, `reported_items` and `full_text_items` are reported so a reader can recompute the
level without trusting the label. The level is a screening aid and must be read together with the
quoted sentences and locators.
