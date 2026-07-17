# uk-transcribe

Self-hosted Ukrainian lecture transcription. Upload audio through a web page,
get the Ukrainian transcript back - running on your own hardware, no per-minute
cloud fees, no audio leaving the machine. Built on
[`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) (CTranslate2
backend) with one code path that runs on a CPU laptop and on the GPU rack
unchanged.

The web UI is the product. There is a small CLI (`main.py`) that shares the same
core, but it exists only for local tests and headless debugging.

## Layout

```
app/transcriber.py      shared core: device selection, model load, decoding
app/server.py           FastAPI: /upload, /status/{id}, /download/{id}, /health
app/static/index.html   single-page UI with a live progress bar
main.py                 thin CLI over the same core (debug/dev only)
requirements.txt        runtime deps for the Docker image
pyproject.toml          uv project (core + optional `app` extra + `dev` group)
Dockerfile              CUDA 12 + cuDNN 9 image; runs the web UI by default
docker-compose.yml      the one stack to deploy (Portainer)
.env.example            WHISPER_MODEL / HF_TOKEN / IMAGE / PORT / GPU selection
outputs/                transcripts + per-job state (git-ignored)
uploads/                upload scratch, cleared after each job (git-ignored)
samples/                local test clips (git-ignored)
```

## Run locally (laptop, CPU)

```bash
uv sync --extra app
uv run uvicorn app.server:app --port 8000
```

Open http://localhost:8000, choose a file, click Transcribe. No GPU is detected
on the laptop, so the model loads on CPU with int8. The first run downloads the
model (`large-v3` is ~3 GB); set `WHISPER_MODEL=small` for a quick smoke test.

Language is always forced to `uk`: Whisper's auto-detect confuses Ukrainian with
Russian on accented/noisy audio, so it is never trusted.

CLI (debug only), same core:

```bash
uv run python main.py samples/sample_uk.wav --model small
```

## Build the image

```bash
docker build -t ghcr.io/gfgf96/uk-transcribe:latest .
docker push ghcr.io/gfgf96/uk-transcribe:latest
```

Build on a machine with Docker and push to a registry the Portainer host can
pull from. The GPU rack itself is usually the easiest place to build.

## Deploy on Portainer

Prerequisite: the host has the NVIDIA Container Toolkit installed and Docker can
see the GPUs (`docker run --rm --gpus all nvidia/cuda:12.3.2-base-ubuntu22.04 nvidia-smi`).

1. Push the image to a registry the host can pull (GHCR above), or build it on
   the host.
2. Portainer -> Stacks -> Add stack. Paste `docker-compose.yml`, or point it at
   this Git repo.
3. Set environment variables (Portainer's env editor, or an attached `.env`):
   `IMAGE`, `WHISPER_MODEL`, `HF_TOKEN`, `PORT`, and optionally
   `NVIDIA_VISIBLE_DEVICES` to restrict which GPUs are candidates.
4. Deploy. The first boot downloads the model, so the container stays
   "starting" (unhealthy) for a few minutes - this is expected; the healthcheck
   has a 10-minute grace period.
5. Once healthy, open `http://<host>:<PORT>`.

Redeploys reuse the `hf-cache` volume, so the model is not re-downloaded.

## GPU selection

The container is given all visible GPUs and the app picks the one with the most
free memory at startup (NVML via `nvidia-ml-py`). This avoids hard-coding a card
on the shared rack and does not exclusively lock the others. Restrict the
candidate set with `NVIDIA_VISIBLE_DEVICES` if you want to keep off specific
cards. The choice is made once at startup and held for the process lifetime.

## Environment variables

| Variable                | Purpose                                                    |
|-------------------------|------------------------------------------------------------|
| `WHISPER_MODEL`         | model id / path (default `large-v3`)                       |
| `HF_TOKEN`              | HuggingFace token; raises download rate limits (recommended) |
| `IMAGE`                 | container image reference                                  |
| `PORT`                  | host port for the web UI (default 8000)                    |
| `NVIDIA_VISIBLE_DEVICES`| restrict candidate GPUs (default `all`)                    |

## Notes / gotchas

- **cuDNN**: the image is `nvidia/cuda:12.3.2-cudnn9-runtime` on purpose;
  CTranslate2 needs CUDA 12 + cuDNN 9, and a `-base` image lacks them.
- **Progress bar**: driven by `segment.end / audio_duration` from faster-whisper,
  so it reflects real decoding position, not a timer.
- **Download name**: `<original-name>_transcribed.txt` (change `DOWNLOAD_SUFFIX`
  in `app/server.py` to rename the convention).
- **Concurrency**: one model instance, one lock. A second upload is queued
  (shown as "pending") until the first finishes - correct, not concurrent.
- **HF_TOKEN** also speeds up the large `large-v3` download.
