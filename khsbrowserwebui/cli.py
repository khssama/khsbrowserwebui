"""Command line entry point: wires up display, browser, capture and server."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path

from aiohttp import web

from . import __version__
from .app import create_app
from .capture import Capture, ffmpeg_supports_x11grab
from .display import (
    display_in_use,
    find_browser,
    focus_browser_window,
    pick_display,
    start_browser,
    start_xvfb,
    terminate,
)
from .input import Input
from .agent import KHSAgent
from .log import log, setup
from .system_doctor import run_doctor


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="khsbrowserwebui",
        description="Serve a controllable Chrome window as a simple web page.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7071, help="port, 0 picks a free one (default: 7071)")
    parser.add_argument("--display", help="X display to use, e.g. :99 (default: auto)")
    parser.add_argument("--width", type=int, default=1280, help="virtual screen width (default: 1280)")
    parser.add_argument("--height", type=int, default=720, help="virtual screen height (default: 720)")
    parser.add_argument("--fps", type=int, default=15, help="capture rate (default: 15)")
    parser.add_argument(
        "--quality",
        type=int,
        default=6,
        help="JPEG quality, 2 best .. 31 worst (default: 6)",
    )
    parser.add_argument("--browser", help="browser binary (default: auto-detect Chrome/Chromium)")
    parser.add_argument("--profile", help="browser profile directory")
    parser.add_argument(
        "--chrome-debug-port",
        type=int,
        default=0,
        help="Chrome remote debugging port, 0 = random (default: 0)",
    )
    parser.add_argument("--token", help="optional shared secret required to access the UI")
    parser.add_argument("--tunnel", action="store_true", help="start a Cloudflare quick tunnel")
    parser.add_argument("--tunnel-binary", help="path to cloudflared (default: auto-detect)")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    parser.add_argument(
        "--check",
        action="store_true",
        help="check system dependencies and exit",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="auto-fix system issues and exit (agent mode)",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="alias for --fix",
    )
    parser.add_argument(
        "--agent",
        action="store_true",
        help="autonomous agent mode: self-heals and restarts until stopped (default)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def print_banner(port: int, display_num: int, browser: str, width: int, height: int) -> None:
    line = "─" * 62
    print()
    print(line)
    print(f"  KHS Browser WebUI {__version__}")
    print(f"  Local   : http://127.0.0.1:{port}/")
    print(f"  Proxy   : <your-origin>/proxy/{port}/")
    print(f"  Display : :{display_num}  ({width}x{height})")
    print(f"  Browser : {browser}")
    print(line)
    print("  Kaggle / VS Code: append /proxy/<port>/ to the URL you already use.")
    print("  Ctrl+C to stop.")
    print(line)
    sys.stdout.flush()


def check_system() -> int:
    """Diagnose the system and report what's missing."""
    from .capture import ffmpeg_supports_x11grab

    issues = []
    ok = []

    def check(name: str, available: bool, install_hint: str = "") -> None:
        if available:
            ok.append(f"[OK] {name}")
        else:
            issues.append(f"[MISSING] {name} {install_hint}")

    # Python
    check("python3", True)
    try:
        import aiohttp  # noqa
        check("python aiohttp", True)
    except ImportError:
        check("python aiohttp", False, "(pip install aiohttp)")
    try:
        import Xlib  # noqa
        check("python Xlib", True)
    except ImportError:
        check("python Xlib", False, "(pip install python-xlib)")

    # System binaries
    check("Xvfb", shutil_available("Xvfb"), "(apt-get install xvfb)")
    check("ffmpeg", shutil_available("ffmpeg"), "(apt-get install ffmpeg)")
    if shutil_available("ffmpeg"):
        check("ffmpeg x11grab", ffmpeg_supports_x11grab(), "(reinstall ffmpeg with x11 support)")

    browser = find_browser_auto()
    check("Chrome/Chromium", browser is not None, "(apt-get install chromium-browser)")

    # X11 libs
    for lib in ["libX11", "libXrandr", "libXtst", "libXfixes", "libXcursor"]:
        check(f"lib {lib}", shutil_available(f"/usr/lib/x86_64-linux-gnu/lib{lib}.so", check_file=True))

    print("=== KHS Browser WebUI System Check ===")
    print()
    for line in ok:
        print(line)
    if issues:
        print()
        print("Issues found:")
        for line in issues:
            print(line)
        print()
        print("Fix with:")
        print("  sudo apt-get update")
        print("  sudo apt-get install -y xvfb ffmpeg chromium-browser python3-pip")
        print("  pip install aiohttp python-xlib")
    else:
        print()
        print("All dependencies satisfied. You can run:")
        print("  ./run.sh --port 7071")

    return 1 if issues else 0


def shutil_available(name: str, check_file: bool = False) -> bool:
    import shutil
    if check_file:
        return os.path.isfile(name)
    return shutil.which(name) is not None


def find_browser_auto() -> str | None:
    import shutil
    for name in ("google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


async def run(args: argparse.Namespace) -> int:
    if args.check:
        return check_system()

    if args.fix or args.doctor:
        return run_doctor(debug=args.debug)

    # Default behavior: autonomous agent that self-heals forever
    agent = KHSAgent(args)
    return await agent.run()
    if display_in_use(display_num):
        log.info("reusing existing X display :%d", display_num)
        xvfb = None
    else:
        xvfb = start_xvfb(display_num, args.width, args.height)

    browser_path = find_browser(args.browser)
    profile = (
        Path(args.profile).expanduser()
        if args.profile
        else Path.home() / ".local" / "share" / "khsbrowserwebui" / "profile"
    )

    browser_proc, flags_used = start_browser(
        browser_path,
        display_num,
        args.width,
        args.height,
        profile,
        debug_port=args.chrome_debug_port,
    )

    log.info("browser flags: %s", " ".join(flags_used[:6]) + "...")

    if focus_browser_window(display_num):
        log.info("browser window focused")
    else:
        log.warning("could not focus the browser window (headless mode — this is normal)")

    capture = Capture(display_num, args.width, args.height, args.fps, args.quality)
    await capture.start()

    input_ = Input(f":{display_num}", args.width, args.height)

    app = create_app(capture, input_, display_num, browser_path, args.token)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, args.host, args.port)
    await site.start()

    sockets = getattr(site._server, "sockets", None) or []
    port = sockets[0].getsockname()[1] if sockets else args.port
    print_banner(port, display_num, browser_path, args.width, args.height)

    tunnel = None
    if args.tunnel:
        from .tunnel import Tunnel
        tunnel = Tunnel(port, args.tunnel_binary)
        await tunnel.start()

    stop_event = asyncio.Event()

    async def monitor() -> None:
        while not stop_event.is_set():
            await asyncio.sleep(2)
            if browser_proc.poll() is not None:
                log.error("browser exited unexpectedly (code %s)", browser_proc.returncode)
                stop_event.set()
                return
            if capture.proc and capture.proc.returncode is not None:
                log.error("capture process exited unexpectedly")
                stop_event.set()
                return

    monitor_task = asyncio.create_task(monitor())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    try:
        await stop_event.wait()
    finally:
        log.info("shutting down")
        monitor_task.cancel()
        if tunnel:
            await tunnel.stop()
        await runner.cleanup()
        await capture.stop()
        input_.close()
        terminate(browser_proc, "browser")
        terminate(xvfb, "Xvfb")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup(args.debug)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        log.error("%s", exc)
        if args.debug:
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
