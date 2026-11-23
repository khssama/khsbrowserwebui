"""KHS Browser WebUI Agent — autonomous self-healing session runner.

The agent acts like a small AI agent:

1. Checks the system and auto-fixes missing/broken dependencies.
2. Starts a browser session (Xvfb + Chrome + capture + web server).
3. Monitors the session — if the browser or capture crashes, the agent
   re-diagnoses, re-fixes, and restarts automatically.
4. Keeps looping until you press Ctrl+C.

Run:
    ./run.sh          # agent mode (default)
    ./run.sh --agent  # explicit agent mode
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
import time
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
from .system_doctor import SystemDoctor


class KHSAgent:
    """Autonomous agent that keeps khsbrowserwebui running."""

    def __init__(self, args) -> None:
        self.args = args
        self.display_num: int | None = None
        self.xvfb: object | None = None
        self.browser_proc: object | None = None
        self.capture: Capture | None = None
        self.input_: Input | None = None
        self.runner: web.AppRunner | None = None
        self.port: int | None = None
        self._restart_count = 0
        self._max_restarts = 999  # effectively unlimited

    # ---------------------------------------------------------------- run
    async def run(self) -> int:
        setup(debug=self.args.debug)
        log.info("KHS Browser WebUI Agent v%s starting…", __version__)

        while True:
            try:
                await self._run_once()
            except KeyboardInterrupt:
                log.info("Agent stopped by user")
                return 0
            except SystemExit:
                raise
            except Exception as exc:  # agent catches everything and heals
                log.error("Session crashed: %s", exc)
                self._restart_count += 1
                if self._restart_count > self._max_restarts:
                    log.error("Too many restarts, giving up")
                    return 1
                wait = min(30, 2 * self._restart_count)
                log.info(
                    "Agent healing in %d seconds (restart #%d)…",
                    wait,
                    self._restart_count,
                )
                await asyncio.sleep(wait)
                self._cleanup_session()
                continue

    async def _run_once(self) -> int:
        """One full session: doctor → start → monitor → exit when stopped or crashed."""

        # --- 1. Doctor: check & fix ---
        log.info("Agent: running system check…")
        doctor = SystemDoctor(debug=self.args.debug)
        if not doctor.diagnose_and_fix():
            log.error("Agent: system doctor could not fix everything")
            # Try anyway — sometimes the doctor is conservative

        # --- 2. Start Xvfb ---
        self.display_num = pick_display(self.args.display)
        if display_in_use(self.display_num):
            log.info("reusing existing X display :%d", self.display_num)
            self.xvfb = None
        else:
            self.xvfb = start_xvfb(self.display_num, self.args.width, self.args.height)

        # --- 3. Start browser ---
        browser_path = find_browser(self.args.browser)
        profile = (
            Path(self.args.profile).expanduser()
            if self.args.profile
            else Path.home() / ".local" / "share" / "khsbrowserwebui" / "profile"
        )

        self.browser_proc, flags_used = start_browser(
            browser_path,
            self.display_num,
            self.args.width,
            self.args.height,
            profile,
            debug_port=self.args.chrome_debug_port,
        )
        log.info("browser flags: %s", " ".join(flags_used[:6]) + "…")

        if focus_browser_window(self.display_num):
            log.info("browser window focused")
        else:
            log.warning("could not focus window (headless — normal)")

        # --- 4. Start capture ---
        self.capture = Capture(
            self.display_num, self.args.width, self.args.height,
            self.args.fps, self.args.quality,
        )
        await self.capture.start()

        # --- 5. Start input ---
        self.input_ = Input(f":{self.display_num}", self.args.width, self.args.height)

        # --- 6. Start web server ---
        app = create_app(self.capture, self.input_, self.display_num, browser_path, self.args.token)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.args.host, self.args.port)
        await site.start()

        sockets = getattr(site._server, "sockets", None) or []
        self.port = sockets[0].getsockname()[1] if sockets else self.args.port

        self._print_banner()

        # --- 7. Tunnel (optional) ---
        tunnel = None
        if self.args.tunnel:
            from .tunnel import Tunnel
            tunnel = Tunnel(self.port, self.args.tunnel_binary)
            await tunnel.start()

        # --- 8. Monitor loop (the agent watches) ---
        stop_event = asyncio.Event()

        async def monitor() -> None:
            while not stop_event.is_set():
                await asyncio.sleep(2)
                # Browser died?
                if self.browser_proc and self.browser_proc.poll() is not None:
                    log.error("browser exited (code %s)", self.browser_proc.returncode)
                    stop_event.set()
                    return
                # Capture died?
                if self.capture and self.capture.proc and self.capture.proc.returncode is not None:
                    log.error("capture process exited")
                    stop_event.set()
                    return

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop_event.set)

        monitor_task = asyncio.create_task(monitor())

        try:
            await stop_event.wait()
        finally:
            monitor_task.cancel()
            if tunnel:
                await tunnel.stop()

        return 0

    # ---------------------------------------------------------------- banner
    def _print_banner(self) -> None:
        line = "─" * 62
        print()
        print(line)
        print(f"  KHS Browser WebUI Agent v{__version__}")
        print(f"  Local   : http://127.0.0.1:{self.port}/")
        print(f"  Proxy   : <your-origin>/proxy/{self.port}/")
        print(f"  Display : :{self.display_num}  ({self.args.width}x{self.args.height})")
        print(f"  Browser : {find_browser(self.args.browser)}")
        print(line)
        print("  Agent mode: will auto-heal if anything crashes")
        print("  Kaggle / VS Code: append /proxy/<port>/ to your URL")
        print("  Ctrl+C to stop the agent")
        print(line)
        sys.stdout.flush()

    # ---------------------------------------------------------------- cleanup
    def _cleanup_session(self) -> None:
        log.info("Agent: cleaning up crashed session…")
        if self.runner:
            try:
                asyncio.get_event_loop().run_until_complete(self.runner.cleanup())
            except Exception:
                pass
        if self.capture:
            try:
                asyncio.get_event_loop().run_until_complete(self.capture.stop())
            except Exception:
                pass
        if self.input_:
            try:
                self.input_.close()
            except Exception:
                pass
        terminate(self.browser_proc, "browser")
        terminate(self.xvfb, "Xvfb")
        self.runner = None
        self.capture = None
        self.input_ = None
        self.browser_proc = None
        self.xvfb = None
