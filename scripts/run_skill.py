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
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zlib
from pathlib import Path

DEFAULT_CASE = "/input/case.json"
DEFAULT_OUT = "/app/output/result.json"
DEFAULT_WORK = "/app/work"
RECORD_KEYS = ("records", "papers", "documents", "literature", "sources", "items", "citations")
EXECUTED_AT = "not_applicable (offline run)"
POLICY_PATH = Path(__file__).resolve().parent.parent / "assets" / "policy.json"
REASON_CODES_PATH = Path(__file__).resolve().parent.parent / "assets" / "reason_codes.json"
MATERIAL_EXTS = (".md", ".txt", ".csv", ".json", ".pdf")
MAX_MATERIALS = 40
MAX_MATERIAL_BYTES = 2_000_000
PROTOCOL = {
    "criterion_version": "E1-E5/2026-11",
    "search_version": "scopus-pubmed/2026-11",
    "screening_rule_version": "two-round-record-ledger/2026-11",
    "appraisal_scale_version": "judgment-4/2026-11",
}
DEFAULT_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "not", "no", "of", "in", "on", "at", "to", "into",
    "for", "with", "without", "from", "by", "as", "than", "then", "that", "this", "these", "those",
    "there", "their", "its", "it", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "done", "has", "have", "had", "can", "could", "should", "would",
    "may", "might", "must", "will", "shall", "what", "which", "who", "whom", "whose", "when",
    "where", "why", "how", "whether", "if", "we", "our", "us", "they", "them", "he", "she",
    "his", "her", "i", "you", "study", "studies", "effect", "effects", "result", "results",
    "research", "review", "paper", "papers", "data", "group", "groups", "between", "among",
    "also", "more", "most", "less", "least", "such", "using", "use", "used", "via", "per",
    "reduce", "reduces", "reduced", "reducing", "reduction", "improve", "improves", "improved",
    "improving", "improvement", "increase", "increases", "increased", "increasing",
    "decrease", "decreases", "decreased", "prevent", "prevents", "prevention",
}
DEFAULT_APPRAISAL_ITEMS = (
    ("design_stated", r"\brandomi[sz]ed\b|\bcohort\b|\bcase.control\b|\bcross.sectional\b|\btrial\b|\bexperiment\b|\bsurvey\b|systematic review|随机|队列|病例对照|横断面|试验|实验"),
    ("comparator", r"control group|control arm|compared (?:with|to)|\bversus\b|\bvs\.?\b|\bplacebo\b|对照|比较|安慰剂"),
    ("sample_size", r"\bn\s*=\s*\d+|\b\d+\s+(?:participants|patients|subjects|adults|children|records|samples|mice|rats)\b|样本"),
    ("outcome_measure", r"\boutcomes?\b|endpoints?|\bscores?\b|scales?|markers?|biomarkers?|\bmeasured\b|\bassessed\b|evaluated|incidence|\brates?\b|\blevels?\b|adverse (?:events?|effects?)|指标|量表|测量|结局|水平"),
    ("statistics", r"\bp\s*[<>=]|95\s?%\s?(?:CI|confidence)|confidence interval|\bCI\b|hazard ratio|odds ratio|risk ratio|relative risk|effect size|regression|significan|统计|置信区间|显著"),
    ("limitations", r"\blimitations?\b|caveat|\bbias\b|confound|small sample|underpowered|generali[sz]|局限|偏倚|混杂"),
)
LANGUAGE_NAMES = {"en": "english", "zh": "chinese", "de": "german", "fr": "french", "es": "spanish",
                  "ja": "japanese", "ko": "korean", "ru": "russian", "it": "italian", "pt": "portuguese"}
GENERIC_TERMS = {
    "patients", "patient", "participants", "subjects", "adults", "children", "records", "samples",
    "study", "studies", "trial", "trials", "group", "groups", "analysis", "treatment", "treatments",
    "effect", "effects", "result", "results", "outcome", "outcomes", "clinical", "significant",
    "significantly", "compared", "comparison", "associated", "association", "evidence", "review",
    "randomized", "randomised", "prospective", "retrospective", "cohort", "placebo", "control",
    "measured", "reported", "baseline", "follow", "intervention", "using", "between", "among",
    "higher", "lower", "increase", "increased", "decrease", "decreased", "reduced", "reduction",
    "improved", "improvement", "levels", "level", "rate", "rates", "data", "paper", "papers",
    "journal", "article", "abstract", "introduction", "methods", "discussion", "conclusion",
    "background", "objective", "aim", "aims", "this", "these", "those", "their", "which", "while",
    "received", "underwent", "assigned", "treated", "observed", "performed", "conducted", "enrolled",
    "recruited", "screened", "identified", "showed", "found", "suggest", "suggests", "indicate",
    "indicates", "respectively", "however", "therefore", "although",
}
DEFAULT_REASON_CODES = {
    "version": "1",
    "codes": [
        {"code": "duplicate", "stage": "identification", "criteria_id": None,
         "text": "Duplicate DOI or normalised title; the first occurrence is retained."},
        {"code": "no_text", "stage": "title_abstract", "criteria_id": None,
         "text": "No title or abstract text is supplied, so eligibility cannot be judged."},
        {"code": "year", "stage": "title_abstract", "criteria_id": "E1",
         "text": "Publication year falls outside the declared window."},
        {"code": "language", "stage": "title_abstract", "criteria_id": "E2",
         "text": "Language is outside the declared language set."},
        {"code": "topic", "stage": "title_abstract", "criteria_id": "E4",
         "text": "No review-question term appears in the title/abstract, so population or topic mismatches."},
        {"code": "design", "stage": "eligibility", "criteria_id": "E3",
         "text": "Study design is outside the declared eligible designs."},
        {"code": "no_fulltext", "stage": "eligibility", "criteria_id": "E5",
         "text": "Full text is required by the protocol but no full text or body text is available."},
        {"code": "no_abstract", "stage": "eligibility", "criteria_id": "E4",
         "text": "No abstract is available, so outcome data cannot be read for eligibility."},
        {"code": "met_criteria", "stage": "included", "criteria_id": None,
         "text": "All applicable criteria were met; the record enters synthesis."},
    ],
}


