"""FastAPI entrypoint."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import run_files, run_text
from .llm import GeminiClient
from .models import Adjudication
from .samples import SAMPLES

STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_BYTES = 8 * 1024 * 1024

app = FastAPI(title="ClaimLens", version="0.1.0",
              description="Health-insurance claim adjudication agent with clause-level citations.")


class TextClaim(BaseModel):
    policy: str
    bill: str
    discharge: str


@app.get("/healthz")
def healthz():
    return {"ok": True, "llm_extraction": GeminiClient.from_env() is not None}


@app.get("/api/samples")
def samples():
    return [{"id": k, "title": v["title"], "expect": v["expect"]} for k, v in SAMPLES.items()]


@app.get("/api/samples/{sample_id}")
def sample(sample_id: str):
    if sample_id not in SAMPLES:
        raise HTTPException(404, "unknown sample")
    return SAMPLES[sample_id]


@app.post("/api/adjudicate", response_model=Adjudication)
def adjudicate_text(body: TextClaim):
    try:
        return run_text(body.policy, body.bill, body.discharge)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/adjudicate/upload", response_model=Adjudication)
async def adjudicate_upload(policy: UploadFile = File(...), bill: UploadFile = File(...),
                            discharge: UploadFile = File(...)):
    blobs = []
    for f in (policy, bill, discharge):
        data = await f.read()
        if len(data) > MAX_BYTES:
            raise HTTPException(413, f"{f.filename} is larger than 8 MB")
        blobs.append((f.filename or "file.txt", data))
    try:
        return run_files(*blobs)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.exception_handler(Exception)
async def unhandled(_, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": f"Unexpected error: {type(exc).__name__}"})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
