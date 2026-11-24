"""Runtime settings, all from environment variables.

One frozen dataclass read once at startup, so every module sees the same values
and tests can build their own without touching the process environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Containers and codecs PyAV/FFmpeg reads reliably. Anything else is refused at
# upload time, before it reaches the queue.
ALLOWED_SUFFIXES = frozenset(
    {
        ".aac", ".flac", ".m4a", ".mka", ".mkv", ".mov", ".mp3", ".mp4",
        ".oga", ".ogg", ".opus", ".wav", ".webm", ".wma",
    }
)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path = BASE_DIR / "data"
    model: str = "large-v3"
    # "auto": float16 on a GPU, int8 on the CPU.
    compute_type: str = "auto"
    beam_size: int = 5
    vad: bool = True
    # "auto" (the card with most free VRAM, else CPU), "cpu", or an nvidia-smi index.
    device: str = "auto"
    # In auto mode the CPU is used when no card has this much VRAM free (MiB).
    min_free_mib: int = 5000
    cpu_threads: int = 4
    max_upload_mb: int = 2048
    max_queue: int = 50
    # Defaults only: the dashboard can change both, and the values live in the DB.
    audio_retention_days: int = 7
    transcript_retention_days: int = 0
    version: str = "dev"
    allowed_suffixes: frozenset[str] = field(default=ALLOWED_SUFFIXES)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ukt.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR") or (BASE_DIR / "data")),
            model=os.environ.get("WHISPER_MODEL") or "large-v3",
            compute_type=os.environ.get("COMPUTE_TYPE") or "auto",
            beam_size=_int("BEAM_SIZE", 5),
            vad=_bool("VAD", True),
            device=(os.environ.get("DEVICE") or "auto").strip().lower(),
            min_free_mib=_int("MIN_FREE_MIB", 5000),
            cpu_threads=_int("CPU_THREADS", 4),
            max_upload_mb=_int("MAX_UPLOAD_MB", 2048),
            max_queue=_int("MAX_QUEUE", 50),
            audio_retention_days=_int("AUDIO_RETENTION_DAYS", 7),
            transcript_retention_days=_int("TRANSCRIPT_RETENTION_DAYS", 0),
            version=os.environ.get("GIT_SHA") or "dev",
        )
