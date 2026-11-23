#!/usr/bin/env bash
# KHS Browser WebUI Agent — self-healing remote browser.
# Runs the system doctor, starts the session, and auto-restarts if anything crashes.
set -euo pipefail
cd "$(dirname "$0")"

echo "KHS Browser WebUI Agent — starting self-healing session…"

# --- Auto-install Python dependencies ---
python3 - <<'PY'
import importlib.util, subprocess, sys
missing = [name for name in ("aiohttp", "Xlib") if importlib.util.find_spec(name) is None]
if missing:
    print("Installing Python dependencies:", ", ".join(missing))
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *missing])
PY

# --- Ensure system packages exist (best effort) ---
if command -v apt-get >/dev/null 2>&1; then
    missing_sys=()
    for cmd in Xvfb ffmpeg chromium-browser chromium google-chrome; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing_sys+=("$cmd")
        fi
    done
    if [ ${#missing_sys[@]} -gt 0 ]; then
        echo "Installing system packages: ${missing_sys[*]}"
        sudo apt-get update -qq 2>/dev/null || true
        sudo apt-get install -y -qq "${missing_sys[@]}" 2>/dev/null || {
            echo "WARNING: could not auto-install system packages."
            echo "The agent will keep trying to fix this on each restart."
        }
    fi
fi

# --- Launch agent (self-heals forever until Ctrl+C) ---
exec python3 -m khsbrowserwebui "$@"
