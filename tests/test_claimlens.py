import io
import sys
from datetime import date
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from app.agent import run_text
from app.extract import categorize, parse_claim
from app.ingest import chunk_pages, pages_from_text
from app.main import app
from app.models import ClaimData, LineItem
from app.policy_terms import extract_terms, item_matches
from app.retrieval import BM25
from app.rules import adjudicate, inr
from app.samples import SAMPLES, policy_text
from app.textutil import NUM_RE, months_between, parse_date


def run(key):
    s = SAMPLES[key]
    return run_text(s["policy"], s["bill"], s["discharge"])


# ------------------------------------------------------------------ helpers
def test_parse_date_is_day_first():
    assert parse_date("12/03/2026") == date(2026, 3, 12)
    assert parse_date("5 Oct 2026") == date(2026, 10, 5)
    assert parse_date("2026-10-05") == date(2026, 10, 5)


def test_months_between():
    assert months_between(date(2023, 4, 1), date(2026, 3, 12)) == 35
    assert months_between(date(2025, 6, 10), date(2025, 12, 2)) == 5


def test_number_regex_handles_indian_grouping_and_rs_dot():
    assert [m.group(1) for m in NUM_RE.finditer("Rs.12,000 and 1,50,000.50")] == ["12,000", "1,50,000.50"]


def test_inr_format():
    assert inr(115800) == "₹1,15,800.00"
    assert inr(88800) == "₹88,800.00"


def test_item_matching_is_word_based_and_plural_safe():
    assert item_matches("gloves", "Consumables (gloves, masks)")
    assert item_matches("registration charges", "Registration / admission charges")
    assert not item_matches("gloves", "Pharmacy")


# ------------------------------------------------------- retrieval + terms
def test_clause_chunking_keeps_page_numbers():
    chunks = chunk_pages(pages_from_text(policy_text("X", "01/01/2024", 500000)))
    by_id = {c.id: c for c in chunks}
    assert by_id["4.1"].page == 4 and "1%" in by_id["4.1"].text
    assert by_id["3.2"].page == 3


def test_bm25_finds_copay_clause():
    chunks = chunk_pages(pages_from_text(policy_text("X", "01/01/2024", 500000)))
    top = BM25(chunks).search("co-payment insured shall bear", 1)[0][0]
    assert top.id == "4.5"


def test_terms_extracted_with_citations():
    t = extract_terms(pages_from_text(policy_text("X", "01/04/2023", 500000)))
    assert t.sum_insured == 500000 and t.inception_date == date(2023, 4, 1)
    assert t.room_cap_per_day == 5000 and t.icu_cap_per_day == 10000
    assert t.copay_pct == 10 and t.initial_waiting_days == 30
    assert t.specific_waiting_months == 24 and t.ped_waiting_months == 36
    assert "hernia" in t.specific_conditions and "cataract" in t.specific_conditions
    assert set(t.proportionate_categories) == {"nursing", "doctor_fee", "ot_charges", "diagnostics"}
    assert {s.name for s in t.sublimits} == {"cataract", "knee replacement"}
    assert t.citations["copay"].clause == "4.5" and t.citations["copay"].page == 4
    assert t.missing == []


def test_missing_schedule_is_an_error():
    with pytest.raises(ValueError):
        extract_terms(["no schedule here"])


def test_missing_policy_term_forces_review():
    text = policy_text("X", "01/04/2023", 500000).replace(
        "The insured shall bear 10% of the admissible claim amount, applied after sub-limits.", "Not applicable.")
    t = extract_terms(pages_from_text(text))
    assert "co-payment" in t.missing
    c = parse_claim(SAMPLES["mehta-hernia"]["bill"], SAMPLES["mehta-hernia"]["discharge"])
    assert adjudicate(c, t).decision == "needs_human_review"


# ------------------------------------------------------------- extraction
def test_categorize():
    assert categorize("ICU CHGS") == "icu_rent"
    assert categorize("Surgical mesh implant") == "implant"
    assert categorize("Pathology & radiology") == "diagnostics"
    assert categorize("misc. hospital services") == "other"


