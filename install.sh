#!/usr/bin/env bash
# =============================================================================
# DJI Reconstruction Pipeline – One-line installer
# =============================================================================
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/hamimmahmud0/colmap-kaggle/main/install.sh | bash
#
# Or with a specific tag:
#   curl -fsSL https://raw.githubusercontent.com/hamimmahmud0/colmap-kaggle/main/install.sh | bash -s -- v1.1.2
#
# Or download and run locally:
#   ./install.sh v1.1.2
#
# The script:
#   1. Downloads the tagged release tarball from GitHub
#   2. Creates a Python virtual environment
#   3. Installs the pipeline CLI (`dji-recon`) and Web UI (`dji-recon-web`)
#   4. Copies default configuration files
#   5. Prints a quick-start summary
#
# Requirements:
#   – Ubuntu 22.04+ (or Debian-based) with sudo
#   – Python 3.10+
#   – Internet access
# =============================================================================

# Use explicit error handling (no set -e) so every failure is visible.
set -uo pipefail

# --- configuration -----------------------------------------------------------
REPO="hamimmahmud0/colmap-kaggle"
DEFAULT_TAG="v1.1.2"
INSTALL_DIR="${HOME}/.dji-recon"
VENV_DIR="${INSTALL_DIR}/venv"
CONFIG_DIR="${INSTALL_DIR}/config"
WORKSPACE_DIR="${INSTALL_DIR}/workspace"

