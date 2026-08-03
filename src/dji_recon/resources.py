from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import psutil

from .util import executable_version


def detect_resources(workspace: Path) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage(workspace)
    gpus: list[dict[str, Any]] = []
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        result = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=index,name,memory.total,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = [part.strip() for part in line.split(",")]
                if len(parts) >= 3:
                    gpus.append(
                        {
                            "index": int(parts[0]),
                            "name": parts[1],
                            "memory_mib": int(parts[2]),
                            "compute_capability": parts[3] if len(parts) > 3 else None,
                        }
                    )
    tools = {
        name: executable_version(name, args)
        for name, args in {
            "colmap": ["-h"],
            "blender": ["--version"],
            "texrecon": ["--help"],
            "exiftool": ["-ver"],
            "rclone": ["version"],
            "frpc": ["--version"],
        }.items()
    }
    colmap_cuda = False
    if shutil.which("colmap"):
        result = subprocess.run(["colmap", "-h"], capture_output=True, text=True, check=False, timeout=15)
        description = f"{result.stdout}\n{result.stderr}"
        colmap_cuda = "with CUDA" in description and "without CUDA" not in description
    return {
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "ram_total_bytes": memory.total,
        "ram_available_bytes": memory.available,
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "gpus": gpus,
        "colmap_cuda_enabled": colmap_cuda,
        "tools": tools,
    }


def choose_working_dimension(config: dict[str, Any], image_count: int, resources: dict[str, Any]) -> tuple[int, str]:
    requested = int(config["resources"].get("max_working_dimension", 4000))
    ram_gb = resources["ram_available_bytes"] / 1024**3
    disk_gb = resources["disk_free_bytes"] / 1024**3
    # Conservative empirical envelope: dense stereo is the peak stage. This is
    # intentionally an estimate and is recorded in the manifest.
    estimated_disk_gb = image_count * (requested**2) * 3 * 8 / 1024**3
    estimated_ram_gb = min(image_count, 64) * (requested**2) * 12 / 1024**3
    if not config["resources"].get("auto_downscale", True):
        return requested, "configured value; automatic downscaling disabled"
    dimension = requested
    reasons: list[str] = []
    while dimension > 1600 and (
        estimated_disk_gb > disk_gb * 0.65 or estimated_ram_gb > max(1, ram_gb - 4)
    ):
        dimension = int(dimension * 0.8) // 16 * 16
        scale = (dimension / requested) ** 2
        estimated_disk_gb *= scale
        estimated_ram_gb *= scale
        reasons.append("estimated dense-stage resources exceed safe RAM/disk envelope")
    return dimension, "; ".join(dict.fromkeys(reasons)) or "configured value fits estimated resource envelope"
