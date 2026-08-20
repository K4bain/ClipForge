---
title: ClipForge
emoji: 🔥
colorFrom: black
colorTo: orange
sdk: docker
app_port: 7860
hardware: cpu-basic
pinned: false
---

# ClipForge

Long video in, forged clips out — every score auditable.

Paste a YouTube URL and get back scored vertical clips (face-tracked camera,
burned captions, virality score with a full audit trail), rendered by the
same pipeline that ships in the desktop app. Runs on CPU here (2 vCPU /
16 GB) — an hour of video takes roughly 20–40 minutes. The first job after a
cold start also downloads ~5 GB of models, so give it time.

## API

- `POST /api/jobs` `{"source": "https://…", "llm": "ollama"|"gemini", "captions": "classic"|"minimal"|"kinetic", "camera": "cut"|"pan"|"locked"}` → `{"job_id"}`
- `GET /api/jobs` — list with progress
- `GET /api/jobs/{id}/events` — SSE stream of pipeline progress events
- `GET /api/jobs/{id}/clips` — rendered clips + score summary
- `GET /api/jobs/{id}/clips/{n}/file` — download the mp4
- `DELETE /api/jobs/{id}` — cancel
- `GET /ui` — browser UI

```
curl -s -X POST https://<space>.hf.space/api/jobs \
  -H "Content-Type: application/json" \
  -d '{"source": "https://www.youtube.com/watch?v=…"}'
```

## Notes

- Scoring brain: Ollama (`llama3.1:8b`) by default, self-hosted in this container. Set `CLIPFORGE_GEMINI_API_KEY` as a Space secret and pass `"llm": "gemini"` to use Gemini instead.
- Models, ffmpeg state, jobs and the Ollama store persist in the `/data` volume.
- The full on-device pipeline (ASR, diarization, laughter, camera, render) is AGPL-3.0 — source: https://github.com/K4bain/ClipForge