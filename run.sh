#!/usr/bin/env bash
# =============================================================================
# khsbrowserwebui — auto-installer + launcher
# One command: installs everything needed, then starts the remote browser UI.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo "=============================================="
echo "  khsbrowserwebui — setup & launch"
echo "=============================================="

# ------------------------------------------------------- detect package manager
PKG_MGR=""
PKG_INSTALL=""
if command -v apt-get &>/dev/null; then
    PKG_MGR="apt-get"
    PKG_INSTALL="apt-get install -y -qq"
elif command -v dnf &>/dev/null; then
    PKG_MGR="dnf"
    PKG_INSTALL="dnf install -y -q"
elif command -v yum &>/dev/null; then
    PKG_MGR="yum"
    PKG_INSTALL="yum install -y -q"
elif command -v pacman &>/dev/null; then
    PKG_MGR="pacman"
    PKG_INSTALL="pacman -S --needed --noconfirm -q"
elif command -v apk &>/dev/null; then
    PKG_MGR="apk"
    PKG_INSTALL="apk add --no-cache -q"
else
    echo "WARNING: no supported package manager found (apt/dnf/yum/pacman/apk)"
    echo "Please install manually: xvfb ffmpeg chromium-browser python3-pip"
fi

# ------------------------------------------------------- install system deps
if [ -n "${PKG_MGR:-}" ]; then
    echo "[1/4] Installing system packages via ${PKG_MGR}..."
    case "${PKG_MGR}" in
        apt-get)
            sudo apt-get update -qq 2>/dev/null || true
            sudo ${PKG_INSTALL} xvfb ffmpeg chromium-browser python3-pip python3-venv 2>/dev/null || {
                echo "  Some packages failed; trying chromium instead of chromium-browser..."
                sudo ${PKG_INSTALL} xvfb ffmpeg chromium python3-pip python3-venv 2>/dev/null || true
            }
            ;;
        dnf|yum)
            sudo ${PKG_INSTALL} xorg-x11-server-Xvfb ffmpeg chromium python3-pip 2>/dev/null || true
            ;;
        pacman)
            sudo ${PKG_INSTALL} xorg-server-xvfb ffmpeg chromium python-pip 2>/dev/null || true
            ;;
        apk)
            sudo ${PKG_INSTALL} xvfb-run ffmpeg chromium python3 py3-pip 2>/dev/null || true
            ;;
    esac
    echo "  System packages done."
fi

# ------------------------------------------------------- install python deps
echo "[2/4] Installing Python dependencies..."
python3 -m pip install --upgrade pip -q 2>/dev/null || true
python3 -m pip install -q aiohttp python-xlib 2>/dev/null || {
    echo "  pip install failed; trying pip3..."
    pip3 install -q aiohttp python-xlib 2>/dev/null || true
}
echo "  Python dependencies done."

# ------------------------------------------------------- verify essentials
echo "[3/4] Checking essentials..."
MISSING=0
for cmd in Xvfb ffmpeg python3; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "  MISSING: $cmd"
        MISSING=1
    else
        echo "  OK: $cmd"
    fi
done

# Browser: accept chromium-browser, chromium, or google-chrome
BROWSER=""
for cmd in chromium-browser chromium google-chrome google-chrome-stable; do
    if command -v "$cmd" &>/dev/null; then
        BROWSER="$cmd"
        echo "  OK: browser = $cmd"
        break
    fi
done
if [ -z "$BROWSER" ]; then
    echo "  MISSING: no Chrome/Chromium found"
    MISSING=1
fi

if [ "$MISSING" -ne 0 ]; then
    echo ""
    echo "ERROR: Some essentials are missing. Install them manually, then re-run:"
    echo "  sudo apt-get install -y xvfb ffmpeg chromium-browser python3-pip"
    exit 1
fi

# ------------------------------------------------------- clean stale X11 sockets
echo "[4/4] Cleaning stale X11 sockets..."
rm -f /tmp/.X11-unix/X* /tmp/.X*-lock 2>/dev/null || true
echo "  Cleaned."

# ------------------------------------------------------- launch
echo ""
echo "=============================================="
echo "  Starting khsbrowserwebui..."
echo "  Local   : http://127.0.0.1:7071/"
echo "  Proxy   : <your-origin>/proxy/7071/"
echo "=============================================="
echo ""

exec python3 -m khsbrowserwebui --port 7071 "$@"
