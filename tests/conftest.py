from __future__ import annotations

import io
import sys
import threading
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))

from app.config import Settings  # noqa: E402
from app.server import create_app  # noqa: E402
from app.transcriber import Cancelled, EngineInfo, Segment  # noqa: E402


def wav_bytes(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


class FakeEngine:
    """Emits three segments per file. `gate` (when set) holds each segment until released."""

    def __init__(self, fail_load: bool = False) -> None:
        self.info: EngineInfo | None = None
        self.fail_load = fail_load
        self.gate: threading.Event | None = None
        self.calls: list[Path] = []
        self.fail_next = False

    def load(self) -> EngineInfo:
        if self.fail_load:
            raise RuntimeError("no model")
        self.info = EngineInfo("fake", "cpu", "int8", "test")
        return self.info

    def transcribe(self, audio_path, on_segment, should_cancel=lambda: False) -> float:
        self.calls.append(Path(audio_path))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("decoder exploded")
        duration = 3.0
        for i, text in enumerate(["Привіт.", "Це тест.", "Кінець."]):
            if self.gate is not None:
                assert self.gate.wait(10)
            if should_cancel():
                raise Cancelled()
            on_segment(Segment(float(i), float(i + 1), text), duration)
        return duration


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", max_upload_mb=1, max_queue=5)


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def client(settings, engine):
    with TestClient(create_app(settings, engine)) as c:
        wait_for(lambda: c.get("/healthz").status_code == 200)
        yield c
        if engine.gate is not None:
            engine.gate.set()


def wait_for(cond, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def upload(client, name: str = "лекція.wav", body: bytes | None = None):
    return client.post(
        "/jobs", params={"filename": name}, content=wav_bytes() if body is None else body
    )
