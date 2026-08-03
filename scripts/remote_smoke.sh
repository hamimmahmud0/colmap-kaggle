#!/usr/bin/env bash
set -euo pipefail
repo_dir="${1:-/kaggle/working/colmap-kaggle}"
cd "$repo_dir"
python3 -m pip install --disable-pip-version-check -e '.[test]'
pytest
smoke_root=$(mktemp -d /kaggle/working/dji-recon-smoke-XXXXXX)
trap 'rm -rf "$smoke_root"' EXIT
sed -e "s#workspace: workspace#workspace: $smoke_root/workspace#" \
    -e 's/execution_mode: real/execution_mode: mock/' \
    configs/default.yaml > "$smoke_root/config.yaml"
printf '\n_config_root: %s/configs\n' "$repo_dir" >> "$smoke_root/config.yaml"
dji-recon --config "$smoke_root/config.yaml"
test -f "$smoke_root/workspace/artifacts/manifest.json"
python3 -m json.tool "$smoke_root/workspace/artifacts/manifest.json" >/dev/null
echo "Remote orchestration smoke test passed: $smoke_root/workspace/artifacts/manifest.json"
