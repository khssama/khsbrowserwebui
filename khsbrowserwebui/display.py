"""Virtual display and browser process management."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .log import log

BROWSER_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chrome",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
)

BROWSER_PATHS = (
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/opt/google/chrome/chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
)

# Chromium/Chrome flags that make it usable on a bare Xvfb display.
# --headless=new is critical: without it Chromium often crashes when there is
# no real GPU / window manager available.
CHROME_FLAGS = (
    "--headless=new",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-translate",
    "--disable-infobars",
    "--disable-features=TranslateUI",
    "--password-store=basic",
    "--use-mock-keychain",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-gpu-sandbox",
    "--disable-software-rasterizer",
    "--no-zygote",
    "--autoplay-policy=no-user-gesture-required",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-ipc-flooding-protection",
    "--disable-background-networking",
    "--disable-hang-monitor",
    "--disable-breakpad",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-domain-reliability",
    "--disable-sync",
    "--metrics-recording-only",
    "--safebrowsing-disable-auto-update",
    "--force-color-profile=srgb",
    "--force-device-scale-factor=1",
    "--hide-crash-restore-bubble",
)


def display_socket(display_num: int) -> Path:
    return Path(f"/tmp/.X11-unix/X{display_num}")


def display_in_use(display_num: int) -> bool:
    return display_socket(display_num).exists()


def pick_display(requested: str | None) -> int:
    """Return the X display number to use.

    An explicit ``--display`` wins. Otherwise try :99 first (the usual
    vbrowser display) and walk downwards until a free one is found.
    """
    if requested:
        num = int(requested.lstrip(":"))
        return num

    for num in range(99, 89, -1):
        if not display_in_use(num):
            return num
    raise RuntimeError("no free X display found (tried :90-:99)")


def start_xvfb(display_num: int, width: int, height: int, depth: int = 24) -> subprocess.Popen:
    xvfb = shutil.which("Xvfb")
    if not xvfb:
        raise RuntimeError("Xvfb not found; install it with: apt-get install -y xvfb")

    cmd = [
        xvfb,
        f":{display_num}",
        "-screen",
        "0",
        f"{width}x{height}x{depth}",
        "-ac",
        "-nolisten",
        "tcp",
        "+extension",
        "RANDR",
    ]
    log.info("starting Xvfb on :%d (%dx%d)", display_num, width, height)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    for _ in range(100):
        if display_in_use(display_num):
            return proc
        if proc.poll() is not None:
            raise RuntimeError("Xvfb exited immediately; check your Xvfb install")
        time.sleep(0.05)
    raise RuntimeError("timed out waiting for Xvfb to start")


def find_browser(explicit: str | None = None) -> str:
    if explicit:
        if Path(explicit).is_file():
            return explicit
        found = shutil.which(explicit)
        if found:
            return found
        raise RuntimeError(f"browser not found: {explicit}")

    for name in BROWSER_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    for path in BROWSER_PATHS:
        if os.path.isfile(path):
            return path
    raise RuntimeError(
        "no Chrome/Chromium found; install one with: apt-get install -y google-chrome-stable"
    )


def start_browser(
    browser: str,
    display_num: int,
    width: int,
    height: int,
    profile_dir: Path,
    debug_port: int = 0,
    extra_args: list[str] | None = None,
) -> subprocess.Popen:
    profile_dir.mkdir(parents=True, exist_ok=True)
    # A crashed instance can leave a lock behind; it only hurts the next start.
    for stale in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            (profile_dir / stale).unlink()
        except FileNotFoundError:
            pass

    env = dict(os.environ)
    env["DISPLAY"] = f":{display_num}"

    cmd = [
        browser,
        f"--remote-debugging-port={debug_port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir}",
        f"--window-size={width},{height}",
        "--window-position=0,0",
        f"--display=:{display_num}",
        *CHROME_FLAGS,
        *(extra_args or []),
        "about:blank",
    ]

    log.info("starting browser: %s", browser)
    return subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def focus_browser_window(display_num: int, timeout: float = 15.0) -> bool:
    """Give keyboard focus to the browser window.

    There is no window manager on a bare Xvfb, so nothing focuses the window
    for us. Without this, keystrokes would go to the root window.
    """
    try:
        from Xlib import X, display as xdisplay
    except ImportError:
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            d = xdisplay.Display(f":{display_num}")
        except Exception:
            time.sleep(0.2)
            continue

        try:
            root = d.screen().root
            candidates = []

            def walk(window, depth=0):
                if depth > 4:
                    return
                try:
                    children = window.query_tree().children
                except Exception:
                    return
                for child in children:
                    try:
                        attrs = child.get_attributes()
                        wm_class = child.get_wm_class() or ("", "")
                        name = child.get_wm_name() or ""
                    except Exception:
                        continue
                    if attrs.map_state == X.IsViewable:
                        label = " ".join(wm_class) + " " + str(name)
                        if "chrome" in label.lower() or "chromium" in label.lower():
                            geom = child.get_geometry()
                            candidates.append((geom.width * geom.height, child))
                    walk(child, depth + 1)

            walk(root)
            if candidates:
                candidates.sort(key=lambda item: item[0], reverse=True)
                win = candidates[0][1]
                try:
                    win.set_input_focus(X.RevertToParent, X.CurrentTime)
                    win.configure(stack_mode=X.Above)
                    d.sync()
                    return True
                except Exception:
                    pass
        finally:
            try:
                d.close()
            except Exception:
                pass

        time.sleep(0.25)

    return False


def terminate(proc: subprocess.Popen | None, name: str) -> None:
    if proc is None or proc.poll() is not None:
        return
    log.info("stopping %s", name)
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
