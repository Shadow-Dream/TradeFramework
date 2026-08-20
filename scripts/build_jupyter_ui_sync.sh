#!/usr/bin/env bash
set -euo pipefail

trade_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
extension_root="$trade_root/jupyter_ui_sync"
preview_python="$trade_root/.runtime/preview/venv/bin/python"

if [[ ! -x "$preview_python" ]]; then
  echo "Preview Jupyter runtime is unavailable; run prepare_engine_preview.py first." >&2
  exit 1
fi

cd "$extension_root"
npm ci --ignore-scripts
npm run build:lib
core_path="$($preview_python -c 'from pathlib import Path; import jupyterlab; print(Path(jupyterlab.__file__).resolve().parent / "static")')"
if [[ ! -d "$core_path" ]]; then
  echo "JupyterLab core assets are unavailable at the resolved path." >&2
  exit 1
fi
./node_modules/.bin/build-labextension . --core-path "$core_path"

asset_root="$trade_root/engine/assets/jupyter_labextensions/@trade-engine/jupyter-ui-sync"
if [[ ! -f "$asset_root/package.json" ]] || ! compgen -G "$asset_root/static/remoteEntry.*.js" >/dev/null; then
  echo "JupyterLab prebuilt extension output is incomplete." >&2
  exit 1
fi
