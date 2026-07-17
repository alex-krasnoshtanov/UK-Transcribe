"""Command-line transcription - a thin wrapper over the shared core.

The web UI (app/server.py) is the primary way to use this project; this CLI is
kept only for quick local tests and headless debugging on the server. Both share
the exact same core in app/transcriber.py, so there is one implementation, not
two competing ones.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.transcriber import DEFAULT_MODEL, load_model, transcribe_file

BASE_DIR = Path(__file__).resolve().parent
OUTPUTS_DIR = BASE_DIR / "outputs"


def _slugify(value: str) -> str:
    """Filesystem-safe folder name from a model id like 'org/name'."""
    return value.replace("/", "__").replace(":", "_")


def _print_progress(fraction: float) -> None:
    # Rewrite a single stderr line so the transcript on stdout stays clean for
    # redirection (`python main.py clip.wav > out.txt`).
    sys.stderr.write(f"\r[uk-transcribe] {fraction * 100:5.1f}%")
    sys.stderr.flush()


def run_cli() -> int:
    parser = argparse.ArgumentParser(description="Transcribe Ukrainian audio to text.")
    parser.add_argument("audio_path", type=Path, help="path to an audio file")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"faster-whisper model id or local path (default: {DEFAULT_MODEL})",
    )
    args = parser.parse_args()

    audio_path = args.audio_path.expanduser()
    if not audio_path.is_file():
        print(f"[uk-transcribe] audio file not found: {audio_path}", file=sys.stderr)
        return 1

    model = load_model(args.model)
    text = transcribe_file(model, audio_path, progress_callback=_print_progress)
    sys.stderr.write("\n")

    # One output folder per (clip, model) pair, so different models on the same
    # clip do not overwrite each other - handy for eyeballing model differences.
    run_dir = OUTPUTS_DIR / f"{audio_path.stem}__{_slugify(args.model)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = run_dir / "transcript.txt"
    transcript_path.write_text(text, encoding="utf-8")

    print(text)
    print(f"[uk-transcribe] written to {transcript_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
