"""Virtual display and browser process management with robust fallbacks."""

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
    "/usr/bin/chromium-browser-headless",
    "/snap/bin/chromium",
)

# Flags grouped by strategy. We try each group until the browser stays alive.
# Group 0: headless new (most reliable on bare Xvfb / containers)
CHROME_FLAGS_HEADLESS_NEW = (
    "--headless=new",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-translate",
    "--disable-infobars",
    "--disable-features=TranslateUI",
    "--password-store=basic",
    "--use-mock-keychain",
    "--no-sandbox",
    "--disable-setuid-sandbox",
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
    "--remote-debugging-port=0",
)

# Group 1: headless old (older Chromium versions)
CHROME_FLAGS_HEADLESS_OLD = (
    "--headless",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-translate",
    "--disable-infobars",
    "--disable-features=TranslateUI",
    "--password-store=basic",
    "--use-mock-keychain",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-gpu-sandbox",
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
    "--remote-debugging-port=0",
)

# Group 2: non-headless on Xvfb (last resort, needs a real display)
CHROME_FLAGS_VISIBLE = (
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-translate",
    "--disable-infobars",
    "--disable-features=TranslateUI",
    "--password-store=basic",
    "--use-mock-keychain",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-gpu-sandbox",
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
    "--remote-debugging-port=0",
)

CHROME_FLAG_GROUPS = (
    CHROME_FLAGS_HEADLESS_NEW,
    CHROME_FLAGS_HEADLESS_OLD,
    CHROME_FLAGS_VISIBLE,
)


def display_socket(display_num: int) -> Path:
    return Path(f"/tmp/.X11-unix/X{display_num}")


def display_in_use(display_num: int) -> bool:
    return display_socket(display_num).exists()


def pick_display(requested: str | None) -> int:
    """Return the X display number to use."""
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

    # Clean stale socket and lock files for this display
    for path in [display_socket(display_num), Path(f"/tmp/.X{display_num}-lock")]:
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass

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
            # Try once more after cleaning
            for path in [display_socket(display_num), Path(f"/tmp/.X{display_num}-lock")]:
                if path.exists():
                    try:
                        path.unlink()
                    except Exception:
                        pass
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
        "no Chrome/Chromium found; install one with: apt-get install -y chromium-browser"
    )


def start_browser(
    browser: str,
    display_num: int,
    width: int,
    height: int,
    profile_dir: Path,
    debug_port: int = 0,
    extra_args: list[str] | None = None,
) -> tuple[subprocess.Popen, tuple[str, ...]]:
    """Start the browser, trying multiple flag groups until one stays alive.

    Returns (process, flags_used).
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    for stale in ("SingletonLock", "SingletonSocket", "SingletonCookie", "DevToolsActivePort"):
        try:
            (profile_dir / stale).unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass

    env = dict(os.environ)
    env["DISPLAY"] = f":{display_num}"
    # Chrome/Chrome often complains about running as root without this
    env.setdefault("CHROME_DEVEL_SANDBOX", "/usr/bin/chrome-devel-sandbox")

    last_error = ""
    for flags in CHROME_FLAG_GROUPS:
        # Override the debugging port with the real one
        real_flags = []
        for f in flags:
            if f.startswith("--remote-debugging-port="):
                real_flags.append(f"--remote-debugging-port={debug_port}")
            else:
                real_flags.append(f)
        real_flags.extend(extra_args or [])

        cmd = [
            browser,
            f"--window-size={width},{height}",
            "--window-position=0,0",
            f"--display=:{display_num}",
            f"--user-data-dir={profile_dir}",
            *real_flags,
            "about:blank",
        ]

        flag_str = " ".join(real_flags[:5]) + "..."
        log.info("trying browser flags: %s", flag_str)

        try:
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            last_error = str(exc)
            log.warning("failed to launch browser: %s", exc)
            continue

        # Give it a moment to see if it crashes immediately
        time.sleep(2.0)
        if proc.poll() is None:
            log.info("browser started successfully with flags group")
            return proc, tuple(real_flags)
        else:
            log.warning("browser crashed immediately with this flag group, trying next...")
            last_error = f"browser exited with code {proc.returncode}"
            try:
                proc.kill()
            except Exception:
                pass
            # Clean up profile so next attempt starts fresh
            for stale in ("SingletonLock", "SingletonSocket", "SingletonCookie", "DevToolsActivePort"):
                try:
                    (profile_dir / stale).unlink()
                except FileNotFoundError:
                    pass

    raise RuntimeError(
        f"browser could not stay running on display :{display_num}. Last error: {last_error}\n"
        "Try installing missing libraries: apt-get install -y libgbm1 libasound2 libatk1.0-0 "
        "libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 "
        "libxfixes3 libxrandr2 libgdk-pixbuf2.0-0"
    )


def focus_browser_window(display_num: int, timeout: float = 5.0) -> bool:
    """Try to focus the browser window. On headless this usually fails — that's OK."""
    try:
        from Xlib import X, display as xdisplay
    except ImportError:
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            d = xdisplay.Display(f":{display_num}")
        except Exception:
            time.sleep(0.25)
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
