# uk-transcribe

Self-hosted Ukrainian transcription. Drop audio or video into a web dashboard
and get a timestamped Ukrainian transcript back (TXT, SRT, VTT). Everything runs
on your own hardware or the university cluster, with no per-minute cloud fees
and no audio leaving the machine. Built on
[`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) (CTranslate2).

- **Dashboard** (Next.js, `web/`): drag-and-drop uploads, live queue with
  progress and ETA, searchable history, a transcript viewer that follows the
  audio player, downloads, and retention controls.
- **API** (FastAPI, `app/`): one GPU worker, a durable SQLite queue that
  survives restarts, and timestamped segments stored as they are decoded.
- **Deploy** (`deploy/`): the university "Student apps (cluster)" with a
  **GPU memory slice** (not a whole card), and a script to deploy, stop and
  start it with the Portainer key kept in Doppler. See
  [deploy/README.md](deploy/README.md).

Language is always forced to `uk`, because Whisper's auto-detect confuses
Ukrainian with Russian on accented or noisy audio.

## Layout

```
app/config.py        settings from environment variables
app/gpu.py           picks the card with most free VRAM (nvidia-smi), else the CPU
app/transcriber.py   faster-whisper engine: load once, stream segments
app/db.py            SQLite job history + queue (atomic, restart-safe)
app/worker.py        the single transcription worker + retention sweeper
app/exports.py       TXT / SRT / VTT
app/server.py        JSON API (see the docstring for the routes)
web/                 Next.js dashboard; talks to the API via /api/backend
main.py              CLI over the same engine (debug)
deploy/cluster.yml   compose stack for the student cluster
deploy/ukt.sh        deploy / stop / start / status / logs through the Portainer API
docker-compose.yml   local or desktop run (whole GPU, builds both images)
tests/               API tests (no GPU needed) + cluster compose rules
```

## Run locally

```bash
# API (CPU on a laptop; a small model for quick tests)
uv sync --extra app
WHISPER_MODEL=small DEVICE=cpu uv run uvicorn app.server:app --port 8002

# Dashboard, in a second terminal
cd web && npm install && API_URL=http://127.0.0.1:8002 npm run dev
```

Open http://localhost:3000. Data goes to `./data` (git-ignored).

With Docker and an NVIDIA GPU (e.g. the RTX 5070 desktop), this runs both
images with `large-v3` on http://localhost:3000:

```bash
docker compose up --build
```

CLI (debug):

```bash
uv run python main.py samples/sample_uk.wav --model small --format srt -o out.srt
```

## Tests

```bash
uv run pytest                         # API, worker, exports, cluster compose rules
cd web && npm test && npm run lint && npm run typecheck && npm run build
```

CI (`.github/workflows/images.yml`) runs the tests on every push. On `main` it
also builds both images, smoke-tests them on the CPU, and pushes
`ghcr.io/alex-krasnoshtanov/uk-transcribe[-web]:sha-<short>`.

## Environment variables (API)

| Variable | Default | Meaning |
|---|---|---|
| `WHISPER_MODEL` | `large-v3` | model id or path |
| `COMPUTE_TYPE` | `auto` | `float16` on a GPU, `int8` on the CPU |
| `BEAM_SIZE` / `VAD` | `5` / `1` | decoding |
| `DEVICE` | `auto` | `auto`, `cpu`, or an nvidia-smi index |
| `MIN_FREE_MIB` | `5000` | in `auto`, use the CPU if no card has this much free |
| `CPU_THREADS` | `4` | CTranslate2 threads on the CPU |
| `DATA_DIR` | `./data` (`/data` in the image) | SQLite DB + uploads |
| `HF_HOME` | HF default (`/models` in the image) | model cache |
| `MAX_UPLOAD_MB` / `MAX_QUEUE` | `2048` / `50` | limits |
| `AUDIO_RETENTION_DAYS` / `TRANSCRIPT_RETENTION_DAYS` | `7` / `0` | defaults; the dashboard can change them (0 = keep) |
| `HF_TOKEN` | – | optional, raises Hugging Face download limits |

The dashboard reads one variable: `API_URL` (default `http://127.0.0.1:8000`).

## Notes

- **One worker, one model.** Jobs run one at a time on purpose: a second model
  copy would double the VRAM. The queue is in SQLite, so jobs survive restarts
  (an interrupted job starts again).
- **Blackwell GPUs** (RTX 50xx, RTX PRO 6000) need CTranslate2 ≥ 4.9.1 and
  CUDA ≥ 12.8; both are pinned (`pyproject.toml`, `Dockerfile`).
- **No login.** The cluster deployment is open to the campus network by
  choice. Inputs are still validated (ids, sizes, file types).
- **Batch audio extraction:** `python scripts/extract_audio_from_videos.py`
  turns videos in `raw_videos/` into 16 kHz mono WAVs in `ready_audios/`.
