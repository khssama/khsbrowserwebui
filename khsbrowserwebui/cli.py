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
from .tunnel import Tunnel


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


async def run(args: argparse.Namespace) -> int:
    display_num = pick_display(args.display)
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

    browser = start_browser(
        browser_path,
        display_num,
        args.width,
        args.height,
        profile,
        debug_port=args.chrome_debug_port,
    )

    if focus_browser_window(display_num):
        log.info("browser window focused")
    else:
        log.warning("could not focus the browser window; click inside it once")

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
        tunnel = Tunnel(port, args.tunnel_binary)
        await tunnel.start()

    stop_event = asyncio.Event()

    async def monitor() -> None:
        while not stop_event.is_set():
            await asyncio.sleep(2)
            if browser.poll() is not None:
                log.error("browser exited unexpectedly")
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
        # Give the tunnel a moment to publish its URL if the user stops early.
        await asyncio.sleep(0)
    finally:
        log.info("shutting down")
        monitor_task.cancel()
        if tunnel:
            await tunnel.stop()
        await runner.cleanup()
        await capture.stop()
        input_.close()
        terminate(browser, "browser")
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
