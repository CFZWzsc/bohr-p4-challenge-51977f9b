# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-51977f9b` (track: `deep-research-literature-review`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable entry point and all supporting scripts. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `systematic-review-prisma-workflow`
- Summary: Use when a review must follow a reproducible systematic-review protocol, requiring an explicit search string and limits, duplicate removal, two-round screening with recorded exclusion reasons, per-study reporting appraisal, a PRISMA-style flow and a bounded synthesis with conflicts and gaps.

## Contents

| File |
|------|
| `SKILL.md` |
| `scripts/run_skill.py` |
| `README.md` |

## How to run

```bash
python scripts/run_skill.py --case /input/case.json --output /app/output/result.json
```

The entry point is `SKILL.md`; input/output contracts, error handling and a minimal
usage example are documented there.

## Notes

- Pure Python standard library; no network access required.
- The result JSON keeps the input case id, the main results, per-item evidence,
  a run status and an uncertainty section.
