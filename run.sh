#!/usr/bin/env bash
# Start khsbrowserwebui, installing dependencies if needed.
set -euo pipefail
cd "$(dirname "$0")"

# --- Auto-install Python dependencies ---
python3 - <<'PY'
import importlib.util, subprocess, sys

missing = [name for name in ("aiohttp", "Xlib") if importlib.util.find_spec(name) is None]
if missing:
    print("Installing Python dependencies:", ", ".join(missing))
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "aiohttp", "python-xlib"])
PY

# --- Auto-install system dependencies (best effort, needs sudo) ---
if command -v apt-get >/dev/null 2>&1; then
    missing_sys=()
    for cmd in Xvfb ffmpeg chromium-browser chromium google-chrome; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing_sys+=("$cmd")
        fi
    done
    if [ ${#missing_sys[@]} -gt 0 ]; then
        echo "Installing system dependencies: ${missing_sys[*]}"
        sudo apt-get update -qq 2>/dev/null || true
        sudo apt-get install -y -qq "${missing_sys[@]}" 2>/dev/null || {
            echo "WARNING: could not auto-install system packages."
            echo "Please run manually: sudo apt-get install -y xvfb ffmpeg chromium-browser python3-pip"
        }
    fi
fi

exec python3 -m khsbrowserwebui "$@"
