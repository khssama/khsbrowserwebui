"""Optional Cloudflare quick tunnel so the UI gets a public URL."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

from .log import log

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CANDIDATES = ("cloudflared", "/kaggle/working/cloudflared", "./cloudflared")


def find_cloudflared(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit if Path(explicit).is_file() else shutil.which(explicit)
    for candidate in CANDIDATES:
        if Path(candidate).is_file():
            return str(Path(candidate).resolve())
        found = shutil.which(candidate)
        if found:
            return found
    return None


class Tunnel:
    def __init__(self, port: int, binary: str | None = None) -> None:
        self.port = port
        self.binary = find_cloudflared(binary)
        self.proc: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self.url: str | None = None
        self.url_event = asyncio.Event()

    async def start(self) -> bool:
        if not self.binary:
            log.warning(
                "cloudflared not found; skipping tunnel "
                "(download it or pass --tunnel-binary PATH)"
            )
            return False

        log.info("starting Cloudflare quick tunnel...")
        self.proc = await asyncio.create_subprocess_exec(
            self.binary,
            "tunnel",
            "--url",
            f"http://127.0.0.1:{self.port}",
            "--no-autoupdate",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self.task = asyncio.create_task(self._read_logs())
        return True

    async def _read_logs(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        async for raw in self.proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            match = URL_RE.search(line)
            if match and not self.url:
                self.url = match.group(0)
                self.url_event.set()
                log.info("public URL: %s", self.url)
            elif "ERR" in line or "error" in line.lower():
                log.debug("cloudflared: %s", line)

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None
