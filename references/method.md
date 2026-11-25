# Method notes

## Scope

The skill runs offline on a supplied record batch. It does not contact PubMed, Scopus or any other
service. `search_strategy.query_string` is Scopus syntax and `search_strategy.query_string_pubmed`
is PubMed syntax; both are generated from the same question and criteria and can be pasted into the
respective interface, but `hits_per_query` counts matches in the supplied batch only.

## Query construction

1. Tokenise the review question; drop boilerplate (`does`, `do`, `is`, `are`, `the`, `a`, `of`,
   `with`, …) and direction verbs (`reduce`, `improve`, `increase`, …). The stopword set lives in
   `scripts/run_skill.py` (`DEFAULT_STOPWORDS`) and can be extended with `stopwords` in
   `assets/policy.json`.
2. The first three content terms are AND-ed into an anchor clause; remaining terms form an OR
   clause, so a query stays specific without demanding every question word.
3. Eligibility facets are appended: design, publication year and language.
   - `year_from` produces `PUBYEAR >= year_from` (`"year_from"[dp]` in PubMed).
   - `year_to` produces `PUBYEAR <= year_to`; when it is absent no upper bound is emitted at all.
4. `database`, `executed_at` (the fixed string `not_applicable (offline run)`) and
   `hits_per_query` make the strategy auditable without a clock or network.

## Determinism

No wall-clock time, randomness, locale-dependent formatting or absolute host paths are used.
Two runs on the same case in different work directories produce byte-identical `result.json`.

## Conflicts

Included studies are grouped by direction (`favours`, `no_difference`, `against`, `unclear`).
Each `favours` study paired with a `no_difference` or `against` study yields one `conflicts[]`
entry with an `id`, both positions' quotes and locators, and a handling note. The synthesis
statement cites those ids; no numerical pooling is performed.

## Direction cues

`lower`, `reduced`, `decreased`, `fewer`, `improved`, `benefit`, `effective`, `protective`,
`higher survival/recovery` count as favourable; `worse`, `harmful`, `increased risk/mortality`,
`higher risk/fall/fracture`, `more falls/fractures` count as against. An explicit
`no significant difference` / `no difference` statement takes precedence and is labelled
`no_difference`.
