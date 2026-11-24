"""JSON API for the transcription service (the Next.js dashboard in web/ is its UI).

- POST   /jobs?filename=<name>      raw request body = the audio file; queues a job
- GET    /jobs                      history (q, status, limit, offset)
- GET    /jobs/{id}                 one job with progress, queue position, ETA
- GET    /jobs/{id}/segments        timestamped transcript (after=<idx> for live updates)
- GET    /jobs/{id}/transcript.{txt,srt,vtt}
- GET    /jobs/{id}/audio           the uploaded file (range requests, for the player)
- POST   /jobs/{id}/cancel | /retry
- DELETE /jobs/{id}                 job, transcript and audio
- DELETE /jobs/{id}/audio           audio only, keeps the transcript
- GET    /queue                     running + queued jobs
- GET    /settings, PUT /settings   retention (days, 0 = keep)
- GET    /healthz                   readiness: 503 until the model is loaded

No login: the deployment is open to the campus network by choice. Inputs are
still validated (ids, sizes, file types) because that network is shared.
"""

from __future__ import annotations

import mimetypes
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from app import gpu
from app.config import Settings
from app.db import Store
from app.exports import FORMATS
from app.transcriber import Engine, WhisperEngine
from app.worker import Worker, delete_audio, retention

JOB_ID = re.compile(r"^[0-9a-f]{32}$")
CHUNK = 1024 * 1024


def _probe_duration(path: Path) -> float | None:
    """Audio length in seconds, or None if the file cannot be decoded."""
    try:
        import av

        with av.open(str(path)) as container:
            if not container.streams.audio:
                return None
            if container.duration:
                return container.duration / 1_000_000
            stream = container.streams.audio[0]
            if stream.duration and stream.time_base:
                return float(stream.duration * stream.time_base)
            return 0.0
    except Exception:  # noqa: BLE001 - any decode failure means "not audio"
        return None


def _clean_name(filename: str) -> str:
    """Display name: basename only, no control characters, at most 200 chars."""
    name = Path(filename.replace("\\", "/")).name
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"/\\|?*').strip()
    return name[:200] or "audio"


