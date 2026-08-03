from pathlib import Path

from dji_recon.config import load_config
from dji_recon.pipeline import STAGES, run_pipeline


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
