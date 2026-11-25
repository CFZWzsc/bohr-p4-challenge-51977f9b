# Appraisal rubric

Appraisal measures whether the supplied text *reports* an item. It is not a risk-of-bias judgement
and cannot be read as "high quality" or "low quality" on its own.

Each appraisal item returns:

| field | meaning |
|---|---|
| `item` | rubric item name |
| `value` | `reported` or `not reported` |
| `quote` | the first sentence in title/abstract/body that matched the item pattern; empty when nothing matched |
| `checked` | always `true`: the item was actively checked |
| `reported` | boolean mirror of `value`, kept for backward compatibility |

`not reported` means no sentence matched the pattern in the supplied text — not that the study
failed to do it.

## Items

| item | what a hit looks like |
|---|---|
| `design_stated` | randomised, cohort, case-control, cross-sectional, trial, survey, systematic review |
| `comparator` | control group/arm, compared with/to, versus, placebo |
| `sample_size` | `n = 120`, `180 patients`, `96 participants`, 样本 |
| `outcome_measure` | falls, fractures, HbA1c, mortality, risk, incidence, outcome, endpoint, score, marker, recovery, survival, dialysis, adverse events, rate, level, measured, assessed |
| `statistics` | p-value, 95% CI, confidence interval, hazard/odds/risk ratio, effect size, regression, a significance statement |
| `limitations` | limitation, caveat, bias, confounding, small sample, underpowered, generalisability |

Patterns are configurable through `assets/policy.json`; if the file is absent the built-in defaults
are used and no error is raised.

## Scoring

`reported_items` counts hits (0–6); `appraisal_level` is `high` at 5–6, `moderate` at 3–4 and
`low` at 0–2. Because a single missing sentence lowers the score, the level is a screening aid and
must always be read together with the quoted sentences.
