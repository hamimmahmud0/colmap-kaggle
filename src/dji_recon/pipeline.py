from __future__ import annotations

import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .raw_convert import convert_dng, converted_is_valid
from .resources import choose_working_dimension, detect_resources
from .storage import EphemeralRclone, download_input
from .util import (
    CommandError,
    atomic_write_json,
    file_identity,
    fingerprint,
    redact,
    redact_text,
    run_command,
    sha256_file,
    utc_now,
)


STAGES = [
    "download",
    "convert",
    "preflight",
    "feature_extract",
    "match",
    "sparse",
    "align",
    "dense",
    "mesh",
    "texture",
    "variants",
    "validate",
    "package",
    "upload",
]


class PipelineError(RuntimeError):
    pass


@dataclass
class RuntimeSecrets:
    mega_email: str | None = None
    mega_password: str | None = None
    drive_token: str | None = None
    input_folder_url: str | None = None

    @classmethod
    def from_environment(cls) -> "RuntimeSecrets":
        return cls(
            mega_email=os.environ.get("MEGA_EMAIL"),
            mega_password=os.environ.get("MEGA_PASSWORD"),
            drive_token=os.environ.get("GOOGLE_DRIVE_TOKEN"),
            input_folder_url=os.environ.get("INPUT_FOLDER_URL"),
        )

    def values(self) -> list[str]:
        return [value for value in vars(self).values() if value]


@dataclass
class PipelineEvent:
    timestamp: str
    level: str
    message: str
    stage: str | None = None


