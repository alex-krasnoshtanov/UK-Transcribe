# The transcription API (FastAPI + faster-whisper). The dashboard is web/Dockerfile.
#
# CTranslate2 needs the CUDA 12 runtime libraries (cuBLAS) at runtime; this
# -cudnn-runtime image has them. CUDA 12.9 and ctranslate2 >= 4.9.1 run on every
# card the student cluster has, including the RTX PRO 6000 Blackwell node, and on
# newer host drivers. Without a GPU the same image runs on the CPU (int8).
FROM nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04

# uv from Docker Hub (astral/uv mirrors ghcr.io/astral-sh/uv): one registry for every base layer.
COPY --from=astral/uv:0.13.9 /uv /usr/local/bin/uv

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# Ubuntu 24.04's Python is 3.12, which the project requires. No system ffmpeg:
# faster-whisper decodes through PyAV, which bundles its own FFmpeg.
RUN apt-get update && apt-get install -y --no-install-recommends python3.12 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first (cached across code changes), exactly as locked.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-default-groups --extra app --python /usr/bin/python3.12

COPY app ./app
COPY main.py ./

# The app's user. The cluster's external volumes start out root-owned; the
# deploy script hands /data and /models to this uid once.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin ukt \
    && mkdir -p /data /models && chown ukt:ukt /data /models
USER ukt

ARG GIT_SHA=dev
ENV PATH=/opt/venv/bin:$PATH \
    GIT_SHA=${GIT_SHA} \
    DATA_DIR=/data \
    HF_HOME=/models \
    CUDA_DEVICE_ORDER=PCI_BUS_ID \
    OMP_NUM_THREADS=4

EXPOSE 8000

# Ready once the model is loaded (the first start downloads ~3 GB of weights).
HEALTHCHECK --interval=30s --timeout=10s --start-period=900s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=8)"]

# One worker on purpose: each uvicorn worker would load its own copy of the model.
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
