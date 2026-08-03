#!/usr/bin/env bash
set -euo pipefail
repo_dir="${1:-/kaggle/working/colmap-kaggle}"
cd "$repo_dir"
venv_path=/kaggle/working/dji-recon-smoke-venv
if [[ ! -x "$venv_path/bin/pytest" ]]; then
    rm -rf "$venv_path"
    if command -v uv >/dev/null; then
        uv venv --python python3 "$venv_path"
    else
        python3 -m venv "$venv_path"
    fi
fi
if command -v uv >/dev/null; then
    uv pip install --python "$venv_path/bin/python" -e '.[test]'
else
    "$venv_path/bin/python" -m pip install --disable-pip-version-check -e '.[test]'
fi
"$venv_path/bin/pytest"
smoke_root=$(mktemp -d /kaggle/working/dji-recon-smoke-XXXXXX)
trap 'rm -rf "$smoke_root"' EXIT
sed -e "s#workspace: workspace#workspace: $smoke_root/workspace#" \
    -e 's/execution_mode: real/execution_mode: mock/' \
    configs/default.yaml > "$smoke_root/config.yaml"
printf '\n_config_root: %s/configs\n' "$repo_dir" >> "$smoke_root/config.yaml"
"$venv_path/bin/dji-recon" --config "$smoke_root/config.yaml"
test -f "$smoke_root/workspace/artifacts/manifest.json"
python3 -m json.tool "$smoke_root/workspace/artifacts/manifest.json" >/dev/null
echo "Remote orchestration smoke test passed: $smoke_root/workspace/artifacts/manifest.json"
