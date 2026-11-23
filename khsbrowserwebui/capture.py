"""Screen capture: ffmpeg x11grab -> JPEG frames -> WebSocket subscribers."""

from __future__ import annotations

import asyncio
import shutil

from .log import log

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"
READ_CHUNK = 65536
MAX_BUFFER = 8 * 1024 * 1024


class Capture:
    def __init__(
        self,
        display_num: int,
        width: int,
        height: int,
        fps: int,
        quality: int,
    ) -> None:
        self.display_num = display_num
        self.width = width
        self.height = height
        self.fps = fps
        self.quality = quality
        self.proc: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self._subscribers: set[asyncio.Queue[bytes]] = set()
        self.frames = 0

    async def start(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg not found; install it with: apt-get install -y ffmpeg")

        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "x11grab",
            "-draw_mouse",
            "1",
            "-video_size",
            f"{self.width}x{self.height}",
            "-framerate",
            str(self.fps),
            "-i",
            f":{self.display_num}.0",
            "-c:v",
            "mjpeg",
            "-q:v",
            str(self.quality),
            "-f",
            "mjpeg",
            "pipe:1",
        ]

        log.info(
            "capturing :%d at %dx%d, %d fps (q=%d)",
            self.display_num,
            self.width,
            self.height,
            self.fps,
            self.quality,
        )
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        stdout = self.proc.stdout
        buffer = b""

        try:
            while True:
                chunk = await stdout.read(READ_CHUNK)
                if not chunk:
                    break
                buffer += chunk

                while True:
                    start = buffer.find(JPEG_SOI)
                    if start < 0:
                        buffer = buffer[-1:]
                        break

                    end = buffer.find(JPEG_EOI, start + 2)
                    if end < 0:
                        buffer = buffer[start:]
                        break

                    frame = buffer[start : end + 2]
                    buffer = buffer[end + 2 :]
                    self._publish(frame)

                if len(buffer) > MAX_BUFFER:
                    buffer = b""
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            log.error("capture loop failed: %s", exc)
        finally:
            log.info("capture stopped")

    def _publish(self, frame: bytes) -> None:
        self.frames += 1
        for queue in self._subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue[bytes]:
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[bytes]) -> None:
        self._subscribers.discard(queue)

    @property
    def client_count(self) -> int:
        return len(self._subscribers)

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
