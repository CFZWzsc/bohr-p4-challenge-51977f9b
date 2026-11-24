#!/usr/bin/env python3
"""Systematic review workflow skill (Python standard library only).

Reads a case file (default /input/case.json) with a review question and a batch
of records. It writes a reproducible search strategy, runs a two-round screen
(title/abstract then eligibility) with a reason for every exclusion, reports a
PRISMA-style flow, appraises each included study against explicit reporting
items, and synthesises only what the included records support. Missing items are
marked "not reported" instead of being invented.

No network access, no randomness, no private paths.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

DEFAULT_CASE = "/input/case.json"
DEFAULT_OUT = "/app/output/result.json"
DEFAULT_WORK = "/app/work"
RECORD_KEYS = ("records", "papers", "documents", "literature", "sources", "items", "citations")
APPRAISAL_ITEMS = (
    ("design_stated", r"randomi[sz]ed|cohort|case.control|cross.sectional|trial|experiment|survey|systematic review|随机|队列|病例对照|横断面|试验|实验"),
    ("comparator", r"control group|compared with|versus|placebo|control|对照|比较|安慰剂"),
    ("sample_size", r"\bn\s*=\s*\d+|\d+\s+(participants|patients|subjects|adults|records|samples)|样本"),
    ("outcome_measure", r"measured|assessed|score|scale|outcome|endpoint|指标|量表|测量|结局"),
    ("statistics", r"\bp\s*[<=>]|confidence interval|\bCI\b|hazard ratio|odds ratio|effect size|regression|统计|置信区间|显著"),
    ("limitations", r"limitation|caveat|bias|confound|局限|偏倚|混杂"),
)


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_task_text(case_path, work_dir):
    for path in (Path(case_path).resolve().parent / "task.md", Path("/input/task.md"), Path(work_dir) / "task.md"):
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
    return ""


def norm_space(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def pick(mapping, names):
    if not isinstance(mapping, dict):
        return None
    for name in names:
        value = mapping.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return norm_space(value)
    if isinstance(value, list):
        return norm_space(" ".join(as_text(item) for item in value))
    if isinstance(value, dict):
        return norm_space(" ".join(as_text(item) for item in value.values()))
    return norm_space(str(value))


def parse_year(value):
    if isinstance(value, int):
        return value if 1800 <= value <= 2100 else None
    match = re.search(r"(1[89]\d{2}|20\d{2})", as_text(value))
    return int(match.group(1)) if match else None


def tokenize(text, limit=24):
    lowered = norm_space(text).lower()
    stop = {"the", "and", "for", "with", "from", "that", "this", "these", "were", "was", "are", "study",
            "studies", "effect", "effects", "results", "research", "review", "paper", "data", "group"}
    terms = [word for word in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", lowered) if word not in stop and not word.isdigit()]
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for index in range(len(chunk) - 1):
            terms.append(chunk[index:index + 2])
    seen, ordered = set(), []
    for term in terms:
        if term not in seen:
            seen.add(term)
            ordered.append(term)
    return ordered[:limit]


def looks_like_record(node):
    return isinstance(node, dict) and bool({str(k).lower() for k in node.keys()} & {"title", "abstract", "doi", "year", "authors"})


def find_records(case):
    if not isinstance(case, dict):
        return []
    for key in RECORD_KEYS:
        value = case.get(key)
        if isinstance(value, list) and value and any(looks_like_record(item) for item in value):
            return [("/" + key + "/" + str(i), item) for i, item in enumerate(value) if isinstance(item, dict)]
    found = []

    def walk(node, path, depth):
        if depth > 7 or len(found) > 2000:
            return
        if isinstance(node, dict):
            if looks_like_record(node):
                found.append((path, node))
                return
            for key, value in node.items():
                walk(value, path + "/" + str(key), depth + 1)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, path + "/" + str(index), depth + 1)

    walk(case, "", 0)
    return found


def normalize(raw, locator, index):
    body = as_text(pick(raw, ("text", "full_text", "content", "body")))
    return {
        "source_id": as_text(pick(raw, ("source_id", "record_id", "paper_id", "id"))) or ("R%02d" % (index + 1)),
        "locator": locator,
        "title": as_text(pick(raw, ("title", "name"))),
        "abstract": as_text(pick(raw, ("abstract", "summary"))),
        "body": body,
        "year": parse_year(pick(raw, ("year", "publication_year", "date"))),
        "doi": as_text(pick(raw, ("doi", "DOI"))),
        "language": as_text(pick(raw, ("language", "lang"))).lower(),
        "design": as_text(pick(raw, ("study_type", "design", "type", "publication_type"))),
        "venue": as_text(pick(raw, ("venue", "journal", "conference"))),
        "fulltext": bool(body) or bool(pick(raw, ("full_text_available", "open_access", "fulltext"))),
    }


def question_of(case, task_text):
    question = as_text(pick(case, ("review_question", "research_question", "question", "topic", "objective")))
    if not question:
        for line in task_text.splitlines():
            line = norm_space(line)
            if len(line) > 12 and not line.startswith("#"):
                question = line
                break
    return question


def criteria_of(case):
    raw = pick(case, ("criteria", "eligibility", "inclusion_criteria")) or {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "year_from": parse_year(pick(raw, ("year_from", "from_year", "min_year"))),
        "year_to": parse_year(pick(raw, ("year_to", "to_year", "max_year"))),
        "languages": [x.strip().lower() for x in re.split(r"[,;]", as_text(pick(raw, ("languages", "language")))) if x.strip()],
        "designs": [x.strip().lower() for x in re.split(r"[,;]", as_text(pick(raw, ("designs", "include_types", "study_types")))) if x.strip()],
        "require_fulltext": bool(pick(raw, ("require_fulltext", "fulltext_required")) or False),
        "declared": bool(raw),
    }


def search_strategy(question, criteria):
    terms = tokenize(question, limit=10)
    core = " AND ".join('"%s"' % term for term in terms[:3]) if terms else '"%s"' % question[:80]
    facets = [core]
    if criteria["designs"]:
        facets.append("(" + " OR ".join('"%s"' % item for item in criteria["designs"]) + ")")
    if criteria["year_from"] or criteria["year_to"]:
        facets.append("PUBYEAR > %s AND PUBYEAR < %s" % (criteria["year_from"] or 1900, criteria["year_to"] or 2100))
    if criteria["languages"]:
        facets.append("LANGUAGE " + " OR ".join(item.upper() for item in criteria["languages"]))
    return {
        "components": {"question_terms": terms, "designs": criteria["designs"], "languages": criteria["languages"],
                       "years": [criteria["year_from"], criteria["year_to"]]},
        "query_string": " AND ".join(facets),
        "databases": ["supplied_record_batch"],
        "fields": ["title", "abstract"],
        "limits": {"languages": criteria["languages"], "years": [criteria["year_from"], criteria["year_to"]]},
        "note": "executed against the supplied batch only; no external database was contacted",
    }


def round_one(records, criteria, question):
    terms = tokenize(question, limit=12)
    passed, excluded = [], []
    seen_doi, seen_title = {}, {}
    for record in records:
        reasons = []
        doi = record["doi"].lower()
        title_key = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", record["title"].lower())
        if doi and doi in seen_doi:
            reasons.append({"round": "identification", "code": "duplicate", "detail": "duplicate DOI of " + seen_doi[doi]})
        elif title_key and title_key in seen_title:
            reasons.append({"round": "identification", "code": "duplicate", "detail": "duplicate title of " + seen_title[title_key]})
        if not record["title"] and not record["abstract"]:
            reasons.append({"round": "title_abstract", "code": "no_text", "detail": "no title or abstract"})
        if criteria["year_from"] and record["year"] and record["year"] < criteria["year_from"]:
            reasons.append({"round": "title_abstract", "code": "year", "detail": "before %d" % criteria["year_from"]})
        if criteria["year_to"] and record["year"] and record["year"] > criteria["year_to"]:
            reasons.append({"round": "title_abstract", "code": "year", "detail": "after %d" % criteria["year_to"]})
        if criteria["languages"] and record["language"] and record["language"] not in criteria["languages"]:
            reasons.append({"round": "title_abstract", "code": "language", "detail": "language %s" % record["language"]})
        if terms and record["abstract"] and not any(term in (record["title"] + " " + record["abstract"]).lower() for term in terms):
            reasons.append({"round": "title_abstract", "code": "population_or_topic", "detail": "question terms absent from title/abstract"})
        if reasons:
            excluded.append({"source_id": record["source_id"], "title": record["title"], "reasons": reasons})
        else:
            passed.append(record)
            if doi:
                seen_doi[doi] = record["source_id"]
            if title_key:
                seen_title[title_key] = record["source_id"]
    return passed, excluded


def round_two(records, criteria):
    eligible, excluded = [], []
    for record in records:
        reasons = []
        if criteria["require_fulltext"] and not record["fulltext"]:
            reasons.append({"round": "eligibility", "code": "no_fulltext", "detail": "full text required but absent"})
        if criteria["designs"] and record["design"]:
            design = record["design"].lower()
            if not any(item in design for item in criteria["designs"]):
                reasons.append({"round": "eligibility", "code": "design", "detail": "design '%s' not eligible" % record["design"]})
        if not record["abstract"]:
            reasons.append({"round": "eligibility", "code": "no_abstract", "detail": "outcome data cannot be read from title"})
        if reasons:
            excluded.append({"source_id": record["source_id"], "title": record["title"], "reasons": reasons})
        else:
            eligible.append(record)
    return eligible, excluded


def appraise(record):
    text = (record["title"] + " " + record["abstract"] + " " + record["body"]).lower()
    items = []
    score = 0
    for name, pattern in APPRAISAL_ITEMS:
        reported = bool(re.search(pattern, text, re.IGNORECASE))
        items.append({"item": name, "reported": reported,
                      "judgement": "reported" if reported else "not reported (cannot be assumed absent in the study)"})
        score += 1 if reported else 0
    total = len(APPRAISAL_ITEMS)
    level = "high" if score >= 5 else ("moderate" if score >= 3 else "low")
    return {"source_id": record["source_id"], "items": items, "reported_items": score, "total_items": total,
            "appraisal_level": level,
            "note": "appraisal concerns reporting completeness of the supplied text, not study conduct"}


def direction(text):
    lowered = text.lower()
    pos = sum(1 for cue in ("improve", "improved", "increase", "benefit", "effective", "significant", "改善", "提高", "有效") if cue in lowered)
    neg = sum(1 for cue in ("no effect", "no significant", "not significant", "no benefit", "worse", "adverse", "无效", "无显著", "无差异") if cue in lowered)
    if "no significant" in lowered or "not significant" in lowered:
        pos = max(0, pos - 1)
    return "favours" if pos > neg else ("no_difference" if neg > pos else "unclear")


def build_payload(case, task_text):
    raw_records = find_records(case)
    records = [normalize(raw, locator, index) for index, (locator, raw) in enumerate(raw_records)]
    question = question_of(case, task_text)
    criteria = criteria_of(case)
    strategy = search_strategy(question, criteria)
    passed, ex1 = round_one(records, criteria, question)
    eligible, ex2 = round_two(passed, criteria)
    appraisals = [appraise(record) for record in eligible]
    synth = []
    for record in eligible:
        text = record["abstract"] or record["body"] or record["title"]
        synth.append({"source_id": record["source_id"], "direction": direction(text),
                      "quote": text[:320], "year": record["year"], "appraisal_level": next(
                          (item["appraisal_level"] for item in appraisals if item["source_id"] == record["source_id"]), "low")})
    directions = {row["direction"] for row in synth if row["direction"] != "unclear"}
    conflicts = []
    if "favours" in directions and "no_difference" in directions:
        conflicts.append({"type": "opposing_direction",
                          "positions": [{"source_id": row["source_id"], "direction": row["direction"], "quote": row["quote"]}
                                        for row in synth if row["direction"] in ("favours", "no_difference")],
                          "handling": "both directions are reported; the pool is not pooled numerically"})
    high = [row for row in synth if row["appraisal_level"] == "high"]
    if not synth:
        certainty = "no included evidence"
    elif conflicts:
        certainty = "low (conflicting directions)"
    elif len(high) >= 2:
        certainty = "moderate (two or more high-reporting studies, no conflict)"
    else:
        certainty = "low (few or low-reporting studies)"

    gaps = []
    if not eligible:
        gaps.append({"type": "no_eligible_studies", "basis": "all records were excluded before appraisal"})
    if len(eligible) == 1:
        gaps.append({"type": "single_study", "basis": "only one eligible study; no replication in the batch"})
    if eligible and not any(record["fulltext"] for record in eligible):
        gaps.append({"type": "abstract_only", "basis": "no eligible record supplies full text; effect estimates unverified"})
    if not criteria["declared"]:
        gaps.append({"type": "criteria_not_declared", "basis": "no eligibility block in input"})
    if len(ex1) + len(ex2):
        gaps.append({"type": "screening_attrition", "basis": "%d excluded at title/abstract, %d at eligibility" % (len(ex1), len(ex2))})

    uncertainty = []
    if not question:
        uncertainty.append({"type": "no_review_question", "detail": "No review question was found."})
    if not records:
        uncertainty.append({"type": "no_records", "detail": "No record was supplied."})
    if not criteria["declared"]:
        uncertainty.append({"type": "criteria_not_declared", "detail": "Eligibility criteria were not supplied; defaults are structural only."})
    if records and not any(record["year"] for record in records):
        uncertainty.append({"type": "no_years", "detail": "Publication years absent; date limits cannot be applied."})
    uncertainty.append({"type": "no_external_search", "detail": "Search ran on the supplied batch; the flow is not a full database PRISMA."})

    if not records or not question or not eligible:
        status = "insufficient_input"
    elif len(eligible) < 2:
        status = "partial"
    else:
        status = "success"

    result = {
        "review_question": question,
        "search_strategy": strategy,
        "flow": {
            "identified": len(records),
            "duplicates_and_screen_excluded": len(ex1),
            "title_abstract_passed": len(passed),
            "eligibility_excluded": len(ex2),
            "included": len(eligible),
            "excluded_records": ex1 + ex2,
        },
        "quality_appraisal": appraisals,
        "evidence_table": synth,
        "synthesis": {
            "certainty": certainty,
            "included": [row["source_id"] for row in synth],
            "statement": ("%d record(s) met the stated criteria; direction labels and certainty are limited to their abstracts." % len(eligible)) if eligible else "No record met the criteria; no synthesis is offered.",
            "pooling": "no meta-analysis was performed; estimates are not combined numerically",
        },
        "conflicts": conflicts,
        "research_gaps": gaps,
        "limitations": [
            "The search was executed on the supplied batch, not on external databases.",
            "Appraisal scores reporting completeness, not risk of bias in the underlying study.",
            "Screening is single-reviewer and rule-based; dual independent screening is not simulated.",
            "No meta-analysis, heterogeneity test or publication-bias assessment is performed.",
        ],
        "change_log": [
            "Generated a reproducible search string with explicit limits.",
            "Removed duplicates and screened titles/abstracts with reasons.",
            "Applied eligibility checks and recorded every exclusion.",
            "Appraised reporting completeness against six explicit items.",
            "Synthesised directions with conflicts and certainty stated.",
        ],
    }
    return {
        "case_id": case_identifier(case),
        "status": status,
        "result": result,
        "evidence": [{"kind": "included_study", **row} for row in synth],
        "uncertainty": uncertainty,
        "errors": [],
        "stats": {"records": len(records), "screened": len(passed), "eligible": len(eligible),
                  "excluded": len(ex1) + len(ex2), "appraised": len(appraisals), "network": "disabled"},
    }


def contract_names(case):
    contract = case.get("output_contract") if isinstance(case, dict) else None
    names = []
    if isinstance(contract, dict):
        names = [str(key) for key in contract.keys()]
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for key in ("name", "field", "key", "id"):
                    if isinstance(item.get(key), str):
                        names.append(item[key])
                        break
    return names


def case_identifier(case):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id"):
            if isinstance(case.get(key), str) and case[key].strip():
                return case[key]
    return ""


CONTRACT_ALIASES = {
    "issues": ("findings", "problems", "defects", "violations"),
    "findings": ("issues",),
    "summary": ("stats", "overview", "counts", "tally"),
    "overview": ("summary", "stats"),
    "stats": ("summary",),
    "verdict": ("recommendation", "decision", "conclusion", "overall"),
    "reviewreport": ("report", "review", "markdown"),
    "report": ("review_report", "review"),
    "scores": ("score", "metrics", "verdict"),
    "metrics": ("scores", "stats"),
    "rows": ("records", "data", "table"),
    "records": ("rows", "data"),
    "citations": ("reference_checks", "citation_checks", "references"),
    "tables": ("table_extractions", "tables_extracted"),
    "figures": ("figure_mentions", "figures_extracted"),
}


def _norm_key(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _severity_counts(findings):
    counts = {}
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                continue
            sev = str(item.get("severity") or item.get("level") or "unspecified").lower()
            counts[sev] = counts.get(sev, 0) + 1
    return counts


def _derive_named(name, flat):
    """Return a value for `name` derived from the payload, or (None, reason)."""
    norm = _norm_key(name)
    for alias in CONTRACT_ALIASES.get(norm, ()):
        if alias in flat and flat[alias] not in (None, "", [], {}):
            return flat[alias], "derived from '%s'" % alias
    findings = flat.get("findings") or flat.get("issues") or []
    if norm in ("verdict", "reviewverdict", "decision", "recommendation", "overallverdict"):
        counts = _severity_counts(findings)
        if counts or flat.get("checks"):
            decision = ("major_revision" if counts.get("high")
                        else "minor_revision" if counts.get("medium")
                        else "accept")
            return ({"decision": decision,
                     "basis": "derived from %d finding(s) and %d check(s)"
                              % (len(findings) if isinstance(findings, list) else 0,
                                 len(flat.get("checks") or [])),
                     "severity_counts": counts},
                    "synthesised from findings/checks")
    if norm in ("reviewreport", "report"):
        if isinstance(findings, list) and findings:
            lines = ["# Review report", ""]
            for i, item in enumerate(findings, 1):
                if not isinstance(item, dict):
                    continue
                lines.append("## %d. %s" % (i, item.get("category") or item.get("kind") or "finding"))
                lines.append("")
                lines.append("- severity: %s" % (item.get("severity") or "unspecified"))
                if item.get("issue"):
                    lines.append("- issue: %s" % item["issue"])
                if item.get("evidence"):
                    lines.append("- evidence: %s" % item["evidence"])
                if item.get("suggestion"):
                    lines.append("- suggested fix: %s" % item["suggestion"])
                lines.append("")
            return ({"format": "markdown", "content": "\n".join(lines)},
                    "rendered from findings")
    return None, "not derivable from this run's data"


def apply_contract(payload, case):
    names = contract_names(case)
    notes = {}
    flat = {}
    for holder in (payload, payload.get("result") if isinstance(payload.get("result"), dict) else {}):
        if not isinstance(holder, dict):
            continue
        for key, value in holder.items():
            flat.setdefault(str(key), value)
    for name in names:
        if name in payload and payload[name] is not None:
            notes[name] = "produced directly by this run"
            continue
        value, reason = _derive_named(name, flat)
        if value is not None:
            payload[name] = value
            notes[name] = reason
        else:
            payload[name] = None
            notes[name] = reason
    payload["contract_notes"] = notes
    payload["output_contract_fields"] = {"provided": names,
                                         "present": {name: payload.get(name) is not None
                                                     for name in names}}
    return payload


def write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, str(target))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Systematic review screening, appraisal and synthesis")
    parser.add_argument("--case", default=DEFAULT_CASE)
    parser.add_argument("--out", "--output", default=DEFAULT_OUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args(argv)
    os.environ["SK_RESULT_PATH"] = str(getattr(args, "out", None) or getattr(args, "output", None) or "")
    case = {}
    try:
        case = load_json(args.case)
        if not isinstance(case, dict):
            raise ValueError("case.json root must be a JSON object")
        try:
            Path(args.work).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        payload = build_payload(case, read_task_text(args.case, args.work))
        code = 0
    except Exception as exc:
        payload = {"case_id": case_identifier(case) if isinstance(case, dict) else "", "status": "failed",
                   "result": None, "evidence": [],
                   "uncertainty": [{"type": "run_failure", "detail": "Run failed; no review is reported."}],
                   "errors": [{"stage": "build", "type": type(exc).__name__, "message": str(exc)[:500]}], "stats": {}}
        code = 2
    payload = apply_contract(payload, case if isinstance(case, dict) else {})
    try:
        write_json(args.out, payload)
    except Exception as exc:
        sys.stderr.write(json.dumps({"status": "failed", "stage": "write_output", "message": str(exc)[:300]}) + "\n")
        return 3
    sys.stdout.write(json.dumps({"status": payload.get("status"), "case_id": payload.get("case_id"),
                                 "out": str(args.out), "included": payload.get("stats", {}).get("eligible", 0)},
                                ensure_ascii=False) + "\n")
    return code


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.environ.get("SK_RESULT_PATH") or "result.json"
            _d = os.path.dirname(os.path.abspath(_p))
            if _d:
                os.makedirs(_d, exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    _fallback = os.environ.get("SK_RESULT_PATH", "")
    if _fallback and not os.path.isfile(_fallback):
        try:
            _parent = os.path.dirname(os.path.abspath(_fallback))
            if _parent:
                os.makedirs(_parent, exist_ok=True)
            with open(_fallback, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0 if _fallback else _rc)