def _disposition(filename: str) -> str:
    ascii_name = "".join(c if c.isascii() and c.isalnum() or c in "-_." else "_" for c in filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


class RetentionIn(BaseModel):
    audio_retention_days: int = Field(ge=0, le=3650)
    transcript_retention_days: int = Field(ge=0, le=3650)


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = engine or WhisperEngine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        store = Store(settings.db_path)
        worker = Worker(store, engine, settings)
        app.state.store, app.state.worker = store, worker
        # The model loads in the worker thread, so history and the dashboard work
        # while it loads; /healthz stays 503 (not ready) until it is done.
        worker.start()
        yield
        worker.stop()
        store.close()

    app = FastAPI(title="uk-transcribe", version=settings.version, lifespan=lifespan)

    def store_of(request: Request) -> Store:
        return request.app.state.store

    def get_job_or_404(store: Store, job_id: str) -> dict[str, Any]:
        if not JOB_ID.match(job_id):
            raise HTTPException(404, "unknown job")
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return job

    def public(store: Store, job: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        out = {
            "id": job["id"],
            "filename": job["filename"],
            "status": job["status"],
            "progress": round(job["progress"] or 0.0, 4),
            "duration": job["duration"],
            "size_bytes": job["size_bytes"],
            "model": job["model"],
            "device": job["device"],
            "error": job["error"],
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "audio_available": job["audio_deleted_at"] is None and bool(job["audio_path"])
            and Path(job["audio_path"]).is_file(),
            "eta_seconds": None,
            "queue_position": None,
        }
        if job["status"] == "processing":
            left = remaining(job, store.speed(), now)
            out["eta_seconds"] = round(left) if left is not None else None
        elif job["status"] == "queued":
            out["queue_position"] = store.queue_position(job["id"])
            # Time left for everything ahead of it, plus its own length.
            speed = store.speed()
            ahead = [
                j for j in store.active_jobs()
                if j["status"] == "processing" or j["created_at"] < job["created_at"]
            ]
            total = 0.0
            for j in ahead + [job]:
                left = remaining(j, speed, now)
                if left is None:
                    total = -1.0
                    break
                total += left
            out["eta_seconds"] = round(total) if total >= 0 else None
        return out

    def remaining(job: dict[str, Any], speed: float | None, now: float) -> float | None:
        """Seconds until this job is done, or None when there is no basis to guess."""
        if job["status"] == "processing" and job["started_at"]:
            elapsed = now - job["started_at"]
            p = job["progress"] or 0.0
            if p >= 0.03:
                return elapsed / p * (1 - p)
            if speed and job["duration"]:
                return max(job["duration"] * speed - elapsed, 0.0)
            return None
        if speed and job["duration"]:
            return job["duration"] * speed
        return None

    # --- jobs ---------------------------------------------------------------------

    @app.post("/jobs", status_code=201)
    async def create_job(request: Request, filename: str = Query(min_length=1, max_length=400)):
        store = store_of(request)
        name = _clean_name(filename)
        suffix = Path(name).suffix.lower()
        if suffix not in settings.allowed_suffixes:
            raise HTTPException(415, f"unsupported file type '{suffix or '?'}'")
        if store.queue_length() >= settings.max_queue:
            raise HTTPException(429, "the queue is full, try again later")
        limit = settings.max_upload_mb * 1024 * 1024
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, f"file is larger than {settings.max_upload_mb} MB")

        job_id = uuid.uuid4().hex
        final = settings.uploads_dir / f"{job_id}{suffix}"
        part = final.with_suffix(final.suffix + ".part")
        size = 0
        try:
            with part.open("wb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"file is larger than {settings.max_upload_mb} MB")
                    out.write(chunk)
            if size == 0:
                raise HTTPException(400, "empty upload")
            duration = await run_in_threadpool(_probe_duration, part)
            if duration is None:
                raise HTTPException(415, "the file has no audio track that can be decoded")
            os.replace(part, final)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        store.add_job(job_id, name, final, size, duration)
        request.app.state.worker.notify()
        return public(store, store.get_job(job_id))

    @app.get("/jobs")
    def list_jobs(
        request: Request,
        q: str = Query("", max_length=200),
        status: str = Query("", pattern="^(|queued|processing|done|error|cancelled)$"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        store = store_of(request)
        rows, total = store.list_jobs(q=q.strip(), status=status, limit=limit, offset=offset)
        return {"total": total, "items": [public(store, r) for r in rows]}

    @app.get("/jobs/{job_id}")
    def get_job(request: Request, job_id: str):
        store = store_of(request)
        return public(store, get_job_or_404(store, job_id))

    @app.get("/jobs/{job_id}/segments")
    def get_segments(request: Request, job_id: str, after: int = Query(-1, ge=-1)):
        store = store_of(request)
        get_job_or_404(store, job_id)
        return [
            {"idx": s.idx, "start": s.start, "end": s.end, "text": s.text}
            for s in store.segments(job_id, after)
        ]

    @app.get("/jobs/{job_id}/transcript.{fmt}")
    def get_transcript(request: Request, job_id: str, fmt: str):
        store = store_of(request)
        job = get_job_or_404(store, job_id)
        if fmt not in FORMATS:
            raise HTTPException(404, "unknown format")
        render, media_type = FORMATS[fmt]
        body = render(store.segments(job_id))
        stem = Path(job["filename"]).stem or "transcript"
        return Response(
            body,
            media_type=media_type,
            headers={"Content-Disposition": _disposition(f"{stem}.{fmt}")},
        )

    @app.get("/jobs/{job_id}/audio")
    def get_audio(request: Request, job_id: str):
        store = store_of(request)
        job = get_job_or_404(store, job_id)
        path = Path(job["audio_path"] or "")
        if job["audio_deleted_at"] is not None or not path.is_file():
            raise HTTPException(410, "audio was deleted")
        media_type = mimetypes.guess_type(job["filename"])[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type)

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(request: Request, job_id: str):
        store = store_of(request)
        job = get_job_or_404(store, job_id)
        if job["status"] == "queued":
            store.cancel_queued(job_id)
        elif job["status"] == "processing":
            store.request_cancel(job_id)
        else:
            raise HTTPException(409, f"job is {job['status']}")
        return public(store, store.get_job(job_id))

    @app.post("/jobs/{job_id}/retry")
    def retry_job(request: Request, job_id: str):
        store = store_of(request)
        get_job_or_404(store, job_id)
        if store.queue_length() >= settings.max_queue:
            raise HTTPException(429, "the queue is full, try again later")
        if not store.requeue(job_id):
            raise HTTPException(409, "only failed or cancelled jobs with their audio can be retried")
        request.app.state.worker.notify()
        return public(store, store.get_job(job_id))

    @app.delete("/jobs/{job_id}", status_code=204)
    def delete_job(request: Request, job_id: str):
        store = store_of(request)
        job = get_job_or_404(store, job_id)
        if job["status"] == "processing":
            # The worker stops at the next segment and removes the file.
            store.request_cancel(job_id)
            store.delete_job(job_id)
        else:
            delete_audio(store, job)
            store.delete_job(job_id)
        return Response(status_code=204)

    @app.delete("/jobs/{job_id}/audio", status_code=204)
    def delete_job_audio(request: Request, job_id: str):
        store = store_of(request)
        job = get_job_or_404(store, job_id)
        if job["status"] in ("queued", "processing"):
            raise HTTPException(409, "the job still needs its audio")
        delete_audio(store, job)
        return Response(status_code=204)

    # --- queue, settings, health --------------------------------------------------------

    @app.get("/queue")
    def queue(request: Request):
        store = store_of(request)
        return {"items": [public(store, j) for j in store.active_jobs()]}

    @app.get("/settings")
    def get_settings(request: Request):
        store = store_of(request)
        audio, transcript = retention(store, settings)
        return {
            "audio_retention_days": audio,
            "transcript_retention_days": transcript,
            "max_upload_mb": settings.max_upload_mb,
            "max_queue": settings.max_queue,
            "allowed_types": sorted(settings.allowed_suffixes),
            "model": settings.model,
            "beam_size": settings.beam_size,
            "vad": settings.vad,
        }

    @app.put("/settings")
    def put_settings(request: Request, body: RetentionIn):
        store = store_of(request)
        store.set_setting("audio_retention_days", str(body.audio_retention_days))
        store.set_setting("transcript_retention_days", str(body.transcript_retention_days))
        return get_settings(request)

    @app.get("/healthz")
    def healthz(request: Request):
        worker: Worker = request.app.state.worker
        info = engine.info
        body: dict[str, Any] = {
            "version": settings.version,
            "model": settings.model,
            "queue": store_of(request).queue_length(),
            "current_job": worker.current_job,
        }
        if worker.load_error:
            body.update(status="error", error=worker.load_error)
            return JSONResponse(body, status_code=503)
        if not worker.ready.is_set() or info is None:
            body["status"] = "loading"
            return JSONResponse(body, status_code=503)
        body.update(
            status="ok",
            device=info.device,
            compute_type=info.compute_type,
            device_reason=info.reason,
            gpu=info.gpu_name,
            gpu_free_mib=gpu.free_mib(info.gpu_uuid),
        )
        return body

    return app


def __getattr__(name: str):
    # `uvicorn app.server:app` builds the app lazily from the environment, so
    # importing this module in tests does not load a model.
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