def test_bill_parser_handles_messy_formats():
    s = SAMPLES["iyer-pneumonia"]
    c = parse_claim(s["bill"], s["discharge"])
    assert c.admission_date == date(2026, 10, 5) and c.discharge_date == date(2026, 10, 10)
    assert c.stated_total == 79250
    amounts = {li.description.lower()[:8]: li.amount for li in c.line_items}
    assert len(c.line_items) == 7 and sum(li.amount for li in c.line_items) == 78250
    icu = next(li for li in c.line_items if li.category == "icu_rent")
    assert (icu.days, icu.rate_per_day, icu.amount) == (2, 12000, 24000)
    ward = next(li for li in c.line_items if li.category == "room_rent")
    assert (ward.days, ward.rate_per_day) == (3, 4000)
    assert amounts  # advance / balance / patient lines were not mistaken for items


def test_header_lines_with_numbers_are_not_line_items():
    c = parse_claim(SAMPLES["mehta-hernia"]["bill"], SAMPLES["mehta-hernia"]["discharge"])
    assert len(c.line_items) == 10 and c.stated_total == 115800


# ------------------------------------------------------------ adjudication
def test_mehta_matches_hand_calculation():
    r = run("mehta-hernia")
    ratio = D(5000) / D(6000)
    admissible = D(20000) + (D(4000) + D(35000) + D(8000) + D(12000) + D(6000)) * ratio + D(9500) + D(15000)
    expected = (admissible * D("0.9")).quantize(D("0.01"))
    assert r.decision == "partially_approved"
    assert r.billed_total == 115800
    assert r.payable_total == pytest.approx(float(expected), abs=0.02) == pytest.approx(88800, abs=0.02)
    assert r.review_flags == []
    nonpay = {l.description for l in r.lines if l.payable == 0}
    assert any("Consumables" in n for n in nonpay) and any("Registration" in n for n in nonpay)
    pharm = next(l for l in r.lines if l.category == "pharmacy")
    assert pharm.payable == pharm.billed  # pharmacy is exempt from proportionate deduction


def test_money_identity_holds():
    for key in SAMPLES:
        r = run(key)
        itemised = sum(l.deducted for l in r.lines + r.adjustments)
        assert itemised == pytest.approx(r.billed_total - r.payable_total, abs=0.05)


def test_every_deduction_cites_a_clause():
    r = run("mehta-hernia")
    for l in r.lines + r.adjustments:
        if l.deducted > 0:
            assert l.citation is not None, l.description


def test_waiting_period_rejection():
    r = run("sharma-cataract")
    assert r.decision == "rejected" and r.payable_total == 0
    assert r.rejection_reasons[0].citation.clause == "3.2"
    assert all(l.payable == 0 for l in r.lines)


def test_messy_bill_goes_to_human_review_with_provisional_amount():
    r = run("iyer-pneumonia")
    assert r.decision == "needs_human_review" and r.provisional
    assert any("79,250" in f for f in r.review_flags)  # stated total mismatch
    assert any("pre-existing" in f.lower() or "past history" in f.lower() for f in r.review_flags)
    assert r.payable_total == pytest.approx(64635.00, abs=0.02)


def _terms():
    return extract_terms(pages_from_text(policy_text("X", "01/04/2023", 500000)))


def _claim(**kw):
    base = dict(admission_date=date(2026, 3, 12), discharge_date=date(2026, 3, 13), diagnosis="Fracture femur",
                procedure="ORIF", stated_total=10000,
                line_items=[LineItem(description="Surgeon fee", amount=10000, category="doctor_fee")])
    base.update(kw)
    return ClaimData(**base)


def test_initial_waiting_rejects_illness_but_not_accident():
    t = _terms()
    early = date(2023, 4, 10)
    r = adjudicate(_claim(admission_date=early, discharge_date=date(2023, 4, 11)), t)
    assert r.decision == "rejected" and r.rejection_reasons[0].citation.clause == "3.1"
    r = adjudicate(_claim(admission_date=early, discharge_date=date(2023, 4, 11), is_accident=True), t)
    assert r.decision != "rejected"


