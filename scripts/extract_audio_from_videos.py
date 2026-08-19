from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from shutil import which

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = REPO_ROOT / "raw_videos"
LEGACY_INPUT_DIR = REPO_ROOT / "raw_video"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "ready_audios"
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi"}
AUDIO_EXTENSION = ".wav"
AUDIO_SUFFIX = "_audio"


def resolve_input_dirs(override_dirs: list[Path] | None) -> list[Path]:
    if override_dirs:
        return override_dirs

    if DEFAULT_INPUT_DIR.exists():
        return [DEFAULT_INPUT_DIR]

    if LEGACY_INPUT_DIR.exists():
        return [LEGACY_INPUT_DIR]

    return [DEFAULT_INPUT_DIR]


def iter_videos(input_dirs: list[Path]) -> list[Path]:
    videos: list[Path] = []
    seen: set[Path] = set()

    for input_dir in input_dirs:
        if not input_dir.exists():
            logging.warning("input directory does not exist: %s", input_dir)
            continue
        if not input_dir.is_dir():
            logging.warning("input path is not a directory: %s", input_dir)
            continue

        for path in sorted(input_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            if path in seen:
                continue
            seen.add(path)
            videos.append(path)

    return videos


def output_path_for(video_path: Path, output_dir: Path) -> Path:
    return output_dir / f"{video_path.stem}{AUDIO_SUFFIX}{AUDIO_EXTENSION}"


def extract_audio(ffmpeg_path: str, video_path: Path, output_path: Path, overwrite: bool) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f"{output_path.stem}.tmp{output_path.suffix}")

    if temp_path.exists():
        temp_path.unlink()

    if overwrite and output_path.exists():
        output_path.unlink()

    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(temp_path),
    ]

    try:
        subprocess.run(command, check=True)
        temp_path.replace(output_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract mono 16 kHz audio tracks from videos into ready_audios/.",
    )
    parser.add_argument(
        "--input-dir",
        action="append",
        type=Path,
        dest="input_dirs",
        help="video folder to scan (repeatable; defaults to raw_videos/ or raw_video/)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="directory for extracted audio files (default: ready_audios/)",
    )
    parser.add_argument(
        "--ffmpeg",
        default=which("ffmpeg"),
        help="path to ffmpeg executable (default: auto-detect from PATH)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="recreate audio files even when a matching output already exists",
    )
    return parser.parse_args()


def run() -> int:
    args = parse_args()

    if not args.ffmpeg:
        print(
            "ffmpeg was not found. Install it or pass --ffmpeg with the executable path.",
            file=sys.stderr,
        )
        return 1

    input_dirs = resolve_input_dirs(args.input_dirs)
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    videos = iter_videos(input_dirs)
    if not videos:
        print("No video files found to convert.", file=sys.stderr)
        return 0

    converted = 0
    skipped = 0
    failed = 0

    for video_path in videos:
        output_path = output_path_for(video_path, output_dir)
        if output_path.exists() and output_path.stat().st_size > 0 and not args.overwrite:
            logging.info("skipping existing audio: %s", output_path.name)
            skipped += 1
            continue

        try:
            logging.info("extracting %s -> %s", video_path.name, output_path.name)
            extract_audio(args.ffmpeg, video_path, output_path, args.overwrite)
            converted += 1
        except subprocess.CalledProcessError as exc:
            failed += 1
            print(
                f"Failed to extract audio from {video_path}: ffmpeg exited with {exc.returncode}",
                file=sys.stderr,
            )
        except OSError as exc:
            failed += 1
            print(f"Failed to extract audio from {video_path}: {exc}", file=sys.stderr)

    print(
        f"Done. converted={converted} skipped={skipped} failed={failed} output={output_dir}",
        file=sys.stderr,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    raise SystemExit(run())
