"""Three synthetic claims. All names, hospitals and policy wording are invented demo data."""
from __future__ import annotations


def policy_text(holder: str, inception: str, si: int) -> str:
    si_fmt = f"{si:,}"
    return f"""--- Page 1 ---
CARELINK DEMO HEALTH POLICY (SAMPLE WORDING - NOT A REAL PRODUCT)
POLICY SCHEDULE
Policyholder: {holder}
Date of first inception: {inception}
Sum Insured: Rs. {si_fmt}
--- Page 2 ---
2. DEFINITIONS
2.1 Admissible claim amount means the hospital expenses payable after applying all limits in this policy.
2.2 Eligible room means the room category whose daily rent is within the limit in clause 4.1.
--- Page 3 ---
3. WAITING PERIODS
3.1 Initial waiting period: No illness is covered during the first 30 days from the date of first inception, except injury caused by an accident.
3.2 Specific waiting period: Expenses are payable only after 24 months of continuous coverage for the following: cataract, hernia, hysterectomy, joint replacement, kidney stones, piles, sinusitis, tonsillectomy.
3.3 Pre-existing diseases: Expenses related to pre-existing diseases are payable only after 36 months of continuous coverage.
--- Page 4 ---
4. LIMITS AND DEDUCTIONS
4.1 Room rent is limited to 1% of the Sum Insured per day.
4.2 ICU charges are limited to 2% of the Sum Insured per day.
4.3 Proportionate deduction: if the insured occupies a room costlier than the eligible room, nursing charges, surgeon and anaesthetist fees, operation theatre charges and diagnostics are reduced in the same proportion as the eligible room rent bears to the room rent actually charged. Pharmacy and implants are not subject to proportionate deduction.
4.4 Sub-limits: Cataract surgery is limited to Rs. 40,000 per eye. Knee replacement is limited to Rs. 1,50,000 per knee.
4.5 Co-payment: The insured shall bear 10% of the admissible claim amount, applied after sub-limits.
--- Page 5 ---
5. EXCLUSIONS
5.1 Cosmetic or plastic surgery, unless reconstructive following an accident.
5.2 Infertility treatment.
5.3 Dental treatment, unless required due to an accident.
5.4 Obesity or bariatric surgery.
5.5 Self-inflicted injury.
6. NON-PAYABLE ITEMS
6.1 The following items are not payable: registration charges, admission charges, gloves, masks, syringes, needles, gowns, disposable items, toiletries.
"""


MEHTA_BILL = """SUNRISE MULTISPECIALITY HOSPITAL, PUNE
Patient: Rahul Mehta    Age: 46    UHID: 88231
Date of Admission: 12/03/2026      Date of Discharge: 16/03/2026
Room: Deluxe Single AC @ Rs. 6,000 / day x 4 days

ITEMISED BILL
Room Rent (4 days @ 6,000/day)          24,000.00
Nursing charges                          4,000.00
Surgeon fee                             35,000.00
Anaesthetist fee                         8,000.00
OT charges                              12,000.00
Pharmacy (Bill #4421)                    9,500.00
Pathology & radiology                    6,000.00
Surgical mesh implant                   15,000.00
Consumables (gloves, masks)              1,800.00
Registration / admission charges           500.00
TOTAL BILL                             115,800.00
Advance paid                            50,000.00
"""

MEHTA_DS = """DISCHARGE SUMMARY
Patient: Rahul Mehta
Diagnosis: Right inguinal hernia
Procedure: Laparoscopic inguinal hernia repair with mesh
Past history: None
Condition at discharge: Stable
"""

SHARMA_BILL = """LOTUS EYE CARE CENTRE
Patient: Anita Sharma     Age: 63
DOA: 02/12/2025    DOD: 03/12/2025
Room charges (1 day @ Rs.3000)        3,000
Surgeon fee                          18,000
OT charges                            6,000
IOL lens implant                     12,000
Pharmacy                              1,800
Grand Total                          40,800
"""

SHARMA_DS = """Diagnosis: Senile cataract, left eye
Procedure: Phacoemulsification with foldable IOL, left eye
Past history: Hypertension
"""

IYER_BILL = """ st. mary's hospital   -  final bill
patient : Suresh Iyer     age 58      mr no 20931
doa 05-10-2026     dod 10-10-2026
ICU CHGS  2d x Rs.12,000          Rs. 24,000/-
general ward 3 days @ Rs 4000      Rs.12,000
nursing                             5,000.00
Physician visits (Dr. R. Iyer)      7,500
pharmacy   18250.00
pathology + radiology               9,400
misc. hospital services             2,100
GRAND TOTAL                  Rs. 79,250/-
advance paid   30,000
balance        49,250
"""

IYER_DS = """Diagnosis: Community acquired pneumonia with type 1 respiratory failure
Procedure: Medical management, ICU monitoring
Past history: Type 2 diabetes mellitus since 2019
"""

SAMPLES = {
    "mehta-hernia": {
        "title": "Hernia repair, costlier room than eligible",
        "expect": "Partially approved",
        "policy": policy_text("Rahul Mehta", "01/04/2023", 500000),
        "bill": MEHTA_BILL, "discharge": MEHTA_DS,
    },
    "sharma-cataract": {
        "title": "Cataract surgery inside the 24-month waiting period",
        "expect": "Rejected",
        "policy": policy_text("Anita Sharma", "10/06/2025", 300000),
        "bill": SHARMA_BILL, "discharge": SHARMA_DS,
    },
    "iyer-pneumonia": {
        "title": "Messy ICU bill, wrong total, diabetic history",
        "expect": "Needs human review",
        "policy": policy_text("Suresh Iyer", "01/02/2024", 500000),
        "bill": IYER_BILL, "discharge": IYER_DS,
    },
}