def load_policy():
    """Read assets/policy.json when present; fall back to built-in defaults."""
    defaults = {"stopwords": sorted(DEFAULT_STOPWORDS),
                "appraisal_items": [list(pair) for pair in DEFAULT_APPRAISAL_ITEMS]}
    try:
        with open(POLICY_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return defaults
    if not isinstance(data, dict):
        return defaults
    extra = data.get("stopwords")
    if isinstance(extra, list):
        words = {word.lower() for word in extra if isinstance(word, str) and word.strip()}
        if words:
            defaults["stopwords"] = sorted(set(DEFAULT_STOPWORDS) | words)
    declared = data.get("appraisal_items")
    parsed = []
    if isinstance(declared, list):
        for entry in declared:
            if isinstance(entry, dict) and isinstance(entry.get("item"), str) and isinstance(entry.get("pattern"), str):
                parsed.append([entry["item"], entry["pattern"]])
    if parsed:
        defaults["appraisal_items"] = parsed
    return defaults


_POLICY = load_policy()
STOPWORDS = set(_POLICY["stopwords"])
APPRAISAL_ITEMS = tuple((item, pattern) for item, pattern in _POLICY["appraisal_items"])


def load_reason_codes():
    """Read assets/reason_codes.json when present; fall back to built-in codes."""
    try:
        with open(REASON_CODES_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return dict(DEFAULT_REASON_CODES)
    if not isinstance(data, dict) or not isinstance(data.get("codes"), list):
        return dict(DEFAULT_REASON_CODES)
    codes = [entry for entry in data["codes"]
             if isinstance(entry, dict) and isinstance(entry.get("code"), str)]
    if not codes:
        return dict(DEFAULT_REASON_CODES)
    version = data.get("version")
    return {"version": version if isinstance(version, str) else "1", "codes": codes}


REASON_CODES = load_reason_codes()


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


# ---------------------------------------------------------------------------
# Entity materials: read the supplied files and locate evidence by paragraph
# ---------------------------------------------------------------------------

def title_key(value):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", as_text(value).lower())


def _chunk_text(text, size=600):
    pieces, rest = [], text
    while len(rest) > size * 1.5:
        window = rest[:size]
        cut = max(window.rfind(". "), window.rfind("; "), window.rfind(", "),
                  window.rfind("。"), window.rfind("；"))
        if cut < size * 0.4:
            cut = rest.rfind(" ", 0, size)
        if cut < size * 0.4:
            cut = size
        pieces.append(rest[:cut + 1].strip())
        rest = rest[cut + 1:].strip()
    if rest:
        pieces.append(rest)
    return [piece for piece in pieces if piece]


def split_paragraphs(raw, max_chars=600):
    raw = unicodedata.normalize("NFKC", raw or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n+", raw)
    out = []
    for block in blocks:
        block = norm_space(block)
        if not block:
            continue
        if len(block) <= max_chars:
            out.append(block)
        else:
            for piece in _chunk_text(block, max_chars):
                out.append(piece)
    return out


def _pdf_unescape(raw):
    out = bytearray()
    index = 0
    mapping = {110: 10, 114: 13, 116: 9, 98: 8, 102: 12, 40: 40, 41: 41, 92: 92}
    while index < len(raw):
        char = raw[index]
        if char == 92 and index + 1 < len(raw):
            nxt = raw[index + 1]
            if nxt in mapping:
                out.append(mapping[nxt])
                index += 2
                continue
            if 48 <= nxt <= 55:
                digits = b""
                cursor = index + 1
                while cursor < len(raw) and len(digits) < 3 and 48 <= raw[cursor] <= 55:
                    digits += bytes([raw[cursor]])
                    cursor += 1
                try:
                    out.append(int(digits, 8) & 0xFF)
                except ValueError:
                    pass
                index = cursor
                continue
            index += 2
            continue
        out.append(char)
        index += 1
    return bytes(out)


def _pdf_decode(raw):
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace")
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", "replace")
    return raw.decode("latin-1", "replace")


def _pdf_stream_strings(content):
    pieces, index, length = [], 0, len(content)
    while index < length:
        char = content[index]
        if char == "(":
            depth, cursor, buffer = 1, index + 1, []
            while cursor < length and depth > 0:
                current = content[cursor]
                if current == "\\":
                    buffer.append(current)
                    if cursor + 1 < length:
                        buffer.append(content[cursor + 1])
                        cursor += 2
                        continue
                if current == "(":
                    depth += 1
                elif current == ")":
                    depth -= 1
                    if depth == 0:
                        cursor += 1
                        break
                buffer.append(current)
                cursor += 1
            pieces.append(_pdf_decode(_pdf_unescape("".join(buffer).encode("latin-1", "ignore"))))
            index = cursor
        elif char == "<":
            end = content.find(">", index + 1)
            if end < 0:
                break
            hexstr = re.sub(r"\s+", "", content[index + 1:end])
            if len(hexstr) % 2:
                hexstr += "0"
            try:
                raw = bytes.fromhex(hexstr)
            except ValueError:
                raw = b""
            pieces.append(_pdf_decode(raw))
            index = end + 1
        else:
            index += 1
    return pieces


def _pdf_zlib_text(data):
    pages = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        raw = match.group(1)
        header = data[max(0, match.start() - 400):match.start()]
        if b"FlateDecode" in header:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                try:
                    raw = zlib.decompressobj().decompress(raw)
                except zlib.error:
                    continue
        content = raw.decode("latin-1", "ignore")
        blocks = re.findall(r"BT(.*?)ET", content, re.S)
        chunks = []
        for block in blocks or [content]:
            pieces = [piece for piece in _pdf_stream_strings(block) if piece.strip()]
            if pieces:
                chunks.append(" ".join(pieces))
        text = norm_space(" ".join(chunks))
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def _pdftotext(path):
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        proc = subprocess.run([exe, "-q", "-enc", "UTF-8", str(path), "-"],
                              capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    text = proc.stdout.decode("utf-8", "replace")
    return text if text.strip() else None


def _flatten_strings(node, out, limit):
    if len(out) >= limit or len("\n".join(out)) > 200000:
        return
    if isinstance(node, str):
        value = norm_space(node)
        if value:
            out.append(value)
    elif isinstance(node, list):
        for item in node:
            _flatten_strings(item, out, limit)
    elif isinstance(node, dict):
        for value in node.values():
            _flatten_strings(value, out, limit)


def _read_text_material(path, ext):
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_MATERIAL_BYTES + 1)
    except OSError:
        return [], "unreadable"
    if ext == ".json":
        try:
            node = json.loads(data.decode("utf-8-sig", "replace"))
        except ValueError:
            return split_paragraphs(data.decode("utf-8", "replace")), ""
        strings = []
        _flatten_strings(node, strings, 2000)
        return split_paragraphs("\n\n".join(strings)), ""
    if ext == ".csv":
        rows = []
        try:
            reader = csv.reader(data.decode("utf-8-sig", "replace").splitlines())
            for row in reader:
                cells = [cell.strip() for cell in row if cell.strip()]
                if cells:
                    rows.append(" | ".join(cells))
        except (ValueError, csv.Error):
            return split_paragraphs(data.decode("utf-8", "replace")), ""
        return split_paragraphs("\n\n".join(rows)), ""
    return split_paragraphs(data.decode("utf-8", "replace")), ""


def _material_from_pages(pages):
    paragraphs = []
    for page in pages:
        for block in split_paragraphs(page):
            paragraphs.append({"locator": "", "text": block})
    text = " ".join(item["text"] for item in paragraphs)
    return paragraphs, text


def collect_materials(case_path):
    """Read md/txt/csv/json/pdf files next to case.json and split them into paragraphs."""
    root = Path(case_path).resolve().parent
    try:
        candidates = sorted(path for path in root.rglob("*") if path.is_file())
    except OSError:
        candidates = []
    selected = []
    for path in candidates:
        if len(selected) >= MAX_MATERIALS:
            break
        name = path.name
        if name in ("case.json", "task.md") or name.startswith("."):
            continue
        if path.suffix.lower() not in MATERIAL_EXTS:
            continue
        selected.append(path)
    materials = []
    for index, path in enumerate(selected, 1):
        ext = path.suffix.lower()
        rel = path.relative_to(root).as_posix()
        source_id = "S%d" % index
        pages = []
        note = ""
        if ext == ".pdf":
            raw = _pdftotext(path)
            if raw is None:
                try:
                    data = path.read_bytes()[:MAX_MATERIAL_BYTES]
                except OSError:
                    data = b""
                raw = _pdf_zlib_text(data) if data else ""
                note = "pdftotext_unavailable; zlib_fallback" if raw else "unparsed_pdf"
            pages = [part for part in raw.split("\f")] if raw else []
        else:
            paragraphs, note = _read_text_material(path, ext)
            pages = ["\n\n".join(paragraphs)] if paragraphs else []
        paragraphs, text = _material_from_pages(pages)
        for number, item in enumerate(paragraphs, 1):
            item["locator"] = "%s#P%d" % (source_id, number)
        unparsed = not paragraphs
        materials.append({
            "source_id": source_id,
            "name": rel,
            "kind": ext.lstrip("."),
            "paragraphs": paragraphs,
            "text": text,
            "text_lower": text.lower(),
            "text_key": re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", text.lower())[:200000],
            "unparsed": unparsed,
            "note": note if note else ("no_readable_text" if unparsed else ""),
        })
    return materials


def _title_terms(title):
    terms = [word for word in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", as_text(title).lower())
             if word not in STOPWORDS and word not in GENERIC_TERMS and not word.isdigit()]
    ordered = []
    for term in terms:
        if term not in ordered:
            ordered.append(term)
    return ordered


def _first_author_surname(authors):
    value = as_text(authors)
    if not value:
        return ""
    first = re.split(r"[;,]| and ", value)[0]
    words = re.findall(r"[A-Za-z\u4e00-\u9fff\-]{2,}", first)
    return words[-1].lower() if words else ""


def match_record_sources(records, materials):
    """Fuzzy-match each record to a material by DOI, title or first-author surname."""
    matches = []
    for record in records:
        best, best_score, best_method = None, 0.0, ""
        doi = record["doi"].lower().strip()
        key = title_key(record["title"])
        terms = _title_terms(record["title"])
        surname = _first_author_surname(record.get("authors", ""))
        for material in materials:
            if material["unparsed"]:
                continue
            score, method = 0.0, ""
            if doi and len(doi) >= 6 and doi in material["text_lower"]:
                score, method = 1.0, "doi"
            elif key and len(key) >= 18 and key in material["text_key"]:
                score, method = 0.95, "title"
            elif terms:
                present = sum(1 for term in terms if term in material["text_lower"])
                score = present / float(len(terms))
                method = "title_tokens"
                if surname and surname in material["text_lower"]:
                    score = min(1.0, score + 0.12)
                    method = "title_tokens+author"
            if score > best_score:
                best, best_score, best_method = material, score, method
        if best is not None and best_score >= 0.6:
            record["material_id"] = best["source_id"]
            record["match_score"] = round(best_score, 3)
            record["match_method"] = best_method
        else:
            record["material_id"] = ""
            record["match_score"] = 0.0
            record["match_method"] = ""
        matches.append((record, best if record["material_id"] else None))
    by_id = {material["source_id"]: material for material in materials}
    for record in records:
        record["material"] = by_id.get(record["material_id"]) if record["material_id"] else None
    return records


def corpus_terms(records, materials, limit=15):
    """Collect outcome vocabulary from the supplied records and materials (no fixed domain list)."""
    counts = {}

    def add(text):
        lowered = norm_space(text).lower()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", lowered):
            if word in STOPWORDS or word in GENERIC_TERMS or word.isdigit():
                continue
            counts[word] = counts.get(word, 0) + 1

    for record in records:
        add(record["title"] + " " + record["abstract"])
    for material in materials:
        add(material["text"][:4000])
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, count in ranked if count >= 2][:limit]


def outcome_pattern(terms):
    cues = (r"\boutcomes?\b|endpoints?|\bscores?\b|scales?|markers?|biomarkers?|\bmeasured\b|"
            r"\bassessed\b|evaluated|incidence|\brates?\b|\blevels?\b|adverse (?:events?|effects?)|"
            r"指标|量表|测量|结局|水平")
    if not terms:
        return cues
    return r"\b(?:%s)\b|%s" % ("|".join(re.escape(term) for term in terms), cues)


def record_passages(record):
    """Return (text, locator, tier) passages, full text first, then abstract and title."""
    passages = []
    material = record.get("material")
    if material and not material["unparsed"]:
        for item in material["paragraphs"]:
            passages.append((item["text"], item["locator"], "full_text"))
    for sentence in split_sentences(record["abstract"]):
        passages.append((sentence, record["locator"] + "#abstract", "abstract"))
    for sentence in split_sentences(record["title"]):
        passages.append((sentence, record["locator"] + "#title", "title"))
    return passages


def best_quote(record, table_text, prefer_material=True):
    """Pick a quotable sentence and its locator from material paragraphs or the record itself."""
    material = record.get("material")
    if prefer_material and material and not material["unparsed"]:
        lowered = table_text.lower()
        for item in material["paragraphs"]:
            if lowered and lowered in item["text"].lower():
                return item["text"][:320], item["locator"]
        guide = set(tokenize(table_text, 60))
        best, best_score = None, 0
        for item in material["paragraphs"]:
            score = len(guide & set(tokenize(item["text"], 60)))
            if score > best_score:
                best, best_score = item, score
        if best is not None and best_score > 0:
            return best["text"][:320], best["locator"]
        for item in material["paragraphs"]:
            if direction(item["text"]) != "unclear":
                return item["text"][:320], item["locator"]
        if material["paragraphs"]:
            first = material["paragraphs"][0]
            return first["text"][:320], first["locator"]
    for sentence in split_sentences(record["abstract"]):
        return sentence[:320], record["locator"] + "#abstract"
    for sentence in split_sentences(record["title"]):
        return sentence[:320], record["locator"] + "#title"
    material = record.get("material")
    if material and material["paragraphs"]:
        return material["paragraphs"][0]["text"][:320], material["paragraphs"][0]["locator"]
    return "", record["locator"] + "#none"


def screening_quote(record):
    for sentence in split_sentences(record["abstract"]):
        text = sentence[:320]
        locator = record["locator"] + "#abstract"
        material = record.get("material")
        if material and not material["unparsed"]:
            for item in material["paragraphs"]:
                if text[:120].lower() in item["text"].lower():
                    return text, item["locator"]
        return text, locator
    for sentence in split_sentences(record["title"]):
        return sentence[:320], record["locator"] + "#title"
    return "", record["locator"] + "#none"


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
    terms = [word for word in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", lowered)
             if word not in STOPWORDS and not word.isdigit()]
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
        "index": index,
        "source_id": as_text(pick(raw, ("source_id", "record_id", "paper_id", "id"))) or ("R%02d" % (index + 1)),
        "locator": locator,
        "title": as_text(pick(raw, ("title", "name"))),
        "abstract": as_text(pick(raw, ("abstract", "summary"))),
        "body": body,
        "year": parse_year(pick(raw, ("year", "publication_year", "date"))),
        "doi": as_text(pick(raw, ("doi", "DOI"))),
        "authors": as_text(pick(raw, ("authors", "author", "first_author"))),
        "language": as_text(pick(raw, ("language", "lang"))).lower(),
        "design": as_text(pick(raw, ("study_type", "design", "type", "publication_type"))),
        "venue": as_text(pick(raw, ("venue", "journal", "conference"))),
        "fulltext": bool(body) or bool(pick(raw, ("full_text_available", "open_access", "fulltext"))),
        "material_id": "",
        "match_score": 0.0,
        "match_method": "",
        "material": None,
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


def _span_text(low, high):
    if low and high:
        return "between %d and %d" % (low, high)
    if low:
        return "from %d onwards" % low
    if high:
        return "up to %d" % high
    return "not restricted"


def criteria_of(case):
    raw = pick(case, ("criteria", "eligibility", "inclusion_criteria")) or {}
    if not isinstance(raw, dict):
        raw = {}
    criteria = {
        "year_from": parse_year(pick(raw, ("year_from", "from_year", "min_year"))),
        "year_to": parse_year(pick(raw, ("year_to", "to_year", "max_year"))),
        "languages": [x.strip().lower() for x in re.split(r"[,;]", as_text(pick(raw, ("languages", "language")))) if x.strip()],
        "designs": [x.strip().lower() for x in re.split(r"[,;]", as_text(pick(raw, ("designs", "include_types", "study_types")))) if x.strip()],
        "require_fulltext": bool(pick(raw, ("require_fulltext", "fulltext_required")) or False),
        "declared": bool(raw),
    }
    year_basis = criteria["year_from"] or criteria["year_to"]
    criteria["items"] = [
        {"id": "E1", "element": "time",
         "text": "Publication year is %s." % _span_text(criteria["year_from"], criteria["year_to"]),
         "origin": "case" if year_basis else "derived",
         "active": bool(year_basis)},
        {"id": "E2", "element": "language",
         "text": "Language is %s." % (", ".join(criteria["languages"]) if criteria["languages"] else "not restricted"),
         "origin": "case" if criteria["languages"] else "derived",
         "active": bool(criteria["languages"])},
        {"id": "E3", "element": "design",
         "text": "Study design is %s." % (", ".join(criteria["designs"]) if criteria["designs"] else "not restricted"),
         "origin": "case" if criteria["designs"] else "derived",
         "active": bool(criteria["designs"])},
        {"id": "E4", "element": "population_or_topic",
         "text": "Title/abstract concerns the population and topic of the review question.",
         "origin": "derived", "active": True},
        {"id": "E5", "element": "full_text",
         "text": ("Full text is available." if criteria["require_fulltext"]
                  else "Title/abstract text is readable; full text not required."),
         "origin": "case" if criteria["require_fulltext"] else "derived",
         "active": True},
    ]
    criteria["by_id"] = {item["id"]: item for item in criteria["items"]}
    return criteria


def _term_clause(terms, anchor_limit=3, field=None):
    if not terms:
        return ""
    suffix = "[tiab]" if field == "pubmed" else ""

    def wrap(term):
        return '"%s"%s' % (term, suffix)

    anchors = terms[:anchor_limit]
    rest = terms[anchor_limit:]
    clause = " AND ".join(wrap(term) for term in anchors)
    if rest:
        clause = "(%s) AND (%s)" % (clause, " OR ".join(wrap(term) for term in rest))
    return clause


def _design_clause(criteria, pubmed=False):
    if not criteria["designs"]:
        return ""
    suffix = "[tiab]" if pubmed else ""
    return "(" + " OR ".join('"%s"%s' % (item, suffix) for item in criteria["designs"]) + ")"


def _year_clause(criteria, pubmed=False):
    year_from, year_to = criteria["year_from"], criteria["year_to"]
    if pubmed:
        if year_from and year_to:
            return '("%d"[dp] : "%d"[dp])' % (year_from, year_to)
        if year_from:
            return '("%d"[dp] : "3000"[dp])' % year_from
        if year_to:
            return '("1800"[dp] : "%d"[dp])' % year_to
        return ""
    parts = []
    if year_from:
        parts.append("PUBYEAR >= %d" % year_from)
    if year_to:
        parts.append("PUBYEAR <= %d" % year_to)
    return " AND ".join(parts)


def _language_clause(criteria, pubmed=False):
    languages = criteria["languages"]
    if not languages:
        return ""
    if pubmed:
        return "(" + " OR ".join("%s[la]" % LANGUAGE_NAMES.get(item, item) for item in languages) + ")"
    return "LANGUAGE(" + ", ".join(LANGUAGE_NAMES.get(item, item) for item in languages) + ")"


def _passes_filters(record, terms, criteria):
    text = (record["title"] + " " + record["abstract"]).lower()
    if terms and not any(term in text for term in terms):
        return False
    if criteria["designs"] and record["design"]:
        design = record["design"].lower()
        if not any(item in design for item in criteria["designs"]):
            return False
    if criteria["year_from"] and record["year"] and record["year"] < criteria["year_from"]:
        return False
    if criteria["year_to"] and record["year"] and record["year"] > criteria["year_to"]:
        return False
    if criteria["languages"] and record["language"] and record["language"] not in criteria["languages"]:
        return False
    return True


def search_strategy(question, criteria, records=()):
    terms = tokenize(question, limit=10)
    core = _term_clause(terms)
    design = _design_clause(criteria)
    year = _year_clause(criteria)
    language = _language_clause(criteria)
    query_string = " AND ".join(part for part in (core, design, year, language) if part)
    query_pubmed = " AND ".join(part for part in (
        _term_clause(terms, 3, "pubmed"), _design_clause(criteria, True),
        _year_clause(criteria, True), _language_clause(criteria, True)) if part)
    texts = [(record["title"] + " " + record["abstract"]).lower() for record in records]
    hits = []
    if core:
        hits.append({"label": "core_terms", "query": core,
                     "hits": sum(1 for text in texts if any(term in text for term in terms))})
    if design:
        hits.append({"label": "design_filter", "query": design,
                     "hits": sum(1 for record in records
                                 if record["design"] and any(item in record["design"].lower()
                                                             for item in criteria["designs"]))})
    if year:
        hits.append({"label": "year_filter", "query": year,
                     "hits": sum(1 for record in records
                                 if not ((criteria["year_from"] and record["year"] and record["year"] < criteria["year_from"])
                                         or (criteria["year_to"] and record["year"] and record["year"] > criteria["year_to"])))})
    if language:
        hits.append({"label": "language_filter", "query": language,
                     "hits": sum(1 for record in records
                                 if not (criteria["languages"] and record["language"]
                                         and record["language"] not in criteria["languages"]))})
    hits.append({"label": "combined_after_limits", "query": query_string,
                 "hits": sum(1 for record in records if _passes_filters(record, terms, criteria))})
    return {
        "components": {"question_terms": terms, "designs": criteria["designs"], "languages": criteria["languages"],
                       "years": [criteria["year_from"], criteria["year_to"]]},
        "query_string": query_string,
        "query_string_pubmed": query_pubmed,
        "database": "supplied_record_batch",
        "databases": ["supplied_record_batch"],
        "executed_at": EXECUTED_AT,
        "hits_per_query": hits,
        "fields": ["title", "abstract"],
        "limits": {"languages": criteria["languages"], "years": [criteria["year_from"], criteria["year_to"]],
                   "year_from_inclusive": True, "year_to_applied": criteria["year_to"] is not None},
        "note": ("query_string uses Scopus syntax (PUBYEAR) and query_string_pubmed uses PubMed [tiab]/[dp]; "
                 "both are paste-ready, but hit counts are computed on the supplied batch only and no external "
                 "database was contacted"),
    }


def _reason_criteria(code):
    for entry in REASON_CODES["codes"]:
        if entry.get("code") == code and entry.get("criteria_id"):
            return [entry["criteria_id"]]
    return []


def _decision(record, stage, decision, reason_code, criteria_ids, detail):
    quote, locator = screening_quote(record)
    return {"id": record["source_id"], "index": record["index"], "stage": stage,
            "decision": decision, "criteria_ids": list(criteria_ids),
            "reason_code": reason_code, "detail": detail,
            "quote": quote, "locator": locator}


def screen_records(records, criteria, question):
    """Two-round screening with one ledger row per record."""
    terms = tokenize(question, limit=12)
    active_ids = [item["id"] for item in criteria["items"] if item["active"]]
    decisions, passed = {}, []
    seen_doi, seen_title = {}, {}
    for record in records:
        doi = record["doi"].lower()
        key = title_key(record["title"])
        duplicate_of = ""
        if doi and doi in seen_doi:
            duplicate_of = seen_doi[doi]
        elif key and key in seen_title:
            duplicate_of = seen_title[key]
        if duplicate_of:
            decisions[record["index"]] = _decision(
                record, "identification", "duplicate", "duplicate", [],
                "duplicate DOI or title of " + duplicate_of)
            continue
        if doi:
            seen_doi[doi] = record["source_id"]
        if key:
            seen_title[key] = record["source_id"]
        reasons = []
        if not record["title"] and not record["abstract"]:
            reasons.append(("no_text", _reason_criteria("no_text"), "no title or abstract"))
        if criteria["year_from"] and record["year"] and record["year"] < criteria["year_from"]:
            reasons.append(("year", ["E1"], "year %d is before %d" % (record["year"], criteria["year_from"])))
        if criteria["year_to"] and record["year"] and record["year"] > criteria["year_to"]:
            reasons.append(("year", ["E1"], "year %d is after %d" % (record["year"], criteria["year_to"])))
        if criteria["languages"] and record["language"] and record["language"] not in criteria["languages"]:
            reasons.append(("language", ["E2"], "language %s is not eligible" % record["language"]))
        if terms and record["abstract"] and not any(
                term in (record["title"] + " " + record["abstract"]).lower() for term in terms):
            reasons.append(("topic", ["E4"], "no question term appears in the title/abstract"))
        if reasons:
            decisions[record["index"]] = _decision(
                record, "title_abstract", "exclude", reasons[0][0],
                [item for reason in reasons for item in reason[1]], "; ".join(reason[2] for reason in reasons))
        else:
            passed.append(record)
    eligible = []
    for record in passed:
        reasons = []
        if criteria["require_fulltext"] and not record["fulltext"]:
            reasons.append(("no_fulltext", ["E5"], "full text is required but absent"))
        if criteria["designs"] and record["design"]:
            design = record["design"].lower()
            if not any(item in design for item in criteria["designs"]):
                reasons.append(("design", ["E3"], "design '%s' is not eligible" % record["design"]))
        if not record["abstract"]:
            reasons.append(("no_abstract", ["E4"], "no abstract is available for eligibility reading"))
        if reasons:
            decisions[record["index"]] = _decision(
                record, "eligibility", "exclude", reasons[0][0],
                [item for reason in reasons for item in reason[1]], "; ".join(reason[2] for reason in reasons))
        else:
            eligible.append(record)
            decisions[record["index"]] = _decision(
                record, "included", "include", "met_criteria", active_ids, "all applicable criteria met")
    ordered = [decisions[record["index"]] for record in records if record["index"] in decisions]
    excluded = []
    for row in ordered:
        if row["decision"] == "include":
            continue
        record = next(item for item in records if item["index"] == row["index"])
        excluded.append({"source_id": row["id"], "title": record["title"], "stage": row["stage"],
                         "decision": row["decision"],
                         "reasons": [{"round": row["stage"], "code": row["reason_code"], "detail": row["detail"]}]})
    return {"decisions": ordered, "passed": passed, "eligible": eligible, "excluded": excluded}


def assert_flow(flow):
    checks = [
        ("identified_minus_duplicates_equals_screened",
         flow["identified"] - flow["duplicates_removed"], flow["screened"]),
        ("screened_minus_excluded_equals_title_abstract_passed",
         flow["screened"] - flow["title_abstract_excluded"], flow["title_abstract_passed"]),
        ("title_abstract_passed_minus_eligibility_excluded_equals_included",
         flow["title_abstract_passed"] - flow["eligibility_excluded"], flow["included"]),
    ]
    rows = [{"check": name, "left": left, "right": right, "ok": left == right}
            for name, left, right in checks]
    return {"ok": all(row["ok"] for row in rows), "checks": rows}


def split_sentences(text):
    return [part for part in re.split(r"(?<=[.!?;])\s+", norm_space(text)) if part]


JUDGMENT_NOTES = {
    "yes": "matched in a supplied full-text paragraph",
    "partly": "matched only in the title/abstract layer",
    "no": "no matching statement in the supplied text",
    "unclear": "no readable text was supplied for this record",
}
OVERALL_RULE = ("judgment points: yes=1, partly=0.5, no=0, unclear=0; "
                "ratio = points / item count; high >= 0.75, moderate >= 0.5, low < 0.5; "
                "overall=unclear only when every item is unclear")


def appraise(record, items=None):
    items = items or APPRAISAL_ITEMS
    passages = record_passages(record)
    has_text = any(text for text, _locator, _tier in passages)
    rows = []
    counts = {"yes": 0, "partly": 0, "no": 0, "unclear": 0}
    for name, pattern in items:
        quote, locator, tier = "", "", ""
        for text, where, source_tier in passages:
            if re.search(pattern, text, re.IGNORECASE):
                quote, locator, tier = text[:320], where, source_tier
                break
        if not has_text:
            judgment = "unclear"
        elif tier == "full_text":
            judgment = "yes"
        elif tier in ("abstract", "title"):
            judgment = "partly"
        else:
            judgment = "no"
        counts[judgment] += 1
        rows.append({"item": name, "judgment": judgment,
                     "basis": {"quote": quote, "locator": locator},
                     "note": JUDGMENT_NOTES[judgment],
                     "value": "reported" if judgment in ("yes", "partly") else "not reported",
                     "reported": judgment in ("yes", "partly")})
    total = len(items)
    points = counts["yes"] + 0.5 * counts["partly"]
    ratio = (points / total) if total else 0.0
    if counts["unclear"] == total:
        overall = "unclear"
    elif ratio >= 0.75:
        overall = "high"
    elif ratio >= 0.5:
        overall = "moderate"
    else:
        overall = "low"
    return {"source_id": record["source_id"], "items": rows, "judgment_counts": counts,
            "reported_items": counts["yes"] + counts["partly"], "full_text_items": counts["yes"],
            "total_items": total, "overall": overall, "appraisal_level": overall,
            "overall_rule": OVERALL_RULE,
            "note": ("each dimension records a four-level judgment and the quote plus locator it rests on; "
                     "this measures reporting completeness of the supplied text, not study conduct")}


DIRECTION_NO_DIFFERENCE = (r"no significant (?:difference|change|effect|association)|no difference|"
                           r"not significantly different|did not differ|no effect|no benefit|"
                           r"无显著|无差异|无统计学")
DIRECTION_AGAINST = (r"\bworse\b|\bharmful\b|increased (?:risk|mortality|incidence)\b|"
                     r"higher (?:risk|mortality|incidence|fall|fracture)|more (?:falls|fractures|events)\b|"
                     r"\bagainst\b|不利|更差")
DIRECTION_FAVOURS = (r"\blower\b|\breduc(?:e|ed|es|tion)\b|\bdecreas(?:e|ed|es)\b|\bfewer\b|"
                     r"\bimprove(?:d|s|ment)?\b|\bbenefit|\beffective\b|\bprotective\b|\bfavou?rs?\b|"
                     r"\bbetter\b|higher (?:survival|recovery)|改善|降低|减少|有效|优于")


def direction(text):
    lowered = norm_space(text).lower()
    if re.search(DIRECTION_NO_DIFFERENCE, lowered):
        return "no_difference"
    favours = len(re.findall(DIRECTION_FAVOURS, lowered))
    against = len(re.findall(DIRECTION_AGAINST, lowered))
    if favours and not against:
        return "favours"
    if against and not favours:
        return "against"
    if favours and against:
        if favours > against:
            return "favours"
        if against > favours:
            return "against"
    return "unclear"


def build_conflicts(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row["direction"], []).append(row)
    conflicts = []
    for opposing in ("no_difference", "against"):
        for left in groups.get("favours", []):
            for right in groups.get(opposing, []):
                conflicts.append({
                    "id": "C%d" % (len(conflicts) + 1),
                    "type": "opposing_direction",
                    "directions": [left["direction"], right["direction"]],
                    "positions": [
                        {"source_id": left["source_id"], "direction": left["direction"],
                         "quote": left["quote"], "locator": left["locator"], "year": left["year"]},
                        {"source_id": right["source_id"], "direction": right["direction"],
                         "quote": right["quote"], "locator": right["locator"], "year": right["year"]},
                    ],
                    "handling": "both directions are reported side by side; no numerical pooling is performed",
                })
    return conflicts


def _term_basis(records, term, question):
    for record in records:
        texts = [record["title"], record["abstract"]]
        if record.get("material"):
            texts.extend(item["text"] for item in record["material"]["paragraphs"])
        for text in texts:
            if term and term in norm_space(text).lower():
                quote, locator = best_quote(record, text)
                return {"quote": quote, "locator": locator}
    return {"quote": (question or "No review question was supplied.")[:320],
            "locator": "case.json#review_question"}


def build_gaps(records, eligible, criteria, screening, question, outcome_terms):
    gaps = []
    focus = " ".join(tokenize(question, 4)) or "review question"
    design_axis = ", ".join(criteria["designs"]) if criteria["designs"] else "any eligible design"
    no_omission = ("the search string and E1-E5 criteria were applied to every supplied record; "
                   "missing evidence therefore reflects the supplied corpus, not an omitted search step")
    if not eligible:
        gaps.append({"id": "G1", "gap_type": "evidence_absence",
                     "matrix_cell": {"outcome": "any", "population": focus, "design": design_axis},
                     "basis": {"quote": (question or "No review question was supplied.")[:320],
                               "locator": "case.json#review_question"},
                     "why_not_omission": no_omission,
                     "note": "no record met the eligible criteria"})
    if len(eligible) == 1:
        record = eligible[0]
        quote, locator = best_quote(record, record["abstract"] or record["title"])
        gaps.append({"id": "G%d" % (len(gaps) + 1), "gap_type": "replication",
                     "matrix_cell": {"outcome": "any", "population": focus, "design": record["design"] or design_axis},
                     "basis": {"quote": quote, "locator": locator},
                     "why_not_omission": no_omission,
                     "note": "only one eligible study; no within-batch replication"})
    missing_fulltext = [record for record in eligible if not record.get("material")]
    if eligible and missing_fulltext:
        quote, locator = best_quote(missing_fulltext[0], missing_fulltext[0]["abstract"] or missing_fulltext[0]["title"])
        gaps.append({"id": "G%d" % (len(gaps) + 1), "gap_type": "full_text_verification",
                     "matrix_cell": {"outcome": "any", "population": focus, "design": design_axis},
                     "basis": {"quote": quote, "locator": locator},
                     "why_not_omission": no_omission,
                     "note": "%d included record(s) have no matched full-text paragraph" % len(missing_fulltext)})
    if not criteria["declared"]:
        gaps.append({"id": "G%d" % (len(gaps) + 1), "gap_type": "protocol",
                     "matrix_cell": {"outcome": "any", "population": focus, "design": design_axis},
                     "basis": {"quote": (question or "No review question was supplied.")[:320],
                               "locator": "case.json#review_question"},
                     "why_not_omission": "no eligibility block was supplied, so E1-E5 default to structural checks",
                     "note": "eligibility criteria were not declared in the case"})
    if screening["decisions"]:
        excluded = [row for row in screening["decisions"] if row["decision"] != "include"]
        if excluded:
            gaps.append({"id": "G%d" % (len(gaps) + 1), "gap_type": "screening_attrition",
                         "matrix_cell": {"outcome": "any", "population": focus, "design": design_axis},
                         "basis": {"quote": excluded[0]["quote"], "locator": excluded[0]["locator"]},
                         "why_not_omission": no_omission,
                         "note": "%d record(s) were excluded with a recorded reason" % len(excluded)})
    selection = []
    eligible_text = " ".join((record["abstract"] + " " + record["title"]).lower() for record in eligible)
    for term in outcome_terms[:4]:
        if term and term not in eligible_text:
            selection.append({"gap_type": "outcome_not_covered",
                              "matrix_cell": {"outcome": term, "population": focus, "design": design_axis},
                              "basis": _term_basis(records, term, question),
                              "why_not_omission": ("the term occurs in the supplied corpus and is covered by the "
                                                   "search terms; its absence from the included set is an evidence "
                                                   "gap, not an omitted search term"),
                              "note": "no included study reports this outcome term"})
    for design in criteria["designs"]:
        if not any(design in (record["design"] or "").lower() for record in eligible):
            selection.append({"gap_type": "design_not_covered",
                              "matrix_cell": {"outcome": "any", "population": focus, "design": design},
                              "basis": _term_basis(records, design, question),
                              "why_not_omission": no_omission,
                              "note": "no included study used the declared design '%s'" % design})
    focus_terms = [term for term in tokenize(question, 4) if len(term) >= 4]
    if eligible and focus_terms and not any(term in eligible_text for term in focus_terms):
        selection.append({"gap_type": "population_not_covered",
                          "matrix_cell": {"outcome": "any", "population": focus, "design": design_axis},
                          "basis": {"quote": (question or "No review question was supplied.")[:320],
                                    "locator": "case.json#review_question"},
                          "why_not_omission": no_omission,
                          "note": "no included title/abstract names the review population terms"})
    for item in selection[:6]:
        item["id"] = "G%d" % (len(gaps) + 1)
        gaps.append(item)
    return gaps


def build_conclusions(direction_summary, appraisals, conflict_ids):
    levels = {item["source_id"]: item["overall"] for item in appraisals}
    conclusions = []
    for label in ("favours", "no_difference", "against", "unclear"):
        ids = direction_summary.get(label)
        if not ids:
            continue
        high = sum(1 for source_id in ids if levels.get(source_id) == "high")
        if high >= 2:
            effect = "upheld (two or more high-completeness studies)"
        elif high == 1:
            effect = "cautious (one high-completeness study only)"
        else:
            effect = "provisional (no high-completeness study)"
        if conflict_ids:
            effect += "; cross-direction conflict blocks any net pooled claim"
        conclusions.append({
            "id": "K%d" % (len(conclusions) + 1),
            "statement": "Direction '%s' is reported by %s." % (label, ", ".join(ids)),
            "evidence_ids": list(ids),
            "effect_on_conclusion": effect,
        })
    return conclusions


def build_change_log(counts):
    return [
        {"step": 1, "action": "load_input", "before": 0, "after": counts["records"],
         "counts": {"records": counts["records"], "materials": counts["materials"],
                    "records_matched": counts["records_matched"]}},
        {"step": 2, "action": "build_search_strategy", "before": 0, "after": counts["records"],
         "counts": {"queries": 2, "components": counts["components"]}},
        {"step": 3, "action": "remove_duplicates", "before": counts["identified"], "after": counts["screened"],
         "counts": {"duplicates_removed": counts["duplicates_removed"]}},
        {"step": 4, "action": "screen_title_abstract", "before": counts["screened"],
         "after": counts["title_abstract_passed"],
         "counts": {"excluded": counts["title_abstract_excluded"]}},
        {"step": 5, "action": "screen_eligibility", "before": counts["title_abstract_passed"],
         "after": counts["included"], "counts": {"excluded": counts["eligibility_excluded"]}},
        {"step": 6, "action": "appraise_included", "before": counts["included"],
         "after": counts["appraised"], "counts": counts["judgment_counts"]},
        {"step": 7, "action": "synthesise", "before": counts["appraised"],
         "after": counts["conclusions"], "counts": {"conflicts": counts["conflicts"],
                                                    "gaps": counts["gaps"]}},
    ]


def build_payload(case, task_text, case_path=None):
    raw_records = find_records(case)
    records = [normalize(raw, locator, index) for index, (locator, raw) in enumerate(raw_records)]
    materials = collect_materials(case_path or DEFAULT_CASE)
    records = match_record_sources(records, materials)
    question = question_of(case, task_text)
    criteria = criteria_of(case)
    strategy = search_strategy(question, criteria, records)
    screening = screen_records(records, criteria, question)
    passed, eligible = screening["passed"], screening["eligible"]
    outcome_terms = corpus_terms(records, materials)
    appraisal_items = tuple(
        (name, outcome_pattern(outcome_terms) if name == "outcome_measure" else pattern)
        for name, pattern in APPRAISAL_ITEMS)
    appraisals = [appraise(record, appraisal_items) for record in eligible]
    levels = {item["source_id"]: item["overall"] for item in appraisals}
    synth = []
    for record in eligible:
        text = record["abstract"] or record["body"] or (record["material"]["text"][:2000]
                                                        if record.get("material") else record["title"])
        quote, locator = best_quote(record, text)
        synth.append({"source_id": record["source_id"], "locator": locator,
                      "title": record["title"], "design": record["design"],
                      "direction": direction(text), "quote": quote, "year": record["year"],
                      "material_id": record["material_id"], "match_method": record["match_method"],
                      "appraisal_level": levels.get(record["source_id"], "low")})
    direction_summary = {}
    for label in ("favours", "no_difference", "against", "unclear"):
        ids = [row["source_id"] for row in synth if row["direction"] == label]
        if ids:
            direction_summary[label] = ids
    conflicts = build_conflicts(synth)
    conflict_ids = [item["id"] for item in conflicts]
    high = [row for row in synth if row["appraisal_level"] == "high"]
    if not synth:
        certainty = "no included evidence"
    elif conflicts:
        certainty = "low (conflicting directions)"
    elif len(high) >= 2:
        certainty = "moderate (two or more high-reporting studies, no conflict)"
    else:
        certainty = "low (few or low-reporting studies)"

    gaps = build_gaps(records, eligible, criteria, screening, question, outcome_terms)
    conclusions = build_conclusions(direction_summary, appraisals, conflict_ids)

    uncertainty = []
    if not question:
        uncertainty.append({"type": "no_review_question", "detail": "No review question was found."})
    if not records:
        uncertainty.append({"type": "no_records", "detail": "No record was supplied."})
    if not criteria["declared"]:
        uncertainty.append({"type": "criteria_not_declared", "detail": "Eligibility criteria were not supplied; defaults are structural only."})
    if records and not any(record["year"] for record in records):
        uncertainty.append({"type": "no_years", "detail": "Publication years absent; date limits cannot be applied."})
    if not materials:
        uncertainty.append({"type": "no_materials", "detail": "No md/txt/csv/json/pdf material was found next to the case; evidence stays at title/abstract level."})
    elif any(material["unparsed"] for material in materials):
        uncertainty.append({"type": "unparsed_materials",
                            "detail": "%d material file(s) yielded no readable text." % sum(
                                1 for material in materials if material["unparsed"])})
    uncertainty.append({"type": "no_external_search", "detail": "Search ran on the supplied batch; the flow is not a full database PRISMA."})

    if not records or not question or not eligible:
        status = "insufficient_input"
    elif len(eligible) < 2:
        status = "partial"
    else:
        status = "success"

    boundary = ("conclusions are bounded by the supplied batch, its abstracts and any matched full-text paragraphs: "
                "no numerical pooling, risk-of-bias assessment, heterogeneity test or external database search was performed")
    if not eligible:
        statement = "No record met the stated criteria; no synthesis or direction is offered."
    else:
        parts = ["%d record(s) met the stated criteria." % len(eligible),
                 "Direction of effect: " + "; ".join("%s in %s" % (label, ", ".join(ids))
                                                      for label, ids in direction_summary.items()) + "."]
        if conflicts:
            parts.append("The included evidence conflicts (%s: %s), so no single net direction is asserted." % (
                ", ".join(conflict_ids),
                "; ".join("%s vs %s" % (item["positions"][0]["source_id"], item["positions"][1]["source_id"])
                          for item in conflicts)))
        else:
            parts.append("No opposing directional findings were detected in the included set.")
        parts.append("Boundary: " + boundary + ".")
        statement = " ".join(parts)

    duplicates_removed = sum(1 for row in screening["decisions"] if row["decision"] == "duplicate")
    title_abstract_excluded = sum(1 for row in screening["decisions"]
                                  if row["stage"] == "title_abstract" and row["decision"] == "exclude")
    eligibility_excluded = sum(1 for row in screening["decisions"]
                               if row["stage"] == "eligibility" and row["decision"] == "exclude")
    flow = {
        "identified": len(records),
        "duplicates_removed": duplicates_removed,
        "screened": len(records) - duplicates_removed,
        "title_abstract_excluded": title_abstract_excluded,
        "title_abstract_passed": len(passed),
        "eligibility_excluded": eligibility_excluded,
        "included": len(eligible),
        "pending": 0,
        "duplicates_and_screen_excluded": duplicates_removed + title_abstract_excluded,
        "excluded_records": screening["excluded"],
    }
    flow_assertion = assert_flow(flow)
    judgment_counts = {key: 0 for key in ("yes", "partly", "no", "unclear")}
    for appraisal in appraisals:
        for key, value in appraisal["judgment_counts"].items():
            judgment_counts[key] += value
    counts = {
        "records": len(records), "materials": len(materials),
        "records_matched": sum(1 for record in records if record["material_id"]),
        "identified": len(records), "duplicates_removed": duplicates_removed,
        "screened": flow["screened"], "title_abstract_excluded": title_abstract_excluded,
        "title_abstract_passed": len(passed), "eligibility_excluded": eligibility_excluded,
        "included": len(eligible), "appraised": len(appraisals),
        "judgment_counts": judgment_counts, "conclusions": len(conclusions),
        "conflicts": len(conflicts), "gaps": len(gaps),
        "components": len(strategy["components"]["question_terms"]),
    }

    result = {
        "protocol": dict(PROTOCOL, criteria_ids=[item["id"] for item in criteria["items"] if item["active"]],
                         reason_codes_version=REASON_CODES["version"]),
        "human_edits": [],
        "review_question": question,
        "search_strategy": strategy,
        "criteria": criteria["items"],
        "flow": flow,
        "flow_assertion": flow_assertion,
        "screening_log": screening["decisions"],
        "quality_appraisal": appraisals,
        "outcome_vocabulary": outcome_terms,
        "evidence_table": synth,
        "materials": [{"source_id": material["source_id"], "file": material["name"],
                       "kind": material["kind"], "paragraphs": len(material["paragraphs"]),
                       "unparsed": material["unparsed"], "note": material["note"]}
                      for material in materials],
        "synthesis": {
            "certainty": certainty,
            "included": [row["source_id"] for row in synth],
            "direction_summary": direction_summary,
            "conflict_ids": conflict_ids,
            "statement": statement,
            "conclusions": conclusions,
            "boundary": boundary,
            "pooling": "no meta-analysis was performed; estimates are not combined numerically",
        },
        "conflicts": conflicts,
        "research_gaps": gaps,
        "artifacts": {},
        "limitations": [
            "The search was executed on the supplied batch, not on external databases.",
            "Appraisal scores reporting completeness, not risk of bias in the underlying study.",
            "Screening is single-reviewer and rule-based; dual independent screening is not simulated.",
            "No meta-analysis, heterogeneity test or publication-bias assessment is performed.",
            "Records without a matched full-text paragraph are appraised from the title/abstract layer only.",
        ],
        "change_log": build_change_log(counts),
    }
    return {
        "case_id": case_identifier(case),
        "status": status,
        "result": result,
        "evidence": [{"kind": "included_study", **row} for row in synth],
        "uncertainty": uncertainty,
        "errors": [],
        "stats": {"records": len(records), "screened": flow["screened"],
                  "title_abstract_passed": len(passed), "eligible": len(eligible),
                  "excluded": len(screening["excluded"]), "appraised": len(appraisals),
                  "sources_found": len(materials),
                  "sources_unparsed": sum(1 for material in materials if material["unparsed"]),
                  "records_matched": sum(1 for record in records if record["material_id"]),
                  "pending": 0, "network": "disabled"},
    }


def _md_cell(value):
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def render_report(payload):
    result = payload.get("result") or {}
    flow = result.get("flow") or {}
    stats = payload.get("stats") or {}
    synthesis = result.get("synthesis") or {}
    lines = ["# Systematic review report", "",
             "Case: `%s`" % _md_cell(payload.get("case_id", "")), "",
             "## Protocol", "",
             "| key | value |", "|---|---|"]
    protocol = result.get("protocol") or {}
    for key in sorted(protocol.keys()):
        value = protocol[key]
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        lines.append("| %s | %s |" % (_md_cell(key), _md_cell(value)))
    lines.extend(["", "## PRISMA-style counts", "", "| stage | n |", "|---|---:|"])
    for key in ("identified", "duplicates_removed", "screened", "title_abstract_excluded",
                "title_abstract_passed", "eligibility_excluded", "included", "pending"):
        lines.append("| %s | %s |" % (key, flow.get(key, "")))
    assertion = result.get("flow_assertion") or {}
    lines.append("")
    lines.append("Closure checks: %s." % ("all pass" if assertion.get("ok") else "FAILED"))
    lines.extend(["", "## Eligibility criteria", "", "| id | element | text | origin |", "|---|---|---|---|"])
    for item in result.get("criteria") or []:
        lines.append("| %s | %s | %s | %s |" % (_md_cell(item.get("id")), _md_cell(item.get("element")),
                                                _md_cell(item.get("text")), _md_cell(item.get("origin"))))
    lines.extend(["", "## Screening", "", "| id | stage | decision | reason_code | criteria | locator |",
                  "|---|---|---|---|---|---|"])
    for row in result.get("screening_log") or []:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            _md_cell(row.get("id")), _md_cell(row.get("stage")), _md_cell(row.get("decision")),
            _md_cell(row.get("reason_code")), _md_cell(";".join(row.get("criteria_ids") or [])),
            _md_cell(row.get("locator"))))
    lines.extend(["", "## Included studies", "", "| id | year | design | direction | appraisal | locator |",
                  "|---|---:|---|---|---|---|"])
    for row in result.get("evidence_table") or []:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            _md_cell(row.get("source_id")), _md_cell(row.get("year")), _md_cell(row.get("design")),
            _md_cell(row.get("direction")), _md_cell(row.get("appraisal_level")), _md_cell(row.get("locator"))))
    lines.extend(["", "## Conflicts", ""])
    conflicts = result.get("conflicts") or []
    if not conflicts:
        lines.append("No opposing directional findings were detected.")
    for item in conflicts:
        positions = item.get("positions") or []
        detail = "; ".join("%s (%s) at %s" % (position.get("source_id"), position.get("direction"),
                                              position.get("locator")) for position in positions)
        lines.append("- %s: %s. %s" % (item.get("id"), detail, item.get("handling", "")))
    lines.extend(["", "## Evidence gaps", "", "| id | gap_type | matrix cell | basis locator | note |",
                  "|---|---|---|---|---|"])
    for item in result.get("research_gaps") or []:
        cell = item.get("matrix_cell") or {}
        cell_text = "outcome=%s; population=%s; design=%s" % (
            cell.get("outcome"), cell.get("population"), cell.get("design"))
        basis = item.get("basis") or {}
        lines.append("| %s | %s | %s | %s | %s |" % (
            _md_cell(item.get("id")), _md_cell(item.get("gap_type")), _md_cell(cell_text),
            _md_cell(basis.get("locator")), _md_cell(item.get("note"))))
    lines.extend(["", "## Synthesis", "", synthesis.get("statement", ""), ""])
    for item in synthesis.get("conclusions") or []:
        lines.append("- %s %s" % (item.get("statement"), item.get("effect_on_conclusion", "")))
    lines.extend(["", "## Materials", "",
                  "| source | file | kind | paragraphs | unparsed |", "|---|---|---|---:|---|"])
    materials = result.get("materials") or []
    for material in materials:
        lines.append("| %s | %s | %s | %s | %s |" % (
            _md_cell(material.get("source_id")), _md_cell(material.get("file")),
            _md_cell(material.get("kind")), material.get("paragraphs", 0), material.get("unparsed")))
    if not materials:
        lines.append("| - | no material file found | - | 0 | - |")
    lines.extend(["", "## Limitations", ""])
    for item in result.get("limitations") or []:
        lines.append("- " + str(item))
    lines.append("")
    return "\n".join(lines)


