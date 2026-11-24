"""Virtual display and browser process management.

Starts Xvfb + Chrome on a virtual X display so ximagesrc can capture real
screen content (not black). Chrome must run NON-headless so it draws to X11.
"""

from __future__ import annotations

import os
import re
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

# Chrome flags for NON-headless mode on Xvfb.
# Must NOT use --headless=new or --headless, otherwise Chrome draws to an
# offscreen buffer and ximagesrc captures a black screen.
CHROME_FLAGS = (
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
            # Try once more after cleaning stale files
            for path in [display_socket(display_num), Path(f"/tmp/.X{display_num}-lock")]:
                if path.exists():
                    try:
                        path.unlink()
                    except Exception:
                        pass
            raise RuntimeError("Xvfb exited immediately; check your Xvfb install")
        time.sleep(0.05)
    raise RuntimeError("timed out waiting for Xvfb to start")


def browser_log_path(profile_dir: Path) -> Path:
    return profile_dir / "browser.log"


def probe_browser(browser: str) -> tuple[bool, str]:
    """Check that a browser binary actually runs.

    Some distros ship a `chromium-browser` wrapper that exits with an error
    unless the chromium snap is installed. Such a wrapper passes `which` but
    kills the app a few seconds after launch, so reject it up front.
    """
    try:
        result = subprocess.run(
            [browser, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)

    output = result.stdout.strip()
    if result.returncode != 0:
        return False, output or f"exit status {result.returncode}"
    if not re.search(r"(?:chrome|chromium|edge|brave)\s+\d", output, re.IGNORECASE):
        return False, output or "no version output"
    return True, output.splitlines()[0]


def find_browser(explicit: str | None = None) -> str:
    if explicit:
        if Path(explicit).is_file():
            path = explicit
        else:
            path = shutil.which(explicit) or ""
        if not path:
            raise RuntimeError(f"browser not found: {explicit}")
        ok, detail = probe_browser(path)
        if not ok:
            raise RuntimeError(f"browser is not usable: {path} ({detail})")
        return path

    candidates = [found for name in BROWSER_CANDIDATES if (found := shutil.which(name))]
    candidates += [path for path in BROWSER_PATHS if os.path.isfile(path)]

    for path in dict.fromkeys(candidates):
        ok, detail = probe_browser(path)
        if ok:
            return path
        log.warning("skipping unusable browser %s: %s", path, detail.replace("\n", " ")[:200])

    raise RuntimeError(
        "no working Chrome/Chromium found; install Google Chrome or a real Chromium package"
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
    """Start Chrome/Chromium on the virtual X display.

    Returns the process. The caller should verify it stays alive.
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

    cmd = [
        browser,
        f"--window-size={width},{height}",
        "--window-position=0,0",
        f"--display=:{display_num}",
        f"--user-data-dir={profile_dir}",
        *CHROME_FLAGS,
        *(extra_args or []),
        "about:blank",
    ]

    # Override the debugging port with the real one
    final_cmd = []
    for arg in cmd:
        if arg.startswith("--remote-debugging-port="):
            final_cmd.append(f"--remote-debugging-port={debug_port}")
        else:
            final_cmd.append(arg)

    log.info("starting browser on :%d", display_num)
    # Keep Chrome's own error output: without it a browser that fails to start
    # only produces the unhelpful "browser exited unexpectedly" message.
    with browser_log_path(profile_dir).open("wb") as browser_log:
        return subprocess.Popen(
            final_cmd,
            env=env,
            stdout=browser_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def focus_browser_window(display_num: int, timeout: float = 5.0) -> bool:
    """Try to focus the browser window. On headless/Xvfb this usually fails — that's OK."""
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


def browser_log_tail(profile_dir: Path, max_lines: int = 10) -> list[str]:
    try:
        lines = browser_log_path(profile_dir).read_text(errors="replace").splitlines()
    except OSError:
        return []
    return [line for line in lines[-max_lines:] if line.strip()]


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
