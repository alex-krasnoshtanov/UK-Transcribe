from __future__ import annotations

import time

from app.config import Settings
from app.db import Store
from app.exports import to_srt, to_txt, to_vtt
from app.gpu import allowed, choose, parse_nvidia_smi
from app.transcriber import Segment
from app.worker import sweep

SMI = """0, GPU-aaaa, NVIDIA RTX A6000, 1000, 49140, 90
1, GPU-bbbb, NVIDIA RTX A6000, 30000, 49140, 10
2, GPU-cccc, NVIDIA RTX A6000, 30000, 49140, 50
"""


def test_gpu_choice():
    gpus = parse_nvidia_smi(SMI)
    c = choose(gpus, "auto", 5000)
    assert c.device == "cuda" and c.gpu.uuid == "GPU-bbbb"  # tie broken by utilisation
    assert choose(gpus, "auto", 40000).device == "cpu"
    assert choose(gpus, "cpu").device == "cpu"
    assert choose(gpus, "0", 5000).reason.startswith("requested (warning")
    assert choose([], "auto").device == "cpu"
    assert [g.index for g in allowed(gpus, "GPU-cccc")] == [2]
    assert [g.index for g in allowed(gpus, "0,1")] == [0, 1]


def test_exports_format_hours_and_skip_empty():
    segs = [Segment(3725.5, 3727.25, "Так."), Segment(3728, 3729, "")]
    assert to_txt(segs) == "Так.\n"
    assert to_srt(segs) == "1\n01:02:05,500 --> 01:02:07,250\nТак.\n"
    assert to_vtt(segs) == "WEBVTT\n\n01:02:05.500 --> 01:02:07.250\nТак.\n"


def test_retention_sweep(tmp_path):
    settings = Settings(data_dir=tmp_path)
    store = Store(settings.db_path)
    old, new = tmp_path / "old.wav", tmp_path / "new.wav"
    ids = [f"{i:032x}" for i in range(2)]
    for job_id, p in zip(ids, (old, new)):
        p.write_bytes(b"x")
        store.add_job(job_id, p.name, p, 1)
        store.claim_next()
        store.finish(job_id, "done")
    store._exec("UPDATE jobs SET finished_at = ? WHERE id = ?", (time.time() - 10 * 86400, ids[0]))

    assert sweep(store, settings) == (1, 0)  # defaults: audio after 7 days, keep transcripts
    assert not old.exists() and new.exists()
    assert store.get_job(ids[0])["audio_deleted_at"] is not None

    store.set_setting("transcript_retention_days", "5")
    assert sweep(store, settings) == (0, 1)
    assert store.get_job(ids[0]) is None and store.get_job(ids[1]) is not None
