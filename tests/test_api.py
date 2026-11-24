from __future__ import annotations

import threading

from conftest import FakeEngine, upload, wait_for, wav_bytes
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Store
from app.server import create_app

FINAL = ("done", "error", "cancelled")


def done(client, job_id):
    wait_for(lambda: client.get(f"/jobs/{job_id}").json()["status"] in FINAL)
    return client.get(f"/jobs/{job_id}").json()


def test_upload_transcribe_and_export(client):
    r = upload(client)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["filename"] == "лекція.wav"
    assert abs(job["duration"] - 1.0) < 0.05  # probed at upload time

    job = done(client, job["id"])
    assert job["status"] == "done"
    assert job["progress"] == 1
    assert job["audio_available"]

    segs = client.get(f"/jobs/{job['id']}/segments").json()
    assert [s["text"] for s in segs] == ["Привіт.", "Це тест.", "Кінець."]
    later = client.get(f"/jobs/{job['id']}/segments", params={"after": 1}).json()
    assert [s["idx"] for s in later] == [2]

    txt = client.get(f"/jobs/{job['id']}/transcript.txt")
    assert txt.text == "Привіт.\nЦе тест.\nКінець.\n"
    assert "filename*=UTF-8''%D0%BB" in txt.headers["content-disposition"]
    srt = client.get(f"/jobs/{job['id']}/transcript.srt").text
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,000\nПривіт.\n")
    vtt = client.get(f"/jobs/{job['id']}/transcript.vtt").text
    assert vtt.startswith("WEBVTT\n") and "00:00:02.000 --> 00:00:03.000" in vtt
    assert client.get(f"/jobs/{job['id']}/transcript.pdf").status_code == 404

    audio = client.get(f"/jobs/{job['id']}/audio", headers={"Range": "bytes=0-3"})
    assert audio.status_code == 206 and audio.content == b"RIFF"


def test_history_search_and_paging(client):
    a = upload(client, "first.wav").json()["id"]
    done(client, a)
    b = upload(client, "second.mp3").json()["id"]
    done(client, b)
    hist = client.get("/jobs").json()
    assert hist["total"] == 2 and hist["items"][0]["id"] == b  # newest first
    assert client.get("/jobs", params={"q": "first"}).json()["total"] == 1
    assert client.get("/jobs", params={"q": "тест"}).json()["total"] == 2  # transcript text
    assert client.get("/jobs", params={"q": "%"}).json()["total"] == 0  # wildcards escaped
    assert client.get("/jobs", params={"limit": 1, "offset": 1}).json()["items"][0]["id"] == a
    assert client.get("/jobs", params={"status": "bogus"}).status_code == 422


def test_upload_validation(client, settings: Settings):
    assert upload(client, "x.exe").status_code == 415
    assert upload(client, "x.wav", b"not audio at all").status_code == 415
    assert upload(client, "x.wav", b"").status_code == 400
    assert upload(client, "x.wav", wav_bytes(40)).status_code == 413  # > 1 MB
    assert client.get("/jobs").json()["total"] == 0
    assert list(settings.uploads_dir.iterdir()) == []  # no leftover files


def test_path_names_are_cleaned(client):
    job = upload(client, "..\\..\\etc/evil<>.wav").json()
    assert job["filename"] == "evil.wav"


def test_bad_ids(client):
    for path in ("/jobs/abc", "/jobs/" + "0" * 32, "/jobs/ZZ/segments", "/jobs/..%2F..%2Fetc"):
        assert client.get(path).status_code == 404, path


def test_queue_position_cancel_delete_retry(client, engine: FakeEngine):
    engine.gate = threading.Event()
    first = upload(client, "a.wav").json()["id"]
    second = upload(client, "b.wav").json()["id"]
    wait_for(lambda: client.get(f"/jobs/{first}").json()["status"] == "processing")
    assert client.get(f"/jobs/{second}").json()["queue_position"] == 1
    assert [j["id"] for j in client.get("/queue").json()["items"]] == [first, second]

    assert client.post(f"/jobs/{second}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/jobs/{first}/cancel").status_code == 200
    engine.gate.set()
    assert done(client, first)["status"] == "cancelled"

    assert client.post(f"/jobs/{second}/retry").json()["status"] == "queued"
    assert done(client, second)["status"] == "done"
    assert client.post(f"/jobs/{second}/retry").status_code == 409

    assert client.delete(f"/jobs/{second}/audio").status_code == 204
    j = client.get(f"/jobs/{second}").json()
    assert not j["audio_available"]
    assert client.get(f"/jobs/{second}/audio").status_code == 410
    assert client.get(f"/jobs/{second}/transcript.txt").status_code == 200  # transcript kept

    assert client.delete(f"/jobs/{second}").status_code == 204
    assert client.get(f"/jobs/{second}").status_code == 404


def test_delete_while_processing(client, engine: FakeEngine, settings: Settings):
    engine.gate = threading.Event()
    job = upload(client).json()["id"]
    wait_for(lambda: client.get(f"/jobs/{job}").json()["status"] == "processing")
    assert client.delete(f"/jobs/{job}").status_code == 204
    engine.gate.set()
    wait_for(lambda: list(settings.uploads_dir.iterdir()) == [])
    assert client.get(f"/jobs/{job}").status_code == 404


def test_error_is_reported(client, engine: FakeEngine):
    engine.fail_next = True
    job = done(client, upload(client).json()["id"])
    assert job["status"] == "error" and "decoder exploded" in job["error"]


def test_queue_limit(client, engine: FakeEngine):
    engine.gate = threading.Event()
    for _ in range(5):
        assert upload(client).status_code == 201
    assert upload(client).status_code == 429


def test_settings(client):
    s = client.get("/settings").json()
    assert s["audio_retention_days"] == 7 and s["transcript_retention_days"] == 0
    r = client.put("/settings", json={"audio_retention_days": 3, "transcript_retention_days": 30})
    assert r.json()["audio_retention_days"] == 3
    bad = {"audio_retention_days": -1, "transcript_retention_days": 0}
    assert client.put("/settings", json=bad).status_code == 422


def test_health_while_loading_and_on_failure(settings):
    gate = threading.Event()

    class Slow(FakeEngine):
        def load(self):
            gate.wait(10)
            return super().load()

    with TestClient(create_app(settings, Slow())) as c:
        r = c.get("/healthz")
        assert r.status_code == 503 and r.json()["status"] == "loading"
        assert c.get("/jobs").status_code == 200  # history works while loading
        gate.set()
        wait_for(lambda: c.get("/healthz").status_code == 200)
        assert c.get("/healthz").json()["device"] == "cpu"

    with TestClient(create_app(settings, FakeEngine(fail_load=True))) as c:
        wait_for(lambda: c.get("/healthz").json()["status"] == "error")
        assert c.get("/healthz").status_code == 503


def test_restart_requeues_interrupted_jobs(settings: Settings, engine):
    settings.uploads_dir.mkdir(parents=True)
    job_id = "a" * 32
    audio = settings.uploads_dir / f"{job_id}.wav"
    audio.write_bytes(wav_bytes())
    store = Store(settings.db_path)
    store.add_job(job_id, "a.wav", audio, 10)
    assert store.claim_next()["status"] == "processing"  # the container "dies" here
    store.close()

    with TestClient(create_app(settings, engine)) as c:
        wait_for(lambda: c.get(f"/jobs/{job_id}").json()["status"] == "done")
