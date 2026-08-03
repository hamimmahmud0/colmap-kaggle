# DJI reconstruction pipeline

This repository provides a Kaggle-first, resumable pipeline that converts DJI
Mini 2 DNG captures to TIFF, reconstructs a COLMAP model, textures it, and
exports bounded high, medium, and low variants as both GLB and portable
OBJ/MTL packages. The CLI is the implementation; the notebook and Web UI call
that same pipeline.

Kaggle is the required end-to-end environment. Colab is best effort. The
included remote workflow is only a CLI/orchestration smoke test unless the
remote is explicitly provisioned with every reconstruction dependency.

## Kaggle: exact setup

1. Create a Kaggle notebook, enable Internet, and select a GPU accelerator.
   The target acceptance runtime is two T4 GPUs and about 30 GB RAM.
2. Add this Git repository as notebook content or clone it into
   `/kaggle/working/colmap-kaggle`, then change to the repository root.
3. Open and run `notebooks/dji_reconstruction.ipynb` from top to bottom.
   The installer pins a CUDA-enabled COLMAP 3.11.1 source build for T4,
   Ubuntu 22.04 Blender and ExifTool,
   MVS-Texturing source revision, Python packages, and FRP client. Python
   dependencies are isolated in `/kaggle/working/dji-recon-venv` so they do not
   downgrade or conflict with Kaggle's preinstalled notebook packages.
4. The final cell prints a public `/setup` URL. Open it and create a password
   of at least ten characters. No bootstrap token is used. This is an accepted
   first-run security tradeoff: anyone who reaches the page first can claim
   the runtime. The route returns 404 immediately after password creation.
5. Sign in, enter the private input location and runtime credentials, select
   `aerial_grid`, `oblique_orbit`, or `mixed`, and start the pipeline.
6. Keep the notebook cell running. The UI shows stage state, elapsed log
   events, warnings, and sanitized failure detail. It does not provide a shell,
   arbitrary file browser, environment view, raw config view, or retry button.
7. After successful validation and packaging, explicitly confirm upload in the
   UI. Without that click, no remote upload occurs.

The primary tunnel uses the allocation, 20-second heartbeat, and release API
at `http://163.61.236.112:7001`. If allocation or `frpc` fails, the launcher
tries a Cloudflare quick tunnel. The VS Code-like UI uses only functional
regions: settings, stages, logs, connection state, and upload confirmation.

## Inputs and secrets

The UI supports a private-key MEGA folder, a runtime directory, or an existing
rclone source. Never add a private URL to YAML or the notebook. For CLI-only
runs, supported environment variables are `INPUT_FOLDER_URL`, `MEGA_EMAIL`,
`MEGA_PASSWORD`, `GOOGLE_DRIVE_TOKEN`, `OUTPUT_REMOTE`, `RCLONE_CONFIG`, and
`TRANSPORT_HOST`.

MEGA folder URLs are sent to the interactive `mega-cmd` process over stdin so
the folder key is not placed in its process arguments. Install MEGAcmd in the
runtime if using a public/private-key folder; the pipeline refuses that input
mode when only an argv-based client is available. Account-based MEGA uploads
use an rclone config inside a mode-0600 temporary directory. The password is
obscured through rclone stdin.

For Google Drive:

1. Install rclone on a trusted local computer with a browser.
2. Run `rclone authorize drive` locally and complete Google OAuth.
3. Paste the resulting JSON token into the authenticated UI.
4. The runtime creates and validates a mode-0600 rclone config and deletes the
   temporary directory at the end of the upload.

Passwords, private URL fragments, OAuth tokens, and rclone config contents are
redacted from public logs and excluded from state/manifests. Runtime secrets
are held only in process memory except for short-lived permission-restricted
tool files, and are not restored after runtime restart.

## CLI

