"""deploy/cluster.yml against the student cluster's rules.

The cluster only refuses a broken stack at deploy time (or shows it as a pod
that never starts), so these checks catch it in CI first. Ported from the
Florensis project's tests/deployment/test_compose.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "deploy/cluster.yml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]
THREAD_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_cluster_rules_for_each_service(name):
    service = SERVICES[name]
    # Service names are DNS names shared with every team in the namespace.
    assert name.startswith("ukt-")
    assert service.get("restart") in {"unless-stopped", "always"}  # no bare pods
    assert "healthcheck" in service  # d2k ignores the image's HEALTHCHECK
    assert "build" not in service
    assert re.fullmatch(r"\d+:\d+", service["user"]) and not service["user"].startswith("0:")
    for volume in service.get("volumes", []):
        assert volume.split(":", 1)[0] in COMPOSE["volumes"], f"{name}: {volume} is a bind mount"
    for key in ("privileged", "cap_add", "devices", "network_mode", "pid", "security_opt", "gpus"):
        assert key not in service, f"{name}: {key}"


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_limits_and_no_whole_gpu(name):
    resources = SERVICES[name].get("deploy", {}).get("resources", {})
    limits = resources["limits"]
    assert float(limits["cpus"]) > 0
    assert re.fullmatch(r"\d+[mg]", str(limits["memory"]))
    # A device reservation takes a whole card; a slice is BUAS_GPU_MEMORY.
    assert "reservations" not in resources


def test_only_the_api_gets_a_gpu_slice_and_it_is_sized():
    api = SERVICES["ukt-api"]["environment"]
    slice_mib = int(api["BUAS_GPU_MEMORY"])
    assert 4096 <= slice_mib <= 12288
    # The app must fall back to the CPU before it would overrun its slice.
    assert int(api["MIN_FREE_MIB"]) <= slice_mib
    assert api["COMPUTE_TYPE"] == "auto"  # float16 would crash on the CPU fallback
    assert "BUAS_GPU_MEMORY" not in SERVICES["ukt-web"].get("environment", {})


def test_python_threads_stay_within_the_cpu_limit():
    api = SERVICES["ukt-api"]
    cpus = float(api["deploy"]["resources"]["limits"]["cpus"])
    for variable in (*THREAD_VARIABLES, "CPU_THREADS"):
        assert 1 <= int(api["environment"][variable]) <= cpus, variable


def test_ports():
    published = []
    for service in SERVICES.values():
        for port in service.get("ports", []):
            resolved = re.sub(r"\$\{[A-Z_]+:-(\d+)\}", r"\1", port)
            published.append(int(resolved.split(":")[0]))
    assert published == [30500]  # only the dashboard is published
    assert all(30000 <= p <= 32699 for p in published)
    assert "ports" not in SERVICES["ukt-api"]


def test_volumes_are_external_prefixed_and_single_user():
    users: dict[str, list[str]] = {}
    for name, service in SERVICES.items():
        for volume in service.get("volumes", []):
            users.setdefault(volume.split(":", 1)[0], []).append(name)
    assert all(len(v) == 1 for v in users.values()), users  # ReadWriteOnce PVCs
    for key, volume in COMPOSE["volumes"].items():
        assert volume.get("external") is True, key
        assert volume["name"].startswith("ukt-"), key


def test_dashboard_reads_only_the_api():
    web = SERVICES["ukt-web"]
    assert "volumes" not in web
    assert web["environment"]["API_URL"] == "http://ukt-api:8000"


def test_deploy_script_knows_the_services_and_volumes():
    script = (ROOT / "deploy/ukt.sh").read_text(encoding="utf-8")
    for name in SERVICES:
        assert name in script, name
    for volume in COMPOSE["volumes"].values():
        assert volume["name"] in script, volume["name"]
    uid = SERVICES["ukt-api"]["user"].split(":")[0]
    assert uid in script  # the volumes are handed to the api's user
