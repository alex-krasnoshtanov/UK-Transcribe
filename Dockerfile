# CTranslate2 (faster-whisper's backend) needs CUDA 12 + cuDNN 9 at runtime.
# A plain `nvidia/cuda:*-base` image ships neither cuBLAS nor cuDNN, so GPU
# inference would fail with "libcudnn... not found". This `-cudnn9-runtime`
# image is the one faster-whisper's docs recommend and includes both. CUDA 12.3
# runs fine on a newer host driver (drivers are backward-compatible with older
# CUDA runtimes), so this is safe on the CUDA 13 rack.
FROM nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# python3 + pip only. faster-whisper decodes via PyAV (bundled FFmpeg), so
# system ffmpeg is not required; it is installed anyway as a cheap safety net
# for any odd container/codec the bundled build does not handle.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        ffmpeg \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Application code. main.py (the debug CLI) shares the same core, so it comes
# along for free and can be run with `docker exec ... python main.py ...`.
COPY main.py .
COPY app ./app

EXPOSE 8000

# The web UI is the product, so the image runs it by default.
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
