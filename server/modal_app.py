"""Deploy the ClipForge worker to Modal (free Starter credits).

One container: FastAPI jobs API + UI, the pipeline venv, Ollama for scoring.
A Modal Volume at /data keeps models, ffmpeg state and job artifacts across
cold starts.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import modal

SERVER_DIR = Path(__file__).parent

def _repo_rev() -> str:
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=str(SERVER_DIR.parent),
        ).stdout.strip()
        return rev or "main"
    except OSError:
        return "main"

REV = _repo_rev()

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg", "git", "curl", "ca-certificates", "zstd")
    .pip_install("uv==0.5.18", "fastapi>=0.115", "uvicorn[standard]>=0.30")
    .run_commands(
        "git init /tmp/cf && cd /tmp/cf && "
        "git remote add origin https://github.com/K4bain/ClipForge.git && "
        f"git fetch --depth 1 origin {REV} && git checkout FETCH_HEAD && "
        "mkdir -p /opt/clipforge && mv /tmp/cf/pipeline /opt/clipforge/pipeline && rm -rf /tmp/cf",
        "cd /opt/clipforge/pipeline && uv sync --frozen --no-dev",
        "curl -fsSL https://ollama.com/install.sh | sh",
        "ln -sf /opt/clipforge/pipeline/.venv/bin/clipforge /usr/local/bin/clipforge",
    )
    .env(
        {
            "CLIPFORGE_HOME": "/data/clipforge",
            "HF_HOME": "/data/clipforge/models/hf",
            "CLIPFORGE_SERVER_HOME": "/data/clipforge-server",
            "CLIPFORGE_FFMPEG": "/usr/bin/ffmpeg",
            "OLLAMA_MODELS": "/data/ollama",
        }
    )
    .add_local_dir(SERVER_DIR, "/root/server")
)

volume = modal.Volume.from_name("clipforge-data", create_if_missing=True)

app = modal.App(
    "clipforge",
    image=image,
    volumes={"/data": volume},
)


@app.function(
    cpu=4,
    memory=12288,
    max_containers=1,
    scaledown_window=1800,
)
@modal.concurrent(max_inputs=100)
@modal.web_server(7860, startup_timeout=900)
def serve():
    subprocess.Popen(["ollama", "serve"])
    for _ in range(120):
        try:
            urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2)
            break
        except Exception:
            time.sleep(1)
    models = json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/tags")).get("models", [])
    if not any(m.get("name", "").startswith("llama3.1") for m in models):
        subprocess.run(["ollama", "pull", "llama3.1:8b"], check=True)
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"],
        cwd="/root/server",
    )