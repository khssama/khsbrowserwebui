#!/usr/bin/env bash
# Start khsbrowserwebui, installing the two Python dependencies if needed.
set -euo pipefail
cd "$(dirname "$0")"

python3 - <<'PY'
import importlib.util, subprocess, sys

missing = [name for name in ("aiohttp", "Xlib") if importlib.util.find_spec(name) is None]
if missing:
    print("Installing Python dependencies:", ", ".join(missing))
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "aiohttp", "python-xlib"])
PY

exec python3 -m khsbrowserwebui "$@"
