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
from .capture import Capture
from .display import (
    browser_log_tail,
    display_in_use,
    find_browser,
    focus_browser_window,
    pick_display,
    start_browser,
    start_xvfb,
    terminate,
)
from .input import Input
from .log import log, setup


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="khsbrowserwebui",
        description="Serve a controllable Chrome window as a simple web page.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7071, help="port (default: 7071)")
    parser.add_argument("--display", help="X display, e.g. :99 (default: auto)")
    parser.add_argument("--width", type=int, default=1280, help="width (default: 1280)")
    parser.add_argument("--height", type=int, default=720, help="height (default: 720)")
    parser.add_argument("--fps", type=int, default=15, help="capture fps (default: 15)")
    parser.add_argument(
        "--quality",
        type=int,
        default=6,
        help="JPEG quality 2..31 (default: 6)",
    )
    parser.add_argument("--browser", help="browser binary (default: auto-detect)")
    parser.add_argument("--profile", help="browser profile directory")
    parser.add_argument(
        "--chrome-debug-port",
        type=int,
        default=0,
        help="Chrome debugging port, 0 = random",
    )
    parser.add_argument("--token", help="optional token required to open the UI")
    parser.add_argument("--tunnel", action="store_true", help="start Cloudflare tunnel")
    parser.add_argument("--tunnel-binary", help="path to cloudflared")
    parser.add_argument("--check", action="store_true", help="check deps and exit")
    parser.add_argument("--debug", action="store_true", help="verbose logs")
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
    print("  Kaggle / VS Code: append /proxy/<port>/ to your URL.")
    print("  Ctrl+C to stop.")
    print(line)
    sys.stdout.flush()


def check_system() -> int:
    from .capture import ffmpeg_supports_x11grab
    import shutil

    issues = []
    def check(name, ok, hint=""):
        status = "[OK]" if ok else "[MISSING]"
        print(f"  {status} {name}{hint}")
        if not ok:
            issues.append(name)

    check("python3", True)
    try:
        import aiohttp  # noqa
        check("aiohttp", True)
    except ImportError:
        check("aiohttp", False, "  (pip install aiohttp)")
    try:
        import Xlib  # noqa
        check("Xlib", True)
    except ImportError:
        check("Xlib", False, "  (pip install python-xlib)")
    check("Xvfb", shutil.which("Xvfb") is not None, "  (apt-get install xvfb)")
    check("ffmpeg", shutil.which("ffmpeg") is not None, "  (apt-get install ffmpeg)")
    if shutil.which("ffmpeg"):
        check("ffmpeg x11grab", ffmpeg_supports_x11grab(), "  (reinstall ffmpeg)")
    browser = find_browser(None)
    check("Chrome/Chromium", browser is not None, f"  (found: {browser})")

    print()
    if issues:
        print("Missing:", ", ".join(issues))
        print("Install with: sudo apt-get install -y xvfb ffmpeg chromium-browser python3-pip")
        print("              pip install aiohttp python-xlib")
        return 1
    print("All dependencies satisfied.")
    return 0


async def run(args: argparse.Namespace) -> int:
    if args.check:
        return check_system()

    # Validate the browser before starting Xvfb so a bad binary cannot leave
    # a virtual display behind (which would break the next launch).
    display_num = pick_display(args.display)
    browser_path = find_browser(args.browser)
    profile = (
        Path(args.profile).expanduser()
        if args.profile
        else Path.home() / ".local" / "share" / "khsbrowserwebui" / "profile"
    )

    xvfb = None
    browser_proc = None
    capture = None
    input_ = None
    runner = None
    tunnel = None
    monitor_task = None
    stop_event = asyncio.Event()

    try:
        if display_in_use(display_num):
            log.info("reusing existing X display :%d", display_num)
        else:
            xvfb = start_xvfb(display_num, args.width, args.height)

        browser_proc = start_browser(
            browser_path,
            display_num,
            args.width,
            args.height,
            profile,
            debug_port=args.chrome_debug_port,
        )

        focus_browser_window(display_num)

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

        if args.tunnel:
            from .tunnel import Tunnel
            tunnel = Tunnel(port, args.tunnel_binary)
            await tunnel.start()

        async def monitor() -> None:
            while not stop_event.is_set():
                await asyncio.sleep(2)
                if browser_proc.poll() is not None:
                    log.error("browser exited unexpectedly (code %s)", browser_proc.returncode)
                    for line in browser_log_tail(profile):
                        log.error("  browser: %s", line.strip())
                    stop_event.set()
                    return
                if capture.proc and capture.proc.returncode is not None:
                    log.error("capture process exited")
                    stop_event.set()
                    return

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop_event.set)

        monitor_task = asyncio.create_task(monitor())
        await stop_event.wait()
    finally:
        log.info("shutting down")
        if monitor_task:
            monitor_task.cancel()
        if tunnel:
            await tunnel.stop()
        if runner:
            await runner.cleanup()
        if capture:
            await capture.stop()
        if input_:
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
