"""Transcription core shared by the web UI and the CLI.

One implementation of device selection, model loading and decoding, imported by
both app/server.py (the primary, web entry point) and main.py (a thin CLI kept
for local/headless debugging). Keeping the core here - rather than inside
main.py as before - means the deployable product (the UI) no longer imports
from a top-level script, which is the cleaner dependency direction.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional

import ctranslate2
from faster_whisper import WhisperModel

# Ukrainian is forced everywhere. Whisper's auto-detect regularly misclassifies
# Ukrainian as Russian on accented/noisy audio, so it is never trusted - a hard
# project requirement, not a tunable.
LANGUAGE = "uk"

# Model id, overridable per deployment via WHISPER_MODEL. large-v3 is the broad
# 99-language baseline; the Ukrainian fine-tune can be swapped in without a
# rebuild.
DEFAULT_MODEL = os.environ.get("WHISPER_MODEL", "large-v3")

# Progress callback receives a float in [0, 1]. Optional so the CLI can pass a
# stderr printer, the server can update job state, and callers who do not care
# can pass nothing.
ProgressCallback = Callable[[float], None]


def _gpu_with_most_free_memory(cuda_count: int) -> int:
    """Index of the visible CUDA device with the most free VRAM.

    On the shared rack this avoids hard-coding a card: the container is given
    all GPUs (see docker-compose.yml) and we pick the emptiest one at startup,
    which is the polite thing to do when other jobs share the rack.

    NVML is queried via nvidia-ml-py. Inside the container the NVIDIA runtime
    masks NVML to exactly the GPUs exposed to us, so NVML indices line up with
    CTranslate2's indices (both enumerate only the visible set). If NVML is
    missing or errors, fall back to device 0 rather than failing.

    Alternative considered: parsing `nvidia-smi --query-gpu=memory.free`. NVML
    avoids spawning a subprocess and brittle text parsing, so it is preferred.
    """
    try:
        import pynvml
    except Exception:
        return 0
    try:
        pynvml.nvmlInit()
    except Exception:
        return 0
    try:
        best_index, best_free = 0, -1
        for i in range(cuda_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            free = pynvml.nvmlDeviceGetMemoryInfo(handle).free
            if free > best_free:
                best_index, best_free = i, free
        return best_index
    except Exception:
        return 0
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


def select_device() -> tuple[str, str, int]:
    """Return (device, compute_type, device_index) for the best target.

    ctranslate2 is queried directly instead of importing torch - faster-whisper's
    backend is CTranslate2, so torch would be gigabytes of dead weight.
      GPU present -> float16 on the emptiest visible card
      CPU only    -> int8 (the practical CPU compute type)
    """
    try:
        cuda_count = ctranslate2.get_cuda_device_count()
    except Exception:
        # A driver quirk while querying the CUDA runtime is treated as "no GPU"
        # rather than crashing, so the laptop path is never blocked.
        cuda_count = 0

    if cuda_count <= 0:
        return ("cpu", "int8", 0)
    return ("cuda", "float16", _gpu_with_most_free_memory(cuda_count))


def load_model(model_name: str = DEFAULT_MODEL) -> WhisperModel:
    """Load a faster-whisper model onto the best available device."""
    device, compute_type, index = select_device()
    where = f"{device}:{index}" if device == "cuda" else device
    print(
        f"[uk-transcribe] loading '{model_name}' on {where} ({compute_type})",
        file=sys.stderr,
    )
    return WhisperModel(
        model_name,
        device=device,
        device_index=index,
        compute_type=compute_type,
    )


def transcribe_file(
    model: WhisperModel,
    audio_path: Path,
    progress_callback: Optional[ProgressCallback] = None,
) -> str:
    """Transcribe one file and return the full text.

    faster-whisper returns a lazy segment generator; iterating it is what drives
    decoding. Each segment carries an end timestamp, so segment.end / duration
    is a real progress fraction (not a fake timed spinner). beam_size=5 is the
    usual quality/speed compromise; vad_filter trims silence, which speeds up
    lecture audio and cuts the repeated-phrase hallucinations Whisper emits on
    quiet stretches (onnxruntime, which the VAD needs, ships with faster-whisper).
    """
    segments, info = model.transcribe(
        str(audio_path),
        language=LANGUAGE,
        beam_size=5,
        vad_filter=True,
    )
    duration = info.duration or 0.0
    parts: list[str] = []
    for segment in segments:
        parts.append(segment.text)
        if progress_callback is not None and duration > 0:
            progress_callback(min(segment.end / duration, 1.0))
    # Guarantee a final 100% even if the last segment ended before duration
    # (VAD can trim trailing silence, so the last end < duration).
    if progress_callback is not None:
        progress_callback(1.0)
    # Each segment's text carries its own leading space, so "" is the right join.
    return "".join(parts).strip()
