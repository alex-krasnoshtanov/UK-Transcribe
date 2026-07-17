"""FastAPI web UI - the primary entry point for the project.

A single-user, self-hosted transcription service: upload an audio file, watch a
progress bar, download the Ukrainian transcript. Deliberately minimal for
occasional single-user use (project plan, Phase 5): no auth, no database, and
FastAPI BackgroundTasks instead of a real job queue. A multi-user deployment
would need a durable queue (Celery/RQ) and persistent job state instead.

Job state is a folder under outputs/<job_id>/ holding a status.json and, once
finished, a transcript.txt. Trade-off accepted for v1: a process restart
mid-job leaves that job stuck in 'processing' and it is not retried - fine at
this scale.
"""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.transcriber import DEFAULT_MODEL, load_model, transcribe_file

APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent
OUTPUTS_DIR = BASE_DIR / "outputs"
UPLOADS_DIR = BASE_DIR / "uploads"
INDEX_HTML = APP_DIR / "static" / "index.html"

# Suffix added to the downloaded transcript filename, e.g. lecture.mp3 ->
# lecture_transcribed.txt. One constant to change if you want a different word.
DOWNLOAD_SUFFIX = "_transcribed"

# The model is loaded once at startup and reused for every job (large-v3 is
# ~3 GB; reloading per request would dominate runtime). One lock serialises
# decoding so concurrent background jobs never touch the same model instance at
# once - correctness over throughput, the right trade at single-user scale.
_state: dict[str, object] = {}
_model_lock = threading.Lock()


def _job_dir(job_id: str) -> Path:
    return OUTPUTS_DIR / job_id


def _read_status(job_id: str) -> dict | None:
    status_path = _job_dir(job_id) / "status.json"
    if not status_path.is_file():
        return None
    return json.loads(status_path.read_text(encoding="utf-8"))


def _write_status(
    job_id: str,
    status: str,
    *,
    progress: float = 0.0,
    detail: str = "",
    original_name: str = "",
) -> None:
    """Persist job state, merging onto any existing record.

    Merging means a progress-only update does not clobber the original_name
    captured at upload time, and vice versa.
    """
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    record = _read_status(job_id) or {}
    record.update({"job_id": job_id, "status": status, "progress": round(progress, 4)})
    if detail:
        record["detail"] = detail
    if original_name:
        record["original_name"] = original_name
    (job_dir / "status.json").write_text(json.dumps(record), encoding="utf-8")


def _run_job(job_id: str, audio_path: Path) -> None:
    """Background worker: transcribe one uploaded file, updating status.json."""
    last_written = -1.0

    def on_progress(fraction: float) -> None:
        nonlocal last_written
        # Throttle disk writes: only persist when progress advances >=1% (or
        # hits 100%), so a lecture with hundreds of segments does not rewrite
        # status.json hundreds of times.
        if fraction - last_written >= 0.01 or fraction >= 1.0:
            last_written = fraction
            _write_status(job_id, "processing", progress=fraction)

    try:
        # The lock is acquired before flipping to "processing": a second upload
        # stays "pending" (queued) in its status.json until this one releases.
        with _model_lock:
            _write_status(job_id, "processing", progress=0.0)
            text = transcribe_file(_state["model"], audio_path, on_progress)
        (_job_dir(job_id) / "transcript.txt").write_text(text, encoding="utf-8")
        _write_status(job_id, "done", progress=1.0)
    except Exception as exc:  # noqa: BLE001 - any failure is surfaced to the client
        _write_status(job_id, "error", detail=str(exc))
    finally:
        # The uploaded audio is only needed for the transcription itself.
        audio_path.unlink(missing_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    # Load weights at startup, not lazily per request, so the first user upload
    # is not penalised with a multi-GB download. FastAPI does not serve requests
    # until this completes, so no request can race an unloaded model.
    _state["model"] = load_model(DEFAULT_MODEL)
    yield
    _state.clear()


app = FastAPI(title="uk-transcribe", lifespan=lifespan)


def _safe_stem(filename: str | None) -> str:
    """Sanitised base name for the download, from the uploaded filename.

    Strips the directory and extension, then keeps only characters that are safe
    in a Content-Disposition header and on any filesystem. Falls back to
    'transcript' if nothing usable remains.
    """
    stem = Path(filename or "").stem
    cleaned = "".join(c for c in stem if c.isalnum() or c in (" ", "-", "_")).strip()
    return cleaned or "transcript"


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> JSONResponse:
    # Healthy only once the model is loaded. The Docker healthcheck relies on
    # this so Portainer reports the service ready only when it can actually work
    # (the first startup downloads the model, which takes a while).
    ready = _state.get("model") is not None
    return JSONResponse(
        {"status": "ok" if ready else "loading"},
        status_code=200 if ready else 503,
    )


@app.post("/upload")
def upload(file: UploadFile, background_tasks: BackgroundTasks) -> dict:
    # Sync endpoint on purpose: FastAPI runs it in a threadpool, so the streamed
    # disk write below does not block the event loop that serves /status polls.
    job_id = uuid.uuid4().hex
    # Preserve the original suffix so faster-whisper/PyAV can identify the
    # container format; the stored name is the job id to avoid collisions.
    suffix = Path(file.filename or "").suffix
    audio_path = UPLOADS_DIR / f"{job_id}{suffix}"
    # Stream to disk in chunks rather than loading the whole file into memory;
    # lecture recordings can be hundreds of MB.
    with audio_path.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    _write_status(
        job_id, "pending", progress=0.0, original_name=_safe_stem(file.filename)
    )
    background_tasks.add_task(_run_job, job_id, audio_path)
    return {"job_id": job_id}


@app.get("/status/{job_id}")
def status(job_id: str) -> dict:
    result = _read_status(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="unknown job id")
    return result


@app.get("/download/{job_id}")
def download(job_id: str) -> FileResponse:
    transcript_path = _job_dir(job_id) / "transcript.txt"
    if not transcript_path.is_file():
        raise HTTPException(status_code=404, detail="transcript not ready")
    record = _read_status(job_id) or {}
    stem = record.get("original_name") or "transcript"
    return FileResponse(
        transcript_path,
        media_type="text/plain; charset=utf-8",
        filename=f"{stem}{DOWNLOAD_SUFFIX}.txt",
    )
