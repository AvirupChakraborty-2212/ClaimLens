"""Orchestrator: documents in, settlement decision out."""
from __future__ import annotations

from .extract import parse_claim, parse_claim_llm
from .ingest import NoTextError, pages_from_bytes, pages_from_text
from .llm import GeminiClient
from .models import Adjudication, Step
from .policy_terms import extract_terms
from .rules import adjudicate


def run_text(policy: str, bill: str, discharge: str) -> Adjudication:
    terms = extract_terms(pages_from_text(policy))
    llm = GeminiClient.from_env()
    claim, trace = None, []
    if llm:
        try:
            claim = parse_claim_llm(llm, bill, discharge)
        except Exception as e:  # network, schema or JSON failure: fall back, and say so
            trace.append(Step(name="Extraction", status="flag",
                              detail=f"LLM extraction failed ({type(e).__name__}); used the rule-based parser instead."))
    if claim is None:
        claim = parse_claim(bill, discharge)
    result = adjudicate(claim, terms)
    result.steps = trace + result.steps
    result.review_flags = list(dict.fromkeys([s.detail for s in trace] + result.review_flags))
    if trace and result.decision in ("approved", "partially_approved"):
        result.decision, result.provisional = "needs_human_review", True
    return result


def run_files(policy: tuple[str, bytes], bill: tuple[str, bytes], discharge: tuple[str, bytes]) -> Adjudication:
    """Files may be PDF or text. Scanned bill/discharge files need GEMINI_API_KEY (vision path)."""
    llm = GeminiClient.from_env()
    texts: dict[str, str] = {}
    scans: list[tuple[str, str, bytes, str]] = []
    for key, (name, data) in (("policy", policy), ("bill", bill), ("discharge", discharge)):
        try:
            pages = pages_from_bytes(name, data)
            texts[key] = "\n".join(f"--- Page {i} ---\n{p}" for i, p in enumerate(pages, 1))
        except NoTextError as e:
            scans.append((key, name, data, str(e)))
    if not scans:
        return run_text(texts["policy"], texts["bill"], texts["discharge"])
    if not llm or any(k == "policy" for k, *_ in scans):
        raise ValueError("; ".join(m for *_, m in scans) +
                         ". Upload text-based PDFs, or set GEMINI_API_KEY to read scanned bills (the policy must be text-based).")
    terms = extract_terms(pages_from_text(texts["policy"]))
    claim = parse_claim_llm(llm, texts.get("bill", ""), texts.get("discharge", ""),
                            files=[(n, d) for _, n, d, _ in scans])
    return adjudicate(claim, terms)
