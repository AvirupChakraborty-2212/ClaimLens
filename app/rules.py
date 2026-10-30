"""Deterministic adjudication. No LLM touches a rupee amount.

Order of operations (stated so a reviewer can audit it):
  1. verify the extracted claim (totals, dates, day counts)
  2. eligibility: waiting periods, pre-existing disease, exclusions  -> reject
  3. per-line: non-payable items, room/ICU caps, proportionate deduction
  4. sub-limits, then co-payment (policy 4.5: applied after sub-limits), then sum-insured cap
  5. decide: approved / partially approved / rejected / needs human review
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal as D

from .models import (Adjudication, ClaimData, LineDecision, PolicyTerms, Reason, Step)
from .policy_terms import item_matches
from .textutil import months_between

SYNONYMS = {"joint replacement": ["knee replacement", "hip replacement", "arthroplasty"]}
ROOM_CATS = {"room_rent", "icu_rent"}


def d(x) -> D:
    return D(str(x))


def q(x: D) -> D:
    return x.quantize(D("0.01"), ROUND_HALF_UP)


def inr(x) -> str:
    """Indian digit grouping: 1,15,800.00"""
    s = f"{float(x):.2f}"
    whole, frac = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        whole = head + "," + tail
    return f"₹{whole}.{frac}"


def _mentions(cond: str, text: str) -> bool:
    return item_matches(cond, text) or any(item_matches(s, text) for s in SYNONYMS.get(cond, []))


def _ld(desc, cat, billed, payable, reason, cite=None) -> LineDecision:
    billed, payable = q(d(billed)), q(d(payable))
    return LineDecision(description=desc, category=cat, billed=float(billed), payable=float(payable),
                        deducted=float(billed - payable), reason=reason, citation=cite)


def adjudicate(claim: ClaimData, terms: PolicyTerms) -> Adjudication:
    steps: list[Step] = []
    flags: list[str] = []
    reasons: list[Reason] = []
    items = claim.line_items
    billed = sum((d(i.amount) for i in items), D(0))

    def step(name, status, detail):
        steps.append(Step(name=name, status=status, detail=detail))
        if status == "flag":
            flags.append(detail)

    if not items:
        step("Verify claim data", "flag", "No billable line items could be extracted from the bill.")
        return _finish(claim, terms, steps, flags, reasons, [], [], D(0), D(0))

    # ------------------------------------------------------------ 1. verify
    s = sum(d(i.amount) for i in items)
    if claim.stated_total is None:
        step("Reconcile bill total", "flag", "Bill has no readable total to reconcile against.")
    elif abs(s - d(claim.stated_total)) > D("1"):
        step("Reconcile bill total", "flag",
             f"Line items sum to {inr(s)} but the bill states {inr(claim.stated_total)} "
             f"(difference {inr(abs(s - d(claim.stated_total)))}).")
    else:
        step("Reconcile bill total", "ok", f"{len(items)} line items sum to {inr(s)}, matching the bill total.")

    stay = None
    if claim.admission_date and claim.discharge_date:
        stay = (claim.discharge_date - claim.admission_date).days
        if stay < 0:
            step("Check dates", "flag", "Discharge date is before admission date.")
        else:
            step("Check dates", "ok", f"Admitted {claim.admission_date}, discharged {claim.discharge_date} ({stay} days).")
    else:
        step("Check dates", "flag", "Admission or discharge date could not be read.")

    room_days = 0
    for i in items:
        if i.category in ROOM_CATS and i.days and i.rate_per_day:
            room_days += i.days
            if abs(d(i.days) * d(i.rate_per_day) - d(i.amount)) > D("1"):
                step("Check room line", "flag",
                     f"'{i.description}': {i.days} days × {inr(i.rate_per_day)} ≠ billed {inr(i.amount)}.")
    if stay is not None and stay >= 0 and room_days and abs(room_days - stay) > 1:
        step("Check stay length", "flag", f"Room/ICU days billed ({room_days}) differ from length of stay ({stay}).")

    for m in terms.missing:
        step("Policy terms", "flag", f"Policy term not found in the document: {m}.")
    for n in claim.notes:
        step("Extraction", "flag", n)

    # ---------------------------------------------------------- 2. eligibility
    adm = claim.admission_date
    text = f"{claim.diagnosis} {claim.procedure}".lower()
    if not adm:
        step("Eligibility", "flag", "Admission date missing; waiting-period checks skipped.")
    else:
        days_in = (adm - terms.inception_date).days
        months_in = months_between(terms.inception_date, adm)
        if adm < terms.inception_date:
            reasons.append(Reason(text=f"Admission ({adm}) is before policy inception ({terms.inception_date}).",
                                  citation=terms.citations.get("schedule")))
        if terms.initial_waiting_days is not None:
            if days_in < terms.initial_waiting_days and not claim.is_accident:
                reasons.append(Reason(
                    text=f"Admitted {days_in} days after inception; initial waiting period is {terms.initial_waiting_days} days.",
                    citation=terms.citations.get("initial_waiting")))
            else:
                step("Initial waiting period", "ok",
                     f"{days_in} days since inception vs {terms.initial_waiting_days}-day waiting period"
                     + (" (accident exempt)." if claim.is_accident and days_in < terms.initial_waiting_days else "."))
        if terms.specific_waiting_months is not None:
            hit = next((c for c in terms.specific_conditions if _mentions(c, text)), None)
            if hit and months_in < terms.specific_waiting_months:
                reasons.append(Reason(
                    text=f"'{hit}' has a {terms.specific_waiting_months}-month waiting period; claim is at month {months_in}.",
                    citation=terms.citations.get("specific_waiting")))
            elif hit:
                step("Specific-disease waiting", "ok",
                     f"'{hit}' is a listed condition; month {months_in} ≥ {terms.specific_waiting_months}.")
            else:
                step("Specific-disease waiting", "ok", "Diagnosis/procedure is not a listed waiting-period condition.")
        if terms.ped_waiting_months is not None and claim.past_history and not reasons:
            hist = [h.strip() for h in re.split(r",|;|\band\b", claim.past_history) if h.strip()]
            related = [h for h in hist if any(w[:5] in text for w in re.findall(r"[a-z]{5,}", h.lower())
                                              if w not in {"type", "since", "history", "chronic"})]
            if related and months_in < terms.ped_waiting_months:
                reasons.append(Reason(
                    text=f"Diagnosis appears linked to pre-existing '{related[0]}'; PED waiting is "
                         f"{terms.ped_waiting_months} months, claim is at month {months_in}.",
                    citation=terms.citations.get("ped_waiting")))
            elif months_in < terms.ped_waiting_months:
                step("Pre-existing disease", "flag",
                     f"Past history recorded ({claim.past_history}); no text link to the diagnosis, but the "
                     f"{terms.ped_waiting_months}-month PED waiting is still running (month {months_in}). "
                     "Medical reviewer should confirm the diagnosis is unrelated.")
            else:
                step("Pre-existing disease", "ok", f"PED waiting period completed (month {months_in}).")
        for ex in terms.exclusions:
            if any(item_matches(k, text) for k in ex.keywords):
                if ex.accident_exception and claim.is_accident:
                    step("Exclusions", "ok", f"'{ex.phrase}' matched but accident exception applies.")
                else:
                    reasons.append(Reason(text=f"Excluded: {ex.phrase}.", citation=ex.citation))
        if not any(r for r in reasons):
            step("Exclusions", "ok", "No exclusion matched the diagnosis/procedure.")

    if reasons:
        step("Eligibility", "fail", " ".join(r.text for r in reasons))
        lines = [_ld(i.description, i.category, i.amount, 0, reasons[0].text, reasons[0].citation) for i in items]
        return _finish(claim, terms, steps, flags, reasons, lines, [], billed, D(0))

    # ------------------------------------------------- 3. per-line calculation
    elig_by_idx: dict[int, D] = {}
    room_actual = room_elig = D(0)
    nonpay: dict[int, object] = {}
    for idx, it in enumerate(items):
        np_ = next((n for n in terms.non_payable if item_matches(n.item, it.description)), None)
        if np_:
            nonpay[idx] = np_
            continue
        if it.category in ROOM_CATS:
            amt = d(it.amount)
            cap = terms.icu_cap_per_day if it.category == "icu_rent" else terms.room_cap_per_day
            days = it.days
            rate = d(it.rate_per_day) if it.rate_per_day else (amt / days if days else None)
            if cap is None or not days or rate is None:
                elig = amt
                if cap is not None:
                    step("Room cap", "flag", f"Cannot apply room cap to '{it.description}' (missing days/rate).")
            else:
                elig = min(amt, min(rate, d(cap)) * days)
            elig_by_idx[idx] = q(elig)
            room_actual += amt
            room_elig += elig
    ratio = (room_elig / room_actual) if room_actual > 0 and room_elig < room_actual else D(1)
    if ratio < 1:
        step("Proportionate deduction", "ok",
             f"Eligible room charges {inr(room_elig)} ÷ actual {inr(room_actual)} = {ratio:.4f}; "
             f"applied to: {', '.join(terms.proportionate_categories)}.")
    elif room_actual > 0:
        step("Room caps", "ok", "Room/ICU charges are within policy limits; no proportionate deduction.")

    lines: list[LineDecision] = []
    for idx, it in enumerate(items):
        amt = d(it.amount)
        if idx in nonpay:
            n = nonpay[idx]
            lines.append(_ld(it.description, it.category, amt, 0, f"Non-payable item: {n.item}.", n.citation))
        elif it.category in ROOM_CATS:
            e = elig_by_idx[idx]
            if e < q(amt):
                key = "icu_cap" if it.category == "icu_rent" else "room_cap"
                cap = terms.icu_cap_per_day if it.category == "icu_rent" else terms.room_cap_per_day
                lines.append(_ld(it.description, it.category, amt, e,
                                 f"Capped at {inr(cap)}/day vs {inr(it.rate_per_day or 0)}/day charged × {it.days} days.",
                                 terms.citations.get(key)))
            else:
                lines.append(_ld(it.description, it.category, amt, amt, "Within room/ICU limit."))
        elif it.category in terms.proportionate_categories and ratio < 1:
            lines.append(_ld(it.description, it.category, amt, q(amt * ratio),
                             f"Proportionate deduction at {ratio:.4f} (room cost exceeded the eligible room).",
                             terms.citations.get("proportionate")))
        elif it.category in ("consumables", "admin"):
            step("Classification", "flag",
                 f"'{it.description}' looks like a {it.category} item but matches nothing in the policy's "
                 "non-payable list; paid provisionally.")
            lines.append(_ld(it.description, it.category, amt, amt, "Payable (no matching non-payable entry)."))
        elif it.category == "other":
            step("Classification", "flag", f"'{it.description}' could not be classified; paid provisionally.")
            lines.append(_ld(it.description, it.category, amt, amt, "Payable provisionally (unclassified)."))
        else:
            lines.append(_ld(it.description, it.category, amt, amt, "Payable."))

    # ----------------------------------------- 4. sub-limit, co-pay, SI cap
    admissible = sum((d(l.payable) for l in lines), D(0))
    adjustments: list[LineDecision] = []
    for sl in terms.sublimits:
        if item_matches(sl.name, text):
            limit = d(sl.amount) * (2 if sl.unit == "eye" and claim.bilateral else 1)
            if admissible > limit:
                adjustments.append(_ld(f"Sub-limit: {sl.name}", "adjustment", 0, 0, "", sl.citation))
                adjustments[-1].deducted = float(q(admissible - limit))
                adjustments[-1].reason = f"Admissible {inr(admissible)} capped at sub-limit {inr(limit)}."
                admissible = limit
    if terms.copay_pct:
        cp = q(admissible * d(terms.copay_pct) / D(100))
        adjustments.append(_ld(f"Co-payment {terms.copay_pct:g}%", "adjustment", 0, 0, "", terms.citations.get("copay")))
        adjustments[-1].deducted = float(cp)
        adjustments[-1].reason = f"{terms.copay_pct:g}% of admissible {inr(admissible)}, applied after sub-limits."
        admissible -= cp
    if admissible > d(terms.sum_insured):
        adjustments.append(_ld("Sum insured cap", "adjustment", 0, 0, "", terms.citations.get("schedule")))
        adjustments[-1].deducted = float(q(admissible - d(terms.sum_insured)))
        adjustments[-1].reason = f"Capped at sum insured {inr(terms.sum_insured)} (assumes no earlier claims this year)."
        admissible = d(terms.sum_insured)

    return _finish(claim, terms, steps, flags, reasons, lines, adjustments, billed, q(admissible))


def _finish(claim, terms, steps, flags, reasons, lines, adjustments, billed, payable) -> Adjudication:
    billed, payable = q(billed), q(payable)
    ded = billed - payable

    if lines:
        total_ded = sum((d(l.deducted) for l in lines + adjustments), D(0))
        if abs(total_ded - ded) > D("0.05"):
            flags.append(f"Internal check failed: itemised deductions {inr(total_ded)} ≠ billed − payable {inr(ded)}.")
            steps.append(Step(name="Accounting identity", status="flag", detail=flags[-1]))
        else:
            steps.append(Step(name="Accounting identity", status="ok",
                              detail=f"{inr(billed)} billed = {inr(payable)} payable + {inr(ded)} deductions."))

    if reasons:
        decision, summary = "rejected", f"Rejected. {reasons[0].text}"
    elif flags:
        decision = "needs_human_review"
        summary = (f"Provisional settlement {inr(payable)} of {inr(billed)} billed, but {len(set(flags))} item(s) "
                   "need a human reviewer before this can be paid.")
        if not lines:
            summary = "Could not process this claim automatically; routed to a human reviewer."
    elif payable < billed:
        decision, summary = "partially_approved", f"Partially approved: {inr(payable)} payable of {inr(billed)} billed."
    else:
        decision, summary = "approved", f"Approved in full: {inr(payable)}."

    return Adjudication(
        decision=decision, summary=summary, billed_total=float(billed), payable_total=float(payable),
        deductions_total=float(ded), provisional=decision == "needs_human_review",
        lines=lines, adjustments=adjustments, rejection_reasons=reasons,
        review_flags=list(dict.fromkeys(flags)), steps=steps, claim=claim, terms=terms)
