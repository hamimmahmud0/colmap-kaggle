from pathlib import Path

from dji_recon.config import load_config
import json
import struct

from dji_recon.pipeline import (
    STAGES,
    PipelineContext,
    _aligned_model_is_finite,
    _gps_references,
    _ply_vertex_count,
    run_pipeline,
)


ROOT = Path(__file__).resolve().parents[1]


def mock_config(tmp_path):
    config = load_config(ROOT / "configs" / "default.yaml")
    config["workspace"] = str(tmp_path / "workspace")
    config["execution_mode"] = "mock"
    return config


def test_mock_pipeline_runs_and_resumes(tmp_path):
    config = mock_config(tmp_path)
    first = run_pipeline(config)
    assert all(first.state["stages"][stage]["status"] == "complete" for stage in STAGES)
    manifest = Path(config["workspace"]) / "artifacts" / "manifest.json"
    assert manifest.exists()
    before = first.state["stages"]["convert"]["started_at"]
    second = run_pipeline(config)
    assert second.state["stages"]["convert"]["started_at"] == before


def test_force_stage_reruns_only_requested_range(tmp_path):
    config = mock_config(tmp_path)
    run_pipeline(config)
    context = run_pipeline(config, from_stage="validate", to_stage="package", force_stages={"validate"})
    assert context.state["stages"]["validate"]["status"] == "complete"


def test_gps_references_reject_empty_undefined_and_nonfinite_values(tmp_path):
    config = mock_config(tmp_path)
    context = PipelineContext(config)
    capture = context.path("capture")
    capture.mkdir(parents=True)
    samples = [
        {"GPSLatitude": "", "GPSLongitude": ""},
        {"GPSLatitude": "undef", "GPSLongitude": "undef"},
        {"GPSLatitude": "nan", "GPSLongitude": 90},
        {"GPSLatitude": 23.5, "GPSLongitude": 90.5, "GPSAltitude": "undef"},
    ]
    for index, exif in enumerate(samples):
        (capture / f"image-{index}.json").write_text(json.dumps({"exif": exif}))
    path, count = _gps_references(context)
    assert path is None
    assert count == 1


def test_binary_model_and_ply_validation(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    with (model / "images.bin").open("wb") as stream:
        stream.write(struct.pack("<Q", 1))
        stream.write(struct.pack("<i7di", 1, 1, 0, 0, 0, 1, 2, 3, 1))
        stream.write(b"image.tif\0")
        stream.write(struct.pack("<Q", 0))
    assert _aligned_model_is_finite(model)
    ply = tmp_path / "points.ply"
    ply.write_bytes(b"ply\nformat binary_little_endian 1.0\nelement vertex 42\nend_header\n")
    assert _ply_vertex_count(ply) == 42
    with (model / "images.bin").open("r+b") as stream:
        stream.seek(8)
        stream.write(struct.pack("<i7di", 1, 0, 0, 0, 0, float("nan"), 2, 3, 1))
    assert not _aligned_model_is_finite(model)