@dataclass
class PipelineContext:
    config: dict[str, Any]
    secrets: RuntimeSecrets = field(default_factory=RuntimeSecrets.from_environment)
    event_callback: Callable[[PipelineEvent], None] | None = None

    def __post_init__(self) -> None:
        self.workspace = Path(self.config["workspace"]).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.meta_dir = self.workspace / ".pipeline"
        self.logs_dir = self.workspace / "logs"
        self.meta_dir.mkdir(exist_ok=True)
        self.logs_dir.mkdir(exist_ok=True)
        self.state_path = self.meta_dir / "state.json"
        self.current_stage: str | None = None
        self._lock = threading.Lock()
        self.state = self._load_state()
        self.resources = detect_resources(self.workspace)

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                backup = self.state_path.with_suffix(".corrupt.json")
                shutil.copy2(self.state_path, backup)
        return {"schema_version": 1, "created_at": utc_now(), "stages": {}, "events": []}

    def save(self) -> None:
        with self._lock:
            atomic_write_json(self.state_path, redact(self.state))

    def log(self, message: str, level: str = "info") -> None:
        clean = redact_text(str(message), self.secrets.values())
        event = PipelineEvent(utc_now(), level, clean, self.current_stage)
        line = f"{event.timestamp} [{level.upper()}]"
        if self.current_stage:
            line += f" [{self.current_stage}]"
        line += f" {clean}"
        with (self.logs_dir / "pipeline.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        self.state.setdefault("events", []).append(vars(event))
        self.state["events"] = self.state["events"][-500:]
        if level == "warning" and self.current_stage:
            record = self.state.get("stages", {}).get(self.current_stage)
            if record is not None:
                record.setdefault("warnings", []).append(clean)
        if self.event_callback:
            self.event_callback(event)

    def path(self, name: str) -> Path:
        paths = {
            "input": self.workspace / "downloads" / "input",
            "capture": self.workspace / "dji_capture",
            "working": self.workspace / "working_images",
            "database": self.workspace / "colmap.db",
            "sparse": self.workspace / "sparse",
            "aligned": self.workspace / "aligned",
            "dense": self.workspace / "dense",
            "mesh": self.workspace / "meshes" / "reconstruction.ply",
            "textured": self.workspace / "meshes" / "textured" / "model.obj",
            "variants": self.workspace / "artifacts" / "variants",
            "packages": self.workspace / "artifacts" / "packages",
            "manifest": self.workspace / "artifacts" / "manifest.json",
        }
        return paths[name]


def _files(path: Path, extensions: tuple[str, ...] | None = None) -> list[Path]:
    if not path.exists():
        return []
    result = [item for item in path.rglob("*") if item.is_file()]
    if extensions:
        result = [item for item in result if item.suffix.lower() in extensions]
    return sorted(result, key=lambda item: str(item).casefold())


def _outputs_exist(outputs: list[str]) -> bool:
    return bool(outputs) and all(Path(item).exists() for item in outputs)


def _stage_fingerprint(ctx: PipelineContext, stage: str) -> str:
    index = STAGES.index(stage)
    previous = STAGES[index - 1] if index else None
    previous_record = ctx.state.get("stages", {}).get(previous, {}) if previous else {}
    data = {
        "stage": stage,
        "config": ctx.config,
        "previous_fingerprint": previous_record.get("fingerprint"),
        "previous_outputs": previous_record.get("output_identities", []),
    }
    if stage == "download" and ctx.config["input"].get("type") == "local":
        source = Path(ctx.config["input"]["path"]).expanduser()
        data["local_inputs"] = [
            {"path": str(item.relative_to(source)), "size": item.stat().st_size, "mtime_ns": item.stat().st_mtime_ns}
            for item in _files(source, (".dng", ".jpg", ".jpeg"))
        ]
    return fingerprint(data)


def _record_outputs(outputs: list[Path]) -> list[dict[str, Any]]:
    identities = []
    for output in outputs:
        if output.is_file():
            identities.append(file_identity(output))
        elif output.is_dir():
            members = [
                {
                    "path": str(item.relative_to(output)),
                    "size": item.stat().st_size,
                    "mtime_ns": item.stat().st_mtime_ns,
                }
                for item in _files(output)
            ]
            identities.append({"path": str(output), "files": len(members), "listing_fingerprint": fingerprint(members)})
    return identities


def _mock_stage(ctx: PipelineContext, stage: str) -> list[Path]:
    marker = ctx.meta_dir / "mock" / f"{stage}.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(marker, {"stage": stage, "mode": "mock", "timestamp": utc_now()})
    if stage == "download":
        ctx.path("input").mkdir(parents=True, exist_ok=True)
    elif stage == "convert":
        ctx.path("capture").mkdir(parents=True, exist_ok=True)
    elif stage == "preflight":
        ctx.path("working").mkdir(parents=True, exist_ok=True)
    elif stage == "variants":
        for name in ("high", "medium", "low"):
            root = ctx.path("variants") / name
            root.mkdir(parents=True, exist_ok=True)
            (root / f"{name}.glb").write_bytes(b"glTF-mock\n")
            (root / f"{name}.obj").write_text("o mock\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
            (root / f"{name}.mtl").write_text("newmtl mock\n", encoding="utf-8")
    elif stage == "validate":
        report = ctx.workspace / "artifacts" / "validation-report.json"
        atomic_write_json(report, {"mode": "mock", "valid": True, "not_a_reconstruction": True})
        return [marker, report]
    elif stage == "package":
        return [marker, *_package(ctx, mock=True)]
    return [marker]


def _download(ctx: PipelineContext) -> list[Path]:
    input_config = ctx.config["input"]
    if ctx.secrets.input_folder_url:
        input_config["url"] = ctx.secrets.input_folder_url
    download_input(ctx.config, ctx.path("input"), ctx.log)
    dngs = _files(ctx.path("input"), (".dng",))
    limit = input_config.get("subset_limit")
    if limit and len(dngs) > int(limit):
        selected = set(dngs[: int(limit)])
        for item in dngs:
            if item not in selected and item.is_symlink():
                item.unlink()
        dngs = dngs[: int(limit)]
    if not dngs:
        raise PipelineError("no DNG images were found after input acquisition")
    inventory = ctx.meta_dir / "input-inventory.json"
    atomic_write_json(
        inventory,
        {
            "count": len(dngs),
            "images": [
                {"relative_path": str(item.relative_to(ctx.path("input"))), "size": item.stat().st_size}
                for item in dngs
            ],
        },
    )
    return [ctx.path("input"), inventory]


def _safe_relative_tiff(source: Path, root: Path) -> Path:
    relative = source.relative_to(root)
    # Preserve directory hierarchy and append the original extension marker to
    # avoid case-insensitive collisions such as image.DNG/image.dng.
    return relative.parent / f"{relative.stem}__{relative.suffix[1:]}.tif"


def _read_exif(source: Path) -> dict[str, Any]:
    if not shutil.which("exiftool"):
        return {"warning": "exiftool unavailable; source EXIF retained only in source hash/sidecar reference"}
    result = subprocess.run(
        ["exiftool", "-json", "-n", "-GPSLatitude", "-GPSLongitude", "-GPSAltitude", "-Orientation", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return {"warning": "exiftool could not read metadata"}
    try:
        values = json.loads(result.stdout)
        return values[0] if values else {}
    except json.JSONDecodeError:
        return {"warning": "exiftool returned invalid JSON"}


def _convert(ctx: PipelineContext) -> list[Path]:
    outputs: list[Path] = []
    failed: list[dict[str, str]] = []
    dngs = _files(ctx.path("input"), (".dng",))
    for number, source in enumerate(dngs, 1):
        relative = _safe_relative_tiff(source, ctx.path("input"))
        target = ctx.path("capture") / relative
        sidecar = target.with_suffix(".json")
        if converted_is_valid(source, target, sidecar, ctx.config["raw"]):
            ctx.log(f"reusing validated TIFF {number}/{len(dngs)}: {relative}")
            outputs.extend([target, sidecar])
            continue
        ctx.log(f"converting DNG {number}/{len(dngs)}: {source.name}")
        try:
            metadata = convert_dng(source, target, ctx.config["raw"])
            metadata["exif"] = _read_exif(source)
            atomic_write_json(sidecar, metadata)
            outputs.extend([target, sidecar])
        except Exception as error:  # continue to report all corrupt inputs
            failed.append({"source": str(source.relative_to(ctx.path("input"))), "error": str(error)})
            ctx.log(f"conversion failed for {source.name}: {error}", "error")
    report = ctx.meta_dir / "conversion-report.json"
    jpg_names = {item.relative_to(ctx.path("input")).with_suffix("").as_posix().casefold() for item in _files(ctx.path("input"), (".jpg", ".jpeg"))}
    unmatched = [
        str(item.relative_to(ctx.path("input")))
        for item in dngs
        if item.relative_to(ctx.path("input")).with_suffix("").as_posix().casefold() not in jpg_names
    ]
    atomic_write_json(report, {"converted": len(outputs) // 2, "failed": failed, "unmatched_dng": unmatched})
    outputs.append(report)
    if failed:
        raise PipelineError(f"{len(failed)} DNG conversion(s) failed; see {report}")
    return outputs


def _preflight(ctx: PipelineContext) -> list[Path]:
    tiffs = _files(ctx.path("capture"), (".tif", ".tiff"))
    if not tiffs:
        raise PipelineError("no converted TIFFs available")
    resources = ctx.resources
    dimension, reason = choose_working_dimension(ctx.config, len(tiffs), resources)
    working = ctx.path("working")
    working.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    dimensions: list[dict[str, Any]] = []
    for source in tiffs:
        target = working / source.relative_to(ctx.path("capture"))
        target.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            original = image.size
            if max(original) > dimension:
                image.thumbnail((dimension, dimension), Image.Resampling.LANCZOS)
                temporary = target.with_suffix(".tmp.tif")
                image.save(temporary, format="TIFF", compression="tiff_deflate")
                os.replace(temporary, target)
            elif not target.exists():
                target.symlink_to(source)
            dimensions.append({"image": str(target.relative_to(working)), "original": original, "working": Image.open(target).size})
        outputs.append(target)
    report = ctx.meta_dir / "preflight.json"
    atomic_write_json(
        report,
        {
            "image_count": len(tiffs),
            "working_max_dimension": dimension,
            "selection_reason": reason,
            "resources": resources,
            "dimensions": dimensions,
        },
    )
    return [working, report]


def _colmap(ctx: PipelineContext, args: list[str]) -> None:
    executable = ctx.config["colmap"].get("executable", "colmap")
    if not shutil.which(executable):
        raise PipelineError(f"COLMAP executable not found: {executable}; run scripts/install_kaggle.sh")
    run_command([executable, *args], cwd=ctx.workspace, log=ctx.log)


def _gpu_enabled(ctx: PipelineContext) -> bool:
    setting = ctx.config["colmap"].get("use_gpu", "auto")
    if setting is False:
        return False
    resources = detect_resources(ctx.workspace)
    if not resources["gpus"]:
        ctx.log("no NVIDIA GPU detected; attempting CPU reconstruction", "warning")
        return False
    if not resources.get("colmap_cuda_enabled"):
        ctx.log("NVIDIA GPU detected, but COLMAP was built without CUDA; using supported CPU stages", "warning")
        return False
    return True


def _append_options(args: list[str], options: dict[str, Any]) -> list[str]:
    for key, value in options.items():
        args.extend([f"--{key}", str(int(value)) if isinstance(value, bool) else str(value)])
    return args


def _feature_extract(ctx: PipelineContext) -> list[Path]:
    mode = ctx.config["capture_modes"][ctx.config["capture_mode"]]
    args = [
        "feature_extractor",
        "--database_path", str(ctx.path("database")),
        "--image_path", str(ctx.path("working")),
        "--ImageReader.camera_model", ctx.config["colmap"].get("camera_model", "OPENCV"),
        "--ImageReader.single_camera", "1" if mode.get("single_camera", True) else "0",
        "--SiftExtraction.use_gpu", "1" if _gpu_enabled(ctx) else "0",
    ]
    _append_options(args, ctx.config["colmap"].get("options", {}).get("feature_extractor", {}))
    _colmap(ctx, args)
    return [ctx.path("database")]


def _match(ctx: PipelineContext) -> list[Path]:
    mode = ctx.config["capture_modes"][ctx.config["capture_mode"]]
    matcher = mode.get("matcher", "exhaustive")
    args = [f"{matcher}_matcher", "--database_path", str(ctx.path("database")), "--SiftMatching.use_gpu", "1" if _gpu_enabled(ctx) else "0"]
    _append_options(args, mode.get("options", {}))
    _append_options(args, ctx.config["colmap"].get("options", {}).get("matcher", {}))
    _colmap(ctx, args)
    return [ctx.path("database")]


def _sparse(ctx: PipelineContext) -> list[Path]:
    output = ctx.path("sparse")
    output.mkdir(parents=True, exist_ok=True)
    args = [
        "mapper", "--database_path", str(ctx.path("database")), "--image_path", str(ctx.path("working")),
        "--output_path", str(output), "--Mapper.num_threads", str(ctx.config["colmap"].get("mapper_threads", -1)),
    ]
    _append_options(args, ctx.config["colmap"].get("options", {}).get("mapper", {}))
    _colmap(ctx, args)
    models = [item.parent for item in output.rglob("images.bin")]
    if not models:
        raise PipelineError("COLMAP mapper did not produce a sparse model")
    def count(path: Path) -> int:
        with path.open("rb") as stream:
            header = stream.read(8)
        return int(struct.unpack("<Q", header)[0]) if len(header) == 8 else 0

    statistics = [
        {
            "path": str(model),
            "cameras": count(model / "cameras.bin"),
            "registered_images": count(model / "images.bin"),
            "points3D": count(model / "points3D.bin"),
        }
        for model in models
    ]
    best = max(statistics, key=lambda item: (item["registered_images"], item["points3D"]))
    selected = Path(best["path"])
    total_images = len(_files(ctx.path("working"), (".tif", ".tiff", ".jpg", ".jpeg", ".png")))
    ratio = best["registered_images"] / total_images if total_images else 0.0
    report = ctx.meta_dir / "sparse-model.json"
    atomic_write_json(report, {"selected": best, "registered_ratio": ratio, "all_models": statistics})
    minimum_images = int(ctx.config["colmap"].get("min_registered_images", 3))
    minimum_ratio = float(ctx.config["colmap"].get("min_registered_ratio", 0.5))
    minimum_points = int(ctx.config["colmap"].get("min_sparse_points", 100))
    if best["registered_images"] < minimum_images or best["points3D"] < minimum_points or ratio < minimum_ratio:
        raise PipelineError(
            "sparse reconstruction is below validation bounds: "
            f"{best['registered_images']}/{total_images} images ({ratio:.1%}), {best['points3D']} points; "
            f"required >= {minimum_images} images, >= {minimum_ratio:.0%}, >= {minimum_points} points"
        )
    (ctx.meta_dir / "selected-sparse-model.txt").write_text(str(selected), encoding="utf-8")
    return [selected, ctx.meta_dir / "selected-sparse-model.txt", report]


def _selected_sparse(ctx: PipelineContext) -> Path:
    pointer = ctx.meta_dir / "selected-sparse-model.txt"
    if not pointer.exists():
        raise PipelineError("selected sparse model pointer is missing")
    return Path(pointer.read_text(encoding="utf-8").strip())


def _gps_references(ctx: PipelineContext) -> tuple[Path | None, int]:
    lines = []
    for sidecar in _files(ctx.path("capture"), (".json",)):
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            exif = data.get("exif", {})
            lat = float(exif.get("GPSLatitude"))
            lon = float(exif.get("GPSLongitude"))
            if not math.isfinite(lat) or not math.isfinite(lon):
                continue
            if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
                continue
            relative = sidecar.relative_to(ctx.path("capture")).with_suffix(".tif")
            try:
                altitude = float(exif.get("GPSAltitude", 0.0))
            except (TypeError, ValueError):
                altitude = 0.0
            if not math.isfinite(altitude):
                altitude = 0.0
            lines.append(f"{relative.as_posix()} {lat:.12g} {lon:.12g} {altitude:.12g}")
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    if len(lines) < 3:
        return None, len(lines)
    path = ctx.meta_dir / "gps-reference.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, len(lines)


def _aligned_model_is_finite(model: Path) -> bool:
    """Reject models that COLMAP wrote successfully with invalid camera poses."""
    images = model / "images.bin"
    if not images.is_file():
        return False
    try:
        with images.open("rb") as stream:
            count_data = stream.read(8)
            if len(count_data) != 8 or struct.unpack("<Q", count_data)[0] == 0:
                return False
            image_count = struct.unpack("<Q", count_data)[0]
            for _ in range(image_count):
                pose = stream.read(64)
                if len(pose) != 64:
                    return False
                unpacked = struct.unpack("<i7di", pose)
                quaternion = unpacked[1:5]
                translation = unpacked[5:8]
                if not all(math.isfinite(value) for value in (*quaternion, *translation)):
                    return False
                if math.sqrt(sum(value * value for value in quaternion)) < 1e-8:
                    return False
                while True:
                    character = stream.read(1)
                    if not character:
                        return False
                    if character == b"\0":
                        break
                point_count_data = stream.read(8)
                if len(point_count_data) != 8:
                    return False
                point_count = struct.unpack("<Q", point_count_data)[0]
                stream.seek(24 * point_count, os.SEEK_CUR)
    except (OSError, struct.error):
        return False
    return True


def _use_arbitrary_alignment(
    ctx: PipelineContext,
    aligned: Path,
    report: Path,
    gps_count: int,
    warning: str,
) -> list[Path]:
    ctx.log(f"{warning}; retaining arbitrary COLMAP scale", "warning")
    if aligned.exists():
        shutil.rmtree(aligned)
    shutil.copytree(_selected_sparse(ctx), aligned)
    atomic_write_json(
        report,
        {"scale": "arbitrary", "gps_reference_count": gps_count, "warning": warning},
    )
    return [aligned, report]


def _align(ctx: PipelineContext) -> list[Path]:
    references, count = _gps_references(ctx)
    report = ctx.meta_dir / "coordinate-system.json"
    aligned = ctx.path("aligned")
    if not references:
        return _use_arbitrary_alignment(ctx, aligned, report, count, f"only {count} valid GPS-tagged images")
    if aligned.exists():
        shutil.rmtree(aligned)
    aligned.mkdir(parents=True, exist_ok=True)
    try:
        _colmap(
            ctx,
            [
                "model_aligner", "--input_path", str(_selected_sparse(ctx)), "--output_path", str(aligned),
                "--ref_images_path", str(references), "--ref_is_gps", "1", "--alignment_type", "enu",
                "--alignment_max_error", "5.0",
            ],
        )
    except CommandError:
        return _use_arbitrary_alignment(ctx, aligned, report, count, "GPS model alignment failed")
    if not _aligned_model_is_finite(aligned):
        return _use_arbitrary_alignment(ctx, aligned, report, count, "GPS alignment produced invalid camera poses")
    atomic_write_json(
        report,
        {
            "scale": "approximate_metric",
            "coordinate_system": "local East-North-Up derived from WGS84 EXIF GPS/altitude",
            "gps_reference_count": count,
            "expected_accuracy": "consumer GNSS/altitude; inspect against surveyed control before measurement",
            "export": "geometry is centered later; original ENU-to-centered transform is written by Blender export",
        },
    )
    return [aligned, report]


def _ply_vertex_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        with path.open("rb") as stream:
            for _ in range(1024):
                line = stream.readline(4096)
                if not line:
                    break
                if line.startswith(b"element vertex "):
                    return int(line.split()[2])
                if line.strip() == b"end_header":
                    break
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def _dense(ctx: PipelineContext) -> list[Path]:
    dense = ctx.path("dense")
    _colmap(ctx, ["image_undistorter", "--image_path", str(ctx.path("working")), "--input_path", str(ctx.path("aligned")), "--output_path", str(dense), "--output_type", "COLMAP", "--max_image_size", str(ctx.config["colmap"].get("dense_max_image_size", 3200))])
    if not _gpu_enabled(ctx):
        raise PipelineError(
            "COLMAP dense PatchMatch requires a CUDA-enabled build; this COLMAP cannot complete dense reconstruction on CPU"
        )
    _colmap(ctx, ["patch_match_stereo", "--workspace_path", str(dense), "--workspace_format", "COLMAP", "--PatchMatchStereo.gpu_index", "-1" if _gpu_enabled(ctx) else "-1"])
    depth_maps = list((dense / "stereo" / "depth_maps").glob("*.geometric.bin"))
    minimum_depth_maps = int(ctx.config["colmap"].get("min_dense_images", 3))
    if len(depth_maps) < minimum_depth_maps:
        raise PipelineError(
            f"dense stereo produced {len(depth_maps)} geometric depth maps; required >= {minimum_depth_maps}"
        )
    fused = dense / "fused.ply"
    _colmap(ctx, ["stereo_fusion", "--workspace_path", str(dense), "--workspace_format", "COLMAP", "--input_type", "geometric", "--output_path", str(fused)])
    fused_points = _ply_vertex_count(fused)
    minimum_points = int(ctx.config["colmap"].get("min_dense_points", 100))
    if fused_points < minimum_points:
        raise PipelineError(f"dense fusion produced {fused_points} points; required >= {minimum_points}")
    return [dense, fused]


def _mesh(ctx: PipelineContext) -> list[Path]:
    mesh = ctx.path("mesh")
    mesh.parent.mkdir(parents=True, exist_ok=True)
    fused_points = _ply_vertex_count(ctx.path("dense") / "fused.ply")
    if fused_points <= 0:
        raise PipelineError("cannot mesh an empty or invalid dense point cloud")
    _colmap(ctx, ["poisson_mesher", "--input_path", str(ctx.path("dense") / "fused.ply"), "--output_path", str(mesh), "--PoissonMeshing.trim", str(ctx.config["mesh"].get("poisson_trim", 7))])
    if _ply_vertex_count(mesh) <= 0:
        raise PipelineError("mesh output is missing or contains no vertices")
    return [mesh]


def _texture(ctx: PipelineContext) -> list[Path]:
    if not shutil.which("texrecon"):
        raise PipelineError("texrecon is required for UV/texture generation; run scripts/install_kaggle.sh")
    # NVM stores image names as relative paths. Keeping it beside the working
    # image hierarchy lets texrecon resolve those paths without rewriting it.
    nvm = ctx.path("working") / "scene.nvm"
    _colmap(ctx, ["model_converter", "--input_path", str(ctx.path("aligned")), "--output_path", str(nvm), "--output_type", "NVM"])
    output_prefix = ctx.path("textured").with_suffix("")
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "texrecon", "--data_term", ctx.config["mesh"].get("texture_data_term", "area"),
            "--outlier_removal", ctx.config["mesh"].get("texture_outlier_removal", "gauss_clamping"),
            str(nvm), str(ctx.path("mesh")), str(output_prefix),
        ],
        cwd=ctx.path("working"),
        log=ctx.log,
    )
    candidates = _files(output_prefix.parent, (".obj",))
    if not candidates:
        raise PipelineError("texrecon did not create a textured OBJ")
    selected = candidates[0]
    if selected != ctx.path("textured"):
        shutil.copy2(selected, ctx.path("textured"))
    return [output_prefix.parent]


def _variants(ctx: PipelineContext) -> list[Path]:
    blender = shutil.which("blender")
    if not blender:
        raise PipelineError("Blender is required for profile generation and export")
    script = Path(__file__).resolve().parents[2] / "scripts" / "blender_variants.py"
    config_file = ctx.meta_dir / "blender-profiles.json"
    atomic_write_json(
        config_file,
        {
            "profiles": ctx.config["quality_profiles"],
            "center_at_origin": ctx.config["quality"].get("center_at_origin", True),
            "coordinate_report": str(ctx.meta_dir / "coordinate-system.json"),
        },
    )
    run_command([blender, "--background", "--python", str(script), "--", str(ctx.path("textured")), str(ctx.path("variants")), str(config_file)], log=ctx.log)
    required = []
    for name in ("high", "medium", "low"):
        required.extend([ctx.path("variants") / name / f"{name}.glb", ctx.path("variants") / name / f"{name}.obj", ctx.path("variants") / name / f"{name}.mtl"])
    missing = [path for path in required if not path.exists()]
    if missing:
        raise PipelineError(f"Blender export is incomplete: {missing}")
    return [ctx.path("variants")]


def _validate(ctx: PipelineContext) -> list[Path]:
    blender = shutil.which("blender")
    if not blender:
        raise PipelineError("Blender is required for headless validation")
    script = Path(__file__).resolve().parents[2] / "scripts" / "blender_validate.py"
    report = ctx.workspace / "artifacts" / "validation-report.json"
    profiles = ctx.meta_dir / "blender-profiles.json"
    run_command([blender, "--background", "--python", str(script), "--", str(ctx.path("variants")), str(profiles), str(report)], log=ctx.log)
    data = json.loads(report.read_text(encoding="utf-8"))
    if not data.get("valid"):
        raise PipelineError(f"one or more profiles failed validation; see {report}")
    return [report]


def _package(ctx: PipelineContext, mock: bool = False) -> list[Path]:
    packages = ctx.path("packages")
    packages.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for name in ("high", "medium", "low"):
        source = ctx.path("variants") / name
        archive = Path(shutil.make_archive(str(packages / f"dji-reconstruction-{name}"), "zip", source))
        outputs.append(archive)
    manifest_path = ctx.path("manifest")
    validation = ctx.workspace / "artifacts" / "validation-report.json"
    coordinate = ctx.meta_dir / "coordinate-system.json"
    artifacts = [item for item in _files(ctx.path("variants")) + outputs if item.is_file()]
    manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "execution_mode": ctx.config.get("execution_mode", "real"),
        "mock_warning": "smoke-test outputs are not reconstructions" if mock else None,
        "effective_configuration": redact(ctx.config),
        "tool_versions": ctx.resources.get("tools", {}),
        "resource_estimates": json.loads((ctx.meta_dir / "preflight.json").read_text(encoding="utf-8")) if (ctx.meta_dir / "preflight.json").exists() else {"status": "not_available"},
        "stage_status": redact(ctx.state.get("stages", {})),
        "warnings": [event for event in ctx.state.get("events", []) if event.get("level") == "warning"],
        "coordinate_scale": json.loads(coordinate.read_text(encoding="utf-8")) if coordinate.exists() else {"status": "not_available"},
        "validation": json.loads(validation.read_text(encoding="utf-8")) if validation.exists() else {"status": "not_run"},
        "files": [
            {"path": str(item.relative_to(ctx.workspace)), "size": item.stat().st_size, "sha256": sha256_file(item)}
            for item in artifacts
        ],
    }
    atomic_write_json(manifest_path, manifest)
    outputs.append(manifest_path)
    return outputs


def _upload(ctx: PipelineContext, confirmed: bool) -> list[Path]:
    backend = ctx.config["upload"].get("backend", "none")
    receipt = ctx.meta_dir / "upload-receipt.json"
    if backend == "none":
        atomic_write_json(receipt, {"status": "not_requested", "timestamp": utc_now()})
        return [receipt]
    if ctx.config["upload"].get("require_confirmation", True) and not confirmed:
        atomic_write_json(receipt, {"status": "awaiting_user_confirmation", "timestamp": utc_now()})
        ctx.log("artifacts are ready; upload requires explicit confirmation", "warning")
        return [receipt]
    with EphemeralRclone() as session:
        if backend == "mega":
            if not ctx.secrets.mega_email or not ctx.secrets.mega_password:
                raise PipelineError("MEGA_EMAIL and MEGA_PASSWORD are required at runtime")
            remote = session.add_mega(ctx.secrets.mega_email, ctx.secrets.mega_password)
        elif backend == "gdrive":
            if not ctx.secrets.drive_token:
                raise PipelineError("an ephemeral Google Drive rclone OAuth token is required")
            remote = session.add_drive(ctx.secrets.drive_token)
        else:
            raise PipelineError(f"unsupported upload backend {backend!r}")
        session.validate(remote, ctx.log)
        session.copy_to(ctx.path("packages").parent, remote, ctx.config["upload"].get("destination", "dji-recon"), ctx.log)
    atomic_write_json(receipt, {"status": "uploaded", "backend": backend, "timestamp": utc_now()})
    return [receipt]


REAL_RUNNERS: dict[str, Callable[[PipelineContext], list[Path]]] = {
    "download": _download,
    "convert": _convert,
    "preflight": _preflight,
    "feature_extract": _feature_extract,
    "match": _match,
    "sparse": _sparse,
    "align": _align,
    "dense": _dense,
    "mesh": _mesh,
    "texture": _texture,
    "variants": _variants,
    "validate": _validate,
    "package": _package,
}


def run_pipeline(
    config: dict[str, Any],
    *,
    secrets: RuntimeSecrets | None = None,
    from_stage: str | None = None,
    to_stage: str | None = None,
    force_stages: set[str] | None = None,
    confirm_upload: bool = False,
    event_callback: Callable[[PipelineEvent], None] | None = None,
) -> PipelineContext:
    ctx = PipelineContext(config, secrets or RuntimeSecrets.from_environment(), event_callback)
    force_stages = force_stages or set()
    for name in [item for item in (from_stage, to_stage, *force_stages) if item]:
        if name not in STAGES:
            raise PipelineError(f"unknown stage {name!r}; choose from {', '.join(STAGES)}")
    start = STAGES.index(from_stage) if from_stage else 0
    stop = STAGES.index(to_stage) if to_stage else len(STAGES) - 1
    if start > stop:
        raise PipelineError("--from-stage must not come after --to-stage")
    mode = config.get("execution_mode", "real")
    ctx.state["effective_configuration"] = redact(config)
    ctx.state["execution_mode"] = mode
    ctx.save()
    for index, stage in enumerate(STAGES):
        if index < start or index > stop:
            continue
        ctx.current_stage = stage
        stage_fp = _stage_fingerprint(ctx, stage)
        old = ctx.state["stages"].get(stage, {})
        if stage not in force_stages and old.get("status") in {"complete", "awaiting_confirmation"} and old.get("fingerprint") == stage_fp and _outputs_exist(old.get("outputs", [])):
            ctx.log("reusing validated stage outputs")
            continue
        started_clock = time.monotonic()
        record = {
            "status": "running",
            "started_at": utc_now(),
            "input_fingerprint": stage_fp,
            "fingerprint": stage_fp,
            "tool_versions": ctx.resources.get("tools", {}),
            "warnings": [],
        }
        ctx.state["stages"][stage] = record
        ctx.save()
        ctx.log("stage started")
        try:
            if mode == "mock":
                outputs = _mock_stage(ctx, stage)
            elif stage == "upload":
                outputs = _upload(ctx, confirm_upload)
            else:
                outputs = REAL_RUNNERS[stage](ctx)
            outputs = [path.resolve() for path in outputs]
            record.update(
                {
                    "status": "awaiting_confirmation" if stage == "upload" and json.loads(outputs[0].read_text()).get("status") == "awaiting_user_confirmation" else "complete",
                    "ended_at": utc_now(),
                    "duration_seconds": round(time.monotonic() - started_clock, 3),
                    "outputs": [str(path) for path in outputs],
                    "output_identities": _record_outputs(outputs),
                }
            )
            ctx.log(f"stage {record['status']} in {record['duration_seconds']:.3f}s")
            ctx.save()
        except Exception as error:
            record.update({"status": "failed", "ended_at": utc_now(), "duration_seconds": round(time.monotonic() - started_clock, 3), "error": redact_text(str(error), ctx.secrets.values())})
            ctx.log(f"stage failed: {error}", "error")
            ctx.log(traceback.format_exc(), "debug")
            ctx.save()
            raise
    ctx.current_stage = None
    ctx.state["ended_at"] = utc_now()
    ctx.save()
    return ctx
