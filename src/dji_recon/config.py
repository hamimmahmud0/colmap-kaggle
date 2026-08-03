from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    pass


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream) or {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    return loaded


def load_config(path: Path) -> dict[str, Any]:
    config = load_yaml(path)
    root = Path(config.pop("_config_root", path.parent)).resolve()
    for key, default_name in (
        ("capture_modes_file", "capture_modes.yaml"),
        ("quality_profiles_file", "quality_profiles.yaml"),
    ):
        referenced = Path(config.get(key, default_name))
        if not referenced.is_absolute():
            referenced = root / referenced
        if referenced.exists():
            config[key.removesuffix("_file")] = load_yaml(referenced)
    apply_environment(config)
    validate_config(config)
    config["_config_path"] = str(path.resolve())
    return config


def apply_environment(config: dict[str, Any]) -> None:
    mappings = {
        "INPUT_FOLDER_URL": ("input", "url"),
        "OUTPUT_REMOTE": ("upload", "remote"),
        "TRANSPORT_HOST": ("web", "transport_host"),
    }
    for variable, path in mappings.items():
        value = os.environ.get(variable)
        if value:
            config.setdefault(path[0], {})[path[1]] = value


def validate_config(config: dict[str, Any]) -> None:
    required = ["workspace", "capture_mode", "input", "raw", "colmap", "quality"]
    missing = [key for key in required if key not in config]
    if missing:
        raise ConfigError(f"missing required configuration: {', '.join(missing)}")
    modes = config.get("capture_modes", {})
    if config["capture_mode"] not in modes:
        raise ConfigError(f"unknown capture_mode {config['capture_mode']!r}")
    profiles = config.get("quality_profiles", {})
    for name in ("high", "medium", "low"):
        if name not in profiles:
            raise ConfigError(f"quality profile {name!r} is missing")
        profile = profiles[name]
        for field in (
            "max_triangles",
            "max_texture_atlases",
            "max_texture_dimension",
            "max_texture_megapixels",
        ):
            if int(profile.get(field, 0)) <= 0:
                raise ConfigError(f"quality profile {name}.{field} must be positive")
