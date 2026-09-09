#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT/app"
INSTALLER_DIR="$ROOT/installer"
DIST_DIR="$ROOT/dist"
PAYLOAD_ZIP="$INSTALLER_DIR/payload.zip"
APP_ZIP="$DIST_DIR/BONECO_RUN_WINDOWS_WEBVIEW.zip"
INSTALLER_EXE="$DIST_DIR/BONECO_RUN_WINDOWS_INSTALLER.exe"
VERSION="$(python3 - <<'PY'
import importlib.util
from pathlib import Path

module = Path("app/app/boneco_windows_daemon.py")
spec = importlib.util.spec_from_file_location("daemon", module)
daemon = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(daemon)
print(daemon.APP_VERSION)
PY
)"

mkdir -p "$DIST_DIR"

python3 -m py_compile \
  "$APP_DIR/app/boneco_windows_daemon.py" \
  "$APP_DIR"/core/*.py \
  "$APP_DIR"/platform/windows/*.py \
  "$APP_DIR"/tests/*.py

python3 -m unittest discover -s "$APP_DIR/tests" -v

7z a -tzip -mx=9 "$APP_ZIP" "$APP_DIR/." \
  -xr'!__pycache__' \
  -xr'!*.pyc' \
  -xr'!runtime/state' \
  -xr'!installer'

cp "$APP_ZIP" "$PAYLOAD_ZIP"

dotnet publish "$INSTALLER_DIR/boneco-windows-installer-dotnet.csproj" \
  -c Release \
  -r win-x64 \
  --self-contained true \
  -p:PublishSingleFile=true \
  -p:EnableCompressionInSingleFile=true \
  -p:IncludeNativeLibrariesForSelfExtract=true \
  -o "$INSTALLER_DIR/out-win"

cp "$INSTALLER_DIR/out-win/BONECO_RUN_WINDOWS_INSTALLER.exe" "$INSTALLER_EXE"

SHA256="$(sha256sum "$INSTALLER_EXE" | awk '{print $1}')"
cat > "$ROOT/update/latest.json" <<JSON
{
  "version": "$VERSION",
  "download_url": "https://github.com/golgol2/boneco-run-windows/releases/latest/download/BONECO_RUN_WINDOWS_INSTALLER.exe",
  "sha256": "$SHA256",
  "notes": [
    "Instalador grafico em arquivo unico.",
    "Correcao de instalacao em caminhos com espaco.",
    "API local separada do gateway mobile.",
    "Agente mobile inicia automaticamente apos registro ou pareamento.",
    "Sanitizacao contra embedded null character.",
    "Verificacao de atualizacao pelo painel Windows."
  ]
}
JSON

printf 'version=%s\n' "$VERSION"
printf 'installer=%s\n' "$INSTALLER_EXE"
printf 'zip=%s\n' "$APP_ZIP"
printf 'sha256=%s\n' "$SHA256"