Install the Python project, edit a copy of `configs/default.yaml` without
putting secrets in it, and run:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/dji-recon --config configs/default.yaml --print-resources
.venv/bin/dji-recon --config configs/default.yaml
```

Resume or isolate stages with:

```bash
dji-recon --config run-config.yaml --from-stage dense
dji-recon --config run-config.yaml --to-stage sparse
dji-recon --config run-config.yaml --force-stage match --from-stage match
dji-recon --config run-config.yaml --from-stage upload --confirm-upload
```

A stage is reused only when its fingerprint matches and required outputs still
exist. A fingerprint covers effective configuration, preceding-stage identity,
and tool-dependent output state. Failed stages preserve previous valid stages.
Stage state and sanitized logs live in `workspace/.pipeline` and
`workspace/logs`; neither is tracked.

`execution_mode: mock` is only for orchestration tests. Mock artifacts are
marked as non-reconstructions and must never be used to claim end-to-end
acceptance.

## Processing behavior

- DNG is decoded by pinned rawpy/LibRaw using AHD demosaicing, neutral unit
  white balance, linear gamma, no automatic brightening, clipped highlights,
  sRGB output, and 16-bit Deflate TIFF by default. Effective settings and the
  source SHA-256 are stored beside each TIFF. Valid conversions are reused.
- JPGs are ignored. Missing JPG pairs are reported but do not fail conversion.
  Corrupt DNGs are all reported before the conversion stage fails.
- Preflight detects GPU inventory, RAM, disk, tool versions, and predicts a
  conservative working dimension. Downscaling affects only working images;
  original TIFF conversions remain intact.
- Capture presets select sequential matching for a grid and exhaustive
  matching for orbit/mixed data. Every COLMAP section accepts advanced option
  overrides in YAML.
- When at least three GPS-tagged images are available, COLMAP's robust model
  aligner creates a local East-North-Up model. It is approximate consumer-GNSS
  scale, not survey control. Otherwise the model is explicitly arbitrary-scale.
- Blender centers geometry for stable viewing and writes the translation in
  `profile-metrics.json`. All variants share the aligned orientation and scale.
- `docs/quality-profiles.md` defines enforceable triangle/texture limits and
  clearly separates projected resource values from measurements.

## Outputs and Blender import

Successful packages are under `workspace/artifacts/packages`, with the
machine-readable SHA-256 manifest at `workspace/artifacts/manifest.json`.
Each profile ZIP contains a self-contained GLB and OBJ/MTL/textures with
relative paths.

In Blender, prefer **File → Import → glTF 2.0** and choose the GLB. For OBJ,
extract the complete ZIP first and keep the MTL and texture directory beside
the OBJ before choosing **File → Import → Wavefront (.obj)**. Units are meters
only when the coordinate report says `approximate_metric`; otherwise they are
arbitrary. Do not use the model for precise measurement without surveyed
control.

## Remote read-only smoke workflow

Local source is authoritative. Commit and push locally, then use the remote's
read-only GitHub key:

```bash
git clone git@github.com:hamimmahmud0/colmap-kaggle.git /kaggle/working/colmap-kaggle
cd /kaggle/working/colmap-kaggle
git pull --ff-only
bash scripts/remote_smoke.sh
```

Do not copy local credentials or the legacy prompt to the remote. The smoke
script installs the Python project in the remote environment, runs pytest, and
exercises every stage in explicit mock mode. It deletes its temporary runtime
on exit.

## Troubleshooting

- **`COLMAP executable not found`**: run `scripts/install_kaggle.sh` and inspect
  its version footer.
- **CUDA requested but unavailable**: verify `nvidia-smi`, the Kaggle GPU
  setting, and the effective COLMAP build. Feature extraction/matching use CPU
  fallback. COLMAP dense PatchMatch may require a CUDA-enabled build; this is
  reported as a dense-stage failure rather than hidden.
- **Disk preflight warning/failure**: use a representative subset, lower
  `resources.max_working_dimension`, or use a runtime with more free storage.
  Do not delete TIFF originals during a run.
- **Sparse model missing**: confirm image overlap and correct capture preset,
  then inspect feature/matching logs. Retry through CLI/notebook using
  `--force-stage`; the Web UI intentionally has no retry button.
- **MEGA input refused**: install `mega-cmd`. The pipeline will not fall back to
  putting a key-bearing folder URL in process arguments.
- **Profile validation failed**: inspect `validation-report.json`. A failed
  profile is not relabeled or uploaded as successful.
- **Tunnel failure**: check broker health and that `frpc` matches the pinned
  installer. The launcher reports the Cloudflare fallback result.

## Acceptance boundary

Tests and remote mock runs validate configuration, redaction, resumability,
authentication, setup-route closure, orchestration, and manifests. Only a real
run from DNG through Blender validation on Kaggle can satisfy end-to-end
acceptance. Private dataset URLs, credentials, source images, generated TIFFs,
COLMAP databases, and meshes are intentionally ignored by Git.
