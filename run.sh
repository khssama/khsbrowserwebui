#!/usr/bin/env bash
# Start khsbrowserwebui, auto-fixing the system first if needed.
set -euo pipefail
cd "$(dirname "$0")"

echo "Running system doctor (auto-fix agent)..."
python3 -m khsbrowserwebui.system_doctor --debug || true

# Ensure python deps are installed
python3 - <<'PY'
import importlib.util, subprocess, sys
missing = [name for name in ("aiohttp", "Xlib") if importlib.util.find_spec(name) is None]
if missing:
    print("Installing Python dependencies:", ", ".join(missing))
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *missing])
PY

echo "Starting khsbrowserwebui..."
exec python3 -m khsbrowserwebui "$@"
