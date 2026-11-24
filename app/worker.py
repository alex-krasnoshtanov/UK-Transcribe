"""The single transcription worker and the retention sweeper.

One background thread loads the model, then takes queued jobs from the store
one at a time. One model instance is the right shape for a GPU slice: a second
worker would need a second copy of the weights in VRAM.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from app.config import Settings
from app.db import Store
from app.transcriber import Cancelled, Engine, Segment

SWEEP_EVERY_S = 3600


def _log(msg: str) -> None:
    print(f"[uk-transcribe] {msg}", file=sys.stderr, flush=True)


def retention(store: Store, settings: Settings) -> tuple[int, int]:
    """(audio_days, transcript_days) - dashboard values override the env defaults. 0 = keep."""
    audio = store.get_setting("audio_retention_days")
    transcript = store.get_setting("transcript_retention_days")
    return (
        int(audio) if audio is not None else settings.audio_retention_days,
        int(transcript) if transcript is not None else settings.transcript_retention_days,
    )


def delete_audio(store: Store, job: dict) -> None:
    path = job.get("audio_path")
    if path:
        Path(path).unlink(missing_ok=True)
    store.mark_audio_deleted(job["id"])


def sweep(store: Store, settings: Settings, now: float | None = None) -> tuple[int, int]:
    """Apply the retention rules once. Returns (audio files removed, jobs removed)."""
    now = time.time() if now is None else now
    audio_days, transcript_days = retention(store, settings)
    removed_audio = removed_jobs = 0
    if audio_days > 0:
        for job in store.iter_expired_audio(now - audio_days * 86400):
            delete_audio(store, job)
            removed_audio += 1
    if transcript_days > 0:
        for job in store.iter_expired_jobs(now - transcript_days * 86400):
            delete_audio(store, job)
            store.delete_job(job["id"])
            removed_jobs += 1
    return removed_audio, removed_jobs


class Worker:
    def __init__(self, store: Store, engine: Engine, settings: Settings) -> None:
        self.store = store
        self.engine = engine
        self.settings = settings
        self.ready = threading.Event()
        self.load_error: str | None = None
        self.current_job: str | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_sweep = 0.0

    def start(self) -> None:
        requeued = self.store.requeue_interrupted()
        if requeued:
            _log(f"re-queued {requeued} job(s) interrupted by a restart")
        self._thread = threading.Thread(target=self._run, name="transcriber", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def notify(self) -> None:
        """A job was added: wake the worker instead of waiting for the poll."""
        self._wake.set()

    def _run(self) -> None:
        try:
            self.engine.load()
        except Exception as exc:  # noqa: BLE001 - reported through /healthz
            self.load_error = f"{type(exc).__name__}: {exc}"
            _log(f"model failed to load: {self.load_error}")
            return
        self.ready.set()
        while not self._stop.is_set():
            self._maybe_sweep()
            job = self.store.claim_next()
            if job is None:
                self._wake.wait(timeout=5)
                self._wake.clear()
                continue
            self._process(job)

    def _maybe_sweep(self) -> None:
        if time.time() - self._last_sweep < SWEEP_EVERY_S:
            return
        self._last_sweep = time.time()
        try:
            audio, jobs = sweep(self.store, self.settings)
            if audio or jobs:
                _log(f"retention: removed {audio} audio file(s), {jobs} job(s)")
        except Exception as exc:  # noqa: BLE001 - never kill the worker over cleanup
            _log(f"retention sweep failed: {exc}")

    def _process(self, job: dict) -> None:
        job_id = job["id"]
        self.current_job = job_id
        info = self.engine.info
        if info:
            self.store.set_running_info(job_id, info.model, info.device)
        counter = {"idx": 0}

        def on_segment(seg: Segment, duration: float) -> None:
            progress = min(seg.end / duration, 1.0) if duration > 0 else 0.0
            self.store.add_segment(
                job_id, counter["idx"], seg.start, seg.end, seg.text, progress, duration
            )
            counter["idx"] += 1

        try:
            duration = self.engine.transcribe(
                Path(job["audio_path"]), on_segment, lambda: self.store.is_cancelled(job_id)
            )
            self.store.finish(job_id, "done", duration=duration)
        except Cancelled:
            self.store.finish(job_id, "cancelled")
        except Exception as exc:  # noqa: BLE001 - surfaced to the dashboard as text
            _log(f"job {job_id} failed: {exc}")
            self.store.finish(job_id, "error", error=f"{type(exc).__name__}: {exc}"[:2000])
        finally:
            self.current_job = None
            # A job deleted while it ran: its row is gone, clean the file too.
            if self.store.get_job(job_id) is None:
                Path(job["audio_path"]).unlink(missing_ok=True)
