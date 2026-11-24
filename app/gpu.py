"""Choosing the GPU before CTranslate2 initialises CUDA.

Free memory differs from card to card on the shared university servers, and on
the student cluster the card is also used by work outside Kubernetes. So the
choice is made once at startup from `nvidia-smi`: the card with the most free
VRAM, or the CPU when no card has `min_free_mib` free.

The chosen card is pinned by UUID in CUDA_VISIBLE_DEVICES (with
CUDA_DEVICE_ORDER=PCI_BUS_ID), so inside the process it is always device 0.
Pinning by UUID avoids the mismatch between nvidia-smi's PCI order and CUDA's
default "fastest first" order, which picked the wrong card on mixed racks.

Ported from the CEVA project's vision/gpu.py.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field


class DeviceError(RuntimeError):
    """A specific GPU was requested but is not available."""


@dataclass(frozen=True)
class Gpu:
    index: int
    uuid: str
    name: str
    free_mib: int
    total_mib: int
    util_pct: int


@dataclass(frozen=True)
class DeviceChoice:
    device: str  # "cuda" or "cpu"
    reason: str
    gpu: Gpu | None = None
    candidates: list[Gpu] = field(default_factory=list)


def parse_nvidia_smi(text: str) -> list[Gpu]:
    """Cards from `nvidia-smi --query-gpu=index,uuid,name,memory.free,memory.total,utilization.gpu`."""
    gpus = []
    for line in text.strip().splitlines():
        if not line.strip():
            continue
        idx, uuid, name, free, total, util = (v.strip() for v in line.split(","))
        gpus.append(
            Gpu(int(idx), uuid, name, int(free), int(total), int(util) if util.isdigit() else 0)
        )
    return gpus


def list_gpus() -> list[Gpu]:
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return []
    try:
        out = subprocess.run(  # noqa: S603 - fixed arguments, resolved path
            [
                smi,
                "--query-gpu=index,uuid,name,memory.free,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    try:
        return parse_nvidia_smi(out)
    except ValueError:
        return []


def allowed(gpus: list[Gpu], visible: str | None) -> list[Gpu]:
    """Cards allowed by an existing CUDA_VISIBLE_DEVICES (indices or UUIDs)."""
    if visible is None:
        return gpus
    wanted = [v.strip() for v in visible.split(",") if v.strip()]
    return [g for g in gpus if str(g.index) in wanted or any(g.uuid.startswith(w) for w in wanted)]


def choose(gpus: list[Gpu], choice: str = "auto", min_free_mib: int = 5000) -> DeviceChoice:
    if choice == "cpu":
        return DeviceChoice("cpu", "requested", candidates=gpus)
    if not gpus:
        if choice != "auto":
            raise DeviceError(f"gpu {choice} requested, but nvidia-smi lists no usable GPU")
        return DeviceChoice("cpu", "no GPU found via nvidia-smi")
    if choice == "auto":
        best = max(gpus, key=lambda g: (g.free_mib, -g.util_pct))
        if best.free_mib < min_free_mib:
            return DeviceChoice(
                "cpu",
                f"best card (gpu {best.index}) has only {best.free_mib} MiB free, need {min_free_mib}",
                candidates=gpus,
            )
        return DeviceChoice("cuda", "most free VRAM", best, gpus)
    match = [g for g in gpus if str(g.index) == choice]
    if not match:
        raise DeviceError(f"gpu {choice} not found or not allowed by CUDA_VISIBLE_DEVICES")
    gpu = match[0]
    reason = "requested"
    if gpu.free_mib < min_free_mib:
        reason += f" (warning: only {gpu.free_mib} MiB free)"
    return DeviceChoice("cuda", reason, gpu, gpus)


def select_device(choice: str = "auto", min_free_mib: int = 5000) -> DeviceChoice:
    """Choose a device and pin the chosen card for this process."""
    result = choose(
        allowed(list_gpus(), os.environ.get("CUDA_VISIBLE_DEVICES")), choice, min_free_mib
    )
    if result.gpu is not None:
        os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        os.environ["CUDA_VISIBLE_DEVICES"] = result.gpu.uuid
    return result


def free_mib(uuid: str | None) -> int | None:
    """Current free VRAM of one card, for /healthz. None if unknown."""
    if not uuid:
        return None
    for gpu in list_gpus():
        if gpu.uuid == uuid:
            return gpu.free_mib
    return None
