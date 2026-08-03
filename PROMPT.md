# Task: Reproducible DJI Photogrammetry Pipeline

## Goal

Build a reproducible pipeline that converts DJI Mini 2 photographs into three
textured 3D mesh variants suitable for importing into Blender.

The required production and end-to-end validation environment is Kaggle
Notebooks. Assume that the intended Kaggle session has:

- Two NVIDIA T4 GPUs
- 30 GB of system RAM
- Up to 500 or more 12-megapixel photographs

GPU availability must still be detected at runtime. When no compatible GPU is
available, attempt the complete CPU pipeline wherever the selected tools
support CPU execution. Warn clearly that it may be substantially slower.

Google Colab support is best effort. Prefer one portable notebook that can run
on both Kaggle and Colab, but only Kaggle must pass the complete end-to-end
acceptance test. The provided remote development environment is for CLI
development and smoke testing only; a full reconstruction is not required
there.

## Scope and priorities

Implement in this order:

1. Reusable, configuration-driven command-line pipeline
2. Kaggle notebook and Web UI integration
3. Resumability, validation, and artifact packaging
4. MEGA and Google Drive/rclone upload paths
5. Best-effort Colab portability

The notebook and Web UI must call the reusable pipeline rather than contain a
separate implementation.

## Source data

The input folder contains `.DNG` photographs and may contain matching `.JPG`
versions with the same basename.

Requirements:

- Use DNG files as the reconstruction source.
- Ignore matching JPG files for reconstruction. Never delete or modify source
  files.
- Convert DNG files into valid 16-bit TIFF files under `dji_capture/`.
- Use a pinned RAW converter and record its version and effective settings.
- Use neutral, deterministic RAW development suitable for feature matching;
  matching the appearance of DJI's processed JPG files is not required.
- Make demosaicing, white balance, output color space, gamma, exposure
  normalization, highlight handling, orientation, and TIFF compression
  explicit and configurable.
- Preserve orientation and useful EXIF/GPS metadata where possible. If the
  TIFF writer cannot preserve a field, retain it in a documented sidecar or
  manifest.
- Use lossless TIFF compression when supported by the selected tools.
- Make preprocessing idempotent. Validate an existing conversion before
  reusing it, and do not repeat a valid conversion unless explicitly forced.
- Process DNG files even when they have no matching JPG. Report unmatched,
  failed, and corrupt inputs without treating an unmatched JPG pair as a
  conversion failure.
- Handle filenames safely, including spaces, Unicode, and duplicate basenames
  in different directories.

Inputs may be supplied through a MEGA folder or Google Drive. Do not commit
private folder URLs or URL fragments containing decryption keys.

## Capture modes and matching

Provide a configurable capture-mode preset with these values:

- `aerial_grid`: primarily nadir/grid imagery
- `oblique_orbit`: primarily oblique or orbit imagery
- `mixed`: a combination of both

Each preset must document and configure an appropriate COLMAP matching
strategy and camera assumptions. Allow advanced COLMAP options to be
overridden without editing source code.

## Reconstruction pipeline

Implement an automated, resumable pipeline covering:

1. Input download or mounting
2. DNG-to-TIFF conversion
3. Image/resource preflight and optional working-image downscaling
4. COLMAP feature extraction
5. Feature matching
6. Sparse reconstruction
7. Dense reconstruction
8. Mesh generation and cleanup
9. UV and texture generation
10. Mesh simplification into three variants
11. Validation, packaging, and user-confirmed upload

COLMAP must be used for photogrammetry. Additional open-source tools may be
used for RAW conversion, texturing, cleanup, format conversion,
simplification, and Blender validation. Pin tested versions and document every
external dependency, why it is required, and its license.

Detect GPUs, compatible COLMAP CUDA features, system RAM, and free disk space
before expensive stages. Use GPU acceleration when compatible. Otherwise,
fall back to CPU execution with an explanatory warning rather than silently
changing behavior or failing without context.

The pipeline may automatically downscale working reconstruction images when a
full-resolution run is predicted to exceed available RAM, VRAM, or disk space.
Never replace the original TIFF conversions. Record the selected working
resolution, reason, and resource estimate in the run manifest.

## Coordinate system and metric scale

Prefer approximate real-world metric scale and orientation derived from DJI
GPS and altitude metadata when sufficient metadata is available. Manual
ground-control-point support is not required.

Because image metadata may be incomplete or inaccurate:

- Report which metadata was used and the resulting alignment/scale method.
- Report an estimate or warning about expected accuracy.
- Fall back to arbitrary scale when metric alignment cannot be established,
  and label the output clearly.
