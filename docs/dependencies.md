# Dependencies, versions, purpose, and license

The Kaggle installer is tested against Ubuntu 22.04 and records the effective
versions at runtime.

| Dependency | Pinned/tested version | Purpose | License |
|---|---|---|---|
| COLMAP | source `3.11.1` commit `682ea9ac4020a143047758739259b3ff04dabe8d` with CUDA compute 7.5; Ubuntu `3.7-2` CPU fallback | SfM, dense stereo, fusion, meshing and GPS alignment | BSD-3-Clause |
| Blender | Ubuntu `3.0.1+dfsg-7` | decimation, GLB/OBJ export and independent headless validation | GPL-2.0-or-later |
| MVS-Texturing / texrecon | commit `f3374298ac959cb5afe47a14e4d35d2ac7fbdbb1` | UV/material and image-based texture generation | BSD-3-Clause |
| ExifTool | Ubuntu `12.40+dfsg-1` | GPS, altitude and orientation extraction | Artistic-1.0 or GPL-1.0-or-later |
| rclone | effective Ubuntu package recorded at install | ephemeral MEGA and Google Drive upload | MIT |
| frp client | `0.64.0`, archive SHA-256 pinned | primary public Web UI transport | Apache-2.0 |
| MEGAcmd | `2.5.2-1.1`, package SHA-256 pinned | key-bearing MEGA folder download over interactive stdin | MEGA terms / SDK license |
| rawpy | `0.24.0` | deterministic LibRaw DNG decoding/demosaicing | MIT; LibRaw LGPL-2.1/CDDL |
| tifffile | `2025.2.18` | validated 16-bit TIFF output | BSD-3-Clause |
| FastAPI / Uvicorn | `0.115.8` / `0.34.0` | runtime Web UI and API | MIT / BSD-3-Clause |
| wrapt | `1.17.2` | satisfies Kaggle's injected Python startup instrumentation in the isolated environment | BSD-2-Clause |

See `pyproject.toml` for all pinned Python packages. Transitive Debian and
source-build dependencies retain their upstream licenses. The installer does
not imply that the Ubuntu COLMAP build has CUDA; preflight detects effective
GPU support and logs CPU fallback explicitly.
