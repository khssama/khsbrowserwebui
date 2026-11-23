#!/usr/bin/env bash
# KHS Browser WebUI — simple start, stops with Ctrl+C.
set -euo pipefail
cd "$(dirname "$0")"

# Auto-install Python dependencies if missing
python3 - <<'PY'
import importlib.util, subprocess, sys
missing = [name for name in ("aiohttp", "Xlib") if importlib.util.find_spec(name) is None]
if missing:
    print("Installing:", ", ".join(missing))
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *missing])
PY

exec python3 -m khsbrowserwebui "$@"
