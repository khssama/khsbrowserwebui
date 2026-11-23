"""aiohttp application: static UI, health check and the streaming WebSocket."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from . import __version__
from .capture import Capture
from .input import Input
from .log import log

STATIC_DIR = Path(__file__).resolve().parent / "static"

NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def create_app(
    capture: Capture,
    input_: Input,
    display_num: int,
    browser: str,
    token: str | None = None,
) -> web.Application:
    started_at = time.time()
    token_hash = hashlib.sha256(token.encode()).hexdigest() if token else None

    @web.middleware
    async def auth_middleware(request: web.Request, handler):
        if token_hash is None or request.path == "/healthz":
            return await handler(request)

        if request.cookies.get("khs_auth") == token_hash:
            return await handler(request)

        if request.query.get("token") == token:
            response = await handler(request)
            response.set_cookie(
                "khs_auth",
                token_hash,
                httponly=True,
                samesite="Lax",
                max_age=30 * 24 * 3600,
            )
            return response

        raise web.HTTPForbidden(text="Forbidden: append ?token=... to the URL")

    @web.middleware
    async def headers_middleware(request: web.Request, handler):
        response = await handler(request)
        response.headers.update(NO_CACHE)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def healthz(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "version": __version__,
                "display": f":{display_num}",
                "size": [capture.width, capture.height],
                "fps": capture.fps,
                "clients": capture.client_count,
                "frames": capture.frames,
                "uptime": round(time.time() - started_at, 1),
                "browser": browser,
            }
        )

    async def index(_request: web.Request) -> web.Response:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html")

    async def static_file(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        path = (STATIC_DIR / name).resolve()
        if STATIC_DIR not in path.parents or not path.is_file():
            raise web.HTTPNotFound()
        content_type, _ = mimetypes.guess_type(path.name)
        return web.Response(
            body=path.read_bytes(),
            content_type=content_type or "application/octet-stream",
        )

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=2 * 1024 * 1024)
        await ws.prepare(request)

        queue = capture.subscribe()
        log.info("client connected (%d total)", capture.client_count)

        async def sender() -> None:
            try:
                while True:
                    frame = await queue.get()
                    if ws.closed:
                        return
                    await ws.send_bytes(frame)
            except asyncio.CancelledError:
                raise
            except (ConnectionResetError, RuntimeError):
                pass

        send_task = asyncio.create_task(sender())
        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    handle_input(input_, message.data)
                elif message.type == WSMsgType.ERROR:
                    break
        finally:
            send_task.cancel()
            capture.unsubscribe(queue)
            log.info("client disconnected (%d left)", capture.client_count)

        return ws

    app = web.Application(middlewares=[headers_middleware, auth_middleware])
    app.router.add_get("/", index)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/static/{name}", static_file)
    app.router.add_get("/ws", websocket)
    return app


def handle_input(input_: Input, raw: str) -> None:
    try:
        event = json.loads(raw)
    except (ValueError, TypeError):
        return
    if not isinstance(event, dict):
        return

    kind = event.get("t")

    try:
        if kind == "move":
            input_.move(to_int(event.get("x")), to_int(event.get("y")))
        elif kind == "down":
            input_.button(to_int(event.get("button")) + 1, True)
        elif kind == "up":
            input_.button(to_int(event.get("button")) + 1, False)
        elif kind == "wheel":
            input_.scroll(to_float(event.get("dx")), to_float(event.get("dy")))
        elif kind == "text":
            text = event.get("text")
            if isinstance(text, str):
                input_.type_text(text)
        elif kind == "key":
            keys = event.get("keys")
            if isinstance(keys, list):
                input_.combo([str(key) for key in keys])
    except Exception as exc:  # keep one bad event from killing the session
        log.warning("input event failed (%s): %s", kind, exc)


def to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
