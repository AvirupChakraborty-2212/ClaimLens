"""Data models shared across the pipeline."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "room_rent", "icu_rent", "nursing", "doctor_fee", "ot_charges",
    "diagnostics", "pharmacy", "implant", "consumables", "admin", "other",
]


class Citation(BaseModel):
    clause: str
    page: int
    quote: str


# ---------------------------------------------------------------- policy side
class Sublimit(BaseModel):
    name: str
    amount: float
    unit: str | None = None
    citation: Citation


class Exclusion(BaseModel):
    phrase: str
    keywords: list[str]
    accident_exception: bool = False
    citation: Citation


class NonPayable(BaseModel):
    item: str
    citation: Citation


class PolicyTerms(BaseModel):
    policyholder: str | None = None
    sum_insured: float
    inception_date: date
    room_cap_pct: float | None = None
    icu_cap_pct: float | None = None
    room_cap_per_day: float | None = None
    icu_cap_per_day: float | None = None
    initial_waiting_days: int | None = None
    specific_waiting_months: int | None = None
    specific_conditions: list[str] = Field(default_factory=list)
    ped_waiting_months: int | None = None
    copay_pct: float | None = None
    proportionate_categories: list[str] = Field(default_factory=list)
    sublimits: list[Sublimit] = Field(default_factory=list)
    exclusions: list[Exclusion] = Field(default_factory=list)
    non_payable: list[NonPayable] = Field(default_factory=list)
    citations: dict[str, Citation] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------- claim side
class LineItem(BaseModel):
    description: str
    amount: float
    category: Category = "other"
    days: int | None = None
    rate_per_day: float | None = None


class ClaimData(BaseModel):
    patient_name: str | None = None
    admission_date: date | None = None
    discharge_date: date | None = None
    diagnosis: str = ""
    procedure: str = ""
    past_history: str = ""
    is_accident: bool = False
    bilateral: bool = False
    line_items: list[LineItem] = Field(default_factory=list)
    stated_total: float | None = None
    extractor: str = "heuristic"
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------- result
class LineDecision(BaseModel):
    description: str
    category: str
    billed: float
    payable: float
    deducted: float
    reason: str
    citation: Citation | None = None


class Step(BaseModel):
    name: str
    status: Literal["ok", "flag", "fail"]
    detail: str


class Reason(BaseModel):
    text: str
    citation: Citation | None = None


class Adjudication(BaseModel):
    decision: Literal["approved", "partially_approved", "rejected", "needs_human_review"]
    summary: str
    billed_total: float
    payable_total: float
    deductions_total: float
    provisional: bool = False
    lines: list[LineDecision]
    adjustments: list[LineDecision] = Field(default_factory=list)
    rejection_reasons: list[Reason] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    claim: ClaimData
    terms: PolicyTerms
