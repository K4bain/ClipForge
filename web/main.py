"""ClipForge Demo API — the auditable virality score, server-side.

A small showcase service for the ClipForge brand. The real pipeline (ASR,
diarization, laughter detection, camera direction, rendering) runs locally in
the desktop app; this service exposes the product's signature idea — a
virality score you can audit — as a public demo endpoint.

Endpoints:
    GET  /            service info
    GET  /health      liveness
    POST /audit       score a short transcript + optional event hints,
                      returns subscores, adjustments and the full audit trail
"""

from __future__ import annotations

import time
import uuid

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(
    title="ClipForge Demo API",
    version="0.1.0",
    description="The auditable virality score from ClipForge, as a public demo.",
)

STARTED_AT = time.time()


class AuditRequest(BaseModel):
    transcript: str = Field(min_length=20, max_length=4000, description="The spoken text of the candidate moment")
    laughs: int = Field(default=0, ge=0, le=20, description="Detected laughter bursts")
    energy: float = Field(default=0.5, ge=0.0, le=1.0, description="Vocal energy 0–1")
    speaker_changes: int = Field(default=0, ge=0, le=20)


class AuditResponse(BaseModel):
    job_id: str
    score: int
    verdict: str
    subscores: dict
    adjustments: list[dict]
    signals: dict
    audit: list[str]
    generated_at: str


VERDICTS = [
    (88, "clip it — this is the moment"),
    (74, "strong clip, trim the front"),
    (60, "clip it, but punch in sooner"),
    (45, "keep scanning"),
]


def _hashish(text: str) -> float:
    """Deterministic 0–1 hash of the transcript (stable across deploys)."""
    h = 2166136261
    for ch in text.encode("utf-8"):
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return (h % 1000) / 1000.0


@app.get("/")
def info():
    return {
        "service": "ClipForge Demo API",
        "tagline": "Long video in, forged clips out — every score auditable.",
        "version": "0.1.0",
        "endpoints": ["/health", "/audit"],
        "note": "The full pipeline (ASR, diarization, laughter, camera, render) runs locally in the desktop app.",
    }


@app.get("/health")
def health():
    return {"ok": True, "uptime_s": round(time.time() - STARTED_AT, 1)}


@app.post("/audit", response_model=AuditResponse)
def audit(req: AuditRequest):
    base = _hashish(req.transcript)

    humor = round(30 + base * 45 + min(req.laughs, 4) * 5)
    shock = round(20 + (1 - base) * 50)
    clarity = round(50 + (1 - abs(base - 0.5)) * 40)
    hook = round(40 + req.energy * 45 + min(req.speaker_changes, 3) * 4)

    subscores = {
        "humor": min(humor, 99),
        "shock": min(shock, 99),
        "clarity": min(clarity, 99),
        "hook": min(hook, 99),
    }

    adjustments: list[dict] = []
    score = int(round(sum(subscores.values()) / 4))

    if req.laughs == 0 and humor >= 70:
        penalty = -12
        adjustments.append({"factor": penalty, "rule": "LLM_HUMOR_UNCONFIRMED", "reason": "high humor score with no detected laughter — discounted"})
        score += penalty
    if req.laughs >= 3:
        boost = 8
        adjustments.append({"factor": boost, "rule": "LAUGHTER_CORROBORATION", "reason": "real laughter corroborates the moment"})
        score += boost
    if req.energy < 0.35:
        penalty = -6
        adjustments.append({"factor": penalty, "rule": "LOW_ENERGY_OPENING", "reason": "quiet delivery — weaker hook retention"})
        score += penalty

    score = max(0, min(99, score))
    verdict = next(v for threshold, v in VERDICTS if score >= threshold)

    audit_trail = [
        f"base scores: humor {subscores['humor']}, shock {subscores['shock']}, clarity {subscores['clarity']}, hook {subscores['hook']}",
        *(f"{a['rule']}: {a['factor']:+d} — {a['reason']}" for a in adjustments),
        f"final: {score}/99 — {verdict}",
    ]

    return AuditResponse(
        job_id=uuid.uuid4().hex[:8],
        score=score,
        verdict=verdict,
        subscores=subscores,
        adjustments=adjustments,
        signals={
            "laughs_detected": req.laughs,
            "vocal_energy": req.energy,
            "speaker_changes": req.speaker_changes,
        },
        audit=audit_trail,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )