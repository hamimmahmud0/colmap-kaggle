import time
from pathlib import Path

from fastapi.testclient import TestClient

from dji_recon.web import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_setup_closes_and_mock_run_reports_progress(tmp_path):
    config_text = (ROOT / "configs" / "default.yaml").read_text()
    config_text = config_text.replace("workspace: workspace", f"workspace: {tmp_path / 'workspace'}")
    config_text = config_text.replace("execution_mode: real", "execution_mode: mock")
    config_text += f"\n_config_root: {ROOT / 'configs'}\n"
    config_path = tmp_path / "web.yaml"
    config_path.write_text(config_text, encoding="utf-8")
    client = TestClient(create_app(config_path))
    assert client.get("/setup").status_code == 200
    response = client.post("/setup", data={"password": "long-test-password", "confirm": "long-test-password"})
    assert response.status_code == 200
    assert client.get("/setup").status_code == 404
    response = client.post("/login", data={"password": "long-test-password"})
    assert response.status_code == 200
    response = client.post(
        "/api/run",
        data={"input_type": "local", "input_path": "unused", "capture_mode": "mixed", "medium_vram_gb": "8", "upload_backend": "none", "output_destination": "unused"},
    )
    assert response.status_code == 202
    for _ in range(100):
        status = client.get("/api/status").json()
        if status["status"] in {"complete", "failed"}:
            break
        time.sleep(0.02)
    assert status["status"] == "complete"
    assert status["events"]