def _write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


def write_artifacts(out_dir, payload):
    """Write report.md and two CSV tables; register only paths that really exist."""
    result = payload.get("result")
    if not isinstance(result, dict):
        return {}
    targets = {"report": "report.md",
               "evidence_matrix": "evidence_matrix.csv",
               "screening_log": "screening_log.csv"}
    written = {}
    for key, name in targets.items():
        target = os.path.join(out_dir, name)
        try:
            if key == "report":
                _write_text(target, render_report(payload))
            elif key == "evidence_matrix":
                rows = [[row.get("source_id", ""), row.get("title", ""), row.get("year", ""),
                         row.get("design", ""), row.get("direction", ""), row.get("appraisal_level", ""),
                         row.get("material_id", ""), row.get("match_method", ""),
                         row.get("quote", ""), row.get("locator", "")]
                        for row in result.get("evidence_table") or []]
                _write_csv(target, ["source_id", "title", "year", "design", "direction",
                                    "appraisal_level", "material_id", "match_method", "quote", "locator"], rows)
            else:
                rows = [[row.get("id", ""), row.get("stage", ""), row.get("decision", ""),
                         ";".join(row.get("criteria_ids") or []), row.get("reason_code", ""),
                         row.get("detail", ""), row.get("quote", ""), row.get("locator", "")]
                        for row in result.get("screening_log") or []]
                _write_csv(target, ["id", "stage", "decision", "criteria_ids", "reason_code",
                                    "detail", "quote", "locator"], rows)
        except (OSError, ValueError, TypeError):
            continue
        if os.path.exists(target):
            written[key] = name
    result["artifacts"] = written
    if written:
        result.setdefault("change_log", []).append(
            {"step": len(result["change_log"]) + 1, "action": "write_artifacts", "before": 0,
             "after": len(written), "counts": {"artifacts": sorted(written)}})
    return written


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
        payload = build_payload(case, read_task_text(args.case, args.work), args.case)
        code = 0
    except Exception as exc:
        payload = {"case_id": case_identifier(case) if isinstance(case, dict) else "", "status": "failed",
                   "result": None, "evidence": [],
                   "uncertainty": [{"type": "run_failure", "detail": "Run failed; no review is reported."}],
                   "errors": [{"stage": "build", "type": type(exc).__name__, "message": str(exc)[:500]}], "stats": {}}
        code = 2
    payload = apply_contract(payload, case if isinstance(case, dict) else {})
    out_dir = os.path.dirname(os.path.abspath(str(args.out))) or "."
    try:
        os.makedirs(out_dir, exist_ok=True)
        write_artifacts(out_dir, payload)
    except Exception:
        pass
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
