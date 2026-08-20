FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg git curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.5.18

RUN git clone --depth 1 https://github.com/K4bain/ClipForge.git /tmp/clipforge \
    && mv /tmp/clipforge/pipeline /opt/clipforge/pipeline \
    && rm -rf /tmp/clipforge

RUN cd /opt/clipforge/pipeline && uv sync --frozen --no-dev

WORKDIR /opt/clipforge/server
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py static/ start.sh ./

RUN curl -fsSL https://ollama.com/install.sh | sh

ENV CLIPFORGE_HOME=/data/clipforge \
    HF_HOME=/data/clipforge/models/hf \
    CLIPFORGE_SERVER_HOME=/data/clipforge-server \
    CLIPFORGE_FFMPEG=/usr/bin/ffmpeg \
    OLLAMA_MODELS=/data/ollama \
    OLLAMA_HOST=127.0.0.1:11434

EXPOSE 7860
CMD ["bash", "/opt/clipforge/server/start.sh"]