- Center exported geometry near Blender's origin for numerical and rendering
  stability.
- Store the GPS-to-model transform and coordinate-system details in the
  manifest or a sidecar file.
- Document Blender axes, units, origin, and any coordinate conversion.
- Preserve the established coordinate system, relative scale, UVs, and
  texture references across all simplified variants.

## Output variants and resource profiles

Produce these textured variants:

- `high`: highest practical quality supported by the input data and current
  execution environment
- `medium`: interactive Blender use on an RTX 5070 system
- `low`: interactive Blender use within a 4 GB GPU-memory budget

The RTX 5070 memory capacity is unknown and must not be inferred from the
product name. Expose the `medium` viewing-memory budget as configuration and
use a clearly documented conservative default until the actual capacity is
provided.

Before implementing the pipeline stages, record proposed measurable defaults
in `docs/quality-profiles.md` and configuration. Continue implementation
without waiting for approval. For every profile define:

- Maximum triangle count as a hard validation limit
- Maximum texture atlas count and dimensions as hard validation limits
- Texture format, color space, mipmap policy, and compression settings
- Simplification method and UV/material preservation settings
- Estimated packaged disk size
- Estimated peak Blender viewing RAM and VRAM
- A safety margin for the low profile below its 4 GB limit

Choose initial limits by documenting assumptions and benchmarking a
representative subset. Treat triangle and texture limits as enforceable bounds;
treat RAM, VRAM, and disk figures as estimates tied to the documented workload
and measurement method. If a profile exceeds a hard limit, fail that profile's
validation clearly rather than silently relabeling it.

Export every variant in both of these forms:

- A self-contained GLB file
- OBJ + MTL + texture files with portable relative paths

PLY may also be included for geometry inspection, but it is not a substitute
for either required textured deliverable.

## Resumability and stage integrity

Each stage must:

- Write outputs atomically where practical.
- Validate its outputs before marking itself complete.
- Record input fingerprints, relevant configuration, tool versions, start/end
  times, duration, warnings, and output paths.
- Rerun automatically when its inputs, effective configuration, tool version,
  or required outputs have changed.
- Never treat a partial or corrupt output as completed work.

Provide explicit CLI controls equivalent to `--from-stage`, `--to-stage`, and
`--force-stage`. A failed or interrupted stage must leave prior valid stages
reusable.

## Runtime Web UI

A lightweight Web UI is required for runtime setup. The core pipeline must
also remain fully usable from the CLI and notebook without relying on browser
automation.

The Web UI must:

- Be reachable through the primary FRP tunnel broker documented at
  `http://163.61.236.112:7001/llm.txt`.
- Implement the broker's required allocation, heartbeat, and release behavior.
- Use a Cloudflare tunnel only when the primary tunnel service is unavailable.
- On the first run, allow the user to create the UI password through the public
  setup page. A bootstrap token is not required; this accepted security tradeoff
  must be documented.
- Disable the unauthenticated setup route immediately after the first password
  is created and require authentication for subsequent access.
- Collect input location, capture mode, reconstruction settings, quality
  budgets, output destination, and runtime credentials.
- Start the pipeline after valid configuration is submitted.
- Remain available to show read-only stage progress, elapsed time, sanitized
  warnings, and sanitized failure logs.
- On failure, show enough sanitized logs to diagnose the failed stage. Do not
  expose a retry button; retries are performed through the notebook or CLI.
- Ask for explicit user confirmation before uploading successful artifacts.

The Web UI must not expose arbitrary command execution, unrestricted
filesystem browsing, environment dumps, raw configuration files, or secrets.
Publicly displayed logs must redact passwords, OAuth tokens, private keys,
cookies, MEGA folder keys, sensitive URL fragments, and rclone configuration
contents.

## Storage, authentication, and secret lifetime

A run must support at least one user-selected upload backend:

1. MEGA using credentials entered at runtime
2. Google Drive using an rclone remote configured during the run

The Google Drive Web UI flow must:

- Invoke an rclone-compatible headless authorization workflow in Kaggle.
- Generate the authorization link/instructions in Kaggle.
- Allow the user to complete Google OAuth in their local browser.
- Accept the resulting authorization response/token through an authenticated
  Web UI form.
- Create and validate the Google Drive rclone remote for the current run.

All MEGA credentials, Google OAuth tokens, and generated rclone configuration
are ephemeral and valid only for the current runtime. Keep them in memory when
possible. If a tool requires a file, use a permission-restricted temporary file
outside the repository and remove it during normal shutdown. Do not persist
these values in notebooks, manifests, logs, generated artifacts, Git, or
long-lived configuration.

