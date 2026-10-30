"""Optional Gemini client (REST, via httpx). Disabled unless GEMINI_API_KEY is set."""
from __future__ import annotations

import base64
import json
import os

import httpx


class GeminiClient:
    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    @classmethod
    def from_env(cls) -> "GeminiClient | None":
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            return None
        return cls(key, os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))

    def json_call(self, prompt: str, files: list[tuple[str, bytes]] | None = None) -> dict:
        parts: list[dict] = [{"text": prompt}]
        for name, data in files or []:
            mime = "application/pdf" if name.lower().endswith(".pdf") else \
                   "image/png" if name.lower().endswith(".png") else "image/jpeg"
            parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        r = httpx.post(url, headers={"x-goog-api-key": self.api_key}, timeout=60,
                       json={"contents": [{"parts": parts}],
                             "generationConfig": {"responseMimeType": "application/json", "temperature": 0}})
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
