"""Transcription core shared by the API worker and the CLI.

`WhisperEngine` picks the device (app/gpu.py), loads the faster-whisper model
once, and streams timestamped segments back through a callback, so the worker
can store each one as it arrives (live transcript, real progress).

Ukrainian is forced: Whisper's auto-detect regularly mistakes accented or noisy
Ukrainian for Russian, so detection is never trusted.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

from app import gpu
from app.config import Settings

LANGUAGE = "uk"


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class EngineInfo:
    model: str
    device: str  # "cuda" or "cpu"
    compute_type: str
    reason: str
    gpu_name: str | None = None
    gpu_uuid: str | None = None


class Cancelled(Exception):
    """The job was cancelled while it was being transcribed."""


SegmentCallback = Callable[[Segment, float], None]
"""Called with each segment and the duration of the audio (seconds)."""


class Engine(Protocol):
    info: EngineInfo | None

    def load(self) -> EngineInfo: ...

    def transcribe(
        self,
        audio_path: Path,
        on_segment: SegmentCallback,
        should_cancel: Callable[[], bool] = lambda: False,
    ) -> float:
        """Transcribe one file; returns the audio duration in seconds."""
        ...


def _log(msg: str) -> None:
    print(f"[uk-transcribe] {msg}", file=sys.stderr, flush=True)


class WhisperEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.info: EngineInfo | None = None
        self._model = None

    def load(self) -> EngineInfo:
        # The device must be chosen before ctranslate2 touches CUDA: select_device
        # pins the card through CUDA_VISIBLE_DEVICES.
        choice = gpu.select_device(self.settings.device, self.settings.min_free_mib)
        from faster_whisper import WhisperModel  # imported after pinning

        device = choice.device
        compute = self.settings.compute_type
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        where = f"{device} ({choice.gpu.name})" if choice.gpu else device
        _log(f"loading '{self.settings.model}' on {where}, {compute}: {choice.reason}")
        try:
            self._model = WhisperModel(
                self.settings.model,
                device=device,
                device_index=0,
                compute_type=compute,
                cpu_threads=self.settings.cpu_threads,
            )
        except Exception as exc:  # noqa: BLE001 - e.g. CUDA OOM on a busy shared card
            if device != "cuda" or self.settings.device != "auto":
                raise
            _log(f"GPU load failed ({exc}); falling back to the CPU")
            device, compute = "cpu", "int8"
            choice = gpu.DeviceChoice("cpu", f"GPU load failed: {exc}")
            self._model = WhisperModel(
                self.settings.model,
                device="cpu",
                compute_type=compute,
                cpu_threads=self.settings.cpu_threads,
            )
        self.info = EngineInfo(
            model=self.settings.model,
            device=device,
            compute_type=compute,
            reason=choice.reason,
            gpu_name=choice.gpu.name if choice.gpu else None,
            gpu_uuid=choice.gpu.uuid if choice.gpu else None,
        )
        _log("model ready")
        return self.info

    def transcribe(
        self,
        audio_path: Path,
        on_segment: SegmentCallback,
        should_cancel: Callable[[], bool] = lambda: False,
    ) -> float:
        if self._model is None:
            raise RuntimeError("model not loaded")
        segments, info = self._model.transcribe(
            str(audio_path),
            language=LANGUAGE,
            beam_size=self.settings.beam_size,
            vad_filter=self.settings.vad,
        )
        duration = float(info.duration or 0.0)
        # The generator is lazy: iterating it is what drives decoding, so a
        # cancel takes effect between two segments.
        for seg in segments:
            if should_cancel():
                raise Cancelled()
            on_segment(Segment(float(seg.start), float(seg.end), seg.text.strip()), duration)
        return duration


def transcribe_to_text(
    engine: Engine,
    audio_path: Path,
    progress: Optional[Callable[[float], None]] = None,
) -> str:
    """Plain-text transcript of one file (used by the CLI)."""
    parts: list[str] = []

    def on_segment(seg: Segment, duration: float) -> None:
        parts.append(seg.text)
        if progress and duration > 0:
            progress(min(seg.end / duration, 1.0))

    engine.transcribe(audio_path, on_segment)
    if progress:
        progress(1.0)
    return " ".join(p for p in parts if p).strip()
