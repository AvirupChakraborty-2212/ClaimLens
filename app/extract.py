"""Claim extraction: bill + discharge summary -> ClaimData.

Two extractors share one schema:
  * heuristic  - regex/keyword parser, no network, used by tests and when no API key is set
  * gemini     - LLM extraction (also reads scanned PDFs/images), used when GEMINI_API_KEY is set
The LLM path validates against the same pydantic schema and falls back to the heuristic
parser if the call or validation fails.
"""
from __future__ import annotations

import re

from .models import ClaimData, LineItem
from .textutil import NUM_RE, money, parse_date

CATEGORY_RULES: list[tuple[str, str]] = [
    ("icu_rent", r"\bicu\b|intensive care|\biccu\b|\bnicu\b"),
    ("room_rent", r"\broom\b|\bward\b|bed charge"),
    ("nursing", r"nursing"),
    ("ot_charges", r"\bot\b|operation theat|theatre|theater"),
    ("doctor_fee", r"surgeon|anaesth|anesth|doctor|\bdr\.?\b|consultant|consultation|physician|visit"),
    ("diagnostics", r"\blab\b|patholog|radiolog|x-?ray|\bmri\b|\bct\b|\becg\b|\busg\b|ultrasound|diagnostic|investigation|blood test"),
    ("implant", r"implant|mesh|stent|prosthe|\blens\b|\biol\b|screw"),
    ("pharmacy", r"pharmacy|medicine|drug|medication"),
    ("consumables", r"consumable|glove|\bmask|syringe|needle|gown|disposable"),
    ("admin", r"registration|admission charge|admin|file charge|documentation|service charge"),
]

SKIP_LINE = re.compile(
    r"\b(age|uhid|mr\s?no|bill\s?no|invoice|gstin?|phone|ph|mobile|advance|deposit|paid|balance|"
    r"insurance|tpa|policy|doa|dod|date|dob|patient|sub[\s-]?total)\b", re.I)
TOTAL_LINE = re.compile(r"\b(grand\s+total|total\s+bill|bill\s+total|gross\s+total|total\s+amount|total)\b", re.I)


def categorize(desc: str) -> str:
    d = desc.lower()
    for cat, pat in CATEGORY_RULES:
        if re.search(pat, d):
            return cat
    return "other"


def _trailing_amount(line: str):
    """Return (description, amount) if the line *ends* with an amount, else None."""
    s = re.sub(r"(/-|\.-|\bonly\b)\s*$", "", line.strip(), flags=re.I).strip()
    nums = list(NUM_RE.finditer(s))
    if not nums:
        return None
    last = nums[-1]
    if s[last.end():].strip():  # text after the last number -> not an amount column
        return None
    desc = s[:last.start()]
    desc = re.sub(r"(rs\.?|inr|₹)\s*$", "", desc.strip(), flags=re.I).strip(" .:-\t")
    return desc, money(last.group(1))


def _days_rate(desc: str, amount: float):
    days = rate = None
    dm = re.search(r"(\d+)\s*(?:days?|d)\b", desc, re.I)
    if dm:
        days = int(dm.group(1))
    rm = re.search(r"(?:rs\.?|₹|inr)?\s*([\d,]+(?:\.\d+)?)\s*(?:/|per)\s*day", desc, re.I) or \
         re.search(r"(?:@|\bx\b|\*)\s*(?:rs\.?|₹|inr)?\s*([\d,]+(?:\.\d+)?)", desc, re.I)
    if rm:
        rate = money(rm.group(1))
    if days and not rate:
        rate = round(amount / days, 2)
    return days, rate


