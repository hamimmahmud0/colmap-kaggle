#!/usr/bin/env bash
set -euo pipefail

# Tested base: Ubuntu 22.04 (Kaggle image), Python 3.12, two NVIDIA T4 GPUs.
# Debian package versions are intentionally pinned to the Jammy repository.
if [[ "$(id -u)" -ne 0 ]]; then
  SUDO=sudo
else
  SUDO=
fi

$SUDO apt-get update
env DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y --no-install-recommends \
  blender=3.0.1+dfsg-7 \
  colmap=3.7-2 \
  libimage-exiftool-perl=12.40+dfsg-1 \
  rclone \
  ca-certificates curl git cmake ninja-build build-essential python3-venv \
  libpng-dev libjpeg-dev libtiff-dev libtbb-dev \
  libboost-program-options-dev libboost-graph-dev libboost-system-dev \
  libeigen3-dev libfreeimage-dev libflann-dev libmetis-dev libgoogle-glog-dev \
  libgtest-dev libsqlite3-dev libglew-dev qtbase5-dev libqt5opengl5-dev \
  libcgal-dev libceres-dev libsuitesparse-dev

if [[ -d /kaggle/working ]]; then
  runtime_root=/kaggle/working
else
  runtime_root="$PWD/.runtime"
  mkdir -p "$runtime_root"
fi
venv_path="${DJI_RECON_VENV:-$runtime_root/dji-recon-venv}"
rm -rf "$venv_path"
if command -v uv >/dev/null; then
  uv venv --python python3 "$venv_path"
  uv pip install --python "$venv_path/bin/python" -e '.[test]'
else
  python3.10 -m venv "$venv_path"
  "$venv_path/bin/python" -m pip install --disable-pip-version-check -e '.[test]'
fi

dependency_root="${DJI_RECON_DEPENDENCY_ROOT:-$runtime_root/.dji-recon-dependencies}"
mkdir -p "$dependency_root"

# Distribution COLMAP packages omit CUDA. Build the pinned release for T4
# (compute capability 7.5) whenever nvcc is available; keep apt COLMAP as the
# documented CPU-only fallback.
colmap_commit=682ea9ac4020a143047758739259b3ff04dabe8d
colmap_source="$dependency_root/colmap"
build_colmap=0
if command -v nvcc >/dev/null && ! /usr/local/bin/colmap -h 2>&1 | grep -q 'with CUDA'; then
  build_colmap=1
fi
if [[ "$build_colmap" -eq 1 ]]; then
  if [[ ! -d "$colmap_source/.git" ]]; then
    git clone --recursive https://github.com/colmap/colmap.git "$colmap_source"
  fi
  git -C "$colmap_source" fetch origin "$colmap_commit"
  git -C "$colmap_source" checkout --detach "$colmap_commit"
  git -C "$colmap_source" submodule update --init --recursive
  cmake -S "$colmap_source" -B "$colmap_source/build" -GNinja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CUDA_ARCHITECTURES=75 \
    -DOPENGL_gl_LIBRARY=/usr/local/nvidia/lib64/libGL.so \
    -DCUDA_ENABLED=ON \
    -DGUI_ENABLED=OFF \
    -DTESTS_ENABLED=OFF
  cmake --build "$colmap_source/build" --parallel "${DJI_RECON_BUILD_JOBS:-2}"
  $SUDO cmake --install "$colmap_source/build"
fi

frp_version=0.64.0
frp_archive="frp_${frp_version}_linux_amd64.tar.gz"
frp_sha256=d422aa5f8c775513171ed8b30139ed1a2a7b3bb4649112830b8100ac20774c56
if ! command -v frpc >/dev/null; then
  curl -fsSL "https://github.com/fatedier/frp/releases/download/v${frp_version}/${frp_archive}" -o "$dependency_root/$frp_archive"
  printf '%s  %s\n' "$frp_sha256" "$dependency_root/$frp_archive" | sha256sum --check
  tar -xzf "$dependency_root/$frp_archive" -C "$dependency_root"
  $SUDO install -m 0755 "$dependency_root/frp_${frp_version}_linux_amd64/frpc" /usr/local/bin/frpc
fi

megacmd_version=2.5.2-1.1
megacmd_sha256=61bf53bf14b9b4a6966a5ce94f61ee58b347ac4f11c6eddc8e9c637e1781a27b
megacmd_deb="$dependency_root/megacmd-xUbuntu_22.04_amd64.deb"
if ! command -v mega-cmd >/dev/null; then
  curl -fsSL https://mega.nz/linux/repo/xUbuntu_22.04/amd64/megacmd-xUbuntu_22.04_amd64.deb -o "$megacmd_deb"
  printf '%s  %s\n' "$megacmd_sha256" "$megacmd_deb" | sha256sum --check
  actual_megacmd_version=$(dpkg-deb -f "$megacmd_deb" Version)
  if [[ "$actual_megacmd_version" != "$megacmd_version" ]]; then
    echo "MEGAcmd version mismatch: expected $megacmd_version, got $actual_megacmd_version" >&2
    exit 1
  fi
  $SUDO apt-get install -y "$megacmd_deb"
fi

texturing_commit=f3374298ac959cb5afe47a14e4d35d2ac7fbdbb1
texturing_source="$dependency_root/mvs-texturing"
if ! command -v texrecon >/dev/null; then
  if [[ ! -d "$texturing_source/.git" ]]; then
    git clone --recursive https://github.com/nmoehrle/mvs-texturing.git "$texturing_source"
  fi
  git -C "$texturing_source" fetch --recurse-submodules origin "$texturing_commit"
  git -C "$texturing_source" checkout --detach "$texturing_commit"
  git -C "$texturing_source" submodule update --init --recursive
  cmake -S "$texturing_source" -B "$texturing_source/build" -DCMAKE_BUILD_TYPE=Release
  cmake --build "$texturing_source/build" --parallel "${DJI_RECON_BUILD_JOBS:-2}"
  $SUDO install -m 0755 "$texturing_source/build/apps/texrecon/texrecon" /usr/local/bin/texrecon
fi

echo "Installed versions:"
"$venv_path/bin/python" -c 'import dji_recon; print("dji-recon", dji_recon.__version__)'
colmap -h 2>&1 | head -n 2
blender --version | head -n 1
exiftool -ver
rclone version | head -n 1
frpc --version
mega-version 2>/dev/null | head -n 1 || true
texrecon --help 2>&1 | head -n 1 || true
echo "Python CLI: $venv_path/bin/dji-recon"
echo "Web UI:    $venv_path/bin/dji-recon-web"
