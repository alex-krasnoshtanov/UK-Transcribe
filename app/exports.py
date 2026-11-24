"""Transcript formats built from stored segments: plain text, SRT and WebVTT."""

from __future__ import annotations

from typing import Iterable, Protocol


class _Seg(Protocol):
    start: float
    end: float
    text: str


def _stamp(seconds: float, sep: str) -> str:
    ms = max(0, round(seconds * 1000))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_txt(segments: Iterable[_Seg]) -> str:
    return "\n".join(s.text for s in segments if s.text).strip() + "\n"


def to_srt(segments: Iterable[_Seg]) -> str:
    blocks = []
    for n, s in enumerate((s for s in segments if s.text), start=1):
        blocks.append(f"{n}\n{_stamp(s.start, ',')} --> {_stamp(s.end, ',')}\n{s.text}\n")
    return "\n".join(blocks)


def to_vtt(segments: Iterable[_Seg]) -> str:
    blocks = ["WEBVTT\n"]
    for s in segments:
        if s.text:
            blocks.append(f"{_stamp(s.start, '.')} --> {_stamp(s.end, '.')}\n{s.text}\n")
    return "\n".join(blocks)


FORMATS = {
    "txt": (to_txt, "text/plain; charset=utf-8"),
    "srt": (to_srt, "application/x-subrip; charset=utf-8"),
    "vtt": (to_vtt, "text/vtt; charset=utf-8"),
}
