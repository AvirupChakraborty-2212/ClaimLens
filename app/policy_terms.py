"""Turn a policy document into structured, citable terms.

Every term is found by *retrieving* the relevant clause (BM25) and parsing it, so each
number the rules engine uses carries the clause id, page and quote it came from.
Parsers are written for numbered-clause wording ("4.1 Room rent ..."). For an insurer
with very different wording, swap `_parse_*` for an LLM extractor and keep the
retrieval + citation structure.
"""
from __future__ import annotations

import re

from .ingest import Chunk, chunk_pages
from .models import Citation, Exclusion, NonPayable, PolicyTerms, Sublimit
from .retrieval import BM25
from .textutil import money, parse_date, squash, stem

CATEGORY_WORDS = {
    "nursing": "nursing",
    "surgeon": "doctor_fee",
    "anaesthetist": "doctor_fee",
    "anesthetist": "doctor_fee",
    "operation theatre": "ot_charges",
    "diagnostics": "diagnostics",
}
GENERIC = {"surgery", "treatment", "procedure", "procedures", "or", "and", "the", "of", "injury", "related"}


def _cite(ch: Chunk) -> Citation:
    q = ch.text if len(ch.text) <= 320 else ch.text[:317] + "..."
    return Citation(clause=ch.id, page=ch.page, quote=q)


def _find(index: BM25, query: str, regex: re.Pattern, k: int = 4):
    for ch, _ in index.search(query, k):
        m = regex.search(ch.text)
        if m:
            return m, ch
    return None, None