# Colour helpers (optional – no hard dependency)
if [[ -t 2 ]]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[1;33m'
  BOLD='\033[1m'
  RESET='\033[0m'
else
  RED='' GREEN='' YELLOW='' BOLD='' RESET=''
fi

log()   { printf "${BOLD}[dji-recon]${RESET} %s\n" "$*" >&2; }
ok()    { printf "${GREEN}[✓]${RESET} %s\n" "$*" >&2; }
warn()  { printf "${YELLOW}[!]${RESET} %s\n" "$*" >&2; }
fail()  { printf "${RED}[✗]${RESET} %s\n" "$*" >&2; exit 1; }

# --- helpers ----------------------------------------------------------------
has()    { command -v "$1" >/dev/null 2>&1; }

check_prerequisites() {
  log "Checking prerequisites …"
  has curl    || fail "curl is required. Install with: sudo apt install curl"
  has python3 || fail "python3 >= 3.10 is required"
  has git     || warn "git is not installed – some features may be unavailable"
  has cmake   || warn "cmake is not installed – COLMAP / texrecon will need system packages"
  ok "Prerequisites satisfied"
}

# --- download & extract -----------------------------------------------------
download_release() {
  local tag="$1"
  local dest="$2"
  local tarball_url="https://github.com/${REPO}/archive/refs/tags/${tag}.tar.gz"

  log "Downloading ${tag} from GitHub …"
  if ! curl -fsSL --retry 3 --retry-delay 5 --max-time 300 \
    "${tarball_url}" -o "${dest}"; then
    fail "Download failed — check internet connectivity and that tag ${tag} exists\n" \
         "       URL: ${tarball_url}"
  fi
  ok "Downloaded ${tag}.tar.gz"

  log "Extracting …"
  if ! tar -xzf "${dest}" -C "$(dirname "${dest}")"; then
    fail "Failed to extract tarball"
  fi
  ok "Extracted"
}

# --- Python environment -----------------------------------------------------
setup_python() {
  local src_dir="$1"

  log "Setting up Python environment …"
  mkdir -p "${INSTALL_DIR}"

  # Remove any previous venv
  rm -rf "${VENV_DIR}"

  # Create venv without pip (most portable across Python versions)
  if ! python3 -m venv --without-pip "${VENV_DIR}" 2>/dev/null; then
    warn "python3-venv may not be installed — attempting to install it …"
    if has apt-get && has sudo; then
      sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv || true
    fi
    if ! python3 -m venv --without-pip "${VENV_DIR}"; then
      fail "Cannot create Python virtual environment\n" \
           "       Install python3-venv manually: sudo apt install python3-venv"
    fi
  fi

  # Bootstrap pip into the venv
  log "Bootstrapping pip …"
  local pip_bootstrap="${INSTALL_DIR}/get-pip.py"
  if ! curl -fsSL --retry 2 --max-time 60 \
    https://bootstrap.pypa.io/get-pip.py -o "${pip_bootstrap}"; then
    fail "Could not download pip bootstrap"
  fi
  if ! "${VENV_DIR}/bin/python" "${pip_bootstrap}" --quiet; then
    fail "pip bootstrap failed"
  fi
  rm -f "${pip_bootstrap}"

  # Install the project
  log "Installing pipeline package …"
  if ! "${VENV_DIR}/bin/python" -m pip install --quiet "${src_dir}"; then
    fail "pip install failed\n" \
         "       Check that ${src_dir} contains a valid pyproject.toml"
  fi
  ok "Python package installed"
}

# --- config -----------------------------------------------------------------
setup_config() {
  local src_dir="$1"

  log "Copying default configuration …"
  mkdir -p "${CONFIG_DIR}" "${WORKSPACE_DIR}"

  local src_config="${src_dir}/configs/default.yaml"
  local dst_config="${CONFIG_DIR}/default.yaml"

  if [[ ! -f "${src_config}" ]]; then
    warn "default.yaml not found in release — skipping"
    return
  fi

  if [[ -f "${dst_config}" ]]; then
    warn "Configuration already exists at ${dst_config} — not overwriting"
    warn "Delete it manually if you want a fresh copy"
  else
    cp "${src_config}" "${dst_config}"
    ok "Config copied to ${dst_config}"
  fi

  # Also copy referenced profile files (capture_modes, quality_profiles)
  local extra
  for extra in capture_modes.yaml quality_profiles.yaml; do
    local src_extra="${src_dir}/configs/${extra}"
    local dst_extra="${CONFIG_DIR}/${extra}"
    if [[ -f "${src_extra}" && ! -f "${dst_extra}" ]]; then
      cp "${src_extra}" "${dst_extra}"
      ok "${extra} copied to ${dst_extra}"
    fi
  done
}

# --- summary ----------------------------------------------------------------
print_summary() {
  local tag="$1"
  echo ""
  printf "${BOLD}${GREEN}═══ DJI Reconstruction Pipeline installed ═══${RESET}\n"
  echo ""
  printf "  Version    : ${BOLD}%s${RESET}\n" "${tag}"
  printf "  Install    : ${BOLD}%s${RESET}\n" "${INSTALL_DIR}"
  printf "  Config     : ${BOLD}%s${RESET}\n" "${CONFIG_DIR}/default.yaml"
  printf "  Workspace  : ${BOLD}%s${RESET}\n" "${WORKSPACE_DIR}"
  echo ""
  echo "  Quick start:"
  echo ""
  printf "    ${GREEN}# Edit the config with your input path and settings${RESET}\n"
  printf "    ${GREEN}\$ ${BOLD}%s --config ${CONFIG_DIR}/default.yaml --print-resources${RESET}\n" \
    "${VENV_DIR}/bin/dji-recon"
  printf "    ${GREEN}\$ ${BOLD}%s --config ${CONFIG_DIR}/default.yaml${RESET}\n" \
    "${VENV_DIR}/bin/dji-recon"
  echo ""
  echo "  Web UI:"
  printf "    ${GREEN}\$ ${BOLD}%s${RESET}\n" "${VENV_DIR}/bin/dji-recon-web"
  echo ""
  printf "  Add to PATH:  ${YELLOW}export PATH=\"%s:\$PATH\"${RESET}\n" "${VENV_DIR}/bin"
  echo ""

  # Warn if heavy dependencies are missing
  local missing=()
  has blender    || missing+=("blender   (apt install blender)")
  has colmap     || missing+=("colmap    (apt install colmap)")
  has exiftool   || missing+=("exiftool  (apt install libimage-exiftool-perl)")
  has texrecon   || missing+=("texrecon  (built from mvs-texturing)")
  has frpc       || missing+=("frpc      (for Web UI tunnel)")
  has cloudflared|| missing+=("cloudflared (for Web UI fallback tunnel)")

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "  ${YELLOW}Missing runtime dependencies (install with apt or see docs/dependencies.md):${RESET}"
    for item in "${missing[@]}"; do
      printf "    ${YELLOW}– %s${RESET}\n" "${item}"
    done
    echo ""
    printf "  ${YELLOW}Tip: run ${BOLD}scripts/install_kaggle.sh${RESET}${YELLOW} for a full dependency install${RESET}\n"
    echo ""
  fi
}

# --- main -------------------------------------------------------------------
main() {
  echo ""
  log "DJI Reconstruction Pipeline – Installer"
  echo ""

  check_prerequisites

  # Resolve tag
  local tag="${1:-${DEFAULT_TAG}}"
  tag="${tag#v}"
  tag="v${tag}"
  log "Installing version ${tag}"

  # Create temp workspace
  local tmp_dir
  tmp_dir=$(mktemp -d -t dji-recon-install.XXXXXX)

  # Download and extract
  local tarball="${tmp_dir}/${tag}.tar.gz"
  download_release "${tag}" "${tarball}"

  # Find the extracted directory
  local src_dir
  src_dir=$(find "${tmp_dir}" -maxdepth 1 -type d -name "colmap-kaggle-*" 2>/dev/null | head -n1)
  if [[ -z "${src_dir}" ]]; then
    fail "Could not find extracted directory in ${tmp_dir}"
  fi

  # Setup
  setup_python "${src_dir}"
  setup_config "${src_dir}"

  # Cleanup temp
  rm -rf "${tmp_dir}"

  print_summary "${tag}"
}

main "$@"
