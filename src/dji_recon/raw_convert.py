from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import rawpy
import tifffile

from .util import file_identity, sha256_file


DEMOSAIC = {
    "AHD": rawpy.DemosaicAlgorithm.AHD,
    "DCB": rawpy.DemosaicAlgorithm.DCB,
    "LINEAR": rawpy.DemosaicAlgorithm.LINEAR,
}
COLOR = {
    "sRGB": rawpy.ColorSpace.sRGB,
    "Adobe": rawpy.ColorSpace.Adobe,
    "ProPhoto": rawpy.ColorSpace.ProPhoto,
    "raw": rawpy.ColorSpace.raw,
}
HIGHLIGHT = {
    "clip": rawpy.HighlightMode.Clip,
    "ignore": rawpy.HighlightMode.Ignore,
    "blend": rawpy.HighlightMode.Blend,
    "reconstruct": rawpy.HighlightMode.ReconstructDefault,
}


def converted_is_valid(source: Path, target: Path, sidecar: Path, settings: dict[str, Any]) -> bool:
    if not target.is_file() or not sidecar.is_file():
        return False
    try:
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        if metadata.get("source", {}).get("sha256") != sha256_file(source):
            return False
        if metadata.get("settings") != settings:
            return False
        with tifffile.TiffFile(target) as tif:
            page = tif.pages[0]
            return page.bitspersample == 16 and page.imagewidth > 0 and page.imagelength > 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False


def convert_dng(source: Path, target: Path, settings: dict[str, Any]) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    gamma = tuple(float(value) for value in settings.get("gamma", [1.0, 1.0]))
    white_balance = settings.get("white_balance", "neutral")
    kwargs: dict[str, Any] = {
        "demosaic_algorithm": DEMOSAIC[settings.get("demosaic", "AHD")],
        "output_color": COLOR[settings.get("output_color", "sRGB")],
        "gamma": gamma,
        "no_auto_bright": not bool(settings.get("auto_bright", False)),
        "bright": float(settings.get("exposure_shift", 1.0)),
        "highlight_mode": HIGHLIGHT[settings.get("highlight_mode", "clip")],
        "output_bps": 16,
    }
    if white_balance == "camera":
        kwargs["use_camera_wb"] = True
        kwargs["use_auto_wb"] = False
    elif white_balance == "auto":
        kwargs["use_camera_wb"] = False
        kwargs["use_auto_wb"] = True
    else:
        kwargs["use_camera_wb"] = False
        kwargs["use_auto_wb"] = False
        kwargs["user_wb"] = [1.0, 1.0, 1.0, 1.0]
    with rawpy.imread(str(source)) as raw:
        array = raw.postprocess(**kwargs)
        raw_metadata = {
            "camera_whitebalance": list(raw.camera_whitebalance),
            "daylight_whitebalance": list(raw.daylight_whitebalance),
            "raw_height": raw.sizes.raw_height,
            "raw_width": raw.sizes.raw_width,
        }
    handle, temporary = tempfile.mkstemp(suffix=".tif", dir=target.parent)
    os.close(handle)
    try:
        tifffile.imwrite(
            temporary,
            np.asarray(array, dtype=np.uint16),
            photometric="rgb",
            compression=settings.get("tiff_compression", "deflate"),
            metadata=None,
        )
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {
        "source": file_identity(source),
        "settings": settings,
        "converter": {
            "rawpy": rawpy.__version__,
            "libraw": ".".join(str(item) for item in rawpy.libraw_version),
            "tifffile": tifffile.__version__,
        },
        "raw_metadata": raw_metadata,
    }