def parse_claim(bill_text: str, discharge_text: str) -> ClaimData:
    c = ClaimData(extractor="heuristic")
    both = bill_text + "\n" + discharge_text

    m = re.search(r"patient(?:\s*name)?\s*[:\-]\s*([A-Za-z][A-Za-z .]+?)(?=\s{2,}|\s+(?:age|uhid|mr)\b|$)", both, re.I | re.M)
    c.patient_name = m.group(1).strip() if m else None

    m = re.search(r"(?:date\s+of\s+admission|admission\s+date|admitted\s+on|\bdoa\b)\s*[:\-]?\s*([^\n]+)", both, re.I)
    c.admission_date = parse_date(m.group(1)) if m else None
    m = re.search(r"(?:date\s+of\s+discharge|discharge\s+date|discharged\s+on|\bdod\b)\s*[:\-]?\s*([^\n]+)", both, re.I)
    c.discharge_date = parse_date(m.group(1)) if m else None

    def field(label: str, text: str) -> str:
        fm = re.search(rf"^\s*(?:{label})\s*[:\-]\s*(.+)$", text, re.I | re.M)
        return fm.group(1).strip() if fm else ""

    c.diagnosis = field(r"(?:final\s+)?diagnosis", discharge_text)
    c.procedure = field(r"(?:procedure|surgery|treatment)(?:\s+done)?", discharge_text)
    c.past_history = field(r"(?:past|medical)\s+history|known\s+case\s+of", discharge_text)
    if re.search(r"^none|^nil|no significant", c.past_history, re.I):
        c.past_history = ""
    c.is_accident = bool(re.search(r"\b(accident|road traffic|rta)\b", discharge_text, re.I))
    c.bilateral = bool(re.search(r"\bbilateral\b|both eyes", discharge_text, re.I))

    totals: list[tuple[str, float]] = []
    for raw in bill_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parsed = _trailing_amount(line)
        if not parsed:
            continue
        desc, amt = parsed
        if TOTAL_LINE.search(desc) and not re.search(r"sub[\s-]?total", desc, re.I) \
                and not re.search(r"\b(advance|balance|paid)\b", desc, re.I):
            totals.append((desc.lower(), amt))
            continue
        if SKIP_LINE.search(desc) or amt <= 0 or len(re.findall(r"[A-Za-z]", desc)) < 3:
            continue
        days, rate = _days_rate(raw, amt)
        cat = categorize(desc)
        li = LineItem(description=desc, amount=amt, category=cat)
        if cat in ("room_rent", "icu_rent"):
            li.days, li.rate_per_day = days, rate
            if not days:
                c.notes.append(f"'{desc}': no day count found; room cap cannot be checked for this line.")
        c.line_items.append(li)

    grand = [a for d, a in totals if "grand" in d] or [a for d, a in totals]
    c.stated_total = grand[0] if grand else None
    return c


# ------------------------------------------------------------------- LLM path
CLAIM_PROMPT = """You extract data from an Indian hospital bill and discharge summary for a health-insurance claim.
Return ONLY JSON matching this schema (dates as YYYY-MM-DD, amounts as plain numbers in INR):
{"patient_name": str|null, "admission_date": str|null, "discharge_date": str|null,
 "diagnosis": str, "procedure": str, "past_history": str, "is_accident": bool, "bilateral": bool,
 "stated_total": number|null,
 "line_items": [{"description": str, "amount": number, "days": int|null, "rate_per_day": number|null}]}
Rules: copy line items exactly as printed, one entry per billed row, do NOT include totals, advances or balances.
For room/ward/ICU rows fill days and rate_per_day when printed. Do not invent values; use null when absent.
"""


def parse_claim_llm(llm, bill_text: str, discharge_text: str, files=None) -> ClaimData:
    data = llm.json_call(CLAIM_PROMPT + f"\n--- BILL ---\n{bill_text}\n--- DISCHARGE SUMMARY ---\n{discharge_text}",
                         files=files)
    items = []
    for it in data.get("line_items", []):
        items.append(LineItem(description=str(it["description"]), amount=float(it["amount"]),
                              category=categorize(str(it["description"])),
                              days=it.get("days"), rate_per_day=it.get("rate_per_day")))
    c = ClaimData(
        patient_name=data.get("patient_name"),
        admission_date=parse_date(data.get("admission_date") or ""),
        discharge_date=parse_date(data.get("discharge_date") or ""),
        diagnosis=data.get("diagnosis") or "", procedure=data.get("procedure") or "",
        past_history=data.get("past_history") or "", is_accident=bool(data.get("is_accident")),
        bilateral=bool(data.get("bilateral")), stated_total=data.get("stated_total"),
        line_items=items, extractor="gemini")
    return c