def extract_terms(pages: list[str]) -> PolicyTerms:
    chunks = chunk_pages(pages)
    index = BM25(chunks)
    full = "\n".join(pages)
    missing: list[str] = []
    cites: dict[str, Citation] = {}

    # ---- schedule (not clause-numbered) -------------------------------------
    si = re.search(r"sum\s+insured\s*[:\-]?\s*(?:rs\.?|₹|inr)?\s*([\d,]+)", full, re.I)
    inc = re.search(r"(?:date\s+of\s+)?first\s+inception\s*[:\-]?\s*([^\n]+)", full, re.I)
    holder = re.search(r"policyholder\s*[:\-]\s*([^\n]+)", full, re.I)
    if not si or not inc or not parse_date(inc.group(1)):
        raise ValueError("Policy schedule not found: need 'Sum Insured' and 'Date of first inception'.")
    sum_insured = money(si.group(1))
    inception = parse_date(inc.group(1))
    sched_page = next((i for i, p in enumerate(pages, 1) if inc.group(0) in p), 1)
    cites["schedule"] = Citation(clause="Schedule", page=sched_page,
                                 quote=squash(f"Sum Insured {si.group(1)}; first inception {inc.group(1)}"))

    t = PolicyTerms(policyholder=holder.group(1).strip() if holder else None,
                    sum_insured=sum_insured, inception_date=inception)

    # ---- room / ICU caps -----------------------------------------------------
    m, ch = _find(index, "room rent limited per day sum insured",
                  re.compile(r"room rent.*?(\d+(?:\.\d+)?)\s*%\s*of\s*(?:the\s*)?sum insured", re.I))
    if m:
        t.room_cap_pct = float(m.group(1))
        t.room_cap_per_day = round(sum_insured * t.room_cap_pct / 100, 2)
        cites["room_cap"] = _cite(ch)
    else:
        missing.append("room rent cap")

    m, ch = _find(index, "ICU charges limited per day sum insured",
                  re.compile(r"icu.*?(\d+(?:\.\d+)?)\s*%\s*of\s*(?:the\s*)?sum insured", re.I))
    if m:
        t.icu_cap_pct = float(m.group(1))
        t.icu_cap_per_day = round(sum_insured * t.icu_cap_pct / 100, 2)
        cites["icu_cap"] = _cite(ch)
    else:
        missing.append("ICU cap")

    # ---- proportionate deduction --------------------------------------------
    m, ch = _find(index, "proportionate deduction associated charges room",
                  re.compile(r"proportion", re.I))
    if m:
        first_sentence = re.split(r"\.\s", ch.text, maxsplit=1)[0].lower()
        t.proportionate_categories = sorted({cat for w, cat in CATEGORY_WORDS.items() if w in first_sentence})
        cites["proportionate"] = _cite(ch)
    else:
        missing.append("proportionate deduction rule")

    # ---- waiting periods -----------------------------------------------------
    m, ch = _find(index, "initial waiting period first days inception",
                  re.compile(r"first\s+(\d+)\s+days", re.I))
    if m:
        t.initial_waiting_days = int(m.group(1))
        cites["initial_waiting"] = _cite(ch)
    else:
        missing.append("initial waiting period")

    m, ch = _find(index, "specific waiting period months following cataract hernia",
                  re.compile(r"after\s+(\d+)\s+months.*?:\s*(.+?)\.?$", re.I))
    if m:
        t.specific_waiting_months = int(m.group(1))
        t.specific_conditions = [c.strip().lower() for c in re.split(r",|;|\band\b", m.group(2)) if c.strip()]
        cites["specific_waiting"] = _cite(ch)
    else:
        missing.append("specific-disease waiting period")

    m, ch = _find(index, "pre-existing diseases waiting period months",
                  re.compile(r"pre-?existing.*?after\s+(\d+)\s+months", re.I))
    if m:
        t.ped_waiting_months = int(m.group(1))
        cites["ped_waiting"] = _cite(ch)
    else:
        missing.append("pre-existing disease waiting period")

    # ---- co-pay ----------------------------------------------------------------
    m, ch = _find(index, "co-payment insured shall bear percent admissible claim",
                  re.compile(r"bear\s+(\d+(?:\.\d+)?)\s*%", re.I))
    if m:
        t.copay_pct = float(m.group(1))
        cites["copay"] = _cite(ch)
    else:
        missing.append("co-payment")

    # ---- sub-limits ------------------------------------------------------------
    sub_re = re.compile(r"([A-Z][A-Za-z ]+?)(?: surgery)? is limited to (?:Rs\.?|₹)\s*([\d,]+)(?: per (\w+))?")
    for ch, _ in index.search("sub-limits limited to per eye surgery", 6):
        for sm in sub_re.finditer(ch.text):
            name = sm.group(1).strip().lower()
            if name.startswith(("room", "icu")):
                continue
            t.sublimits.append(Sublimit(name=name, amount=money(sm.group(2)),
                                        unit=sm.group(3), citation=_cite(ch)))

    # ---- exclusions & non-payables: whole sections ------------------------------
    for ch in chunks:
        top = ch.id.split(".")[0]
        if top == "5":
            phrase = re.split(r",|;| unless ", ch.text)[0].strip().rstrip(".")
            kws = [w for w in re.findall(r"[a-z][a-z\-]+", phrase.lower()) if w not in GENERIC]
            t.exclusions.append(Exclusion(phrase=phrase, keywords=kws,
                                          accident_exception=bool(re.search(r"unless.*accident", ch.text, re.I)),
                                          citation=_cite(ch)))
        if top == "6" and re.search(r"not payable", ch.text, re.I):
            listing = ch.text.split(":", 1)[-1]
            for item in re.split(r",|;|\band\b", listing):
                item = item.strip().rstrip(".")
                if item:
                    t.non_payable.append(NonPayable(item=item.lower(), citation=_cite(ch)))

    if not t.exclusions:
        missing.append("exclusions")
    if not t.non_payable:
        missing.append("non-payable items list")
    t.citations, t.missing = cites, missing
    return t


def item_matches(keyword_phrase: str, description: str) -> bool:
    """All words of the policy item appear (stemmed) in the bill line description."""
    kw = {stem(w) for w in re.findall(r"[A-Za-z]+", keyword_phrase)}
    desc = {stem(w) for w in re.findall(r"[A-Za-z]+", description)}
    return bool(kw) and kw <= desc