Accept runtime values through the Web UI and, for non-Web-UI execution, through
Kaggle Secrets, Colab secrets, environment variables, or an external rclone
configuration. Supported configuration names should include:

- `MEGA_EMAIL`
- `MEGA_PASSWORD`
- `RCLONE_CONFIG`
- `INPUT_FOLDER_URL`
- `OUTPUT_REMOTE`
- `TRANSPORT_HOST`

Do not put credentials or private folder keys in command-line arguments where
they may be exposed through process listings. Never hard-code or commit
passwords, OAuth tokens, private keys, cookies, or shared-folder decryption
keys.

## Deliverables

Add the following to this repository:

- One Kaggle-first portable notebook with best-effort Colab support
- Reusable command-line scripts for major pipeline stages
- Configuration files for reconstruction, capture modes, and quality profiles
- Dependency installation/setup scripts with pinned tested versions
- The runtime configuration/progress Web UI
- A README containing exact Kaggle setup and execution instructions, CLI and
  remote smoke-test instructions, best-effort Colab notes, troubleshooting,
  security tradeoffs, and output import instructions
- `.gitignore` rules covering credentials, temporary rclone files, downloaded
  images, TIFF conversions, databases, intermediate reconstructions, logs, and
  generated meshes
- A small validation or smoke-test workflow using generated or redistributable
  fixtures rather than private source imagery

Do not commit source imagery, generated reconstruction data, credentials,
private dataset links, or other large artifacts.

## Validation, manifests, and packaging

Produce a final machine-readable artifact manifest using SHA-256 checksums. It
must include file sizes, tool versions, effective configuration, stage status,
resource estimates, coordinate/scale status, and warnings without containing
secrets.

Use an automated Blender headless validation step where possible. For each GLB
and OBJ deliverable verify at least:

- The asset opens successfully.
- A nonempty mesh and at least one material exist.
- Texture references resolve inside the package.
- Triangle, texture count, and texture dimension limits are respected.
- Transforms and geometry contain no NaN or infinite values.
- OBJ/MTL/texture paths are relative and portable.
- The three variants preserve consistent orientation and relative scale.

Clearly report incomplete reconstructions and validation failures. A profile
that fails validation must not be reported as a successful deliverable.

## Development workflow

Repository:

`git@github.com:hamimmahmud0/colmap-kaggle.git`

Make all source-code changes in the local workspace. Use the remote environment
only for CLI execution and smoke testing. Remote connection details and
passwords must be supplied outside this file through environment variables or
an SSH configuration entry. Never place a literal password in a committed
`sshpass` command.

Document the repository or synchronization workflow used to make local changes
available in the read-only remote environment.

For development, obtain the MEGA test-folder URL through runtime configuration.
Do not commit its URL, credentials, or private folder key. Start with a small,
representative subset before running the complete dataset.

## Definition of done

The task is complete when:

1. A clean Kaggle session can install dependencies and start the notebook/Web
   UI using only documented steps.
2. The end-to-end pipeline completes on the representative Kaggle test dataset.
3. DNG inputs are converted to validated 16-bit TIFFs without modifying the
   originals, using recorded deterministic RAW settings.
4. The selected capture mode produces a valid sparse reconstruction and dense
   mesh; all three capture-mode presets are configuration-tested.
5. High, medium, and low variants are produced as both GLB and OBJ packages.
6. Blender headless validation confirms that meshes and textures load and that
   profile hard limits are respected.
7. The low profile respects its configured 4 GB viewing target and documented
   safety margin; the medium profile uses an explicit configurable budget.
8. GPS-derived metric scale/orientation is used when viable, otherwise the
   arbitrary-scale fallback is clearly reported.
9. A user can configure either MEGA or Google Drive/rclone through the Web UI,
   and upload occurs only after explicit confirmation.
10. Interrupted or failed stages can resume without rerunning prior valid
    stages.
11. The CLI smoke test passes in the remote development environment.
12. Colab compatibility limitations are documented; Colab completion is not a
    release blocker.
13. Documentation contains exact setup, execution, troubleshooting, Blender
    import, security, and output instructions.
14. The final manifest contains sizes and SHA-256 checksums, and no secrets or
    large generated artifacts are tracked by Git.

If any required value is missing, identify it explicitly and continue with a
safe placeholder or small local smoke test where possible. Do not invent
credentials, private URLs, hardware capacity, or successful validation results.
