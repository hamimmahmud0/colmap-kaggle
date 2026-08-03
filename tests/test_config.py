from pathlib import Path

import pytest

from dji_recon.config import ConfigError, load_config


ROOT = Path(__file__).resolve().parents[1]


def test_default_config_has_all_modes_and_profiles():
    config = load_config(ROOT / "configs" / "default.yaml")
    assert set(config["capture_modes"]) == {"aerial_grid", "oblique_orbit", "mixed"}
    assert set(config["quality_profiles"]) == {"high", "medium", "low"}
    assert config["quality_profiles"]["low"]["estimated_view_vram_gb"] < 4


def test_unknown_capture_mode_is_rejected(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        (ROOT / "configs" / "default.yaml").read_text().replace("capture_mode: mixed", "capture_mode: unknown")
        + f"\n_config_root: {ROOT / 'configs'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(path)
