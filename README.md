# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-51977f9b` (track: `deep-research-literature-review`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable pipeline and all supporting documents. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `systematic-review-prisma-workflow`
- Summary: Use when a review must follow a reproducible systematic-review protocol, requiring an explicit search string and limits, duplicate removal, two-round screening with recorded exclusion reasons, per-study reporting appraisal, a PRISMA-style flow and a bounded synthesis with conflicts and gaps.

## Contents

| File |
|------|
| `SKILL.md` |
| `examples/demo-case/case.json` |
| `scripts/run_skill.py` |
| `README.md` |

## How to run

```bash
python scripts/sk_entry.py --input /input --work /app/work --out /app/output
```

A single deterministic pass reads `/input`, computes the deliverable and writes a
contract-complete `/app/output/result.json` plus the human-readable review files
described in `SKILL.md`. The same input always produces byte-identical output; no
network access is used, and fields the materials cannot support are reported
explicitly instead of being left empty.

## Notes

- Pure Python standard library; no network access required.
- Every claim is anchored to paragraph-level evidence; unknown items are reported
  in `uncertainty` instead of being fabricated.
