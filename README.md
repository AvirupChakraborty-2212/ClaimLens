# ClaimLens

An agent that does what a health-insurance claims processor (TPA) is paid for: it takes a policy, a hospital bill and a discharge summary, and returns a settlement. It shows what is payable, what is deducted, and the policy clause behind every deduction. Anything it can't settle with confidence goes to a human reviewer instead of being guessed.

## What it does

```
policy + bill + discharge summary
   │
   ├─ 1 ingest        PDF/text → pages → clause chunks (page numbers kept)
   ├─ 2 retrieve      BM25 finds the clause for each rule; terms parsed with clause id + page + quote
   ├─ 3 extract       bill → line items, dates, diagnosis  (rule-based parser; Gemini if GEMINI_API_KEY is set)
   ├─ 4 verify        bill total reconciles? dates sane? days × rate = amount? stay length matches?
   ├─ 5 eligibility   waiting periods, pre-existing disease, exclusions → reject, citing the clause
   ├─ 6 compute       non-payables, room/ICU caps, proportionate deduction, sub-limit, then co-pay (Decimal maths)
   └─ 7 decide        approved · partially approved · rejected · needs human review (with provisional amount)
```

The LLM, when enabled, only extracts text into a schema. Every rupee amount is computed by deterministic code, and every deduction links to the clause text it came from.

### Sample claims (synthetic, built in)

| Sample | Shows | Outcome |
|---|---|---|
| Hernia repair, ₹6,000/day room vs ₹5,000 cap | proportionate deduction, non-payables, co-pay | Partially approved, ₹88,800 of ₹1,15,800 |
| Cataract inside the 24-month waiting period | rejection with clause 3.2 | Rejected |
| Messy ICU bill with a wrong total and diabetic history | reconciliation failure, PED window, unclassified item | Needs human review, provisional ₹64,635 |

The first two are hand-calculated in `tests/test_claimlens.py`.

## Run locally

```bash
pip install -r requirements-dev.txt
pytest -q                                   # 29 tests
uvicorn app.main:app --reload               # http://localhost:8000
```

Optional: `export GEMINI_API_KEY=...` enables LLM extraction for messy bills. Scanned-bill reading is wired but **untested**.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/adjudicate` | JSON `{policy, bill, discharge}` as text |
| POST | `/api/adjudicate/upload` | multipart files `policy`, `bill`, `discharge` (PDF or text) |
| GET | `/api/samples`, `/api/samples/{id}` | built-in sample claims |
| GET | `/healthz` | liveness |

## Deploy on Render (free, about 5 minutes)

1. Push to GitHub (commands below).
2. Render dashboard → **New → Blueprint** → pick the repo. It reads `render.yaml`.
   (Or **New → Web Service** → pick the repo → Runtime **Docker** → Instance type **Free** → Health Check Path `/healthz`.)
3. Optional: add the `GEMINI_API_KEY` environment variable.
4. Wait for the build, then open `https://claimlens-xxxx.onrender.com`.

The free tier sleeps after about 15 minutes idle and the first request after that takes roughly a minute. Open the URL just before you submit the form, and again before anyone is likely to click it.

```bash
cd claimlens
git init && git add . && git commit -m "ClaimLens: claim adjudication agent"
git branch -M main
git remote add origin https://github.com/AvirupChakraborty-2212/ClaimLens.git
git push -u origin main
```

Fallbacks: the same Dockerfile works on Hugging Face Spaces (Docker SDK, set `app_port: 7860` and `PORT=7860`), Fly.io (`fly launch`), or Google Cloud Run.

## Limits (read before relying on it)

- The policy parser is written for numbered-clause wording (`4.1 Room rent …`). Other insurers' wording needs the parsers in `policy_terms.py` adapted, or an LLM extractor behind the same citation structure.
- Only individual health policies are modelled. Prior claims in the year, family floater and restoration benefits are not.
- The rules are a reasonable reading of common Indian health-policy terms, not any real insurer's contract. This is a demo, not claims advice.
- The rule-based bill parser is tuned to itemised text bills. Real-world scans depend on the Gemini path, which has not been run end to end.
