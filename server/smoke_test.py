"""Smoke test for the ClipForge server API. Simulates a pipeline run by
replacing _spawn with a fake worker that writes the same artifacts the real
pipeline produces (events.jsonl, clips/clip_00.mp4, score.json).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

TEST_HOME = Path(os.environ["CLIPFORGE_SERVER_HOME"])
JOBS = TEST_HOME / "jobs"
EVENTS = TEST_HOME / "events"
CLIP_HOME = Path(os.environ["CLIPFORGE_HOME"])

sys.path.insert(0, str(Path(__file__).parent))
import app as server_app  # noqa: E402

FAIL = 0


def fake_spawn(job_id: str, source: str, llm: str, captions: str | None, camera: str | None) -> None:
    """Replacement for app._spawn: emits progress events, writes score.json,
    renders a fake clip, then finishes."""
    events = EVENTS / f"{job_id}.jsonl"

    def run() -> None:
        time.sleep(0.2)
        meta = server_app._load_meta(job_id)
        meta["status"] = "running"
        server_app._meta_path(job_id).write_text(json.dumps(meta))
        job_dir = CLIP_HOME / "jobs" / job_id
        clips_dir = job_dir / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        with events.open("a", buffering=1) as fh:
            for stage in server_app.STAGES:
                fh.write(json.dumps({"event": "progress", "stage": stage, "fraction": 1.0, "message": f"{stage} ok"}) + "\n")
                time.sleep(0.05)
        (clips_dir / "clip_00.mp4").write_bytes(b"fake-mp4")
        (clips_dir / "clip_01.mp4").write_bytes(b"fake-mp4")
        (job_dir / "score.json").write_text(json.dumps({
            "data": {"clips": [
                {"start": 12.0, "end": 45.0, "score": 77, "verdict": "strong clip, trim the front", "subscores": {"humor": 58}},
                {"start": 300.0, "end": 330.0, "score": 61, "verdict": "decent", "subscores": {"humor": 40}},
            ]}
        }))
        meta = server_app._load_meta(job_id)
        meta["status"] = "done"
        meta["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        server_app._meta_path(job_id).write_text(json.dumps(meta))

    threading.Thread(target=run, daemon=True).start()


server_app._spawn = fake_spawn


def check(label: str, cond: bool) -> None:
    global FAIL
    if cond:
        print(f"  ok  {label}")
    else:
        FAIL += 1
        print(f"FAIL  {label}")


def main() -> int:
    from fastapi.testclient import TestClient

    client = TestClient(server_app.app)

    r = client.get("/health")
    check("health ok", r.status_code == 200 and r.json()["ok"] is True)

    r = client.get("/api/jobs")
    check("empty jobs list", r.status_code == 200 and r.json()["jobs"] == [])

    r = client.post("/api/jobs", json={"source": "https://www.youtube.com/watch?v=test"})
    check("create job", r.status_code == 202 and "job_id" in r.json())
    job_id = r.json()["job_id"]

    r = client.post("/api/jobs", json={"source": "bad"})
    check("reject short source", r.status_code == 422)

    r = client.post("/api/jobs", json={"source": "https://x.example/v", "llm": "bogus"})
    check("reject bad llm", r.status_code == 422)

    time.sleep(0.4)
    r = client.get("/api/jobs")
    jobs = r.json()["jobs"]
    check("job listed with progress", len(jobs) == 1 and jobs[0]["status"] in ("running", "done") and jobs[0]["progress"]["stage"] is not None)

    r = client.get(f"/api/jobs/{job_id}/events", headers={"Accept": "text/event-stream"})
    check("events endpoint reachable", r.status_code == 200)

    time.sleep(0.7)
    r = client.get(f"/api/jobs/{job_id}")
    body = r.json()
    check("job done", body["status"] == "done")
    check("score summary present", body["score"] is not None and len(body["score"]["clips"]) == 2)
    check("clips listed", len(body["clips"]) == 2)

    r = client.get(f"/api/jobs/{job_id}/clips/0/file")
    check("download clip", r.status_code == 200 and r.content == b"fake-mp4")

    r = client.get(f"/api/jobs/{job_id}/clips/9/file")
    check("404 unknown clip", r.status_code == 404)

    r = client.get("/api/jobs/nope")
    check("404 unknown job", r.status_code == 404)

    r = client.delete(f"/api/jobs/{job_id}")
    check("cancel done job rejected", r.status_code == 400)

    r = client.get("/ui")
    check("ui served", r.status_code == 200 and "CLIPFORGE" in r.text)

    print("FAILURES:", FAIL)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())