def test_exclusion_and_accident_exception():
    t = _terms()
    r = adjudicate(_claim(diagnosis="Infertility", procedure="IVF"), t)
    assert r.decision == "rejected" and r.rejection_reasons[0].citation.clause == "5.2"
    ok = adjudicate(_claim(diagnosis="Dental fracture", procedure="Dental implant", is_accident=True), t)
    assert ok.decision != "rejected"


def test_ped_related_diagnosis_rejected_inside_window():
    t = extract_terms(pages_from_text(policy_text("X", "01/01/2025", 500000)))
    r = adjudicate(_claim(admission_date=date(2026, 3, 1), discharge_date=date(2026, 3, 2),
                          diagnosis="Diabetic foot ulcer", procedure="Debridement",
                          past_history="Type 2 diabetes since 2019"), t)
    assert r.decision == "rejected" and r.rejection_reasons[0].citation.clause == "3.3"


def test_sublimit_then_copay_order():
    t = extract_terms(pages_from_text(policy_text("X", "01/04/2022", 500000)))
    c = _claim(diagnosis="Senile cataract", procedure="Phaco with IOL", stated_total=60000,
               line_items=[LineItem(description="Surgeon fee", amount=60000, category="doctor_fee")])
    r = adjudicate(c, t)
    assert r.decision == "partially_approved"
    assert r.payable_total == pytest.approx(36000)  # 40,000 sub-limit, then 10% co-pay
    assert [a.description for a in r.adjustments][0].startswith("Sub-limit")


def test_bilateral_cataract_doubles_sublimit():
    t = extract_terms(pages_from_text(policy_text("X", "01/04/2022", 500000)))
    c = _claim(diagnosis="Cataract both eyes", procedure="Phaco", bilateral=True, stated_total=100000,
               line_items=[LineItem(description="Surgeon fee", amount=100000, category="doctor_fee")])
    assert adjudicate(c, t).payable_total == pytest.approx(72000)  # 80,000 less 10%


def test_no_items_routes_to_human():
    r = adjudicate(ClaimData(admission_date=date(2026, 3, 1)), _terms())
    assert r.decision == "needs_human_review"


# --------------------------------------------------------------------- API
client = TestClient(app)


def test_api_health_and_samples():
    assert client.get("/healthz").json()["ok"] is True
    assert len(client.get("/api/samples").json()) == 3
    assert client.get("/api/samples/nope").status_code == 404
    assert client.get("/").status_code == 200


def test_api_adjudicate_text():
    s = SAMPLES["mehta-hernia"]
    r = client.post("/api/adjudicate", json={"policy": s["policy"], "bill": s["bill"], "discharge": s["discharge"]})
    assert r.status_code == 200 and r.json()["decision"] == "partially_approved"


def test_api_bad_policy_is_422_not_500():
    s = SAMPLES["mehta-hernia"]
    r = client.post("/api/adjudicate", json={"policy": "garbage", "bill": s["bill"], "discharge": s["discharge"]})
    assert r.status_code == 422


def test_api_upload_text_files():
    s = SAMPLES["sharma-cataract"]
    files = {k: (f"{k}.txt", io.BytesIO(s[k].encode()), "text/plain") for k in ("policy", "bill", "discharge")}
    r = client.post("/api/adjudicate/upload", files=files)
    assert r.status_code == 200 and r.json()["decision"] == "rejected"


def test_api_upload_real_pdf():
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    def pdf(text):
        buf = io.BytesIO()
        cv = canvas.Canvas(buf, pagesize=A4)
        y = 800
        import textwrap
        for raw in text.splitlines():
            if raw.startswith("--- Page"):
                cv.showPage(); y = 800; continue
            for line in textwrap.wrap(raw, 105) or [""]:  # wrap like a real PDF would
                cv.setFont("Helvetica", 9); cv.drawString(30, y, line); y -= 12
                if y < 40: cv.showPage(); y = 800
        cv.save()
        return buf.getvalue()

    s = SAMPLES["mehta-hernia"]
    files = {k: (f"{k}.pdf", io.BytesIO(pdf(s[k])), "application/pdf") for k in ("policy", "bill", "discharge")}
    r = client.post("/api/adjudicate/upload", files=files)
    assert r.status_code == 200, r.text
    assert r.json()["payable_total"] == pytest.approx(88800, abs=0.02)
