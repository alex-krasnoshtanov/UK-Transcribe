"""Command-line transcription for quick local tests and headless debugging.

The web dashboard (web/) and API (app/server.py) are the product; this shares
the same engine (app/transcriber.py), so there is one implementation.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from app.config import Settings
from app.exports import FORMATS
from app.transcriber import Segment, WhisperEngine


def run_cli() -> int:
    base = Settings.from_env()
    parser = argparse.ArgumentParser(description="Transcribe Ukrainian audio.")
    parser.add_argument("audio_path", type=Path, help="path to an audio or video file")
    parser.add_argument("--model", default=base.model, help=f"default: {base.model}")
    parser.add_argument("--device", default=base.device, help="auto, cpu or a GPU index")
    parser.add_argument("--format", choices=sorted(FORMATS), default="txt")
    parser.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    args = parser.parse_args()

    audio_path = args.audio_path.expanduser()
    if not audio_path.is_file():
        print(f"[uk-transcribe] audio file not found: {audio_path}", file=sys.stderr)
        return 1

    engine = WhisperEngine(replace(base, model=args.model, device=args.device))
    engine.load()
    segments: list[Segment] = []

    def on_segment(seg: Segment, duration: float) -> None:
        segments.append(seg)
        if duration > 0:
            sys.stderr.write(f"\r[uk-transcribe] {min(seg.end / duration, 1) * 100:5.1f}%")
            sys.stderr.flush()

    engine.transcribe(audio_path, on_segment)
    sys.stderr.write("\n")
    text = FORMATS[args.format][0](segments)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"[uk-transcribe] written to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
