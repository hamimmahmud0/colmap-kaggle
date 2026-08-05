#!/usr/bin/env bash
# =============================================================================
# DJI Reconstruction Pipeline – One-line installer
# =============================================================================
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/hamimmahmud0/colmap-kaggle/main/install.sh | bash
#
# Or with a specific tag:
#   curl -fsSL https://raw.githubusercontent.com/hamimmahmud0/colmap-kaggle/main/install.sh | bash -s -- v1.1.1
#
# Or download and run locally:
#   ./install.sh v1.1.1
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

set -euo pipefail

# --- configuration -----------------------------------------------------------
REPO="hamimmahmud0/colmap-kaggle"
DEFAULT_TAG="v1.1.1"
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

  # Ensure python3-venv / ensurepip is available
  if ! python3 -m venv --help >/dev/null 2>&1; then
    warn "python3-venv is not installed – attempting to install it …"
    if has apt-get && has sudo; then
      sudo apt-get update -qq
      sudo apt-get install -y -qq python3-venv || fail "could not install python3-venv via apt"
      ok "python3-venv installed"
    else
      fail "python3-venv is required but cannot be installed automatically (no apt/sudo)"
    fi
  fi

  has git     || warn "git is not installed – some features may be unavailable"
  has cmake   || warn "cmake is not installed – COLMAP / texrecon will need system packages"
  ok "Prerequisites satisfied"
}

# --- tag resolution ---------------------------------------------------------
resolve_tag() {
  local tag="${1:-$DEFAULT_TAG}"
  # Strip leading 'v' if present and re-add for consistency
  tag="${tag#v}"
  tag="v${tag}"

  # Verify the tag exists on GitHub (fast check with curl)
  local http_code
  http_code=$(curl -s -o /dev/null -w '%{http_code}' \
    "https://github.com/${REPO}/archive/refs/tags/${tag}.tar.gz")

  if [[ "$http_code" == "404" ]]; then
    fail "Tag ${tag} not found at https://github.com/${REPO}/releases\n" \
         "       Available at: https://github.com/${REPO}/tags"
  elif [[ "$http_code" != "200" && "$http_code" != "302" ]]; then
    fail "Unexpected HTTP ${http_code} when checking tag ${tag}"
  fi

  echo "$tag"
}

# --- download & extract -----------------------------------------------------
download_release() {
  local tag="$1"
  local tarball_url="https://github.com/${REPO}/archive/refs/tags/${tag}.tar.gz"
  local tmp_dir

  log "Downloading ${tag} …"
  tmp_dir=$(mktemp -d -t dji-recon-install.XXXXXX)
  trap "rm -rf '${tmp_dir}'" EXIT

  curl -fsSL "$tarball_url" -o "${tmp_dir}/${tag}.tar.gz" || fail "Download failed"
  tar -xzf "${tmp_dir}/${tag}.tar.gz" -C "$tmp_dir"

  local extracted
  extracted=$(find "$tmp_dir" -maxdepth 1 -type d -name "colmap-kaggle-*" | head -n1)
  if [[ -z "$extracted" ]]; then
    fail "Failed to extract tarball (expected colmap-kaggle-* directory)"
  fi

  # Only return the directory path — no other output on stdout
  printf '%s' "$extracted"
}

# --- Python environment -----------------------------------------------------
setup_python() {
  local src="$1"

  log "Setting up Python environment …"
  mkdir -p "$INSTALL_DIR"

  # Remove any previous venv and recreate.
  # `python3 -m venv` can fail when ensurepip is missing/broken (common on
  # minimal Docker / WSL images).  We always create with --without-pip and
  # install pip ourselves via get-pip.py for reliability.
  rm -rf "$VENV_DIR"

  if ! python3 -m venv --without-pip "$VENV_DIR" 2>/dev/null; then
    warn "python3 -m venv --without-pip failed – installing python3-venv …"
    if has apt-get && has sudo; then
      sudo apt-get update -qq
      sudo apt-get install -y -qq python3-venv || true
    fi
    if ! python3 -m venv --without-pip "$VENV_DIR"; then
      fail "Cannot create Python virtual environment; install python3-venv manually"
    fi
  fi

  # Bootstrap pip into the venv
  local tmp_pip
  tmp_pip=$(mktemp -t get-pip.XXXXXX.py)
  curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$tmp_pip"
  "${VENV_DIR}/bin/python" "$tmp_pip" --quiet || fail "pip bootstrap failed"
  rm -f "$tmp_pip"

  # Install the pipeline project
  "${VENV_DIR}/bin/python" -m pip install --quiet "${src}"

  ok "Python package installed"
}

# --- config -----------------------------------------------------------------
setup_config() {
  log "Copying default configuration …"
  mkdir -p "$CONFIG_DIR" "$WORKSPACE_DIR"

  local src_config="$1/configs/default.yaml"
  local dst_config="${CONFIG_DIR}/default.yaml"

  if [[ ! -f "$src_config" ]]; then
    warn "default.yaml not found in release – skipping"
    return
  fi

  if [[ -f "$dst_config" ]]; then
    warn "Configuration already exists at ${dst_config} – not overwriting"
    warn "Delete it manually if you want a fresh copy"
  else
    cp "$src_config" "$dst_config"
    ok "Config copied to ${dst_config}"
  fi
}

# --- summary ----------------------------------------------------------------
print_summary() {
  local tag="$1"
  echo ""
  printf "${BOLD}${GREEN}═══ DJI Reconstruction Pipeline installed ═══${RESET}\n"
  echo ""
  printf "  Version    : ${BOLD}%s${RESET}\n" "$tag"
  printf "  Install    : ${BOLD}%s${RESET}\n" "$INSTALL_DIR"
  printf "  Config     : ${BOLD}%s${RESET}\n" "${CONFIG_DIR}/default.yaml"
  printf "  Workspace  : ${BOLD}%s${RESET}\n" "$WORKSPACE_DIR"
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
      printf "    ${YELLOW}– %s${RESET}\n" "$item"
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

  local tag
  tag=$(resolve_tag "${1:-}")

  local src
  src=$(download_release "$tag")

  setup_python "$src"
  setup_config "$src"

  print_summary "$tag"
}

main "$@"
