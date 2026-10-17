"""The /manage web application. Authentication belongs to the existing reverse proxy."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import secrets

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from manage_core import ApiError, Manager

WEB = Path(__file__).with_name("manage_web")
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}


class ManageService:
    def __init__(self, app, settings):
        loop = asyncio.get_running_loop()
        self.pending = set()
        self.closing = False

        async def local_request(method, path, payload, timeout):
            # Preserve remote queue priority for browser operations on this node.
            # ASGI transport also avoids a new listener, self-HTTP deadlocks and
            # accidental production connections in isolated gateway tests.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url=f"http://127.0.0.1:{settings.proxy_listen_port}", trust_env=False) as client:
                response = await client.request(method, path, json=payload, timeout=timeout)
                return response.status_code, response.content

        def from_worker(method, path, payload, timeout):
            future = asyncio.run_coroutine_threadsafe(local_request(method, path, payload, timeout), loop)
            try:
                return future.result(timeout=timeout + 1)
            except BaseException:
                future.cancel()
                raise

        self.manager = Manager(Path(settings.data_dir) / "manage", from_worker)

    async def call(self, function, *args):
        if self.closing:
            raise ApiError("OSS 正在停止，请稍后重试。", 503)
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)
        return await asyncio.shield(task)

    async def close(self):
        self.closing = True
        await asyncio.gather(*list(self.pending), return_exceptions=True)
        await asyncio.to_thread(self.manager.close)


async def json_body(request, limit):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise ApiError("请求必须是 JSON。", 415)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise ApiError("请求内容过大。", 413)
    try:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise ApiError("请求内容无效。") from None


def install_routes(app, version):
    @app.api_route("/manage", methods=["GET", "HEAD"])
    @app.api_route("/manage/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS"])
    async def manage(request: Request, path: str = ""):
        try:
            # No independent password, challenge or cookie. Nginx must protect
            # this page and its /manage/api/* endpoints just like the OSS API.
            navigation = request.method in {"GET", "HEAD"} and path == "" and request.headers.get("sec-fetch-mode") == "navigate"
            if (request.headers.get("sec-fetch-site") in {"cross-site", "same-site"} and not navigation) or request.headers.get("origin") == "null":
                raise ApiError("不接受跨站管理请求。", 403)
            service = request.app.state.manage
            manager = service.manager
            if request.method in {"GET", "HEAD"}:
                assets = {"": ("index.html", "text/html"), "app.js": ("app.js", "text/javascript"),
                          "style.css": ("style.css", "text/css"), "favicon.svg": ("favicon.svg", "image/svg+xml")}
                if path in assets:
                    filename, mime = assets[path]
                    return Response((WEB / filename).read_bytes(), media_type=mime, headers=HEADERS)
                if path == "api/session":
                    return JSONResponse({"token": manager.token, "name": "OSS 管理", "version": version,
                        "authentication": "reverse_proxy", "embedded": True}, headers=HEADERS)
                if path == "api/overview":
                    return JSONResponse(await service.call(manager.overview), headers=HEADERS)
                raise ApiError("管理接口不存在。", 404)
            if request.method == "OPTIONS":
                raise ApiError("不支持跨站管理请求。", 405)
            if not secrets.compare_digest(request.headers.get("X-OSS-Token", ""), manager.token):
                raise ApiError("页面会话已更新，请刷新后重试。", 403)
            payload = await json_body(request, 1024 * 1024 if path == "api/import-servers" else 16384)
            if request.method == "POST":
                if path == "api/servers":
                    return JSONResponse(await service.call(manager.update_server, payload), status_code=201, headers=HEADERS)
                if path == "api/import-servers":
                    return JSONResponse(await service.call(manager.import_servers, payload), headers=HEADERS)
                if path == "api/refresh":
                    await service.call(manager.refresh)
                    return JSONResponse({"ok": True}, status_code=202, headers=HEADERS)
                match = re.fullmatch(r"api/jobs/([a-f0-9]{32}(?:-[a-f0-9]{32})?)/(pause|resume)", path)
                if match:
                    return JSONResponse(await service.call(manager.control_job, *match.groups()), headers=HEADERS)
            match = re.fullmatch(r"api/servers/([a-f0-9]{32})(?:/(pull|delete-model|refresh))?", path)
            if match:
                ident, action = match.groups()
                if request.method == "PUT" and not action:
                    return JSONResponse(await service.call(manager.update_server, payload, ident), headers=HEADERS)
                if request.method == "DELETE" and not action:
                    await service.call(manager.remove_server, ident)
                    return JSONResponse({"ok": True}, headers=HEADERS)
                if request.method == "POST" and action in {"pull", "delete-model"}:
                    result = await service.call(manager.create_job, ident, payload, "pull" if action == "pull" else "delete")
                    return JSONResponse(result, status_code=202, headers=HEADERS)
                if request.method == "POST" and action == "refresh":
                    await service.call(manager.store.get, ident)
                    await service.call(manager.refresh, ident)
                    return JSONResponse({"ok": True}, status_code=202, headers=HEADERS)
            raise ApiError("管理接口不存在。", 404)
        except ApiError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=HEADERS)
        except Exception:
            return JSONResponse({"error": "暂时无法完成请求，请刷新状态后重试。"}, status_code=500, headers=HEADERS)
